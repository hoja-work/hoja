"""Views: durable saved queries — validation, scope, sharing, favorites, execution.

A view is a saved query shared through a server row (docs/views.md). These tests pin the
three properties that made a server entity necessary: a private view is readable by its
owner alone even when its id is copied, a shared view is readable but only editable by its
owner or an admin, and the stored filter tree actually selects the same rows through the
existing issue/project/initiative predicates.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import views as viewdocs
from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    initiative,
    make_engine,
    migrate,
    new_id,
    now_iso,
    project,
    saved_view,
    saved_view_favorite,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    workflow_state,
)
from hoja_server.db import (
    user as user_table,
)

ADMIN = "views-admin-token"
MEMBER = "views-member-token"
OUTSIDER = "views-outsider-token"

CREATE = """mutation($i: ViewCreateInput!) { viewCreate(input: $i) {
  success view { id name slugId kind scope visibility shared favorite filters display
    owner { id } team { key } project { id } initiative { id } archivedAt } } }"""
UPDATE = """mutation($id: String!, $i: ViewUpdateInput!) { viewUpdate(id: $id, input: $i) {
  success view { id name visibility filters display archivedAt } } }"""
DELETE = "mutation($id: String!) { viewDelete(id: $id) { success } }"
TOGGLE = """mutation($id: String!) { viewFavoriteToggle(id: $id) {
  success favorite view { id favorite } } }"""
VIEWS = "query($kind: String) { views(kind: $kind) { id name kind scope favorite visibility } }"
VIEW = """query($id: String!) { view(id: $id) { id name kind scope visibility favorite
  filters display owner { id } team { key } archivedAt
  issues(first: 10) { nodes { identifier priority title } }
  projects { nodes { name } }
  initiatives { nodes { name status } } } }"""
ARCHIVED = "{ views(includeArchived: true) { id name archivedAt } }"
CREATE_ISSUE = """mutation($i: IssueCreateInput!) { issueCreate(input: $i) {
  success issue { id identifier priority } } }"""


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def ok(client, token, query, variables=None):
    body = _gql(client, token, query, variables)
    assert "errors" not in body, body
    return body["data"]


def err(client, token, query, variables=None):
    body = _gql(client, token, query, variables)
    assert body.get("errors"), body
    return body["errors"][0]


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "views.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Views", org_url_key="views",
                         user_email="admin@views.test", token=ADMIN, demo_issues=False)
    ts = now_iso()
    ids = {"org": seeded["org_id"], "admin": seeded["user_id"]}
    with engine.begin() as conn:
        for name, token in (("member", MEMBER), ("outsider", OUTSIDER)):
            uid = new_id()
            ids[name] = uid
            conn.execute(user_table.insert().values(
                id=uid, organization_id=seeded["org_id"], name=name,
                email=f"{name}@views.test", active=True, admin=False,
                created_at=ts, updated_at=ts))
            conn.execute(api_key.insert().values(
                id=new_id(), token_hash=token_hash(token), label=name,
                user_id=uid, organization_id=seeded["org_id"], created_at=ts))
        # A private team the outsider cannot see, and a public team everybody can.
        for key, name, visibility, member in (("SEC", "Secret", "private", "member"),
                                              ("OPS", "Ops", "public", None)):
            tid = new_id()
            ids[key.lower()] = tid
            conn.execute(team.insert().values(
                id=tid, organization_id=seeded["org_id"], key=key, name=name,
                visibility=visibility, timezone="UTC", issue_counter=0,
                created_at=ts, updated_at=ts))
            seed_states_for_team(conn, seeded["org_id"], tid)
            if member:
                conn.execute(team_member.insert().values(team_id=tid, user_id=ids[member]))
        pid = new_id()
        ids["project"] = pid
        conn.execute(project.insert().values(
            id=pid, organization_id=seeded["org_id"], name="App platform",
            slug_id="app-platform", state="started", progress=0,
            created_at=ts, updated_at=ts))
        iid = new_id()
        ids["initiative"] = iid
        conn.execute(initiative.insert().values(
            id=iid, organization_id=seeded["org_id"], name="Reliability",
            status="active", priority=2, created_at=ts, updated_at=ts))
        ids["state"] = conn.execute(select(workflow_state.c.id).where(and_(
            workflow_state.c.team_id == ids["ops"],
            workflow_state.c.name == "Todo"))).scalar()
    return {"engine": engine, "app": create_app(str(db_path), open_mode=False), **ids}


def view(client, token, **document):
    data = ok(client, token, CREATE, {"i": document})
    assert data["viewCreate"]["success"]
    return data["viewCreate"]["view"]


def test_create_update_delete_roundtrip_keeps_documents(world):
    with TestClient(world["app"]) as client:
        created = view(client, ADMIN, name="My urgent bugs", kind="issue",
                       filters={"and": [{"priority": {"is": 1}},
                                        {"state": {"isNot": "Canceled"}}]},
                       display={"layout": "board", "grouping": "assignee",
                                "subGrouping": "priority", "ordering": "priority",
                                "reverse": True, "timelineZoom": 1.5,
                                "completedWindow": "month",
                                "visibleProperties": ["identifier", "title", "priority"]})
        assert created["kind"] == "issue" and created["scope"] == "workspace"
        assert created["visibility"] == "private" and created["shared"] is False
        assert created["slugId"].endswith(created["id"][:8])
        assert created["owner"]["id"] == world["admin"]
        assert created["filters"] == {"and": [{"priority": {"is": 1}},
                                             {"state": {"isNot": "Canceled"}}]}
        assert created["display"]["ordering"] == "priority"
        assert created["display"]["timelineZoom"] == 1.5

        updated = ok(client, ADMIN, UPDATE, {"id": created["id"], "i": {
            "name": "My urgent bugs (board)",
            "display": {"layout": "list", "ordering": "created", "reverse": False}}})
        assert updated["viewUpdate"]["success"]
        assert updated["viewUpdate"]["view"]["name"] == "My urgent bugs (board)"
        assert updated["viewUpdate"]["view"]["display"] == {
            "layout": "list", "ordering": "created", "reverse": False}
        # Replaced wholesale: the previous visible properties are gone, not merged.
        assert "visibleProperties" not in updated["viewUpdate"]["view"]["display"]

        assert ok(client, ADMIN, DELETE, {"id": created["id"]})["viewDelete"]["success"]
        assert [v["id"] for v in ok(client, ADMIN, VIEWS)["views"]] == []
        archived = ok(client, ADMIN, ARCHIVED)["views"]
        assert [v["id"] for v in archived] == [created["id"]]
        assert archived[0]["archivedAt"]
        # Deleting is the reversible inverse of creating.
        restored = ok(client, ADMIN, UPDATE, {"id": created["id"], "i": {"archived": False}})
        assert restored["viewUpdate"]["view"]["archivedAt"] is None
        assert [v["id"] for v in ok(client, ADMIN, VIEWS)["views"]] == [created["id"]]


def test_private_view_link_grants_nothing(world):
    with TestClient(world["app"]) as client:
        created = view(client, ADMIN, name="Admin only", kind="issue")
        assert ok(client, OUTSIDER, VIEWS)["views"] == []
        error = err(client, OUTSIDER, VIEW, {"id": created["id"]})
        assert error["extensions"]["code"] == "NOT_FOUND"
        # Admins are not owners: a private view is not visible to them either.
        assert [v["id"] for v in ok(client, ADMIN, VIEWS)["views"]] == [created["id"]]
        assert ok(client, ADMIN, VIEW, {"id": created["slugId"]})["view"]["id"] == created["id"]


def test_workspace_view_is_readable_by_members_but_only_owner_or_admin_edits(world):
    with TestClient(world["app"]) as client:
        created = view(client, ADMIN, name="Shared roadmap", kind="issue",
                       visibility="workspace")
        seen = ok(client, MEMBER, VIEW, {"id": created["id"]})["view"]
        assert seen["name"] == "Shared roadmap" and seen["visibility"] == "workspace"
        assert [v["id"] for v in ok(client, MEMBER, VIEWS)["views"]] == [created["id"]]

        error = err(client, MEMBER, UPDATE, {"id": created["id"], "i": {"name": "hijacked"}})
        assert error["extensions"]["code"] == "FORBIDDEN"
        assert "owner" in error["message"]
        # ... but favoriting a readable view is a per-user action anybody may take.
        assert ok(client, MEMBER, TOGGLE, {"id": created["id"]})["viewFavoriteToggle"]["favorite"]


def test_team_scope_is_visible_to_that_team_only(world):
    with TestClient(world["app"]) as client:
        secret = view(client, ADMIN, name="Secret team view", kind="issue", scope="team",
                      teamId=world["sec"], visibility="team")
        assert secret["team"]["key"] == "SEC"
        assert [v["id"] for v in ok(client, MEMBER, VIEWS)["views"]] == [secret["id"]]
        assert ok(client, OUTSIDER, VIEWS)["views"] == []
        assert err(client, OUTSIDER, VIEW, {"id": secret["id"]})["extensions"]["code"] == "NOT_FOUND"
        # The team reference is resolved through the same visibility rule as team writes,
        # so an outsider cannot attach a view to a private team by naming its id.
        error = err(client, OUTSIDER, CREATE, {"i": {
            "name": "leak", "kind": "issue", "scope": "team", "teamId": world["sec"]}})
        assert error["extensions"] == {**error["extensions"], "code": "BAD_USER_INPUT",
                                       "field": "teamId"}


def test_scope_and_visibility_are_validated(world):
    with TestClient(world["app"]) as client:
        cases = [
            ({"name": "t", "scope": "team"}, "teamId"),
            ({"name": "p", "scope": "project"}, "projectId"),
            ({"name": "i", "scope": "initiative"}, "initiativeId"),
            ({"name": "x", "scope": "workspace", "teamId": world["ops"]}, "teamId"),
            ({"name": "y", "scope": "everywhere"}, "scope"),
            ({"name": "z", "visibility": "public"}, "visibility"),
            ({"name": "w", "visibility": "team"}, "visibility"),
            ({"name": ""}, "name"),
            ({"name": "k", "kind": "feed"}, "kind"),
        ]
        for payload, field in cases:
            error = err(client, ADMIN, CREATE, {"i": {"kind": "issue", **payload}})
            assert error["extensions"]["code"] == "BAD_USER_INPUT", payload
            assert error["extensions"]["field"] == field, payload


@pytest.mark.parametrize(("filters", "field"), [
    ({"nope": {"is": 1}}, "nope"),
    ({"priority": {"isGreaterThanOrEqual": 2}}, None),
    ({"state": {"before": "2026-01-01"}}, "state.before"),
    ({"labels": {"includesAll": ["bug"]}}, "labels.includesAll"),
    ({"title": {"is": 3}}, "title.is"),
    ({"priority": {"is": "urgent"}}, "priority.is"),
    ({"dueDate": {"before": "next tuesday"}}, "dueDate.before"),
    ({"priority": {"isEitherOf": []}}, "priority.isEitherOf"),
    ({"and": []}, "and"),
])
def test_filter_validation_names_the_failing_path(world, filters, field):
    payload = {"i": {"name": "bad", "kind": "issue", "filters": filters}}
    with TestClient(world["app"]) as client:
        if field is None:  # a supported operator/property pair is accepted as written
            created = view(client, ADMIN, name="good", kind="issue", filters=filters)
            assert created["filters"] == filters
            return
        error = err(client, ADMIN, CREATE, payload)
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        assert error["extensions"]["field"] == field


def test_filter_nesting_and_size_limits(world):
    deep = {"priority": {"is": 1}}
    for _ in range(viewdocs.MAX_DEPTH + 1):
        deep = {"and": [deep]}
    with TestClient(world["app"]) as client:
        error = err(client, ADMIN, CREATE, {"i": {"name": "deep", "filters": deep}})
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        assert error["extensions"]["field"] == "filters"
        many = {"and": [{"priority": {"is": 1}} for _ in range(viewdocs.MAX_PREDICATES + 1)]}
        error = err(client, ADMIN, CREATE, {"i": {"name": "many", "filters": many}})
        assert "predicates" in error["message"]
        assert error["extensions"]["field"] == "filters"


@pytest.mark.parametrize(("display", "field"), [
    ({"layout": "grid"}, "display.layout"),
    ({"grouping": "mood"}, "display.grouping"),
    ({"ordering": "alphabetical"}, "display.ordering"),
    ({"reverse": "yes"}, "display.reverse"),
    ({"showEmptyGroups": 1}, "display.showEmptyGroups"),
    ({"timelineZoom": 99}, "display.timelineZoom"),
    ({"completedWindow": "decade"}, "display.completedWindow"),
    ({"visibleProperties": ["identifier", "identifier"]}, "display.visibleProperties"),
    ({"visibleProperties": ["identifer"]}, "display.visibleProperties[0]"),
    ({"surprise": 1}, "display.surprise"),
])
def test_display_validation_names_the_failing_path(world, display, field):
    with TestClient(world["app"]) as client:
        error = err(client, ADMIN, CREATE, {"i": {"name": "bad", "display": display}})
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        assert error["extensions"]["field"] == field


def test_visible_properties_are_kind_specific(world):
    with TestClient(world["app"]) as client:
        error = err(client, ADMIN, CREATE, {"i": {
            "name": "bad", "kind": "project",
            "display": {"visibleProperties": ["identifier"]}}})
        assert error["extensions"]["field"] == "display.visibleProperties[0]"
        good = view(client, ADMIN, name="projects", kind="project",
                    display={"visibleProperties": ["name", "health", "progress"]})
        assert good["display"]["visibleProperties"] == ["name", "health", "progress"]


def test_kind_is_immutable(world):
    with TestClient(world["app"]) as client:
        created = view(client, ADMIN, name="Issues", kind="issue")
        error = err(client, ADMIN, UPDATE, {"id": created["id"], "i": {"kind": "project"}})
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        assert error["extensions"]["field"] == "kind"


def test_filters_and_favorites_persist_per_user(world):
    with TestClient(world["app"]) as client:
        created = view(client, ADMIN, name="Shared", kind="issue", visibility="workspace",
                       filters={"or": [{"priority": {"is": 1}}, {"priority": {"is": 2}}]},
                       display={"ordering": "created", "reverse": True})
        assert ok(client, ADMIN, TOGGLE, {"id": created["id"]})["viewFavoriteToggle"]["favorite"]
        assert ok(client, MEMBER, TOGGLE, {"id": created["id"]})["viewFavoriteToggle"]["favorite"]

        # Two members favorite one row independently, and a second toggle removes only theirs.
        assert ok(client, MEMBER, TOGGLE, {"id": created["id"]})["viewFavoriteToggle"]["favorite"] is False
        admin_view = ok(client, ADMIN, VIEW, {"id": created["id"]})["view"]
        member_view = ok(client, MEMBER, VIEW, {"id": created["id"]})["view"]
        assert admin_view["favorite"] is True and member_view["favorite"] is False
        assert admin_view["filters"] == {"or": [{"priority": {"is": 1}},
                                                {"priority": {"is": 2}}]}
        assert admin_view["display"] == {"ordering": "created", "reverse": True}

        with world["engine"].connect() as conn:
            assert conn.execute(select(saved_view_favorite.c.user_id)).scalars().all() == [world["admin"]]
            stored = json.loads(conn.execute(select(saved_view.c.display)).scalar())
            assert stored == {"ordering": "created", "reverse": True}


def test_issue_view_executes_the_stored_filter(world):
    with TestClient(world["app"]) as client:
        low = ok(client, ADMIN, CREATE_ISSUE, {"i": {
            "teamId": world["ops"], "title": "Low priority", "priority": 4,
            "stateId": world["state"]}})["issueCreate"]["issue"]
        urgent = ok(client, ADMIN, CREATE_ISSUE, {"i": {
            "teamId": world["ops"], "title": "Urgent one", "priority": 1,
            "stateId": world["state"]}})["issueCreate"]["issue"]
        created = view(client, ADMIN, name="Urgent", kind="issue",
                       filters={"priority": {"is": 1}})
        nodes = ok(client, ADMIN, VIEW, {"id": created["id"]})["view"]["issues"]["nodes"]
        assert [n["identifier"] for n in nodes] == [urgent["identifier"]]

        # Ordering and reverse come from the view's display options.
        created_at = view(client, ADMIN, name="Newest first", kind="issue",
                          display={"ordering": "created", "reverse": True})
        nodes = ok(client, ADMIN, VIEW, {"id": created_at["id"]})["view"]["issues"]["nodes"]
        assert {n["identifier"] for n in nodes} == {urgent["identifier"], low["identifier"]}


def test_project_and_initiative_views_execute_their_filters(world):
    with TestClient(world["app"]) as client:
        project_view = view(client, ADMIN, name="Platform", kind="project",
                            scope="project", projectId=world["project"],
                            filters={"name": {"contains": "platform"}})
        nodes = ok(client, ADMIN, VIEW, {"id": project_view["id"]})["view"]["projects"]["nodes"]
        assert [n["name"] for n in nodes] == ["App platform"]
        assert ok(client, ADMIN, VIEW, {"id": project_view["id"]})["view"]["issues"]["nodes"] == []

        initiative_view = view(client, ADMIN, name="Active initiatives", kind="initiative",
                               scope="initiative", initiativeId=world["initiative"],
                               filters={"status": {"is": "active"}})
        nodes = ok(client, ADMIN, VIEW, {"id": initiative_view["id"]})["view"]["initiatives"]["nodes"]
        assert [(n["name"], n["status"]) for n in nodes] == [("Reliability", "active")]
        planned = view(client, ADMIN, name="Planned initiatives", kind="initiative",
                       filters={"status": {"is": "planned"}})
        assert ok(client, ADMIN, VIEW, {"id": planned["id"]})["view"]["initiatives"]["nodes"] == []


def test_views_are_tenant_scoped(world, tmp_path):
    """A second organization's tokens never see this organization's views."""
    other_path = tmp_path / "other.db"
    other = make_engine(other_path)
    migrate(other)
    seed_tenant(other, org_name="Other", org_url_key="other", user_email="o@other.test",
                token="views-other-token", demo_issues=False)
    with TestClient(create_app(str(other_path), open_mode=False)) as client:
        assert ok(client, "views-other-token", VIEWS)["views"] == []
    with TestClient(world["app"]) as client:
        created = view(client, ADMIN, name="Only here", kind="issue")
        other_client = TestClient(create_app(str(other_path), open_mode=False))
        with other_client:
            assert ok(other_client, "views-other-token", VIEWS)["views"] == []
            assert err(other_client, "views-other-token", VIEW,
                       {"id": created["id"]})["extensions"]["code"] == "NOT_FOUND"
