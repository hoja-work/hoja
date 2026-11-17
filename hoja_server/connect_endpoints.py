"""The Hoja Connect surface an admin screen reads (HOJA-602-A-10, WP09 read half).

``connect_api.py`` is the data plane's half: the runner reports an exposure over ``/runner``, the
server records it, and the lease's end closes it. This module is the other half — the read (and the
one control-plane write) that turns those rows into the Connect block on the Runners screen, and
retires ``SAMPLE_SURFACES.connect`` (WP13 slice 6).

Three properties, the same three every workspace-wide read in this codebase carries:

* **Admin-gated, refuse-don't-empty.** "What is my workspace exposing to the network" is a
  workspace-wide question, so a member is answered ``FORBIDDEN`` rather than an empty connection:
  an empty list reads as "nothing is exposed", which is a different and much more comfortable
  sentence than "you may not look".
* **Org-scoped twice.** The explicit ``organization_id`` predicate *and* the ``hoja_tenant`` policy
  (revision 71 classifies ``connect_endpoint`` as a direct tenant table). Under ``HOJA_TEST_RLS`` a
  cross-tenant read is therefore refused by the database too, not only by this file.
* **No secret is served.** The SDL has no address field, no join token and no lease token — the
  address never reaches the database in the first place (WP09's grep test), and a field that does
  not exist cannot be selected.

``connectEndpointClose`` is the one mutation here, and it is a *control-plane* action: it closes
the record (audited, ``connect.exposure.close``) and marks the exposure for revocation, which the
runner learns on its next heartbeat and answers by killing the process. Marking the row closed
first is deliberate — a screen must never keep offering "revoke" on a tunnel the workspace has
already withdrawn.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, desc, or_, select

from hoja_server import connect_api
from hoja_server.db import connect_endpoint, runner

query = QueryType()
mutation = MutationType()
connect_endpoint_type = ObjectType("ConnectEndpoint")

MAX_PAGE = 200

SDL = gql(
    """
    "One published port of one Hoja Connect exposure (WP09), as a review screen reads it."
    type ConnectEndpoint {
      id: ID!
      "The runner's own identifier for the serve process. Rows of one exposure share it."
      exposureId: String!
      "The machine whose tunnel this is."
      runnerId: ID!
      runnerName: String
      "The lease that opened it, or null for an exposure with no lease (legacy rows)."
      leaseId: ID
      "The session the lease executes, when the task was delegated."
      sessionId: ID
      "The agent the task belongs to. Null for an undeligated task, which has no policy."
      agentId: ID
      port: Int!
      "The host or label the exposure is published under."
      host: String
      "`nodekey:...`: the single pinned peer the tunnel accepts. Public key material, not a secret."
      pinnedPeer: String!
      "open | closed"
      state: String!
      "lease_finished | lease_canceled | lease_expired | runner_close | revoked"
      closeReason: String
      openedAt: DateTime!
      "The deadline the serve process was given: the lease's TTL at open time."
      expiresAt: DateTime
      closedAt: DateTime
      "Set when an admin revoked it; the runner kills the process on its next heartbeat."
      revokeRequestedAt: DateTime
    }

    type ConnectEndpointConnection { nodes: [ConnectEndpoint!]! pageInfo: PageInfo! }

    "The result of withdrawing one exposure: what changed, and the audit seq that says so."
    type ConnectEndpointCloseResult {
      success: Boolean!
      "Rows closed by this call: 0 when it was already closed."
      closed: Int!
      endpoints: [ConnectEndpoint!]!
      "The audit ledger's own seq for the close, or null when nothing changed."
      auditSeq: Int
    }

    extend type Query {
      "Every Connect endpoint in the workspace, newest first, admin only, paged."
      connectEndpoints(first: Int = 50, after: String, runnerId: String, leaseId: String,
                       live: Boolean): ConnectEndpointConnection!
      "One endpoint row by id, admin only. Null when this workspace has no such row."
      connectEndpoint(id: String!): ConnectEndpoint
    }

    extend type Mutation {
      "Revoke one exposure: closes its rows, audits the close, and tells the runner on its next heartbeat."
      connectEndpointClose(exposureId: String!): ConnectEndpointCloseResult!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org_id: str, user_id: str) -> dict:
    """The same gate ``runners`` and ``auditEvents`` use: refuse, never empty."""
    actor = store.get_user(org_id, user_id)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can read what the workspace is exposing.",
                           extensions={"code": "FORBIDDEN"})
    return actor


def _bad(field: str, message: str) -> None:
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_live(row: dict, *, now: datetime | None = None) -> bool:
    """``live`` is open *and* inside the TTL it was opened with.

    The second half is not a detail: a serve process that outlived its deadline is dead by its own
    timer (``ConnectSupervisor._expire``), so a row past ``expires_at`` must not be drawn as a live
    tunnel even before the close arrives.
    """
    if row["state"] != connect_api.OPEN:
        return False
    deadline = _parse_iso(row.get("expires_at"))
    if deadline is None:
        return True
    return deadline > (now or datetime.now(timezone.utc))


def serialize_endpoint(row: dict, *, runner_name: str | None = None) -> dict:
    return {
        "id": row["id"],
        "exposureId": row["exposure_id"],
        "runnerId": row["runner_id"],
        "runnerName": runner_name,
        "leaseId": row.get("lease_id"),
        "sessionId": row.get("session_id"),
        "agentId": row.get("agent_user_id"),
        "port": int(row["port"]),
        "host": row.get("host"),
        "pinnedPeer": row["pinned_peer"],
        "state": row["state"],
        "closeReason": row.get("close_reason"),
        "openedAt": row["opened_at"],
        "expiresAt": row.get("expires_at"),
        "closedAt": row.get("closed_at"),
        "revokeRequestedAt": row.get("revoke_requested_at"),
        "_row": row,
    }


def cursor_of(row: dict, key: str = "opened_at") -> str:
    """``<sort key>|<id>``: the id is in the cursor because two ports can open in one millisecond."""
    return f"{row[key]}|{row['id']}"


def _split_cursor(cursor: str) -> tuple[str, str]:
    stamp, _, row_id = str(cursor).partition("|")
    if not stamp or not row_id:
        raise GraphQLError("The cursor does not name a row in this workspace.",
                           extensions={"code": "BAD_USER_INPUT", "field": "after"})
    return stamp, row_id


def _page_size(first) -> int:
    return max(1, min(int(first or 50), MAX_PAGE))


def runner_names(conn, org_id: str, runner_ids: list[str]) -> dict[str, str]:
    if not runner_ids:
        return {}
    rows = conn.execute(select(runner.c.id, runner.c.name).where(and_(
        runner.c.organization_id == org_id,
        runner.c.id.in_(tuple(set(runner_ids))),
    ))).all()
    return {row[0]: row[1] for row in rows}


def read_endpoints(conn, org_id: str, *, first=50, after: str | None = None,
                   runner_id: str | None = None, lease_id: str | None = None,
                   live: bool | None = None, now: datetime | None = None) -> dict:
    """One bounded page of the workspace's Connect endpoints, newest first.

    ``live`` filters after the row is serialized, because liveness is open-plus-TTL rather than a
    column: the filter and the row must agree about the same tunnel, which is only guaranteed when
    one function answers both.
    """
    size = _page_size(first)
    stmt = select(connect_endpoint).where(connect_endpoint.c.organization_id == org_id)
    if runner_id:
        stmt = stmt.where(connect_endpoint.c.runner_id == runner_id)
    if lease_id:
        stmt = stmt.where(connect_endpoint.c.lease_id == lease_id)
    if live is True:
        # Narrowed in SQL for the same reason the status filter on ``runners`` is not: a page that
        # has to read every historical row to answer "what is live" stops being a page.
        stmt = stmt.where(connect_endpoint.c.state == connect_api.OPEN)
    if after:
        stamp, row_id = _split_cursor(after)
        stmt = stmt.where(or_(
            connect_endpoint.c.opened_at < stamp,
            and_(connect_endpoint.c.opened_at == stamp, connect_endpoint.c.id < row_id),
        ))
    rows = [dict(row) for row in conn.execute(
        stmt.order_by(desc(connect_endpoint.c.opened_at), desc(connect_endpoint.c.id))
        .limit(size + 1)).mappings().all()]
    has_next = len(rows) > size
    rows = rows[:size]
    names = runner_names(conn, org_id, [row["runner_id"] for row in rows])
    nodes = [serialize_endpoint(row, runner_name=names.get(row["runner_id"])) for row in rows]
    if live is not None:
        nodes = [node for node in nodes if is_live(node["_row"], now=now) is bool(live)]
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": bool(after),
            "startCursor": cursor_of(rows[0]) if rows else None,
            "endCursor": cursor_of(rows[-1]) if rows else None,
        },
    }


def read_endpoint(conn, org_id: str, endpoint_id: str) -> dict | None:
    row = conn.execute(select(connect_endpoint).where(and_(
        connect_endpoint.c.organization_id == org_id,
        connect_endpoint.c.id == endpoint_id,
    ))).mappings().first()
    if not row:
        return None
    item = dict(row)
    names = runner_names(conn, org_id, [item["runner_id"]])
    return serialize_endpoint(item, runner_name=names.get(item["runner_id"]))


@query.field("connectEndpoints")
def r_connect_endpoints(_obj, info, first=None, after=None, runnerId=None,  # noqa: N803
                        leaseId=None, live=None):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_endpoints(conn, org, first=first or 50, after=after, runner_id=runnerId,
                              lease_id=leaseId, live=live)


@query.field("connectEndpoint")
def r_connect_endpoint(_obj, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_endpoint(conn, org, id)


@mutation.field("connectEndpointClose")
def m_connect_endpoint_close(_obj, info, exposureId):  # noqa: N803 - GraphQL argument name
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    exposure = str(exposureId or "").strip()
    if not exposure:
        _bad("exposureId", "exposureId is required")
    with store.engine.connect() as conn:
        known = conn.execute(select(connect_endpoint.c.id).where(and_(
            connect_endpoint.c.organization_id == org,
            connect_endpoint.c.exposure_id == exposure,
        )).limit(1)).scalar()
    if not known:
        # A 404 rather than a success with zero rows: the screen asked to revoke something, and
        # "nothing happened" must not be reported as "done".
        raise GraphQLError("No exposure with that id exists in this workspace.",
                           extensions={"code": "NOT_FOUND", "field": "exposureId"})
    # The audit row is written inside ``revoke_exposure``; it hands back the ledger seq so the
    # caller can point at the entry instead of this file inventing one.
    closed, seq = connect_api.revoke_exposure(
        store.engine, organization_id=org, exposure_id=exposure,
        actor_kind="human", actor_id=uid)
    with store.engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(select(connect_endpoint).where(and_(
            connect_endpoint.c.organization_id == org,
            connect_endpoint.c.exposure_id == exposure,
        )).order_by(connect_endpoint.c.port)).mappings().all()]
        names = runner_names(conn, org, [row["runner_id"] for row in rows]) if rows else {}
    return {
        "success": True,
        "closed": len(closed),
        "endpoints": [serialize_endpoint(row, runner_name=names.get(row["runner_id"]))
                      for row in rows],
        "auditSeq": seq,
    }
