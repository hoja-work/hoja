"""Agent sessions: one run of an agent against one task, and its transcript (HOJA-602-A-7).

Linear separates three ideas that are easy to conflate, and this module keeps them
separate: the **delegate** (who the agent works for, on the issue), the **session**
(one run, with a status), and the **activities** (the visible conversation). A
session carries two labels — ``state``, our ten-state assignment machine, and
``status``, Linear's seven-value ``AgentSessionStatus`` — and the status is always
derived from the state. The direction matters: ``complete`` is a *session* status,
while ``in_review`` and ``done`` are issue workflow states, so a session is not the
place to infer what happened to the task.

Two rules from Linear's agent documentation shape the code below, because a
queueing design violates them silently rather than loudly:

* The first activity must arrive within ten seconds of the session being created,
  or the agent is shown as unresponsive. **Queueing is therefore itself an act
  that must emit an activity**: ``queued`` is not "doing nothing", it is "has
  acknowledged and is deliberately waiting", and it says so.
* After that first activity a session stays usable for thirty minutes before it
  is ``stale``, and stale is documented as recoverable. A session that is merely
  waiting on capacity must not look broken, so the scheduler re-emits one
  ephemeral ``thought`` per window instead of letting it age out.

Cancel is authoritative: a human stop moves the session to ``stopping`` and then
``cancelled``, and no later event can revive it. Cancel does not need to race a
runner — it only needs to make the runner's later work unattributable, which is
why the transition set has no edge out of a terminal state.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, desc, or_, select

from hoja_server import audit
from hoja_server.db import agent_activity, agent_lease, agent_session, new_id, now_iso

query = QueryType()
mutation = MutationType()
agent_session_type = ObjectType("AgentSession")
agent_activity_type = ObjectType("AgentActivity")

# --- the ten states (agent-security/04-assignment-state-machine.md §4.1) -------
DELEGATED = "delegated"
QUEUED = "queued"
LEASED = "leased"
RUNNING = "running"
AWAITING_INPUT = "awaiting_input"
VERIFYING = "verifying"
IN_REVIEW = "in_review"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

STATES = (DELEGATED, QUEUED, LEASED, RUNNING, AWAITING_INPUT, VERIFYING,
          IN_REVIEW, DONE, FAILED, CANCELLED)
TERMINAL = frozenset({DONE, FAILED, CANCELLED})
# States that hold one of the agent's concurrency slots. ``awaiting_input`` is
# deliberately absent: an elicitation releases the slot and drops the lease,
# otherwise an agent waiting on a human would block its own capacity.
SLOT_STATES = frozenset({LEASED, RUNNING})
# States in which the task has a live claim on it, so a second session for the
# same task is ``lease_held`` rather than admitted twice.
CLAIM_STATES = frozenset({LEASED, RUNNING, AWAITING_INPUT, VERIFYING})
# The two states a timer must never settle. ``verifying`` holds a finished claim
# nobody has judged yet, so settling it deletes the claim before it can be
# verified — that is the recorded failure (S12/S15) this file exists to close.
# ``in_review`` is a person's decision; a clock may not take it either.
SETTLE_EXEMPT = frozenset({VERIFYING, IN_REVIEW})


def under_verification(row: dict) -> bool:
    """True while a claim is waiting on a verifier. Names the state, not the timer."""
    return row["state"] == VERIFYING


def may_be_settled(row: dict) -> bool:
    """May an idle sweep cancel, expire or mark this session stale?"""
    return row["state"] not in SETTLE_EXEMPT


def holds_slot(row: dict) -> bool:
    """Does this session actually occupy one of its agent's concurrency slots?

    Only a leased or running session does. The slot is released when the runner
    reports a finish (the row moves to ``verifying``), because nothing is executing
    any more — I5a says no ``in_review`` or ``done`` state may hold a lease.
    """
    return row["state"] in SLOT_STATES


def reserves_capacity(row: dict) -> bool:
    """Should the capacity fact in the schedule check count this session?

    Yes once it holds a slot, and also once it is *admitted*: an admitted session has
    passed the check and is waiting for the lease service to hand it to a runner.
    Without that, a burst of delegations all pass the same check before any of them
    reaches ``leased`` and the bound the check promised is never enforced.
    """
    return holds_slot(row) or (row["state"] in (DELEGATED, QUEUED) and bool(row.get("admitted_at")))

# Linear's AgentSessionStatus, and the mapping from our state (§4.3).
STATUSES = ("pending", "active", "awaitingInput", "complete", "error", "stale", "stopping")
STATUS_BY_STATE = {
    DELEGATED: "pending",
    QUEUED: "pending",
    LEASED: "active",
    RUNNING: "active",
    AWAITING_INPUT: "awaitingInput",
    VERIFYING: "active",
    IN_REVIEW: "complete",
    DONE: "complete",
    FAILED: "error",
    CANCELLED: "complete",
}

# Linear's AgentActivityType. ``prompt`` is user-only: an agent may not author it.
ACTIVITY_KINDS = ("thought", "action", "elicitation", "response", "error")
USER_ONLY_ACTIVITY_KINDS = frozenset({"prompt"})
SIGNALS = ("auth", "continue", "select", "stop")

ACK_DEADLINE_SECONDS = 10
STALE_AFTER_SECONDS = 30 * 60

# A page of sessions is bounded: the run queue walks the queue, it never asks for
# every run the workspace ever made (the same rule ``audit.MAX_PAGE`` states).
MAX_PAGE = 200

# §4.2. ``heartbeat`` is absent: it renews a lease and leaves the state alone.
TRANSITIONS = {
    (DELEGATED, "schedule"): QUEUED,
    (QUEUED, "lease"): LEASED,
    (LEASED, "start"): RUNNING,
    (RUNNING, "finish"): VERIFYING,
    (RUNNING, "await_input"): AWAITING_INPUT,
    (AWAITING_INPUT, "prompt"): QUEUED,
    (VERIFYING, "verify_pass"): IN_REVIEW,
    (VERIFYING, "verify_fail"): FAILED,
    (IN_REVIEW, "accept"): DONE,
    # A crashed runner leaves the row claiming the lease; only the timer moves it.
    (LEASED, "tick"): QUEUED,
    (RUNNING, "tick"): QUEUED,
    (AWAITING_INPUT, "tick"): QUEUED,
    # A question answered while the run that asked it is still live goes back to that
    # run; the answer travels on the lease's heartbeat. ``prompt`` (to queued) remains
    # for a session whose run is gone.
    (AWAITING_INPUT, "answer"): RUNNING,
    # A failed finish is the runner saying the attempt produced nothing (WP10).
    # It is not a verdict on a claim: there is no claim to verify, so it fails
    # where it stands and the schedule check decides whether to retry.
    (LEASED, "fail"): FAILED,
    (RUNNING, "fail"): FAILED,
    (AWAITING_INPUT, "fail"): FAILED,
}
# Events that are legal but leave the state where it is.
STEADY_EVENTS = {
    (LEASED, "heartbeat"),
    (RUNNING, "heartbeat"),
    (RUNNING, "open_pr"),
}


class RefusedTransitionError(GraphQLError):
    """A transition the machine does not allow, carrying the reason as data."""


def _refuse(session, event, why):
    raise RefusedTransitionError(
        f"{session['state']} cannot take {event}: {why}",
        extensions={"code": "CONFLICT", "field": "state", "reason": why},
    )


def status_for(state: str, *, stale: bool = False) -> str:
    """Linear's status for one of our states; ``stale`` only qualifies a pending session."""
    if state in (DELEGATED, QUEUED) and stale:
        return "stale"
    return STATUS_BY_STATE[state]


def parse_reasons(value) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [r for r in parsed if isinstance(r, str)] if isinstance(parsed, list) else []


def open_session(conn, org_id: str, issue_id: str, agent_user_id: str,
                 creator_id: str | None, *, trigger: str = "delegation",
                 at: str | None = None) -> dict:
    """Create the session a delegation starts. It begins in ``delegated``: nothing has
    been checked yet, and saying otherwise would claim a decision nobody made."""
    at = at or now_iso()
    sid = new_id()
    conn.execute(agent_session.insert().values(
        id=sid, organization_id=org_id, issue_id=issue_id,
        agent_user_id=agent_user_id, creator_id=creator_id,
        status=status_for(DELEGATED), state=DELEGATED, queue_reason=None,
        trigger=trigger, admitted_at=None, acked_at=None, ended_at=None,
        dismissed_at=None, dismissed_by=None, error_message=None,
        created_at=at, updated_at=at,
    ))
    return get_session(conn, org_id, sid)


def get_session(conn, org_id: str, session_id: str) -> dict | None:
    row = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.id == session_id,
    ))).mappings().first()
    return dict(row) if row else None


def latest_for_issue(conn, org_id: str, issue_id: str) -> dict | None:
    row = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.issue_id == issue_id,
    )).order_by(desc(agent_session.c.created_at)).limit(1)).mappings().first()
    return dict(row) if row else None


def live_sessions(conn, org_id: str, agent_user_id: str) -> list[dict]:
    """Sessions that are not terminal for one agent — the input to capacity."""
    rows = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.agent_user_id == agent_user_id,
        ~agent_session.c.state.in_(tuple(TERMINAL)),
    ))).mappings().all()
    return [dict(r) for r in rows]


def _save(conn, session: dict, *, at: str, **changes) -> dict:
    changes.setdefault("updated_at", at)
    conn.execute(agent_session.update().where(agent_session.c.id == session["id"]).values(**changes))
    session.update(changes)
    return session


def append_activity(conn, org_id: str, session_id: str, kind: str, *, body: str | None = None,
                    payload: dict | None = None, signal: str | None = None,
                    author_kind: str = "agent", author_id: str | None = None,
                    ephemeral: bool = False, at: str | None = None) -> dict:
    """Append one activity. Append-only by construction: there is no update path."""
    if kind not in ACTIVITY_KINDS and kind not in USER_ONLY_ACTIVITY_KINDS:
        raise ValueError(f"unknown activity kind {kind!r}")
    if kind in USER_ONLY_ACTIVITY_KINDS and author_kind != "user":
        # Linear: "An agent cannot generate a prompt type activity."
        raise ValueError("prompt activities are authored by a human, never by an agent")
    if signal is not None and signal not in SIGNALS:
        raise ValueError(f"unknown signal {signal!r}")
    # WP12 (FR-035): this is the single writer of the transcript, so it is the one
    # place a secret value can be kept out of it. An agent that echoes its
    # environment into a thought or an action body is scrubbed here, before the row
    # exists - a transcript that had to be cleaned afterwards would already have
    # stored the value.
    from hoja_server import agent_policy

    body, payload = agent_policy.scrub_activity(conn, org_id, session_id, body, payload)
    at = at or now_iso()
    aid = new_id()
    conn.execute(agent_activity.insert().values(
        id=aid, organization_id=org_id, session_id=session_id, kind=kind,
        author_kind=author_kind, author_id=author_id, body=body,
        payload=json.dumps(payload) if payload is not None else None,
        # A real boolean, not 1/0: SQLite accepts either into a BOOLEAN column,
        # PostgreSQL rejects the integer with "operator does not exist: boolean = integer".
        signal=signal, ephemeral=bool(ephemeral), created_at=at,
    ))
    return {"id": aid, "kind": kind, "authorKind": author_kind, "authorId": author_id,
            "body": body, "payload": payload, "signal": signal,
            "ephemeral": ephemeral, "createdAt": at}


def activities(conn, org_id: str, session_id: str, first: int = 50) -> list[dict]:
    rows = conn.execute(select(agent_activity).where(and_(
        agent_activity.c.organization_id == org_id,
        agent_activity.c.session_id == session_id,
        # Ephemeral thoughts keep a waiting session observably alive; they are not
        # part of the durable transcript as Linear defines it.
        agent_activity.c.ephemeral.is_(False),
    )).order_by(agent_activity.c.created_at).limit(max(1, min(first, 200)))).mappings().all()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item["payload"]) if item["payload"] else None
        except (TypeError, ValueError):
            item["payload"] = None
        out.append(item)
    return out


def acknowledge(conn, session: dict, *, at: str | None = None) -> dict:
    """Post the first activity, which is what stops the session looking unresponsive.

    Idempotent: a retry finds ``acked_at`` set and posts nothing, so a client that
    re-sends a delegation cannot make the transcript claim two starts. The elapsed
    time is recorded rather than asserted, so a late ack is visible in the payload
    instead of being quietly fixed up.
    """
    if session.get("acked_at"):
        return session
    at = at or now_iso()
    reasons = parse_reasons(session.get("queue_reason"))
    elapsed = _seconds_between(session["created_at"], at)
    if reasons:
        append_activity(
            conn, session["organization_id"], session["id"], "thought",
            body="Waiting: " + ", ".join(reasons) + ".",
            payload={"reasons": reasons, "elapsed_seconds": elapsed,
                     "ack_deadline_seconds": ACK_DEADLINE_SECONDS},
            at=at,
        )
    else:
        append_activity(
            conn, session["organization_id"], session["id"], "action",
            body="Starting now.",
            payload={"elapsed_seconds": elapsed,
                     "ack_deadline_seconds": ACK_DEADLINE_SECONDS},
            at=at,
        )
    return _save(conn, session, at=at, acked_at=at)


def _seconds_between(start_iso: str, end_iso: str) -> float:
    from datetime import datetime

    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    return round((end - start).total_seconds(), 3)


def ack_deadline_missed(session: dict) -> bool:
    """True when the first activity arrived later than the ten-second rule allows."""
    if not session.get("acked_at"):
        return True
    return _seconds_between(session["created_at"], session["acked_at"]) > ACK_DEADLINE_SECONDS


def apply_schedule(conn, session: dict, reasons: list[str], *, at: str | None = None) -> dict:
    """Move ``delegated`` to ``queued`` and record the verdict.

    Every delegation passes through ``queued``; the 64-row decision table is about
    the *reason list*, and exactly one of its rows is empty. An empty list means the
    session is admitted — it has nothing left to wait for — and the lease service
    (WP07) is what turns an admitted session into ``leased``. Recording an empty
    verdict as ``leased`` here would claim a lease nobody holds.
    """
    at = at or now_iso()
    if session["state"] != DELEGATED:
        _refuse(session, "schedule", "the schedule check runs once, from delegated")
    changes = {
        "state": QUEUED,
        "status": status_for(QUEUED),
        "queue_reason": json.dumps(reasons) if reasons else None,
        "admitted_at": None if reasons else at,
    }
    return _save(conn, session, at=at, **changes)


def transition(conn, session: dict, event: str, *, at: str | None = None,
               lease_live: bool = True, actor_id: str | None = None,
               reason: str | None = None) -> dict:
    """Apply one event from §4.2 and return the updated session.

    ``lease_live`` is the fencing check WP07 owns; this module takes it as an input
    rather than inventing lease columns, so the guard is testable now and real
    later. Cancel is handled here, not by the table, because it is allowed from any
    non-terminal state — that asymmetry *is* "cancel always wins".
    """
    at = at or now_iso()
    state = session["state"]
    if state in TERMINAL:
        # I3b: nothing leaves a terminal state, so a late finish cannot revive a
        # cancelled task.
        _refuse(session, event, f"{state} is terminal; no event reopens it")
    if event == "cancel":
        return _cancel(conn, session, at=at, actor_id=actor_id, reason=reason)
    if event == "finish" and not lease_live:
        # I4: a finish from an expired or superseded lease has no edge at all.
        _refuse(session, event, "the lease is not live, so the finish is unattributable")
    if event in ("start", "heartbeat", "finish") and not lease_live:
        _refuse(session, event, "the lease is not live")
    if (state, event) in STEADY_EVENTS:
        return _save(conn, session, at=at)
    target = TRANSITIONS.get((state, event))
    if target is None:
        _refuse(session, event, f"{event} is not an event of {state}")
    if event == "lease" and not session.get("admitted_at"):
        # The only admitted row in the decision table runs; a queued one waits.
        _refuse(session, event, "the schedule check has not admitted this session")
    changes = {"state": target, "status": status_for(target)}
    if target in TERMINAL:
        changes["ended_at"] = at
    if target != QUEUED:
        # The promise of a lease is spent once there is a lease; keeping it would
        # make a verifying or finished session look like it still reserves capacity.
        changes["admitted_at"] = None
    if target == RUNNING and event == "start":
        # Only the first start: going back to running after an answer is not a new start.
        append_activity(conn, session["organization_id"], session["id"], "action",
                        body="Started.", at=at)
    if target == VERIFYING:
        append_activity(conn, session["organization_id"], session["id"], "action",
                        body="Verifying.", at=at)
    if target == IN_REVIEW:
        append_activity(conn, session["organization_id"], session["id"], "response",
                        body="Work is ready for review.", at=at)
    if target == FAILED:
        append_activity(conn, session["organization_id"], session["id"], "error",
                        body=reason or "Verification failed.", at=at)
        changes["error_message"] = reason or "Verification failed."
    return _save(conn, session, at=at, **changes)


CLAIM_IMMUTABLE = "a claim is submitted once and never rewritten"


def submit_claim(conn, session: dict, claim: dict, *, worker_model: str | None = None,
                 at: str | None = None) -> dict:
    """Record the runner's claim and move the session to ``verifying`` (WP10).

    This is the only function the finish route may use for a completed attempt, and
    it structurally cannot produce ``done``: the machine's edge out of ``running``
    for a finish is ``verifying``, and ``done`` is reachable only through
    ``verify_pass`` (a verified claim) and then ``accept`` (a person). WP07's
    finish route already refuses to touch the task; this closes the session half.

    The claim is written once. A second finish under a *different* idempotency key
    is refused rather than allowed to edit the evidence a verifier will read —
    otherwise the last writer would decide what was claimed.
    """
    at = at or now_iso()
    if session.get("claim"):
        _refuse(session, "finish", CLAIM_IMMUTABLE)
    if session["state"] in TERMINAL:
        _refuse(session, "finish", f"{session['state']} is terminal; no claim is taken")
    # Catch the machine up rather than jumping it: a granted lease and a started
    # run are real events, and each one leaves its own line in the transcript. A
    # skipped step here would make the transcript claim a start that never got a
    # row, which is exactly the kind of quiet gap this work package is about.
    if session["state"] == QUEUED:
        session = transition(conn, session, "lease", at=at)
    if session["state"] == LEASED:
        session = transition(conn, session, "start", at=at)
    if session["state"] != RUNNING:
        _refuse(session, "finish", "only a running session submits a claim")
    session = transition(conn, session, "finish", at=at)
    if session["state"] != VERIFYING:  # pragma: no cover - the table says verifying
        raise AssertionError(f"a finish left the session {session['state']}, not verifying")
    return _save(conn, session, at=at,
                 claim=json.dumps(claim, sort_keys=True, default=str),
                 claim_at=at, worker_model=worker_model)


def finish_session(conn, session: dict, finish_state: str, claim: dict | None = None, *,
                   worker_model: str | None = None, reason: str | None = None,
                   at: str | None = None) -> dict:
    """Apply one of WP07's three finish states to the session bound to the lease.

    ``completed`` is a *claim*, so it goes to ``verifying``. ``canceled`` and
    ``failed`` are not claims and are applied directly: there is nothing to verify
    about an attempt that produced nothing.
    """
    at = at or now_iso()
    if finish_state == "completed":
        return submit_claim(conn, session, claim or {}, worker_model=worker_model, at=at)
    if finish_state == "canceled":
        return transition(conn, session, "cancel", at=at,
                          reason=reason or "the runner canceled the attempt")
    if finish_state == "failed":
        return transition(conn, session, "fail", at=at,
                          reason=reason or "the attempt failed")
    raise ValueError(f"unknown finish state {finish_state!r}")


def _cancel(conn, session: dict, *, at: str, actor_id: str | None, reason: str | None) -> dict:
    """Stopping, then cancelled. The transient status exists so a client can show
    "stopping" while a runner settles; the state is cancelled immediately, because
    the row — not the runner — is what the rest of the system reads."""
    conn.execute(agent_session.update().where(agent_session.c.id == session["id"]).values(
        status="stopping", updated_at=at))
    session["status"] = "stopping"
    append_activity(conn, session["organization_id"], session["id"], "response",
                    body="Cancelled" + (f": {reason}" if reason else "") +
                         "; no completion implied.",
                    payload={"cancelled": True}, signal="stop",
                    author_kind="user" if actor_id else "agent", author_id=actor_id, at=at)
    changes = {
        "state": CANCELLED, "status": status_for(CANCELLED),
        "ended_at": at, "dismissed_at": at, "dismissed_by": actor_id,
        "admitted_at": None,
    }
    return _save(conn, session, at=at, **changes)


def keepalive(conn, session: dict, *, at: str | None = None,
              idle_seconds: float | None = None) -> dict:
    """Re-emit one ephemeral thought per stale window for a waiting session.

    A session that is merely waiting on capacity must not be shown as
    unresponsive, and a ``stale`` session must be recoverable, so this both
    prevents and clears ``stale`` without pretending work happened.
    """
    at = at or now_iso()
    if not may_be_settled(session):
        # Verifying and in_review are not "waiting on capacity": a keepalive would
        # tell a reader the session is alive but idle, when it is waiting on a
        # verdict or a person.
        return session
    if session["state"] not in (DELEGATED, QUEUED):
        return session
    idle = idle_seconds if idle_seconds is not None else _seconds_between(session["updated_at"], at)
    if idle < STALE_AFTER_SECONDS and session["status"] != "stale":
        return session
    reasons = parse_reasons(session.get("queue_reason"))
    append_activity(conn, session["organization_id"], session["id"], "thought",
                    body="Still waiting" + (f": {', '.join(reasons)}." if reasons else "."),
                    payload={"reasons": reasons}, ephemeral=True, at=at)
    return _save(conn, session, at=at, status=status_for(session["state"]))


def mark_stale(session: dict, *, idle_seconds: float) -> str:
    """The status a waiting session has after a full window with no activity."""
    if (may_be_settled(session) and session["state"] in (DELEGATED, QUEUED)
            and idle_seconds >= STALE_AFTER_SECONDS):
        return "stale"
    return session["status"]


def lease_harnesses(conn, org_id: str, lease_ids: Iterable[str | None]) -> dict[str, str]:
    """The harness each of these leases names, in one query (HOJA-602-A-22).

    A session's ``queue_reason`` says *why* it is queued; it cannot say *which runner*
    it is queued for, and after A-22 that is a different question - a session waiting
    on a codex runner and a session waiting on capacity are both "queued" and only
    the lease knows which is which. Batched so a page of 50 sessions costs one query
    rather than fifty, the same way the transcript is a separate selection.
    """
    wanted = sorted({str(i) for i in lease_ids if i})
    if not wanted:
        return {}
    rows = conn.execute(select(agent_lease.c.id, agent_lease.c.harness).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.id.in_(wanted)))).all()
    return {str(lease_id): harness for lease_id, harness in rows}


def serialize(conn, org_id: str, row: dict, *,
              harness: str | None = None) -> dict:
    """One session in its GraphQL shape."""
    reasons = parse_reasons(row.get("queue_reason"))
    return {
        "id": row["id"],
        "issueId": row["issue_id"],
        "agentUserId": row["agent_user_id"],
        "creatorId": row["creator_id"],
        "status": row["status"],
        "state": row["state"],
        "queueReason": reasons,
        "admitted": bool(row.get("admitted_at")),
        "leaseId": row.get("lease_id"),
        # Which harness the lease this session waits on names. None until the lease
        # exists, which is the honest answer for a session the schedule check queued
        # and which has not been handed to the runner queue yet.
        "harness": harness if harness is not None else (
            lease_harnesses(conn, org_id, [row.get("lease_id")]).get(
                str(row.get("lease_id") or ""))),
        "trigger": row["trigger"],
        "ackedAt": row.get("acked_at"),
        "activatedAt": row.get("admitted_at"),
        "endedAt": row.get("ended_at"),
        # WP10: what the runner claimed and who signed it. The claim is served as
        # the object the runner sent, so a reader never has to parse a string.
        "claim": parse_claim(row),
        "claimAt": row.get("claim_at"),
        "workerModel": row.get("worker_model"),
        "verdict": row.get("verdict"),
        "verifiedAt": row.get("verdict_at"),
        "verifierId": row.get("verifier_id"),
        "verifierModel": row.get("verifier_model"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "_row": row,
    }


def parse_claim(row: dict) -> dict | None:
    """The stored claim as a dict, or None when nobody claimed anything."""
    raw = row.get("claim")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def cursor_of(row: dict) -> str:
    """The sort key of one row, as the opaque cursor a client hands back.

    ``created_at|id`` and not the timestamp alone: two delegations in the same
    millisecond are ordinary (a burst, a test fixture), and a cursor that only knows
    the timestamp either repeats a row or skips one at the page boundary.
    """
    return f"{row['created_at']}|{row['id']}"


def _split_cursor(cursor: str) -> tuple[str, str]:
    created_at, _, row_id = str(cursor).partition("|")
    if not created_at or not row_id:
        raise GraphQLError("The cursor does not name a session in this workspace.",
                           extensions={"code": "BAD_USER_INPUT", "field": "after"})
    return created_at, row_id


def read_page(conn, org_id: str, *, first: int = 50, after: str | None = None,
              status: str | None = None, agent_id: str | None = None) -> dict:
    """One bounded page of a workspace's sessions, newest first.

    The ``organization_id`` filter is explicit *and* enforced by row-level security,
    the same belt-and-braces ``audit.read_events`` uses: under the policies the
    predicate is applied twice rather than trusted once.
    """
    first = max(1, min(int(first or 50), MAX_PAGE))
    stmt = select(agent_session).where(agent_session.c.organization_id == org_id)
    if status:
        stmt = stmt.where(agent_session.c.status == status)
    if agent_id:
        stmt = stmt.where(agent_session.c.agent_user_id == agent_id)
    if after:
        created_at, row_id = _split_cursor(after)
        stmt = stmt.where(or_(
            agent_session.c.created_at < created_at,
            and_(agent_session.c.created_at == created_at, agent_session.c.id < row_id),
        ))
    rows = [dict(row) for row in conn.execute(
        stmt.order_by(desc(agent_session.c.created_at), desc(agent_session.c.id))
        .limit(first + 1)).mappings().all()]
    has_next = len(rows) > first
    rows = rows[:first]
    harnesses = lease_harnesses(conn, org_id, [row.get("lease_id") for row in rows])
    return {
        "nodes": [serialize(conn, org_id, row,
                            harness=harnesses.get(str(row.get("lease_id") or "")))
                  for row in rows],
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": bool(after),
            "startCursor": cursor_of(rows[0]) if rows else None,
            "endCursor": cursor_of(rows[-1]) if rows else None,
        },
    }


SDL = gql(
    """
    "Linear's AgentSessionStatus, so a client written against Linear reads the same values."
    enum AgentSessionStatus { pending active awaitingInput complete error stale stopping }

    "Linear's AgentActivityType. prompt is user-only: an agent may not author it."
    enum AgentActivityType { thought action elicitation response error prompt }

    enum AgentSessionState {
      delegated queued leased running awaiting_input verifying in_review done failed cancelled
    }

    type AgentActivity {
      id: ID!
      kind: AgentActivityType!
      authorKind: String!
      authorId: String
      body: String
      payload: JSON
      signal: String
      createdAt: DateTime!
    }

    type AgentSession {
      id: ID!
      issueId: ID!
      "The task this run belongs to: a queue row shows the issue's own identifier, title and priority."
      issue: Issue
      agentUserId: ID!
      agent: User
      creatorId: ID
      "Linear's seven values, derived from state."
      status: AgentSessionStatus!
      "Our ten-state assignment machine."
      state: AgentSessionState!
      "Why the session is queued, in the fixed order of the schedule check. Empty once admitted."
      queueReason: [String!]!
      admitted: Boolean!
      "The WP07 lease this session is waiting on, or null while queued."
      leaseId: ID
      "The harness profile that lease names: which runner this run needs. Null until a lease exists."
      harness: String
      trigger: String!
      ackedAt: DateTime
      activatedAt: DateTime
      endedAt: DateTime
      "What the runner claimed at finish: the summary, the evidence and the references it points at. Immutable once submitted."
      claim: JSON
      claimAt: DateTime
      "The model that produced the work, as the runner reported it."
      workerModel: String
      "met, not_met or uncertain — null until a separate verifier signs. Never set by the runner."
      verdict: String
      verifiedAt: DateTime
      "The principal that recorded the verdict; never the session's own agent."
      verifierId: ID
      verifierModel: String
      createdAt: DateTime!
      updatedAt: DateTime!
      activities(first: Int): [AgentActivity!]!
    }

    type AgentSessionPayload { success: Boolean! agentSession: AgentSession }

    type AgentSessionConnection { nodes: [AgentSession!]! pageInfo: PageInfo! }

    extend type Issue {
      "The most recent agent session on this issue, or null when it was never delegated."
      agentSession: AgentSession
      "Every session on this issue, newest first."
      agentSessions(first: Int): [AgentSession!]!
    }

    # Extended here rather than in schema_tasks.graphql, which is generated from
    # schema.graphql and cannot reference a type this module owns.
    extend type Task {
      "The most recent agent session on this task, or null when it was never delegated."
      agentSession: AgentSession
      "Every session on this task, newest first."
      agentSessions(first: Int): [AgentSession!]!
    }

    extend type Query {
      agentSession(id: String!): AgentSession
      "Sessions for one issue, newest first."
      agentSessions(issueId: String!, first: Int): [AgentSession!]!
      "Every session in the workspace, newest first, admin only, paged. `after` is the connection's `endCursor`; `agentId` is the agent's `user.id` (what `AgentSession.agentUserId` reports), not the `AgentMember.id`."
      workspaceAgentSessions(first: Int = 50, after: String, status: AgentSessionStatus,
                             agentId: String): AgentSessionConnection!
    }

    extend type Mutation {
      "A human stop. Cancel always wins: no later event revives the session."
      agentSessionStop(id: String!, reason: String): AgentSessionPayload!
      "A human answer to a session that is waiting for one. With a live run (the agent asked through its harness) the answer goes back to that run on its next heartbeat; otherwise the work is queued again. `response` answers the elicitation's questions: {answers: [{header, selected: [label], customAnswer}]} or {cancelled: true}."
      agentSessionPrompt(id: String!, body: String!, response: JSON): AgentSessionPayload!
      "A separate verifier's verdict on a finished claim. Only met moves the task to In Review; not_met fails the session and uncertain leaves it awaiting evidence. A verifier sharing the worker's principal or model is refused, and no check reads CI status."
      agentSessionVerify(id: String!, verdict: String!, rationale: String,
                         model: String, liveRevision: String): AgentSessionPayload!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _bad(field, message):
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _require_session(conn, org_id, session_id):
    row = get_session(conn, org_id, session_id)
    if not row:
        _bad("id", "No such agent session in this workspace.")
    return row


@query.field("agentSession")
def r_agent_session(_obj, info, id):
    store, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        row = get_session(conn, org, id)
        return serialize(conn, org, row) if row else None


@query.field("agentSessions")
def r_agent_sessions(_obj, info, issueId, first=None):  # noqa: N803 - GraphQL argument name
    store, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        rows = conn.execute(select(agent_session).where(and_(
            agent_session.c.organization_id == org,
            agent_session.c.issue_id == issueId,
        )).order_by(desc(agent_session.c.created_at)).limit(max(1, min(first or 20, 100)))).mappings().all()
        return [serialize(conn, org, dict(r)) for r in rows]


@query.field("workspaceAgentSessions")
def r_workspace_agent_sessions(_obj, info, first=None, after=None, status=None,
                               agentId=None):  # noqa: N803 - GraphQL argument name
    """The workspace's runs, newest first, admin only, paged.

    Admin-gated exactly as ``auditEvents`` is, and for the same reason: every agent's
    run queue is a workspace-wide view, and a non-admin is *refused* rather than
    handed an empty connection, because an empty connection reads as "nothing is
    running".
    """
    store, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can read every agent session.",
                           extensions={"code": "FORBIDDEN"})
    with store.engine.connect() as conn:
        return read_page(conn, org, first=first or 50, after=after,
                         status=status, agent_id=agentId)


@mutation.field("agentSessionStop")
def m_agent_session_stop(_obj, info, id, reason=None):
    store, org, uid = _ctx(info)
    with store.engine.begin() as conn:
        row = _require_session(conn, org, id)
        try:
            row = transition(conn, row, "cancel", actor_id=uid, reason=reason)
        except RefusedTransitionError as exc:
            raise GraphQLError(str(exc), extensions=exc.extensions) from exc
        payload = serialize(conn, org, row)
        issue_id = row["issue_id"]
    # After the commit, like every other audited action: an event written inside the
    # transaction would vanish with a rollback, and one written first would claim a
    # stop that never happened. A stop the machine refuses never reaches this line,
    # so a rejected stop writes no event.
    audit.record_request(store, info, user_id=uid, action="agent.session.stop",
                         target_kind="agent_session", target_id=id,
                         detail={"issueId": issue_id, "reason": reason})
    return {"success": True, "agentSession": payload}


@mutation.field("agentSessionPrompt")
def m_agent_session_prompt(_obj, info, id, body, response=None):
    store, org, uid = _ctx(info)
    with store.engine.begin() as conn:
        row = _require_session(conn, org, id)
        if row["state"] != AWAITING_INPUT:
            _bad("id", "A prompt answers a session that is awaiting input.")
        ask = _open_ask(conn, org, row["id"])
        if ask is not None and _lease_is_live(conn, org, row.get("lease_id")):
            # The run that asked is still holding its lease and waiting on the answer:
            # hand it back to that run (the heartbeat carries it) instead of queueing a
            # new attempt that would start the work over.
            answer = _clean_response(response, body)
            append_activity(conn, org, row["id"], "prompt", body=body, author_kind="user",
                            author_id=uid, signal="continue",
                            payload={"ask_id": ask, "response": answer})
            row = transition(conn, row, "answer", actor_id=uid)
            payload = serialize(conn, org, row)
            return {"success": True, "agentSession": payload}
        append_activity(conn, org, row["id"], "prompt", body=body, author_kind="user",
                        author_id=uid, signal="continue")
        row = transition(conn, row, "prompt", actor_id=uid)
        # The prompt answers the elicitation, so the session rejoins the schedule
        # rather than sitting queued on a verdict that was computed for a question
        # that no longer exists.
        from hoja_server import agent_schedule

        row = agent_schedule.reevaluate(conn, row, at=now_iso())
        payload = serialize(conn, org, row)
    return {"success": True, "agentSession": payload}


def _open_ask(conn, org_id: str, session_id: str) -> str | None:
    """The ask id of the latest elicitation, when it has not been answered yet."""
    rows = conn.execute(select(agent_activity.c.kind, agent_activity.c.payload).where(and_(
        agent_activity.c.organization_id == org_id,
        agent_activity.c.session_id == session_id,
        agent_activity.c.kind.in_(("elicitation", "prompt")),
    )).order_by(desc(agent_activity.c.created_at)).limit(1)).mappings().first()
    if not rows or rows["kind"] != "elicitation":
        return None
    try:
        payload = json.loads(rows["payload"] or "{}")
    except (TypeError, ValueError):
        return None
    return str(payload.get("ask_id") or "") or None


def _lease_is_live(conn, org_id: str, lease_id: str | None) -> bool:
    if not lease_id:
        return False
    state = conn.execute(select(agent_lease.c.state).where(and_(
        agent_lease.c.organization_id == org_id, agent_lease.c.id == lease_id))).scalar()
    return state == "active"


#: How much free text one answer may carry to a run.
MAX_ANSWER_CHARS = 4000


def _clean_response(response, body: str) -> dict:
    """The answer as the runner relays it: picked labels per question, or a decline.

    Validation against the question's options happens in the harness, which owns the
    question; here the shape is bounded and a plain-text prompt becomes a custom answer,
    so a person who just types a reply is still heard.
    """
    if isinstance(response, str):
        # A JSON scalar written as a string literal arrives as text; read it the same way.
        try:
            response = json.loads(response)
        except (TypeError, ValueError):
            response = None
    if isinstance(response, dict) and response.get("cancelled"):
        return {"cancelled": True}
    answers = []
    raw = response.get("answers") if isinstance(response, dict) else None
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("header"):
            continue
        selected = [str(x)[:200] for x in (item.get("selected") or []) if str(x).strip()][:10]
        custom = str(item.get("customAnswer") or "").strip()[:MAX_ANSWER_CHARS] or None
        answers.append({"header": str(item["header"])[:200], "selected": selected,
                        "custom_answer": custom})
    if not answers:
        return {"answers": [], "text": (body or "").strip()[:MAX_ANSWER_CHARS]}
    return {"answers": answers}


@mutation.field("agentSessionVerify")
def m_agent_session_verify(_obj, info, id, verdict, rationale=None, model=None,
                           liveRevision=None):  # noqa: N803 - GraphQL argument name
    """A separate principal signs the claim. The caller's identity is the verifier.

    Nothing here writes the work product: the verifier re-reads it. That is what
    makes the principal read-only in substance rather than by a permission claim.
    """
    from hoja_server import agent_verify

    store, org, uid = _ctx(info)
    with store.engine.begin() as conn:
        row = _require_session(conn, org, id)
        verifier = agent_verify.principal_for(conn, org, uid, model=model)
        try:
            result = agent_verify.record_verdict(
                conn, row, verdict=verdict, verifier=verifier, rationale=rationale,
                live_revision=liveRevision)
        except agent_verify.VerificationRefusedError as exc:
            raise GraphQLError(str(exc), extensions=exc.extensions) from exc
        payload = serialize(conn, org, result["session"])
    return {"success": True, "agentSession": payload}


@agent_session_type.field("agent")
def r_agent(obj, info):
    store, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["agentUserId"]), uid)


@agent_session_type.field("issue")
def r_session_issue(obj, info):
    """The task a run belongs to, in the issue page's own vocabulary.

    The run queue is an issue list: a row keeps the issue's identifier, title and
    priority and adds a session chip. Carrying the issue on the session is what lets
    that happen in one read instead of a lookup per row from the browser.
    """
    store, org, uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["issueId"], uid)


def serialize_activity(row: dict) -> dict:
    """One activity in its GraphQL shape.

    The storage row is snake_case and the schema's fields are camelCase, and graphql-core's default
    resolver looks the field name up verbatim. Without this mapping ``authorKind`` (non-nullable)
    resolved to ``None`` and the whole read failed with INTERNAL_SERVER_ERROR — so the transcript
    was only ever readable through the four fields whose names happen to match. ``payload`` is
    already decoded by ``activities``.
    """
    return {
        "id": row["id"],
        "kind": row["kind"],
        "authorKind": row.get("author_kind") or "agent",
        "authorId": row.get("author_id"),
        "body": row.get("body"),
        "payload": row.get("payload"),
        "signal": row.get("signal"),
        "createdAt": row["created_at"],
    }


@agent_session_type.field("activities")
def r_activities(obj, info, first=None):
    store, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        return [serialize_activity(row) for row in activities(conn, org, obj["id"], first or 50)]


@agent_activity_type.field("payload")
def r_payload(obj, info):
    return obj.get("payload")


def bind(issue_type):
    """Attach the session surface to Issue before the Task alias copies it."""

    @issue_type.field("agentSession")
    def r_latest(issue_row, info):
        store, org, _uid = _ctx(info)
        issue_id = issue_row.get("id") if isinstance(issue_row, dict) else None
        if not issue_id:
            return None
        with store.engine.connect() as conn:
            row = latest_for_issue(conn, org, issue_id)
            return serialize(conn, org, row) if row else None

    @issue_type.field("agentSessions")
    def r_all(issue_row, info, first=None):
        store, org, _uid = _ctx(info)
        issue_id = issue_row.get("id") if isinstance(issue_row, dict) else None
        if not issue_id:
            return []
        with store.engine.connect() as conn:
            rows = conn.execute(select(agent_session).where(and_(
                agent_session.c.organization_id == org,
                agent_session.c.issue_id == issue_id,
            )).order_by(desc(agent_session.c.created_at)).limit(max(1, min(first or 20, 100)))).mappings().all()
            return [serialize(conn, org, dict(r)) for r in rows]

    return issue_type
