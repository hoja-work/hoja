"""Team update, archive, and delete through GraphQL."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import (
    comment, issue, make_engine, migrate, new_id, now_iso, seed_tenant, team, workflow_state,
    user as user_table,
)


def _setup(tmp_path):
    db_path = tmp_path / "team-manage.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Teams Org", org_url_key="teams-org",
                        user_email="admin@teams.test", token="teams-admin-token", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


CREATE = "mutation($input: TeamCreateInput!) { teamCreate(input: $input) { success team { id key } } }"
UPDATE = ("mutation($id: String!, $input: TeamUpdateInput!) { teamUpdate(id: $id, input: $input)"
          " { success team { id name key description } } }")
ARCHIVE = "mutation($id: String!) { teamArchive(id: $id) { success } }"
DELETE = "mutation($id: String!) { teamDelete(id: $id) { success } }"
TEAMS = "{ teams { nodes { id key } } }"


def test_team_update_archive_and_delete_admin_flow(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        tok = admin["token"]
        made = _gql(client, tok, CREATE, {"input": {"name": "Scratch", "key": "scr"}})
        team_id = made["data"]["teamCreate"]["team"]["id"]

        renamed = _gql(client, tok, UPDATE, {"id": team_id, "input": {"name": "Scratch 2", "key": "scr2", "description": "d"}})
        assert renamed["data"]["teamUpdate"]["success"] is True, renamed
        assert renamed["data"]["teamUpdate"]["team"]["name"] == "Scratch 2"
        assert renamed["data"]["teamUpdate"]["team"]["key"] == "SCR2"

        archived = _gql(client, tok, ARCHIVE, {"id": team_id})
        assert archived["data"]["teamArchive"]["success"] is True, archived
        listed = _gql(client, tok, TEAMS)
        assert team_id not in [n["id"] for n in listed["data"]["teams"]["nodes"]]

        deleted = _gql(client, tok, DELETE, {"id": team_id})
        assert deleted["data"]["teamDelete"]["success"] is True, deleted
        with engine.connect() as conn:
            from sqlalchemy import select
            assert conn.execute(select(team.c.id).where(team.c.id == team_id)).first() is None


def test_team_update_rejects_duplicate_and_bad_key(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        tok = admin["token"]
        a = _gql(client, tok, CREATE, {"input": {"name": "A", "key": "aaa"}})["data"]["teamCreate"]["team"]["id"]
        _gql(client, tok, CREATE, {"input": {"name": "B", "key": "bbb"}})
        dup = _gql(client, tok, UPDATE, {"id": a, "input": {"key": "BBB"}})
        assert dup.get("errors") and "already exists" in dup["errors"][0]["message"]
        bad = _gql(client, tok, UPDATE, {"id": a, "input": {"key": "no spaces"}})
        assert bad.get("errors")


def test_team_delete_refused_when_team_has_issues(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        tok = admin["token"]
        team_id = _gql(client, tok, CREATE, {"input": {"name": "Busy", "key": "bsy"}})["data"]["teamCreate"]["team"]["id"]
    # Seed a real issue on this team via the same engine (issue rows require a team and state).
    from sqlalchemy import select
    with engine.begin() as conn:
        state_id = conn.execute(select(workflow_state.c.id).where(workflow_state.c.team_id == team_id)).first()
        org_id = admin["org_id"]
        if state_id is None:
            pytest.skip("team created without seeded workflow states")
        conn.execute(issue.insert().values(
            id=new_id(), organization_id=org_id, team_id=team_id, number=1, identifier="BSY-1",
            title="Blocker", state_id=state_id[0], created_at=now_iso(), updated_at=now_iso(),
            priority=0, creator_id=admin["user_id"]))
    app2 = create_app(str(engine.url.database), open_mode=False)
    with TestClient(app2) as client:
        refused = _gql(client, admin["token"], DELETE, {"id": team_id})
        assert refused.get("errors"), refused
        assert "issues" in refused["errors"][0]["message"]


def test_non_admin_cannot_update_archive_or_delete(tmp_path) -> None:
    from sqlalchemy import update
    from cliniar_server.db import user as user_table
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id = _gql(client, admin["token"], CREATE, {"input": {"name": "Locked", "key": "lck"}})["data"]["teamCreate"]["team"]["id"]
        with engine.begin() as conn:
            conn.execute(update(user_table).where(user_table.c.id == admin["user_id"]).values(admin=False))
        for query, variables in ((UPDATE, {"id": team_id, "input": {"name": "x"}}), (ARCHIVE, {"id": team_id}), (DELETE, {"id": team_id})):
            res = _gql(client, admin["token"], query, variables)
            assert res.get("errors") and "Admin access required" in res["errors"][0]["message"], res


DELETE_WITH_ISSUES = "mutation($id: String!) { teamDelete(id: $id, deleteIssues: true) { success } }"


def _team_with_issue_and_comment(engine, admin, key):
    from sqlalchemy import select
    with TestClient(create_app(str(engine.url.database), open_mode=False)) as client:
        team_id = _gql(client, admin["token"], CREATE, {"input": {"name": key, "key": key}})["data"]["teamCreate"]["team"]["id"]
    with engine.begin() as conn:
        state_id = conn.execute(select(workflow_state.c.id).where(workflow_state.c.team_id == team_id)).first()[0]
        issue_id = new_id()
        conn.execute(issue.insert().values(
            id=issue_id, organization_id=admin["org_id"], team_id=team_id, number=1,
            identifier=f"{key.upper()}-1", title="Doomed", state_id=state_id,
            created_at=now_iso(), updated_at=now_iso(), priority=0, creator_id=admin["user_id"]))
        conn.execute(comment.insert().values(
            id=new_id(), organization_id=admin["org_id"], issue_id=issue_id,
            body="bye", user_id=admin["user_id"], created_at=now_iso(), updated_at=now_iso()))
    return team_id, issue_id


def test_team_delete_with_issues_removes_team_issues_and_comments(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    team_id, issue_id = _team_with_issue_and_comment(engine, admin, "dwi")
    from sqlalchemy import select
    with TestClient(create_app(str(engine.url.database), open_mode=False)) as client:
        assert _gql(client, admin["token"], DELETE, {"id": team_id}).get("errors")
        done = _gql(client, admin["token"], DELETE_WITH_ISSUES, {"id": team_id})
        assert done["data"]["teamDelete"]["success"] is True, done
    with engine.connect() as conn:
        assert conn.execute(select(team.c.id).where(team.c.id == team_id)).first() is None
        assert conn.execute(select(issue.c.id).where(issue.c.id == issue_id)).first() is None
        assert conn.execute(select(comment.c.id).where(comment.c.issue_id == issue_id)).first() is None


def test_non_admin_cannot_delete_team_with_issues(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    team_id, issue_id = _team_with_issue_and_comment(engine, admin, "nad")
    from sqlalchemy import update
    with engine.begin() as conn:
        conn.execute(update(user_table).where(user_table.c.id == admin["user_id"]).values(admin=False))
    with TestClient(create_app(str(engine.url.database), open_mode=False)) as client:
        denied = _gql(client, admin["token"], DELETE_WITH_ISSUES, {"id": team_id})
        assert denied.get("errors") and "Admin access required" in denied["errors"][0]["message"], denied
    with engine.connect() as conn:
        assert conn.execute(issue.select().where(issue.c.id == issue_id)).first() is not None


def test_team_delete_with_issues_also_removes_project_links_and_cycles(tmp_path) -> None:
    """deleteIssues=True deletes the team's work instead of dead-ending on projects/cycles."""
    from sqlalchemy import select

    from cliniar_server.db import cycle, project, project_team

    engine, app, admin = _setup(tmp_path)
    team_id, issue_id = _team_with_issue_and_comment(engine, admin, "prj")
    org_id = admin["org_id"]
    project_id, cycle_id = new_id(), new_id()
    with engine.begin() as conn:
        conn.execute(project.insert().values(
            id=project_id, organization_id=org_id, name="Linked project",
            created_at=now_iso(), updated_at=now_iso()))
        conn.execute(project_team.insert().values(project_id=project_id, team_id=team_id))
        conn.execute(cycle.insert().values(
            id=cycle_id, organization_id=org_id, team_id=team_id, number=1,
            name="Cycle 1", created_at=now_iso(), updated_at=now_iso()))
        conn.execute(issue.update().where(issue.c.id == issue_id).values(cycle_id=cycle_id))
    with TestClient(create_app(str(engine.url.database), open_mode=False)) as client:
        refused = _gql(client, admin["token"], DELETE, {"id": team_id})
        assert refused.get("errors") and "projects" in refused["errors"][0]["message"], refused
        done = _gql(client, admin["token"], DELETE_WITH_ISSUES, {"id": team_id})
        assert done["data"]["teamDelete"]["success"] is True, done
    with engine.connect() as conn:
        assert conn.execute(select(team.c.id).where(team.c.id == team_id)).first() is None
        assert conn.execute(select(issue.c.id).where(issue.c.id == issue_id)).first() is None
        assert conn.execute(select(cycle.c.id).where(cycle.c.id == cycle_id)).first() is None
        assert conn.execute(select(project_team.c.project_id)
                            .where(project_team.c.team_id == team_id)).first() is None
        # The project itself survives: it is workspace-level, only the team link goes.
        assert conn.execute(select(project.c.id).where(project.c.id == project_id)).first() is not None
