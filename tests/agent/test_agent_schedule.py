"""Delegation, the schedule check and the Linear session (HOJA-602-A-7, WP06).

The acceptance for this work package is not "the code exists". It is that the
invariants of `agent-security/model_check.py` hold *against real transitions*, and
that a queued delegation is acknowledged inside ten seconds. So the tests below
drive the same functions the GraphQL surface drives, at the state level, and only
the delegation tests go through HTTP to prove the wiring.

Where a fact has no model in Hoja yet, the test says so instead of inventing one:
the lease epoch is WP07's, the usage ledger is WP14's, and the ``blocked`` fact
reads unfinished children because Hoja has no blocker relation table.
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import agent_schedule, agent_sessions
from hoja_server.app import create_app
from hoja_server.db import (
    agent_activity,
    agent_lease,
    agent_member,
    agent_side_effect,
    issue,
    issue_decision,
    seed_tenant,
)
from tests import rls_mode

T0 = "2026-11-13T02:00:00Z"


def _at(seconds: float) -> str:
    return (datetime.fromisoformat(T0.replace("Z", "+00:00"))
            + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --------------------------------------------------------------------------- HTTP
def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "agent-schedule-master")
    engine, kwargs = rls_mode.workspace(tmp_path, "agent-schedule")
    seeded = seed_tenant(engine, org_name="Scheduling", org_url_key="scheduling",
                         user_email="admin@scheduling.test", token="sched-tok",
                         demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return engine, TestClient(app), seeded


def _create_agent(client, token, *, capacity=1, require_approval=None, engine=None) -> dict:
    body = _post(client, token, f'''
        mutation {{
          agentMemberCreate(input: {{
            name: "Scout", capacity: {capacity}, monthlyBudgetCredits: 500
          }}) {{
            success agentMember {{ id userId capacity }}
          }}
        }}''')
    assert "errors" not in body, body
    agent = body["data"]["agentMemberCreate"]["agentMember"]
    if require_approval is not None:
        assert engine is not None, "require_approval is set directly on the row"
        with engine.begin() as conn:
            conn.execute(agent_member.update().where(
                agent_member.c.user_id == agent["userId"]).values(
                require_approval=require_approval))
    return agent


def _create_issue(client, token, team_id, *, delegate_id=None, title="Delegated work",
                  parent_id=None) -> dict:
    fields = [f'teamId: "{team_id}"', f'title: {json.dumps(title)}']
    if delegate_id:
        fields.append(f'delegateId: "{delegate_id}"')
    if parent_id:
        fields.append(f'parentId: "{parent_id}"')
    body = _post(client, token, f'''
        mutation {{
          issueCreate(input: {{{", ".join(fields)}}}) {{ success issue {{ id }} }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]


def _session_of(client, token, issue_id) -> dict:
    body = _post(client, token, f'''
        query {{ issue(id: "{issue_id}") {{
          agentSession {{ id state status queueReason admitted trigger
            agent {{ id name }} activities {{ kind body payload }} }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["issue"]["agentSession"]


# ------------------------------------------------------------------ the 64 rows
def test_the_decision_table_is_64_rows_with_exactly_one_run_now():
    """§5: 64 combinations, exactly one runs, the rest queue with all their reasons."""
    verdicts = []
    for bits in itertools.product((True, False), repeat=6):
        facts = agent_schedule.Facts(*bits)
        runnable, reasons = agent_schedule.decide(facts)
        assert runnable == (not reasons)
        assert reasons == [r for r in agent_schedule.REASON_ORDER if r in reasons], (
            "reasons must be reported in the table's fixed order")
        verdicts.append((bits, runnable, reasons))
    assert sum(1 for _b, runnable, _r in verdicts if runnable) == 1
    assert len(verdicts) == 64
    # A task with five failing facts reports five reasons, not the first one: the
    # card has to be able to say "waiting on approval + quiet hours".
    all_false_but_capacity = next(
        v for v in verdicts if v[0] == (True, False, False, False, False, False))
    assert all_false_but_capacity[2] == [
        "budget", "window", "blocked", "approval", "lease_held"]


def test_the_decision_table_matches_the_model_checker():
    """Independent oracle: the same six facts through ``model_check.py --decisions``.

    The checker is the artifact the design was proved with, and it lives outside the
    repository, so this runs only when ``HOJA_MODEL_CHECK`` names it. When it does,
    the two implementations have to agree row for row.
    """
    path = os.environ.get("HOJA_MODEL_CHECK")
    if not path or not os.path.exists(path):
        pytest.skip("HOJA_MODEL_CHECK does not name model_check.py")
    out = subprocess.run([sys.executable, path, "--decisions"], capture_output=True,
                         text=True, timeout=300, check=True).stdout
    rows = [line.strip().split(" -> ") for line in out.splitlines()
            if line.strip().startswith(("y ", "n "))]
    assert len(rows) == 64
    for bits, verdict in rows:
        facts = agent_schedule.Facts(*[b == "y" for b in bits.split()])
        _runnable, reasons = agent_schedule.decide(facts)
        expected = "run" if not reasons else "queue:" + "+".join(reasons)
        assert expected == verdict, f"{bits}: {expected} != {verdict}"


# ------------------------------------------------------------------ delegation
def test_delegation_opens_a_session_and_acknowledges_immediately(workspace):
    _engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], capacity=2)
    task = _create_issue(client, seeded["token"], seeded["team_id"],
                         delegate_id=agent["userId"])
    session = _session_of(client, seeded["token"], task["id"])
    assert session["trigger"] == "delegation"
    assert session["agent"]["id"] == agent["userId"]
    assert session["state"] == "queued" and session["status"] == "pending"
    # Every fact held, so the reason list is empty and the session is admitted.
    assert session["queueReason"] == [] and session["admitted"] is True
    first = session["activities"][0]
    assert first["kind"] in ("action", "thought")
    assert first["payload"]["elapsed_seconds"] <= agent_sessions.ACK_DEADLINE_SECONDS


def test_capacity_queues_the_second_task_with_the_reason_as_data(workspace):
    _engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], capacity=1)
    first = _create_issue(client, seeded["token"], seeded["team_id"],
                          delegate_id=agent["userId"], title="One")
    second = _create_issue(client, seeded["token"], seeded["team_id"],
                           delegate_id=agent["userId"], title="Two")
    assert _session_of(client, seeded["token"], first["id"])["queueReason"] == []
    queued = _session_of(client, seeded["token"], second["id"])
    assert queued["queueReason"] == ["capacity"]
    assert queued["admitted"] is False
    # The reason reaches the transcript too, so the client does not have to
    # reconstruct why a session is waiting.
    assert queued["activities"][0]["kind"] == "thought"
    assert queued["activities"][0]["payload"]["reasons"] == ["capacity"]


def test_cancel_releases_the_slot_and_the_timer_admits_the_next_task(workspace):
    engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], capacity=1)
    first = _create_issue(client, seeded["token"], seeded["team_id"],
                          delegate_id=agent["userId"], title="One")
    second = _create_issue(client, seeded["token"], seeded["team_id"],
                           delegate_id=agent["userId"], title="Two")
    session = _session_of(client, seeded["token"], first["id"])
    body = _post(client, seeded["token"], f'''
        mutation {{ agentSessionStop(id: "{session["id"]}", reason: "not needed") {{
          success agentSession {{ state status }} }} }}''')
    assert "errors" not in body, body
    assert body["data"]["agentSessionStop"]["agentSession"]["state"] == "cancelled"
    # Nothing but the timer revisits a queued session: the freed slot is claimed on
    # the next evaluation, not on the cancel.
    queued = _session_of(client, seeded["token"], second["id"])
    assert queued["queueReason"] == ["capacity"]
    with engine.begin() as conn:
        row = agent_sessions.get_session(conn, seeded["org_id"], queued["id"])
        row = agent_schedule.reevaluate(conn, row, at=_at(5))
    assert row["queue_reason"] is None and row["admitted_at"] is not None


# ------------------------------------------------------- the invariants, for real
def _fresh_session(conn, seeded, agent_user_id, issue_id, at=T0):
    row = agent_sessions.open_session(conn, seeded["org_id"], issue_id, agent_user_id,
                                      seeded["user_id"], at=at)
    return agent_sessions.apply_schedule(conn, row, [], at=at)


@pytest.fixture
def delegated(workspace):
    engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], capacity=1)
    task = _create_issue(client, seeded["token"], seeded["team_id"])
    return engine, client, seeded, agent, task["id"]


def test_i3a_i3b_cancel_always_wins_and_nothing_revives_it(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = _fresh_session(conn, seeded, agent["userId"], issue_id)
        row = agent_sessions.transition(conn, row, "cancel", at=_at(1),
                                        actor_id=seeded["user_id"], reason="changed my mind")
        assert row["state"] == "cancelled"
        # I3a: a cancelled session holds no lease and no slot.
        assert not agent_sessions.holds_slot(row)
        # I3b: nothing leaves cancelled — the late runner cannot bring it back.
        for event in ("lease", "start", "finish", "verify_pass", "accept", "cancel"):
            with pytest.raises(agent_sessions.RefusedTransitionError):
                agent_sessions.transition(conn, row, event, at=_at(2))
        live = agent_sessions.live_sessions(conn, seeded["org_id"], agent["userId"])
        assert [s for s in live if agent_sessions.holds_slot(s)] == []


def test_i1_a_second_delegation_of_the_same_task_waits_for_the_claim(workspace):
    """I1: one live claim per task, even when the agent has spare capacity."""
    engine, _client, seeded = workspace
    agent = _create_agent(_client, seeded["token"], capacity=4)
    task = _create_issue(_client, seeded["token"], seeded["team_id"])
    with engine.begin() as conn:
        row = conn.execute(select(issue).where(issue.c.id == task["id"])).mappings().one()
        first = agent_schedule.delegate(conn, seeded["org_id"], dict(row), agent["userId"],
                                       seeded["user_id"], at=T0)
        second = agent_schedule.delegate(conn, seeded["org_id"], dict(row), agent["userId"],
                                        seeded["user_id"], at=_at(1))
    # Capacity is not the reason: the first session already claims the task.
    assert first["queue_reason"] is None
    assert second["queue_reason"] == json.dumps(["lease_held"])
    assert second["admitted_at"] is None


def test_i2a_the_agent_never_reserves_more_slots_than_its_capacity(workspace):
    engine, _client, seeded = workspace
    agent = _create_agent(_client, seeded["token"], capacity=1)
    with engine.begin() as conn:
        tasks = []
        for index in range(3):
            tasks.append(_create_issue(_client, seeded["token"], seeded["team_id"],
                                       title=f"task {index}"))
        rows = [conn.execute(select(issue).where(
            issue.c.id == t["id"])).mappings().one() for t in tasks]
        sessions = [agent_schedule.delegate(conn, seeded["org_id"], dict(r),
                                            agent["userId"], seeded["user_id"],
                                            at=_at(index))
                    for index, r in enumerate(rows)]
        live = agent_sessions.live_sessions(conn, seeded["org_id"], agent["userId"])
        reserved = [s for s in live if agent_sessions.reserves_capacity(s)]
    assert sum(1 for s in sessions if s["admitted_at"]) == 1
    assert len(reserved) == 1


def test_i4_a_finish_from_a_dead_lease_is_refused(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = _fresh_session(conn, seeded, agent["userId"], issue_id)
        row = agent_sessions.transition(conn, row, "lease", at=_at(1))
        row = agent_sessions.transition(conn, row, "start", at=_at(2))
        assert row["state"] == "running"
        with pytest.raises(agent_sessions.RefusedTransitionError):
            agent_sessions.transition(conn, row, "finish", at=_at(3), lease_live=False)
        assert agent_sessions.get_session(
            conn, seeded["org_id"], row["id"])["state"] == "running"


def test_i5b_in_review_is_only_reachable_through_verifying(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = _fresh_session(conn, seeded, agent["userId"], issue_id)
        row = agent_sessions.transition(conn, row, "lease", at=_at(1))
        row = agent_sessions.transition(conn, row, "start", at=_at(2))
        with pytest.raises(agent_sessions.RefusedTransitionError):
            agent_sessions.transition(conn, row, "verify_pass", at=_at(3))
        row = agent_sessions.transition(conn, row, "finish", at=_at(3))
        assert row["state"] == "verifying"
        row = agent_sessions.transition(conn, row, "verify_pass", at=_at(4))
        assert row["state"] == "in_review" and row["status"] == "complete"
        row = agent_sessions.transition(conn, row, "accept", at=_at(5))
        assert row["state"] == "done"
        # I5a: no in_review or done state holds a lease.
        assert not agent_sessions.holds_slot(row)


def test_i6c_every_non_terminal_state_still_has_a_move(delegated):
    """A state with no enabled transition is a stuck task, whatever the diagram says."""
    engine, _client, seeded, agent, issue_id = delegated
    for state in agent_sessions.STATES:
        if state in agent_sessions.TERMINAL:
            continue
        events = [e for s, e in agent_sessions.TRANSITIONS if s == state]
        assert events, f"{state} has no transition in the table"
        with engine.begin() as conn:
            row = _fresh_session(conn, seeded, agent["userId"], issue_id)
            agent_sessions._save(conn, row, at=_at(1), state=state, status="active",
                                 admitted_at=_at(1) if state == "queued" else None)
            moved = agent_sessions.transition(conn, row, "cancel", at=_at(2),
                                              actor_id=seeded["user_id"])
            assert moved["state"] == "cancelled"


def test_i7_the_side_effect_is_recorded_once_under_retries(delegated):
    """I7 on WP07's ledger: one PR per task, keyed run_id + seq.

    The ledger is WP07's (agent_side_effect, unique per organization on the key);
    this test drives it through ``record_effect`` rather than adding a second
    idempotency store, so the invariant is proved against the code that ships.
    """
    from hoja_server import runner_api

    engine, _client, seeded, _agent, issue_id = delegated
    lease = runner_api.enqueue_task(engine, organization_id=seeded["org_id"],
                                    task_id=issue_id, harness="pi")
    key = f"{lease['run_id']}:1"
    first = runner_api.record_effect(engine, lease=lease, key=key, kind="open_pr",
                                     payload={"external_ref": "https://example/pr/1"})
    second = runner_api.record_effect(engine, lease=lease, key=key, kind="open_pr",
                                      payload={"external_ref": "https://example/pr/2"})
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["external_ref"] == first["external_ref"] == "https://example/pr/1"
    with engine.connect() as conn:
        rows = conn.execute(select(agent_side_effect).where(
            agent_side_effect.c.organization_id == seeded["org_id"])).all()
    assert len(rows) == 1


def test_delegation_enqueues_a_lease_through_wp07s_seam(workspace):
    """An admitted session is handed to ``runner_api.enqueue_task``, not a second queue."""
    engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], capacity=1)
    task = _create_issue(client, seeded["token"], seeded["team_id"],
                         delegate_id=agent["userId"])
    session = _session_of(client, seeded["token"], task["id"])
    assert session["admitted"] is True
    with engine.connect() as conn:
        leases = conn.execute(select(agent_lease).where(and_(
            agent_lease.c.organization_id == seeded["org_id"],
            agent_lease.c.task_id == task["id"],
        ))).mappings().all()
        stored = agent_sessions.get_session(conn, seeded["org_id"], session["id"])
    assert len(leases) == 1
    assert leases[0]["state"] == "queued" and leases[0]["fence"] == 1
    assert leases[0]["harness"] == "pi"
    # The session records which lease it is waiting on; the lease stays WP07's row.
    assert stored["lease_id"] == leases[0]["id"]
    assert stored["state"] == "queued"


# ------------------------------------------------------------------- the clock
def test_the_ack_lands_inside_ten_seconds_on_the_admitted_path(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = agent_sessions.open_session(conn, seeded["org_id"], issue_id,
                                          agent["userId"], seeded["user_id"], at=T0)
        row = agent_sessions.apply_schedule(conn, row, [], at=T0)
        row = agent_sessions.acknowledge(conn, row, at=_at(9.5))
        assert agent_sessions.ack_deadline_missed(row) is False
        acts = agent_sessions.activities(conn, seeded["org_id"], row["id"])
    assert acts[0]["payload"]["elapsed_seconds"] == 9.5
    assert acts[0]["body"] == "Starting now."


def test_the_ack_lands_inside_ten_seconds_when_queued(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = agent_sessions.open_session(conn, seeded["org_id"], issue_id,
                                          agent["userId"], seeded["user_id"], at=T0)
        row = agent_sessions.apply_schedule(conn, row, ["capacity", "budget"], at=T0)
        row = agent_sessions.acknowledge(conn, row, at=_at(4))
        assert agent_sessions.ack_deadline_missed(row) is False
        acts = agent_sessions.activities(conn, seeded["org_id"], row["id"])
        # Idempotent: a retried delegation does not write a second start.
        again = agent_sessions.acknowledge(conn, row, at=_at(5))
        assert again["acked_at"] == row["acked_at"]
        written = conn.execute(select(agent_activity).where(
            agent_activity.c.session_id == row["id"])).all()
    assert acts[0]["body"] == "Waiting: capacity, budget."
    assert acts[0]["payload"]["reasons"] == ["capacity", "budget"]
    assert len(written) == 1


def test_a_late_ack_is_recorded_as_late_not_corrected(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = agent_sessions.open_session(conn, seeded["org_id"], issue_id,
                                          agent["userId"], seeded["user_id"], at=T0)
        row = agent_sessions.apply_schedule(conn, row, ["window"], at=T0)
        row = agent_sessions.acknowledge(conn, row, at=_at(10.5))
        acts = agent_sessions.activities(conn, seeded["org_id"], row["id"])
    assert agent_sessions.ack_deadline_missed(row) is True
    assert acts[0]["payload"]["elapsed_seconds"] == 10.5


def test_a_waiting_session_goes_stale_at_thirty_minutes_and_recovers(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = agent_sessions.open_session(conn, seeded["org_id"], issue_id,
                                          agent["userId"], seeded["user_id"], at=T0)
        row = agent_sessions.apply_schedule(conn, row, ["capacity"], at=T0)
        row = agent_sessions.acknowledge(conn, row, at=T0)
        assert agent_sessions.mark_stale(row, idle_seconds=1799) == "pending"
        assert agent_sessions.mark_stale(row, idle_seconds=1800) == "stale"
        # Stale is recoverable: one ephemeral thought per window, and the durable
        # transcript is unchanged by it.
        recovered = agent_sessions.keepalive(conn, row, at=_at(1800), idle_seconds=1800)
        assert recovered["status"] == "pending"
        durable = agent_sessions.activities(conn, seeded["org_id"], row["id"])
    assert [a["kind"] for a in durable] == ["thought"]
    assert durable[0]["ephemeral"] is False


def test_only_a_human_authors_a_prompt_activity(delegated):
    """Linear: "An agent cannot generate a prompt type activity"."""
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        row = _fresh_session(conn, seeded, agent["userId"], issue_id)
        with pytest.raises(ValueError):
            agent_sessions.append_activity(conn, seeded["org_id"], row["id"], "prompt",
                                           body="hello", author_kind="agent")
        made = agent_sessions.append_activity(
            conn, seeded["org_id"], row["id"], "prompt", body="hello",
            author_kind="user", author_id=seeded["user_id"], signal="continue")
    assert made["kind"] == "prompt"


# --------------------------------------------------------- window, approval, queue
def test_the_working_window_follows_the_wall_clock_across_a_dst_boundary():
    hours = json.dumps([{"start": "09:00", "end": "17:00", "days": [0, 1, 2, 3, 4]}])
    # Europe/Madrid: UTC+1 before the spring change, UTC+2 after it.
    winter = datetime(2026, 3, 27, 7, 30, tzinfo=timezone.utc)   # 08:30 local
    summer = datetime(2026, 4, 1, 7, 30, tzinfo=timezone.utc)    # 09:30 local
    assert agent_schedule.working_window(hours, "Europe/Madrid", winter) is False
    assert agent_schedule.working_window(hours, "Europe/Madrid", summer) is True
    # An overnight window belongs to the day it started on.
    night = json.dumps([{"start": "22:00", "end": "06:00", "days": [4]}])
    late_friday = datetime(2026, 3, 27, 23, 0, tzinfo=timezone.utc)
    assert agent_schedule.working_window(night, "UTC", late_friday) is True


def test_an_unreadable_window_policy_does_not_stop_the_scheduler(delegated):
    engine, _client, seeded, agent, issue_id = delegated
    with engine.begin() as conn:
        conn.execute(agent_member.update().where(
            agent_member.c.user_id == agent["userId"]).values(working_hours="{not json"))
        task = conn.execute(select(issue).where(issue.c.id == issue_id)).mappings().one()
        row = agent_sessions.open_session(conn, seeded["org_id"], issue_id,
                                          agent["userId"], seeded["user_id"], at=T0)
        member = agent_schedule.load_member(conn, seeded["org_id"], agent["userId"])
        facts = agent_schedule.gather(conn, seeded["org_id"], member, dict(task), row,
                                      datetime(2026, 11, 13, 3, tzinfo=timezone.utc))
    assert facts.in_window is True


def test_an_unfinished_child_blocks_the_run(delegated):
    engine, client, seeded, agent, issue_id = delegated
    _create_issue(client, seeded["token"], seeded["team_id"], parent_id=issue_id,
                  title="unfinished child")
    with engine.begin() as conn:
        task = conn.execute(select(issue).where(issue.c.id == issue_id)).mappings().one()
        row = agent_sessions.open_session(conn, seeded["org_id"], issue_id,
                                          agent["userId"], seeded["user_id"], at=T0)
        member = agent_schedule.load_member(conn, seeded["org_id"], agent["userId"])
        facts = agent_schedule.gather(conn, seeded["org_id"], member, dict(task), row,
                                      datetime(2026, 11, 13, 3, tzinfo=timezone.utc))
    assert facts.dependencies_clear is False


def test_approval_is_required_then_satisfied_by_a_human_answer(workspace):
    engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], require_approval=True, engine=engine)
    task = _create_issue(client, seeded["token"], seeded["team_id"],
                         delegate_id=agent["userId"])
    session = _session_of(client, seeded["token"], task["id"])
    assert session["queueReason"] == ["approval"]
    assert session["admitted"] is False
    with engine.begin() as conn:
        row = agent_sessions.get_session(conn, seeded["org_id"], session["id"])
        decision = conn.execute(select(issue_decision).where(
            issue_decision.c.id == row["approval_decision_id"])).mappings().one()
        assert decision["status"] == "open"
        conn.execute(issue_decision.update().where(
            issue_decision.c.id == decision["id"]).values(
            status="answered", chosen_option="approve", answer="ok",
            answered_by_id=seeded["user_id"], updated_at=_at(60)))
        row = agent_schedule.reevaluate(conn, row, at=_at(61))
    assert row["admitted_at"] is not None and row["queue_reason"] is None


def test_a_rejection_is_not_an_approval(workspace):
    engine, client, seeded = workspace
    agent = _create_agent(client, seeded["token"], require_approval=True, engine=engine)
    task = _create_issue(client, seeded["token"], seeded["team_id"],
                         delegate_id=agent["userId"])
    session = _session_of(client, seeded["token"], task["id"])
    with engine.begin() as conn:
        row = agent_sessions.get_session(conn, seeded["org_id"], session["id"])
        conn.execute(issue_decision.update().where(
            issue_decision.c.id == row["approval_decision_id"]).values(
            status="answered", chosen_option="reject", answer="no",
            answered_by_id=seeded["user_id"], updated_at=_at(60)))
        row = agent_schedule.reevaluate(conn, row, at=_at(61))
    assert row["queue_reason"] == json.dumps(["approval"])
    assert row["admitted_at"] is None


def test_the_queue_partition_is_the_agent_not_a_kwarg():
    """T028: DBOS's per-partition bound, reached through ``EnqueueOptions``.

    ``Queue.enqueue``'s ``**kwargs`` are the workflow's arguments, so a partition key
    passed there would land in the turn payload and every run would share the default
    partition. This pins that the key travels as an enqueue option.
    """
    from hoja_server import agent_durable

    seen = {}

    class _FakeDBOS:
        @staticmethod
        def enqueue_workflow_with_options(options, *args, **kwargs):
            seen["options"], seen["args"] = options, args
            return "handle"

    original = agent_durable.DBOS
    agent_durable.DBOS = _FakeDBOS
    try:
        handle = agent_durable.enqueue_agent_turn(
            "agent-turn-1", {"conversation_id": "c"}, agent_user_id="agent-user")
    finally:
        agent_durable.DBOS = original
    assert handle == "handle"
    assert seen["options"]["queue_partition_key"] == "agent-user"
    assert seen["options"]["queue_name"] == agent_durable.AGENT_QUEUE
    assert seen["options"]["workflow_name"] == agent_durable.TURN_WORKFLOW
    assert seen["args"] == ({"conversation_id": "c"},)


def test_registering_the_queue_uses_the_capacity_it_is_given():
    import asyncio

    from hoja_server import agent_durable

    seen = {}

    class _FakeDBOS:
        @staticmethod
        async def register_queue_async(name, **kwargs):
            seen["name"], seen["kwargs"] = name, kwargs
            return "queue"

    original = agent_durable.DBOS
    agent_durable.DBOS = _FakeDBOS
    try:
        asyncio.run(agent_durable.register_agent_queue(3))
    finally:
        agent_durable.DBOS = original
    assert seen["name"] == agent_durable.AGENT_QUEUE
    assert seen["kwargs"]["partition_concurrency"] == 3
