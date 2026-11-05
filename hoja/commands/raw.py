"""`hoja raw query ...` — escape hatch for arbitrary GraphQL."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer

from hoja.cli_state import build_client, get_state

raw_app = typer.Typer(help="Escape hatch: arbitrary GraphQL")


@raw_app.command("query")
def raw_query(
    query: Optional[str] = typer.Argument(None, help="GraphQL query string. If omitted, reads from stdin."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read query from file"),
    variables: Optional[str] = typer.Option(None, "--variables", help="JSON variables"),
    variables_file: Optional[Path] = typer.Option(None, "--variables-file", help="Read variables from JSON file"),
) -> None:
    """Execute an arbitrary GraphQL query/mutation."""
    if file:
        q = file.read_text()
    elif query:
        q = query
    else:
        q = sys.stdin.read()

    if not q.strip():
        typer.echo("error: empty query", err=True)
        raise typer.Exit(2)

    v: dict | None = None
    if variables_file:
        v = json.loads(variables_file.read_text())
    elif variables:
        v = json.loads(variables)

    asyncio.run(_run(q, v))


async def _run(query: str, variables: dict | None) -> None:
    state = get_state()
    async with build_client(state) as client:
        data = await client.execute(query, variables)
        print(json.dumps(data, indent=2, default=str))
