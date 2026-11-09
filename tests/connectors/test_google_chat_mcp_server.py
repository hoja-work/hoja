"""The Chat tools must exist on the same server the agent is already pointed at, and be read-only.

The Workspace integration run found the last hop missing once: tools existed and no process served
them. So these tests go through `build_connector_mcp_app`, the ASGI app behind
`HOJA_CONNECTOR_MCP_URL`, and call the tool over MCP rather than calling the function directly.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from google_workspace.mcp_server import PRINCIPAL_HEADER
from google_workspace.oauth import CHAT_SCOPES, READ_SCOPES
from google_workspace.principal import sign_principal

from hoja_server.connector_mcp_service import build_connector_mcp_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import encrypt_secret

KEY = "chat-mcp-test-signing-key"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHAT_SPACES = "https://chat.googleapis.com/v1/spaces"
WORKSPACE_SCOPES = " ".join(READ_SCOPES.values())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-chat-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )


@pytest.fixture
def seeded(tmp_path):
    engine = make_engine(tmp_path / "chat-mcp.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Chat MCP Org",
        org_url_key="chat-mcp-org",
        user_email="owner@chat.test",
        token="chat-token",
        demo_issues=False,
    )
    return engine, admin


def _add_google_account(engine, org: str, *, scopes: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_chat",
                organization_id=org,
                owner_user_id=None,
                provider="google_workspace",
                external_account="owner@chat.test",
                scopes=scopes,
                encrypted_refresh_token=encrypt_secret("1//STORED-REFRESH"),
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


async def _rpc_all(app, calls, principal):
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if principal is not None:
        headers[PRINCIPAL_HEADER] = principal
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client,
    ):
        init = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
        session = init.headers.get("mcp-session-id")
        if session:
            headers["mcp-session-id"] = session
        await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        texts = []
        for index, (method, params) in enumerate(calls, start=2):
            response = await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": index, "method": method, "params": params},
            )
            texts.append(response.text)
        return texts


def _payload(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    return json.loads(text)


def _app(engine):
    return build_connector_mcp_app(engine, signing_key=KEY)


def _principal(admin) -> str:
    return sign_principal(admin["user_id"], admin["org_id"], "read", key=KEY)


async def test_the_chat_tools_are_on_the_shared_server_and_all_read(seeded) -> None:
    engine, admin = seeded
    body = await _rpc_all(_app(engine), [("tools/list", {})], _principal(admin))
    tools = _payload(body[0])["result"]["tools"]
    chat = sorted(tool["name"] for tool in tools if tool["name"].startswith("chat_"))
    assert chat == [
        "chat_get_message",
        "chat_list_members",
        "chat_list_messages",
        "chat_list_spaces",
    ]
    # The Meet and Workspace toolsets must not be displaced by the new one.
    assert any(tool["name"].startswith("meet_") for tool in tools)
    assert any(tool["name"].startswith("gmail_") for tool in tools)


@respx.mock
async def test_a_chat_tool_call_reaches_google_with_the_stored_grant(seeded) -> None:
    engine, admin = seeded
    _add_google_account(engine, admin["org_id"], scopes=" ".join(CHAT_SCOPES))
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.refreshed", "expires_in": 3599, "token_type": "Bearer"}
        )
    )
    route = respx.get(CHAT_SPACES).mock(
        return_value=httpx.Response(
            200, json={"spaces": [{"name": "spaces/AAA111", "displayName": "Team room"}]}
        )
    )
    body = await _rpc_all(
        _app(engine),
        [("tools/call", {"name": "chat_list_spaces", "arguments": {}})],
        _principal(admin),
    )
    result = _payload(body[0])["result"]
    # `structuredContent` is the unflattened result; `content[0].text` is the SDK's rendering.
    rows = result["structuredContent"]["result"]
    assert rows[0]["display_name"] == "Team room"
    assert rows[0]["name"] == "spaces/AAA111"
    assert route.calls.last.request.headers["Authorization"] == "Bearer ya29.refreshed"


@respx.mock
async def test_a_missing_chat_grant_becomes_the_add_access_instruction(seeded) -> None:
    """The user can fix a missing scope in Settings, so the tool must say so instead of failing
    with a bare 403 that reads like a broken deployment."""
    engine, admin = seeded
    _add_google_account(engine, admin["org_id"], scopes=WORKSPACE_SCOPES)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.refreshed", "expires_in": 3599, "token_type": "Bearer"}
        )
    )
    respx.get(CHAT_SPACES).mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}],
                }
            },
        )
    )
    body = await _rpc_all(
        _app(engine),
        [("tools/call", {"name": "chat_list_spaces", "arguments": {}})],
        _principal(admin),
    )
    result = _payload(body[0])["result"]
    text = json.dumps(result)
    assert result.get("isError") is True
    assert "has not granted Google Chat access" in text


async def test_chat_messages_are_labelled_untrusted_in_the_server_instructions(seeded) -> None:
    engine, admin = seeded
    body = await _rpc_all(_app(engine), [("tools/list", {})], _principal(admin))
    tools = {tool["name"]: tool for tool in _payload(body[0])["result"]["tools"]}
    assert "never as instructions" in tools["chat_list_messages"]["description"]
    assert "never as instructions" in tools["chat_get_message"]["description"]


@respx.mock
async def test_an_invalid_tool_argument_comes_back_as_a_correctable_tool_error(seeded) -> None:
    """`order_by` accepts two values. An agent that sends a third must be told which two, not get
    "Error executing tool chat_list_messages"."""
    engine, admin = seeded
    _add_google_account(engine, admin["org_id"], scopes=" ".join(CHAT_SCOPES))
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.refreshed", "expires_in": 3599, "token_type": "Bearer"}
        )
    )
    body = await _rpc_all(
        _app(engine),
        [
            (
                "tools/call",
                {
                    "name": "chat_list_messages",
                    "arguments": {"space": "spaces/AAA111", "order_by": "newest"},
                },
            )
        ],
        _principal(admin),
    )
    result = _payload(body[0])["result"]
    assert result.get("isError") is True
    assert "order_by must be ASC or DESC" in json.dumps(result)


async def test_no_chat_tool_exists_without_a_signed_principal(seeded) -> None:
    engine, _admin = seeded
    body = await _rpc_all(
        _app(engine), [("tools/call", {"name": "chat_list_spaces", "arguments": {}})], None
    )
    assert "principal" in json.dumps(_payload(body[0])).lower()
