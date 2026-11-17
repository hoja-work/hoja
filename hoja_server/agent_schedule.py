"""The schedule check: six facts, one verdict, and the reasons as data (HOJA-602-A-7).

`agent-security/04-assignment-state-machine.md` §5 enumerates all 64 combinations of
six facts; exactly one runs immediately and 63 queue, each with a reason list in a
fixed order (``capacity, budget, window, blocked, approval, lease_held``). Two
properties of that table are load-bearing and easy to lose in an implementation:

* a queued task reports **all** the failing reasons, not the first one, so a card
  can say "waiting on approval + quiet hours" instead of making a person ask twice;
* the reason is a code, not a sentence. The client groups by it; prose is written
  from the code at the edge, never stored in its place.

The facts themselves are read from Hoja as it exists today, and where Hoja has no
model for a fact the gap is named rather than papered over:

``capacity``
    Live sessions in a slot-holding state against ``agent_member.capacity``.
``budget``
    The agent's monthly credit budget (WP12's policy, else
    ``agent_member.monthly_budget_credits``) against what the usage ledger says the
    agent has actually spent this month (WP14). Null means the workspace stated no
    ceiling, which is treated as available; 0 is exhausted at once. Spend is never
    inferred from an activity count — it is summed from ``usage_event`` rows, and a
    workspace with no reports therefore has no spend rather than a guess.
``window``
    ``agent_member.working_hours`` (``[{start, end, days}]``, days 0=Monday) in the
    member's timezone, evaluated on the local wall clock so a DST boundary moves the
    window with the clock rather than shifting it by an hour.
``blocked``
    Hoja has no blocker relation table; children are the only dependency it models,
    and a task with an unfinished child is not runnable. Where a real blocker
    relation lands, this is the single place that reads it.
``approval``
    ``agent_member.require_approval``: the delegation opens an approval decision and
    waits for a human to choose "approve". An answer of "reject" is not approval and
    the session stays queued, because a rejection is a decision to not run.
``lease_held``
    Another live session already claims this task. One live lease per task is
    invariant I1; in WP06 the session row is the claim, and WP07's lease is what the
    same fact will be read from.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select

from hoja_server import agent_sessions as sessions
from hoja_server import usage_ledger
from hoja_server.db import (
    agent_member,
    agent_session,
    issue,
    issue_decision,
    new_id,
    now_iso,
    workflow_state,
)

# The fixed order the decision table reports reasons in. Reordering this changes
# what every queued card says, so it is part of the contract, not a detail.
REASON_ORDER = ("capacity", "budget", "window", "blocked", "approval", "lease_held")

APPROVE_OPTION = "approve"
REJECT_OPTION = "reject"

LOG = logging.getLogger("hoja.agent_schedule")


@dataclass(frozen=True)
class Facts:
    """The six inputs. All default to true, so a caller that knows one fact can
    state it without inventing the other five."""

    capacity_free: bool = True
    budget_remaining: bool = True
    in_window: bool = True
    dependencies_clear: bool = True
    approved: bool = True
    lease_free: bool = True


def decide(facts: Facts) -> tuple[bool, list[str]]:
    """Return (runnable, reasons). The 64-row table, as a pure function."""
    failing = (
        ("capacity", facts.capacity_free),
        ("budget", facts.budget_remaining),
        ("window", facts.in_window),
        ("blocked", facts.dependencies_clear),
        ("approval", facts.approved),
        ("lease_held", facts.lease_free),
    )
    reasons = [reason for reason, ok in failing if not ok]
    return (not reasons, reasons)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def working_window(hours_doc: str | None, tz_name: str | None,
                   at: datetime) -> bool:
    """Is ``at`` inside the member's working hours?

    No document means no restriction: the agent was configured without hours, and
    inventing a default would silently stop work a person never limited. A document
    that cannot be parsed is treated the same way, because a scheduler that stops
    running on a hand-edited typo is worse than one that runs.
    """
    if not hours_doc:
        return True
    try:
        windows = json.loads(hours_doc)
    except (TypeError, ValueError):
        return True
    if not isinstance(windows, list) or not windows:
        return True
    try:
        zone = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    local = at.astimezone(zone)
    weekday = local.weekday()
    clock = local.time()
    for window in windows:
        if not isinstance(window, dict):
            continue
        start, end = window.get("start"), window.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        days = window.get("days")
        if isinstance(days, list) and days and weekday not in [
            int(d) for d in days if isinstance(d, (int, float))
        ]:
            continue
        try:
            start_t = datetime.strptime(start, "%H:%M").time()
            end_t = datetime.strptime(end, "%H:%M").time()
        except ValueError:
            continue
        if start_t <= end_t:
            if start_t <= clock < end_t:
                return True
        elif clock >= start_t or clock < end_t:
            # An overnight window (22:00-06:00) belongs to the day it started on.
            return True
    return False


def _terminal_state_ids(conn, org_id: str) -> set[str]:
    rows = conn.execute(select(workflow_state.c.id).where(and_(
        workflow_state.c.organization_id == org_id,
        workflow_state.c.type.in_(("completed", "canceled")),
    ))).scalars().all()
    return set(rows)


def _dependencies_clear(conn, org_id: str, issue_id: str) -> bool:
    """False while any child of the task is unfinished."""
    children = conn.execute(select(issue.c.state_id).where(and_(
        issue.c.organization_id == org_id,
        issue.c.parent_id == issue_id,
        issue.c.archived_at.is_(None),
    ))).scalars().all()
    if not children:
        return True
    done = _terminal_state_ids(conn, org_id)
    return all(child in done for child in children if child)


def _approved_for(conn, org_id: str, session_row: dict) -> bool:
    decision_id = session_row.get("approval_decision_id")
    if not decision_id:
        return True
    row = conn.execute(select(issue_decision.c.status, issue_decision.c.chosen_option).where(and_(
        issue_decision.c.organization_id == org_id,
        issue_decision.c.id == decision_id,
    ))).first()
    if not row:
        return False
    return row[0] == "answered" and row[1] == APPROVE_OPTION


def _lease_free(conn, org_id: str, issue_id: str, excluding_session_id: str | None) -> bool:
    rows = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.issue_id == issue_id,
        ~agent_session.c.state.in_(tuple(sessions.TERMINAL)),
        agent_session.c.id != (excluding_session_id or ""),
    ))).mappings().all()
    # A session claims the task once it is leased, running, waiting on a human or
    # being verified — and once it is admitted, because admission is the promise of
    # a lease. A merely queued session claims nothing, which is why two delegations
    # that both wait on capacity coexist instead of fighting over the row.
    return not any(sessions.reserves_capacity(dict(r)) or dict(r)["state"] in sessions.CLAIM_STATES
                   for r in rows)


def gather(conn, org_id: str, member: dict, issue_row: dict, session_row: dict,
           at: datetime) -> Facts:
    """Read the six facts for one session of one task."""
    live = sessions.live_sessions(conn, org_id, member["user_id"])
    holding = [s for s in live
               if sessions.reserves_capacity(s) and s["id"] != session_row["id"]]
    # WP14: the budget fact is the ledger's, not a configuration value. A member whose
    # budget has been spent this month is not runnable, however the member row was
    # configured, and the reason the card reports is the same "budget" code either way.
    budget = usage_ledger.budget_status(conn, org_id, member["user_id"])
    return Facts(
        capacity_free=len(holding) < int(member.get("capacity") or 1),
        budget_remaining=not budget["exhausted"],
        in_window=working_window(member.get("working_hours"), member.get("timezone"), at),
        dependencies_clear=_dependencies_clear(conn, org_id, issue_row["id"]),
        approved=_approved_for(conn, org_id, session_row),
        lease_free=_lease_free(conn, org_id, issue_row["id"], session_row["id"]),
    )


def open_approval(conn, org_id: str, issue_row: dict, member: dict,
                  session_row: dict, at: str) -> str:
    """Open the decision a member with ``require_approval`` waits on."""
    did = new_id()
    agent_name = member.get("user_name") or member["user_id"]
    conn.execute(issue_decision.insert().values(
        id=did, organization_id=org_id, issue_id=issue_row["id"],
        question=f"Approve delegating this task to the agent {agent_name}?",
        options=json.dumps([
            {"key": APPROVE_OPTION, "label": "Approve", "description": "The agent may start."},
            {"key": REJECT_OPTION, "label": "Reject", "description": "The agent does not start."},
        ]),
        context="The agent requires approval before it runs delegated work.",
        status="open", asked_by_id=None, asked_by_agent=member["user_id"],
        answer=None, chosen_option=None, answered_by_id=None, answered_at=None,
        canceled_at=None, created_at=at, updated_at=at,
    ))
    conn.execute(agent_session.update().where(agent_session.c.id == session_row["id"]).values(
        approval_decision_id=did, updated_at=at))
    session_row["approval_decision_id"] = did
    return did


def load_member(conn, org_id: str, agent_user_id: str) -> dict | None:
    row = conn.execute(select(agent_member).where(and_(
        agent_member.c.organization_id == org_id,
        agent_member.c.user_id == agent_user_id,
        agent_member.c.archived_at.is_(None),
    ))).mappings().first()
    return dict(row) if row else None


def delegate(conn, org_id: str, issue_row: dict, agent_user_id: str,
             creator_id: str | None, *, at: str | None = None) -> dict:
    """Delegation: open a session, run the schedule check, acknowledge, queue or admit.

    The acknowledgement is written in the same transaction as the delegation, so a
    client that creates the delegation can never observe a session with no first
    activity — the state that Linear renders as an unresponsive agent.
    """
    at = at or now_iso()
    member = load_member(conn, org_id, agent_user_id)
    if member is None:
        raise ValueError("delegate is not an active agent member of this workspace")
    session_row = sessions.open_session(conn, org_id, issue_row["id"], agent_user_id,
                                        creator_id, at=at)
    if member.get("require_approval"):
        open_approval(conn, org_id, issue_row, member, session_row, at)
    facts = gather(conn, org_id, member, issue_row, session_row, _parse_iso(at))
    _runnable, reasons = decide(facts)
    session_row = sessions.apply_schedule(conn, session_row, reasons, at=at)
    session_row = sessions.acknowledge(conn, session_row, at=at)
    return session_row


def reevaluate(conn, session_row: dict, *, at: str | None = None) -> dict:
    """Re-run the check for a queued session. The timer is what gives a queued
    session a future: nothing else will look at it again."""
    at = at or now_iso()
    if session_row["state"] != sessions.QUEUED:
        if session_row["state"] in (sessions.DELEGATED,):
            return session_row
        return sessions.keepalive(conn, session_row, at=at)
    member = load_member(conn, session_row["organization_id"], session_row["agent_user_id"])
    issue_row = conn.execute(select(issue).where(and_(
        issue.c.organization_id == session_row["organization_id"],
        issue.c.id == session_row["issue_id"],
    ))).mappings().first()
    if member is None or issue_row is None:
        return session_row
    facts = gather(conn, session_row["organization_id"], member, dict(issue_row),
                   session_row, _parse_iso(at))
    _runnable, reasons = decide(facts)
    if reasons == sessions.parse_reasons(session_row.get("queue_reason")):
        # Same verdict as before: this is the window where a waiting session must
        # prove it is alive without claiming progress.
        return sessions.keepalive(conn, session_row, at=at)
    changes = {
        "queue_reason": json.dumps(reasons) if reasons else None,
        "admitted_at": None if reasons else at,
        "status": sessions.status_for(sessions.QUEUED),
        "updated_at": at,
    }
    conn.execute(agent_session.update().where(
        agent_session.c.id == session_row["id"]).values(**changes))
    session_row.update(changes)
    return session_row


def enqueue_admitted(engine, session_row: dict, *, harness: str | None = None) -> dict:
    """Hand an admitted session to WP07's queue, after the delegating commit.

    The lease is WP07's: ``runner_api.enqueue_task`` owns the fence, the run id and
    the prompt, and the runner long-polls that row. This function adds nothing
    parallel — it records which lease the session is waiting on, so the two rows
    stay joinable.

    The harness is no longer a constant (HOJA-602-A-22). It comes from the
    delegation when one names it, else from the agent's policy, else ``pi`` -
    ``agent_policy.resolve_harness`` holds the chain and every rule about it. A
    delegated session therefore queues for the harness the agent's policy chose, and
    the lease is only granted to a runner that advertised that name
    (``runner_api._grantable``); a runner that did not leaves the lease ``queued``
    and says so in the ``refused`` list of its poll.

    Called *after* the writer's transaction, deliberately: ``enqueue_task`` opens
    its own connection, and a second connection taken inside a SQLite write
    transaction waits on the lock that transaction already holds.
    """
    from hoja_server import runner_api

    if not session_row or session_row["state"] != sessions.QUEUED:
        return session_row
    if not session_row.get("admitted_at") or session_row.get("lease_id"):
        return session_row
    choice = _harness_for(engine, session_row, requested=harness)
    lease = runner_api.enqueue_task(
        engine,
        organization_id=session_row["organization_id"],
        task_id=session_row["issue_id"],
        # The harness the runner will execute this with, and the rung that named it
        # (delegation | policy | allowlist | default) for the log line below.
        harness=choice.harness,
    )
    LOG.info("session %s queued for harness %s (%s)", session_row["id"], choice.harness,
             choice.source)
    with engine.begin() as conn:
        conn.execute(agent_session.update().where(
            agent_session.c.id == session_row["id"]).values(
            lease_id=lease["id"], updated_at=now_iso()))
    session_row["lease_id"] = lease["id"]
    session_row["harness"] = choice.harness
    return session_row


def _harness_for(engine, session_row: dict, *, requested: str | None = None):
    """Resolve this session's harness from the agent's policy, in its own connection.

    A missing policy is not a refusal here. The schedule check and WP12's lease gate
    are what decide whether an agent may run at all, and both of them already refuse
    an agent with no policy; this function only answers *which* harness, so an absent
    policy simply leaves the chain at its last rung (``pi``) and the refusal that
    matters keeps happening where it always did.
    """
    from hoja_server import agent_policy

    org_id = session_row["organization_id"]
    with engine.connect() as conn:
        view = agent_policy.view_for_member
        row = agent_policy.member_row_for_user(conn, org_id, session_row["agent_user_id"])
        policy_view = view(conn, org_id, row["id"]) if row else None
    return agent_policy.resolve_harness(policy_view, requested=requested)
