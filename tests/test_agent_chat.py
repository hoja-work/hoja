"""Agent chat endpoint: auth, no-model message, and streamed reply from an admin-configured provider."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")
pytest.importorskip("pydantic_ai")

from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant

CREATE = (
    "mutation($k: String!, $u: String!) { modelProviderCreate(name: \"Stub\", provider: \"openai\", "
    "modelId: \"stub-model\", baseUrl: $u, apiKey: $k) { id } }"
)
SECRET = "sk-stub-chat-key"
MESSAGES = {"messages": [{"role": "user", "content": "hi"}]}


class _StubOpenAI(BaseHTTPRequestHandler):
    seen_auth: list[str] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length))
        _StubOpenAI.seen_auth.append(self.headers.get("authorization", ""))
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        pieces = ["Hello", " from", " stub"]
        for piece in pieces:
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                     "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        end = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_base_url():
    server = HTTPServer(("127.0.0.1", 0), _StubOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _setup(tmp_path):
    db_path = tmp_path / "chat.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Chat Org", org_url_key="chat-org",
                        user_email="owner@chat.test", token="owner-token", demo_issues=False)
    return engine, create_app(str(db_path), open_mode=False), admin


def _gql(client, token, query, variables):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables}).json()


def test_requires_authentication(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/agent/chat", json=MESSAGES).status_code == 401


def test_without_configured_model_says_so(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.post("/api/agent/chat", json=MESSAGES, headers={"Authorization": f"Bearer {admin['token']}"})
        assert res.status_code == 200
        assert "No model is configured" in res.text
        assert res.text.rstrip().endswith("data: [DONE]")


def test_streams_reply_from_admin_configured_provider(tmp_path, stub_base_url) -> None:
    _, app, admin = _setup(tmp_path)
    _StubOpenAI.seen_auth.clear()
    with TestClient(app) as client:
        assert _gql(client, admin["token"], CREATE, {"k": SECRET, "u": stub_base_url})["data"]
        res = client.post("/api/agent/chat", json=MESSAGES, headers={"Authorization": f"Bearer {admin['token']}"})
    assert res.status_code == 200
    deltas = [json.loads(line[6:])["choices"][0]["delta"].get("content", "")
              for line in res.text.splitlines() if line.startswith("data: {")]
    assert "".join(deltas) == "Hello from stub"
    assert res.text.rstrip().endswith("data: [DONE]")
    assert _StubOpenAI.seen_auth == [f"Bearer {SECRET}"]


def test_rejects_request_without_user_message(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.post("/api/agent/chat", json={"messages": [{"role": "assistant", "content": "x"}]},
                          headers={"Authorization": f"Bearer {admin['token']}"})
        assert res.status_code == 400
        assert "Last message must be a user message" in res.json()["error"]



def test_agent_recovers_from_empty_graphql_call(tmp_path) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from cliniar_server.agent_chat import AgentDeps, build_agent
    from cliniar_server.resolvers import build_schema
    from cliniar_server.schema_sdl import SCHEMA_SDL
    from cliniar_server.store import Store

    engine, _app, admin = _setup(tmp_path)
    calls = []

    def scripted(messages, info: AgentInfo):
        if len(calls) < 2:
            calls.append("empty")
            return ModelResponse(parts=[ToolCallPart("graphql", {})])
        if len(calls) == 2:
            calls.append("valid")
            return ModelResponse(parts=[ToolCallPart("graphql", {"query": "{ viewer { id } }", "variables": {}})])
        return ModelResponse(parts=[TextPart("done")])

    context = {
        "store": Store(engine), "writer": None, "org_id": admin["org_id"],
        "user_id": admin["user_id"], "identity_id": None, "browser_session": False,
    }
    agent = build_agent(FunctionModel(scripted), SCHEMA_SDL)
    result = agent.run_sync("who am i", deps=AgentDeps(build_schema(), context))
    assert result.output == "done"
    assert calls == ["empty", "empty", "valid"]


def test_agent_graphql_tool_runs_as_signed_in_user(tmp_path) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from cliniar_server.agent_chat import AgentDeps, build_agent
    from cliniar_server.resolvers import build_schema
    from cliniar_server.schema_sdl import SCHEMA_SDL
    from cliniar_server.store import Store

    engine, _app, admin = _setup(tmp_path)
    seen = {}

    def scripted(messages, info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("graphql", {"query": "{ viewer { id } }", "variables": {}})])
        seen["tool_result"] = str(messages[-1].parts[0].content)
        return ModelResponse(parts=[TextPart("done")])

    context = {
        "store": Store(engine), "writer": None, "org_id": admin["org_id"],
        "user_id": admin["user_id"], "identity_id": None, "browser_session": False,
    }
    agent = build_agent(FunctionModel(scripted), SCHEMA_SDL)
    agent.run_sync("who am i", deps=AgentDeps(build_schema(), context))
    assert admin["user_id"] in seen["tool_result"]
