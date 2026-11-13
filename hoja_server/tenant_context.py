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

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Iterator, TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine

T = TypeVar("T")

_org: ContextVar[str | None] = ContextVar("hoja_tenant_org", default=None)
_system: ContextVar[bool] = ContextVar("hoja_tenant_system", default=False)
# Who is writing (revision 64, HOJA-602-A-3). change_event is written by a trigger,
# so attribution has to travel the same way the tenant does: a transaction-local
# session setting. Unset means "no actor recorded", which is what every local
# (SQLite) database and every pre-agent call path keeps getting.
_actor: ContextVar[tuple[str | None, str | None, str | None]] = ContextVar(
    "hoja_tenant_actor", default=(None, None, None))
# user.kind values: "human", "bot" (an extension's identity), "agent" (revision 64).
ACTOR_KINDS = ("human", "bot", "agent")

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

_SET = ("SELECT set_config('hoja.org_id', %s, true), set_config('hoja.system', %s, true), "
        "set_config('hoja.actor_kind', %s, true), set_config('hoja.actor_id', %s, true), "
        "set_config('hoja.on_behalf_of', %s, true)")


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


def current_actor() -> tuple[str | None, str | None, str | None]:
    return _actor.get()


def set_actor(kind: str, actor_id: str, on_behalf_of: str | None = None) -> None:
    """Scope the rest of the current context's writes to ``actor_id`` (mirrors ``bind``)."""
    if kind not in ACTOR_KINDS:
        raise ValueError(f"actor kind must be one of {ACTOR_KINDS}, not {kind!r}")
    if not actor_id:
        raise ValueError("a write must name its actor_id")
    _actor.set((kind, actor_id, on_behalf_of))


def enter(org: str | None, uid: str, kind: str = "human") -> None:
    """Scope the rest of the request to its tenant and to whoever authenticated.

    ``kind='agent'`` when the credential names an agent principal (a runner or an
    agent's own MCP key), so its writes are attributed to the agent, not to a
    person it never was.
    """
    bind(org)
    set_actor(kind, uid)


@contextmanager
def actor(kind: str, actor_id: str, on_behalf_of: str | None = None) -> Iterator[None]:
    """Attribute every change in this context to ``actor_id`` (revision 64).

    An agent turn runs with ``kind='agent'``, the agent's user id, and the human
    whose request it serves as ``on_behalf_of``. An ordinary request is already
    attributed to its human by ``enter``; this context overrides that for the
    duration of the block, and restores it after.

    Fails closed when an agent write names no agent: an anonymous agent write is
    exactly the unattributable row this change exists to prevent.
    """
    token = _actor.set((None, None, None))
    try:
        set_actor(kind, actor_id, on_behalf_of)
        yield
    finally:
        _actor.reset(token)


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


def probe(engine: Engine) -> str | None:
    """Prove tenant isolation is working; return the failure reason, or None.

    A tenant-bound read must see its own organization. Under row-level security
    it must see no other organization, and a read with no context must see
    nothing. With HOJA_REQUIRE_RLS set, row-level security must be enabled.
    """
    import os

    from sqlalchemy import func, select, text

    from hoja_server.db import organization

    count = select(func.count()).select_from(organization)
    with system(), engine.connect() as conn:
        org_id = conn.execute(select(organization.c.id).order_by(organization.c.id).limit(1)).scalar()
    rls = False
    if engine.dialect.name == "postgresql":
        with system(), engine.connect() as conn:
            rls = bool(conn.execute(text(
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'organization'::regclass")).scalar())
    if os.environ.get("HOJA_REQUIRE_RLS") and not rls:
        return "rls_disabled"
    if org_id is None:
        return None
    with scoped(org_id), engine.connect() as conn:
        if conn.execute(count.where(organization.c.id == org_id)).scalar_one() != 1:
            return "tenant_read_failed"
        if rls and conn.execute(count.where(organization.c.id != org_id)).scalar_one() != 0:
            return "tenant_isolation_failed"
    if rls:
        with scoped(None), engine.connect() as conn:
            if conn.execute(count).scalar_one() != 0:
                return "fail_open"
    return None


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
        actor_kind, actor_id, on_behalf_of = current_actor()
        # The raw DBAPI cursor: psycopg opens the transaction implicitly on this
        # first statement, so the settings are local to exactly this transaction.
        cursor = conn.connection.dbapi_connection.cursor()
        try:
            cursor.execute(_SET, (org or "", "on" if sys_ else "",
                                  actor_kind or "", actor_id or "", on_behalf_of or ""))
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
