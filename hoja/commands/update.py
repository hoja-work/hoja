"""`hoja update` — check PyPI and upgrade to the latest version."""

from __future__ import annotations

import json as _json
import shutil
import subprocess
import sys
import urllib.request
from typing import Any

import typer

from hoja import DISTRIBUTION_NAME, __version__
from hoja.cli_state import get_state
from hoja.errors import UsageError
from hoja.models.enums import OutputFormat

update_app = typer.Typer(help="Update hoja to the latest version")


PYPI_JSON_URL = f"https://pypi.org/pypi/{DISTRIBUTION_NAME}/json"


@update_app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without upgrading"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Check PyPI for a newer version and upgrade hoja."""
    if ctx.invoked_subcommand is not None:
        return

    state = get_state()
    current = __version__

    try:
        latest = _fetch_latest_version()
    except Exception as e:
        if state.output == OutputFormat.JSON:
            print(_json.dumps({"error": str(e), "current": current}, indent=2))
        else:
            typer.echo(f"Could not check for updates: {e}", err=True)
        raise typer.Exit(1)

    needs_update = _version_compare(current, latest) < 0

    manager = _detect_package_manager()
    if manager == "pipx":
        cmd = ["pipx", "upgrade", DISTRIBUTION_NAME]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", DISTRIBUTION_NAME]

    result: dict[str, Any] = {
        "current": current,
        "latest": latest,
        "needs_update": needs_update,
        "manager": manager,
        "command": " ".join(cmd),
    }

    if state.output == OutputFormat.JSON:
        print(_json.dumps(result, indent=2))
        if needs_update and not dry_run:
            _run_upgrade(cmd, yes=yes)
        return

    # Human output
    if not needs_update:
        typer.echo(f"hoja is up to date (v{current})")
        return

    typer.echo(f"Current: v{current}")
    typer.echo(f"Latest:  v{latest}")

    if dry_run:
        typer.echo(f"Would run: {' '.join(cmd)}")
        return

    if not yes:
        confirmed = typer.confirm("Upgrade now?")
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(0)

    _run_upgrade(cmd)


def _fetch_latest_version() -> str:
    """Query PyPI JSON API for the latest release version."""
    req = urllib.request.Request(
        PYPI_JSON_URL,
        headers={"Accept": "application/json", "User-Agent": "hoja/update"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        data = _json.load(resp)
    info = data.get("info", {})
    version = info.get("version")
    if not version:
        raise UsageError("Could not parse PyPI response", hint="Try again later.")
    return version


def _version_compare(a: str, b: str) -> int:
    """Compare two PEP 440 version strings. Returns <0 if a < b, 0 if equal, >0 if a > b."""
    # Simple tuple comparison for x.y.z (good enough for this CLI)
    def _parse(v: str) -> tuple[int, ...]:
        parts = v.split(".")
        result: list[int] = []
        for p in parts:
            # Strip any suffix like "a1", "b2", "rc1", "dev0"
            num = ""
            for ch in p:
                if ch.isdigit():
                    num += ch
                else:
                    break
            result.append(int(num) if num else 0)
        return tuple(result)

    pa = _parse(a)
    pb = _parse(b)
    for x, y in zip(pa, pb):
        if x != y:
            return x - y
    return len(pa) - len(pb)


def _detect_package_manager() -> str:
    """Detect whether hoja was installed via pipx or pip."""
    # Check if pipx is available and manages hoja
    pipx_path = shutil.which("pipx")
    if pipx_path:
        try:
            out = subprocess.run(
                [pipx_path, "list", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            data = _json.loads(out.stdout)
            venvs = data.get("venvs", {})
            # The current distribution is `hoja-cli`; `hoja` covers installs
            # made with the pre-release name.
            if DISTRIBUTION_NAME in venvs or "hoja" in venvs:
                return "pipx"
        except (subprocess.SubprocessError, _json.JSONDecodeError, FileNotFoundError):
            pass
    return "pip"


def _run_upgrade(cmd: list[str], *, yes: bool = True) -> None:
    """Execute the upgrade command."""
    try:
        subprocess.run(cmd, check=True)  # noqa: S603
    except subprocess.CalledProcessError as e:
        raise UsageError(
            f"Upgrade failed (exit {e.returncode})",
            hint="Try running the command manually: " + " ".join(cmd),
        ) from e
