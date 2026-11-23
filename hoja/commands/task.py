"""`hoja issue ...` commands.

Includes list/get/create/update/state/assign/prio/comment.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from hoja.auth import get_viewer
from hoja.cli_state import build_client, get_state
from hoja.client import GraphClient
from hoja.errors import APIError, NotFoundError, UsageError, ValidationError
from hoja.filters import build_issue_filter
from hoja.graphql import mutations, queries
from hoja.models.enums import OutputFormat
from hoja.models.task import Issue
from hoja.models.project import Project
from hoja.models.team import Team
from hoja.models.view import SavedView
from hoja.output import console, err_console, render
from hoja.similarity import Corpus, Document

task_app = typer.Typer(help="Tasks")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@task_app.command("list")
def list_issues(
    team: Optional[str] = typer.Option(None, "--team", "-t", help="Team key (e.g. ENG) or UUID"),
    state: Optional[str] = typer.Option(None, "--state", "-s", help="Workflow state name. Comma-sep for OR."),
    not_state: Optional[str] = typer.Option(None, "--not-state", help="Exclude states (comma-sep)"),
    state_type: Optional[str] = typer.Option(None, "--state-type", help="State type (backlog|started|completed|...)"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="User. 'me' for self, or email/UUID"),
    creator: Optional[str] = typer.Option(None, "--creator", help="User who created the issue"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project name or UUID"),
    cycle: Optional[str] = typer.Option(None, "--cycle", "-c", help="'current', 'next', UUID, or name"),
    parent: Optional[str] = typer.Option(
        None, "--parent",
        help="Only direct children of this issue (identifier or UUID)",
    ),
    no_parent: bool = typer.Option(
        False, "--no-parent",
        help="Only issues with no parent (orphans)",
    ),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Label name. Comma-sep for OR."),
    priority: Optional[int] = typer.Option(None, "--priority", min=0, max=4, help="Priority 0-4"),
    contains: Optional[str] = typer.Option(None, "--contains", help="Free-text in title/description"),
    updated_after: Optional[str] = typer.Option(None, "--updated-after", help="ISO date or duration (e.g. -P7D)"),
    updated_before: Optional[str] = typer.Option(None, "--updated-before", help="ISO date"),
    due_before: Optional[str] = typer.Option(None, "--due-before", help="ISO date"),
    due_after: Optional[str] = typer.Option(None, "--due-after", help="ISO date"),
    view: str | None = typer.Option(
        None, "--view",
        help="Run a saved task view (name, slug or id) on the server. Other filters narrow it.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max issues to return"),
) -> None:
    """List issues, optionally filtered or through a saved view."""
    asyncio.run(_run_list(
        team=team, state=state, not_state=not_state, state_type=state_type,
        assignee=assignee, creator=creator, project=project, cycle=cycle,
        parent=parent, no_parent=no_parent,
        label=label, priority=priority, contains=contains,
        updated_after=updated_after, updated_before=updated_before,
        due_before=due_before, due_after=due_after, view=view, limit=limit,
    ))


@task_app.command("get")
def get_issue(
    id: str = typer.Argument(..., help="Issue identifier (e.g. ENG-123) or UUID"),
) -> None:
    """Get a single issue (full detail)."""
    asyncio.run(_run_get(id))


@task_app.command("create")
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


@task_app.command("update")
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
    parent: Optional[str] = typer.Option(
        None, "--parent",
        help="Parent issue (identifier or UUID). Pass '' to detach, or use --no-parent.",
    ),
    no_parent: bool = typer.Option(
        False, "--no-parent",
        help="Detach this issue from its parent",
    ),
    label: Optional[list[str]] = typer.Option(
        None, "--label", "-l",
        help="Replace labels (use multiple --label or comma-separated)",
    ),
) -> None:
    """Update fields on an existing issue."""
    asyncio.run(_run_update(
        id=id, title=title, description=description, state=state,
        assignee=assignee, priority=priority, project=project, cycle=cycle,
        due_date=due_date, parent=parent, no_parent=no_parent, label=label or [],
    ))


@task_app.command("state")
def change_state(
    id: str = typer.Argument(...),
    state: str = typer.Argument(..., help="State name (must match team's workflow)"),
) -> None:
    """Quick-change the workflow state of an issue."""
    asyncio.run(_run_update(id=id, state=state))


@task_app.command("assign")
def assign_issue(
    id: str = typer.Argument(...),
    user: str = typer.Argument(..., help="'me', email, or user UUID"),
) -> None:
    """Quick-assign an issue."""
    asyncio.run(_run_update(id=id, assignee=user))


@task_app.command("prio")
def change_priority(
    id: str = typer.Argument(...),
    priority: int = typer.Argument(..., min=0, max=4),
) -> None:
    """Quick-change issue priority (0=none, 1=urgent, 2=high, 3=med, 4=low)."""
    asyncio.run(_run_update(id=id, priority=priority))


@task_app.command("url")
def issue_url(id: str = typer.Argument(...)) -> None:
    """Print the upstream API URL for an issue."""
    asyncio.run(_run_url(id))


@task_app.command("search")
def search_issues(
    query: str = typer.Argument(..., help="Free-text search query"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Full-text search across issues."""
    asyncio.run(_run_search(query, limit))


@task_app.command("similar")
def similar_issues(
    text: str | None = typer.Argument(
        None,
        help="Draft text to compare against the tracker. Omit it to read stdin.",
    ),
    title: str | None = typer.Option(None, "--title", help="Draft title (ranked above body text)"),
    description_file: str | None = typer.Option(
        None,
        "--description-file",
        help="Read the draft body from a file (use - for stdin)",
    ),
    limit: int = typer.Option(5, "--limit", "-n", min=1, max=50),
    state_types: str | None = typer.Option(
        None,
        "--state-types",
        help="Comma-separated state types to scan (e.g. backlog,unstarted,started). Default: all.",
    ),
    exclude: str | None = typer.Option(
        None,
        "--exclude",
        help="Identifier or UUID to leave out — normally the issue being checked itself.",
    ),
    min_coverage: float | None = typer.Option(
        None,
        "--min-coverage",
        min=0.0,
        max=1.0,
        help=(
            "Drop candidates matching less than this share of the draft's distinct terms. "
            "Default 0 (show everything); --fail-on-duplicate defaults it to 0.25."
        ),
    ),
    min_terms: int | None = typer.Option(
        None,
        "--min-terms",
        min=0,
        help="Drop candidates matching fewer than this many distinct terms. Default 0.",
    ),
    min_strength: float | None = typer.Option(
        None,
        "--min-strength",
        min=0.0,
        max=1.0,
        help=(
            "Drop candidates holding less than this share of the draft's IDF mass. "
            "Defaults to 0 for listing and to 0.6 for --fail-on-duplicate."
        ),
    ),
    fail_on_duplicate: bool = typer.Option(
        False,
        "--fail-on-duplicate",
        help="Exit 1 when the best candidate looks like a duplicate, for a pre-create hook.",
    ),
) -> None:
    """Rank existing issues against a draft, so a duplicate is visible before it is filed.

    Ranking is lexical and client-side (the server's search only does literal
    substring matching, ordered by update time): IDF-weighted token overlap with
    the title weighted three times, stop-words removed, light stemming. Each row
    reports the score, the coverage (share of the draft's distinct terms that
    matched) and the matched terms, so the reason is visible.

    Known blind spot in this phase: synonyms, typos and reordered phrasing are
    not matched — "log out" will not find "sign-out".

    Exit codes: 0 normally; 1 with --fail-on-duplicate when a likely duplicate
    exists (coverage >= --min-coverage and >= --min-terms distinctive terms);
    2 for usage errors.
    """
    asyncio.run(
        _run_similar(
            text=text,
            title=title,
            description_file=description_file,
            limit=limit,
            state_types=state_types,
            exclude=exclude,
            min_coverage=min_coverage,
            min_terms=min_terms,
            min_strength=min_strength,
            fail_on_duplicate=fail_on_duplicate,
        )
    )


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------

async def _run_list(**kwargs) -> None:
    state = get_state()
    limit = kwargs.pop("limit", 50)
    view_ref = kwargs.pop("view", None)
    async with build_client(state) as client:
        viewer_id: str | None = None
        if (kwargs.get("assignee") or "").lower() == "me" or (
            kwargs.get("creator") or ""
        ).lower() == "me":
            viewer = await get_viewer(client)
            viewer_id = viewer.id

        parent_ref = kwargs.pop("parent", None)
        no_parent = bool(kwargs.pop("no_parent", False))
        if parent_ref and no_parent:
            raise UsageError("--parent and --no-parent are mutually exclusive")
        if parent_ref:
            # Resolve here: a child is stored by UUID, so an identifier such as
            # ENG-12 must be translated before it reaches the filter.
            kwargs["parent"] = (await _fetch_issue(client, parent_ref)).id
        elif no_parent:
            kwargs["no_parent"] = True

        if view_ref is not None and kwargs.get("project"):
            # The view grammar matches a project by id or exact name, not by a
            # substring, so resolve the flag to one project first.
            kwargs["project"] = await _resolve_project_id(client, kwargs["project"])
        filt = build_issue_filter(viewer_id=viewer_id, **kwargs)
        if view_ref is not None:
            saved = await _resolve_view(client, view_ref)
            if "cycle" in filt:
                # The view grammar names a cycle by id; "current", "next" and part of a
                # name are resolved by the server's own cycles query first.
                cycle_ids = await _resolve_cycle_ids(client, filt["cycle"])
                if not cycle_ids:
                    render([], fmt=state.output, title=saved.name)
                    return
                filt["cycle"] = {"id": {"in": cycle_ids}}
            issues = await _run_view_issues(client, saved, filt, limit)
            render(issues, fmt=state.output, title=saved.name)
            return
        try:
            issues = await client.execute_list(
                Issue,
                queries.ISSUES_LIST,
                {"filter": filt or None, "first": limit},
                path=["issues"],
                operation="Issues",
            )
        except APIError as e:
            # An older server has no ``IssueParentFilter.isNull``; say so instead
            # of leaking the schema validation error.
            if no_parent and "isNull" in e.message:
                raise UsageError(
                    "--no-parent needs a Hoja server that supports "
                    "IssueParentFilter.isNull (this one does not).",
                    hint="Upgrade the server, or filter with `--parent <issue>`.",
                ) from e
            raise
        render(issues, fmt=state.output, title="Issues")


# Page size the server allows for one ``View.issues`` page.
_VIEW_PAGE = 250


async def _resolve_cycle_ids(client: GraphClient, cycle_filter: dict[str, Any]) -> list[str]:
    """The ids of the cycles a ``CycleFilter`` selects (active, next, by name or id).

    Run through the server's ``cycles`` query, so the ids are exactly the cycles the
    plain ``issues`` path would match, limited to teams the caller can see.
    """
    if set(cycle_filter) == {"id"}:
        cmp = cycle_filter["id"]
        if set(cmp) == {"eq"}:
            return [cmp["eq"]]
        if set(cmp) == {"in"}:
            return list(cmp["in"])
    data = await client.execute(
        queries.CYCLE_IDS, {"filter": cycle_filter, "first": _VIEW_PAGE},
        operation="CycleIds",
    )
    nodes = ((data.get("cycles") or {}).get("nodes")) or []
    return [node["id"] for node in nodes if node.get("id")]


async def _resolve_view(client: GraphClient, ref: str) -> SavedView:
    """Find a task view the caller can read by id, slug, or name (case-insensitive)."""
    wanted = ref.strip()
    if not wanted:
        raise UsageError("--view needs a view name, slug or id")
    data = await client.execute(queries.ISSUE_VIEWS, operation="IssueViews")
    try:
        views = [SavedView.model_validate(row) for row in data.get("views") or []]
    except Exception as e:  # pydantic.ValidationError
        raise ValidationError(f"Response did not match SavedView: {e}") from e
    for view in views:
        if wanted in (view.id, view.slug_id):
            return view
    named = [view for view in views if view.name.casefold() == wanted.casefold()]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise UsageError(
            f"{len(named)} task views are named {wanted!r}",
            hint="Pass the slug or id instead: " + ", ".join(v.slug_id for v in named),
        )
    raise NotFoundError(
        f"No task view {wanted!r} that you can read",
        hint="List views in the web app, or pass the view's slug or id.",
    )


async def _run_view_issues(client: GraphClient, view: SavedView, filt: dict[str, Any], limit: int) -> list[Issue]:
    """Run the saved view on the server, page by page, until ``limit`` rows."""
    out: list[Issue] = []
    after: str | None = None
    while len(out) < limit:
        try:
            data = await client.execute(
                queries.VIEW_ISSUES,
                {"id": view.id, "first": min(_VIEW_PAGE, limit - len(out)),
                 "after": after, "filter": filt or None},
                operation="ViewIssues",
            )
        except APIError as e:
            bad = [err for err in e.errors
                   if (err.get("extensions") or {}).get("code") == "BAD_USER_INPUT"]
            if bad:
                # A refusal is the flags' fault only when flags were sent and the server
                # did not name paging; otherwise the saved view itself cannot run.
                field = str((bad[0].get("extensions") or {}).get("field") or "")
                if filt and field not in ("first", "after"):
                    raise UsageError(
                        f"These filters cannot be combined with --view: {e.message}",
                        hint="Drop the flag the message names, or save it into the view.",
                    ) from e
                raise UsageError(f"View {view.name!r} cannot run: {e.message}") from e
            raise
        conn = ((data.get("customView") or {}).get("issues")) or {}
        try:
            out.extend(Issue.model_validate(node) for node in conn.get("nodes") or [])
        except Exception as e:  # pydantic.ValidationError
            raise ValidationError(f"List items did not match Issue: {e}") from e
        page = conn.get("pageInfo") or {}
        after = page.get("endCursor")
        if not page.get("hasNextPage") or not after:
            break
    return out[:limit]


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

    the upstream contract's ``projectId`` field in mutations requires the full 36-char UUID.
    Users often copy the shorter ``slugId`` (e.g. ``24a5eb4e800e``) from the
    the upstream API UI, which the API rejects with "Argument Validation Error".
    This helper resolves slugIds and names to the canonical UUID.
    """
    if len(value) == 36 and value.count("-") == 4:
        return value
    # Try slugId first (most common case when copy-pasting from the upstream API UI)
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
    # Otherwise fall through to email — the upstream contract's IssueUpdateInput requires user UUID
    raise UsageError(
        f"Cannot resolve user {user!r} to a UUID without an extra lookup. "
        "Pass a UUID or 'me' for this operation."
    )


async def _fetch_issue(client, value: str) -> Issue:
    """Fetch one issue by identifier (ENG-12) or UUID, with a precise failure.

    Returns the full Issue record (the query includes ``parent { id identifier
    title }``), which the parent helpers need to walk the hierarchy.
    """
    ref = (value or "").strip()
    if not ref:
        raise UsageError("Expected an issue identifier (e.g. ENG-12) or UUID")
    data = await client.execute(
        queries.ISSUE_BY_IDENTIFIER, {"id": ref}, operation="IssueByIdentifier",
    )
    node = (data or {}).get("issue")
    if not node:
        raise NotFoundError(
            f"No issue with identifier or id {ref!r}",
            hint="List candidates with `hoja issue list` or `hoja issue search <term>`.",
        )
    return Issue.model_validate(node)


async def _resolve_parent(client, value: str, *, team: dict | None = None) -> Issue:
    """Resolve a ``--parent`` value and check it can legally be a parent.

    The server validates both cases, but its error is a bare
    ``Invalid reference``; resolving first lets the CLI say which value was
    wrong and why.
    """
    parent = await _fetch_issue(client, value)
    if team and parent.team and parent.team.get("id") != team.get("id"):
        raise UsageError(
            f"{parent.identifier} is in team "
            f"{parent.team.get('key') or parent.team.get('id')}, but this issue is in "
            f"{team.get('key') or team.get('id')} — a parent must be in the same team.",
            hint="Only issues from the same team can be nested.",
        )
    return parent


async def _assert_no_parent_cycle(client, issue: Issue, parent: Issue) -> None:
    """Refuse a re-parent that would make ``issue`` its own ancestor."""
    seen: set[str] = set()
    cursor: Issue | None = parent
    chain: list[str] = [parent.identifier]
    while cursor is not None and cursor.id not in seen:
        if cursor.id == issue.id:
            if len(chain) == 1:
                raise UsageError(
                    f"Cannot make {issue.identifier} its own parent: an issue cannot be "
                    "its own ancestor.",
                    hint="Pick a different issue as the parent.",
                )
            raise UsageError(
                f"Cannot make {issue.identifier} a child of {parent.identifier}: "
                f"{issue.identifier} is already an ancestor of {parent.identifier} "
                f"({' -> '.join(reversed(chain))}), so the link would create a cycle.",
                hint="A parent must not be the issue itself or one of its own sub-issues.",
            )
        seen.add(cursor.id)
        ancestor_id = (cursor.parent or {}).get("id")
        if not ancestor_id:
            break
        cursor = await _fetch_issue(client, ancestor_id)
        chain.append(cursor.identifier)


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
            team_ref = kwargs["team"]
            team_hint: dict = {"id": team_id}
            if not (len(team_ref) == 36 and team_ref.count("-") == 4):
                team_hint["key"] = team_ref.upper()
            parent = await _resolve_parent(client, kwargs["parent"], team=team_hint)
            input_["parentId"] = parent.id
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

        try:
            data = await client.execute(
                mutations.ISSUE_CREATE,
                {"input": input_},
                operation="IssueCreate",
            )
        except APIError as e:
            raise _parent_error(e, input_.get("parentId")) from e
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

        # --parent / --no-parent. ``--parent ''`` is the shorthand for detach.
        parent_ref = kwargs.get("parent")
        detach = bool(kwargs.get("no_parent"))
        if isinstance(parent_ref, str) and not parent_ref.strip():
            parent_ref, detach = None, True
        if parent_ref and detach:
            raise UsageError("--parent and --no-parent are mutually exclusive")
        if parent_ref:
            issue = await _fetch_issue(client, issue_id)
            parent = await _resolve_parent(client, parent_ref, team=issue.team)
            await _assert_no_parent_cycle(client, issue, parent)
            input_["parentId"] = parent.id
        elif detach:
            # Explicit detach. The client strips None from variables, so an empty
            # string is the wire form the server reads as "clear parent_id".
            input_["parentId"] = ""

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

        try:
            data = await client.execute(
                mutations.ISSUE_UPDATE,
                {"id": issue_id, "input": input_},
                operation="IssueUpdate",
            )
        except APIError as e:
            raise _parent_error(e, input_.get("parentId")) from e
        payload = data.get("issueUpdate", {})
        if not payload.get("success"):
            raise NotFoundError("issueUpdate returned success=false")
        issue = Issue.model_validate(payload["issue"])
        render(issue, fmt=state.output, title=f"Updated {issue.identifier}")


def _parent_error(error: APIError, parent_id: str | None) -> Exception:
    """Explain a server-side parentId rejection instead of printing
    "Invalid reference"."""
    fields = {
        (err.get("extensions") or {}).get("field") for err in error.errors
    }
    if parent_id and "parentId" in fields:
        return UsageError(
            f"The server refused parent {parent_id!r}: {error.message}",
            hint=(
                "A parent must be an active issue in the same team, and the link "
                "must not make the issue its own ancestor."
            ),
        )
    return error


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


# ---------------------------------------------------------------------------
# similar (HOJA-208)
# ---------------------------------------------------------------------------

# Titles and bodies in one request: ISSUE_CORE (the fragment `issue list` uses)
# carries no description, so ranking on bodies needs its own selection. Paging
# is required, not optional — the API rejects `first` above 250 and this
# workspace already holds more issues than that.
SIMILAR_CORPUS = """
query SimilarCorpus($filter: IssueFilter, $first: Int!, $after: String) {
  issues(filter: $filter, first: $first, after: $after) {
    nodes { id identifier title description state { name type } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# The server's own ceiling for one page (`resolvers._MAX_PAGE`).
_SIMILAR_PAGE = 250
# 40 pages is 10 000 issues: far past this workspace, and a bound so a broken
# cursor cannot spin forever. Hitting it is reported, never silently ignored.
_SIMILAR_MAX_PAGES = 40

# Calibrated on this workspace, not guessed (HOJA-208 report): over 12
# paraphrases and 12 genuinely new drafts, 0.60 fired on 9 paraphrases and 2
# new drafts; 0.65 fired on 7 paraphrases and 0 new drafts. A pre-create hook
# must not block a real issue, and the ranked list is printed either way, so
# the gate takes precision over recall.
_GATE_STRENGTH = 0.65


def _read_draft(
    text: str | None,
    title: str | None,
    description_file: str | None,
) -> str:
    """Assemble the draft from arguments, a file, or stdin — in that order."""
    parts: list[str] = []
    if title:
        parts.append(title)
    if text:
        parts.append(text)
    if description_file is not None:
        if description_file == "-":
            parts.append(sys.stdin.read())
        else:
            path = Path(description_file)
            try:
                parts.append(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise UsageError(
                    f"Cannot read draft file {path}: {exc}",
                    hint="Pass a readable path, or - to read the draft from stdin.",
                ) from exc
    if not parts and not sys.stdin.isatty():
        parts.append(sys.stdin.read())
    draft = "\n".join(part for part in parts if part).strip()
    if not draft:
        raise UsageError(
            "No draft text to compare.",
            hint=(
                "Pass the draft as an argument, with --title/--description-file, "
                "or pipe it on stdin."
            ),
        )
    return draft


async def _fetch_documents(
    client,
    state_types: list[str] | None,
) -> tuple[list[Document], int, bool]:
    """Page the whole issue corpus. Returns (documents, pages, complete)."""
    variables: dict[str, object] = {"first": _SIMILAR_PAGE}
    if state_types:
        variables["filter"] = {"state": {"type": {"in": state_types}}}
    documents: list[Document] = []
    after: str | None = None
    pages = 0
    complete = False
    while True:
        data = await client.execute(
            SIMILAR_CORPUS,
            {**variables, "after": after},
            operation="SimilarCorpus",
        )
        connection = data.get("issues") or {}
        for node in connection.get("nodes") or []:
            state = node.get("state") or {}
            documents.append(
                Document(
                    identifier=node.get("identifier") or "",
                    title=node.get("title") or "",
                    description=node.get("description"),
                    state_name=state.get("name"),
                    state_type=state.get("type"),
                )
            )
        pages += 1
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            complete = True
            break
        after = page_info.get("endCursor")
        if not after or pages >= _SIMILAR_MAX_PAGES:
            break
    return documents, pages, complete


async def _run_similar(
    *,
    text: str | None,
    title: str | None,
    description_file: str | None,
    limit: int,
    state_types: str | None,
    exclude: str | None,
    min_coverage: float | None,
    min_terms: int | None,
    min_strength: float | None,
    fail_on_duplicate: bool,
) -> None:
    state = get_state()
    draft = _read_draft(text, title, description_file)
    types = [part.strip() for part in (state_types or "").split(",") if part.strip()]

    async with build_client(state) as client:
        documents, pages, complete = await _fetch_documents(client, types or None)

    corpus = Corpus(documents)
    gate_strength = _GATE_STRENGTH if min_strength is None else min_strength
    hits = corpus.rank(
        draft,
        limit=limit,
        exclude=[exclude] if exclude else [],
        min_coverage=0.0 if min_coverage is None else min_coverage,
        min_terms=0 if min_terms is None else min_terms,
        min_strength=0.0 if min_strength is None else min_strength,
    )
    # The gate is evaluated separately from the listing so that `-n 1` and a
    # display filter cannot hide a duplicate from a pre-create hook.
    gate_hits = corpus.rank(
        draft,
        limit=1,
        exclude=[exclude] if exclude else [],
        min_coverage=0.0 if min_coverage is None else min_coverage,
        min_terms=0 if min_terms is None else min_terms,
        min_strength=gate_strength,
    )

    if not complete:
        err_console.print(
            f"[yellow]warning:[/] scanned {len(documents)} issues in {pages} pages and "
            "the server still reported another page; results cover that window only.",
        )
    elif not state.quiet:
        err_console.print(f"[dim]scanned {len(documents)} issues in {pages} page(s)[/]")

    rows = [hit.as_dict() for hit in hits]
    if state.output is OutputFormat.HUMAN:
        _render_similar(rows)
    else:
        render(rows, fmt=state.output, title="Similar issues")

    if fail_on_duplicate and gate_hits:
        best = gate_hits[0]
        err_console.print(
            f"[red]duplicate likely:[/] {best.identifier} "
            f"(score {best.score:.2f}, coverage {best.coverage:.0%}, "
            f"strength {best.strength:.0%}; terms: {', '.join(best.matched_terms[:4])})"
        )
        raise typer.Exit(1)


def _render_similar(rows: list[dict]) -> None:
    if not rows:
        console.print("[dim]No candidates above the threshold.[/]")
        return
    from rich.table import Table

    table = Table(title="Similar issues", show_lines=False)
    table.add_column("ID", no_wrap=True)
    table.add_column("State")
    table.add_column("Score", justify="right")
    table.add_column("Coverage", justify="right")
    table.add_column("Strength", justify="right")
    table.add_column("Matched", overflow="fold")
    table.add_column("Title", overflow="fold")
    for row in rows:
        table.add_row(
            str(row["identifier"]),
            str(row.get("state") or "—"),
            f"{row['score']:.2f}",
            f"{float(row['coverage']):.0%}",
            f"{float(row['strength']):.0%}",
            ", ".join(row["matchedTerms"][:4]),
            str(row["title"]),
        )
    console.print(table)
