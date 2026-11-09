"""The Meet tools must exist on the same server the agent is already pointed at.

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
from google_workspace.oauth import MEET_SCOPE
from google_workspace.principal import sign_principal

from hoja_server.connector_mcp_service import build_connector_mcp_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import encrypt_secret

KEY = "meet-mcp-test-signing-key"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MEET_LIST = "https://meet.googleapis.com/v2/conferenceRecords"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-meet-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )


@pytest.fixture
def seeded(tmp_path):
    engine = make_engine(tmp_path / "meet-mcp.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Meet MCP Org",
        org_url_key="meet-mcp-org",
        user_email="owner@meet.test",
        token="meet-token",
        demo_issues=False,
    )
    return engine, admin


def _add_google_account(engine, org: str, *, owner: str | None, scopes: str, email=None):
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_meet",
                organization_id=org,
                owner_user_id=owner,
                provider="google_workspace",
                external_account=email or "owner@meet.test",
                scopes=scopes,
                encrypted_refresh_token=encrypt_secret("1//STORED-REFRESH"),
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


async def _rpc_all(app, calls, principal):
    """One lifespan for several JSON-RPC calls: the streamable HTTP session manager can run once."""
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


def _principal(admin, org: str | None = None) -> str:
    return sign_principal(admin["user_id"], org or admin["org_id"], "read", key=KEY)


async def test_the_meet_tools_are_on_the_shared_server_and_all_read(seeded) -> None:
    engine, admin = seeded
    body = await _rpc_all(_app(engine), [("tools/list", {})], _principal(admin))
    tools = _payload(body[0])["result"]["tools"]
    meet = sorted(tool["name"] for tool in tools if tool["name"].startswith("meet_"))
    assert meet == [
        "meet_list_conference_records",
        "meet_list_participants",
        "meet_list_recordings",
        "meet_list_transcripts",
        "meet_read_transcript",
    ]
    for tool in tools:
        if not tool["name"].startswith("meet_"):
            continue
        assert not any(
            verb in tool["name"]
            for verb in ("create", "post", "write", "delete", "update", "end", "patch")
        )
        # No tool may name a user: the grant comes from the signed principal, not from arguments.
        assert "user_id" not in (tool.get("inputSchema") or {}).get("properties", {})
        assert "account" in (tool.get("inputSchema") or {}).get("properties", {})


@respx.mock
async def test_a_meet_call_refreshes_the_google_token_and_reaches_the_api(seeded) -> None:
    engine, admin = seeded
    _add_google_account(engine, admin["org_id"], owner=admin["user_id"], scopes=MEET_SCOPE)
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.FRESH-MEET",
                "expires_in": 3599,
                "token_type": "Bearer",
                "scope": MEET_SCOPE,
            },
        )
    )
    meet_route = respx.get(MEET_LIST).mock(
        return_value=httpx.Response(
            200,
            json={
                "conferenceRecords": [
                    {"name": "conferenceRecords/rec-1", "startTime": "2026-11-08T10:00:00Z"}
                ]
            },
        )
    )
    body = await _rpc_all(
        _app(engine),
        [("tools/call", {"name": "meet_list_conference_records", "arguments": {"max_results": 5}})],
        _principal(admin),
    )
    assert '"isError":true' not in body[0]
    assert "conferenceRecords/rec-1" in body[0]
    assert token_route.call_count == 1
    assert meet_route.calls.last.request.headers["Authorization"] == "Bearer ya29.FRESH-MEET"


@respx.mock
async def test_an_account_from_another_org_cannot_read_meetings(seeded) -> None:
    engine, admin = seeded
    _add_google_account(engine, admin["org_id"], owner=admin["user_id"], scopes=MEET_SCOPE)
    meet_route = respx.get(MEET_LIST).mock(
        return_value=httpx.Response(200, json={"conferenceRecords": []})
    )
    body = await _rpc_all(
        _app(engine),
        [("tools/call", {"name": "meet_list_conference_records", "arguments": {}})],
        sign_principal(admin["user_id"], "another-org", "read", key=KEY),
    )
    assert "No connected Google account" in body[0]
    assert meet_route.call_count == 0


@respx.mock
async def test_a_grant_without_meet_access_is_a_tool_error_the_agent_can_relay(seeded) -> None:
    """The MCP layer otherwise prints only 'Error executing tool'. A user whose grant lacks the
    scope has to be told to add it."""
    engine, admin = seeded
    _add_google_account(engine, admin["org_id"], owner=admin["user_id"], scopes="")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.FRESH", "expires_in": 3599, "token_type": "Bearer"}
        )
    )
    respx.get(MEET_LIST).mock(
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
        [("tools/call", {"name": "meet_list_conference_records", "arguments": {}})],
        _principal(admin),
    )
    assert '"isError":true' in body[0]
    assert "Google Meet access" in body[0]
    assert "Settings &gt; Connections" in body[0] or "Settings > Connections" in body[0]
