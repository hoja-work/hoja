"""Read-path audit for team visibility (IMPLEMENTATION-BRIEF item C).

Every query that can reach a team-scoped row must express the same rule. These
tests probe the paths that do not go through the issue/team list helpers:
a cycle fetched by id, an uploaded file attached to a private team's issue, and
the personal activity feed (whose `changes` payload repeats issue content).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    attachment,
    cycle,
    issue,
    issue_activity,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    user as user_table,
)

ADMIN = "audit-admin-token"
OUTSIDER = "audit-outsider-token"
CREATE_ISSUE = ("mutation($i: IssueCreateInput!) { issueCreate(input: $i) "
                "{ success issue { id identifier title } } }")
CYCLE = "query($id: String!) { cycle(id: $id) { id name } }"
ACTIVITY = ("{ myIssueActivity(first: 50) { nodes { id eventType changes "
            "issue { id identifier title } } } }")


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "audit.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Audit", org_url_key="audit",
                         user_email="admin@audit.test", token=ADMIN, demo_issues=False)
    ts = now_iso()
    outsider = new_id()
    secret = new_id()
    with engine.begin() as conn:
        conn.execute(user_table.insert().values(
            id=outsider, organization_id=seeded["org_id"], name="Out", email="out@audit.test",
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
    return {"engine": engine, "app": app, "org": seeded["org_id"], "admin": seeded["user_id"],
            "outsider": outsider, "secret": secret}


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _data(client, token, query, variables=None):
    body = _gql(client, token, query, variables)
    assert not body.get("errors"), body
    return body["data"]


def test_cycle_by_id_respects_team_visibility(world) -> None:
    """A cycle that belongs to a private team must not be readable by id."""
    engine = world["engine"]
    ts = now_iso()
    cycle_id = new_id()
    with engine.begin() as conn:
        conn.execute(cycle.insert().values(
            id=cycle_id, organization_id=world["org"], team_id=world["secret"], number=1,
            name="Secret sprint", starts_at=ts, ends_at=ts, created_at=ts, updated_at=ts))
    with TestClient(world["app"]) as client:
        assert _data(client, ADMIN, CYCLE, {"id": cycle_id})["cycle"]["name"] == "Secret sprint"
        assert _data(client, OUTSIDER, CYCLE, {"id": cycle_id})["cycle"] is None


def test_upload_serve_respects_issue_team_visibility(world, tmp_path) -> None:
    """A file attached to a private team's issue is not downloadable by a non-member."""
    engine = world["engine"]
    ts = now_iso()
    file_id = "deadbeefdeadbeefdeadbeefdeadbeef"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / f"{file_id}.txt").write_text("secret bytes")
    (uploads / f"{file_id}.json").write_text(json.dumps(
        {"org": world["org"], "filename": "secret.txt", "content_type": "text/plain", "size": 12}))
    with TestClient(world["app"]) as client:
        made = _data(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "Has a file", "teamId": world["secret"]}})["issueCreate"]["issue"]
    with engine.begin() as conn:
        conn.execute(attachment.insert().values(
            id=new_id(), organization_id=world["org"], issue_id=made["id"],
            creator_id=world["admin"], url=f"/api/uploads/{file_id}",
            title="secret.txt", created_at=ts))
    import hoja_server.db as db_module
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "default_db_dir", lambda: tmp_path)
        with TestClient(world["app"]) as client:
            allowed = client.get(f"/api/uploads/{file_id}",
                                 headers={"Authorization": f"Bearer {ADMIN}"})
            denied = client.get(f"/api/uploads/{file_id}",
                                headers={"Authorization": f"Bearer {OUTSIDER}"})
    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 404, denied.text


def test_my_issue_activity_hides_teams_the_viewer_cannot_see(world) -> None:
    """The activity feed repeats issue content in `changes`, so it must be team-scoped."""
    engine = world["engine"]
    ts = now_iso()
    with TestClient(world["app"]) as client:
        made = _data(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "Hidden plan", "teamId": world["secret"]}})["issueCreate"]["issue"]
    # The outsider used to be on the team and acted on the issue; then left.
    with engine.begin() as conn:
        conn.execute(issue_activity.insert().values(
            id=new_id(), organization_id=world["org"], issue_id=made["id"],
            actor_id=world["outsider"], event_type="issue.update",
            changes=json.dumps({"title": "Hidden plan"}), created_at=ts))
    with TestClient(world["app"]) as client:
        nodes = _data(client, OUTSIDER, ACTIVITY)["myIssueActivity"]["nodes"]
    assert made["id"] not in [n["issue"]["id"] for n in nodes if n.get("issue")]
    assert all("Hidden plan" not in json.dumps(n.get("changes")) for n in nodes)
