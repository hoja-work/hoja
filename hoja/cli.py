"""Root CLI entrypoint.

Defines the top-level Typer app, global flags, error handling, and
registers all subcommand modules.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer

from hoja import __version__
from hoja.cli_state import CLIState, set_state
from hoja.commands.auth_cmd import auth_app, me_app
from hoja.commands.comment import comment_app
from hoja.commands.cycle import cycle_app
from hoja.commands.init import init_app
from hoja.commands.issue import issue_app
from hoja.commands.label import label_app
from hoja.commands.memory import memory_app
from hoja.commands.project import project_app
from hoja.commands.raw import raw_app
from hoja.commands.team import team_app
from hoja.commands.update import update_app
from hoja.config import load_config, resolve_account, resolve_base_url, resolve_token
from hoja.errors import HojaError
from hoja.models.enums import OutputFormat
from hoja.output import emit_error

app = typer.Typer(
    name="hoja",
    help="Agent-native work management for Hoja and Linear-compatible services",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(f"hoja {__version__}")
        raise typer.Exit()


def _team_key_hint_from_argv(argv: list[str]) -> str | None:
    """Best-effort scan of argv for a team key, used for account selection.

    The root callback runs before subcommands parse their own options, so we
    sniff argv for either a ``--team/-t <value>`` option or a bare issue
    identifier like ``SWA-20``. This is purely a hint — it only influences
    which account (token) is chosen, never what the subcommand does.
    """
    for i, arg in enumerate(argv):
        if arg in ("--team", "-t") and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--team="):
            return arg.split("=", 1)[1]
    # Fall back to the first issue-identifier-looking positional (KEY-123).
    for arg in argv:
        if arg.startswith("-"):
            continue
        head, sep, tail = arg.partition("-")
        if sep and head.isalpha() and tail.isdigit():
            return arg
    return None


@app.callback()
def main(
    ctx: typer.Context,
    token: Optional[str] = typer.Option(None, "--token", envvar=None, help="Linear API token (overrides $LINEAR_TOKEN)"),
    account: Optional[str] = typer.Option(None, "--account", "-a", envvar=None, help="Account name to use (overrides workspace/default)"),
    output: OutputFormat = typer.Option(OutputFormat.HUMAN, "--output", "-o", help="Output format"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print GraphQL operations to stderr"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-essential output"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print mutations without executing"),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds"),
    version: Optional[bool] = typer.Option(
        None, "--version", callback=_version_cb, is_eager=True, help="Show version and exit"
    ),
) -> None:
    """Global flags applied before any subcommand."""
    # Resolve config + account + token
    config = load_config()

    # Account resolution: only required for commands that hit the API.
    # We defer the actual AuthError to first API call to allow `--help` etc.
    try:
        team_hint = _team_key_hint_from_argv(sys.argv[1:])
        account_name, account_cfg = resolve_account(
            account, config, team_key=team_hint
        )
        resolved_token = resolve_token(token, account_cfg)
        resolved_base_url = resolve_base_url(None, account_cfg)
    except HojaError:
        account_name = ""
        resolved_token = ""
        resolved_base_url = None

    state = CLIState(
        token=resolved_token,
        config=config,
        output=output,
        verbose=verbose,
        quiet=quiet,
        no_color=no_color,
        dry_run=dry_run,
        timeout=timeout,
        account_name=account_name,
        base_url=resolved_base_url,
    )
    set_state(state)


# Register subcommands
app.add_typer(me_app, name="me")
app.add_typer(auth_app, name="auth")
app.add_typer(init_app, name="init")
app.add_typer(team_app, name="team")
app.add_typer(issue_app, name="issue")
app.add_typer(project_app, name="project")
app.add_typer(cycle_app, name="cycle")
app.add_typer(comment_app, name="comment")
app.add_typer(label_app, name="label")
app.add_typer(memory_app, name="memory")
app.add_typer(raw_app, name="raw")
app.add_typer(update_app, name="update")


def _entry() -> None:
    """Wrap typer_app() with consistent error handling and exit codes."""
    try:
        typer_app()
    except HojaError as e:
        from hoja.cli_state import get_state
        state = get_state()
        if state.output == OutputFormat.JSON:
            import json as _json
            print(_json.dumps(e.to_dict(), indent=2), file=sys.stderr)
        else:
            emit_error(e.message, hint=e.hint)
        sys.exit(int(e.exit_code))
    except KeyboardInterrupt:
        emit_error("Interrupted")
        sys.exit(130)


# Public callable for `hoja` script entry point in pyproject.toml.
# Renamed to `typer_app` internally so the wrapper is what pip-installed
# `hoja` actually invokes.
typer_app = app
app = _entry  # type: ignore[assignment]


if __name__ == "__main__":
    _entry()
