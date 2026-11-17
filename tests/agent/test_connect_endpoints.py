"""Hoja Connect's server record (HOJA-602-A-10, WP09) — the policies, not the tunnels.

WP09's runner half (PR #215) opens one ``tailcat serve`` per exposure and keeps the address in
memory only. That left the Connect screen on a fixture and an admin with no answer to "what is this
workspace exposing?", so this revision adds ``connect_endpoint`` and the two ``/runner/connect``
routes. This file is the acceptance list:

================================================  ============================================
test                                                source
================================================  ============================================
a port the policy does not grant is refused         issue Do: "an explicit port/host allowlist"
a stale fence cannot publish or close               FR-018, every lease route's 409
the lease's end closes its exposures                issue Do: "a TTL tied to the lease"
a member is FORBIDDEN, not given an empty list      WP13's admin-gated read convention
a cross-tenant read returns zero rows               RLS, under HOJA_TEST_RLS
the kill switch refuses the open and says why       issue Do: "an org admin can turn Connect off"
the close is audited, and the runner is told        issue Do: "audited"; revocation delivery
================================================  ============================================
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import connect_api, runner_api
from hoja_server.app import create_app
from hoja_server.db import audit_event, connect_endpoint, seed_tenant
from hoja_server.store import Store
from tests import rls_mode

PEER = "nodekey:" + "a" * 64
ENDPOINT_FIELDS = """id exposureId runnerId runnerName leaseId sessionId agentId port host
  pinnedPeer state closeReason openedAt expiresAt closedAt revokeRequestedAt"""


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
    monkeypatch.setenv("HOJA_SECRET_KEY", "connect-endpoint-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "connect-endpoint")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo,
            "store": Store(engine)}


# --------------------------------------------------------------------- helpers
def _enroll(world, *, org: str = "alpha", name: str = "runner-1",
            harnesses: tuple[str, ...] = ("pi",)) -> dict:
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment, "harnesses": list(harnesses)})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(identity: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity['token']}"}


def _agent(client: TestClient, token: str, name: str = "Scout") -> dict:
    return _data(client, token, f'''
        mutation {{
          agentMemberCreate(input: {{name: "{name}", capacity: 4}}) {{
            success agentMember {{ id userId }} }} }}''')["agentMemberCreate"]["agentMember"]


def _policy(client: TestClient, token: str, member_id: str, team_id: str,
            *, connect: str = '[{host: "localhost", port: 3000}]') -> dict:
    """A policy that names the team (so a lease is grantable) and the Connect grant."""
    return _data(client, token, f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}",
                            input: {{teams: ["{team_id}"], maxConcurrency: 4,
                                    connect: {connect}}}) {{ success }} }}''')["agentPolicyUpdate"]


def _delegate(client: TestClient, token: str, team_id: str, agent_user_id: str,
              title: str = "Expose the preview") -> dict:
    return _data(client, token, f'''
        mutation {{
          issueCreate(input: {{teamId: "{team_id}", title: "{title}",
                               delegateId: "{agent_user_id}"}}) {{
            success issue {{ id identifier }} }} }}''')["issueCreate"]["issue"]


def _lease(world, identity: dict) -> dict:
    response = world["client"].post("/runner/lease",
                                    json={"runner_id": identity["runner_id"],
                                          "poll_timeout_ms": 0},
                                    headers=_auth(identity))
    assert response.status_code == 200, response.text
    lease = response.json().get("lease")
    assert lease, "the delegated task should have queued a lease for this runner"
    return lease


def _leased_agent(world, *, org: str = "alpha", grant: bool = True) -> tuple[dict, dict, dict]:
    """Register a runner, delegate to a fresh agent, and hand back (runner, agent, lease)."""
    tenant = world[org]
    identity = _enroll(world, org=org)
    agent = _agent(world["client"], tenant["token"], name=f"agent-{org}")
    _policy(world["client"], tenant["token"], agent["id"], tenant["team_id"],
            connect=('[{host: "localhost", port: 3000}]' if grant
                     else '[{host: "example.internal", port: 22}]'))
    _delegate(world["client"], tenant["token"], tenant["team_id"], agent["userId"])
    return identity, agent, _lease(world, identity)


def _open_body(lease: dict, **extra) -> dict:
    body = {
        "lease_id": lease["lease_id"], "run_id": lease["run_id"],
        "fence": lease["fence"], "token": lease["token"],
        "exposure_id": "exp-1", "ports": [3000], "host": "localhost",
        "pinned_peer": PEER,
    }
    body.update(extra)
    return body


def _rows(world, org: str = "alpha") -> list[dict]:
    org_id = world[org]["org_id"]
    with world["engine"].connect() as conn:
        return [dict(row) for row in conn.execute(select(connect_endpoint).where(
            connect_endpoint.c.organization_id == org_id)).mappings().all()]


def _member_token(world, org: str = "alpha") -> str:
    tenant = world[org]
    member_id = _data(world["client"], tenant["token"], '''
        mutation { memberInvite(input: {name: "Second", email: "second@alpha.test"}) {
          success user { id } } }''')["memberInvite"]["user"]["id"]
    # read_write, not read: a read-only key is refused by the API-key guard before any resolver
    # runs, which would prove nothing about this surface's own admin gate.
    _metadata, token = world["store"].create_api_key(tenant["org_id"], member_id,
                                                     "member-key", "read_write")
    return token


def _audit_actions(world, org: str = "alpha") -> list[str]:
    org_id = world[org]["org_id"]
    with world["engine"].connect() as conn:
        return list(conn.execute(select(audit_event.c.action).where(
            audit_event.c.organization_id == org_id)).scalars().all())


# ------------------------------------------------------------------ the refusal
def test_a_port_the_policy_does_not_grant_is_refused(world):
    identity, agent_row, lease = _leased_agent(world, grant=False)
    response = world["client"].post("/runner/connect/open", json=_open_body(lease),
                                    headers=_auth(identity))
    assert response.status_code == 403, response.text
    payload = response.json()
    assert payload["code"] == connect_api.ERROR_NOT_GRANTED, payload
    # The refusal names the grant that is missing, not just "no".
    assert "connect" in payload["message"] and "3000" in payload["message"], payload
    assert payload["port"] == 3000 and payload["host"] == "localhost", payload
    # Nothing was recorded, and nothing was audited as if it had been.
    assert _rows(world) == []
    assert "connect.exposure.open" not in _audit_actions(world)

    # The granted port is the whole allowlist: the same policy admits 3000 and nothing else.
    tenant = world["alpha"]
    _policy(world["client"], tenant["token"], agent_row["id"], tenant["team_id"])
    allowed = world["client"].post("/runner/connect/open",
                                   json=_open_body(lease, ports=[3000, 9229]),
                                   headers=_auth(identity))
    assert allowed.status_code == 403, allowed.text
    assert allowed.json()["port"] == 9229, allowed.json()
    ok = world["client"].post("/runner/connect/open", json=_open_body(lease),
                              headers=_auth(identity))
    assert ok.status_code == 200, ok.text
    assert ok.json()["ports"] == [3000] and ok.json()["state"] == "open", ok.json()


def test_a_task_with_no_delegated_agent_has_no_grant_to_spend(world):
    identity = _enroll(world)
    tenant = world["alpha"]
    # A task nobody delegated: no agent, therefore no policy, therefore no allowlist to spend.
    task = _data(world["client"], tenant["token"], f'''
        mutation {{
          issueCreate(input: {{teamId: "{tenant["team_id"]}", title: "Undeligated"}}) {{
            success issue {{ id }} }} }}''')["issueCreate"]["issue"]
    runner_api.enqueue_task(world["engine"], organization_id=tenant["org_id"],
                            task_id=task["id"], harness="pi", prompt="do it")
    lease = _lease(world, identity)
    response = world["client"].post("/runner/connect/open", json=_open_body(lease),
                                    headers=_auth(identity))
    assert response.status_code == 403, response.text
    assert response.json()["code"] == connect_api.ERROR_NOT_GRANTED
    assert response.json()["rule"] == "no-policy", response.json()
    assert _rows(world) == []


# -------------------------------------------------------------------- the fence
def test_a_stale_fence_cannot_publish_or_close(world):
    identity, _agent_row, lease = _leased_agent(world)
    tenant = world["alpha"]
    # A newer attempt of the same task makes the held fence superseded (FR-018).
    runner_api.enqueue_task(world["engine"], organization_id=tenant["org_id"],
                            task_id=lease["task_id"], harness="pi", prompt="again")
    stale = world["client"].post("/runner/connect/open", json=_open_body(lease),
                                 headers=_auth(identity))
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == runner_api.ERROR_STALE_FENCE, stale.json()
    assert _rows(world) == []

    # A close from the superseded attempt is refused by the same check, before it can touch a row.
    close_stale = world["client"].post("/runner/connect/close",
                                       json={"lease_id": lease["lease_id"],
                                             "fence": lease["fence"], "token": lease["token"],
                                             "exposure_id": "exp-1"},
                                       headers=_auth(identity))
    assert close_stale.status_code == 409, close_stale.text
    assert close_stale.json()["code"] == runner_api.ERROR_STALE_FENCE


# --------------------------------------------------------------- the lease's end
def test_the_leases_end_closes_its_exposures(world):
    identity, _agent_row, lease = _leased_agent(world)
    opened = world["client"].post("/runner/connect/open",
                                  json=_open_body(lease, ports=[3000]),
                                  headers=_auth(identity))
    assert opened.status_code == 200, opened.text
    assert [row["state"] for row in _rows(world)] == ["open"]

    finished = world["client"].post("/runner/finish", json={
        "lease_id": lease["lease_id"], "run_id": lease["run_id"], "fence": lease["fence"],
        "token": lease["token"], "state": "completed", "summary": "exposed",
        "idempotency_key": f"{lease['run_id']}:1",
    }, headers=_auth(identity))
    assert finished.status_code == 200, finished.text

    rows = _rows(world)
    assert len(rows) == 1, rows
    assert rows[0]["state"] == "closed", rows
    assert rows[0]["close_reason"] == connect_api.CLOSE_LEASE_FINISHED, rows
    assert rows[0]["closed_at"], rows
    assert "connect.exposure.close" in _audit_actions(world)


def test_a_cancelled_lease_closes_with_the_cancel_reason(world):
    identity, _agent_row, lease = _leased_agent(world)
    assert world["client"].post("/runner/connect/open", json=_open_body(lease),
                                headers=_auth(identity)).status_code == 200
    runner_api.request_cancel(world["engine"], organization_id=world["alpha"]["org_id"],
                              task_id=lease["task_id"], reason="human_stop")
    # Expiry is the reaper's job, on Hoja's clock: the deadline makes the lease end, and the
    # exposure has to follow it rather than outlive it.
    with world["engine"].begin() as conn:
        conn.execute(connect_endpoint.update().where(
            connect_endpoint.c.exposure_id == "exp-1"
        ).values(expires_at="2000-01-01T00:00:00+00:00"))
        conn.execute(runner_api.agent_lease.update().where(
            runner_api.agent_lease.c.id == lease["lease_id"]
        ).values(lease_until="2000-01-01T00:00:00+00:00"))
    runner_api.reap_expired_leases(world["engine"], grace_s=0)
    rows = _rows(world)
    assert [row["state"] for row in rows] == ["closed"], rows
    assert rows[0]["close_reason"] == connect_api.CLOSE_LEASE_CANCELED, rows


# ------------------------------------------------------------------- the admin read
def test_only_an_admin_reads_the_connect_surface(world):
    identity, _agent_row, lease = _leased_agent(world)
    assert world["client"].post("/runner/connect/open", json=_open_body(lease),
                                headers=_auth(identity)).status_code == 200
    member = _member_token(world)
    for query in ("query { connectEndpoints { nodes { id } } }",
                  'query { connectEndpoint(id: "x") { id } }',
                  'mutation { connectEndpointClose(exposureId: "exp-1") { success } }'):
        refused = _post(world["client"], member, query)
        assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN", refused
        assert "admin" in refused["errors"][0]["message"].lower(), refused

    served = _data(world["client"], world["alpha"]["token"], f'''
        query {{ connectEndpoints {{ nodes {{ {ENDPOINT_FIELDS} }} }} }}''')
    nodes = served["connectEndpoints"]["nodes"]
    assert len(nodes) == 1, nodes
    node = nodes[0]
    assert node["port"] == 3000 and node["host"] == "localhost", node
    assert node["pinnedPeer"] == PEER and node["exposureId"] == "exp-1", node
    assert node["runnerId"] == identity["runner_id"] and node["runnerName"] == "runner-1", node
    assert node["leaseId"] == lease["lease_id"] and node["agentId"], node
    # No address, join token or lease token: a field that is not in the SDL cannot be served.
    assert not {"address", "token", "joinToken", "leaseToken"} & set(node), node


def test_a_cross_tenant_connect_read_returns_zero_rows(world):
    alpha_identity, _row, alpha_lease = _leased_agent(world, org="alpha")
    bravo_identity, _brow, bravo_lease = _leased_agent(world, org="bravo")
    for org, identity, lease in (("alpha", alpha_identity, alpha_lease),
                                 ("bravo", bravo_identity, bravo_lease)):
        response = world["client"].post("/runner/connect/open",
                                        json=_open_body(lease),
                                        headers=_auth(identity))
        assert response.status_code == 200, (org, response.text)

    ours = _data(world["client"], world["alpha"]["token"],
                 "{ connectEndpoints { nodes { id runnerName } } }")["connectEndpoints"]["nodes"]
    assert [node["runnerName"] for node in ours] == ["runner-1"], ours

    # The id cannot be used as a filter to reach across the tenant either.
    bravo_rows = _data(world["client"], world["bravo"]["token"],
                       "{ connectEndpoints { nodes { id } } }")["connectEndpoints"]["nodes"]
    foreign = _data(world["client"], world["alpha"]["token"],
                    f'{{ connectEndpoint(id: "{bravo_rows[0]["id"]}") {{ id }} }}')
    assert foreign["connectEndpoint"] is None, foreign


# ---------------------------------------------------------------- the kill switch
def test_the_kill_switch_refuses_the_open_and_says_why(world):
    identity, _agent_row, lease = _leased_agent(world)
    _data(world["client"], world["alpha"]["token"],
          "mutation { workspaceSecurityUpdate(input: {connectEnabled: false}) "
          "{ connectEnabled } }")
    response = world["client"].post("/runner/connect/open", json=_open_body(lease),
                                    headers=_auth(identity))
    assert response.status_code == 403, response.text
    assert response.json()["code"] == connect_api.ERROR_DISABLED, response.json()
    assert _rows(world) == []
    # The switch is a state, not an empty list: the read still answers, and says so.
    policy = _data(world["client"], world["alpha"]["token"],
                   "{ workspaceSecurityPolicy { connectEnabled } }")["workspaceSecurityPolicy"]
    assert policy["connectEnabled"] is False, policy

    _data(world["client"], world["alpha"]["token"],
          "mutation { workspaceSecurityUpdate(input: {connectEnabled: true}) "
          "{ connectEnabled } }")
    assert world["client"].post("/runner/connect/open", json=_open_body(lease),
                                headers=_auth(identity)).status_code == 200


# --------------------------------------------------------------------- the revoke
def test_an_admin_revoke_closes_audits_and_reaches_the_runner(world):
    identity, agent_row, lease = _leased_agent(world)
    assert world["client"].post("/runner/connect/open",
                                json=_open_body(lease, ports=[3000, 3001]),
                                headers=_auth(identity)).status_code == 403  # 3001 is not granted
    _policy(world["client"], world["alpha"]["token"], agent_row["id"],
            world["alpha"]["team_id"],
            connect='[{host: "localhost", port: 3000}, {host: "localhost", port: 3001}]')
    opened = world["client"].post("/runner/connect/open",
                                  json=_open_body(lease, ports=[3000, 3001]),
                                  headers=_auth(identity))
    assert opened.status_code == 200, opened.text
    assert opened.json()["ports"] == [3000, 3001], opened.json()
    assert len(_rows(world)) == 2, _rows(world)

    closed = _data(world["client"], world["alpha"]["token"],
                   'mutation { connectEndpointClose(exposureId: "exp-1") '
                   "{ success closed auditSeq endpoints { id state closeReason } } }")
    result = closed["connectEndpointClose"]
    assert result["success"] is True and result["closed"] == 2, result
    assert [node["state"] for node in result["endpoints"]] == ["closed", "closed"], result
    assert result["auditSeq"], result
    assert "connect.exposure.close" in _audit_actions(world)

    # The runner is told on the heartbeat it already sends, so the process is killed too.
    beat = world["client"].post("/runner/heartbeat", json={
        "lease_id": lease["lease_id"], "fence": lease["fence"], "token": lease["token"],
    }, headers=_auth(identity))
    assert beat.status_code == 200, beat.text
    assert beat.json()["connect_revoke"] == ["exp-1"], beat.json()

    # The runner's own close is the acknowledgement: the instruction stops being redelivered once
    # the process is gone, and it reports itself as a duplicate because the record was already
    # closed by the control plane.
    acked = world["client"].post("/runner/connect/close",
                                 json={"lease_id": lease["lease_id"], "fence": lease["fence"],
                                       "token": lease["token"], "exposure_id": "exp-1",
                                       "reason": "revoked"},
                                 headers=_auth(identity))
    assert acked.status_code == 200 and acked.json()["duplicate"] is True, acked.text
    settled = world["client"].post("/runner/heartbeat", json={
        "lease_id": lease["lease_id"], "fence": lease["fence"], "token": lease["token"],
    }, headers=_auth(identity))
    assert settled.json()["connect_revoke"] == [], settled.json()

    # A second revoke is the same revoke: nothing changed, and nothing is double-audited.
    again = _data(world["client"], world["alpha"]["token"],
                  'mutation { connectEndpointClose(exposureId: "exp-1") { success closed } }')
    assert again["connectEndpointClose"]["closed"] == 0, again
    # A revoke of an exposure this workspace never opened is a 404, not a quiet success.
    missing = _post(world["client"], world["alpha"]["token"],
                    'mutation { connectEndpointClose(exposureId: "nope") { success } }')
    assert missing["errors"][0]["extensions"]["code"] == "NOT_FOUND", missing


def test_the_open_is_idempotent_and_pins_exactly_one_peer(world):
    identity, _agent_row, lease = _leased_agent(world)
    first = world["client"].post("/runner/connect/open", json=_open_body(lease),
                                 headers=_auth(identity))
    assert first.status_code == 200, first.text
    second = world["client"].post("/runner/connect/open", json=_open_body(lease),
                                 headers=_auth(identity))
    assert second.status_code == 200 and second.json()["duplicate"] is True, second.text
    assert len(_rows(world)) == 1, _rows(world)

    # A wildcard or malformed pin is refused: the pin is the whole ACL.
    for pin in ("all", "nodekey:short", "*"):
        bad = world["client"].post("/runner/connect/open",
                                   json=_open_body(lease, exposure_id="exp-2", pinned_peer=pin),
                                   headers=_auth(identity))
        assert bad.status_code == 400, (pin, bad.text)
        assert bad.json()["code"] == connect_api.ERROR_PEER_INVALID, bad.json()

    # An out-of-range port is refused before anything is recorded.
    out = world["client"].post("/runner/connect/open",
                               json=_open_body(lease, exposure_id="exp-3", ports=[70000]),
                               headers=_auth(identity))
    assert out.status_code == 400 and out.json()["code"] == connect_api.ERROR_PORT, out.text


# --------------------------------------------------------------------- migrations
def test_revision_71_applies_to_a_70_database_twice(tmp_path):
    """The upgrade path is 70 -> 71 on an existing database, not a fresh create.

    Both runs must exit clean: the second is the idempotence proof, and it is the run that catches
    a migration which creates its table (or adds its column) unconditionally.
    """
    from sqlalchemy import inspect

    from hoja_server.db import (
        CURRENT_SCHEMA_REVISION,
        applied_schema_revision,
        make_engine,
        migrate,
    )
    from tests import legacy_schema

    assert CURRENT_SCHEMA_REVISION >= 71
    engine = make_engine(tmp_path / "connect-upgrade.db")
    legacy_schema.create_schema(engine, 70)
    with engine.begin() as conn:
        # A 70 database has neither the table nor the column; the fresh create above used today's
        # metadata, so both are removed to make the upgrade path real rather than a no-op.
        conn.exec_driver_sql("DROP TABLE IF EXISTS connect_endpoint")
        conn.exec_driver_sql(
            "ALTER TABLE workspace_security_policy DROP COLUMN connect_enabled")
    assert applied_schema_revision(engine) == 70

    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        assert connect_endpoint.name in set(inspect(conn).get_table_names())
        columns = {c["name"] for c in inspect(conn).get_columns(connect_endpoint.name)}
        policy_columns = {c["name"] for c in inspect(conn).get_columns(
            "workspace_security_policy")}
    assert {"organization_id", "runner_id", "lease_id", "session_id", "agent_user_id",
            "port", "host", "pinned_peer", "opened_at", "closed_at", "state"} <= columns, columns
    assert "connect_enabled" in policy_columns, policy_columns

    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION


def test_a_migrated_database_binds_the_connect_table(tmp_path):
    """`hoja-serve migrate` must bind revision 71's table, or `/ready` answers 503.

    WP01 makes ``/ready`` refuse a deployment where any tenant table is unbound, and ``db.migrate``
    installs the policies after the revisions run. This asserts the classification the gate uses:
    the new table is a *direct* tenant table with a ``hoja_tenant`` policy and no reason to refuse
    it — the same three facts ``/ready`` reads, without needing a second role here (the RLS suite
    proves it end to end under ``HOJA_TEST_RLS``).
    """
    if not rls_mode.ENABLED:
        pytest.skip("needs PostgreSQL (HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL)")
    from hoja_server import rls_policy
    from hoja_server.db import make_engine, migrate

    owner_url, schema = rls_mode.new_schema_url()
    owner = make_engine(owner_url)
    try:
        migrate(owner)  # heal runs here, as the owner credential does in a deployment
        with owner.connect() as conn:
            rows = {row["table"]: row for row in rls_policy.report(conn, schema)["tables"]}
        row = rows[connect_endpoint.name]
        assert row["kind"] == rls_policy.DIRECT, row
        assert row["policy"] is True and row["rls"] is True, row
        assert row["reason"] is None, row
    finally:
        owner.dispose()
