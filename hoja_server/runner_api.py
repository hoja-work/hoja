"""Runner lease API: register, lease, heartbeat, events, finish (HOJA-602-A-6, WP07).

The customer's machine is the data plane and nothing dials into it (plan.md §1).
A runner enrolls once, then long-polls for work, heartbeats while it holds a
lease, streams activity, and finishes with a claim. This module is the server
half of that contract; ``runner/hoja_runnerd/protocol.py`` is the shared,
contract-first definition of the wire and ``runner/hoja_runnerd/client.py`` is
the real client the conformance test drives against these routes.

Three decisions shape every route:

* **The fence is the authority, not the workflow id.** A DBOS workflow id is an
  idempotency key: the measured spike ran the same id on two executors twice
  (0/6 clean) and duplicated a side effect when a kill landed inside a step.
  ``agent_lease.fence`` is monotonic per task, every route compares it, and a
  fence that is not the current attempt is ``409 stale_fence`` — never a retry,
  because retrying is exactly the zombie run the spec forbids (FR-018).
* **External writes are keyed ``run_id + seq``** (FR-019). ``agent_side_effect``
  is unique per ``(organization, key)``, so a duplicate finish (or a retry after
  a lost response) records the effect once and returns the first reference. The
  ``perform`` callable is the seam a server-side PR creation uses.
* **Expiry is Hoja's clock** (FR-020). ``lease_until`` is written from the
  server's ``now``, the reaper compares it to the server's ``now`` with a grace
  window longer than the heartbeat interval, and it never asks the runner
  whether it is alive. A lost attempt is marked ``lost`` and the task is
  re-queued at ``fence + 1``, which is what makes the old runner's next call a
  409 instead of a second completion.

Credentials: a runner token lives on ``runner.lease_token_hash`` and never in
``api_key`` (whose ``access`` accepts only ``read|read_write``). Only the sha256
is stored; the plaintext is returned exactly once, at registration. A per-lease
token is issued on every grant for the same reason and is re-issued when the
fence moves, so a token from a previous attempt cannot drive the new one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hoja_server import audit, tenant_context
from hoja_server.db import (
    agent_lease,
    agent_side_effect,
    gen_token,
    issue,
    new_id,
    now_iso,
    organization,
    runner,
    runner_enrollment,
    token_hash,
)

LOG = logging.getLogger("hoja_server.runner")

# ---------------------------------------------------------------------------
# Wire contract. Mirrored, byte for byte, in runner/hoja_runnerd/protocol.py and
# asserted equal by tests/agent/test_runner_conformance.py -- the runner package
# must not import the server, so equality is checked rather than shared.
# ---------------------------------------------------------------------------
ROUTE_REGISTER = "/runner/register"
ROUTE_LEASE = "/runner/lease"
ROUTE_HEARTBEAT = "/runner/heartbeat"
ROUTE_EVENTS = "/runner/events"
ROUTE_FINISH = "/runner/finish"
# WP07 closes two of the gaps WP08 reported (PR #218, "Contract gaps"):
ROUTE_EFFECT = "/runner/effect"
ROUTE_RELEASE = "/runner/release"

HEADER_RUNNER = "X-Hoja-Runner"
HEADER_LEASE = "X-Hoja-Lease"
HEADER_FENCE = "X-Hoja-Fence"
HEADER_LEASE_TOKEN = "X-Hoja-Lease-Token"

ERROR_STALE_FENCE = "stale_fence"
ERROR_LEASE_LOST = "lease_lost"
ERROR_EXPIRED = "expired"
ERROR_LEASE_TOKEN = "lease_token_invalid"
ERROR_UNAUTHORIZED = "unauthorized"
ERROR_BAD_PARAMS = "bad_params"
ERROR_FENCE_MISMATCH = "fence_mismatch"
ERROR_HARNESS_NOT_ALLOWED = "harness_not_allowed"
ERROR_CANCEL_NOT_REQUESTED = "cancel_not_requested"
ERROR_ENROLLMENT_SPENT = "enrollment_spent"

# The cadence the server pins (WP08 gap 5). The TTL is at least four beats so a
# single dropped heartbeat cannot lose a live lease, and the reaper's grace is
# longer than one cadence so it cannot race a runner that is still beating.
HEARTBEAT_INTERVAL_S = 5
LEASE_TTL_S = 60
MIN_LEASE_TTL_S = 4 * HEARTBEAT_INTERVAL_S
MAX_LEASE_TTL_S = 3600
REAP_GRACE_S = 20
REAP_INTERVAL_S = 10

# Bounded long-poll (T031 step 1) and bounded batches: both are attacker-supplied.
MAX_POLL_MS = 60_000
MAX_EVENTS_PER_BATCH = 200
MAX_EVENT_BYTES = 8 * 1024
MAX_EFFECTS_PER_FINISH = 50
MAX_ATTEMPTS = 3
MAX_PROMPT_CHARS = 64_000

FINISH_STATES = ("completed", "canceled", "failed")
# Where a finished run leaves the task. WP10 (FR-028) owns the session and the
# verifier; WP07 deliberately moves nothing to "done" and never closes a task.
SESSION_STATE = {
    "completed": "awaiting_verification",
    "canceled": "canceled",
    "failed": "failed",
}

LEASE_QUEUED = "queued"
LEASE_ACTIVE = "active"
LEASE_FINISHED = "finished"
LEASE_RELEASED = "released"
LEASE_CANCELED = "canceled"
LEASE_LOST = "lost"


class LeaseRequestError(Exception):
    """A route refused a call. ``status``/``code`` are the wire answer."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def response(self) -> JSONResponse:
        return _error(self.status, self.code, self.message, **self.extra)


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, **extra}, status_code=status)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row(conn, stmt) -> dict[str, Any] | None:
    found = conn.execute(stmt).mappings().first()
    return dict(found) if found else None


# ---------------------------------------------------------------------------
# Enrollment and registration (T029)
# ---------------------------------------------------------------------------


def mint_enrollment_token(
    engine,
    *,
    organization_id: str,
    created_by: str | None = None,
    label: str | None = None,
    ttl_s: int = 24 * 3600,
) -> tuple[dict[str, Any], str]:
    """Create a single-use registration token. Returns (row, plaintext).

    Called by whatever admin surface hands a customer their enrollment string
    (the admin screen is WP13's). The plaintext is returned once and never
    stored: only the sha256 is, exactly like ``api_key``.
    """
    token = gen_token()
    token_id = new_id()
    created = _iso(_now())
    row = {
        "id": token_id,
        "organization_id": organization_id,
        "token_hash": token_hash(token),
        "hint": token[-4:],
        "label": (label or "").strip() or None,
        "created_by": created_by,
        "expires_at": _iso(_now() + timedelta(seconds=int(ttl_s))) if ttl_s else None,
        "used_at": None,
        "used_by_runner": None,
        "created_at": created,
    }
    with tenant_context.scoped(organization_id), engine.begin() as conn:
        conn.execute(runner_enrollment.insert().values(**row))
    return row, token


def register_runner(engine, body: Mapping[str, Any]) -> dict[str, Any]:
    """Spend an enrollment token and mint the runner's bearer token (T029)."""
    enrollment_token = str(body.get("enrollment_token") or "").strip()
    name = str(body.get("name") or "").strip()
    harnesses = [str(item).strip().lower() for item in (body.get("harnesses") or [])
                 if str(item).strip()]
    labels = body.get("labels") or {}
    if not enrollment_token:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "enrollment_token is required")
    if not name or len(name) > 80:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "name is required (max 80 chars)")
    if not harnesses:
        # An empty allowlist would mean "run anything", which is the opposite of
        # what declaring an allowlist is for. Refuse at registration, once.
        raise LeaseRequestError(400, ERROR_BAD_PARAMS,
                                "harnesses is required: declare what this runner can run")
    if not isinstance(labels, Mapping):
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "labels must be an object")

    digest = token_hash(enrollment_token)
    with tenant_context.system():
        with engine.connect() as conn:
            enrollment = _row(conn, select(runner_enrollment).where(
                runner_enrollment.c.token_hash == digest))
        if not enrollment:
            raise LeaseRequestError(401, ERROR_UNAUTHORIZED, "unknown enrollment token")
        if enrollment["used_at"]:
            # Single use. Saying "already spent" rather than "unknown" is
            # deliberate: an operator who re-runs the installer needs to know why.
            raise LeaseRequestError(409, ERROR_ENROLLMENT_SPENT,
                                    "this enrollment token has already been used")
        expires = _parse_iso(enrollment["expires_at"])
        if expires is not None and expires < _now():
            raise LeaseRequestError(401, ERROR_UNAUTHORIZED, "this enrollment token has expired")
        org_id = enrollment["organization_id"]

    token = gen_token()
    runner_id = new_id()
    created = now_iso()
    with tenant_context.scoped(org_id), engine.begin() as conn:
        # Spend it first, conditionally: a second concurrent registration of
        # the same token finds used_at set and gets nothing.
        spent = conn.execute(runner_enrollment.update().where(and_(
            runner_enrollment.c.id == enrollment["id"],
            runner_enrollment.c.used_at.is_(None),
        )).values(used_at=created, used_by_runner=runner_id))
        if spent.rowcount != 1:
            raise LeaseRequestError(409, ERROR_ENROLLMENT_SPENT,
                                    "this enrollment token has already been used")
        try:
            conn.execute(runner.insert().values(
                id=runner_id,
                organization_id=org_id,
                name=name,
                labels=json.dumps(dict(labels), sort_keys=True),
                harnesses=json.dumps(sorted(set(harnesses))),
                lease_token_hash=token_hash(token),
                token_hint=token[-4:],
                enabled=True,
                last_seen=None,
                created_at=created,
                revoked_at=None,
            ))
        except IntegrityError as exc:
            raise LeaseRequestError(409, ERROR_BAD_PARAMS,
                                    f"a runner named {name!r} is already enrolled") from exc
    return {
        "runner_id": runner_id,
        "id": runner_id,
        "token": token,
        "name": name,
        "organization_id": org_id,
        "harnesses": sorted(set(harnesses)),
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
        "lease_ttl_s": LEASE_TTL_S,
    }


def revoke_runner(engine, *, organization_id: str, runner_id: str) -> bool:
    """Disable a runner and every token it holds."""
    with tenant_context.scoped(organization_id), engine.begin() as conn:
        result = conn.execute(runner.update().where(and_(
            runner.c.id == runner_id,
            runner.c.organization_id == organization_id,
            runner.c.revoked_at.is_(None),
        )).values(enabled=False, revoked_at=now_iso()))
        return bool(result.rowcount)


# ---------------------------------------------------------------------------
# Queueing work (the WP06 seam)
# ---------------------------------------------------------------------------


def _task_prompt(conn, org_id: str, task_id: str, prompt: str | None) -> str:
    if prompt:
        return prompt[:MAX_PROMPT_CHARS]
    found = _row(conn, select(issue.c.identifier, issue.c.title, issue.c.description).where(
        and_(issue.c.id == task_id, issue.c.organization_id == org_id)))
    if not found:
        raise LeaseRequestError(404, ERROR_BAD_PARAMS, f"task {task_id} does not exist")
    return (
        f"{found['identifier']}: {found['title']}\n\n"
        f"{(found['description'] or '').strip()}"
    ).strip()[:MAX_PROMPT_CHARS]


def enqueue_task(
    engine,
    *,
    organization_id: str,
    task_id: str,
    harness: str,
    prompt: str | None = None,
    ttl_s: int = LEASE_TTL_S,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Queue one attempt of one task at the next fence.

    This is the seam WP06's scheduler calls when it decides a task should run;
    WP07 owns the queue itself. The fence is ``max(fence) + 1`` per task, so a
    re-queue after a lost attempt can never reuse a fence the old runner holds.
    """
    harness = str(harness or "").strip().lower()
    if not harness:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "harness is required")
    ttl = int(ttl_s or LEASE_TTL_S)
    if ttl < MIN_LEASE_TTL_S or ttl > MAX_LEASE_TTL_S:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS,
                                f"ttl_s must be between {MIN_LEASE_TTL_S} and {MAX_LEASE_TTL_S}")
    with tenant_context.scoped(organization_id), engine.begin() as conn:
        text_ = _task_prompt(conn, organization_id, task_id, prompt)
        current = conn.execute(select(func.max(agent_lease.c.fence)).where(
            and_(agent_lease.c.organization_id == organization_id,
                 agent_lease.c.task_id == task_id))).scalar()
        attempt = conn.execute(select(func.max(agent_lease.c.attempt)).where(
            and_(agent_lease.c.organization_id == organization_id,
                 agent_lease.c.task_id == task_id))).scalar()
        lease_id = new_id()
        stamp = now_iso()
        values = {
            "id": lease_id,
            "organization_id": organization_id,
            "task_id": task_id,
            "runner_id": None,
            "fence": int(current or 0) + 1,
            "run_id": run_id or f"run-{new_id()}",
            "state": LEASE_QUEUED,
            "attempt": int(attempt or 0) + 1,
            "harness": harness,
            "prompt": text_,
            "lease_token_hash": None,
            "ttl_seconds": ttl,
            "lease_until": None,
            "granted_at": None,
            "last_heartbeat_at": None,
            "cancel_requested_at": None,
            "cancel_reason": None,
            "finish_state": None,
            "finish_key": None,
            "result": None,
            "last_event_seq": 0,
            "finished_at": None,
            "lost_at": None,
            "lost_reason": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        conn.execute(agent_lease.insert().values(**values))
        return values


def request_cancel(engine, *, organization_id: str, task_id: str, reason: str = "human_stop",
                   actor_kind: str = "human", actor_id: str | None = None) -> dict[str, Any] | None:
    """Ask the current attempt of a task to stop. Delivered on the next heartbeat.

    A cancel is a request, not a state change: the lease stays ``active`` so the
    runner can still finish it as ``canceled`` (which is the only finish that is
    accepted with a reason), and the runner learns about it the only way the
    contract allows — the heartbeat response.
    """
    stamp = now_iso()
    with tenant_context.scoped(organization_id), engine.begin() as conn:
        current = _row(conn, select(agent_lease).where(and_(
            agent_lease.c.organization_id == organization_id,
            agent_lease.c.task_id == task_id,
            agent_lease.c.state == LEASE_ACTIVE,
        )).order_by(agent_lease.c.fence.desc()).limit(1))
        if not current:
            return None
        conn.execute(agent_lease.update().where(and_(
            agent_lease.c.id == current["id"],
            agent_lease.c.state == LEASE_ACTIVE,
        )).values(cancel_requested_at=stamp, cancel_reason=reason[:200], updated_at=stamp))
        return {**current, "cancel_requested_at": stamp, "cancel_reason": reason[:200]}


# ---------------------------------------------------------------------------
# Granting a lease (T030, T031)
# ---------------------------------------------------------------------------


def _grantable(conn, org_id: str, harnesses: Sequence[str]) -> dict[str, Any] | None:
    """One queued attempt for a harness this runner declared, or None.

    Postgres takes the row lock with ``SKIP LOCKED`` so two runners claiming at
    the same instant take two different tasks instead of colliding; SQLite is
    the single-tenant local mode and serializes on its write lock anyway.
    """
    stmt = select(agent_lease.c.id, agent_lease.c.task_id, agent_lease.c.fence,
                  agent_lease.c.ttl_seconds).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.state == LEASE_QUEUED,
        agent_lease.c.harness.in_(list(harnesses)),
    )).order_by(agent_lease.c.created_at, agent_lease.c.fence).limit(1)
    if conn.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return _row(conn, stmt)


def _refused(conn, org_id: str, harnesses: Sequence[str]) -> list[dict[str, Any]]:
    """Queued attempts this runner may not take, for the operator's benefit."""
    rows = conn.execute(select(agent_lease.c.task_id, agent_lease.c.harness).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.state == LEASE_QUEUED,
        agent_lease.c.harness.notin_(list(harnesses)),
    )).limit(10))
    return [{"task_id": row[0], "harness": row[1], "reason": ERROR_HARNESS_NOT_ALLOWED}
            for row in rows]


def _lease_actor(conn, lease: Mapping[str, Any]) -> tuple[str, str]:
    """Who is acting when a runner does something: the task's agent, or the runner.

    An agent-delegated task is attributed to the agent (revision 64's principal);
    anything else to the runner as a non-human principal. Never a guessed human.
    """
    if lease.get("task_id"):
        delegate = conn.execute(select(issue.c.delegate_id).where(
            issue.c.id == lease["task_id"])).scalar()
        if delegate:
            return "agent", str(delegate)
    return "bot", str(lease.get("runner_id") or lease.get("id") or "runner")


def claim_lease(engine, runner_row: Mapping[str, Any]) -> dict[str, Any] | None:
    """One non-blocking attempt at granting a lease to ``runner_row``."""
    org_id = runner_row["organization_id"]
    harnesses = _json_list(runner_row.get("harnesses"))
    stamp = _now()
    with tenant_context.scoped(org_id), engine.begin() as conn:
        if not harnesses:
            return None
        candidate = _grantable(conn, org_id, harnesses)
        if not candidate:
            return None
        lease_token = gen_token()
        until = _iso(stamp + timedelta(seconds=int(candidate["ttl_seconds"])))
        granted = conn.execute(agent_lease.update().where(and_(
            agent_lease.c.id == candidate["id"],
            agent_lease.c.state == LEASE_QUEUED,
            agent_lease.c.fence == candidate["fence"],
        )).values(
            state=LEASE_ACTIVE,
            runner_id=runner_row["id"],
            lease_token_hash=token_hash(lease_token),
            lease_until=until,
            granted_at=now_iso(),
            updated_at=now_iso(),
        ))
        if granted.rowcount != 1:
            # Another runner won the same row between our read and our write.
            return None
        lease = _row(conn, select(agent_lease).where(agent_lease.c.id == candidate["id"]))
        if lease is None:  # pragma: no cover - the row we just updated
            return None
        lease["token"] = lease_token
        org_key = conn.execute(select(organization.c.url_key).where(
            organization.c.id == org_id)).scalar()
    _audit_lease(engine, "agent.lease.grant", lease, runner_row,
                 detail={"fence": lease["fence"], "attempt": lease["attempt"],
                         "harness": lease["harness"], "ttl_s": lease["ttl_seconds"]})
    return {
        "lease_id": lease["id"],
        "run_id": lease["run_id"],
        "task_id": lease["task_id"],
        "fence": lease["fence"],
        "token": lease_token,
        "harness": lease["harness"],
        "prompt": lease["prompt"],
        "ttl_s": lease["ttl_seconds"],
        "worktree": None,
        "workspace": org_key,
        "model": None,
        "labels": _json_dict(runner_row.get("labels")),
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
    }

def _audit_lease(engine, action: str, lease: Mapping[str, Any], runner_row: Mapping[str, Any],
                 *, detail: Mapping[str, Any] | None = None,
                 actor: tuple[str, str] | None = None) -> None:
    """Append one audit row. A failure to audit never fails the lease call."""
    try:
        kind, who = actor or ("bot", str(runner_row.get("id") or "runner"))
        audit.record(
            engine,
            organization_id=lease["organization_id"],
            action=action,
            actor_kind=kind,
            actor_id=who,
            target_kind="agent_lease",
            target_id=str(lease.get("id") or lease.get("lease_id") or ""),
            runner=str(runner_row.get("name") or ""),
            detail=dict(detail or {}),
        )
    except Exception:
        LOG.warning("could not audit %s for lease %s", action, lease.get("id"), exc_info=True)


# ---------------------------------------------------------------------------
# Fence and token checks shared by every lease route
# ---------------------------------------------------------------------------


def _current_attempt(conn, lease: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return _row(conn, select(agent_lease).where(and_(
        agent_lease.c.organization_id == lease["organization_id"],
        agent_lease.c.task_id == lease["task_id"],
    )).order_by(agent_lease.c.fence.desc()).limit(1))


def _resolve_lease(conn, runner_row: Mapping[str, Any], lease_id: str,
                   fence: int, lease_token: str | None) -> dict[str, Any]:
    """Load a lease and prove the caller still owns it. Everything else is a 4xx."""
    lease = _row(conn, select(agent_lease).where(and_(
        agent_lease.c.id == lease_id,
        agent_lease.c.organization_id == runner_row["organization_id"],
    )))
    if lease is None:
        raise LeaseRequestError(410, ERROR_LEASE_LOST, "this lease is not known to this runner")
    token_ok = bool(lease.get("lease_token_hash")) and bool(lease_token) and \
        lease["lease_token_hash"] == token_hash(str(lease_token))
    if not token_ok:
        # A token from a previous attempt (re-issued when the fence moved) or a
        # token for another lease. Authorization, not a retryable condition.
        raise LeaseRequestError(403, ERROR_LEASE_TOKEN,
                                "the lease token does not match the current attempt")
    current = _current_attempt(conn, lease)
    if lease["state"] == LEASE_LOST or (current and current["fence"] != lease["fence"]):
        raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                f"fence {fence} is not the current attempt for this task")
    if int(fence) != int(lease["fence"]):
        raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                f"fence {fence} is not {lease['fence']}")
    if lease["state"] != LEASE_ACTIVE:
        raise LeaseRequestError(410, ERROR_LEASE_LOST,
                                f"this lease is {lease['state']}, not active")
    return lease


def _lease_fields(body: Mapping[str, Any], request: Request | None) -> tuple[str, int, str | None]:
    """Fence, lease id and lease token, from the body and from the headers.

    The fence travels twice on purpose (WP08 gap 4): the body is the contract and
    the headers survive a proxy that logs only headers. When both are present and
    disagree, the request is refused rather than one of them being believed.
    """
    header_fence = request.headers.get(HEADER_FENCE) if request is not None else None
    body_fence = body.get("fence")
    if header_fence is not None and body_fence is not None:
        try:
            if int(header_fence) != int(body_fence):
                raise LeaseRequestError(400, ERROR_FENCE_MISMATCH,
                                        f"body fence {body_fence} != header fence {header_fence}")
        except (TypeError, ValueError) as exc:
            raise LeaseRequestError(400, ERROR_BAD_PARAMS, "fence must be an integer") from exc
    try:
        fence = int(body_fence if body_fence is not None else (header_fence or 0))
    except (TypeError, ValueError) as exc:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "fence must be an integer") from exc
    lease_id = str(body.get("lease_id")
                   or (request.headers.get(HEADER_LEASE) if request is not None else "")
                   or "").strip()
    if not lease_id:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "lease_id is required")
    token = body.get("token")
    if token is None and request is not None:
        token = request.headers.get(HEADER_LEASE_TOKEN)
    return lease_id, fence, (str(token) if token is not None else None)


# ---------------------------------------------------------------------------
# Heartbeat: the only place cancel is delivered (T031)
# ---------------------------------------------------------------------------


def heartbeat_lease(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any],
                    request: Request | None = None) -> dict[str, Any]:
    lease_id, fence, lease_token = _lease_fields(body, request)
    org_id = runner_row["organization_id"]
    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            lease = _resolve_lease(conn, runner_row, lease_id, fence, lease_token)
        deadline = _parse_iso(lease["lease_until"])
        if deadline is not None and deadline < _now():
            raise LeaseRequestError(409, ERROR_EXPIRED,
                                    "this lease expired before the heartbeat; it is no longer yours")
        stamp = _iso(_now())
        ttl = int(lease["ttl_seconds"] or LEASE_TTL_S)
        extends_to = _iso(_now() + timedelta(seconds=ttl))
        with engine.begin() as conn:
            updated = conn.execute(agent_lease.update().where(and_(
                agent_lease.c.id == lease["id"],
                agent_lease.c.fence == lease["fence"],
                agent_lease.c.state == LEASE_ACTIVE,
            )).values(lease_until=extends_to, last_heartbeat_at=stamp, updated_at=stamp))
            if updated.rowcount != 1:
                raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                        "the lease was re-granted while this heartbeat was in flight")
    _touch_runner(engine, runner_row, stamp)
    cancel = bool(lease.get("cancel_requested_at"))
    return {
        "fence": lease["fence"],
        "cancel": cancel,
        "cancel_reason": lease.get("cancel_reason") if cancel else None,
        "ttl_s": ttl,
        "lease_state": LEASE_ACTIVE,
        "lease_until": extends_to,
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
    }


def _touch_runner(engine, runner_row: Mapping[str, Any], stamp: str) -> None:
    """Record liveness, but at most once per heartbeat window per runner."""
    last = _parse_iso(runner_row.get("last_seen"))
    now = _parse_iso(stamp) or _now()
    if last is not None and (now - last).total_seconds() < HEARTBEAT_INTERVAL_S:
        return
    with tenant_context.scoped(runner_row["organization_id"]), engine.begin() as conn:
        conn.execute(runner.update().where(runner.c.id == runner_row["id"]).values(
            last_seen=stamp))


# ---------------------------------------------------------------------------
# Activity stream
# ---------------------------------------------------------------------------


def post_events(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any],
                request: Request | None = None) -> dict[str, Any]:
    lease_id, fence, lease_token = _lease_fields(body, request)
    events = list(body.get("events") or [])
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS,
                                f"at most {MAX_EVENTS_PER_BATCH} events per batch")
    org_id = runner_row["organization_id"]
    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            lease = _resolve_lease(conn, runner_row, lease_id, fence, lease_token)
        accepted = 0
        with engine.begin() as conn:
            for event in events:
                try:
                    seq = int(event.get("seq") or 0)
                except (TypeError, ValueError):
                    continue
                if seq <= int(lease["last_event_seq"] or 0) or seq <= 0:
                    continue  # already recorded: the stream is monotonic per lease
                kind = str(event.get("kind") or "event")[:80]
                payload = event.get("payload") or {}
                encoded = json.dumps(payload, sort_keys=True, default=str)
                if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
                    continue
                conn.execute(issue_activity_insert(org_id, lease["task_id"], kind, encoded))
                accepted = max(accepted, seq)
            if accepted:
                conn.execute(agent_lease.update().where(agent_lease.c.id == lease["id"]).values(
                    last_event_seq=accepted, updated_at=now_iso()))
    return {"accepted_seq": accepted or int(lease["last_event_seq"] or 0)}


def issue_activity_insert(org_id: str, task_id: str, kind: str, payload: str):
    from hoja_server.db import issue_activity

    return issue_activity.insert().values(
        id=new_id(),
        organization_id=org_id,
        issue_id=task_id,
        actor_id=None,
        event_type=f"runner.{kind}"[:80],
        changes=payload,
        created_at=now_iso(),
    )


# ---------------------------------------------------------------------------
# Per-effect idempotency (T032)
# ---------------------------------------------------------------------------


def _effect_ref(payload: Mapping[str, Any]) -> str | None:
    for key in ("external_ref", "pr_url", "url", "pull_request", "number"):
        if payload.get(key) not in (None, ""):
            value = payload[key]
            return str(value)
    return None


def record_effect(
    engine,
    *,
    lease: Mapping[str, Any],
    key: str,
    kind: str,
    payload: Mapping[str, Any] | None = None,
    perform: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Record one external write exactly once, keyed ``run_id + seq`` (FR-019).

    A duplicate key returns the first record and calls nothing: that is what
    makes a duplicate finish open one PR. ``perform`` is where the write happens
    when the server is the one writing (PR creation); with ``perform=None`` the
    runner reports the reference it produced itself. The row is inserted only
    after ``perform`` returns, so a failed write is safely retryable and a
    successful one is never repeated.
    """
    org_id = lease["organization_id"]
    payload = dict(payload or {})
    key = str(key or "")
    if not key:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "an effect key is required")
    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            existing = _row(conn, select(agent_side_effect).where(and_(
                agent_side_effect.c.organization_id == org_id,
                agent_side_effect.c.key == key,
            )))
        if existing:
            return {**existing, "duplicate": True}
        external_ref = _effect_ref(payload)
        if perform is not None:
            produced = perform()
            if produced not in (None, ""):
                external_ref = str(produced)
        # The sequence is in the key by construction (run_id + seq), so it is
        # derived rather than trusted from a second, independently-writable field.
        seq = _seq_from_key(key, lease["run_id"])
        row = {
            "id": new_id(),
            "organization_id": org_id,
            "lease_id": lease["id"],
            "run_id": lease["run_id"],
            "seq": seq,
            "key": key,
            "kind": str(kind or "effect")[:80],
            "payload": json.dumps(payload, sort_keys=True, default=str),
            "external_ref": external_ref,
            "created_at": now_iso(),
        }
        try:
            with engine.begin() as conn:
                conn.execute(agent_side_effect.insert().values(**row))
        except IntegrityError:
            # Lost the race with a concurrent duplicate: the other row is the
            # record, and it is the same key, so the caller gets that one.
            with engine.connect() as conn:
                existing = _row(conn, select(agent_side_effect).where(and_(
                    agent_side_effect.c.organization_id == org_id,
                    agent_side_effect.c.key == key,
                )))
            if existing:
                return {**existing, "duplicate": True}
            raise
        return {**row, "duplicate": False}


def _seq_from_key(key: str, run_id: str) -> int:
    """``run_id:seq`` when the key is well-formed, else 0 (kind-only effect)."""
    if key.startswith(f"{run_id}:"):
        tail = key[len(run_id) + 1:]
        if tail.isdigit():
            return int(tail)
    return 0


# ---------------------------------------------------------------------------
# Finish (T031) and release
# ---------------------------------------------------------------------------


def finish_lease(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any],
                 request: Request | None = None) -> dict[str, Any]:
    """Accept a claim. A duplicate finish is idempotent; a stale fence is 409.

    Canceled and failed are first-class finishes (WP08 gap 3), with the policy
    spelled out here because the wire allows all three and WP10 only describes
    ``completed``:

    * ``completed`` — the lease is ``finished``; the task is left untouched for
      WP10's verifier, and the ack names ``awaiting_verification``.
    * ``canceled`` — accepted **only** when the control plane asked for the
      cancel. A runner cannot cancel itself out of a fence it lost.
    * ``failed`` — the attempt is consumed (``failed``) and the task is *not*
      re-queued; retry policy is WP06's scheduling decision, not the runner's.
    """
    lease_id, fence, lease_token = _lease_fields(body, request)
    state = str(body.get("state") or "").strip().lower()
    if state not in FINISH_STATES:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS,
                                f"state must be one of {', '.join(FINISH_STATES)}")
    key = str(body.get("idempotency_key") or "").strip()
    if not key:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "idempotency_key is required")
    org_id = runner_row["organization_id"]
    summary = str(body.get("summary") or "")[:2000]
    evidence = body.get("evidence") or {}
    effects = list(body.get("effects") or [])
    if len(effects) > MAX_EFFECTS_PER_FINISH:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS,
                                f"at most {MAX_EFFECTS_PER_FINISH} effects per finish")
    if not isinstance(evidence, Mapping):
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "evidence must be an object")

    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            lease = _row(conn, select(agent_lease).where(and_(
                agent_lease.c.id == lease_id,
                agent_lease.c.organization_id == org_id,
            )))
            if lease is None:
                raise LeaseRequestError(410, ERROR_LEASE_LOST,
                                        "this lease is not known to this runner")
        # Idempotency first: the same finish under the same key is the same
        # finish, even though the first response was lost. A *different* key on a
        # finished lease is not a duplicate, and falls through to the fence check.
        if lease["state"] == LEASE_FINISHED and lease.get("finish_key") == key:
            with engine.connect() as conn:
                applied = _row(conn, select(agent_side_effect).where(and_(
                    agent_side_effect.c.organization_id == org_id,
                    agent_side_effect.c.key == key,
                )))
            return _finish_ack(lease, applied or {"key": key, "external_ref": None},
                               duplicate=True, key=str(lease.get("finish_key") or key))
        with engine.connect() as conn:
            lease = _resolve_lease(conn, runner_row, lease_id, fence, lease_token)
        deadline = _parse_iso(lease["lease_until"])
        if deadline is not None and deadline < _now():
            raise LeaseRequestError(409, ERROR_EXPIRED,
                                    "this lease expired before the finish; the run is lost")
        if state == "canceled" and not lease.get("cancel_requested_at"):
            raise LeaseRequestError(400, ERROR_CANCEL_NOT_REQUESTED,
                                    "a canceled finish needs a cancel the control plane asked for")

        # Record every effect under this lease's run: the claim itself is seq 1,
        # and a reported PR is whatever seq the daemon numbered it.
        primary = None
        with_ref = None
        for effect in effects:
            effect_key = str(effect.get("key") or "")
            effect_payload = dict(effect.get("payload") or {})
            if not effect_key.startswith(f"{lease['run_id']}:"):
                raise LeaseRequestError(400, ERROR_BAD_PARAMS,
                                        f"effect key {effect_key!r} must start with {lease['run_id']}:")
            recorded = record_effect(engine, lease=lease, key=effect_key,
                                     kind=str(effect.get("kind") or "effect"),
                                     payload=effect_payload)
            if primary is None:
                primary = recorded
            if with_ref is None and recorded.get("external_ref"):
                # The ack names the external object the run produced (the PR),
                # not whichever effect the daemon happened to number first.
                with_ref = recorded
        if primary is None:
            primary = record_effect(engine, lease=lease, key=key, kind="claim",
                                    payload={"state": state, "summary": summary})
        ack_effect = with_ref or primary
        session_state = SESSION_STATE[state]
        stamp = now_iso()
        with tenant_context.scoped(org_id), engine.begin() as conn:
            updated = conn.execute(agent_lease.update().where(and_(
                agent_lease.c.id == lease["id"],
                agent_lease.c.fence == lease["fence"],
                agent_lease.c.state == LEASE_ACTIVE,
            )).values(
                state=LEASE_FINISHED,
                finish_state=state,
                finish_key=key,
                finished_at=stamp,
                updated_at=stamp,
                result=json.dumps({"state": state, "summary": summary,
                                   "evidence": dict(evidence),
                                   "session_state": session_state,
                                   "external_ref": ack_effect.get("external_ref")},
                                  sort_keys=True, default=str),
            ))
            if updated.rowcount != 1:
                raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                        "the lease was re-granted while this finish was in flight")
        _audit_lease(engine, "agent.session.stop", lease, runner_row,
                     detail={"state": state, "key": key,
                             "external_ref": ack_effect.get("external_ref"),
                             "session_state": session_state},
                     actor=_lease_actor_of(engine, lease))
    return _finish_ack({**lease, "finish_state": state, "finish_key": key},
                       ack_effect, duplicate=False, key=key, session_state=session_state)


def _lease_actor_of(engine, lease: Mapping[str, Any]) -> tuple[str, str]:
    with engine.connect() as conn:
        return _lease_actor(conn, lease)


def _finish_ack(lease: Mapping[str, Any], effect: Mapping[str, Any], *, duplicate: bool,
                key: str, session_state: str | None = None) -> dict[str, Any]:
    stored = _json_dict(lease.get("result"))
    return {
        "accepted": True,
        "duplicate": duplicate,
        # The caller's key, echoed: it is what makes the retry the same finish.
        "idempotency_key": key,
        # The effect row is the witness; the stored result is the fallback for a
        # replayed finish whose primary effect (the claim) carries no reference.
        "external_ref": effect.get("external_ref") or stored.get("external_ref"),
        "session_state": session_state or stored.get("session_state"),
    }


def release_lease(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any],
                  request: Request | None = None) -> dict[str, Any]:
    """Give a lease back without a claim (WP08 gap 1).

    The daemon's graceful stop path: it stops the harness and says so, instead
    of leaving the task ``active`` until the TTL. ``requeue`` asks the server to
    put the attempt back on the queue at the next fence; a release for a cancel
    does not requeue, because a human asked it to stop.
    """
    lease_id, fence, lease_token = _lease_fields(body, request)
    requeue = bool(body.get("requeue", False))
    reason = str(body.get("reason") or "released")[:200]
    org_id = runner_row["organization_id"]
    with tenant_context.scoped(org_id):
        with engine.connect() as conn:
            lease = _resolve_lease(conn, runner_row, lease_id, fence, lease_token)
        stamp = now_iso()
        terminal = LEASE_CANCELED if lease.get("cancel_requested_at") else LEASE_RELEASED
        with engine.begin() as conn:
            updated = conn.execute(agent_lease.update().where(and_(
                agent_lease.c.id == lease["id"],
                agent_lease.c.fence == lease["fence"],
                agent_lease.c.state == LEASE_ACTIVE,
            )).values(state=terminal, lost_reason=reason, updated_at=stamp,
                      finished_at=stamp))
            if updated.rowcount != 1:
                raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                        "the lease was re-granted while this release was in flight")
    requeued = None
    if requeue and terminal == LEASE_RELEASED and int(lease["attempt"]) < MAX_ATTEMPTS:
        requeued = enqueue_task(
            engine,
            organization_id=org_id,
            task_id=lease["task_id"],
            harness=lease["harness"],
            prompt=lease["prompt"],
            ttl_s=int(lease["ttl_seconds"]),
        )
    return {"released": True, "fence": lease["fence"], "state": terminal,
            "requeued": ({"lease_id": requeued["id"], "fence": requeued["fence"]}
                         if requeued else None)}


# ---------------------------------------------------------------------------
# The reaper: expiry on Hoja's clock (T033, FR-020)
# ---------------------------------------------------------------------------


def reap_expired_leases(engine, *, now: datetime | None = None,
                        grace_s: int = REAP_GRACE_S) -> list[dict[str, Any]]:
    """Mark expired attempts lost and re-queue the task at the next fence.

    Deliberately independent of any runner report and of the DBOS process: a DB
    outage blocks DBOS silently, so liveness cannot be inferred from it. The
    grace window is longer than the heartbeat interval, which is what stops the
    reaper from racing a runner that is one beat behind.
    """
    stamp_dt = now or _now()
    stamp = _iso(stamp_dt)
    cutoff = _iso(stamp_dt - timedelta(seconds=max(0, int(grace_s))))
    lost: list[dict[str, Any]] = []
    with tenant_context.system(), engine.connect() as conn:
        expired = [dict(row) for row in conn.execute(select(agent_lease).where(and_(
            agent_lease.c.state == LEASE_ACTIVE,
            agent_lease.c.lease_until.is_not(None),
            agent_lease.c.lease_until < cutoff,
        )).limit(200)).mappings()]
    for lease in expired:
        org_id = lease["organization_id"]
        canceled = bool(lease.get("cancel_requested_at"))
        terminal = LEASE_CANCELED if canceled else LEASE_LOST
        with tenant_context.scoped(org_id):
            with engine.connect() as conn:
                runner_row = _row(conn, select(runner).where(runner.c.id == lease["runner_id"])) \
                    if lease.get("runner_id") else None
                actor = _lease_actor(conn, lease)
            with engine.begin() as conn:
                updated = conn.execute(agent_lease.update().where(and_(
                    agent_lease.c.id == lease["id"],
                    agent_lease.c.state == LEASE_ACTIVE,
                    agent_lease.c.lease_until == lease["lease_until"],
                )).values(state=terminal, lost_at=stamp, updated_at=stamp,
                          lost_reason="cancel_requested" if canceled else "lease_expired"))
                if updated.rowcount != 1:
                    continue  # it finished or was reaped by another worker
        requeued = None
        if not canceled and int(lease["attempt"]) < MAX_ATTEMPTS:
            requeued = enqueue_task(
                engine,
                organization_id=org_id,
                task_id=lease["task_id"],
                harness=lease["harness"],
                prompt=lease["prompt"],
                ttl_s=int(lease["ttl_seconds"]),
            )
        _audit_lease(engine, "agent.lease.expiry", lease, runner_row or {},
                     detail={"fence": lease["fence"], "attempt": lease["attempt"],
                             "reason": "cancel_requested" if canceled else "lease_expired",
                             "requeued_fence": requeued["fence"] if requeued else None},
                     actor=actor)
        lost.append({"lease_id": lease["id"], "task_id": lease["task_id"],
                     "fence": lease["fence"], "state": terminal,
                     "requeued_fence": requeued["fence"] if requeued else None})
    return lost


async def run_reaper_forever(engine, *, interval_s: int = REAP_INTERVAL_S) -> None:
    """The scheduled reaper, run in system context like the webhook worker."""
    while True:
        try:
            reaped = await run_in_threadpool(reap_expired_leases, engine)
            if reaped:
                LOG.info("reaped %d expired lease(s)", len(reaped))
        except Exception:
            LOG.warning("lease reaper failed", exc_info=True)
        await asyncio.sleep(interval_s)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


async def _json_body(request: Request) -> Mapping[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, f"malformed JSON body: {exc}") from exc
    if not isinstance(body, Mapping):
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "body must be a JSON object")
    return body


def _runner_from(request: Request, engine) -> dict[str, Any]:
    """Authenticate the runner token, then bind the request to its organization.

    The credential lookup runs in system context because the runner is not a
    tenant member and no tenant is known yet; everything after it is
    tenant-scoped, which is what makes the RLS probe suite meaningful here.
    """
    raw = request.headers.get("authorization", "") or ""
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
    if not token:
        raise LeaseRequestError(401, ERROR_UNAUTHORIZED, "a runner token is required")
    with tenant_context.system(), engine.connect() as conn:
        found = _row(conn, select(runner).where(and_(
            runner.c.lease_token_hash == token_hash(token),
            runner.c.enabled.is_(True),
            runner.c.revoked_at.is_(None),
        )))
    if not found:
        raise LeaseRequestError(401, ERROR_UNAUTHORIZED, "unknown or revoked runner token")
    tenant_context.bind(found["organization_id"])
    claimed = request.headers.get(HEADER_RUNNER)
    if claimed and claimed != found["id"]:
        raise LeaseRequestError(403, ERROR_UNAUTHORIZED, "this token does not belong to that runner")
    request.state.runner_id = found["id"]
    return found


def _run(call: Callable[[], Any]) -> Any:
    """Run a sync function that may reject, turning the rejection into a response."""
    try:
        return call()
    except LeaseRequestError as rejected:
        return rejected.response()


def runner_routes(engine) -> list[Route]:
    """The seven routes, as Starlette ``Route`` objects for ``create_app``."""

    async def register(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            payload = await run_in_threadpool(register_runner, engine, body)
        except LeaseRequestError as rejected:
            return rejected.response()
        return JSONResponse(payload)

    async def lease(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)
        try:
            poll_ms = int(body.get("poll_timeout_ms") or 0)
        except (TypeError, ValueError):
            return _error(400, ERROR_BAD_PARAMS, "poll_timeout_ms must be an integer")
        poll_ms = max(0, min(poll_ms, MAX_POLL_MS))
        deadline = time.monotonic() + poll_ms / 1000.0
        while True:
            granted = await run_in_threadpool(claim_lease, engine, runner_row)
            if granted:
                return JSONResponse({"lease": granted})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                refused = await run_in_threadpool(_refused_for, engine, runner_row)
                return JSONResponse({"refused": refused} if refused else {})
            # Bounded sleep: a long poll is many short waits, so shutting the
            # server down is never blocked behind a 60 s sleep.
            await asyncio.sleep(min(0.25, remaining))

    async def heartbeat(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)
        return await run_in_threadpool(_run, lambda: JSONResponse(
            heartbeat_lease(engine, runner_row, body, request)))

    async def events(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)
        return await run_in_threadpool(_run, lambda: JSONResponse(
            post_events(engine, runner_row, body, request)))

    async def finish(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)
        return await run_in_threadpool(_run, lambda: JSONResponse(
            finish_lease(engine, runner_row, body, request)))

    async def effect(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)

        def record() -> JSONResponse:
            lease_id, fence, lease_token = _lease_fields(body, request)
            with tenant_context.scoped(runner_row["organization_id"]), \
                    engine.connect() as conn:
                lease = _resolve_lease(conn, runner_row, lease_id, fence, lease_token)
            recorded = record_effect(
                engine,
                lease=lease,
                key=str(body.get("key") or ""),
                kind=str(body.get("kind") or "effect"),
                payload=dict(body.get("payload") or {}),
            )
            return JSONResponse({
                "accepted": True,
                "duplicate": bool(recorded.get("duplicate")),
                "key": recorded["key"],
                "external_ref": recorded.get("external_ref"),
            })

        return await run_in_threadpool(_run, record)

    async def release(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)
        return await run_in_threadpool(_run, lambda: JSONResponse(
            release_lease(engine, runner_row, body, request)))

    return [
        Route(ROUTE_REGISTER, register, methods=["POST"]),
        Route(ROUTE_LEASE, lease, methods=["POST"]),
        Route(ROUTE_HEARTBEAT, heartbeat, methods=["POST"]),
        Route(ROUTE_EVENTS, events, methods=["POST"]),
        Route(ROUTE_FINISH, finish, methods=["POST"]),
        Route(ROUTE_EFFECT, effect, methods=["POST"]),
        Route(ROUTE_RELEASE, release, methods=["POST"]),
    ]


def _refused_for(engine, runner_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    harnesses = _json_list(runner_row.get("harnesses"))
    with tenant_context.scoped(runner_row["organization_id"]), engine.connect() as conn:
        return _refused(conn, runner_row["organization_id"], harnesses)
