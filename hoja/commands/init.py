"""`hoja init` — scaffold the config file at the standard location."""

from __future__ import annotations

import os

import typer

from hoja.config import config_path

init_app = typer.Typer(help="Initialize hoja configuration")


CONFIG_TEMPLATE = """\
# hoja configuration file
# Location: __CONFIG_PATH__
#
# Token resolution order (per account):
#   1. --token CLI flag
#   2. $<ACCOUNT_TOKEN_ENV> environment variable (default: LINEAR_TOKEN)
#   3. [accounts.<name>].token below (discouraged: plaintext in dotfiles)
#
# Account resolution order:
#   1. --account CLI flag
#   2. Workspace-mapped account (git repo → account)
#   3. Global default account

[accounts.default]
# Environment variable to read the token from. Default: LINEAR_TOKEN.
token_env = "LINEAR_TOKEN"
# Uncomment to store the token here directly (not recommended):
# token = "lin_api_..."
# Team keys this account owns — enables automatic account selection when a
# command targets one of these teams (e.g. `hoja issue get SWA-20`).
# teams = ["SWA", "ENG"]

[defaults]
# Default team key — used when commands accept --team but you omit it.
# team = "ENG"
output = "human"
# editor = "$EDITOR"
# Global default account when not in a mapped workspace
default_account = "default"

[display]
color = true
table_max_width = 120
truncate_descriptions = 80

# Per-workspace account mappings (absolute repo path → account name).
# Detected automatically when you run `hoja auth workspace`.
# [workspaces]
# "/home/user/work/acme" = "work"
# "/home/user/oss/project" = "personal"

# Named filter aliases. Use with: hoja issue list --view <name>
# (Not yet wired up in v0.2 — reserved.)
[views]
# my-open-bugs = { assignee = "me", label = "Bug", "state.type" = "!completed" }
# team-blockers = { team = "ENG", priority = 1, "state.type" = "started" }
"""


@init_app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
    path: str | None = typer.Option(
        None, "--path", help="Write to this path instead of the default"
    ),
) -> None:
    """Create a starter config file at the standard location."""
    if ctx.invoked_subcommand is not None:
        return
    target = config_path() if path is None else _to_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        typer.echo(
            f"Config already exists at {target}. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(2)
    target.write_text(
        CONFIG_TEMPLATE.replace("__CONFIG_PATH__", str(target)),
        encoding="utf-8",
    )
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    typer.echo(f"Created {target}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"  1. export LINEAR_TOKEN=\"lin_api_...\"  # or edit {target}")
    typer.echo("  2. hoja me  # verify it works")
    typer.echo("")
    typer.echo("Multi-account setup:")
    typer.echo("  hoja auth add work --token $LINEAR_WORK_TOKEN")
    typer.echo("  hoja auth add personal --token $LINEAR_PERSONAL_TOKEN")
    typer.echo("  hoja auth switch work")


def _to_path(p: str):
    from pathlib import Path

    return Path(p).expanduser().resolve()
