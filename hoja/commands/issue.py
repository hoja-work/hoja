"""`hoja issue ...` commands.

Includes list/get/create/update/state/assign/prio/comment.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from hoja.auth import get_viewer
from hoja.cli_state import build_client, get_state
from hoja.errors import NotFoundError, UsageError
from hoja.filters import build_issue_filter
from hoja.graphql import mutations, queries
from hoja.models.issue import Issue
from hoja.models.project import Project
from hoja.models.team import Team
from hoja.output import render

issue_app = typer.Typer(help="Issues")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@issue_app.command("list")
def list_issues(
    team: Optional[str] = typer.Option(None, "--team", "-t", help="Team key (e.g. ENG) or UUID"),
    state: Optional[str] = typer.Option(None, "--state", "-s", help="Workflow state name. Comma-sep for OR."),
    not_state: Optional[str] = typer.Option(None, "--not-state", help="Exclude states (comma-sep)"),
    state_type: Optional[str] = typer.Option(None, "--state-type", help="State type (backlog|started|completed|...)"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="User. 'me' for self, or email/UUID"),
    creator: Optional[str] = typer.Option(None, "--creator", help="User who created the issue"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project name or UUID"),
    cycle: Optional[str] = typer.Option(None, "--cycle", "-c", help="'current', 'next', UUID, or name"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Label name. Comma-sep for OR."),
    priority: Optional[int] = typer.Option(None, "--priority", min=0, max=4, help="Priority 0-4"),
    contains: Optional[str] = typer.Option(None, "--contains", help="Free-text in title/description"),
    updated_after: Optional[str] = typer.Option(None, "--updated-after", help="ISO date or duration (e.g. -P7D)"),
    updated_before: Optional[str] = typer.Option(None, "--updated-before", help="ISO date"),
    due_before: Optional[str] = typer.Option(None, "--due-before", help="ISO date"),
    due_after: Optional[str] = typer.Option(None, "--due-after", help="ISO date"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max issues to return"),
) -> None:
    """List issues, optionally filtered."""
    asyncio.run(_run_list(
        team=team, state=state, not_state=not_state, state_type=state_type,
        assignee=assignee, creator=creator, project=project, cycle=cycle,
        label=label, priority=priority, contains=contains,
        updated_after=updated_after, updated_before=updated_before,
        due_before=due_before, due_after=due_after, limit=limit,
    ))


@issue_app.command("get")
def get_issue(
    id: str = typer.Argument(..., help="Issue identifier (e.g. ENG-123) or UUID"),
) -> None:
    """Get a single issue (full detail)."""
    asyncio.run(_run_get(id))


@issue_app.command("create")
def create_issue(
    team: str = typer.Option(..., "--team", "-t", help="Team key or UUID"),
    title: str = typer.Option(..., "--title", help="Issue title"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Markdown description"),
    priority: Optional[int] = typer.Option(None, "--priority", "-p", min=0, max=4),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="'me', email, or UUID"),
    project: Optional[str] = typer.Option(None, "--project", help="Project UUID, slugId, or name"),
    cycle: Optional[str] = typer.Option(None, "--cycle", help="Cycle UUID"),
    label: Optional[list[str]] = typer.Option(None, "--label", "-l", help="Label name(s)"),
    due_date: Optional[str] = typer.Option(None, "--due-date", help="ISO date YYYY-MM-DD"),
    parent: Optional[str] = typer.Option(None, "--parent", help="Parent issue UUID or identifier"),
) -> None:
    """Create a new issue."""
    asyncio.run(_run_create(
        team=team, title=title, description=description, priority=priority,
        assignee=assignee, project=project, cycle=cycle, label=label or [],
        due_date=due_date, parent=parent,
    ))


@issue_app.command("update")
def update_issue(
    id: str = typer.Argument(..., help="Issue identifier or UUID"),
    title: Optional[str] = typer.Option(None, "--title"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    state: Optional[str] = typer.Option(None, "--state", "-s", help="State name"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="'me', email, or UUID"),
    priority: Optional[int] = typer.Option(None, "--priority", "-p", min=0, max=4),
    project: Optional[str] = typer.Option(None, "--project", help="Project UUID, slugId, or name"),
    cycle: Optional[str] = typer.Option(None, "--cycle", help="Cycle UUID"),
    due_date: Optional[str] = typer.Option(None, "--due-date", help="ISO date"),
    label: Optional[list[str]] = typer.Option(
        None, "--label", "-l",
        help="Replace labels (use multiple --label or comma-separated)",
    ),
) -> None:
    """Update fields on an existing issue."""
    asyncio.run(_run_update(
        id=id, title=title, description=description, state=state,
        assignee=assignee, priority=priority, project=project, cycle=cycle,
        due_date=due_date, label=label or [],
    ))


@issue_app.command("state")
def change_state(
    id: str = typer.Argument(...),
    state: str = typer.Argument(..., help="State name (must match team's workflow)"),
) -> None:
    """Quick-change the workflow state of an issue."""
    asyncio.run(_run_update(id=id, state=state))


@issue_app.command("assign")
def assign_issue(
    id: str = typer.Argument(...),
    user: str = typer.Argument(..., help="'me', email, or user UUID"),
) -> None:
    """Quick-assign an issue."""
    asyncio.run(_run_update(id=id, assignee=user))


@issue_app.command("prio")
def change_priority(
    id: str = typer.Argument(...),
    priority: int = typer.Argument(..., min=0, max=4),
) -> None:
    """Quick-change issue priority (0=none, 1=urgent, 2=high, 3=med, 4=low)."""
    asyncio.run(_run_update(id=id, priority=priority))


@issue_app.command("url")
def issue_url(id: str = typer.Argument(...)) -> None:
    """Print the Linear URL for an issue."""
    asyncio.run(_run_url(id))


@issue_app.command("search")
def search_issues(
    query: str = typer.Argument(..., help="Free-text search query"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Full-text search across issues."""
    asyncio.run(_run_search(query, limit))


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------

async def _run_list(**kwargs) -> None:
    state = get_state()
    limit = kwargs.pop("limit", 50)
    async with build_client(state) as client:
        viewer_id: str | None = None
        if (kwargs.get("assignee") or "").lower() == "me" or (
            kwargs.get("creator") or ""
        ).lower() == "me":
            viewer = await get_viewer(client)
            viewer_id = viewer.id

        filt = build_issue_filter(viewer_id=viewer_id, **kwargs)
        issues = await client.execute_list(
            Issue,
            queries.ISSUES_LIST,
            {"filter": filt or None, "first": limit},
            path=["issues"],
            operation="Issues",
        )
        render(issues, fmt=state.output, title="Issues")


async def _run_get(id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        issue = await client.execute_as(
            Issue,
            queries.ISSUE_BY_IDENTIFIER,
            {"id": id},
            path=["issue"],
            operation="IssueByIdentifier",
        )
        render(issue, fmt=state.output, title=issue.identifier)


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


async def _resolve_project_id(client, value: str) -> str:
    """Resolve a project identifier (UUID, slugId, or name) to a full UUID.

    Linear's ``projectId`` field in mutations requires the full 36-char UUID.
    Users often copy the shorter ``slugId`` (e.g. ``24a5eb4e800e``) from the
    Linear UI, which the API rejects with "Argument Validation Error".
    This helper resolves slugIds and names to the canonical UUID.
    """
    if len(value) == 36 and value.count("-") == 4:
        return value
    # Try slugId first (most common case when copy-pasting from Linear UI)
    projects = await client.execute_list(
        Project, queries.PROJECTS_LIST,
        {"filter": {"slugId": {"eq": value}}, "first": 1},
        path=["projects"], operation="Projects",
    )
    if projects:
        return projects[0].id
    # Fallback: try name match
    projects = await client.execute_list(
        Project, queries.PROJECTS_LIST,
        {"filter": {"name": {"eqIgnoreCase": value}}, "first": 1},
        path=["projects"], operation="Projects",
    )
    if projects:
        return projects[0].id
    raise NotFoundError(
        f"No project with UUID, slugId, or name {value!r}. "
        "Use `hoja project list` to see available projects."
    )


async def _resolve_user_id(client, user: str) -> str:
    if user.lower() == "me":
        return (await get_viewer(client)).id
    if len(user) == 36 and user.count("-") == 4:
        return user
    # Otherwise fall through to email — Linear's IssueUpdateInput requires user UUID
    raise UsageError(
        f"Cannot resolve user {user!r} to a UUID without an extra lookup. "
        "Pass a UUID or 'me' for this operation."
    )


async def _resolve_state_id(client, issue_id: str, state_name: str) -> str:
    """Look up the state UUID for a given state name on the issue's team."""
    issue = await client.execute_as(
        Issue, queries.ISSUE_BY_IDENTIFIER, {"id": issue_id},
        path=["issue"], operation="IssueByIdentifier",
    )
    if not issue.team:
        raise NotFoundError(f"Issue {issue_id} has no team")
    team_id = issue.team["id"]
    from hoja.models.workflow import WorkflowState
    states = await client.execute_list(
        WorkflowState, queries.TEAM_STATES, {"teamId": team_id},
        path=["team", "states"], operation="TeamStates",
    )
    for s in states:
        if s.name.lower() == state_name.lower():
            return s.id
    available = ", ".join(s.name for s in states)
    raise NotFoundError(
        f"No state named {state_name!r} on team. Available: {available}"
    )


async def _run_create(**kwargs) -> None:
    state = get_state()
    async with build_client(state) as client:
        team_id = await _resolve_team_id(client, kwargs["team"])
        input_: dict = {
            "teamId": team_id,
            "title": kwargs["title"],
        }
        if kwargs.get("description"):
            input_["description"] = kwargs["description"]
        if kwargs.get("priority") is not None:
            input_["priority"] = kwargs["priority"]
        if kwargs.get("assignee"):
            input_["assigneeId"] = await _resolve_user_id(client, kwargs["assignee"])
        if kwargs.get("project"):
            input_["projectId"] = await _resolve_project_id(client, kwargs["project"])
        if kwargs.get("cycle"):
            input_["cycleId"] = kwargs["cycle"]
        if kwargs.get("due_date"):
            input_["dueDate"] = kwargs["due_date"]
        if kwargs.get("parent"):
            input_["parentId"] = kwargs["parent"]
        labels = kwargs.get("label") or []
        # Allow comma-separated single flag value too
        flat_labels: list[str] = []
        for v in labels:
            flat_labels.extend(p.strip() for p in v.split(",") if p.strip())
        if flat_labels:
            from hoja.commands.label import resolve_label_ids
            input_["labelIds"] = await resolve_label_ids(
                client, team_id=team_id, names=flat_labels
            )

        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=str({"input": input_}))
            return

        data = await client.execute(
            mutations.ISSUE_CREATE,
            {"input": input_},
            operation="IssueCreate",
        )
        payload = data.get("issueCreate", {})
        if not payload.get("success"):
            raise NotFoundError("issueCreate returned success=false")
        issue = Issue.model_validate(payload["issue"])
        render(issue, fmt=state.output, title=f"Created {issue.identifier}")


async def _run_update(**kwargs) -> None:
    state = get_state()
    issue_id = kwargs.pop("id")
    async with build_client(state) as client:
        input_: dict = {}
        if kwargs.get("title"):
            input_["title"] = kwargs["title"]
        if kwargs.get("description"):
            input_["description"] = kwargs["description"]
        if kwargs.get("priority") is not None:
            input_["priority"] = kwargs["priority"]
        if kwargs.get("assignee"):
            input_["assigneeId"] = await _resolve_user_id(client, kwargs["assignee"])
        if kwargs.get("project"):
            input_["projectId"] = await _resolve_project_id(client, kwargs["project"])
        if kwargs.get("cycle"):
            input_["cycleId"] = kwargs["cycle"]
        if kwargs.get("due_date"):
            input_["dueDate"] = kwargs["due_date"]
        if kwargs.get("state"):
            input_["stateId"] = await _resolve_state_id(client, issue_id, kwargs["state"])

        labels = kwargs.get("label") or []
        flat_labels: list[str] = []
        for v in labels:
            flat_labels.extend(p.strip() for p in v.split(",") if p.strip())
        if flat_labels:
            # Need the team to resolve labels — fetch issue first
            issue = await client.execute_as(
                Issue, queries.ISSUE_BY_IDENTIFIER, {"id": issue_id},
                path=["issue"], operation="IssueByIdentifier",
            )
            if not issue.team:
                raise NotFoundError(f"Issue {issue_id} has no team")
            from hoja.commands.label import resolve_label_ids
            input_["labelIds"] = await resolve_label_ids(
                client, team_id=issue.team["id"], names=flat_labels
            )

        if not input_:
            raise UsageError("Nothing to update — pass at least one field")

        if state.dry_run:
            from hoja.output import emit_error
            emit_error("dry-run: not executing", hint=str({"id": issue_id, "input": input_}))
            return

        data = await client.execute(
            mutations.ISSUE_UPDATE,
            {"id": issue_id, "input": input_},
            operation="IssueUpdate",
        )
        payload = data.get("issueUpdate", {})
        if not payload.get("success"):
            raise NotFoundError("issueUpdate returned success=false")
        issue = Issue.model_validate(payload["issue"])
        render(issue, fmt=state.output, title=f"Updated {issue.identifier}")


async def _run_url(id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        issue = await client.execute_as(
            Issue, queries.ISSUE_BY_IDENTIFIER, {"id": id},
            path=["issue"], operation="IssueByIdentifier",
        )
        print(issue.url or "")


async def _run_search(query: str, limit: int) -> None:
    state = get_state()
    async with build_client(state) as client:
        issues = await client.execute_list(
            Issue, queries.SEARCH_ISSUES, {"term": query, "first": limit},
            path=["searchIssues"], operation="SearchIssues",
        )
        render(issues, fmt=state.output, title=f"Search: {query!r}")
