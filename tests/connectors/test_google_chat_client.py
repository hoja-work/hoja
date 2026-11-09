"""The Chat half of the Google client: request shape, response mapping, pagination and the errors.

Response bodies are shaped after Google's Chat v1 discovery document, vendored in
`fixtures/chat_v1_discovery.json` and cross-checked by `test_google_chat_discovery.py`.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from google_workspace.client import CHAT_BASE, CHAT_PAGE_SIZE, GoogleWorkspaceClient
from google_workspace.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError

SPACE = "spaces/AAA111"
MESSAGE = "spaces/AAA111/messages/BBB222"


async def _client() -> tuple[httpx.AsyncClient, GoogleWorkspaceClient]:
    http = httpx.AsyncClient()
    return http, GoogleWorkspaceClient(http, "ya29.chat")


def _scope_error() -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "error": {
                "code": 403,
                "message": "Request had insufficient authentication scopes.",
                "status": "PERMISSION_DENIED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                    }
                ],
            }
        },
    )


def _disabled_error() -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "error": {
                "code": 403,
                "message": "Google Chat API has not been used in project 1044875551381 before or it is disabled.",
                "status": "PERMISSION_DENIED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                    }
                ],
            }
        },
    )


@respx.mock
async def test_spaces_are_listed_with_the_documented_page_size_and_authorization() -> None:
    route = respx.get(f"{CHAT_BASE}/spaces").mock(
        return_value=httpx.Response(
            200,
            json={
                "spaces": [
                    {
                        "name": SPACE,
                        "displayName": "Team room",
                        "spaceType": "SPACE",
                        "spaceUri": "https://chat.google.com/room/AAA111",
                        "lastActiveTime": "2026-11-08T10:00:00Z",
                        "membershipCount": {
                            "joinedDirectHumanUserCount": 7,
                            "joinedGroupCount": 2,
                        },
                    }
                ]
            },
        )
    )
    http, client = await _client()
    try:
        spaces = await client.chat_spaces(max_results=10)
    finally:
        await http.aclose()
    assert [s.name for s in spaces] == [SPACE]
    assert spaces[0].display_name == "Team room"
    assert spaces[0].membership_count is not None
    assert spaces[0].membership_count.joined_direct_human_user_count == 7
    assert spaces[0].membership_count.joined_group_count == 2
    sent = route.calls.last.request
    assert sent.url.params["pageSize"] == "10"
    assert "filter" not in sent.url.params
    assert sent.headers["Authorization"] == "Bearer ya29.chat"


@respx.mock
async def test_a_page_size_above_the_documented_maximum_is_capped_where_google_caps_it() -> None:
    route = respx.get(f"{CHAT_BASE}/spaces").mock(
        return_value=httpx.Response(200, json={"spaces": []})
    )
    http, client = await _client()
    try:
        await client.chat_spaces(max_results=99_999)
    finally:
        await http.aclose()
    assert route.calls.last.request.url.params["pageSize"] == str(CHAT_PAGE_SIZE["spaces"])


@respx.mock
async def test_pagination_walks_tokens_and_stops_on_a_repeated_one() -> None:
    """A repeated token is a server bug; trusting it would spin to the page cap."""
    route = respx.get(f"{CHAT_BASE}/spaces")
    route.side_effect = [
        httpx.Response(200, json={"spaces": [{"name": "spaces/AAA111"}], "nextPageToken": "p2"}),
        httpx.Response(200, json={"spaces": [{"name": "spaces/BBB222"}], "nextPageToken": "p2"}),
    ]
    http, client = await _client()
    try:
        spaces = await client.chat_spaces(max_results=10)
    finally:
        await http.aclose()
    assert [s.name for s in spaces] == ["spaces/AAA111", "spaces/BBB222"]
    assert route.call_count == 2


@respx.mock
async def test_messages_are_newest_first_only_when_asked_and_never_silently_reordered() -> None:
    route = respx.get(f"{CHAT_BASE}/{SPACE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    http, client = await _client()
    try:
        await client.chat_messages(SPACE)
    finally:
        await http.aclose()
    # The API default is create_time ASC. Sending nothing is what keeps that explicit and honest.
    assert "orderBy" not in route.calls.last.request.url.params


@respx.mock
async def test_messages_accept_the_two_documented_orderings_and_reject_anything_else() -> None:
    route = respx.get(f"{CHAT_BASE}/{SPACE}/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "name": MESSAGE,
                        "text": "Hello",
                        "argumentText": "Hello",
                        "createTime": "2026-11-08T10:00:00Z",
                        "thread": {"name": f"{SPACE}/threads/T1"},
                        "threadReply": False,
                        "sender": {
                            "name": "users/123",
                            "displayName": "Ada",
                            "type": "HUMAN",
                            "email": "ada@example.test",
                        },
                    }
                ]
            },
        )
    )
    http, client = await _client()
    try:
        messages = await client.chat_messages(SPACE, order_by="desc", max_results=5)
        with pytest.raises(ValueError, match="ASC or DESC"):
            await client.chat_messages(SPACE, order_by="newest")
    finally:
        await http.aclose()
    assert route.calls.last.request.url.params["orderBy"] == "DESC"
    assert messages[0].thread_name == f"{SPACE}/threads/T1"
    assert messages[0].sender is not None
    assert messages[0].sender.email == "ada@example.test"
    assert messages[0].thread_reply is False


@respx.mock
async def test_a_bare_space_id_is_accepted_but_a_path_traversal_is_not() -> None:
    route = respx.get(f"{CHAT_BASE}/{SPACE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    http, client = await _client()
    try:
        await client.chat_messages("AAA111")
        with pytest.raises(ValueError, match="Invalid Google Chat space"):
            await client.chat_messages("AAA111/../BBB222")
    finally:
        await http.aclose()
    assert str(route.calls.last.request.url.path) == f"/v1/{SPACE}/messages"


@respx.mock
async def test_one_message_is_read_by_its_resource_name() -> None:
    route = respx.get(f"{CHAT_BASE}/{MESSAGE}").mock(
        return_value=httpx.Response(200, json={"name": MESSAGE, "text": "Hi there"})
    )
    http, client = await _client()
    try:
        message = await client.chat_message(MESSAGE)
        with pytest.raises(ValueError, match="Invalid Google Chat message name"):
            await client.chat_message(f"{MESSAGE}/extra")
    finally:
        await http.aclose()
    assert message.text == "Hi there"
    assert str(route.calls.last.request.url.path) == f"/v1/{MESSAGE}"


@respx.mock
async def test_memberships_keep_person_and_group_apart_and_do_not_widen_the_result() -> None:
    route = respx.get(f"{CHAT_BASE}/{SPACE}/members").mock(
        return_value=httpx.Response(
            200,
            json={
                "memberships": [
                    {
                        "name": f"{SPACE}/members/123",
                        "role": "ROLE_MANAGER",
                        "state": "JOINED",
                        "affiliation": "INTERNAL",
                        "member": {"name": "users/123", "displayName": "Ada", "type": "HUMAN"},
                    },
                    {
                        "name": f"{SPACE}/members/group-1",
                        "state": "JOINED",
                        "groupMember": {"name": "groups/group-1"},
                    },
                ]
            },
        )
    )
    http, client = await _client()
    try:
        members = await client.chat_members(SPACE, max_results=10)
    finally:
        await http.aclose()
    assert members[0].member is not None and members[0].member.display_name == "Ada"
    assert members[0].group_member_name is None
    assert members[1].member is None
    assert members[1].group_member_name == "groups/group-1"
    params = route.calls.last.request.url.params
    # Both flags only widen the answer, so the connector must not send them by accident.
    assert "showInvited" not in params and "showGroups" not in params


@respx.mock
async def test_the_two_403s_are_told_apart_because_only_one_is_the_users_to_fix() -> None:
    http, client = await _client()
    try:
        respx.get(f"{CHAT_BASE}/spaces").mock(return_value=_disabled_error())
        with pytest.raises(ConnectorConfigError, match="Google Chat API is not enabled"):
            await client.chat_spaces()
        respx.get(f"{CHAT_BASE}/spaces").mock(return_value=_scope_error())
        with pytest.raises(ConnectorAuthError, match="has not granted Google Chat access"):
            await client.chat_spaces()
    finally:
        await http.aclose()


@respx.mock
async def test_a_401_asks_for_a_reconnect_and_a_404_names_the_resource() -> None:
    http, client = await _client()
    try:
        respx.get(f"{CHAT_BASE}/spaces").mock(
            return_value=httpx.Response(401, json={"error": {"code": 401}})
        )
        with pytest.raises(ConnectorAuthError, match="rejected the access token"):
            await client.chat_spaces()
        respx.get(f"{CHAT_BASE}/{MESSAGE}").mock(
            return_value=httpx.Response(404, json={"error": {"code": 404}})
        )
        with pytest.raises(ConnectorAPIError, match="was not found"):
            await client.chat_message(MESSAGE)
    finally:
        await http.aclose()


@respx.mock
async def test_an_unexpected_body_shape_is_an_error_not_an_empty_list() -> None:
    """Some Google list endpoints return `{}` for "nothing"; Chat returns the field as an empty
    list. A missing field is a different fact and must not be reported as "you have no spaces"."""
    respx.get(f"{CHAT_BASE}/spaces").mock(return_value=httpx.Response(200, json={}))
    http, client = await _client()
    try:
        assert await client.chat_spaces() == []
    finally:
        await http.aclose()
    respx.get(f"{CHAT_BASE}/spaces").mock(return_value=httpx.Response(200, json={"spaces": {}}))
    http, client = await _client()
    try:
        with pytest.raises(ConnectorAPIError, match="unexpected response shape"):
            await client.chat_spaces()
    finally:
        await http.aclose()
