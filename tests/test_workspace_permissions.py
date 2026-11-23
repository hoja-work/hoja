"""HOJA-490: workspace permissions and security policy.

The issue's acceptance bar, as tests:

  * a member without a grant is refused by the API (a GraphQL ``FORBIDDEN``
    error), not merely hidden in the UI; a grant makes the same call succeed and
    revoking it refuses the very next one;
  * an admin always holds every capability, and a grant in one workspace does
    not leak into another;
  * a session past the configured idle timeout is rejected on the next request;
  * a caller outside the IP allowlist is refused, except an admin (break-glass);
  * a sign-in method the policy removes is refused at /auth/login;
  * the migration survives a pre-populated revision-58 database and a second boot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import db as db_module
from hoja_server.app import create_app
from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    browser_session,
    make_engine,
    migrate,
    seed_tenant,
    token_hash,
)
from hoja_server.store import Store


PERMISSIONS = "{ workspacePermissions { capability label description memberAllowed } }"
GRANT = (
    "mutation($c: String!, $a: Boolean!) { workspacePermissionUpdate(capability: $c, "
    "memberAllowed: $a) { capability memberAllowed } }"
)
SECURITY = (
    "{ workspaceSecurityPolicy { idleTimeoutMinutes allowedSigninMethods ipAllowlist } }"
)
SECURITY_UPDATE = (
    "mutation($i: WorkspaceSecurityPolicyInput!) { workspaceSecurityUpdate(input: $i) "
    "{ idleTimeoutMinutes allowedSigninMethods ipAllowlist } }"
)
TEAM_CREATE = (
    "mutation($i: TeamCreateInput!) { teamCreate(input: $i) { success team { id key } } }"
)
INVITE = (
    "mutation($i: MemberInviteInput!) { memberInvite(input: $i) "
    "{ success user { id } apiKey } }"
)
LABEL_CREATE = (
    "mutation($i: IssueLabelCreateInput!) { issueLabelCreate(input: $i) "
    "{ success issueLabel { id name } } }"
)
LABEL_UPDATE = (
    "mutation($id: String!, $i: IssueLabelUpdateInput!) { issueLabelUpdate(id: $id, input: $i) "
    "{ success issueLabel { id name } } }"
)
ISSUE_CREATE = (
    "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success issue { id } } }"
)
ISSUE_DELETE = "mutation($id: String!) { issueDelete(id: $id) { success } }"


def _setup(tmp_path, name="permissions", org_key="permissions-org"):
    db_path = tmp_path / f"{name}.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Permissions Org", org_url_key=org_key,
                        user_email="owner@permissions.test", token="owner-token",
                        demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _member(store, client, admin, email="member@permissions.test"):
    from tests.test_member_management import _add_member
    member_id = _add_member(client, admin["token"], email)
    _, key = store.create_api_key(admin["org_id"], member_id, "member key", "read_write", None)
    return member_id, key


def _refused(result):
    return bool(result.get("errors")) and any(
        (e.get("extensions") or {}).get("code") == "FORBIDDEN" for e in result["errors"])


def _payload_refused(result, field):
    """A mutation that reports refusal in its payload instead of an error.

    Still the API refusing the call: no row is created and no id comes back. The
    two shapes exist because these mutations pre-date HOJA-490 and the frontend
    handles `success: false` on them; the tests assert the refusal either way.
    """
    body = (result.get("data") or {}).get(field) or {}
    return body.get("success") is False and not body.get("user") and not body.get("issueLabel")


def test_defaults_are_admin_only_and_member_is_refused_by_the_api(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        listed = _gql(client, admin["token"], PERMISSIONS)
        assert "errors" not in listed, listed
        rows = listed["data"]["workspacePermissions"]
        assert [r["capability"] for r in rows] == [
            "team.create", "member.invite", "task.delete", "label.manage", "extension.manage"]
        assert all(r["memberAllowed"] is False for r in rows), rows
        assert all(r["label"] and r["description"] for r in rows)

        _member_id, key = _member(store, client, admin)
        # Reads of the grant matrix are admin-only (it is workspace configuration).
        assert _refused(_gql(client, key, PERMISSIONS))
        # Each gated action refuses a member, at the API.
        assert _refused(_gql(client, key, TEAM_CREATE, {"i": {"name": "Shadow", "key": "SHD"}}))
        # Member management and label create keep their existing payload shape:
        # `success: false` with no row is the server's refusal, not a hidden button.
        assert _payload_refused(_gql(client, key, INVITE,
                                     {"i": {"name": "X", "email": "x@permissions.test"}}),
                                "memberInvite")
        assert _refused(_gql(client, key, LABEL_CREATE, {"i": {"name": "red"}}))
        team = _gql(client, admin["token"], "{ teams { nodes { id } } }")["data"]["teams"]["nodes"][0]
        own = _gql(client, admin["token"], ISSUE_CREATE,
                   {"i": {"teamId": team["id"], "title": "Admin task"}})
        assert _refused(_gql(client, key, ISSUE_DELETE,
                             {"id": own["data"]["issueCreate"]["issue"]["id"]}))
        # And the admin still can.
        ok = _gql(client, admin["token"], TEAM_CREATE, {"i": {"name": "Ops", "key": "OPS"}})
        assert ok["data"]["teamCreate"]["success"] is True, ok


def test_a_grant_allows_the_member_and_revocation_refuses_the_next_call(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _member_id, key = _member(store, client, admin)
        granted = _gql(client, admin["token"], GRANT,
                       {"c": "team.create", "a": True})
        assert granted["data"]["workspacePermissionUpdate"]["memberAllowed"] is True, granted
        made = _gql(client, key, TEAM_CREATE, {"i": {"name": "Member team", "key": "MEM"}})
        assert made["data"]["teamCreate"]["success"] is True, made

        revoked = _gql(client, admin["token"], GRANT, {"c": "team.create", "a": False})
        assert revoked["data"]["workspacePermissionUpdate"]["memberAllowed"] is False
        assert _refused(_gql(client, key, TEAM_CREATE, {"i": {"name": "Again", "key": "AGA"}}))


VIEWER_CAPS = "{ viewerCapabilities }"


def test_viewer_capabilities_follow_grants_so_the_ui_can_gate_on_them(tmp_path) -> None:
    """HOJA-734: the settings UI gates on what the API will allow, not on `admin`."""
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        owner = _gql(client, admin["token"], VIEWER_CAPS)
        assert owner["data"]["viewerCapabilities"] == sorted([
            "extension.manage", "label.manage", "member.invite", "task.delete", "team.create"])
        _member_id, key = _member(store, client, admin)
        assert _gql(client, key, VIEWER_CAPS)["data"]["viewerCapabilities"] == []
        _gql(client, admin["token"], GRANT, {"c": "label.manage", "a": True})
        assert _gql(client, key, VIEWER_CAPS)["data"]["viewerCapabilities"] == ["label.manage"]
        _gql(client, admin["token"], GRANT, {"c": "label.manage", "a": False})
        assert _gql(client, key, VIEWER_CAPS)["data"]["viewerCapabilities"] == []


def test_label_mutations_refuse_a_member_with_one_shape(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        created = _gql(client, admin["token"], LABEL_CREATE, {"i": {"name": "bug"}})
        label_id = created["data"]["issueLabelCreate"]["issueLabel"]["id"]
        _member_id, key = _member(store, client, admin)
        # Revision 58 raised for update and returned success:false for
        # create/delete, all from ad-hoc admin checks. HOJA-490 gives all three
        # the same FORBIDDEN error naming the capability that unlocks them.
        assert _refused(_gql(client, key, LABEL_UPDATE,
                             {"id": label_id, "i": {"name": "renamed"}}))
        _gql(client, admin["token"], GRANT, {"c": "label.manage", "a": True})
        renamed = _gql(client, key, LABEL_UPDATE, {"id": label_id, "i": {"name": "renamed"}})
        assert renamed["data"]["issueLabelUpdate"]["success"] is True, renamed


def test_task_delete_grant_is_enforced_on_the_real_mutation(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        team = _gql(client, admin["token"], "{ teams { nodes { id } } }")["data"]["teams"]["nodes"][0]
        made = _gql(client, admin["token"], ISSUE_CREATE,
                    {"i": {"teamId": team["id"], "title": "Delete me"}})
        iid = made["data"]["issueCreate"]["issue"]["id"]
        _member_id, key = _member(store, client, admin)
        assert _refused(_gql(client, key, ISSUE_DELETE, {"id": iid}))
        _gql(client, admin["token"], GRANT, {"c": "task.delete", "a": True})
        deleted = _gql(client, key, ISSUE_DELETE, {"id": iid})
        assert deleted["data"]["issueDelete"]["success"] is True, deleted


def test_a_grant_does_not_cross_workspaces(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    other = seed_tenant(engine, org_name="Other Org", org_url_key="other-org",
                        user_email="owner@other.test", token="other-token", demo_issues=False)
    with TestClient(app) as client:
        _gql(client, admin["token"], GRANT, {"c": "team.create", "a": True})
        listed = _gql(client, other["token"], PERMISSIONS)["data"]["workspacePermissions"]
        assert all(r["memberAllowed"] is False for r in listed), listed


def test_idle_timeout_rejects_the_next_request(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        raw = store.create_browser_session_for_token(admin["token"])[0]
        # Fresh session works.
        client.cookies.set("hoja_session", raw)
        assert _gql(client, "", "{ viewer { id } }")["data"]["viewer"]["id"]

        saved = _gql(client, admin["token"], SECURITY_UPDATE,
                     {"i": {"idleTimeoutMinutes": 30}})
        assert saved["data"]["workspaceSecurityUpdate"]["idleTimeoutMinutes"] == 30, saved
        # A recent sighting stays inside the window.
        assert _gql(client, "", "{ viewer { id } }")["data"]["viewer"]["id"]

        # Backdate the sighting past the window: the next request is refused.
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with engine.begin() as conn:
            conn.execute(browser_session.update()
                         .where(browser_session.c.token_hash == token_hash(raw))
                         .values(last_seen_at=stale))
        refused = client.post("/graphql", json={"query": "{ viewer { id } }"})
        assert refused.status_code in (401, 403), refused.text
        assert not (refused.json().get("data") or {}).get("viewer"), refused.text


def test_ip_allowlist_refuses_a_member_and_exempts_an_admin_for_break_glass(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _member_id, key = _member(store, client, admin)
        saved = _gql(client, admin["token"], SECURITY_UPDATE,
                     {"i": {"ipAllowlist": ["10.0.0.0/8"]}})
        assert saved["data"]["workspaceSecurityUpdate"]["ipAllowlist"] == ["10.0.0.0/8"], saved
        # The test client's address is not in 10/8 and cannot be parsed as one.
        refused = _gql(client, key, "{ viewer { id } }")
        assert not (refused.get("data") or {}).get("viewer"), refused
        # An admin keeps access so a wrong list cannot lock the workspace out of
        # the screen that fixes it.
        assert _gql(client, admin["token"], "{ viewer { id } }")["data"]["viewer"]["id"]


def test_signin_methods_are_enforced_at_login(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        assert store.set_password(admin["token"], "correct-horse-battery")
        ok = client.post("/auth/login", json={
            "email": "owner@permissions.test", "password": "correct-horse-battery"})
        assert ok.status_code == 200, ok.text
        saved = _gql(client, admin["token"], SECURITY_UPDATE,
                     {"i": {"allowedSigninMethods": ["google"]}})
        assert saved["data"]["workspaceSecurityUpdate"]["allowedSigninMethods"] == ["google"], saved
        denied = client.post("/auth/login", json={
            "email": "owner@permissions.test", "password": "correct-horse-battery"})
        assert denied.status_code == 403, denied.text
        assert denied.json().get("reason") == "signin_method_disabled", denied.text


def test_migration_upgrades_a_populated_revision_58_database(tmp_path) -> None:
    """The revision-59 migration on a database that already has rows.

    Built at 58 by pinning the constant, seeded with a member and a browser
    session, then migrated for real. A migration proven only on an empty
    database is unproven: the column addition and the empty tables have to
    coexist with rows that predate them.
    """
    db_path = tmp_path / "legacy.db"
    original = db_module.CURRENT_SCHEMA_REVISION
    engine = make_engine(db_path)
    try:
        db_module.CURRENT_SCHEMA_REVISION = 58
        migrate(engine)
        assert applied_schema_revision(engine) == 58
        admin = seed_tenant(engine, org_name="Legacy", org_url_key="legacy",
                            user_email="owner@legacy.test", token="legacy-token",
                            demo_issues=False)
        ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with engine.begin() as conn:
            conn.execute(browser_session.insert().values(
                id="legacy-session", token_hash=token_hash("legacy-raw"),
                user_id=admin["user_id"], organization_id=admin["org_id"],
                created_at=ts, expires_at=(datetime.now(timezone.utc) + timedelta(days=13)).isoformat()))
    finally:
        db_module.CURRENT_SCHEMA_REVISION = original

    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        from sqlalchemy import inspect as sa_inspect, select
        columns = {c["name"] for c in sa_inspect(conn).get_columns("browser_session")}
        assert "last_seen_at" in columns
        row = conn.execute(select(browser_session).where(
            browser_session.c.id == "legacy-session")).mappings().one()
        assert row["last_seen_at"] is None  # reads as created_at, pre-59 lifetime kept
        assert row["created_at"] == ts
        tables = set(sa_inspect(conn).get_table_names())
        assert {"workspace_permission", "workspace_security_policy"} <= tables
    migrate(engine)  # idempotent
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        from sqlalchemy import select as sa_select
        count = conn.execute(sa_select(browser_session.c.id)).all()
        assert [r[0] for r in count] == ["legacy-session"]
