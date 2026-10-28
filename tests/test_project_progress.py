"""Project.progress is derived from the project's issues, not the never-updated stored column."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant

TOKEN = "progress-admin-token"


def _gql(client, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert "errors" not in body, body
    return body["data"]


def test_project_progress_counts_done_over_non_canceled_issues(tmp_path) -> None:
    db_path = tmp_path / "progress.db"
    engine = make_engine(db_path)
    migrate(engine)
    seed_tenant(engine, org_name="Progress Org", org_url_key="progress-org",
                user_email="admin@progress.test", token=TOKEN, demo_issues=False)
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        t = _gql(client, "{ teams { nodes { id states { nodes { id type } } } } }")["teams"]["nodes"][0]
        state = {s["type"]: s["id"] for s in t["states"]["nodes"]}
        pid = _gql(client, "mutation($i: ProjectCreateInput!) { projectCreate(input: $i) { project { id progress } } }",
                   {"i": {"name": "P", "teamIds": [t["id"]]}})["projectCreate"]["project"]["id"]
        empty = _gql(client, "mutation($i: ProjectCreateInput!) { projectCreate(input: $i) { project { id } } }",
                     {"i": {"name": "Empty", "teamIds": [t["id"]]}})["projectCreate"]["project"]["id"]
        # 3 done, 3 open, 2 canceled: progress = 3 / 6 = 50%; canceled issues are out of scope.
        for kind, n in (("completed", 3), ("started", 2), ("unstarted", 1), ("canceled", 2)):
            for i in range(n):
                _gql(client, "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success } }",
                     {"i": {"title": f"{kind} {i}", "teamId": t["id"], "projectId": pid, "stateId": state[kind]}})

        def progress():
            nodes = _gql(client, "{ projects { nodes { id progress } } }")["projects"]["nodes"]
            return {n["id"]: n["progress"] for n in nodes}

        assert progress() == {pid: 50.0, empty: 0.0}
        single = _gql(client, "query($id: String!) { project(id: $id) { progress } }", {"id": pid})
        assert single["project"]["progress"] == 50.0

        # Moving one open issue to done is reflected immediately: 4 / 6.
        issue_id = _gql(client, "{ issues(first: 50) { nodes { id title } } }")["issues"]["nodes"]
        started = next(i["id"] for i in issue_id if i["title"] == "started 0")
        _gql(client, "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }",
             {"id": started, "i": {"stateId": state["completed"]}})
        assert progress()[pid] == pytest.approx(66.67)

        # A project update snapshots the derived progress, so Pulse shows the real number.
        upd = _gql(client, "mutation($i: ProjectUpdateCreateInput!) { projectUpdateCreate(input: $i)"
                           " { projectUpdate { progress } } }", {"i": {"projectId": pid, "body": "ok"}})
        assert upd["projectUpdateCreate"]["projectUpdate"]["progress"] == pytest.approx(66.67)
