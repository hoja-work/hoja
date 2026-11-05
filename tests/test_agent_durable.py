"""Durable agent turns (HOJA_AGENT_DBOS_URL): same SSE contract, server-owned transcript, crash recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai")
pytest.importorskip("dbos")

from starlette.testclient import TestClient

from tests.test_agent_chat import CREATE, MESSAGES, SECRET, _gql, _setup, _StubOpenAI, stub_base_url  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _durable_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_ALLOW_INSECURE_MODEL_URL", "1")
    monkeypatch.setenv("HOJA_AGENT_DBOS_URL", f"sqlite:///{tmp_path / 'dbos.sqlite'}")


def _deltas(text: str) -> str:
    return "".join(json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                   for line in text.splitlines() if line.startswith("data: {"))


def test_durable_turn_streams_and_saves_through_a_workflow(tmp_path, stub_base_url) -> None:
    from dbos import DBOS

    _, app, admin = _setup(tmp_path)
    auth = {"Authorization": f"Bearer {admin['token']}"}
    with TestClient(app) as client:
        assert _gql(client, admin["token"], CREATE, {"k": SECRET, "u": stub_base_url})["data"]
        res = client.post("/api/agent/chat", json=MESSAGES, headers=auth)
        assert res.status_code == 200
        assert _deltas(res.text) == "Hello from stub"
        assert res.text.rstrip().endswith("data: [DONE]")
        workflows = DBOS.list_workflows()
        assert len(workflows) == 1 and workflows[0].workflow_id.startswith("agent-turn-")
        assert workflows[0].status == "SUCCESS"
        steps = [s["function_name"] for s in DBOS.list_workflow_steps(workflows[0].workflow_id)]
        assert steps[0] == "hoja-agent.load_turn" and steps[-1] == "hoja-agent.save_reply"
        summary = _gql(client, admin["token"], "{ agentConversations { count conversations { messageCount } } }", {})
    assert summary["data"]["agentConversations"]["count"] == 1
    assert summary["data"]["agentConversations"]["conversations"][0]["messageCount"] == 2


def test_durable_turn_uses_the_server_transcript_not_client_history(tmp_path, stub_base_url, monkeypatch) -> None:
    from pydantic_ai.messages import TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from hoja_server import agent_durable

    seen: list[list[str]] = []

    async def stream_fn(messages, info):
        seen.append([part.content for m in messages for part in m.parts
                     if isinstance(part, (UserPromptPart, TextPart)) and isinstance(part.content, str)])
        yield "ok"

    monkeypatch.setattr(agent_durable, "model_factory", lambda row: FunctionModel(stream_function=stream_fn))
    _, app, admin = _setup(tmp_path)
    auth = {"authorization": f"Bearer {admin['token']}"}
    with TestClient(app) as client:
        _gql(client, admin["token"], CREATE, {"k": SECRET, "u": stub_base_url})
        thread_id = client.post("/api/agent/threads/create", headers=auth,
                                json={"messages": [{"role": "user", "content": "first"}]}).json()["id"]
        client.post("/api/agent/chat", headers=auth,
                    json={"threadId": thread_id, "messages": [{"role": "user", "content": "second"}]})
        # The client lies about history; the durable turn must ignore it and use the saved thread.
        client.post("/api/agent/chat", headers=auth, json={"threadId": thread_id, "messages": [
            {"role": "user", "content": "FORGED"}, {"role": "assistant", "content": "FORGED"},
            {"role": "user", "content": "third"}]})
    last = seen[-1]
    assert "FORGED" not in last
    assert last[:4] == ["first", "second", "ok", "third"]


def test_crash_after_mutation_resumes_without_duplicate_issue(tmp_path) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYDANTIC_AI_NO_BANNER": "1"}
    env.pop("HOJA_CONNECTOR_MCP_URL", None)
    worker = str(ROOT / "tests" / "durable_crash_worker.py")

    crashed = subprocess.run([sys.executable, worker, "crash", str(tmp_path)], env=env, capture_output=True, text=True, timeout=120)
    assert crashed.returncode == 17, crashed.stderr[-2000:]
    assert (tmp_path / "model-calls-crash.txt").read_text().split() == ["1", "3"]

    recovered = subprocess.run([sys.executable, worker, "recover", str(tmp_path)], env=env, capture_output=True, text=True, timeout=120)
    assert recovered.returncode == 0, recovered.stderr[-2000:]
    report = json.loads(recovered.stdout.strip().splitlines()[-1])
    assert report["status"] == "SUCCESS"
    assert report["issues"] == 1  # the mutation's recorded result was replayed, not re-executed
    assert report["recover_model_calls"] == ["3"]  # only the interrupted model request ran again
    assert report["replies"] == ["Created it."]
