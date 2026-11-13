"""Agent identity: a principal of its own, a delegate separate from the assignee,
and an actor on every change (HOJA-602-A-3, finding F4).

The acceptance names two probes: an agent in organization A cannot be delegated
organization B's tasks, and an issue created by an agent turn records the agent as
actor with the human as ``on_behalf_of``. Both run here. Attribution is recorded by
the PostgreSQL capture trigger, so that probe skips on SQLite, which is the
single-tenant local mode and records no actor by design.
"""
from __future__ import annotations

import pytest
from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import tenant_context
from hoja_server.app import create_app
from hoja_server.db import (
    change_event,
    issue,
    organization_membership,
    seed_tenant,
    user,
)
from hoja_server.writer import Writer
from tests import rls_mode


def _post(client: TestClient, token: str, query: str) -> dict:
    response = client.post("/graphql", json={"query": query},
                           headers={"Authorization": f"Bearer {token}"})
    return response.json()


def _create_agent(client: TestClient, token: str, name: str = "Scout") -> dict:
    body = _post(client, token, f'''
        mutation {{
          agentMemberCreate(input: {{
            name: "{name}", displayName: "{name} (agent)", capacity: 2,
            monthlyBudgetCredits: 500, model: "gpt-test", runnerPool: "default"
          }}) {{
            success
            agentMember {{ id userId capacity model runnerPool user {{ id name }} }}
          }}
        }}''')
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    """Two workspaces on one database: the cross-tenant probe needs a second org."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "agent-identity-master")
    engine, kwargs = rls_mode.workspace(tmp_path, "agent-identity")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return engine, TestClient(app), alpha, bravo


def _create_issue(client, token, team_id, *, delegate_id=None, assignee_id=None) -> dict:
    fields = [f'teamId: "{team_id}"', 'title: "Delegated work"']
    if delegate_id:
        fields.append(f'delegateId: "{delegate_id}"')
    if assignee_id:
        fields.append(f'assigneeId: "{assignee_id}"')
    body = _post(client, token, f'''
        mutation {{
          issueCreate(input: {{{", ".join(fields)}}}) {{
            success
            issue {{ id title delegateId delegate {{ id }} assignee {{ id }} }}
          }}
        }}''')
    return body


def test_an_agent_member_is_a_principal_and_never_a_seat(two_workspaces):
    engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"])
    with engine.connect() as conn:
        identity = conn.execute(select(user).where(user.c.id == agent["userId"])).mappings().one()
        seat = conn.execute(select(organization_membership.c.id).where(and_(
            organization_membership.c.organization_id == alpha["org_id"],
            organization_membership.c.profile_user_id == agent["userId"]))).first()
    assert identity["kind"] == "agent"
    # Not a billable seat: seat counting reads organization_membership, and an
    # agent has no row there because it cannot sign in.
    assert seat is None


def test_delegate_is_separate_from_the_assignee(two_workspaces):
    _engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"])
    body = _create_issue(client, alpha["token"], alpha["team_id"],
                         delegate_id=agent["userId"], assignee_id=alpha["user_id"])
    assert "errors" not in body, body
    task = body["data"]["issueCreate"]["issue"]
    assert task["delegateId"] == agent["userId"]
    assert task["delegate"]["id"] == agent["userId"]
    # The assignee stays the human: delegation does not move accountability.
    assert task["assignee"]["id"] == alpha["user_id"]


def _update(client: TestClient, token: str, member_id: str, fields: str) -> dict:
    return _post(client, token, f'''
        mutation {{
          agentMemberUpdate(id: "{member_id}", input: {{{fields}}}) {{
            success
            agentMember {{ id capacity monthlyBudgetCredits model runnerPool user {{ id name displayName }} }}
          }}
        }}''')


def test_an_update_that_omits_the_name_keeps_the_identity(two_workspaces):
    """A partial edit is the normal edit.

    The web sidebar writes one column at a time (capacity, budget, pool …), so an update
    that names only the column it changes has to work. It used to raise ``KeyError`` on the
    absent ``_name``, which surfaced as INTERNAL_SERVER_ERROR for every control on the agent
    page, and it blanked ``display_name`` on the way.
    """
    _engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"])
    body = _update(client, alpha["token"], agent["id"], "capacity: 7")
    assert "errors" not in body, body
    updated = body["data"]["agentMemberUpdate"]
    assert updated["success"] is True
    assert updated["agentMember"]["capacity"] == 7
    # Untouched identity columns survive an edit that did not mention them.
    assert updated["agentMember"]["user"]["name"] == "Scout"
    assert updated["agentMember"]["user"]["displayName"] == "Scout (agent)"


def test_an_update_can_clear_a_nullable_column(two_workspaces):
    """``null`` is a value here: it means "the workspace default", not "leave it alone"."""
    _engine, client, alpha, _bravo = two_workspaces
    agent = _create_agent(client, alpha["token"])
    body = _update(client, alpha["token"], agent["id"], "monthlyBudgetCredits: null, model: null")
    assert "errors" not in body, body
    updated = body["data"]["agentMemberUpdate"]["agentMember"]
    assert updated["monthlyBudgetCredits"] is None
    assert updated["model"] is None
    # The columns the update did not mention are still the created ones.
    assert updated["capacity"] == 2
    assert updated["runnerPool"] == "default"


def test_a_human_cannot_be_delegated(two_workspaces):
    _engine, client, alpha, _bravo = two_workspaces
    body = _create_issue(client, alpha["token"], alpha["team_id"],
                         delegate_id=alpha["user_id"])
    assert body["data"]["issueCreate"] is None
    assert body["errors"][0]["extensions"] == {"code": "BAD_USER_INPUT", "field": "delegateId"}


def test_an_agent_in_another_workspace_cannot_be_delegated(two_workspaces):
    """The cross-tenant probe: knowing another org's agent id is not authority."""
    engine, client, alpha, bravo = two_workspaces
    foreign_agent = _create_agent(client, bravo["token"], name="Bravo Scout")
    body = _create_issue(client, alpha["token"], alpha["team_id"],
                         delegate_id=foreign_agent["userId"])
    assert body["data"]["issueCreate"] is None
    assert body["errors"][0]["extensions"] == {"code": "BAD_USER_INPUT", "field": "delegateId"}
    with engine.connect() as conn:
        stored = conn.execute(select(issue.c.id).where(and_(
            issue.c.organization_id == alpha["org_id"],
            issue.c.delegate_id == foreign_agent["userId"]))).first()
    assert stored is None


def test_an_agent_write_with_no_actor_fails_closed():
    with pytest.raises(ValueError):
        tenant_context.set_actor("agent", "")


def _actor_of(engine, issue_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(select(change_event).where(and_(
            change_event.c.entity == "issue",
            change_event.c.entity_id == issue_id,
            change_event.c.action == "create"))).mappings().one()
    return dict(row)


def test_agent_turn_records_the_agent_actor_and_the_human_on_behalf_of(two_workspaces):
    """The attribution probe. Acted out at the seam an agent turn uses."""
    engine, client, alpha, _bravo = two_workspaces
    if engine.dialect.name != "postgresql":
        pytest.skip("actor attribution is written by the PostgreSQL capture trigger")
    agent = _create_agent(client, alpha["token"])
    writer = Writer(engine)
    with (tenant_context.scoped(alpha["org_id"]),
          tenant_context.actor("agent", agent["userId"], on_behalf_of=alpha["user_id"])):
        issue_id = writer.issue_create(
            alpha["org_id"], alpha["user_id"],
            {"title": "Written by the agent", "teamId": alpha["team_id"]})
    captured = _actor_of(engine, issue_id)
    assert captured["actor_kind"] == "agent"
    assert captured["actor_id"] == agent["userId"]
    assert captured["on_behalf_of"] == alpha["user_id"]


def test_a_human_write_records_the_human_actor(two_workspaces):
    engine, _client, alpha, _bravo = two_workspaces
    if engine.dialect.name != "postgresql":
        pytest.skip("actor attribution is written by the PostgreSQL capture trigger")
    writer = Writer(engine)
    with tenant_context.scoped(alpha["org_id"]):
        tenant_context.set_actor("human", alpha["user_id"])
        issue_id = writer.issue_create(
            alpha["org_id"], alpha["user_id"],
            {"title": "Written by the person", "teamId": alpha["team_id"]})
    captured = _actor_of(engine, issue_id)
    assert captured["actor_kind"] == "human"
    assert captured["actor_id"] == alpha["user_id"]
    assert captured["on_behalf_of"] is None
