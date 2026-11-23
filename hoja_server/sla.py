"""SLA policies and the per-task clock they start (HOJA-491).

The reference has Issue → SLAs; Hoja had no SLA model, so the settings entry was
a visible gap. This module is the policy store, the clock, and the three events
that move the clock — deliberately the same events the reference documents:

* **start** — a task created in a scope the policy covers starts its clock at
  creation. Nothing else starts one: a policy added later does not retro-date a
  clock for tasks that were already open, because a deadline that appears
  retroactively would report a breach nobody could have avoided.
* **pause / resume** — a transition *into* one of the policy's pause states
  (the "waiting on the customer" states) pauses the clock, and leaving it
  resumes. Paused time is accumulated in ``paused_ms`` and added to both due
  dates, so a pause is a real pause rather than a paused-looking number.
* **first response** — the first comment by someone other than the task's
  creator, which is what the target measures. A creator replying to themselves
  is not a response.
* **resolution** — a transition to a completed-category state stops the clock.

Breach is derived on read and *recorded once*: the first read that observes a
passed due date stamps ``breached_at`` and records an activity row, so Pulse and
the inbox have one event instead of one per page view.

HOJA-704 makes the policy configurable instead of fixed. A policy chooses
whether its resolution target is calendar minutes or the end of the task's
cycle, whether weekends count, and what ends the clock (completion, a started
state, or the arrival of a priority for a triage policy). The same revision
re-targets a clock when the task's priority, team or cycle changes: the clock
follows the task, in the same transaction as the write. Re-targeting never
erases a breach the clock already earned, and it never back-dates a clock.

Time is stored as the same ISO-8601 UTC strings the rest of the schema uses;
all arithmetic goes through ``_parse``/``_iso`` so a naive string comparison is
never the thing that decides whether a deadline passed.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server import activity
from hoja_server.db import (
    cycle,
    issue,
    new_id,
    now_iso,
    sla_clock,
    sla_policy,
    team,
    workflow_state,
)

STOPS_ON = ("completed", "started", "prioritized")

query = QueryType()
mutation = MutationType()
sla_policy_type = ObjectType("SlaPolicy")
sla_clock_type = ObjectType("SlaClock")

SDL = gql(
    """
    type SlaPolicy {
      id: ID!
      name: String!
      "Null for a workspace-wide policy; set for one team's own."
      team: Team
      "Priorities this policy covers; an empty list means every priority."
      appliesPriorities: [Int!]!
      firstResponseMinutes: Int
      resolutionMinutes: Int
      "States that pause the clock while the task sits in them."
      pauseStates: [WorkflowState!]!
      "When set, the resolution deadline is the end of the task's cycle."
      cycleBound: Boolean!
      "completed | started | prioritized — what ends the clock (HOJA-704)."
      stopsOn: String!
      "When set, minutes count only Monday to Friday in the team's timezone."
      businessDaysOnly: Boolean!
      enabled: Boolean!
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    "The clock a task is running against. Absent when no policy covers the task."
    type SlaClock {
      policy: SlaPolicy!
      startedAt: DateTime!
      firstResponseDueAt: DateTime
      resolutionDueAt: DateTime
      firstRespondedAt: DateTime
      resolvedAt: DateTime
      "ok | paused | breached | met"
      state: String!
      "Minutes past the nearest unmet deadline; 0 when nothing is overdue."
      overdueMinutes: Int!
      pausedMinutes: Int!
      breachedAt: DateTime
      "The end of the task's cycle, when it is in one; null otherwise."
      cycleEndsAt: DateTime
      "When the clock last moved to another policy or another cycle."
      retargetedAt: DateTime
    }

    extend type Issue {
      "The SLA clock this task runs against, or null when no policy covers it."
      sla: SlaClock
    }

    type SlaPolicyPayload {
      success: Boolean!
      slaPolicy: SlaPolicy
      "How many open tasks this call started a clock for (applyToOpen / apply)."
      startedClocks: Int
    }

    input SlaPolicyInput {
      name: String!
      teamId: String
      appliesPriorities: [Int!]
      firstResponseMinutes: Int
      resolutionMinutes: Int
      pauseStateIds: [String!]
      enabled: Boolean
      cycleBound: Boolean
      stopsOn: String
      businessDaysOnly: Boolean
      "Start clocks for open tasks this policy covers, at this moment."
      applyToOpen: Boolean
    }

    extend type Query {
      "Every policy in the workspace; with teamId, that team's plus the workspace ones."
      slaPolicies(teamId: String): [SlaPolicy!]!
    }

    extend type Mutation {
      slaPolicyCreate(input: SlaPolicyInput!): SlaPolicyPayload!
      slaPolicyUpdate(id: String!, input: SlaPolicyInput!): SlaPolicyPayload!
      slaPolicyArchive(id: String!): Boolean!
      "Start clocks for the open tasks this policy covers, at this moment."
      slaPolicyApply(id: String!): SlaPolicyPayload!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can manage SLAs.",
            extensions={"code": "FORBIDDEN"},
        )


def _bad(field, message):
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _minutes(value, field):
    if value is None:
        return None
    if not isinstance(value, int) or value < 1 or value > 60 * 24 * 365:
        _bad(field, f"{field} must be a positive number of minutes (1..525600).")
    return value


def _priority_list(values):
    if not values:
        return None
    cleaned = sorted({int(v) for v in values})
    if any(v < 0 or v > 4 for v in cleaned):
        _bad("appliesPriorities", "appliesPriorities may only contain 0..4.")
    return json.dumps(cleaned)


def _pause_ids(conn, org_id, team_id, values):
    ids = [v for v in (values or []) if v]
    if not ids:
        return None
    stmt = select(workflow_state.c.id).where(and_(
        workflow_state.c.organization_id == org_id,
        workflow_state.c.id.in_(ids)))
    found = set(conn.execute(stmt).scalars())
    if found != set(ids):
        _bad("pauseStateIds", "pauseStateIds must be states in this workspace.")
    return json.dumps(ids)


def _policies(conn, org_id, team_id=None):
    stmt = select(sla_policy).where(and_(
        sla_policy.c.organization_id == org_id,
        sla_policy.c.archived_at.is_(None)))
    if team_id:
        stmt = stmt.where((sla_policy.c.team_id == team_id)
                          | (sla_policy.c.team_id.is_(None)))
    return [dict(r._mapping) for r in conn.execute(stmt.order_by(sla_policy.c.name)).all()]


def _serialize(conn, org_id, row) -> dict:
    team_row = None
    if row["team_id"]:
        t = conn.execute(select(team).where(and_(
            team.c.id == row["team_id"], team.c.organization_id == org_id))).first()
        team_row = dict(t._mapping) if t else None
    pause_ids = json.loads(row["pause_state_ids"]) if row["pause_state_ids"] else []
    states = []
    if pause_ids:
        states = [dict(r._mapping) for r in conn.execute(select(workflow_state).where(and_(
            workflow_state.c.organization_id == org_id,
            workflow_state.c.id.in_(pause_ids)))).all()]
    return {
        "id": row["id"], "name": row["name"], "team": team_row,
        "appliesPriorities": json.loads(row["applies_priorities"]) if row["applies_priorities"] else [],
        "firstResponseMinutes": row["first_response_minutes"],
        "resolutionMinutes": row["resolution_minutes"],
        "pauseStates": states,
        # NULL means "the behaviour this policy already had", so a policy written
        # before revision 74 reads as calendar minutes, stopped on completion,
        # every day counted — never as an error the admin has to go and fix.
        "cycleBound": bool(row.get("cycle_bound")),
        "stopsOn": row.get("stops_on") or "completed",
        "businessDaysOnly": bool(row.get("business_days_only")),
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _policy_by_id(conn, org_id, policy_id):
    row = conn.execute(select(sla_policy).where(and_(
        sla_policy.c.organization_id == org_id,
        sla_policy.c.id == policy_id))).first()
    return dict(row._mapping) if row else None


def _stops_on(policy) -> str:
    return (policy.get("stops_on") or "completed") if policy else "completed"


def _team_zone(conn, org_id, team_id):
    """The team's cadence timezone, which is what its cycles are drawn in.

    UTC when the team has none, and UTC for a name this build cannot resolve:
    a wrong-but-defined deadline is better than a crash on a task read, and the
    cadence reconciler already refuses an unknown zone on the path that writes
    one.
    """
    name = None
    if team_id:
        name = conn.execute(select(team.c.timezone).where(and_(
            team.c.organization_id == org_id, team.c.id == team_id))).scalar()
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _add_minutes(start: datetime, minutes: int, business_only: bool, zone) -> datetime:
    """Advance by ``minutes``, skipping Saturday and Sunday when asked to.

    A business day counts as its full 24 local hours — the policy expresses its
    targets in minutes, so the honest reading of "weekdays only" is that the
    weekend contributes none of them, not that the working day is assumed to be
    nine hours. Segments are computed between two instants in the same zone, so
    a daylight-saving boundary moves the deadline by the real elapsed time.

    A target raised *during* the weekend starts counting when the working week
    resumes, at the same local time: a one-hour target raised on Saturday
    morning is due Monday morning, not Monday 01:00, because Saturday and Sunday
    contribute nothing to count.
    """
    if not business_only:
        return start + timedelta(minutes=minutes)
    remaining = int(minutes)
    local = start.astimezone(zone)
    if local.weekday() >= 5:  # raised on a weekend: begin at Monday, same hour
        local = local + timedelta(days=7 - local.weekday())
        cursor = local.astimezone(timezone.utc)
    else:
        cursor = start
    while remaining > 0:
        local = cursor.astimezone(zone)
        next_midnight = datetime.combine(
            local.date() + timedelta(days=1), time(0, 0), tzinfo=zone)
        if local.weekday() >= 5:  # Saturday, Sunday
            cursor = next_midnight.astimezone(timezone.utc)
            continue
        segment = int((next_midnight - local).total_seconds() // 60)
        if segment >= remaining:
            return cursor + timedelta(minutes=remaining)
        remaining -= segment
        cursor = next_midnight.astimezone(timezone.utc)
    return cursor


def cycle_ends_at(conn, org_id, cycle_id):
    """The end of one cycle, or None when it has no readable end."""
    if not cycle_id:
        return None
    ends = conn.execute(select(cycle.c.ends_at).where(and_(
        cycle.c.organization_id == org_id,
        cycle.c.id == cycle_id))).scalar()
    return _parse(ends)


def _deadlines(conn, org_id, issue_row, policy, start, paused_ms=0):
    """Both due dates for a task under ``policy``, computed from ``start``.

    A cycle-bound policy takes its resolution deadline from the task's cycle and
    adds the time the clock has been paused; a task with no cycle falls back to
    ``resolution_minutes`` from ``start``, which is exactly what a policy without
    the option does. The first-response target is always a number of minutes —
    a cycle says when work is due, not when somebody must answer.
    """
    zone = _team_zone(conn, org_id, issue_row.get("team_id"))
    business = bool(policy.get("business_days_only"))
    first = policy.get("first_response_minutes")
    first_due = _add_minutes(start, first, business, zone) if first else None
    ends = cycle_ends_at(conn, org_id, issue_row.get("cycle_id")) \
        if policy.get("cycle_bound") else None
    if ends:
        resolution_due = ends + timedelta(milliseconds=int(paused_ms or 0))
    elif policy.get("resolution_minutes"):
        resolution_due = _add_minutes(
            start, policy["resolution_minutes"], business, zone)
    else:
        resolution_due = None
    return first_due, resolution_due


def matching_policy(conn, org_id, team_id, priority):
    """The policy that covers this task: the team's own first, then the workspace's.

    Team-beats-workspace (not "first by name") so an admin can tighten one
    team without deleting the workspace policy everyone else inherits.
    """
    rows = _policies(conn, org_id, team_id)
    ordered = sorted(rows, key=lambda r: 0 if r["team_id"] else 1)
    for row in ordered:
        if not row["enabled"]:
            continue
        prios = json.loads(row["applies_priorities"]) if row["applies_priorities"] else []
        if prios and int(priority or 0) not in prios:
            continue
        return row
    return None


def _issue_row(conn, org_id, issue_id):
    row = conn.execute(select(issue).where(and_(
        issue.c.organization_id == org_id,
        issue.c.id == issue_id))).mappings().first()
    return dict(row) if row else None


def _activity(conn, org_id, issue_id, event_type, payload, at, actor_id=None):
    activity.record(conn, org_id=org_id, issue_id=issue_id, actor_id=actor_id,
                    event_type=event_type, changes=json.dumps(payload), created_at=at)


def start_clock(conn, org_id, issue_id, policy, started_at):
    """Insert the clock row for a new task. Idempotent per issue."""
    existing = conn.execute(select(sla_clock.c.id).where(
        sla_clock.c.issue_id == issue_id)).first()
    if existing:
        return
    issue_row = _issue_row(conn, org_id, issue_id) or {}
    fr, res = _deadlines(conn, org_id, issue_row, policy, _parse(started_at))
    # A task that is already sitting in one of the policy's pause states starts
    # paused. Otherwise applying a policy to a backlog would charge every waiting
    # task for the time it waited before the policy existed.
    paused = started_at if issue_row.get("state_id") in _paused_states(policy) else None
    conn.execute(sla_clock.insert().values(
        id=new_id(), organization_id=org_id, issue_id=issue_id, policy_id=policy["id"],
        started_at=started_at,
        first_response_due_at=_iso(fr) if fr else None,
        resolution_due_at=_iso(res) if res else None,
        paused_since=paused,
        paused_ms=0, created_at=started_at, updated_at=started_at))


def _paused_states(policy) -> set:
    return set(json.loads(policy["pause_state_ids"])) if policy.get("pause_state_ids") else set()


def _restart(conn, org_id, issue_row, policy, at, clock, reason, event):
    """Point an existing clock at ``policy`` and recompute from ``at``.

    The clock row is one per task (``uq_sla_clock_issue``), so re-targeting
    rewrites it rather than adding history: ``started_at`` becomes the moment of
    the change, the deadlines are computed from there, and the ledger keeps the
    previous policy. A breach already stamped stays stamped — the clock is
    allowed to move forward, never backwards over a deadline it missed.
    """
    now = _parse(at)
    fr, res = _deadlines(conn, org_id, issue_row, policy, now)
    paused = at if issue_row.get("state_id") in _paused_states(policy) else None
    values = {
        "policy_id": policy["id"], "started_at": at,
        "first_response_due_at": _iso(fr) if fr else None,
        "resolution_due_at": _iso(res) if res else None,
        "resolved_at": None, "detached_at": None, "retargeted_at": at,
        "paused_since": paused, "paused_ms": 0, "updated_at": at,
    }
    conn.execute(sla_clock.update().where(
        sla_clock.c.id == clock["id"]).values(**values))
    _activity(conn, org_id, issue_row["id"], event, reason, at)


def retarget_clock(conn, org_id, issue_id, at, actor_id=None):
    """Follow the task: re-match the policy after a priority, team or cycle change.

    Runs inside the write's own transaction, so a reader never sees the task and
    its clock disagree. The order is the one that cannot lose a breach:

    1. evaluate the clock that is running and stamp ``breached_at`` if a deadline
       it should have met has already passed — moving a task to the next cycle
       after its cycle ended is a breach, not an erasure;
    2. a triage clock (``stops_on=prioritized``) resolves when the priority
       arrives, and is then replaced by the priority's own policy;
    3. otherwise switch policy, refresh a cycle-bound deadline, detach when
       nothing matches, or start a clock for a task that had none.
    """
    issue_row = _issue_row(conn, org_id, issue_id)
    if not issue_row:
        return None
    now = _parse(at)
    clock_row = conn.execute(select(sla_clock).where(
        sla_clock.c.issue_id == issue_id)).first()
    clock = dict(clock_row._mapping) if clock_row else None
    target = matching_policy(conn, org_id, issue_row.get("team_id"),
                             issue_row.get("priority"))
    priority = int(issue_row.get("priority") or 0)

    if clock is None:
        if target is None:
            return None
        start_clock(conn, org_id, issue_id, target, at)
        _activity(conn, org_id, issue_id, "slaStarted",
                  {"policyId": target["id"], "reason": "matched"}, at, actor_id)
        return target["id"]

    current = _policy_by_id(conn, org_id, clock["policy_id"])
    # 1. the clock it is already running keeps what it earned.
    if current and not clock["detached_at"]:
        state, _overdue = _clock_state(clock, current, now)
        if state == "breached" and not clock["breached_at"]:
            _record_breach(conn, org_id, issue_id, at)
            _activity(conn, org_id, issue_id, "slaBreached",
                      {"policyId": current["id"]}, at)
            clock["breached_at"] = at

    # 2. a triage clock resolves when a priority arrives, and the priority's
    #    policy then takes the task over. Handled before the stopped check,
    #    because resolving is what the clock was for.
    triage_done = (
        current is not None
        and _stops_on(current) == "prioritized"
        and priority != 0
        and not clock["resolved_at"]
        and not clock["detached_at"]
    )
    if triage_done:
        _activity(conn, org_id, issue_id, "slaResolved",
                  {"policyId": current["id"], "reason": "prioritized",
                   "priority": priority}, at, actor_id)

    # A stopped clock is not re-matched: it already answered its question.
    if clock["resolved_at"] and not triage_done:
        return None

    # 3a. nothing covers the task any more: stop following a policy. The state
    #     reads "detached" rather than "met", because nothing was met.
    if target is None:
        if clock["detached_at"]:
            return None
        conn.execute(sla_clock.update().where(sla_clock.c.id == clock["id"]).values(
            detached_at=at, updated_at=at))
        _activity(conn, org_id, issue_id, "slaDetached",
                  {"policyId": clock["policy_id"]}, at, actor_id)
        return None

    # 3b. a different policy, or a detached clock that matches again: start from
    #     the change time, never from the original creation.
    if target["id"] != clock["policy_id"] or clock["detached_at"]:
        reason = {"fromPolicyId": clock["policy_id"], "toPolicyId": target["id"],
                  "priority": priority, "cycleId": issue_row.get("cycle_id")}
        _restart(conn, org_id, issue_row, target, at, clock, reason,
                 "slaRetargeted")
        return target["id"]

    # 3c. same policy, and it binds to the cycle: the task may have moved to the
    #     next one, so the resolution deadline follows that cycle's end. Paused
    #     time still shifts it, so a pause is not undone by a re-target. Nothing
    #     else is recomputed here: a policy measured in calendar minutes already
    #     holds the deadlines it was started with, and re-deriving them would
    #     silently move a target an admin never changed.
    if target.get("cycle_bound"):
        ends = cycle_ends_at(conn, org_id, issue_row.get("cycle_id"))
        if ends:
            new_res = _iso(ends + timedelta(milliseconds=int(clock["paused_ms"] or 0)))
            if new_res != clock["resolution_due_at"]:
                conn.execute(sla_clock.update().where(
                    sla_clock.c.id == clock["id"]).values(
                        resolution_due_at=new_res,
                        retargeted_at=at, updated_at=at))
                _activity(conn, org_id, issue_id, "slaRetargeted",
                          {"policyId": target["id"], "reason": "cycle",
                           "cycleId": issue_row.get("cycle_id"),
                           "fromResolutionDueAt": clock["resolution_due_at"],
                           "resolutionDueAt": new_res}, at, actor_id)
    return target["id"]


def apply_to_open(conn, org_id, policy, at):
    """Start clocks for the open tasks this policy already covers.

    ``started_at`` is now, always: back-dating would report a breach nobody
    could have avoided. A task whose cycle-bound deadline has already passed is
    skipped for the same reason — the cycle ended before the policy existed, so
    there is nothing fair to measure. Returns how many clocks started.
    """
    rows = conn.execute(select(issue).where(and_(
        issue.c.organization_id == org_id,
        issue.c.archived_at.is_(None),
        issue.c.completed_at.is_(None),
        issue.c.canceled_at.is_(None)))).mappings().all()
    started = 0
    # One lookup per (team, priority) rather than per task: an admin applying a
    # policy to a workspace with ten thousand open tasks should not run ten
    # thousand policy scans.
    matched: dict = {}

    def target_for(issue_row):
        key = (issue_row.get("team_id"), int(issue_row.get("priority") or 0))
        if key not in matched:
            matched[key] = matching_policy(conn, org_id, *key)
        return matched[key]

    for row in rows:
        issue_row = dict(row)
        target = target_for(issue_row)
        if target is None or target["id"] != policy["id"]:
            continue
        existing = conn.execute(select(sla_clock.c.id).where(
            sla_clock.c.issue_id == issue_row["id"])).first()
        if existing:
            continue
        _fr, res = _deadlines(conn, org_id, issue_row, target, _parse(at))
        if res is not None and res < _parse(at):
            continue
        start_clock(conn, org_id, issue_row["id"], target, at)
        _activity(conn, org_id, issue_row["id"], "slaStarted",
                  {"policyId": target["id"], "reason": "appliedToOpen"}, at)
        started += 1
    return started


def _shift(row, delta: timedelta) -> dict:
    """Move both unmet due dates by the time spent paused."""
    out = dict(row)
    for key in ("first_response_due_at", "resolution_due_at"):
        if out[key]:
            out[key] = _iso(_parse(out[key]) + delta)
    return out


def on_state_change(conn, org_id, issue_id, state_id, at):
    """Pause, resume or stop the clock for a state transition.

    Called from ``issue_update`` after the row is written, inside the same
    transaction: a state change and the clock it moves commit together.
    """
    clock = conn.execute(select(sla_clock).where(
        sla_clock.c.issue_id == issue_id)).first()
    if not clock:
        return
    clock = dict(clock._mapping)
    policy = conn.execute(select(sla_policy).where(
        sla_policy.c.id == clock["policy_id"])).first()
    if not policy:
        return
    policy = dict(policy._mapping)
    pause_ids = set(json.loads(policy["pause_state_ids"])) if policy["pause_state_ids"] else set()
    stype = conn.execute(select(workflow_state.c.type).where(and_(
        workflow_state.c.organization_id == org_id,
        workflow_state.c.id == state_id))).scalar()
    now = _parse(at)
    values = {"updated_at": at}
    paused_since = _parse(clock["paused_since"])
    if state_id in pause_ids and not paused_since:
        values["paused_since"] = at
    elif state_id not in pause_ids and paused_since:
        elapsed = now - paused_since
        values["paused_since"] = None
        values["paused_ms"] = int(clock["paused_ms"] or 0) + int(elapsed.total_seconds() * 1000)
        shifted = _shift(clock, elapsed)
        values["first_response_due_at"] = shifted["first_response_due_at"]
        values["resolution_due_at"] = shifted["resolution_due_at"]
    # What ends the clock is the policy's choice (HOJA-704). Completion always
    # ends it — a finished task is finished under every policy; ``started`` also
    # ends it when the work begins, which is what a policy measuring "we picked
    # it up" wants; ``prioritized`` ends it when the priority arrives, which
    # ``retarget_clock`` handles because no state transition is involved.
    stops_on = _stops_on(policy)
    ends = stype == "completed" or (stops_on == "started" and stype == "started")
    if ends and not clock["resolved_at"]:
        values["resolved_at"] = at
    conn.execute(sla_clock.update().where(
        sla_clock.c.id == clock["id"]).values(**values))


def on_first_response(conn, org_id, issue_id, responder_id, at, *, on_behalf_of: str | None = None):
    """Record the first comment by someone other than the task's creator or their agents."""
    clock = conn.execute(select(sla_clock).where(
        sla_clock.c.issue_id == issue_id)).first()
    if not clock:
        return
    clock = dict(clock._mapping)
    if clock["first_responded_at"]:
        return
    creator = conn.execute(select(issue.c.creator_id).where(
        issue.c.id == issue_id)).scalar()
    if on_behalf_of is None:
        from hoja_server import tenant_context

        on_behalf_of = tenant_context.current_actor()[2]
    principal = on_behalf_of or responder_id
    if principal == creator or responder_id == creator:
        return
    conn.execute(sla_clock.update().where(sla_clock.c.id == clock["id"]).values(
        first_responded_at=at, updated_at=at))


def _clock_state(clock, policy, now):
    """Derive state and overdue minutes; no writes."""
    if clock["resolved_at"]:
        return "met" if not clock["breached_at"] else "breached", 0
    if clock.get("detached_at"):
        # No policy covers the task any more. It is not "met" — nothing was met —
        # and it is not overdue, because no target applies.
        return ("breached", 0) if clock["breached_at"] else ("detached", 0)
    if clock["paused_since"]:
        return "paused", 0
    overdue = 0
    breached = bool(clock["breached_at"])
    for due_key, done_key in (("first_response_due_at", "first_responded_at"),
                              ("resolution_due_at", "resolved_at")):
        due = _parse(clock[due_key])
        if not due or clock[done_key]:
            continue
        if now > due:
            breached = True
            overdue = max(overdue, int((now - due).total_seconds() // 60))
    if clock["breached_at"]:
        return "breached", overdue
    return ("breached", overdue) if breached else ("ok", 0)


def _record_breach(conn, org_id, issue_id, at):
    conn.execute(sla_clock.update().where(and_(
        sla_clock.c.issue_id == issue_id,
        sla_clock.c.breached_at.is_(None))).values(breached_at=at, updated_at=at))


@query.field("slaPolicies")
def r_policies(_, info, teamId=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        rows = _policies(conn, org, teamId)
        return [_serialize(conn, org, r) for r in rows]


def _values(conn, org, inp, *, creating=True):
    team_id = inp.get("teamId") or None
    if team_id:
        found = conn.execute(select(team.c.id).where(and_(
            team.c.id == team_id,
            team.c.organization_id == org,
            team.c.archived_at.is_(None)))).first()
        if not found:
            _bad("teamId", "teamId must be a team in this workspace.")
    fr = _minutes(inp.get("firstResponseMinutes"), "firstResponseMinutes")
    res = _minutes(inp.get("resolutionMinutes"), "resolutionMinutes")
    if fr is None and res is None:
        _bad("resolutionMinutes", "A policy needs at least one target.")
    stops_on = inp.get("stopsOn")
    if stops_on is not None and stops_on not in STOPS_ON:
        _bad("stopsOn", "stopsOn must be one of " + ", ".join(STOPS_ON) + ".")
    out = {
        "team_id": team_id,
        "name": (inp.get("name") or "").strip() or _bad("name", "name must not be empty."),
        "applies_priorities": _priority_list(inp.get("appliesPriorities")),
        "first_response_minutes": fr,
        "resolution_minutes": res,
        "pause_state_ids": _pause_ids(conn, org, team_id, inp.get("pauseStateIds")),
        "cycle_bound": bool(inp.get("cycleBound")),
        "stops_on": stops_on,
        "business_days_only": bool(inp.get("businessDaysOnly")),
    }
    # A new policy starts enabled unless told otherwise. An update only touches
    # ``enabled`` when the input names it: editing a disabled policy's targets
    # must not quietly turn it back on (HOJA-734).
    if inp.get("enabled") is not None:
        out["enabled"] = bool(inp["enabled"])
    elif creating:
        out["enabled"] = True
    return out


def _name_taken(conn, org, team_id, name, exclude=None):
    stmt = select(sla_policy.c.id).where(and_(
        sla_policy.c.organization_id == org,
        sla_policy.c.archived_at.is_(None),
        sla_policy.c.name == name))
    stmt = stmt.where(sla_policy.c.team_id.is_(None)) if team_id is None \
        else stmt.where(sla_policy.c.team_id == team_id)
    if exclude:
        stmt = stmt.where(sla_policy.c.id != exclude)
    return conn.execute(stmt).first() is not None


@mutation.field("slaPolicyCreate")
def m_create(_, info, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        values = _values(conn, org, input)
        if _name_taken(conn, org, values["team_id"], values["name"]):
            _bad("name", f"A policy named '{values['name']}' already exists here.")
        pid, ts = new_id(), now_iso()
        conn.execute(sla_policy.insert().values(
            id=pid, organization_id=org, created_at=ts, updated_at=ts, **values))
        row = dict(conn.execute(select(sla_policy).where(
            sla_policy.c.id == pid)).first()._mapping)
        started = apply_to_open(conn, org, row, ts) if input.get("applyToOpen") else 0
        payload = _serialize(conn, org, row)
    return {"success": True, "slaPolicy": payload, "startedClocks": started}


@mutation.field("slaPolicyUpdate")
def m_update(_, info, id, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        existing = conn.execute(select(sla_policy.c.id).where(and_(
            sla_policy.c.organization_id == org,
            sla_policy.c.id == id,
            sla_policy.c.archived_at.is_(None)))).first()
        if not existing:
            raise GraphQLError("SLA policy not found.", extensions={"code": "NOT_FOUND"})
        values = _values(conn, org, input, creating=False)
        if _name_taken(conn, org, values["team_id"], values["name"], exclude=id):
            _bad("name", f"A policy named '{values['name']}' already exists here.")
        conn.execute(sla_policy.update().where(and_(
            sla_policy.c.organization_id == org,
            sla_policy.c.id == id)).values(updated_at=now_iso(), **values))
        row = dict(conn.execute(select(sla_policy).where(
            sla_policy.c.id == id)).first()._mapping)
        started = apply_to_open(conn, org, row, now_iso()) if input.get("applyToOpen") else 0
        payload = _serialize(conn, org, row)
    return {"success": True, "slaPolicy": payload, "startedClocks": started}


@mutation.field("slaPolicyApply")
def m_apply(_, info, id):
    """Start clocks for the open tasks this policy covers, at this moment."""
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        row = conn.execute(select(sla_policy).where(and_(
            sla_policy.c.organization_id == org,
            sla_policy.c.id == id,
            sla_policy.c.archived_at.is_(None)))).first()
        if not row:
            raise GraphQLError("SLA policy not found.", extensions={"code": "NOT_FOUND"})
        row = dict(row._mapping)
        started = apply_to_open(conn, org, row, now_iso())
        payload = _serialize(conn, org, row)
    return {"success": True, "slaPolicy": payload, "startedClocks": started}


@mutation.field("slaPolicyArchive")
def m_archive(_, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        result = conn.execute(sla_policy.update().where(and_(
            sla_policy.c.organization_id == org,
            sla_policy.c.id == id,
            sla_policy.c.archived_at.is_(None),
        )).values(archived_at=now_iso(), updated_at=now_iso()))
    return result.rowcount > 0


def bind(issue_type):
    """Attach ``Issue.sla`` to the shared Issue object the schema already has."""

    @issue_type.field("sla")
    def r_sla(issue_row, info):
        store, org, _uid = _ctx(info)
        issue_id = issue_row.get("id") if isinstance(issue_row, dict) else None
        if not issue_id:
            return None
        return clock_payload(store, org, issue_id)

    return issue_type


@sla_clock_type.field("state")
def r_state(clock, info):
    return clock["_state"]


@sla_clock_type.field("overdueMinutes")
def r_overdue(clock, info):
    return clock["_overdue"]


@sla_clock_type.field("pausedMinutes")
def r_paused(clock, info):
    return int((clock["paused_ms"] or 0) // 60000)


def clock_payload(store, org_id, issue_id, *, record_breach=True):
    """Read a task's clock, deriving state and recording the breach once."""
    with store.engine.connect() as conn:
        clock = conn.execute(select(sla_clock).where(
            sla_clock.c.issue_id == issue_id)).first()
        if not clock:
            return None
        clock = dict(clock._mapping)
        policy = conn.execute(select(sla_policy).where(
            sla_policy.c.id == clock["policy_id"])).first()
        if not policy:
            return None
        policy = dict(policy._mapping)
        payload_policy = _serialize(conn, org_id, policy)
        issue_row = _issue_row(conn, org_id, issue_id) or {}
        cycle_end = cycle_ends_at(conn, org_id, issue_row.get("cycle_id"))
        now = _parse(now_iso())
        state, overdue = _clock_state(clock, policy, now)
    if state == "breached" and not clock["breached_at"] and record_breach:
        # One event per breach, written by the first read that observes it: the
        # clock row is the source of truth, the activity row is what Pulse and
        # the inbox read.
        ts = now_iso()
        with store.engine.begin() as conn:
            _record_breach(conn, org_id, issue_id, ts)
            _activity(conn, org_id, issue_id, "slaBreached", {"policyId": policy["id"]}, ts)
        # The read that observes the breach reports it stamped, not blank: the
        # caller must not have to read twice to learn what just happened.
        clock["breached_at"] = ts
    return {
        "policy": payload_policy,
        "startedAt": clock["started_at"],
        "firstResponseDueAt": clock["first_response_due_at"],
        "resolutionDueAt": clock["resolution_due_at"],
        "firstRespondedAt": clock["first_responded_at"],
        "resolvedAt": clock["resolved_at"],
        "paused_ms": clock["paused_ms"],
        "breachedAt": clock["breached_at"],
        "cycleEndsAt": _iso(cycle_end) if cycle_end else None,
        "retargetedAt": clock.get("retargeted_at"),
        "_state": state,
        "_overdue": overdue,
    }
