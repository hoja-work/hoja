"""Streams agent replies to the Hoja chat screen as OpenAI-compatible SSE."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

from ariadne import graphql
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import and_, select
from starlette.responses import StreamingResponse

from cliniar_server.db import model_provider
from cliniar_server.secrets_box import decrypt_secret

INSTRUCTIONS = (
    "You are the Hoja workspace agent. Help the signed-in user with their issues, "
    "projects, and cycles. Use the graphql tool to read and change data; use the "
    "schema tool first when you need field names. Be concise."
)


class AgentDeps:
    def __init__(self, schema, context: dict):
        self.schema = schema
        self.context = context


def build_agent(model, schema_sdl: str) -> Agent:
    agent = Agent(model, deps_type=AgentDeps, instructions=INSTRUCTIONS)

    @agent.tool
    async def schema(ctx: RunContext[AgentDeps]) -> str:
        """Return the Hoja GraphQL schema (SDL) listing every query and mutation."""
        return schema_sdl

    @agent.tool
    async def graphql(ctx: RunContext[AgentDeps], query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query or mutation against Hoja as the signed-in user."""
        _ok, result = await graphql_run(ctx.deps.schema, query, variables or {}, ctx.deps.context)
        return result

    return agent


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


async def stream_reply(store, org_id: str, uid: str, messages: list[dict], schema, schema_sdl: str, base_context: dict) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    prompt, history = _history(messages)
    row = _enabled_provider(store, org_id)
    if row is None:
        yield _chunk(completion_id, {"role": "assistant", "content": "No model is configured. Ask a workspace admin to add one."}, "stop")
        yield "data: [DONE]\n\n"
        return
    provider = OpenAIProvider(base_url=row["base_url"], api_key=decrypt_secret(row["encrypted_api_key"]))
    agent = build_agent(OpenAIChatModel(row["model_id"], provider=provider), schema_sdl)
    yield _chunk(completion_id, {"role": "assistant"})
    deps = AgentDeps(schema, dict(base_context))
    async with agent.run_stream(prompt, message_history=history, deps=deps) as result:
        async for text in result.stream_text(delta=True):
            yield _chunk(completion_id, {"content": text})
    yield _chunk(completion_id, {}, "stop")
    yield "data: [DONE]\n\n"


def sse_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
