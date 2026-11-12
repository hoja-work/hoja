"""Per-transaction tenant context for PostgreSQL row-level security.

Every transaction on a PostgreSQL engine starts with

    SELECT set_config('hoja.org_id', <org or ''>, true),
           set_config('hoja.system', <'on' or ''>, true)

The third argument makes both settings transaction-local (``SET LOCAL``): they
vanish at COMMIT or ROLLBACK, so a pooled connection can never carry one
request's tenant into the next. This is the pattern PostgREST uses for request
claims. Deployments without row-level security policies are unaffected; the
settings are simply never read.

Context sources:

- ``bind(org)``: once a request has authenticated, for the rest of that request.
- ``system()``: an explicit, enumerated cross-tenant path (credential lookups
  before a tenant is known, realtime fan-out, background loops).
- neither: no tenant. Under row-level security that reads as no rows (fail closed).

Both live in ContextVars, so they follow a request through awaits and into
Starlette's thread pool, and never leak across requests (each runs in its own
task with its own context copy).

A context set inside a worker thread does NOT come back: whoever authenticates
with ``run_in_threadpool(auth, ...)`` must ``bind`` after it returns.

Gate: ``HOJA_TEST_RLS=1 HOJA_TEST_POSTGRES_URL=... pytest tests/test_meetings.py
tests/test_meetings_social.py tests/test_agent_chat.py tests/test_agent_durable.py tests/e2e``
runs those suites with the app as a restricted role under the production
policies (see tests/rls_mode.py).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Iterator, TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine

T = TypeVar("T")

_org: ContextVar[str | None] = ContextVar("hoja_tenant_org", default=None)
_system: ContextVar[bool] = ContextVar("hoja_tenant_system", default=False)

# Paths that run before a tenant is known, or that are tenant-agnostic by
# nature. They see every row, exactly as before row-level security. Keep this
# list short and reviewed: anything not matched fails closed until _auth binds.
SYSTEM_PATH_PREFIXES = (
    "/auth/",                 # login, setup, invite accept, workspaces, Google, me/logout
    "/onboarding/",           # first-run status and bootstrap
    "/api/signup",            # pre-tenant sign-up (global tables)
    "/.well-known/",          # OAuth discovery
    "/mcp/authorize", "/mcp/token", "/mcp/register", "/mcp/revoke", "/mcp/oauth/",
)
SYSTEM_PATHS = frozenset({"/health", "/ready"})

_SET = "SELECT set_config('hoja.org_id', %s, true), set_config('hoja.system', %s, true)"


def bind(org: str | None) -> None:
    """Scope the rest of the current context (one request) to ``org``."""
    _org.set(org or None)


def enter_system() -> None:
    """Mark the rest of the current context (one request) as a system path.

    For requests whose credential is not a tenant member: a meeting guest token
    or a public meeting code. The service's own predicates scope those queries.
    """
    _system.set(True)


def current() -> tuple[str | None, bool]:
    return _org.get(), _system.get()


@contextmanager
def system() -> Iterator[None]:
    """Run an enumerated cross-tenant operation."""
    token = _system.set(True)
    try:
        yield
    finally:
        _system.reset(token)


@contextmanager
def scoped(org: str | None) -> Iterator[None]:
    """Run as exactly ``org``, with no system context, whatever the caller's context is."""
    org_token, sys_token = _org.set(org or None), _system.set(False)
    try:
        yield
    finally:
        _system.reset(sys_token)
        _org.reset(org_token)


def _strict_report(engine: Engine) -> dict[str, Any] | None:
    """The deployment-side verdict: is row-level security binding this connection?

    Only consulted when ``HOJA_REQUIRE_RLS`` is set, which the production compose
    file does and local development does not. Reads the same catalog and role
    facts as ``infra/scripts/hoja-rls check``, but with the app's own credentials,
    so a deployment that skipped the operator command cannot report itself ready.
    """
    from hoja_server import rls_policy

    if engine.dialect.name != "postgresql":
        return {
            "reason": "rls_unsupported",
            "detail": "HOJA_REQUIRE_RLS is set but this deployment is not PostgreSQL; "
                      "row-level security only exists on PostgreSQL",
        }
    with system(), engine.connect() as conn:
        rows, role = rls_policy.catalog_rows(conn), rls_policy.role_facts(conn)
    found = rls_policy.gaps(rows, role)
    if found:
        return {"reason": found[0][0], "detail": found[0][1], "gaps": [detail for _, detail in found]}
    return None


def probe_report(engine: Engine) -> dict[str, Any] | None:
    """Prove tenant isolation is working; return a report to serve, or None.

    A tenant-bound read must see its own organization. Under row-level security
    it must see no other organization, and a read with no context must see
    nothing. With HOJA_REQUIRE_RLS set it must also be *bound*: the role must not
    bypass policies (superuser / BYPASSRLS), must own no table (owners skip
    unforced policies), and every tenant table must carry both RLS and its
    policy — not only ``organization``.
    """
    from sqlalchemy import func, select, text

    from hoja_server.db import organization

    strict = bool(os.environ.get("HOJA_REQUIRE_RLS"))
    if strict:
        report = _strict_report(engine)
        if report:
            return report

    count = select(func.count()).select_from(organization)
    with system(), engine.connect() as conn:
        org_id = conn.execute(select(organization.c.id).order_by(organization.c.id).limit(1)).scalar()
    rls = False
    if engine.dialect.name == "postgresql":
        with system(), engine.connect() as conn:
            rls = bool(conn.execute(text(
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'organization'::regclass")).scalar())
    if strict and not rls:
        return {"reason": "rls_disabled", "detail": "row-level security is off on organization"}
    if org_id is None:
        return None
    with scoped(org_id), engine.connect() as conn:
        if conn.execute(count.where(organization.c.id == org_id)).scalar_one() != 1:
            return {"reason": "tenant_read_failed",
                    "detail": "a tenant-bound read could not see its own organization"}
        if rls and conn.execute(count.where(organization.c.id != org_id)).scalar_one() != 0:
            return {"reason": "tenant_isolation_failed",
                    "detail": "a tenant-bound read saw another organization"}
    if rls:
        with scoped(None), engine.connect() as conn:
            if conn.execute(count).scalar_one() != 0:
                return {"reason": "fail_open",
                        "detail": "an unbound read saw rows, so the policies fail open"}
    return None


def probe(engine: Engine) -> str | None:
    """The failure reason ``/ready`` reports, or None. ``probe_report`` for detail."""
    report = probe_report(engine)
    return report["reason"] if report else None


async def as_system(awaitable: Awaitable[T]) -> T:
    """Await ``awaitable`` in system context; for tasks that serve every tenant."""
    _system.set(True)  # this coroutine runs in its own task's context copy
    return await awaitable


def is_system_path(path: str) -> bool:
    return path in SYSTEM_PATHS or path.startswith(SYSTEM_PATH_PREFIXES)


def install(engine: Engine) -> None:
    """Prefix every PostgreSQL transaction with the transaction-local settings."""
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "begin")
    def _set_tenant(conn: Any) -> None:
        org, sys_ = current()
        # The raw DBAPI cursor: psycopg opens the transaction implicitly on this
        # first statement, so the settings are local to exactly this transaction.
        cursor = conn.connection.dbapi_connection.cursor()
        try:
            cursor.execute(_SET, (org or "", "on" if sys_ else ""))
        finally:
            cursor.close()


class TenantContextMiddleware:
    """ASGI: run the enumerated system paths in system context; leave the rest empty."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket") and is_system_path(scope.get("path", "")):
            token = _system.set(True)
            try:
                await self.app(scope, receive, send)
            finally:
                _system.reset(token)
            return
        await self.app(scope, receive, send)
