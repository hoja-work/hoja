"""Streams agent replies to the Hoja chat screen as OpenAI-compatible SSE."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from dataclasses import dataclass

import openai
from ariadne import graphql
from fastmcp.client.transports import StreamableHttpTransport
from google_workspace.mcp_server import PRINCIPAL_HEADER
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, ToolOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import and_, select
from starlette.responses import StreamingResponse

from hoja_server import agent_members, audit, tenant_context
from hoja_server.agent_conversations import MAX_TOOL_CALLS_BYTES, save_message, start_conversation
from hoja_server.agent_prompt import compose_prompt, workspace_snapshot
from hoja_server.agent_threads import _owned as _owned_thread
from hoja_server.agent_threads import _title_from
from hoja_server.db import model_provider
from hoja_server.denials import DENY_HINT, READ_ONLY_HINT, read_only_violation
from hoja_server.knowledge import resolve_knowledge_context
from hoja_server.model_providers import validate_model_base_url
from hoja_server.secrets_box import decrypt_secret

INSTRUCTIONS = """You are the Hoja workspace agent. Hoja is a wire-compatible task and issue tracker.

Entities: teams (keys like ENG), projects, issues (identifiers like ENG-12), cycles, and users.

How to work:
- Use the `schema` tool once per conversation before writing a mutation you have not used before, so you use real field names.
- Use the `graphql` tool to read and change data. Always pass a non-empty `query` string.
- Before creating anything, search for an existing issue or project with the same title to avoid duplicates.
- Lists, counts, and summaries: the default page size is 50, so pass `first: 250` and, while `pageInfo.hasNextPage` is true, repeat the query with `after` set to `pageInfo.endCursor`. Report totals only after the last page. If you stop early, say how many you saw and that more exist.
- When creating a task, set title, teamId, and projectId when the user names a project. Add a short description with acceptance criteria when the request is vague.
- Assign people only after confirming they are members of the team.
- To filter to the signed-in user, first run `{ viewer { id } }`, then use the returned id, e.g. `issues(filter: { assignee: { id: { eq: "<id>" } } })`. `UserFilter` has only id, email, displayName, and name; there is no `isMe` field.
- After a write, read it back to confirm it exists and is linked to the right project.
- To hand a task to an agent, call the `delegate` tool with the task and the agent's user id. Find agents with `{ agentMembers { userId user { name } archivedAt } }`; only agents with no `archivedAt` can take work. Delegate only when the user asks for an agent to do the work. If they did not say which agent and more than one exists, ask with `ask_user`. The tool returns the agent session: report its state, and when it is queued, say why in plain words (the `queueReason` values are capacity, budget, window, blocked, approval and lease_held). The chat shows a live card for each delegation, so do not poll for progress.

How to answer:
- Reply in Markdown. Use short paragraphs, bullet lists for multiple items, and fenced code blocks for queries or IDs.
- Reference every issue as a link in the form [ENG-12](/workspace/task/ENG-12/slug-of-title), where slug-of-title is the lowercase title with non-alphanumerics replaced by hyphens. Hoja's UI routes tasks there.
- Be concise. Lead with the result, then list what changed.
- If a request is ambiguous (which team, which project, which person), ask one focused question instead of guessing.
- To ask the user something, call the `ask_user` tool with one to four questions, each with short options when there are sensible choices. It ends your turn; the user's answers arrive as their next message. Set `multiple` when several options may apply and `allow_other` when they may write their own answer. Never create, comment on, or change Hoja issues to ask questions.
- Never reveal tokens, secrets, or raw authorization headers."""


ASK_FENCE = "hoja-ask"
DELEGATION_FENCE = "hoja-delegation"

DELEGATE_MUTATION = """
mutation Delegate($id: String!, $agent: String!) {
  issueUpdate(id: $id, input: { delegateId: $agent }) {
    success
    issue {
      id identifier title
      delegate { id name }
      agentSession { id state status queueReason admitted }
    }
  }
}"""


class AskOption(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)


class AskQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    header: str | None = Field(default=None, max_length=40, description="Very short label for the question, e.g. 'Team'.")
    options: list[AskOption] = Field(default_factory=list, max_length=8, description="Choices to pick from. Leave empty for a free-text question.")
    multiple: bool = Field(default=False, description="Allow selecting more than one option.")
    allow_other: bool = Field(default=True, description="Let the user write their own answer instead of an option.")


class AskUser(BaseModel):
    """Ask the user one or more questions in the chat UI and wait for their answers."""

    questions: list[AskQuestion] = Field(min_length=1, max_length=4)


def ask_block(ask: AskUser) -> str:
    """Fenced block the chat UI renders as an interactive question card."""
    return f"\n\n```{ASK_FENCE}\n{ask.model_dump_json(exclude_none=True)}\n```\n"


def delegation_blocks(delegations: list[dict]) -> str:
    """Fenced blocks the chat UI renders as live cards, one per delegated task (the last one wins)."""
    latest = {d["issueId"]: d for d in delegations}
    return "".join(f"\n\n```{DELEGATION_FENCE}\n{json.dumps(d, separators=(',', ':'))}\n```\n"
                   for d in latest.values())


class AgentDeps:
    def __init__(self, schema, context: dict, graphql_runner=None, sink=None):
        self.schema = schema
        self.context = context
        self.snapshot: str | None = None
        self.knowledge: str | None = None
        # Durable runs route the graphql tool through a checkpointed step and stream text to a sink.
        self.graphql_runner = graphql_runner
        self.sink = sink
        # Tasks this turn handed to an agent; the reply ends with one card per task.
        self.delegations: list[dict] = []


async def _run_graphql(deps: AgentDeps, query: str, variables: dict) -> dict:
    """The one path every agent read and write takes: the read-only guard, then the durable
    runner when there is one, else the schema directly, as the signed-in user."""
    if read_only_violation(query, deps.context.get("access", "read")):
        return {"errors": [{"message": "This API key is read-only.", "extensions": {"code": "FORBIDDEN", "agentHint": READ_ONLY_HINT}}]}
    if deps.graphql_runner is not None:
        return await deps.graphql_runner(query, variables)
    _ok, result = await graphql_run(deps.schema, query, variables, deps.context)
    return result


def _merge_connector_bindings(
    connector: ConnectorBinding | None,
    connectors: list[ConnectorBinding] | None,
) -> list[ConnectorBinding]:
    combined: list[ConnectorBinding] = []
    seen_keys: set[tuple[str, str]] = set()
    candidates: list[ConnectorBinding] = []
    if connectors:
        candidates.extend(connectors)
    if connector is not None:
        candidates.append(connector)
    for binding in candidates:
        key = (binding.url, binding.id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        combined.append(binding)
    return combined


def build_agent(
    model,
    schema_sdl: str,
    connector: ConnectorBinding | None = None,
    *,
    connectors: list[ConnectorBinding] | None = None,
    name: str | None = None,
    capabilities: list | None = None,
    toolsets: list | None = None,
) -> Agent:
    if toolsets is None:
        bindings = _merge_connector_bindings(connector, connectors)
        toolsets = [connector_toolset(binding) for binding in bindings]
    # ask_user is an output tool: it ends the run and never receives deps, so it cannot read or change workspace data.
    agent = Agent(
        model,
        name=name,
        deps_type=AgentDeps,
        retries=3,
        toolsets=toolsets,
        capabilities=capabilities or [],
        output_type=[str, ToolOutput(AskUser, name="ask_user", description=AskUser.__doc__)],
    )

    @agent.instructions
    async def workspace_instructions(ctx: RunContext[AgentDeps]) -> str:
        """Compose the system prompt per run from the fixed working examples, live workspace state, and company knowledge."""
        # Instructions are re-evaluated on every model step; load snapshot & knowledge once per run.
        if ctx.deps.snapshot is None:
            async def fetch(query: str, variables: dict) -> dict:
                _ok, result = await graphql_run(ctx.deps.schema, query, variables, ctx.deps.context)
                return result.get("data") or {}

            ctx.deps.snapshot = await workspace_snapshot(fetch, ctx.deps.context.get("user_id"))
        if ctx.deps.knowledge is None:
            store = ctx.deps.context.get("store")
            org_id = ctx.deps.context.get("org_id")
            if store is not None and org_id:
                with store.engine.connect() as conn:
                    ctx.deps.knowledge = resolve_knowledge_context(conn, org_id)
            else:
                ctx.deps.knowledge = ""
        return compose_prompt(INSTRUCTIONS, ctx.deps.snapshot, knowledge=ctx.deps.knowledge)

    @agent.tool
    async def schema(ctx: RunContext[AgentDeps]) -> str:
        """Return the Hoja GraphQL schema (SDL) listing every query and mutation."""
        return schema_sdl

    @agent.tool
    async def graphql(ctx: RunContext[AgentDeps], query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query or mutation against Hoja as the signed-in user.
        `query` is REQUIRED and must be a non-empty GraphQL string. Call `schema` first if you need field names."""
        return await _run_graphql(ctx.deps, query, variables or {})

    @agent.tool
    async def delegate(ctx: RunContext[AgentDeps], issue: str, agent_user_id: str) -> dict:
        """Hand a task to an agent member to work on.
        `issue` is the task's id or identifier (e.g. TREBOL-12); `agent_user_id` is the agent's
        `userId` from `agentMembers`. Returns the task and its agent session (state, status and, when
        queued, queueReason). The server checks that the agent belongs to this workspace."""
        result = await _run_graphql(ctx.deps, DELEGATE_MUTATION, {"id": issue, "agent": agent_user_id})
        # issueUpdate, not taskUpdate: TaskUpdateInput has no delegateId; the task page uses this too.
        payload = (result.get("data") or {}).get("issueUpdate") or {}
        task = payload.get("issue")
        if payload.get("success") and task:
            session = task.get("agentSession") or {}
            ctx.deps.delegations.append({
                "issueId": task["id"],
                "identifier": task.get("identifier"),
                "title": task.get("title"),
                "agentName": (task.get("delegate") or {}).get("name"),
                "sessionId": session.get("id"),
            })
        return result

    return agent


@dataclass(frozen=True)
class ConnectorBinding:
    """Where a connector MCP server lives and the signed principal for this request."""

    url: str
    principal: str
    provider: str = "google_workspace"
    toolset_id: str | None = None

    @property
    def id(self) -> str:
        if self.toolset_id:
            return self.toolset_id
        return self.provider.replace("_", "-")


def connector_toolset(binding: ConnectorBinding) -> MCPToolset:
    transport = StreamableHttpTransport(
        binding.url,
        headers={PRINCIPAL_HEADER: binding.principal},
    )
    return MCPToolset(transport, id=binding.id)


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


_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "apikey", "authorization", "cookie", "credential")
_MAX_TOOL_ARGS_CHARS = 2000
_MAX_TOOL_OUTPUT_CHARS = 4000


def _redact_value(value, *, depth: int = 0):
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            low = key.lower()
            if any(part in low for part in _SECRET_KEY_PARTS):
                out[key] = "[redacted]"
            else:
                out[key] = _redact_value(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_redact_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str) and len(value) > _MAX_TOOL_OUTPUT_CHARS:
        return value[:_MAX_TOOL_OUTPUT_CHARS] + "… (truncated)"
    return value


def _truncate_str(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "… (truncated)"


def format_tool_args(args) -> dict | str | None:
    if args is None:
        return None
    if isinstance(args, str):
        raw = args.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return _redact_value(parsed)
        except ValueError:
            pass
        return _truncate_str(raw, _MAX_TOOL_ARGS_CHARS)
    if isinstance(args, dict):
        return _redact_value(args)
    return _truncate_str(str(args), _MAX_TOOL_ARGS_CHARS)


def format_tool_result(tool_name: str, part) -> tuple[str, str | None, str | None]:
    """Return (status, output_preview, error_message) for a tool return or retry part."""
    if isinstance(part, RetryPromptPart):
        msg = part.content if isinstance(part.content, str) else json.dumps(part.content, default=str)
        return "error", None, _truncate_str(msg, _MAX_TOOL_OUTPUT_CHARS)

    outcome = getattr(part, "outcome", "success")
    content = getattr(part, "content", None)

    if tool_name == "schema" and isinstance(content, str):
        return (
            "ok" if outcome == "success" else "error",
            f"Loaded Hoja GraphQL schema ({len(content):,} characters).",
            None if outcome == "success" else "Failed to load schema.",
        )

    if isinstance(content, dict):
        redacted = _redact_value(content)
        errors = redacted.get("errors") if isinstance(redacted, dict) else None
        error_msg: str | None = None
        if isinstance(errors, list) and errors:
            first = errors[0]
            error_msg = first.get("message") if isinstance(first, dict) else str(first)
        serialized = _truncate_str(json.dumps(redacted, indent=2, default=str), _MAX_TOOL_OUTPUT_CHARS)
        if outcome != "success":
            return "error", serialized, error_msg or f"Tool {outcome}."
        if error_msg and not redacted.get("data"):
            return "error", serialized, _truncate_str(str(error_msg), 500)
        return "ok", serialized, None

    if isinstance(content, list):
        redacted = _redact_value(content)
        serialized = _truncate_str(json.dumps(redacted, indent=2, default=str), _MAX_TOOL_OUTPUT_CHARS)
        return ("ok" if outcome == "success" else "error"), serialized, (None if outcome == "success" else f"Tool {outcome}.")

    text = "" if content is None else str(content)
    preview = _truncate_str(text, _MAX_TOOL_OUTPUT_CHARS) if text else None
    if outcome != "success":
        return "error", preview, _truncate_str(text or f"Tool {outcome}.", 500)
    return "ok", preview, None


def serialize_tool_calls(records: list[dict]) -> str | None:
    """Serialize completed tool call traces for persistence within MAX_TOOL_CALLS_BYTES."""
    if not records:
        return None
    cleaned = [
        {
            "id": r["id"],
            "name": r["name"],
            **({"args": r["args"]} if r.get("args") is not None else {}),
            "status": r.get("status", "ok"),
            **({"output": r["output"]} if r.get("output") else {}),
            **({"error": r["error"]} if r.get("error") else {}),
            **({"durationMs": r["durationMs"]} if r.get("durationMs") is not None else {}),
        }
        for r in records
    ]
    encoded = json.dumps(cleaned, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) <= MAX_TOOL_CALLS_BYTES:
        return encoded
    # Trim verbose outputs first so the trace structure still persists cleanly.
    for item in cleaned:
        if isinstance(item.get("output"), str) and len(item["output"]) > 400:
            item["output"] = item["output"][:400] + "… (truncated)"
    encoded = json.dumps(cleaned, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) <= MAX_TOOL_CALLS_BYTES:
        return encoded
    for item in cleaned:
        item.pop("output", None)
    encoded = json.dumps(cleaned, separators=(",", ":"), default=str)
    return encoded if len(encoded.encode("utf-8")) <= MAX_TOOL_CALLS_BYTES else None


def extract_tool_calls_from_messages(messages) -> list[dict]:
    """Build persisted tool-call trace records from pydantic_ai new_messages()."""
    by_id: dict[str, dict] = {}
    ordered: list[dict] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_name != "ask_user":
                    rec = {
                        "id": part.tool_call_id,
                        "name": part.tool_name,
                        "args": format_tool_args(part.args),
                        "status": "ok",
                    }
                    by_id[part.tool_call_id] = rec
                    ordered.append(rec)
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    cid = getattr(part, "tool_call_id", None)
                    rec = by_id.get(cid) if cid else None
                    if rec is None:
                        continue
                    status, output, err = format_tool_result(rec["name"], part)
                    rec["status"] = status
                    if output:
                        rec["output"] = output
                    if err:
                        rec["error"] = err
    return ordered


def _chunk(completion_id: str, delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "hoja-agent",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _event_chunk(completion_id: str, event: dict) -> str:
    return _chunk(completion_id, {"hoja_event": event})


async def stream_reply(
    store,
    org_id: str,
    uid: str,
    messages: list[dict],
    schema,
    schema_sdl: str,
    base_context: dict,
    thread_id: str | None = None,
    connector: ConnectorBinding | None = None,
    *,
    connectors: list[ConnectorBinding] | None = None,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    prompt, history = _history(messages)
    row = _enabled_provider(store, org_id)
    if row is None:
        yield _chunk(completion_id, {"role": "assistant", "content": "No model is configured. Ask a workspace admin to add one."}, "stop")
        yield "data: [DONE]\n\n"
        return
    provider = OpenAIProvider(base_url=row["base_url"], api_key=decrypt_secret(row["encrypted_api_key"], row["organization_id"]))
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
            api_key=decrypt_secret(row["encrypted_api_key"], row["organization_id"]),
            http_client=http_client,
        )
    )
    agent = build_agent(
        OpenAIChatModel(row["model_id"], provider=provider),
        schema_sdl,
        connector,
        connectors=connectors,
    )
    deps = AgentDeps(schema, dict(base_context))
    with store.engine.begin() as conn:
        if thread_id and _owned_thread(conn, org_id, uid, thread_id):
            conversation_id = thread_id
        else:
            conversation_id = start_conversation(conn, org_id, uid, title=_title_from(prompt))
        save_message(conn, conversation_id, "user", prompt)
    yield _event_chunk(completion_id, {"type": "thread", "threadId": conversation_id})
    reply_parts: list[str] = []
    tool_records: list[dict] = []
    tool_by_id: dict[str, dict] = {}
    tool_started: dict[str, float] = {}
    # F4: what the agent writes is attributed to the agent, with the person who
    # asked as on_behalf_of. No agent member means the writes stay unattributed
    # rather than borrowing the human's identity.
    agent_user_id = agent_members.turn_agent(store.engine, org_id)
    # WP05: an agent turn is the session this stack has (WP06 adds the durable
    # schedule/session). Start/stop bracket the turn, so a crash leaves a start
    # with no stop — which is the trace an operator needs, not a missing one.
    # A workspace with no agent member runs the human's turn, and that is not an
    # agent session: it is not audited here.
    if agent_user_id:
        audit.record(store.engine, organization_id=org_id, action="agent.session.start",
                     actor_kind="agent", actor_id=agent_user_id, on_behalf_of=uid,
                     target_kind="conversation", target_id=conversation_id)
    try:
        with (tenant_context.actor("agent", agent_user_id, on_behalf_of=uid)
              if agent_user_id else nullcontext()):
            # Stream every model turn and tool execution: run_stream/stream_text stops after
            # the first text response, so iterating nodes streams both collapsed tool traces
            # and the final answer written after tool calls.
            async with agent.iter(prompt, message_history=history, deps=deps) as run:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as stream:
                            async for event in stream:
                                text = None
                                thinking = None
                                if isinstance(event, PartStartEvent):
                                    if isinstance(event.part, TextPart):
                                        text = event.part.content
                                    elif isinstance(event.part, ThinkingPart):
                                        thinking = event.part.content
                                elif isinstance(event, PartDeltaEvent):
                                    if isinstance(event.delta, TextPartDelta):
                                        text = event.delta.content_delta
                                    elif isinstance(event.delta, ThinkingPartDelta):
                                        thinking = event.delta.content_delta
                                if thinking:
                                    yield _event_chunk(completion_id, {"type": "thinking", "delta": thinking})
                                if text:
                                    reply_parts.append(text)
                                    yield _chunk(completion_id, {"content": text})
                    elif Agent.is_call_tools_node(node):
                        async with node.stream(run.ctx) as tool_stream:
                            async for event in tool_stream:
                                if isinstance(event, FunctionToolCallEvent):
                                    part = event.part
                                    if part.tool_name == "ask_user":
                                        continue
                                    args = format_tool_args(part.args)
                                    rec = {
                                        "id": part.tool_call_id,
                                        "name": part.tool_name,
                                        "args": args,
                                        "status": "running",
                                    }
                                    tool_records.append(rec)
                                    tool_by_id[part.tool_call_id] = rec
                                    tool_started[part.tool_call_id] = time.monotonic()
                                    yield _event_chunk(completion_id, {
                                        "type": "tool_start",
                                        "id": part.tool_call_id,
                                        "name": part.tool_name,
                                        "args": args,
                                    })
                                elif isinstance(event, FunctionToolResultEvent):
                                    part = event.part
                                    cid = getattr(part, "tool_call_id", None)
                                    rec = tool_by_id.get(cid) if cid else None
                                    if rec is None:
                                        continue
                                    started = tool_started.pop(cid, None)
                                    duration_ms = round((time.monotonic() - started) * 1000) if started is not None else None
                                    status, output, err = format_tool_result(rec["name"], part)
                                    rec["status"] = status
                                    if output:
                                        rec["output"] = output
                                    if err:
                                        rec["error"] = err
                                    if duration_ms is not None:
                                        rec["durationMs"] = duration_ms
                                    yield _event_chunk(completion_id, {
                                        "type": "tool_end",
                                        "id": rec["id"],
                                        "name": rec["name"],
                                        "status": status,
                                        "output": output,
                                        "error": err,
                                        "durationMs": duration_ms,
                                    })
            if run.result is not None and isinstance(run.result.output, AskUser):
                block = ask_block(run.result.output)
                reply_parts.append(block)
                yield _chunk(completion_id, {"content": block})
            if deps.delegations:
                cards = delegation_blocks(deps.delegations)
                reply_parts.append(cards)
                yield _chunk(completion_id, {"content": cards})
    except UnexpectedModelBehavior:
        reply_parts = ["The assistant could not complete that request. Please try again."]
        yield _chunk(completion_id, {"content": reply_parts[0]})
    finally:
        if agent_user_id:
            audit.record(store.engine, organization_id=org_id, action="agent.session.stop",
                         actor_kind="agent", actor_id=agent_user_id, on_behalf_of=uid,
                         target_kind="conversation", target_id=conversation_id)
    with store.engine.begin() as conn:
        save_message(
            conn,
            conversation_id,
            "assistant",
            "".join(reply_parts),
            tool_calls=serialize_tool_calls(tool_records),
        )
    yield _chunk(completion_id, {}, "stop")
    yield "data: [DONE]\n\n"


def sse_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
