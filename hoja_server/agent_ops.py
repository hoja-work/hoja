"""Agent operations over GraphQL: launch, inspect and debug runs in one query (HOJA-602).

Everything a person can do on the agent screens is reachable here too, so a run can be
driven and debugged from a GraphQL client (or a script) without the UI:

* **launch** — ``agentRunLaunch`` delegates an existing task, or creates one, and returns
  the session, its lease and the schedule's queue reasons;
* **inspect** — ``agentRun(id)`` takes a lease id or a run id and returns the attempt with
  its prompt, finish result, session, side effects and its whole timeline (``logs``);
* **debug** — ``agentRunLogs`` filters every run's timeline by machine, run, level and
  source: the runner's events, its warnings and tracebacks, and the server's notes.

The rest of the surface already exists and composes with these: machines
(``runners``, ``runnerEnrollmentCreate`` → ``connectCommand``, ``runnerRevoke``), sessions
(``workspaceAgentSessions``, ``agentSessionStop``, ``agentSessionPrompt``), policy
(``agentPolicy``, ``agentPolicyUpdate``, ``agentSecretSet``) and ``agentLeases``.
Everything here is admin-only, like the runner and lease reads it extends.
"""
from __future__ import annotations

import json

from ariadne import MutationType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, desc, or_, select

from hoja_server import agent_run_logs, agent_runners, agent_sessions, agent_verify
from hoja_server.db import agent_lease, agent_member, agent_run_log, agent_side_effect, runner

query = QueryType()
mutation = MutationType()
lease_type = agent_runners.agent_lease_type

SDL = gql(
    """
    "One line of a run's timeline: a runner event, a runner log line or a server note."
    type AgentRunLog {
      id: ID!
      leaseId: ID
      runnerId: ID
      runnerName: String
      "runner | server"
      source: String!
      "event | debug | info | warning | error"
      level: String!
      "The event kind, the runner's logger name, or the server's note kind."
      kind: String
      message: String!
      "The event payload, or {traceback, logger, ...} for a log line."
      detail: JSON
      createdAt: DateTime!
    }

    type AgentRunLogConnection { nodes: [AgentRunLog!]! pageInfo: PageInfo! }

    "One external write a run recorded (keyed run_id:seq), e.g. its claim or a pull request."
    type AgentRunEffect {
      key: String!
      kind: String!
      externalRef: String
      payload: JSON
      createdAt: DateTime!
    }

    extend type AgentLease {
      "The task text the runner was given."
      prompt: String!
      cancelRequestedAt: DateTime
      cancelReason: String
      lostAt: DateTime
      lostReason: String
      "The last policy refusal while it was queued (why it is not running)."
      refusalReason: String
      policyVersion: Int
      "The finish as stored: {state, summary, evidence, session_state, external_ref}."
      result: JSON
      "The finish summary: for a failed run, the reason."
      summary: String
      "The agent session this attempt executes."
      session: AgentSession
      effects: [AgentRunEffect!]!
      "This attempt's timeline, oldest first. `level` filters (comma-separated), `source` too."
      logs(first: Int = 500, level: String, source: String): [AgentRunLog!]!
    }

    input AgentRunLaunchInput {
      "The agent member (AgentMember.id) or its user id."
      agentMemberId: String!
      "Delegate this existing task (id or identifier, e.g. HOJA-12)…"
      taskId: String
      "…or create one in this team, with this title and description."
      teamId: String
      title: String
      description: String
    }

    type AgentRunLaunchPayload {
      success: Boolean!
      issue: Issue
      agentSession: AgentSession
      "The queued or granted attempt; null while the schedule check holds the session."
      lease: AgentLease
      "Why it is waiting, if it is (capacity, budget, window, blocked, approval, lease_held)."
      queueReason: [String!]!
    }

    extend type Query {
      "One run attempt by lease id or run id, admin only: prompt, result, session, effects, logs."
      agentRun(id: String!): AgentLease
      "Run timelines, newest first, admin only. Filter by machine, run, level and source; `before` pages back (a createdAt)."
      agentRunLogs(runnerId: String, leaseId: String, level: String, source: String,
                   first: Int = 100, before: String): AgentRunLogConnection!
    }

    extend type Mutation {
      "Launch a job: delegate a task (or create one) to an agent. Re-launching a task whose last run ended starts a new run."
      agentRunLaunch(input: AgentRunLaunchInput!): AgentRunLaunchPayload!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _admin(info):
    store, org, uid = _ctx(info)
    agent_runners._require_admin(store, org, uid)
    return store, org, uid


def _levels(value: str | None) -> list[str]:
    return [v.strip().lower() for v in (value or "").split(",") if v.strip()]


def _runner_names(conn, org_id: str, ids) -> dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    rows = conn.execute(select(runner.c.id, runner.c.name).where(and_(
        runner.c.organization_id == org_id, runner.c.id.in_(ids))))
    return {r[0]: r[1] for r in rows}


# ------------------------------------------------------------------------------- queries
@query.field("agentRun")
def r_agent_run(_obj, info, id):  # noqa: A002 - GraphQL argument name
    store, org, _uid = _admin(info)
    with store.engine.connect() as conn:
        row = conn.execute(select(agent_lease).where(and_(
            agent_lease.c.organization_id == org,
            or_(agent_lease.c.id == id, agent_lease.c.run_id == id),
        ))).mappings().first()
        if row is None:
            return None
        names = _runner_names(conn, org, [row["runner_id"]])
    return agent_runners.serialize_lease(dict(row), runner_name=names.get(row["runner_id"]))


@query.field("agentRunLogs")
def r_agent_run_logs(_obj, info, runnerId=None, leaseId=None, level=None,  # noqa: N803
                     source=None, first=100, before=None):
    store, org, _uid = _admin(info)
    limit = max(1, min(int(first or 100), 1000))
    clauses = [agent_run_log.c.organization_id == org]
    if runnerId:
        clauses.append(agent_run_log.c.runner_id == runnerId)
    if leaseId:
        clauses.append(agent_run_log.c.lease_id == leaseId)
    if _levels(level):
        clauses.append(agent_run_log.c.level.in_(_levels(level)))
    if _levels(source):
        clauses.append(agent_run_log.c.source.in_(_levels(source)))
    if before:
        clauses.append(agent_run_log.c.created_at < before)
    with store.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(select(agent_run_log).where(and_(*clauses))
                                              .order_by(desc(agent_run_log.c.created_at))
                                              .limit(limit + 1)).mappings()]
        names = _runner_names(conn, org, [r["runner_id"] for r in rows])
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "nodes": [agent_run_logs.serialize(r, runner_name=names.get(r["runner_id"])) for r in rows],
        "pageInfo": {"hasNextPage": more, "endCursor": rows[-1]["created_at"] if rows else None,
                     "hasPreviousPage": False, "startCursor": rows[0]["created_at"] if rows else None},
    }


# ----------------------------------------------------------------- AgentLease debug fields
def _row(obj) -> dict:
    return obj.get("_row") or {}


@lease_type.field("prompt")
def r_prompt(obj, _info):
    return _row(obj).get("prompt") or ""


for _field, _column in (("cancelRequestedAt", "cancel_requested_at"),
                        ("cancelReason", "cancel_reason"), ("lostAt", "lost_at"),
                        ("lostReason", "lost_reason"), ("refusalReason", "refusal_reason"),
                        ("policyVersion", "policy_version")):
    lease_type.set_field(_field, lambda obj, _info, _c=_column: _row(obj).get(_c))


def _result(obj) -> dict | None:
    raw = _row(obj).get("result")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": raw}


@lease_type.field("result")
def r_result(obj, _info):
    return _result(obj)


@lease_type.field("summary")
def r_summary(obj, _info):
    return (_result(obj) or {}).get("summary")


@lease_type.field("session")
def r_session(obj, info):
    store, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        session = agent_verify.session_for_lease(conn, org, _row(obj))
        return agent_sessions.serialize(conn, org, session) if session else None


@lease_type.field("effects")
def r_effects(obj, info):
    store, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        rows = conn.execute(select(agent_side_effect).where(and_(
            agent_side_effect.c.organization_id == org,
            agent_side_effect.c.lease_id == obj["id"],
        )).order_by(agent_side_effect.c.created_at)).mappings().all()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) if r["payload"] else None
        except (TypeError, ValueError):
            payload = None
        out.append({"key": r["key"], "kind": r["kind"], "externalRef": r["external_ref"],
                    "payload": payload, "createdAt": r["created_at"]})
    return out


@lease_type.field("logs")
def r_logs(obj, info, first=500, level=None, source=None):
    store, org, _uid = _ctx(info)
    clauses = [agent_run_log.c.organization_id == org, agent_run_log.c.lease_id == obj["id"]]
    if _levels(level):
        clauses.append(agent_run_log.c.level.in_(_levels(level)))
    if _levels(source):
        clauses.append(agent_run_log.c.source.in_(_levels(source)))
    with store.engine.connect() as conn:
        rows = conn.execute(select(agent_run_log).where(and_(*clauses))
                            .order_by(agent_run_log.c.created_at)
                            .limit(max(1, min(int(first or 500), 2000)))).mappings().all()
    return [agent_run_logs.serialize(dict(r), runner_name=obj.get("runnerName")) for r in rows]


# ------------------------------------------------------------------------------ launching
@mutation.field("agentRunLaunch")
def m_agent_run_launch(_obj, info, input):  # noqa: A002 - GraphQL argument name
    store, org, uid = _admin(info)
    # Through the issue resolvers, not the writer: extension hooks see every issue write
    # (tests/test_extensions.py holds that rule for the whole server).
    from hoja_server import resolvers

    ref = str(input.get("agentMemberId") or "").strip()
    with store.engine.connect() as conn:
        member = conn.execute(select(agent_member).where(and_(
            agent_member.c.organization_id == org,
            or_(agent_member.c.id == ref, agent_member.c.user_id == ref),
            agent_member.c.archived_at.is_(None),
        ))).mappings().first()
    if member is None:
        raise GraphQLError("No active agent member with that id.",
                           extensions={"code": "BAD_USER_INPUT", "field": "agentMemberId"})
    agent_user = member["user_id"]
    task_ref = str(input.get("taskId") or "").strip()
    if task_ref:
        current = store.issue_by_id_or_identifier(org, task_ref, viewer_id=uid)
        if current is None:
            raise GraphQLError("No such task.", extensions={"code": "NOT_FOUND", "field": "taskId"})
        with store.engine.connect() as conn:
            latest = agent_sessions.latest_for_issue(conn, org, current["id"])
        if current.get("_delegate_id") == agent_user and latest \
                and latest["state"] in agent_sessions.TERMINAL:
            # Same agent, last run over: clear and set again, which is what opens a new session.
            resolvers.m_issue_update(None, info, current["id"], {"delegateId": None})
        updated = resolvers.m_issue_update(None, info, current["id"], {"delegateId": agent_user})
        if not updated.get("success"):
            return {"success": False, "issue": None, "agentSession": None, "lease": None,
                    "queueReason": []}
        issue_id = current["id"]
    else:
        if not input.get("teamId") or not input.get("title"):
            raise GraphQLError("Give a taskId, or a teamId and a title to create one.",
                               extensions={"code": "BAD_USER_INPUT", "field": "taskId"})
        created = resolvers.m_issue_create(None, info, {
            "teamId": input["teamId"], "title": input["title"],
            "description": input.get("description"), "delegateId": agent_user})
        issue_id = (created.get("issue") or {}).get("id") if created.get("success") else None
        if not issue_id:
            return {"success": False, "issue": None, "agentSession": None, "lease": None,
                    "queueReason": []}
    with store.engine.connect() as conn:
        session = agent_sessions.latest_for_issue(conn, org, issue_id)
        lease = None
        if session and session.get("lease_id"):
            row = conn.execute(select(agent_lease).where(and_(
                agent_lease.c.organization_id == org,
                agent_lease.c.id == session["lease_id"]))).mappings().first()
            if row is not None:
                lease = agent_runners.serialize_lease(dict(row))
        payload = agent_sessions.serialize(conn, org, session) if session else None
        reasons = agent_sessions.parse_reasons(session.get("queue_reason")) if session else []
    return {"success": session is not None,
            "issue": store.issue_by_id_or_identifier(org, issue_id, viewer_id=uid),
            "agentSession": payload, "lease": lease, "queueReason": reasons}
