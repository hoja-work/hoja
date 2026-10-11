"""Approval queue for connector write actions. A write is never executed on request; it waits here
until the requesting user explicitly approves it. Approval claims the row atomically so a double
click or retry cannot run the same write twice."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.engine import Connection, Engine, RowMapping

from cliniar_server.db import connector_action

PENDING = "pending"
EXECUTING = "executing"
APPROVED = "approved"
REJECTED = "rejected"
FAILED = "failed"

Executor = Callable[[str, dict[str, Any], str, str], Awaitable[str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue_action(
    conn: Connection,
    *,
    org_id: str,
    account_id: str,
    user_id: str,
    tool: str,
    args: dict[str, Any],
) -> str:
    action_id = f"cact_{uuid.uuid4().hex}"
    conn.execute(
        connector_action.insert().values(
            id=action_id,
            organization_id=org_id,
            account_id=account_id,
            requested_by_user_id=user_id,
            tool=tool,
            args_json=json.dumps(args, sort_keys=True),
            status=PENDING,
            created_at=_now(),
        )
    )
    return action_id


def _owned_pending(
    conn: Connection, org_id: str, user_id: str, action_id: str
) -> RowMapping | None:
    row = (
        conn.execute(
            connector_action.select().where(
                connector_action.c.id == action_id,
                connector_action.c.organization_id == org_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["requested_by_user_id"] != user_id:
        return None
    return row


def reject_action(engine: Engine, org_id: str, user_id: str, action_id: str) -> bool:
    with engine.begin() as conn:
        row = _owned_pending(conn, org_id, user_id, action_id)
        if row is None or row["status"] != PENDING:
            return False
        conn.execute(
            update(connector_action)
            .where(connector_action.c.id == action_id, connector_action.c.status == PENDING)
            .values(status=REJECTED, resolved_at=_now())
        )
    return True


async def approve_action(
    engine: Engine, org_id: str, user_id: str, action_id: str, executor: Executor
) -> str | None:
    """Run a pending action once. Returns the final status, or None when not approvable."""
    with engine.begin() as conn:
        row = _owned_pending(conn, org_id, user_id, action_id)
        if row is None or row["status"] != PENDING:
            return None
        claimed = conn.execute(
            update(connector_action)
            .where(connector_action.c.id == action_id, connector_action.c.status == PENDING)
            .values(status=EXECUTING)
        ).rowcount
        if claimed != 1:
            return None
        tool, args = row["tool"], json.loads(row["args_json"])
        account_id, requester = row["account_id"], row["requested_by_user_id"]
    try:
        summary = await executor(tool, args, account_id, requester)
    except Exception as exc:
        _finish(engine, action_id, FAILED, f"{type(exc).__name__}")
        return FAILED
    _finish(engine, action_id, APPROVED, summary)
    return APPROVED


def _finish(engine: Engine, action_id: str, status: str, summary: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(connector_action)
            .where(connector_action.c.id == action_id)
            .values(status=status, result_summary=summary, resolved_at=_now())
        )
