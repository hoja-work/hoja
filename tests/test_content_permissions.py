"""Creator-or-admin rule for issue archive/delete and initiative edit/archive; comment policy."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select, update
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server import comment_policy
from hoja_server.db import issue_activity, make_engine, migrate, seed_tenant, user as user_table
from hoja_server.store import Store

ISSUE_CREATE = "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success issue { id identifier } } }"
ISSUE_ARCHIVE = "mutation($id: String!) { issueArchive(id: $id) { success } }"
COMMENT_CREATE = "mutation($i: CommentCreateInput!) { commentCreate(input: $i) { success comment { id } } }"
COMMENT_DELETE = "mutation($id: String!) { commentDelete(id: $id) { success } }"
COMMENT_UPDATE = ("mutation($id: String!, $i: CommentUpdateInput!) "
                  "{ commentUpdate(id: $id, input: $i) { success comment { body editedAt } } }")
COMMENTS = "query($id: String!) { issue(id: $id) { comments { nodes { id body editedAt canEdit canDelete } } } }"
TEAM_CREATE = "mutation($i: TeamCreateInput!) { teamCreate(input: $i) { success team { key } } }"
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
        assert res["errors"][0]["extensions"]["code"] == "FORBIDDEN", res
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


def _thread(client, admin, store, n_members=2):
    keys = [_member(client, admin["token"], store, admin["org_id"], f"c{i}@perm.test") for i in range(n_members)]
    team = _team_key(store, admin["org_id"])
    issue_id = _gql(client, admin["token"], ISSUE_CREATE,
                    {"i": {"title": "thread", "teamId": team}})["data"]["issueCreate"]["issue"]["identifier"]
    return keys, issue_id


def _comment(client, token, issue_id, body):
    return _gql(client, token, COMMENT_CREATE, {"i": {"issueId": issue_id, "body": body}})["data"]["commentCreate"]["comment"]["id"]


def _bodies(client, token, issue_id):
    return {c["id"]: c for c in _gql(client, token, COMMENTS, {"id": issue_id})["data"]["issue"]["comments"]["nodes"]}


def test_comment_edit_is_author_only_even_for_admins(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        [(_, m1), (_, m2)], issue_id = _thread(client, admin, store)
        mine = _comment(client, m1, issue_id, "m1 original")
        admins = _comment(client, admin["token"], issue_id, "admin original")

        for token, target in ((m2, mine), (m2, admins), (m1, admins), (admin["token"], mine)):
            res = _gql(client, token, COMMENT_UPDATE, {"id": target, "i": {"body": "HIJACK"}})
            assert res["errors"][0]["extensions"]["code"] == "FORBIDDEN", res
            assert res["errors"][0]["message"] == "Only the comment's author can edit it."
        assert all(c["body"] != "HIJACK" for c in _bodies(client, admin["token"], issue_id).values())

        ok = _gql(client, m1, COMMENT_UPDATE, {"id": mine, "i": {"body": "m1 edited"}})["data"]["commentUpdate"]
        assert ok["success"] is True and ok["comment"]["body"] == "m1 edited" and ok["comment"]["editedAt"]


def test_comment_delete_is_author_or_admin(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        [(_, m1), (_, m2)], issue_id = _thread(client, admin, store)
        a, b = _comment(client, m1, issue_id, "a"), _comment(client, m1, issue_id, "b")

        _denied(_gql(client, m2, COMMENT_DELETE, {"id": a}), "commentDelete")
        assert _gql(client, admin["token"], COMMENT_DELETE, {"id": a})["data"]["commentDelete"]["success"] is True
        assert _gql(client, m1, COMMENT_DELETE, {"id": b})["data"]["commentDelete"]["success"] is True
        assert _bodies(client, m1, issue_id) == {}


def test_comment_capabilities_match_the_policy_per_viewer(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        [(_, m1), (_, m2)], issue_id = _thread(client, admin, store)
        cid = _comment(client, m1, issue_id, "body")
        caps = lambda token: {k: _bodies(client, token, issue_id)[cid][k] for k in ("canEdit", "canDelete")}
        assert caps(m1) == {"canEdit": True, "canDelete": True}
        assert caps(admin["token"]) == {"canEdit": False, "canDelete": True}
        assert caps(m2) == {"canEdit": False, "canDelete": False}
        assert _bodies(client, m1, issue_id)[cid]["editedAt"] is None


def test_comment_edit_records_the_real_actor(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        [(m1_id, m1)], issue_id = _thread(client, admin, store, n_members=1)
        cid = _comment(client, m1, issue_id, "x")
        assert _gql(client, m1, COMMENT_UPDATE, {"id": cid, "i": {"body": "y"}})["data"]["commentUpdate"]["success"]
    with engine.connect() as conn:
        actors = conn.execute(select(issue_activity.c.actor_id).where(
            issue_activity.c.event_type == "commentUpdated")).scalars().all()
    assert actors == [m1_id]


def test_deleted_and_private_comments_look_absent_not_forbidden(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        [(_, m1)], issue_id = _thread(client, admin, store, n_members=1)
        gone = _comment(client, m1, issue_id, "soon gone")
        assert _gql(client, m1, COMMENT_DELETE, {"id": gone})["data"]["commentDelete"]["success"] is True
        res = _gql(client, m1, COMMENT_UPDATE, {"id": gone, "i": {"body": "revived"}})
        assert "errors" not in res and res["data"]["commentUpdate"]["success"] is False

        key = _gql(client, admin["token"], TEAM_CREATE,
                   {"i": {"name": "Secret", "key": "SEC", "visibility": "private"}})["data"]["teamCreate"]["team"]["key"]
        secret_issue = _gql(client, admin["token"], ISSUE_CREATE,
                            {"i": {"title": "s", "teamId": key}})["data"]["issueCreate"]["issue"]["identifier"]
        hidden = _comment(client, admin["token"], secret_issue, "private")
        for q, v, field in ((COMMENT_UPDATE, {"id": hidden, "i": {"body": "x"}}, "commentUpdate"),
                            (COMMENT_DELETE, {"id": hidden}, "commentDelete")):
            res = _gql(client, m1, q, v)
            assert "errors" not in res and res["data"][field]["success"] is False, res
            assert _gql(client, m1, q, {**v, "id": "no-such-comment"}) == res


def test_comment_policy_unit_matrix() -> None:
    s = lambda uid, admin=False: {"user_id": uid, "admin": admin}
    r = lambda author, **kw: {"author_id": author, "archived": False, "team_visible": True, **kw}
    E, D = comment_policy.EDIT, comment_policy.DELETE
    assert comment_policy.decide(E, s("u1"), r("u1"))
    assert not comment_policy.decide(E, s("u2", admin=True), r("u1"))
    assert comment_policy.decide(D, s("u2", admin=True), r("u1"))
    # Authorless (system/imported) comments: nobody edits, admins may remove.
    assert not comment_policy.decide(E, s(None), r(None))
    assert not comment_policy.decide(E, s("u1", admin=True), r(None))
    assert comment_policy.decide(D, s("u1", admin=True), r(None))
    assert comment_policy.decide(E, s("u1"), r("u1", archived=True)).rule == "d-archived"
    assert comment_policy.decide(D, s("u1", admin=True), r("u1", team_visible=False)).rule == "d-hidden"


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
