"""The usage ledger: credits, run-minutes, budgets and the usage screen (HOJA-602-A-12, WP14).

The acceptance list this file exists to prove, one group each:

====================================================  ============================================
test                                                   acceptance
====================================================  ============================================
events then finish reconcile to the run's own totals   "ledger totals equal the sum of session
                                                       token counts (a reconciliation test)"
a duplicate finish adds no second row                  the same, from the other side: the writer
                                                       cannot double count a re-delivery
a run over budget is queued with reason ``budget``     "a run over budget is queued with reason
                                                       budget", driven by the LEDGER, not by a
                                                       configuration value
``budget_action=stop`` refuses the lease               the same acceptance's other half
a bring-your-own key costs nothing and records credits the pricing decision's "free" line
managed compute floors the run at 15 minutes           "managed compute $0.005/run-minute, 15
                                                       minute floor"
cost comes from tokens and the price card              the harness study: ``usage.cost`` was 0
                                                       everywhere, so it is never trusted
a new price card cannot rewrite a past row             "price-card changes must not rewrite
                                                       history"
there is no update or delete path                      "an append-only test: there is no code
                                                       path that updates or deletes usage_event"
usage reads are org-scoped and admin-only              "a cross-tenant usage read returns 0, and
                                                       a non-admin is refused"
revision 70 applies to a 69 database, twice            "migrate main (69) -> yours (70), both
                                                       exit 0, idempotent"
a migrated database binds the new table and ``/ready``
answers 200                                            WP01: an unclassified or unbound table
                                                       makes ``/ready`` 503
====================================================  ============================================

SQLite by default and PostgreSQL with ``HOJA_TEST_RLS=1``, exactly like the other agent suites:
the RLS probe is what proves the new table is tenant-scoped, and a test that only ever ran on
SQLite could not see a missing ``hoja_tenant`` policy.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import and_, inspect, select
from starlette.testclient import TestClient

from hoja_server import logging_setup, runner_api, usage_ledger
from hoja_server.app import create_app
from hoja_server.db import (
    agent_lease,
    agent_session,
    audit_event,
    seed_tenant,
    usage_event,
)
from hoja_server.store import Store
from tests import rls_mode

#: The harness a delegated session is queued for (WP06's default, and WP11 ships a
#: profile for it).
DELEGATED_HARNESS = "pi"
GENERIC = "generic"


def _post(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    return client.post("/graphql", json={"query": query, "variables": variables or {}},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "usage-ledger-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    logging_setup.forget_secrets()
    engine, kwargs = rls_mode.workspace(tmp_path, "usage-ledger")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    yield {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo,
           "store": Store(engine), "kwargs": kwargs}
    logging_setup.forget_secrets()


# --------------------------------------------------------------------------- helpers


def _agent(world, *, name: str = "Scout", org: str = "alpha") -> dict:
    tenant = world[org]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{
          agentMemberCreate(input: {{ name: "{name}" }}) {{
            success agentMember {{ id userId }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]


def _input_literal(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_input_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_input_literal(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"cannot render {value!r} as GraphQL input")


def _set_policy(world, member_id: str, document: dict, *, org: str = "alpha") -> dict:
    body = _post(world["client"], world[org]["token"], f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}", input: {_input_literal(document)}) {{
            success agentPolicy {{ id version monthlyCreditBudget budgetAction }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentPolicyUpdate"]["agentPolicy"]


def _delegated_task(world, agent: dict, *, org: str = "alpha") -> str:
    """Create a task delegated to the agent; WP06 opens the session and queues a lease."""
    tenant = world[org]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{
          issueCreate(input: {{ teamId: "{tenant["team_key"]}",
            title: "Delegated work", delegateId: "{agent["userId"]}" }}) {{
            success issue {{ id }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]["id"]


def _enroll(world, *, org: str = "alpha", name: str = "runner-1") -> dict:
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment,
        "harnesses": [GENERIC, DELEGATED_HARNESS]})
    assert response.status_code == 200, response.text
    return response.json()


def _poll(world, identity: dict) -> dict:
    response = world["client"].post("/runner/lease",
                                    json={"runner_id": identity["runner_id"],
                                          "poll_timeout_ms": 0},
                                    headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _granted_lease(world, agent: dict, *, org: str = "alpha") -> tuple[dict, dict]:
    """(lease, runner identity) for a delegated task, granted the production way."""
    _delegated_task(world, agent, org=org)
    identity = _enroll(world, org=org)
    payload = _poll(world, identity)
    assert "lease" in payload, payload
    return payload["lease"], identity


def _queue_directly(world, task_id: str, *, org: str = "alpha") -> dict:
    """Queue one attempt at WP07's seam, without WP06's schedule machine.

    Used where the schedule check would refuse to admit the attempt at all (an
    exhausted budget) and the question is what the LEASE gate does with it.
    """
    return runner_api.enqueue_task(world["engine"], organization_id=world[org]["org_id"],
                                   task_id=task_id, harness=DELEGATED_HARNESS,
                                   prompt="do the task")


def _usage_document(input_tokens: int, output_tokens: int, *, cache: int = 0,
                    model: str = "pi/default", seconds: float = 0,
                    auth_mode: str = "managed-key", **extra) -> dict:
    document = {"inputTokens": input_tokens, "outputTokens": output_tokens,
                "cacheReadTokens": cache, "model": model, "runSeconds": seconds,
                "authMode": auth_mode}
    document.update(extra)
    return document


def _report_event(world, lease: dict, identity: dict, seq: int, document: dict) -> dict:
    response = world["client"].post("/runner/events", json={
        "lease_id": lease["lease_id"], "run_id": lease["run_id"],
        "fence": lease["fence"], "token": lease["token"],
        "events": [{"seq": seq, "kind": "usage", "payload": document}],
    }, headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _finish(world, lease: dict, identity: dict, *, document: dict | None = None,
            state: str = "completed", summary: str = "done") -> dict:
    body = {"lease_id": lease["lease_id"], "run_id": lease["run_id"],
            "fence": lease["fence"], "token": lease["token"], "state": state,
            "summary": summary, "idempotency_key": f"{lease['run_id']}:finish",
            "effects": []}
    if document is not None:
        body["usage"] = document
    response = world["client"].post("/runner/finish", json=body,
                                    headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _session_for(world, lease: dict, *, org: str = "alpha") -> dict:
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_session).where(and_(
            agent_session.c.organization_id == world[org]["org_id"],
            agent_session.c.lease_id == lease["lease_id"],
        ))).mappings().one()
    return dict(row)


def _rows(world, *, org: str = "alpha") -> list[dict]:
    with world["engine"].connect() as conn:
        return [dict(row) for row in conn.execute(
            select(usage_event).where(usage_event.c.organization_id == world[org]["org_id"])
            .order_by(usage_event.c.recorded_at, usage_event.c.id)).mappings().all()]


def _spend(world, agent: dict, credits_tokens: int, *, org: str = "alpha",
           run_id: str = "prior-run", model: str = "pi/default") -> dict:
    """Put an earlier run's spend in the ledger, so a budget has something to compare."""
    with world["engine"].begin() as conn:
        return usage_ledger.record_report(
            conn, organization_id=world[org]["org_id"], run_id=run_id,
            agent_user_id=agent["userId"],
            usage={"input_tokens": credits_tokens, "output_tokens": 0,
                   "cache_read_tokens": 0, "model": model, "run_seconds": 0,
                   "auth_mode": "byo-key", "compute": "customer"},
            source=usage_ledger.SOURCE_FINISH)


# ------------------------------------- the ledger, filled from the runner's two reports


def test_events_then_finish_reconcile_to_the_run_token_totals(world):
    """The reconciliation acceptance, end to end through the runner's own routes.

    A runner reports cumulative counters twice on the event stream and once more at
    finish, where the finish's numbers already include the events'. The ledger must sum
    to the runner's LAST report — not to the sum of the reports, which is what a ledger
    that appended each report verbatim would do.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    session = _session_for(world, lease)

    _report_event(world, lease, identity, 1, _usage_document(1_000, 200, seconds=30))
    _report_event(world, lease, identity, 2, _usage_document(3_000, 700, cache=500, seconds=120))
    _finish(world, lease, identity,
            document=_usage_document(4_000, 900, cache=600, seconds=1_260))

    with world["engine"].connect() as conn:
        totals = usage_ledger.session_totals(conn, world["alpha"]["org_id"], session["id"])
        month = usage_ledger.month_totals(conn, world["alpha"]["org_id"])
    # The runner's last cumulative report, exactly.
    assert totals["input_tokens"] == 4_000, totals
    assert totals["output_tokens"] == 900, totals
    assert totals["cache_read_tokens"] == 600, totals
    assert totals["tokens"] == usage_ledger.processed_tokens(4_000, 900, 600)
    # 1 credit per 1,000 processed tokens, and the rounding is per RUN, not per report:
    # 5,500 tokens is 6 credits whether it arrived in one report or in three.
    assert totals["credits"] == 6, totals
    # Org totals agree with the session's: nothing landed anywhere else.
    assert month["input_tokens"] == 4_000 and month["credits"] == 6, month
    # Three reports, three rows: each report's delta is stored, so both the shape and
    # the total are checkable. A ledger that stored the running total per row would
    # have a bigger sum than the run's own report, and this assertion would fail.
    rows = _rows(world)
    assert len(rows) == 3, rows
    assert [row["source"] for row in rows] == ["events", "events", "finish"]
    assert [row["cumulative_tokens"] for row in rows] == [1_200, 4_200, 5_500]
    # The usage report is metered, not narrated: it must not appear in the transcript.
    with world["engine"].connect() as conn:
        narrated = conn.execute(select(agent_lease.c.last_event_seq).where(
            agent_lease.c.id == lease["lease_id"])).scalar()
    assert narrated == 2, "a usage event still advances the stream's sequence"


def test_a_duplicate_finish_adds_no_second_row(world):
    """A re-sent finish is the same finish: the ledger must not bill it twice."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    _report_event(world, lease, identity, 1, _usage_document(2_000, 500))
    _finish(world, lease, identity, document=_usage_document(3_000, 800))
    before = _rows(world)
    assert len(before) == 2

    # The same lease, the same key, the same numbers.
    again = _finish(world, lease, identity, document=_usage_document(3_000, 800))
    assert again["duplicate"] is True, again
    after = _rows(world)
    assert len(after) == 2, "a duplicate finish appended another row"
    with world["engine"].connect() as conn:
        assert usage_ledger.month_totals(conn, world["alpha"]["org_id"])["credits"] == 4


def test_a_report_behind_the_ledger_never_subtracts(world):
    """Counters are cumulative per run and never go backwards: a lower one adds nothing.

    The contract is a monotone cumulative counter. A report that is behind what is stored
    is a re-delivery (or a runner that restarted its own count), and the one thing a
    ledger may not do is let it become a refund.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    _report_event(world, lease, identity, 1, _usage_document(2_000, 500, seconds=30))
    _report_event(world, lease, identity, 2, _usage_document(500, 100, seconds=30))
    rows = _rows(world)
    assert len(rows) == 1, rows
    with world["engine"].connect() as conn:
        totals = usage_ledger.month_totals(conn, world["alpha"]["org_id"])
    assert totals["input_tokens"] == 2_000 and totals["output_tokens"] == 500, totals


# ------------------------------------------------------------------------ the budget


def test_a_run_over_budget_is_queued_with_the_reason_budget(world):
    """The schedule check reads the LEDGER: spend, not a configured remittance.

    Two delegations, identical except for what the ledger says one agent already spent
    this month. The agent under its ceiling is admitted; the one over it is queued with
    ``budget`` in its reason list, in the contract's fixed order.
    """
    thrifty = _agent(world, name="Thrifty")
    _set_policy(world, thrifty["id"], {"teams": [world["alpha"]["team_id"]],
                                       "monthlyCreditBudget": 100})
    _delegated_task(world, thrifty)
    with world["engine"].connect() as conn:
        admitted = conn.execute(select(agent_session.c.queue_reason, agent_session.c.admitted_at)
                                .where(agent_session.c.agent_user_id == thrifty["userId"])
                                .order_by(agent_session.c.created_at)).mappings().all()
    assert admitted[-1]["queue_reason"] is None, admitted
    assert admitted[-1]["admitted_at"], "an agent inside its budget is admitted"

    spender = _agent(world, name="Spender")
    _set_policy(world, spender["id"], {"teams": [world["alpha"]["team_id"]],
                                       "monthlyCreditBudget": 100,
                                       "budgetAction": "queue"})
    # 150 credits already spent this month, from a run that really is in the ledger.
    _spend(world, spender, 150_000, run_id="spender-run-1")
    _delegated_task(world, spender)
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_session.c.queue_reason, agent_session.c.admitted_at,
                                  agent_session.c.id)
                           .where(agent_session.c.agent_user_id == spender["userId"])
                           .order_by(agent_session.c.created_at.desc())).mappings().first()
        budget = usage_ledger.budget_status(conn, world["alpha"]["org_id"], spender["userId"])
        activities = conn.execute(select(agent_session)).mappings().all()
    assert budget["used"] == 150 and budget["limit"] == 100, budget
    assert budget["exhausted"] is True
    assert json.loads(row["queue_reason"]) == ["budget"], row["queue_reason"]
    assert row["admitted_at"] is None, "an exhausted agent must not be admitted"
    assert activities, "the session exists"
    # The queue is not a silent limbo: the acknowledgement says which fact it waits on.
    with world["engine"].connect() as conn:
        from hoja_server.db import agent_activity
        thought = conn.execute(select(agent_activity.c.body).where(and_(
            agent_activity.c.organization_id == world["alpha"]["org_id"],
            agent_activity.c.session_id == row["id"],
        ))).scalars().first()
    assert thought == "Waiting: budget.", thought


def test_budget_action_stop_refuses_the_lease_the_ledger_says_is_spent(world):
    """``stop`` is the other half of the acceptance, at the place a run would start."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]],
                                     "monthlyCreditBudget": 10, "budgetAction": "stop"})
    # Spend past the ceiling in the ledger, so the refusal can only come from it.
    _spend(world, agent, 40_000, run_id="stop-run-1")
    # The delegation is created after the spend, so the schedule check queues it
    # without a lease (that half is the previous test); this one asks the lease gate.
    task_id = _delegated_task(world, agent)
    queued = _queue_directly(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert response.get("lease") is None, response
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_lease.c.state, agent_lease.c.cancel_reason).where(
            agent_lease.c.id == queued["id"])).mappings().one()
        audited = [json.loads(entry["detail"]) for entry in conn.execute(
            select(audit_event).where(audit_event.c.action == "agent.lease.refuse")).mappings()]
    assert row["state"] == "canceled", row
    assert row["cancel_reason"] == "policy_budget", row
    assert any(entry.get("reason") == "policy_budget" and entry.get("credits_used") == 40
               for entry in audited), audited


def test_an_agent_inside_its_budget_is_not_refused(world):
    """The near miss: the same setup with the spend below the ceiling grants the lease.

    Without this, "the budget refuses" would pass on a screen that refused everything.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]],
                                     "monthlyCreditBudget": 50, "budgetAction": "stop"})
    _delegated_task(world, agent)
    _spend(world, agent, 10_000, run_id="inside-run-1")
    identity = _enroll(world)
    payload = _poll(world, identity)
    assert payload.get("lease"), payload


# --------------------------------------------------------------------- money and units


def test_a_bring_your_own_key_records_credits_and_costs_nothing(world):
    """BYO: the customer paid their provider, so the money is zero — and the usage is real."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    _finish(world, lease, identity,
            document=_usage_document(5_000, 1_000, seconds=90, auth_mode="byo-key"))
    row = _rows(world)[0]
    assert row["auth_mode"] == "byo-key"
    assert row["compute"] == "customer"
    assert row["credits"] == 6, row
    assert row["cost_usd"] == "0.000000", "a bring-your-own key is free"
    assert row["compute_cost_usd"] == "0.000000"
    # No floor on compute nobody rented: 90 seconds is two minutes, not fifteen.
    assert row["run_minutes"] == 2, row


def test_managed_compute_floors_the_run_at_fifteen_minutes(world):
    """The floor belongs to the run: three reports on one run bill 15 then 0 then 6."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    _report_event(world, lease, identity, 1, _usage_document(1_000, 100, seconds=30))
    _report_event(world, lease, identity, 2, _usage_document(1_200, 100, seconds=120))
    _finish(world, lease, identity,
            document=_usage_document(1_500, 100, seconds=1_260))
    rows = _rows(world)
    assert [row["run_minutes"] for row in rows] == [15, 0, 6], rows
    assert rows[0]["compute_cost_usd"] == "0.075000", "15 minutes at $0.005"
    assert rows[2]["compute_cost_usd"] == "0.030000", "6 minutes at $0.005"
    with world["engine"].connect() as conn:
        assert usage_ledger.month_totals(conn, world["alpha"]["org_id"])["run_minutes"] == 21


def test_cost_comes_from_tokens_and_the_price_card_not_from_the_harness(world):
    """The study's finding: ``usage.cost`` was 0 in every log, so it is never believed."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    document = _usage_document(1_000_000, 1_000_000, model="pi/default")
    document["cost"] = 0          # exactly what a harness reports
    document["usageCost"] = 0
    _finish(world, lease, identity, document=document)
    row = _rows(world)[0]
    expected = (Decimal(1_000_000) * Decimal("0.60")
                + Decimal(1_000_000) * Decimal("2.20")) / Decimal(1_000_000)
    assert row["cost_usd"] == usage_ledger.money(expected), row
    assert Decimal(row["cost_usd"]) > 0, "a harness-reported zero was believed"
    assert row["price_card"] == usage_ledger.PRICE_CARD_VERSION
    assert row["model"] == "pi/default"
    # Credits are tokens, not money: 2,000,000 processed tokens is 2,000 credits.
    assert row["credits"] == 2_000, row


def test_a_new_price_card_cannot_rewrite_a_past_row(world, monkeypatch):
    """A reprice applies to future rows only: history is strings that were already written."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    with world["engine"].begin() as conn:
        first = usage_ledger.record_report(
            conn, organization_id=world["alpha"]["org_id"], run_id="card-run-1",
            agent_user_id=agent["userId"],
            usage={"input_tokens": 100_000, "output_tokens": 0, "cache_read_tokens": 0,
                   "model": "pi/default", "run_seconds": 0,
                   "auth_mode": "managed-key", "compute": "managed"},
            source=usage_ledger.SOURCE_FINISH)
    assert first["cost_usd"] == "0.060000", first["cost_usd"]  # 100k at $0.60/M

    monkeypatch.setitem(usage_ledger.PRICE_CARD_RATES, "pi/default",
                        {"input": "6.00", "cache_read": "0.60", "output": "22.00"})
    with world["engine"].begin() as conn:
        second = usage_ledger.record_report(
            conn, organization_id=world["alpha"]["org_id"], run_id="card-run-2",
            agent_user_id=agent["userId"],
            usage={"input_tokens": 100_000, "output_tokens": 0, "cache_read_tokens": 0,
                   "model": "pi/default", "run_seconds": 0,
                   "auth_mode": "managed-key", "compute": "managed"},
            source=usage_ledger.SOURCE_FINISH)
        stored = conn.execute(select(usage_event.c.cost_usd).where(
            usage_event.c.id == first["id"])).scalar()
    assert second["cost_usd"] == "0.600000"
    assert stored == "0.060000", "the new card rewrote a row that had already been priced"


# ------------------------------------------------------------------------- append-only


LEDGER_SOURCE = Path(usage_ledger.__file__)


def test_there_is_no_code_path_that_updates_or_deletes_usage_event():
    """Append-only, proven by reading every line of server code that names the table.

    Three checks, because an absence is easy to claim: no SQLAlchemy update or delete
    construction, no raw SQL statement against the table, and exactly one INSERT — the
    single writer this module documents. A correction is a new row, so there is no
    "fix the row" path to find.
    """
    server = LEDGER_SOURCE.parent
    offenders: list[str] = []
    inserts: list[str] = []
    for path in sorted(server.glob("*.py")):
        text = path.read_text()
        for pattern in (r"usage_event\.update\(", r"usage_event\.delete\(",
                        r"UPDATE\s+usage_event", r"DELETE\s+FROM\s+usage_event",
                        r"delete\(usage_event\)", r"update\(usage_event\)"):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}: {match.group(0)}")
        inserts.extend(f"{path.name}" for _ in re.finditer(r"usage_event\.insert\(", text))
    assert not offenders, "usage_event is meant to be append-only: " + "; ".join(offenders)
    assert inserts == ["usage_ledger.py"], (
        "the ledger must have exactly one writer, found: " + ", ".join(inserts))

    # And the module's own surface offers no mutation: a caller cannot ask for one.
    for name in dir(usage_ledger):
        lowered = name.lower()
        if lowered.startswith(("update_", "delete_", "remove_", "purge_")):
            pytest.fail(f"usage_ledger exposes {name}, which reads as a mutation path")


def test_a_correction_is_a_new_row_rather_than_an_edit(world):
    """The one supported way to fix a number: append what the run should have said."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    with world["engine"].begin() as conn:
        original = usage_ledger.record_report(
            conn, organization_id=world["alpha"]["org_id"], run_id="fix-run",
            agent_user_id=agent["userId"],
            usage={"input_tokens": 1_000, "output_tokens": 0, "cache_read_tokens": 0,
                   "model": "pi/default", "run_seconds": 0,
                   "auth_mode": "byo-key", "compute": "customer"},
            source=usage_ledger.SOURCE_FINISH)
        usage_ledger.record_correction(conn, organization_id=world["alpha"]["org_id"],
                                       agent_user_id=agent["userId"], run_id="fix-run",
                                       model="pi/default", credits=1, input_tokens=1_000)
        rows = [dict(row) for row in conn.execute(select(usage_event)).mappings()]
    assert len(rows) == 2
    assert rows[0]["credits"] == original["credits"] == 1, "the original row was rewritten"
    assert rows[1]["source"] == "correction"
    with world["engine"].connect() as conn:
        assert usage_ledger.month_totals(conn, world["alpha"]["org_id"])["credits"] == 2


# --------------------------------------------------------------- the GraphQL surface


SUMMARY = '''
    query Usage($month: String, $groupBy: String) {
      usageSummary(month: $month, groupBy: $groupBy) {
        month groupBy credits runMinutes costUsd totalUsd priceCard
        rows { key label agentUserId model month credits runMinutes runs costUsd }
      }
    }'''


def test_usage_reads_are_admin_only_and_org_scoped(world):
    """A cross-tenant read returns zero, and a member of the workspace is refused.

    Both halves matter. The org filter is what this module promises; the row-level
    policy is what the database enforces, and the two are checked in the same place so
    a test that ran only on SQLite could not silently pass a missing policy.
    """
    from tests.test_member_management import _add_member

    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    _finish(world, lease, identity, document=_usage_document(3_000, 1_000))

    mine = _post(world["client"], world["alpha"]["token"], SUMMARY,
                 {"groupBy": "agent"})
    assert "errors" not in mine, mine
    summary = mine["data"]["usageSummary"]
    assert summary["credits"] == 4, summary
    assert summary["rows"][0]["agentUserId"] == agent["userId"]

    theirs = _post(world["client"], world["bravo"]["token"], SUMMARY, {"groupBy": "agent"})
    assert "errors" not in theirs, theirs
    foreign = theirs["data"]["usageSummary"]
    assert foreign["credits"] == 0 and foreign["rows"] == [], foreign
    assert foreign["totalUsd"] == "0.000000"

    member_id = _add_member(world["client"], world["alpha"]["token"], "member@alpha.test")
    _, member_token = world["store"].create_api_key(
        world["alpha"]["org_id"], member_id, "member key", "read_write", None)
    refused = _post(world["client"], member_token, SUMMARY, {"groupBy": "agent"})
    assert "FORBIDDEN" in str(refused.get("errors")), refused
    refused_events = _post(world["client"], member_token, '''
        query { usageEvents { id } }''')
    assert "FORBIDDEN" in str(refused_events.get("errors")), refused_events


def test_the_screen_can_group_by_agent_model_or_month(world):
    """Every grouping the resolver advertises answers, and they agree on the total."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    _finish(world, lease, identity,
            document=_usage_document(2_000, 0, model="pi/default"))
    with world["engine"].begin() as conn:
        usage_ledger.record_report(
            conn, organization_id=world["alpha"]["org_id"], run_id="other-model",
            agent_user_id=agent["userId"],
            usage={"input_tokens": 1_000, "output_tokens": 0, "cache_read_tokens": 0,
                   "model": "openai/gpt-5-codex", "run_seconds": 0,
                   "auth_mode": "byo-key", "compute": "customer"},
            source=usage_ledger.SOURCE_FINISH)

    totals = {}
    for grouping in ("agent", "model", "month"):
        body = _post(world["client"], world["alpha"]["token"], SUMMARY, {"groupBy": grouping})
        assert "errors" not in body, body
        payload = body["data"]["usageSummary"]
        assert payload["groupBy"] == grouping
        assert payload["rows"], f"grouping {grouping} returned no rows"
        totals[grouping] = payload["credits"]
        assert payload["priceCard"] == usage_ledger.PRICE_CARD_VERSION
    assert totals == {"agent": 3, "model": 3, "month": 3}, totals

    by_model = _post(world["client"], world["alpha"]["token"], SUMMARY, {"groupBy": "model"})
    keys = {row["model"] for row in by_model["data"]["usageSummary"]["rows"]}
    assert keys == {"pi/default", "openai/gpt-5-codex"}, keys

    bad = _post(world["client"], world["alpha"]["token"], SUMMARY, {"groupBy": "nonsense"})
    assert "BAD_USER_INPUT" in str(bad.get("errors")), bad


def test_a_budget_report_is_readable_for_one_agent(world):
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]],
                                     "monthlyCreditBudget": 25, "budgetAction": "stop"})
    _spend(world, agent, 10_000, run_id="budget-report-run")
    body = _post(world["client"], world["alpha"]["token"], f'''
        query {{ usageBudget(agentUserId: "{agent["userId"]}") {{
          month limitCredits usedCredits remainingCredits exhausted action }} }}''')
    assert "errors" not in body, body
    budget = body["data"]["usageBudget"]
    assert budget["limitCredits"] == 25 and budget["usedCredits"] == 10, budget
    assert budget["remainingCredits"] == 15
    assert budget["exhausted"] is False
    assert budget["action"] == "stop"


# ------------------------------------------------------------------------- migrations


def test_revision_70_applies_to_a_69_database_twice(tmp_path):
    """The upgrade path is 69 -> 70 on an existing database, not a fresh create.

    Both runs must exit clean: the second is the idempotence proof, and it is the run
    that catches a migration which creates its table unconditionally.
    """
    from hoja_server.db import (
        CURRENT_SCHEMA_REVISION,
        applied_schema_revision,
        make_engine,
        migrate,
    )
    from tests import legacy_schema

    assert CURRENT_SCHEMA_REVISION == 70
    engine = make_engine(tmp_path / "usage-upgrade.db")
    legacy_schema.create_schema(engine, 69)
    with engine.begin() as conn:
        # A 69 database has no ledger; the fresh create above used today's metadata, so
        # the table is dropped to make the upgrade path real rather than a no-op.
        conn.exec_driver_sql("DROP TABLE IF EXISTS usage_event")
    assert applied_schema_revision(engine) == 69

    migrate(engine)
    assert applied_schema_revision(engine) == 70
    with engine.connect() as conn:
        assert "usage_event" in set(inspect(conn).get_table_names())
        columns = {c["name"] for c in inspect(conn).get_columns("usage_event")}
    assert {"organization_id", "agent_user_id", "session_id", "model", "input_tokens",
            "output_tokens", "cache_read_tokens", "credits", "run_minutes", "source",
            "price_card"} <= columns, columns

    migrate(engine)
    assert applied_schema_revision(engine) == 70


def test_a_migrated_database_binds_the_ledger_and_ready_answers_200(tmp_path):
    """`hoja-serve migrate` must bind revision 70's table, and `/ready` must agree.

    WP01 makes ``/ready`` answer 503 when any table is unclassified or any tenant table
    is unbound, and ``db.migrate`` calls ``rls_policy.heal`` to bind what a revision just
    created. This takes the path a deployment takes — migrate as the owner, then serve
    as a role with no BYPASSRLS that owns nothing — and asserts the new table is
    classified as a tenant row, carries the ``hoja_tenant`` policy, and leaves
    ``/ready`` at 200.
    """
    if not rls_mode.ENABLED:
        pytest.skip("needs PostgreSQL (HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL)")
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from hoja_server import rls_policy
    from hoja_server.db import make_engine, migrate

    owner_url, schema = rls_mode.new_schema_url()
    owner = make_engine(owner_url)
    migrate(owner)  # heal runs here, as the owner credential does in a deployment

    role, password = f"hoja_wp14_{schema[-6:]}", "wp14-probe"
    with owner.begin() as conn:
        conn.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT "
                          f"PASSWORD '{password}'"))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
        conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA '
                          f'"{schema}" TO {role}'))
        conn.execute(text(f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA '
                          f'"{schema}" TO {role}'))
        conn.execute(text(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA "{schema}" TO {role}'))
    restricted_url = make_url(owner_url).set(username=role, password=password).render_as_string(
        hide_password=False)
    restricted = make_engine(restricted_url)
    try:
        with restricted.connect() as conn:
            rows = {row["table_name"]: row for row in rls_policy.tables(conn)}
            report = rls_policy.report(conn)
        assert "usage_event" in rows, "usage_event was not classified at all"
        assert rows["usage_event"]["kind"] == rls_policy.DIRECT, rows["usage_event"]
        assert rows["usage_event"]["rls"] is True, "usage_event has no row-level security"
        assert rows["usage_event"]["policy"] is True, "usage_event has no hoja_tenant policy"
        assert report["bound"] is True, f"/ready would answer 503: {report['gaps']}"
        assert not report["gaps"], report["gaps"]

        app = create_app(None, database_url=restricted_url, open_mode=False)
        response = TestClient(app).get("/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ready"}
    finally:
        restricted.dispose()
        with owner.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {role}; DROP ROLE IF EXISTS {role}"))
        owner.dispose()


def test_the_ledger_refuses_a_report_it_cannot_attribute(world):
    """No agent, no row: a number nobody owns is one the screen could never explain."""
    with world["engine"].begin() as conn, pytest.raises(usage_ledger.ReportError):
        usage_ledger.record_report(
            conn, organization_id=world["alpha"]["org_id"], run_id="orphan",
            agent_user_id="", usage={"input_tokens": 10}, source="finish")
    with world["engine"].begin() as conn, pytest.raises(usage_ledger.ReportError):
        usage_ledger.record_report(
            conn, organization_id=world["alpha"]["org_id"], run_id="bad-source",
            agent_user_id=world["alpha"]["user_id"], usage={"input_tokens": 10},
            source="guess")


def test_an_unreadable_report_never_fails_the_finish(world):
    """Metering is downstream of the run: a malformed count is dropped, not fatal."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    lease, identity = _granted_lease(world, agent)
    ack = _finish(world, lease, identity,
                  document={"inputTokens": "many", "outputTokens": 1})
    assert ack["accepted"] is True, ack
    assert _rows(world) == [], "a report that cannot be read must not be half-written"
    with world["engine"].connect() as conn:
        state = conn.execute(select(agent_lease.c.state).where(
            agent_lease.c.id == lease["lease_id"])).scalar()
    assert state == "finished", "the finish itself still stands"
