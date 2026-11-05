"""`hoja project ...` commands."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from hoja.cli_state import build_client, get_state
from hoja.graphql import queries
from hoja.models.project import Project
from hoja.output import render

project_app = typer.Typer(help="Projects")


@project_app.command("list")
def list_projects(
    state_filter: Optional[str] = typer.Option(
        None, "--state", help="started|planned|completed|paused|backlog|canceled"
    ),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """List projects."""
    asyncio.run(_run_list(state_filter, limit))


@project_app.command("get")
def get_project(id: str = typer.Argument(..., help="Project UUID")) -> None:
    """Get a project by UUID."""
    asyncio.run(_run_get(id))


async def _run_list(state_filter: str | None, limit: int) -> None:
    state = get_state()
    filt: dict | None = None
    if state_filter:
        filt = {"state": {"eq": state_filter}}
    async with build_client(state) as client:
        projects = await client.execute_list(
            Project, queries.PROJECTS_LIST,
            {"filter": filt, "first": limit},
            path=["projects"], operation="Projects",
        )
        render(projects, fmt=state.output, title="Projects")


async def _run_get(id: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        project = await client.execute_as(
            Project, queries.PROJECT_BY_ID, {"id": id},
            path=["project"], operation="Project",
        )
        render(project, fmt=state.output, title=project.name)
