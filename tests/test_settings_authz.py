"""HOJA-735: settings authorization gaps, each proven against a real non-admin member.

  * cycleUpdate refuses a cycle on a team the caller cannot see (it used to edit any cycle);
  * member.invite brings people in as members only: it cannot invite an admin, nor
    deactivate / reactivate / remove / restore an admin or mint a new invite link for one;
  * the last *active* admin cannot be demoted (deactivated admins no longer count);
  * a private team's knowledge docs and labels are invisible to non-members, on read and write.

Every refusal is paired with the allowed path, so a fix that simply broke the feature fails.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.db import cycle as cycle_table
from hoja_server.store import Store
from tests.test_workspace_permissions import GRANT, _gql, _member, _setup

TEAM_CREATE = "mutation($i: TeamCreateInput!) { teamCreate(input: $i) { success team { id key } } }"
CADENCE = "mutation($t: String!) { teamCycleCadenceEnable(teamId: $t, weekday: 0) { enabled } }"
CYCLE_UPDATE = ("mutation($id: String!, $i: CycleUpdateInput!) { cycleUpdate(id: $id, input: $i) "
                "{ success cycle { id name } } }")
INVITE = ("mutation($i: MemberInviteInput!) { memberInvite(input: $i) "
          "{ success user { id } } }")
DEACTIVATE = "mutation($u: String!) { memberDeactivate(userId: $u) { success } }"
REACTIVATE = "mutation($u: String!) { memberReactivate(userId: $u) { success } }"
ARCHIVE = "mutation($u: String!) { memberArchive(userId: $u) { success } }"
RESTORE = "mutation($u: String!) { memberRestore(userId: $u) { success } }"
NEW_LINK = "mutation($u: String!) { membershipInvitationCreate(userId: $u) { success inviteCode } }"
ROLE = "mutation($u: String!, $a: Boolean!) { memberRoleUpdate(userId: $u, admin: $a) { success } }"
LABEL_CREATE = ("mutation($i: IssueLabelCreateInput!) { issueLabelCreate(input: $i) "
                "{ success issueLabel { id } } }")
LABEL_SETTINGS = "{ issueLabelSettings { id name } }"


def _code(result) -> str | None:
    errors = result.get("errors") or []
    return (errors[0].get("extensions") or {}).get("code") if errors else None


def _private_team(client, admin, key="SEC"):
    made = _gql(client, admin["token"], TEAM_CREATE,
                {"i": {"name": f"Secret {key}", "key": key, "visibility": "private"}})
    assert made["data"]["teamCreate"]["success"] is True, made
    return made["data"]["teamCreate"]["team"]["id"]


def test_cycle_update_requires_seeing_the_cycles_team(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _member_id, key = _member(store, client, admin)
        secret = _private_team(client, admin)
        public = _gql(client, admin["token"], "{ teams { nodes { id key } } }")["data"]["teams"]["nodes"]
        public_id = next(t["id"] for t in public if t["id"] != secret)
        for team_id in (secret, public_id):
            enabled = _gql(client, admin["token"], CADENCE, {"t": team_id})
            assert "errors" not in enabled, enabled
        with engine.connect() as conn:
            secret_cycle = conn.execute(select(cycle_table.c.id).where(cycle_table.c.team_id == secret)).scalar()
            public_cycle = conn.execute(select(cycle_table.c.id).where(cycle_table.c.team_id == public_id)).scalar()
        assert secret_cycle and public_cycle

        refused = _gql(client, key, CYCLE_UPDATE, {"id": secret_cycle, "i": {"name": "Hijacked"}})
        assert _code(refused) == "NOT_FOUND", refused
        with engine.connect() as conn:
            assert conn.execute(select(cycle_table.c.name).where(cycle_table.c.id == secret_cycle)).scalar() != "Hijacked"

        # A member still edits the cycles of a team they can see, and an admin edits any.
        allowed = _gql(client, key, CYCLE_UPDATE, {"id": public_cycle, "i": {"name": "Sprint A"}})
        assert allowed["data"]["cycleUpdate"]["success"] is True, allowed
        by_admin = _gql(client, admin["token"], CYCLE_UPDATE, {"id": secret_cycle, "i": {"name": "Sprint S"}})
        assert by_admin["data"]["cycleUpdate"]["cycle"]["name"] == "Sprint S", by_admin


def test_member_invite_cannot_create_or_manage_admins(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _member_id, key = _member(store, client, admin)
        _gql(client, admin["token"], GRANT, {"c": "member.invite", "a": True})

        escalate = _gql(client, key, INVITE, {"i": {"name": "Mallory", "email": "mallory@permissions.test",
                                                    "admin": True}})
        assert _code(escalate) == "FORBIDDEN", escalate
        assert store.get_user(admin["org_id"], admin["user_id"])  # the workspace is untouched
        # The grant still does what it is for: inviting a member.
        plain = _gql(client, key, INVITE, {"i": {"name": "Pat", "email": "pat@permissions.test"}})
        pat = plain["data"]["memberInvite"]["user"]["id"]

        # A second admin the delegated member must not be able to touch.
        other = _gql(client, admin["token"], INVITE, {"i": {"name": "Ann", "email": "ann@permissions.test",
                                                            "admin": True}})
        ann = other["data"]["memberInvite"]["user"]["id"]
        for mutation in (DEACTIVATE, ARCHIVE, NEW_LINK):
            result = _gql(client, key, mutation, {"u": ann})
            assert _code(result) == "FORBIDDEN", (mutation, result)
        assert store.get_user(admin["org_id"], ann)["active"] is True

        # Members remain theirs to manage.
        assert _gql(client, key, DEACTIVATE, {"u": pat})["data"]["memberDeactivate"]["success"] is True
        assert _gql(client, key, REACTIVATE, {"u": pat})["data"]["memberReactivate"]["success"] is True

        # An admin removed by an admin cannot be brought back by the delegated member.
        assert _gql(client, admin["token"], ARCHIVE, {"u": ann})["data"]["memberArchive"]["success"] is True
        assert _code(_gql(client, key, RESTORE, {"u": ann})) == "FORBIDDEN"
        assert _gql(client, admin["token"], RESTORE, {"u": ann})["data"]["memberRestore"]["success"] is True


def test_last_active_admin_cannot_be_demoted_when_the_other_admin_is_deactivated(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        other = _gql(client, admin["token"], INVITE, {"i": {"name": "Ann", "email": "ann@permissions.test",
                                                            "admin": True}})
        ann = other["data"]["memberInvite"]["user"]["id"]
        assert _gql(client, admin["token"], DEACTIVATE, {"u": ann})["data"]["memberDeactivate"]["success"] is True
        # Ann is still an admin on paper but cannot sign in: the owner is the last active admin.
        demoted = _gql(client, admin["token"], ROLE, {"u": admin["user_id"], "a": False})
        assert demoted["data"]["memberRoleUpdate"]["success"] is False, demoted
        assert store.get_user(admin["org_id"], admin["user_id"])["admin"] is True
        # With Ann active again, the owner may step down.
        assert _gql(client, admin["token"], REACTIVATE, {"u": ann})["data"]["memberReactivate"]["success"] is True
        stepped = _gql(client, admin["token"], ROLE, {"u": admin["user_id"], "a": False})
        assert stepped["data"]["memberRoleUpdate"]["success"] is True, stepped


def test_private_team_labels_are_invisible_to_non_members(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _member_id, key = _member(store, client, admin)
        secret = _private_team(client, admin)
        made = _gql(client, admin["token"], LABEL_CREATE, {"i": {"name": "project-falcon", "teamId": secret}})
        assert made["data"]["issueLabelCreate"]["success"] is True, made
        _gql(client, admin["token"], LABEL_CREATE, {"i": {"name": "bug"}})
        member_sees = [lbl["name"] for lbl in _gql(client, key, LABEL_SETTINGS)["data"]["issueLabelSettings"]]
        assert "project-falcon" not in member_sees and "bug" in member_sees, member_sees
        admin_sees = [lbl["name"] for lbl in _gql(client, admin["token"], LABEL_SETTINGS)["data"]["issueLabelSettings"]]
        assert "project-falcon" in admin_sees
