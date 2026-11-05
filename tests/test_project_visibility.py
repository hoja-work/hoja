"""Project visibility and project/team linking.

A project carries a team dimension through ``project_team``. Before this change
``store.projects`` filtered on organization only, so a project linked solely to a
private team stayed visible — name, description, lead — to every member, and there
was no way to attach or detach teams after creation.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    make_engine,
    migrate,
    new_id,
    now_iso,
    project_team,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    user as user_table,
)

ADMIN = "project-admin-token"
OUTSIDER = "project-outsider-token"

PROJECT_CREATE = ("mutation($i: ProjectCreateInput!) { projectCreate(input: $i) "
                  "{ success project { id name teams(first: 10) { nodes { key } } } } }")
PROJECT_UPDATE = ("mutation($id: String!, $i: ProjectUpdateInput!) { projectUpdate(id: $id, input: $i) "
                  "{ success project { id teams(first: 10) { nodes { key } } } } }")
PROJECTS = "query { projects(first: 50) { nodes { id name } } }"
CREATE_ISSUE = ("mutation($i: IssueCreateInput!) { issueCreate(input: $i) "
                "{ success issue { id identifier title } } }")
PROJECT = "query($id: String!) { project(id: $id) { id name } }"


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "projects.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Projects", org_url_key="projects",
                         user_email="admin@projects.test", token=ADMIN, demo_issues=False)
    ts = now_iso()
    outsider = new_id()
    secret = new_id()
    with engine.begin() as conn:
        conn.execute(user_table.insert().values(
            id=outsider, organization_id=seeded["org_id"], name="Out", email="out@projects.test",
            active=True, admin=False, created_at=ts, updated_at=ts))
        conn.execute(api_key.insert().values(
            id=new_id(), token_hash=token_hash(OUTSIDER), label="out",
            user_id=outsider, organization_id=seeded["org_id"], created_at=ts))
        conn.execute(team.insert().values(
            id=secret, organization_id=seeded["org_id"], key="SEC", name="Secret",
            visibility="private", timezone="UTC", issue_counter=0,
            created_at=ts, updated_at=ts))
        seed_states_for_team(conn, seeded["org_id"], secret)
        conn.execute(team_member.insert().values(team_id=secret, user_id=seeded["user_id"]))
    app = create_app(str(db_path), open_mode=False)
    return {"engine": engine, "app": app, "org": seeded["org_id"],
            "admin": seeded["user_id"], "outsider": outsider, "secret": secret}


def _data(client, token, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert not body.get("errors"), body
    return body["data"]


def _team_keys(payload):
    return sorted(n["key"] for n in payload["project"]["teams"]["nodes"])


def test_projects_linked_only_to_a_private_team_are_hidden(world) -> None:
    """A project whose only team is private must not appear to a non-member."""
    with TestClient(world["app"]) as client:
        hidden_id = _data(client, ADMIN, PROJECT_CREATE, {"i": {"name": "Secret roadmap"}})[
            "projectCreate"]["project"]["id"]
        open_id = _data(client, ADMIN, PROJECT_CREATE, {"i": {"name": "Open roadmap"}})[
            "projectCreate"]["project"]["id"]
        with world["engine"].begin() as conn:
            conn.execute(project_team.insert().values(project_id=hidden_id, team_id=world["secret"]))
        admin_names = {n["name"] for n in _data(client, ADMIN, PROJECTS)["projects"]["nodes"]}
        outsider_names = {n["name"] for n in _data(client, OUTSIDER, PROJECTS)["projects"]["nodes"]}
        assert "Secret roadmap" in admin_names
        assert "Open roadmap" in outsider_names
        assert "Secret roadmap" not in outsider_names
        assert _data(client, OUTSIDER, PROJECT, {"id": hidden_id})["project"] is None
        assert _data(client, ADMIN, PROJECT, {"id": hidden_id})["project"]["name"] == "Secret roadmap"
        # an unlinked project keeps the historical org-wide behaviour
        assert _data(client, OUTSIDER, PROJECT, {"id": open_id})["project"]["name"] == "Open roadmap"


def test_project_update_can_attach_and_detach_teams(world) -> None:
    """projectUpdate accepts teamIds, so a project can join and leave teams after creation."""
    with TestClient(world["app"]) as client:
        created = _data(client, ADMIN, PROJECT_CREATE, {"i": {"name": "Roadmap"}})["projectCreate"]
        pid = created["project"]["id"]
        assert _team_keys(created) == []
        attached = _data(client, ADMIN, PROJECT_UPDATE, {"id": pid, "i": {"teamIds": [world["secret"]]}})
        assert attached["projectUpdate"]["success"] is True
        assert _team_keys(attached["projectUpdate"]) == ["SEC"]
        detached = _data(client, ADMIN, PROJECT_UPDATE, {"id": pid, "i": {"teamIds": []}})
        assert _team_keys(detached["projectUpdate"]) == []


def test_sync_bootstrap_respects_private_team_visibility(world) -> None:
    """The sync payload is built through the resolvers, so private material never enters it.

    ``/sync/bootstrap`` runs META_QUERY through graphql_sync with the viewer's context
    (app.py) and scopes issues in sync._issue_select, so a non-member must not receive
    a private team's project or issue. This test fails if either path ever goes direct.
    """
    with TestClient(world["app"]) as client:
        hidden_id = _data(client, ADMIN, PROJECT_CREATE, {"i": {"name": "Secret roadmap"}})[
            "projectCreate"]["project"]["id"]
        with world["engine"].begin() as conn:
            conn.execute(project_team.insert().values(project_id=hidden_id, team_id=world["secret"]))
        created = _data(client, ADMIN, CREATE_ISSUE, {"i": {"title": "secret work", "teamId": world["secret"]}})
        assert created["issueCreate"]["success"] is True

        outsider = client.get("/sync/bootstrap", headers={"Authorization": f"Bearer {OUTSIDER}"})
        admin = client.get("/sync/bootstrap", headers={"Authorization": f"Bearer {ADMIN}"})
        assert outsider.status_code == 200 and admin.status_code == 200
        assert "Secret roadmap" in admin.text and "secret work" in admin.text
        assert "Secret roadmap" not in outsider.text
        assert "secret work" not in outsider.text
        # visibility is part of the snapshot cache key, so the two viewers never share a payload
        assert outsider.headers["etag"] != admin.headers["etag"]
