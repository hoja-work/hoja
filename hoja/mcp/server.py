"""hoja-mcp — Model Context Protocol server for the hoja CLI.

Run via the `hoja-mcp` console script (registered in pyproject.toml).
Uses the official `mcp` Python SDK with FastMCP convenience layer.

Exposes:
  - 1 tool      : hoja_guide(topic) — teaches the agent how to use hoja
  - 7 resources : read-only Linear context
  - 6 prompts   : workflow templates

By design the server does NOT expose mutation tools. Mutations happen via the
`hoja` CLI in a shell — the tool's response payload and the prompt bodies
both carry a behavioral reminder reinforcing this rule.

STDIO TRANSPORT NOTE: When running on stdio (the default), stdout is reserved
for the JSON-RPC stream. ALL logging must go to stderr. We configure that at
the top of this module before any other import that might log.
"""

from __future__ import annotations

import logging
import sys

# Route logging to stderr BEFORE importing anything that might log.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[hoja-mcp] %(levelname)s %(message)s",
)

# Module-level imports for types used in tool/resource/prompt signatures.
# The MCP SDK uses inspect.signature(..., eval_str=True) which resolves
# stringified annotations against MODULE globals — so Topic/HojaGuide
# must be importable at module level, not just inside _build_server().
from . import prompts as _prompts  # noqa: E402
from . import resources as _resources  # noqa: E402
from .content import HojaGuide, Topic, load_topic  # noqa: E402


def main() -> None:
    """Entry point for the `hoja-mcp` console script.

    Lazy-imports the MCP SDK so that `import hoja` (without the [mcp] extra)
    does not fail at the top of this module. If the dep is missing, print a
    friendly install hint to stderr and exit 2.
    """
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "hoja-mcp requires the [mcp] extra.\nInstall with:\n  pip install 'hoja-cli[mcp]'\n"
        )
        sys.exit(2)

    _build_server().run()


def _build_server():
    """Wire tools, resources, and prompts onto an MCP v2 server."""
    from mcp.server.mcpserver import MCPServer

    from hoja import __version__

    mcp = MCPServer(
        name="hoja",
        instructions=(
            "Use this server to learn how to drive Linear from a shell using "
            "the `hoja` CLI. Call `hoja_guide(topic)` BEFORE attempting "
            "any Linear-related task. Read resources for read-only context. "
            "Read prompts for multi-step workflow templates. Perform all "
            "mutations via `hoja <command>` invoked through the Bash tool."
        ),
    )

    # ------------------------------------------------------------------
    # Tool — single teaching tool
    # ------------------------------------------------------------------
    @mcp.tool()
    def hoja_guide(topic: Topic = Topic.OVERVIEW) -> HojaGuide:
        """Return a guide on how to use the `hoja` CLI for a given topic.

        ALWAYS call this BEFORE any Linear-related task. Then execute hoja
        commands via the Bash tool. Do NOT write Python wrappers around the
        Linear GraphQL API — hoja is the supported interface.

        Available topics:
          - overview         — when to reach for hoja; behavioral rules
          - commands         — full command tree with examples
          - workflows        — common multi-step patterns
          - filters          — `issue list` filter DSL
          - output-formats   — when to use --output json vs human vs ids
          - examples         — copy-pasteable recipes
        """
        return load_topic(topic)

    # ------------------------------------------------------------------
    # Resources — read-only Linear context
    # ------------------------------------------------------------------
    @mcp.resource("hoja://me", name="viewer", description="Currently-authenticated Linear user.")
    async def res_me() -> str:
        return await _resources.viewer()

    @mcp.resource(
        "hoja://issue/{id}",
        name="issue",
        description="Full Linear issue by identifier (e.g. ENG-35), including comments, labels, subscribers.",
    )
    async def res_issue(id: str) -> str:
        return await _resources.issue(id)

    @mcp.resource(
        "hoja://team/{key}",
        name="team",
        description="Team detail + workflow states + members for a team key (e.g. ENG).",
    )
    async def res_team(key: str) -> str:
        return await _resources.team(key)

    @mcp.resource(
        "hoja://project/{id_or_slug}",
        name="project",
        description="Linear project by id or slug, including lead, creator, members.",
    )
    async def res_project(id_or_slug: str) -> str:
        return await _resources.project(id_or_slug)

    @mcp.resource(
        "hoja://cycle/current/{team_key}",
        name="cycle-current",
        description="Active cycle for a team. Returns {active_cycle: null} when none.",
    )
    async def res_cycle(team_key: str) -> str:
        return await _resources.cycle_current(team_key)

    @mcp.resource(
        "hoja://issues/mine",
        name="issues-mine",
        description="Open issues assigned to the viewer (Todo + In Progress).",
    )
    async def res_issues_mine() -> str:
        return await _resources.issues_mine()

    @mcp.resource(
        "hoja://issues/team/{team_key}",
        name="issues-team",
        description="Open issues for a team (Todo + In Progress).",
    )
    async def res_issues_team(team_key: str) -> str:
        return await _resources.issues_team(team_key)

    # ------------------------------------------------------------------
    # Prompts — workflow templates
    # ------------------------------------------------------------------
    @mcp.prompt(description="Triage unassigned issues for a team — step-by-step shell commands.")
    def triage(team_key: str) -> str:
        return _prompts.triage(team_key)

    @mcp.prompt(description="Generate a daily standup report from viewer's recent activity.")
    def daily_standup() -> str:
        return _prompts.daily_standup()

    @mcp.prompt(description="Create an issue from an error trace, with duplicate-search step.")
    def create_from_error(error_text: str, team_key: str, priority: int = 3) -> str:
        return _prompts.create_from_error(error_text, team_key, priority)

    @mcp.prompt(description="Hand off an issue to another teammate in 3 commands.")
    def hand_off(issue_id: str, to_user: str, note: str = "") -> str:
        return _prompts.hand_off(issue_id, to_user, note)

    @mcp.prompt(
        description="Review the current cycle for a team — done/in-progress/blockers/stale."
    )
    def cycle_review(team_key: str) -> str:
        return _prompts.cycle_review(team_key)

    @mcp.prompt(description="Deep-dive investigation of a single issue.")
    def issue_investigate(issue_id: str) -> str:
        return _prompts.issue_investigate(issue_id)

    logging.info(
        "hoja-mcp v%s ready (tools=1, resources=7, prompts=6)",
        __version__,
    )
    return mcp


if __name__ == "__main__":
    main()
