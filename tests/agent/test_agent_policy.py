"""What an agent may touch: one deny-by-default policy, three enforcement points
(HOJA-602-A-14, WP12).

The acceptance list this file exists to prove, one test group each:

======================================================  ============================================
test                                                     source
======================================================  ============================================
every registered MCP tool is denied without a grant      acceptance "a table-driven test: every MCP
                                                         tool is denied without a grant"
a granted tool is allowed, and a write grant is not a    T051/T052, read and write are separate
read grant
the dispatcher refuses an agent and leaves a human       T052 "refuse before contacting the runner",
alone                                                    "agent principals only; humans unchanged"
an irreversible tool needs approval even when granted    T052 approval classes, the floor
a secret value never reaches activity, audit or logs     FR-035, acceptance "a redaction test: a
                                                         secret value never shows up in
                                                         agent_activity"
a secret travels in the lease payload, never the prompt  T053 "inject secrets as environment at
                                                         execution time only"
a policy change writes an audit_event                    FR-036, acceptance "policy changes are
                                                         audited"
the default branch can never be granted                  T051, FR-034
a lease beyond max concurrency or budget is refused      T053, acceptance "a lease beyond max
                                                         concurrency or budget is refused (or
                                                         queued, per policy)"
a policy edited under a live lease cancels it            T053 "reject a lease whose policy changed
                                                         after allocation"
revision 69 applies to an existing 68 database, twice    task acceptance "migrate main (68) ->
                                                         yours (69) with both exit 0, idempotent"
======================================================  ============================================

SQLite by default and PostgreSQL with ``HOJA_TEST_RLS=1``; the RLS probe is what
proves the new tables are tenant-scoped, so the policy fixtures run the same way
every other agent suite does.
"""
from __future__ import annotations

import io
import json

import pytest
from sqlalchemy import and_, inspect, select
from starlette.testclient import TestClient

from hoja_server import agent_policy, logging_setup, runner_api
from hoja_server.app import create_app
from hoja_server.db import (
    agent_activity,
    agent_lease,
    agent_member,
    agent_secret,
    audit_event,
    issue,
    issue_activity,
    seed_tenant,
)
from hoja_server.db import (
    agent_policy as agent_policy_table,
)
from hoja_server.store import Store
from tests import rls_mode

GENERIC = "generic"
#: The harness ``agent_schedule.enqueue_admitted`` queues a delegated session for.
DELEGATED_HARNESS = "pi"
SENTINEL = "s3ntinel-do-not-store-4f2a"

# Every tool, as the dispatcher sees them. Importing the registry here is the point:
# a tool added to mcp_tools and not to a policy must show up as denied, not be
# silently missing from the table.
from hoja_server.mcp_tools import DESTRUCTIVE, READ_TOOLS, WRITE_TOOLS  # noqa: E402


def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "agent-policy-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    logging_setup.forget_secrets()
    engine, kwargs = rls_mode.workspace(tmp_path, "agent-policy")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    yield {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo,
           "store": Store(engine)}
    logging_setup.forget_secrets()


# --------------------------------------------------------------------------- helpers


def _agent(world, *, name: str = "Scout", org: str = "alpha", capacity: int = 1) -> dict:
    tenant = world[org]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{
          agentMemberCreate(input: {{ name: "{name}", capacity: {capacity} }}) {{
            success agentMember {{ id userId }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]


def _input_literal(value) -> str:
    """Render a document as GraphQL input syntax, not as JSON (keys are bare)."""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_input_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_input_literal(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"cannot render {value!r} as GraphQL input")


def _set_policy(world, member_id: str, document: dict, *, org: str = "alpha") -> dict:
    token = world[org]["token"]
    body = _post(world["client"], token, f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}", input: {_input_literal(document)}) {{
            success agentPolicy {{ id version readTools writeTools tasksDelegatedOnly }}
          }}
        }}''')
    return body


def _policy(world, member_id: str, *, org: str = "alpha") -> dict:
    body = _post(world[org]["token"],
                 world[org]["token"],
                 f'query {{ agentPolicy(agentMemberId: "{member_id}") {{ version readTools }} }}'
                 ) if False else _post(world["client"], world[org]["token"],
                                       f'query {{ agentPolicy(agentMemberId: "{member_id}") '
                                       f'{{ id version readTools writeTools secrets }} }}')
    assert "errors" not in body, body
    return body["data"]["agentPolicy"]


def _view(world, member_id: str, *, org: str = "alpha"):
    with world["engine"].connect() as conn:
        return agent_policy.view_for_member(conn, world[org]["org_id"], member_id)


def _delegated_task(world, agent: dict, *, org: str = "alpha", title: str = "Delegated") -> str:
    tenant = world[org]
    body = _post(world["client"], tenant["token"], f'''
        mutation {{
          issueCreate(input: {{ teamId: "{tenant["team_key"]}",
            title: {json.dumps(title)}, delegateId: "{agent["userId"]}" }}) {{
            success issue {{ id }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]["id"]


def _enroll(world, *, org: str = "alpha", name: str = "runner-1") -> dict:
    """A runner for the harness a delegation actually queues (``pi``, WP06's default)."""
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment, "harnesses": [GENERIC, DELEGATED_HARNESS]})
    assert response.status_code == 200, response.text
    return response.json()


def _queued_lease(world, task_id: str, *, org: str = "alpha") -> dict:
    """The lease the delegation queued for this task (WP06 hands it to WP07).

    Reading it instead of creating one keeps the fixture on the production path:
    the lease under test is the one a delegated session actually produced.
    """
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_lease).where(and_(
            agent_lease.c.organization_id == world[org]["org_id"],
            agent_lease.c.task_id == task_id,
            agent_lease.c.state == "queued",
        )).order_by(agent_lease.c.fence)).mappings().first()
    assert row is not None, "the delegation did not queue a lease for this task"
    return dict(row)


def _queue(world, task_id: str, *, org: str = "alpha", prompt: str = "do the task") -> dict:
    """Queue one attempt directly (WP07's seam, without WP06's schedule machine).

    Used only where the schedule check would refuse to create the attempt at all -
    a task with no delegate, or a second admission at member capacity. The lease
    queue is its own authority, and the acceptance for this work package is the
    lease-time check, so the attempt is made at that seam.
    """
    return runner_api.enqueue_task(
        world["engine"], organization_id=world[org]["org_id"], task_id=task_id,
        harness=DELEGATED_HARNESS, prompt=prompt)


def _poll(world, identity: dict) -> dict:
    response = world["client"].post("/runner/lease",
                                    json={"runner_id": identity["runner_id"],
                                          "poll_timeout_ms": 0},
                                    headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _reasons(payload: dict) -> list[str]:
    return [entry["reason"] for entry in payload.get("refused", [])]


def _grant_all_tools(world, member_id: str, *, org: str = "alpha") -> dict:
    """A policy that grants every tool, so a denial can only come from the rule under test."""
    return _set_policy(world, member_id, {
        "readTools": sorted(READ_TOOLS),
        "writeTools": sorted(WRITE_TOOLS),
        "teams": [world[org]["team_id"]],
    }, org=org)


# ------------------------------------------------------- every tool, in one table


def test_every_registered_mcp_tool_is_denied_without_a_grant(world):
    """The table-driven acceptance: 95 tools, none reachable without its own grant.

    Three columns per tool: no policy at all, an empty policy, and the matching
    grant. The third is what makes the first two meaningful - a table that only
    ever refuses would pass even if the dispatcher never consulted a policy.
    """
    agent = _agent(world)
    tools = [("read", name) for name in sorted(READ_TOOLS)] + \
            [("write", name) for name in sorted(WRITE_TOOLS)]
    assert len(tools) == 95, f"the registry has {len(tools)} tools, not 95"

    # Column 1: no policy row at all. Absence is denial.
    for kind, tool in tools:
        decision = agent_policy.decide_tool(None, tool, write=(kind == "write"),
                                            irreversible=tool in DESTRUCTIVE)
        assert not decision.allowed, f"{tool} was allowed with no policy"
        assert tool in decision.reason, "the refusal must name the tool's missing grant"

    # Column 2: a policy that grants nothing.
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    empty = _view(world, agent["id"])
    assert empty is not None
    for kind, tool in tools:
        decision = agent_policy.decide_tool(empty, tool, write=(kind == "write"),
                                            irreversible=tool in DESTRUCTIVE)
        assert not decision.allowed, f"{tool} was allowed by an empty policy"
        assert f"{kind}Tools" in decision.reason

    # Column 3: the matching grant. Every tool, one at a time, so a tool that is
    # granted by accident cannot hide behind its neighbours.
    for kind, tool in tools:
        document = {("writeTools" if kind == "write" else "readTools"): [tool],
                    "teams": [world["alpha"]["team_id"]]}
        _set_policy(world, agent["id"], document)
        view = _view(world, agent["id"])
        decision = agent_policy.decide_tool(view, tool, write=(kind == "write"),
                                            irreversible=tool in DESTRUCTIVE,
                                            approvals={tool})
        assert decision.allowed, f"{tool} was refused despite its grant: {decision.reason}"
        # And the other class does not leak: a read grant is not a write grant.
        other = next(name for kind2, name in tools if kind2 != kind)
        wrong = agent_policy.decide_tool(view, other, write=(kind != "write"),
                                         irreversible=False)
        assert not wrong.allowed, f"{other} was allowed across the read/write split"


def test_the_approval_floor_is_the_destructive_set_plus_invites():
    """The floor names real tools, and it covers everything mcp_tools calls destructive."""
    assert DESTRUCTIVE <= agent_policy.APPROVAL_FLOOR
    registry = set(READ_TOOLS) | set(WRITE_TOOLS)
    assert registry >= agent_policy.APPROVAL_FLOOR
    # An invite is not destructive but it is authority: it belongs in the floor.
    assert "teamMemberAdd" in agent_policy.APPROVAL_FLOOR


def test_an_irreversible_tool_needs_approval_even_when_it_is_granted(world):
    agent = _agent(world)
    _grant_all_tools(world, agent["id"])
    view = _view(world, agent["id"])
    refused = agent_policy.decide_tool(view, "issueDelete", write=True, irreversible=True)
    assert not refused.allowed
    assert "approval" in refused.reason
    # With an approval for that tool the same call is allowed: the floor is a gate,
    # not a permanent ban.
    allowed = agent_policy.decide_tool(view, "issueDelete", write=True, irreversible=True,
                                       approvals={"issueDelete"})
    assert allowed.allowed, allowed.reason
    # A non-destructive tool is unaffected.
    assert agent_policy.decide_tool(view, "issueUpdate", write=True,
                                    irreversible=False).allowed


# --------------------------------------------------------- the dispatcher call site


def _call_tool(world, context: dict, tool_name: str, args: dict):
    """Run one real MCP tool through the real dispatcher with a chosen principal.

    ``Tool.fn`` is the async closure ``build_tools`` builds, which is the dispatcher
    itself: the same principal lookup and the same policy check the MCP server
    reaches. Calling the Tool's own ``run`` would need an MCP request context that
    has nothing to do with what this test is proving.
    """
    import asyncio as _asyncio

    from hoja_server.mcp_tools import build_tools
    from hoja_server.resolvers import build_schema

    tools = {t.name: t for t in build_tools(build_schema(), lambda write: context)}
    return _asyncio.run(tools[tool_name].fn(args))


def test_the_dispatcher_refuses_an_agent_and_leaves_a_human_alone(world):
    """T052: the check runs at the dispatcher, for agent principals only."""
    from mcp.server.mcpserver.exceptions import ToolError

    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]]})
    base = {"store": world["store"], "writer": None, "org_id": world["alpha"]["org_id"],
            "user_id": world["alpha"]["user_id"], "identity_id": None,
            "browser_session": False, "read_only": False}

    with pytest.raises(ToolError) as refused:
        _call_tool(world, {**base, "agent_member_id": agent["id"]}, "hoja_viewer", {})
    assert "refused by this agent's policy" in str(refused.value)
    assert "viewer" in str(refused.value)
    # The refusal is audited, so an operator can see the attempt (WP05's vocabulary).
    with world["engine"].connect() as conn:
        rows = conn.execute(select(audit_event).where(
            audit_event.c.action == "agent.tool.call")).mappings().all()
    assert rows and json.loads(rows[-1]["detail"])["allowed"] is False

    # The same call as a human: no policy applies, and the tool runs.
    _grant_all_tools(world, agent["id"])
    _call_tool(world, {**base, "agent_member_id": agent["id"]}, "hoja_viewer", {})
    _call_tool(world, base, "hoja_viewer", {})


# ----------------------------------------------------------------- FR-035 redaction


def test_a_secret_value_never_reaches_activity_audit_or_logs(world):
    """The redaction acceptance, proved at each sink rather than in the abstract."""
    agent = _agent(world)
    task_id = _delegated_task(world, agent)
    session_id = world["client"].post(
        "/graphql",
        json={"query": f'query {{ issue(id: "{task_id}") {{ agentSession {{ id }} }} }}'},
        headers={"Authorization": "Bearer " + world["alpha"]["token"]},
    ).json()["data"]["issue"]["agentSession"]["id"]

    body = _post(world["client"], world["alpha"]["token"], f'''
        mutation {{
          agentSecretSet(name: "DEPLOY_KEY", value: "{SENTINEL}")
        }}''')
    assert body.get("data", {}).get("agentSecretSet") is True, body
    _grant_all_tools(world, agent["id"])
    _set_policy(world, agent["id"], {"secrets": ["DEPLOY_KEY"],
                                     "teams": [world["alpha"]["team_id"]]})

    _queued_lease(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert "lease" in response, response
    lease = response["lease"]

    # 1. The lease payload carries the value (that is the point of a secret).
    assert lease["policy"]["secrets"] == {"DEPLOY_KEY": SENTINEL}
    # 2. The prompt does not: a secret travels only in the payload that the runner
    # injects into the environment, because the prompt is what lands in the
    # transcript.
    assert lease["prompt"], "the prompt is the task text, and it is not empty"
    assert SENTINEL not in lease["prompt"]

    # 3. The transcript scrubs it: the agent writes its environment into a thought.
    from hoja_server import agent_sessions

    with world["engine"].begin() as conn:
        agent_sessions.append_activity(
            conn, world["alpha"]["org_id"], session_id, "action",
            body=f"export DEPLOY_KEY={SENTINEL}", payload={"env": {"DEPLOY_KEY": SENTINEL}})
    with world["engine"].connect() as conn:
        stored = conn.execute(select(agent_activity).where(
            agent_activity.c.session_id == session_id)).mappings().all()
    assert stored, "the activity was not written"
    assert all(SENTINEL not in (row["body"] or "") for row in stored)
    assert all(SENTINEL not in (row["payload"] or "") for row in stored)
    assert logging_setup.REDACTED in stored[-1]["body"]

    # 4. The event stream scrubs it: a runner echoing its environment.
    events = world["client"].post("/runner/events", json={
        "lease_id": lease["lease_id"], "fence": lease["fence"], "token": lease["token"],
        "events": [{"seq": 1, "kind": "log", "payload": {"line": f"key={SENTINEL}"}}],
    }, headers={"Authorization": f"Bearer {identity['token']}"})
    assert events.status_code == 200, events.text
    with world["engine"].connect() as conn:
        activity = conn.execute(select(issue_activity).where(
            issue_activity.c.issue_id == task_id)).mappings().all()
    assert activity and all(SENTINEL not in (row["changes"] or "") for row in activity)

    # 5. The audit trail scrubs it, even when a call site quotes a payload.
    from hoja_server import audit

    seq = audit.record(world["engine"], organization_id=world["alpha"]["org_id"],
                       action="agent.tool.call", actor_kind="agent", actor_id=agent["userId"],
                       detail={"quote": f"echo {SENTINEL}"})
    with world["engine"].connect() as conn:
        row = conn.execute(select(audit_event).where(audit_event.c.seq == seq)).mappings().one()
    assert SENTINEL not in row["detail"]
    assert logging_setup.REDACTED in row["detail"]

    # 6. The logger scrubs it, through the real handler. The handler this installs
    # is removed again: `configure_logging` is idempotent and would otherwise leave
    # a dead stream attached that a later test's own `configure_logging` cannot
    # replace (test_auth_telemetry asserts on exactly that stream).
    import logging as _logging

    stream = io.StringIO()
    app_logger = _logging.getLogger("hoja_server")
    try:
        logger = logging_setup.configure_logging("info", stream=stream)
        logger.info("connected with %s", SENTINEL)
    finally:
        for handler in list(app_logger.handlers):
            app_logger.removeHandler(handler)
    assert SENTINEL not in stream.getvalue()
    assert logging_setup.REDACTED in stream.getvalue()

    # Nothing anywhere in the database holds the value except the sealed column.
    with world["engine"].connect() as conn:
        for table, column in ((agent_activity, "body"), (agent_activity, "payload"),
                              (audit_event, "detail"), (issue_activity, "changes")):
            for value in conn.execute(select(table.c[column])).scalars():
                assert SENTINEL not in (value or ""), f"{table.name}.{column} leaked the secret"
        sealed = conn.execute(select(agent_secret.c.secret_sealed)).scalar_one()
    assert SENTINEL not in sealed, "the stored secret must be sealed, not plaintext"


# ------------------------------------------------------------------- FR-036 audit


def test_a_policy_change_writes_an_audit_event_with_before_and_after(world):
    agent = _agent(world)
    _set_policy(world, agent["id"], {"readTools": ["viewer"],
                                     "teams": [world["alpha"]["team_id"]]})
    _set_policy(world, agent["id"], {"writeTools": ["taskUpdate"],
                                     "teams": [world["alpha"]["team_id"]]})
    with world["engine"].connect() as conn:
        rows = conn.execute(select(audit_event).where(and_(
            audit_event.c.action == "agent.policy.change",
            audit_event.c.target_id == agent["id"]))).mappings().all()
    assert len(rows) == 2
    first, second = (json.loads(row["detail"]) for row in rows)
    assert first["before"] is None and first["after"]["readTools"] == ["viewer"]
    assert second["before"]["readTools"] == ["viewer"]
    assert second["after"]["writeTools"] == ["taskUpdate"]
    # Created at version 1, then one edit: the version is the lease's witness.
    assert first["after"]["version"] == 1
    assert second["after"]["version"] == 2, "the version moved on the edit"
    # The actor is named, and it is the admin who made the change (WP05's rule).
    assert rows[-1]["actor_kind"] == "human"
    assert rows[-1]["actor_id"] == world["alpha"]["user_id"]


def test_only_an_admin_can_read_or_write_a_policy(world):
    agent = _agent(world)
    member = world["store"].get_user(world["alpha"]["org_id"], world["alpha"]["user_id"])
    assert member["admin"] is True
    with world["engine"].begin() as conn:
        conn.execute(agent_member.update().where(agent_member.c.id == agent["id"]).values(
            capacity=1))
    # A non-admin principal is refused by the resolver itself.
    from hoja_server import agent_policy as module

    class _Store:
        engine = world["engine"]

        def get_user(self, org, uid):
            return {"id": uid, "admin": False}

    with pytest.raises(Exception) as denied:
        module._require_admin(_Store(), world["alpha"]["org_id"], "someone")
    assert "FORBIDDEN" in str(getattr(denied.value, "extensions", {}))


# ------------------------------------------------------------- default branch rule


def test_a_policy_is_invisible_to_another_workspace(world):
    """The policy and its secrets are tenant rows, not workspace-wide settings.

    Under ``HOJA_TEST_RLS`` the app runs as a role with no BYPASSRLS, so this is
    also the probe that the two revision-69 tables are bound: a table whose policy
    was missing would answer here instead of refusing.
    """
    agent = _agent(world)
    _set_policy(world, agent["id"], {"readTools": ["viewer"],
                                     "teams": [world["alpha"]["team_id"]]})
    body = _post(world["client"], world["alpha"]["token"],
                 'mutation { agentSecretSet(name: "ALPHA_ONLY", value: "value-in-alpha") }')
    assert body.get("data", {}).get("agentSecretSet") is True, body

    # The other workspace's admin asks for the same member id: no row, no names.
    other = _post(world["client"], world["bravo"]["token"],
                  f'query {{ agentPolicy(agentMemberId: "{agent["id"]}") {{ id version }} }}')
    assert "errors" not in other, other
    assert other["data"]["agentPolicy"] is None
    names = _post(world["client"], world["bravo"]["token"], "query { agentSecretNames }")
    assert names["data"]["agentSecretNames"] == []
    # And alpha still sees its own.
    mine = _post(world["client"], world["alpha"]["token"], "query { agentSecretNames }")
    assert mine["data"]["agentSecretNames"] == ["ALPHA_ONLY"]


def test_the_default_branch_can_never_be_granted(world):
    agent = _agent(world)
    for branch in ("main", "master", "*"):
        body = _set_policy(world, agent["id"], {
            "repos": json.dumps([{"repo": "acme/api", "branches": [branch]}]),
            "teams": [world["alpha"]["team_id"]],
        })
        assert "errors" in body, f"{branch} was accepted as a grant"
        assert "default branch" in body["errors"][0]["message"] or \
               "not grantable" in body["errors"][0]["message"]
    # A feature branch is fine.
    ok = _set_policy(world, agent["id"], {
        "repos": json.dumps([{"repo": "acme/api", "branches": ["feature/x"]}]),
        "teams": [world["alpha"]["team_id"]],
    })
    assert "errors" not in ok, ok

    # And the decision refuses a default branch even for a row edited by hand.
    with world["engine"].begin() as conn:
        conn.execute(agent_policy_table.update().where(
            agent_policy_table.c.agent_member_id == agent["id"]).values(
            repos=json.dumps([{"repo": "acme/api", "branches": ["main", "feature/x"]}])))
    view = _view(world, agent["id"])
    assert not agent_policy.decide_repo(view, "acme/api", "main").allowed
    assert agent_policy.decide_repo(view, "acme/api", "feature/x").allowed
    assert not agent_policy.decide_repo(view, "other/repo", "feature/x").allowed


def test_egress_connect_and_people_are_allowlists(world):
    agent = _agent(world)
    body = _set_policy(world, agent["id"], {"egress": ["*"]})
    assert "errors" in body, "a wildcard egress was accepted"
    _set_policy(world, agent["id"], {
        "egress": ["api.example.com", ".corp.example", "10.0.0.0/8"],
        "connect": json.dumps([{"host": "dev.example", "port": 5432}]),
        "delegators": [world["alpha"]["user_id"]],
        "approvers": [world["alpha"]["user_id"]],
        "teams": [world["alpha"]["team_id"]],
    })
    view = _view(world, agent["id"])
    assert agent_policy.decide_egress(view, "api.example.com").allowed
    assert agent_policy.decide_egress(view, "git.corp.example").allowed
    assert agent_policy.decide_egress(view, "10.1.2.3").allowed
    assert not agent_policy.decide_egress(view, "evil.example").allowed
    assert agent_policy.decide_connect(view, "dev.example", 5432).allowed
    assert not agent_policy.decide_connect(view, "dev.example", 22).allowed
    assert agent_policy.decide_delegate(view, world["alpha"]["user_id"]).allowed
    assert not agent_policy.decide_delegate(view, world["bravo"]["user_id"]).allowed
    assert agent_policy.decide_approve(view, world["alpha"]["user_id"]).allowed


@pytest.mark.parametrize("bad, sentence", [
    ([{"host": "dev.example", "port": 0}], "between 1 and 65535"),
    ([{"host": "dev.example", "port": 70000}], "between 1 and 65535"),
    ([{"host": "dev.example", "port": "not-a-port"}], "must be an integer"),
    ([{"host": "   ", "port": 5432}], "must be named explicitly"),
    # The wildcard is the whole internet, not an allowlist, so it is refused at the policy row
    # rather than silently filtered at exposure time.
    ([{"host": "*", "port": 5432}], "must be named explicitly"),
])
def test_connect_grants_round_trip_through_the_admin_mutation_and_a_bad_one_is_refused(
        world, bad, sentence):
    """The Connect field on the agent page writes ``connect`` through ``agentPolicyUpdate``.

    What that field is allowed to rely on, one assertion each: the list persists as the pairs the
    server stores, it comes back through ``agentPolicy`` unchanged, ``clean_connect`` refuses a
    malformed entry with a sentence about the field, a refused write leaves the stored list and the
    version alone and adds no audit row, and an accepted write is in the ledger with before/after.
    """
    agent = _agent(world)
    first = [{"host": "dev.example", "port": 5432}, {"host": "localhost", "port": 3000}]
    written = _set_policy(world, agent["id"], {"connect": first})
    assert "errors" not in written, written
    assert written["data"]["agentPolicyUpdate"]["success"] is True
    read = _post(world["client"], world["alpha"]["token"],
                 f'query {{ agentPolicy(agentMemberId: "{agent["id"]}") '
                 f'{{ connect version }} }}')
    assert "errors" not in read, read
    assert read["data"]["agentPolicy"]["connect"] == first, (
        "connect must round-trip as the list the editor sent, in the order it sent it")

    # A refusal is the server's: the field shows this sentence, and nothing is stored.
    refused = _set_policy(world, agent["id"], {"connect": bad})
    assert "errors" in refused, f"{bad} was accepted"
    error = refused["errors"][0]
    assert sentence in error["message"], error
    assert error.get("extensions", {}).get("code") == "BAD_USER_INPUT", error
    assert error.get("extensions", {}).get("field") == "connect", error
    after_refusal = _post(world["client"], world["alpha"]["token"],
                          f'query {{ agentPolicy(agentMemberId: "{agent["id"]}") '
                          f'{{ connect version }} }}')["data"]["agentPolicy"]
    assert after_refusal["connect"] == first, "a refused write changed the stored grants"
    assert after_refusal["version"] == read["data"]["agentPolicy"]["version"], (
        "a refused write moved the policy version")

    # The empty list is a write, not an omission: that is how the editor removes the last grant.
    cleared = _set_policy(world, agent["id"], {"connect": []})
    assert "errors" not in cleared, cleared
    assert _post(world["client"], world["alpha"]["token"],
                 f'query {{ agentPolicy(agentMemberId: "{agent["id"]}") {{ connect }} }}'
                 )["data"]["agentPolicy"]["connect"] == []

    with world["engine"].connect() as conn:
        rows = conn.execute(select(audit_event.c.detail).where(and_(
            audit_event.c.action == "agent.policy.change",
            audit_event.c.target_id == agent["id"]))).scalars().all()
    assert len(rows) == 2, "two accepted writes, and neither refusal is a ledger row"
    details = [json.loads(row) for row in rows]
    assert details[0]["before"] is None and details[0]["after"]["connect"] == first
    assert details[1]["before"]["connect"] == first and details[1]["after"]["connect"] == []


def test_scope_refuses_a_task_the_agent_may_not_touch(world):
    agent = _agent(world)
    _set_policy(world, agent["id"], {"teams": [world["alpha"]["team_id"]],
                                     "tasksDelegatedOnly": True})
    view = _view(world, agent["id"])
    assert agent_policy.decide_task(view, delegated=True,
                                    team=world["alpha"]["team_id"]).allowed
    refused = agent_policy.decide_task(view, delegated=False,
                                       team=world["alpha"]["team_id"])
    assert not refused.allowed and "delegated" in refused.reason
    # With the flag off, an in-scope task is allowed and an out-of-scope one is not.
    _set_policy(world, agent["id"], {"tasksDelegatedOnly": False})
    view = _view(world, agent["id"])
    assert agent_policy.decide_task(view, delegated=False,
                                    team=world["alpha"]["team_id"]).allowed
    assert not agent_policy.decide_task(view, delegated=False, team="other-team").allowed


# ---------------------------------------------------- lease time: scope, concurrency, cost


def _policy_ready(world, agent: dict, **limits) -> dict:
    document = {"teams": [world["alpha"]["team_id"]],
                "readTools": ["viewer"], "writeTools": ["taskUpdate"]}
    document.update(limits)
    _set_policy(world, agent["id"], document)
    return document


def test_a_lease_is_refused_when_the_agent_has_no_policy(world):
    agent = _agent(world)
    task_id = _delegated_task(world, agent)
    queued = _queued_lease(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert response.get("lease") is None
    # 'stop' is the action for a missing policy: the attempt leaves the queue, so
    # the runner does not re-examine it forever. That is why it is not in the
    # refused list (which only reports attempts still queued) - the audit row and
    # the lease state are the record.
    assert "policy_no_policy" not in _reasons(response)
    with world["engine"].connect() as conn:
        assert conn.execute(select(agent_lease.c.state).where(
            agent_lease.c.id == queued["id"])).scalar() == "canceled"
        audited = [json.loads(row["detail"]) for row in conn.execute(select(audit_event).where(
            audit_event.c.action == "agent.lease.refuse")).mappings()]
    assert any(entry.get("reason") == "policy_no_policy" for entry in audited)
    with world["engine"].connect() as conn:
        assert conn.execute(select(agent_lease.c.state)).scalar() == "canceled"


def test_a_lease_beyond_max_concurrency_is_queued_then_stopped(world):
    """max concurrency 1 with a live run: the second lease is refused per policy.

    The agent member's own capacity is 2, so WP06 admits both sessions and queues
    both leases. That is deliberate: it isolates the policy's ceiling from the
    schedule check's, which is the point of checking at lease time as well.
    """
    agent = _agent(world, capacity=2)
    first = _delegated_task(world, agent, title="First")
    second = _delegated_task(world, agent, title="Second")
    _queued_lease(world, first)
    _queued_lease(world, second)
    _policy_ready(world, agent, maxConcurrency=1, budgetAction="queue")
    identity = _enroll(world)
    assert _poll(world, identity).get("lease") is not None
    queued = _poll(world, identity)
    assert queued.get("lease") is None
    assert "policy_concurrency" in _reasons(queued)
    with world["engine"].connect() as conn:
        state = conn.execute(select(agent_lease.c.state).where(
            agent_lease.c.task_id == second)).scalar()
    assert state == "queued", "the queue action must leave the attempt queued"

    # The same situation with the 'stop' action cancels it instead.
    _set_policy(world, agent["id"], {"budgetAction": "stop",
                                     "teams": [world["alpha"]["team_id"]]})
    stopped = _poll(world, identity)
    assert stopped.get("lease") is None
    # 'stop' takes the attempt out of the queue, so it is the row and the audit
    # trail that carry the refusal, not the poll's refused list.
    assert "policy_concurrency" not in _reasons(stopped)
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_lease.c.state, agent_lease.c.cancel_reason).where(
            agent_lease.c.task_id == second)).mappings().one()
    assert row["state"] == "canceled"
    assert row["cancel_reason"] == "policy_concurrency"
    # And the refusal is in the audit trail.
    with world["engine"].connect() as conn:
        reasons = [json.loads(row["detail"]).get("reason") for row in conn.execute(
            select(audit_event).where(
                audit_event.c.action == "agent.lease.refuse")).mappings()]
    assert "policy_concurrency" in reasons


def test_a_lease_beyond_the_run_minute_budget_is_refused(world):
    agent = _agent(world)
    task_id = _delegated_task(world, agent)
    _policy_ready(world, agent, runMinuteLimit=5)
    _queued_lease(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert "lease" in response, response
    lease = response["lease"]
    assert lease["policy"]["run_minute_limit"] == 5
    # Finish it, then backdate the run so this month's minutes exceed the limit.
    world["client"].post("/runner/finish", json={
        "lease_id": lease["lease_id"], "run_id": lease["run_id"], "fence": lease["fence"],
        "token": lease["token"], "state": "completed", "summary": "done",
        "idempotency_key": f"{lease['run_id']}:1", "effects": [],
    }, headers={"Authorization": f"Bearer {identity['token']}"})
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    with world["engine"].begin() as conn:
        # Two hours inside this calendar month, so the minutes are provably spend
        # rather than a number the test asserts about a lease it also wrote.
        conn.execute(agent_lease.update().where(agent_lease.c.id == lease["lease_id"]).values(
            granted_at=(now - timedelta(hours=3)).isoformat(),
            finished_at=(now - timedelta(hours=1)).isoformat()))
    _set_policy(world, agent["id"], {"runMinuteLimit": 5,
                                     "teams": [world["alpha"]["team_id"]]})
    # The 120 minutes already run are this month's spend, so the next lease is refused.
    with world["engine"].connect() as conn:
        assert agent_policy.run_minutes_this_month(
            conn, world["alpha"]["org_id"], agent["userId"]) > 5
        verdict = agent_policy.check_lease(
            conn, world["alpha"]["org_id"],
            {"task_id": task_id}, now=datetime.now(timezone.utc))
    assert not verdict.allowed
    assert verdict.reason == agent_policy.RUN_MINUTES
    assert verdict.action == "queue"


def test_a_lease_beyond_the_credit_budget_is_refused(world):
    agent = _agent(world)
    task_id = _delegated_task(world, agent)
    _policy_ready(world, agent, monthlyCreditBudget=0, budgetAction="stop")
    queued = _queued_lease(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert response.get("lease") is None
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_lease.c.state, agent_lease.c.cancel_reason).where(
            agent_lease.c.id == queued["id"])).mappings().one()
        audited = [json.loads(row["detail"]) for row in conn.execute(select(audit_event).where(
            audit_event.c.action == "agent.lease.refuse")).mappings()]
    # 'stop' cancels the exhausted attempt, and the audit row names the reason.
    assert row["state"] == "canceled"
    assert row["cancel_reason"] == "policy_budget"
    assert any(entry.get("reason") == "policy_budget" for entry in audited)


def test_the_lease_payload_carries_egress_and_a_scoped_tool_list(world):
    agent = _agent(world)
    task_id = _delegated_task(world, agent)
    _policy_ready(world, agent, egress=["api.example.com"],
                  connect=json.dumps([{"host": "dev.example", "port": 5432}]))
    _queued_lease(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert "lease" in response, response
    lease = response["lease"]
    policy = lease["policy"]
    assert policy["egress"] == ["api.example.com"]
    assert policy["connect"] == [{"host": "dev.example", "port": 5432}]
    assert policy["tools"]["read"] == ["viewer"]
    assert policy["tools"]["write"] == ["taskUpdate"]
    assert "issueDelete" in policy["tools"]["approval"]
    assert lease["prompt"], "the prompt is the task text, and it is not empty"
    with world["engine"].connect() as conn:
        assert conn.execute(select(agent_lease.c.policy_version).where(
            agent_lease.c.id == lease["lease_id"])).scalar() == policy["version"]


def test_a_policy_edited_under_a_live_lease_cancels_it_on_the_next_heartbeat(world):
    agent = _agent(world)
    task_id = _delegated_task(world, agent)
    _policy_ready(world, agent)
    _queued_lease(world, task_id)
    identity = _enroll(world)
    response = _poll(world, identity)
    assert "lease" in response, response
    lease = response["lease"]
    body = {"lease_id": lease["lease_id"], "run_id": lease["run_id"],
            "fence": lease["fence"], "token": lease["token"]}

    quiet = world["client"].post("/runner/heartbeat", json=body,
                                 headers={"Authorization": f"Bearer {identity['token']}"})
    assert quiet.json()["cancel"] is False

    _set_policy(world, agent["id"], {"readTools": ["viewer"]})
    after = world["client"].post("/runner/heartbeat", json=body,
                                 headers={"Authorization": f"Bearer {identity['token']}"})
    assert after.status_code == 200, after.text
    assert after.json()["cancel"] is True
    assert after.json()["cancel_reason"] == runner_api.POLICY_CHANGED
    assert after.json()["policy_version"] == lease["policy"]["version"]


def test_a_human_lease_with_no_agent_is_unchanged(world):
    """A task nobody delegated keeps exactly the behaviour it had before WP12."""
    tenant = world["alpha"]
    with world["engine"].connect() as conn:
        task_id = conn.execute(select(issue.c.id).where(and_(
            issue.c.organization_id == tenant["org_id"],
            issue.c.delegate_id.is_(None))).order_by(issue.c.identifier)).scalars().first()
    identity = _enroll(world)
    _queue(world, task_id)
    lease = _poll(world, identity).get("lease")
    assert lease is not None
    assert lease["policy"] is None


# ------------------------------------------------------------------- migration 68 -> 69


def test_revision_69_applies_to_a_68_database_twice(tmp_path):
    """The upgrade path is 68 -> 69 on an existing database, not a fresh create.

    Both runs must exit clean: the second is the idempotence proof, and it is the
    run that catches a migration which adds a column or table unconditionally.
    """
    from hoja_server.db import (
        CURRENT_SCHEMA_REVISION,
        applied_schema_revision,
        make_engine,
        migrate,
    )
    from tests import legacy_schema

    # WP14 took revision 70, so this suite's upgrade path lands one revision later.
    assert CURRENT_SCHEMA_REVISION >= 69
    engine = make_engine(tmp_path / "policy-upgrade.db")
    legacy_schema.create_schema(engine, 68)
    with engine.begin() as conn:
        for name in ("agent_secret", "agent_policy"):
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
    assert applied_schema_revision(engine) == 68

    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        lease_columns = {c["name"] for c in inspect(conn).get_columns("agent_lease")}
    assert {"agent_policy", "agent_secret"} <= tables
    assert "policy_version" in lease_columns

    # Second run: idempotent, and the revision does not move.
    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        assert "policy_version" in {c["name"] for c in inspect(conn).get_columns("agent_lease")}


# --------------------------------------------------- revision 69 under row-level security


def test_a_migrated_database_binds_the_new_tables_and_ready_answers_200(tmp_path):
    """`hoja-serve migrate` must bind revision 69's tables, and `/ready` must agree.

    WP01 (revision 68's fail-closed RLS re-land) makes ``/ready`` answer 503 when any
    table is unclassified or any tenant table is unbound, and ``db.migrate`` calls
    ``rls_policy.heal`` to bind what a revision just created. This test takes the
    path a deployment takes — migrate as the owner, then serve as a role with no
    BYPASSRLS that owns nothing — and asserts the two new tables are classified as
    tenant rows, carry the ``hoja_tenant`` policy, and leave ``/ready`` at 200.

    Skipped without PostgreSQL: on SQLite there is no row-level security to bind.
    """
    if not rls_mode.ENABLED:
        pytest.skip("needs PostgreSQL (HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL)")
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from hoja_server import rls_policy
    from hoja_server.db import make_engine, migrate

    owner_url, schema = rls_mode.new_schema_url()
    owner = make_engine(owner_url)
    migrate(owner)  # heal runs here, as the owner credential does in a deployment

    role, password = f"hoja_wp12_{schema[-6:]}", "wp12-probe"
    with owner.begin() as conn:
        conn.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT "
                          f"PASSWORD '{password}'"))
        # Grants only: the policies under test are the ones `migrate` installed.
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
        conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA '
                          f'"{schema}" TO {role}'))
        conn.execute(text(f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA '
                          f'"{schema}" TO {role}'))
        conn.execute(text(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA "{schema}" TO {role}'))
    restricted_url = make_url(owner_url).set(username=role, password=password).render_as_string(
        hide_password=False)
    restricted = make_engine(restricted_url)
    try:
        with restricted.connect() as conn:
            rows = {row["table_name"]: row for row in rls_policy.tables(conn)}
            report = rls_policy.report(conn)
        for table in ("agent_policy", "agent_secret"):
            assert table in rows, f"{table} was not classified at all"
            assert rows[table]["kind"] == rls_policy.DIRECT, rows[table]
            assert rows[table]["rls"] is True, f"{table} has no row-level security"
            assert rows[table]["policy"] is True, f"{table} has no hoja_tenant policy"
        assert report["bound"] is True, f"/ready would answer 503: {report['gaps']}"

        app = create_app(None, database_url=restricted_url, open_mode=False)
        response = TestClient(app).get("/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ready"}
    finally:
        restricted.dispose()
        with owner.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {role}; DROP ROLE IF EXISTS {role}"))
        owner.dispose()
