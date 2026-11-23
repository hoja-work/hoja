"""`hoja decision ...` commands — ask, list, answer, wait (docs/decisions.md).

An agent's path to a human decision without a browser: `ask` opens the question, `wait`
polls it until the human answers, so a worker can block on a real answer instead of
writing prose into a comment and hoping.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer

from hoja.cli_state import build_client, get_state
from hoja.errors import NotFoundError, UsageError
from hoja.graphql import mutations, queries
from hoja.models.decision import IssueDecision
from hoja.output import render

decision_app = typer.Typer(help="Decisions (ask a human a question while an issue waits)")


def _parse_option(raw: str) -> dict:
    """Parse ``--option KEY=LABEL`` or ``--option LABEL`` into an option object."""
    if "=" in raw:
        key, label = raw.split("=", 1)
        key, label = key.strip(), label.strip()
        if not key or not label:
            raise UsageError(f"--option {raw!r}: use KEY=LABEL or LABEL")
        return {"key": key, "label": label}
    label = raw.strip()
    if not label:
        raise UsageError("--option needs a label")
    return {"label": label}


@decision_app.command("ask")
def ask(
    issue: str = typer.Argument(..., help="Issue identifier (HOJA-175) or UUID"),
    question: str = typer.Option(..., "--question", "-q", help="The question to ask"),
    option: Optional[list[str]] = typer.Option(
        None, "--option", "-o", help="An option, as KEY=LABEL or LABEL. Repeat for each."),
    context: Optional[str] = typer.Option(None, "--context", "-c", help="Markdown context/links"),
    agent: Optional[str] = typer.Option(None, "--agent", help="Agent/session identity, e.g. claw:run-30d5"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Block until a human answers"),
    timeout: float = typer.Option(900.0, "--timeout", help="Seconds to wait with --wait"),
    interval: float = typer.Option(5.0, "--interval", help="Poll interval in seconds"),
) -> None:
    """Ask a human a decision on an issue; the issue is flagged while it is open."""
    payload = {
        "issueId": issue,
        "question": question,
        "options": [_parse_option(o) for o in (option or [])],
        "context": context,
        "agent": agent,
    }
    asyncio.run(_run_ask(payload, wait=wait, timeout=timeout, interval=interval))


@decision_app.command("list")
def list_decisions(
    issue: str = typer.Argument(..., help="Issue identifier or UUID"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="open | answered | canceled"),
) -> None:
    """List the decisions on an issue, newest first."""
    asyncio.run(_run_list(issue, status))


@decision_app.command("get")
def get_decision(
    decision_id: str = typer.Argument(..., help="Decision id from `ask` or `list`"),
) -> None:
    """Read one decision — the poll an agent performs."""
    asyncio.run(_run_get(decision_id))


@decision_app.command("answer")
def answer(
    decision_id: str = typer.Argument(..., help="Decision id"),
    option: Optional[str] = typer.Option(None, "--option", "-o", help="Chosen option key or label"),
    text: Optional[str] = typer.Option(None, "--answer", "-a", help="Answer text (free-form or a note)"),
) -> None:
    """Answer a decision. Answering the same answer twice is a no-op."""
    asyncio.run(_run_answer(decision_id, option, text))


@decision_app.command("wait")
def wait(
    decision_id: str = typer.Argument(..., help="Decision id"),
    timeout: float = typer.Option(900.0, "--timeout", help="Seconds to wait"),
    interval: float = typer.Option(5.0, "--interval", help="Poll interval in seconds"),
) -> None:
    """Block until the decision is answered, then print the answer (exit 2 on timeout)."""
    asyncio.run(_run_wait(decision_id, timeout, interval))


@decision_app.command("cancel")
def cancel(
    decision_id: str = typer.Argument(..., help="Decision id"),
) -> None:
    """Withdraw an open decision (the asker or a workspace admin)."""
    asyncio.run(_run_cancel(decision_id))


# ---------- runners ----------

def _model(node: dict | None) -> IssueDecision:
    if not node:
        raise NotFoundError("Decision not found")
    return IssueDecision.model_validate(node)


async def _run_ask(payload: dict, *, wait: bool, timeout: float, interval: float) -> None:
    state = get_state()
    async with build_client(state) as client:
        body = {k: v for k, v in payload.items() if v not in (None, [])}
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=json.dumps(body))
            return
        data = await client.execute(mutations.DECISION_CREATE, {"input": body},
                                   operation="IssueDecisionCreate")
        created = _model((data.get("issueDecisionCreate") or {}).get("decision"))
        if not wait:
            render(created, fmt=state.output, title="Decision asked")
            return
        print(f"Asked {created.id}; waiting for an answer (timeout {timeout:g}s)…")
        answered = await _poll(client, created.id, timeout, interval)
        render(answered, fmt=state.output, title="Decision answered")


async def _run_list(issue: str, status: str | None) -> None:
    state = get_state()
    async with build_client(state) as client:
        data = await client.execute(queries.ISSUE_DECISIONS,
                                    {"issueId": issue, "status": status},
                                    operation="IssueDecisions")
        nodes = data.get("issueDecisions") or []
        render([_model(n) for n in nodes], fmt=state.output,
               title=f"Decisions on {issue}")


async def _run_get(decision_id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        data = await client.execute(queries.ISSUE_DECISION, {"id": decision_id},
                                    operation="IssueDecision")
        render(_model(data.get("issueDecision")), fmt=state.output)


async def _run_answer(decision_id: str, option: str | None, text: str | None) -> None:
    state = get_state()
    async with build_client(state) as client:
        body = {k: v for k, v in (("option", option), ("answer", text)) if v}
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=json.dumps({"id": decision_id, "input": body}))
            return
        data = await client.execute(mutations.DECISION_ANSWER,
                                    {"id": decision_id, "input": body},
                                    operation="IssueDecisionAnswer")
        payload = data.get("issueDecisionAnswer") or {}
        if not payload.get("success"):
            raise UsageError("issueDecisionAnswer returned success=false")
        decision = _model(payload.get("decision"))
        title = "Answer already recorded" if payload.get("replayed") else "Decision answered"
        render(decision, fmt=state.output, title=title)


async def _run_wait(decision_id: str, timeout: float, interval: float) -> None:
    state = get_state()
    async with build_client(state) as client:
        decision = await _poll(client, decision_id, timeout, interval)
        render(decision, fmt=state.output, title="Decision answered")


async def _run_cancel(decision_id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        data = await client.execute(mutations.DECISION_CANCEL, {"id": decision_id},
                                    operation="IssueDecisionCancel")
        payload = data.get("issueDecisionCancel") or {}
        if not payload.get("success"):
            raise UsageError("issueDecisionCancel returned success=false")
        render(_model(payload.get("decision")), fmt=state.output, title="Decision canceled")


async def _poll(client, decision_id: str, timeout: float, interval: float) -> IssueDecision:
    """Poll one decision until it is no longer open; ``--timeout`` bounds the wait."""
    waited = 0.0
    step = max(interval, 0.1)
    while True:
        data = await client.execute(queries.ISSUE_DECISION, {"id": decision_id},
                                    operation="IssueDecision")
        decision = _model(data.get("issueDecision"))
        if decision.status in ("answered", "canceled"):
            return decision
        if waited >= timeout:
            raise UsageError(
                f"No answer within {timeout:g}s; the decision is still {decision.status}.",
                hint=f"Poll again with `hoja decision wait {decision_id}` or read it with "
                     f"`hoja decision get {decision_id}`.")
        await asyncio.sleep(step)
        waited += step
