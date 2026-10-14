"""End-to-end: the in-app agent calls ask_user through /api/agent/chat against a scripted model.

Malformed tool arguments must never end the run. Every case must stream a final reply, return one status
per question, and create exactly the Hoja issues its statuses claim. Re-running the same call creates nothing.
"""

from __future__ import annotations

import json
import random
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
TOKEN = "owner-token"
VALID = {"text": "Which region should we deploy to?", "options": ["EU", "US"], "context": "Affects latency."}


@pytest.fixture(autouse=True)
def _loopback_model(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("CLINIAR_ALLOW_INSECURE_MODEL_URL", "1")


class _Model:
    """Scripted OpenAI-compatible model. Each user turn streams one ask_user call with the next raw arguments
    string. After the tool result arrives, it streams the result back as its final answer."""

    def __init__(self, scripted_arguments: list[str]):
        self.scripted = list(scripted_arguments)
        self.tool_results: list[str] = []


def _serve(model: _Model) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            last = body["messages"][-1]
            if last["role"] == "tool":
                model.tool_results.append(last["content"])
                deltas, finish = [{"content": "RESULT " + last["content"]}], "stop"
            elif model.scripted:
                call = {"index": 0, "id": f"call_{len(model.tool_results)}", "type": "function",
                        "function": {"name": "ask_user", "arguments": model.scripted.pop(0)}}
                deltas, finish = [{"role": "assistant", "tool_calls": [call]}], "tool_calls"
            else:
                deltas, finish = [{"content": "no script left"}], "stop"
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for delta in deltas:
                self._write(body["model"], delta, None)
            self._write(body["model"], {}, finish)
            self.wfile.write(b"data: [DONE]\n\n")

        def _write(self, model_id, delta, finish):
            payload = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": model_id,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _gql(client, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()


def _issue_titles(client) -> list[str]:
    return [n["title"] for n in _gql(client, "{ issues(first: 200) { nodes { title } } }")["data"]["issues"]["nodes"]]


def _chat(client, text="Ask me about the deployment."):
    res = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": text}]},
                      headers={"Authorization": f"Bearer {TOKEN}"})
    reply = "".join(
        json.loads(line[6:])["choices"][0]["delta"].get("content", "")
        for line in res.text.splitlines() if line.startswith("data: {")
    )
    return res, reply


def _run(tmp_path, arguments: dict, chats: int = 1):
    """Run `chats` agent turns, each calling ask_user with `arguments`. Returns one dict per turn with the
    response, streamed reply, tool result, and Hoja issue titles before and after the turn."""
    model = _Model([json.dumps(arguments)] * chats)
    server = _serve(model)
    try:
        engine = make_engine(tmp_path / "ask.db")
        migrate(engine)
        seed_tenant(engine, org_name="Ask Org", org_url_key="ask-org", user_email="owner@ask.test",
                    token=TOKEN, demo_issues=False)
        app = create_app(str(tmp_path / "ask.db"), open_mode=False)
        base = f"http://127.0.0.1:{server.server_port}/v1"
        turns = []
        with TestClient(app) as client:
            assert _gql(client, CREATE, {"k": "sk-stub", "u": base})["data"]
            for _ in range(chats):
                before = _issue_titles(client)
                res, reply = _chat(client)
                after = _issue_titles(client)
                turns.append({"res": res, "reply": reply, "before": before, "after": after,
                              "tool": model.tool_results[-1] if model.tool_results else None})
        return turns
    finally:
        server.shutdown()


def _statuses(turn) -> list[dict]:
    return json.loads(turn["tool"])


def test_loose_json_string_questions_are_repaired(tmp_path) -> None:
    arguments = {"teamId": "eng", "questions": "[{'text': 'Which region?', 'options': ['EU', 'US',],}]"}
    (turn,) = _run(tmp_path, arguments)
    assert turn["res"].status_code == 200
    assert [s["status"] for s in _statuses(turn)] == ["created"]
    assert turn["after"] == turn["before"] + ["Which region?"]
    assert turn["reply"] == "RESULT " + turn["tool"]


def test_fenced_json_and_plain_string_question_are_accepted(tmp_path) -> None:
    (turn,) = _run(tmp_path, {"teamId": "ENG", "questions": "```json\n[\"Ship on Friday?\"]\n```"})
    assert [s["status"] for s in _statuses(turn)] == ["created"]
    assert "Ship on Friday?" in turn["after"]


def test_single_object_is_treated_as_one_question(tmp_path) -> None:
    (turn,) = _run(tmp_path, {"teamId": "ENG", "questions": {"text": "Keep the old name?"}})
    assert [s["status"] for s in _statuses(turn)] == ["created"]


def test_near_miss_extra_keys_still_create_the_issue(tmp_path) -> None:
    question = {"text": "Pick a colour", "options": ["red", "blue"], "priority": "high", "assignee": "nobody"}
    (turn,) = _run(tmp_path, {"teamId": "ENG", "questions": [question]})
    assert [s["status"] for s in _statuses(turn)] == ["created"]
    assert "Pick a colour" in turn["after"]


def test_unknown_team_fails_without_creating_anything(tmp_path) -> None:
    (turn,) = _run(tmp_path, {"teamId": "NOPE", "questions": [VALID]})
    (status,) = _statuses(turn)
    assert status["status"] == "failed" and "no team matches" in status["error"]
    assert turn["after"] == turn["before"]


def test_repeating_the_same_call_creates_nothing_new(tmp_path) -> None:
    first, second = _run(tmp_path, {"teamId": "ENG", "questions": [VALID]}, chats=2)
    assert [s["status"] for s in _statuses(first)] == ["created"]
    assert [s["status"] for s in _statuses(second)] == ["skipped"]
    assert second["after"] == first["after"]


def _fuzz_case(rng: random.Random) -> dict:
    bad_items = [None, 7, 3.5, True, [], {}, "", "   ", {"text": None}, {"text": 42}, {"text": "   "},
                 {"question": "Alternate key works?"}, {"text": "x" * 5000},
                 {"text": "line one\nline two \"quoted\" 'single'; DROP TABLE issue; --"},
                 {"text": "ask-user-key: 0123456789abcdef"}, {"text": "Options as string?", "options": "only one"},
                 {"text": "Options with junk?", "options": [None, 3, " ", "keep"]},
                 {"text": "Bad context?", "context": 99}, {"text": "Deep?", "options": [{"nested": ["x"]}]},
                 "plain string question", "  spaced   question  "]
    bad_values = ["[]", "{}", "not json at all", "[{'text': 'Loose quotes?',}]", "[{\"text\": \"unterminated",
                  "", None, 0, True, {"text": "single object"}, [], {}, [None], ["a" * 250], "[" * 5000]
    items = [dict(VALID, text=f"Question {i}?") for i in range(rng.randint(1, 3))]
    if rng.random() < 0.3:
        items.append(rng.choice(bad_items))
    rng.shuffle(items)
    questions = items
    if rng.random() < 0.2:
        questions = rng.choice(bad_values)
    if rng.random() < 0.1:
        questions = [dict(VALID, text=f"Bulk {i}?") for i in range(12)]
    team = rng.choice(["ENG", "eng", "  ENG ", "", "   ", "NOPE", "x" * 300])
    return {"teamId": team, "questions": questions}


@pytest.mark.parametrize("seed", range(40))
def test_fuzzed_bad_inputs_never_end_the_run(tmp_path, seed) -> None:
    arguments = _fuzz_case(random.Random(seed))
    first, second = _run(tmp_path, arguments, chats=2)
    context = f"seed={seed} args={json.dumps(arguments)[:300]}"

    for turn in (first, second):
        assert turn["res"].status_code == 200, context
        assert turn["res"].text.rstrip().endswith("data: [DONE]"), context
        assert turn["tool"] is not None, context
        assert turn["reply"] == "RESULT " + turn["tool"], context
        statuses = _statuses(turn)
        assert isinstance(statuses, list) and statuses, context
        for status in statuses:
            assert status["status"] in {"created", "skipped", "failed"}, context
            if status["status"] == "created":
                assert status["identifier"], context
            if status["status"] == "failed":
                assert isinstance(status["error"], str) and status["error"], context
        created = [s for s in statuses if s["status"] == "created"]
        assert len(turn["after"]) - len(turn["before"]) == len(created), context

    assert second["after"] == first["after"], context
