"""`hoja label ...` commands."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from hoja.cli_state import build_client, get_state
from hoja.errors import NotFoundError, UsageError
from hoja.graphql import mutations, queries
from hoja.models.issue import IssueLabel
from hoja.models.team import Team
from hoja.output import render

label_app = typer.Typer(help="Labels")


@label_app.command("list")
def list_labels(
    team: Optional[str] = typer.Option(None, "--team", "-t", help="Filter to team key/UUID"),
    limit: int = typer.Option(100, "--limit", "-n"),
) -> None:
    """List issue labels (optionally scoped to a team)."""
    asyncio.run(_run_list(team, limit))


@label_app.command("create")
def create_label(
    name: str = typer.Argument(..., help="Label name"),
    team: str = typer.Option(..., "--team", "-t", help="Team key or UUID"),
    color: Optional[str] = typer.Option(None, "--color", help="Hex color (e.g. #ff0000)"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
) -> None:
    """Create a new label on a team."""
    asyncio.run(_run_create(name, team, color, description))


@label_app.command("delete")
def delete_label(
    id: str = typer.Argument(..., help="Label UUID"),
) -> None:
    """Delete a label by UUID."""
    asyncio.run(_run_delete(id))


# ---------- runners ----------

async def _resolve_team_id(client, key: str) -> str:
    if len(key) == 36 and key.count("-") == 4:
        return key
    teams = await client.execute_list(
        Team, queries.TEAM_BY_KEY, {"key": key.upper()},
        path=["teams"], operation="TeamByKey",
    )
    if not teams:
        raise NotFoundError(f"No team with key {key!r}")
    return teams[0].id


async def _run_list(team: str | None, limit: int) -> None:
    state = get_state()
    async with build_client(state) as client:
        filt: dict | None = None
        if team:
            team_id = await _resolve_team_id(client, team)
            filt = {"team": {"id": {"eq": team_id}}}
        labels = await client.execute_list(
            IssueLabel,
            queries.ISSUE_LABELS,
            {"filter": filt, "first": limit},
            path=["issueLabels"],
            operation="IssueLabels",
        )
        render(labels, fmt=state.output, title="Labels")


async def _run_create(
    name: str, team: str, color: str | None, description: str | None
) -> None:
    state = get_state()
    async with build_client(state) as client:
        team_id = await _resolve_team_id(client, team)
        input_: dict = {"name": name, "teamId": team_id}
        if color:
            input_["color"] = color
        if description:
            input_["description"] = description
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=str({"input": input_}))
            return
        data = await client.execute(
            mutations.LABEL_CREATE, {"input": input_}, operation="LabelCreate"
        )
        payload = data.get("issueLabelCreate", {})
        if not payload.get("success"):
            raise UsageError("issueLabelCreate returned success=false")
        label = IssueLabel.model_validate(payload["issueLabel"])
        render(label, fmt=state.output, title=f"Created label: {label.name}")


async def _run_delete(id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=f"delete label {id}")
            return
        data = await client.execute(
            mutations.LABEL_DELETE, {"id": id}, operation="LabelDelete"
        )
        ok = data.get("issueLabelDelete", {}).get("success")
        if not ok:
            raise UsageError("issueLabelDelete returned success=false")
        from hoja.models.enums import OutputFormat
        if state.output == OutputFormat.JSON:
            import json as _json
            print(_json.dumps({"deleted": id, "success": True}))
        else:
            print(f"Deleted label {id}")


# Helper used by issue.py to resolve label names → UUIDs for a team
async def resolve_label_ids(
    client, *, team_id: str, names: list[str]
) -> list[str]:
    """Resolve label names to UUIDs for a given team.

    Names that don't resolve raise UsageError listing available labels.
    """
    if not names:
        return []
    filt = {"team": {"id": {"eq": team_id}}}
    labels = await client.execute_list(
        IssueLabel,
        queries.ISSUE_LABELS,
        {"filter": filt, "first": 200},
        path=["issueLabels"],
        operation="IssueLabels",
    )
    by_name = {label.name.lower(): label.id for label in labels}
    ids: list[str] = []
    missing: list[str] = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        # Allow UUIDs to pass through
        if len(n) == 36 and n.count("-") == 4:
            ids.append(n)
            continue
        if n.lower() in by_name:
            ids.append(by_name[n.lower()])
        else:
            missing.append(n)
    if missing:
        available = ", ".join(sorted(label.name for label in labels))
        raise UsageError(
            f"Unknown label(s): {missing}. Available on team: {available}"
        )
    return ids
