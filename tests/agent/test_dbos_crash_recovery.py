"""DBOS crash recovery: a workflow interrupted by SIGKILL finishes after restart."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

POSTGRES_URL = os.environ.get("CLINIAR_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="set CLINIAR_TEST_POSTGRES_URL to run")

WORKER = textwrap.dedent(
    """
    import os, sys, time
    from dbos import DBOS, SetWorkflowID

    DBOS(config={"name": "cliniar-agent-test", "system_database_url": os.environ["DB_URL"]})

    gate = os.environ["GATE"]

    @DBOS.step()
    def slow_step(value: str) -> str:
        while not os.path.exists(gate):
            time.sleep(0.05)
        return value.upper()

    @DBOS.workflow()
    def agent_run(value: str) -> str:
        return slow_step(value)

    DBOS.launch()
    if sys.argv[1] == "start":
        with SetWorkflowID("crash-run-1"):
            handle = DBOS.start_workflow(agent_run, "hello")
        print("started", flush=True)
        time.sleep(600)
    else:
        handle = DBOS.retrieve_workflow("crash-run-1")
        print("result=" + handle.get_result(), flush=True)
        DBOS.destroy()
    """
)


def _run(script: Path, mode: str, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(script), mode],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_interrupted_workflow_completes_after_sigkill(tmp_path: Path) -> None:
    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    gate = tmp_path / "gate"
    env = {**os.environ, "DB_URL": POSTGRES_URL, "GATE": str(gate)}

    first = _run(script, "start", env)
    try:
        deadline = time.time() + 60
        assert first.stdout is not None
        while True:
            line = first.stdout.readline()
            if "started" in line:
                break
            assert time.time() < deadline and line, "first process never started the workflow"
        time.sleep(2)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
    finally:
        if first.poll() is None:
            first.kill()

    gate.touch()
    second = _run(script, "resume", env)
    out, _ = second.communicate(timeout=120)
    assert second.returncode == 0, out
    assert "result=HELLO" in out, out
