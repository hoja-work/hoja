"""`hoja comment ...` commands."""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

import typer

from hoja.cli_state import build_client, get_state
from hoja.errors import UsageError
from hoja.graphql import mutations, queries
from hoja.models.task import Comment, Issue
from hoja.output import render

comment_app = typer.Typer(help="Comments")


@comment_app.command("list")
def list_comments(
    issue_id: str = typer.Argument(..., help="Issue identifier (ENG-34) or UUID"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """List comments on an issue."""
    asyncio.run(_run_list(issue_id, limit))


@comment_app.command("add")
def add_comment(
    issue_id: str = typer.Argument(..., help="Issue identifier or UUID"),
    body: Optional[str] = typer.Argument(None, help="Comment body. If omitted, reads stdin."),
) -> None:
    """Add a comment to an issue. Reads stdin if body argument is omitted."""
    text = body if body is not None else sys.stdin.read()
    if not text.strip():
        raise UsageError("Empty comment body — provide an argument or pipe content")
    asyncio.run(_run_add(issue_id, text))


@comment_app.command("edit")
def edit_comment(
    comment_id: str = typer.Argument(..., help="Comment UUID"),
    body: Optional[str] = typer.Argument(None, help="New body. If omitted, reads stdin."),
) -> None:
    """Edit an existing comment."""
    text = body if body is not None else sys.stdin.read()
    if not text.strip():
        raise UsageError("Empty body")
    asyncio.run(_run_edit(comment_id, text))


@comment_app.command("delete")
def delete_comment(
    comment_id: str = typer.Argument(..., help="Comment UUID"),
) -> None:
    """Delete a comment."""
    asyncio.run(_run_delete(comment_id))


# ---------- runners ----------

async def _resolve_issue_uuid(client, ref: str) -> str:
    """Resolve identifier like ENG-34 to a UUID; pass through if already UUID."""
    if len(ref) == 36 and ref.count("-") == 4:
        return ref
    issue = await client.execute_as(
        Issue, queries.ISSUE_BY_IDENTIFIER, {"id": ref},
        path=["issue"], operation="IssueByIdentifier",
    )
    return issue.id


async def _run_list(issue_ref: str, limit: int) -> None:
    state = get_state()
    async with build_client(state) as client:
        data = await client.execute(
            queries.ISSUE_COMMENTS,
            {"issueId": issue_ref, "first": limit},
            operation="IssueComments",
        )
        issue_data = data.get("issue") or {}
        nodes = (issue_data.get("comments") or {}).get("nodes") or []
        comments = [Comment.model_validate(n) for n in nodes]
        render(
            comments,
            fmt=state.output,
            title=f"Comments on {issue_data.get('identifier', issue_ref)}",
        )


async def _run_add(issue_ref: str, body: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        issue_uuid = await _resolve_issue_uuid(client, issue_ref)
        input_ = {"issueId": issue_uuid, "body": body.strip()}
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=str({"input": input_}))
            return
        data = await client.execute(
            mutations.COMMENT_CREATE, {"input": input_}, operation="CommentCreate"
        )
        payload = data.get("commentCreate", {})
        if not payload.get("success"):
            raise UsageError("commentCreate returned success=false")
        c = Comment.model_validate(payload["comment"])
        render(c, fmt=state.output, title="Comment added")


async def _run_edit(comment_id: str, body: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        input_ = {"body": body.strip()}
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=str({"id": comment_id, "input": input_}))
            return
        data = await client.execute(
            mutations.COMMENT_UPDATE,
            {"id": comment_id, "input": input_},
            operation="CommentUpdate",
        )
        payload = data.get("commentUpdate", {})
        if not payload.get("success"):
            raise UsageError("commentUpdate returned success=false")
        c = Comment.model_validate(payload["comment"])
        render(c, fmt=state.output, title="Comment updated")


async def _run_delete(comment_id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=f"delete comment {comment_id}")
            return
        data = await client.execute(
            mutations.COMMENT_DELETE, {"id": comment_id}, operation="CommentDelete"
        )
        ok = data.get("commentDelete", {}).get("success")
        if not ok:
            raise UsageError("commentDelete returned success=false")
        from hoja.models.enums import OutputFormat
        if state.output == OutputFormat.JSON:
            import json as _json
            print(_json.dumps({"deleted": comment_id, "success": True}))
        else:
            print(f"Deleted comment {comment_id}")
