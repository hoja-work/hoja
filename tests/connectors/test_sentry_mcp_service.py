"""The Sentry tools on the shared connector MCP server, called the way the agent calls them.

The package tests prove the client and the tools; this proves the wiring: a signed principal and a
stored installation become a tool result over streamable HTTP, with the organization taken from the
grant.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from google_workspace.mcp_server import PRINCIPAL_HEADER
from google_workspace.principal import sign_principal

from hoja_server.connector_mcp_service import build_connector_mcp_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import encrypt_secret

KEY = "service-test-signing-key"
INSTALLATION = "262e5ed2-b7fd-4e80-bbfc-fa30dc2be0db"
PROJECTS = "https://sentry.io/api/0/organizations/hoja/projects/"
ISSUES = "https://sentry.io/api/0/organizations/hoja/issues/"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-service-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )
    monkeypatch.setenv(
        "HOJA_SENTRY_CLIENT_ID", "63f307c32617f9445b9deba149d733004d49495ce7b07fb142017482bbf67167"
    )
    monkeypatch.setenv("HOJA_SENTRY_CLIENT_SECRET", "sentry-service-test-secret")
    monkeypatch.setenv(
        "HOJA_SENTRY_REDIRECT_URI", "https://hoja.example.test/api/connectors/sentry/callback"
    )


def _seeded(tmp_path):
    engine = make_engine(tmp_path / "sentry-service.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Service Org",
        org_url_key="service",
        user_email="owner@service.test",
        token="owner-token",
        demo_issues=False,
    )
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct-sentry-service",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="sentry",
                external_account="hoja",
                external_id=INSTALLATION,
                scopes="event:read org:read project:read",
                encrypted_refresh_token=encrypt_secret("refresh-service"),
                encrypted_access_token=encrypt_secret("access-service"),
                access_token_expires_at=str(int(time.time()) + 3600),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    return engine, admin


def _api(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)


async def _rpc(app, method: str, params: dict, principal: str | None) -> dict:
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
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        response = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
        )
        return response


def _payload(response: httpx.Response) -> dict:
    text = response.text
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    return json.loads(text)


async def test_the_shared_server_lists_the_four_sentry_tools(tmp_path) -> None:
    engine, admin = _seeded(tmp_path)
    app = build_connector_mcp_app(engine, http=_api(lambda r: httpx.Response(200, json=[])))
    response = await _rpc(
        app,
        "tools/list",
        {},
        sign_principal(admin["user_id"], admin["org_id"], "read", key=KEY),
    )
    names = {tool["name"] for tool in _payload(response)["result"]["tools"]}
    assert {
        "sentry_get_organization",
        "sentry_list_projects",
        "sentry_list_issues",
        "sentry_get_issue",
        "sentry_latest_event",
    } <= names
    # The read surface stays read-only: nothing that could write.
    assert not any(name.endswith(("_create", "_update", "_delete")) for name in names)


async def test_a_tool_call_reaches_sentry_with_the_grants_organization(tmp_path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, json=[{"id": "2", "slug": "hoja-web", "name": "Hoja Web", "platform": "python"}]
        )

    engine, admin = _seeded(tmp_path)
    app = build_connector_mcp_app(engine, http=_api(handler))
    response = await _rpc(
        app,
        "tools/call",
        {"name": "sentry_list_projects", "arguments": {}},
        sign_principal(admin["user_id"], admin["org_id"], "read", key=KEY),
    )
    body = _payload(response)
    assert "error" not in body
    assert seen and seen[0].startswith(PROJECTS)
    text = json.dumps(body["result"])
    assert "hoja-web" in text


async def test_an_unauthenticated_call_is_refused(tmp_path) -> None:
    engine, _admin = _seeded(tmp_path)
    app = build_connector_mcp_app(engine, http=_api(lambda r: httpx.Response(200, json=[])))
    response = await _rpc(app, "tools/call", {"name": "sentry_list_projects", "arguments": {}}, None)
    body = _payload(response)
    assert "error" in body or "principal" in json.dumps(body).lower()
