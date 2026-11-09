"""The Zoom client against documented shapes: `me` only, a bounded page, an explicit window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from zoom.client import ZoomClient
from zoom.errors import ConnectorAPIError, ConnectorAuthError

API = "https://api.zoom.us/v2"
USERS_ME = f"{API}/users/me"
MEETINGS = f"{API}/users/me/meetings"
RECORDINGS = f"{API}/users/me/recordings"


class Tokens:
    def __init__(self, token: str = "access-1", base: str = API) -> None:
        self.token = token
        self.base = base
        self.invalidated = 0

    async def access_token(self) -> str:
        return self.token

    async def invalidate(self) -> None:
        self.invalidated += 1

    def api_base(self) -> str:
        return self.base


def _client(tokens: Tokens | None = None) -> tuple[httpx.AsyncClient, Tokens]:
    tokens = tokens or Tokens()
    return httpx.AsyncClient(), tokens


# --- user ----------------------------------------------------------------------------------


@respx.mock
async def test_the_current_user_is_mapped_from_the_documented_fields() -> None:
    respx.get(USERS_ME).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "APv5EPHiSvitxgPAw0DbaQ",
                "first_name": "Luis",
                "last_name": "Rincon",
                "email": "luis@hoja.work",
                "type": 1,
                "role_name": "Owner",
                "timezone": "America/Bogota",
                "account_id": "Cx3wERazSgup7ZWRHQM8-w",
                "status": "active",
                "pmi": 5017823017,
            },
        )
    )
    http, tokens = _client()
    async with http:
        user = await ZoomClient(http, tokens).get_current_user()
    assert user.email == "luis@hoja.work"
    assert user.account_id == "Cx3wERazSgup7ZWRHQM8-w"
    assert user.pmi == "5017823017"
    assert user.name == "Luis Rincon"


@respx.mock
async def test_the_display_name_is_the_fallback_when_names_are_missing() -> None:
    respx.get(USERS_ME).mock(
        return_value=httpx.Response(
            200, json={"id": "u", "email": "a@b.test", "display_name": "A B"}
        )
    )
    http, tokens = _client()
    async with http:
        user = await ZoomClient(http, tokens).get_current_user()
    assert user.name == "A B"


# --- meetings ------------------------------------------------------------------------------


@respx.mock
async def test_list_meetings_asks_for_scheduled_by_default() -> None:
    route = respx.get(MEETINGS).mock(
        return_value=httpx.Response(
            200,
            json={
                "next_page_token": "",
                "meetings": [
                    {"id": 97763643886, "uuid": "abc==", "topic": "Standup", "type": 2, "duration": 30}
                ],
            },
        )
    )
    http, tokens = _client()
    async with http:
        page = await ZoomClient(http, tokens).list_meetings()
    request = route.calls[0].request
    assert "type=scheduled" in str(request.url)
    assert "page_size=50" in str(request.url)
    assert page.items[0].topic == "Standup"
    assert page.items[0].id == 97763643886
    assert page.next_page_token is None
    assert page.truncated is False


@respx.mock
async def test_a_meeting_type_outside_zooms_enum_is_refused_before_the_call() -> None:
    http, tokens = _client()
    async with http:
        with pytest.raises(ValueError):
            await ZoomClient(http, tokens).list_meetings(meeting_type="tomorrow")


@respx.mock
async def test_pagination_walks_next_page_token_until_it_is_empty() -> None:
    responses = [
        httpx.Response(200, json={"next_page_token": "cursor-1", "meetings": [{"id": 1, "topic": "a"}]}),
        httpx.Response(200, json={"next_page_token": "", "meetings": [{"id": 2, "topic": "b"}]}),
    ]
    route = respx.get(MEETINGS).mock(side_effect=responses)
    http, tokens = _client()
    async with http:
        page = await ZoomClient(http, tokens).list_meetings(max_results=10)
    assert [m.topic for m in page.items] == ["a", "b"]
    assert len(route.calls) == 2
    assert "next_page_token=cursor-1" in str(route.calls[1].request.url)


@respx.mock
async def test_the_result_cap_stops_the_walk_and_says_so() -> None:
    """A provider that always returns a cursor must not turn one tool call into a loop."""
    respx.get(MEETINGS).mock(
        return_value=httpx.Response(
            200, json={"next_page_token": "more", "meetings": [{"id": 1, "topic": "a"}, {"id": 2, "topic": "b"}]}
        )
    )
    http, tokens = _client()
    async with http:
        page = await ZoomClient(http, tokens).list_meetings(max_results=2)
    assert len(page.items) == 2
    assert page.truncated is True
    assert page.next_page_token == "more"


# --- recordings ----------------------------------------------------------------------------


@respx.mock
async def test_list_recordings_always_sends_an_explicit_window() -> None:
    """Zoom defaults a missing range to one day, which would silently return almost nothing."""
    route = respx.get(RECORDINGS).mock(return_value=httpx.Response(200, json={"meetings": []}))
    http, tokens = _client()
    async with http:
        await ZoomClient(http, tokens).list_recordings()
    url = str(route.calls[0].request.url)
    today = datetime.now(timezone.utc).date()
    assert f"to={today.isoformat()}" in url
    assert f"from={(today - timedelta(days=30)).isoformat()}" in url
    assert "trash=false" in url


@respx.mock
async def test_a_range_wider_than_a_month_is_refused() -> None:
    http, tokens = _client()
    async with http:
        with pytest.raises(ValueError) as excinfo:
            await ZoomClient(http, tokens).list_recordings(
                from_date="2026-01-01", to_date="2026-03-01"
            )
    assert "one month" in str(excinfo.value)


async def test_a_backwards_range_is_refused() -> None:
    http, tokens = _client()
    async with http:
        with pytest.raises(ValueError):
            await ZoomClient(http, tokens).list_recordings(
                from_date="2026-02-01", to_date="2026-01-01"
            )


async def test_a_malformed_date_is_refused() -> None:
    http, tokens = _client()
    async with http:
        with pytest.raises(ValueError):
            await ZoomClient(http, tokens).list_recordings(from_date="01/02/2026")


@respx.mock
async def test_recording_files_are_mapped_including_the_transcript_type() -> None:
    respx.get(f"{API}/meetings/987654321/recordings").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 987654321,
                "uuid": "abcd==",
                "topic": "Kickoff",
                "recording_count": 2,
                "recording_files": [
                    {
                        "id": "f1",
                        "file_type": "MP4",
                        "file_extension": "MP4",
                        "file_size": 7220,
                        "status": "completed",
                        "download_url": "https://us02web.zoom.us/rec/download/x",
                    },
                    {
                        "id": "f2",
                        "file_type": "TRANSCRIPT",
                        "file_extension": "VTT",
                        "status": "completed",
                        "download_url": "https://us02web.zoom.us/rec/download/y",
                    },
                ],
            },
        )
    )
    http, tokens = _client()
    async with http:
        meeting = await ZoomClient(http, tokens).list_recording_files("987654321")
    assert meeting.topic == "Kickoff"
    assert [f.file_type for f in meeting.recording_files] == ["MP4", "TRANSCRIPT"]


# --- meeting ids ---------------------------------------------------------------------------


@respx.mock
async def test_a_uuid_starting_with_a_slash_is_double_encoded() -> None:
    """Zoom's documented rule; a single encoding reaches the wrong route."""
    uuid = "/ajXp112QmuoKj4854875=="
    route = respx.get(url__startswith=f"{API}/meetings/").mock(
        return_value=httpx.Response(200, json={"id": 1, "topic": "x"})
    )
    http, tokens = _client()
    async with http:
        await ZoomClient(http, tokens).get_meeting(uuid)
    # The wire path is what Zoom receives: httpx keeps the double encoding in `raw_path`,
    # while `path` is the once-decoded form.
    raw = route.calls[0].request.url.raw_path.decode()
    assert "%252FajXp112QmuoKj4854875%253D%253D" in raw


async def test_an_id_that_is_not_a_meeting_id_is_refused() -> None:
    http, tokens = _client()
    async with http:
        with pytest.raises(ValueError):
            await ZoomClient(http, tokens).get_meeting("../../accounts")


# --- transcript ----------------------------------------------------------------------------


@respx.mock
async def test_the_transcript_endpoint_returns_metadata_not_text() -> None:
    respx.get(f"{API}/meetings/987654321/transcript").mock(
        return_value=httpx.Response(
            200,
            json={
                "meeting_id": "abcd==",
                "meeting_topic": "Kickoff",
                "transcript_created_time": "2026-11-09T13:48:24Z",
                "can_download": True,
                "download_url": "https://us02web.zoom.us/rec/meeting/transcript/download/x",
            },
        )
    )
    http, tokens = _client()
    async with http:
        meta = await ZoomClient(http, tokens).get_transcript("987654321")
    assert meta.can_download is True
    assert meta.meeting_topic == "Kickoff"
    assert meta.download_url.startswith("https://us02web.zoom.us/")


@respx.mock
async def test_an_undownloadable_transcript_carries_the_provider_reason() -> None:
    respx.get(f"{API}/meetings/1/transcript").mock(
        return_value=httpx.Response(
            200,
            json={
                "meeting_id": "1",
                "can_download": False,
                "download_restriction_reason": "NOT_READY",
            },
        )
    )
    http, tokens = _client()
    async with http:
        meta = await ZoomClient(http, tokens).get_transcript("1")
    assert meta.can_download is False
    assert meta.download_restriction_reason == "NOT_READY"


@respx.mock
async def test_zoom_code_3322_is_reported_as_no_transcript() -> None:
    respx.get(f"{API}/meetings/1/transcript").mock(
        return_value=httpx.Response(
            404, json={"code": 3322, "message": "This meeting transcript does not exist."}
        )
    )
    http, tokens = _client()
    async with http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await ZoomClient(http, tokens).get_transcript("1")
    assert "no transcript" in str(excinfo.value).lower()


@respx.mock
async def test_the_transcript_body_is_downloaded_with_the_bearer_token() -> None:
    url = "https://us02web.zoom.us/rec/meeting/transcript/download/x"
    route = respx.get(url).mock(
        return_value=httpx.Response(200, text="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n")
    )
    http, tokens = _client()
    async with http:
        text, truncated = await ZoomClient(http, tokens).fetch_transcript_text(url)
    assert text.startswith("WEBVTT")
    assert truncated is False
    assert route.calls[0].request.headers["authorization"] == "Bearer access-1"


@respx.mock
async def test_a_long_transcript_is_truncated_on_a_line_boundary() -> None:
    url = "https://us02web.zoom.us/rec/meeting/transcript/download/x"
    body = ("00:00:01.000 --> 00:00:02.000\n" + "word " * 20 + "\n") * 4000
    respx.get(url).mock(return_value=httpx.Response(200, text=body))
    http, tokens = _client()
    async with http:
        text, truncated = await ZoomClient(http, tokens).fetch_transcript_text(url)
    assert truncated is True
    assert len(text) <= 200_000
    # The cut is exactly a newline boundary: what remains is a prefix of the body whose next
    # character is the newline the slice dropped.
    assert body.startswith(text)
    assert body[len(text)] == "\n"


async def test_a_transcript_url_outside_zoom_is_never_fetched() -> None:
    """The URL comes from the provider, but a bearer token in front of a third party host would
    be a credential leak, so it is re-checked."""
    http, tokens = _client()
    async with http:
        with pytest.raises(ConnectorAPIError):
            await ZoomClient(http, tokens).fetch_transcript_text("https://evil.example.com/x")


# --- failure matrix ------------------------------------------------------------------------


@respx.mock
async def test_a_401_is_refreshed_exactly_once_then_succeeds() -> None:
    route = respx.get(USERS_ME).mock(
        side_effect=[
            httpx.Response(401, json={"code": 124, "message": "Invalid access token."}),
            httpx.Response(200, json={"id": "u", "email": "a@b.test"}),
        ]
    )
    tokens = Tokens()
    http = httpx.AsyncClient()
    async with http:
        user = await ZoomClient(http, tokens).get_current_user()
    assert user.email == "a@b.test"
    assert len(route.calls) == 2
    assert tokens.invalidated == 1


@respx.mock
async def test_a_401_that_persists_is_an_auth_error_and_is_not_retried_twice() -> None:
    route = respx.get(USERS_ME).mock(
        return_value=httpx.Response(401, json={"code": 124, "message": "Invalid access token."})
    )
    tokens = Tokens()
    http = httpx.AsyncClient()
    async with http:
        with pytest.raises(ConnectorAuthError):
            await ZoomClient(http, tokens).get_current_user()
    assert len(route.calls) == 2  # exactly one retry, never a storm on a rotating grant


@respx.mock
async def test_a_403_names_the_scope_problem() -> None:
    respx.get(USERS_ME).mock(
        return_value=httpx.Response(403, json={"code": 4711, "message": "missing scope"})
    )
    http, tokens = _client()
    async with http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await ZoomClient(http, tokens).get_current_user()
    assert "scope" in str(excinfo.value).lower()


@respx.mock
async def test_a_missing_scope_arrives_as_400_code_4711_not_403() -> None:
    """Measured against the live API: this is the shape Zoom actually returns, and reporting it as
    a bare HTTP 400 would send the user looking for a bug instead of reconnecting."""
    respx.get(USERS_ME).mock(
        return_value=httpx.Response(
            400,
            json={
                "code": 4711,
                "message": (
                    "Invalid access token, does not contain scopes:"
                    "[webinar:read:list_webinars:admin, webinar:read:list_webinars]"
                ),
            },
        )
    )
    http, tokens = _client()
    async with http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await ZoomClient(http, tokens).get_current_user()
    assert "scope" in str(excinfo.value).lower()
    assert "consent screen" in (excinfo.value.hint or "").lower()


@respx.mock
async def test_another_400_keeps_zooms_own_message() -> None:
    respx.get(USERS_ME).mock(
        return_value=httpx.Response(400, json={"code": 300, "message": "Validation Failed."})
    )
    http, tokens = _client()
    async with http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await ZoomClient(http, tokens).get_current_user()
    assert "Validation Failed" in str(excinfo.value)


@respx.mock
async def test_a_429_surfaces_retry_after() -> None:
    respx.get(USERS_ME).mock(
        return_value=httpx.Response(429, headers={"retry-after": "7"}, json={})
    )
    http, tokens = _client()
    async with http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await ZoomClient(http, tokens).get_current_user()
    assert "7" in (excinfo.value.hint or "")


@respx.mock
async def test_the_configured_api_base_is_used() -> None:
    route = respx.get("https://eu01api.zoom.us/v2/users/me").mock(
        return_value=httpx.Response(200, json={"id": "u", "email": "a@b.test"})
    )
    http = httpx.AsyncClient()
    async with http:
        await ZoomClient(http, Tokens(base="https://eu01api.zoom.us/v2")).get_current_user()
    assert len(route.calls) == 1
