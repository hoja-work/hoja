"""`IssueFilter.delegate` is `assignee`'s twin, one column over (HOJA-712).

Linear filters a view by delegate; Hoja keeps delegation (`issue.delegate_id`)
separate from assignment (`issue.assignee_id`), so a client that asks "what is
delegated to this agent" needs the same input shape as the assignee filter
rather than a second dialect. These tests pin the shape (introspected, field for
field equal to `assignee`'s), the translation (it reads `delegate_id`, not
`assignee_id`), the tenant boundary (another organization's user id selects no
rows), and the saved-view path that compiles a Linear-style `delegate` predicate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import views as viewdocs
from hoja_server.app import create_app
from hoja_server.db import seed_tenant
from tests import rls_mode

AGENTS = """mutation { agentMemberCreate(input: { %s }) {
  agentMember { id userId user { name displayName } } } }"""

CREATE_ISSUE = """mutation($i: IssueCreateInput!) {
  issueCreate(input: $i) { success issue { id identifier } } }"""

UPDATE_ISSUE = """mutation($id: String!, $i: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $i) { success issue { id } } }"""

LIST = """query($filter: IssueFilter) {
  issues(filter: $filter, first: 100) { nodes { id identifier } } }"""

VIEW_MUTATION = """mutation($i: ViewCreateInput!) {
  viewCreate(input: $i) { success view { id } } }"""

VIEW_ISSUES = """query($id: String!) {
  view(id: $id) { id issues(first: 25) { nodes { id identifier } } } }"""

INTROSPECT = """query {
  filterType: __type(name: "IssueFilter") { inputFields { name type { name kind } } }
  userFilter: __type(name: "UserFilter") { inputFields { name } }
  comparator: __type(name: "IDComparator") { inputFields { name } }
}"""


def _gql(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    body = client.post("/graphql", json={"query": query, "variables": variables or {}},
                       headers={"Authorization": f"Bearer {token}"}).json()
    assert "errors" not in body, body
    return body["data"]


def _agent(client: TestClient, token: str, name: str) -> dict:
    data = _gql(client, token, AGENTS % f'name: "{name}", displayName: "{name} Agent", capacity: 2')
    return data["agentMemberCreate"]["agentMember"]


def _issue(client: TestClient, token: str, team_id: str, title: str) -> dict:
    data = _gql(client, token, CREATE_ISSUE, {"i": {"teamId": team_id, "title": title}})
    return data["issueCreate"]["issue"]


def _delegate(client: TestClient, token: str, issue_id: str, agent_uid: str) -> None:
    data = _gql(client, token, UPDATE_ISSUE, {"id": issue_id, "i": {"delegateId": agent_uid}})
    assert data["issueUpdate"]["success"] is True


def _filtered(client: TestClient, token: str, flt: dict) -> set[str]:
    nodes = _gql(client, token, LIST, {"filter": flt})["issues"]["nodes"]
    return {node["identifier"] for node in nodes}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "delegate-filter-master")
    engine, kwargs = rls_mode.workspace(tmp_path, "delegate-filter")
    seeded = seed_tenant(engine, org_name="Delegated", org_url_key="delegated",
                         user_email="admin@delegated.test", token="delegated-tok",
                         user_name="Delegated Admin", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    with TestClient(app) as client:
        yield {"engine": engine, "kwargs": kwargs, "seeded": seeded, "client": client,
               "token": seeded["token"],
               "scout": _agent(client, seeded["token"], "Scout"),
               "ranger": _agent(client, seeded["token"], "Ranger")}


def test_delegate_filter_selects_only_the_delegated_issues(world):
    """`delegate.id.eq` is the delegated-to agent, and it reads the same reference
    columns as `assignee` (`id`, `displayName`, `email`)."""
    client, token, team = world["client"], world["token"], world["seeded"]["team_id"]
    scout, ranger = world["scout"], world["ranger"]
    first = _issue(client, token, team, "Scout one")
    second = _issue(client, token, team, "Scout two")
    third = _issue(client, token, team, "Ranger one")
    nobody = _issue(client, token, team, "Nobody's")
    _delegate(client, token, first["id"], scout["userId"])
    _delegate(client, token, second["id"], scout["userId"])
    _delegate(client, token, third["id"], ranger["userId"])

    assert _filtered(client, token, {"delegate": {"id": {"eq": scout["userId"]}}}) == {
        first["identifier"], second["identifier"]}
    assert _filtered(client, token, {"delegate": {"id": {"eq": ranger["userId"]}}}) == {
        third["identifier"]}
    assert _filtered(client, token, {"delegate": {"id": {
        "in": [scout["userId"], ranger["userId"]]}}}) == {
        first["identifier"], second["identifier"], third["identifier"]}
    # A purely negative predicate keeps the undelegated rows, as Linear does and as
    # the assignee filter does ("not delegated to Scout" includes "delegated to nobody").
    assert _filtered(client, token, {"delegate": {"id": {"neq": scout["userId"]}}}) == {
        third["identifier"], nobody["identifier"]}
    # `displayName` resolves through the same user lookup the assignee filter uses.
    assert _filtered(client, token, {"delegate": {"displayName": {"eq": "Scout Agent"}}}) == {
        first["identifier"], second["identifier"]}
    # An empty comparator is no predicate at all, exactly as for assignee; a match on
    # nobody names no user and selects nothing.
    assert _filtered(client, token, {"delegate": {}}) == _filtered(client, token, {}) == {
        first["identifier"], second["identifier"], third["identifier"], nobody["identifier"]}
    assert _filtered(client, token, {"delegate": {"id": {
        "eq": "00000000-0000-0000-0000-000000000000"}}}) == set()


def test_delegate_and_assignee_read_their_own_columns(world):
    """One issue assigned to the person and delegated to the agent is found by
    either filter under its own field, and by the other field under neither."""
    client, token, team = world["client"], world["token"], world["seeded"]["team_id"]
    scout, admin = world["scout"], world["seeded"]
    issue = _issue(client, token, team, "Both")
    _delegate(client, token, issue["id"], scout["userId"])
    _gql(client, token, UPDATE_ISSUE,
         {"id": issue["id"], "i": {"assigneeId": admin["user_id"]}})

    assert _filtered(client, token, {"delegate": {"id": {"eq": scout["userId"]}}}) == {
        issue["identifier"]}
    assert _filtered(client, token, {"assignee": {"id": {"eq": admin["user_id"]}}}) == {
        issue["identifier"]}
    # Neither field is an alias for the other.
    assert _filtered(client, token, {"delegate": {"id": {"eq": admin["user_id"]}}}) == set()
    assert _filtered(client, token, {"assignee": {"id": {"eq": scout["userId"]}}}) == set()


def test_a_delegate_id_from_another_workspace_selects_nothing(world):
    """The user reference is resolved inside the caller's organization, so another
    organization's agent id cannot select (or reveal) anything here."""
    client, token, team = world["client"], world["token"], world["seeded"]["team_id"]
    mine = _issue(client, token, team, "Mine")
    _delegate(client, token, mine["id"], world["scout"]["userId"])

    other = seed_tenant(world["engine"], org_name="Elsewhere", org_url_key="elsewhere",
                        team_key="ELSE", user_email="admin@elsewhere.test",
                        token="elsewhere-tok", demo_issues=False)
    foreign = _agent(client, other["token"], "Foreigner")
    foreign_issue = _issue(client, other["token"], other["team_id"], "Theirs")
    _delegate(client, other["token"], foreign_issue["id"], foreign["userId"])

    assert _filtered(client, token, {"delegate": {"id": {"eq": foreign["userId"]}}}) == set()
    # The other organization does see its own delegated issue, so the empty result
    # above is the tenant predicate and not a broken filter.
    assert _filtered(client, other["token"], {"delegate": {"id": {"eq": foreign["userId"]}}}) == {
        foreign_issue["identifier"]}
    assert _filtered(client, token, {"delegate": {"id": {"eq": world["scout"]["userId"]}}}) == {
        mine["identifier"]}


def test_delegate_has_the_assignee_filter_shape_and_no_null_operator(world):
    """Wire parity: the field exists, with `assignee`'s exact input type.

    `UserFilter` has no `null`/`isNull` field, so `{delegate: {null: true}}` is
    rejected exactly as `{assignee: {null: true}}` is — "no delegate" is not
    expressible today (the `parent.isNull` extension exists only for parent).
    """
    types = _gql(world["client"], world["token"], INTROSPECT)
    fields = {f["name"]: f["type"] for f in types["filterType"]["inputFields"]}
    assert set(fields) >= {"assignee", "delegate", "creator", "or", "and"}
    assert fields["delegate"] == fields["assignee"] == {"name": "UserFilter", "kind": "INPUT_OBJECT"}
    assert [f["name"] for f in types["userFilter"]["inputFields"]] == [
        "id", "email", "displayName", "name"]
    assert [f["name"] for f in types["comparator"]["inputFields"]] == ["eq", "in", "neq"]
    assert len(fields) == 19


def test_a_stored_view_compiles_and_executes_a_delegate_filter(world):
    """A saved view stores Linear's `{"delegate": {"is": <userId>}}` and selects by it."""
    client, token, team = world["client"], world["token"], world["seeded"]["team_id"]
    scout = world["scout"]["userId"]
    delegated = _issue(client, token, team, "Delegated")
    _issue(client, token, team, "Not delegated")
    _delegate(client, token, delegated["id"], scout)

    assert viewdocs.compile_filter("issue", {"delegate": {"is": scout}}) == {
        "delegate": {"id": {"eq": scout}}}

    created = _gql(client, token, VIEW_MUTATION, {"i": {
        "name": "Delegated to Scout", "kind": "issue",
        "filters": {"delegate": {"is": scout}}}})["viewCreate"]
    assert created["success"] is True
    nodes = _gql(client, token, VIEW_ISSUES,
                 {"id": created["view"]["id"]})["view"]["issues"]["nodes"]
    assert [node["identifier"] for node in nodes] == [delegated["identifier"]]


def test_a_stored_view_selects_undelegated_tasks(world):
    """`delegate` is nullable like `assignee`, so a view can say "No delegate"."""
    client, token, team = world["client"], world["token"], world["seeded"]["team_id"]
    delegated = _issue(client, token, team, "Delegated")
    loose = _issue(client, token, team, "Not delegated")
    _delegate(client, token, delegated["id"], world["scout"]["userId"])

    assert viewdocs.compile_filter("issue", {"delegate": {"is": None}}) == {
        "delegate": {"null": True}}
    created = _gql(client, token, VIEW_MUTATION, {"i": {
        "name": "Undelegated", "kind": "issue",
        "filters": {"delegate": {"is": None}}}})["viewCreate"]
    nodes = _gql(client, token, VIEW_ISSUES,
                 {"id": created["view"]["id"]})["view"]["issues"]["nodes"]
    assert [node["identifier"] for node in nodes] == [loose["identifier"]]
