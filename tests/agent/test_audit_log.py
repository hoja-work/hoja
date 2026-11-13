"""Audit log: append-only, org-scoped, one event per audited action (HOJA-602-A-8, WP05).

The three acceptance probes from the work package are the three tests below:
an app-role UPDATE or DELETE on ``audit_event`` is refused, a cross-tenant read
returns zero rows, and a table-driven list asserts exactly one event for each
action whose surface exists on this stack.

Append-only and cross-tenant both run on the database that enforces them. The
default fixture is SQLite (single-tenant local mode, trigger-enforced
append-only); with ``HOJA_TEST_RLS=1`` and ``HOJA_TEST_POSTGRES_URL`` the same
tests run as a restricted PostgreSQL role under the production policies, which
is the only way the cross-tenant probe means anything.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from starlette.testclient import TestClient

from hoja_server import audit
from hoja_server.app import create_app
from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    audit_event,
    make_engine,
    migrate,
    seed_tenant,
)
from hoja_server.store import Store
from tests import legacy_schema
from tests import rls_mode


def _post(client: TestClient, token: str, query: str) -> dict:
    response = client.post("/graphql", json={"query": query},
                           headers={"Authorization": f"Bearer {token}"})
    return response.json()


def _mutation(client: TestClient, token: str, query: str) -> dict:
    body = _post(client, token, query)
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    """Two workspaces on one database: the cross-tenant probe needs a second org."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "audit-log-master")
    engine, kwargs = rls_mode.workspace(tmp_path, "audit-log")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return engine, TestClient(app), alpha, bravo


def _events(engine, org_id: str, action: str | None = None) -> list[dict]:
    query = select(audit_event).where(audit_event.c.organization_id == org_id)
    if action:
        query = query.where(audit_event.c.action == action)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in
                conn.execute(query.order_by(audit_event.c.seq.asc()))]


# --------------------------------------------------------------- append-only
def test_the_app_role_cannot_update_or_delete_an_audit_row(two_workspaces):
    engine, _client, alpha, _bravo = two_workspaces
    seq = audit.record(engine, organization_id=alpha["org_id"], action="api_key.create",
                       actor_kind="human", actor_id=alpha["user_id"],
                       target_kind="api_key", target_id="key-1")

    with pytest.raises(DBAPIError) as refused_update:
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_event SET action = 'api_key.revoke' "
                              "WHERE seq = :seq"), {"seq": seq})
    assert "append-only" in str(refused_update.value)

    with pytest.raises(DBAPIError) as refused_delete:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_event WHERE seq = :seq"), {"seq": seq})
    assert "append-only" in str(refused_delete.value)

    # The row is still there, unchanged.
    rows = _events(engine, alpha["org_id"])
    assert [row["action"] for row in rows] == ["api_key.create"]


# ------------------------------------------------------------- cross tenant
def test_a_cross_tenant_read_returns_zero_rows(two_workspaces):
    engine, client, alpha, bravo = two_workspaces
    audit.record(engine, organization_id=bravo["org_id"], action="api_key.revoke",
                 actor_kind="human", actor_id=bravo["user_id"],
                 target_kind="api_key", target_id="bravo-key")

    body = _post(client, alpha["token"], '''
        query { auditEvents(first: 50) { nodes { seq action actorId } pageInfo { hasNextPage } } }''')
    assert "errors" not in body, body
    assert body["data"]["auditEvents"]["nodes"] == []

    # And the storage read is scoped even when a caller forgets the GraphQL layer.
    assert audit.read_events(engine, alpha["org_id"])["nodes"] == []
    assert len(audit.read_events(engine, bravo["org_id"])["nodes"]) == 1


def test_only_an_admin_reads_the_ledger(two_workspaces):
    engine, client, alpha, _bravo = two_workspaces
    member_id = _invite(client, alpha["token"])
    _metadata, member_token = Store(engine).create_api_key(
        alpha["org_id"], member_id, "member-key", "read")
    audit.record(engine, organization_id=alpha["org_id"], action="api_key.revoke",
                 actor_kind="human", actor_id=alpha["user_id"],
                 target_kind="api_key", target_id="some-key")
    body = _post(client, member_token, "query { auditEvents { nodes { seq } } }")
    assert body["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    # ... while the admin sees the row, rendered through every schema field.
    admin_body = _post(client, alpha["token"], '''
        query { auditEvents {
          nodes { seq action actorKind actorId onBehalfOf targetKind targetId
                  ip runner requestId detail prevHash rowHash createdAt }
          pageInfo { hasNextPage startCursor endCursor } } }''')
    assert "errors" not in admin_body, admin_body
    nodes = admin_body["data"]["auditEvents"]["nodes"]
    assert [row["action"] for row in nodes] == ["member.invite", "api_key.revoke"]
    assert nodes[1]["actorKind"] == "human"
    assert nodes[1]["actorId"] == alpha["user_id"]
    assert nodes[1]["targetId"] == "some-key"
    assert len(nodes[1]["rowHash"]) == 64
    assert nodes[1]["detail"] is None


# --------------------------------------------------------- one event per action
def _invite(client: TestClient, token: str, email: str = "second@alpha.test") -> str:
    data = _mutation(client, token, '''
        mutation { memberInvite(input: {name: "Second", email: "%s"}) { success user { id } } }'''
                      % email)
    return data["memberInvite"]["user"]["id"]


def _create_key(client: TestClient, token: str) -> str:
    data = _mutation(client, token, '''
        mutation { apiKeyCreate(label: "ci", access: "read") { success apiKey { id } } }''')
    return data["apiKeyCreate"]["apiKey"]["id"]


def _create_then_revoke(client: TestClient, token: str) -> None:
    key_id = _create_key(client, token)
    _mutation(client, token, 'mutation { apiKeyRevoke(id: "%s") }' % key_id)


def _seat(client: TestClient, token: str) -> str:
    """An invited member is the target every membership action below changes."""
    return _invite(client, token)


def _drive_key_create(client, token) -> None:
    _create_key(client, token)


def _drive_key_revoke(client, token) -> None:
    _create_then_revoke(client, token)


def _drive_invite(client, token) -> None:
    _invite(client, token)


def _drive_role(client, token) -> None:
    member_id = _seat(client, token)
    _mutation(client, token,
              'mutation { memberRoleUpdate(userId: "%s", admin: true) { success } }' % member_id)


def _drive_deactivate(client, token) -> None:
    member_id = _seat(client, token)
    _mutation(client, token,
              'mutation { memberDeactivate(userId: "%s") { success } }' % member_id)


def _drive_reactivate(client, token) -> None:
    member_id = _seat(client, token)
    _mutation(client, token, 'mutation { memberDeactivate(userId: "%s") { success } }' % member_id)
    _mutation(client, token, 'mutation { memberReactivate(userId: "%s") { success } }' % member_id)


def _drive_archive(client, token) -> None:
    member_id = _seat(client, token)
    _mutation(client, token, 'mutation { memberArchive(userId: "%s") { success } }' % member_id)


def _drive_restore(client, token) -> None:
    member_id = _seat(client, token)
    _mutation(client, token, 'mutation { memberArchive(userId: "%s") { success } }' % member_id)
    _mutation(client, token, 'mutation { memberRestore(userId: "%s") { success } }' % member_id)


# (action, driver). Every driver performs exactly one action of that kind; setup
# actions of other kinds are fine because the assertion counts one action only.
ACTIONS = [
    ("api_key.create", _drive_key_create),
    ("api_key.revoke", _drive_key_revoke),
    ("member.invite", _drive_invite),
    ("member.role.change", _drive_role),
    ("member.deactivate", _drive_deactivate),
    ("member.reactivate", _drive_reactivate),
    ("member.archive", _drive_archive),
    ("member.restore", _drive_restore),
]


@pytest.mark.parametrize("action,drive", ACTIONS, ids=[case[0] for case in ACTIONS])
def test_each_action_writes_exactly_one_event(two_workspaces, action, drive):
    engine, client, alpha, _bravo = two_workspaces
    drive(client, alpha["token"])
    rows = _events(engine, alpha["org_id"], action)
    assert len(rows) == 1, rows
    assert rows[0]["actor_id"] == alpha["user_id"]
    assert rows[0]["actor_kind"] == "human"
    assert audit.verify_chain(engine, alpha["org_id"]) is None


def test_a_failed_action_writes_no_event(two_workspaces):
    _engine, client, alpha, _bravo = two_workspaces
    # A label the server refuses: the key is never created, so nothing is audited.
    data = _mutation(client, alpha["token"], '''
        mutation { apiKeyCreate(label: "   ", access: "read") { success } }''')
    assert data["apiKeyCreate"]["success"] is False
    assert _events(_engine, alpha["org_id"], "api_key.create") == []


# ------------------------------------------------------------------ hash chain
def test_the_chain_notices_a_rewritten_row(two_workspaces):
    engine, _client, alpha, _bravo = two_workspaces
    org = alpha["org_id"]
    seqs = [audit.record(engine, organization_id=org, action="api_key.create",
                         actor_kind="human", actor_id=alpha["user_id"],
                         target_kind="api_key", target_id=f"key-{n}")
            for n in range(3)]
    assert audit.verify_chain(engine, org) is None
    assert audit.verify_chain(engine, org, limit=2) is None

    # The trigger refuses a rewrite for everyone, including the owner, so the
    # tamper the verifier has to catch is simulated by disabling it.
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("ALTER TABLE audit_event DISABLE TRIGGER trg_audit_event_append_only"))
        else:
            conn.execute(text("DROP TRIGGER trg_audit_event_no_update"))
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_event SET target_id = 'someone-else' "
                          "WHERE seq = :seq"), {"seq": seqs[1]})
    broken = audit.verify_chain(engine, org)
    assert broken == {"seq": seqs[1], "reason": "row_hash does not recompute"}
    # Every row after the rewritten one is suspect, and the verifier names the first.
    assert audit.verify_chain(engine, org, limit=1) is None


def test_record_refuses_an_unknown_action_and_a_missing_actor(two_workspaces):
    engine, _client, alpha, _bravo = two_workspaces
    with pytest.raises(audit.UnknownActionError):
        audit.record(engine, organization_id=alpha["org_id"], action="audit.handwave",
                     actor_kind="human", actor_id=alpha["user_id"])
    with pytest.raises(audit.AuditActorMissingError):
        audit.record(engine, organization_id=alpha["org_id"], action="api_key.create")


def test_a_leftover_actor_context_cannot_attribute_a_row(two_workspaces):
    """The trap the RLS run exposed: the request's actor ContextVar is not reset.

    ``tenant_context.enter`` sets it when a request authenticates and nothing
    clears it when the request ends, so an ``audit.record`` that trusted the
    ambient value would attribute the next action to whoever went last. Reading
    it here would silently inherit the key creation the line above performed.
    """
    engine, client, alpha, _bravo = two_workspaces
    _create_key(client, alpha["token"])
    with pytest.raises(audit.AuditActorMissingError):
        audit.record(engine, organization_id=alpha["org_id"], action="api_key.create")


# ------------------------------------------------------------------- upgrade
def test_a_revision_64_database_gains_the_audit_trail(tmp_path):
    """WP04 ships revision 64, so the upgrade path is 64 -> 65, not a fresh create.

    A production workspace that is already at 64 must gain the table and its
    trigger without a rewrite and without a data step.
    """
    engine = make_engine(tmp_path / "audit-upgrade.db")
    legacy_schema.create_schema(engine, 64)
    with engine.begin() as conn:
        # Revision 64 did not have this table; the ledger stops one short of it.
        conn.exec_driver_sql("DROP TABLE audit_event")
    assert applied_schema_revision(engine) == 64
    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION == 65

    admin = seed_tenant(engine, org_name="Upgraded", org_url_key="upgraded",
                        user_email="admin@upgraded.test", token="upgraded-tok")
    seq = audit.record(engine, organization_id=admin["org_id"], action="api_key.create",
                       actor_kind="human", actor_id=admin["user_id"])
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_event WHERE seq = :seq"), {"seq": seq})
