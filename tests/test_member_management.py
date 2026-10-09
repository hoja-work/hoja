"""Member deactivate/reactivate and membership invite revoke through GraphQL."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select, update
from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import make_engine, membership_invitation, migrate, seed_tenant, user as user_table
from cliniar_server.store import Store

INVITE = "mutation($i: MemberInviteInput!) { memberInvite(input: $i) { success user { id email } } }"
DEACTIVATE = "mutation($u: String!) { memberDeactivate(userId: $u) { success user { id active } } }"
REACTIVATE = "mutation($u: String!) { memberReactivate(userId: $u) { success user { id active } } }"
REVOKE = "mutation($u: String!) { membershipInvitationRevoke(userId: $u) { success } }"
CREATE_INVITE = "mutation($u: String!) { membershipInvitationCreate(userId: $u) { success inviteCode } }"
ME = "{ viewer { id } }"


def _setup(tmp_path):
    db_path = tmp_path / "members.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Members Org", org_url_key="members-org",
                        user_email="owner@members.test", token="owner-token", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _add_member(client, token, email, name="Helper"):
    res = _gql(client, token, INVITE, {"i": {"name": name, "email": email}})
    assert res["data"]["memberInvite"]["success"] is True, res
    return res["data"]["memberInvite"]["user"]["id"]


def test_deactivated_member_loses_api_access_and_reactivation_restores_it(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member_id = _add_member(client, admin["token"], "helper@members.test")
        _, member_key = store.create_api_key(admin["org_id"], member_id, "helper key", "read_write", None)
        assert _gql(client, member_key, ME)["data"]["viewer"]["id"] == member_id

        off = _gql(client, admin["token"], DEACTIVATE, {"u": member_id})
        assert off["data"]["memberDeactivate"]["success"] is True, off
        assert off["data"]["memberDeactivate"]["user"]["active"] is False
        locked = _gql(client, member_key, ME)
        assert not (locked.get("data") or {}).get("viewer"), locked

        on = _gql(client, admin["token"], REACTIVATE, {"u": member_id})
        assert on["data"]["memberReactivate"]["success"] is True, on
        assert _gql(client, member_key, ME)["data"]["viewer"]["id"] == member_id


def test_deactivate_refuses_self_and_requires_admin_caller(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        member_id = _add_member(client, admin["token"], "second@members.test")
        self_off = _gql(client, admin["token"], DEACTIVATE, {"u": admin["user_id"]})
        assert self_off["data"]["memberDeactivate"]["success"] is False

        with engine.begin() as conn:
            conn.execute(update(user_table).where(user_table.c.id == member_id).values(admin=False))
        non_admin_attempt = _gql(client, admin["token"], DEACTIVATE, {"u": admin["user_id"]})
        assert non_admin_attempt["data"]["memberDeactivate"]["success"] is False  # self-guard still applies
        store = Store(engine)
        _, member_key = store.create_api_key(admin["org_id"], member_id, "non-admin key", "read_write", None)
        denied = _gql(client, member_key, DEACTIVATE, {"u": admin["user_id"]})
        assert denied.get("errors") or denied["data"]["memberDeactivate"]["success"] is False


def test_invite_revoke_removes_pending_link(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        member_id = _add_member(client, admin["token"], "pending@members.test")
        made = _gql(client, admin["token"], CREATE_INVITE, {"u": member_id})
        assert made["data"]["membershipInvitationCreate"]["inviteCode"], made
        revoked = _gql(client, admin["token"], REVOKE, {"u": member_id})
        assert revoked["data"]["membershipInvitationRevoke"]["success"] is True, revoked
        with engine.connect() as conn:
            assert conn.execute(select(membership_invitation.c.id).where(
                membership_invitation.c.profile_user_id == member_id)).first() is None
        again = _gql(client, admin["token"], REVOKE, {"u": member_id})
        assert again["data"]["membershipInvitationRevoke"]["success"] is False


def test_pending_invites_lists_unaccepted_and_hides_revoked(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    list_q = "{ pendingMembershipInvitations { userId email } }"
    with TestClient(app) as client:
        member_id = _add_member(client, admin["token"], "listed@members.test")
        assert _gql(client, admin["token"], CREATE_INVITE, {"u": member_id})["data"]["membershipInvitationCreate"]["inviteCode"]
        listed = _gql(client, admin["token"], list_q)["data"]["pendingMembershipInvitations"]
        assert [r["userId"] for r in listed] == [member_id]
        _gql(client, admin["token"], REVOKE, {"u": member_id})
        assert _gql(client, admin["token"], list_q)["data"]["pendingMembershipInvitations"] == []
