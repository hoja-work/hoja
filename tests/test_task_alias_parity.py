"""HOJA-288: the task* surface must be the same entities as the issue* surface.

Wiring that "works" but forks state is the failure this file exists to catch. Two
names, one row: identical payloads, and an update through either name visible
through the other.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

FIELDS = """
  id identifier title priority updatedAt
  state { id name }
  assignee { id name }
  team { id key }
  parent { id identifier }
  children(first: 50) { nodes { id identifier } }
"""

CREATE_TASK = """
mutation($input: TaskCreateInput!) {
  taskCreate(input: $input) { success task { %s } }
}
""" % FIELDS

ONE_TASK = "query($id: String!) { task(id: $id) { %s } }" % FIELDS
ONE_ISSUE = "query($id: String!) { issue(id: $id) { %s } }" % FIELDS
TASKS = "query { tasks(first: 50) { nodes { %s } } }" % FIELDS
ISSUES = "query { issues(first: 50) { nodes { %s } } }" % FIELDS

UPDATE_TASK = """
mutation($id: String!, $input: TaskUpdateInput!) {
  taskUpdate(id: $id, input: $input) { success task { id title } }
}
"""


@pytest.fixture
def world(tmp_path):
    path = tmp_path / "parity.db"
    engine = make_engine(path)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Parity", org_url_key="parity", team_key="ENG",
        user_name="Parity Admin", user_email="admin@example.test", demo_issues=False,
    )
    app = create_app(db_path=str(path))
    with TestClient(app) as client:
        yield client, seeded["token"]


def _data(client, token, query, variables=None):
    result = client.post(
        "/graphql", headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables or {}},
    ).json()
    assert "errors" not in result, result
    return result["data"]


def test_task_and_issue_names_return_the_same_entity(world) -> None:
    client, token = world
    created = _data(client, token, CREATE_TASK, {"input": {"title": "Shared row", "teamId": "ENG"}})
    assert created["taskCreate"]["success"] is True
    task = created["taskCreate"]["task"]

    via_task = _data(client, token, ONE_TASK, {"id": task["identifier"]})["task"]
    via_issue = _data(client, token, ONE_ISSUE, {"id": task["identifier"]})["issue"]

    assert via_task is not None and via_issue is not None
    assert via_task == via_issue, "task and issue must project the identical row"
    assert via_task["id"] == via_issue["id"]


def test_list_surfaces_agree(world) -> None:
    client, token = world
    for title in ("Alpha", "Beta"):
        _data(client, token, CREATE_TASK, {"input": {"title": title, "teamId": "ENG"}})

    tasks = _data(client, token, TASKS)["tasks"]["nodes"]
    issues = _data(client, token, ISSUES)["issues"]["nodes"]
    assert [n["id"] for n in tasks] == [n["id"] for n in issues]


def test_update_through_the_new_name_is_visible_through_the_old_one(world) -> None:
    client, token = world
    task = _data(client, token, CREATE_TASK, {"input": {"title": "Before", "teamId": "ENG"}})["taskCreate"]["task"]

    _data(client, token, UPDATE_TASK, {"id": task["identifier"], "input": {"title": "After"}})

    assert _data(client, token, ONE_ISSUE, {"id": task["identifier"]})["issue"]["title"] == "After"
    assert _data(client, token, ONE_TASK, {"id": task["identifier"]})["task"]["title"] == "After"


def test_issue_names_are_deprecated_in_introspection(world) -> None:
    """Deprecation is a contract that codegen and agents read, not a comment."""
    client, token = world
    data = _data(client, token, """
      query {
        __type(name: "Query") {
          fields(includeDeprecated: true) { name isDeprecated deprecationReason }
        }
        m: __type(name: "Mutation") {
          fields(includeDeprecated: true) { name isDeprecated deprecationReason }
        }
      }
    """)
    fields = data["__type"]["fields"] + data["m"]["fields"]
    issue_fields = [f for f in fields if f["name"].lower().startswith("issue")]
    assert issue_fields, "expected issue* root fields to still exist"
    for f in issue_fields:
        assert f["isDeprecated"] is True, f"{f['name']} kept but not marked deprecated"
        assert f["deprecationReason"], f"{f['name']} deprecated without a reason"

    task_fields = [f for f in fields if f["name"].lower().startswith("task")]
    assert task_fields, "expected task* root fields"
    for f in task_fields:
        assert f["isDeprecated"] is False, f"new surface {f['name']} must not be deprecated"


def test_mcp_exposes_both_name_families(world) -> None:
    from hoja_server.mcp_tools import build_tools
    from hoja_server.resolvers import build_schema

    names = {t.name for t in build_tools(build_schema(), lambda write: {})}
    for op in ("tasks", "task", "taskCreate", "taskUpdate", "taskDelete", "searchTasks"):
        assert f"hoja_{op}" in names, f"missing new tool hoja_{op}"
    for op in ("issues", "issue", "issueCreate", "issueUpdate", "issueDelete", "searchIssues"):
        assert f"hoja_{op}" in names, f"the old tool hoja_{op} must keep working"
