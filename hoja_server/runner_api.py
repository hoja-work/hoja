"""Runner lease API: register, lease, heartbeat, events, finish (HOJA-602-A-6, WP07).

The customer's machine is the data plane and nothing dials into it (plan.md §1).
A runner enrolls once, then long-polls for work, heartbeats while it holds a
lease, streams activity, and finishes with a claim. This module is the server
half of that contract; ``runner/hoja_client/wire.py`` is the shared,
contract-first definition of the wire and ``runner/hoja_client/client.py`` is
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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hoja_server import activity, agent_policy, audit, tenant_context, usage_ledger
from hoja_server.db import (
    agent_lease,
    agent_member,
    agent_session,
    agent_side_effect,
    comment,
    gen_token,
    issue,
    new_id,
    now_iso,
    organization,
    runner,
    runner_enrollment,
    token_hash,
    user,
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
ROUTE_LOGS = "/runner/logs"

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
# HOJA-712: the attempt names a runner pool this runner is not in. Reported rather
# than silently skipped, for the same reason ``harness_not_allowed`` is: an empty
# queue with no explanation is a support ticket.
ERROR_POOL_NOT_ALLOWED = "pool_not_allowed"
# WP12: the heartbeat cancel reason when a policy changed under a live lease.
POLICY_CHANGED = "policy_changed"
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
# HOJA-712: how many reaper ticks pass between automatic-verification sweeps. The
# finish route schedules verification directly; this is the catch-up for a process
# that died in between, so a minute of extra latency is affordable and a cross-tenant
# scan every ten seconds is not.
VERIFY_SWEEP_TICKS = 6

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


def default_harness_for(harnesses: Sequence[str]) -> str | None:
    """The one harness a runner falls back to when a host carries several (HOJA-602-A-20).

    Enrolling registers an *allowlist*, not a launch command, so before this rule a
    daemon on a host with more than one profile installed refused to start at all
    ("no default harness: pass --default-harness") — even though the enroll command
    the admin screen prints does not pass that flag, and every lease names its own
    harness anyway.

    The rule is the first harness of the enrolled allowlist, sorted, so it is
    deterministic: the same allowlist always answers the same name, whatever order
    the operator passed ``--allow-harness`` in, and the answer is a fact the control
    plane states once (in the register response) rather than one the daemon guesses
    locally on every start. ``None`` only when the allowlist is empty, which
    ``register_runner`` already refuses.
    """

    resolved = sorted({str(item).strip().lower() for item in harnesses if str(item).strip()})
    return resolved[0] if resolved else None


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
            # HOJA-635: a revoked enrollment is one whose ``expires_at`` was moved to
            # the server's now (there is no ``revoked_at`` column, and this change
            # adds none), so the two share this branch. Both answers are the same
            # refusal; the audit ledger is what says which one it was.
            raise LeaseRequestError(401, ERROR_UNAUTHORIZED,
                                    "this enrollment token has expired or been revoked")
        org_id = enrollment["organization_id"]

    token = gen_token()
    runner_id = new_id()
    created = now_iso()
    allowlist = sorted(set(harnesses))
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
                harnesses=json.dumps(allowlist),
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
        "harnesses": allowlist,
        # HOJA-602-A-20: named here, once, so the daemon that enrolls records the
        # control plane's answer instead of guessing when a later start has several
        # profiles to choose from.
        "default_harness": default_harness_for(allowlist),
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


def revoke_enrollment(engine, *, organization_id: str, enrollment_id: str) -> dict[str, Any] | None:
    """Take back an enrollment token nobody has spent yet (HOJA-635).

    ``runner_enrollment`` has no ``revoked_at`` column and HOJA-635 forbids a
    migration, so a revocation is expressed with the column the enroll route
    already refuses on: ``expires_at`` is moved to the server's now, which makes
    the token expired rather than deleted. Nothing is removed from the table, so
    the ledger of "this token existed and was withdrawn" survives, and the audit
    row ``runner.enrollment.revoke`` is what distinguishes a withdrawal from a
    token that simply aged out — the row itself cannot tell the two apart.

    A spent enrollment is not revoked: its plaintext is already burnt (``used_at``),
    so there is nothing left to take back and the runner it created is a separate
    row with its own revocation (``revoke_runner``).

    Returns the enrollment row, or ``None`` when this workspace has no such
    enrollment. ``revoked`` in the result says whether this call changed anything:
    revoking twice is a no-op, not an error.
    """
    with tenant_context.scoped(organization_id), engine.begin() as conn:
        row = _row(conn, select(runner_enrollment).where(and_(
            runner_enrollment.c.id == enrollment_id,
            runner_enrollment.c.organization_id == organization_id,
        )))
        if row is None:
            return None
        revoked = False
        # Only an enrollment that would still be accepted can be withdrawn. Without this
        # guard a second revoke would rewrite ``expires_at`` to a later "now" and report a
        # revocation that did not happen; the condition is what makes the call idempotent.
        expires = _parse_iso(row.get("expires_at"))
        still_usable = row.get("used_at") is None and (expires is None or expires > _now())
        if still_usable:
            stamp = _iso(_now())
            changed = conn.execute(runner_enrollment.update().where(and_(
                runner_enrollment.c.id == enrollment_id,
                runner_enrollment.c.organization_id == organization_id,
                runner_enrollment.c.used_at.is_(None),
            )).values(expires_at=stamp))
            revoked = bool(changed.rowcount)
            if revoked:
                row["expires_at"] = stamp
    return {**row, "revoked": revoked}


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

# Runner pools (HOJA-712). ``agent_member.runner_pool`` says *where* an agent's work
# may run; a runner says where it is through the labels it already registers, so no
# column is added and an operator who already labels a host keeps doing what they do:
# ``labels = {"pool": "gpu"}``, the same free-form object the schema documents as
# "pool, host, region". A list of names is accepted for a host that serves two pools.
# Pool names are compared trimmed and case-insensitively.
POOL_LABEL = "pool"


def runner_pools(runner_row: Mapping[str, Any]) -> frozenset[str]:
    """The pools this runner declared at registration. Empty means "no pool declared"."""
    raw = _json_dict(runner_row.get("labels")).get(POOL_LABEL)
    names = [raw] if isinstance(raw, str) else (
        list(raw) if isinstance(raw, (list, tuple)) else [])
    return frozenset(str(name).strip().lower() for name in names if str(name).strip())


def _agent_pool():
    """Scalar subquery: the pool of the agent member a lease's task is delegated to.

    NULL when the task is not delegated to an agent member, or the member declares no
    pool. Both mean "any runner", which is the behaviour that existed before pools and
    is deliberately unchanged — a pool is an extra restriction, never a precondition.
    """
    return select(agent_member.c.runner_pool).join(
        issue, issue.c.delegate_id == agent_member.c.user_id).where(and_(
            issue.c.id == agent_lease.c.task_id,
            agent_member.c.organization_id == agent_lease.c.organization_id,
        )).limit(1).scalar_subquery()


def _agent_pool_name():
    """``_agent_pool`` normalized the way runner labels are: trimmed, folded, empty→NULL."""
    return func.lower(func.nullif(func.trim(_agent_pool()), ""))


def _pool_grantable(pools: frozenset[str]):
    """Grant only when the agent's pool is unset or matches one this runner declares."""
    pool = _agent_pool_name()
    if not pools:
        return pool.is_(None)
    return or_(pool.is_(None), pool.in_(sorted(pools)))


#: How many queued attempts one claim looks at. A refused attempt is skipped rather than
#: retried at the head of the queue, so one agent whose policy refuses its work cannot
#: starve every other agent in the workspace; the window bounds the work one poll does.
GRANT_WINDOW = 25


def _grantable(conn, org_id: str, harnesses: Sequence[str],
               pools: frozenset[str] = frozenset(),
               limit: int = GRANT_WINDOW) -> list[dict[str, Any]]:
    """Queued attempts for a harness this runner declared, oldest first.

    Postgres takes the row lock with ``SKIP LOCKED`` so two runners claiming at
    the same instant take two different tasks instead of colliding; SQLite is
    the single-tenant local mode and serializes on its write lock anyway.

    A pooled agent's attempt is only visible to a runner inside that pool, and that
    restriction rides on the same statement as the harness filter: it narrows the
    candidate set *before* the lock, so an out-of-pool runner never holds a row it
    may not take and ``SKIP LOCKED`` keeps its meaning.
    """
    stmt = select(agent_lease.c.id, agent_lease.c.task_id, agent_lease.c.fence,
                  agent_lease.c.ttl_seconds, agent_lease.c.refusal_reason).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.state == LEASE_QUEUED,
        agent_lease.c.harness.in_(list(harnesses)),
    )).where(_pool_grantable(pools)
    ).order_by(agent_lease.c.created_at, agent_lease.c.fence).limit(max(1, int(limit)))
    if conn.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return [dict(row) for row in conn.execute(stmt).mappings()]


def _refused(conn, org_id: str, harnesses: Sequence[str]) -> list[dict[str, Any]]:
    """Queued attempts this runner may not take, for the operator's benefit."""
    rows = conn.execute(select(agent_lease.c.task_id, agent_lease.c.harness).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.state == LEASE_QUEUED,
        agent_lease.c.harness.notin_(list(harnesses)),
    )).limit(10))
    return [{"task_id": row[0], "harness": row[1], "reason": ERROR_HARNESS_NOT_ALLOWED}
            for row in rows]


def _pool_refused(conn, org_id: str, harnesses: Sequence[str],
                  pools: frozenset[str]) -> list[dict[str, Any]]:
    """Queued attempts whose agent's pool this runner is not in (HOJA-712).

    Same reason ``_harness_not_allowed`` exists: without it a runner that is one
    label away from the work sees a permanently empty queue and nothing to read.
    """
    if not harnesses:
        return []
    pool = _agent_pool_name()
    conditions = [agent_lease.c.organization_id == org_id,
                  agent_lease.c.state == LEASE_QUEUED,
                  agent_lease.c.harness.in_(list(harnesses)),
                  pool.is_not(None)]
    if pools:
        conditions.append(pool.notin_(sorted(pools)))
    rows = conn.execute(select(agent_lease.c.task_id, agent_lease.c.harness,
                               pool.label("pool")).where(and_(*conditions)).limit(10)).all()
    return [{"task_id": row[0], "harness": row[1],
             "reason": ERROR_POOL_NOT_ALLOWED, "pool": row[2]}
            for row in rows]


def _policy_refused(conn, org_id: str, harnesses: Sequence[str]) -> list[dict[str, Any]]:
    """Queued attempts this runner declared a harness for but the agent's policy refused.

    WP12: a lease the policy will not grant would otherwise be indistinguishable
    from "no work", and an operator would see a permanent empty queue with nothing
    to explain it. Reporting the policy's own reason code is what makes a
    deny-by-default policy diagnosable from the runner's side.
    """
    if not harnesses:
        return []

    rows = conn.execute(select(agent_lease).where(and_(
        agent_lease.c.organization_id == org_id,
        agent_lease.c.state == LEASE_QUEUED,
        agent_lease.c.harness.in_(list(harnesses)),
    )).order_by(agent_lease.c.created_at).limit(10)).mappings().all()
    refused: list[dict[str, Any]] = []
    for row in rows:
        verdict = agent_policy.check_lease(conn, org_id, dict(row))
        if not verdict.allowed:
            refused.append({
                "task_id": row["task_id"],
                "harness": row["harness"],
                "reason": verdict.refusal_code(),
                "detail": verdict.detail,
                "action": verdict.action,
            })
    return refused


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


def _bind_session_to_lease(conn, org_id: str, lease: Mapping[str, Any]) -> dict | None:
    """Mark the session this lease executes as leased — via the machine, not a write.

    The lease is WP07's row and the session is WP06's; this is the one place they are
    joined for real, so a session a runner is executing stops looking queued. A
    session the schedule check never admitted is left where it is rather than forced
    along the machine: a refused transition is a fact about the session, not a reason
    to fail a lease the runner already holds.
    """
    from hoja_server import agent_sessions

    row = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.lease_id == lease["id"],
    )).limit(1)).mappings().first()
    if not row:
        return None
    session = dict(row)
    if session["state"] != agent_sessions.QUEUED:
        return session
    try:
        return agent_sessions.transition(conn, session, "lease")
    except agent_sessions.RefusedTransitionError:
        LOG.warning("lease %s could not move session %s from queued to leased",
                    lease["id"], session["id"])
        return session


def _apply_finish_to_session(conn, org_id: str, lease: Mapping[str, Any], state: str, *,
                             claim: Mapping[str, Any], worker_model: str | None,
                             summary: str) -> dict | None:
    """Hand the finish to the session machine, which is what decides what remains true.

    A ``completed`` finish is a claim, and WP10 makes that move to ``verifying`` the
    only reachable outcome: this function cannot produce ``done``, and nothing else on
    the runner surface can either. A refused transition (a session a human already
    stopped, say) is logged and left alone — the finish itself still stands, because
    the runner's contract is with the lease, not with the session.
    """
    from hoja_server import agent_sessions, agent_verify

    session = agent_verify.session_for_lease(conn, org_id, dict(lease))
    if session is None:
        return None
    try:
        return agent_sessions.finish_session(
            conn, session, state, claim=dict(claim), worker_model=worker_model,
            reason=summary or None)
    except agent_sessions.RefusedTransitionError as exc:
        LOG.warning("lease %s finished %s but session %s took no finish: %s",
                    lease["id"], state, session["id"], exc)
        from hoja_server import agent_run_logs

        try:
            with conn.begin_nested():
                agent_run_logs.record(
                    conn, org_id, level="warning", kind="session.refused",
                    runner_id=lease.get("runner_id"), lease_id=lease.get("id"),
                    message=f"the {state} finish did not move session {session['id']}: {exc}")
        except Exception:  # noqa: BLE001 - the finish outranks its note
            LOG.debug("could not note a refused finish", exc_info=True)
        return agent_sessions.get_session(conn, org_id, session["id"])


def _claim_under_verification(conn, org_id: str, lease: Mapping[str, Any]) -> dict | None:
    """The session whose claim is being verified for this lease's task, if any."""
    from hoja_server import agent_sessions, agent_verify

    session = agent_verify.session_for_lease(conn, org_id, dict(lease))
    if session is not None and agent_sessions.under_verification(session):
        return session
    return None


def claim_lease(engine, runner_row: Mapping[str, Any]) -> dict[str, Any] | None:
    """One non-blocking attempt at granting a lease to ``runner_row``.

    WP12 (T053) inserts the agent's policy between "there is work" and "here is a
    lease": scope, concurrency and budget are checked against the policy row before
    the lease is awarded, and the version granted under is recorded on the lease so
    a later edit is detectable (see ``heartbeat_lease``).

    A refused attempt is skipped, not retried at the head of the queue: the claim walks
    the oldest ``GRANT_WINDOW`` attempts and grants the first one the policy allows. The
    refusal is stored on the lease (``refusal_reason``) and audited only when it changes,
    so a refused attempt costs one audit row per reason instead of one per poll.
    """

    granted, refusals = _claim_locked(engine, runner_row)
    from hoja_server import agent_run_logs

    for refusal in refusals:
        agent_run_logs.note(
            engine, runner_row["organization_id"],
            {"id": refusal.get("lease_id"), "runner_id": None},
            f"not granted: {refusal['reason']} ({refusal.get('action')})",
            level="warning", kind="lease.refused", detail=dict(refusal.get("detail") or {}))
        agent_policy.record_refusal(
            engine,
            organization_id=runner_row["organization_id"],
            actor_kind="bot",
            actor_id=str(runner_row.get("id") or "runner"),
            reason=str(refusal["reason"]),
            detail={**dict(refusal.get("detail") or {}),
                    "lease_id": refusal.get("lease_id"),
                    "task_id": refusal.get("task_id"),
                    "policy_action": refusal.get("action")},
            target_id=str(refusal.get("lease_id") or ""),
        )
    return granted


def _claim_locked(engine, runner_row: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict]]:
    """The transaction half of ``claim_lease``: (lease payload or None, new refusals)."""

    org_id = runner_row["organization_id"]
    harnesses = _json_list(runner_row.get("harnesses"))
    pools = runner_pools(runner_row)
    stamp = _now()
    refusals: list[dict] = []
    with tenant_context.scoped(org_id), engine.begin() as conn:
        if not harnesses:
            return None, refusals
        candidate = None
        verdict = None
        for row in _grantable(conn, org_id, harnesses, pools):
            check = agent_policy.check_lease(conn, org_id, row)
            if check.allowed:
                candidate, verdict = row, check
                break
            code = check.refusal_code()
            values: dict[str, Any] = {}
            if check.action == agent_policy.STOP:
                # 'stop' is recorded on the lease, not only refused: otherwise the
                # same exhausted attempt is re-examined on every poll forever.
                values = {"state": LEASE_CANCELED, "cancel_requested_at": now_iso(),
                          "cancel_reason": code, "lost_reason": code}
            if code != row.get("refusal_reason") or values:
                conn.execute(agent_lease.update().where(and_(
                    agent_lease.c.id == row["id"],
                    agent_lease.c.state == LEASE_QUEUED,
                    agent_lease.c.fence == row["fence"],
                )).values(refusal_reason=code, updated_at=now_iso(), **values))
                refusals.append({
                    "lease_id": row["id"], "task_id": row["task_id"],
                    "fence": row["fence"], "reason": code,
                    "action": check.action, "detail": check.detail,
                })
        if candidate is None or verdict is None:
            return None, refusals
        lease_token = gen_token()
        until = _iso(stamp + timedelta(seconds=int(candidate["ttl_seconds"])))
        awarded = conn.execute(agent_lease.update().where(and_(
            agent_lease.c.id == candidate["id"],
            agent_lease.c.state == LEASE_QUEUED,
            agent_lease.c.fence == candidate["fence"],
        )).values(
            state=LEASE_ACTIVE,
            runner_id=runner_row["id"],
            lease_token_hash=token_hash(lease_token),
            lease_until=until,
            granted_at=now_iso(),
            policy_version=verdict.policy_version,
            refusal_reason=None,
            updated_at=now_iso(),
        ))
        if awarded.rowcount != 1:
            # Another runner won the same row between our read and our write.
            return None, refusals
        lease = _row(conn, select(agent_lease).where(agent_lease.c.id == candidate["id"]))
        if lease is None:  # pragma: no cover - the row we just updated
            return None, refusals
        lease["token"] = lease_token
        org_key = conn.execute(select(organization.c.url_key).where(
            organization.c.id == org_id)).scalar()
        # The session a delegated task opened follows the lease it was queued for.
        _bind_session_to_lease(conn, org_id, lease)
        policy = None
        view = None
        if verdict.agent_member_id:
            view = agent_policy.view_for_member(conn, org_id, verdict.agent_member_id)
            if view is not None:
                # Secrets and egress travel here, in the lease payload, and never in
                # the prompt: the prompt is the task text and lands in the transcript.
                policy = agent_policy.lease_payload(conn, org_id, view)
        run_spec = _run_spec(conn, org_id, lease, verdict.agent_member_id, view)
    from hoja_server import agent_run_logs

    where = run_spec["repo"]
    agent_run_logs.note(
        engine, runner_row["organization_id"], {**lease, "runner_id": runner_row["id"]},
        (f"granted to {runner_row.get('name')}; works in {where['repo']} on {where['branch']} "
         f"({'from project ' + str(where.get('project')) if where.get('source') == 'project' else 'from the agent' + chr(39) + 's repo grant'})"
         if where else f"granted to {runner_row.get('name')}; no repository: a scratch folder"),
        kind="lease.grant", detail={"repo": where, "harness": lease["harness"]})
    _audit_lease(engine, "agent.lease.grant", lease, runner_row,
                 detail={"fence": lease["fence"], "attempt": lease["attempt"],
                         "harness": lease["harness"], "ttl_s": lease["ttl_seconds"],
                         "policy_version": lease.get("policy_version")})
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
        # The agent member's configured model. Null when the member names none; the
        # harness then uses its own default.
        "model": run_spec["model"],
        # What the run is about, as data: the verifier answers these acceptance items one
        # by one, so a harness that is given them can answer them verbatim.
        "task": run_spec["task"],
        # The repository to work in, from the agent's repo grant: {repo, url, branch,
        # base}. Null when the policy grants no repo; the runner then gives the run an
        # empty scratch directory, never the daemon's own working directory.
        "repo": run_spec["repo"],
        "labels": _json_dict(runner_row.get("labels")),
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
        # WP12: what this agent may touch, and the values it may use, for this
        # lease only.
        "policy": policy,
    }, refusals


def _clone_url(repo: str) -> str:
    """Where a granted repo is cloned from. ``owner/name`` is GitHub; anything else is a URL
    or a path the operator's git already understands (a local mirror, an SSH remote)."""
    if "://" in repo or repo.startswith(("git@", "/", "file:")):
        return repo
    return f"https://github.com/{repo}.git"


def _run_spec(conn, org_id: str, lease: Mapping[str, Any], agent_member_id: str | None,
              view: Any) -> dict[str, Any]:
    """The model, the task facts and the repository a granted lease runs with."""
    from hoja_server import agent_verify

    found = _row(conn, select(issue.c.identifier, issue.c.title, issue.c.description).where(
        and_(issue.c.id == lease["task_id"], issue.c.organization_id == org_id)))
    task = None
    if found:
        task = {"id": lease["task_id"], "identifier": found["identifier"],
                "title": found["title"],
                "acceptance": agent_verify.acceptance_items(found["description"])}
    model = None
    if agent_member_id:
        model = conn.execute(select(agent_member.c.model).where(and_(
            agent_member.c.organization_id == org_id,
            agent_member.c.id == agent_member_id))).scalar() or None
    # Where the run works (hoja_server/project_repos.py): the task's project's repository, else
    # the agent's own repo grant, else nothing (a scratch folder). Delegating the task is the
    # permission to work on it there (agent_policy: a delegated task is the grant itself).
    from hoja_server import project_repos

    repo = None
    project_repo, project_name = project_repos.repository_for_task(conn, org_id, lease["task_id"])
    grants = tuple(getattr(view, "repos", ()) or ()) if view is not None else ()
    if project_repo:
        identifier = str((task or {}).get("identifier") or lease["task_id"]).lower()
        repo = {"repo": project_repo, "url": _clone_url(project_repo),
                "branch": f"{project_repos.BRANCH_PREFIX}{identifier}",
                "source": "project", "project": project_name}
    elif grants:
        grant = grants[0]
        # The first branch the grant names is the one the run pushes to: a grant is an
        # explicit list (no wildcard, never a default branch), so any entry is allowed.
        branch = grant.branches[0] if grant.branches else None
        if branch and branch.endswith("/") and task:
            # A prefix grant (``agent/``): each task works on its own branch under it, so two
            # tasks on the same repo never share (or fight over) one branch.
            branch = f"{branch}{str(task['identifier']).lower()}"
        repo = {"repo": grant.repo, "url": _clone_url(grant.repo), "branch": branch,
                "source": "agent"}
    return {"model": model, "task": task, "repo": repo}


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
            # WP12 (T053): a lease is granted under one policy version. If the
            # policy changed while the work was running, the runner is told to stop
            # rather than left holding grants a human has already withdrawn.
            policy_drift = _policy_drift(conn, org_id, lease)
        deadline = _parse_iso(lease["lease_until"])
        if deadline is not None and deadline < _now():
            raise LeaseRequestError(409, ERROR_EXPIRED,
                                    "this lease expired before the heartbeat; it is no longer yours")
        stamp = _iso(_now())
        ttl = int(lease["ttl_seconds"] or LEASE_TTL_S)
        extends_to = _iso(_now() + timedelta(seconds=ttl))
        changed = {}
        if policy_drift:
            changed = {"cancel_requested_at": stamp, "cancel_reason": POLICY_CHANGED}
        with engine.begin() as conn:
            updated = conn.execute(agent_lease.update().where(and_(
                agent_lease.c.id == lease["id"],
                agent_lease.c.fence == lease["fence"],
                agent_lease.c.state == LEASE_ACTIVE,
            )).values(lease_until=extends_to, last_heartbeat_at=stamp, updated_at=stamp,
                      **changed))
            if updated.rowcount != 1:
                raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                        "the lease was re-granted while this heartbeat was in flight")
    _touch_runner(engine, runner_row, stamp)
    cancel = bool(lease.get("cancel_requested_at")) or policy_drift
    reason = POLICY_CHANGED if policy_drift else (
        lease.get("cancel_reason") if cancel else None)
    # WP09: an exposure an admin revoked is closed in the record already, so the process is the
    # only thing left to kill. The heartbeat is where WP07 already delivers an instruction to a
    # runner mid-lease, so it is where this one travels; a runner that predates the field ignores
    # it, and the record still says the workspace stopped exposing the port.
    from hoja_server import connect_api

    revoke = connect_api.pending_revocations(engine, runner_row)
    with tenant_context.scoped(org_id), engine.connect() as conn:
        answers = pending_answers(conn, org_id, lease)
    return {
        "fence": lease["fence"],
        # A person's answers to the questions this run asked (HOJA-602 e2e loop).
        "answers": answers,
        # How long this server will hold a heartbeat open when the runner asks it to
        # (``wait_ms``): a cancel or an answer ends the wait at once, so the runner can
        # beat every few seconds instead of four times a second and still hear a stop
        # inside the NFR-005 budget.
        "max_wait_ms": MAX_HEARTBEAT_WAIT_MS,
        "cancel": cancel,
        "cancel_reason": reason,
        # The cancel is the instruction; the version is the evidence for why it
        # arrived, so a runner can log which policy it was stopped by.
        "policy_version": lease.get("policy_version"),
        "policy_changed": policy_drift,
        "ttl_s": ttl,
        "lease_state": LEASE_ACTIVE,
        "lease_until": extends_to,
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
        "connect_revoke": revoke,
    }


#: The longest a heartbeat is held open for a waiting runner. Well under the minimum TTL,
#: so a held beat can never be what lets a lease expire.
MAX_HEARTBEAT_WAIT_MS = 5_000
#: How often a held heartbeat re-reads its lease for a cancel or an answer.
HEARTBEAT_WATCH_S = 0.2


def _heartbeat_signal(engine, org_id: str, lease_id: str, waiting: Sequence[str]) -> bool:
    """Has anything the runner must hear happened? One indexed read, no writes.

    Only the cheap facts are watched: a cancel request, the lease leaving ``active``,
    and an answer to one of the asks the runner said it is waiting on. Everything else
    (policy drift, revocations) is re-read by the next full beat a few seconds later.
    """
    with tenant_context.scoped(org_id), engine.connect() as conn:
        row = _row(conn, select(agent_lease.c.state, agent_lease.c.cancel_requested_at,
                                agent_lease.c.task_id, agent_lease.c.id).where(and_(
            agent_lease.c.organization_id == org_id, agent_lease.c.id == lease_id)))
        if row is None or row["state"] != LEASE_ACTIVE or row["cancel_requested_at"]:
            return True
        if waiting:
            answered = {a["ask_id"] for a in pending_answers(conn, org_id, row)}
            if answered & set(waiting):
                return True
    return False


def _policy_drift(conn, org_id: str, lease: Mapping[str, Any]) -> bool:
    """True when the agent policy changed since this lease was granted (WP12).

    A lease with no recorded version predates the policy surface entirely, so it
    cannot have drifted from a version it never had.
    """

    granted_under = lease.get("policy_version")
    if granted_under is None:
        return False
    agent_user_id = agent_policy.agent_user_for_task(conn, org_id, str(lease.get("task_id") or ""))
    if not agent_user_id:
        return False
    _, view = agent_policy.view_for_user(conn, org_id, agent_user_id)
    if view is None:
        # The policy was deleted mid-run: the grants are gone, so stop.
        return True
    return int(view.version) != int(granted_under)


def _touch_runner(engine, runner_row: Mapping[str, Any], stamp: str | None = None) -> bool:
    """Record liveness for one runner, at most once per heartbeat window.

    Two callers mean "this machine is alive": the heartbeat route, which is the
    original WP07 evidence, and — since HOJA-602-A-20 — the lease long-poll. The
    second one matters because a daemon with no work holds no lease, so it never
    heartbeats, and a runner that is demonstrably up (it holds a
    connection open for the whole poll window) used to freeze at its last heartbeat
    and be drawn as offline in Settings -> Runners. `agent_runners.ONLINE_WINDOW_S`
    then never saw it come back until it was granted something.

    Rate limiting is the point of the shape: the poll sleeps in 250 ms slices for up
    to a minute, so an unconditional write would turn one idle machine into a write
    every quarter second. The window is `HEARTBEAT_INTERVAL_S`, the same cadence the
    server asks a beating runner for, and the guard is read *inside* the
    transaction and re-stated as a predicate on the update, so two concurrent polls
    of the same runner (and a heartbeat racing a poll) still produce one write per
    window: the conditional update is what makes the limit hold across processes,
    not just within one request.

    Returns True when the row was written. Every statement is inside
    `tenant_context.scoped(org)` and carries an explicit `organization_id`
    predicate, like every other runner write: this is a tenant row, not a shared
    counter.
    """

    org_id = runner_row["organization_id"]
    stamp = stamp or now_iso()
    now = _parse_iso(stamp) or _now()
    cutoff = now - timedelta(seconds=HEARTBEAT_INTERVAL_S)
    with tenant_context.scoped(org_id), engine.begin() as conn:
        seen = conn.execute(select(runner.c.last_seen).where(and_(
            runner.c.id == runner_row["id"],
            runner.c.organization_id == org_id,
        ))).scalar()
        previous = _parse_iso(seen)
        if previous is not None and previous.tzinfo is None:
            previous = previous.replace(tzinfo=now.tzinfo)
        if previous is not None and previous >= cutoff:
            return False
        # Conditional on the value just read: if anything moved it in between, this
        # update matches nothing and the other writer's stamp stands.
        unchanged = (runner.c.last_seen.is_(None) if seen is None
                     else runner.c.last_seen == seen)
        written = conn.execute(runner.update().where(and_(
            runner.c.id == runner_row["id"],
            runner.c.organization_id == org_id,
            unchanged,
        )).values(last_seen=stamp))
        return bool(written.rowcount)


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
                if kind == "usage":
                    # WP14: a usage report is metered, not narrated. It advances the
                    # stream's sequence like any other event, but writing billing
                    # counters into the transcript would put them in front of a person
                    # reading the run.
                    record_usage_report(conn, org_id, lease, payload,
                                        source=usage_ledger.SOURCE_EVENTS)
                    accepted = max(accepted, seq)
                    continue
                encoded = json.dumps(payload, sort_keys=True, default=str)
                if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
                    continue
                # WP12: a runner that echoes its environment must not be able to
                # write an injected secret into the activity stream. Scrubbed before
                # the row exists, so the value is never stored to be redacted later.
                encoded = agent_policy.scrub_lease_text(conn, org_id, lease, encoded)
                _log_event(conn, org_id, lease, kind, encoded)
                if kind in SESSION_EVENT_KINDS:
                    # The session machine's own inputs: started, asked, said. They go
                    # to the session transcript (what the agent screens read), not to
                    # the issue's activity log.
                    _apply_session_event(conn, org_id, lease, kind, json.loads(encoded))
                else:
                    issue_activity_record(conn, org_id, lease, kind, encoded)
                    if kind in START_EVENT_KINDS:
                        _apply_session_event(conn, org_id, lease, EVENT_STARTED, {})
                accepted = max(accepted, seq)
            if accepted:
                conn.execute(agent_lease.update().where(agent_lease.c.id == lease["id"]).values(
                    last_event_seq=accepted, updated_at=now_iso()))
    return {"accepted_seq": accepted or int(lease["last_event_seq"] or 0)}


#: Runner events that move the session or write its transcript (HOJA-602 e2e loop).
EVENT_STARTED = "session.started"
EVENT_ASK = "session.ask"
EVENT_ACTION = "session.action"
EVENT_THOUGHT = "session.thought"
SESSION_EVENT_KINDS = frozenset({EVENT_STARTED, EVENT_ASK, EVENT_ACTION, EVENT_THOUGHT})
#: Older runner events that also mean "the harness is running": a runner that predates
#: ``session.started`` still moves its session out of ``leased``.
START_EVENT_KINDS = frozenset({"harness.spawned", "harness.prompted"})
#: How much of one transcript line a runner may write.
MAX_SESSION_BODY = 2000


def _apply_session_event(conn, org_id: str, lease: Mapping[str, Any], kind: str,
                         payload: Mapping[str, Any]) -> None:
    """Drive the session a lease executes from what its runner reports.

    The machine had edges for ``start`` and ``await_input`` that nothing ever sent: a
    session sat in ``leased`` for its whole run and could not reach ``awaiting_input``,
    so a harness's question had nowhere to go. A refused transition is logged, never
    raised: the event stream is the runner's contract with the lease.
    """
    from hoja_server import agent_sessions, agent_verify

    session = agent_verify.session_for_lease(conn, org_id, dict(lease))
    if session is None or session["state"] in agent_sessions.TERMINAL:
        return
    try:
        if kind == EVENT_STARTED:
            if session["state"] == agent_sessions.QUEUED:
                session = agent_sessions.transition(conn, session, "lease")
            if session["state"] == agent_sessions.LEASED:
                agent_sessions.transition(conn, session, "start")
        elif kind == EVENT_ASK:
            questions = payload.get("questions")
            ask_id = str(payload.get("ask_id") or "")
            if not ask_id or not isinstance(questions, list):
                return
            if session["state"] == agent_sessions.LEASED:
                session = agent_sessions.transition(conn, session, "start")
            agent_sessions.append_activity(
                conn, org_id, session["id"], "elicitation",
                body=str(payload.get("body") or _ask_summary(questions))[:MAX_SESSION_BODY],
                payload={"ask_id": ask_id, "questions": questions})
            if session["state"] == agent_sessions.RUNNING:
                agent_sessions.transition(conn, session, "await_input")
        else:
            body = str(payload.get("body") or "").strip()
            if not body:
                return
            agent_sessions.append_activity(
                conn, org_id, session["id"],
                "action" if kind == EVENT_ACTION else "thought",
                body=body[:MAX_SESSION_BODY],
                payload={k: v for k, v in payload.items() if k != "body"} or None)
    except agent_sessions.RefusedTransitionError as exc:
        LOG.info("lease %s event %s left session %s %s: %s", lease.get("id"), kind,
                 session["id"], session["state"], exc)


def _ask_summary(questions: Sequence[Any]) -> str:
    texts = [str(q.get("question") or "") for q in questions if isinstance(q, Mapping)]
    return " ".join(t for t in texts if t) or "The agent has a question."


def pending_answers(conn, org_id: str, lease: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Answers a person gave to this run's questions, for the heartbeat to deliver.

    Every answered ask of the lease's session is returned on every beat; the runner
    keeps the ids it is waiting on and ignores the rest, so a lost response costs a
    repeat, never an answer.
    """
    from hoja_server import agent_sessions, agent_verify
    from hoja_server.db import agent_activity

    session = agent_verify.session_for_lease(conn, org_id, dict(lease))
    if session is None or session["state"] in agent_sessions.TERMINAL:
        return []
    rows = conn.execute(select(agent_activity.c.payload).where(and_(
        agent_activity.c.organization_id == org_id,
        agent_activity.c.session_id == session["id"],
        agent_activity.c.kind == "prompt",
    )).order_by(agent_activity.c.created_at)).scalars().all()
    out = []
    for raw in rows:
        payload = _json_dict(raw)
        if payload.get("ask_id"):
            out.append({"ask_id": payload["ask_id"], "response": payload.get("response") or {}})
    return out


def _log_event(conn, org_id: str, lease: Mapping[str, Any], kind: str, encoded: str) -> None:
    """Copy one runner event into the run's timeline (agent_run_logs), never failing the batch."""
    from hoja_server import agent_run_logs

    try:
        with conn.begin_nested():
            payload = json.loads(encoded) if encoded else {}
            summary = str(payload.get("body") or payload.get("reason") or "")[:300]
            if payload.get("tool"):
                summary = f"{payload['tool']} {summary}".strip()
            agent_run_logs.record(conn, org_id, message=f"{kind}{': ' + summary if summary else ''}",
                                  level="event", source="runner", kind=kind,
                                  runner_id=lease.get("runner_id"), lease_id=lease.get("id"),
                                  detail=payload if isinstance(payload, dict) else None)
    except Exception:  # noqa: BLE001 - the event itself is already stored
        LOG.debug("could not copy event %s to the run log", kind, exc_info=True)


def post_logs(engine, runner_row: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """Store log lines a runner shipped (HOJA-602), optionally tagged with a lease it holds.

    The lease is checked for ownership, not for a live fence: the lines that explain why a run
    was lost arrive after it was, and they are the ones worth keeping.
    """
    from hoja_server import agent_run_logs

    lines = body.get("lines")
    if not isinstance(lines, list):
        raise LeaseRequestError(400, ERROR_BAD_PARAMS, "lines must be a list")
    org_id = runner_row["organization_id"]
    lease_id = str(body.get("lease_id") or "").strip()
    with tenant_context.scoped(org_id), engine.begin() as conn:
        lease = None
        if lease_id:
            lease = _row(conn, select(agent_lease).where(and_(
                agent_lease.c.organization_id == org_id,
                agent_lease.c.id == lease_id,
                agent_lease.c.runner_id == runner_row["id"],
            )))
        scrub = (lambda text: agent_policy.scrub_lease_text(conn, org_id, lease, text)) \
            if lease else None
        kept = agent_run_logs.ingest(conn, org_id, runner_row["id"], lease, lines, scrub=scrub)
    return {"accepted": kept}


def issue_activity_record(conn, org_id: str, lease: Mapping[str, Any], kind: str,
                          payload: str) -> None:
    """Put one runner event on the task's timeline, attributed to the agent doing the run.

    HOJA-728: these rows used to carry no actor, so the timeline showed "Someone updated
    the task" for every lease a runner claimed. The run belongs to the delegated agent;
    only a run on a task nobody delegated stays actor-less (rendered as Hoja, the system).
    """
    agent_user_id, _session_id = _usage_agent(conn, org_id, lease)
    activity.record(conn, org_id=org_id, issue_id=lease["task_id"], actor_id=agent_user_id,
                    event_type=f"runner.{kind}"[:80], changes=payload, created_at=now_iso())


# ---------------------------------------------------------------------------
# Usage metering (WP14)
# ---------------------------------------------------------------------------


def _usage_agent(conn, org_id: str, lease: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Who a run's usage belongs to: (agent user id, session id).

    The session is the authority when the lease has one, because the session row is
    what the usage screen links a run back to. A lease on a task nobody delegated has
    no agent, and a usage row with no agent would be a number the screen could never
    explain — so the caller logs and stores nothing rather than inventing an owner.
    """
    from hoja_server import agent_verify

    session = agent_verify.session_for_lease(conn, org_id, dict(lease))
    agent_user_id = (session or {}).get("agent_user_id") or agent_policy.agent_user_for_task(
        conn, org_id, str(lease.get("task_id") or ""))
    return (str(agent_user_id) if agent_user_id else None,
            str(session["id"]) if session else None)


def record_usage_report(conn, org_id: str, lease: Mapping[str, Any], document: Any, *,
                        source: str) -> dict[str, Any] | None:
    """Meter one report from a runner. Never raises for a malformed report.

    Metering is downstream of the run: a run whose token counts arrive in a shape this
    server cannot read is still a run the runner did, and failing the finish over it
    would throw the claim away to punish the meter. A bad report is logged and
    dropped; whatever already arrived stays in the ledger.
    """
    try:
        usage = usage_ledger.usage_report({"usage": document})
    except usage_ledger.ReportError as exc:
        LOG.warning("lease %s reported usage this server cannot read: %s", lease.get("id"), exc)
        return None
    if usage is None:
        return None
    agent_user_id, session_id = _usage_agent(conn, org_id, lease)
    if not agent_user_id:
        LOG.warning("lease %s reported usage for a task with no delegated agent; "
                    "nothing recorded", lease.get("id"))
        return None
    try:
        return usage_ledger.record_report(
            conn,
            organization_id=org_id,
            run_id=str(lease.get("run_id") or lease.get("id") or ""),
            agent_user_id=agent_user_id,
            session_id=session_id,
            lease_id=str(lease.get("id") or "") or None,
            usage=usage,
            source=source,
        )
    except usage_ledger.ReportError as exc:
        LOG.warning("lease %s reported usage that was refused: %s", lease.get("id"), exc)
        return None


# ---------------------------------------------------------------------------
# The agent's reply on the task (HOJA-602-A-17)
# ---------------------------------------------------------------------------

#: How much of the harness's answer becomes the comment. Comment bodies are read on a page, and a
#: 4 KB answer is already the whole of what a persona wants; the run itself holds the rest.
MAX_FINISH_COMMENT_CHARS = 4000

#: Where the run can be read: the agent's own run queue, which is the page that lists its
#: sessions. There is no per-session route yet (``AgentsSurface`` calls the task page's inline
#: session view "a later slice"), so this is the page a run is actually findable on, and the
#: comment names the session id beside it rather than promising a permalink that does not exist.
AGENT_QUEUE_PATH = "/workspace/agents/{agent_id}/queue"


def _origin_of(url: Any) -> str | None:
    """``scheme://host`` from a stored absolute URL, or None when it is not one."""
    text = str(url or "").strip()
    if not text:
        return None
    scheme, sep, rest = text.partition("://")
    if not sep or not rest:
        return None
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}" if host else None


def _answer_from_effects(effects: Sequence[Mapping[str, Any]]) -> str | None:
    """The harness's final answer, as the daemon already sent it on the claim effect."""
    for effect in effects:
        if not isinstance(effect, Mapping):
            continue
        payload = effect.get("payload")
        if not isinstance(payload, Mapping):
            continue
        answer = payload.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
    return None


def finish_comment_body(*, state: str, summary: str, answer: str | None,
                        agent_name: str, harness: str, model: str | None,
                        session_id: str, link: str | None) -> str:
    """What the person who delegated the task reads on it.

    The answer when the harness produced one, otherwise the run's own summary -- and never a
    comment that says nothing, because "the agent finished" with no words is the noise this
    change exists to remove. The provenance line and the link are what make the reply an
    account of a run rather than an unattributed opinion.
    """
    if answer:
        body = answer[:MAX_FINISH_COMMENT_CHARS]
    elif summary.strip():
        body = summary.strip()[:MAX_FINISH_COMMENT_CHARS]
    else:
        body = f"The run finished with claim `{state}`."
    provenance = f"Run by {agent_name} · harness {harness} · model {model or 'unknown'}"
    footer = f"_{provenance} · session {session_id}_"
    if link:
        footer = f"{footer}\n\n[All runs for this agent]({link})"
    return f"{body}\n\n{footer}"


def post_finish_comment(conn, org_id: str, lease: Mapping[str, Any], *,
                        session: Mapping[str, Any], state: str, summary: str,
                        answer: str | None, model: str | None) -> str | None:
    """Write the agent's one comment on the task this lease ran, and say its id.

    Called inside the finish's own transaction, which is what makes it exactly-once: the lease
    CAS and the comment commit together, so a re-delivered finish finds the lease already
    finished under the same ``run_id + seq`` key and returns before reaching this code, while a
    crash before the commit leaves neither behind. Nothing here re-reads or edits a comment; a
    later finish under a different key is refused by the fence/claim rules above it.

    The author is the agent user, the organization is the lease's, and the row is the same shape
    ``writer.comment_create`` writes, so the task page renders it like any other comment.
    """
    issue_row = conn.execute(select(issue.c.id, issue.c.url).where(and_(
        issue.c.organization_id == org_id,
        issue.c.id == lease["task_id"],
    )).limit(1)).mappings().first()
    if issue_row is None:
        LOG.warning("lease %s ran a task that no longer exists; no finish comment",
                    lease.get("id"))
        return None
    agent_id = str(session.get("agent_user_id") or "")
    if not agent_id:
        # A lease on a task nobody delegated has no agent to speak for it.
        return None
    name = conn.execute(select(user.c.name).where(and_(
        user.c.id == agent_id, user.c.organization_id == org_id))).scalar()
    origin = _origin_of(issue_row["url"])
    link = f"{origin}{AGENT_QUEUE_PATH.format(agent_id=agent_id)}" if origin else None
    body = finish_comment_body(
        state=state, summary=summary, answer=answer,
        agent_name=str(name or "the agent"),
        harness=str(lease.get("harness") or "unknown"),
        model=model, session_id=str(session.get("id") or ""), link=link)
    stamp = now_iso()
    comment_id = new_id()
    conn.execute(comment.insert().values(
        id=comment_id,
        organization_id=org_id,
        issue_id=issue_row["id"],
        body=body,
        user_id=agent_id,
        url=f"{issue_row['url']}#comment-{comment_id[:8]}" if issue_row["url"] else None,
        created_at=stamp,
        updated_at=stamp,
    ))
    # The task's own timeline says a comment arrived, exactly as a human's would.
    activity.record(conn, org_id=org_id, issue_id=issue_row["id"], actor_id=agent_id,
                    event_type="commentCreated", changes={"commentId": comment_id},
                    created_at=stamp)
    return comment_id


def _audit_finish_comment(engine, org_id: str, lease: Mapping[str, Any],
                          runner_row: Mapping[str, Any], *, comment_id: str,
                          session: Mapping[str, Any], model: str | None) -> None:
    """One audit row for the agent's write. A failure to audit never fails the finish."""
    try:
        audit.record(
            engine,
            organization_id=org_id,
            action="agent.session.comment",
            actor_kind="agent",
            actor_id=str(session.get("agent_user_id") or ""),
            on_behalf_of=str(session.get("creator_id") or "") or None,
            target_kind="comment",
            target_id=comment_id,
            runner=str(runner_row.get("name") or ""),
            detail={"lease_id": str(lease.get("id") or ""),
                    "issue_id": str(lease.get("task_id") or ""),
                    "session_id": str(session.get("id") or ""),
                    "model": model,
                    "harness": str(lease.get("harness") or "")},
        )
    except Exception:
        LOG.warning("could not audit the finish comment %s on lease %s",
                    comment_id, lease.get("id"), exc_info=True)


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
        comment_id: str | None = None
        # WP10: the claim, as the verifier will re-read it. Immutable once stored —
        # a later finish under a different key is refused rather than allowed to
        # rewrite the evidence, so the last writer never decides what was claimed.
        worker_model = str(body.get("model") or "").strip() or None
        pr_claim = body.get("pull_request")
        pr_claim = dict(pr_claim) if isinstance(pr_claim, Mapping) else {}
        if not pr_claim and ack_effect.get("external_ref"):
            # The ack already names the object the run produced; a runner that does
            # not repeat it in the claim still has it recorded.
            pr_claim = {"url": ack_effect["external_ref"]}
        claim = {
            "state": state,
            "summary": summary,
            "evidence": dict(evidence),
            "model": worker_model,
            "acceptance": body.get("acceptance") or [],
            # The repository the run was given, so the verifier asks a run that was meant to
            # change code for its pull request (and does not ask one that was not).
            "repo": body.get("repo") if isinstance(body.get("repo"), Mapping) else None,
            "pull_request": pr_claim,
            "merged_sha": body.get("merged_sha"),
            "deployed_revision": body.get("deployed_revision"),
            "effects": [{"key": e.get("key"), "kind": e.get("kind")} for e in effects],
            "idempotency_key": key,
            "submitted_at": stamp,
        }
        with tenant_context.scoped(org_id), engine.begin() as conn:
            # The session moves first, inside this transaction: if the lease CAS
            # below fails, the raise rolls both writes back, so a stale finish can
            # never leave a session awaiting verification.
            session_row = _apply_finish_to_session(
                conn, org_id, lease, state, claim=claim,
                worker_model=worker_model, summary=summary)
            if session_row is not None:
                # The machine's answer, not the design's hope: a refused transition
                # reports the state that is actually true.
                session_state = session_row["state"]
            # HOJA-602-A-17: the person who delegated this task gets an answer on the task,
            # written by the agent, in the same transaction as the finish itself. Inside the
            # transaction is what makes "exactly once" true: a re-delivered finish returns from
            # the duplicate branch above without reaching this line, and a crash rolls the
            # comment back with the lease change so the retry writes both again.
            if session_row is not None:
                try:
                    # A savepoint, so a comment that cannot be written (a task deleted under a
                    # running lease, say) cannot take the claim down with it: the runner's
                    # contract is with the lease, and the claim is the record that matters.
                    with conn.begin_nested():
                        comment_id = post_finish_comment(
                            conn, org_id, lease, session=session_row, state=state,
                            summary=summary, answer=_answer_from_effects(effects),
                            model=worker_model)
                except Exception:
                    LOG.warning("lease %s finished but its comment could not be written",
                                lease.get("id"), exc_info=True)
            # WP14: the finish carries the run's cumulative token counts and elapsed
            # compute. The ledger stores the difference from what the event stream
            # already reported, so a run that streamed to the end is not counted twice.
            record_usage_report(conn, org_id, lease, body.get("usage"),
                                source=usage_ledger.SOURCE_FINISH)
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
                                   "session_id": session_row["id"] if session_row else None,
                                   "external_ref": ack_effect.get("external_ref")},
                                  sort_keys=True, default=str),
            ))
            if updated.rowcount != 1:
                raise LeaseRequestError(409, ERROR_STALE_FENCE,
                                        "the lease was re-granted while this finish was in flight")
        if comment_id and session_row is not None:
            _audit_finish_comment(engine, org_id, lease, runner_row, comment_id=comment_id,
                                  session=session_row, model=worker_model)
        from hoja_server import agent_run_logs

        agent_run_logs.note(
            engine, org_id, lease,
            f"finished {state}: {summary or 'no summary'} (session {session_state})",
            level="error" if state == "failed" else "info", kind="run.finish",
            detail={"state": state, "session_state": session_state,
                    "external_ref": ack_effect.get("external_ref"), "model": worker_model})
        _audit_lease(engine, "agent.session.stop", lease, runner_row,
                     detail={"state": state, "key": key,
                             "external_ref": ack_effect.get("external_ref"),
                             "session_state": session_state},
                     actor=_lease_actor_of(engine, lease))
    # WP09: an exposure cannot outlive the lease that opened it. Closed here, after the finish
    # committed, with the reason that is actually true (a canceled finish and a completed one are
    # different sentences about the same tunnel).
    from hoja_server import connect_api

    connect_api.close_for_lease(
        engine, lease, reason=(connect_api.CLOSE_LEASE_CANCELED if state == "canceled"
                              else connect_api.CLOSE_LEASE_FINISHED))
    return _finish_ack({**lease, "finish_state": state, "finish_key": key},
                       ack_effect, duplicate=False, key=key, session_state=session_state,
                       session_id=session_row["id"] if session_row else None)


def _lease_actor_of(engine, lease: Mapping[str, Any]) -> tuple[str, str]:
    with engine.connect() as conn:
        return _lease_actor(conn, lease)


def _finish_ack(lease: Mapping[str, Any], effect: Mapping[str, Any], *, duplicate: bool,
                key: str, session_state: str | None = None,
                session_id: str | None = None) -> dict[str, Any]:
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
        # Additive: the session this claim opened, so the caller can name the run it
        # just finished. A runner that ignores it is unaffected.
        "session_id": session_id or stored.get("session_id"),
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
    # WP09: a released lease is over, so its exposures are too.
    from hoja_server import connect_api

    connect_api.close_for_lease(
        engine, lease, reason=(connect_api.CLOSE_LEASE_CANCELED if terminal == LEASE_CANCELED
                              else connect_api.CLOSE_LEASE_FINISHED))
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
                verifying = _claim_under_verification(conn, org_id, lease)
            if verifying is not None:
                # WP10 (I5b): a claim being verified is not a lost run. Expiring the
                # lease here would re-queue the task underneath the verifier and
                # dispatch a second attempt at work nobody has judged yet — the
                # recorded failure (S15, class D2) in its other direction.
                LOG.info("lease %s expired while session %s awaits verification: "
                         "left alone, not requeued", lease["id"], verifying["id"])
                continue
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
        _follow_lost_lease(engine, org_id, lease, requeued, canceled=canceled)
        from hoja_server import agent_run_logs

        agent_run_logs.note(
            engine, org_id, lease,
            ("cancelled: the runner stopped answering after a cancel" if canceled else
             f"lost: no heartbeat before the lease expired; "
             f"{'retrying as fence ' + str(requeued['fence']) if requeued else 'no attempt left'}"),
            level="warning", kind="lease.expired")
        _audit_lease(engine, "agent.lease.expiry", lease, runner_row or {},
                     detail={"fence": lease["fence"], "attempt": lease["attempt"],
                             "reason": "cancel_requested" if canceled else "lease_expired",
                             "requeued_fence": requeued["fence"] if requeued else None},
                     actor=actor)
        # WP09: a reaped lease is the strongest "this tunnel has no owner" signal there is, so its
        # exposures are closed here rather than left for a screen to filter out.
        from hoja_server import connect_api

        connect_api.close_for_lease(
            engine, lease, reason=(connect_api.CLOSE_LEASE_CANCELED if canceled
                                  else connect_api.CLOSE_LEASE_EXPIRED))
        lost.append({"lease_id": lease["id"], "task_id": lease["task_id"],
                     "fence": lease["fence"], "state": terminal,
                     "requeued_fence": requeued["fence"] if requeued else None})
    return lost


def _follow_lost_lease(engine, org_id: str, lease: Mapping[str, Any],
                       requeued: Mapping[str, Any] | None, *, canceled: bool) -> None:
    """Move the session a lost lease was executing along with the attempt.

    The session used to keep pointing at the dead lease, still ``leased``: the next
    attempt ran under a session that claimed an older one, and an attempt the reaper
    gave up on left the session live forever. Now a re-queued attempt sends the session
    back to ``queued`` on the new lease (the machine's ``tick`` edge), and an attempt with
    no retry left fails it with the reason.
    """
    from hoja_server import agent_sessions, agent_verify

    if canceled:
        return  # a human stop already ended the session; the reaper only closes the lease
    stamp = now_iso()
    try:
        with tenant_context.scoped(org_id), engine.begin() as conn:
            session = agent_verify.session_for_lease(conn, org_id, dict(lease))
            if session is None or session["state"] not in (
                    agent_sessions.LEASED, agent_sessions.RUNNING, agent_sessions.AWAITING_INPUT):
                return
            if requeued is not None:
                session = agent_sessions.transition(conn, session, "tick", at=stamp)
                agent_sessions.append_activity(
                    conn, org_id, session["id"], "thought",
                    body=f"The runner stopped answering; retrying (attempt {requeued['attempt']}).",
                    at=stamp)
                conn.execute(agent_session.update().where(
                    agent_session.c.id == session["id"]).values(
                    lease_id=requeued["id"], admitted_at=stamp, updated_at=stamp))
            else:
                agent_sessions.transition(
                    conn, session, "fail", at=stamp,
                    reason=f"the runner stopped answering after {lease['attempt']} attempt(s)")
    except Exception:
        LOG.warning("could not move the session of lost lease %s", lease.get("id"), exc_info=True)


async def run_reaper_forever(engine, *, interval_s: int = REAP_INTERVAL_S) -> None:
    """The scheduled reaper, run in system context like the webhook worker.

    HOJA-712 adds the second half of a tick: a claim under verification with nobody
    judging it. The same loop is the right home for it — this is the one task that
    already runs on Hoja's own clock in every deployment, whatever else is configured,
    so a verdict does not depend on a runner retrying anything or on the optional DBOS
    process existing. Verification goes first: a claim being judged is not a lost run,
    and judging it before the expiry sweep keeps the two from describing the same
    attempt differently.

    The sweep is a catch-up, not the primary trigger (the finish route schedules the
    same idempotent call directly), so it runs once every ``VERIFY_SWEEP_TICKS`` ticks
    rather than on every one: it looks for rows across every tenant and nothing
    indexes "verifying and unjudged", which is the one thing a 60-second cadence can
    pay for and a 10-second one cannot.
    """
    from hoja_server import agent_autoverify

    tick = 0
    while True:
        if tick % VERIFY_SWEEP_TICKS == 0:
            try:
                judged = await run_in_threadpool(agent_autoverify.verify_pending, engine)
                if judged:
                    LOG.info("judged %d unverified claim(s)", len(judged))
            except Exception:
                LOG.warning("automatic verification sweep failed", exc_info=True)
        tick += 1
        try:
            # The schedule tick: queued sessions are re-checked and admitted ones queued
            # for a runner. Before this, nothing re-asked after the delegation itself.
            from hoja_server import agent_schedule

            swept = await run_in_threadpool(agent_schedule.sweep, engine)
            if swept.get("enqueued") or swept.get("cancelled"):
                LOG.info("schedule sweep: %s", swept)
        except Exception:
            LOG.warning("schedule sweep failed", exc_info=True)
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
        if isinstance(body.get("labels"), Mapping):
            try:
                await run_in_threadpool(_update_machine_labels, engine, runner_row, body["labels"])
            except Exception:  # labels are a description; they never cost the poll
                LOG.debug("could not record runner labels", exc_info=True)
        try:
            poll_ms = int(body.get("poll_timeout_ms") or 0)
        except (TypeError, ValueError):
            return _error(400, ERROR_BAD_PARAMS, "poll_timeout_ms must be an integer")
        poll_ms = max(0, min(poll_ms, MAX_POLL_MS))
        deadline = time.monotonic() + poll_ms / 1000.0
        # HOJA-602-A-20: a poll is liveness. `_touch_runner` is the rate limiter (one
        # write per HEARTBEAT_INTERVAL_S per runner, enforced in SQL), and this is the
        # cheaper half of it: with nothing to grant, the loop below runs four times a
        # second, so it must not even try to write that often.
        next_liveness = time.monotonic()
        while True:
            if time.monotonic() >= next_liveness:
                next_liveness = time.monotonic() + HEARTBEAT_INTERVAL_S
                try:
                    await run_in_threadpool(_touch_runner, engine, runner_row)
                except Exception:  # liveness must never refuse work
                    # The machine asked for work and is entitled to it; a failed stamp
                    # costs it a wrong "offline" dot, not its lease. The next poll
                    # (or the heartbeat of the lease it is granted) repairs the row.
                    LOG.warning("could not record liveness for runner %s",
                                runner_row.get("id"), exc_info=True)
            # One indexed read first: an idle workspace must not cost a locking claim
            # transaction (and a policy check) four times a second per runner.
            granted = None
            if await run_in_threadpool(_has_queued, engine, runner_row):
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
        try:
            wait_ms = max(0, min(int(body.get("wait_ms") or 0), MAX_HEARTBEAT_WAIT_MS))
        except (TypeError, ValueError):
            wait_ms = 0
        waiting = [str(x) for x in (body.get("waiting_asks") or []) if x][:20]
        lease_id = str(body.get("lease_id") or request.headers.get(HEADER_LEASE) or "")
        if wait_ms and lease_id:
            # Hold the beat open until something the runner must hear happens, or the
            # wait runs out; then do the full beat (extend, drift, answers) once.
            deadline = time.monotonic() + wait_ms / 1000.0
            org_id = runner_row["organization_id"]
            while time.monotonic() < deadline:
                try:
                    if await run_in_threadpool(_heartbeat_signal, engine, org_id,
                                               lease_id, waiting):
                        break
                except Exception:  # the full beat below reports the real error
                    break
                await asyncio.sleep(min(HEARTBEAT_WATCH_S,
                                        max(0.0, deadline - time.monotonic())))
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
        ack = await run_in_threadpool(
            _run, lambda: finish_lease(engine, runner_row, body, request))
        if not isinstance(ack, Mapping):
            # ``_run`` answered with a refusal response; it is already the answer.
            return ack
        response = JSONResponse(ack)
        # HOJA-712: the verifier runs *after* this response is written, so a runner's
        # finish never waits on a verdict. The reaper's sweep is the retry if this
        # process dies first; both entries call the same idempotent function.
        from hoja_server import agent_autoverify, agent_sessions

        if (ack.get("session_id")
                and ack.get("session_state") == agent_sessions.VERIFYING
                and agent_autoverify.enabled()):
            response.background = BackgroundTask(
                agent_autoverify.verify_after_finish, engine,
                runner_row["organization_id"], str(ack["session_id"]))
        return response

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

    async def logs(request: Request) -> JSONResponse:
        try:
            runner_row = _runner_from(request, engine)
        except LeaseRequestError as rejected:
            return rejected.response()
        body = await _json_body(request)
        return await run_in_threadpool(_run, lambda: JSONResponse(
            post_logs(engine, runner_row, body)))

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
        Route(ROUTE_LOGS, logs, methods=["POST"]),
    ]


#: What a daemon may say about its machine on its first poll (HOJA-602 machine workspace).
MACHINE_LABELS = ("work_root", "os", "hostname", "runner_version")


def _update_machine_labels(engine, runner_row: Mapping[str, Any], labels: Mapping[str, Any]) -> None:
    """Merge the machine's self-description into ``runner.labels`` when it changed.

    Registration labels (an operator's pool) are kept; only the known machine keys are taken
    from the daemon, so the Machines list stays true after an upgrade or a moved work root.
    """
    org_id = runner_row["organization_id"]
    with tenant_context.scoped(org_id), engine.begin() as conn:
        current = _json_dict(conn.execute(select(runner.c.labels).where(and_(
            runner.c.organization_id == org_id, runner.c.id == runner_row["id"]))).scalar())
        merged = {**current, **{k: str(v)[:300] for k, v in labels.items()
                                if k in MACHINE_LABELS and v is not None}}
        if merged != current:
            conn.execute(runner.update().where(and_(
                runner.c.organization_id == org_id, runner.c.id == runner_row["id"],
            )).values(labels=json.dumps(merged, sort_keys=True)))


def _has_queued(engine, runner_row: Mapping[str, Any]) -> bool:
    """Is there any queued attempt this runner could be offered? Read-only and cheap.

    A refused attempt still counts, so its policy is re-checked on the next claim (a
    budget can reset, a policy can be edited); the refusal itself is audited only when
    its reason changes (``claim_lease``).
    """
    harnesses = _json_list(runner_row.get("harnesses"))
    if not harnesses:
        return False
    org_id = runner_row["organization_id"]
    with tenant_context.scoped(org_id), engine.connect() as conn:
        return conn.execute(select(agent_lease.c.id).where(and_(
            agent_lease.c.organization_id == org_id,
            agent_lease.c.state == LEASE_QUEUED,
            agent_lease.c.harness.in_(list(harnesses)),
        )).limit(1)).first() is not None


def _refused_for(engine, runner_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    harnesses = _json_list(runner_row.get("harnesses"))
    with tenant_context.scoped(runner_row["organization_id"]), engine.connect() as conn:
        return _refused(conn, runner_row["organization_id"], harnesses) + \
            _pool_refused(conn, runner_row["organization_id"], harnesses,
                          runner_pools(runner_row)) + \
            _policy_refused(conn, runner_row["organization_id"], harnesses)
