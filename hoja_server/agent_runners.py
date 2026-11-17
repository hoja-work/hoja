"""The runner and lease read surface a review screen needs (HOJA-602-A-16, WP13 slice 5).

WP07 (``runner_api.py``) is a REST data plane: a runner registers, long-polls for a
lease, heartbeats, and finishes. That is everything a *runner* needs, and nothing a
*person* can read. Until this module there was no way for an admin screen to answer
"which machines are enrolled, are they alive, and what are they holding?" without
opening the database, so the Runners screen drew a fixture (``SAMPLE_SURFACES.runners``).

Two properties decide the shape of this read:

* **Read-only.** There is no mutation here. Revoking a runner is ``revoke_runner`` in
  ``runner_api.py`` and enrolment is an operator action; a review surface that could
  also disable a machine would be two authorities on one fact.
* **The same gate as ``workspaceAgentSessions``.** Every runner in a workspace is a
  workspace-wide view, so a non-admin is *refused* rather than handed an empty
  connection: an empty list reads as "this workspace has no machines", which is a
  different and wrong sentence. The explicit ``organization_id`` predicate and RLS
  both apply, the same belt-and-braces ``audit.read_events`` uses.

What is deliberately absent: the runner's ``lease_token_hash`` (a credential column —
FR-018) and ``token_hint`` (a prefix of that credential). A screen has no use for
either, so neither is in the SDL and neither can leak from here.
"""
from __future__ import annotations

import json
from datetime import datetime

from ariadne import ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, desc, or_, select

from hoja_server import runner_api
from hoja_server.db import agent_lease, issue, runner

query = QueryType()
runner_type = ObjectType("Runner")
agent_lease_type = ObjectType("AgentLease")

# Bounded, like the audit ledger's page: an admin screen pages, it never asks for
# every machine ever enrolled.
MAX_PAGE = 200

# A runner is online while its last heartbeat is inside one lease TTL plus the
# reaper's grace — the same window after which the reaper declares its lease lost
# (``runner_api.REAP_GRACE_S``). Reading liveness off the same clock the reaper uses
# is what keeps this screen from disagreeing with the server about a dead machine.
ONLINE_WINDOW_S = runner_api.LEASE_TTL_S + runner_api.REAP_GRACE_S

ONLINE = "online"
OFFLINE = "offline"

SDL = gql(
    """
    "One enrolled machine, as a review screen reads it: never its credential."
    type Runner {
      id: ID!
      "The runner's own name, unique per workspace (uq_runner_org_name)."
      name: String!
      "The workspace the row belongs to: the request's own tenant."
      organizationId: ID!
      "online | offline — derived from lastHeartbeatAt on Hoja's clock, never the runner's."
      status: String!
      "False once an admin disabled it or it was revoked; a disabled runner is refused at the lease route."
      enabled: Boolean!
      "When an admin revoked it, or null."
      revokedAt: DateTime
      "The operator's free-form labels (pool, host, region), as registered."
      labels: [String!]!
      "The harnesses this runner declared at registration: the allowlist the lease route enforces."
      harnesses: [String!]!
      "The last heartbeat or long-poll Hoja recorded; null before the first one."
      lastHeartbeatAt: DateTime
      createdAt: DateTime!
      "The lease this runner is holding right now, or null when it holds none."
      currentLease: AgentLease
    }

    "One grant of one task to one runner, under a monotonic fence (WP07)."
    type AgentLease {
      id: ID!
      taskId: ID!
      "The task this lease was opened for, in the issue page's own vocabulary."
      issue: Issue
      runnerId: ID
      runnerName: String
      "Monotonic per task; a stale fence is refused with 409 on every route (FR-018)."
      fence: Int!
      "The run identity the daemon journals and keys its effects by."
      runId: String!
      "queued | active | finished | released | canceled | lost"
      state: String!
      attempt: Int!
      harness: String!
      ttlSeconds: Int!
      "Hoja's deadline for this grant, not the runner's."
      leaseUntil: DateTime
      grantedAt: DateTime
      lastHeartbeatAt: DateTime
      "completed | canceled | failed, once the runner finished."
      finishState: String
      finishedAt: DateTime
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    type RunnerConnection { nodes: [Runner!]! pageInfo: PageInfo! }
    type AgentLeaseConnection { nodes: [AgentLease!]! pageInfo: PageInfo! }

    extend type Query {
      "Every runner in the workspace, newest first, admin only, paged. `after` is the connection's `endCursor`."
      runners(first: Int = 50, after: String, status: String): RunnerConnection!
      "One runner, admin only. Null when this workspace has no such machine."
      runner(id: String!): Runner
      "Every lease in the workspace, newest first, admin only, paged. Filter by runner, state or task."
      agentLeases(first: Int = 50, after: String, runnerId: String, state: String,
                  taskId: String): AgentLeaseConnection!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org_id: str, user_id: str) -> None:
    """The ``workspaceAgentSessions`` gate, for the same reason.

    An empty connection would read as "nothing is enrolled", so a non-admin is
    refused outright instead of being shown an empty list.
    """
    actor = store.get_user(org_id, user_id)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can read the workspace's runners.",
                           extensions={"code": "FORBIDDEN"})


def _bad(field: str, message: str) -> None:
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def runner_status(row: dict, *, lease: dict | None = None,
                  now: datetime | None = None) -> str:
    """``online`` iff Hoja has evidence of life inside ``ONLINE_WINDOW_S``.

    Two facts are evidence, and both are Hoja's own: the recorded heartbeat
    (``last_seen``, written by the heartbeat route and — since HOJA-602-A-20 — by
    the lease long-poll, rate limited to one write per heartbeat interval) and a
    lease the runner is holding right now. The second one matters because a machine
    that has just been granted work has no heartbeat yet — and a row that said
    "offline" while showing the lease it holds would be contradicting itself. Both
    converge within one window, and a runner with no work at all is kept fresh by
    its polls, which is the only evidence an idle machine can give.

    A revoked or disabled runner is offline whatever either says: the lease route
    refuses its token (``_runner_from``), so drawing it as online would promise work
    it can never take.
    """
    if not row.get("enabled") or row.get("revoked_at"):
        return OFFLINE
    if lease is not None:
        return ONLINE
    seen = _parse_iso(row.get("last_seen"))
    if seen is None:
        return OFFLINE
    now = now or datetime.now(seen.tzinfo)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=now.tzinfo)
    return ONLINE if (now - seen).total_seconds() <= ONLINE_WINDOW_S else OFFLINE


def _json_list(value) -> list[str]:
    """The ``labels``/``harnesses`` columns are JSON text; an unreadable value is empty.

    A hand-edited column must not fail a whole screen, and "no harnesses" is what the
    lease route already does with a shape it cannot parse.
    """
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, dict):
        return sorted(str(key) for key in parsed)
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def serialize_lease(row: dict, *, runner_name: str | None = None) -> dict:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "runnerId": row.get("runner_id"),
        "runnerName": runner_name,
        "fence": int(row.get("fence") or 1),
        "runId": row["run_id"],
        "state": row["state"],
        "attempt": int(row.get("attempt") or 1),
        "harness": row["harness"],
        "ttlSeconds": int(row.get("ttl_seconds") or 0),
        "leaseUntil": row.get("lease_until"),
        "grantedAt": row.get("granted_at"),
        "lastHeartbeatAt": row.get("last_heartbeat_at"),
        "finishState": row.get("finish_state"),
        "finishedAt": row.get("finished_at"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "_row": row,
    }


def serialize_runner(row: dict, *, lease: dict | None = None,
                     now: datetime | None = None) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "organizationId": row["organization_id"],
        "status": runner_status(row, lease=lease, now=now),
        "enabled": bool(row.get("enabled")),
        "revokedAt": row.get("revoked_at"),
        "labels": _json_list(row.get("labels")),
        "harnesses": _json_list(row.get("harnesses")),
        "lastHeartbeatAt": row.get("last_seen"),
        "createdAt": row["created_at"],
        "currentLease": serialize_lease(lease, runner_name=row["name"]) if lease else None,
        "_row": row,
    }


def cursor_of(row: dict, key: str = "created_at") -> str:
    """``<sort key>|<id>``: the id is in the cursor because two rows can share a
    timestamp, and a timestamp-only cursor repeats or skips one at the boundary.
    """
    return f"{row[key]}|{row['id']}"


def _split_cursor(cursor: str) -> tuple[str, str]:
    stamp, _, row_id = str(cursor).partition("|")
    if not stamp or not row_id:
        raise GraphQLError("The cursor does not name a row in this workspace.",
                           extensions={"code": "BAD_USER_INPUT", "field": "after"})
    return stamp, row_id


def _page_size(first) -> int:
    return max(1, min(int(first or 50), MAX_PAGE))


def read_runner(conn, org_id: str, runner_id: str) -> dict | None:
    """One runner, with the lease it is holding."""
    row = conn.execute(select(runner).where(and_(
        runner.c.organization_id == org_id,
        runner.c.id == runner_id,
    ))).mappings().first()
    if not row:
        return None
    item = dict(row)
    return serialize_runner(item, lease=current_leases(conn, org_id, [item["id"]]).get(item["id"]))


def read_runners(conn, org_id: str, *, first=50, after: str | None = None,
                 status: str | None = None) -> dict:
    """One bounded page of the workspace's runners, newest first.

    The status filter is applied after the derived ``online``/``offline`` value is
    computed, because it is derived rather than stored: it is the heartbeat window,
    not a column a client could filter on. That means a filtered page reads its own
    rows first — the window is the whole workspace's runner count, which is a bounded
    number of machines by construction.
    """
    size = _page_size(first)
    stmt = select(runner).where(runner.c.organization_id == org_id)
    if after:
        stamp, row_id = _split_cursor(after)
        stmt = stmt.where(or_(
            runner.c.created_at < stamp,
            and_(runner.c.created_at == stamp, runner.c.id < row_id),
        ))
    rows = [dict(row) for row in conn.execute(
        stmt.order_by(desc(runner.c.created_at), desc(runner.c.id))
        .limit(size + 1)).mappings().all()]
    has_next = len(rows) > size
    rows = rows[:size]
    leases = current_leases(conn, org_id, [row["id"] for row in rows])
    nodes = [serialize_runner(row, lease=leases.get(row["id"])) for row in rows]
    if status:
        wanted = str(status).strip().lower()
        if wanted not in (ONLINE, OFFLINE):
            _bad("status", f"status must be {ONLINE} or {OFFLINE}")
        # Filtered on the serialized row, so the lease that makes a machine online is part of the
        # answer: the filter and the row cannot disagree about the same machine.
        nodes = [node for node in nodes if node["status"] == wanted]
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": bool(after),
            "startCursor": cursor_of(rows[0]) if rows else None,
            "endCursor": cursor_of(rows[-1]) if rows else None,
        },
    }


def current_leases(conn, org_id: str, runner_ids: list[str]) -> dict[str, dict]:
    """The live lease of each runner in one query, not one query per row.

    ``active`` is the only state a runner is holding work in: a ``queued`` lease is
    not granted to it yet, and every other state is over.
    """
    if not runner_ids:
        return {}
    rows = conn.execute(select(agent_lease).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.runner_id.in_(tuple(runner_ids)),
        agent_lease.c.state == runner_api.LEASE_ACTIVE,
    )).order_by(desc(agent_lease.c.granted_at), desc(agent_lease.c.created_at))).mappings().all()
    found: dict[str, dict] = {}
    for row in rows:
        # Newest first, so the first row seen per runner is the one it holds.
        found.setdefault(row["runner_id"], dict(row))
    return found


def read_leases(conn, org_id: str, *, first=50, after: str | None = None,
                runner_id: str | None = None, state: str | None = None,
                task_id: str | None = None) -> dict:
    """One bounded page of the workspace's leases, newest first."""
    size = _page_size(first)
    stmt = select(agent_lease).where(agent_lease.c.organization_id == org_id)
    if runner_id:
        stmt = stmt.where(agent_lease.c.runner_id == runner_id)
    if state:
        stmt = stmt.where(agent_lease.c.state == state)
    if task_id:
        stmt = stmt.where(agent_lease.c.task_id == task_id)
    if after:
        stamp, row_id = _split_cursor(after)
        stmt = stmt.where(or_(
            agent_lease.c.created_at < stamp,
            and_(agent_lease.c.created_at == stamp, agent_lease.c.id < row_id),
        ))
    rows = [dict(row) for row in conn.execute(
        stmt.order_by(desc(agent_lease.c.created_at), desc(agent_lease.c.id))
        .limit(size + 1)).mappings().all()]
    has_next = len(rows) > size
    rows = rows[:size]
    names = runner_names(conn, org_id, [row["runner_id"] for row in rows if row.get("runner_id")])
    return {
        "nodes": [serialize_lease(row, runner_name=names.get(row.get("runner_id")))
                  for row in rows],
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": bool(after),
            "startCursor": cursor_of(rows[0]) if rows else None,
            "endCursor": cursor_of(rows[-1]) if rows else None,
        },
    }


def runner_names(conn, org_id: str, runner_ids: list[str]) -> dict[str, str]:
    if not runner_ids:
        return {}
    rows = conn.execute(select(runner.c.id, runner.c.name).where(and_(
        runner.c.organization_id == org_id,
        runner.c.id.in_(tuple(set(runner_ids))),
    ))).all()
    return {row[0]: row[1] for row in rows}


@query.field("runners")
def r_runners(_obj, info, first=None, after=None, status=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_runners(conn, org, first=first or 50, after=after, status=status)


@query.field("runner")
def r_runner(_obj, info, id):  # noqa: A002 - GraphQL argument name
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_runner(conn, org, id)


@query.field("agentLeases")
def r_agent_leases(_obj, info, first=None, after=None, runnerId=None, state=None,
                   taskId=None):  # noqa: N803 - GraphQL argument names
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_leases(conn, org, first=first or 50, after=after,
                           runner_id=runnerId, state=state, task_id=taskId)


@agent_lease_type.field("issue")
def r_lease_issue(obj, info):
    store, org, uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["taskId"], uid)


@runner_type.field("currentLease")
def r_current_lease(obj, info):
    """Already resolved by ``read_runners``: one query per page, not one per row."""
    return obj.get("currentLease")
