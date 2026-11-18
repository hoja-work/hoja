"""Which harness a delegated run executes under (HOJA-602-A-22).

Before this work package the answer was a constant: ``agent_schedule.enqueue_admitted``
queued every delegation for ``harness or "pi"``, and ``runner_api._grantable`` only
gives a lease to a runner that declared that exact harness. A host that enrolled only
``codex`` therefore sat on a ``pi`` lease forever - measured in HOJA-647 (A-19): the
session stayed ``queued`` for 80 s against a lease nobody could take.

The acceptance list, one test group each:

===========================================================  ==================================
test                                                          acceptance
===========================================================  ==================================
no policy -> ``pi``, and a ``pi`` runner leases it             "an agent member, or its policy,
                                                              carries a default harness"
the policy's default is what is queued, and a codex runner    ``enqueue_admitted`` uses that
takes it                                                      harness, and a runner that
                                                              advertises it leases the job
a runner that does not advertise it is never granted it       "a runner that doesn't
                                                              advertise it never gets the job"
an unknown name, an alias and a default outside the           "validated against the profiles the
allowlist are all refused                                     server knows"
revision 72 upgrades a 71 database, twice                     "if a migration is needed it's
                                                              revision 72, classified in
                                                              rls_policy.py"
===========================================================  ==================================

SQLite by default and PostgreSQL under ``HOJA_TEST_RLS=1``, like every other suite in
this directory: the harness columns sit on ``agent_policy``, whose tenant binding is
what ``/ready`` answers 503 about, so the migration test re-reads the classification
from the catalog rather than trusting the model.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import and_, inspect, select
from starlette.testclient import TestClient

from hoja_server import agent_policy, logging_setup, runner_api
from hoja_server.app import create_app
from hoja_server.db import agent_lease, agent_session, seed_tenant
from hoja_server.store import Store
from tests import rls_mode

#: The harness every delegation queued for before revision 72.
FALLBACK = "pi"


def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "harness-selection-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    logging_setup.forget_secrets()
    engine, kwargs = rls_mode.workspace(tmp_path, "harness-selection")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    yield {"engine": engine, "client": TestClient(app), "alpha": alpha,
           "store": Store(engine)}
    logging_setup.forget_secrets()


# --------------------------------------------------------------------------- helpers


def _agent(world, *, name: str = "Scout") -> dict:
    body = _post(world["client"], world["alpha"]["token"], f'''
        mutation {{
          agentMemberCreate(input: {{ name: "{name}", capacity: 1 }}) {{
            success agentMember {{ id userId }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]


def _input(document: dict) -> str:
    """Render an input document as GraphQL input syntax."""
    rendered = []
    for key, value in document.items():
        if isinstance(value, list):
            inner = ", ".join(json.dumps(item) for item in value)
            rendered.append(f"{key}: [{inner}]")
        else:
            rendered.append(f"{key}: {json.dumps(value)}")
    return ", ".join(rendered)


def _policy(world, member_id: str, document: dict) -> dict:
    """Write a policy through the real admin mutation, validation included."""
    return _post(world["client"], world["alpha"]["token"], f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}", input: {{ {_input(document)} }}) {{
            success
            agentPolicy {{ defaultHarness harnessAllowlist version }}
          }}
        }}''')


def _scoped_policy(world, member_id: str, **extra) -> dict:
    """A policy that grants this tenant's team, so WP12's lease gate lets a run through.

    The scope grant is not what these tests are about, but a policy with no team and no
    project is refused at claim time (WP12, ``OUT_OF_SCOPE``), which would hide the
    harness question behind an unrelated refusal.
    """
    document = {"teams": [world["alpha"]["team_id"]]}
    document.update(extra)
    return _policy(world, member_id, document)


def _delegated_task(world, agent: dict, *, title: str = "Delegated") -> str:
    body = _post(world["client"], world["alpha"]["token"], f'''
        mutation {{
          issueCreate(input: {{ teamId: "{world["alpha"]["team_key"]}",
            title: {json.dumps(title)}, delegateId: "{agent["userId"]}" }}) {{
            success issue {{ id }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]["id"]


def _enroll(world, *, name: str, harnesses) -> dict:
    """A runner that declared exactly ``harnesses`` at enrollment."""
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=world["alpha"]["org_id"],
        created_by=world["alpha"]["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment, "harnesses": list(harnesses)})
    assert response.status_code == 200, response.text
    return response.json()


def _poll(world, identity: dict) -> dict:
    response = world["client"].post(
        "/runner/lease", json={"runner_id": identity["runner_id"], "poll_timeout_ms": 0},
        headers={"Authorization": f"Bearer {identity['token']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _latest_lease(world, task_id: str) -> dict:
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_lease).where(and_(
            agent_lease.c.organization_id == world["alpha"]["org_id"],
            agent_lease.c.task_id == task_id,
        )).order_by(agent_lease.c.fence.desc())).mappings().first()
    assert row is not None, "the delegation queued no lease for this task"
    return dict(row)


def _session_for(world, task_id: str) -> dict:
    with world["engine"].connect() as conn:
        row = conn.execute(select(agent_session).where(and_(
            agent_session.c.organization_id == world["alpha"]["org_id"],
            agent_session.c.issue_id == task_id,
        ))).mappings().first()
    assert row is not None, "the delegation opened no session for this task"
    return dict(row)


# ----------------------------------------------------------- the chain, rung by rung


def test_a_policy_that_names_no_harness_still_queues_pi_and_a_pi_runner_leases_it(world):
    """The default is the pre-A-22 behaviour, byte for byte: an agent with no opinion
    queues for ``pi``, and the ``pi`` runner that already exists takes the job.

    The policy here grants scope and nothing else, because WP12 refuses a lease to an
    agent with *no* policy row at all (deny by default) - that refusal is unchanged by
    this work package and would hide the harness question behind it.
    """
    agent = _agent(world)
    written = _scoped_policy(world, agent["id"])
    assert "errors" not in written, written
    assert written["data"]["agentPolicyUpdate"]["agentPolicy"]["defaultHarness"] is None
    task_id = _delegated_task(world, agent)

    lease = _latest_lease(world, task_id)
    assert lease["harness"] == FALLBACK, (
        "a delegation with no harness anywhere must queue for pi, which is what every "
        "existing runner advertises")
    session = _session_for(world, task_id)
    assert session["lease_id"] == lease["id"], "the session and its lease must stay joinable"

    identity = _enroll(world, name="pi-host", harnesses=[FALLBACK])
    payload = _poll(world, identity)
    assert payload.get("lease"), payload
    assert payload["lease"]["harness"] == FALLBACK
    assert payload["lease"]["task_id"] == task_id
    with world["engine"].connect() as conn:
        choice = agent_policy.resolve_harness(
            agent_policy.view_for_member(conn, world["alpha"]["org_id"], agent["id"]))
    assert choice.source == "default", choice


def test_the_policys_default_harness_is_queued_and_a_codex_runner_leases_it(world):
    """The acceptance: a codex-only runner gets a delegation.

    This is the live failure A-19 measured, inverted: the lease names ``codex``, the
    ``codex`` runner is the only one that can take it, and it does.
    """
    agent = _agent(world)
    written = _scoped_policy(world, agent["id"], defaultHarness="codex")
    assert "errors" not in written, written
    assert written["data"]["agentPolicyUpdate"]["agentPolicy"]["defaultHarness"] == "codex"

    task_id = _delegated_task(world, agent)
    lease = _latest_lease(world, task_id)
    assert lease["harness"] == "codex", (
        "the policy's default harness must be the one the lease names, not pi")
    assert lease["state"] == "queued"

    identity = _enroll(world, name="codex-host", harnesses=["codex"])
    payload = _poll(world, identity)
    assert payload.get("lease"), payload
    assert payload["lease"]["harness"] == "codex"
    assert payload["lease"]["task_id"] == task_id

    with world["engine"].connect() as conn:
        granted = dict(conn.execute(select(agent_lease).where(
            agent_lease.c.id == payload["lease"]["lease_id"])).mappings().one())
    assert granted["state"] == "active"
    assert granted["runner_id"] == identity["runner_id"]


def test_a_runner_that_does_not_advertise_the_harness_is_never_granted_it(world):
    """The other half of the acceptance, and the reason the reason has to be explicit.

    A ``pi`` runner polling a workspace whose only queued work wants ``codex`` gets no
    lease - and is told why, by name, in the ``refused`` list. The session stays
    queued: nothing is granted, nothing is consumed, and the work is still there for
    the runner that can take it.
    """
    agent = _agent(world)
    _scoped_policy(world, agent["id"], defaultHarness="codex")
    task_id = _delegated_task(world, agent)

    pi_host = _enroll(world, name="pi-host", harnesses=[FALLBACK])
    payload = _poll(world, pi_host)
    assert not payload.get("lease"), "a pi-only runner was handed work queued for codex"
    refused = payload.get("refused") or []
    entry = next((row for row in refused if row["task_id"] == task_id), None)
    assert entry is not None, payload
    assert entry["harness"] == "codex"
    assert entry["reason"] == runner_api.ERROR_HARNESS_NOT_ALLOWED

    assert _latest_lease(world, task_id)["state"] == "queued"
    codex_host = _enroll(world, name="codex-host", harnesses=["codex"])
    taken = _poll(world, codex_host)
    assert taken.get("lease"), taken
    assert taken["lease"]["task_id"] == task_id


def test_the_session_read_says_which_harness_the_run_is_waiting_for(world):
    """The admin side of "stays queued with a clear reason".

    ``queue_reason`` says why the schedule queued the session; it cannot say which
    runner the lease now waits for. ``AgentSession.harness`` is that fact, read from
    the lease rather than stored twice.
    """
    agent = _agent(world)
    _scoped_policy(world, agent["id"], defaultHarness="opencode")
    _delegated_task(world, agent, title="Waits for opencode")

    body = _post(world["client"], world["alpha"]["token"], """
        query { workspaceAgentSessions(first: 5) {
          nodes { id state harness leaseId } } }""")
    assert "errors" not in body, body
    nodes = body["data"]["workspaceAgentSessions"]["nodes"]
    assert len(nodes) == 1, nodes
    assert nodes[0]["state"] == "queued"
    assert nodes[0]["harness"] == "opencode"
    assert nodes[0]["leaseId"], nodes[0]


def test_a_delegation_that_names_a_harness_outranks_the_policy(world):
    """Rung 1: an explicit request is the answer, when the policy allows it.

    The chain is exercised at its own seam (``enqueue_admitted``'s ``harness``
    argument): no production caller passes one today, which is exactly why the rung is
    worth a test - otherwise the preference would be untested code that silently falls
    back the first time somebody uses it.
    """
    from hoja_server import agent_schedule

    agent = _agent(world)
    _scoped_policy(world, agent["id"], defaultHarness="codex",
                   harnessAllowlist=["codex", "gemini"])
    task_id = _delegated_task(world, agent)
    first = _latest_lease(world, task_id)
    assert first["harness"] == "codex"

    session_id = _session_for(world, task_id)["id"]
    with world["engine"].begin() as conn:
        # Order matters: agent_session.lease_id references the lease, so the session
        # lets go before the row it points at can go.
        conn.execute(agent_session.update().where(
            agent_session.c.id == session_id).values(lease_id=None))
        conn.execute(agent_lease.delete().where(agent_lease.c.id == first["id"]))
    requeued = agent_schedule.enqueue_admitted(world["engine"], _session_for(world, task_id),
                                              harness="gemini")
    second = _latest_lease(world, task_id)
    assert second["harness"] == "gemini", second
    assert second["id"] == requeued["lease_id"]
    assert requeued["harness"] == "gemini"


# ------------------------------------------------------------------- what is refused


def test_an_unknown_harness_is_refused_and_nothing_is_stored(world):
    """A name no profile has is a lease no runner can take, so it never reaches a row."""
    agent = _agent(world)
    body = _policy(world, agent["id"], {"defaultHarness": "gpt-5-turbo"})
    assert "errors" in body, body
    error = body["errors"][0]
    assert error["extensions"]["code"] == "BAD_USER_INPUT"
    assert error["extensions"]["field"] == "defaultHarness"
    assert "not a harness profile" in error["message"]

    with world["engine"].connect() as conn:
        row = agent_policy.row_for_member(conn, world["alpha"]["org_id"], agent["id"])
    assert row is None or row.get("default_harness") is None, row

    # The same refusal at the queue's own seam: an explicit request for a name the
    # server does not know cannot silently become pi.
    with pytest.raises(agent_policy.HarnessError):
        agent_policy.resolve_harness(None, requested="gpt-5-turbo")


def test_an_alias_is_refused_with_the_profile_name_it_stands_for(world):
    """``claude`` is a runner-side alias, not a harness. A lease names the real one."""
    agent = _agent(world)
    body = _policy(world, agent["id"], {"defaultHarness": "claude"})
    assert "errors" in body, body
    message = body["errors"][0]["message"]
    assert "claude-code" in message, message


def test_a_default_outside_the_allowlist_is_refused_in_one_write(world):
    """The pair is checked together, so the contradiction cannot be stored either way."""
    agent = _agent(world)
    body = _policy(world, agent["id"],
                   {"defaultHarness": "codex", "harnessAllowlist": [FALLBACK]})
    assert "errors" in body, body
    assert body["errors"][0]["extensions"]["field"] == "defaultHarness"
    with world["engine"].connect() as conn:
        assert agent_policy.row_for_member(conn, world["alpha"]["org_id"], agent["id"]) is None


def test_a_default_outside_the_allowlist_is_refused_across_two_writes(world):
    """The half the single-document check cannot see: yesterday's default, today's list."""
    agent = _agent(world)
    assert "errors" not in _policy(world, agent["id"], {"defaultHarness": "codex"})
    body = _policy(world, agent["id"], {"harnessAllowlist": ["gemini"]})
    assert "errors" in body, body
    assert body["errors"][0]["extensions"]["field"] == "defaultHarness"
    with world["engine"].connect() as conn:
        row = agent_policy.row_for_member(conn, world["alpha"]["org_id"], agent["id"])
    assert row["harness_allowlist"] in (None, "[]"), (
        f"the refused allowlist must not be stored: {row['harness_allowlist']!r}")
    assert row["default_harness"] == "codex"


def test_a_delegation_cannot_ask_for_a_harness_outside_the_allowlist(world):
    """Rung 1 is bounded by the allowlist, not above it."""
    view = agent_policy.PolicyView(agent_member_id="m", version=1,
                                   harness_allowlist=frozenset({FALLBACK}))
    with pytest.raises(agent_policy.HarnessError) as refused:
        agent_policy.resolve_harness(view, requested="codex")
    assert "outside this agent's harness allowlist" in str(refused.value)


# ------------------------------------------------------------- the surfaces that read


def test_the_allowlist_alone_chooses_the_harness_when_the_policy_names_none(world):
    """An agent that may only run on codex is the case A-22 exists for.

    No default, allowlist ``{codex}``: the chain's last rung cannot answer with ``pi``
    (the allowlist forbids it), so it takes the allowlist's own first entry, sorted -
    the same deterministic rule HOJA-602-A-20 used to name a runner's enrolled
    default. Never a guess outside what the policy allows.
    """
    agent = _agent(world)
    assert "errors" not in _scoped_policy(world, agent["id"], harnessAllowlist=["codex"])
    task_id = _delegated_task(world, agent)
    assert _latest_lease(world, task_id)["harness"] == "codex"

    view = agent_policy.PolicyView(agent_member_id="m", version=1,
                                   harness_allowlist=frozenset({"opencode", "codex"}))
    assert agent_policy.resolve_harness(view).harness == "codex"
    assert agent_policy.resolve_harness(view).source == "allowlist"


def test_the_graphql_surface_lists_the_profiles_and_round_trips_the_fields(world):
    """The field is readable and writable through the admin API the UI uses."""
    agent = _agent(world)
    names = _post(world["client"], world["alpha"]["token"], "query { agentHarnesses }")
    assert "errors" not in names, names
    assert names["data"]["agentHarnesses"] == sorted(agent_policy.KNOWN_HARNESSES)

    assert "errors" not in _policy(world, agent["id"],
                                   {"defaultHarness": "gemini",
                                    "harnessAllowlist": ["gemini", "codex"]})
    read = _post(world["client"], world["alpha"]["token"], f'''
        query {{ agentPolicy(agentMemberId: "{agent["id"]}") {{
          defaultHarness harnessAllowlist }} }}''')
    assert "errors" not in read, read
    assert read["data"]["agentPolicy"] == {"defaultHarness": "gemini",
                                           "harnessAllowlist": ["codex", "gemini"]}
    # Clearing the default is a real write, and it puts the chain back on pi.
    assert "errors" not in _policy(world, agent["id"], {"defaultHarness": None})
    read = _post(world["client"], world["alpha"]["token"], f'''
        query {{ agentPolicy(agentMemberId: "{agent["id"]}") {{ defaultHarness }} }}''')
    assert read["data"]["agentPolicy"]["defaultHarness"] is None


def test_the_server_knows_exactly_the_profiles_this_repository_ships():
    """The drift guard.

    The profiles travel with the runner, not with the app image, so the server holds a
    copy of their names - and a copy that is not checked is a copy that goes stale. A
    profile added to ``runner/hoja_runnerd/profiles`` without being added here would be
    a harness no policy could ever select.
    """
    import pathlib
    import re

    profiles = pathlib.Path(__file__).resolve().parents[3] / "runner/hoja_runnerd/profiles"
    if not profiles.is_dir():  # pragma: no cover - an installed wheel has no runner/
        pytest.skip(f"the profile TOMLs are not in this checkout ({profiles})")
    names, aliases = set(), {}
    for path in sorted(profiles.glob("*.toml")):
        text = path.read_text(encoding="utf-8")
        names.add(re.search(r'^name = "([^"]+)"', text, re.MULTILINE).group(1))
        found = re.search(r'^aliases = \[(.*?)\]', text, re.MULTILINE)
        for alias in re.findall(r'"([^"]+)"', found.group(1) if found else ""):
            aliases[alias] = re.search(r'^name = "([^"]+)"', text, re.MULTILINE).group(1)
    assert names == set(agent_policy.KNOWN_HARNESSES), (
        f"server {sorted(agent_policy.KNOWN_HARNESSES)} vs profiles {sorted(names)}")
    # The alias map exists only to make a refusal helpful, but a stale one would print
    # a name that is not a profile, so it is checked too.
    for alias, target in agent_policy.HARNESS_ALIASES.items():
        assert aliases.get(alias) == target, (alias, target, aliases.get(alias))


# ------------------------------------------------------------------ revision 72 itself


def test_revision_72_upgrades_a_71_database_twice(tmp_path):
    """The upgrade path is 71 -> 72 on an existing database, run twice.

    The second run is the idempotence proof: it is what catches a migration that adds a
    column unconditionally. The columns are dropped first so the ``ALTER`` branch is
    the one exercised, not a no-op on a database built from today's metadata.
    """
    from hoja_server.db import (
        CURRENT_SCHEMA_REVISION,
        applied_schema_revision,
        make_engine,
        migrate,
    )
    from tests import legacy_schema

    # The ledger is global: a later revision (73, entity link hosts) takes the next
    # number without changing what this proves, so the assertion follows the head.
    assert CURRENT_SCHEMA_REVISION >= 72
    engine = make_engine(tmp_path / "harness-upgrade.db")
    legacy_schema.create_schema(engine, 71)
    with engine.begin() as conn:
        for column in ("default_harness", "harness_allowlist"):
            conn.exec_driver_sql(f"ALTER TABLE agent_policy DROP COLUMN {column}")
    assert applied_schema_revision(engine) == 71
    with engine.connect() as conn:
        before = {column["name"] for column in inspect(conn).get_columns("agent_policy")}
    assert not {"default_harness", "harness_allowlist"} & before

    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("agent_policy")}
    assert {"default_harness", "harness_allowlist"} <= columns

    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION


def test_revision_72_leaves_the_tenant_binding_and_the_ready_probe_as_it_found_them():
    """No new table, so nothing new to classify - and ``/ready`` must still agree.

    ``agent_policy`` was classified ``direct`` by revision 69 (it carries
    ``organization_id``) and ``db.migrate`` binds it with ``rls_policy.heal``, whose
    ``unresolved``/``skipped`` lists are what the readiness probe turns into a 503.
    Asserted from the catalog on real PostgreSQL, because that is the only place the
    classification exists.
    """
    if not rls_mode.ENABLED:
        pytest.skip("needs PostgreSQL (HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL)")
    from hoja_server import rls_policy
    from hoja_server.db import make_engine, migrate

    owner_url, schema = rls_mode.new_schema_url()
    owner = make_engine(owner_url)
    try:
        summary = migrate(owner)
        with owner.connect() as conn:
            kinds = {name: kind for name, kind, _expr in rls_policy.classify(conn, schema)}
            bound = {row["table_name"] for row in rls_policy.tables(conn, schema)}
        assert kinds["agent_policy"] == rls_policy.DIRECT
        assert "agent_policy" in bound
        # Nothing a migration left unclassified or unbound: what /ready reads.
        assert summary["unresolved"] == [], summary
        assert summary["skipped"] == [], summary
    finally:
        owner.dispose()
