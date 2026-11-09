"""The GitHub tools on the one shared connector MCP server.

The agent gets a URL and a signed principal from `connector_binding_for`; this is the process that
must answer on that URL. It resolves the accounts a principal may use and refreshes the eight-hour
GitHub access token on use, so a stale token is never a failure.
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
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    monkeypatch.setenv("HOJA_GITHUB_CLIENT_ID", "Iv23li4RW9Mh8RHVfCGi")
    monkeypatch.setenv("HOJA_GITHUB_CLIENT_SECRET", "github-service-test-secret")
    monkeypatch.setenv(
        "HOJA_GITHUB_REDIRECT_URI", "https://hoja.example.test/api/connectors/github/callback"
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


def _add_github_account(engine, org: str, *, owner: str | None, login: str = "rinadelph") -> None:
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_github_service",
                organization_id=org,
                owner_user_id=owner,
                provider="github",
                external_account=login,
                scopes="",
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
async def test_the_eight_github_tools_are_listed_on_the_shared_server(seeded) -> None:
    engine, admin = seeded
    _add_github_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, _principal(admin)
    )
    tools = {tool["name"]: tool for tool in _data(response)["result"]["tools"]}
    assert {
        "github_get_current_user",
        "github_list_repositories",
        "github_get_file_contents",
        "github_search_issues_and_prs",
        "github_list_issues",
        "github_get_issue",
        "github_list_pull_requests",
        "github_get_pull_request",
    } <= set(tools)
    # Every tool that returns repository text says where the text came from and that it is data.
    for name in ("github_get_file_contents", "github_get_issue", "github_get_pull_request"):
        assert "untrusted" in tools[name]["description"].lower()


@respx.mock
async def test_an_expired_github_token_is_refreshed_before_the_call(seeded) -> None:
    engine, admin = seeded
    _add_github_account(engine, admin["org_id"], owner=admin["user_id"])
    refresh = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ghu_fresh",
                "expires_in": 28800,
                "refresh_token": "ghr_next",
                "token_type": "bearer",
            },
        )
    )
    user = respx.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"login": "rinadelph", "id": 68679193})
    )
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "github_get_current_user", "arguments": {}},
        },
        _principal(admin),
    )
    assert refresh.called and user.called
    assert user.calls[0].request.headers["authorization"] == "Bearer ghu_fresh"
    result = _data(response)["result"]
    assert "rinadelph" in json.dumps(result)


async def test_no_principal_is_refused(seeded) -> None:
    engine, admin = seeded
    _add_github_account(engine, admin["org_id"], owner=admin["user_id"])
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "github_get_current_user", "arguments": {}},
        },
        None,
    )
    assert "principal" in response.text.lower()


async def test_another_users_github_account_is_not_resolved(seeded) -> None:
    engine, admin = seeded
    _add_github_account(engine, admin["org_id"], owner="someone-else")
    app = build_connector_mcp_app(engine, signing_key=KEY)
    response = await _rpc(
        app,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "github_get_current_user", "arguments": {}},
        },
        _principal(admin),
    )
    assert "no connected github account" in response.text.lower()
