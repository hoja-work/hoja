"""Approval routes: approval is refused without a registered write executor, and only the requester can act."""

from __future__ import annotations

from sqlalchemy import select
from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.connector_actions import PENDING, queue_action
from cliniar_server.db import connector_account, connector_action, make_engine, migrate, seed_tenant


def _setup(tmp_path):
    db_path = tmp_path / "actions.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Act Org",
        org_url_key="act-org",
        user_email="owner@act.test",
        token="owner-token",
        demo_issues=False,
    )
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct-1",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="google",
                external_account="owner@act.test",
                scopes="",
                encrypted_refresh_token="x",
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        action_id = queue_action(
            conn,
            org_id=admin["org_id"],
            account_id="acct-1",
            user_id=admin["user_id"],
            tool="gmail_send",
            args={"to": "bob@act.test"},
        )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin, action_id


def _status(engine, action_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            select(connector_action.c.status).where(connector_action.c.id == action_id)
        ).scalar_one()


def test_approve_is_refused_without_registered_executor(tmp_path) -> None:
    engine, app, _admin, action_id = _setup(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            f"/api/connectors/actions/{action_id}/approve",
            headers={"Authorization": "Bearer owner-token"},
        )
    assert r.status_code == 409
    assert _status(engine, action_id) == PENDING


def test_reject_by_requester_marks_action_rejected(tmp_path) -> None:
    engine, app, _admin, action_id = _setup(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            f"/api/connectors/actions/{action_id}/reject",
            headers={"Authorization": "Bearer owner-token"},
        )
    assert r.status_code == 200
    assert _status(engine, action_id) == "rejected"


def test_reject_requires_authentication(tmp_path) -> None:
    engine, app, _admin, action_id = _setup(tmp_path)
    with TestClient(app) as client:
        r = client.post(f"/api/connectors/actions/{action_id}/reject")
    assert r.status_code == 401
    assert _status(engine, action_id) == PENDING


def test_other_user_cannot_reject(tmp_path) -> None:
    engine, app, _admin, action_id = _setup(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            f"/api/connectors/actions/{action_id}/reject",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert r.status_code == 401
    assert _status(engine, action_id) == PENDING
def test_pending_list_shows_only_requesters_pending_actions(tmp_path) -> None:
    engine, app, admin, _action_id = _setup(tmp_path)
    with engine.begin() as conn:
        queue_action(
            conn,
            org_id=admin["org_id"],
            account_id="acct-1",
            user_id="someone-else",
            tool="gmail_send",
            args={"to": "private@act.test"},
        )
    with TestClient(app) as client:
        r = client.get("/api/connectors/actions", headers={"Authorization": "Bearer owner-token"})
    assert r.status_code == 200
    actions = r.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["args"] == {"to": "bob@act.test"}
    assert "private@act.test" not in r.text


def test_pending_list_requires_authentication(tmp_path) -> None:
    _engine, app, _admin, _action_id = _setup(tmp_path)
    with TestClient(app) as client:
        r = client.get("/api/connectors/actions")
    assert r.status_code == 401
