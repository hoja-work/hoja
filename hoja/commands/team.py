"""`hoja team ...` commands."""

from __future__ import annotations

import asyncio

import typer

from hoja.cli_state import build_client, get_state
from hoja.errors import NotFoundError
from hoja.graphql import queries
from hoja.models.team import Team
from hoja.models.user import User
from hoja.models.workflow import WorkflowState
from hoja.output import render

team_app = typer.Typer(help="Teams")


@team_app.command("list")
def list_teams() -> None:
    """List all teams in the workspace."""
    asyncio.run(_run_list())


@team_app.command("get")
def get_team(key: str = typer.Argument(..., help="Team key (e.g. ENG) or UUID")) -> None:
    """Get a single team by key or id."""
    asyncio.run(_run_get(key))


@team_app.command("states")
def team_states(
    key: str = typer.Argument(..., help="Team key or UUID"),
) -> None:
    """List workflow states for a team."""
    asyncio.run(_run_states(key))


@team_app.command("members")
def team_members(
    key: str = typer.Argument(..., help="Team key or UUID"),
) -> None:
    """List members of a team."""
    asyncio.run(_run_members(key))


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


async def _run_list() -> None:
    state = get_state()
    async with build_client(state) as client:
        teams = await client.execute_list(
            Team, queries.TEAMS, path=["teams"], operation="Teams"
        )
        render(teams, fmt=state.output, title="Teams")


async def _run_get(key: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        teams = await client.execute_list(
            Team, queries.TEAM_BY_KEY, {"key": key.upper()},
            path=["teams"], operation="TeamByKey",
        )
        if not teams:
            raise NotFoundError(f"No team with key {key!r}")
        render(teams[0], fmt=state.output)


async def _run_states(key: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        team_id = await _resolve_team_id(client, key)
        states = await client.execute_list(
            WorkflowState, queries.TEAM_STATES, {"teamId": team_id},
            path=["team", "states"], operation="TeamStates",
        )
        render(states, fmt=state.output, title=f"States for {key}")


async def _run_members(key: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        team_id = await _resolve_team_id(client, key)
        members = await client.execute_list(
            User, queries.TEAM_MEMBERS, {"teamId": team_id},
            path=["team", "members"], operation="TeamMembers",
        )
        render(members, fmt=state.output, title=f"Members of {key}")
