import os

import pytest
from sqlalchemy import text
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
    assert (status, body["reason"]) == (503, "rls_disabled")


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
