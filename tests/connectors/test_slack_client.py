"""Read-only Slack client: the 200 error envelope, cursor paging, and token hygiene."""

from __future__ import annotations

import httpx
import pytest
import respx
from slack.client import API_BASE, SlackClient, revoke
from slack.errors import ConnectorAPIError, ConnectorAuthError

TOKEN = "xoxp-fixture"


def _client() -> SlackClient:
    return SlackClient(httpx.AsyncClient(), TOKEN)


@respx.mock
async def test_http_200_with_ok_false_means_reconnect() -> None:
    """The measured live shape for a dead token on every method: 200 + ok:false."""
    route = respx.get(f"{API_BASE}conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await SlackClient(http, TOKEN).list_channels()
    assert "Reconnect" in (excinfo.value.hint or "")
    assert route.call_count == 1


@respx.mock
async def test_missing_scope_names_the_scope_slack_asked_for() -> None:
    respx.get(f"{API_BASE}conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": False,
                "error": "missing_scope",
                "needed": "groups:history",
                "provided": "channels:history",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await SlackClient(http, TOKEN).read_channel_history("C0123ABCD")
    assert "groups:history" in (excinfo.value.hint or "")
    assert "channels:history" in (excinfo.value.hint or "")


@respx.mock
async def test_rate_limit_surfaces_the_retry_after_header() -> None:
    respx.get(f"{API_BASE}users.list").mock(
        return_value=httpx.Response(429, headers={"retry-after": "3"}, json={})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await SlackClient(http, TOKEN).lookup_users()
    assert "3 seconds" in (excinfo.value.hint or "")


@respx.mock
async def test_list_channels_walks_the_cursor_and_caps_the_result() -> None:
    route = respx.get(f"{API_BASE}conversations.list")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {
                        "id": "C1",
                        "name": "general",
                        "is_channel": True,
                        "is_member": True,
                        "num_members": 12,
                        "topic": {"value": "Everyone"},
                    },
                    {"id": "G1", "name": "secret", "is_private": True, "is_member": False},
                ],
                "response_metadata": {"next_cursor": "page-2"},
            },
        ),
        httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [{"id": "C2", "name": "eng", "is_member": True}],
                "response_metadata": {"next_cursor": ""},
            },
        ),
    ]
    async with httpx.AsyncClient() as http:
        channels = await SlackClient(http, TOKEN).list_channels(max_results=10)
    assert [c.name for c in channels] == ["general", "secret", "eng"]
    assert channels[1].is_private is True
    assert channels[0].topic == "Everyone"
    first, second = route.calls[0].request, route.calls[1].request
    assert first.url.params["types"] == "public_channel,private_channel"
    assert "cursor" not in first.url.params
    assert second.url.params["cursor"] == "page-2"


@respx.mock
async def test_the_token_travels_in_the_header_and_never_in_the_url() -> None:
    route = respx.get(f"{API_BASE}users.list").mock(
        return_value=httpx.Response(200, json={"ok": True, "members": []})
    )
    async with httpx.AsyncClient() as http:
        await SlackClient(http, TOKEN).lookup_users()
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)


@respx.mock
async def test_history_returns_text_and_honours_the_window() -> None:
    route = respx.get(f"{API_BASE}conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "ts": "1762700000.000100",
                        "user": "U1",
                        "text": "Deploy is green",
                        "thread_ts": "1762699999.000001",
                        "reply_count": 2,
                    }
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        messages = await SlackClient(http, TOKEN).read_channel_history(
            "C0123ABCD", max_results=5, oldest="1762600000.000000"
        )
    assert messages[0].text == "Deploy is green"
    assert messages[0].channel_id == "C0123ABCD"
    assert messages[0].reply_count == 2
    params = route.calls.last.request.url.params
    assert params["channel"] == "C0123ABCD"
    assert params["oldest"] == "1762600000.000000"
    assert params["limit"] == "5"


async def test_an_invalid_channel_id_is_rejected_before_a_request() -> None:
    with pytest.raises(ValueError):
        await _client().read_channel_history("general")
    with pytest.raises(ValueError):
        await _client().read_channel_history("C1&token=leak")
    with pytest.raises(ValueError):
        await _client().read_channel_history("C1", oldest="yesterday")


@respx.mock
async def test_search_pages_by_number_and_keeps_the_nested_channel() -> None:
    route = respx.get(f"{API_BASE}search.messages")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "ok": True,
                "messages": {
                    "total": 3,
                    "paging": {"count": 2, "page": 1, "pages": 2},
                    "matches": [
                        {
                            "ts": "1762700000.000100",
                            "text": "ship it",
                            "channel": {"id": "C1", "name": "eng"},
                            "permalink": "https://hoja-hq.slack.com/archives/C1/p1",
                        }
                    ],
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "ok": True,
                "messages": {
                    "total": 3,
                    "paging": {"count": 2, "page": 2, "pages": 2},
                    "matches": [{"ts": "1762700001.000100", "text": "done", "channel": {"id": "C2"}}],
                },
            },
        ),
    ]
    async with httpx.AsyncClient() as http:
        found = await SlackClient(http, TOKEN).search_messages("ship", max_results=2)
    assert found.total == 3
    assert [m.text for m in found.messages] == ["ship it", "done"]
    assert found.messages[0].channel_name == "eng"
    assert found.messages[0].permalink.endswith("/p1")
    assert route.calls[0].request.url.params["page"] == "1"


async def test_an_empty_search_query_is_refused_before_a_request() -> None:
    with pytest.raises(ValueError):
        await _client().search_messages("   ")
    with pytest.raises(ValueError):
        await _client().search_messages("x", sort="relevance")


@respx.mock
async def test_lookup_users_filters_locally_on_name_and_handle() -> None:
    respx.get(f"{API_BASE}users.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "members": [
                    {
                        "id": "U1",
                        "name": "ana",
                        "real_name": "Ana Silva",
                        "profile": {"display_name": "ana"},
                        "is_bot": False,
                    },
                    {"id": "U2", "name": "deploy-bot", "is_bot": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        everyone = await SlackClient(http, TOKEN).lookup_users()
        filtered = await SlackClient(http, TOKEN).lookup_users(query="silva")
    assert [u.id for u in everyone] == ["U1", "U2"]
    assert [u.id for u in filtered] == ["U1"]
    assert everyone[1].is_bot is True


@respx.mock
async def test_a_filtered_user_lookup_pages_past_the_first_hundred() -> None:
    """A workspace bigger than one page must not answer "no such person" for a match on page 2."""
    directory = [{"id": f"U{i}", "name": f"user{i}"} for i in range(150)]
    route = respx.get(f"{API_BASE}users.list")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "ok": True,
                "members": directory[:100],
                "response_metadata": {"next_cursor": "page-2"},
            },
        ),
        httpx.Response(
            200,
            json={
                "ok": True,
                "members": directory[100:],
                "response_metadata": {"next_cursor": ""},
            },
        ),
    ]
    async with httpx.AsyncClient() as http:
        found = await SlackClient(http, TOKEN).lookup_users(query="user120", max_results=5)
    assert [u.id for u in found] == ["U120"]
    assert route.call_count == 2


@respx.mock
async def test_revoke_reports_success_only_when_slack_confirms() -> None:
    route = respx.post(f"{API_BASE}auth.revoke").mock(
        return_value=httpx.Response(200, json={"ok": True, "revoked": True})
    )
    async with httpx.AsyncClient() as http:
        assert await revoke(http, TOKEN) is True
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_revoke_treats_an_already_invalid_token_as_revoked() -> None:
    respx.post(f"{API_BASE}auth.revoke").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )
    async with httpx.AsyncClient() as http:
        assert await revoke(http, TOKEN) is True


@respx.mock
async def test_revoke_does_not_claim_success_on_an_unknown_error() -> None:
    respx.post(f"{API_BASE}auth.revoke").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "fatal_error"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError):
            await revoke(http, TOKEN)
