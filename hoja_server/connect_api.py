"""The server record of Hoja Connect exposures (HOJA-602-A-10, WP09).

WP09 shipped the data plane in PR #215: one ``tailcat serve`` per exposure, a fresh key per lease
(``--key=new``), a single pinned peer (``--allow=nodekey:<worker>``), an explicit port allowlist and
a TTL tied to the lease. That is everything a *tunnel* needs and nothing a *person* can read, so
the Connect screen drew a fixture and an org admin could not answer "what is exposed right now?"
without opening the database. This module is the record, reported by the runner over the same
``/runner`` surface it already drives.

Four properties are load-bearing, and each one is why the code is shaped the way it is:

* **The fence is the authority.** An open or a close carries the lease id, the fence and the lease
  token, and goes through ``runner_api._resolve_lease`` — the same check every lease route uses. A
  zombie run holding a superseded fence is refused with ``409 stale_fence``; it cannot publish a
  port and it cannot close somebody else's exposure.
* **The policy decides, not the runner.** ``agent_policy.decide_connect`` is asked, per port, for
  the agent the lease's task is delegated to. A policy that does not name ``(host, port)`` refuses
  the whole call — a partial allowlist would leave the runner guessing which half survived. A
  lease on a task nobody delegated has no policy at all, and deny-by-default is the only answer
  that is not an invented grant.
* **The kill switch is the workspace's.** ``workspace_security_policy.connect_enabled`` is the org
  switch WP09 names ("an org admin can turn Connect off"). NULL — the state of every workspace that
  predates revision 71 — reads as *enabled*.
* **Nothing secret is stored.** The tailcat address is a bearer secret and never leaves the runner
  (WP09's grep test). The only identity recorded is the pin (``nodekey:<64 hex>``), which is public
  key material and is the whole ACL.

Closing is deliberately over-determined. A runner closes its own exposure when the process dies;
the control plane closes it when the lease ends (``close_for_lease``), when an admin revokes it
(``connect_endpoints.connectEndpointClose``, audited), and reports the pending revocation on the
next heartbeat so the runner kills the process rather than merely forgetting it. "It is not in the
list" was never an audit trail.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from hoja_server import agent_policy, audit, runner_api, tenant_context
from hoja_server.db import (
    connect_endpoint,
    new_id,
    now_iso,
    workspace_security_policy,
)

LOG = logging.getLogger("hoja_server.connect")

# ---------------------------------------------------------------------------
# Wire contract. Mirrored in runner/hoja_runnerd/protocol.py, and asserted equal
# by tests/agent/test_runner_conformance.py: the runner package must not import
# the server, so equality is checked rather than shared.
# ---------------------------------------------------------------------------
ROUTE_OPEN = "/runner/connect/open"
ROUTE_CLOSE = "/runner/connect/close"

ERROR_DISABLED = "connect_disabled"
ERROR_NOT_GRANTED = "connect_not_granted"
ERROR_PEER_INVALID = "connect_peer_invalid"
ERROR_PORT = "connect_port_invalid"
ERROR_EXISTS = "connect_exposure_exists"
ERROR_UNKNOWN = "connect_exposure_unknown"

# The supervisor caps a lease at an hour (connect.py MAX_TTL_SECONDS); a record that outlives that
# would describe a tunnel the runner itself refuses to keep.
MAX_TTL_S = 3600
MAX_PORTS = 16

OPEN = "open"
CLOSED = "closed"

CLOSE_RUNNER = "runner_close"
CLOSE_LEASE_FINISHED = "lease_finished"
CLOSE_LEASE_CANCELED = "lease_canceled"
CLOSE_LEASE_EXPIRED = "lease_expired"
CLOSE_REVOKED = "revoked"

# Public key material, and exactly what `--allow=` accepts (connect.py `_NODE_KEY_RE`).
_PEER_RE = re.compile(r"^nodekey:[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _ports(body: Mapping[str, Any]) -> list[int]:
    """The requested ports, validated as a bounded, deduplicated, ordered list."""
    raw = body.get("ports")
    if raw is None and body.get("port") is not None:
        raw = [body.get("port")]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, "ports is required: the explicit allowlist to publish")
    if len(raw) > MAX_PORTS:
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, f"at most {MAX_PORTS} ports per exposure")
    ports: list[int] = []
    for value in raw:
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise runner_api.LeaseRequestError(
                400, ERROR_PORT, f"port {value!r} is not an integer") from None
        if port < 1 or port > 65535:
            raise runner_api.LeaseRequestError(
                400, ERROR_PORT, f"port {port} is outside 1-65535")
        if port not in ports:
            ports.append(port)
    return ports


def _pin(body: Mapping[str, Any]) -> str:
    """The pinned peer. A malformed pin is refused rather than stored: the pin *is* the ACL."""
    value = str(body.get("pinned_peer") or body.get("pinnedPeer") or "").strip().lower()
    if not value:
        raise runner_api.LeaseRequestError(
            400, ERROR_PEER_INVALID,
            "pinned_peer is required: Hoja supplies the worker's node key, never a wildcard")
    if not _PEER_RE.match(value):
        raise runner_api.LeaseRequestError(
            400, ERROR_PEER_INVALID,
            "pinned_peer must be nodekey:<64 lowercase hex>; a wildcard peer is never accepted")
    return value


def _host(body: Mapping[str, Any]) -> str:
    value = str(body.get("host") or body.get("label") or "").strip()
    if not value:
        # `agent_policy.clean_connect` makes the same demand of a policy entry, so an empty host
        # could never match a grant: refusing here says why instead of refusing later, vaguely.
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS,
            "host is required: a Connect grant names the host it exposes")
    if len(value) > 200:
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, "host must be at most 200 characters")
    return value


def connect_enabled(conn, org_id: str) -> bool:
    """The workspace kill switch. NULL and a missing row both read as enabled."""
    value = conn.execute(select(workspace_security_policy.c.connect_enabled).where(
        workspace_security_policy.c.organization_id == org_id)).scalar()
    return value is not False


def _policy_for_lease(conn, org_id: str, lease: Mapping[str, Any]):
    """The policy view that governs this lease's agent, or None when nobody delegated it."""
    agent_user_id = agent_policy.agent_user_for_task(conn, org_id, str(lease.get("task_id") or ""))
    if not agent_user_id:
        return None, None
    _member, view = agent_policy.view_for_user(conn, org_id, agent_user_id)
    return agent_user_id, view


def _refuse(port: int, host: str, decision) -> None:
    raise runner_api.LeaseRequestError(
        403, ERROR_NOT_GRANTED,
        f"agent policy does not grant a Connect exposure on {host}:{port}; "
        f"the missing grant is connect + {{{host}, {port}}}",
        port=port,
        host=host,
        rule=decision.rule,
    )


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


def open_exposure(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any],
                  request=None) -> dict[str, Any]:
    """Record a newly opened exposure. Every refusal names the grant that is missing."""
    lease_id, fence, lease_token = runner_api._lease_fields(body, request)
    org_id = runner_row["organization_id"]
    ports = _ports(body)
    pinned_peer = _pin(body)
    host = _host(body)
    exposure_id = str(body.get("exposure_id") or "").strip()
    if not exposure_id:
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, "exposure_id is required")
    if len(exposure_id) > 120:
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, "exposure_id must be at most 120 characters")
    try:
        requested_ttl = int(body.get("ttl_s") or 0)
    except (TypeError, ValueError):
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, "ttl_s must be an integer") from None

    stamp = _now()
    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            if not connect_enabled(conn, org_id):
                raise runner_api.LeaseRequestError(
                    403, ERROR_DISABLED,
                    "an admin turned Connect off for this workspace; no exposure can be opened")
            lease = runner_api._resolve_lease(conn, runner_row, lease_id, fence, lease_token)
            agent_user_id, view = _policy_for_lease(conn, org_id, lease)
            # Deny by default, and say which half is missing: WP09's allowlist is the policy's, so
            # a lease with no policy has no allowlist to publish against.
            for port in ports:
                if view is None:
                    raise runner_api.LeaseRequestError(
                        403, ERROR_NOT_GRANTED,
                        "this task has no delegated agent, so no policy grants a Connect "
                        f"exposure on {host}:{port}",
                        port=port, host=host, rule="no-policy")
                granted = agent_policy.decide_connect(view, host, port)
                if not granted.allowed:
                    _refuse(port, host, granted)
            existing = [dict(row) for row in conn.execute(select(connect_endpoint).where(and_(
                connect_endpoint.c.organization_id == org_id,
                connect_endpoint.c.exposure_id == exposure_id,
                connect_endpoint.c.state == OPEN,
            ))).mappings().all()]
            session = _session_for_lease(conn, org_id, lease)
        if existing:
            # A retry after a lost response is the same exposure, not a second one. A *different*
            # pin under the same id is a different tunnel and must not be silently merged.
            if (sorted(row["port"] for row in existing) == sorted(ports)
                    and {row["pinned_peer"] for row in existing} == {pinned_peer}):
                return _exposure_payload(exposure_id, existing, duplicate=True)
            raise runner_api.LeaseRequestError(
                409, ERROR_EXISTS,
                f"exposure {exposure_id} is already open with a different pin or port set")

        ttl = requested_ttl or int(lease.get("ttl_seconds") or runner_api.LEASE_TTL_S)
        ttl = max(1, min(ttl, MAX_TTL_S))
        expires_at = _iso(stamp + timedelta(seconds=ttl))
        stamp_iso = _iso(stamp)
        rows = []
        for port in ports:
            row = {
                "id": new_id(),
                "organization_id": org_id,
                "exposure_id": exposure_id,
                "runner_id": runner_row["id"],
                "lease_id": lease["id"],
                "session_id": (session or {}).get("id"),
                "agent_user_id": agent_user_id,
                "port": port,
                "host": host,
                "pinned_peer": pinned_peer,
                "state": OPEN,
                "close_reason": None,
                "opened_at": stamp_iso,
                "expires_at": expires_at,
                "closed_at": None,
                "revoke_requested_at": None,
                "policy_version": (view.version if view is not None else None),
                "created_at": stamp_iso,
                "updated_at": stamp_iso,
            }
            rows.append(row)
        try:
            with engine.begin() as conn:
                conn.execute(connect_endpoint.insert(), rows)
        except IntegrityError as exc:  # pragma: no cover - the read above wins the race
            raise runner_api.LeaseRequestError(
                409, ERROR_EXISTS, f"exposure {exposure_id} was opened concurrently") from exc

    _audit_open(engine, org_id, lease, runner_row, rows)
    return _exposure_payload(exposure_id, rows, duplicate=False)


def _session_for_lease(conn, org_id: str, lease: Mapping[str, Any]) -> dict | None:
    """The session this lease executes, when the task was delegated (WP06 owns the row)."""
    from hoja_server import agent_verify

    try:
        return agent_verify.session_for_lease(conn, org_id, dict(lease))
    except Exception:  # pragma: no cover - the record matters more than the grouping column
        LOG.warning("could not resolve the session for lease %s", lease.get("id"), exc_info=True)
        return None


def _exposure_payload(exposure_id: str, rows: list[Mapping[str, Any]], *,
                      duplicate: bool) -> dict[str, Any]:
    first = rows[0]
    return {
        "exposure_id": exposure_id,
        "state": first["state"],
        "ports": sorted(int(row["port"]) for row in rows),
        "host": first["host"],
        "pinned_peer": first["pinned_peer"],
        "opened_at": first["opened_at"],
        "expires_at": first["expires_at"],
        "duplicate": duplicate,
    }


def _audit_open(engine, org_id: str, lease: Mapping[str, Any], runner_row: Mapping[str, Any],
                rows: list[Mapping[str, Any]]) -> None:
    """One audit row per exposure. A failure to audit never fails the open."""
    try:
        audit.record(
            engine,
            organization_id=org_id,
            action="connect.exposure.open",
            actor_kind="bot",
            actor_id=str(runner_row.get("id") or "runner"),
            target_kind="connect_endpoint",
            target_id=str(rows[0]["exposure_id"]),
            runner=str(runner_row.get("name") or ""),
            detail={
                "exposure_id": rows[0]["exposure_id"],
                "lease_id": lease.get("id"),
                "fence": lease.get("fence"),
                "host": rows[0]["host"],
                "ports": sorted(int(row["port"]) for row in rows),
                # The pin is an identifier, and the address is never here: WP09's grep test is
                # what keeps this true rather than a promise.
                "pinned_peer": rows[0]["pinned_peer"],
                "expires_at": rows[0]["expires_at"],
                "policy_version": rows[0]["policy_version"],
            },
        )
    except Exception:
        LOG.warning("could not audit the open of exposure %s", rows[0]["exposure_id"],
                    exc_info=True)


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


def _close_rows(engine, org_id: str, where, *, reason: str,
                actor: tuple[str, str] | None = None, revoke: bool = False) -> list[dict]:
    """Close every matching open row, in one transaction, and return what changed."""
    stamp = now_iso()
    values: dict[str, Any] = {"state": CLOSED, "close_reason": reason,
                              "closed_at": stamp, "updated_at": stamp}
    if revoke:
        values["revoke_requested_at"] = stamp
    with tenant_context.scoped(org_id), engine.begin() as conn:
        closed = [dict(row) for row in conn.execute(select(connect_endpoint).where(and_(
            connect_endpoint.c.organization_id == org_id,
            connect_endpoint.c.state == OPEN,
            where,
        ))).mappings().all()]
        if closed:
            conn.execute(connect_endpoint.update().where(and_(
                connect_endpoint.c.organization_id == org_id,
                connect_endpoint.c.state == OPEN,
                where,
            )).values(**values))
    return closed


def close_exposure(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any],
                   request=None) -> dict[str, Any]:
    """Close an exposure the runner has already killed (or is about to)."""
    lease_id, fence, lease_token = runner_api._lease_fields(body, request)
    org_id = runner_row["organization_id"]
    exposure_id = str(body.get("exposure_id") or "").strip()
    if not exposure_id:
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS, "exposure_id is required")
    reason = str(body.get("reason") or CLOSE_RUNNER)[:80]
    if reason not in (CLOSE_RUNNER, CLOSE_LEASE_FINISHED, CLOSE_LEASE_CANCELED,
                      CLOSE_LEASE_EXPIRED, CLOSE_REVOKED):
        raise runner_api.LeaseRequestError(
            400, runner_api.ERROR_BAD_PARAMS,
            f"reason must be one of {CLOSE_RUNNER}, {CLOSE_LEASE_FINISHED}, "
            f"{CLOSE_LEASE_CANCELED}, {CLOSE_LEASE_EXPIRED}, {CLOSE_REVOKED}")
    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            # The fence check is what stops a superseded attempt from closing the live attempt's
            # exposure. It runs *before* the where-clause below, so the refusal is a refusal and
            # not a silent no-op.
            lease = runner_api._resolve_lease(conn, runner_row, lease_id, fence, lease_token)
            known = conn.execute(select(connect_endpoint.c.id).where(and_(
                connect_endpoint.c.organization_id == org_id,
                connect_endpoint.c.exposure_id == exposure_id,
            )).limit(1)).scalar()
        if not known:
            raise runner_api.LeaseRequestError(
                404, ERROR_UNKNOWN, f"exposure {exposure_id} is not known to this workspace")
        closed = _close_rows(engine, org_id, and_(
            connect_endpoint.c.exposure_id == exposure_id,
            connect_endpoint.c.lease_id == lease["id"],
        ), reason=reason)
        # The runner saying "it is gone" is the acknowledgement the heartbeat waits for, so the
        # flag is cleared whether or not this call is the one that closed the row (an admin's
        # revoke closes it first, and the process dies second).
        _clear_revocation(engine, org_id, exposure_id, lease["id"])
    if closed:
        _audit_close(engine, org_id, closed[0], reason=reason, actor=(
            "bot", str(runner_row.get("id") or "runner")), runner_row=runner_row)
    return {
        "exposure_id": exposure_id,
        "state": CLOSED,
        "closed": len(closed),
        "reason": reason,
        # A second close is the same close: the runner's TTL timer and the control plane's lease
        # end can both be true, and neither should fail the other.
        "duplicate": not closed,
    }


def _clear_revocation(engine, org_id: str, exposure_id: str, lease_id: str) -> None:
    """Mark the revoke delivered. ``pending_revocations`` reports what this has not cleared."""
    stamp = now_iso()
    with tenant_context.scoped(org_id), engine.begin() as conn:
        conn.execute(connect_endpoint.update().where(and_(
            connect_endpoint.c.organization_id == org_id,
            connect_endpoint.c.exposure_id == exposure_id,
            connect_endpoint.c.lease_id == lease_id,
            connect_endpoint.c.revoke_requested_at.is_not(None),
        )).values(revoke_requested_at=None, updated_at=stamp))


def close_for_lease(engine, lease: Mapping[str, Any], *, reason: str) -> list[str]:
    """Close every exposure a lease opened. Called when the lease ends — it cannot outlive it.

    A serve process whose lease is over is a tunnel nobody is accountable for, so this is a write
    and not a filter on the read path: the row says ``closed`` with the reason, and the screen, the
    audit log and the next runner all read the same fact.
    """
    org_id = str(lease.get("organization_id") or "")
    lease_id = str(lease.get("id") or lease.get("lease_id") or "")
    if not org_id or not lease_id:
        return []
    try:
        closed = _close_rows(engine, org_id, connect_endpoint.c.lease_id == lease_id, reason=reason)
        for row in closed:
            _audit_close(engine, org_id, row, reason=reason,
                         actor=("bot", str(lease.get("runner_id") or "runner")))
        return sorted({str(row["exposure_id"]) for row in closed})
    except Exception:  # the lease transition already committed; never fail it from here
        LOG.warning("could not close the Connect exposures of lease %s", lease_id, exc_info=True)
        return []


def _audit_close(engine, org_id: str, row: Mapping[str, Any], *, reason: str,
                 actor: tuple[str, str],
                 runner_row: Mapping[str, Any] | None = None) -> int | None:
    try:
        kind, who = actor
        return audit.record(
            engine,
            organization_id=org_id,
            action="connect.exposure.close",
            actor_kind=kind,
            actor_id=who,
            target_kind="connect_endpoint",
            target_id=str(row.get("exposure_id") or row.get("id") or ""),
            runner=str((runner_row or {}).get("name") or ""),
            detail={
                "exposure_id": row.get("exposure_id"),
                "lease_id": row.get("lease_id"),
                "port": row.get("port"),
                "reason": reason,
            },
        )
    except Exception:
        LOG.warning("could not audit the close of exposure %s", row.get("exposure_id"),
                    exc_info=True)
        return None


def revoke_exposure(engine, *, organization_id: str, exposure_id: str,
                    actor_kind: str, actor_id: str) -> tuple[list[dict], int | None]:
    """An admin withdrew an exposure: close the rows and tell the runner on its next beat.

    Returns the rows that changed and the audit ``seq`` of the close (None when nothing changed),
    so the caller can point at the ledger entry rather than invent one.
    """
    closed = _close_rows(engine, organization_id,
                         connect_endpoint.c.exposure_id == exposure_id,
                         reason=CLOSE_REVOKED, revoke=True)
    seq = None
    if closed:
        seq = _audit_close(engine, organization_id, closed[0], reason=CLOSE_REVOKED,
                           actor=(actor_kind, actor_id))
    return closed, seq


def pending_revocations(engine, runner_row: Mapping[str, Any]) -> list[str]:
    """Exposures this runner must kill: the control plane closed them and the process may live.

    The heartbeat is where WP07 already delivers an instruction to a runner in flight (cancel), so
    it is where this one travels too. "Pending" is cleared by the runner's own
    ``/runner/connect/close`` (``_clear_revocation``), so a runner that never answers keeps being
    told — which is the right amount of nagging for a process that is still listening on a port.
    Bounded by the exposure count of one runner.
    """
    org_id = runner_row["organization_id"]
    with tenant_context.scoped(org_id), engine.connect() as conn:
        rows = conn.execute(select(connect_endpoint.c.exposure_id).where(and_(
            connect_endpoint.c.organization_id == org_id,
            connect_endpoint.c.runner_id == runner_row["id"],
            connect_endpoint.c.revoke_requested_at.is_not(None),
            connect_endpoint.c.closed_at.is_not(None),
        )).limit(64)).scalars().all()
    return sorted({str(value) for value in rows})


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------


def connect_routes(engine) -> list:
    """The two routes, as Starlette ``Route``s for ``create_app``."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def open_(request):
        try:
            runner_row = runner_api._runner_from(request, engine)
        except runner_api.LeaseRequestError as rejected:
            return rejected.response()
        body = await runner_api._json_body(request)
        return await run_in_threadpool(
            runner_api._run,
            lambda: JSONResponse(open_exposure(engine, runner_row, body, request)))

    async def close_(request):
        try:
            runner_row = runner_api._runner_from(request, engine)
        except runner_api.LeaseRequestError as rejected:
            return rejected.response()
        body = await runner_api._json_body(request)
        return await run_in_threadpool(
            runner_api._run,
            lambda: JSONResponse(close_exposure(engine, runner_row, body, request)))

    return [
        Route(ROUTE_OPEN, open_, methods=["POST"]),
        Route(ROUTE_CLOSE, close_, methods=["POST"]),
    ]
