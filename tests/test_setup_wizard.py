"""The setup wizard's server contract, on a real app and a disposable database.

The wizard can only be as truthful as these answers:

* ``/onboarding/status`` distinguishes a fresh instance from a completed one;
* ``/onboarding/bootstrap`` writes the workspace, admin, team and session in one
  transaction and refuses a second attempt instead of creating a duplicate;
* ``/api/connectors/config`` reports what this deployment can connect without
  publishing a secret, so the integrations step is not offered when it could
  never complete;
* the project and invitation mutations the later steps use behave as the wizard
  assumes — in particular a project create is *not* idempotent, which is why the
  wizard reconciles by name before retrying.

Nothing here calls the network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    make_engine,
    migrate,
    organization,
    team,
    user,
)

BOOTSTRAP = {
    "name": "Alex Morgan",
    "email": "alex@example.com",
    "password": "a-sufficiently-long-password",
    "workspaceName": "Acme Studio",
    "workspaceKey": "acme-studio",
    "teamName": "Engineering",
    "teamKey": "ENG",
}

GOOGLE_ENV = {
    "HOJA_GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com",
    "HOJA_GOOGLE_CLIENT_SECRET": "GOCSPX-never-return-this",
    "HOJA_GOOGLE_REDIRECT_URI": "http://localhost/api/connectors/google/callback",
    # Encrypts the OAuth state; any non-empty deployment key is accepted.
    "HOJA_SECRET_KEY": "test-only-master-key",
}


@pytest.fixture
def app(tmp_path):
    engine = make_engine(tmp_path / "setup.db")
    migrate(engine)
    return engine, create_app(str(tmp_path / "setup.db"), open_mode=False)


def _clear_google_env(monkeypatch):
    for name in GOOGLE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_status_and_bootstrap_are_a_single_irreversible_step(app):
    engine, application = app
    client = TestClient(application)
    client.__enter__()
    try:
        assert client.get("/onboarding/status").json() == {"initialized": False, "singleOrg": False}

        created = client.post("/onboarding/bootstrap", json=BOOTSTRAP)
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["status"] == "created"
        assert body["workspace"]["urlKey"] == "acme-studio"
        assert body["apiKey"].startswith("lin_")
        assert body["teamId"]

        with engine.connect() as conn:
            assert conn.execute(select(organization.c.name)).scalar_one() == "Acme Studio"
            assert conn.execute(select(team.c.key)).scalar_one() == "ENG"
            assert conn.execute(select(user.c.email)).scalar_one() == "alex@example.com"

        # Duplicate submission is refused by the server, not only by a disabled button.
        again = client.post("/onboarding/bootstrap", json=BOOTSTRAP)
        assert again.status_code == 409
        assert "already set up" in again.json()["error"].lower()
        with engine.connect() as conn:
            assert len(conn.execute(select(organization.c.id)).all()) == 1
            assert len(conn.execute(select(user.c.id)).all()) == 1

        assert client.get("/onboarding/status").json()["initialized"] is True

        # The bootstrap cookie is a real session: the admin can read the workspace.
        viewer = client.post("/graphql", json={"query": "{ viewer { email admin } }"}).json()
        assert viewer["data"]["viewer"]["email"] == "alex@example.com"
        assert viewer["data"]["viewer"]["admin"] is True
    finally:
        client.__exit__(None, None, None)


def test_bootstrap_rejects_a_short_password_without_writing(app):
    engine, application = app
    client = TestClient(application)
    client.__enter__()
    try:
        short = dict(BOOTSTRAP, password="short")
        assert client.post("/onboarding/bootstrap", json=short).status_code == 400
        with engine.connect() as conn:
            assert conn.execute(select(organization.c.id)).first() is None
    finally:
        client.__exit__(None, None, None)


def test_connector_config_is_public_and_reveals_no_secret(app, monkeypatch):
    _engine, application = app
    client = TestClient(application)
    client.__enter__()
    try:
        # Public capability flags: available before a session, and still no secret.
        anonymous = client.get("/api/connectors/config")
        assert anonymous.status_code == 200
        assert anonymous.json() == {
            "googleWorkspace": False,
            "googleWorkspaceAgent": False,
            "notion": False,
            "notionAgent": False,
            "slack": False,
            "slackAgent": False,
        }

        client.post("/onboarding/bootstrap", json=BOOTSTRAP)
        _clear_google_env(monkeypatch)
        for name in ("HOJA_NOTION_CLIENT_ID", "HOJA_NOTION_CLIENT_SECRET", "HOJA_NOTION_REDIRECT_URI"):
            monkeypatch.delenv(name, raising=False)
        for name in ("HOJA_SLACK_CLIENT_ID", "HOJA_SLACK_CLIENT_SECRET", "HOJA_SLACK_REDIRECT_URI"):
            monkeypatch.delenv(name, raising=False)
        off = client.get("/api/connectors/config")
        assert off.status_code == 200
        assert off.json() == {
            "googleWorkspace": False,
            "googleWorkspaceAgent": False,
            "notion": False,
            "notionAgent": False,
            "slack": False,
            "slackAgent": False,
        }
        assert off.headers["cache-control"] == "no-store"

        for name, value in GOOGLE_ENV.items():
            monkeypatch.setenv(name, value)
        on = client.get("/api/connectors/config")
        assert on.status_code == 200
        # The wizard only needs `googleWorkspace`; `googleWorkspaceAgent` stays false here
        # because this deployment has no signing key yet (covered by the route tests).
        assert on.json() == {
            "googleWorkspace": True,
            "googleWorkspaceAgent": False,
            "notion": False,
            "notionAgent": False,
            "slack": False,
            "slackAgent": False,
        }
        assert "GOCSPX" not in on.text
        assert "cid.apps" not in on.text
    finally:
        client.__exit__(None, None, None)


def test_project_create_is_not_idempotent_so_the_wizard_reconciles_first(app):
    engine, application = app
    client = TestClient(application)
    client.__enter__()
    try:
        client.post("/onboarding/bootstrap", json=BOOTSTRAP)
        team_id = client.post("/graphql", json={"query": "{ teams(first:1){nodes{id}} }"}).json()["data"]["teams"]["nodes"][0]["id"]

        create = "mutation($i: ProjectCreateInput!){ projectCreate(input:$i){ success project{ id name } } }"
        first = client.post("/graphql", json={"query": create, "variables": {"i": {"name": "Launch", "teamIds": [team_id]}}}).json()
        second = client.post("/graphql", json={"query": create, "variables": {"i": {"name": "Launch", "teamIds": [team_id]}}}).json()
        assert first["data"]["projectCreate"]["success"] is True
        assert second["data"]["projectCreate"]["success"] is True
        assert first["data"]["projectCreate"]["project"]["id"] != second["data"]["projectCreate"]["project"]["id"]

        listed = client.post("/graphql", json={"query": "{ projects(first:100){nodes{id name}} }"}).json()
        assert len(listed["data"]["projects"]["nodes"]) == 2
    finally:
        client.__exit__(None, None, None)


def test_invitation_flow_is_reconcilable_by_email(app):
    engine, application = app
    client = TestClient(application)
    client.__enter__()
    try:
        client.post("/onboarding/bootstrap", json=BOOTSTRAP)
        teams = client.post("/graphql", json={"query": "{ teams(first:1){nodes{id}} }"}).json()["data"]["teams"]["nodes"]
        team_id = teams[0]["id"]

        invite = "mutation($i: MemberInviteInput!){ memberInvite(input:$i){ success user{ id email } } }"
        first = client.post("/graphql", json={"query": invite, "variables": {"i": {"name": "Sam", "email": "sam@example.com", "teamIds": [team_id]}}}).json()
        assert first["data"]["memberInvite"]["success"] is True
        user_id = first["data"]["memberInvite"]["user"]["id"]

        duplicate = client.post("/graphql", json={"query": invite, "variables": {"i": {"name": "Sam", "email": "sam@example.com", "teamIds": [team_id]}}}).json()
        assert duplicate["data"]["memberInvite"]["success"] is False

        members = client.post("/graphql", json={"query": "{ organizationMembers(first:100){nodes{id email}} }"}).json()
        emails = [row["email"] for row in members["data"]["organizationMembers"]["nodes"]]
        assert emails.count("sam@example.com") == 1, "lookup by email is how a retry avoids a second member"

        code = client.post("/graphql", json={"query": "mutation($id:String!){membershipInvitationCreate(userId:$id){success inviteCode}}", "variables": {"id": user_id}}).json()
        assert code["data"]["membershipInvitationCreate"]["success"] is True
        assert code["data"]["membershipInvitationCreate"]["inviteCode"]

        pending = client.post("/graphql", json={"query": "{ pendingMembershipInvitations{userId name email} }"}).json()
        assert pending["data"]["pendingMembershipInvitations"][0]["email"] == "sam@example.com"
    finally:
        client.__exit__(None, None, None)


def test_google_start_and_a_cancelled_callback_are_safe(app, monkeypatch):
    """The integrations step cannot even start without configuration, and a
    cancelled consent returns a clear refusal instead of a broken session."""
    _engine, application = app
    client = TestClient(application)
    client.__enter__()
    try:
        client.post("/onboarding/bootstrap", json=BOOTSTRAP)

        unavailable = client.get("/api/connectors/google/start?scope=org", follow_redirects=False)
        assert unavailable.status_code == 503

        for name, value in GOOGLE_ENV.items():
            monkeypatch.setenv(name, value)
        start = client.get("/api/connectors/google/start?scope=org", follow_redirects=False)
        assert start.status_code == 302
        location = start.headers["location"]
        assert location.startswith("https://accounts.google.com/")
        assert "code_challenge=" in location
        assert "GOCSPX" not in location

        cancelled = client.get("/api/connectors/google/callback?error=access_denied", follow_redirects=False)
        assert cancelled.status_code == 400
        assert "did not grant" in cancelled.json()["error"]
    finally:
        client.__exit__(None, None, None)
