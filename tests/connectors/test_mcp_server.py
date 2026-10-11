"""Google Workspace MCP server over HTTP: principal header is required, identity is never taken from arguments."""

from __future__ import annotations

import httpx
import respx
from google_workspace.mcp_server import PRINCIPAL_HEADER, build_server
from google_workspace.principal import sign_principal
from google_workspace.tools import AccountGrant

KEY = "mcp-test-signing-key"


async def _token() -> str:
    return "ya29.MCP"


def _resolver_for(accounts: list[str]):
    async def resolve(principal, account):
        return [
            AccountGrant(account_id=f"acct-{i}", external_account=email, access_token=_token)
            for i, email in enumerate(accounts)
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
    route = respx.get(host="gmail.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["ana@example.test"]), http=http
        ).streamable_http_app()
        body = await _call(app, "gmail_search", {"query": "x"}, principal=None)
    assert "Missing connector principal" in body
    assert route.call_count == 0


@respx.mock
async def test_forged_principal_fails_closed() -> None:
    route = respx.get(host="gmail.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    forged = sign_principal("victim", "org", "read", key="attacker-key")
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["ana@example.test"]), http=http
        ).streamable_http_app()
        body = await _call(app, "gmail_search", {"query": "x"}, principal=forged)
    assert "signature" in body
    assert route.call_count == 0


@respx.mock
async def test_valid_principal_reaches_google_with_grant_token() -> None:
    route = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["ana@example.test"]), http=http
        ).streamable_http_app()
        body = await _call(app, "gmail_search", {"query": "from:ana"}, principal=principal)
    assert '"isError":true' not in body
    assert route.calls.last.request.headers["Authorization"] == "Bearer ya29.MCP"


@respx.mock
async def test_ambiguous_accounts_require_explicit_choice() -> None:
    route = respx.get(host="gmail.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    principal = sign_principal("user-1", "org-1", "read", key=KEY)
    async with httpx.AsyncClient() as http:
        app = build_server(
            signing_key=KEY, resolve_grants=_resolver_for(["a@x.test", "b@x.test"]), http=http
        ).streamable_http_app()
        body = await _call(app, "gmail_search", {"query": "x"}, principal=principal)
    assert "Several accounts are connected" in body
    assert route.call_count == 0
