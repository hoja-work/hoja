"""Automatic verification and runner pools (HOJA-712).

Two server changes, one file because they meet at the same sentence: delegated work
must reach a verdict, and it must run where the agent is allowed to run.

=====================================================  ==========================
test                                                   proves
=====================================================  ==========================
a_finished_claim_is_verified_automatically             finish -> verifying -> an
                                                       automatic ``met`` by a
                                                       different principal, task in
                                                       In Review
missing_evidence_records_uncertain_and_stays_verifying  missing evidence is never met
a_second_trigger_records_one_verdict                    one verdict per claim
a_person_can_still_sign_a_claim_judged_uncertain        late evidence, re-judged
a_verifier_on_the_workers_model_is_refused              separation, automatically
automatic_verification_can_be_switched_off              the deployment switch
the_sweep_judges_a_claim_whose_hook_was_lost            the recovery path
a_pooled_agent_is_leased_only_inside_its_pool           in-pool vs out-of-pool
a_runner_without_a_pool_takes_only_unpooled_work        no label means no pool
an_agent_without_a_pool_is_leased_to_any_runner          today's behaviour, kept
pools_do_not_cross_tenants                              tenant boundary unchanged
=====================================================  ==========================

SQLite by default; Postgres with ``HOJA_TEST_RLS=1 HOJA_TEST_POSTGRES_URL=...``.
"""
from __future__ import annotations

import itertools
import json

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import agent_autoverify, agent_sessions, runner_api
from hoja_server.app import create_app
from hoja_server.db import (
    agent_member,
    agent_verification,
    github_link,
    new_id,
    now_iso,
    seed_tenant,
    user,
)
from tests import rls_mode

WORKER_MODEL = "claude-sonnet-4"
MERGE_SHA = "abc1234def5678"
PR_URL = "https://github.com/hoja-work/hoja-cloud/pull/975"
_RUNNERS = itertools.count(1)
_LINKS = itertools.count(975)

ACCEPTANCE = ("the finish route moves the session to verifying\n"
              "only a met verdict reaches In Review")


# --------------------------------------------------------------------------- fixture
@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "auto-verify-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    monkeypatch.setenv("HOJA_LIVE_REVISION", MERGE_SHA)
    monkeypatch.delenv(agent_autoverify.ENABLED_ENV, raising=False)
    monkeypatch.delenv(agent_autoverify.MODEL_ENV, raising=False)
    engine, kwargs = rls_mode.workspace(tmp_path, "auto-verify-pools")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok",
                        demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok",
                        demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo}


# ----------------------------------------------------------------------------- graphql
def _gql(world, token: str, query: str, *, org: str = "alpha") -> dict:
    response = world["client"].post("/graphql", json={"query": query},
                                    headers={"Authorization": f"Bearer {token}"})
    return response.json()


def _create_agent(world, *, org="alpha", name="Scout", model=WORKER_MODEL,
                  runner_pool=None, capacity=1) -> dict:
    tenant = world[org]
    pool = f', runnerPool: "{runner_pool}"' if runner_pool else ""
    body = _gql(world, tenant["token"], f'''
        mutation {{
          agentMemberCreate(input: {{
            name: "{name}", capacity: {capacity}, model: "{model}"{pool}
          }}) {{ success agentMember {{ id userId model runnerPool }} }}
        }}''', org=org)
    assert "errors" not in body, body
    agent = body["data"]["agentMemberCreate"]["agentMember"]
    if runner_pool:
        assert agent["runnerPool"] == runner_pool
    # A delegated task may not be leased without a policy (WP12 deny-by-default).
    grant = _gql(world, tenant["token"], f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{agent["id"]}",
                            input: {{ teams: ["{tenant["team_id"]}"],
                                     maxConcurrency: {capacity} }}) {{ success }}
        }}''', org=org)
    assert "errors" not in grant, grant
    return agent


def _delegate(world, agent: dict, *, org="alpha", description=None) -> dict:
    tenant = world[org]
    if description is None:
        description = "## Acceptance\n" + "\n".join(f"- {item}"
                                                    for item in ACCEPTANCE.split("\n"))
    body = _gql(world, tenant["token"], f'''
        mutation {{
          issueCreate(input: {{ teamId: "{tenant["team_id"]}",
                               title: "Delegated work",
                               delegateId: "{agent["userId"]}",
                               description: {json.dumps(description)} }}) {{
            success issue {{ id identifier }} }} }}''', org=org)
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]


def _issue_state(world, issue_id: str, *, org="alpha") -> str | None:
    body = _gql(world, world[org]["token"],
                f'query {{ issue(id: "{issue_id}") {{ state {{ name }} }} }}', org=org)
    assert "errors" not in body, body
    return body["data"]["issue"]["state"]["name"]


def _session(world, issue_id: str, *, org="alpha") -> dict:
    body = _gql(world, world[org]["token"], f'''
        query {{ issue(id: "{issue_id}") {{
          agentSession {{ id state status verdict verifierId verifierModel workerModel }} }}
        }}''', org=org)
    assert "errors" not in body, body
    return body["data"]["issue"]["agentSession"]


# ------------------------------------------------------------------------------- runner
def _enroll(world, *, org="alpha", pool=None, harnesses=("pi",), name=None) -> dict:
    tenant = world[org]
    name = name or f"runner-{next(_RUNNERS)}"
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    labels = {"pool": pool} if pool else {}
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment,
        "harnesses": list(harnesses), "labels": labels})
    assert response.status_code == 200, response.text
    return response.json()


def _claim(world, identity: dict) -> tuple[dict | None, list[dict]]:
    response = world["client"].post(
        "/runner/lease", json={"runner_id": identity["runner_id"], "poll_timeout_ms": 0},
        headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    body = response.json()
    return body.get("lease"), list(body.get("refused") or [])


def _finish(world, identity: dict, lease: dict, **overrides):
    body = {
        "lease_id": lease["lease_id"],
        "run_id": lease["run_id"],
        "fence": lease["fence"],
        "token": lease["token"],
        "state": "completed",
        "summary": "did the work",
        "model": WORKER_MODEL,
        "idempotency_key": f"{lease['run_id']}:1",
        "evidence": {"merged": MERGE_SHA},
        "acceptance": [{"item": item, "evidence": "tests/agent/test_auto_verify_and_pools.py"}
                       for item in ACCEPTANCE.split("\n")],
        "deployed_revision": MERGE_SHA,
        "effects": [{"key": f"{lease['run_id']}:2", "kind": "pull_request",
                     "payload": {"external_ref": PR_URL}}],
    }
    body.update(overrides)
    return world["client"].post("/runner/finish", json=body,
                                headers={"Authorization": f"Bearer {identity['token']}"})


def _link_pull_request(world, issue_id: str, *, files=2, org="alpha") -> str:
    """What the webhook + enrichment pass would have written for this task."""
    now = now_iso()
    number = next(_LINKS)
    url = PR_URL.replace("/975", f"/{number}")
    with world["engine"].begin() as conn:
        conn.execute(github_link.insert().values(
            id=new_id(), organization_id=world[org]["org_id"], issue_id=issue_id,
            kind="pull_request", external_id=str(number), url=url, title="HOJA-712",
            state="MERGED", sha=MERGE_SHA, base_ref="main", head_ref="feat/hoja-712",
            merged_at=now,
            files=json.dumps([{"path": f"file-{i}.py"} for i in range(files)]),
            created_at=now, updated_at=now))
    return url


def _enrich_link(world, url: str, *, files=2) -> None:
    """The diff the enrichment pass records after the webhook first saw the pull request."""
    with world["engine"].begin() as conn:
        conn.execute(github_link.update().where(
            github_link.c.url == url).values(
                files=json.dumps([{"path": f"file-{i}.py"} for i in range(files)]),
                updated_at=now_iso()))


def _verdicts(world, session_id: str) -> list[dict]:
    with world["engine"].connect() as conn:
        return [dict(row) for row in conn.execute(select(agent_verification).where(
            agent_verification.c.session_id == session_id)).mappings()]


def _claimed(world, *, description=None, pool=None) -> dict:
    """An agent, its delegated task, a runner and the lease that task queued."""
    agent = _create_agent(world)
    task = _delegate(world, agent, description=description)
    identity = _enroll(world, pool=pool)
    lease, _refused = _claim(world, identity)
    assert lease is not None, "the delegation should have queued a lease"
    return {"agent": agent, "task": task, "identity": identity, "lease": lease}


# --------------------------------------------------------------------- automatic verify
def test_a_finished_claim_is_verified_automatically(world):
    case = _claimed(world)
    pr = _link_pull_request(world, case["task"]["id"])
    response = _finish(world, case["identity"], case["lease"],
                       pull_request={"url": pr, "state": "merged",
                                     "merged_sha": MERGE_SHA})
    assert response.status_code == 200, response.text
    # The ack is the machine's answer at finish time: the claim is awaiting a verdict.
    assert response.json()["session_state"] == agent_sessions.VERIFYING

    # ...and the verdict lands without anybody calling agentSessionVerify.
    session = _session(world, case["task"]["id"])
    assert session["verdict"] == "met", session
    assert session["state"] == "in_review"
    assert _issue_state(world, case["task"]["id"]) == "In Review"

    verdicts = _verdicts(world, session["id"])
    assert len(verdicts) == 1, verdicts
    signed = verdicts[0]
    assert signed["verifier_kind"] == "agent"
    assert signed["verifier_model"] == agent_autoverify.DEFAULT_VERIFIER_MODEL
    assert signed["requested_verdict"] == "met" and signed["verdict"] == "met"
    # Separation, on the row: the verifier is neither the worker nor its model.
    assert signed["verifier_id"] != case["agent"]["userId"]
    assert signed["worker_principal_id"] == case["agent"]["userId"]
    assert signed["worker_model"] == WORKER_MODEL
    outcomes = {c["name"]: c["outcome"] for c in json.loads(signed["checks"])}
    assert outcomes["pull_request"] == "passed" and outcomes["acceptance_list"] == "passed"
    assert outcomes["deployed"] == "passed"

    # The principal is a real identity: a user row with no seat and no member row.
    with world["engine"].connect() as conn:
        kind = conn.execute(select(user.c.kind).where(
            user.c.id == signed["verifier_id"])).scalars().one()
        members = conn.execute(select(agent_member.c.id).where(
            agent_member.c.user_id == signed["verifier_id"])).first()
    assert kind == "agent"
    assert members is None, "the verifier is not a delegation target"


def test_missing_evidence_records_uncertain_and_stays_verifying(world):
    # No "Acceptance" heading: there is nothing the claim can be checked against, and
    # a check with no evidence is uncertain, never met.
    case = _claimed(world, description="Do the thing.\n")
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world, case["task"]["id"])
    assert session["verdict"] == "uncertain", "missing evidence must never read as met"
    assert session["state"] == "verifying"
    assert _issue_state(world, case["task"]["id"]) != "In Review"
    signed = _verdicts(world, session["id"])[0]
    outcomes = {c["name"]: c["outcome"] for c in json.loads(signed["checks"])}
    assert outcomes["acceptance_list"] == "uncertain"


def test_a_second_trigger_records_one_verdict(world):
    case = _claimed(world)
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world, case["task"]["id"])
    assert len(_verdicts(world, session["id"])) == 1

    # The same claim triggered again — directly, and by the sweep — records nothing.
    again = agent_autoverify.verify_session(world["engine"], world["alpha"]["org_id"],
                                            session["id"])
    assert again["status"] in ("already_judged", "not_verifying"), again
    swept = agent_autoverify.verify_pending(world["engine"])
    assert [r for r in swept if r.get("status") == "recorded"] == []
    assert len(_verdicts(world, session["id"])) == 1


def test_a_person_can_still_sign_a_claim_the_platform_judged_uncertain(world):
    """One automatic verdict per claim, not one verdict ever: a human may re-judge.

    The realistic case is evidence that arrives late: the webhook links the pull request
    before enrichment records its diff, so the automatic verdict is ``uncertain`` on
    ``pull_request``. Once the diff is there, the same claim is judged again — and
    only that second verdict moves the task.
    """
    case = _claimed(world)
    pr = _link_pull_request(world, case["task"]["id"], files=0)
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world, case["task"]["id"])
    assert session["verdict"] == "uncertain" and session["state"] == "verifying"
    assert _issue_state(world, case["task"]["id"]) != "In Review"

    _enrich_link(world, pr)
    signed = _gql(world, world["alpha"]["token"], f'''
        mutation {{ agentSessionVerify(id: "{session["id"]}", verdict: "met",
                   model: "gpt-5-codex", liveRevision: "{MERGE_SHA}") {{
          success agentSession {{ state verdict verifierId }} }} }}''')
    assert "errors" not in signed, signed
    payload = signed["data"]["agentSessionVerify"]["agentSession"]
    assert payload["verifierId"] == world["alpha"]["user_id"]
    assert payload["verdict"] == "met" and payload["state"] == "in_review"
    assert len(_verdicts(world, session["id"])) == 2


def test_a_verifier_on_the_workers_model_is_refused(world, monkeypatch):
    monkeypatch.setenv(agent_autoverify.MODEL_ENV, WORKER_MODEL)
    case = _claimed(world)
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world, case["task"]["id"])
    # A verifier that would grade its own model is not a second opinion: no row.
    assert _verdicts(world, session["id"]) == []
    assert session["state"] == "verifying" and session["verdict"] is None
    result = agent_autoverify.verify_session(world["engine"], world["alpha"]["org_id"],
                                             session["id"])
    assert result["status"] == "blocked"
    assert any("model" in problem for problem in result["problems"])


def test_automatic_verification_can_be_switched_off(world, monkeypatch):
    monkeypatch.setenv(agent_autoverify.ENABLED_ENV, "0")
    case = _claimed(world)
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world, case["task"]["id"])
    assert session["state"] == "verifying" and session["verdict"] is None
    assert _verdicts(world, session["id"]) == []
    assert agent_autoverify.verify_pending(world["engine"]) == []


def test_the_sweep_judges_a_claim_whose_hook_was_lost(world, monkeypatch):
    """The reaper's sweep is the recovery path when the finish hook never ran."""
    monkeypatch.setenv(agent_autoverify.ENABLED_ENV, "0")
    case = _claimed(world)
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world, case["task"]["id"])
    assert session["state"] == "verifying" and session["verdict"] is None

    # The process that should have judged it is gone; the next sweep is not.
    monkeypatch.delenv(agent_autoverify.ENABLED_ENV, raising=False)
    judged = agent_autoverify.verify_pending(world["engine"])
    assert [r["status"] for r in judged if r["sessionId"] == session["id"]] == ["recorded"]
    session = _session(world, case["task"]["id"])
    assert session["verdict"] == "met" and session["state"] == "in_review"
    assert len(_verdicts(world, session["id"])) == 1


# ------------------------------------------------------------------------- pools
def test_a_pooled_agent_is_leased_only_inside_its_pool(world):
    agent = _create_agent(world, runner_pool="gpu")
    task = _delegate(world, agent)
    outsider = _enroll(world, pool="cpu", name="cpu-runner")
    lease, refused = _claim(world, outsider)
    assert lease is None, "a runner outside the agent's pool must not be granted it"
    assert [r["reason"] for r in refused] == [runner_api.ERROR_POOL_NOT_ALLOWED], refused
    assert refused[0]["pool"] == "gpu" and refused[0]["task_id"] == task["id"]

    insider = _enroll(world, pool="gpu", name="gpu-runner")
    granted, _refused = _claim(world, insider)
    assert granted is not None, "a runner inside the pool takes the same attempt"
    assert granted["task_id"] == task["id"]


def test_a_runner_without_a_pool_takes_only_unpooled_work(world):
    agent = _create_agent(world, runner_pool="gpu")
    _delegate(world, agent)
    unlabelled = _enroll(world, pool=None, name="bare-runner")
    lease, refused = _claim(world, unlabelled)
    assert lease is None
    assert [r["reason"] for r in refused] == [runner_api.ERROR_POOL_NOT_ALLOWED], refused


def test_an_agent_without_a_pool_is_leased_to_any_runner(world):
    agent = _create_agent(world)  # no runnerPool
    task = _delegate(world, agent)
    pooled = _enroll(world, pool="cpu", name="cpu-runner")
    granted, _refused = _claim(world, pooled)
    assert granted is not None and granted["task_id"] == task["id"]

    agent2 = _create_agent(world, name="Scout Two")
    task2 = _delegate(world, agent2)
    bare = _enroll(world, pool=None, name="bare-runner")
    granted2, _refused2 = _claim(world, bare)
    assert granted2 is not None and granted2["task_id"] == task2["id"]


def test_pools_do_not_cross_tenants(world):
    alpha_agent = _create_agent(world, runner_pool="gpu")
    alpha_task = _delegate(world, alpha_agent)
    # Same pool name, different workspace: the tenant boundary still decides.
    foreign = _enroll(world, org="bravo", pool="gpu", name="bravo-gpu")
    lease, refused = _claim(world, foreign)
    assert lease is None and refused == [], refused
    # And the alpha runner inside the pool still gets it.
    insider = _enroll(world, pool="gpu", name="alpha-gpu")
    granted, _refused = _claim(world, insider)
    assert granted is not None and granted["task_id"] == alpha_task["id"]
