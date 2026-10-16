"""Browser sync: compact bootstrap, ETag revalidation, and seq deltas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select, update
from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import issue, make_engine, migrate, seed_tenant, workflow_state

TOKEN = "sync-token"
CREATE = "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success issue { id } } }"
UPDATE = "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }"
SUBSCRIBE = "mutation($id: String!) { issueSubscribe(id: $id) }"
UNSUBSCRIBE = "mutation($id: String!) { issueUnsubscribe(id: $id) }"


def _setup(tmp_path):
    db_path = tmp_path / "sync.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Sync Org", org_url_key="sync-org",
                         user_email="owner@sync.test", token=TOKEN, demo_issues=False)
    return engine, create_app(str(db_path), open_mode=False), seeded


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _gql(client, query, variables=None):
    body = client.post("/graphql", headers=_auth(), json={"query": query, "variables": variables or {}}).json()
    assert not body.get("errors"), body
    return body["data"]


def _rows(body):
    cols = body["columns"]
    return {r[0]: dict(zip(cols, r, strict=True)) for r in body["issues"]}


def _state_id(engine, state_type):
    with engine.connect() as conn:
        return conn.execute(select(workflow_state.c.id).where(workflow_state.c.type == state_type)).scalars().first()


def _create(client, team_id, title, **extra):
    return _gql(client, CREATE, {"i": {"title": title, "teamId": team_id, **extra}})["issueCreate"]["issue"]["id"]


def test_bootstrap_sends_open_and_recently_closed_issues_only(tmp_path) -> None:
    engine, app, seeded = _setup(tmp_path)
    with TestClient(app) as client:
        team_id = _gql(client, "{ teams { nodes { id } } }")["teams"]["nodes"][0]["id"]
        open_id = _create(client, team_id, "Open work")
        recent_id = _create(client, team_id, "Closed recently", stateId=_state_id(engine, "completed"))
        old_id = _create(client, team_id, "Closed long ago", stateId=_state_id(engine, "canceled"))
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        with engine.begin() as conn:
            conn.execute(update(issue).where(issue.c.id == old_id).values(updated_at=old))

        res = client.get("/sync/bootstrap", headers=_auth())
        assert res.status_code == 200, res.text
        body = res.json()
        rows = _rows(body)
        assert set(rows) == {open_id, recent_id}
        assert rows[open_id]["teamId"] == team_id and rows[open_id]["title"] == "Open work"
        assert body["meta"]["viewer"]["email"] == "owner@sync.test"
        assert [seeded["user_id"], "Local User"] in body["meta"]["users"]
        assert body["meta"]["teams"]["nodes"][0]["states"]["nodes"]
        assert body["seq"] > 0

        wide = _rows(client.get("/sync/bootstrap?closedDays=90", headers=_auth()).json())
        assert old_id in wide


def test_bootstrap_revalidates_with_etag_until_data_changes(tmp_path) -> None:
    _engine, app, _seeded = _setup(tmp_path)
    with TestClient(app) as client:
        team_id = _gql(client, "{ teams { nodes { id } } }")["teams"]["nodes"][0]["id"]
        _create(client, team_id, "First")
        first = client.get("/sync/bootstrap", headers=_auth())
        etag = first.headers["etag"]
        again = client.get("/sync/bootstrap", headers={**_auth(), "If-None-Match": etag})
        assert again.status_code == 304 and again.content == b""
        _create(client, team_id, "Second")
        changed = client.get("/sync/bootstrap", headers={**_auth(), "If-None-Match": etag})
        assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_bootstrap_is_gzip_compressed_when_accepted(tmp_path) -> None:
    _engine, app, _seeded = _setup(tmp_path)
    with TestClient(app) as client:
        team_id = _gql(client, "{ teams { nodes { id } } }")["teams"]["nodes"][0]["id"]
        for n in range(40):
            _create(client, team_id, f"Issue number {n}")
        res = client.get("/sync/bootstrap", headers={**_auth(), "Accept-Encoding": "gzip"})
        assert res.headers.get("content-encoding") == "gzip"
        assert len(_rows(res.json())) == 40


def test_delta_returns_touched_rows_removed_ids_and_link_changes(tmp_path) -> None:
    _engine, app, _seeded = _setup(tmp_path)
    with TestClient(app) as client:
        team_id = _gql(client, "{ teams { nodes { id } } }")["teams"]["nodes"][0]["id"]
        keep = _create(client, team_id, "Keep")
        doomed = _create(client, team_id, "Doomed")
        seq = client.get("/sync/bootstrap", headers=_auth()).json()["seq"]

        _gql(client, UPDATE, {"id": keep, "i": {"title": "Kept and renamed", "priority": 1}})
        _gql(client, "mutation($id: String!) { issueArchive(id: $id) { success } }", {"id": doomed})
        delta = client.get(f"/sync/delta?after={seq}", headers=_auth()).json()
        assert delta["reload"] is False
        assert _rows(delta)[keep]["title"] == "Kept and renamed"
        assert delta["removed"] == [doomed]
        assert delta["seq"] > seq

        # A subscriber link alone (no issue row write) still reaches the delta.
        _gql(client, SUBSCRIBE, {"id": keep})
        cursor = client.get(f"/sync/delta?after={delta['seq']}", headers=_auth()).json()["seq"]
        _gql(client, UNSUBSCRIBE, {"id": keep})
        after_unsub = client.get(f"/sync/delta?after={cursor}", headers=_auth()).json()
        assert keep in _rows(after_unsub)

        empty = client.get(f"/sync/delta?after={after_unsub['seq']}", headers=_auth()).json()
        assert empty["issues"] == [] and empty["removed"] == [] and empty["meta"] is False


def test_delta_asks_for_reload_when_cursor_is_ahead(tmp_path) -> None:
    _engine, app, _seeded = _setup(tmp_path)
    with TestClient(app) as client:
        assert client.get("/sync/delta?after=999999", headers=_auth()).json()["reload"] is True
        boot = client.get("/sync/bootstrap", headers=_auth()).json()
        cursor = f"/sync/delta?after={boot['seq']}"
        assert client.get(f"{cursor}&scope={boot['scope']}", headers=_auth()).json()["reload"] is False
        assert client.get(f"{cursor}&scope=someone-else", headers=_auth()).json()["reload"] is True
        assert client.get("/sync/delta?after=abc", headers=_auth()).status_code == 400
        assert client.get("/sync/delta?after=0").status_code == 401
        assert client.get("/sync/bootstrap").status_code == 401
