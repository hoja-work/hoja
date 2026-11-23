"""Every task-timeline row names who did it (HOJA-728).

Production had archives, runner events and SLA events written with ``actor_id``
NULL, which the timeline rendered as "Someone updated the task". This file pins:

* the shared writer refuses a user-driven event with no actor;
* only the system's own events (SLA clock, ``runner.*``) may be actor-less;
* archiving a task records the person who archived it;
* a runner event on a delegated task is attributed to that agent, and one on a
  task nobody delegated stays a system event;
* no module writes ``issue_activity`` except through ``activity.record``, so a
  new call site cannot reintroduce an anonymous row.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import and_, select

from hoja_server import activity
from hoja_server.db import issue, issue_activity
from tests.agent.test_usage_ledger import (  # noqa: F401  (``world`` is a fixture)
    _agent,
    _granted_lease,
    _post,
    _set_policy,
    world,
)

SERVER = Path(__file__).resolve().parents[2] / "hoja_server"


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


def test_runner_events_on_a_delegated_task_name_the_agent(world):
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    response = world["client"].post("/runner/events", json={
        "lease_id": lease["lease_id"], "run_id": lease["run_id"],
        "fence": lease["fence"], "token": lease["token"],
        "events": [{"seq": 1, "kind": "harness.spawned", "payload": {"pid": 1}}],
    }, headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    rows = _activity_rows(world["engine"], lease["task_id"], prefix="runner.")
    assert rows, "the runner event never reached the task timeline"
    assert {r["actor_id"] for r in rows} == {agent["userId"]}


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
