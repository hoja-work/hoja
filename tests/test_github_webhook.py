"""Signed GitHub webhook receiver: authentication, dedupe, replay and unknown installations."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    connector_account,
    github_link,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_tenant,
)

HOST = "https://testserver"
PATH = "/api/connectors/github/webhook"
SECRET = "whsec_test_value_not_a_real_secret"
INSTALLATION = 555000111


def _setup(tmp_path):
    db_path = tmp_path / "github-webhook.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Webhook Org",
        org_url_key="webhook-org",
        token="owner-token",
        demo_issues=True,  # creates ENG-1 and ENG-2 to link against
    )
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=new_id(),
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="github",
                external_account="rinadelph",
                external_id=str(INSTALLATION),
                scopes="metadata:read",
                encrypted_refresh_token="",
                status="active",
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )
    app = create_app(str(db_path), open_mode=False)
    return engine, app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_GITHUB_WEBHOOK_SECRET", SECRET)


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client: TestClient, payload: dict, *, event: str, delivery: str, secret: str = SECRET,
          signature: str | None = None, omit_delivery: bool = False):
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature if signature is not None else _sign(body, secret),
        "Content-Type": "application/json",
    }
    if not omit_delivery:
        headers["X-GitHub-Delivery"] = delivery
    return client.post(PATH, content=body, headers=headers)


def _pr_payload(identifiers=("ENG-1",), *, updated=None, installation=INSTALLATION):
    updated = updated or datetime.now(timezone.utc).isoformat()
    ref = "4c114d5b/eng-1-fix-it"
    return {
        "action": "opened",
        "installation": {"id": installation},
        "pull_request": {
            "id": 4242,
            "number": 67,
            "title": f"Work on {identifiers[0]}",
            "body": " ".join(identifiers),
            "state": "open",
            "merged": False,
            "html_url": "https://github.com/hoja-work/hoja/pull/67",
            "user": {"login": "rinadelph"},
            "head": {"ref": ref, "sha": "343458f"},
            "updated_at": updated,
        },
    }


def _push_payload(message, *, installation=INSTALLATION):
    return {
        "ref": "refs/heads/main",
        "installation": {"id": installation},
        "commits": [
            {
                "id": "abc1234",
                "message": message,
                "url": "https://github.com/hoja-work/hoja/commit/abc1234",
                "author": {"name": "Luis"},
            }
        ],
        "head_commit": {
            "id": "abc1234",
            "message": message,
            "url": "https://github.com/hoja-work/hoja/commit/abc1234",
            "author": {"name": "Luis"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# --- authentication -----------------------------------------------------------------------


def test_every_delivery_is_refused_when_the_secret_is_unset(tmp_path, monkeypatch):
    engine, app = _setup(tmp_path)
    monkeypatch.delenv("HOJA_GITHUB_WEBHOOK_SECRET", raising=False)
    client = TestClient(app)

    response = _post(client, _pr_payload(), event="pull_request", delivery="d-no-secret")

    assert response.status_code == 503
    with engine.connect() as conn:
        assert conn.execute(select(github_link.c.id)).all() == []


def test_bad_signature_is_rejected(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(
        client,
        _pr_payload(),
        event="pull_request",
        delivery="d-bad-sig",
        signature="sha256=" + "0" * 64,
    )

    assert response.status_code == 401
    with engine.connect() as conn:
        assert conn.execute(select(github_link.c.id)).all() == []


def test_signature_from_the_wrong_secret_is_rejected(tmp_path):
    _engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(
        client, _pr_payload(), event="pull_request", delivery="d-wrong-secret",
        secret="the_wrong_secret",
    )

    assert response.status_code == 401


def test_missing_delivery_header_is_rejected(tmp_path):
    _engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(client, _pr_payload(), event="pull_request", delivery="", omit_delivery=True)

    assert response.status_code == 400


# --- dedupe and replay --------------------------------------------------------------------


def test_replayed_delivery_does_no_work_the_second_time(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)
    payload = _pr_payload()

    first = _post(client, payload, event="pull_request", delivery="d-replay")
    assert first.status_code == 200
    with engine.connect() as conn:
        after_first = conn.execute(select(github_link.c.id)).all()
    assert len(after_first) == 1

    second = _post(client, payload, event="pull_request", delivery="d-replay")
    assert second.status_code == 200
    assert second.json().get("duplicate") is True
    with engine.connect() as conn:
        after_second = conn.execute(select(github_link.c.id)).all()
    assert len(after_second) == 1


def test_stale_event_outside_the_replay_window_is_rejected(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    response = _post(
        client, _pr_payload(updated=old), event="pull_request", delivery="d-stale"
    )

    assert response.status_code == 400
    with engine.connect() as conn:
        assert conn.execute(select(github_link.c.id)).all() == []


def test_unknown_installation_is_acknowledged_without_work(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(
        client,
        _pr_payload(installation=999999999),
        event="pull_request",
        delivery="d-unknown-install",
    )

    assert response.status_code == 202
    assert response.json().get("unknown_installation") is True
    with engine.connect() as conn:
        assert conn.execute(select(github_link.c.id)).all() == []


# --- linking ------------------------------------------------------------------------------


def test_pull_request_creates_a_pr_link(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(client, _pr_payload(), event="pull_request", delivery="d-pr")

    assert response.status_code == 200
    assert response.json()["links"] == 1
    with engine.connect() as conn:
        row = conn.execute(select(github_link)).mappings().one()
    assert row["kind"] == "pr"
    assert row["external_id"] == "4242"
    assert row["author"] == "rinadelph"
    assert row["state"] == "open"
    assert row["sha"] == "343458f"


def test_push_commit_creates_a_commit_link(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(
        client, _push_payload("fix: repairs the drawer floor\n\nfixes ENG-2"),
        event="push", delivery="d-push",
    )

    assert response.status_code == 200
    assert response.json()["links"] >= 1
    with engine.connect() as conn:
        row = conn.execute(select(github_link)).mappings().one()
    assert row["kind"] == "commit"
    assert row["external_id"] == "abc1234"


def test_a_commit_naming_an_unknown_team_creates_nothing(tmp_path):
    engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(
        client, _push_payload("chore: bump ISO-9001 baseline"),
        event="push", delivery="d-push-iso",
    )

    assert response.status_code == 200
    assert response.json()["links"] == 0
    with engine.connect() as conn:
        assert conn.execute(select(github_link.c.id)).all() == []


def test_unhandled_event_is_acknowledged_and_ignored(tmp_path):
    _engine, app = _setup(tmp_path)
    client = TestClient(app)

    response = _post(
        client, {"installation": {"id": INSTALLATION}, "action": "opened"},
        event="issues", delivery="d-issues",
    )

    assert response.status_code == 200
    assert response.json().get("ignored") == "issues"
