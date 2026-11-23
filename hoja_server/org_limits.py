"""Per-organization in-flight request cap for the shared tier.

One organization's burst must not take every worker thread and database
connection from the others. ``_auth`` claims a slot for the resolved
organization; the middleware returns 429 when the organization is at its cap
and releases the slot when the response finishes, however it finishes.
WebSockets are not counted: they are long-lived and limited elsewhere.
"""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from typing import Any

_SLOTS = "hoja.org_slots"


class OrgBusy(Exception):
    pass


class OrgConcurrency:
    def __init__(self, limit: int | None = None) -> None:
        self.limit = int(os.environ.get("HOJA_ORG_MAX_INFLIGHT", "32")) if limit is None else limit
        self._lock = threading.Lock()
        self._active: dict[str, int] = defaultdict(int)

    def enter(self, scope: dict, org: str | None) -> None:
        if not org or self.limit <= 0 or scope.get("type") != "http":
            return
        held = scope.setdefault(_SLOTS, [])
        if org in held:
            return
        with self._lock:
            if self._active[org] >= self.limit:
                raise OrgBusy(org)
            self._active[org] += 1
        held.append(org)

    def release(self, scope: dict) -> None:
        held = scope.pop(_SLOTS, [])
        with self._lock:
            for org in held:
                self._active[org] -= 1
                if self._active[org] <= 0:
                    del self._active[org]

    def active(self, org: str) -> int:
        with self._lock:
            return self._active.get(org, 0)


class OrgConcurrencyMiddleware:
    def __init__(self, app: Any, limiter: OrgConcurrency) -> None:
        self.app, self.limiter = app, limiter

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except OrgBusy:
            if started:
                raise
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1")]})
            await send({"type": "http.response.body",
                        "body": b'{"error":"Too many concurrent requests for this workspace."}'})
        finally:
            self.limiter.release(scope)
