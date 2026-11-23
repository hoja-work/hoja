"""Which organization a request's Host belongs to, in the shared tier.

One deployment and one database serve every workspace. The canonical host
(``HOJA_TRUSTED_PUBLIC_HOST``, e.g. app.hoja.work) is the account and sign-in
home: Google sign-in runs there (it is also ``HOJA_CONTROL_PLANE_URL``), and a
workspace without a host of its own is used there.

An enterprise workspace has its own host, stored as ``organization.vanity_host``
(e.g. core.hoja.work). Requests to that host are pinned to that organization:
only its credentials are accepted there (``required_org``), and its browser
session is a host-only cookie minted on that host by ``/auth/google/finish``
from a handoff the control plane signed for exactly that host. Such a workspace
is therefore *used* on its own host; the apex sends a browser holding its
session there instead of serving it (see ``_org_redirect`` in ``app.py``).

Any other host under the apex is redirected to the canonical one, so a former
tenant URL keeps working; a credential sent to such a host is refused (421),
never forwarded. The connector broker host is recognised and belongs to no
organization.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from hoja_server import tenant_context
from hoja_server.db import organization

OPEN, ORG, REDIRECT = "open", "org", "redirect"
_SCOPE_KEY = "hoja.host_org"
_EXEMPT = frozenset({"/health", "/ready"})


def bare(host: str | None) -> str:
    return (host or "").strip().lower().split(":")[0]


class TenantHosts:
    def __init__(self, engine: Engine, canonical: str, ttl: float = 30.0,
                 broker: str = "") -> None:
        self.engine = engine
        self.canonical = bare(canonical)
        # The connector broker (api.hoja.work) is a control-plane name that is NOT a
        # tenant: it serves the shared Calendly/Sentry callbacks for every workspace.
        # Without this it matches the apex and is redirected to the canonical host,
        # which would break the one callback those providers can register.
        self.broker = bare(broker)
        parts = self.canonical.split(".")
        self.apex = ".".join(parts[1:]) if len(parts) > 2 else ""
        self.ttl = ttl
        self._lock = threading.Lock()
        self._map: dict[str, str] = {}
        self._loaded = float("-inf")

    def hosts(self) -> dict[str, str]:
        now = time.monotonic()
        with self._lock:
            if now - self._loaded < self.ttl:
                return self._map
        with tenant_context.system(), self.engine.connect() as conn:
            rows = conn.execute(select(organization.c.vanity_host, organization.c.id)
                                .where(organization.c.vanity_host.isnot(None))).all()
        fresh = {bare(h): org for h, org in rows if h}
        with self._lock:
            self._map, self._loaded = fresh, now
        return fresh

    def host_for(self, org_id: str) -> str | None:
        """The workspace's own host, or None when it is served on the canonical host."""
        for host, org in self.hosts().items():
            if org == org_id:
                return host
        return None

    def decide(self, host: str | None) -> tuple[str, str | None]:
        name = bare(host)
        if self.broker and name == self.broker:
            # Recognised and served, but it belongs to no single organization: the
            # organization comes from the authenticated session or the sealed state.
            return OPEN, None
        if not self.canonical or not name or name == self.canonical:
            return OPEN, None
        org = self.hosts().get(name)
        if org:
            return ORG, org
        if self.apex and name.endswith("." + self.apex):
            return REDIRECT, None
        return OPEN, None


def required_org(scope: dict) -> str | None:
    return scope.get(_SCOPE_KEY)


WRONG_WORKSPACE_HOST = "wrong_workspace_host"


def _scope_host(scope: dict) -> str:
    headers = dict(scope.get("headers") or [])
    return bare(headers.get(b"host", b"").decode("latin-1"))


def host_refusal(hosts: TenantHosts, scope: dict, org_id: str, *, credential: bool) -> dict | None:
    """Why a valid identity of ``org_id`` may not be used on this request's host, or None.

    Two rules, one answer shape. On a workspace's own host only that workspace's
    identities count. On the canonical host, a credential (API key, OAuth or MCP
    bearer; ``credential=True``) of a workspace that has its own host is refused:
    such a workspace is used on its own host only. Browser sessions are not
    judged by the second rule here; the sign-in flow owns where they live.

    The answer names the identity's OWN origin. That is safe to say: only the
    holder of a valid credential for that workspace ever sees it.
    """
    pinned = required_org(scope)
    if pinned:
        if org_id == pinned:
            return None
        own = hosts.host_for(org_id)
        return {
            "code": WRONG_WORKSPACE_HOST,
            "reason": "pinned_host",
            "message": "This credential belongs to another workspace, which is served at "
                       f"https://{own or hosts.canonical}.",
            "origin": f"https://{own or hosts.canonical}",
        }
    if credential and hosts.canonical and _scope_host(scope) == hosts.canonical:
        own = hosts.host_for(org_id)
        if own:
            return {
                "code": WRONG_WORKSPACE_HOST,
                "reason": "workspace_has_own_host",
                "message": f"This workspace is served at https://{own} and is used there only.",
                "origin": f"https://{own}",
            }
    return None


class TenantHostMiddleware:
    """Pin vanity-host requests to their organization; send other apex hosts to the canonical one."""

    def __init__(self, app: Any, hosts: TenantHosts) -> None:
        self.app, self.hosts = app, hosts

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        kind, org = self.hosts.decide(host)
        if kind == ORG:
            scope[_SCOPE_KEY] = org
        elif kind == REDIRECT and scope.get("path") not in _EXEMPT:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            path = scope.get("path", "")
            if (scope.get("method") not in ("GET", "HEAD") or b"authorization" in headers
                    or path == "/mcp" or path.startswith("/mcp/")):
                # A credential sent to the wrong host is refused, never forwarded by a redirect.
                body = json.dumps({
                    "error": "This host does not serve a Hoja workspace.",
                    "reason": "not_a_workspace_host",
                    "canonicalHost": self.hosts.canonical,
                    "hint": f"Use https://{self.hosts.canonical} or your workspace's own address.",
                }).encode()
                await send({"type": "http.response.start", "status": 421,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            target = f"https://{self.hosts.canonical}{scope.get('path', '/')}"
            if scope.get("query_string"):
                target += "?" + scope["query_string"].decode("latin-1")
            await send({"type": "http.response.start", "status": 308,
                        "headers": [(b"location", target.encode("latin-1")), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)
