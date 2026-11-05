"""Memory board storage layer.

Persistent project-scoped memory for Hoja agents.
File location: .hoja/memory.yaml (with one-release legacy fallback).
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hoja.config import _find_git_root
from hoja.errors import UsageError

# Prefer PyYAML if available, otherwise fall back to tomli-w + our own YAML-ish serializer
try:
    import yaml as _yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]


@dataclass
class MemoryEntry:
    """A single memory entry on the board."""

    id: str
    priority: int = 5  # 1 = highest (forced), 10 = lowest (fleeting)
    title: str = ""
    body: str = ""
    created_by: str = "agent"
    created_at: str = field(default_factory=lambda: _now())
    last_updated: str = field(default_factory=lambda: _now())
    auto_heal: bool = True
    heal_after_days: int = 30


@dataclass
class MemoryBoard:
    """Top-level memory board container."""

    forced: list[MemoryEntry] = field(default_factory=list)
    community: list[MemoryEntry] = field(default_factory=list)
    version: str = "1"


def _now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def memory_path(start: Path | None = None) -> Path:
    """Resolve the canonical project memory board path."""
    root = _find_git_root(start) or Path.cwd()
    return root / ".hoja" / "memory.yaml"


def _default_forced_entries() -> list[MemoryEntry]:
    """Seed forced rules that every agent must see."""
    return [
        MemoryEntry(
            id="verify-auth-first",
            priority=1,
            title="Verify auth before any mutation",
            body="Always run `hoja me` first. If it fails, stop and surface the auth error to the user before any mutation.",
            created_by="system",
            auto_heal=False,
            heal_after_days=0,
        ),
        MemoryEntry(
            id="search-before-create",
            priority=2,
            title="Search before creating issues",
            body='Run `hoja issue search "<query>"` before `hoja issue create` to avoid duplicate issues.',
            created_by="system",
            auto_heal=False,
            heal_after_days=0,
        ),
        MemoryEntry(
            id="use-json-for-piping",
            priority=3,
            title="Use --output json for programmatic reading",
            body="`--output human` is for terminal display only. Use `-o json` when piping, filtering, or feeding into another tool.",
            created_by="system",
            auto_heal=False,
            heal_after_days=0,
        ),
        MemoryEntry(
            id="dry-run-on-ambiguous",
            priority=4,
            title="Use --dry-run on ambiguous mutations",
            body="When the user's intent is ambiguous or destructive, use `hoja <cmd> --dry-run` first to preview before executing.",
            created_by="system",
            auto_heal=False,
            heal_after_days=0,
        ),
        MemoryEntry(
            id="memory-remind-first",
            priority=5,
            title="Read the memory board before starting work",
            body="Run `hoja memory remind` first to load forced rules and recent project context. Add what you learn with `hoja memory add`.",
            created_by="system",
            auto_heal=False,
            heal_after_days=0,
        ),
    ]


def load_memory(path: Path | None = None) -> MemoryBoard:
    """Load the memory board from disk. Returns a seeded board if file missing."""
    p = path or memory_path()
    if not p.exists():
        # Seed with forced entries on first use
        return MemoryBoard(forced=_default_forced_entries())

    try:
        with open(p, encoding="utf-8") as f:
            raw = _yaml.safe_load(f) if _yaml else _parse_yaml_fallback(f.read())
    except Exception as e:
        raise UsageError(
            f"Cannot read memory board: {e}",
            hint=f"Fix or delete {p}",
        ) from e

    if raw is None:
        return MemoryBoard(forced=_default_forced_entries())

    return _deserialize_board(raw)


def save_memory(board: MemoryBoard, path: Path | None = None) -> None:
    """Persist the memory board to disk."""
    p = path or memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    data = _serialize_board(board)
    if _yaml:
        with open(p, "w", encoding="utf-8") as f:
            _yaml.dump(
                data,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
    else:
        with open(p, "w", encoding="utf-8") as f:
            f.write(_dump_yaml_fallback(data))
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)


def remind(board: MemoryBoard) -> str:
    """Generate a formatted digest of forced + recent community entries."""
    lines: list[str] = ["═══════════════════════════════════════════════════════════════"]
    lines.append("                     HOJA MEMORY BOARD")
    lines.append("═══════════════════════════════════════════════════════════════")
    lines.append("")

    if board.forced:
        lines.append("▶ FORCED RULES (always follow these)")
        for entry in sorted(board.forced, key=lambda e: e.priority):
            lines.append(f"  [{entry.priority}] {entry.title}")
            for para in entry.body.split("\n"):
                lines.append(f"      {para}")
        lines.append("")

    active_community = [e for e in board.community if not _is_healed(e)]
    if active_community:
        lines.append("▶ COMMUNITY CONTEXT (recent learnings)")
        for entry in sorted(active_community, key=lambda e: e.priority):
            lines.append(f"  [{entry.priority}] {entry.title}")
            for para in entry.body.split("\n"):
                lines.append(f"      {para}")
            lines.append(f"      (added {entry.created_at[:10]} by {entry.created_by})")
        lines.append("")
    else:
        lines.append("▶ No community entries yet.")
        lines.append("  Add what you learn: hoja memory add --title \"...\" --body \"...\"")
        lines.append("")

    lines.append("═══════════════════════════════════════════════════════════════")
    return "\n".join(lines)


def heal(board: MemoryBoard) -> list[str]:
    """Remove stale community entries. Returns list of removed IDs."""
    removed: list[str] = []
    kept: list[MemoryEntry] = []
    for entry in board.community:
        if _is_healed(entry):
            removed.append(entry.id)
        else:
            kept.append(entry)
    board.community = kept
    return removed


def _is_healed(entry: MemoryEntry) -> bool:
    """Determine if a community entry is stale and should be removed."""
    if not entry.auto_heal:
        return False
    if entry.heal_after_days <= 0:
        return False
    try:
        created = datetime.fromisoformat(entry.created_at)
    except (ValueError, TypeError):
        return False
    age = datetime.now(timezone.utc) - created
    return age.days > entry.heal_after_days


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _deserialize_board(raw: dict[str, Any]) -> MemoryBoard:
    """Convert raw dict to MemoryBoard."""
    forced = [_deserialize_entry(e) for e in raw.get("forced", [])]
    community = [_deserialize_entry(e) for e in raw.get("community", [])]
    return MemoryBoard(forced=forced, community=community, version=raw.get("version", "1"))


def _deserialize_entry(raw: dict[str, Any]) -> MemoryEntry:
    """Convert raw dict to MemoryEntry."""
    return MemoryEntry(
        id=raw.get("id", ""),
        priority=raw.get("priority", 5),
        title=raw.get("title", ""),
        body=raw.get("body", ""),
        created_by=raw.get("created_by", "agent"),
        created_at=raw.get("created_at", _now()),
        last_updated=raw.get("last_updated", _now()),
        auto_heal=raw.get("auto_heal", True),
        heal_after_days=raw.get("heal_after_days", 30),
    )


def _serialize_board(board: MemoryBoard) -> dict[str, Any]:
    """Convert MemoryBoard to plain dict for YAML writing."""
    return {
        "version": board.version,
        "forced": [_serialize_entry(e) for e in board.forced],
        "community": [_serialize_entry(e) for e in board.community],
    }


def _serialize_entry(entry: MemoryEntry) -> dict[str, Any]:
    """Convert MemoryEntry to plain dict."""
    d = asdict(entry)
    # Drop default fields to keep YAML concise
    if entry.auto_heal is False:
        d["auto_heal"] = False
    else:
        d.pop("auto_heal", None)
    if entry.heal_after_days == 0:
        d["heal_after_days"] = 0
    else:
        d.pop("heal_after_days", None)
    return d


# ---------------------------------------------------------------------------
# Fallback YAML-ish parser when PyYAML is not installed
# ---------------------------------------------------------------------------

def _parse_yaml_fallback(text: str) -> dict[str, Any]:
    """Minimal YAML parser for our constrained schema.

    Only supports the structure we produce:
    - Top-level keys
    - Lists of dicts under 'forced' and 'community'
    - Scalar string/int/bool values
    """
    result: dict[str, Any] = {"forced": [], "community": []}
    current_list: list[dict[str, Any]] | None = None
    current_item: dict[str, Any] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        if line.strip() == "---":
            continue

        # Top-level key (no leading spaces)
        if not line.startswith(" ") and line.endswith(":"):
            key = line[:-1].strip()
            current_list = (
                result.setdefault(key, [])  # type: ignore[assignment]
                if key in ("forced", "community")
                else None
            )
            current_item = {}
            continue

        # List item start (- id: ...)
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_item and current_list is not None:
                current_list.append(current_item)
            current_item = {}
            rest = stripped[2:]
            if ":" in rest:
                k, v = rest.split(":", 1)
                current_item[k.strip()] = _parse_scalar(v.strip())
            continue

        # Nested key under current item
        if current_item is not None and ":" in line:
            indent = len(line) - len(line.lstrip())
            if indent >= 2:
                k, v = line.split(":", 1)
                current_item[k.strip()] = _parse_scalar(v.strip())

    if current_item and current_list is not None:
        current_list.append(current_item)

    return result


def _parse_scalar(v: str) -> str | int | bool:
    """Parse a simple YAML scalar."""
    v = v.strip().strip('"').strip("'")
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
        return int(v)
    return v


def _dump_yaml_fallback(data: dict[str, Any]) -> str:
    """Minimal YAML serializer for our constrained schema."""
    lines: list[str] = ["---"]

    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        if first:
                            lines.append(f"  - {k}: {_fmt_scalar(v)}")
                            first = False
                        else:
                            lines.append(f"    {k}: {_fmt_scalar(v)}")
                else:
                    lines.append(f"  - {_fmt_scalar(item)}")
        else:
            lines.append(f"{key}: {_fmt_scalar(value)}")
    lines.append("")
    return "\n".join(lines)


def _fmt_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        if "\n" in v or ":" in v or v.startswith(" "):
            return repr(v)
        return v
    return str(v)
