"""Agent operations over GraphQL: launch, inspect, debug (HOJA-602).

=============================================  ============================================
test                                           proves
=============================================  ============================================
launch_creates_and_delegates_in_one_call       agentRunLaunch -> issue, session, queued lease
a_run_is_debuggable_from_one_query             agentRun: prompt, result, session, effects and
                                               a timeline of runner events, runner logs with
                                               the traceback, and the server's finish note
run_logs_filter_by_machine_level_and_source    agentRunLogs narrows; lines the runner wrote
                                               while holding no lease are kept too
relaunching_a_finished_task_starts_a_new_run   same task, same agent, a new session
the_surface_is_admin_only                      a member is refused
=============================================  ============================================
"""
from __future__ import annotations

import json

from hoja_server.db import api_key, new_id, now_iso, token_hash, user
from tests.agent.test_auto_verify_and_pools import (  # noqa: F401 - `world` is a fixture
    _claim,
    _create_agent,
    _enroll,
    _gql,
    world,
)

RUN_FIELDS = """id runId state harness prompt summary finishState refusalReason
  result session { id state } effects { key kind }
  logs { source level kind message detail leaseId runnerName }"""


def _auth(identity: dict) -> dict:
    return {"Authorization": f"Bearer {identity['token']}"}


def _launch(world, agent: dict, **extra) -> dict:
    fields = {"agentMemberId": agent["id"], **extra}
    body = _gql(world, world["alpha"]["token"], f'''mutation {{ agentRunLaunch(input: {{
        {", ".join(f"{k}: {json.dumps(v)}" for k, v in fields.items())} }}) {{
        success queueReason issue {{ id identifier }} agentSession {{ id state }}
        lease {{ id state harness }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["agentRunLaunch"]


def test_launch_creates_and_delegates_in_one_call(world):
    agent = _create_agent(world)
    launched = _launch(world, agent, teamId=world["alpha"]["team_id"], title="Write the notes",
                       description="Summarise the week.")
    assert launched["success"] is True
    assert launched["issue"]["identifier"]
    assert launched["agentSession"]["state"] == "queued"
    assert launched["lease"]["state"] == "queued"
    assert launched["queueReason"] == []


def test_a_run_is_debuggable_from_one_query(world):
    agent = _create_agent(world)
    launched = _launch(world, agent, teamId=world["alpha"]["team_id"], title="Debug me")
    identity = _enroll(world)
    lease, _ = _claim(world, identity)
    client = world["client"]
    base = {"lease_id": lease["lease_id"], "run_id": lease["run_id"], "fence": lease["fence"],
            "token": lease["token"]}
    assert client.post("/runner/events", headers=_auth(identity), json={**base, "events": [
        {"seq": 1, "kind": "harness.spawned", "payload": {"backend": "herdr"}}]}).status_code == 200
    shipped = client.post("/runner/logs", headers=_auth(identity), json={
        "lease_id": lease["lease_id"], "lines": [{
            "level": "error", "logger": "hoja_runnerd.main", "message": "run broke: KeyError",
            "traceback": "Traceback (most recent call last):\n  ...\nKeyError: 'agent_status'"}]})
    assert shipped.status_code == 200 and shipped.json() == {"accepted": 1}
    finished = client.post("/runner/finish", headers=_auth(identity), json={
        **base, "state": "failed", "summary": "runner error: KeyError: 'agent_status'",
        "idempotency_key": f"{lease['run_id']}:1",
        "effects": [{"key": f"{lease['run_id']}:1", "kind": "claim", "payload": {}}]})
    assert finished.status_code == 200, finished.text

    body = _gql(world, world["alpha"]["token"],
                f'query {{ agentRun(id: "{lease["run_id"]}") {{ {RUN_FIELDS} }} }}')
    assert "errors" not in body, body
    run = body["data"]["agentRun"]
    assert run["id"] == lease["lease_id"] and run["finishState"] == "failed"
    assert run["summary"] == "runner error: KeyError: 'agent_status'"
    assert run["prompt"].endswith("Debug me")
    assert run["session"]["id"] == launched["agentSession"]["id"]
    assert run["effects"] == [{"key": f"{lease['run_id']}:1", "kind": "claim"}]
    kinds = [(line["source"], line["level"], line["kind"]) for line in run["logs"]]
    assert ("runner", "event", "harness.spawned") in kinds
    assert ("runner", "error", "hoja_runnerd.main") in kinds
    assert ("server", "error", "run.finish") in kinds
    error = next(line for line in run["logs"] if line["level"] == "error"
                 and line["source"] == "runner")
    assert "KeyError" in error["detail"]["traceback"]
    assert all(line["leaseId"] == lease["lease_id"] for line in run["logs"])


def test_run_logs_filter_by_machine_level_and_source(world):
    identity = _enroll(world)
    client = world["client"]
    assert client.post("/runner/logs", headers=_auth(identity), json={"lines": [
        {"level": "warning", "logger": "hoja_runnerd.herdr", "message": "herdr refused"},
        {"level": "info", "logger": "hoja_runnerd.main", "message": "polling"},
    ]}).status_code == 200
    body = _gql(world, world["alpha"]["token"], f'''query {{
        agentRunLogs(runnerId: "{identity["runner_id"]}", level: "warning,error",
                     source: "runner") {{ nodes {{ level message leaseId runnerName }} }} }}''')
    assert "errors" not in body, body
    nodes = body["data"]["agentRunLogs"]["nodes"]
    assert [n["message"] for n in nodes] == ["herdr refused"]
    assert nodes[0]["leaseId"] is None and nodes[0]["runnerName"]


def test_relaunching_a_finished_task_starts_a_new_run(world):
    agent = _create_agent(world)
    first = _launch(world, agent, teamId=world["alpha"]["team_id"], title="Again")
    stopped = _gql(world, world["alpha"]["token"], f'''mutation {{
        agentSessionStop(id: "{first["agentSession"]["id"]}") {{ success }} }}''')
    assert "errors" not in stopped, stopped
    again = _launch(world, agent, taskId=first["issue"]["identifier"])
    assert again["success"] is True
    assert again["agentSession"]["id"] != first["agentSession"]["id"]
    assert again["agentSession"]["state"] == "queued"


def test_the_surface_is_admin_only(world):
    org = world["alpha"]["org_id"]
    stamp = now_iso()
    member_id = new_id()
    with world["engine"].begin() as conn:
        conn.execute(user.insert().values(id=member_id, organization_id=org, name="Sam",
                                          display_name="Sam", email="sam@alpha.test",
                                          active=True, admin=False, timezone="UTC",
                                          created_at=stamp, updated_at=stamp))
        conn.execute(api_key.insert().values(id=new_id(), token_hash=token_hash("sam-tok"),
                                             label="member", user_id=member_id,
                                             organization_id=org, created_at=stamp))
    body = _gql(world, "sam-tok", "query { agentRunLogs { nodes { id } } }")
    assert body.get("errors"), body


def test_revision_82_adds_the_run_log_and_replays(tmp_path):
    from sqlalchemy import inspect, text

    from hoja_server import db as db_module

    engine = db_module.make_engine(tmp_path / "rev81.db")
    db_module.migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE agent_run_log")
        conn.execute(text("DELETE FROM schema_revision WHERE revision >= 82"))
    assert "agent_run_log" not in inspect(engine).get_table_names()
    for _ in range(2):
        db_module.migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION
    assert "agent_run_log" in inspect(engine).get_table_names()


# ------------------------------------------------------------------- machine workspace
def test_the_connect_command_carries_the_work_folder_and_refuses_unsafe_ones(world, monkeypatch, tmp_path):
    (tmp_path / "hoja_runnerd-0.1.2-py3-none-any.whl").write_bytes(b"w")
    monkeypatch.setenv("HOJA_DOWNLOAD_ROOT", str(tmp_path))
    ok = _gql(world, world["alpha"]["token"], '''mutation { runnerEnrollmentCreate(
        name: "ci", workRoot: "~/code") { connectCommand } }''')
    assert "errors" not in ok, ok
    assert ok["data"]["runnerEnrollmentCreate"]["connectCommand"].endswith(" HOJA_WORK_ROOT=~/code sh")
    bad = _gql(world, world["alpha"]["token"], '''mutation { runnerEnrollmentCreate(
        name: "ci2", workRoot: "~/x; rm -rf /") { connectCommand } }''')
    assert bad["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"


def test_a_machine_says_where_it_works_on_its_first_poll(world):
    identity = _enroll(world)
    response = world["client"].post("/runner/lease", headers=_auth(identity), json={
        "runner_id": identity["runner_id"], "poll_timeout_ms": 0,
        "labels": {"work_root": "/home/ada/code", "os": "linux", "runner_version": "0.1.2",
                   "password": "not a machine label"}})
    assert response.status_code == 200
    body = _gql(world, world["alpha"]["token"], f'''query {{ runner(id: "{identity["runner_id"]}") {{
        workRoot os runnerVersion labels }} }}''')
    assert "errors" not in body, body
    machine = body["data"]["runner"]
    assert machine["workRoot"] == "/home/ada/code" and machine["os"] == "linux"
    assert machine["runnerVersion"] == "0.1.2"
    assert "work_root" not in machine["labels"] and "password" not in machine["labels"]


def test_a_prefix_grant_gives_each_task_its_own_branch(world):
    agent = _create_agent(world)
    granted = _gql(world, world["alpha"]["token"], f'''mutation {{ agentPolicyUpdate(
        agentMemberId: "{agent["id"]}", input: {{ repos: {json.dumps(json.dumps(
            [{"repo": "acme/demo", "branches": ["agent/"]}]))} }}) {{ success }} }}''')
    assert "errors" not in granted, granted
    launched = _launch(world, agent, teamId=world["alpha"]["team_id"], title="Branch me")
    lease, _ = _claim(world, _enroll(world))
    assert lease["repo"]["branch"] == f"agent/{launched['issue']['identifier'].lower()}"


def test_an_unchosen_harness_prefers_the_coder_harness_when_a_machine_offers_it(world):
    agent = _create_agent(world)  # its policy names no harness
    identity = _enroll(world, harnesses=("hoja-coder", "pi"))
    launched = _launch(world, agent, teamId=world["alpha"]["team_id"], title="Which harness")
    assert launched["lease"]["harness"] == "hoja-coder"
    lease, _ = _claim(world, identity)
    assert lease is not None and lease["harness"] == "hoja-coder"
