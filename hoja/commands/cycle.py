"""`hoja cycle ...` commands."""

from __future__ import annotations

import asyncio

import typer

from hoja.cli_state import build_client, get_state
from hoja.errors import NotFoundError
from hoja.graphql import queries
from hoja.models.cycle import Cycle
from hoja.models.team import Team
from hoja.output import render

cycle_app = typer.Typer(help="Cycles (sprints)")


@cycle_app.command("current")
def current_cycle(team: str = typer.Argument(..., help="Team key (e.g. ENG)")) -> None:
    """Show the currently-active cycle for a team."""
    asyncio.run(_run_current(team))


@cycle_app.command("list")
def list_cycles(team: str = typer.Argument(..., help="Team key (e.g. ENG)")) -> None:
    """List cycles for a team."""
    asyncio.run(_run_list(team))


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


async def _run_current(team: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        team_id = await _resolve_team_id(client, team)
        data = await client.execute(
            queries.TEAM_CYCLES, {"teamId": team_id}, operation="TeamCycles"
        )
        active = data.get("team", {}).get("activeCycle")
        if not active:
            # Graceful empty result — exit 0, but signal absence cleanly
            from hoja.models.enums import OutputFormat
            from hoja.output import emit_error
            if state.output == OutputFormat.JSON:
                import json as _json
                print(_json.dumps({"active_cycle": None, "team": team}, indent=2))
            elif state.output == OutputFormat.IDS:
                pass  # empty stdout — no IDs to print
            else:
                emit_error(f"No active cycle on team {team!r}",
                           hint="Run `hoja cycle list <team>` to see all cycles")
            return
        render(Cycle.model_validate(active), fmt=state.output, title="Active Cycle")


async def _run_list(team: str) -> None:
    state = get_state()
    async with build_client(state) as client:
        team_id = await _resolve_team_id(client, team)
        cycles = await client.execute_list(
            Cycle, queries.TEAM_CYCLES, {"teamId": team_id},
            path=["team", "cycles"], operation="TeamCycles",
        )
        render(cycles, fmt=state.output, title=f"Cycles ({team})")
