"""Remove a member (archive), and prove the removal is real and reversible.

Covers HOJA-81: "cant delete member or revoke or inactive member".
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import func, select
from starlette.testclient import TestClient

from hoja_server.app import create_app, create_app as _create_app  # noqa: F401
from hoja_server.db import (api_key as api_key_table, make_engine, migrate,
                            organization_membership, seed_tenant, user as user_table)

INVITE = "mutation($i: MemberInviteInput!) { memberInvite(input: $i) { success user { id email } } }"
ARCHIVE = "mutation($u: String!) { memberArchive(userId: $u) { success user { id active } } }"
ROLE = "mutation($u: String!, $a: Boolean!) { memberRoleUpdate(userId: $u, admin: $a) { success } }"
MEMBERS = "{ organizationMembers { nodes { id email active } } }"


def _setup(tmp_path):
    db_path = tmp_path / "archive.db"
    engine = make_engine(db_path)
    migrate(engine)
    seed_tenant(engine, org_name="Archive Org", org_url_key="archive-org",
                user_email="owner@archive.test", token="owner-token", demo_issues=False)
    return engine, create_app(str(db_path), open_mode=False)


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _invite(client, email, name="Helper"):
    res = _gql(client, "owner-token", INVITE, {"i": {"name": name, "email": email}})
    assert res["data"]["memberInvite"]["success"] is True, res
    return res["data"]["memberInvite"]["user"]["id"]


def test_archive_hides_the_member_and_revokes_access(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)
    member_id = _invite(client, "removed@archive.test")

    before = _gql(client, "owner-token", MEMBERS)["data"]["organizationMembers"]["nodes"]
    assert member_id in [m["id"] for m in before], "member should be listed before removal"

    res = _gql(client, "owner-token", ARCHIVE, {"u": member_id})
    assert res["data"]["memberArchive"]["success"] is True, res
    assert res["data"]["memberArchive"]["user"]["active"] is False

    after = _gql(client, "owner-token", MEMBERS)["data"]["organizationMembers"]["nodes"]
    assert member_id not in [m["id"] for m in after], "removed member must disappear from the list"

    with engine.connect() as conn:
        row = conn.execute(select(user_table.c.active, user_table.c.archived_at)
                           .where(user_table.c.id == member_id)).first()
        assert row.active is False
        assert row.archived_at is not None, "archive must be recorded, not implied by active=False"
        keys = conn.execute(select(func.count()).select_from(api_key_table).where(
            api_key_table.c.user_id == member_id,
            api_key_table.c.revoked_at.is_not(None))).scalar_one()
        assert keys >= 1, "the removed member's API keys must be revoked"
        membership = conn.execute(select(organization_membership.c.active).where(
            organization_membership.c.profile_user_id == member_id)).first()
        assert membership.active is False


def test_a_removed_member_can_be_re_invited_as_one_profile(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)
    member_id = _invite(client, "returning@archive.test")
    assert _gql(client, "owner-token", ARCHIVE, {"u": member_id})["data"]["memberArchive"]["success"] is True

    res = _gql(client, "owner-token", INVITE, {"i": {"name": "Returning", "email": "returning@archive.test"}})
    assert res["data"]["memberInvite"]["success"] is True, res
    assert res["data"]["memberInvite"]["user"]["id"] == member_id, "re-invite must restore the same profile"

    with engine.connect() as conn:
        rows = conn.execute(select(func.count()).select_from(user_table).where(
            func.lower(user_table.c.email) == "returning@archive.test")).scalar_one()
        assert rows == 1, "re-invite must not create a duplicate user row"
        archived = conn.execute(select(user_table.c.archived_at).where(
            user_table.c.id == member_id)).scalar_one()
        assert archived is None


def test_guards_refuse_self_removal_and_non_admins(tmp_path):
    _engine, app = _setup(tmp_path)
    client = TestClient(app)
    member_id = _invite(client, "plain@archive.test")

    me = _gql(client, "owner-token", "{ viewer { id } }")["data"]["viewer"]["id"]
    self_res = _gql(client, "owner-token", ARCHIVE, {"u": me})
    assert self_res["data"]["memberArchive"]["success"] is False, "an admin must not remove themselves"

    # Promote the member so they can act, then aim them at the owner.
    assert _gql(client, "owner-token", ROLE, {"u": member_id, "a": True})["data"]["memberRoleUpdate"]["success"] is True
    twice = _gql(client, "owner-token", ARCHIVE, {"u": member_id})
    assert twice["data"]["memberArchive"]["success"] is True, twice
    again = _gql(client, "owner-token", ARCHIVE, {"u": member_id})
    assert again["data"]["memberArchive"]["success"] is False, "archiving twice must not report success"
