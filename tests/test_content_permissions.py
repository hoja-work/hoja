"""Creator-or-admin rule for issue archive/delete, comment delete, and initiative edit/archive."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import update
from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant, user as user_table
from cliniar_server.store import Store

ISSUE_CREATE = "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success issue { id identifier } } }"
ISSUE_ARCHIVE = "mutation($id: String!) { issueArchive(id: $id) { success } }"
COMMENT_CREATE = "mutation($i: CommentCreateInput!) { commentCreate(input: $i) { success comment { id } } }"
COMMENT_DELETE = "mutation($id: String!) { commentDelete(id: $id) { success } }"
INIT_CREATE = "mutation($i: InitiativeCreateInput!) { initiativeCreate(input: $i) { success initiative { id } } }"
INIT_UPDATE = "mutation($id: String!, $i: InitiativeUpdateInput!) { initiativeUpdate(id: $id, input: $i) { success } }"
INIT_ARCHIVE = "mutation($id: String!) { initiativeArchive(id: $id) { success } }"
INVITE = "mutation($i: MemberInviteInput!) { memberInvite(input: $i) { success user { id } } }"


def _setup(tmp_path):
    db_path = tmp_path / "perms.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Perm Org", org_url_key="perm-org",
                        user_email="owner@perm.test", token="owner-token", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _denied(res, field) -> None:
    """A denied mutation either raises a FORBIDDEN GraphQL error or reports success false."""
    if res.get("errors"):
        assert "Admin access required" in res["errors"][0]["message"], res
        return
    assert res["data"][field]["success"] is False, res


def _member(client, admin_token, store, org_id, email):
    res = _gql(client, admin_token, INVITE, {"i": {"name": email.split("@")[0], "email": email}})
    member_id = res["data"]["memberInvite"]["user"]["id"]
    _, key = store.create_api_key(org_id, member_id, "member key", "read_write", None)
    return member_id, key


def _team_key(store, org_id):
    return store.teams(org_id)[0]["key"]


def test_member_cannot_archive_or_delete_others_issue_but_creator_and_admin_can(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _, member_key = _member(client, admin["token"], store, admin["org_id"], "m1@perm.test")
        team = _team_key(store, admin["org_id"])
        owner_issue = _gql(client, admin["token"], ISSUE_CREATE,
                           {"i": {"title": "owner issue", "teamId": team}})["data"]["issueCreate"]["issue"]["identifier"]
        member_issue = _gql(client, member_key, ISSUE_CREATE,
                            {"i": {"title": "member issue", "teamId": team}})["data"]["issueCreate"]["issue"]["identifier"]

        _denied(_gql(client, member_key, ISSUE_ARCHIVE, {"id": owner_issue}), "issueArchive")
        assert _gql(client, member_key, ISSUE_ARCHIVE, {"id": member_issue})["data"]["issueArchive"]["success"] is True
        assert _gql(client, admin["token"], ISSUE_ARCHIVE, {"id": owner_issue})["data"]["issueArchive"]["success"] is True


def test_only_comment_author_or_admin_can_delete_comment(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _, member_key = _member(client, admin["token"], store, admin["org_id"], "m2@perm.test")
        team = _team_key(store, admin["org_id"])
        issue_id = _gql(client, admin["token"], ISSUE_CREATE,
                        {"i": {"title": "thread", "teamId": team}})["data"]["issueCreate"]["issue"]["identifier"]
        admin_comment = _gql(client, admin["token"], COMMENT_CREATE,
                             {"i": {"issueId": issue_id, "body": "by admin"}})["data"]["commentCreate"]["comment"]["id"]
        member_comment = _gql(client, member_key, COMMENT_CREATE,
                              {"i": {"issueId": issue_id, "body": "by member"}})["data"]["commentCreate"]["comment"]["id"]

        _denied(_gql(client, member_key, COMMENT_DELETE, {"id": admin_comment}), "commentDelete")
        assert _gql(client, member_key, COMMENT_DELETE, {"id": member_comment})["data"]["commentDelete"]["success"] is True


def test_initiative_edit_and_archive_limited_to_creator_or_admin(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _, member_key = _member(client, admin["token"], store, admin["org_id"], "m3@perm.test")
        ini = _gql(client, admin["token"], INIT_CREATE,
                   {"i": {"name": "Admin plan"}})["data"]["initiativeCreate"]["initiative"]["id"]

        _denied(_gql(client, member_key, INIT_UPDATE, {"id": ini, "i": {"name": "hijack"}}), "initiativeUpdate")
        _denied(_gql(client, member_key, INIT_ARCHIVE, {"id": ini}), "initiativeArchive")
        assert _gql(client, admin["token"], INIT_UPDATE, {"id": ini, "i": {"name": "Admin plan v2"}})["data"]["initiativeUpdate"]["success"] is True


def test_creator_regains_no_rights_after_demotion_and_admin_check_is_authoritative(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        _, member_key = _member(client, admin["token"], store, admin["org_id"], "m4@perm.test")
        team = _team_key(store, admin["org_id"])
        mine = _gql(client, member_key, ISSUE_CREATE,
                    {"i": {"title": "still mine", "teamId": team}})["data"]["issueCreate"]["issue"]["identifier"]
        assert _gql(client, member_key, ISSUE_ARCHIVE, {"id": mine})["data"]["issueArchive"]["success"] is True
        # A creator with no ownership of another issue cannot act on the admin's issue, even after
        # the admin is demoted: the admin check is evaluated on each call.
        admin_issue = _gql(client, admin["token"], ISSUE_CREATE,
                           {"i": {"title": "admin issue", "teamId": team}})["data"]["issueCreate"]["issue"]["identifier"]
        with engine.begin() as conn:
            conn.execute(update(user_table).where(user_table.c.id == admin["user_id"]).values(admin=False))
        assert _gql(client, admin["token"], ISSUE_ARCHIVE, {"id": admin_issue})["data"]["issueArchive"]["success"] is True
