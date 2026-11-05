"""`hoja memory` commands — project-scoped memory board for agents.

The memory board lives at `.hoja/memory.yaml` (project root). Agents read it
before starting work (`memory remind`) and contribute learnings (`memory add`).
Stale community entries auto-heal after 30 days.
"""

from __future__ import annotations

import json as _json
from typing import Any

import typer

from hoja.cli_state import get_state
from hoja.errors import NotFoundError
from hoja.memory_board import (
    MemoryBoard,
    MemoryEntry,
    heal,
    load_memory,
    remind,
    save_memory,
)
from hoja.models.enums import OutputFormat

memory_app = typer.Typer(help="Project memory board for hoja agents")


@memory_app.callback(invoke_without_command=True)
def memory(ctx: typer.Context) -> None:
    """Memory board — read before work, write after learning."""
    if ctx.invoked_subcommand is not None:
        return
    # Default action: remind
    _run_remind()


@memory_app.command("remind")
def memory_remind() -> None:
    """Print the memory board digest (what agents see before starting work)."""
    _run_remind()


def _run_remind() -> None:
    state = get_state()
    board = load_memory()
    digest = remind(board)
    if state.output == OutputFormat.JSON:
        data = {
            "forced": [_entry_dict(e) for e in board.forced],
            "community": [_entry_dict(e) for e in board.community if not _is_stale(e)],
        }
        print(_json.dumps(data, indent=2))
        return
    typer.echo(digest)


@memory_app.command("list")
def memory_list() -> None:
    """List all memory entries with metadata."""
    state = get_state()
    board = load_memory()
    if state.output == OutputFormat.JSON:
        data = {
            "forced": [_entry_dict(e) for e in board.forced],
            "community": [_entry_dict(e) for e in board.community],
        }
        print(_json.dumps(data, indent=2))
        return

    typer.echo("Memory board entries:")
    typer.echo("")
    if board.forced:
        typer.echo("Forced rules:")
        for e in sorted(board.forced, key=lambda x: x.priority):
            stale = " [stale]" if _is_stale(e) else ""
            typer.echo(f"  [{e.priority}] {e.id}{stale}")
            typer.echo(f"      {e.title}")
    if board.community:
        typer.echo("")
        typer.echo("Community entries:")
        for e in sorted(board.community, key=lambda x: x.priority):
            stale = " [stale]" if _is_stale(e) else ""
            typer.echo(f"  [{e.priority}] {e.id}{stale}")
            typer.echo(f"      {e.title}")
            typer.echo(f"      (added {e.created_at[:10]} by {e.created_by})")
    else:
        typer.echo("No community entries yet.")


@memory_app.command("add")
def memory_add(
    title: str = typer.Option(..., "--title", "-t", help="Short title for the memory"),
    body: str = typer.Option(..., "--body", "-b", help="Body text — what you learned"),
    priority: int = typer.Option(5, "--priority", "-p", help="Priority 1-10 (1 = most important)"),
    auto_heal: bool = typer.Option(True, "--auto-heal/--no-auto-heal", help="Auto-remove after heal_after_days"),
    heal_after_days: int = typer.Option(30, "--heal-after-days", help="Days before auto-removal"),
) -> None:
    """Add a community memory entry."""
    board = load_memory()
    entry_id = _slugify(title)
    # Handle duplicates by appending timestamp
    existing_ids = {e.id for e in board.forced + board.community}
    if entry_id in existing_ids:
        entry_id = f"{entry_id}-{__import__('time').time():.0f}"

    entry = MemoryEntry(
        id=entry_id,
        priority=priority,
        title=title,
        body=body,
        auto_heal=auto_heal,
        heal_after_days=heal_after_days,
    )
    board.community.append(entry)
    save_memory(board)
    typer.echo(f"Added memory '{entry_id}'")


@memory_app.command("update")
def memory_update(
    entry_id: str = typer.Argument(..., help="Entry ID to update"),
    body: str | None = typer.Option(None, "--body", "-b", help="New body text"),
    title: str | None = typer.Option(None, "--title", "-t", help="New title"),
    priority: int | None = typer.Option(None, "--priority", "-p", help="New priority"),
) -> None:
    """Update an existing memory entry."""
    board = load_memory()
    entry = _find_entry(board, entry_id)
    if entry is None:
        raise NotFoundError(
            f"Memory entry '{entry_id}' not found",
            hint="Run 'hoja memory list' to see available IDs.",
        )
    if title is not None:
        entry.title = title
    if body is not None:
        entry.body = body
    if priority is not None:
        entry.priority = priority
    entry.last_updated = __import__("hoja.memory_board", fromlist=["_now"])._now()
    save_memory(board)
    typer.echo(f"Updated memory '{entry_id}'")


@memory_app.command("remove")
def memory_remove(
    entry_id: str = typer.Argument(..., help="Entry ID to remove"),
) -> None:
    """Remove a memory entry."""
    board = load_memory()
    removed = _remove_entry(board, entry_id)
    if not removed:
        raise NotFoundError(
            f"Memory entry '{entry_id}' not found",
            hint="Run 'hoja memory list' to see available IDs.",
        )
    save_memory(board)
    typer.echo(f"Removed memory '{entry_id}'")


@memory_app.command("heal")
def memory_heal(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed without deleting"),
) -> None:
    """Remove stale community entries (older than heal_after_days)."""
    board = load_memory()
    removed = heal(board)
    if dry_run:
        if removed:
            typer.echo("Would remove:")
            for rid in removed:
                typer.echo(f"  - {rid}")
        else:
            typer.echo("Nothing to heal — all community entries are fresh.")
        return
    if removed:
        save_memory(board)
        typer.echo("Healed stale entries:")
        for rid in removed:
            typer.echo(f"  - {rid}")
    else:
        typer.echo("Nothing to heal — all community entries are fresh.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_dict(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "priority": entry.priority,
        "title": entry.title,
        "body": entry.body,
        "created_by": entry.created_by,
        "created_at": entry.created_at,
        "last_updated": entry.last_updated,
        "auto_heal": entry.auto_heal,
        "heal_after_days": entry.heal_after_days,
        "stale": _is_stale(entry),
    }


def _is_stale(entry: MemoryEntry) -> bool:
    """Check if an entry is stale (for display purposes only)."""
    if not entry.auto_heal or entry.heal_after_days <= 0:
        return False
    try:
        from datetime import datetime, timezone
        created = datetime.fromisoformat(entry.created_at)
        age = datetime.now(timezone.utc) - created
        return age.days > entry.heal_after_days
    except (ValueError, TypeError):
        return False


def _slugify(text: str) -> str:
    """Convert a title into a safe memory ID."""
    import re
    slug = re.sub(r"[^a-zA-Z0-9\s]", "", text).strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    return slug[:40] or "memory"


def _find_entry(board: MemoryBoard, entry_id: str) -> MemoryEntry | None:
    for e in board.forced + board.community:
        if e.id == entry_id:
            return e
    return None


def _remove_entry(board: MemoryBoard, entry_id: str) -> bool:
    for source in (board.forced, board.community):
        for i, e in enumerate(source):
            if e.id == entry_id:
                source.pop(i)
                return True
    return False
