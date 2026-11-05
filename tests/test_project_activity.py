"""Project.activity: events on a project's issues, newest first, filterable and paged."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

TOKEN = "activity-admin-token"
FEED = ("query($id: String!, $first: Int, $after: String, $types: [String!]) { project(id: $id) {"
        " activity(first: $first, after: $after, eventTypes: $types) { nodes { id eventType changes createdAt"
        " issue { identifier } actor { name } } pageInfo { hasNextPage endCursor } } } }")


def _gql(client, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert "errors" not in body, body
    return body["data"]


def test_project_activity_feed(tmp_path) -> None:
    db_path = tmp_path / "activity.db"
    engine = make_engine(db_path)
    migrate(engine)
    seed_tenant(engine, org_name="Activity Org", org_url_key="activity-org",
                user_email="admin@activity.test", token=TOKEN, demo_issues=False)
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        t = _gql(client, "{ teams { nodes { id states { nodes { id type } } } } }")["teams"]["nodes"][0]
        state = {s["type"]: s["id"] for s in t["states"]["nodes"]}
        mk = lambda name: _gql(client, "mutation($i: ProjectCreateInput!) { projectCreate(input: $i) { project { id } } }",
                               {"i": {"name": name, "teamIds": [t["id"]]}})["projectCreate"]["project"]["id"]
        pid, other = mk("P"), mk("Other")
        create = lambda title, project: _gql(client, "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { issue { id } } }",
                                             {"i": {"title": title, "teamId": t["id"], "projectId": project}})["issueCreate"]["issue"]["id"]
        a = create("A", pid)
        create("Elsewhere", other)
        loose = create("Loose", None)
        _gql(client, "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }",
             {"id": a, "i": {"stateId": state["completed"]}})
        _gql(client, "mutation($i: CommentCreateInput!) { commentCreate(input: $i) { success } }",
             {"i": {"issueId": a, "body": "shipped"}})
        # Moving an issue into the project is recorded as a projectId change.
        _gql(client, "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }",
             {"id": loose, "i": {"projectId": pid}})

        nodes = _gql(client, FEED, {"id": pid})["project"]["activity"]["nodes"]
        kinds = [n["eventType"] for n in nodes]
        assert kinds == ["issueUpdated", "commentCreated", "issueUpdated", "issueCreated", "issueCreated"], kinds
        assert nodes[0]["changes"]["projectId"] == {"from": None, "to": pid}
        assert nodes[2]["changes"]["stateId"]["to"] == state["completed"]
        assert all(n["issue"]["identifier"] for n in nodes)
        assert "Elsewhere" not in str(nodes)

        only = _gql(client, FEED, {"id": pid, "types": ["commentCreated"]})["project"]["activity"]["nodes"]
        assert [n["eventType"] for n in only] == ["commentCreated"]

        first = _gql(client, FEED, {"id": pid, "first": 2})["project"]["activity"]
        assert first["pageInfo"]["hasNextPage"]
        rest = _gql(client, FEED, {"id": pid, "first": 10, "after": first["pageInfo"]["endCursor"]})["project"]["activity"]
        assert [n["id"] for n in first["nodes"] + rest["nodes"]] == [n["id"] for n in nodes]
