"""The Zoom tools on the one shared connector MCP server.

The agent gets a URL and a signed principal from `connector_binding_for`; this is the process that
must answer on that URL. It resolves the accounts a principal may use and refreshes the one-hour
Zoom access token on use, so a stale token is never a failure.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from google_workspace.mcp_server import PRINCIPAL_HEADER
from google_workspace.principal import sign_principal

from hoja_server.connector_mcp_service import build_connector_mcp_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import encrypt_secret

KEY = "service-test-signing-key"
TOKEN_URL = "https://zoom.us/oauth/token"
USERS_ME = "https://api.zoom.us/v2/users/me"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    monkeypatch.setenv("HOJA_ZOOM_CLIENT_ID", "5kXHQtScS8WhD2lgmuedKQ")
    monkeypatch.setenv("HOJA_ZOOM_CLIENT_SECRET", "zoom-service-test-secret")
    monkeypatch.setenv(
        "HOJA_ZOOM_REDIRECT_URI", "https://hoja.example.test/api/connectors/zoom/callback"
    )
    # The shared server also serves Google; its config is validated when the app is built.
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-service-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )


@pytest.fixture
def seeded(tmp_path):
    engine = make_engine(tmp_path / "service.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Service Org",
        org_url_key="service-org",
        user_email="owner@service.test",
        token="service-token",
        demo_issues=False,
    )
    return engine, admin


def _add_zoom_account(engine, org: str, *, owner: str | None, email: str = "luis@hoja.work") -> None:
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_zoom_service",
                organization_id=org,
                owner_user_id=owner,
                provider="zoom",
                external_account=email,
                scopes="user:read:user",
                encrypted_refresh_token=encrypt_secret("stored-refresh"),
                # Expired on purpose: the grant must refresh on use.
                encrypted_access_token=encrypt_secret("stale-access"),
                access_token_expires_at="0",
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


async def _rpc(app, payload: dict, principal: str | None) -> httpx.Response:
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if principal is not None:
        headers[PRINCIPAL_HEADER] = principal
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
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
        return await client.post("/mcp", headers=headers, json=payload)


def _data(response: httpx.Response) -> dict:
    """The JSON-RPC body, whether it arrived as JSON or as one SSE `data:` frame."""
    if response.text.lstrip().startswith("{"):
        return json.loads(response.text)
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no JSON-RPC payload in response: {response.text[:200]}")


def _principal(admin, scope: str = "read") -> str:
    return sign_principal(admin["user_id"], admin["org_id"], scope, key=KEY)


@respx.mock
async def test_the_six_zoom_tools_are_listed_on_the_shared_server(seeded) -> None:
    engine, admin = seeded
    _add_zoom_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, _principal(admin)
    )
    names = {tool["name"] for tool in _data(response)["result"]["tools"]}
    assert {
        "zoom_get_current_user",
        "zoom_list_meetings",
        "zoom_get_meeting",
        "zoom_list_cloud_recordings",
        "zoom_list_recording_files",
        "zoom_get_meeting_transcript",
    } <= names


@respx.mock
async def test_a_signed_principal_reads_the_user_with_a_refreshed_token(seeded) -> None:
    engine, admin = seeded
    _add_zoom_account(engine, admin["org_id"], owner=admin["user_id"])
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "fresh-refresh",
                "token_type": "bearer",
                "expires_in": 3600,
                "scope": "user:read:user",
            },
        )
    )
    me_route = respx.get(USERS_ME).mock(
        return_value=httpx.Response(
            200, json={"id": "u1", "email": "luis@hoja.work", "first_name": "Luis", "last_name": "R"}
        )
    )
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "zoom_get_current_user", "arguments": {}},
        },
        _principal(admin),
    )
    payload = _data(response)
    assert "error" not in payload
    assert "luis@hoja.work" in json.dumps(payload["result"])
    assert len(token_route.calls) == 1
    # The refreshed token is what the API call carried.
    assert me_route.calls[0].request.headers["authorization"] == "Bearer fresh-access"


@respx.mock
async def test_a_call_without_a_principal_is_refused(seeded) -> None:
    engine, admin = seeded
    _add_zoom_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "zoom_get_current_user", "arguments": {}},
        },
        None,
    )
    payload = _data(response)
    assert "error" in payload or payload["result"].get("isError") is True


@respx.mock
async def test_another_users_account_is_not_visible(seeded) -> None:
    """A personal grant is personal: a colleague's token must resolve to no account."""
    engine, admin = seeded
    _add_zoom_account(engine, admin["org_id"], owner="someone-else")
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "zoom_get_current_user", "arguments": {}},
        },
        _principal(admin),
    )
    payload = _data(response)
    assert "error" in payload or payload["result"].get("isError") is True
