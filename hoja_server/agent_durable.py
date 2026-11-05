"""Durable agent chat turns: each turn is a DBOS workflow, so a crash resumes the turn instead of losing it.

Enabled when HOJA_AGENT_DBOS_URL is set. Import this module before ``DBOS.launch()``: DBOS
registers workflows and steps at import, and recovery at launch needs them registered.

What is checkpointed (recovery replays these instead of re-running them):
- model requests, through DBOSDurability;
- every graphql tool call, through an explicit step (DBOS does not wrap function tools), so a
  mutation that completed before a crash is not executed again;
- loading the transcript and saving the reply.
The transcript is server-owned: the workflow reads prior turns from agent_message.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from dbos import DBOS, SetWorkflowID
from fastmcp.client.transports import StreamableHttpTransport
from google_workspace.mcp_server import PRINCIPAL_HEADER
from pydantic_ai import RunContext
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelResponse, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import and_, select

from hoja_server import agent_chat as chat
from hoja_server.agent_conversations import save_message, start_conversation
from hoja_server.agent_threads import _owned as _owned_thread
from hoja_server.agent_threads import _title_from
from hoja_server.db import agent_message, model_provider
from hoja_server.model_providers import validate_model_base_url
from hoja_server.schema_sdl import SCHEMA_SDL
from hoja_server.secrets_box import decrypt_secret

AGENT_NAME = "hoja-agent"
MODEL_PREFIX = "hoja-provider:"
FAILED_REPLY = "The assistant could not complete that request. Please try again."


@dataclass
class _Runtime:
    store: Any
    writer: Any
    schema: Any


_runtime: _Runtime | None = None
# Live SSE sinks by workflow id. A recovered workflow has no sink; its reply is still saved.
_sinks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar("hoja_connector_principal", default=None)
_tool_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hoja_tool_context", default=None)

# Tests replace this to script the model; production builds the org's configured provider.
model_factory: Callable[[dict], Model] | None = None


def configure(store, writer, schema) -> None:
    global _runtime
    _runtime = _Runtime(store=store, writer=writer, schema=schema)


def shutdown() -> None:
    global _runtime
    _runtime = None


def active() -> bool:
    return _runtime is not None


def _provider_row(org_id: str, provider_id: str) -> dict | None:
    assert _runtime is not None
    with _runtime.store.engine.connect() as conn:
        row = conn.execute(
            select(model_provider).where(and_(
                model_provider.c.id == provider_id,
                model_provider.c.organization_id == org_id,
                model_provider.c.enabled.is_(True),
            ))
        ).first()
    return dict(row._mapping) if row else None


async def _resolve_model(ctx, model_id: str) -> Model | None:
    """Build the org's model inside the durable step from the provider id the turn recorded."""
    if not model_id.startswith(MODEL_PREFIX):
        return None
    row = _provider_row(ctx.deps.context["org_id"], model_id[len(MODEL_PREFIX):])
    if row is None:
        raise UnexpectedModelBehavior("The configured model is no longer available.")
    if model_factory is not None:
        return model_factory(row)
    base_url = await asyncio.to_thread(validate_model_base_url, row["base_url"]) or None
    client = openai.AsyncOpenAI(
        base_url=base_url,
        api_key=decrypt_secret(row["encrypted_api_key"]),
        http_client=openai.DefaultAsyncHttpxClient(follow_redirects=False),
    )
    return OpenAIChatModel(row["model_id"], provider=OpenAIProvider(openai_client=client))


def _text_of(event) -> str | None:
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return None


async def _forward_text(ctx: RunContext[chat.AgentDeps], events) -> None:
    """Runs live inside each model-request step; pushes text deltas to the waiting SSE response."""
    sink = ctx.deps.sink
    async for event in events:
        text = _text_of(event)
        if text and sink is not None:
            loop, queue = sink
            loop.call_soon_threadsafe(queue.put_nowait, text)


class _PrincipalAuth(httpx.Auth):
    """Signs each MCP request with the principal of the turn that is running."""

    def auth_flow(self, request):
        principal = _principal.get()
        if principal:
            request.headers[PRINCIPAL_HEADER] = principal
        yield request


def _durability(name: str) -> list:
    # Sequential tools: DBOS numbers steps as they start, so parallel graphql steps could replay
    # recorded results into the wrong calls on recovery.
    return [
        ResolveModelId(_resolve_model),
        DBOSDurability(name=name, event_stream_handler=_forward_text, parallel_execution_mode="sequential"),
    ]


# A string default is never built at construction; every turn passes its provider id as `model=`.
_UNSET_MODEL = MODEL_PREFIX + "unset"
AGENT = chat.build_agent(_UNSET_MODEL, SCHEMA_SDL, name=AGENT_NAME, capabilities=_durability(AGENT_NAME), toolsets=[])

_connector_url = os.environ.get("HOJA_CONNECTOR_MCP_URL", "").strip()
# Runtime MCP toolsets are rejected under DBOS, so the connector agent is built once with the toolset.
CONNECTOR_AGENT = (
    chat.build_agent(
        _UNSET_MODEL, SCHEMA_SDL, name=f"{AGENT_NAME}-gw", capabilities=_durability(f"{AGENT_NAME}-gw"),
        toolsets=[MCPToolset(StreamableHttpTransport(_connector_url, auth=_PrincipalAuth()), id="google-workspace")],
    )
    if _connector_url else None
)


@DBOS.step(name=f"{AGENT_NAME}.load_turn")
async def _load_turn(conversation_id: str, user_message_id: str) -> list[list[str]]:
    """Prior turns plus the prompt, read from the server-side transcript."""
    assert _runtime is not None
    with _runtime.store.engine.connect() as conn:
        rows = conn.execute(
            select(agent_message.c.id, agent_message.c.role, agent_message.c.content)
            .where(agent_message.c.conversation_id == conversation_id)
            .order_by(agent_message.c.created_at)
        ).all()
    turns: list[list[str]] = []
    for mid, role, content in rows:
        turns.append([role, content])
        if mid == user_message_id:
            return turns
    raise ValueError("The turn's user message is missing.")


@DBOS.step(name=f"{AGENT_NAME}.graphql")
async def _graphql_step(query: str, variables: dict) -> dict:
    """One checkpointed graphql call. Not retried: a mutation must not run twice."""
    assert _runtime is not None
    context = _tool_context.get()
    assert context is not None
    _ok, result = await chat.graphql_run(_runtime.schema, query, variables, context)
    return result


def _graphql_runner(context: dict):
    async def run(query: str, variables: dict) -> dict:
        token = _tool_context.set(context)
        try:
            return await _graphql_step(query, variables)
        finally:
            _tool_context.reset(token)

    return run


@DBOS.step(name=f"{AGENT_NAME}.save_reply")
async def _save_reply(conversation_id: str, content: str) -> str:
    assert _runtime is not None
    with _runtime.store.engine.begin() as conn:
        return save_message(conn, conversation_id, "assistant", content)


def _reply_text(result) -> str:
    return "".join(
        part.content
        for message in result.new_messages() if isinstance(message, ModelResponse)
        for part in message.parts if isinstance(part, TextPart)
    )


@DBOS.workflow(name=f"{AGENT_NAME}.turn")
async def agent_turn(turn: dict) -> dict:
    """One chat turn. Inputs are plain ids so a new process can rebuild everything on recovery."""
    assert _runtime is not None
    turns = await _load_turn(turn["conversation_id"], turn["user_message_id"])
    prompt, history = chat._history([{"role": role, "content": content} for role, content in turns])
    context = {
        "request": None,
        "store": _runtime.store,
        "writer": _runtime.writer,
        "org_id": turn["org_id"],
        "user_id": turn["user_id"],
        "identity_id": turn["identity_id"],
        "browser_session": turn["browser_session"],
        "access": turn["access"],
    }
    deps = chat.AgentDeps(_runtime.schema, context, graphql_runner=_graphql_runner(context),
                          sink=_sinks.get(DBOS.workflow_id or ""))
    principal = turn.get("principal")
    agent = CONNECTOR_AGENT if principal and CONNECTOR_AGENT is not None else AGENT
    token = _principal.set(principal)
    ask = None
    try:
        result = await agent.run(prompt, message_history=history, deps=deps,
                                 model=MODEL_PREFIX + turn["provider_id"])
        text = _reply_text(result)
        if isinstance(result.output, chat.AskUser):
            ask = chat.ask_block(result.output)
    except UnexpectedModelBehavior:
        text = FAILED_REPLY
    finally:
        _principal.reset(token)
    await _save_reply(turn["conversation_id"], text + (ask or ""))
    return {"text": text, "ask": ask}


async def stream_reply(store, org_id: str, uid: str, messages: list[dict], base_context: dict,
                       thread_id: str | None = None, connector: chat.ConnectorBinding | None = None) -> AsyncIterator[str]:
    """Same OpenAI-compatible SSE contract as agent_chat.stream_reply, backed by a durable workflow.

    The workflow keeps running if the client disconnects; the reply is saved to the thread either way.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    prompt, history = chat._history(messages)
    row = chat._enabled_provider(store, org_id)
    if row is None:
        yield chat._chunk(completion_id, {"role": "assistant", "content": "No model is configured. Ask a workspace admin to add one."}, "stop")
        yield "data: [DONE]\n\n"
        return
    yield chat._chunk(completion_id, {"role": "assistant"})
    try:
        await asyncio.to_thread(validate_model_base_url, row["base_url"])
    except ValueError:
        yield chat._chunk(completion_id, {"content": "The configured model URL is not allowed. Ask a workspace admin to fix it."}, "stop")
        yield "data: [DONE]\n\n"
        return

    with store.engine.begin() as conn:
        if thread_id and _owned_thread(conn, org_id, uid, thread_id):
            conversation_id = thread_id
        else:
            conversation_id = start_conversation(conn, org_id, uid, title=_title_from(prompt))
            # A new conversation adopts the client's earlier turns so the server transcript is complete.
            for msg in history:
                role = "user" if msg.kind == "request" else "assistant"
                save_message(conn, conversation_id, role, msg.parts[-1].content)
        user_message_id = save_message(conn, conversation_id, "user", prompt)

    workflow_id = f"agent-turn-{user_message_id}"
    queue: asyncio.Queue[str] = asyncio.Queue()
    _sinks[workflow_id] = (asyncio.get_running_loop(), queue)
    turn = {
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "provider_id": row["id"],
        "org_id": org_id,
        "user_id": uid,
        "identity_id": base_context.get("identity_id"),
        "browser_session": base_context.get("browser_session", False),
        "access": base_context.get("access", "read_write"),
        "principal": connector.principal if connector is not None else None,
    }
    try:
        with SetWorkflowID(workflow_id):
            handle = await DBOS.start_workflow_async(agent_turn, turn)
        result_task = asyncio.ensure_future(handle.get_result())
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, result_task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield chat._chunk(completion_id, {"content": getter.result()})
                continue
            getter.cancel()
            break
        while not queue.empty():
            yield chat._chunk(completion_id, {"content": queue.get_nowait()})
        try:
            outcome = result_task.result()
        except Exception:
            outcome = {"text": FAILED_REPLY, "ask": None}
        if outcome.get("text") == FAILED_REPLY:
            yield chat._chunk(completion_id, {"content": FAILED_REPLY})
        if outcome.get("ask"):
            yield chat._chunk(completion_id, {"content": outcome["ask"]})
    finally:
        _sinks.pop(workflow_id, None)
    yield chat._chunk(completion_id, {}, "stop")
    yield "data: [DONE]\n\n"
