"""Calendly REST client: OpenAPI shapes, cursor pagination, one-shot 401 recovery, tool scoping."""

from __future__ import annotations

import httpx
import pytest
import respx
from calendly.client import CalendlyClient
from calendly.errors import ConnectorAPIError, ConnectorAuthError
from calendly.tools import AccountGrant, list_event_types, list_invitees

API = "https://api.calendly.com"
USER_URI = f"{API}/users/U0000000000000000000000000000001"
EVENT_UUID = "1f2e3d4c-5b6a-4978-8899-aabbccddeeff"


class _Tokens:
    def __init__(self, token: str = "access-1") -> None:
        self.token = token
        self.invalidated = 0
        self.handed_out: list[str] = []

    async def access_token(self) -> str:
        self.handed_out.append(self.token)
        return self.token

    async def invalidate(self) -> None:
        self.invalidated += 1


class _Sequence(_Tokens):
    """Returns a different token once invalidated, like a real refreshing source."""

    async def invalidate(self) -> None:
        self.invalidated += 1
        self.token = "access-2"


def _user_payload() -> dict:
    return {
        "resource": {
            "uri": USER_URI,
            "name": "Luis",
            "email": "luis@hoja.work",
            "scheduling_url": "https://calendly.com/luis",
            "timezone": "Europe/Berlin",
            "current_organization": f"{API}/organizations/ORG1",
        }
    }


@respx.mock
async def test_get_current_user_maps_the_resource_envelope() -> None:
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, json=_user_payload()))
    async with httpx.AsyncClient() as http:
        user = await CalendlyClient(http, _Tokens()).get_current_user()
    assert user.uri == USER_URI
    assert user.email == "luis@hoja.work"
    assert user.organization == f"{API}/organizations/ORG1"


@respx.mock
async def test_list_event_types_walks_cursor_pages_and_caps_count_at_100() -> None:
    route = respx.get(f"{API}/event_types").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "collection": [
                        {
                            "uri": f"{API}/event_types/ET1",
                            "name": "Intro",
                            "active": True,
                            "duration": 30,
                        }
                    ],
                    "pagination": {"count": 1, "next_page_token": "page-2"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "collection": [
                        {"uri": f"{API}/event_types/ET2", "name": "Deep dive", "active": False}
                    ],
                    "pagination": {"count": 1, "next_page_token": None},
                },
            ),
        ]
    )
    async with httpx.AsyncClient() as http:
        items = await CalendlyClient(http, _Tokens()).list_event_types(
            user_uri=USER_URI, max_results=250
        )
    assert [item.name for item in items] == ["Intro", "Deep dive"]
    first_request = route.calls[0].request
    # OpenAPI: count has a maximum of 100, so a 250-result ask is split, never sent as 250.
    assert first_request.url.params["count"] == "100"
    assert first_request.url.params["user"] == USER_URI
    # The second page carries the cursor from the first.
    assert route.calls[1].request.url.params["page_token"] == "page-2"


@respx.mock
async def test_a_401_is_retried_exactly_once_with_a_fresh_token() -> None:
    respx.get(f"{API}/users/me").mock(
        side_effect=[
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json=_user_payload()),
        ]
    )
    tokens = _Sequence()
    async with httpx.AsyncClient() as http:
        user = await CalendlyClient(http, tokens).get_current_user()
    assert user.email == "luis@hoja.work"
    assert tokens.invalidated == 1
    assert tokens.handed_out == ["access-1", "access-2"]


@respx.mock
async def test_two_401s_in_a_row_are_a_grant_problem_not_a_retry_loop() -> None:
    route = respx.get(f"{API}/users/me").mock(
        return_value=httpx.Response(401, json={"message": "nope"})
    )
    tokens = _Sequence()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError):
            await CalendlyClient(http, tokens).get_current_user()
    assert len(route.calls) == 2  # original + one retry, never more
    assert tokens.invalidated == 1


@respx.mock
async def test_a_forbidden_response_points_at_scopes() -> None:
    respx.get(f"{API}/scheduled_events/{EVENT_UUID}").mock(
        return_value=httpx.Response(403, json={"message": "forbidden"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await CalendlyClient(http, _Tokens()).get_scheduled_event(EVENT_UUID)
    assert "scope" in (excinfo.value.hint or "").lower()


@respx.mock
async def test_a_rate_limited_call_reports_the_wait() -> None:
    respx.get(f"{API}/users/me").mock(
        return_value=httpx.Response(429, headers={"retry-after": "42"}, json={})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await CalendlyClient(http, _Tokens()).get_current_user()
    assert "42" in (excinfo.value.hint or "")


async def test_a_non_uuid_event_id_never_reaches_the_url() -> None:
    with pytest.raises(ValueError):
        await CalendlyClient(httpx.AsyncClient(), _Tokens()).get_scheduled_event(
            "../../organizations/ORG1"
        )


async def test_a_user_filter_must_be_a_calendly_uri() -> None:
    # The filter is what scopes a list to the consenting user; anything else is refused locally.
    with pytest.raises(ValueError):
        await CalendlyClient(httpx.AsyncClient(), _Tokens()).list_event_types(
            user_uri="https://evil.example/users/1"
        )


@respx.mock
async def test_scheduled_event_and_invitee_shapes() -> None:
    respx.get(f"{API}/scheduled_events/{EVENT_UUID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "resource": {
                    "uri": f"{API}/scheduled_events/{EVENT_UUID}",
                    "name": "Intro call",
                    "status": "active",
                    "start_time": "2026-11-10T09:00:00.000000Z",
                    "end_time": "2026-11-10T09:30:00.000000Z",
                    "event_type": f"{API}/event_types/ET1",
                    "location": {"type": "zoom", "join_url": "https://zoom.example/j/1"},
                    "invitees_counter": {"active": 2, "limit": 1},
                }
            },
        )
    )
    respx.get(f"{API}/scheduled_events/{EVENT_UUID}/invitees").mock(
        return_value=httpx.Response(
            200,
            json={
                "collection": [
                    {
                        "uri": f"{API}/invitees/INV1",
                        "email": "guest@example.com",
                        "name": "Guest",
                        "status": "active",
                        "timezone": "Europe/Berlin",
                    },
                    {
                        "uri": f"{API}/invitees/INV2",
                        "email": "gone@example.com",
                        "status": "canceled",
                    },
                ],
                "pagination": {"count": 2},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = CalendlyClient(http, _Tokens())
        event = await client.get_scheduled_event(EVENT_UUID)
        invitees = await client.list_invitees(EVENT_UUID)
    assert event.name == "Intro call"
    assert event.location == "https://zoom.example/j/1"
    assert event.invitees.active == 2
    assert [i.email for i in invitees] == ["guest@example.com", "gone@example.com"]
    assert [i.canceled for i in invitees] == [False, True]


@respx.mock
async def test_tools_scope_every_list_to_the_authorizing_user() -> None:
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, json=_user_payload()))
    scoped = respx.get(f"{API}/event_types").mock(
        return_value=httpx.Response(200, json={"collection": [], "pagination": {"count": 0}})
    )
    grant = AccountGrant(
        account_id="acct-1", external_account="luis@hoja.work", tokens=_Tokens()
    )
    async with httpx.AsyncClient() as http:
        await list_event_types(http, grant, max_results=5)
        with pytest.raises(ValueError):
            await list_invitees(http, grant, "not-a-uuid")
    assert scoped.calls[0].request.url.params["user"] == USER_URI
