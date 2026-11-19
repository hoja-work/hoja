"""The read surfaces the review screens needed (HOJA-602-A-16, WP13 slice 5).

Two reads did not exist on main, and both are workspace-wide views, which makes the
same three properties load-bearing for each:

* ``runners`` / ``agentLeases`` — WP07 is a REST data plane with no read of its own, so
  the Runners screen drew a fixture (``SAMPLE_SURFACES.runners``).
* ``verifierClaims`` / ``agentVerifications`` — WP10 stores claims and verdict rows, and
  the session carries the latest verdict, but nothing served the per-check evidence a
  reviewer needs to see *why* a claim is uncertain.

So, per read:

* a cross-tenant read returns zero rows (the ``organization_id`` predicate is explicit
  *and* enforced by row-level security under ``HOJA_TEST_RLS``);
* a non-admin is refused instead of being handed an empty connection, which on screen
  would read as "no machines" or "nothing is waiting";
* paging walks the set exactly once, newest first.

Plus the two facts the screens render: a runner's status is derived from the last
heartbeat on Hoja's clock and its current lease is the one it holds, and a finished
claim is awaiting until a separate principal signs it — with the verdict it asked for
kept beside the verdict the evidence supported.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import agent_runners, runner_api
from hoja_server.app import create_app
from hoja_server.db import seed_tenant
from hoja_server.store import Store
from tests import rls_mode

RUNNER_FIELDS = """id name organizationId status enabled revokedAt labels harnesses
  lastHeartbeatAt createdAt
  currentLease { id taskId fence runId state attempt harness ttlSeconds leaseUntil
                 issue { identifier title } }"""

CLAIM_FIELDS = """sessionId issueId agentUserId claim claimAt workerModel verdict
  verifiedAt verifierId verifierModel awaiting
  issue { identifier title priority }
  verifications { id verdict requestedVerdict verifierKind verifierModel workerModel
                  rationale createdAt checks { name outcome detail } }"""


def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


def _data(client: TestClient, token: str, query: str) -> dict:
    body = _post(client, token, query)
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two workspaces on one database, plus the app over the real routes."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "runner-read-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    # These tests read the claim queue and then sign a claim by hand; HOJA-712's
    # automatic verifier would have signed it first and the queue would no longer show
    # an unjudged row. Its own behaviour is covered by test_auto_verify_and_pools.py.
    monkeypatch.setenv("HOJA_AUTO_VERIFY", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "runner-read")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "alpha": alpha,
            "bravo": bravo, "store": Store(engine)}


# --------------------------------------------------------------------- helpers
def _enroll(world, *, org: str = "alpha", name: str = "runner-1",
            harnesses: tuple[str, ...] = ("pi",)) -> dict:
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment,
        "harnesses": list(harnesses), "labels": {"pool": "default"}})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(identity: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity['token']}"}


def _agent(client: TestClient, token: str, name: str = "Scout", capacity: int = 4) -> dict:
    return _data(client, token, f'''
        mutation {{
          agentMemberCreate(input: {{name: "{name}", capacity: {capacity}}}) {{
            success agentMember {{ id userId }} }} }}''')["agentMemberCreate"]["agentMember"]


def _grant_policy(client: TestClient, token: str, member_id: str, team_id: str,
                  *, max_concurrency: int = 4) -> dict:
    """WP12: a delegated task is only leaseable under a policy that names its team.

    Deny by default, so a member with no policy gets no lease at all
    (``agent_policy.NO_POLICY``) — which is why every test that wants a granted lease
    grants one first. The slice's own reads do not depend on the policy; the lease
    the screens draw does.
    """
    return _data(client, token, f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}",
                            input: {{teams: ["{team_id}"], maxConcurrency: {max_concurrency}}}) {{
            success }} }}''')["agentPolicyUpdate"]


def _delegate(client: TestClient, token: str, team_id: str, agent_user_id: str,
              title: str, description: str = "") -> dict:
    """A delegation opens the run (WP06) and, when the schedule admits it, its lease."""
    payload = description.replace('"', '\\"').replace("\n", "\\n")
    return _data(client, token, f'''
        mutation {{
          issueCreate(input: {{teamId: "{team_id}", title: "{title}",
                               description: "{payload}",
                               delegateId: "{agent_user_id}"}}) {{
            success issue {{ id identifier agentSession {{ id state admitted leaseId }} }}
          }} }}''')["issueCreate"]["issue"]


def _claim_lease(world, identity: dict) -> dict:
    response = world["client"].post("/runner/lease",
                                    json={"runner_id": identity["runner_id"],
                                          "poll_timeout_ms": 0},
                                    headers=_auth(identity))
    assert response.status_code == 200, response.text
    lease = response.json().get("lease")
    assert lease, "the admitted session's lease should have been granted to this runner"
    return lease


def _finish(world, identity: dict, lease: dict, **extra) -> dict:
    body = {
        "lease_id": lease["lease_id"], "run_id": lease["run_id"],
        "fence": lease["fence"], "token": lease["token"],
        "state": "completed", "summary": "the widget spins",
        "idempotency_key": f"{lease['run_id']}:1",
        "model": "worker-model", "evidence": {"backend": "pytest"},
    }
    body.update(extra)
    response = world["client"].post("/runner/finish", json=body, headers=_auth(identity))
    assert response.status_code == 200, response.text
    return response.json()


def _runners(client: TestClient, token: str, variables: str = "first: 50") -> dict:
    return _data(client, token, f'''
        query {{ runners({variables}) {{
          nodes {{ {RUNNER_FIELDS} }}
          pageInfo {{ hasNextPage hasPreviousPage startCursor endCursor }} }} }}''')["runners"]


def _leases(client: TestClient, token: str, variables: str = "first: 50") -> dict:
    return _data(client, token, f'''
        query {{ agentLeases({variables}) {{
          nodes {{ id taskId runnerId runnerName fence runId state attempt harness
                   leaseUntil grantedAt issue {{ identifier }} }}
          pageInfo {{ hasNextPage hasPreviousPage endCursor }} }} }}''')["agentLeases"]


def _claims(client: TestClient, token: str, variables: str = "first: 50") -> dict:
    return _data(client, token, f'''
        query {{ verifierClaims({variables}) {{
          nodes {{ {CLAIM_FIELDS} }}
          pageInfo {{ hasNextPage hasPreviousPage endCursor }} }} }}''')["verifierClaims"]


def _invite(client: TestClient, token: str, email: str = "second@alpha.test") -> str:
    return _data(client, token, f'''
        mutation {{ memberInvite(input: {{name: "Second", email: "{email}"}}) {{
          success user {{ id }} }} }}''')["memberInvite"]["user"]["id"]


def _member_token(world, org: str) -> str:
    tenant = world[org]
    member_id = _invite(world["client"], tenant["token"])
    _metadata, token = world["store"].create_api_key(tenant["org_id"], member_id,
                                                     "member-key", "read")
    return token


# ----------------------------------------------------------------- runners: tenancy
def test_a_cross_tenant_runner_read_returns_zero_rows(world):
    _enroll(world, org="bravo", name="bravo-runner")
    _enroll(world, org="alpha", name="alpha-runner")

    ours = _runners(world["client"], world["alpha"]["token"])
    assert [node["name"] for node in ours["nodes"]] == ["alpha-runner"]
    # The id cannot be used as a filter to reach across the tenant either.
    bravo_runner_id = _runners(world["client"], world["bravo"]["token"])["nodes"][0]["id"]
    foreign_leases = _data(world["client"], world["alpha"]["token"], f'''
        query {{ agentLeases(runnerId: "{bravo_runner_id}") {{ nodes {{ id }} }} }}''')
    assert foreign_leases["agentLeases"]["nodes"] == []
    # No credential column is served, by name or in the shape.
    assert set(ours["nodes"][0]) == {
        "id", "name", "organizationId", "status", "enabled", "revokedAt", "labels",
        "harnesses", "lastHeartbeatAt", "createdAt", "currentLease"}, ours["nodes"][0]


def test_only_an_admin_reads_the_runner_surface(world):
    _enroll(world)
    member = _member_token(world, "alpha")
    for query in ("query { runners { nodes { id } } }",
                  "query { agentLeases { nodes { id } } }",
                  "query { verifierClaims { nodes { sessionId } } }",
                  "query { agentVerifications { id } }"):
        refused = _post(world["client"], member, query)
        assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN", refused
        assert "admin" in refused["errors"][0]["message"].lower(), refused

    # The admin's own read is served, and an empty workspace is an empty connection
    # rather than a refusal — the two sentences the screen must keep apart.
    assert _runners(world["client"], world["alpha"]["token"])["nodes"]
    empty = _runners(world["client"], world["bravo"]["token"])
    assert empty["nodes"] == [] and empty["pageInfo"]["hasNextPage"] is False


# ------------------------------------------------------------------- runners: shape
def test_a_runner_row_carries_its_status_harnesses_and_current_lease(world):
    identity = _enroll(world, name="mac-mini-01", harnesses=("pi", "generic"))
    agent = _agent(world["client"], world["alpha"]["token"])
    _grant_policy(world["client"], world["alpha"]["token"], agent["id"],
                  world["alpha"]["team_id"])
    _delegate(world["client"], world["alpha"]["token"], world["alpha"]["team_id"],
              agent["userId"], "Rotate the staging credentials")
    lease = _claim_lease(world, identity)

    rows = _runners(world["client"], world["alpha"]["token"])["nodes"]
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "mac-mini-01"
    assert row["organizationId"] == world["alpha"]["org_id"]
    assert row["enabled"] is True and row["revokedAt"] is None
    assert row["harnesses"] == ["generic", "pi"]
    assert row["labels"] == ["pool"]  # a JSON object's keys, sorted
    # A granted lease is Hoja's own evidence of life, so the row is online while holding one.
    # Since HOJA-602-A-20 the poll that granted it is evidence too, so ``last_seen`` is no
    # longer NULL here: this machine reached the lease by long-polling, which is exactly what
    # an idle runner does, and that poll is now recorded. The lease-only branch below is
    # still load-bearing for a row read before any poll landed, so it is asserted there.
    assert row["lastHeartbeatAt"], row
    assert row["status"] == "online", row
    assert agent_runners.runner_status({"enabled": True, "last_seen": None},
                                       lease=lease) == agent_runners.ONLINE
    current = row["currentLease"]
    assert current and current["id"] == lease["lease_id"]
    assert current["state"] == "active" and current["harness"] == "pi"
    assert current["attempt"] == 1 and current["ttlSeconds"] >= runner_api.MIN_LEASE_TTL_S
    assert current["issue"]["identifier"].startswith(world["alpha"]["team_key"])

    # A heartbeat is the other evidence, recorded on the server's clock.
    beat = world["client"].post("/runner/heartbeat", json={
        "lease_id": lease["lease_id"], "run_id": lease["run_id"],
        "fence": lease["fence"], "token": lease["token"]}, headers=_auth(identity))
    assert beat.status_code == 200, beat.text
    beaten = _runners(world["client"], world["alpha"]["token"])["nodes"][0]
    assert beaten["lastHeartbeatAt"], beaten
    assert beaten["status"] == "online", beaten

    # The same lease through its own read, with its runner named: a screen that shows a
    # lease must say which machine holds it.
    leases = _leases(world["client"], world["alpha"]["token"])["nodes"]
    assert [item["id"] for item in leases] == [lease["lease_id"]]
    assert leases[0]["runnerId"] == identity["runner_id"]
    assert leases[0]["runnerName"] == "mac-mini-01"
    assert leases[0]["fence"] == lease["fence"]
    # The state filter is the resolver's, so an unheld state is an empty page.
    assert _leases(world["client"], world["alpha"]["token"],
                   'first: 50, state: "lost"')["nodes"] == []
    # The status filter reads the same derived value the row shows, lease included: this machine
    # holds a lease, so it is online with or without a heartbeat.
    assert [node["name"] for node in
            _runners(world["client"], world["alpha"]["token"], 'first: 50, status: "online"')["nodes"]] == \
        ["mac-mini-01"]
    assert _runners(world["client"], world["alpha"]["token"],
                    'first: 50, status: "offline"')["nodes"] == []
    broken = _post(world["client"], world["alpha"]["token"],
                   'query { runners(status: "sleeping") { nodes { id } } }')
    assert broken["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"


def test_one_runner_read_names_only_this_workspace_machine(world):
    ours = _enroll(world, org="alpha", name="alpha-runner")
    theirs = _enroll(world, org="bravo", name="bravo-runner")
    found = _data(world["client"], world["alpha"]["token"], f'''
        query {{ runner(id: "{ours["runner_id"]}") {{ id name status }} }}''')["runner"]
    assert found == {"id": ours["runner_id"], "name": "alpha-runner", "status": "offline"}
    # Another tenant's id is "no such machine" here, not a row and not a permission
    # message that would confirm it exists.
    foreign = _data(world["client"], world["alpha"]["token"], f'''
        query {{ runner(id: "{theirs["runner_id"]}") {{ id }} }}''')["runner"]
    assert foreign is None


def test_a_revoked_runner_reads_offline_without_its_token(world):
    identity = _enroll(world, name="laptop-adhoc")
    with world["engine"].connect() as conn:
        from hoja_server.db import runner
        before = dict(conn.execute(select(runner).where(
            runner.c.id == identity["runner_id"])).mappings().one())
    assert before["lease_token_hash"] and before["token_hint"]
    assert runner_api.revoke_runner(
        world["engine"], organization_id=world["alpha"]["org_id"],
        runner_id=identity["runner_id"]) is True

    row = _runners(world["client"], world["alpha"]["token"])["nodes"][0]
    assert row["enabled"] is False and row["revokedAt"], row
    assert row["status"] == "offline", "a revoked runner cannot lease, so it is not online"
    # Its token no longer authenticates: the credential is gone from the route as well
    # as from the read surface.
    refused = world["client"].post("/runner/lease", json={"runner_id": identity["runner_id"]},
                                   headers=_auth(identity))
    assert refused.status_code == 401, refused.text


def test_paging_walks_every_runner_exactly_once(world):
    names = [f"runner-{n}" for n in range(5)]
    for name in names:
        _enroll(world, name=name)

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        variables = "first: 2" + (f', after: "{cursor}"' if cursor else "")
        page = _runners(world["client"], world["alpha"]["token"], variables)
        pages += 1
        seen.extend(node["name"] for node in page["nodes"])
        assert len(page["nodes"]) <= 2
        if not page["pageInfo"]["hasNextPage"]:
            assert page["pageInfo"]["hasPreviousPage"] is (cursor is not None)
            break
        cursor = page["pageInfo"]["endCursor"]
        assert cursor, "a page that has a next page must carry a cursor"

    assert pages == 3, pages   # 5 rows at 2 per page: 2, 2, 1
    assert sorted(seen) == sorted(names)
    assert len(set(seen)) == 5, "no row may be served twice across the boundary"

    broken = _post(world["client"], world["alpha"]["token"],
                   'query { runners(after: "nonsense") { nodes { id } } }')
    assert broken["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"


# ------------------------------------------------------- claims: awaiting, verdicts
ACCEPTANCE = "## Acceptance\n\n- the widget spins\n"


def _claimed_run(world, *, org: str = "alpha", title: str = "Ship the widget") -> dict:
    """A runner registers, leases an admitted delegation and finishes with a claim."""
    tenant = world[org]
    identity = _enroll(world, org=org, name=f"{org}-runner")
    agent = _agent(world["client"], tenant["token"])
    _grant_policy(world["client"], tenant["token"], agent["id"], tenant["team_id"])
    issue_row = _delegate(world["client"], tenant["token"], tenant["team_id"],
                          agent["userId"], title, description=ACCEPTANCE)
    lease = _claim_lease(world, identity)
    _finish(world, identity, lease,
            acceptance=[{"item": "the widget spins", "evidence": "manual run"}])
    return {"identity": identity, "agent": agent, "issue": issue_row, "lease": lease}


def test_a_finished_claim_is_awaiting_until_a_separate_principal_signs_it(world):
    claimed = _claimed_run(world)
    token = world["alpha"]["token"]

    awaiting = _claims(world["client"], token, "first: 50, awaiting: true")["nodes"]
    assert len(awaiting) == 1, awaiting
    row = awaiting[0]
    assert row["awaiting"] is True
    assert row["verdict"] is None and row["verifierId"] is None
    assert row["workerModel"] == "worker-model"
    assert row["claim"]["summary"] == "the widget spins"
    assert row["claim"]["acceptance"] == [{"item": "the widget spins",
                                          "evidence": "manual run"}]
    assert row["issue"]["identifier"] == claimed["issue"]["identifier"]
    assert row["agentUserId"] == claimed["agent"]["userId"]
    assert row["claimAt"]

    # The verifier is a person who did not do the work. The evidence it re-reads is
    # Hoja's own records: the acceptance list passes and the missing pull request is a
    # gap, so `uncertain` is the strongest verdict the evidence supports.
    signed = _post(world["client"], token, f'''
        mutation {{ agentSessionVerify(id: "{row["sessionId"]}", verdict: "uncertain",
                                       rationale: "no pull request is linked yet") {{
          success agentSession {{ id state verdict verifiedAt }} }} }}''')
    assert "errors" not in signed, signed
    session = signed["data"]["agentSessionVerify"]["agentSession"]
    assert session["state"] == "verifying", "an uncertain verdict does not settle the claim"
    assert session["verdict"] == "uncertain"

    after = _claims(world["client"], token, "first: 50, awaiting: true")["nodes"][0]
    assert after["verdict"] == "uncertain"
    assert after["verifierId"] == world["alpha"]["user_id"]
    assert len(after["verifications"]) == 1
    verdict_row = after["verifications"][0]
    assert verdict_row["requestedVerdict"] == "uncertain"
    assert verdict_row["verifierKind"] == "human"
    assert verdict_row["workerModel"] == "worker-model"
    outcomes = {check["name"]: check["outcome"] for check in verdict_row["checks"]}
    assert outcomes["acceptance_list"] == "passed", outcomes
    assert outcomes["pull_request"] == "uncertain", outcomes
    assert outcomes["principal_separation"] == "passed", outcomes
    assert "CI" not in " ".join(check["detail"] for check in verdict_row["checks"])

    # The same verdict through its own read, narrowed to the claim.
    ledger = _data(world["client"], token, f'''
        query {{ agentVerifications(sessionId: "{row["sessionId"]}") {{
          id sessionId verdict requestedVerdict checks {{ name outcome }} }} }}''')
    assert [item["id"] for item in ledger["agentVerifications"]] == [verdict_row["id"]]
    # The filters are the resolver's, not a pass over the loaded page. `uncertain`
    # is recorded but does *not* settle the claim, so it is still the awaiting set;
    # a verdict nothing recorded is an empty page.
    assert _claims(world["client"], token, "first: 50, awaiting: false")["nodes"] == []
    assert [node["sessionId"] for node in
            _claims(world["client"], token, 'first: 50, verdict: "uncertain"')["nodes"]] == \
        [row["sessionId"]]
    assert _claims(world["client"], token, 'first: 50, verdict: "met"')["nodes"] == []


def test_a_cross_tenant_claim_read_returns_zero_rows(world):
    _claimed_run(world, org="bravo", title="Bravo work")
    _claimed_run(world, org="alpha", title="Alpha work")

    ours = _claims(world["client"], world["alpha"]["token"])["nodes"]
    assert [node["issue"]["title"] for node in ours] == ["Alpha work"]
    bravo_claim = _claims(world["client"], world["bravo"]["token"])["nodes"][0]
    # Bravo's agent id cannot be used as a filter to reach across the tenant.
    assert _claims(world["client"], world["alpha"]["token"],
                   f'first: 50, agentId: "{bravo_claim["agentUserId"]}"')["nodes"] == []
    # Alpha naming Bravo's session id reads an empty ledger, not Bravo's rows.
    foreign = _data(world["client"], world["alpha"]["token"], f'''
        query {{ agentVerifications(sessionId: "{bravo_claim["sessionId"]}") {{ id }} }}''')
    assert foreign["agentVerifications"] == []


def test_claims_page_exactly_once_newest_first(world):
    tenant = world["alpha"]
    identity = _enroll(world, name="claim-runner")
    agent = _agent(world["client"], tenant["token"], capacity=9)
    _grant_policy(world["client"], tenant["token"], agent["id"], tenant["team_id"],
                  max_concurrency=9)
    for n in range(5):
        issue_row = _delegate(world["client"], tenant["token"], tenant["team_id"],
                              agent["userId"], f"Run {n}", description=ACCEPTANCE)
        lease = _claim_lease(world, identity)
        _finish(world, identity, lease,
                acceptance=[{"item": "the widget spins", "evidence": f"run {n}"}])

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        variables = "first: 2" + (f', after: "{cursor}"' if cursor else "")
        page = _claims(world["client"], tenant["token"], variables)
        pages += 1
        seen.extend(node["sessionId"] for node in page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            assert page["pageInfo"]["hasPreviousPage"] is (cursor is not None)
            break
        cursor = page["pageInfo"]["endCursor"]
    assert pages == 3, pages
    assert len(set(seen)) == 5, seen

    everything = _claims(world["client"], tenant["token"])["nodes"]
    assert [node["sessionId"] for node in everything] == seen
    assert [node["claimAt"] for node in everything] == sorted(
        (node["claimAt"] for node in everything), reverse=True)
    broken = _post(world["client"], tenant["token"],
                   'query { verifierClaims(after: "nonsense") { nodes { sessionId } } }')
    assert broken["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"
