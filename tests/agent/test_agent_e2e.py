"""A delegated task, end to end, with every real part (HOJA-602 e2e loop).

Three processes, none of them faked:

* the Hoja server (``create_app`` under uvicorn, on a real socket);
* ``hoja-runnerd``, started as its own process from ``runner/`` exactly as an operator runs it;
* ``hoja-coder``, the headless harness the daemon starts, which is Pydantic AI Harness's
  ``Coder`` with ``AskUser``. Only the *model* is scripted (``runner/tests/coder_scripted_model.py``),
  so the run needs no network and no API key.

The path under test is the one the architecture review found broken in four places::

    delegate -> schedule admits -> lease -> runner spawns the harness (session running)
      -> the agent asks a question (session awaiting_input, elicitation on the transcript)
      -> a person answers through GraphQL -> the heartbeat carries it -> the agent goes on
      -> typed result (answer + acceptance) -> finish -> automatic verification (met)
      -> task In Review, the agent's reply posted on the task, usage metered

Skipped unless the runner's ``coder`` environment exists (``runner/.venv`` with
``pydantic-ai-harness``, or ``HOJA_CODER_PYTHON``): ``cd runner && uv venv -p 3.12 &&
uv pip install -e '.[coder]'``.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from hoja_server import runner_api
from hoja_server.app import create_app
from hoja_server.db import comment, seed_tenant, usage_event
from tests import rls_mode

uvicorn = pytest.importorskip("uvicorn")

RUNNER_ROOT = Path(__file__).resolve().parents[3] / "runner"
CODER_PYTHON = os.environ.get("HOJA_CODER_PYTHON") or str(RUNNER_ROOT / ".venv" / "bin" / "python")


def _coder_available() -> bool:
    if not Path(CODER_PYTHON).exists():
        return False
    probe = subprocess.run([CODER_PYTHON, "-c", "import pydantic_ai_harness"],
                           capture_output=True)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(
    not _coder_available(),
    reason="needs the runner's coder environment (runner/.venv with pydantic-ai-harness)")

ACCEPTANCE = ["a database is chosen", "the choice is written to choice.txt"]

#: Opt-in live run: a real model instead of the scripted one, e.g.
#: ``HOJA_E2E_LIVE_MODEL=openai:luna OPENAI_BASE_URL=... OPENAI_API_KEY=...``.
LIVE_MODEL = os.environ.get("HOJA_E2E_LIVE_MODEL")
DESCRIPTION = (
    "Decide which database the new service uses. You do not know the answer: ask the person "
    "with your ask_user_question tool, offering exactly two options, Postgres and SQLite, under "
    "the header Database. Then write their choice (just the word) to choice.txt in your "
    "workspace.")


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # it runs in a thread
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "agent-e2e-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "agent-e2e")
    tenant = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                         user_email="admin@alpha.test", token="alpha-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    port = _free_port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the server did not start"
    yield {"engine": engine, "tenant": tenant, "base": f"http://127.0.0.1:{port}",
           "tmp": tmp_path}
    server.should_exit = True
    thread.join(timeout=10)


def _gql(live, query: str) -> dict:
    import httpx

    response = httpx.post(f"{live['base']}/graphql", json={"query": query},
                          headers={"Authorization": f"Bearer {live['tenant']['token']}"},
                          timeout=30)
    body = response.json()
    assert "errors" not in body, body
    return body["data"]


def _session(live, issue_id: str) -> dict:
    return _gql(live, f'''query {{ issue(id: "{issue_id}") {{
        state {{ name }}
        agentSession {{ id state verdict workerModel
                       activities(first: 100) {{ kind body payload authorKind }} }} }} }}''')["issue"]


def _wait(predicate, *, timeout_s: float, what: str, every: float = 0.2):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}; last: {last!r}")


def test_a_delegated_task_reaches_in_review_through_a_real_runner_and_harness(live):
    tenant = live["tenant"]
    model = LIVE_MODEL or "scripted-coder"
    agent = _gql(live, f'''mutation {{ agentMemberCreate(input: {{
        name: "Coder", capacity: 1, model: "{model}" }}) {{
        agentMember {{ id userId }} }} }}''')["agentMemberCreate"]["agentMember"]
    _gql(live, f'''mutation {{ agentPolicyUpdate(agentMemberId: "{agent["id"]}",
        input: {{ teams: ["{tenant["team_id"]}"], defaultHarness: "hoja-coder" }})
        {{ success }} }}''')
    description = DESCRIPTION + "\n\n## Acceptance\n" + "\n".join(f"- {i}" for i in ACCEPTANCE)
    task = _gql(live, f'''mutation {{ issueCreate(input: {{ teamId: "{tenant["team_id"]}",
        title: "Choose a database", delegateId: "{agent["userId"]}",
        description: {json.dumps(description)} }}) {{ issue {{ id identifier }} }} }}''')[
        "issueCreate"]["issue"]

    _row, enrollment = runner_api.mint_enrollment_token(
        live["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    import httpx

    identity = httpx.post(f"{live['base']}/runner/register", json={
        "name": "e2e-runner", "enrollment_token": enrollment,
        "harnesses": ["hoja-coder"]}, timeout=30).json()

    state_dir = live["tmp"] / "runner-state"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(live["tmp"]),
        "HOJA_RUNNER_ID": identity["runner_id"],
        "HOJA_RUNNER_TOKEN": identity["token"],
        "HOJA_CODER_PYTHON": CODER_PYTHON,
        "PYTHONPATH": f"{RUNNER_ROOT}:{RUNNER_ROOT / 'tests'}",
    }
    passthrough = ["PYTHONPATH"]
    if LIVE_MODEL:
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY"):
            if os.environ.get(name):
                env[name] = os.environ[name]
                passthrough.append(name)
    else:
        env["HOJA_CODER_MODEL_FACTORY"] = "coder_scripted_model:model"
        passthrough.append("HOJA_CODER_MODEL_FACTORY")
    log_path = live["tmp"] / "runnerd.log"
    with open(log_path, "w") as log:
        daemon = subprocess.Popen(
            [CODER_PYTHON, "-m", "hoja_runnerd.main", "--base-url", live["base"],
             "--state-dir", str(state_dir), "--max-leases", "1", "--no-herdr",
             "--allow-harness", "hoja-coder", "--keep-workdirs",
             *[arg for name in passthrough for arg in ("--env-passthrough", name)]],
            cwd=str(RUNNER_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        # The agent asks; the session says so and the question is on its transcript.
        waiting = _wait(lambda: (lambda s: s if s["agentSession"]["state"] == "awaiting_input"
                                 else None)(_session(live, task["id"])),
                        timeout_s=300 if LIVE_MODEL else 60, what="the agent's question")
        question = [a for a in waiting["agentSession"]["activities"] if a["kind"] == "elicitation"][-1]
        assert question["payload"]["questions"][0]["header"] == "Database"
        session_id = waiting["agentSession"]["id"]

        # A person answers on the agent screen's mutation; the live run gets it.
        response = json.dumps({"answers": [{"header": "Database", "selected": ["Postgres"]}]})
        answered = _gql(live, f'''mutation {{ agentSessionPrompt(id: "{session_id}",
            body: "Postgres", response: {json.dumps(response)}) {{ agentSession {{ state }} }} }}''')
        assert answered["agentSessionPrompt"]["agentSession"]["state"] == "running"

        # The run finishes, the platform verifies the claim, the task moves to In Review.
        done = _wait(lambda: (lambda s: s if s["state"]["name"] == "In Review" else None)(
            _session(live, task["id"])), timeout_s=600 if LIVE_MODEL else 60, what="In Review")
        assert done["agentSession"]["state"] == "in_review"
        assert done["agentSession"]["verdict"] == "met"
        assert done["agentSession"]["workerModel"] == model
        kinds = [a["kind"] for a in done["agentSession"]["activities"]]
        assert "action" in kinds and "elicitation" in kinds and "prompt" in kinds
        bodies_ = [a["body"] for a in done["agentSession"]["activities"]]
        assert bodies_.count("Started.") == 1, "an answer is not a second start"

        # The agent wrote the file in its own workspace, with the answer it was given.
        written = list((state_dir / "work").glob("*/choice.txt"))
        assert written and written[0].read_text().strip() == "Postgres"

        # Its reply is on the task, and the run was metered from the harness's own usage.
        with live["engine"].connect() as conn:
            bodies = conn.execute(select(comment.c.body).where(
                comment.c.issue_id == task["id"])).scalars().all()
            metered = conn.execute(select(usage_event).where(
                usage_event.c.organization_id == tenant["org_id"])).mappings().all()
        print("TRANSCRIPT", json.dumps([{k: a[k] for k in ("kind", "authorKind", "body")}
                                        for a in done["agentSession"]["activities"]], indent=1))
        print("COMMENTS", json.dumps(bodies, indent=1))
        assert any("Postgres" in (body or "") for body in bodies), bodies
        assert metered and any(row["input_tokens"] for row in metered)
        assert daemon.wait(timeout=30) == 0
    finally:
        if daemon.poll() is None:
            daemon.kill()
        print(log_path.read_text()[-3000:])
