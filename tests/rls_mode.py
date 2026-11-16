"""Test-only: run the app under PostgreSQL row-level security as a restricted role.

With HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL set, fixtures that build a
Postgres workspace call ``restrict(owner_url, schema)`` after migrating and
seeding. It applies the policies a shared-tables deployment uses and returns a
URL for a NOSUPERUSER NOBYPASSRLS role that owns nothing. The fixture keeps its
owner engine for setup; the app gets the restricted URL, so every query the app
makes is subject to the policies.

The policies come from ``hoja_server.rls_policy`` — the module ``/ready`` and
``hoja-serve migrate`` use — rather than from a second copy here. A test schema
and a deployed database are therefore bound by the same classification (ENABLE,
not FORCE; every policy admits ``hoja.system``; direct / organization / child
tables derived from the catalog), and the tests cannot pass under policies that
production would not install.

A path that forgets to bind a tenant then fails the existing tests instead of
failing in production.
"""
from __future__ import annotations

import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url

from hoja_server import rls_policy
from hoja_server.db import make_engine

ENABLED = bool(os.environ.get("HOJA_TEST_RLS")) and bool(os.environ.get("HOJA_TEST_POSTGRES_URL"))

# Hosted global tables: the server's own allowlist, not a second copy.
GLOBALS = rls_policy.GLOBAL_TABLES


def classify(owner_url: str, schema: str = "public") -> list[tuple[str, str, str | None]]:
    engine = make_engine(owner_url)
    try:
        with engine.connect() as conn:
            return [(name, kind, expr) for name, kind, expr in rls_policy.classify(conn, schema)]
    finally:
        engine.dispose()


def restrict(owner_url: str, schema: str = "public") -> str:
    """Apply the policies in ``schema`` and return a URL for a restricted role."""
    role, pw = f"hoja_rls_t_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    q = f'"{schema}"'
    engine = make_engine(owner_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT PASSWORD '{pw}'"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA {q} TO {role}; "
                              f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {q} TO {role}; "
                              f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {q} TO {role}; "
                              f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {q} TO {role}"))
            # Idempotent: migrate() may already have bound this schema.
            summary = rls_policy.heal(conn, schema)
            assert not summary["unresolved"], \
                f"tables with no tenant classification: {summary['unresolved']}"
    finally:
        engine.dispose()
    return make_url(owner_url).set(username=role, password=pw).render_as_string(hide_password=False)


def drop_role(owner_url: str, restricted_url: str) -> None:
    role = make_url(restricted_url).username
    engine = make_engine(owner_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {role}; DROP ROLE IF EXISTS {role}"))
    finally:
        engine.dispose()


def new_schema_url() -> tuple[str, str]:
    """A fresh schema in the test database, for fixtures with no teardown of their
    own (the schema and role stay in the disposable test database)."""
    base = os.environ["HOJA_TEST_POSTGRES_URL"]
    schema = f"rls_{uuid.uuid4().hex[:10]}"
    engine = make_engine(base)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
    finally:
        engine.dispose()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options=-csearch_path%3D{schema}", schema


def workspace(tmp_path, name: str):
    """A migrated database: (owner engine, create_app kwargs). SQLite by default;
    under HOJA_TEST_RLS a Postgres schema whose app URL is the restricted role.
    Policies are not forced, so the owner engine still seeds and inspects freely."""
    from hoja_server.db import migrate

    if ENABLED:
        owner_url, schema = new_schema_url()
        engine = make_engine(owner_url)
        migrate(engine)
        return engine, {"db_path": None, "database_url": restrict(owner_url, schema)}
    path = tmp_path / f"{name}.db"
    engine = make_engine(path)
    migrate(engine)
    return engine, {"db_path": str(path)}
