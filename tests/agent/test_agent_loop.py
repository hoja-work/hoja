"""The delegated-agent loop end to end on the server (HOJA-602 e2e loop).

Each test closes one gap the architecture review found between "the safety layer exists"
and "a delegated task actually reaches In Review":

==============================================  =========================================
test                                            proves
==============================================  =========================================
a_refused_lease_does_not_block_the_queue        a policy-refused attempt is skipped, not
                                                retried at the head of the queue, and is
                                                audited once per reason, not per poll
the_sweep_starts_an_approved_delegation         a queued session is re-checked later and
                                                its lease queued (no timer existed)
a_question_round_trips_to_the_live_run          started -> running, ask -> awaiting_input,
                                                answer -> running, heartbeat delivers it
a_held_heartbeat_returns_early_on_cancel        the long-wait beat ends on a cancel
a_lost_lease_carries_its_session_to_the_retry   the reaper moves the session to the new
                                                attempt, and fails it when none is left
both_gates_read_one_capacity                    schedule and lease gate agree
the_lease_names_the_model_task_and_repo         the run gets what it needs to work
==============================================  =========================================
"""
from __future__ import annotations

import json
import time
from datetime import timedelta

from sqlalchemy import func, select

from hoja_server import agent_policy, agent_schedule, agent_sessions, runner_api
from hoja_server.db import agent_lease, agent_member, audit_event, issue_decision
from tests.agent.test_auto_verify_and_pools import (  # noqa: F401 - `world` is a fixture
    _claim,
    _create_agent,
    _delegate,
    _enroll,
    _gql,
    _session,
    world,
)


def _auth(identity: dict) -> dict:
    return {"Authorization": f"Bearer {identity['token']}"}


def _lease_body(lease: dict, **extra) -> dict:
    return {"lease_id": lease["lease_id"], "run_id": lease["run_id"],
            "fence": lease["fence"], "token": lease["token"], **extra}


def _events(world, identity, lease, *events) -> dict:
    body = _lease_body(lease, events=[{"seq": i + 1, "kind": kind, "payload": payload}
                                      for i, (kind, payload) in enumerate(events)])
    response = world["client"].post("/runner/events", json=body, headers=_auth(identity))
    assert response.status_code == 200, response.text
    return response.json()


def _beat(world, identity, lease, **extra) -> dict:
    response = world["client"].post("/runner/heartbeat", json=_lease_body(lease, **extra),
                                    headers=_auth(identity))
    assert response.status_code == 200, response.text
    return response.json()


def _session_by_id(world, session_id: str) -> dict:
    body = _gql(world, world["alpha"]["token"], f'''
        query {{ agentSession(id: "{session_id}") {{
          id state status leaseId activities {{ kind body payload }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["agentSession"]


def _refusal_audits(world) -> int:
    with world["engine"].connect() as conn:
        return int(conn.execute(select(func.count()).select_from(audit_event).where(
            audit_event.c.action == "agent.lease.refuse")).scalar())


# ------------------------------------------------------------------------- the queue
def test_a_refused_lease_does_not_block_the_queue(world):
    # An agent whose policy grants no team: its attempt is refused with action=queue,
    # which used to leave it at the head of the queue for every runner forever.
    blocked = _create_agent(world, name="Blocked")
    cleared = _gql(world, world["alpha"]["token"], f'''
        mutation {{ agentPolicyUpdate(agentMemberId: "{blocked["id"]}",
                                      input: {{ teams: [] }}) {{ success }} }}''')
    assert "errors" not in cleared, cleared
    first = _delegate(world, blocked)
    ready = _create_agent(world, name="Ready")
    second = _delegate(world, ready)

    identity = _enroll(world)
    lease, _ = _claim(world, identity)
    assert lease is not None, "the second agent's work must not wait behind a refusal"
    assert lease["task_id"] == second["id"]

    # More polls re-check the refused attempt but do not re-audit the same reason.
    for _ in range(5):
        _claim(world, identity)
    assert _refusal_audits(world) == 1
    with world["engine"].connect() as conn:
        refused = conn.execute(select(agent_lease.c.refusal_reason).where(
            agent_lease.c.task_id == first["id"])).scalar()
    assert refused == "policy_scope"


# ------------------------------------------------------------------------- the sweep
def test_the_sweep_starts_an_approved_delegation(world):
    agent = _create_agent(world)
    with world["engine"].begin() as conn:
        conn.execute(agent_member.update().where(agent_member.c.id == agent["id"]).values(
            require_approval=True))
    task = _delegate(world, agent)
    with world["engine"].connect() as conn:
        session = agent_sessions.latest_for_issue(conn, world["alpha"]["org_id"], task["id"])
    assert session["state"] == "queued" and not session.get("lease_id")
    assert "approval" in agent_sessions.parse_reasons(session["queue_reason"])

    # Nothing changes until a person approves; the sweep only re-asks.
    assert agent_schedule.sweep(world["engine"])["enqueued"] == 0
    with world["engine"].begin() as conn:
        conn.execute(issue_decision.update().where(
            issue_decision.c.id == session["approval_decision_id"]).values(
                status="answered", chosen_option=agent_schedule.APPROVE_OPTION))
    counts = agent_schedule.sweep(world["engine"])
    assert counts["enqueued"] == 1, counts

    lease, _ = _claim(world, _enroll(world))
    assert lease is not None and lease["task_id"] == task["id"]
    assert _session(world, task["id"])["state"] == "leased"


# --------------------------------------------------------------------- the question
def test_a_question_round_trips_to_the_live_run(world):
    agent = _create_agent(world)
    task = _delegate(world, agent)
    identity = _enroll(world)
    lease, _ = _claim(world, identity)
    session_id = _session(world, task["id"])["id"]

    _events(world, identity, lease, ("session.started", {}))
    assert _session_by_id(world, session_id)["state"] == "running"

    question = {"header": "Database", "question": "Which database?",
                "options": [{"label": "Postgres"}, {"label": "SQLite"}],
                "multi_select": False}
    body = _lease_body(lease, events=[
        {"seq": 2, "kind": "session.action", "payload": {"body": "read the schema"}},
        {"seq": 3, "kind": "session.ask",
         "payload": {"ask_id": "ask-1", "questions": [question]}}])
    assert world["client"].post("/runner/events", json=body,
                                headers=_auth(identity)).status_code == 200
    waiting = _session_by_id(world, session_id)
    assert waiting["state"] == "awaiting_input"
    kinds = [a["kind"] for a in waiting["activities"]]
    assert "action" in kinds and kinds[-1] == "elicitation"
    assert waiting["activities"][-1]["payload"]["ask_id"] == "ask-1"
    assert _beat(world, identity, lease)["answers"] == []

    answered = _gql(world, world["alpha"]["token"], f'''
        mutation {{ agentSessionPrompt(id: "{session_id}", body: "Postgres",
          response: {json.dumps(json.dumps({"answers": [{"header": "Database",
                                                          "selected": ["Postgres"]}]}))})
          {{ success agentSession {{ state }} }} }}''')
    # The JSON scalar arrives as a string literal here; the server reads either shape.
    assert "errors" not in answered, answered
    assert answered["data"]["agentSessionPrompt"]["agentSession"]["state"] == "running"

    beat = _beat(world, identity, lease, wait_ms=2000, waiting_asks=["ask-1"])
    assert beat["answers"] == [{"ask_id": "ask-1", "response": {"answers": [
        {"header": "Database", "selected": ["Postgres"], "custom_answer": None}]}}]


def test_a_held_heartbeat_returns_early_on_cancel(world):
    agent = _create_agent(world)
    task = _delegate(world, agent)
    identity = _enroll(world)
    lease, _ = _claim(world, identity)
    runner_api.request_cancel(world["engine"], organization_id=world["alpha"]["org_id"],
                              task_id=task["id"])
    started = time.monotonic()
    beat = _beat(world, identity, lease, wait_ms=5000)
    assert beat["cancel"] is True
    assert time.monotonic() - started < 2.0


# ----------------------------------------------------------------------- the reaper
def test_a_lost_lease_carries_its_session_to_the_retry(world):
    agent = _create_agent(world)
    task = _delegate(world, agent)
    identity = _enroll(world)
    lease, _ = _claim(world, identity)
    session_id = _session(world, task["id"])["id"]

    later = runner_api._now() + timedelta(hours=1)
    runner_api.reap_expired_leases(world["engine"], now=later)
    retried = _session_by_id(world, session_id)
    assert retried["state"] == "queued"
    assert retried["leaseId"] and retried["leaseId"] != lease["lease_id"]

    for attempt in (2, 3):
        again, _ = _claim(world, identity)
        assert again is not None and again["fence"] == attempt
        assert _session_by_id(world, session_id)["state"] == "leased"
        later += timedelta(hours=1)
        runner_api.reap_expired_leases(world["engine"], now=later)
    assert _session_by_id(world, session_id)["state"] == "failed"


# ------------------------------------------------------------------------ capacity
def test_both_gates_read_one_capacity(world):
    agent = _create_agent(world, capacity=1)
    raised = _gql(world, world["alpha"]["token"], f'''
        mutation {{ agentPolicyUpdate(agentMemberId: "{agent["id"]}",
                                      input: {{ maxConcurrency: 2 }}) {{ success }} }}''')
    assert "errors" not in raised, raised
    first, second = _delegate(world, agent), _delegate(world, agent)
    assert _session(world, first["id"])["state"] == "queued"
    with world["engine"].connect() as conn:
        org = world["alpha"]["org_id"]
        admitted = [agent_sessions.latest_for_issue(conn, org, t["id"]).get("admitted_at")
                    for t in (first, second)]
        member = conn.execute(select(agent_member).where(
            agent_member.c.id == agent["id"])).mappings().one()
        view = agent_policy.view_for_member(conn, org, agent["id"])
    assert agent_policy.effective_capacity(dict(member), view) == 2
    assert all(admitted), "the policy allows two, so the schedule check admits two"


# ------------------------------------------------------------------- the lease payload
def test_the_lease_names_the_model_task_and_repo(world):
    agent = _create_agent(world, model="openai:wafer-glm-5.2")
    granted = _gql(world, world["alpha"]["token"], f'''
        mutation {{ agentPolicyUpdate(agentMemberId: "{agent["id"]}",
          input: {{ repos: {json.dumps(json.dumps([{"repo": "hoja-work/demo",
                                                    "branches": ["agent/demo"]}]))} }})
          {{ success }} }}''')
    assert "errors" not in granted, granted
    task = _delegate(world, agent)
    lease, _ = _claim(world, _enroll(world))
    assert lease["model"] == "openai:wafer-glm-5.2"
    assert lease["task"]["identifier"] == task["identifier"]
    assert lease["task"]["acceptance"] == [
        "the finish route moves the session to verifying",
        "only a met verdict reaches In Review"]
    assert lease["repo"] == {"repo": "hoja-work/demo",
                             "url": "https://github.com/hoja-work/demo.git",
                             "branch": "agent/demo", "source": "agent"}


# ---------------------------------------------------------------------- the migration
def test_revision_81_adds_the_refusal_column_and_replays(tmp_path):
    """80 -> 81 twice: the column probe makes a replayed deploy a no-op."""
    from sqlalchemy import inspect, text

    from hoja_server import db as db_module

    engine = db_module.make_engine(tmp_path / "rev80.db")
    db_module.migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE agent_lease DROP COLUMN refusal_reason")
        conn.execute(text("DELETE FROM schema_revision WHERE revision >= 81"))
    assert "refusal_reason" not in {c["name"] for c in inspect(engine).get_columns("agent_lease")}
    for _ in range(2):
        db_module.migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION
    assert "refusal_reason" in {c["name"] for c in inspect(engine).get_columns("agent_lease")}
