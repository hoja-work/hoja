"""The chat's `delegate` tool is the task page's Delegate field, reached from a conversation.

Each test runs the real tool inside a real chat agent (a scripted model, so no provider is
needed), against the real schema and writer, with the tenant and actor bound the way
`agent_chat.stream_reply` binds them. The task-page path is the same mutation sent over HTTP, so
the two can be compared row for row. Under HOJA_TEST_RLS the tool runs as the restricted role.
"""
from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import and_, func, select
from starlette.testclient import TestClient

pytest.importorskip("pydantic_ai")

from hoja_server import agent_members, tenant_context  # noqa: E402
from hoja_server.agent_chat import DELEGATION_FENCE, AgentDeps, build_agent, delegation_blocks  # noqa: E402
from hoja_server.app import create_app  # noqa: E402
from hoja_server.db import agent_session, change_event, make_engine, seed_tenant  # noqa: E402
from hoja_server.resolvers import build_schema  # noqa: E402
from hoja_server.schema_sdl import SCHEMA_SDL  # noqa: E402
from hoja_server.store import Store  # noqa: E402
from hoja_server.writer import Writer  # noqa: E402
from tests import rls_mode  # noqa: E402


def _post(client: TestClient, token: str, query: str) -> dict:
    body = client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "chat-delegation-master")
    owner, kwargs = rls_mode.workspace(tmp_path, "chat-delegation")
    seeded = seed_tenant(owner, org_name="Delegating", org_url_key="delegating",
                         user_email="admin@delegating.test", token="deleg-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False)
    client = TestClient(app)
    # The tool runs on the engine the app runs on: the restricted role under RLS.
    app_engine = make_engine(kwargs["database_url"]) if kwargs.get("database_url") else owner
    agent = _post(client, seeded["token"], '''mutation { agentMemberCreate(input: {
        name: "Scout", capacity: 4, monthlyBudgetCredits: 500 }) { agentMember { userId } } }''')
    return {"owner": owner, "engine": app_engine, "client": client, "seeded": seeded,
            "agent_uid": agent["agentMemberCreate"]["agentMember"]["userId"]}


def _task(ws, title: str) -> dict:
    data = _post(ws["client"], ws["seeded"]["token"], f'''mutation {{
        issueCreate(input: {{ teamId: "{ws["seeded"]["team_id"]}", title: {json.dumps(title)} }}) {{
          issue {{ id identifier }} }} }}''')
    return data["issueCreate"]["issue"]


def _chat_delegate(ws, issue: str, agent_uid: str, *, access: str = "read_write"):
    """One chat turn whose model calls `delegate`, bound like agent_chat.stream_reply."""
    seen = {}

    def scripted(messages, info):
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("delegate", {"issue": issue, "agent_user_id": agent_uid})])
        seen["tool_result"] = messages[-1].parts[0].content
        return ModelResponse(parts=[TextPart("Handed over.")])

    from pydantic_ai.models.function import FunctionModel

    seeded, engine = ws["seeded"], ws["engine"]
    context = {"request": None, "store": Store(engine), "writer": Writer(engine),
               "org_id": seeded["org_id"], "user_id": seeded["user_id"], "identity_id": None,
               "browser_session": False, "access": access}
    deps = AgentDeps(build_schema(), context)
    turn_agent = agent_members.turn_agent(ws["owner"], seeded["org_id"])
    tenant_context.enter(seeded["org_id"], seeded["user_id"])
    try:
        with tenant_context.actor("agent", turn_agent, on_behalf_of=seeded["user_id"]):
            build_agent(FunctionModel(scripted), SCHEMA_SDL).run_sync("delegate it", deps=deps)
    finally:
        tenant_context.bind(None)
    return deps, seen["tool_result"], turn_agent


def _sessions(ws, issue_id: str) -> list[dict]:
    with ws["owner"].connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            select(agent_session).where(agent_session.c.issue_id == issue_id))]


def _delegate_change(ws, issue_id: str) -> dict | None:
    with ws["owner"].connect() as conn:
        row = conn.execute(select(change_event).where(and_(
            change_event.c.entity == "issue", change_event.c.entity_id == issue_id,
            change_event.c.action == "update")).order_by(change_event.c.seq.desc()).limit(1)).first()
    return dict(row._mapping) if row else None


def test_chat_delegation_opens_one_session_exactly_like_the_task_page(workspace):
    ws = workspace
    from_chat, from_page = _task(ws, "From chat"), _task(ws, "From the task page")

    _post(ws["client"], ws["seeded"]["token"], f'''mutation {{
        issueUpdate(id: "{from_page["id"]}", input: {{ delegateId: "{ws["agent_uid"]}" }}) {{ success }} }}''')
    deps, tool_result, _turn_agent = _chat_delegate(ws, from_chat["identifier"], ws["agent_uid"])

    assert tool_result["data"]["issueUpdate"]["success"] is True
    chat_rows, page_rows = _sessions(ws, from_chat["id"]), _sessions(ws, from_page["id"])
    assert len(chat_rows) == 1 and len(page_rows) == 1
    chat_s, page_s = chat_rows[0], page_rows[0]
    for field in ("agent_user_id", "trigger", "state", "creator_id"):
        assert chat_s[field] == page_s[field], field
    assert chat_s["agent_user_id"] == ws["agent_uid"]
    assert chat_s["trigger"] == "delegation"
    # The person who asked is the session's creator on both paths.
    assert chat_s["creator_id"] == ws["seeded"]["user_id"]

    # The tool's answer carries the session it opened, and the reply gets one card for it.
    session = tool_result["data"]["issueUpdate"]["issue"]["agentSession"]
    assert session["id"] == chat_s["id"]
    assert deps.delegations == [{"issueId": from_chat["id"], "identifier": from_chat["identifier"],
                                 "title": "From chat", "agentName": "Scout", "sessionId": chat_s["id"]}]


def test_the_chat_write_is_attributed_to_the_agent_on_behalf_of_the_person(workspace):
    ws = workspace
    if ws["engine"].dialect.name != "postgresql":
        pytest.skip("change_event records the actor on Postgres only (SQLite writes NULL)")
    task = _task(ws, "Attributed")
    _deps, _result, turn_agent = _chat_delegate(ws, task["id"], ws["agent_uid"])
    change = _delegate_change(ws, task["id"])
    assert change is not None
    assert (change["actor_kind"], change["actor_id"], change["on_behalf_of"]) == (
        "agent", turn_agent, ws["seeded"]["user_id"])


def test_a_non_agent_delegate_is_refused_by_the_same_check_and_opens_nothing(workspace):
    ws = workspace
    task = _task(ws, "Not an agent")
    deps, tool_result, _ = _chat_delegate(ws, task["id"], ws["seeded"]["user_id"])
    # The writer's own refusal (InvalidReferenceError), not a schema error about the field.
    [error] = tool_result["errors"]
    assert error["message"] == "Invalid reference", error
    assert error["extensions"] == {"code": "BAD_USER_INPUT", "field": "delegateId"}, error
    assert _sessions(ws, task["id"]) == []
    assert deps.delegations == []


def test_a_read_only_key_cannot_delegate_from_chat(workspace):
    ws = workspace
    task = _task(ws, "Read only")
    deps, tool_result, _ = _chat_delegate(ws, task["id"], ws["agent_uid"], access="read")
    assert tool_result["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    assert _sessions(ws, task["id"]) == []
    assert deps.delegations == []


def test_one_card_per_task_and_the_latest_delegation_wins():
    blocks = delegation_blocks([
        {"issueId": "a", "identifier": "T-1", "title": "A", "agentName": "Scout", "sessionId": "s1"},
        {"issueId": "b", "identifier": "T-2", "title": "B", "agentName": "Scout", "sessionId": "s2"},
        {"issueId": "a", "identifier": "T-1", "title": "A", "agentName": "Ranger", "sessionId": "s3"},
    ])
    cards = [json.loads(m) for m in re.findall(rf"```{DELEGATION_FENCE}\n(.*?)\n```", blocks)]
    assert [(c["issueId"], c["sessionId"]) for c in cards] == [("a", "s3"), ("b", "s2")]


def test_a_session_count_never_grows_on_a_repeated_chat_delegation(workspace):
    ws = workspace
    task = _task(ws, "Twice")
    _chat_delegate(ws, task["id"], ws["agent_uid"])
    _chat_delegate(ws, task["id"], ws["agent_uid"])
    with ws["owner"].connect() as conn:
        live = conn.execute(select(func.count()).select_from(agent_session).where(and_(
            agent_session.c.issue_id == task["id"],
            agent_session.c.state.not_in(("done", "failed", "cancelled"))))).scalar_one()
    assert live == 1
