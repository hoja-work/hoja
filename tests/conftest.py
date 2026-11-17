"""Shared test configuration.

Session cookies are marked Secure except for plain-http localhost (the dev
exemption), so in-process TestClients default to http://localhost.

This module also carries the two pieces of machinery that let
``scripts/check.sh`` shard the suite across processes without changing what any
individual test proves:

* a PostgreSQL database per xdist worker (cloned from one migrated template), so
  two workers never share a schema or a role; and
* a SQLite fast path for ``migrate()`` that clones one already-migrated file
  instead of replaying 71 revisions in every test that builds a fresh database
  (the same file ``migrate()`` would have produced, byte for byte in content).

Both are opt-out (``HOJA_TEST_NO_CLONE=1``) and neither can make a test pass
that would have failed: on any condition this file does not recognise, the real
``migrate()`` runs unchanged.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    from starlette import testclient as _testclient
except ImportError:  # pragma: no cover - backend extras not installed
    _testclient = None

if _testclient is not None:
    _original_init = _testclient.TestClient.__init__

    def _localhost_init(self, *args, base_url: str = "http://localhost", **kwargs):
        _original_init(self, *args, base_url=base_url, **kwargs)

    _testclient.TestClient.__init__ = _localhost_init


# --------------------------------------------------------------------------
# PostgreSQL: one database per xdist worker, cloned from a migrated template.
# --------------------------------------------------------------------------

def _isolate_postgres_worker_database() -> None:
    """Point HOJA_TEST_POSTGRES_URL at this worker's own clone of the template.

    Runs at import time because the test modules that gate on PostgreSQL read the
    variable into a module constant before any fixture executes.

    Two suites run concurrently under scripts/check.sh (the full suite and the RLS
    suite), and both name their workers gw0..gwN. HOJA_TEST_DB_PREFIX keeps their
    clones apart so one suite's ``DROP DATABASE ... WITH (FORCE)`` can never pull
    the database out from under the other.
    """
    url = os.environ.get("HOJA_TEST_POSTGRES_URL", "").strip()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "").strip()
    if not url or not worker or os.environ.get("HOJA_TEST_NO_CLONE") == "1":
        return
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    source = parsed.database
    if not source:
        return
    target = f"{source}_{os.environ.get('HOJA_TEST_DB_PREFIX', '')}{worker}"
    import psycopg

    admin = parsed.set(drivername="postgresql", database="postgres")
    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
        # CREATE DATABASE ... TEMPLATE blocks while anyone is attached to the
        # template. Waiting forever looks like a hung suite, so wait briefly and
        # fall back to a plain empty database — which is what the clone of an
        # empty template is.
        conn.execute("SET lock_timeout = '20s'")
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
        # TEMPLATE refuses to copy a database another session is connected to, so a
        # leftover clone from a killed run is dropped first rather than reused: a
        # reused clone could carry state from that run into this one.
        if row:
            conn.execute(f'DROP DATABASE "{target}" WITH (FORCE)')
        try:
            conn.execute(f'CREATE DATABASE "{target}" TEMPLATE "{source}"')
        except psycopg.errors.LockNotAvailable:
            conn.execute("ROLLBACK")
            sys.stderr.write(
                f"conftest: template database {source!r} is in use; creating an "
                f"empty {target!r} instead (same content: the template is empty)\n")
            conn.execute(f'CREATE DATABASE "{target}"')
    os.environ["HOJA_TEST_POSTGRES_URL"] = parsed.set(
        database=target).render_as_string(hide_password=False)


# --------------------------------------------------------------------------
# SQLite: migrate once per process, then clone the file.
# --------------------------------------------------------------------------

_template_path: str | None = None
# CURRENT_SCHEMA_REVISION as this process found it. Tests that pin the constant
# lower want a legacy database, and a clone of the newest shape is not that;
# comparing against the revision captured at import keeps the decision free of
# whatever a previous test did to the constant.
_default_revision = None
# The unpatched migrate(), captured before the fast path replaces the module
# attribute: importing it lazily would find the patched wrapper and recurse.
_REAL_MIGRATE = None


def _sqlite_template() -> str:
    global _template_path
    if _template_path is not None and os.path.exists(_template_path):
        return _template_path
    from hoja_server.db import make_engine

    directory = tempfile.mkdtemp(prefix="hoja-migrate-template-")
    path = os.path.join(directory, "template.db")
    engine = make_engine(path)
    try:
        _REAL_MIGRATE(engine)
        # make_engine opens SQLite in WAL mode, so the schema lives partly in the
        # -wal sidecar until it is checkpointed. Fold it back into the main file:
        # the clone is a single file and must not inherit a half-written journal.
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        engine.dispose()
    _template_path = path
    return path


def _sqlite_has_tables(path: str) -> bool:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            return bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone())
    except sqlite3.Error:
        return False


def _install_sqlite_migrate_fast_path() -> None:
    """Replace hoja_server.db.migrate with a clone for a brand-new SQLite file.

    Every other case (PostgreSQL, in-memory, a database that already has tables,
    an unreadable file) delegates to the real migration, including the second
    call a test makes to prove migrate() is idempotent.
    """
    global _REAL_MIGRATE, _default_revision
    if os.environ.get("HOJA_TEST_NO_CLONE") == "1":
        return
    from hoja_server import db as _db

    real_migrate = _db.migrate
    if getattr(real_migrate, "_hoja_clone_fast_path", False):
        return
    _REAL_MIGRATE = real_migrate
    _default_revision = _db.CURRENT_SCHEMA_REVISION

    def migrate(engine):
        if engine.dialect.name != "sqlite":
            return real_migrate(engine)
        from hoja_server import db as _db

        # A test that pins CURRENT_SCHEMA_REVISION lower wants a legacy database,
        # and a clone of the newest shape is not that database.
        if _db.CURRENT_SCHEMA_REVISION != _default_revision:
            return real_migrate(engine)
        database = engine.url.database
        if not database or database == ":memory:" or database.startswith("file:"):
            return real_migrate(engine)
        if _sqlite_has_tables(database):
            return real_migrate(engine)
        # Drop any pooled handle first: the copy truncates the file the engine has
        # open, and a connection that survived it would keep the pre-copy schema.
        engine.dispose()
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            stale = Path(database + suffix)
            if stale.exists():
                stale.unlink()
        shutil.copyfile(_sqlite_template(), database)
        return None

    migrate._hoja_clone_fast_path = True  # type: ignore[attr-defined]
    migrate.__wrapped__ = real_migrate  # type: ignore[attr-defined]
    _db.migrate = migrate


_isolate_postgres_worker_database()
_install_sqlite_migrate_fast_path()


# --------------------------------------------------------------------------
# The GraphQL schema and the MCP tool specs are pure functions of static SDL.
# --------------------------------------------------------------------------

def _install_schema_build_cache() -> None:
    """Build the GraphQL schema once per process instead of once per app.

    ``create_app`` reaches ``build_schema()`` through ``mcp_oauth.build_server``,
    and that call parses ~2 700 lines of SDL, validates all 216 MCP tool documents
    against it and rebuilds every resolver map: ~1.2 s on this host, ~330 s across
    the suite, for an object that cannot differ between two calls. ``app.py``
    already builds its own schema once at import and serves every request from it,
    so one shared schema per process is the arrangement the server itself uses.

    Nothing in the tests patches ``resolvers`` or ``mcp_tools`` (checked: no
    setattr/monkeypatch targets either module), and the cached objects are only
    read after construction. HOJA_TEST_NO_BUILD_CACHE=1 restores the old cost.
    """
    if os.environ.get("HOJA_TEST_NO_BUILD_CACHE") == "1":
        return
    from hoja_server import mcp_tools, resolvers

    if getattr(resolvers.build_schema, "_hoja_cached", False):
        return

    real_build_schema = resolvers.build_schema
    built = []

    def build_schema():
        if not built:
            built.append(real_build_schema())
        return built[0]

    build_schema._hoja_cached = True  # type: ignore[attr-defined]
    resolvers.build_schema = build_schema

    # Each _Spec parses and validates one tool document against the schema; the
    # resulting object is read-only afterwards (assemble() takes its arguments).
    real_spec = mcp_tools._Spec
    specs: dict[tuple, object] = {}

    class _CachedSpec(real_spec):  # type: ignore[misc, valid-type]
        def __new__(cls, schema, op, write, description):
            # The schema object is held by the cache entry, so its id() cannot be
            # recycled underneath the key.
            key = (id(schema), schema, op, write, description)
            spec = specs.get(key)
            if spec is None:
                spec = real_spec(schema, op, write, description)
                specs[key] = spec
            return spec

    mcp_tools._Spec = _CachedSpec


_install_schema_build_cache()
