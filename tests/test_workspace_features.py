"""The workspace switch for the Agents screens (revision 76, Settings -> Workspace).

Off by default for every workspace, readable by every member (the sidebar asks it), writable by
admins only, audited on change, and scoped to one workspace. Runs on SQLite and, under
HOJA_TEST_RLS, as the restricted Postgres role.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from hoja_server import db as db_module  # noqa: E402
from hoja_server.app import create_app  # noqa: E402
from hoja_server.db import audit_event, organization, seed_tenant  # noqa: E402
from hoja_server.store import Store  # noqa: E402
from tests import rls_mode  # noqa: E402
from tests.test_workspace_permissions import _member  # noqa: E402

FEATURES = "{ workspaceFeatures { agentsEnabled } }"
UPDATE = ("mutation($on: Boolean!) { workspaceFeaturesUpdate(agentsEnabled: $on) "
          "{ agentsEnabled } }")


@pytest.fixture
def workspace(tmp_path):
    engine, kwargs = rls_mode.workspace(tmp_path, "workspace-features")
    admin = seed_tenant(engine, org_name="Features Org", org_url_key="features-org",
                        user_email="owner@features.test", token="owner-token", demo_issues=False)
    other = seed_tenant(engine, org_name="Other Org", org_url_key="other-org",
                        user_email="owner@other.test", token="other-token", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False)
    return engine, TestClient(app), admin, other


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _audit_rows(engine, org_id):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            select(audit_event).where(audit_event.c.organization_id == org_id,
                                      audit_event.c.action == "workspace.features.change")
            .order_by(audit_event.c.seq))]


def test_off_by_default_and_every_member_can_read_it(workspace):
    engine, client, admin, _other = workspace
    _member_id, member_key = _member(Store(engine), client, admin)
    for token in (admin["token"], member_key):
        body = _gql(client, token, FEATURES)
        assert "errors" not in body, body
        assert body["data"]["workspaceFeatures"] == {"agentsEnabled": False}


def test_an_admin_turns_it_on_and_off_and_each_change_is_audited(workspace):
    engine, client, admin, _other = workspace
    on = _gql(client, admin["token"], UPDATE, {"on": True})
    assert on["data"]["workspaceFeaturesUpdate"] == {"agentsEnabled": True}, on
    assert _gql(client, admin["token"], FEATURES)["data"]["workspaceFeatures"]["agentsEnabled"] is True
    # Saving the same value again is not a change, so it writes no audit row.
    _gql(client, admin["token"], UPDATE, {"on": True})
    off = _gql(client, admin["token"], UPDATE, {"on": False})
    assert off["data"]["workspaceFeaturesUpdate"] == {"agentsEnabled": False}, off

    rows = _audit_rows(engine, admin["org_id"])
    assert [r["detail"] if isinstance(r["detail"], dict) else __import__("json").loads(r["detail"])
            for r in rows] == [{"agentsEnabled": {"before": False, "after": True}},
                               {"agentsEnabled": {"before": True, "after": False}}]
    assert {r["actor_id"] for r in rows} == {admin["user_id"]}
    assert {(r["target_kind"], r["target_id"]) for r in rows} == {("organization", admin["org_id"])}


def test_a_member_is_refused_and_nothing_changes(workspace):
    engine, client, admin, _other = workspace
    _member_id, member_key = _member(Store(engine), client, admin)
    refused = _gql(client, member_key, UPDATE, {"on": True})
    assert refused.get("errors"), refused
    assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    assert _gql(client, admin["token"], FEATURES)["data"]["workspaceFeatures"]["agentsEnabled"] is False
    assert _audit_rows(engine, admin["org_id"]) == []


def test_turning_it_on_for_one_workspace_leaves_the_other_off(workspace):
    engine, client, admin, other = workspace
    _gql(client, admin["token"], UPDATE, {"on": True})
    assert _gql(client, other["token"], FEATURES)["data"]["workspaceFeatures"]["agentsEnabled"] is False
    with engine.connect() as conn:
        stored = dict(conn.execute(select(organization.c.id, organization.c.agents_enabled)).all())
    assert stored[admin["org_id"]] is True and stored[other["org_id"]] in (None, False)


def test_revision_76_adds_the_column_to_a_revision_75_database_and_replays(tmp_path):
    """75 -> 76 twice: the column probe makes a replayed deploy a no-op, and existing rows read off."""
    from sqlalchemy import inspect, text

    engine = db_module.make_engine(tmp_path / "rev75.db")
    db_module.migrate(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO organization (id, name, url_key) VALUES ('o1', 'Old', 'old')"))
        conn.exec_driver_sql("ALTER TABLE organization DROP COLUMN agents_enabled")
        conn.execute(text("DELETE FROM schema_revision WHERE revision >= 76"))
    assert "agents_enabled" not in {c["name"] for c in inspect(engine).get_columns("organization")}
    for _ in range(2):
        db_module.migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION >= 78
    with engine.connect() as conn:
        assert conn.execute(text("SELECT agents_enabled FROM organization WHERE id='o1'")).scalar() is None
