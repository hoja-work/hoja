"""Slack MCP tools: signed principal required, workspace grant resolved, read-only surface."""

from __future__ import annotations

import json

import httpx
import respx
from google_workspace.config import GoogleOAuthConfig
from slack.client import API_BASE
from slack.mcp_server import build_server
from slack.principal import PRINCIPAL_HEADER, sign_principal
from slack.tools import AccountGrant

from hoja_server.connector_mcp_service import build_connector_mcp_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import encrypt_secret

KEY = "slack-mcp-test-signing-key"


async def _token() -> str:
    return "xoxp-fixture"


def _resolver_for(accounts: list[str]):
    async def resolve(principal, account):
        return [
            AccountGrant(account_id=f"acct-{i}", external_account=name, access_token=_token)
            for i, name in enumerate(accounts)
        ]

    return resolve


async def _rpc(app, method: str, params: dict, principal: str | None) -> str:
    return (await _rpc_all(app, [(method, params)], principal))[0]


async def _rpc_all(
    app, calls: list[tuple[str, dict]], principal: str | None
) -> list[str]:
    """One lifespan for several JSON-RPC calls: the streamable HTTP session manager can only be
    run once per app instance."""
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


def _app(accounts: list[str], http: httpx.AsyncClient):
    return build_server(
        signing_key=KEY, resolve_grants=_resolver_for(accounts), http=http
    ).streamable_http_app()


@respx.mock
async def test_call_without_principal_fails_closed() -> None:
    route = respx.get(f"{API_BASE}conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": True, "channels": []})
    )
    async with httpx.AsyncClient() as http:
        body = await _rpc(
            _app(["Hoja"], http),
            "tools/call",
            {"name": "slack_list_channels", "arguments": {}},
            principal=None,
        )
    assert "Missing connector principal" in body
    assert route.call_count == 0


@respx.mock
async def test_forged_principal_fails_closed() -> None:
    route = respx.get(f"{API_BASE}conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": True, "channels": []})
    )
    forged = sign_principal("victim", "org", "read", key="attacker-key")
    async with httpx.AsyncClient() as http:
        body = await _rpc(
            _app(["Hoja"], http),
            "tools/call",
            {"name": "slack_list_channels", "arguments": {}},
            principal=forged,
        )
    assert "signature" in body
    assert route.call_count == 0


@respx.mock
async def test_valid_principal_reaches_slack_with_the_grant_token() -> None:
    route = respx.get(f"{API_BASE}conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": "1762700000.000100", "text": "hello"}],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        body = await _rpc(
            _app(["Hoja"], http),
            "tools/call",
            {"name": "slack_read_channel_history", "arguments": {"channel": "C0123ABCD"}},
            principal=principal,
        )
    assert '"isError":true' not in body
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer xoxp-fixture"
    assert "hello" in body


@respx.mock
async def test_no_connected_workspace_is_a_readable_tool_error() -> None:
    route = respx.get(f"{API_BASE}conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": True, "channels": []})
    )
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        body = await _rpc(
            _app([], http),
            "tools/call",
            {"name": "slack_list_channels", "arguments": {}},
            principal=principal,
        )
    assert "No connected Slack workspace" in body
    assert route.call_count == 0


async def test_the_tool_surface_is_read_only() -> None:
    """Least privilege, checked on the surface the agent actually sees: four tools, all verbs
    read-only, and no argument that names another user."""
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        body = await _rpc(
            _app(["Hoja"], http), "tools/list", {}, principal=principal
        )
    tools = _payload(body)["result"]["tools"]
    names = sorted(tool["name"] for tool in tools)
    assert names == [
        "slack_list_channels",
        "slack_lookup_users",
        "slack_read_channel_history",
        "slack_search_messages",
    ]
    for tool in tools:
        assert not any(
            verb in tool["name"] for verb in ("post", "send", "write", "delete", "update", "edit")
        )
        assert "user_id" not in (tool.get("inputSchema") or {}).get("properties", {})


@respx.mock
async def test_the_shared_toolset_serves_slack_for_a_connected_workspace(
    tmp_path, monkeypatch
) -> None:
    """The three pieces must actually meet: the service that answers HOJA_CONNECTOR_MCP_URL
    registers the Slack tools, resolves the sealed grant from `connector_account`, and calls
    Slack with that token. A missing `register_slack_tools` or a resolver typo would otherwise
    pass every isolated test and fail only in production."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    engine = make_engine(tmp_path / "shared.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Shared Org",
        org_url_key="shared-org",
        user_email="owner@shared.test",
        token="shared-token",
        demo_issues=False,
    )
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_slack",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="slack",
                external_account="Hoja",
                scopes="channels:read groups:read",
                encrypted_refresh_token=encrypt_secret("xoxp-fixture"),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    route = respx.get(f"{API_BASE}conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [{"id": "C0123ABCD", "name": "eng", "is_member": True}],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    principal = sign_principal(
        admin["user_id"], admin["org_id"], "read", key=KEY
    )
    async with httpx.AsyncClient() as http:
        app = build_connector_mcp_app(
            engine,
            http=http,
            config=GoogleOAuthConfig(
                client_id="client.apps.googleusercontent.com",
                client_secret="unused-in-this-test",
                redirect_uri="https://hoja.example.test/api/connectors/google/callback",
            ),
            signing_key=KEY,
        )
        listed_text, call_text = await _rpc_all(
            app,
            [
                ("tools/list", {}),
                ("tools/call", {"name": "slack_list_channels", "arguments": {}}),
            ],
            principal,
        )
    listed = _payload(listed_text)
    body = call_text
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"slack_list_channels", "slack_read_channel_history"} <= names
    # The Google and Notion toolsets are still registered on the same server.
    assert {"notion_search", "gmail_search"} & names
    assert '"isError":true' not in body
    assert "eng" in body
    assert route.calls.last.request.headers["Authorization"] == "Bearer xoxp-fixture"
