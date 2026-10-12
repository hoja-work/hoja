"""Streams agent replies to the Hoja chat screen as OpenAI-compatible SSE."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
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
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
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
        async with agent.run_stream(prompt, message_history=history, deps=deps) as result:
            async for text in result.stream_text(delta=True):
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
