"""`hoja me` and `hoja auth ...` commands."""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any, Optional

import typer

from hoja.auth import get_viewer
from hoja.cli_state import build_client, get_state
from hoja.client import LinearClient
from hoja.config import (
    AccountConfig,
    load_config,
    resolve_workspace,
    save_config,
)
from hoja.errors import AuthError, UsageError
from hoja.models.enums import OutputFormat
from hoja.output import render

me_app = typer.Typer(help="Show the currently-authenticated Linear user")
auth_app = typer.Typer(help="Authentication operations")


@me_app.callback(invoke_without_command=True)
def me(ctx: typer.Context) -> None:
    """Show the currently-authenticated user (alias for `auth status`)."""
    if ctx.invoked_subcommand is not None:
        return
    asyncio.run(_run_me())


@auth_app.command("status")
def status() -> None:
    """Show the currently-authenticated user and token source."""
    asyncio.run(_run_me())


@auth_app.command("whoami")
def whoami() -> None:
    """Alias of `auth status`."""
    asyncio.run(_run_me())


async def _run_me() -> None:
    state = get_state()
    async with build_client(state) as client:
        viewer = await get_viewer(client)
        render(viewer, fmt=state.output, title="Viewer")


@auth_app.command("accounts")
def accounts_list() -> None:
    """List all configured accounts with default/workspace/current markers."""
    state = get_state()
    config = state.config
    workspace = resolve_workspace()
    workspace_account = (
        config.workspaces.get(str(workspace)) if workspace else None
    )

    account_rows: list[dict[str, Any]] = []
    for name, acc in config.accounts.items():
        markers: list[str] = []
        if name == config.defaults.default_account:
            markers.append("default")
        if name == workspace_account:
            markers.append("workspace")
        if name == state.account_name:
            markers.append("current")
        account_rows.append(
            {
                "name": name,
                "org_name": acc.org_name or "(unknown)",
                "token_source": "config" if acc.token else f"${acc.token_env}",
                "teams": acc.teams,
                "markers": markers,
            }
        )

    if state.output == OutputFormat.JSON:
        print(_json.dumps(account_rows, indent=2))
        return

    # Human output
    if not account_rows:
        typer.echo("No accounts configured.")
        typer.echo("Run: hoja auth add <name> --token <token>")
        return

    typer.echo("Configured accounts:")
    for row in account_rows:
        marker_str = ""
        if row["markers"]:
            marker_str = " [" + ", ".join(row["markers"]) + "]"
        typer.echo(f"  • {row['name']}{marker_str}")
        if row["org_name"] != "(unknown)":
            typer.echo(f"    org: {row['org_name']}")
        typer.echo(f"    token: {row['token_source']}")
        if row["teams"]:
            typer.echo(f"    teams: {', '.join(row['teams'])}")
    if workspace:
        typer.echo(f"\nWorkspace: {workspace}")
    else:
        typer.echo("\nWorkspace: (none — not in a git repo)")


@auth_app.command("add")
def account_add(
    name: str = typer.Argument(..., help="Account name"),
    token: str = typer.Option(..., "--token", help="Linear API token"),
    token_env: str = typer.Option(
        "LINEAR_TOKEN", "--token-env", help="Environment variable name"
    ),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Verify token by calling Linear API"
    ),
    default: bool = typer.Option(
        False, "--default", help="Set as global default account"
    ),
    teams: Optional[str] = typer.Option(
        None,
        "--teams",
        help="Comma-separated team keys this account owns (e.g. 'SWA,ENG'). "
        "Enables automatic account selection when a command targets one of "
        "these teams.",
    ),
) -> None:
    """Add a new named account."""
    config = load_config()
    if name in config.accounts:
        raise UsageError(
            f"Account '{name}' already exists",
            hint="Use 'hoja auth remove {name}' first, or pick a different name.",
        )

    org_name: str | None = None
    if verify:
        try:
            org_name = asyncio.run(_fetch_org_name(token))
        except Exception as e:
            typer.echo(f"Warning: could not verify token ({e})", err=True)

    team_keys = (
        [t.strip().upper() for t in teams.split(",") if t.strip()] if teams else []
    )
    was_empty = len(config.accounts) == 0
    config.accounts[name] = AccountConfig(
        token=token if token_env == "LINEAR_TOKEN" else None,
        token_env=token_env,
        org_name=org_name,
        teams=team_keys,
    )
    if default or was_empty:
        config.defaults.default_account = name
    save_config(config)
    typer.echo(f"Added account '{name}'" + (f" ({org_name})" if org_name else ""))


async def _fetch_org_name(token: str) -> str | None:
    """Make a quick API call to get the organization name."""
    from hoja.graphql import queries
    from hoja.models.user import User

    client = LinearClient(token=token, timeout=10.0)
    async with client:
        viewer = await client.execute_as(User, queries.VIEWER, path=["viewer"], operation="Viewer")
        # viewer.organization may not exist on the User model; handle gracefully
        return getattr(viewer, "organization_name", None) or getattr(
            viewer, "organization", None
        )


@auth_app.command("switch")
def account_switch(
    name: str = typer.Argument(..., help="Account name to set as default"),
) -> None:
    """Set the global default account."""
    config = load_config()
    if name not in config.accounts:
        raise AuthError(
            f"Account '{name}' not found",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    config.defaults.default_account = name
    save_config(config)
    typer.echo(f"Default account set to '{name}'")


@auth_app.command("teams")
def account_teams(
    name: str = typer.Argument(..., help="Account name to edit"),
    teams: str = typer.Argument(
        ...,
        help="Comma-separated team keys this account owns (e.g. 'SWA,ENG'). "
        "Pass an empty string to clear.",
    ),
) -> None:
    """Set the team keys an account owns (for automatic account selection)."""
    config = load_config()
    if name not in config.accounts:
        raise AuthError(
            f"Account '{name}' not found",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    team_keys = [t.strip().upper() for t in teams.split(",") if t.strip()]
    config.accounts[name].teams = team_keys
    save_config(config)
    if team_keys:
        typer.echo(f"Account '{name}' now owns teams: {', '.join(team_keys)}")
    else:
        typer.echo(f"Cleared team ownership for account '{name}'")


@auth_app.command("remove")
def account_remove(
    name: str = typer.Argument(..., help="Account name to remove"),
) -> None:
    """Remove a named account."""
    config = load_config()
    if name not in config.accounts:
        raise AuthError(
            f"Account '{name}' not found",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    del config.accounts[name]
    # Clean up workspace mappings pointing to this account
    config.workspaces = {
        k: v for k, v in config.workspaces.items() if v != name
    }
    if config.defaults.default_account == name:
        config.defaults.default_account = next(iter(config.accounts), None)
    save_config(config)
    typer.echo(f"Removed account '{name}'")


@auth_app.command("workspace")
def auth_workspace() -> None:
    """Show current workspace detection and mapped account."""
    state = get_state()
    config = state.config
    workspace = resolve_workspace()

    result: dict[str, Any] = {
        "workspace": str(workspace) if workspace else None,
        "mapped_account": None,
        "default_account": config.defaults.default_account,
    }
    if workspace:
        mapped = config.workspaces.get(str(workspace))
        result["mapped_account"] = mapped

    if state.output == OutputFormat.JSON:
        print(_json.dumps(result, indent=2))
        return

    if workspace:
        typer.echo(f"Workspace: {workspace}")
        if mapped := result["mapped_account"]:
            typer.echo(f"Mapped account: {mapped}")
        else:
            typer.echo("No workspace mapping configured.")
            typer.echo(f"Default account: {config.defaults.default_account or '(none)'}")
    else:
        typer.echo("Not inside a git repository.")
        typer.echo(f"Default account: {config.defaults.default_account or '(none)'}")
