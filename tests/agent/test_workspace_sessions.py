"""The workspace run queue: one paged, admin-only, tenant-scoped session read.

Until this query existed the agent screens could only ask for one task's sessions
(``agentSessions(issueId)``), so the Run queue had no server read of its own. The
queue is a workspace-wide list, which makes three properties load-bearing, and the
three tests below are exactly those:

* a cross-tenant read returns zero rows (the filter is explicit *and* enforced by
  row-level security under ``HOJA_TEST_RLS``);
* a non-admin is refused instead of being handed an empty connection, which would
  read on screen as "nothing is running";
* paging walks the whole queue exactly once, newest first.

The fourth test covers the audit trail the stop mutation now writes: stopping a run
is the action ``audit.ACTIONS`` names, so it must produce exactly one event, and a
stop the state machine refuses must produce none.
"""
from __future__ import annotations

import pytest
from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import agent_sessions, audit
from hoja_server.app import create_app
from hoja_server.db import agent_session, audit_event, seed_tenant
from hoja_server.store import Store
from tests import rls_mode

SESSION_FIELDS = """id issueId agentUserId status state queueReason admitted trigger
  ackedAt activatedAt endedAt createdAt updatedAt
  agent { id name }
  issue { id identifier title priority }"""


def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


def _data(client: TestClient, token: str, query: str) -> dict:
    body = _post(client, token, query)
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    """Two workspaces on one database: the cross-tenant probe needs a second org."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "workspace-sessions-master")
    engine, kwargs = rls_mode.workspace(tmp_path, "workspace-sessions")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return engine, TestClient(app), alpha, bravo


def _create_agent(client: TestClient, token: str, name: str = "Scout",
                  capacity: int = 2) -> dict:
    data = _data(client, token, f'''
        mutation {{
          agentMemberCreate(input: {{name: "{name}", capacity: {capacity}}}) {{
            success agentMember {{ id userId }}
          }}
        }}''')
    return data["agentMemberCreate"]["agentMember"]


def _delegate(client: TestClient, token: str, team_id: str, agent_user_id: str,
              title: str) -> dict:
    """Delegating a task opens its session (WP06), so this is the fixture for a run."""
    data = _data(client, token, f'''
        mutation {{
          issueCreate(input: {{teamId: "{team_id}", title: "{title}",
                               delegateId: "{agent_user_id}"}}) {{
            success issue {{ id agentSession {{ id }} }}
          }}
        }}''')
    return data["issueCreate"]["issue"]


def _workspace_sessions(client: TestClient, token: str,
                        variables: str = "first: 50") -> dict:
    return _data(client, token, f'''
        query {{ workspaceAgentSessions({variables}) {{
          nodes {{ {SESSION_FIELDS} }}
          pageInfo {{ hasNextPage hasPreviousPage startCursor endCursor }}
        }} }}''')["workspaceAgentSessions"]


def _invite(client: TestClient, token: str, email: str = "second@alpha.test") -> str:
    data = _data(client, token, f'''
        mutation {{ memberInvite(input: {{name: "Second", email: "{email}"}}) {{
          success user {{ id }} }} }}''')
    return data["memberInvite"]["user"]["id"]


# ------------------------------------------------------------- cross tenant
def test_a_cross_tenant_read_returns_zero_rows(two_workspaces):
    engine, client, alpha, bravo = two_workspaces
    foreign_agent = _create_agent(client, bravo["token"], name="Bravo Scout")
    _delegate(client, bravo["token"], bravo["team_id"], foreign_agent["userId"],
              "Bravo work")
    ours = _create_agent(client, alpha["token"])
    _delegate(client, alpha["token"], alpha["team_id"], ours["userId"], "Alpha work")

    # Alpha's own queue has its run and nothing from Bravo.
    alpha_page = _workspace_sessions(client, alpha["token"])
    assert [node["issue"]["title"] for node in alpha_page["nodes"]] == ["Alpha work"]
    # Bravo's id cannot be used as a filter to reach across the tenant either.
    filtered = _workspace_sessions(
        client, alpha["token"], f'first: 50, agentId: "{foreign_agent["userId"]}"')
    assert filtered["nodes"] == []

    # And the storage read is scoped even when a caller forgets the GraphQL layer.
    with engine.connect() as conn:
        ours_only = agent_sessions.read_page(conn, alpha["org_id"], first=50)["nodes"]
        assert len(ours_only) == 1
        assert all(node["_row"]["organization_id"] == alpha["org_id"] for node in ours_only)
        foreign = agent_sessions.read_page(conn, bravo["org_id"], first=50)["nodes"]
        assert len(foreign) == 1
        assert foreign[0]["_row"]["organization_id"] == bravo["org_id"]


# --------------------------------------------------------------- admin gate
def test_only_an_admin_reads_every_session(two_workspaces):
    engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"])
    _delegate(client, alpha["token"], alpha["team_id"], agent["userId"], "Ship the fix")
    member_id = _invite(client, alpha["token"])
    _metadata, member_token = Store(engine).create_api_key(
        alpha["org_id"], member_id, "member-key", "read")

    refused = _post(client, member_token,
                    "query { workspaceAgentSessions { nodes { id } } }")
    assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    assert "admin" in refused["errors"][0]["message"].lower()

    # The admin sees the run, with the issue a queue row renders and the activity the
    # agent page's timeline reads.
    page = _workspace_sessions(client, alpha["token"])
    assert len(page["nodes"]) == 1
    node = page["nodes"][0]
    assert node["agentUserId"] == agent["userId"]
    assert node["status"] == "pending" and node["state"] == "queued"
    assert node["issue"]["title"] == "Ship the fix"
    assert node["issue"]["identifier"].startswith(alpha["team_key"])
    assert node["issue"]["priority"] is not None
    activities = _data(client, alpha["token"], f'''
        query {{ agentSession(id: "{node["id"]}") {{
          activities {{ kind body authorKind authorId signal createdAt }} }} }}''')["agentSession"]["activities"]
    assert activities, "a delegation acknowledges itself, so the timeline is not empty"
    # The transcript's camelCase fields used to resolve to null (the storage row is snake_case and
    # the default resolver looks the field name up verbatim), which failed the whole read on the
    # non-nullable `authorKind`. A session page needs all of them, so they are asserted here.
    first = activities[0]
    assert first["authorKind"] == "agent"
    assert first["createdAt"], "the timeline needs the activity's own timestamp"
    assert first["body"], "an acknowledgement has something to say"


# -------------------------------------------------------------------- paging
def test_paging_walks_the_whole_queue_exactly_once(two_workspaces):
    _engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"], capacity=9)
    created = [_delegate(client, alpha["token"], alpha["team_id"], agent["userId"],
                         f"Run {n}")["agentSession"]["id"] for n in range(5)]

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        variables = "first: 2" + (f', after: "{cursor}"' if cursor else "")
        page = _workspace_sessions(client, alpha["token"], variables)
        pages += 1
        seen.extend(node["id"] for node in page["nodes"])
        assert len(page["nodes"]) <= 2
        if not page["pageInfo"]["hasNextPage"]:
            assert page["pageInfo"]["hasPreviousPage"] is (cursor is not None)
            break
        cursor = page["pageInfo"]["endCursor"]
        assert cursor, "a page that has a next page must carry a cursor"

    # 5 rows at 2 per page: 2, 2, 1.
    assert pages == 3, pages
    assert sorted(seen) == sorted(created), (seen, created)
    assert len(set(seen)) == 5, "no row may be served twice across the boundary"

    # Newest first: the last delegation is the first row, and the ordering is total.
    everything = _workspace_sessions(client, alpha["token"], "first: 50")["nodes"]
    assert [node["id"] for node in everything] == seen
    assert [node["createdAt"] for node in everything] == sorted(
        (node["createdAt"] for node in everything), reverse=True)

    # The status filter is the server's, so it pages over the filtered set.
    cancelled = _workspace_sessions(client, alpha["token"], "first: 50, status: complete")
    assert cancelled["nodes"] == []
    pending = _workspace_sessions(client, alpha["token"], "first: 50, status: pending")
    assert len(pending["nodes"]) == 5

    # A cursor that names no row is refused as bad input, not served as page one.
    broken = _post(client, alpha["token"],
                   'query { workspaceAgentSessions(after: "nonsense") { nodes { id } } }')
    assert broken["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"


# --------------------------------------------------------------- the audit trail
def _stop(client: TestClient, token: str, session_id: str) -> dict:
    return _post(client, token, f'''
        mutation {{ agentSessionStop(id: "{session_id}", reason: "not needed") {{
          success agentSession {{ state status }} }} }}''')


def _events(engine, org_id: str, action: str) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(
            select(audit_event).where(and_(audit_event.c.organization_id == org_id,
                                           audit_event.c.action == action)))]


def test_a_stop_writes_exactly_one_event_and_a_refused_stop_writes_none(two_workspaces):
    engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"])
    _delegate(client, alpha["token"], alpha["team_id"], agent["userId"], "Stop me")
    session = _workspace_sessions(client, alpha["token"])["nodes"][0]

    body = _stop(client, alpha["token"], session["id"])
    assert "errors" not in body, body
    assert body["data"]["agentSessionStop"]["agentSession"]["state"] == "cancelled"

    rows = _events(engine, alpha["org_id"], "agent.session.stop")
    assert len(rows) == 1, rows
    assert rows[0]["actor_kind"] == "human"
    assert rows[0]["actor_id"] == alpha["user_id"]
    assert rows[0]["target_kind"] == "agent_session"
    assert rows[0]["target_id"] == session["id"]
    assert session["issueId"] in rows[0]["detail"]
    assert audit.verify_chain(engine, alpha["org_id"]) is None

    # Cancel already won, so the second stop is refused by the machine...
    again = _stop(client, alpha["token"], session["id"])
    assert again["errors"][0]["extensions"]["code"] == "CONFLICT"
    # ... and a refused action leaves the ledger alone: still exactly one event.
    assert len(_events(engine, alpha["org_id"], "agent.session.stop")) == 1

    # The state the queue reads afterwards is the cancelled one, in both statuses.
    stopped = _workspace_sessions(client, alpha["token"], "first: 50, status: complete")
    assert [node["state"] for node in stopped["nodes"]] == ["cancelled"]
    with engine.connect() as conn:
        stored = conn.execute(select(agent_session.c.state).where(
            agent_session.c.id == session["id"])).scalar_one()
    assert stored == "cancelled"
