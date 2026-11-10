"""Read-only the upstream API resources exposed via MCP.

Each resource handler:
  1. Resolves auth (GraphClient via env / config).
  2. Issues a GraphQL query.
  3. Validates response into a Pydantic model.
  4. Returns JSON text (string).

If auth is missing, the handler raises `RuntimeError` with a friendly message;
the FastMCP runtime maps that to a JSON-RPC error so the server keeps running.

All resources are **read-only**. Mutations live in the `hoja` CLI — the
agent is expected to call those via the Bash tool. The `reminder` field on
the tool response and the prompt bodies reinforce this rule.
"""
from __future__ import annotations

import json
from typing import Any

from hoja.client import GraphClient
from hoja.config import load_config, resolve_account, resolve_token
from hoja.errors import AuthError, HojaError
from hoja.graphql import queries
from hoja.models.cycle import Cycle
from hoja.models.issue import Issue
from hoja.models.project import Project
from hoja.models.team import Team
from hoja.models.user import User
from hoja.models.workflow import WorkflowState


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _client() -> GraphClient:
    """Build a GraphClient using the same resolution rules as the CLI.

    Order: $HOJA_TOKEN env var (deprecated alias $LINEAR_TOKEN) > config.toml.
    The `--token` CLI flag is not applicable here (the MCP server has no CLI
    args for the token).
    """
    cfg = load_config()
    try:
        _account_name, account_cfg = resolve_account(None, cfg)
        token = resolve_token(None, account_cfg)
    except AuthError as e:
        raise RuntimeError(
            "hoja-mcp could not resolve an API token. "
            "Set $HOJA_TOKEN, or run `hoja init` and put your token in "
            "~/.config/hoja/config.toml. "
            f"Underlying error: {e}"
        ) from e
    return GraphClient(token)


def _to_json(model: Any) -> str:
    """Serialize a Pydantic model (or list of them) to indented JSON."""
    if isinstance(model, list):
        return json.dumps(
            [m.model_dump(mode="json", exclude_none=True) for m in model],
            indent=2,
            ensure_ascii=False,
        )
    if hasattr(model, "model_dump"):
        return model.model_dump_json(indent=2, exclude_none=True)
    return json.dumps(model, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Resource handlers
# ---------------------------------------------------------------------------
async def viewer() -> str:
    """hoja://me — currently-authenticated user."""
    async with _client() as c:
        user = await c.execute_as(
            User, queries.VIEWER, path=["viewer"], operation="Viewer"
        )
    return _to_json(user)


async def issue(id: str) -> str:
    """hoja://issue/{id} — full Issue including comments, labels, subscribers."""
    async with _client() as c:
        issue_model = await c.execute_as(
            Issue,
            queries.ISSUE_BY_IDENTIFIER,
            {"id": id},
            path=["issue"],
            operation="IssueByIdentifier",
        )
    return _to_json(issue_model)


async def team(key: str) -> str:
    """hoja://team/{key} — team + states + members rolled into one payload."""
    async with _client() as c:
        # Resolve team id from key
        team_data = await c.execute(
            queries.TEAM_BY_KEY, {"key": key}, operation="TeamByKey"
        )
        nodes = (team_data.get("teams") or {}).get("nodes") or []
        if not nodes:
            raise RuntimeError(f"No team found with key {key!r}")
        team_model = Team.model_validate(nodes[0])

        # States
        states_raw = await c.execute(
            queries.TEAM_STATES, {"teamId": team_model.id}, operation="TeamStates"
        )
        state_nodes = (
            (states_raw.get("team") or {}).get("states") or {}
        ).get("nodes") or []
        states = [WorkflowState.model_validate(s) for s in state_nodes]

        # Members
        members_raw = await c.execute(
            queries.TEAM_MEMBERS, {"teamId": team_model.id}, operation="TeamMembers"
        )
        member_nodes = (
            (members_raw.get("team") or {}).get("members") or {}
        ).get("nodes") or []
        members = [User.model_validate(m) for m in member_nodes]

    payload = {
        "team": team_model.model_dump(mode="json", exclude_none=True),
        "states": [s.model_dump(mode="json", exclude_none=True) for s in states],
        "members": [m.model_dump(mode="json", exclude_none=True) for m in members],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


async def project(id_or_slug: str) -> str:
    """hoja://project/{id_or_slug} — Project with lead + members."""
    async with _client() as c:
        proj = await c.execute_as(
            Project,
            queries.PROJECT_BY_ID,
            {"id": id_or_slug},
            path=["project"],
            operation="Project",
        )
    return _to_json(proj)


async def cycle_current(team_key: str) -> str:
    """hoja://cycle/current/{team_key} — active cycle or {"active_cycle": null}."""
    async with _client() as c:
        # Resolve team id from key
        team_data = await c.execute(
            queries.TEAM_BY_KEY, {"key": team_key}, operation="TeamByKey"
        )
        nodes = (team_data.get("teams") or {}).get("nodes") or []
        if not nodes:
            raise RuntimeError(f"No team found with key {team_key!r}")
        team_id = nodes[0]["id"]

        # Active cycle
        cycle_query = """
        query ActiveCycle($teamId: String!) {
          team(id: $teamId) {
            activeCycle { id name number startsAt endsAt completedAt progress }
          }
        }
        """
        raw = await c.execute(cycle_query, {"teamId": team_id}, operation="ActiveCycle")
        active = (raw.get("team") or {}).get("activeCycle")
    if not active:
        return json.dumps({"active_cycle": None, "team_key": team_key}, indent=2)
    cycle_model = Cycle.model_validate(active)
    return _to_json(cycle_model)


async def issues_mine() -> str:
    """hoja://issues/mine — viewer's open issues (Todo + In Progress + In Review)."""
    async with _client() as c:
        viewer_data = await c.execute(queries.VIEWER, operation="Viewer")
        viewer_id = (viewer_data.get("viewer") or {}).get("id")
        if not viewer_id:
            raise RuntimeError("Could not resolve viewer id")

        filt = {
            "assignee": {"id": {"eq": viewer_id}},
            "state": {"type": {"in": ["unstarted", "started"]}},
        }
        issues = await c.execute_list(
            Issue,
            queries.ISSUES_LIST,
            {"filter": filt, "first": 50},
            path=["issues"],
            operation="Issues",
        )
    return _to_json(issues)


async def issues_team(team_key: str) -> str:
    """hoja://issues/team/{team_key} — open issues for a team."""
    async with _client() as c:
        filt = {
            "team": {"key": {"eq": team_key}},
            "state": {"type": {"in": ["unstarted", "started"]}},
        }
        try:
            issues = await c.execute_list(
                Issue,
                queries.ISSUES_LIST,
                {"filter": filt, "first": 50},
                path=["issues"],
                operation="Issues",
            )
        except HojaError as e:
            raise RuntimeError(f"Could not list issues for team {team_key!r}: {e}") from e
    return _to_json(issues)
