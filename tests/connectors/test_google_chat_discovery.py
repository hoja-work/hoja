"""Check the Chat client against Google's own discovery document, not against my reading of it.

`fixtures/chat_v1_discovery.json` is a projection of
`https://chat.googleapis.com/$discovery/rest?version=v1`, produced by
`scripts/check_chat_discovery.py --refresh` and carrying the document revision and a content hash.
`--check` re-fetches and fails if Google moved the document, so the fixture cannot silently age.

This test can fail for a reason that is not my opinion:

- a path the client builds that the service does not document,
- a query parameter it sends that the method does not declare,
- a response field it reads that the schema does not contain,
- a scope it requests that the method does not accept,
- a page size above the maximum Google states in the document itself.

The bodies are empty on purpose: the shape is the subject, not the data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import respx
from google_workspace.client import (
    CHAT_DEFAULT_PAGE,
    CHAT_PAGE_SIZE,
    ChatMembership,
    ChatMembershipCount,
    ChatMessage,
    ChatSpace,
    ChatUser,
    GoogleWorkspaceClient,
)
from google_workspace.oauth import (
    CHAT_MEMBERSHIPS_SCOPE,
    CHAT_MESSAGES_SCOPE,
    CHAT_SCOPES,
    CHAT_SPACES_SCOPE,
    WRITE_SCOPES,
)

DISCOVERY = json.loads((Path(__file__).parent / "fixtures" / "chat_v1_discovery.json").read_text())

SPACE = "spaces/AAA111"
MESSAGE = f"{SPACE}/messages/BBB222"

# (projected method, concrete path after substituting the documented placeholders)
CALLS = [
    ("spaces.list", "/v1/spaces"),
    ("spaces.messages.list", f"/v1/{SPACE}/messages"),
    ("spaces.messages.get", f"/v1/{MESSAGE}"),
    ("spaces.members.list", f"/v1/{SPACE}/members"),
]

# The scope each call needs. Not interchangeable: Chat authorizes each resource family separately.
REQUIRED_SCOPE = {
    "spaces.list": CHAT_SPACES_SCOPE,
    "spaces.messages.list": CHAT_MESSAGES_SCOPE,
    "spaces.messages.get": CHAT_MESSAGES_SCOPE,
    "spaces.members.list": CHAT_MEMBERSHIPS_SCOPE,
}

# Every model field, mapped to the documented schema and field it is read from. A field added to a
# model without an entry here fails `test_every_model_field_is_traced_to_a_documented_field`, which
# is the point: the mapping is the claim being checked.
MODEL_FIELDS: dict[type, tuple[str, dict[str, str]]] = {
    ChatSpace: (
        "Space",
        {
            "name": "name",
            "display_name": "displayName",
            "space_type": "spaceType",
            "space_uri": "spaceUri",
            "last_active_time": "lastActiveTime",
            "membership_count": "membershipCount",
        },
    ),
    ChatMembershipCount: (
        "MembershipCount",
        {
            "joined_direct_human_user_count": "joinedDirectHumanUserCount",
            "joined_group_count": "joinedGroupCount",
        },
    ),
    ChatMessage: (
        "Message",
        {
            "name": "name",
            "text": "text",
            "argument_text": "argumentText",
            "create_time": "createTime",
            "last_update_time": "lastUpdateTime",
            "delete_time": "deleteTime",
            "thread_name": "thread.name",
            "thread_reply": "threadReply",
            "sender": "sender",
        },
    ),
    ChatMembership: (
        "Membership",
        {
            "name": "name",
            "role": "role",
            "state": "state",
            "create_time": "createTime",
            "affiliation": "affiliation",
            "member": "member",
            "group_member_name": "groupMember.name",
        },
    ),
    ChatUser: (
        "User",
        {
            "name": "name",
            "display_name": "displayName",
            "type": "type",
            "email": "email",
            "is_anonymous": "isAnonymous",
        },
    ),
}


def _concrete(flat_path: str) -> str:
    return "/" + flat_path.replace("{spacesId}", SPACE.split("/")[1]).replace(
        "{messagesId}", "BBB222"
    )


def _check_request(request: httpx.Request) -> None:
    """Raise unless this request is exactly something the discovery document allows."""
    documented = {_concrete(m["flatPath"]): m for m in DISCOVERY["methods"].values()}
    path = request.url.path
    assert path in documented, f"{path} is not a documented Chat path"
    method = documented[path]
    assert method["httpMethod"] == "GET", f"{path} is not a GET"
    assert "{" not in path, f"{path} still contains an unsubstituted placeholder"
    declared = {param["name"] for param in method["queryParams"]}
    for name in request.url.params:
        assert name in declared, f"{path} does not document the query parameter {name}"


async def _exercise() -> list[httpx.Request]:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    http = httpx.AsyncClient()
    client = GoogleWorkspaceClient(http, "ya29.chat")
    with respx.mock:
        route = respx.route(method="GET").mock(side_effect=_handler)
        try:
            await client.chat_spaces()
            await client.chat_messages(SPACE)
            await client.chat_message(MESSAGE)
            await client.chat_members(SPACE)
        finally:
            await http.aclose()
        return [call.request for call in route.calls]


# --- provenance ------------------------------------------------------------------------------


def test_the_fixture_records_where_it_came_from_and_what_it_was() -> None:
    assert DISCOVERY["source"] == "https://chat.googleapis.com/$discovery/rest?version=v1"
    assert re.fullmatch(r"\d{8}", DISCOVERY["revision"]), DISCOVERY["revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", DISCOVERY["documentCanonicalSha256"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", DISCOVERY["retrieved"])
    assert set(DISCOVERY["methods"]) == set(REQUIRED_SCOPE)


# --- the four checks -------------------------------------------------------------------------


async def test_the_client_only_calls_documented_paths_with_documented_parameters() -> None:
    requests = await _exercise()
    assert len(requests) == len(CALLS)
    for request in requests:
        _check_request(request)


async def test_every_path_is_exercised_so_a_new_endpoint_cannot_slip_past() -> None:
    documented = {_concrete(m["flatPath"]) for m in DISCOVERY["methods"].values()}
    exercised = {request.url.path for request in await _exercise()}
    assert documented == exercised


def test_every_documented_method_accepts_the_read_only_scope_we_request() -> None:
    for name, scope in REQUIRED_SCOPE.items():
        method = DISCOVERY["methods"][name]
        assert method["httpMethod"] == "GET", name
        assert scope in method["scopes"], name
        assert scope.endswith(".readonly"), name
        # The three scopes are not interchangeable, so each must be the one for its method.
        assert scope not in WRITE_SCOPES.values()


def test_the_three_scopes_are_exactly_the_ones_the_four_methods_need() -> None:
    accepted = set()
    for method in DISCOVERY["methods"].values():
        accepted.update(s for s in method["scopes"] if s.endswith(".readonly"))
    # Every scope we ask for is used by at least one method we call.
    assert set(CHAT_SCOPES) == {CHAT_SPACES_SCOPE, CHAT_MESSAGES_SCOPE, CHAT_MEMBERSHIPS_SCOPE}
    for scope in CHAT_SCOPES:
        assert scope in accepted, f"{scope} is accepted by no method this connector calls"


PAGE_KEY = {
    "spaces.list": "spaces",
    "spaces.messages.list": "messages",
    "spaces.members.list": "members",
}


def test_page_sizes_match_the_numbers_google_states_in_the_document() -> None:
    """The default and maximum are only stated in prose, so they are parsed out of the document
    rather than copied into a constant by hand."""
    assert set(DISCOVERY["pageSizeNotes"]) == set(PAGE_KEY)
    for name, note in DISCOVERY["pageSizeNotes"].items():
        default = int(re.search(r"at most (\d+)", note).group(1))
        maximum = int(re.search(r"maximum value is (\d+)", note).group(1))
        assert CHAT_DEFAULT_PAGE[PAGE_KEY[name]] == default, (name, note)
        assert CHAT_PAGE_SIZE[PAGE_KEY[name]] == maximum, (name, note)


async def test_the_client_sends_a_page_size_within_the_documented_maximum() -> None:
    by_path = {_concrete(DISCOVERY["methods"][name]["flatPath"]): name for name in PAGE_KEY}
    lists = 0
    for request in await _exercise():
        name = by_path.get(request.url.path)
        if name is None:
            # spaces.messages.get returns one message and documents no page size.
            assert "pageSize" not in request.url.params
            continue
        lists += 1
        sent = int(request.url.params["pageSize"])
        assert 0 < sent <= CHAT_PAGE_SIZE[PAGE_KEY[name]], (name, sent)
    assert lists == len(PAGE_KEY)


def test_every_model_field_is_traced_to_a_documented_field() -> None:
    for model, (schema_name, mapping) in MODEL_FIELDS.items():
        assert schema_name in DISCOVERY["schemas"], schema_name
        properties = DISCOVERY["schemas"][schema_name]["properties"]
        # Both directions: no model field without a documented source, and no forgotten field.
        assert set(mapping) == set(model.model_fields), model.__name__
        for field, source in mapping.items():
            parts = source.split(".")
            assert parts[0] in properties, f"{schema_name} has no field {parts[0]} ({field})"
            if len(parts) > 1:
                ref = properties[parts[0]].get("$ref")
                assert ref in DISCOVERY["schemas"], f"{schema_name}.{parts[0]} -> {ref}"
                assert parts[1] in DISCOVERY["schemas"][ref]["properties"], source


def test_the_response_schemas_are_the_ones_the_methods_declare() -> None:
    expected = {
        "spaces.list": "ListSpacesResponse",
        "spaces.messages.list": "ListMessagesResponse",
        "spaces.messages.get": "Message",
        "spaces.members.list": "ListMembershipsResponse",
    }
    for name, schema in expected.items():
        assert DISCOVERY["methods"][name]["response"] == schema
        assert schema in DISCOVERY["schemas"]


# --- negative control ------------------------------------------------------------------------


def test_the_checker_rejects_an_undocumented_path_and_parameter() -> None:
    """Without this, a green conformance test could mean the checker never checks anything."""
    _check_request(httpx.Request("GET", f"https://chat.googleapis.com/v1/{SPACE}/messages"))

    with pytest.raises(AssertionError, match="not a documented Chat path"):
        _check_request(
            httpx.Request("GET", f"https://chat.googleapis.com/v1/{SPACE}/messages/BBB222/replies")
        )
    with pytest.raises(AssertionError, match="does not document the query parameter view"):
        _check_request(httpx.Request("GET", "https://chat.googleapis.com/v1/spaces?view=full"))
