"""A claim is not a completion until a separate verifier signs it (HOJA-602-A-13, WP10).

The acceptance list this file exists to prove, one test each:

==================================================  =========================================
test                                                 source
==================================================  =========================================
finish_with_a_claim_moves_to_verifying              issue "with a claim moves the session to
                                                     pendingVerification, not complete"
a_verifier_sharing_the_worker_principal_is_refused  WP10 Done "a verifier sharing the
                                                     implementer's principal is refused"
only_met_moves_the_task_to_in_review                issue "only `met` moves the task to In
                                                     Review"
an_unverified_claim_cannot_reach_done               issue acceptance "a test proves an
                                                     unverified claim can't reach Done"
missing_evidence_records_uncertain_not_met          WP10 T044 "default to uncertain when
                                                     evidence is missing"
no_check_reads_ci_status                            task "must NOT depend on GitHub Actions"
the_reaper_leaves_a_claim_under_verification_alone  issue "nothing in the reaper or idle
                                                     handling cancels a run being verified"
idle_handling_never_settles_a_verifying_session     same, idle half
the_replay_reports_28_of_30_under_s15               issue acceptance "28 of the 30 historical
                                                     failures prevented under S15"
a_revision_67_database_gains_the_verification_rows  the 67 -> 68 upgrade path
the_claim_is_written_once_and_never_edited          WP10 T043 "keep the claim immutable"
==================================================  =========================================

Everything goes through the real HTTP routes and the real state machine: the tests create
a delegated task, let a runner lease it, finish it with a claim, and then have a second
principal judge that claim. SQLite by default; Postgres with ``HOJA_TEST_RLS=1
HOJA_TEST_POSTGRES_URL=...``.
"""
from __future__ import annotations

import itertools
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select
from starlette.testclient import TestClient

from hoja_server import agent_sessions, agent_verify, runner_api
from hoja_server.app import create_app
from hoja_server.db import (
    agent_lease,
    agent_session,
    agent_verification,
    github_link,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_tenant,
)
from tests import rls_mode

WORKER_MODEL = "claude-sonnet-4"
VERIFIER_MODEL = "gpt-5-codex"
PR_URL = "https://github.com/hoja-work/hoja-cloud/pull/975"
MERGE_SHA = "abc1234def5678"
_RUNNERS = itertools.count(1)
_LINKS = itertools.count(975)

ACCEPTANCE = ("the finish route moves the session to verifying\n"
              "only a met verdict reaches In Review")


# --------------------------------------------------------------------------- HTTP
def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "verified-finish-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    monkeypatch.delenv(agent_verify.LIVE_REVISION_ENV, raising=False)
    engine, kwargs = rls_mode.workspace(tmp_path, "verified-finish")
    seeded = seed_tenant(engine, org_name="Verifying", org_url_key="verifying",
                         user_email="admin@verifying.test", token="verify-tok",
                         demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "tenant": seeded}


def _create_agent(client, token, *, name="Scout", model=WORKER_MODEL, capacity=2) -> dict:
    body = _post(client, token, f'''
        mutation {{
          agentMemberCreate(input: {{
            name: "{name}", capacity: {capacity}, model: "{model}"
          }}) {{ success agentMember {{ id userId model }} }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]


def _create_task(client, token, team_id, *, delegate_id, title="Delegated work",
                 description=None) -> dict:
    fields = [f'teamId: "{team_id}"', f'title: {json.dumps(title)}',
              f'delegateId: "{delegate_id}"']
    if description:
        fields.append(f'description: {json.dumps(description)}')
    body = _post(client, token, f'''
        mutation {{
          issueCreate(input: {{{", ".join(fields)}}}) {{
            success issue {{ id identifier }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]


def _issue_state(client, token, issue_id) -> dict:
    body = _post(client, token, f'''
        query {{ issue(id: "{issue_id}") {{ state {{ id name }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["issue"]["state"]


def _session(client, token, issue_id) -> dict:
    body = _post(client, token, f'''
        query {{ issue(id: "{issue_id}") {{
          agentSession {{ id state status verdict verifiedAt verifierId workerModel
            claim activities {{ kind body payload }} }} }} }}''')
    assert "errors" not in body, body
    return body["data"]["issue"]["agentSession"]


def _enroll(world, *, harnesses=("pi",), name=None) -> dict:
    tenant = world["tenant"]
    # A runner name is unique per workspace, and one test may enroll two.
    name = name or f"runner-{next(_RUNNERS)}"
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment, "harnesses": list(harnesses),
        "labels": {"pool": "default"}})
    assert response.status_code == 200, response.text
    return response.json()


def _claim_lease(world, identity: dict) -> dict:
    response = world["client"].post(
        "/runner/lease", json={"runner_id": identity["runner_id"], "poll_timeout_ms": 0},
        headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    lease = response.json().get("lease")
    assert lease is not None, "the delegation should have queued a lease"
    return lease


def _finish(world, identity: dict, lease: dict, **overrides) -> dict:
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
        "acceptance": [
            {"item": "the finish route moves the session to verifying",
             "evidence": "tests/agent/test_verified_finish.py#finish"},
            {"item": "only a met verdict reaches In Review",
             "evidence": "tests/agent/test_verified_finish.py#only_met"},
        ],
        "pull_request": {"url": PR_URL, "state": "merged", "merged_sha": MERGE_SHA},
        "deployed_revision": MERGE_SHA,
        "effects": [{"key": f"{lease['run_id']}:2", "kind": "pull_request",
                     "payload": {"external_ref": PR_URL}}],
    }
    body.update(overrides)
    response = world["client"].post(
        "/runner/finish", json=body,
        headers={"Authorization": f"Bearer {identity['token']}"})
    return response


def _link_pull_request(world, issue_id: str, *, state="MERGED", merged=True,
                       files=2) -> str:
    """Record what the GitHub webhook + enrichment would have written for this task.

    Returns the URL, because the claim has to name the pull request the verifier will
    re-read: a claim pointing at a pull request this task is not linked to is not
    evidence about this task, and the verifier says so instead of picking one.
    """
    now = now_iso()
    # One GitHub object links to one issue, unique per (org, kind, external id).
    number = next(_LINKS)
    url = PR_URL.replace("/975", f"/{number}")
    with world["engine"].begin() as conn:
        conn.execute(github_link.insert().values(
            id=new_id(), organization_id=world["tenant"]["org_id"], issue_id=issue_id,
            kind="pull_request", external_id=str(number),
            url=url, title="WP10",
            state=state, sha=MERGE_SHA,
            base_ref="main", head_ref="feat/hoja-602-a13-verifier-b",
            merged_at=now if merged else None,
            files=json.dumps([{"path": f"file-{i}.py"} for i in range(files)]),
            # Deliberately failing: the verifier must not read this column at all.
            checks_state="failure", checks_total=3, checks_passing=0, checks_failing=3,
            created_at=now, updated_at=now,
        ))
    return url


def _delegated_task_with_claim(world, *, description=None, acceptance=None):
    """A task delegated to an agent, run by a runner, and leased for that run."""
    tenant = world["tenant"]
    acceptance = ACCEPTANCE.split("\n") if acceptance is None else acceptance
    if description is None:
        description = "## Acceptance\n" + "\n".join(f"- {item}" for item in acceptance)
    agent = _create_agent(world["client"], tenant["token"])
    task = _create_task(world["client"], tenant["token"], tenant["team_id"],
                        delegate_id=agent["userId"], description=description)
    identity = _enroll(world)
    lease = _claim_lease(world, identity)
    return {"agent": agent, "task": task, "identity": identity, "lease": lease}


def _verify(world, session_id: str, verdict: str, *, token=None, model=VERIFIER_MODEL,
            live_revision=MERGE_SHA) -> dict:
    token = token or world["tenant"]["token"]
    args = [f'id: "{session_id}"', f'verdict: "{verdict}"',
            'rationale: "checked the diff, the acceptance list, main and the deploy"']
    if model:
        args.append(f'model: "{model}"')
    if live_revision:
        args.append(f'liveRevision: "{live_revision}"')
    return _post(world["client"], token, f'''
        mutation {{ agentSessionVerify({", ".join(args)}) {{
          success agentSession {{ id state status verdict verifiedAt verifierId }} }} }}''')


# --------------------------------------------------------------------- the claim
def test_finish_with_a_claim_moves_to_verifying_and_never_to_complete(world):
    case = _delegated_task_with_claim(world)
    lease = case["lease"]
    response = _finish(world, case["identity"], lease)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_state"] == agent_sessions.VERIFYING, (
        "a completed finish is a claim awaiting verification, not a completion")

    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    assert session["state"] == "verifying"
    # Linear's own vocabulary: verifying is an active session, never "complete".
    assert session["status"] == "active"
    assert session["verdict"] is None
    assert session["claim"]["summary"] == "did the work"
    assert session["claim"]["pull_request"]["url"] == PR_URL
    assert [a["body"] for a in session["activities"]][-1] == "Verifying."

    # The task is untouched: In Review is a verifier's decision, not a runner's.
    assert _issue_state(world["client"], world["tenant"]["token"],
                        case["task"]["id"])["name"] != "In Review"
    assert runner_api.SESSION_STATE["completed"] == "awaiting_verification"


def test_the_claim_is_written_once_and_never_edited(world):
    case = _delegated_task_with_claim(world)
    lease = case["lease"]
    first = _finish(world, case["identity"], lease)
    assert first.status_code == 200, first.text
    with world["engine"].connect() as conn:
        stored = conn.execute(select(agent_session.c.claim).where(
            agent_session.c.id == _session_id(conn, case["task"]["id"]))).scalars().one()
    # A different key means "not the same finish": WP07 refuses it, and the claim the
    # verifier will read stays the one the runner actually submitted.
    second = _finish(world, case["identity"], lease, summary="something else",
                     idempotency_key=f"{lease['run_id']}:9")
    assert second.status_code in (409, 410), second.text
    with world["engine"].connect() as conn:
        after = conn.execute(select(agent_session.c.claim).where(
            agent_session.c.id == _session_id(conn, case["task"]["id"]))).scalars().first()
    assert after == stored, "a claim is submitted once and never rewritten"
    assert json.loads(after)["summary"] == "did the work"


def _session_id(conn, issue_id: str) -> str:
    return conn.execute(select(agent_session.c.id).where(
        agent_session.c.issue_id == issue_id).order_by(
        agent_session.c.created_at.desc())).scalars().first()


# ---------------------------------------------------------------- separation
def test_a_verifier_sharing_the_worker_principal_is_refused(world):
    case = _delegated_task_with_claim(world)
    assert _finish(world, case["identity"], case["lease"]).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    with world["engine"].begin() as conn:
        row = agent_sessions.get_session(conn, world["tenant"]["org_id"], session["id"])
        verifier = agent_verify.Principal(id=case["agent"]["userId"], kind="agent",
                                          model=VERIFIER_MODEL, read_only=True)
        with pytest.raises(agent_verify.VerificationRefusedError) as refused:
            agent_verify.record_verdict(conn, row, verdict="met", verifier=verifier)
        assert "worker's own principal" in str(refused.value)
    # Nothing was recorded, and the session still waits.
    assert _session(world["client"], world["tenant"]["token"],
                    case["task"]["id"])["state"] == "verifying"
    with world["engine"].connect() as conn:
        assert conn.execute(select(agent_verification)).first() is None


def test_a_verifier_on_the_workers_model_is_refused_and_a_write_capable_one_too(world):
    case = _delegated_task_with_claim(world)
    assert _finish(world, case["identity"], case["lease"]).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    other = "user-somebody-else"
    with world["engine"].begin() as conn:
        row = agent_sessions.get_session(conn, world["tenant"]["org_id"], session["id"])
        same_model = agent_verify.Principal(id=other, kind="agent", model=WORKER_MODEL,
                                            read_only=True)
        with pytest.raises(agent_verify.VerificationRefusedError) as refused:
            agent_verify.record_verdict(conn, row, verdict="met", verifier=same_model)
        assert "worker's model" in str(refused.value)
        writable = agent_verify.Principal(id=other, kind="agent", model=VERIFIER_MODEL,
                                         read_only=False)
        with pytest.raises(agent_verify.VerificationRefusedError) as refused:
            agent_verify.record_verdict(conn, row, verdict="met", verifier=writable)
        assert "read-only" in str(refused.value)
    assert agent_verify.independence_problems(
        agent_verify.Principal(id=other, kind="agent", model=VERIFIER_MODEL, read_only=True),
        agent_verify.Principal(id=case["agent"]["userId"], kind="agent", model=WORKER_MODEL),
    ) == []
    # A person is not a model: separation is the load-bearing check for them, and the
    # mutation does not make a reviewer invent a model name.
    assert agent_verify.independence_problems(
        agent_verify.Principal(id=other, kind="human", read_only=True),
        agent_verify.Principal(id=case["agent"]["userId"], kind="agent", model=WORKER_MODEL),
    ) == []


# -------------------------------------------------------------- the verdict gate
def test_only_met_moves_the_task_to_in_review(world):
    # not_met: the claim asked for state=open while the link is merged (class D8).
    case = _delegated_task_with_claim(world)
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "open"}).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    verdict = _verify(world, session["id"], "met")
    assert "errors" not in verdict, verdict
    assert verdict["data"]["agentSessionVerify"]["agentSession"]["verdict"] == "not_met"
    assert _issue_state(world["client"], world["tenant"]["token"],
                        case["task"]["id"])["name"] != "In Review"

    # met: a fresh task, the same evidence, the honest verdict.
    good = _delegated_task_with_claim(world)
    good_pr = _link_pull_request(world, good["task"]["id"])
    assert _finish(world, good["identity"], good["lease"],
                   pull_request={"url": good_pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], good["task"]["id"])
    moved = _verify(world, session["id"], "met")
    assert "errors" not in moved, moved
    signed = moved["data"]["agentSessionVerify"]["agentSession"]
    assert signed["verdict"] == "met" and signed["state"] == "in_review"
    assert signed["verifierId"] == world["tenant"]["user_id"]
    assert _issue_state(world["client"], world["tenant"]["token"],
                        good["task"]["id"])["name"] == "In Review"


def test_missing_evidence_records_uncertain_and_leaves_the_task_alone(world):
    # No "Acceptance" heading on the task: there is nothing to check the claim against.
    case = _delegated_task_with_claim(world, description="Do the thing.\n")
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    body = _verify(world, session["id"], "met")
    assert "errors" not in body, body
    signed = body["data"]["agentSessionVerify"]["agentSession"]
    assert signed["verdict"] == "uncertain", "missing evidence must never read as met"
    assert signed["state"] == "verifying"
    assert _issue_state(world["client"], world["tenant"]["token"],
                        case["task"]["id"])["name"] != "In Review"
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_verification).where(
            agent_verification.c.session_id == session["id"])).mappings().one()
    assert row["requested_verdict"] == "met" and row["verdict"] == "uncertain"
    outcomes = {c["name"]: c["outcome"] for c in json.loads(row["checks"])}
    assert outcomes["acceptance_list"] == "uncertain"


def test_no_check_reads_ci_status(world):
    """Project policy: no CI. A failing checks_state must not move the verdict."""
    case = _delegated_task_with_claim(world)
    # checks_state="failure" on the row; the verifier must not read that column.
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    body = _verify(world, session["id"], "met")
    assert "errors" not in body, body
    assert body["data"]["agentSessionVerify"]["agentSession"]["verdict"] == "met"
    # Structural half of the same claim: the verifier has no code path that could
    # read a CI conclusion, so a future edit has to be deliberate.
    source = pathlib.Path(agent_verify.__file__).read_text(encoding="utf-8")
    for column in ("checks_state", "checks_failing", "checks_passing", "workflow_run"):
        assert column not in source, f"the verifier must not read {column}"
    assert "actions" not in " ".join(agent_verify.CHECK_ORDER).replace("separation", "")


def test_an_unverified_claim_cannot_reach_done(world):
    """The structural claim of the work package: no shortcut, at any entry path."""
    case = _delegated_task_with_claim(world)
    pr = _link_pull_request(world, case["task"]["id"])
    assert _finish(world, case["identity"], case["lease"],
                   pull_request={"url": pr, "state": "merged",
                                 "merged_sha": MERGE_SHA}).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    assert session["state"] == "verifying"

    with world["engine"].begin() as conn:
        row = agent_sessions.get_session(conn, world["tenant"]["org_id"], session["id"])
        assert ("verifying", "accept") not in agent_sessions.TRANSITIONS
        with pytest.raises(agent_sessions.RefusedTransitionError):
            agent_sessions.transition(conn, row, "accept")
    # The task is nowhere near Done, and a second finish does not help.
    assert _issue_state(world["client"], world["tenant"]["token"],
                        case["task"]["id"])["name"] not in ("Done", "In Review")
    assert _finish(world, case["identity"], case["lease"]).status_code == 200
    assert _session(world["client"], world["tenant"]["token"],
                    case["task"]["id"])["state"] == "verifying"

    # Only the verified path opens: met -> in_review, then a person accepts.
    _verify(world, session["id"], "met")
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    assert session["state"] == "in_review"
    with world["engine"].begin() as conn:
        row = agent_sessions.get_session(conn, world["tenant"]["org_id"], session["id"])
        assert agent_sessions.transition(conn, row, "accept")["state"] == "done"


# ------------------------------------------------------- nobody settles a claim
def test_the_reaper_leaves_a_claim_under_verification_alone(world):
    case = _delegated_task_with_claim(world)
    lease = case["lease"]
    assert _finish(world, case["identity"], lease).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    assert session["state"] == "verifying"

    past = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    with world["engine"].begin() as conn:
        conn.execute(agent_lease.update().where(agent_lease.c.id == lease["lease_id"]).values(
            lease_until=past))
    assert runner_api.reap_expired_leases(world["engine"], grace_s=0) == []
    with world["engine"].connect() as conn:
        assert conn.execute(select(agent_lease.c.state).where(
            agent_lease.c.id == lease["lease_id"])).scalar() == runner_api.LEASE_FINISHED

    # Even an attempt that is genuinely still active must not be requeued under a
    # verifying claim: that is the double dispatch S15 exists to prevent.
    with world["engine"].begin() as conn:
        conn.execute(agent_lease.update().where(agent_lease.c.id == lease["lease_id"]).values(
            state=runner_api.LEASE_ACTIVE, lease_until=past))
    assert runner_api.reap_expired_leases(world["engine"], grace_s=0) == []
    with world["engine"].connect() as conn:
        assert conn.execute(select(agent_lease.c.state).where(
            agent_lease.c.id == lease["lease_id"])).scalar() == runner_api.LEASE_ACTIVE
        leases = conn.execute(select(agent_lease.c.id).where(
            agent_lease.c.task_id == case["task"]["id"])).scalars().all()
    assert leases == [lease["lease_id"]], "no second attempt was queued"
    assert _session(world["client"], world["tenant"]["token"],
                    case["task"]["id"])["state"] == "verifying"


def test_idle_handling_never_settles_a_verifying_session(world):
    case = _delegated_task_with_claim(world)
    assert _finish(world, case["identity"], case["lease"]).status_code == 200
    session = _session(world["client"], world["tenant"]["token"], case["task"]["id"])
    with world["engine"].begin() as conn:
        row = agent_sessions.get_session(conn, world["tenant"]["org_id"], session["id"])
        assert agent_sessions.may_be_settled(row) is False
        assert agent_sessions.under_verification(row) is True
        # The 30-minute stale rule does not apply to a claim waiting on a verdict.
        assert agent_sessions.mark_stale(row, idle_seconds=10 ** 6) == "active"
        before = len(agent_sessions.activities(conn, world["tenant"]["org_id"], row["id"]))
        assert agent_sessions.keepalive(conn, row, idle_seconds=10 ** 6)["status"] == "active"
        assert len(agent_sessions.activities(
            conn, world["tenant"]["org_id"], row["id"])) == before
        with pytest.raises(agent_sessions.RefusedTransitionError):
            agent_sessions.transition(conn, row, "tick")


# ------------------------------------------------------------------- the upgrade
ADDED_COLUMNS = ("claim", "claim_at", "worker_model", "verifier_model", "verdict",
                 "verdict_at", "verifier_id")


def _schema_as_of_67(engine) -> None:
    """Create every table as revision 67 declared it: no verdict ledger, no claim.

    ``legacy_schema.create_schema`` builds from today's metadata, so it would create
    the columns 68 adds and prove nothing. Dropping them afterwards is not possible on
    SQLite (a column carrying a foreign key cannot be dropped), so the copy is made
    without them in the first place — which is what a 67 database actually looks like.
    """
    from sqlalchemy import MetaData

    from hoja_server.db import metadata, schema_revision
    from hoja_server.db import now_iso as stamp

    target = MetaData()
    for table in metadata.sorted_tables:
        if table.name == "agent_verification":
            continue
        table.to_metadata(target)
    session_table = target.tables["agent_session"]
    for name in ADDED_COLUMNS:
        session_table._columns.remove(session_table.c[name])
    kept = {column.name for column in session_table.columns}
    for constraint in list(session_table.constraints):
        columns = getattr(constraint, "columns", None)
        if columns is not None and not {c.name for c in columns} <= kept:
            # A foreign key that names a column this revision does not have: dropping
            # the column alone leaves the constraint behind.
            session_table.constraints.discard(constraint)
    target.create_all(engine)
    with engine.begin() as conn:
        conn.execute(schema_revision.insert(),
                     [{"revision": number, "applied_at": stamp()} for number in range(1, 68)])


def test_a_revision_67_database_gains_the_verification_rows(tmp_path):
    from hoja_server.db import applied_schema_revision

    engine = make_engine(tmp_path / "verify-upgrade.db")
    _schema_as_of_67(engine)
    with engine.connect() as conn:
        assert "agent_verification" not in set(inspect(conn).get_table_names())
        assert not (set(ADDED_COLUMNS)
                    & {c["name"] for c in inspect(conn).get_columns("agent_session")})
    assert applied_schema_revision(engine) == 67

    migrate(engine)
    assert applied_schema_revision(engine) == 68
    with engine.connect() as conn:
        assert "agent_verification" in set(inspect(conn).get_table_names())
        columns = {c["name"] for c in inspect(conn).get_columns("agent_session")}
    assert set(ADDED_COLUMNS) <= columns


# ------------------------------------------------------------------- the replay
def test_the_replay_reports_28_of_30_under_s15():
    """Paste-in proof of the phase gate, run against the real corpus.

    The replay lives outside the repository (it reads the Claw run store), so it runs
    only when ``HOJA_SCENARIO_REPLAY`` names ``scenarios_replay.py``; the result is
    reported in the pull request either way.
    """
    script = os.environ.get("HOJA_SCENARIO_REPLAY")
    if not script or not os.path.exists(script):
        pytest.skip("HOJA_SCENARIO_REPLAY does not name scenarios_replay.py")
    out = subprocess.run([sys.executable, script, "--check"], capture_output=True,
                         text=True, timeout=600, cwd=os.path.dirname(script))
    assert out.returncode == 0, out.stdout + out.stderr
    with open(os.path.join(os.path.dirname(script), "scenarios_replay.json"),
              encoding="utf-8") as handle:
        report = json.load(handle)
    s15 = next(s for s in report["scenarios"] if s["id"] == "S15")
    assert report["recommended"] == "S15"
    assert s15["prevented"] == 28, f"S15 prevents {s15['prevented']} of 30"
    assert s15["prevented_by_class"]["D7_scope_exceeds_one_run"] == 0, (
        "the two D7 findings are unfixable by these axes and must stay unprevented")
    # The verifier is the mechanism the D8 class depends on, and WP10 now implements
    # it: `agent_verify` re-reads the linked pull request before In Review.
    assert s15["prevented_by_class"]["D8_claim_drift_contradicted"] == 2
