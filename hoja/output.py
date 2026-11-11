"""Output formatters.

One canonical render() entry point dispatches on OutputFormat.
Each formatter takes a Pydantic model (or list thereof) and returns a string.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from hoja.models.enums import OutputFormat
from hoja.models.task import Issue
from hoja.models.project import Project
from hoja.models.team import Team
from hoja.models.user import User
from hoja.models.workflow import WorkflowState

console = Console()
err_console = Console(stderr=True)


def render(
    payload: Any,
    *,
    fmt: OutputFormat = OutputFormat.HUMAN,
    title: str | None = None,
) -> None:
    """Top-level dispatcher. Writes to stdout."""
    if fmt is OutputFormat.JSON:
        print(_to_json(payload))
    elif fmt is OutputFormat.IDS:
        print(_to_ids(payload))
    elif fmt is OutputFormat.PLAIN:
        print(_to_plain(payload))
    elif fmt is OutputFormat.YAML:
        print(_to_yaml(payload))
    elif fmt is OutputFormat.MARKDOWN:
        print(_to_markdown(payload, title=title))
    else:
        _to_human(payload, title=title)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _to_json(payload: Any) -> str:
    return json.dumps(_jsonify(payload), indent=2, default=str)


def _jsonify(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        # Drop null/empty fields to keep JSON tight and LLM-friendly
        return _prune(payload.model_dump(mode="json", by_alias=False))
    if isinstance(payload, list):
        return [_jsonify(item) for item in payload]
    if isinstance(payload, dict):
        return {k: _jsonify(v) for k, v in payload.items()}
    return payload


def _prune(d: Any) -> Any:
    """Recursively drop null, empty-string, empty-list, empty-dict fields."""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            pv = _prune(v)
            if pv is None or pv == "" or pv == [] or pv == {}:
                continue
            out[k] = pv
        return out
    if isinstance(d, list):
        return [_prune(item) for item in d]
    return d


# ---------------------------------------------------------------------------
# IDs (one per line)
# ---------------------------------------------------------------------------

def _to_ids(payload: Any) -> str:
    items = _to_list(payload)
    ids: list[str] = []
    for item in items:
        if hasattr(item, "identifier") and item.identifier:
            ids.append(item.identifier)
        elif hasattr(item, "id"):
            ids.append(item.id)
        elif isinstance(item, dict):
            ids.append(item.get("identifier") or item.get("id", ""))
    return "\n".join(i for i in ids if i)


# ---------------------------------------------------------------------------
# Plain (TSV)
# ---------------------------------------------------------------------------

def _to_plain(payload: Any) -> str:
    items = _to_list(payload)
    if not items:
        return ""
    rows: list[str] = []
    first = items[0]
    if isinstance(first, Issue):
        for it in items:
            rows.append(
                "\t".join(
                    [
                        it.identifier,
                        it.state_name,
                        it.assignee_name,
                        _safe_priority_label(it),
                        it.title,
                    ]
                )
            )
    elif isinstance(first, Team):
        for it in items:
            rows.append(f"{it.key}\t{it.name}\t{it.id}")
    elif isinstance(first, Project):
        for it in items:
            rows.append(f"{it.id}\t{it.name}\t{it.state or '-'}\t{int((it.progress or 0)*100)}%")
    elif isinstance(first, User):
        for it in items:
            rows.append(f"{it.id}\t{it.name}\t{it.email or '-'}")
    else:
        for it in items:
            rows.append(str(it))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# YAML (minimal, no external dep — we hand-roll a safe subset)
# ---------------------------------------------------------------------------

def _to_yaml(payload: Any) -> str:
    """Minimal YAML emitter. Good enough for inspecting the upstream API objects."""
    obj = _jsonify(payload)
    return _yaml_dump(obj, 0).rstrip()


def _yaml_dump(obj: Any, indent: int) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return f"{pad}{{}}\n"
        out = ""
        for k, v in obj.items():
            if isinstance(v, dict) and v:
                out += f"{pad}{k}:\n{_yaml_dump(v, indent + 1)}"
            elif isinstance(v, list) and v:
                out += f"{pad}{k}:\n{_yaml_dump(v, indent + 1)}"
            else:
                out += f"{pad}{k}: {_yaml_scalar(v)}\n"
        return out
    if isinstance(obj, list):
        if not obj:
            return f"{pad}[]\n"
        out = ""
        for item in obj:
            if isinstance(item, dict):
                inner = _yaml_dump(item, indent + 1).splitlines()
                if not inner:
                    out += f"{pad}- {{}}\n"
                    continue
                first = inner[0].lstrip()
                out += f"{pad}- {first}\n"
                for line in inner[1:]:
                    # add 2 spaces (for the '- ' lead-in) plus existing indent
                    out += f"{pad}  {line[(indent + 1) * 2:]}\n"
            elif isinstance(item, list):
                inner = _yaml_dump(item, indent + 1).splitlines()
                out += f"{pad}-\n"
                for line in inner:
                    out += f"{line}\n"
            else:
                out += f"{pad}- {_yaml_scalar(item)}\n"
        return out
    return f"{pad}{_yaml_scalar(obj)}\n"


def _yaml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in ":#\n\"'") or s.strip() != s:
        # Quote-and-escape
        return json.dumps(s)
    return s


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _to_markdown(payload: Any, *, title: str | None = None) -> str:
    items = _to_list(payload)
    out: list[str] = []
    if title:
        out.append(f"# {title}")
        out.append("")
    if not items:
        out.append("_No results._")
        return "\n".join(out)

    first = items[0]
    if isinstance(first, Issue):
        out.append("| ID | State | Priority | Assignee | Title |")
        out.append("|---|---|---|---|---|")
        for it in items:
            out.append(
                f"| {it.identifier} | {it.state_name} | "
                f"{_safe_priority_label(it)} | {it.assignee_name} | "
                f"{_md_escape(it.title)} |"
            )
    elif isinstance(first, Team):
        out.append("| Key | Name | ID |")
        out.append("|---|---|---|")
        for it in items:
            out.append(f"| {it.key} | {_md_escape(it.name)} | `{it.id}` |")
    elif isinstance(first, Project):
        out.append("| Name | State | Progress | Target Date |")
        out.append("|---|---|---|---|")
        for it in items:
            out.append(
                f"| {_md_escape(it.name)} | {it.state or '—'} | "
                f"{int((it.progress or 0) * 100)}% | "
                f"{it.target_date or '—'} |"
            )
    else:
        for it in items:
            out.append(f"- {it}")
    return "\n".join(out)


def _md_escape(s: str | None) -> str:
    if not s:
        return ""
    return s.replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------------------
# Human (Rich tables)
# ---------------------------------------------------------------------------

def _to_human(payload: Any, *, title: str | None = None) -> None:
    # Single object: render as key-value table
    if isinstance(payload, BaseModel):
        _render_object(payload, title=title)
        return
    items = _to_list(payload)
    if not items:
        console.print("[dim]No results.[/]")
        return
    first = items[0]
    if isinstance(first, Issue):
        _render_issues(items, title=title)
    elif isinstance(first, Team):
        _render_teams(items, title=title)
    elif isinstance(first, Project):
        _render_projects(items, title=title)
    elif isinstance(first, User):
        _render_users(items, title=title)
    elif isinstance(first, WorkflowState):
        _render_states(items, title=title)
    else:
        # Generic Pydantic list — show as a compact name/id table
        if isinstance(first, BaseModel):
            _render_generic(items, title=title)
        else:
            for item in items:
                console.print(item)


def _render_object(obj: BaseModel, *, title: str | None = None) -> None:
    table = Table(title=title or obj.__class__.__name__, show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    for k, v in obj.model_dump(mode="json", by_alias=False).items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, indent=2, default=str)
        table.add_row(str(k), str(v))
    console.print(table)


def _render_issues(items: list[Issue], *, title: str | None = None) -> None:
    table = Table(title=title or f"Issues ({len(items)})")
    table.add_column("ID", style="bold cyan")
    table.add_column("State")
    table.add_column("Pri")
    table.add_column("Assignee")
    table.add_column("Title")
    for it in items:
        table.add_row(
            it.identifier,
            it.state_name,
            _safe_priority_label(it),
            it.assignee_name,
            it.title,
        )
    console.print(table)


def _render_teams(items: list[Team], *, title: str | None = None) -> None:
    table = Table(title=title or f"Teams ({len(items)})")
    table.add_column("Key", style="bold cyan")
    table.add_column("Name")
    table.add_column("ID", style="dim")
    for it in items:
        table.add_row(it.key, it.name, it.id)
    console.print(table)


def _render_projects(items: list[Project], *, title: str | None = None) -> None:
    table = Table(title=title or f"Projects ({len(items)})")
    table.add_column("Name", style="bold cyan")
    table.add_column("State")
    table.add_column("Progress")
    table.add_column("Target")
    for it in items:
        table.add_row(
            it.name,
            str(it.state or "—"),
            f"{int((it.progress or 0) * 100)}%",
            str(it.target_date or "—"),
        )
    console.print(table)


def _render_users(items: list[User], *, title: str | None = None) -> None:
    table = Table(title=title or f"Users ({len(items)})")
    table.add_column("Name", style="bold cyan")
    table.add_column("Email")
    table.add_column("ID", style="dim")
    for it in items:
        table.add_row(it.short, it.email or "—", it.id)
    console.print(table)


def _render_states(items: list[WorkflowState], *, title: str | None = None) -> None:
    table = Table(title=title or f"Workflow States ({len(items)})")
    table.add_column("Name", style="bold cyan")
    table.add_column("Type")
    table.add_column("Position", justify="right")
    table.add_column("Color")
    table.add_column("ID", style="dim")
    for it in items:
        table.add_row(
            it.name,
            it.type.value if hasattr(it.type, "value") else str(it.type),
            f"{it.position:.0f}" if it.position is not None else "—",
            it.color or "—",
            it.id,
        )
    console.print(table)


def _render_generic(items: list, *, title: str | None = None) -> None:
    """Fallback table for any Pydantic model list — show id/name/key if present."""
    table = Table(title=title or f"Results ({len(items)})")
    sample = items[0].model_dump(mode="json", by_alias=False)
    # Pick the most useful 3-4 fields
    preferred = ["identifier", "key", "name", "title", "state", "id"]
    cols = [k for k in preferred if k in sample][:4]
    if not cols:
        cols = list(sample.keys())[:4]
    for c in cols:
        table.add_column(c, style="bold cyan" if c == cols[0] else "")
    for it in items:
        d = it.model_dump(mode="json", by_alias=False)
        table.add_row(*[str(d.get(c, "—") or "—") for c in cols])
    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_list(payload: Any) -> list:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes, dict, BaseModel)):
        return list(payload)
    return [payload]


def _safe_priority_label(issue: Issue) -> str:
    if issue.priority_label:
        return issue.priority_label
    return issue.priority.name.replace("_", " ").title() if issue.priority else "—"


def emit_error(message: str, *, hint: str | None = None) -> None:
    """Print an error to stderr in human mode."""
    err_console.print(f"[bold red]error:[/] {message}")
    if hint:
        err_console.print(f"[dim]hint:[/] {hint}")
