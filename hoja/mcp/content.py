"""Skill content loader + Pydantic model for the `hoja_guide` MCP tool.

Loads the canonical markdown files from `hoja/skill_content/*.md` (shipped
inside the wheel via `[tool.hatch.build.targets.wheel.force-include]`).
"""
from __future__ import annotations

from enum import Enum
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Topic enum — kebab-case in protocol, snake_case in Python
# ---------------------------------------------------------------------------
class Topic(str, Enum):
    """Skill topics. String values match the markdown filenames (no extension)."""

    OVERVIEW = "overview"
    COMMANDS = "commands"
    WORKFLOWS = "workflows"
    FILTERS = "filters"
    OUTPUT_FORMATS = "output-formats"
    EXAMPLES = "examples"


# ---------------------------------------------------------------------------
# Pydantic response model for the MCP tool
# ---------------------------------------------------------------------------
DEFAULT_REMINDER = (
    "Run hoja commands via the Bash tool. Do NOT write Python wrappers "
    "around the Linear GraphQL API — Hoja already handles auth, retries, "
    "pagination, validation, and error mapping. Resources on this MCP server "
    "give you read-only context for free; perform every mutation via "
    "`hoja <command>` in a shell."
)


class HojaGuide(BaseModel):
    """Structured response for the `hoja_guide` tool.

    The agent that calls this tool sees:
      - `instructions`: full markdown content for the requested topic
      - `key_commands`: a short hot-list of one-liners
      - `see_also`: other topics worth reading
      - `reminder`: the behavioral reinforcement string (shown on every reply)
    """

    topic: Topic = Field(description="Which topic this guide covers.")
    title: str = Field(description="Human-readable title for the topic.")
    instructions: str = Field(
        description="Markdown instructions teaching the agent how to use Hoja for this topic.",
    )
    key_commands: list[str] = Field(
        default_factory=list,
        description="One-liner shell commands the agent should know.",
    )
    see_also: list[Topic] = Field(
        default_factory=list,
        description="Other topics linked from this one.",
    )
    reminder: str = Field(
        default=DEFAULT_REMINDER,
        description="Behavioral reminder. Read it on every response.",
    )


# Deprecated public model alias for one compatibility release.


# ---------------------------------------------------------------------------
# Content loading
# ---------------------------------------------------------------------------
def _content_dir() -> Path:
    """Locate the bundled skill_content directory inside the installed package."""
    return Path(str(files("hoja").joinpath("skill_content")))


def _read_topic_file(topic: Topic) -> str:
    """Read the markdown file for a topic. Raises FileNotFoundError if missing."""
    path = _content_dir() / f"{topic.value}.md"
    return path.read_text(encoding="utf-8")


def _extract_title(markdown: str, fallback: str) -> str:
    """Pull the first `# heading` from the markdown body."""
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return fallback


def _extract_key_commands(markdown: str, limit: int = 10) -> list[str]:
    """Grab the first N `hoja ...` one-liners that look like bash commands.

    Heuristic: scan fenced ```bash blocks, collect lines starting with `hoja `.
    Skip comment lines and empty lines. Deduplicate while preserving order.
    """
    out: list[str] = []
    seen: set[str] = set()
    in_block = False
    is_bash = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if in_block:
                in_block = False
                is_bash = False
            else:
                in_block = True
                # Detect language tag (```bash, ```sh, ```shell) or untagged blocks
                tag = line.removeprefix("```").strip().lower()
                is_bash = tag in {"", "bash", "sh", "shell"}
            continue
        if not in_block or not is_bash:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("hoja ") and stripped not in seen:
            seen.add(stripped)
            out.append(stripped)
            if len(out) >= limit:
                return out
    return out


def _see_also_for(topic: Topic) -> list[Topic]:
    """Static cross-link map. Mirrors the 'See also' sections in the markdown."""
    base = [t for t in Topic if t is not topic]
    return base


def load_topic(topic: Topic) -> HojaGuide:
    """Load a topic's full guide.

    Returns a Pydantic-validated `HojaGuide` ready to be returned from the
    MCP tool. The MCP SDK will auto-serialize to JSON for the wire.
    """
    body = _read_topic_file(topic)
    title = _extract_title(body, fallback=f"Hoja — {topic.value}")
    return HojaGuide(
        topic=topic,
        title=title,
        instructions=body,
        key_commands=_extract_key_commands(body),
        see_also=_see_also_for(topic),
    )
