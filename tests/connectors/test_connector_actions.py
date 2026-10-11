"""Connector write queue: a write runs only after the requesting user approves it, exactly once."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from cliniar_server.connector_actions import (
    APPROVED,
    EXECUTING,
    FAILED,
    PENDING,
    REJECTED,
    approve_action,
    queue_action,
    reject_action,
)
from cliniar_server.db import connector_account, connector_action, make_engine, migrate

ORG = "org-1"
OWNER = "user-owner"
OTHER = "user-other"


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "actions.db")
    migrate(eng)
    with eng.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct-1",
                organization_id=ORG,
                owner_user_id=OWNER,
                provider="google",
                external_account="ana@example.test",
                scopes="",
                encrypted_refresh_token="x",
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
    return eng


def _queue(engine, user=OWNER, tool="gmail_send", args=None) -> str:
    with engine.begin() as conn:
        return queue_action(
            conn,
            org_id=ORG,
            account_id="acct-1",
            user_id=user,
            tool=tool,
            args=args or {"to": "bob@example.test"},
        )


def _status(engine, action_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            select(connector_action.c.status).where(connector_action.c.id == action_id)
        ).scalar_one()


async def test_queued_write_does_not_run_until_approved(engine) -> None:
    calls: list[str] = []

    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        calls.append(tool)
        return "sent"

    action_id = _queue(engine)
    assert _status(engine, action_id) == PENDING
    assert calls == []


async def test_approval_by_requester_runs_once(engine) -> None:
    calls: list[str] = []

    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        calls.append(tool)
        return "sent"

    action_id = _queue(engine)
    assert await approve_action(engine, ORG, OWNER, action_id, executor) == APPROVED
    assert await approve_action(engine, ORG, OWNER, action_id, executor) is None
    assert calls == ["gmail_send"]
    assert _status(engine, action_id) == APPROVED


async def test_other_user_cannot_approve(engine) -> None:
    calls: list[str] = []

    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        calls.append(tool)
        return "sent"

    action_id = _queue(engine)
    assert await approve_action(engine, ORG, OTHER, action_id, executor) is None
    assert calls == []
    assert _status(engine, action_id) == PENDING


async def test_action_from_other_org_cannot_be_approved(engine) -> None:
    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        raise AssertionError("must not run")

    action_id = _queue(engine)
    assert await approve_action(engine, "org-2", OWNER, action_id, executor) is None
    assert _status(engine, action_id) == PENDING


async def test_rejected_action_never_runs(engine) -> None:
    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        raise AssertionError("must not run")

    action_id = _queue(engine)
    assert reject_action(engine, ORG, OWNER, action_id) is True
    assert await approve_action(engine, ORG, OWNER, action_id, executor) is None
    assert _status(engine, action_id) == REJECTED


async def test_failed_write_is_recorded_and_not_retried(engine) -> None:
    calls: list[str] = []

    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        calls.append(tool)
        raise RuntimeError("upstream 500 with secret token in body")

    action_id = _queue(engine)
    assert await approve_action(engine, ORG, OWNER, action_id, executor) == FAILED
    assert await approve_action(engine, ORG, OWNER, action_id, executor) is None
    assert calls == ["gmail_send"]
    with engine.connect() as conn:
        summary = conn.execute(
            select(connector_action.c.result_summary).where(connector_action.c.id == action_id)
        ).scalar_one()
    assert summary == "RuntimeError"


async def test_arguments_are_stored_and_passed_unchanged(engine) -> None:
    seen: list[dict] = []

    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        seen.append(args)
        return "ok"

    action_id = _queue(engine, args={"to": "bob@example.test", "subject": "Hi"})
    await approve_action(engine, ORG, OWNER, action_id, executor)
    assert seen == [{"to": "bob@example.test", "subject": "Hi"}]


async def test_executing_row_blocks_second_claim(engine) -> None:
    """A row already claimed (executing) must not be claimable again."""
    action_id = _queue(engine)
    with engine.begin() as conn:
        from sqlalchemy import update

        conn.execute(
            update(connector_action)
            .where(connector_action.c.id == action_id)
            .values(status=EXECUTING)
        )

    async def executor(tool: str, args: dict, account_id: str, requester: str) -> str:
        raise AssertionError("must not run")

    assert await approve_action(engine, ORG, OWNER, action_id, executor) is None
