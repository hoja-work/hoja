"""Task label groups, as Linear has them (SPEC.md §4).

Captured contract: a group cannot be applied; a task holds one label per (exclusive) group,
refused with Linear's own message; "Labels include <group>" matches the group's labels and is
stored as ``{or: [{name}, {parent: {name}}]}``; a list can be grouped by a label group.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant, team, workflow_state

TOKEN = "labels-token"


def call(client, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()


def ok(client, query, variables=None):
    body = call(client, query, variables)
    assert "errors" not in body, body
    return body["data"]


def err(client, query, variables=None):
    body = call(client, query, variables)
    assert body.get("errors"), body
    return body["errors"][0]


LABEL = """mutation($i: TaskLabelCreateInput!) { taskLabelCreate(input: $i) {
  success issueLabel { id name isGroup exclusive parentId } } }"""
LABEL_UPDATE = """mutation($id: String!, $i: TaskLabelUpdateInput!) { taskLabelUpdate(id: $id, input: $i) {
  success issueLabel { id parentId isGroup exclusive } } }"""
CREATE = "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { issue { id identifier } } }"
UPDATE = "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { issue { id } } }"
PREVIEW = """query($f: JSON) { viewPreview(kind: "issue", filters: $f, display: {completedWindow: "all"}) {
  issues(first: 100) { nodes { title } } } }"""
ISSUES = "query($f: IssueFilter) { issues(filter: $f, first: 100) { nodes { title } } }"


@pytest.fixture()
def world(tmp_path):
    db = tmp_path / "labels.db"
    engine = make_engine(db)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="L", org_url_key="l", token=TOKEN, demo_issues=False)
    with engine.connect() as conn:
        tid = conn.execute(select(team.c.id).where(team.c.organization_id == seeded["org_id"])).scalar()
        todo = conn.execute(select(workflow_state.c.id).where(
            (workflow_state.c.team_id == tid) & (workflow_state.c.name == "Todo"))).scalar()
    with TestClient(create_app(str(db), open_mode=False)) as client:
        w = {"client": client, "team": tid, "todo": todo}
        label = lambda **i: ok(client, LABEL, {"i": {"teamId": tid, **i}})["taskLabelCreate"]["issueLabel"]  # noqa: E731
        w["area"] = label(name="area", isGroup=True)
        w["api"] = label(name="api", parentId=w["area"]["id"])
        w["ui"] = label(name="ui", parentId=w["area"]["id"])
        w["bug"] = label(name="Bug")
        for title, labels in (("A", ["api", "bug"]), ("B", ["ui"]), ("C", ["bug"]), ("D", [])):
            ok(client, CREATE, {"i": {"title": title, "teamId": tid, "stateId": todo,
                                      "labelIds": [w[n]["id"] for n in labels]}})
        yield w


def titles(world, filters):
    return sorted(n["title"] for n in ok(world["client"], PREVIEW, {"f": filters})["viewPreview"]["issues"]["nodes"])


def test_group_shape_and_children(world):
    assert world["area"]["isGroup"] and world["area"]["exclusive"]
    assert world["api"]["parentId"] == world["area"]["id"]
    data = ok(world["client"], "{ taskLabels { nodes { name isGroup parent { name } children { name } } } }")
    by = {n["name"]: n for n in data["taskLabels"]["nodes"]}
    assert sorted(c["name"] for c in by["area"]["children"]) == ["api", "ui"]
    assert by["api"]["parent"]["name"] == "area" and by["Bug"]["children"] == []


def test_exclusive_group_refused_with_linears_message(world):
    e = err(world["client"], CREATE, {"i": {"title": "X", "teamId": world["team"],
                                            "labelIds": [world["api"]["id"], world["ui"]["id"]]}})
    assert e["message"] == ("The label 'ui' is in the same group as 'api'. Only one label in a "
                            "group can be applied to an issue.") or \
        e["message"] == ("The label 'api' is in the same group as 'ui'. Only one label in a "
                         "group can be applied to an issue.")
    assert e["extensions"]["field"] == "labelIds"


def test_a_group_cannot_be_applied(world):
    e = err(world["client"], CREATE, {"i": {"title": "X", "teamId": world["team"],
                                            "labelIds": [world["area"]["id"]]}})
    assert "label group" in e["message"]


def test_non_exclusive_group_allows_two(world):
    ok(world["client"], LABEL_UPDATE, {"id": world["area"]["id"], "i": {"exclusive": False}})
    ok(world["client"], CREATE, {"i": {"title": "X", "teamId": world["team"],
                                       "labelIds": [world["api"]["id"], world["ui"]["id"]]}})
    # Turning exclusivity back on would now break that task: refused.
    e = err(world["client"], LABEL_UPDATE, {"id": world["area"]["id"], "i": {"exclusive": True}})
    assert e["extensions"]["field"] == "exclusive"


def test_filter_by_group_matches_its_labels(world):
    assert titles(world, {"labels": {"includesAny": [world["area"]["id"]]}}) == ["A", "B"]
    assert titles(world, {"labels": {"includesAny": ["area"]}}) == ["A", "B"]
    assert titles(world, {"labels": {"includesAll": ["area", "Bug"]}}) == ["A"]
    assert titles(world, {"labels": {"includesNone": ["area"]}}) == ["C", "D"]
    assert titles(world, {"labels": {"includesAny": ["api"]}}) == ["A"]


def test_linear_shaped_group_filter_through_issues(world):
    linear = {"labels": {"or": [{"name": {"eq": "area"}}, {"parent": {"name": {"eq": "area"}}}]}}
    got = ok(world["client"], ISSUES, {"f": linear})["issues"]["nodes"]
    assert sorted(n["title"] for n in got) == ["A", "B"]


def test_reparent_and_ungroup_rules(world):
    # A label carried next to a sibling cannot join an exclusive group.
    loose = ok(world["client"], LABEL, {"i": {"teamId": world["team"], "name": "loose"}})["taskLabelCreate"]["issueLabel"]
    a = ok(world["client"], ISSUES, {"f": {}})["issues"]["nodes"]
    assert a
    task = ok(world["client"], CREATE, {"i": {"title": "E", "teamId": world["team"],
                                              "labelIds": [world["api"]["id"], loose["id"]]}})["issueCreate"]["issue"]
    e = err(world["client"], LABEL_UPDATE, {"id": loose["id"], "i": {"parentId": world["area"]["id"]}})
    assert e["extensions"]["field"] == "parentId"
    ok(world["client"], UPDATE, {"id": task["identifier"], "i": {"labelIds": [loose["id"]]}})
    moved = ok(world["client"], LABEL_UPDATE, {"id": loose["id"], "i": {"parentId": world["area"]["id"]}})
    assert moved["taskLabelUpdate"]["issueLabel"]["parentId"] == world["area"]["id"]
    # A group with labels cannot be ungrouped; nested groups are refused.
    assert err(world["client"], LABEL_UPDATE, {"id": world["area"]["id"], "i": {"isGroup": False}})
    assert err(world["client"], LABEL, {"i": {"teamId": world["team"], "name": "sub",
                                              "isGroup": True, "parentId": world["area"]["id"]}})
    # A label already on tasks cannot become a group.
    assert err(world["client"], LABEL_UPDATE, {"id": world["bug"]["id"], "i": {"isGroup": True}})


def test_label_group_grouping_is_a_valid_display(world):
    data = ok(world["client"], """query($d: JSON) { viewPreview(kind: "issue", filters: {},
      display: $d) { issues(first: 5) { nodes { title } } } }""",
              {"d": {"grouping": "labelGroup", "groupingLabelGroupId": world["area"]["id"]}})
    assert data["viewPreview"]["issues"]["nodes"]
    assert err(world["client"], PREVIEW.replace('display: {completedWindow: "all"}',
                                                'display: {groupingLabelGroupId: 3}'), {"f": {}})
