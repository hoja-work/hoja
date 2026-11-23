"""The self-healing RLS pass and the report that reads it, against a real Postgres.

Two things have to hold or the gate is theatre: a table created *after* the last
``hoja-rls apply`` must be bound by the next ``migrate``, and the report must name
it in the meantime. Both are catalog facts, so they cannot be faked; these tests
skip unless HOJA_TEST_POSTGRES_URL is set (the same gate as the RLS suites).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from hoja_server import cli, db, rls_policy
from tests import rls_mode

PG = os.environ.get("HOJA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG, reason="HOJA_TEST_POSTGRES_URL not set")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def deployment():
    """A migrated schema, its owner engine, and the app role's own connection.

    The gate is about the role the app connects as, so the verdict is read from
    that connection: the owner engine is a superuser here and every policy would
    (correctly) be reported as not binding it.
    """
    url, schema = rls_mode.new_schema_url()
    owner = db.make_engine(url)
    db.migrate(owner)
    app_url = rls_mode.restrict(url, schema)
    app = db.make_engine(app_url)
    yield owner, app, url, schema, app_url
    app.dispose()
    owner.dispose()


def _state(engine, schema: str) -> dict[str, tuple[bool, bool, str]]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relrowsecurity AND "
            "       EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid AND p.polname = 'hoja_tenant'), "
            "       coalesce(pg_get_expr(pol.polqual, pol.polrelid), '') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_policy pol ON pol.polrelid = c.oid AND pol.polname = 'hoja_tenant' "
            "WHERE n.nspname = :schema AND c.relkind = 'r'"), {"schema": schema}).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def test_migrate_binds_every_tenant_table(deployment):
    """The whole schema, not just `organization`: a bound deployment has no gaps."""
    _owner, app, _url, schema, _app_url = deployment
    with app.connect() as conn:
        report = rls_policy.report(conn, schema)
    assert report["bound"], report["detail"]
    assert not [row for row in report["tables"] if row["reason"]]
    # Every declared global table that exists is left alone, and is listed as such.
    globals_ = [row for row in report["tables"] if row["kind"] == rls_policy.GLOBAL]
    assert globals_ and all(not row["rls"] for row in globals_)


def test_a_table_added_after_the_heal_fails_the_gate_until_the_next_migrate(deployment):
    """The production failure, reproduced: a revision creates a table, nothing
    binds it, /ready refuses — and the next migrate heals it."""
    owner, app, _url, schema, _app_url = deployment
    with owner.begin() as conn:
        # Exactly what a later revision's create_table() produces: the owner's table.
        conn.execute(text(f'CREATE TABLE "{schema}".later_tenant ('
                          "id text primary key, organization_id text not null)"))
    with app.connect() as conn:
        before = rls_policy.report(conn, schema)
    assert not before["bound"]
    assert before["reason"] == rls_policy.RLS_DISABLED
    later = next(row for row in before["tables"] if row["table"] == "later_tenant")
    assert later["reason"] == rls_policy.RLS_DISABLED
    assert "later_tenant" in before["detail"]

    db.migrate(owner)  # head already applied; the binding pass still runs

    with app.connect() as conn:
        after = rls_policy.report(conn, schema)
    assert after["bound"], after["detail"]
    with owner.begin() as conn:
        conn.execute(text(f'DROP TABLE "{schema}".later_tenant'))


def test_the_self_heal_is_idempotent(deployment):
    """Running the pass twice converges: same flags, same policies, same bodies."""
    owner, app, _url, schema, _app_url = deployment
    first = _state(owner, schema)
    with owner.begin() as conn:
        again = rls_policy.heal(conn, schema)
    second = _state(owner, schema)
    assert first == second
    assert again["skipped"] == [] and again["unresolved"] == []
    with app.connect() as conn:
        assert rls_policy.report(conn, schema)["bound"]


def test_a_dropped_policy_is_restored_by_the_next_migrate(deployment):
    """Drift, not just new tables: a policy someone dropped comes back."""
    owner, app, _url, schema, _app_url = deployment
    with owner.begin() as conn:
        conn.execute(text(f'DROP POLICY hoja_tenant ON "{schema}".issue'))
    with app.connect() as conn:
        issue = next(r for r in rls_policy.tables(conn, schema) if r["table_name"] == "issue")
        assert rls_policy.table_reason(issue) == rls_policy.POLICY_MISSING
    db.migrate(owner)
    with app.connect() as conn:
        assert rls_policy.report(conn, schema)["bound"]


def test_the_report_names_the_role_and_every_unbound_table(deployment):
    owner, app, _url, schema, _app_url = deployment
    with owner.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{schema}".issue DISABLE ROW LEVEL SECURITY'))
        conn.execute(text(f'DROP POLICY hoja_tenant ON "{schema}".project'))
    with app.connect() as conn:
        report = rls_policy.report(conn, schema)
    reasons = {row["table"]: row["reason"] for row in report["tables"] if row["reason"]}
    assert reasons == {"issue": rls_policy.RLS_DISABLED, "project": rls_policy.POLICY_MISSING}
    assert report["role"]["name"].startswith(("hoja_rls_t_", "hoja_rls_"))
    assert report["role"]["superuser"] is False and report["role"]["bypassrls"] is False
    assert "issue (rls_disabled)" in report["detail"] and "project (policy_missing)" in report["detail"]
    # Nothing that could be a credential: table names and a role name, no DSN.
    body = json.dumps(report)
    assert "://" not in body and "postgresql" not in body and "@" not in body
    assert PG not in body and _app_url not in body


def test_the_operator_tool_and_the_app_classify_the_same_way(deployment):
    """Drift guard: `hoja-rls classify` and this module must agree on a real schema.

    They are two implementations on purpose — the operator tool must run on a host
    with psql and no app install — so the guard runs the operator's SQL directly.
    """
    owner, _app, _url, schema, _app_url = deployment
    path = ROOT / "infra" / "scripts" / "hoja-rls"
    if not path.is_file():
        pytest.skip("infra/scripts/hoja-rls is not in this checkout")
    spec = importlib.util.spec_from_loader(
        "hoja_rls_operator", importlib.machinery.SourceFileLoader("hoja_rls_operator", str(path)))
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    operator_globals = sorted(rls_policy.GLOBAL_TABLES - {"alembic_version"})
    with owner.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        try:
            cursor.execute(operator.classify_sql(operator_globals).replace("public", schema))
            theirs = [(r[0], r[1], r[2] or "") for r in cursor.fetchall()]
        finally:
            cursor.close()
        ours = rls_policy.classify(conn, schema)
    assert sorted(theirs) == sorted(ours)


def test_rls_report_prints_the_same_shape_as_ready(deployment, capsys):
    _owner, _app, _url, _schema, app_url = deployment
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["rls-report", "--database-url", app_url, "--json"])
    assert exit_code.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["bound"] is True and report["reason"] is None
    assert {row["table"] for row in report["tables"]} >= {"organization", "issue", "project"}
    assert app_url not in json.dumps(report)


def test_rls_report_refuses_a_bypass_role_and_says_why(deployment, capsys):
    """The command the coordinator runs against production: exit 1 and the reason."""
    owner, _app, _url, _schema, _app_url = deployment
    url = str(owner.url)
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["rls-report", "--database-url", url])
    assert exit_code.value.code == 1
    out = capsys.readouterr().out
    assert "NOT BOUND (rls_bypass_role)" in out
    assert "superuser=True" in out


def test_rls_report_is_read_only(deployment):
    """It may be pointed at production, so it must not change the catalog."""
    owner, app, _url, schema, _app_url = deployment
    before = _state(owner, schema)
    with app.connect() as conn:
        rls_policy.report(conn, schema)
    assert _state(owner, schema) == before


def test_rls_report_says_unsupported_on_sqlite(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["rls-report", "--database-url", f"sqlite:///{tmp_path / 'x.db'}"])
    assert exit_code.value.code == 2
    assert rls_policy.RLS_UNSUPPORTED in capsys.readouterr().err
