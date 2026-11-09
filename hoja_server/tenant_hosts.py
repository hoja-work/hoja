"""Which organization a request's Host belongs to, in the shared tier.

Regular workspaces live on the canonical host (``HOJA_TRUSTED_PUBLIC_HOST``,
e.g. app.hoja.work). Enterprise workspaces also get their own host, stored as
``organization.vanity_host``: on that host only that organization's credentials
are accepted. Any other host under the apex is redirected to the canonical one,
so a former tenant URL of a regular workspace keeps working.
"""
from __future__ import annotations

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
    def __init__(self, engine: Engine, canonical: str, ttl: float = 30.0) -> None:
        self.engine = engine
        self.canonical = bare(canonical)
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

    def decide(self, host: str | None) -> tuple[str, str | None]:
        name = bare(host)
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
                await send({"type": "http.response.start", "status": 421,
                            "headers": [(b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
            target = f"https://{self.hosts.canonical}{scope.get('path', '/')}"
            if scope.get("query_string"):
                target += "?" + scope["query_string"].decode("latin-1")
            await send({"type": "http.response.start", "status": 308,
                        "headers": [(b"location", target.encode("latin-1")), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)
