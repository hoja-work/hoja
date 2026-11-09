"""The service behind HOJA_CONNECTOR_MCP_URL.

The agent gets a URL and a signed principal from `connector_binding_for`; this is the process
that must answer on that URL. It resolves the accounts a principal may use from the database
and refreshes the Google access token on use, so a stale access token is never a failure.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from google_workspace.errors import ConnectorConfigError
from google_workspace.mcp_server import PRINCIPAL_HEADER
from google_workspace.principal import sign_principal

from hoja_server.connector_mcp_service import build_connector_mcp_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import encrypt_secret

KEY = "service-test-signing-key"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
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


def _add_account(
    engine,
    org: str,
    *,
    account_id: str = "acct_service",
    owner: str | None,
    email: str = "owner@service.test",
    refresh: str = "1//STORED-REFRESH",
    status: str = "active",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=account_id,
                organization_id=org,
                owner_user_id=owner,
                provider="google_workspace",
                external_account=email,
                scopes="https://www.googleapis.com/auth/gmail.readonly",
                encrypted_refresh_token=encrypt_secret(refresh) if refresh else "",
                status=status,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


async def _call(app, tool: str, arguments: dict, principal: str | None) -> str:
    """One MCP tools/call over streamable HTTP, the way the agent reaches the connector."""
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
        response = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        return response.text


def _principal(admin, scope: str = "read", org: str | None = None) -> str:
    return sign_principal(
        admin["user_id"], org or admin["org_id"], scope, key=KEY
    )


@respx.mock
async def test_signed_principal_searches_gmail_with_a_refreshed_token(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner=admin["user_id"])
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.FRESH",
                "expires_in": 3599,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
        )
    )
    list_route = respx.get(GMAIL_LIST_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]})
    )
    respx.get(f"{GMAIL_LIST_URL}/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "snippet": "hello",
                "payload": {"headers": [{"name": "Subject", "value": "Quarterly plan"}]},
            },
        )
    )
    app = build_connector_mcp_app(engine)
    body = await _call(app, "gmail_search", {"query": "plan"}, _principal(admin))

    assert "Quarterly plan" in body
    form = dict(
        pair.split("=", 1) for pair in token_route.calls.last.request.content.decode().split("&")
    )
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "1%2F%2FSTORED-REFRESH"
    assert list_route.calls.last.request.headers["authorization"] == "Bearer ya29.FRESH"


async def test_missing_signing_key_fails_closed(seeded, monkeypatch) -> None:
    engine, _admin = seeded
    monkeypatch.delenv("HOJA_CONNECTOR_SIGNING_KEY", raising=False)
    with pytest.raises(ConnectorConfigError):
        build_connector_mcp_app(engine)


async def test_principal_from_another_org_sees_no_account(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine)
    body = await _call(app, "gmail_search", {"query": "x"}, _principal(admin, org="other-org"))
    assert "No connected Google account" in body


async def test_revoked_account_is_not_offered(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner=admin["user_id"], status="revoked", refresh="")
    app = build_connector_mcp_app(engine)
    body = await _call(app, "gmail_search", {"query": "x"}, _principal(admin))
    assert "No connected Google account" in body


async def test_unreadable_stored_token_is_skipped_not_fatal(seeded) -> None:
    engine, admin = seeded
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_broken",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="google_workspace",
                external_account="broken@service.test",
                scopes="s",
                encrypted_refresh_token="not-a-sealed-value",
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
    app = build_connector_mcp_app(engine)
    body = await _call(app, "gmail_search", {"query": "x"}, _principal(admin))
    assert "No connected Google account" in body


async def test_two_accounts_require_an_explicit_account(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], account_id="a1", owner=admin["user_id"], email="a@x.test")
    _add_account(engine, admin["org_id"], account_id="a2", owner=admin["user_id"], email="b@x.test")
    app = build_connector_mcp_app(engine)
    body = await _call(app, "gmail_search", {"query": "x"}, _principal(admin))
    assert "Several accounts are connected" in body
    assert "a@x.test" in body and "b@x.test" in body


def test_cli_refuses_to_serve_without_a_signing_key(tmp_path, monkeypatch, capsys) -> None:
    """The deploy path is `hoja-serve connector-mcp`; a deployment that forgot the key must stop at
    startup with a message naming the variable, not serve tools nobody can authenticate to."""
    from hoja_server.cli import main
    from hoja_server.db import make_engine, migrate

    db = tmp_path / "cli.db"
    migrate(make_engine(db))
    monkeypatch.delenv("HOJA_CONNECTOR_SIGNING_KEY", raising=False)
    with pytest.raises(SystemExit) as stop:
        main(["connector-mcp", "--db", str(db)])
    assert stop.value.code == 2
    assert "HOJA_CONNECTOR_SIGNING_KEY" in capsys.readouterr().err


@respx.mock
async def test_org_account_is_available_to_a_member(seeded) -> None:
    engine, admin = seeded
    _add_account(
        engine, admin["org_id"], account_id="acct_org", owner=None, email="shared@service.test"
    )
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    app = build_connector_mcp_app(engine)
    body = await _call(
        app, "gmail_search", {"query": "x", "account": "shared@service.test"},
        _principal(admin),
    )
    # The grant resolved; the refresh then fails against Google, which is not a "no account" error.
    assert "No connected Google account" not in body
    assert "invalid_grant" in body
    assert token_route.called
