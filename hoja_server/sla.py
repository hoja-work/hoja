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

Time is stored as the same ISO-8601 UTC strings the rest of the schema uses;
all arithmetic goes through ``_parse``/``_iso`` so a naive string comparison is
never the thing that decides whether a deadline passed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server.db import (
    issue,
    issue_activity,
    new_id,
    now_iso,
    sla_clock,
    sla_policy,
    team,
    workflow_state,
)

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
    }

    extend type Issue {
      "The SLA clock this task runs against, or null when no policy covers it."
      sla: SlaClock
    }

    type SlaPolicyPayload { success: Boolean! slaPolicy: SlaPolicy }

    input SlaPolicyInput {
      name: String!
      teamId: String
      appliesPriorities: [Int!]
      firstResponseMinutes: Int
      resolutionMinutes: Int
      pauseStateIds: [String!]
      enabled: Boolean
    }

    extend type Query {
      "Every policy in the workspace; with teamId, that team's plus the workspace ones."
      slaPolicies(teamId: String): [SlaPolicy!]!
    }

    extend type Mutation {
      slaPolicyCreate(input: SlaPolicyInput!): SlaPolicyPayload!
      slaPolicyUpdate(id: String!, input: SlaPolicyInput!): SlaPolicyPayload!
      slaPolicyArchive(id: String!): Boolean!
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
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


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


def start_clock(conn, org_id, issue_id, policy, started_at):
    """Insert the clock row for a new task. Idempotent per issue."""
    existing = conn.execute(select(sla_clock.c.id).where(
        sla_clock.c.issue_id == issue_id)).first()
    if existing:
        return
    start = _parse(started_at)
    fr = start + timedelta(minutes=policy["first_response_minutes"]) \
        if policy["first_response_minutes"] else None
    res = start + timedelta(minutes=policy["resolution_minutes"]) \
        if policy["resolution_minutes"] else None
    conn.execute(sla_clock.insert().values(
        id=new_id(), organization_id=org_id, issue_id=issue_id, policy_id=policy["id"],
        started_at=started_at,
        first_response_due_at=_iso(fr) if fr else None,
        resolution_due_at=_iso(res) if res else None,
        paused_ms=0, created_at=started_at, updated_at=started_at))


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
    if stype == "completed" and not clock["resolved_at"]:
        values["resolved_at"] = at
    conn.execute(sla_clock.update().where(
        sla_clock.c.id == clock["id"]).values(**values))


def on_first_response(conn, org_id, issue_id, responder_id, at):
    """Record the first comment by someone other than the task's creator."""
    clock = conn.execute(select(sla_clock).where(
        sla_clock.c.issue_id == issue_id)).first()
    if not clock:
        return
    clock = dict(clock._mapping)
    if clock["first_responded_at"]:
        return
    creator = conn.execute(select(issue.c.creator_id).where(
        issue.c.id == issue_id)).scalar()
    if responder_id == creator:
        return
    conn.execute(sla_clock.update().where(sla_clock.c.id == clock["id"]).values(
        first_responded_at=at, updated_at=at))


def _clock_state(clock, policy, now):
    """Derive state and overdue minutes; no writes."""
    if clock["resolved_at"]:
        return "met" if not clock["breached_at"] else "breached", 0
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


def _values(conn, org, inp):
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
    return {
        "team_id": team_id,
        "name": (inp.get("name") or "").strip() or _bad("name", "name must not be empty."),
        "applies_priorities": _priority_list(inp.get("appliesPriorities")),
        "first_response_minutes": fr,
        "resolution_minutes": res,
        "pause_state_ids": _pause_ids(conn, org, team_id, inp.get("pauseStateIds")),
        "enabled": True if inp.get("enabled") is None else bool(inp["enabled"]),
    }


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
        payload = _serialize(conn, org, row)
    return {"success": True, "slaPolicy": payload}


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
        values = _values(conn, org, input)
        if _name_taken(conn, org, values["team_id"], values["name"], exclude=id):
            _bad("name", f"A policy named '{values['name']}' already exists here.")
        conn.execute(sla_policy.update().where(and_(
            sla_policy.c.organization_id == org,
            sla_policy.c.id == id)).values(updated_at=now_iso(), **values))
        row = dict(conn.execute(select(sla_policy).where(
            sla_policy.c.id == id)).first()._mapping)
        payload = _serialize(conn, org, row)
    return {"success": True, "slaPolicy": payload}


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
        now = _parse(now_iso())
        state, overdue = _clock_state(clock, policy, now)
    if state == "breached" and not clock["breached_at"] and record_breach:
        # One event per breach, written by the first read that observes it: the
        # clock row is the source of truth, the activity row is what Pulse and
        # the inbox read.
        ts = now_iso()
        with store.engine.begin() as conn:
            _record_breach(conn, org_id, issue_id, ts)
            conn.execute(issue_activity.insert().values(
                id=new_id(), organization_id=org_id, issue_id=issue_id,
                actor_id=None, event_type="slaBreached",
                changes=json.dumps({"policyId": policy["id"]}), created_at=ts))
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
        "_state": state,
        "_overdue": overdue,
    }
