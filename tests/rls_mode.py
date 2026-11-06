"""Test-only: run the app under PostgreSQL row-level security as a restricted role.

With HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL set, fixtures that build a
Postgres workspace call ``restrict(owner_url, schema)`` after migrating and
seeding. It applies the policies a shared-tables deployment uses (the same
classification as Hoja-Cloud's ``scripts/hoja-rls``: ENABLE, not FORCE; every
policy admits ``hoja.system``; direct / organization / child tables derived from
the catalog) and returns a URL for a NOSUPERUSER NOBYPASSRLS role that owns
nothing. The fixture keeps its owner engine for setup; the app gets the
restricted URL, so every query the app makes is subject to the policies.

A path that forgets to bind a tenant then fails the existing tests instead of
failing in production.
"""
from __future__ import annotations

import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url

from hoja_server.db import make_engine

ENABLED = bool(os.environ.get("HOJA_TEST_RLS")) and bool(os.environ.get("HOJA_TEST_POSTGRES_URL"))

# Hosted global tables (Hoja-Cloud rls/global-tables.txt): not tenant data.
GLOBALS = ("global_identity", "identity_credential", "oauth_client", "oauth_state_nonce", "rate_limit_event",
           "schema_revision", "signup_flow", "signup_identity", "signup_eligibility", "signup_invite",
           "signup_session", "turnstile_use", "alembic_version")

_CUR = "current_setting('hoja.org_id', true)"
_SYS = "current_setting('hoja.system', true) = 'on' OR "


def _classify_sql(schema: str) -> str:
    arr = "ARRAY[" + ",".join(f"'{g}'" for g in GLOBALS) + "]::text[]"
    child = ("'(%sEXISTS (SELECT 1 FROM ' || quote_ident(:schema) || '.%I p WHERE p.%I = %I.%I "
             f"AND p.organization_id = {_CUR.replace(chr(39), chr(39) * 2)}))'")
    return f"""
WITH tabs AS (
  SELECT c.oid, c.relname::text AS relname,
         EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                 AND a.attname = 'organization_id' AND NOT a.attisdropped) AS has_org
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')),
fks AS (
  SELECT k.conrelid, a.attname::text AS col, a.attnotnull AS notnull,
         p.relname::text AS parent, pa.attname::text AS pcol
  FROM pg_constraint k
  JOIN tabs p ON p.oid = k.confrelid AND p.has_org
  JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
  JOIN pg_attribute pa ON pa.attrelid = k.confrelid AND pa.attnum = k.confkey[1]
  WHERE k.contype = 'f' AND array_length(k.conkey, 1) = 1),
cls AS (
  SELECT t.*, CASE
      WHEN t.relname = 'organization' THEN 'organization'
      WHEN t.has_org THEN 'direct'
      WHEN t.relname = ANY({arr}) THEN 'global'
      WHEN EXISTS (SELECT 1 FROM fks f WHERE f.conrelid = t.oid) THEN 'child'
      ELSE 'unresolved' END AS kind
  FROM tabs t)
SELECT relname, kind, CASE kind
    WHEN 'organization' THEN 'id = {_CUR.replace("'", "''")}'
    WHEN 'direct' THEN 'organization_id = {_CUR.replace("'", "''")}'
    WHEN 'child' THEN (
      SELECT string_agg(format({child},
                               CASE WHEN f.notnull THEN '' ELSE format('%I.%I IS NULL OR ', relname, f.col) END,
                               f.parent, f.pcol, relname, f.col), ' AND ' ORDER BY f.col)
      FROM fks f WHERE f.conrelid = cls.oid)
    ELSE NULL END AS expr
FROM cls ORDER BY kind, relname
"""


def classify(owner_url: str, schema: str = "public") -> list[tuple[str, str, str | None]]:
    engine = make_engine(owner_url)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(_classify_sql(schema)), {"schema": schema}).all()]
    finally:
        engine.dispose()


def restrict(owner_url: str, schema: str = "public") -> str:
    """Apply the policies in ``schema`` and return a URL for a restricted role."""
    rows = classify(owner_url, schema)
    unresolved = [name for name, kind, _ in rows if kind == "unresolved"]
    assert not unresolved, f"tables with no tenant classification: {unresolved}"
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
            for name, kind, expr in rows:
                if kind == "global":
                    continue
                full = _SYS + expr
                conn.execute(text(f'ALTER TABLE {q}."{name}" ENABLE ROW LEVEL SECURITY; '
                                  f'CREATE POLICY hoja_tenant ON {q}."{name}" FOR ALL '
                                  f"USING ({full}) WITH CHECK ({full})"))
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
