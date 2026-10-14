"""Streams agent replies to the Hoja chat screen as OpenAI-compatible SSE."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai
from ariadne import graphql
from fastmcp.client.transports import StreamableHttpTransport
from google_workspace.mcp_server import PRINCIPAL_HEADER
from graphql import GraphQLError, OperationDefinitionNode, OperationType, parse
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelRequest, ModelResponse, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import and_, select
from starlette.responses import StreamingResponse

from cliniar_server.agent_conversations import save_message, start_conversation
from cliniar_server.agent_prompt import compose_prompt, workspace_snapshot
from cliniar_server.agent_threads import _owned as _owned_thread
from cliniar_server.agent_threads import _title_from
from cliniar_server.db import model_provider
from cliniar_server.denials import DENY_HINT
from cliniar_server.model_providers import validate_model_base_url
from cliniar_server.secrets_box import decrypt_secret

INSTRUCTIONS = """You are the Hoja workspace agent for Clover. Hoja is a Linear-compatible issue tracker.

Entities: teams (keys like TREBOL), projects, issues (identifiers like TREBOL-12), cycles, and users.

How to work:
- Use the `schema` tool once per conversation before writing a mutation you have not used before, so you use real field names.
- Use the `graphql` tool to read and change data. Always pass a non-empty `query` string.
- Before creating anything, search for an existing issue or project with the same title to avoid duplicates.
- When creating a task, set title, teamId, and projectId when the user names a project. Add a short description with acceptance criteria when the request is vague.
- Assign people only after confirming they are members of the team.
- To filter to the signed-in user, first run `{ viewer { id } }`, then use the returned id, e.g. `issues(filter: { assignee: { id: { eq: "<id>" } } })`. `UserFilter` has only id, email, displayName, and name; there is no `isMe` field.
- After a write, read it back to confirm it exists and is linked to the right project.

How to answer:
- Reply in Markdown. Use short paragraphs, bullet lists for multiple items, and fenced code blocks for queries or IDs.
- Reference every issue as a link in the form [TREBOL-12](/workspace/issue/TREBOL-12/slug-of-title), where slug-of-title is the lowercase title with non-alphanumerics replaced by hyphens. Hoja's UI routes issues there.
- Be concise. Lead with the result, then list what changed.
- If a request is ambiguous (which team, which project, which person), ask one focused question instead of guessing.
- Never reveal tokens, secrets, or raw authorization headers."""


class AgentDeps:
    def __init__(self, schema, context: dict):
        self.schema = schema
        self.context = context
        self.snapshot: str | None = None


ASK_USER_QUESTION_LIMIT = 10
ASK_USER_TEXT_LIMIT = 250
ASK_USER_CONTEXT_LIMIT = 2000
ASK_USER_OPTION_LIMIT = 10
ASK_USER_OPTION_TEXT_LIMIT = 200
ASK_USER_FIND = "query($teamId: ID!, $marker: String!) { issues(first: 1, filter: { team: { id: { eq: $teamId } }, description: { contains: $marker } }) { nodes { identifier } } }"
ASK_USER_CREATE = "mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { identifier } } }"
ASK_USER_TEAMS = "{ teams(first: 100) { nodes { id key } } }"
log = logging.getLogger(__name__)


def _repair_json(raw: str):
    """Parse JSON the model wrote almost correctly: code fences, single quotes, trailing commas."""
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw.strip())
    no_trailing = re.sub(r",\s*([\]}])", r"\1", text)
    for candidate in (text, no_trailing):
        try:
            return json.loads(candidate)
        except (ValueError, RecursionError):
            pass
    try:
        return ast.literal_eval(no_trailing)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        raise ValueError("questions must be a JSON list of questions") from None


def _option_text(option) -> str:
    if isinstance(option, dict):
        label = option.get("label") or option.get("text") or option.get("value")
        detail = option.get("description")
        if isinstance(label, str) and label.strip():
            return f"{label.strip()} ({detail.strip()})" if isinstance(detail, str) and detail.strip() else label.strip()
        return ""
    if isinstance(option, (str, int, float)) and not isinstance(option, bool):
        return str(option).strip()
    return ""


def _normalize_question(raw) -> tuple[dict | None, str | None]:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None, "each question must be a string or an object with text"
    text = next((raw[key] for key in ("text", "question", "title") if isinstance(raw.get(key), str)), "")
    text = " ".join(text.split())[:ASK_USER_TEXT_LIMIT]
    if not text:
        return None, "question text is required"
    options = raw.get("options") or []
    if isinstance(options, str):
        options = [options]
    if not isinstance(options, list):
        return None, "options must be a list of strings"
    options = [_option_text(o) for o in options]
    options = [o[:ASK_USER_OPTION_TEXT_LIMIT] for o in options if o]
    context = raw.get("context") if isinstance(raw.get("context"), str) else ""
    return {"text": text, "options": options[:ASK_USER_OPTION_LIMIT], "context": context[:ASK_USER_CONTEXT_LIMIT]}, None


async def _resolve_team_id(ctx, team: str) -> tuple[str | None, str | None]:
    _ok, result = await graphql_run(ctx.deps.schema, ASK_USER_TEAMS, {}, ctx.deps.context)
    if result.get("errors"):
        return None, result["errors"][0]["message"]
    for node in ((result.get("data") or {}).get("teams") or {}).get("nodes", []):
        if team == node["id"] or team.lower() == node["key"].lower():
            return node["id"], None
    return None, f"no team matches '{team}'"


async def _ask_one(ctx, team_id: str, question: dict) -> dict:
    text = question["text"]
    marker = "ask-user-key: " + hashlib.sha256(f"{team_id}\n{text}".encode()).hexdigest()[:16]
    _ok, found = await graphql_run(ctx.deps.schema, ASK_USER_FIND, {"teamId": team_id, "marker": marker}, ctx.deps.context)
    if found.get("errors"):
        return {"status": "failed", "text": text, "error": found["errors"][0]["message"]}
    existing = (((found.get("data") or {}).get("issues") or {}).get("nodes")) or []
    if existing:
        return {"status": "skipped", "text": text, "identifier": existing[0]["identifier"]}
    description = "\n".join(filter(None, [question["context"], *(f"- {o}" for o in question["options"]), marker]))
    _ok, result = await graphql_run(ctx.deps.schema, ASK_USER_CREATE, {"input": {"title": text, "teamId": team_id, "description": description}}, ctx.deps.context)
    if result.get("errors"):
        return {"status": "failed", "text": text, "error": result["errors"][0]["message"]}
    payload = (result.get("data") or {}).get("issueCreate") or {}
    identifier = (payload.get("issue") or {}).get("identifier")
    if payload.get("success") and identifier:
        return {"status": "created", "text": text, "identifier": identifier}
    return {"status": "failed", "text": text, "error": "issueCreate did not confirm the issue"}


def build_agent(model, schema_sdl: str, connector: ConnectorBinding | None = None) -> Agent:
    toolsets = [connector_toolset(connector)] if connector is not None else []
    agent = Agent(model, deps_type=AgentDeps, retries=3, toolsets=toolsets)

    @agent.instructions
    async def workspace_instructions(ctx: RunContext[AgentDeps]) -> str:
        """Compose the system prompt per run from the fixed working examples and the user's live workspace state."""
        # Instructions are re-evaluated on every model step; load the snapshot once per run.
        if ctx.deps.snapshot is None:
            async def fetch(query: str, variables: dict) -> dict:
                _ok, result = await graphql_run(ctx.deps.schema, query, variables, ctx.deps.context)
                return result.get("data") or {}

            ctx.deps.snapshot = await workspace_snapshot(fetch, ctx.deps.context.get("user_id"))
        return compose_prompt(INSTRUCTIONS, ctx.deps.snapshot)

    @agent.tool
    async def schema(ctx: RunContext[AgentDeps]) -> str:
        """Return the Hoja GraphQL schema (SDL) listing every query and mutation."""
        return schema_sdl

    @agent.tool
    async def graphql(ctx: RunContext[AgentDeps], query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query or mutation against Hoja as the signed-in user.
        `query` is REQUIRED and must be a non-empty GraphQL string. Call `schema` first if you need field names."""
        if read_only_violation(query, ctx.deps.context.get("access", "read")):
            return {"errors": [{"message": "This API key is read-only.", "extensions": {"code": "FORBIDDEN", "agentHint": DENY_HINT}}]}
        _ok, result = await graphql_run(ctx.deps.schema, query, variables or {}, ctx.deps.context)
        return result

    @agent.tool
    async def ask_user(ctx: RunContext[AgentDeps], teamId: str, questions: Any) -> list[dict]:
        """Ask the user questions as Hoja issues in team `teamId` (a key such as TREBOL, or an id), so they can answer
        by commenting on each issue. `questions` is a list of up to 10 objects {"text": required string,
        "options": optional list of strings, "context": optional string}; a plain string per question also works.
        Questions already asked are skipped. Returns one status per question: created (identifier), skipped (identifier),
        or failed (error). Read answers back via `issue { comments }`."""
        if ctx.deps.context.get("access", "read") == "read":
            return [{"status": "failed", "error": "This API key is read-only."}]
        if not isinstance(teamId, str) or not teamId.strip():
            return [{"status": "failed", "error": "teamId is required"}]
        try:
            items = _repair_json(questions) if isinstance(questions, str) else questions
        except ValueError as exc:
            return [{"status": "failed", "error": str(exc)}]
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or not items:
            return [{"status": "failed", "error": "questions must be a non-empty list"}]
        if len(items) > ASK_USER_QUESTION_LIMIT:
            return [{"status": "failed", "error": f"at most {ASK_USER_QUESTION_LIMIT} questions per call; send the rest in another call"}]
        team_id, problem = await _resolve_team_id(ctx, teamId.strip())
        if team_id is None:
            return [{"status": "failed", "error": problem}]
        statuses = []
        for raw in items:
            question, problem = _normalize_question(raw)
            if question is None:
                statuses.append({"status": "failed", "error": problem})
                continue
            try:
                statuses.append(await _ask_one(ctx, team_id, question))
            except Exception as exc:  # one bad question must not end the agent run
                log.exception("ask_user failed for one question")
                statuses.append({"status": "failed", "text": question["text"], "error": f"unexpected {type(exc).__name__}"})
        return statuses

    return agent


@dataclass(frozen=True)
class ConnectorBinding:
    """Where the Google Workspace MCP server lives and the signed principal for this request."""

    url: str
    principal: str


def connector_toolset(binding: ConnectorBinding) -> MCPToolset:
    transport = StreamableHttpTransport(
        binding.url,
        headers={PRINCIPAL_HEADER: binding.principal},
    )
    return MCPToolset(transport, id="google-workspace")


def read_only_violation(query: str, access: str) -> bool:
    """True when a read-only credential submits any operation other than a query."""
    if access != "read":
        return False
    try:
        document = parse(query)
    except GraphQLError:
        return False  # syntax errors are reported by graphql() without executing anything
    return any(
        isinstance(definition, OperationDefinitionNode) and definition.operation != OperationType.QUERY
        for definition in document.definitions
    )


async def graphql_run(schema, query: str, variables: dict, context: dict):
    return await graphql(schema, {"query": query, "variables": variables}, context_value=context)


def _enabled_provider(store, org_id: str) -> dict | None:
    with store.engine.connect() as conn:
        row = conn.execute(
            select(model_provider)
            .where(and_(model_provider.c.organization_id == org_id, model_provider.c.enabled.is_(True)))
            .order_by(model_provider.c.created_at)
            .limit(1)
        ).first()
    return dict(row._mapping) if row else None


def _history(messages: list[dict]) -> tuple[str, list]:
    """Split OpenAI-style messages into the latest prompt and prior turns."""
    turns = []
    for msg in messages:
        role, content = msg.get("role"), msg.get("content")
        if not isinstance(content, str):
            continue
        if role == "user":
            turns.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            turns.append(ModelResponse(parts=[TextPart(content=content)]))
    if not turns or not isinstance(turns[-1], ModelRequest):
        raise ValueError("Last message must be a user message.")
    prompt = turns[-1].parts[-1].content
    return prompt, turns[:-1]


def _chunk(completion_id: str, delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "hoja-agent",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def stream_reply(store, org_id: str, uid: str, messages: list[dict], schema, schema_sdl: str, base_context: dict, thread_id: str | None = None, connector: ConnectorBinding | None = None) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    prompt, history = _history(messages)
    row = _enabled_provider(store, org_id)
    if row is None:
        yield _chunk(completion_id, {"role": "assistant", "content": "No model is configured. Ask a workspace admin to add one."}, "stop")
        yield "data: [DONE]\n\n"
        return
    provider = OpenAIProvider(base_url=row["base_url"], api_key=decrypt_secret(row["encrypted_api_key"]))
    yield _chunk(completion_id, {"role": "assistant"})
    try:
        base_url = await asyncio.to_thread(validate_model_base_url, row["base_url"])
    except ValueError:
        yield _chunk(completion_id, {"content": "The configured model URL is not allowed. Ask a workspace admin to fix it."}, "stop")
        yield "data: [DONE]\n\n"
        return
    if not base_url:
        base_url = None
    http_client = openai.DefaultAsyncHttpxClient(follow_redirects=False)
    provider = OpenAIProvider(
        openai_client=openai.AsyncOpenAI(
            base_url=base_url,
            api_key=decrypt_secret(row["encrypted_api_key"]),
            http_client=http_client,
        )
    )
    agent = build_agent(OpenAIChatModel(row["model_id"], provider=provider), schema_sdl, connector)
    deps = AgentDeps(schema, dict(base_context))
    with store.engine.begin() as conn:
        if thread_id and _owned_thread(conn, org_id, uid, thread_id):
            conversation_id = thread_id
        else:
            conversation_id = start_conversation(conn, org_id, uid, title=_title_from(prompt))
        save_message(conn, conversation_id, "user", prompt)
    reply_parts: list[str] = []
    try:
        # Stream every model turn: run_stream/stream_text stops after the first text response,
        # so text written after a tool call (the final answer) was never sent to the user.
        async with agent.iter(prompt, message_history=history, deps=deps) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            text = None
                            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                                text = event.part.content
                            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                                text = event.delta.content_delta
                            if text:
                                reply_parts.append(text)
                                yield _chunk(completion_id, {"content": text})
    except UnexpectedModelBehavior:
        reply_parts = ["The assistant could not complete that request. Please try again."]
        yield _chunk(completion_id, {"content": reply_parts[0]})
    with store.engine.begin() as conn:
        save_message(conn, conversation_id, "assistant", "".join(reply_parts))
    yield _chunk(completion_id, {}, "stop")
    yield "data: [DONE]\n\n"


def sse_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
