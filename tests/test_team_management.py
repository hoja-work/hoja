"""Team update, archive, and delete through GraphQL."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import issue, make_engine, migrate, new_id, now_iso, seed_tenant, team, workflow_state


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
