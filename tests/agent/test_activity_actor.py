"""Every task-timeline row names who did it (HOJA-728).

Production had archives, runner events and SLA events written with ``actor_id``
NULL, which the timeline rendered as "Someone updated the task". This file pins:

* the shared writer refuses a user-driven event with no actor;
* only the system's own events (SLA clock, ``runner.*``) may be actor-less;
* archiving a task records the person who archived it;
* no module writes ``issue_activity`` except through ``activity.record``, so a
  new call site cannot reintroduce an anonymous row.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import activity, logging_setup
from hoja_server.app import create_app
from hoja_server.db import issue, issue_activity, seed_tenant
from tests import rls_mode

SERVER = Path(__file__).resolve().parents[2] / "hoja_server"


def _post(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    return client.post("/graphql", json={"query": query, "variables": variables or {}},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "activity-actor-master")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    logging_setup.forget_secrets()
    engine, kwargs = rls_mode.workspace(tmp_path, "activity-actor")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    yield {"engine": engine, "client": TestClient(app), "alpha": alpha}
    logging_setup.forget_secrets()


def _activity_rows(engine, task_id: str, *, prefix: str | None = None,
                   event_type: str | None = None) -> list[dict]:
    with engine.connect() as conn:
        stmt = select(issue_activity).where(issue_activity.c.issue_id == task_id)
        if prefix:
            stmt = stmt.where(issue_activity.c.event_type.like(f"{prefix}%"))
        if event_type:
            stmt = stmt.where(issue_activity.c.event_type == event_type)
        return [dict(r) for r in conn.execute(stmt).mappings()]


def test_a_user_event_without_an_actor_is_refused(world):
    tenant = world["alpha"]
    with world["engine"].connect() as conn:
        task_id = conn.execute(select(issue.c.id).where(
            issue.c.organization_id == tenant["org_id"])).scalars().first()
    for event_type in ("issueUpdated", "issueArchived", "commentUpdated", "commentCreated"):
        with pytest.raises(activity.ActivityActorMissingError):
            with world["engine"].begin() as conn:
                activity.record(conn, org_id=tenant["org_id"], issue_id=task_id,
                                actor_id=None, event_type=event_type, created_at="2026-11-23")
    assert not _activity_rows(world["engine"], task_id, event_type="issueArchived")


def test_only_system_events_may_be_actor_less():
    assert activity.is_system_event("slaStarted")
    assert activity.is_system_event("slaBreached")
    assert activity.is_system_event("runner.lease.claimed")
    for event_type in ("issueUpdated", "issueArchived", "issueCreated", "commentCreated",
                       "commentUpdated", "decisionCreated", "extensionPatched",
                       "githubReviewMerged"):
        assert not activity.is_system_event(event_type), event_type


def test_archiving_a_task_records_who_archived_it(world):
    tenant = world["alpha"]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{ issueCreate(input: {{ teamId: "{tenant["team_key"]}", title: "To archive" }}) {{
          issue {{ id }} }} }}''')
    task_id = body["data"]["issueCreate"]["issue"]["id"]
    body = _post(world["client"], tenant["token"],
                 f'mutation {{ issueArchive(id: "{task_id}") {{ success }} }}')
    assert "errors" not in body, body
    rows = _activity_rows(world["engine"], task_id, event_type="issueArchived")
    assert [r["actor_id"] for r in rows] == [tenant["user_id"]]


def _inserts_issue_activity(node: ast.AST) -> bool:
    """``issue_activity.insert(...)`` anywhere in this node."""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and sub.attr == "insert"
                and isinstance(sub.value, ast.Name) and sub.value.id == "issue_activity"):
            return True
    return False


def test_issue_activity_is_written_only_through_activity_record():
    offenders = []
    for path in sorted(SERVER.rglob("*.py")):
        if path.name == "activity.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _inserts_issue_activity(tree):
            offenders.append(str(path.relative_to(SERVER)))
    assert not offenders, (
        "write task activity through hoja_server.activity.record so the actor rule "
        f"applies (HOJA-728): {offenders}")
