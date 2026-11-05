"""Tenant context for row-level security: transaction-local settings, never leaked.

The PostgreSQL tests pin the pool to ONE connection so reuse is deterministic:
tenant A, then no tenant, then a rollback, all on the same backend pid.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from hoja_server import tenant_context as tc

POSTGRES_URL = os.environ.get("HOJA_TEST_POSTGRES_URL")
needs_pg = pytest.mark.skipif(not POSTGRES_URL, reason="set HOJA_TEST_POSTGRES_URL to run")


def test_paths_enumerated_as_system_and_everything_else_is_not() -> None:
    for p in ("/auth/login", "/auth/google/callback", "/onboarding/status", "/api/signup",
              "/.well-known/oauth-authorization-server/mcp", "/mcp/token", "/health", "/ready"):
        assert tc.is_system_path(p), p
    for p in ("/graphql", "/sync/delta", "/ws", "/api/uploads", "/api/agent/chat", "/mcp",
              "/", "/healthz", "/readyz", "/authx"):
        assert not tc.is_system_path(p), p


def test_middleware_sets_system_only_for_system_paths() -> None:
    async def probe(request):
        org, sys_ = tc.current()
        return JSONResponse({"org": org, "system": sys_})

    app = Starlette(routes=[Route("/auth/me", probe), Route("/graphql", probe)])
    app.add_middleware(tc.TenantContextMiddleware)
    with TestClient(app) as client:
        assert client.get("/auth/me").json() == {"org": None, "system": True}
        assert client.get("/graphql").json() == {"org": None, "system": False}


def test_binding_does_not_leak_out_of_a_task() -> None:
    async def main():
        async def request_a():
            tc.bind("org-a")
            return tc.current()
        inner = await asyncio.create_task(request_a())
        return inner, tc.current()
    inner, outer = asyncio.run(main())
    assert inner == ("org-a", False)
    assert outer == (None, False)


@pytest.fixture
def pg_one_connection(monkeypatch):
    monkeypatch.setenv("HOJA_DB_POOL_SIZE", "1")
    monkeypatch.setenv("HOJA_DB_MAX_OVERFLOW", "0")
    from hoja_server.db import make_engine
    eng = make_engine(POSTGRES_URL)
    yield eng
    eng.dispose()


def _settings(conn):
    return conn.execute(text(
        "select current_setting('hoja.org_id', true), current_setting('hoja.system', true), pg_backend_pid()"
    )).one()


@needs_pg
def test_tenant_setting_is_transaction_local_on_a_reused_connection(pg_one_connection) -> None:
    eng = pg_one_connection

    async def scenario():
        async def as_tenant():
            tc.bind("org-a")
            with eng.begin() as conn:
                return _settings(conn)
        a = await asyncio.create_task(as_tenant())
        with eng.begin() as conn:  # a later request with no tenant, same pooled connection
            none = _settings(conn)
        return a, none

    a, none = asyncio.run(scenario())
    assert a[0] == "org-a" and a[1] == ""
    assert none[2] == a[2], "the pool did not reuse the connection; the test proves nothing"
    assert (none[0] or "") == "" and (none[1] or "") == ""


@needs_pg
def test_rollback_clears_the_tenant_too(pg_one_connection) -> None:
    eng = pg_one_connection

    async def failing_request():
        tc.bind("org-a")
        with eng.begin() as conn:
            assert _settings(conn)[0] == "org-a"
            raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError):
        asyncio.run(failing_request())
    with eng.begin() as conn:
        assert (_settings(conn)[0] or "") == ""


@needs_pg
def test_tenant_does_not_survive_on_the_connection_outside_the_listener(pg_one_connection) -> None:
    """The listener rewrites the settings on every transaction it sees, which would
    hide a session-level leak. A raw checkout of the same pooled connection does not
    go through it, so only a transaction-local setting leaves it clean."""
    eng = pg_one_connection

    async def as_tenant():
        tc.bind("org-a")
        with eng.begin() as conn:
            return _settings(conn)[2]

    pid = asyncio.run(as_tenant())
    raw = eng.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("select current_setting('hoja.org_id', true), pg_backend_pid()")
        value, raw_pid = cur.fetchone()
        raw.rollback()
    finally:
        raw.close()
    assert raw_pid == pid, "not the same pooled connection; the test proves nothing"
    assert (value or "") == "", f"tenant {value!r} survived on a pooled connection"


@needs_pg
def test_system_context_is_scoped_and_autobegin_is_covered(pg_one_connection) -> None:
    eng = pg_one_connection
    with tc.system():
        with eng.connect() as conn:  # autobegin path, no explicit begin()
            assert _settings(conn)[1] == "on"
    with eng.connect() as conn:
        assert (_settings(conn)[1] or "") == ""


@needs_pg
def test_binding_follows_into_the_thread_pool(pg_one_connection) -> None:
    eng = pg_one_connection

    def read():
        with eng.begin() as conn:
            return _settings(conn)[0]

    async def request():
        tc.bind("org-t")
        return await asyncio.to_thread(read)

    assert asyncio.run(request()) == "org-t"


@needs_pg
def test_app_under_rls_scopes_each_token_to_its_own_organization() -> None:
    """The real app, connected as a non-owner role, with RLS on issue and api_key.

    Token resolution must still work (it is a system lookup) and each token must
    then see only its own organization's issues, with no WHERE help from the test.
    """
    from hoja_server.app import create_app
    from hoja_server.db import make_engine, migrate, seed_tenant

    admin_url = make_url(POSTGRES_URL)
    name, role, pw = f"hoja_rls_{uuid.uuid4().hex[:10]}", f"hoja_rls_app_{uuid.uuid4().hex[:6]}", uuid.uuid4().hex
    admin = make_engine(admin_url.set(database="postgres").render_as_string(hide_password=False))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
        conn.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{pw}'"))
    owner = make_engine(admin_url.set(database=name).render_as_string(hide_password=False))
    try:
        migrate(owner)
        a = seed_tenant(owner, org_name="A", org_url_key="rls-a", user_email="a@a.test", token="tok-rls-a", demo_issues=True)
        b = seed_tenant(owner, org_name="B", org_url_key="rls-b", user_email="b@b.test", token="tok-rls-b", demo_issues=True)
        with owner.begin() as conn:
            counts = dict(conn.execute(text("select organization_id, count(*) from issue group by 1")).all())
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}; "
                              f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}; "
                              f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role}"))
            for t in ("issue", "api_key"):
                conn.execute(text(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY; "
                                  f"CREATE POLICY t ON {t} USING (current_setting('hoja.system', true) = 'on' "
                                  f"OR organization_id = current_setting('hoja.org_id', true))"))
        assert counts[a["org_id"]] > 0 and counts[b["org_id"]] > 0

        app_url = admin_url.set(database=name, username=role, password=pw).render_as_string(hide_password=False)
        query = {"query": "{ issues(first: 250) { nodes { id } } }"}
        with TestClient(create_app(database_url=app_url)) as client:
            seen = {}
            for org, tok in ((a["org_id"], "tok-rls-a"), (b["org_id"], "tok-rls-b")):
                r = client.post("/graphql", json=query, headers={"Authorization": f"Bearer {tok}"})
                body = r.json()
                assert "errors" not in body, body
                seen[org] = len(body["data"]["issues"]["nodes"])
            anon = client.post("/graphql", json=query).json()
        assert seen == counts, (seen, counts)
        assert "errors" in anon  # unauthenticated: no tenant, no data
        with make_engine(app_url).connect() as conn:  # no context at all: fail closed
            assert conn.execute(text("select count(*) from issue")).scalar() == 0
    finally:
        owner.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
