import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from starlette.testclient import TestClient

from hoja_server import tenant_context
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant
from tests import rls_mode

PG = os.environ.get("HOJA_TEST_POSTGRES_URL")


def _ready(app):
    res = TestClient(app).get("/ready")
    return res.status_code, res.json()


def test_ready_without_row_level_security(tmp_path):
    engine = make_engine(tmp_path / "ready.db")
    migrate(engine)
    seed_tenant(engine, org_name="A", org_url_key="a", user_email="a@a.test", token="a-tok", demo_issues=False)
    assert _ready(create_app(str(tmp_path / "ready.db"), open_mode=False)) == (200, {"status": "ready"})


def test_ready_requires_rls_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_REQUIRE_RLS", "1")
    engine = make_engine(tmp_path / "ready.db")
    migrate(engine)
    status, body = _ready(create_app(str(tmp_path / "ready.db"), open_mode=False))
    # Not "rls_disabled": there is nothing to disable, row-level security does not
    # exist here. A strict deployment on SQLite is the misconfiguration.
    assert (status, body["reason"]) == (503, "rls_unsupported")


@pytest.fixture
def shared(monkeypatch):
    if not PG:
        pytest.skip("HOJA_TEST_POSTGRES_URL not set")
    monkeypatch.setenv("HOJA_REQUIRE_RLS", "1")
    owner_url, schema = rls_mode.new_schema_url()
    owner = make_engine(owner_url)
    migrate(owner)
    for key in ("a", "b"):
        seed_tenant(owner, org_name=key.upper(), org_url_key=key, user_email=f"o@{key}.test",
                    token=f"{key}-tok", demo_issues=False)
    app_url = rls_mode.restrict(owner_url, schema)
    yield owner, app_url
    owner.dispose()


def test_ready_under_row_level_security(shared):
    _owner, app_url = shared
    assert _ready(create_app(None, database_url=app_url, open_mode=False)) == (200, {"status": "ready"})


def test_ready_fails_when_the_tenant_hook_binds_the_wrong_org(shared, monkeypatch):
    _owner, app_url = shared
    # The hook binds the tenant and the actor in one statement; only the tenant is
    # corrupted here, so the probe must still notice it.
    monkeypatch.setattr(tenant_context, "_SET",
                        "SELECT set_config('hoja.org_id', %s || '-x', true), set_config('hoja.system', %s, true), "
                        "set_config('hoja.actor_kind', %s, true), set_config('hoja.actor_id', %s, true), "
                        "set_config('hoja.on_behalf_of', %s, true)")
    status, body = _ready(create_app(None, database_url=app_url, open_mode=False))
    assert (status, body["reason"]) == (503, "tenant_read_failed")


def test_ready_fails_open_policy(shared):
    owner, app_url = shared
    with owner.begin() as conn:
        conn.execute(text("DROP POLICY hoja_tenant ON organization; "
                          "CREATE POLICY hoja_tenant ON organization FOR ALL USING (true) WITH CHECK (true)"))
    status, body = _ready(create_app(None, database_url=app_url, open_mode=False))
    assert (status, body["reason"]) == (503, "tenant_isolation_failed")


def test_ready_fails_when_rls_is_disabled(shared):
    owner, app_url = shared
    with owner.begin() as conn:
        conn.execute(text("ALTER TABLE organization DISABLE ROW LEVEL SECURITY"))
    status, body = _ready(create_app(None, database_url=app_url, open_mode=False))
    assert (status, body["reason"]) == (503, "rls_disabled")


@pytest.fixture
def pg_schema(monkeypatch):
    """A migrated, seeded schema plus its owner engine and schema name."""
    if not PG:
        pytest.skip("HOJA_TEST_POSTGRES_URL not set")
    monkeypatch.setenv("HOJA_REQUIRE_RLS", "1")
    owner_url, schema = rls_mode.new_schema_url()
    owner = make_engine(owner_url)
    migrate(owner)
    for key in ("a", "b"):
        seed_tenant(owner, org_name=key.upper(), org_url_key=key, user_email=f"o@{key}.test",
                    token=f"{key}-tok", demo_issues=False)
    yield owner, schema
    owner.dispose()


def _login_role(owner, schema: str, attrs: str = "LOGIN NOSUPERUSER NOBYPASSRLS",
                create: bool = False) -> str:
    """A fresh role with the app's grants, so a connection as it is a real candidate."""
    role, pw = f"hoja_probe_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    with owner.begin() as conn:
        conn.execute(text(f"CREATE ROLE {role} {attrs} PASSWORD '{pw}'"))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}; '
                          f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO {role}; '
                          f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA "{schema}" TO {role}; '
                          f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA "{schema}" TO {role}'))
        if create:
            conn.execute(text(f'GRANT CREATE ON SCHEMA "{schema}" TO {role}'))
    return make_url(owner.url).set(username=role, password=pw).render_as_string(hide_password=False)


def test_ready_rejects_a_role_that_bypasses_rls(pg_schema):
    """BYPASSRLS is the one-attribute version of the owner hole."""
    owner, schema = pg_schema
    url = _login_role(owner, schema, attrs="LOGIN NOSUPERUSER BYPASSRLS")
    status, body = _ready(create_app(None, database_url=url, open_mode=False))
    assert (status, body["reason"]) == (503, "rls_bypass_role")
    assert "BYPASSRLS" in body["detail"]


def test_ready_rejects_a_connection_that_owns_the_tables(pg_schema):
    """An owner skips unforced policies, so owning one table is already not bound."""
    owner, schema = pg_schema
    url = _login_role(owner, schema, create=True)
    with owner.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{schema}".organization_membership OWNER TO '
                          f"{make_url(url).username}"))
    status, body = _ready(create_app(None, database_url=url, open_mode=False))
    assert (status, body["reason"]) == (503, "rls_owner_role")
    assert "organization_membership" in body["detail"]


def test_ready_names_every_tenant_table_that_lost_its_policy(pg_schema):
    """The old probe read one table; the policies live on every tenant table.

    The body is what makes a refused deploy diagnosable: every failing table, with
    its own reason, and nothing that could be a credential.
    """
    owner, schema = pg_schema
    app_url = rls_mode.restrict(owner.url.render_as_string(hide_password=False), schema)
    with owner.begin() as conn:
        conn.execute(text('ALTER TABLE organization_membership DISABLE ROW LEVEL SECURITY'))
        conn.execute(text(f'DROP POLICY hoja_tenant ON "{schema}".project'))
    status, body = _ready(create_app(None, database_url=app_url, open_mode=False))
    assert (status, body["reason"]) == (503, "rls_disabled")
    named = {row["table"]: row["reason"] for row in body["tables"]}
    assert named == {"organization_membership": "rls_disabled", "project": "policy_missing"}
    assert "organization_membership (rls_disabled)" in body["detail"]
    assert "project (policy_missing)" in body["detail"]
    assert PG not in str(body) and "password" not in str(body).lower()
    rls_mode.drop_role(owner.url.render_as_string(hide_password=False), app_url)
