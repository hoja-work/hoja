"""Chat delegation end to end: a real POST /api/agent/chat, a model on the wire, rows in the database.

test_chat_delegation.py drives the tool from inside Python. This file goes through the endpoint the
browser calls, against an OpenAI-compatible stub that answers the first request with a `delegate`
tool call and the second with text, and records every request it was sent. It then reads three
things nobody in between can fake: what the model was offered, the SSE bytes the client got, and the
agent_session / agent_message rows. Run plainly, with HOJA_AGENT_DBOS_URL (durable turn), and under
HOJA_TEST_RLS.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import select

pytest.importorskip("pydantic_ai")

from starlette.testclient import TestClient  # noqa: E402

from hoja_server.db import agent_message, agent_session  # noqa: E402
from tests.test_agent_chat import CREATE, SECRET, _gql, _setup  # noqa: E402

BLOCK = re.compile(r"```hoja-delegation\n(.*?)\n```", re.S)


class _DelegatingModel(BaseHTTPRequestHandler):
    """First turn: call `delegate`. Once a tool result is in the conversation: say so in text."""
    requests: list[dict] = []
    call: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        _DelegatingModel.requests.append(body)
        answered = any(m.get("role") == "tool" for m in body.get("messages", []))
        if answered:
            message = {"role": "assistant", "content": "Scout has it."}
            finish = "stop"
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "delegate", "arguments": json.dumps(_DelegatingModel.call)}}]}
            finish = "tool_calls"
        self.send_response(200)
        if body.get("stream"):
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            delta = dict(message)
            if "tool_calls" in delta:
                delta["tool_calls"] = [dict(delta["tool_calls"][0], index=0)]
            for choice in ({"index": 0, "delta": delta, "finish_reason": None},
                           {"index": 0, "delta": {}, "finish_reason": finish}):
                chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0,
                         "model": body["model"], "choices": [choice]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def model_url():
    _DelegatingModel.requests = []
    server = HTTPServer(("127.0.0.1", 0), _DelegatingModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture(params=["streaming", "durable"])
def turn_mode(request, monkeypatch, tmp_path):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_ALLOW_INSECURE_MODEL_URL", "1")
    if request.param == "durable":
        pytest.importorskip("dbos")
        monkeypatch.setenv("HOJA_AGENT_DBOS_URL", f"sqlite:///{tmp_path / 'dbos.sqlite'}")
    else:
        monkeypatch.delenv("HOJA_AGENT_DBOS_URL", raising=False)
    return request.param


def _content(sse: str) -> str:
    return "".join(json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                   for line in sse.splitlines() if line.startswith("data: {"))


def test_a_chat_turn_delegates_through_the_endpoint(tmp_path, model_url, turn_mode):
    engine, app, admin = _setup(tmp_path)
    token, auth = admin["token"], {"Authorization": f"Bearer {admin['token']}"}
    with TestClient(app) as client:
        assert _gql(client, token, CREATE, {"k": SECRET, "u": model_url})["data"]
        agent = _gql(client, token, '''mutation { agentMemberCreate(input: {
            name: "Scout", capacity: 2, monthlyBudgetCredits: 500 }) { agentMember { userId } } }''', {})
        agent_uid = agent["data"]["agentMemberCreate"]["agentMember"]["userId"]
        team = _gql(client, token, "{ teams { nodes { id } } }", {})["data"]["teams"]["nodes"][0]["id"]
        issue = _gql(client, token, 'mutation($t: String!) { issueCreate(input: { teamId: $t, title: "Rotate certs" }) '
                                    '{ issue { id identifier } } }', {"t": team})["data"]["issueCreate"]["issue"]
        _DelegatingModel.call = {"issue": issue["identifier"], "agent_user_id": agent_uid}

        res = client.post("/api/agent/chat", headers=auth,
                          json={"messages": [{"role": "user", "content": f"Have Scout take {issue['identifier']}"}]})
        assert res.status_code == 200, res.text
        assert res.text.rstrip().endswith("data: [DONE]")

    # 1. The model was offered the tool, and it was sent the tool's real answer on the second call.
    offered = {t["function"]["name"] for t in _DelegatingModel.requests[0].get("tools", [])}
    assert "delegate" in offered, offered
    assert len(_DelegatingModel.requests) == 2
    tool_msg = next(m for m in _DelegatingModel.requests[1]["messages"] if m.get("role") == "tool")
    tool_answer = json.loads(tool_msg["content"])
    assert tool_answer["data"]["issueUpdate"]["success"] is True, tool_answer

    # 2. The client received exactly one card, for this task and the session the server opened.
    streamed = _content(res.text)
    cards = [json.loads(b) for b in BLOCK.findall(streamed)]
    assert len(cards) == 1, streamed
    assert "Scout has it." in streamed

    # 3. The database: one session for this task, the one on the card; the saved reply carries the card.
    with engine.connect() as conn:
        sessions = conn.execute(select(agent_session).where(agent_session.c.issue_id == issue["id"])).all()
        replies = [r.content for r in conn.execute(
            select(agent_message.c.content).where(agent_message.c.role == "assistant"))]
    assert len(sessions) == 1
    session = sessions[0]._mapping
    assert session["agent_user_id"] == agent_uid and session["trigger"] == "delegation"
    assert session["creator_id"] == admin["user_id"]
    assert cards[0] == {"issueId": issue["id"], "identifier": issue["identifier"], "title": "Rotate certs",
                        "agentName": "Scout", "sessionId": session["id"]}
    assert len(replies) == 1 and len(BLOCK.findall(replies[0])) == 1, replies
    assert json.loads(BLOCK.findall(replies[0])[0]) == cards[0]
