"""Remote MCP for a hosted tenant, checked by the official MCP SDK OAuth client.

Independent of the server code on purpose: the client side is the MCP SDK's own
``OAuthClientProvider`` (discovery, dynamic registration, PKCE, token exchange)
talking to a real uvicorn process over a socket. The tenant's trust settings are
not written by hand: they are read from the env file that ``infra/scripts/hoja-tenant``
renders, so this proves "a tenant provisioned by the script serves working OAuth MCP".

Multi-tenant properties checked here:
- a grant is pinned to the workspace of the browser session that approved it, and
  the consent page names that workspace;
- a token from one tenant is useless on another tenant's host (separate process,
  separate database) and on a host the tenant does not own (421);
- deactivating a membership stops a live token from reading anything on its next
  tool call (the bearer still opens a session; see residual risk in the evidence).
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("mcp")
httpx2 = pytest.importorskip("httpx2")
uvicorn = pytest.importorskip("uvicorn")

from mcp.client.auth import OAuthClientProvider, TokenStorage  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata  # noqa: E402
from sqlalchemy import update  # noqa: E402

from hoja_server.app import create_app  # noqa: E402
from hoja_server.db import make_engine, migrate, organization_membership, seed_tenant  # noqa: E402

TENANT_SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "hoja-tenant"
PASSWORD = "correct horse battery staple"
EMAIL = "owner@tenants.example.test"
REDIRECT = "http://127.0.0.1:53682/callback"  # native loopback, the shape pi uses


# --------------------------------------------------------------------------- #
# provisioning and processes
# --------------------------------------------------------------------------- #


def _tenant_mcp_env(tmp_path: Path, slug: str) -> dict[str, str]:
    """Render a tenant env with the real provisioner and return its MCP trust lines."""
    if not TENANT_SCRIPT.exists():
        pytest.skip("infra/scripts/hoja-tenant is not in this checkout")
    fake_bin = tmp_path / f"bin-{slug}"
    fake_bin.mkdir()
    psql = fake_bin / "psql"
    psql.write_text("#!/bin/sh\ncat >/dev/null\n")
    psql.chmod(0o755)
    root = tmp_path / f"state-{slug}"
    subprocess.run(
        [sys.executable, str(TENANT_SCRIPT), "--root", str(root), "--pg-bin", str(psql),
         "--pg-admin-url", "postgresql://admin@127.0.0.1/postgres", "--base-domain", "hoja.test",
         "create", slug, "--apply", "--trusted-proxy-cidrs", "127.0.0.1/32"],
        check=True, capture_output=True, text=True,
    )
    env = {}
    for line in (root / "env" / f"{slug}.env").read_text().splitlines():
        if line.startswith(("HOJA_TRUSTED_PUBLIC_HOST=", "HOJA_TRUSTED_PROXY_CIDRS=")):
            key, value = line.split("=", 1)
            env[key] = value
    assert env == {"HOJA_TRUSTED_PUBLIC_HOST": f"{slug}.hoja.test",
                   "HOJA_TRUSTED_PROXY_CIDRS": "127.0.0.1/32"}
    return env


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # running in a thread
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app) -> tuple[_Server, int]:
    port = _free_port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    return server, port


class Edge(httpx2.AsyncHTTPTransport):
    """Stand-in for Traefik: route https://<tenant host> to that tenant's process, Host preserved."""

    def __init__(self, routes: dict[str, int]) -> None:
        super().__init__()
        self.routes = routes

    async def handle_async_request(self, request):
        # Build a fresh request: the SDK auth flow re-sends the same object after a 401.
        host = request.url.host
        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() != "host"]
        headers.append(("host", host))
        upstream = httpx2.Request(
            request.method,
            request.url.copy_with(scheme="http", host="127.0.0.1", port=self.routes[host]),
            headers=headers, content=await request.aread(), extensions=request.extensions,
        )
        return await super().handle_async_request(upstream)


def _tenant_app(tmp_path: Path, monkeypatch, slug: str, orgs: list[tuple[str, str]]):
    """One tenant process: its own database, its own trust env, as the container would get it."""
    env = _tenant_mcp_env(tmp_path, slug)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOJA_TRUSTED_PUBLIC_SCHEME", "https")
    path = tmp_path / f"{slug}.db"
    engine = make_engine(path)
    migrate(engine)
    seeded = {
        key: seed_tenant(engine, org_name=name, org_url_key=key, user_name="Owner",
                         user_email=EMAIL, demo_issues=False)
        for key, name in orgs
    }
    app = create_app(db_path=str(path), open_mode=False)
    for key, value in env.items():
        monkeypatch.delenv(key)
    return app, engine, seeded, env["HOJA_TRUSTED_PUBLIC_HOST"]


# --------------------------------------------------------------------------- #
# browser and SDK client
# --------------------------------------------------------------------------- #


class MemoryStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens = None
        self.client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens) -> None:
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info) -> None:
        self.client = client_info


async def _browser(edge: Edge, host: str, seeded: dict, org_key: str) -> httpx2.AsyncClient:
    """A signed-in browser whose session belongs to exactly one workspace."""
    browser = httpx2.AsyncClient(transport=edge, base_url=f"https://{host}", follow_redirects=False, timeout=30)
    for item in seeded.values():  # one password per workspace profile
        setup = await browser.post("/auth/setup", json={"token": item["token"], "password": PASSWORD})
        # 403 "already set" happens when two workspaces share the person's identity.
        assert setup.status_code == 201 or "already set" in setup.text, setup.text
    login = await browser.post("/auth/login", json={
        "email": EMAIL, "password": PASSWORD, "organizationId": seeded[org_key]["org_id"]})
    assert login.status_code == 200, login.text
    return browser


async def _sdk_connect(edge: Edge, host: str, browser: httpx2.AsyncClient, seen: dict):
    """Run the official SDK OAuth flow; the human's approval is the browser's consent POST."""
    captured: dict[str, str] = {}

    async def redirect_handler(authorize_url: str) -> None:
        authorize = await browser.get(authorize_url)
        assert authorize.status_code in (302, 303), authorize.text
        location = authorize.headers["location"]
        # A real browser follows Location as-is: it must name the consent page under the
        # MCP mount on this host, not a root path the web app would answer instead.
        assert location.startswith(f"https://{host}/mcp/oauth/consent?pending="), location
        consent_url = location
        page = await browser.get(consent_url)
        assert page.status_code == 200, page.text
        seen["consent"] = page.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        approved = await browser.post(consent_url, data={"decision": "allow", "csrf_token": csrf})
        # RFC 9700 4.11: never 307 here, or the browser re-POSTs the consent form to the client.
        assert approved.status_code == 303, approved.status_code
        back = urlparse(approved.headers["location"])
        assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
        query = parse_qs(back.query)
        captured["code"] = query["code"][0]
        captured["state"] = query.get("state", [None])[0]
        # RFC 9207: the metadata advertises iss in the response, and the SDK checks it.
        captured["iss"] = query.get("iss", [None])[0]
        assert captured["iss"] == f"https://{host}/mcp"

    async def callback_handler() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=captured["code"], state=captured["state"], iss=captured["iss"])

    storage = MemoryStorage()
    provider = OAuthClientProvider(
        server_url=f"https://{host}/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="mcp-sdk-interop", redirect_uris=[REDIRECT],
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            token_endpoint_auth_method="none", scope="hoja:read",
        ),
        storage=storage, redirect_handler=redirect_handler, callback_handler=callback_handler,
    )
    return provider, storage


async def _call(edge: Edge, host: str, provider, *suffixes: str) -> dict[str, str]:
    """initialize, list tools, call each tool whose name ends with a suffix; return text by suffix."""
    out: dict[str, str] = {}
    async with httpx2.AsyncClient(transport=edge, auth=provider, timeout=30) as http:
        async with streamable_http_client(f"https://{host}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                names = [tool.name for tool in (await session.list_tools()).tools]
                for suffix in suffixes:
                    name = next(n for n in names if n == suffix or n.endswith("_" + suffix))
                    result = await session.call_tool(name, {})
                    assert not result.is_error, result
                    out[suffix] = "".join(getattr(part, "text", "") for part in result.content)
    return out


async def _raw_initialize(edge: Edge, host: str, access_token: str) -> int:
    async with httpx2.AsyncClient(transport=edge, timeout=30) as http:
        response = await http.post(
            f"https://{host}/mcp/",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json"},
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "replay", "version": "0"}}}),
        )
        return response.status_code


async def _raw_rpc(edge: Edge, host: str, access_token: str, method: str, params: dict) -> dict:
    """One stateless JSON-RPC call with a bare bearer, no SDK in the way."""
    async with httpx2.AsyncClient(transport=edge, timeout=30) as http:
        response = await http.post(
            f"https://{host}/mcp",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"},
            content=json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params}),
        )
    assert response.status_code == 200, (response.status_code, response.text[:200])
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """A shared-tier process: two workspaces, one person with a profile in each."""
    app, engine, seeded, host = _tenant_app(tmp_path, monkeypatch, "acme",
                                            [("acme-ws", "Acme Robotics"), ("globex-ws", "Globex Shipping")])
    server, port = _serve(app)
    yield {"engine": engine, "seeded": seeded, "host": host, "port": port, "tmp": tmp_path, "mp": monkeypatch}
    server.should_exit = True


def test_metadata_matches_rfc_9728_and_8414_for_the_provisioned_host(shared) -> None:
    host = shared["host"]
    edge = Edge({host: shared["port"]})

    async def go():
        async with httpx2.AsyncClient(transport=edge, timeout=30) as http:
            prm = (await http.get(f"https://{host}/.well-known/oauth-protected-resource/mcp")).json()
            asm = (await http.get(f"https://{host}/.well-known/oauth-authorization-server/mcp")).json()
        return prm, asm

    prm, asm = asyncio.run(go())
    # RFC 9728 section 2: the resource identifier is the URL clients send as RFC 8707 `resource`.
    assert prm["resource"] == f"https://{host}/mcp"
    assert prm["authorization_servers"] == [f"https://{host}/mcp"]
    # RFC 8414 section 2/3: issuer equals the metadata location's issuer; endpoints share its origin.
    assert asm["issuer"] == f"https://{host}/mcp"
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert asm[key].startswith(f"https://{host}/mcp/"), (key, asm[key])
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert "mcp.invalid" not in json.dumps(asm)


def test_the_advertised_resource_url_itself_is_the_mcp_endpoint(shared) -> None:
    """RFC 9728 advertises https://<host>/mcp; clients POST there, without a trailing slash."""
    host = shared["host"]
    edge = Edge({host: shared["port"]})

    async def go():
        async with httpx2.AsyncClient(transport=edge, timeout=30) as http:
            post = await http.post(f"https://{host}/mcp", headers={"Authorization": "Bearer nope",
                                   "Accept": "application/json, text/event-stream"}, content="{}")
            get = await http.get(f"https://{host}/mcp", headers={"Accept": "text/event-stream"})
        return post, get

    post, get = asyncio.run(go())
    assert post.status_code == 401, (post.status_code, post.text[:120])
    assert "www-authenticate" in post.headers
    assert get.status_code == 401 and "<!doctype html>" not in get.text.lower()


def test_sdk_client_completes_oauth_and_is_pinned_to_the_consenting_workspace(shared) -> None:
    host, seeded = shared["host"], shared["seeded"]
    edge = Edge({host: shared["port"]})

    async def go(org_key: str):
        browser = await _browser(edge, host, seeded, org_key)
        seen: dict = {}
        provider, _storage = await _sdk_connect(edge, host, browser, seen)
        texts = await _call(edge, host, provider, "workspace", "viewer")
        await browser.aclose()
        return seen, texts

    # The consent page names the workspace the browser is signed in to, with its host.
    seen_a, acme = asyncio.run(go("acme-ws"))
    assert f"<dd>Acme Robotics · {host}</dd>" in seen_a["consent"]
    assert "Acme Robotics" in acme["workspace"] and "Globex" not in acme["workspace"]

    # Same person, same password, browser signed in to the other workspace: a different grant.
    seen_g, globex = asyncio.run(go("globex-ws"))
    assert f"<dd>Globex Shipping · {host}</dd>" in seen_g["consent"]
    assert "Globex Shipping" in globex["workspace"] and "Acme" not in globex["workspace"]


def test_token_is_refused_on_another_tenant_and_on_a_foreign_host(shared, tmp_path, monkeypatch) -> None:
    host, seeded = shared["host"], shared["seeded"]
    beta_app, _engine, _beta_seeded, beta_host = _tenant_app(tmp_path, monkeypatch, "beta", [("beta-ws", "Beta Labs")])
    beta_server, beta_port = _serve(beta_app)
    try:
        edge = Edge({host: shared["port"], beta_host: beta_port})
        foreign = Edge({beta_host: shared["port"]})  # acme's process reached under beta's name

        async def go():
            browser = await _browser(edge, host, seeded, "acme-ws")
            provider, storage = await _sdk_connect(edge, host, browser, {})
            await _call(edge, host, provider, "workspace")
            await browser.aclose()
            token = storage.tokens.access_token
            return (
                await _raw_initialize(edge, host, token),
                await _raw_initialize(edge, beta_host, token),
                await _raw_initialize(foreign, beta_host, token),
            )

        own, other_tenant, foreign_host = asyncio.run(go())
        assert own == 200
        assert other_tenant == 401          # another process and database never issued it
        assert foreign_host == 421          # the tenant only answers MCP on its own host
    finally:
        beta_server.should_exit = True


def test_deactivated_membership_stops_a_live_token_from_reading(shared) -> None:
    host, seeded, engine = shared["host"], shared["seeded"], shared["engine"]
    edge = Edge({host: shared["port"]})

    async def connect():
        browser = await _browser(edge, host, seeded, "acme-ws")
        provider, storage = await _sdk_connect(edge, host, browser, {})
        await _call(edge, host, provider, "workspace")
        await browser.aclose()
        return storage.tokens.access_token

    async def workspace_tool(token: str) -> dict:
        tools = (await _raw_rpc(edge, host, token, "tools/list", {}))["result"]["tools"]
        name = next(t["name"] for t in tools if t["name"] == "workspace" or t["name"].endswith("_workspace"))
        return (await _raw_rpc(edge, host, token, "tools/call", {"name": name, "arguments": {}}))["result"]

    token = asyncio.run(connect())
    before = asyncio.run(workspace_tool(token))
    assert not before.get("isError") and "Acme Robotics" in json.dumps(before)
    with engine.begin() as conn:
        conn.execute(update(organization_membership).where(
            organization_membership.c.organization_id == seeded["acme-ws"]["org_id"],
            organization_membership.c.profile_user_id == seeded["acme-ws"]["user_id"],
        ).values(active=False))
    after = asyncio.run(workspace_tool(token))
    assert after.get("isError") is True
    assert "membership" in json.dumps(after) and "Acme Robotics" not in json.dumps(after)
