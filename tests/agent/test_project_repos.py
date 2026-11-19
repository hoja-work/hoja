"""Where a delegated task is worked on: its project's repo, the agent's grant, or scratch (HOJA-602).

======================================================  =======================================
test                                                    proves
======================================================  =======================================
a_task_in_a_project_with_a_repo_works_in_it             project repo, branch agent/<identifier>,
                                                        and it wins over the agent's own grant
a_task_without_a_project_repo_falls_back                the agent's grant; with none, no repo
a_project_repo_never_reaches_a_task_outside_the_project  a project's repo is its tasks' only
the_run_log_says_which_repo_and_why                     "from project Website"
setting_it_is_admin_only_validated_and_audited          members refused, junk refused, audited
revision_83_adds_the_column_and_replays                 migration
======================================================  =======================================
"""
from __future__ import annotations

import json

from sqlalchemy import select

from hoja_server.db import api_key, audit_event, new_id, now_iso, token_hash, user
from tests.agent.test_auto_verify_and_pools import (  # noqa: F401 - `world` is a fixture
    _claim,
    _create_agent,
    _enroll,
    _gql,
    world,
)


def _project(world, name: str, repository: str | None = None) -> str:
    body = _gql(world, world["alpha"]["token"], f'''mutation {{ projectCreate(input: {{
        name: {json.dumps(name)}, teamIds: ["{world["alpha"]["team_id"]}"] }}) {{ project {{ id }} }} }}''')
    assert "errors" not in body, body
    pid = body["data"]["projectCreate"]["project"]["id"]
    if repository:
        set_ = _gql(world, world["alpha"]["token"], f'''mutation {{ projectRepositorySet(
            projectId: "{pid}", repository: {json.dumps(repository)}) {{ id repository }} }}''')
        assert "errors" not in set_, set_
        assert set_["data"]["projectRepositorySet"]["repository"] == repository
    return pid


def _task(world, agent: dict, *, project_id: str | None = None) -> dict:
    extra = f', projectId: "{project_id}"' if project_id else ""
    body = _gql(world, world["alpha"]["token"], f'''mutation {{ issueCreate(input: {{
        teamId: "{world["alpha"]["team_id"]}", title: "Work", delegateId: "{agent["userId"]}"{extra} }}) {{
        issue {{ id identifier }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]


def _grant_repo(world, agent: dict, repo: str) -> None:
    body = _gql(world, world["alpha"]["token"], f'''mutation {{ agentPolicyUpdate(
        agentMemberId: "{agent["id"]}", input: {{ repos: {json.dumps(json.dumps(
            [{"repo": repo, "branches": ["agent/"]}]))} }}) {{ success }} }}''')
    assert "errors" not in body, body


def test_a_task_in_a_project_with_a_repo_works_in_it(world):
    agent = _create_agent(world)
    _grant_repo(world, agent, "acme/agent-own")
    pid = _project(world, "Website", "acme/website")
    task = _task(world, agent, project_id=pid)
    lease, _ = _claim(world, _enroll(world))
    assert lease["repo"]["repo"] == "acme/website"
    assert lease["repo"]["url"] == "https://github.com/acme/website.git"
    assert lease["repo"]["branch"] == f"agent/{task['identifier'].lower()}"
    assert lease["repo"]["source"] == "project" and lease["repo"]["project"] == "Website"


def test_a_task_without_a_project_repo_falls_back(world):
    agent = _create_agent(world)
    pid = _project(world, "Docs")  # a project with no repository
    _task(world, agent, project_id=pid)
    lease, _ = _claim(world, _enroll(world))
    assert lease["repo"] is None, "no repo anywhere: the task runs in a scratch folder"

    other = _create_agent(world, name="Granted")
    _grant_repo(world, other, "acme/agent-own")
    _task(world, other)
    lease, _ = _claim(world, _enroll(world))
    assert lease["repo"]["repo"] == "acme/agent-own" and lease["repo"]["source"] == "agent"


def test_a_project_repo_never_reaches_a_task_outside_the_project(world):
    agent = _create_agent(world)
    _project(world, "Secret", "acme/secret")
    elsewhere = _project(world, "Elsewhere")
    _task(world, agent, project_id=elsewhere)
    lease, _ = _claim(world, _enroll(world))
    assert lease is not None and lease["repo"] is None


def test_the_run_log_says_which_repo_and_why(world):
    agent = _create_agent(world)
    pid = _project(world, "Website", "acme/website")
    _task(world, agent, project_id=pid)
    lease, _ = _claim(world, _enroll(world))
    body = _gql(world, world["alpha"]["token"], f'''query {{ agentRun(id: "{lease["lease_id"]}") {{
        logs(source: "server") {{ kind message }} }} }}''')
    assert "errors" not in body, body
    grant = next(l for l in body["data"]["agentRun"]["logs"] if l["kind"] == "lease.grant")
    assert "acme/website" in grant["message"] and "from project Website" in grant["message"]


def test_setting_it_is_admin_only_validated_and_audited(world):
    pid = _project(world, "Audited", "acme/one")
    bad = _gql(world, world["alpha"]["token"], f'''mutation {{ projectRepositorySet(
        projectId: "{pid}", repository: "acme/x; rm -rf /") {{ id }} }}''')
    assert bad["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"
    stamp, member = now_iso(), new_id()
    with world["engine"].begin() as conn:
        conn.execute(user.insert().values(id=member, organization_id=world["alpha"]["org_id"],
                                          name="Sam", display_name="Sam", email="sam@alpha.test",
                                          active=True, admin=False, timezone="UTC",
                                          created_at=stamp, updated_at=stamp))
        conn.execute(api_key.insert().values(id=new_id(), token_hash=token_hash("sam-tok"),
                                             label="m", user_id=member,
                                             organization_id=world["alpha"]["org_id"],
                                             created_at=stamp))
    refused = _gql(world, "sam-tok", f'''mutation {{ projectRepositorySet(
        projectId: "{pid}", repository: "acme/two") {{ id }} }}''')
    assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    cleared = _gql(world, world["alpha"]["token"], f'''mutation {{ projectRepositorySet(
        projectId: "{pid}", repository: null) {{ repository }} }}''')
    assert cleared["data"]["projectRepositorySet"]["repository"] is None
    with world["engine"].connect() as conn:
        details = [json.loads(d) for d in conn.execute(select(audit_event.c.detail).where(
            audit_event.c.action == "project.repository.change")).scalars()]
    assert {"before": None, "after": "acme/one"} in details
    assert {"before": "acme/one", "after": None} in details


def test_revision_83_adds_the_column_and_replays(tmp_path):
    from sqlalchemy import inspect, text

    from hoja_server import db as db_module

    engine = db_module.make_engine(tmp_path / "rev82.db")
    db_module.migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE project DROP COLUMN repository")
        conn.execute(text("DELETE FROM schema_revision WHERE revision >= 83"))
    for _ in range(2):
        db_module.migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION
    assert "repository" in {c["name"] for c in inspect(engine).get_columns("project")}
