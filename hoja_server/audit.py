"""Append-only audit trail for agent and admin actions (HOJA-602-A-8, WP05).

The threat model (``agent-security/01-threat-model.md``) lists repudiation as an
open control while nothing records who did what: ``change_event`` answers "what
changed", not "who asked for the action that changed nothing". This module is
the second, narrower ledger — one row per action that a human later has to be
able to prove or deny, chained so a deleted or rewritten row is detectable.

Design decisions, and why:

* **Append-only at the database, not in Python.** A trigger refuses every
  UPDATE and DELETE on ``audit_event``. A grant-only design (REVOKE UPDATE)
  would be undone by any deployment that re-grants on all tables — the test
  harness in ``tests/rls_mode.py`` does exactly that — so the refusal has to
  live where it cannot be re-granted away.
* **One event per action that succeeded.** ``record`` runs in its own
  transaction, after the mutation committed. An audit row inside the mutation's
  transaction would vanish with a rollback and leave no trace of the attempt;
  a row written first would claim an action that then failed. The cost is that
  a crash between commit and audit leaves a gap — the hash chain still proves
  the gap exists, which is the property we actually need.
* **Hash chain per organization**, not per table. Each row hashes its own
  fields plus the previous row's hash in the same organization, so a missing or
  edited row breaks every row after it. On PostgreSQL the chain read and the
  insert are serialized with a per-organization advisory lock, so two
  concurrent writers cannot both chain onto the same predecessor. SQLite is the
  single-tenant local mode: its writes already serialize on the database write
  lock, and it records no actor (there is no session-setting seam).
* **The actor is named at the call site, never read from the ambient context.**
  Revision 64 puts the actor in a transaction-scoped session setting for
  ``change_event``, and reading the same ``ContextVar`` here looked free — but
  it is not reset when a request ends, so a value left by an agent turn would
  attribute the next human's action to that agent. Under ``HOJA_TEST_RLS`` that
  leak is reproducible (the missing-actor test caught it), which is the whole
  argument for requiring the caller to pass ``actor_kind``/``actor_id``: an
  audit row that cannot name its actor is refused instead of guessed.

The enumerated ``ACTIONS`` are the nine kinds WP05 names. Kinds whose surface
does not exist on this stack yet (lease, session, tool calls, connector token
use, Connect exposure) are still listed here so a later work package extends
the vocabulary in one place instead of inventing spellings at each call site;
a call site for a kind that does not exist is not written, and the pull request
names what is blocked on which work package.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from hoja_server.db import audit_event

GENESIS = "0" * 64

# The nine kinds from the work package, spelled once. ``record`` refuses a
# spelling that is not here, so a typo cannot become a new action.
ACTIONS = frozenset({
    # Agent surface. Session and lease come from WP06 / WP07; the turn pair is
    # what exists today (a completion stream is the closest thing to a session).
    "agent.session.start",
    "agent.session.stop",
    "agent.lease.grant",
    "agent.lease.expiry",
    # A lease the runner may not take, because the agent's policy refused it
    # (WP12). Distinct from ``expiry``: nothing was granted and nothing lapsed.
    "agent.lease.refuse",
    "agent.tool.call",
    # What an agent may touch (WP12). ``agent.policy.change`` carries the before
    # and after of the policy document; ``agent.policy.secret`` names the secret
    # and never its value, which is not readable here by construction.
    "agent.policy.change",
    "agent.policy.secret",
    # Credential and connector surfaces.
    "connector.token.use",
    "api_key.create",
    "api_key.revoke",
    # Membership and authority.
    "member.invite",
    "member.role.change",
    "member.deactivate",
    "member.reactivate",
    "member.archive",
    "member.restore",
    # Hoja Connect (WP09).
    "connect.exposure.open",
    "connect.exposure.close",
})

# Bounded read: an admin screen pages, it never asks for the whole ledger.
MAX_PAGE = 200


class AuditActorMissingError(RuntimeError):
    """A system path tried to audit, and no actor is set (fail closed)."""


class UnknownActionError(ValueError):
    """The action is not part of the enumerated vocabulary."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def row_hash(fields: dict[str, Any], prev_hash: str) -> str:
    """Hash one row's content plus its predecessor's hash."""
    return hashlib.sha256(_canonical({**fields, "prev_hash": prev_hash})).hexdigest()


def _lock_chain(conn, organization_id: str) -> None:
    """Serialize this organization's chain read + insert (PostgreSQL only).

    The lock is transaction-scoped, so it never outlives the event it guards.
    """
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext(%s))",
                             (f"audit_chain:{organization_id}",))


def record(engine, *, organization_id: str, action: str, actor_kind: str | None = None,
           actor_id: str | None = None, on_behalf_of: str | None = None,
           target_kind: str | None = None, target_id: str | None = None,
           detail: dict | str | None = None, ip: str | None = None,
           runner: str | None = None, request_id: str | None = None) -> int:
    """Append exactly one event; return its ``seq``.

    The actor is required: a call site that cannot say who acted does not get a
    row with a guessed actor. Never pass a secret here — ``detail`` is stored
    verbatim and is readable by admins.
    """
    if action not in ACTIONS:
        raise UnknownActionError(f"{action!r} is not an audited action")
    kind, who, behalf = actor_kind, actor_id, on_behalf_of
    if not who or not kind:
        raise AuditActorMissingError(f"refusing to audit {action} with no actor")
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)
    if detail:
        # Defence in depth for WP12: a secret value an agent lease was given must
        # never become readable here, even if a call site quotes a payload it was
        # handed. Only watched secret VALUES are scrubbed — an audit detail that
        # names a person's email is expected to keep it.
        from hoja_server import logging_setup

        detail = logging_setup.scrub_secrets(detail)
    created_at = datetime.now(timezone.utc).isoformat()
    fields = {
        "organization_id": organization_id,
        "action": action,
        "actor_kind": kind,
        "actor_id": who,
        "on_behalf_of": behalf,
        "target_kind": target_kind,
        "target_id": target_id,
        "ip": ip,
        "runner": runner,
        "request_id": request_id,
        "detail": detail,
        "created_at": created_at,
    }
    with engine.begin() as conn:
        _lock_chain(conn, organization_id)
        previous = conn.execute(
            select(audit_event.c.seq, audit_event.c.row_hash)
            .where(audit_event.c.organization_id == organization_id)
            .order_by(audit_event.c.seq.desc()).limit(1)
        ).first()
        prev_hash = previous.row_hash if previous else GENESIS
        result = conn.execute(audit_event.insert().values(
            **fields, prev_hash=prev_hash, row_hash=row_hash(fields, prev_hash),
        ))
        # inserted_primary_key is the seq the database assigned; SQLite exposes it
        # as lastrowid, PostgreSQL as RETURNING under the same SQLAlchemy API.
        seq = result.inserted_primary_key[0] if result.inserted_primary_key else result.lastrowid
    return int(seq)


def _request_meta(info) -> tuple[str | None, str | None]:
    """Best-effort (ip, request id) for a GraphQL request, never fatal."""
    context = getattr(info, "context", None) or {}
    request = context.get("request") if isinstance(context, dict) else None
    if request is None:
        return None, None
    headers = getattr(request, "headers", {}) or {}
    ip = headers.get("x-forwarded-for", "").split(",")[0].strip() or None
    if not ip:
        client = getattr(request, "client", None)
        ip = getattr(client, "host", None) if client else None
    return ip, headers.get("x-request-id")


def actor_for(info, fallback_user_id: str) -> tuple[str, str, str | None]:
    """Who this request writes as, from the request itself.

    ``request.state.actor_kind`` is what the credential named (``human``, ``bot``
    or ``agent``); the authenticated user id is the actor either way. An agent
    writing through its own key has no human in the request, so ``on_behalf_of``
    stays empty rather than borrowing one.
    """
    context = getattr(info, "context", None) or {}
    request = context.get("request") if isinstance(context, dict) else None
    state = getattr(request, "state", None)
    kind = getattr(state, "actor_kind", None) or "human"
    return kind, fallback_user_id, None


def record_request(store, info, *, user_id: str, action: str, **kwargs) -> int:
    """Record an action a GraphQL resolver just performed on behalf of ``info``."""
    context = getattr(info, "context", None) or {}
    organization_id = kwargs.pop("organization_id", None) or (
        context.get("org_id") if isinstance(context, dict) else None)
    if not organization_id:
        raise AuditActorMissingError(f"refusing to audit {action} outside a workspace")
    kind, actor_id, behalf = actor_for(info, user_id)
    ip, request_id = _request_meta(info)
    return record(store.engine, organization_id=organization_id,
                  actor_kind=kind, actor_id=actor_id,
                  on_behalf_of=behalf, action=action, ip=ip,
                  request_id=request_id, **kwargs)


def _api_row(row: dict) -> dict:
    """Storage row → the GraphQL shape.

    The schema's fields are camelCase and graphql-core's default resolver looks
    the field name up verbatim, so the mapping happens here the same way the
    store's ``ser_*`` helpers do it for every other type.
    """
    return {
        "seq": row["seq"],
        "action": row["action"],
        "actorKind": row["actor_kind"],
        "actorId": row["actor_id"],
        "onBehalfOf": row["on_behalf_of"],
        "targetKind": row["target_kind"],
        "targetId": row["target_id"],
        "ip": row["ip"],
        "runner": row["runner"],
        "requestId": row["request_id"],
        "detail": row["detail"],
        "prevHash": row["prev_hash"],
        "rowHash": row["row_hash"],
        "createdAt": row["created_at"],
    }


def read_events(engine, organization_id: str, *, actor_id: str | None = None,
                action: str | None = None, first: int = 50,
                after: int | None = None) -> dict:
    """One bounded page of an organization's events, oldest first.

    The explicit ``organization_id`` filter is belt and braces: the request is
    already bound to its tenant, so under row-level security the same predicate
    is applied twice rather than trusted once.
    """
    first = max(1, min(int(first or 50), MAX_PAGE))
    query = select(audit_event).where(audit_event.c.organization_id == organization_id)
    if actor_id:
        query = query.where(audit_event.c.actor_id == actor_id)
    if action:
        query = query.where(audit_event.c.action == action)
    if after is not None:
        query = query.where(audit_event.c.seq > int(after))
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in
                conn.execute(query.order_by(audit_event.c.seq.asc()).limit(first + 1))]
    has_next = len(rows) > first
    rows = rows[:first]
    return {
        "nodes": [_api_row(row) for row in rows],
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": after is not None,
            "startCursor": str(rows[0]["seq"]) if rows else None,
            "endCursor": str(rows[-1]["seq"]) if rows else None,
        },
    }


def verify_chain(engine, organization_id: str, limit: int | None = None) -> dict | None:
    """Recompute the chain; return the first broken row, or None when intact."""
    query = select(audit_event).where(
        audit_event.c.organization_id == organization_id,
    ).order_by(audit_event.c.seq.asc())
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(query)]
    if limit is not None:
        rows = rows[-int(limit):]
    # A limited verification starts mid-chain: its first stored prev_hash is the
    # anchor, and every row after it must chain onto the one before.
    prev_hash = rows[0]["prev_hash"] if rows else GENESIS
    for row in rows:
        fields = {key: row[key] for key in (
            "organization_id", "action", "actor_kind", "actor_id", "on_behalf_of",
            "target_kind", "target_id", "ip", "runner", "request_id", "detail",
            "created_at")}
        if row["prev_hash"] != prev_hash:
            return {"seq": row["seq"], "reason": "prev_hash does not match the chain"}
        if row["row_hash"] != row_hash(fields, row["prev_hash"]):
            return {"seq": row["seq"], "reason": "row_hash does not recompute"}
        prev_hash = row["row_hash"]
    return None
