"""Notion MCP tools: signed principal required, workspace grant resolved, no identity in arguments."""

from __future__ import annotations

import httpx
import respx
from notion.mcp_server import build_server
from notion.principal import PRINCIPAL_HEADER, sign_principal
from notion.tools import AccountGrant

KEY = "notion-mcp-test-signing-key"


async def _token() -> str:
    return "secret_notion_access_token"


def _resolver_for(accounts: list[str]):
    async def resolve(principal, account):
        return [
            AccountGrant(account_id=f"acct-{i}", external_account=name, access_token=_token)
            for i, name in enumerate(accounts)
        ]

    return resolve


async def _call(app, tool: str, arguments: dict, principal: str | None) -> str:
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


@respx.mock
async def test_call_without_principal_fails_closed() -> None:
    route = respx.post("https://api.notion.com/v1/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["Space"]), http=http
        ).streamable_http_app()
        body = await _call(app, "notion_search", {"query": "x"}, principal=None)
    assert "Missing connector principal" in body
    assert route.call_count == 0


@respx.mock
async def test_forged_principal_fails_closed() -> None:
    route = respx.post("https://api.notion.com/v1/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    forged = sign_principal("victim", "org", "read", key="attacker-key")
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["Space"]), http=http
        ).streamable_http_app()
        body = await _call(app, "notion_search", {"query": "x"}, principal=forged)
    assert "signature" in body
    assert route.call_count == 0


@respx.mock
async def test_valid_principal_reaches_notion_with_the_grant_token() -> None:
    route = respx.post("https://api.notion.com/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "page",
                        "id": "p1",
                        "properties": {
                            "Name": {"type": "title", "title": [{"plain_text": "Roadmap"}]}
                        },
                    }
                ]
            },
        )
    )
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["Space"]), http=http
        ).streamable_http_app()
        body = await _call(app, "notion_search", {"query": "road"}, principal=principal)
    assert '"isError":true' not in body
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret_notion_access_token"
    assert route.calls.last.request.headers["Notion-Version"] == "2022-06-28"


@respx.mock
async def test_no_connected_workspace_is_a_readable_tool_error() -> None:
    route = respx.post("https://api.notion.com/v1/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for([]), http=http
        ).streamable_http_app()
        body = await _call(app, "notion_search", {"query": "road"}, principal=principal)
    assert "No connected Notion workspace" in body
    assert route.call_count == 0
