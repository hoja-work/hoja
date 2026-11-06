"""GraphQL contracts for creating and maintaining issue hierarchies."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

CREATE_ISSUE = """
mutation($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title parent { id identifier } }
  }
}
"""
UPDATE_ISSUE = """
mutation($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue { id identifier title parent { id identifier } }
  }
}
"""
ONE_ISSUE = """
query($id: String!) {
  issue(id: $id) {
    id identifier parent { id identifier }
    children(first: 50) { nodes { id identifier title } }
  }
}
"""


@pytest.fixture
def world(tmp_path):
    path = tmp_path / "subissues.db"
    engine = make_engine(path)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Hierarchy", org_url_key="hierarchy", team_key="ENG",
        user_name="Hierarchy Admin", user_email="admin@example.test", demo_issues=False,
    )
    app = create_app(db_path=str(path))
    with TestClient(app) as client:
        yield client, seeded["token"]


def _gql(client, token, query, variables=None):
    return client.post(
        "/graphql", headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables or {}},
    ).json()


def _data(client, token, query, variables=None):
    result = _gql(client, token, query, variables)
    assert "errors" not in result, result
    return result["data"]


def _create(client, token, title, team="ENG", **extra):
    issue = _data(client, token, CREATE_ISSUE, {
        "input": {"title": title, "teamId": team, **extra},
    })["issueCreate"]["issue"]
    assert issue is not None
    return issue


def _issue(client, token, ident):
    return _data(client, token, ONE_ISSUE, {"id": ident})["issue"]


def test_graphql_subissues_accept_identifiers_reparent_and_detach(world) -> None:
    client, token = world
    root = _create(client, token, "Parent work")
    child = _create(client, token, "First step", parentId=root["identifier"].lower())
    other = _create(client, token, "Other parent")

    assert root["parent"] is None
    assert child["parent"]["id"] == root["id"]
    assert [node["id"] for node in _issue(client, token, root["id"])["children"]["nodes"]] == [child["id"]]

    reparented = _data(client, token, UPDATE_ISSUE, {
        "id": child["identifier"], "input": {"parentId": other["id"]},
    })["issueUpdate"]["issue"]
    assert reparented["parent"]["id"] == other["id"]
    assert _issue(client, token, root["identifier"])["children"]["nodes"] == []
    assert [node["id"] for node in _issue(client, token, other["id"])["children"]["nodes"]] == [child["id"]]

    detached = _data(client, token, UPDATE_ISSUE, {
        "id": child["id"], "input": {"parentId": None},
    })["issueUpdate"]["issue"]
    assert detached["parent"] is None
    assert _issue(client, token, other["identifier"])["children"]["nodes"] == []

    # Omitting parentId on update must preserve the current hierarchy.
    nested = _create(client, token, "Nested step", parentId=root["id"])
    unchanged = _data(client, token, UPDATE_ISSUE, {
        "id": nested["id"], "input": {"title": "Renamed step"},
    })["issueUpdate"]["issue"]
    assert unchanged["title"] == "Renamed step"
    assert unchanged["parent"]["id"] == root["id"]


def test_graphql_rejects_self_parent_cycles_and_cross_team_parents(world) -> None:
    client, token = world
    root = _create(client, token, "Root")
    child = _create(client, token, "Child", parentId=root["identifier"])
    grandchild = _create(client, token, "Grandchild", parentId=child["identifier"])

    for ident, proposed_parent in (
        (root["identifier"], root["identifier"]),
        (root["id"], grandchild["identifier"]),
    ):
        result = _gql(client, token, UPDATE_ISSUE, {
            "id": ident, "input": {"parentId": proposed_parent},
        })
        assert result["data"]["issueUpdate"] is None
        assert result["errors"][0]["extensions"] == {"code": "BAD_USER_INPUT", "field": "parentId"}
    assert _issue(client, token, root["id"])["parent"] is None

    _data(client, token, """
        mutation { teamCreate(input: {name: "Operations", key: "OPS"}) { success } }
    """)
    foreign = _create(client, token, "Foreign root", team="OPS")
    for parent in (foreign["identifier"], "ENG-9999"):
        result = _gql(client, token, CREATE_ISSUE, {
            "input": {"title": "Invalid child", "teamId": "ENG", "parentId": parent},
        })
        assert result["data"]["issueCreate"] is None
        assert result["errors"][0]["extensions"] == {"code": "BAD_USER_INPUT", "field": "parentId"}
