"""Pulse comments: a reply belongs to a project update, and exactly one parent.

Covers the whole path the UI uses — GraphQL mutation, writer, store, migration — on a
fresh SQLite database, plus the two ways a caller gets the parent wrong (both parents
named, or neither) and the edit/delete authorisation that used to inner-join on issue.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

CREATE_PROJECT = 'mutation($n: String!){ projectCreate(input:{name:$n}){ success project{id} } }'
CREATE_UPDATE = (
    'mutation($p: String!, $b: String!){ projectUpdateCreate(input:{projectId:$p, body:$b})'
    ' { success projectUpdate{id} } }'
)
CREATE_COMMENT = (
    'mutation($i: CommentCreateInput!){ commentCreate(input:$i)'
    ' { success comment{ id body createdAt user{ name } } } }'
)
READ_FEED = (
    'query{ projectUpdates(first:5){ nodes{ id comments(first:20)'
    ' { nodes{ id body user{ name } } } } } }'
)
EDIT_COMMENT = 'mutation($c: String!){ commentUpdate(id:$c, input:{body:"edited"}){ success } }'
DELETE_COMMENT = 'mutation($c: String!){ commentDelete(id:$c){ success } }'


def _setup(tmp_path):
    db_path = tmp_path / "pulse.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Pulse Org", org_url_key="pulse-org",
                        user_email="owner@pulse.test", token="owner-token", demo_issues=False)
    return create_app(str(db_path), open_mode=False), admin


def _gql(client, token, query, variables=None):
    res = client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                      json={"query": query, "variables": variables or {}}).json()
    assert "errors" not in res, res["errors"]
    return res["data"]


def _post(client, admin):
    project = _gql(client, admin["token"], CREATE_PROJECT, {"n": "Hoja Mobile"})["projectCreate"]["project"]
    update = _gql(client, admin["token"], CREATE_UPDATE,
                  {"p": project["id"], "b": "Shipping the mobile app."})["projectUpdateCreate"]["projectUpdate"]
    return project, update


def test_reply_round_trips_and_appears_in_the_feed(tmp_path) -> None:
    app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        _project, update = _post(client, admin)
        created = _gql(client, admin["token"], CREATE_COMMENT,
                       {"i": {"projectUpdateId": update["id"], "body": "Does this include offline mode?"}})
        assert created["commentCreate"]["success"] is True
        assert created["commentCreate"]["comment"]["body"] == "Does this include offline mode?"
        assert created["commentCreate"]["comment"]["user"]["name"]

        nodes = _gql(client, admin["token"], READ_FEED)["projectUpdates"]["nodes"]
        replies = nodes[0]["comments"]["nodes"]
        assert [r["body"] for r in replies] == ["Does this include offline mode?"]


def test_the_author_can_edit_and_delete_a_pulse_reply(tmp_path) -> None:
    """The authorisation query used an inner join on issue, so a Pulse reply was
    invisible to its own author and neither edit nor delete could ever succeed."""
    app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        _project, update = _post(client, admin)
        cid = _gql(client, admin["token"], CREATE_COMMENT,
                   {"i": {"projectUpdateId": update["id"], "body": "first"}})["commentCreate"]["comment"]["id"]
        assert _gql(client, admin["token"], EDIT_COMMENT, {"c": cid})["commentUpdate"]["success"] is True
        assert _gql(client, admin["token"], DELETE_COMMENT, {"c": cid})["commentDelete"]["success"] is True
        assert _gql(client, admin["token"], READ_FEED)["projectUpdates"]["nodes"][0]["comments"]["nodes"] == []


@pytest.mark.parametrize("input_,expect", [
    ({"body": "no parent"}, "exactly one parent"),
    ({"issueId": "HOJ-1", "projectUpdateId": "x", "body": "two parents"}, "exactly one parent"),
])
def test_a_comment_needs_exactly_one_parent(tmp_path, input_, expect) -> None:
    app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        _project, update = _post(client, admin)
        payload = dict(input_)
        if payload.get("projectUpdateId") == "x":
            payload["projectUpdateId"] = update["id"]
        res = client.post("/graphql", headers={"Authorization": f"Bearer {admin['token']}"},
                          json={"query": CREATE_COMMENT, "variables": {"i": payload}}).json()
        assert "errors" in res, res
        assert expect in res["errors"][0]["message"].lower()


def test_a_reply_to_an_unknown_update_is_refused_without_a_row(tmp_path) -> None:
    app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        _post(client, admin)
        data = _gql(client, admin["token"], CREATE_COMMENT,
                    {"i": {"projectUpdateId": "does-not-exist", "body": "hi"}})
        assert data["commentCreate"]["success"] is False


def test_revision_75_upgrades_an_existing_database(tmp_path) -> None:
    """A fresh database gets the column from create_all; production gets it from
    revision 75. This rehearses the second path, because that is the one that runs
    against the live tenant.

    SQLite refuses to drop a column that a foreign key names, so the pre-75 shape is
    rebuilt rather than altered: the same columns the old table really had, and no
    project_update_id.
    """
    from sqlalchemy import inspect

    engine = make_engine(tmp_path / "upgrade.db")
    migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE comment RENAME TO comment_pre75")
        conn.exec_driver_sql(
            "CREATE TABLE comment (id VARCHAR PRIMARY KEY, organization_id VARCHAR,"
            " issue_id VARCHAR, body TEXT NOT NULL, user_id VARCHAR, url VARCHAR,"
            " created_at VARCHAR, updated_at VARCHAR, archived_at VARCHAR)")
        conn.exec_driver_sql("DROP TABLE comment_pre75")
        conn.exec_driver_sql("DELETE FROM schema_revision WHERE revision = 75")
    assert "project_update_id" not in {c["name"] for c in inspect(engine).get_columns("comment")}

    migrate(engine)

    assert "project_update_id" in {c["name"] for c in inspect(engine).get_columns("comment")}
    assert "ix_comment_project_update_id" in {i["name"] for i in inspect(engine).get_indexes("comment")}
