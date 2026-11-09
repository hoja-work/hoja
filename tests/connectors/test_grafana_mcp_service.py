"""The six Grafana tools on the one shared connector MCP server.

The agent gets a URL and a signed principal from `connector_binding_for`; this is the process that
must answer on that URL. The token is long-lived and stored sealed, so the test asserts that the
package receives a source rather than the stored string, and that another user's instance is not
visible.
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
PUBLIC = "93.184.216.34"
URL = "https://grafana.example.com"
TOKEN = "glsa_abcdefghijklmnopqrstuvwxyz0123456789AB"
USER_ENDPOINT = f"https://{PUBLIC}/api/user"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    # The shared server also serves Google; its config is validated when the app is built.
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-service-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )
    monkeypatch.setattr("grafana.ssrf._system_resolver", lambda host, port: [PUBLIC])


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


def _add_account(engine, org: str, *, owner: str | None, label: str = "sa-1-hoja-connector") -> None:
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_grafana_service",
                organization_id=org,
                owner_user_id=owner,
                provider="grafana",
                external_account=label,
                scopes="viewer",
                encrypted_refresh_token=encrypt_secret(TOKEN),
                external_url=URL,
                external_id="service-account:20",
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
    if response.text.lstrip().startswith("{"):
        return json.loads(response.text)
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no JSON-RPC payload in response: {response.text[:200]}")


def _principal(admin, scope: str = "read") -> str:
    return sign_principal(admin["user_id"], admin["org_id"], scope, key=KEY)


@respx.mock
async def test_the_six_grafana_tools_are_listed_on_the_shared_server(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, _principal(admin)
    )
    names = {tool["name"] for tool in _data(response)["result"]["tools"]}
    assert {
        "grafana_get_current_user",
        "grafana_search_dashboards",
        "grafana_get_dashboard",
        "grafana_list_data_sources",
        "grafana_list_alert_rules",
        "grafana_list_annotations",
        "grafana_query_data_source",
    } <= names


@respx.mock
async def test_a_signed_principal_reads_through_the_pinned_address(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner=admin["user_id"])
    route = respx.get(USER_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"login": "sa-1-hoja-connector"})
    )
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "grafana_get_current_user", "arguments": {}},
        },
        _principal(admin),
    )
    payload = _data(response)
    assert "error" not in payload
    assert "sa-1-hoja-connector" in json.dumps(payload["result"])
    assert route.calls[0].request.url.host == PUBLIC
    assert route.calls[0].request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_a_call_without_a_principal_is_refused(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "grafana_get_current_user", "arguments": {}},
        },
        None,
    )
    payload = _data(response)
    assert "error" in payload or payload["result"].get("isError") is True


@respx.mock
async def test_another_users_instance_is_not_visible(seeded) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], owner="someone-else")
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "grafana_get_current_user", "arguments": {}},
        },
        _principal(admin),
    )
    payload = _data(response)
    assert payload["result"].get("isError") is True
    assert "No connected Grafana instance" in json.dumps(payload["result"])
