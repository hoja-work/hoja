"""The agent answers on the task, and the run reaches the usage ledger (A-17 / A-18).

Two things the live run against production (HOJA-641, ``live-runner/REPORT.md`` §2 and §7) found
missing on the finish path, one group of tests each:

======================================================  ======================================
test                                                     acceptance
======================================================  ======================================
a_finish_posts_one_comment_written_by_the_agent          A-17 "the agent's final answer ...
                                                         is posted as a comment by the agent
                                                         user on the task"
a_re_delivered_finish_posts_no_second_comment            A-17 "exactly once, idempotent on a
                                                         re-delivered finish"
the_comment_falls_back_to_the_run_summary_and_a_link     A-17 "or a summary with a link to the
                                                         session"
another_workspace_cannot_read_the_comment                A-17 "org-scoped ... like other agent
                                                         writes"
a_finish_records_exactly_one_usage_event_for_the_run     A-18 "the server writes one
                                                         usage_event per run ... a live run
                                                         shows up in usageSummary"
a_finish_without_readable_tokens_still_records_the_run-  A-18 "explicitly reports them as
then_the_run_as_unknown                                  unknown"
======================================================  ======================================

Everything goes through the real HTTP routes: a delegated task, a runner lease, and the finish
payload the daemon now sends. SQLite by default; PostgreSQL with ``HOJA_TEST_RLS=1
HOJA_TEST_POSTGRES_URL=...``, which is where the comment row and the ledger row are also checked
against their tenant policies.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import logging_setup, runner_api
from hoja_server.app import create_app
from hoja_server.db import (
    audit_event,
    comment,
    issue_activity,
    seed_tenant,
    usage_event,
)
from hoja_server.store import Store
from tests import rls_mode

DELEGATED_HARNESS = "pi"
GENERIC = "generic"

#: The word the daemon sends when it could not read the harness's accounting (A-18).
UNKNOWN = "unknown"

#: What pi's session file recorded for the live run's single answer, verbatim from
#: ``live-runner/REPORT.md`` §2 / the session file it names: 56,307 in, 4 out, no cache read,
#: answered by ``claude-haiku-5-5`` through the router the run was configured with.
LIVE_USAGE = {
    "model": "claude-haiku-5-5",
    "inputTokens": 56307,
    "outputTokens": 4,
    "cacheReadTokens": 0,
    "runSeconds": 4.36,
    "source": "pi-session",
}


def _post(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    return client.post("/graphql", json={"query": query, "variables": variables or {}},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "finish-reply-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    logging_setup.forget_secrets()
    engine, kwargs = rls_mode.workspace(tmp_path, "finish-reply")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    yield {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo,
           "store": Store(engine)}
    logging_setup.forget_secrets()


# --------------------------------------------------------------------------- the run
def _input_literal(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_input_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key}: {_input_literal(item)}" for key, item in value.items()) + "}"
    raise TypeError(f"cannot render {value!r} as GraphQL input")


def _agent(world, *, name: str = "Scout", org: str = "alpha") -> dict:
    tenant = world[org]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{
          agentMemberCreate(input: {{ name: "{name}" }}) {{
            success agentMember {{ id userId }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]


def _set_policy(world, member_id: str, document: dict, *, org: str = "alpha") -> dict:
    body = _post(world["client"], world[org]["token"], f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}", input: {_input_literal(document)}) {{
            success agentPolicy {{ id version }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentPolicyUpdate"]["agentPolicy"]


def _delegated_task(world, agent: dict, *, org: str = "alpha", title="Delegated work") -> dict:
    tenant = world[org]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{
          issueCreate(input: {{ teamId: "{tenant["team_key"]}",
            title: {json.dumps(title)}, delegateId: "{agent["userId"]}" }}) {{
            success issue {{ id identifier url }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]


def _enroll(world, *, org: str = "alpha", name: str = "runner-1") -> dict:
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment,
        "harnesses": [GENERIC, DELEGATED_HARNESS]})
    assert response.status_code == 200, response.text
    return response.json()


def _poll(world, identity: dict) -> dict:
    response = world["client"].post("/runner/lease",
                                    json={"runner_id": identity["runner_id"],
                                          "poll_timeout_ms": 0},
                                    headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _granted_lease(world, agent: dict, *, org: str = "alpha") -> tuple[dict, dict, dict]:
    """(task, lease, runner identity) for a delegated task, granted the production way."""
    task = _delegated_task(world, agent, org=org)
    identity = _enroll(world, org=org)
    payload = _poll(world, identity)
    assert "lease" in payload, payload
    return task, payload["lease"], identity


def _finish_body(lease: dict, *, answer: str | None = None, document: dict | None = None,
                 state: str = "completed", summary: str = "harness pi finished on pty",
                 key: str | None = None) -> dict:
    """The finish payload, shaped exactly like the daemon's after A-18.

    The answer rides on the claim effect the daemon already sends (``payload.answer``); the model
    and the counters ride on the top-level ``model``/``usage`` the daemon now adds.
    """
    effects = []
    if answer is not None:
        effects.append({"key": f"{lease['run_id']}:1", "kind": "claim",
                        "payload": {"answer": answer, "task_id": lease["task_id"],
                                    "harness": DELEGATED_HARNESS}})
    body = {
        "lease_id": lease["lease_id"], "run_id": lease["run_id"], "fence": lease["fence"],
        "token": lease["token"], "state": state, "summary": summary,
        "idempotency_key": key or f"{lease['run_id']}:1",
        "model": (document or {}).get("model", UNKNOWN),
        "usage": document or {"model": UNKNOWN, "source": UNKNOWN},
        "evidence": {"backend": "pty", "harness_profile": DELEGATED_HARNESS,
                     "observed_state": "done"},
        "effects": effects,
    }
    return body


def _finish(world, identity: dict, body: dict) -> dict:
    response = world["client"].post("/runner/finish", json=body,
                                    headers={"Authorization": f"Bearer {identity['token']}"})
    return response


def _comments(world, task_id: str, *, org: str = "alpha") -> list[dict]:
    with world["engine"].connect() as conn:
        return [dict(row) for row in conn.execute(
            select(comment).where(and_(
                comment.c.organization_id == world[org]["org_id"],
                comment.c.issue_id == task_id,
            )).order_by(comment.c.created_at)).mappings().all()]


def _usage_rows(world, run_id: str, *, org: str = "alpha") -> list[dict]:
    with world["engine"].connect() as conn:
        return [dict(row) for row in conn.execute(
            select(usage_event).where(and_(
                usage_event.c.organization_id == world[org]["org_id"],
                usage_event.c.run_id == run_id,
            ))).mappings().all()]


def _audit_rows(world, action: str, *, org: str = "alpha") -> list[dict]:
    with world["engine"].connect() as conn:
        return [dict(row) for row in conn.execute(
            select(audit_event).where(and_(
                audit_event.c.organization_id == world[org]["org_id"],
                audit_event.c.action == action,
            )).order_by(audit_event.c.seq)).mappings().all()]


def _summary(world, *, org: str = "alpha", group_by: str = "model") -> dict:
    body = _post(world["client"], world[org]["token"], '''
        query($groupBy: String) {
          usageSummary(groupBy: $groupBy) {
            month groupBy credits events inputTokens outputTokens rows {
              key label model agentUserId credits runs inputTokens outputTokens runMinutes
            }
          }
        }''', {"groupBy": group_by})
    assert "errors" not in body, body
    return body["data"]["usageSummary"]


# --------------------------------------------------------------------------- A-17
def test_a_finish_posts_one_comment_written_by_the_agent(world):
    """The delegating person gets the answer on the task, from the agent, once.

    Before HOJA-602-A-17 the finish wrote nothing a person could read: the claim lived on the
    session row and the task page stayed silent (live-runner/REPORT.md §2, "Task comment: none").
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    task, lease, identity = _granted_lease(world, agent)

    response = _finish(world, identity, _finish_body(
        lease, answer="ready", document=LIVE_USAGE))
    assert response.status_code == 200, response.text
    assert response.json()["session_state"] == "verifying"

    comments = _comments(world, task["id"])
    assert len(comments) == 1, [c["body"] for c in comments]
    posted = comments[0]
    assert posted["user_id"] == agent["userId"], (
        "the comment must be written by the agent user, not by the runner or a system principal")
    assert posted["organization_id"] == world["alpha"]["org_id"]
    assert posted["body"].startswith("ready"), posted["body"]
    # A link to the run, and the provenance that makes the reply accountable.
    assert f"/workspace/agents/{agent['userId']}/queue" in posted["body"], posted["body"]
    assert lease["run_id"] and "model claude-haiku-5-5" in posted["body"], posted["body"]
    assert f"harness {DELEGATED_HARNESS}" in posted["body"], posted["body"]

    # The task's timeline shows the comment arriving, like any other.
    with world["engine"].connect() as conn:
        events = [dict(row) for row in conn.execute(
            select(issue_activity).where(and_(
                issue_activity.c.organization_id == world["alpha"]["org_id"],
                issue_activity.c.issue_id == task["id"],
            ))).mappings().all()]
    created = [row for row in events if row["event_type"] == "commentCreated"]
    assert len(created) == 1, [row["event_type"] for row in events]
    assert json.loads(created[0]["changes"])["commentId"] == posted["id"]
    assert created[0]["actor_id"] == agent["userId"]

    # And the write is audited as the agent's, naming the comment as its target.
    audited = _audit_rows(world, "agent.session.comment")
    assert len(audited) == 1, audited
    assert audited[0]["actor_kind"] == "agent"
    assert audited[0]["actor_id"] == agent["userId"]
    assert audited[0]["target_kind"] == "comment"
    assert audited[0]["target_id"] == posted["id"]
    detail = json.loads(audited[0]["detail"])
    assert detail["issue_id"] == task["id"] and detail["session_id"]

    # Reading it back over GraphQL, as the task page does, sees the agent as the author.
    body = _post(world["client"], world["alpha"]["token"], f'''
        query {{ issue(id: "{task["id"]}") {{
          comments(first: 10) {{ nodes {{ id body user {{ id name }} }} }} }} }}''')
    assert "errors" not in body, body
    nodes = body["data"]["issue"]["comments"]["nodes"]
    assert len(nodes) == 1 and nodes[0]["user"]["id"] == agent["userId"], nodes


def test_a_re_delivered_finish_posts_no_second_comment(world):
    """The same ``run_id + seq`` twice is one finish, so it is one comment.

    A lost response is the normal way this happens: the daemon retries with the key it already
    used, and the task must not gain a second copy of the same answer.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    task, lease, identity = _granted_lease(world, agent)
    body = _finish_body(lease, answer="ready", document=LIVE_USAGE)

    first = _finish(world, identity, body)
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    again = _finish(world, identity, body)
    assert again.status_code == 200, again.text
    assert again.json()["duplicate"] is True

    assert len(_comments(world, task["id"])) == 1
    assert len(_audit_rows(world, "agent.session.comment")) == 1
    # The ledger is idempotent for the same reason: one report, one row.
    assert len(_usage_rows(world, lease["run_id"])) == 1


def test_the_comment_falls_back_to_the_run_summary_and_a_link(world):
    """A harness that produced no answer text still gets a reply that says what happened."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    task, lease, identity = _granted_lease(world, agent)

    response = _finish(world, identity, _finish_body(
        lease, answer=None, document=LIVE_USAGE,
        summary="harness pi finished on pty"))
    assert response.status_code == 200, response.text

    comments = _comments(world, task["id"])
    assert len(comments) == 1
    assert comments[0]["body"].startswith("harness pi finished on pty"), comments[0]["body"]
    body = _post(world["client"], world["alpha"]["token"], f'''
        query {{ issue(id: "{task["id"]}") {{
          agentSession {{ id state }} }} }}''')
    session_id = body["data"]["issue"]["agentSession"]["id"]
    assert f"session {session_id}" in comments[0]["body"], comments[0]["body"]


def test_another_workspace_cannot_read_the_comment(world):
    """The comment is the alpha tenant's row, and Bravo's admin sees neither it nor its usage."""
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    task, lease, identity = _granted_lease(world, agent)
    assert _finish(world, identity, _finish_body(
        lease, answer="ready", document=LIVE_USAGE)).status_code == 200
    assert len(_comments(world, task["id"])) == 1

    theirs = _post(world["client"], world["bravo"]["token"],
                   f'query {{ issue(id: "{task["id"]}") {{ id }} }}')
    assert theirs["data"]["issue"] is None, theirs
    assert _comments(world, task["id"], org="bravo") == []
    assert _usage_rows(world, lease["run_id"], org="bravo") == []
    foreign = _summary(world, org="bravo")
    assert foreign["rows"] == [] and foreign["events"] == 0, foreign


# --------------------------------------------------------------------------- A-18
def test_a_finish_records_exactly_one_usage_event_for_the_run(world):
    """The counters the daemon read off pi's session file land as one row, priced.

    This is the half the live run proved missing: ``usageEvents`` was empty and ``workerModel``
    null for a real job because the finish carried no model and no token counts.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    task, lease, identity = _granted_lease(world, agent)
    response = _finish(world, identity, _finish_body(
        lease, answer="ready", document=LIVE_USAGE))
    assert response.status_code == 200, response.text

    rows = _usage_rows(world, lease["run_id"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["model"] == "claude-haiku-5-5"
    assert row["input_tokens"] == 56307 and row["output_tokens"] == 4
    assert row["credits"] == 57, row["credits"]        # 56,311 processed tokens -> 57 credits
    assert row["run_minutes"] == 1                      # a 4.36 s run is one minute
    assert row["source"] == "finish"
    assert row["agent_user_id"] == agent["userId"]
    assert row["session_id"], "the row must point at the session it belongs to"
    assert row["lease_id"] == lease["lease_id"]

    # The claim names the model too, which is what the session row shows as workerModel.
    session = _post(world["client"], world["alpha"]["token"], f'''
        query {{ issue(id: "{task["id"]}") {{ agentSession {{ workerModel state }} }} }}''')
    assert session["data"]["issue"]["agentSession"]["workerModel"] == "claude-haiku-5-5"

    # And a live run shows up in the admin screen's own data.
    summary = _summary(world)
    assert summary["events"] == 1 and summary["credits"] == 57, summary
    assert summary["inputTokens"] == 56307 and summary["outputTokens"] == 4, summary
    row_keys = {row["model"]: row for row in summary["rows"]}
    assert "claude-haiku-5-5" in row_keys, row_keys
    assert row_keys["claude-haiku-5-5"]["runs"] == 1
    # Grouped by model a row has no agent; grouped by agent it names the one that spent it.
    by_agent = _summary(world, group_by="agent")
    assert [row["agentUserId"] for row in by_agent["rows"]] == [agent["userId"]], by_agent
    assert by_agent["rows"][0]["runs"] == 1 and by_agent["credits"] == 57, by_agent

    events = _post(world["client"], world["alpha"]["token"],
                   'query { usageEvents { model runId inputTokens outputTokens source } }')
    assert "errors" not in events, events
    listed = events["data"]["usageEvents"]
    assert [e["runId"] for e in listed] == [lease["run_id"]], listed
    assert listed[0]["model"] == "claude-haiku-5-5"


def test_a_finish_without_readable_tokens_still_records_the_run_as_unknown(world):
    """A harness whose accounting cannot be read is a run with a gap, not a run that was free.

    The daemon sends ``unknown`` for the model and omits the counters; the server stores the row
    with what it actually knows (the elapsed minute), so the run is visible in the ledger and
    nobody has invented a token count for it.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    task, lease, identity = _granted_lease(world, agent)
    response = _finish(world, identity, _finish_body(
        lease, answer="ready",
        document={"model": UNKNOWN, "source": UNKNOWN, "runSeconds": 4.36}))
    assert response.status_code == 200, response.text

    rows = _usage_rows(world, lease["run_id"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["model"] == UNKNOWN
    assert row["input_tokens"] == 0 and row["output_tokens"] == 0 and row["cache_read_tokens"] == 0
    assert row["credits"] == 0
    assert row["run_minutes"] == 1, "the elapsed minute is a fact the daemon observed"
    summary = _summary(world, group_by="agent")
    assert summary["events"] == 1, summary
    assert [row["runs"] for row in summary["rows"]] == [1], summary["rows"]
    assert summary["credits"] == 0, "an unreadable harness must not be billed for tokens"

    # The comment still says ``unknown`` rather than naming a model nobody read.
    body = _comments(world, task["id"])[0]["body"]
    assert "model unknown" in body, body
