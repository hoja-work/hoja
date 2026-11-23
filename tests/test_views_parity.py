"""Views at Linear parity: grammar breadth, preference layers, subscriptions, ownership,
CSV export, the customView* aliases and stable view URLs.

``test_views.py`` pins the document contract and ``test_views_exec.py`` the execution
engine; these tests pin what a view selects for *whom* and *when* ("me", "my teams",
relative dates), what each member sees when a shared view is re-arranged, who hears about a
view's tasks, and that Linear-shaped clients read and write the same rows.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select, update
from starlette.testclient import TestClient

from hoja_server import view_linear, views_api
from hoja_server import views as viewdocs
from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    inbox_notification,
    initiative,
    initiative_project,
    issue,
    issue_label,
    issue_label_link,
    issue_subscriber,
    make_engine,
    migrate,
    new_id,
    now_iso,
    project,
    project_member,
    project_update,
    saved_view,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    view_preference,
    view_subscription,
    workflow_state,
)
from hoja_server.db import (
    user as user_table,
)

ADMIN, ALICE, BOB = "parity-admin", "parity-alice", "parity-bob"

PREVIEW = """query($k: String!, $f: JSON, $d: JSON) { viewPreview(kind: $k, filters: $f,
  display: $d) { issues(first: 100) { nodes { title } } projects(first: 100) { nodes { name } } } }"""
CREATE = """mutation($i: ViewCreateInput!) { viewCreate(input: $i) { success view {
  id slugId url filters display owner { id } visibility scope } } }"""
UPDATE = """mutation($id: String!, $i: ViewUpdateInput!) { viewUpdate(id: $id, input: $i) {
  view { id owner { id } visibility scope team { key } } } }"""
VIEW = """query($id: String!) { view(id: $id) { id slugId url name filters display
  subscriberCount subscription { onAdded onCompleted }
  preferences { default user effective hasUserOverrides }
  issues(first: 50) { nodes { title } } } }"""
PREFS = """query($v: String, $t: String) { viewPreferences(viewId: $v, viewType: $t) {
  viewId viewType default user effective hasUserOverrides } }"""
PREFS_SET = """mutation($v: String, $t: String, $values: JSON!, $scope: String) {
  viewPreferencesSet(viewId: $v, viewType: $t, values: $values, scope: $scope) {
    default user effective hasUserOverrides } }"""
PREFS_RESET = """mutation($v: String, $t: String) { viewPreferencesReset(viewId: $v, viewType: $t) {
  default user effective hasUserOverrides } }"""
SUBSCRIBE = """mutation($v: String!, $a: Boolean, $c: Boolean) { viewSubscribe(viewId: $v,
  onAdded: $a, onCompleted: $c) { success subscription { onAdded onCompleted view { id } } } }"""
UNSUBSCRIBE = """mutation($v: String!) { viewUnsubscribe(viewId: $v) { success subscription { id } } }"""
DUPLICATE = """mutation($id: String!, $n: String, $vis: String) { viewDuplicate(id: $id, name: $n,
  visibility: $vis) { success view { id name visibility filters display owner { id } } } }"""
ISSUE_CREATE = """mutation($i: IssueCreateInput!) { issueCreate(input: $i) { issue { id } } }"""
ISSUE_UPDATE = """mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) {
  success } }"""
CV_FIELDS = """id slugId name shared modelName filterData projectFilterData url
  owner { id } team { key } viewPreferencesValues organizationViewPreferences { type preferences }
  userViewPreferences { type preferences } issues(first: 50) { nodes { title } }"""


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


NOW = datetime.now(timezone.utc)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "parity.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Parity", org_url_key="parity",
                         user_email="admin@parity.test", token=ADMIN, demo_issues=False)
    org, admin = seeded["org_id"], seeded["user_id"]
    ts = now_iso()
    ids: dict = {"org": org, "admin": admin}
    today = NOW.date()
    with engine.begin() as conn:
        for name, token in (("alice", ALICE), ("bob", BOB)):
            uid = new_id()
            ids[name] = uid
            conn.execute(user_table.insert().values(
                id=uid, organization_id=org, name=name, display_name=name,
                email=f"{name}@parity.test", active=True, admin=False,
                created_at=ts, updated_at=ts))
            conn.execute(api_key.insert().values(
                id=new_id(), token_hash=token_hash(token), label=name,
                user_id=uid, organization_id=org, created_at=ts))
        for key in ("OPS", "WEB"):
            tid = new_id()
            ids[key] = tid
            conn.execute(team.insert().values(
                id=tid, organization_id=org, key=key, name=key.title(), visibility="public",
                timezone="UTC", issue_counter=100, created_at=ts, updated_at=ts))
            seed_states_for_team(conn, org, tid)
        conn.execute(team_member.insert().values(team_id=ids["OPS"], user_id=ids["alice"]))
        conn.execute(team_member.insert().values(team_id=ids["WEB"], user_id=ids["bob"]))

        def state(team_key, name):
            return conn.execute(select(workflow_state.c.id).where(and_(
                workflow_state.c.team_id == ids[team_key],
                workflow_state.c.name == name))).scalar()

        ids["ops_done"] = state("OPS", "Done")
        ids["ops_todo"] = state("OPS", "Todo")
        for name in ("Bug", "Frontend"):
            lid = new_id()
            ids[name] = lid
            conn.execute(issue_label.insert().values(
                id=lid, organization_id=org, name=name, created_at=ts, updated_at=ts))
        rows = [
            # title, team, state, prio, assignee, creator, labels, estimate, created,
            # due, parent, started, completed
            ("Parent", "OPS", "In Progress", 1, "alice", "admin", ["Bug", "Frontend"], 3,
             ago(3), (today - timedelta(days=2)).isoformat(), None, ago(2), None),
            ("Child", "OPS", "Todo", 2, "bob", "alice", ["Bug"], None,
             ago(20), (today + timedelta(days=5)).isoformat(), "Parent", None, None),
            ("Loner", "WEB", "Backlog", 3, None, "bob", [], 1,
             ago(40), None, None, None, None),
            ("Shipped", "WEB", "Done", 0, "alice", "admin", ["Frontend"], 5,
             ago(10), None, None, ago(9), ago(1)),
        ]
        for n, (title, team_key, state_name, prio, assignee, creator, labels, estimate,
                created, due, parent, started, completed) in enumerate(rows):
            iid = new_id()
            ids[title] = iid
            conn.execute(issue.insert().values(
                id=iid, organization_id=org, team_id=ids[team_key], number=n + 1,
                identifier=f"{team_key}-{n + 1}", title=title, priority=prio,
                estimate=estimate, state_id=state(team_key, state_name),
                assignee_id=ids.get(assignee), creator_id=ids.get(creator),
                parent_id=ids.get(parent), due_date=due, started_at=started,
                completed_at=completed, created_at=created, updated_at=created,
                url=f"https://hoja.test/parity/issue/{team_key}-{n + 1}",
                decision_needed=False))
            for label in labels:
                conn.execute(issue_label_link.insert().values(issue_id=iid, label_id=ids[label]))
        conn.execute(issue_subscriber.insert().values(issue_id=ids["Loner"], user_id=ids["alice"]))

        # Projects: lead, members, health through updates, dates, an initiative.
        for name, status, lead, creator, start, target, summary in (
                ("Platform", "In Progress", "alice", "admin", (today - timedelta(days=10)),
                 (today + timedelta(days=20)), "Core services and the API"),
                ("Website", "Planned", "bob", "bob", None, (today - timedelta(days=1)),
                 "Marketing site"),
                ("Archive", "Backlog", None, "admin", None, None, None)):
            pid = new_id()
            ids[name] = pid
            conn.execute(project.insert().values(
                id=pid, organization_id=org, name=name, slug_id=name.lower(), state=status,
                progress=0, lead_id=ids.get(lead), creator_id=ids.get(creator),
                start_date=start.isoformat() if start else None,
                target_date=target.isoformat() if target else None, description=summary,
                created_at=ts, updated_at=ts))
        conn.execute(project_member.insert().values(project_id=ids["Platform"], user_id=ids["bob"]))
        for pid, health in ((ids["Platform"], "on track"), (ids["Website"], "at risk")):
            conn.execute(project_update.insert().values(
                id=new_id(), organization_id=org, project_id=pid, body="update",
                health=health, created_at=ts, updated_at=ts))
        iid = new_id()
        ids["Reliability"] = iid
        conn.execute(initiative.insert().values(
            id=iid, organization_id=org, name="Reliability", status="active", priority=0,
            created_at=ts, updated_at=ts))
        conn.execute(initiative_project.insert().values(
            organization_id=org, initiative_id=iid, project_id=ids["Platform"]))
    return {"engine": engine, "app": create_app(str(db_path), open_mode=False), **ids}


def issue_titles(client, token, filters, display=None):
    data = ok(client, token, PREVIEW, {"k": "issue", "f": filters,
                                       "d": display or {"completedWindow": "all"}})
    return sorted(n["title"] for n in data["viewPreview"]["issues"]["nodes"])


def project_names(client, token, filters):
    data = ok(client, token, PREVIEW, {"k": "project", "f": filters})
    return sorted(n["name"] for n in data["viewPreview"]["projects"]["nodes"])


def make_view(client, token, **document):
    return ok(client, token, CREATE, {"i": document})["viewCreate"]["view"]


# ------------------------------------------------------------------ (a) grammar

def test_me_is_the_viewer_running_the_view(world):
    with TestClient(world["app"]) as client:
        assert issue_titles(client, ALICE, {"assignee": {"is": "me"}}) == ["Parent", "Shipped"]
        assert issue_titles(client, BOB, {"assignee": {"is": "me"}}) == ["Child"]
        assert issue_titles(client, ALICE, {"assignee": {"isMe": True}}) == ["Parent", "Shipped"]
        # "is not me" keeps unassigned tasks, like any negative predicate on a nullable field.
        assert issue_titles(client, ALICE, {"assignee": {"isNot": "me"}}) == ["Child", "Loner"]
        assert issue_titles(client, ALICE, {"assignee": {"isEitherOf": ["me", None]}}) == \
            ["Loner", "Parent", "Shipped"]
        assert issue_titles(client, ALICE, {"creator": {"isMe": True}}) == ["Child"]
        assert issue_titles(client, ALICE, {"subscriber": {"is": "me"}}) == ["Loner"]
        assert issue_titles(client, ALICE, {"subscriber": {"isMe": False}}) == \
            ["Child", "Parent", "Shipped"]
        # One stored view, two viewers, two results: "me" is resolved when the view runs.
        saved = make_view(client, ALICE, name="Mine", visibility="workspace",
                          filters={"assignee": {"is": "me"}})
        assert saved["filters"] == {"assignee": {"is": "me"}}
        for token, expected in ((ALICE, ["Parent", "Shipped"]), (BOB, ["Child"])):
            nodes = ok(client, token, VIEW, {"id": saved["id"]})["view"]["issues"]["nodes"]
            assert sorted(n["title"] for n in nodes) == expected


def test_my_teams_is_the_viewers_team_membership(world):
    with TestClient(world["app"]) as client:
        assert issue_titles(client, ALICE, {"team": {"is": "myTeams"}}) == ["Child", "Parent"]
        assert issue_titles(client, BOB, {"team": {"is": "myTeams"}}) == ["Loner", "Shipped"]
        assert issue_titles(client, ALICE, {"team": {"isNot": "myTeams"}}) == ["Loner", "Shipped"]
        assert project_names(client, BOB, {"team": {"is": "myTeams"}}) == []


def test_relative_dates_resolve_when_the_view_runs(world):
    with TestClient(world["app"]) as client:
        assert issue_titles(client, ADMIN, {"createdAt": {"after": "-P7D"}}) == ["Parent"]
        assert issue_titles(client, ADMIN, {"createdAt": {"onOrAfter": "-P2W"}}) == \
            ["Parent", "Shipped"]
        assert issue_titles(client, ADMIN, {"createdAt": {"before": "-P1M"}}) == ["Loner"]
        assert issue_titles(client, ADMIN, {"dueDate": {"is": "overdue"}}) == ["Parent"]
        assert issue_titles(client, ADMIN, {"dueDate": {"before": "P1W"}}) == ["Child", "Parent"]
        assert issue_titles(client, ADMIN, {"completedAt": {"after": "-PT36H"}}) == ["Shipped"]
        assert issue_titles(client, ADMIN, {"completedAt": {"is": None}}) == \
            ["Child", "Loner", "Parent"]
        assert issue_titles(client, ADMIN, {"startedAt": {"onOrAfter": "-P3D"}}) == ["Parent"]
        assert issue_titles(client, ADMIN, {"canceledAt": {"is": None}}) == \
            ["Child", "Loner", "Parent", "Shipped"]
        saved = make_view(client, ADMIN, name="Fresh", filters={"createdAt": {"after": "-P7D"}})
        assert saved["filters"] == {"createdAt": {"after": "-P7D"}}  # stored as written
        bad = err(client, ADMIN, CREATE, {"i": {"name": "x", "filters": {
            "createdAt": {"is": "overdue"}}}})
        assert bad["extensions"]["field"] == "createdAt.is"
        bad = err(client, ADMIN, CREATE, {"i": {"name": "x", "filters": {
            "createdAt": {"after": "last tuesday"}}}})
        assert "duration" in bad["message"]


def test_resolve_date_months_clamp_and_time_parts():
    now = datetime(2026, 3, 31, 15, 30, tzinfo=timezone.utc)
    assert viewdocs.resolve_date("-P1M", now) == "2026-02-28"
    assert viewdocs.resolve_date("P1Y", now) == "2027-03-31"
    assert viewdocs.resolve_date("-P1W", now) == "2026-03-24"
    assert viewdocs.resolve_date("today", now) == "2026-03-31"
    assert viewdocs.resolve_date("tomorrow", now) == "2026-04-01"
    assert viewdocs.resolve_date("-PT12H", now) == "2026-03-31T03:30:00+00:00"
    assert viewdocs.resolve_date("2026-01-01", now) == "2026-01-01"


def test_relation_estimate_and_label_filters(world):
    with TestClient(world["app"]) as client:
        assert issue_titles(client, ADMIN, {"parent": {"isNull": True}}) == \
            ["Loner", "Parent", "Shipped"]
        assert issue_titles(client, ADMIN, {"isSubIssue": {"is": True}}) == ["Child"]
        assert issue_titles(client, ADMIN, {"hasSubIssues": {"is": True}}) == ["Parent"]
        assert issue_titles(client, ADMIN, {"hasSubIssues": {"is": False}}) == \
            ["Child", "Loner", "Shipped"]
        assert issue_titles(client, ADMIN, {"estimate": {"isGreaterThan": 2}}) == \
            ["Parent", "Shipped"]
        assert issue_titles(client, ADMIN, {"estimate": {"is": None}}) == ["Child"]
        assert issue_titles(client, ADMIN, {"estimate": {"isNot": 3}}) == \
            ["Child", "Loner", "Shipped"]
        assert issue_titles(client, ADMIN, {"estimate": {"isEitherOf": [1, 5]}}) == \
            ["Loner", "Shipped"]
        assert issue_titles(client, ADMIN, {"labels": {"includesAll": ["Bug", "Frontend"]}}) == \
            ["Parent"]
        for prop in ("blocked", "blocking", "hasRelations", "duplicate"):
            bad = err(client, ADMIN, PREVIEW, {"k": "issue", "f": {prop: {"is": True}}})
            assert bad["extensions"]["field"] == prop
            assert "relations" in bad["message"]
        bad = err(client, ADMIN, PREVIEW, {"k": "issue", "f": {"estimate": {"isGreaterThan": None}}})
        assert bad["extensions"]["field"] == "estimate.isGreaterThan"


def test_project_filter_breadth(world):
    with TestClient(world["app"]) as client:
        assert project_names(client, ADMIN, {"status": {"is": "in progress"}}) == ["Platform"]
        assert project_names(client, ADMIN, {"status": {"isNeitherOf": ["Planned", "Backlog"]}}) \
            == ["Platform"]
        assert project_names(client, ALICE, {"lead": {"is": "me"}}) == ["Platform"]
        assert project_names(client, ADMIN, {"lead": {"is": None}}) == ["Archive"]
        assert project_names(client, BOB, {"members": {"isMe": True}}) == ["Platform"]
        assert project_names(client, ADMIN, {"members": {"is": None}}) == ["Archive", "Website"]
        assert project_names(client, BOB, {"creator": {"isMe": True}}) == ["Website"]
        assert project_names(client, ADMIN, {"health": {"is": "atRisk"}}) == ["Website"]
        assert project_names(client, ADMIN, {"health": {"isEitherOf": ["onTrack", "none"]}}) == \
            ["Archive", "Platform"]
        assert project_names(client, ADMIN, {"targetDate": {"is": "overdue"}}) == ["Website"]
        assert project_names(client, ADMIN, {"startDate": {"before": "-P1W"}}) == ["Platform"]
        assert project_names(client, ADMIN, {"targetDate": {"after": "P1W"}}) == ["Platform"]
        assert project_names(client, ADMIN, {"initiative": {"is": "Reliability"}}) == ["Platform"]
        assert project_names(client, ADMIN, {"initiative": {"is": None}}) == ["Archive", "Website"]
        assert project_names(client, ADMIN, {"initiative": {"isNot": world["Reliability"]}}) == \
            ["Archive", "Website"]
        assert project_names(client, ADMIN, {"summary": {"contains": "api"}}) == ["Platform"]
        assert project_names(client, ADMIN, {"name": {"containsIgnoreCase": "web"}}) == ["Website"]
        assert project_names(client, ADMIN, {"id": {"isEitherOf": [
            world["Archive"], world["Website"]]}}) == ["Archive", "Website"]
        bad = err(client, ADMIN, PREVIEW, {"k": "project", "f": {"priority": {"is": 1}}})
        assert bad["extensions"]["field"] == "priority"


# ------------------------------------------------------------- (b) preferences

def test_user_layer_overrides_the_default_for_that_user_only(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Shared", visibility="workspace",
                         display={"ordering": "title", "grouping": "status"})
        state = ok(client, ALICE, PREFS, {"v": view["id"]})["viewPreferences"]
        assert state == {"viewId": view["id"], "viewType": None,
                         "default": {"grouping": "status", "ordering": "title"}, "user": None,
                         "effective": {"grouping": "status", "ordering": "title"},
                         "hasUserOverrides": False}
        mine = ok(client, ALICE, PREFS_SET, {"v": view["id"], "values": {
            "ordering": "priority", "nestedSubIssues": "matching", "layout": "board",
            "orderCompletedByRecency": True, "hiddenColumns": ["Done"],
            "collapsedGroups": ["Backlog"], "visibleProperties": ["id", "milestone",
                                                                  "timeInStatus"]}})
        mine = mine["viewPreferencesSet"]
        assert mine["default"] == {"grouping": "status", "ordering": "title"}
        assert mine["effective"]["ordering"] == "priority"
        assert mine["effective"]["grouping"] == "status"
        assert mine["hasUserOverrides"] is True
        # The view runs in the caller's effective order; Bob still gets the default.
        alice_rows = ok(client, ALICE, VIEW, {"id": view["id"]})["view"]["issues"]["nodes"]
        bob_rows = ok(client, BOB, VIEW, {"id": view["id"]})["view"]["issues"]["nodes"]
        assert alice_rows[0]["title"] == "Parent"  # priority 1 first
        assert [n["title"] for n in bob_rows] == sorted((n["title"] for n in bob_rows),
                                                         key=str.lower)
        # null clears one key; Reset drops the whole layer.
        cleared = ok(client, ALICE, PREFS_SET, {"v": view["id"], "values": {"layout": None}})
        assert "layout" not in cleared["viewPreferencesSet"]["user"]
        reset = ok(client, ALICE, PREFS_RESET, {"v": view["id"]})["viewPreferencesReset"]
        assert reset["user"] is None and reset["effective"] == reset["default"]
        bad = err(client, ALICE, PREFS_SET, {"v": view["id"], "values": {
            "nestedSubIssues": "some"}})
        assert bad["extensions"]["field"] == "values.nestedSubIssues"


def test_set_default_for_everyone_needs_edit_rights(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Team board", visibility="workspace",
                         display={"grouping": "status"})
        denied = err(client, ALICE, PREFS_SET, {"v": view["id"], "scope": "default",
                                                "values": {"grouping": "assignee"}})
        assert denied["extensions"]["code"] == "FORBIDDEN"
        ok(client, ADMIN, PREFS_SET, {"v": view["id"], "values": {"grouping": "priority"}})
        result = ok(client, ADMIN, PREFS_SET, {"v": view["id"], "scope": "default",
                                               "values": {"grouping": "assignee"}})
        result = result["viewPreferencesSet"]
        # The default moved, and the owner's own override of that key went with it.
        assert result["default"] == {"grouping": "assignee"}
        assert result["effective"]["grouping"] == "assignee"
        assert result["user"] is None
        assert ok(client, BOB, VIEW, {"id": view["id"]})["view"]["display"] == \
            {"grouping": "assignee"}


def test_built_in_page_preferences(world):
    team_page = f"team:{world['OPS']}:active"
    with TestClient(world["app"]) as client:
        # Any member who can see the team sets its page default; it reaches everyone.
        ok(client, ALICE, PREFS_SET, {"t": team_page, "scope": "default",
                                      "values": {"grouping": "assignee"}})
        assert ok(client, BOB, PREFS, {"t": team_page})["viewPreferences"]["effective"] == \
            {"grouping": "assignee"}
        ok(client, BOB, PREFS_SET, {"t": team_page, "values": {"ordering": "created"}})
        state = ok(client, BOB, PREFS, {"t": team_page})["viewPreferences"]
        assert state["effective"] == {"grouping": "assignee", "ordering": "created"}
        assert ok(client, ALICE, PREFS, {"t": team_page})["viewPreferences"]["user"] is None
        # Workspace pages that are not a team's or a project's: the default is admin-only.
        denied = err(client, ALICE, PREFS_SET, {"t": "my-issues", "scope": "default",
                                                "values": {"layout": "board"}})
        assert denied["extensions"]["code"] == "FORBIDDEN"
        ok(client, ADMIN, PREFS_SET, {"t": "my-issues", "scope": "default",
                                      "values": {"layout": "board"}})
        # A project-list page validates as a project display.
        bad = err(client, ADMIN, PREFS_SET, {"t": "projects:all", "values": {
            "visibleProperties": ["assignee"]}})
        assert bad["extensions"]["field"].startswith("values.visibleProperties")
        bad = err(client, ADMIN, PREFS, {"t": f"team:{new_id()}:active"})
        assert bad["extensions"]["field"] == "viewType"
        bad = err(client, ADMIN, PREFS, {"t": "bad type!"})
        assert bad["extensions"]["field"] == "viewType"
        both = err(client, ADMIN, PREFS, {"t": "my-issues", "v": "x"})
        assert both["extensions"]["code"] == "BAD_USER_INPUT"
    with world["engine"].connect() as conn:
        rows = conn.execute(select(view_preference.c.user_id, view_preference.c.view_type)).all()
    assert sorted((r.user_id is None, r.view_type) for r in rows) == [
        (False, team_page), (True, "my-issues"), (True, team_page)]


# ------------------------------------------------------------ (c) subscriptions

def _inbox(world, user_key):
    with world["engine"].connect() as conn:
        return [(r.kind, r.issue_id) for r in conn.execute(
            select(inbox_notification.c.kind, inbox_notification.c.issue_id).where(and_(
                inbox_notification.c.recipient_id == world[user_key],
                inbox_notification.c.kind.like("view%"))).order_by(
                inbox_notification.c.created_at))]


def test_subscribers_hear_when_a_task_joins_or_finishes(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Urgent", visibility="workspace",
                         filters={"priority": {"is": 1}})
        sub = ok(client, ALICE, SUBSCRIBE, {"v": view["id"]})["viewSubscribe"]
        assert sub["subscription"] == {"onAdded": True, "onCompleted": True,
                                       "view": {"id": view["id"]}}
        assert ok(client, ALICE, VIEW, {"id": view["id"]})["view"]["subscriberCount"] == 1
        created = ok(client, ADMIN, ISSUE_CREATE, {"i": {
            "teamId": world["OPS"], "title": "Fire", "priority": 1}})["issueCreate"]["issue"]["id"]
        quiet = ok(client, ADMIN, ISSUE_CREATE, {"i": {
            "teamId": world["OPS"], "title": "Smoke", "priority": 3}})["issueCreate"]["issue"]["id"]
        assert _inbox(world, "alice") == [("viewIssueAdded", created)]
        # An edit that makes a task match is an addition; an edit inside the view is not.
        ok(client, ADMIN, ISSUE_UPDATE, {"id": quiet, "i": {"priority": 1}})
        ok(client, ADMIN, ISSUE_UPDATE, {"id": quiet, "i": {"title": "Smoke!"}})
        assert _inbox(world, "alice")[-1] == ("viewIssueAdded", quiet)
        assert len(_inbox(world, "alice")) == 2
        # Completing a matching task.
        ok(client, ADMIN, ISSUE_UPDATE, {"id": created, "i": {"stateId": world["ops_done"]}})
        assert _inbox(world, "alice")[-1] == ("viewIssueCompleted", created)
        # The actor never hears about their own change.
        ok(client, ALICE, ISSUE_CREATE, {"i": {"teamId": world["OPS"], "title": "Mine",
                                               "priority": 1}})
        assert len(_inbox(world, "alice")) == 3
        # Only completions now.
        ok(client, ALICE, SUBSCRIBE, {"v": view["id"], "a": False})
        ok(client, ADMIN, ISSUE_CREATE, {"i": {"teamId": world["OPS"], "title": "Quiet",
                                               "priority": 1}})
        assert len(_inbox(world, "alice")) == 3
        gone = ok(client, ALICE, UNSUBSCRIBE, {"v": view["id"]})["viewUnsubscribe"]
        assert gone["subscription"] is None
        ok(client, ADMIN, ISSUE_UPDATE, {"id": quiet, "i": {"stateId": world["ops_done"]}})
        assert len(_inbox(world, "alice")) == 3
        assert _inbox(world, "bob") == []


def test_subscription_matches_for_the_subscriber_and_respects_visibility(world):
    with TestClient(world["app"]) as client:
        # "me" in a followed view is the follower.
        view = make_view(client, ADMIN, name="Mine", visibility="workspace",
                         filters={"assignee": {"is": "me"}})
        ok(client, BOB, SUBSCRIBE, {"v": view["id"], "a": True, "c": False})
        task = ok(client, ADMIN, ISSUE_CREATE, {"i": {"teamId": world["WEB"], "title": "For bob",
                                                      "assigneeId": world["bob"]}})
        task = task["issueCreate"]["issue"]["id"]
        ok(client, ADMIN, ISSUE_CREATE, {"i": {"teamId": world["OPS"], "title": "For alice",
                                               "assigneeId": world["alice"]}})
        assert _inbox(world, "bob") == [("viewIssueAdded", task)]
        # A view the follower can no longer read notifies nobody.
        ok(client, ADMIN, UPDATE, {"id": view["id"], "i": {"visibility": "private"}})
        ok(client, ADMIN, ISSUE_CREATE, {"i": {"teamId": world["WEB"], "title": "Again",
                                               "assigneeId": world["bob"]}})
        assert len(_inbox(world, "bob")) == 1
        projects = make_view(client, ADMIN, name="Projects", kind="project",
                             visibility="workspace")
        bad = err(client, BOB, SUBSCRIBE, {"v": projects["id"]})
        assert bad["extensions"]["field"] == "viewId"
        private = make_view(client, ALICE, name="Secret")
        assert err(client, BOB, SUBSCRIBE, {"v": private["id"]})["extensions"]["code"] == \
            "NOT_FOUND"
    with world["engine"].connect() as conn:
        assert conn.execute(select(view_subscription.c.user_id)).scalars().all() == [world["bob"]]


# ------------------------------------------------------ (d) owner and visibility

def test_owner_handover_move_to_and_duplicate(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ALICE, name="Handover", visibility="workspace",
                         filters={"priority": {"is": 1}}, display={"grouping": "assignee"},
                         icon="Bug", color="#ff0000")
        denied = err(client, BOB, UPDATE, {"id": view["id"], "i": {"ownerId": world["bob"]}})
        assert denied["extensions"]["code"] == "FORBIDDEN"
        bad = err(client, ALICE, UPDATE, {"id": view["id"], "i": {"ownerId": new_id()}})
        assert bad["extensions"]["field"] == "ownerId"
        moved = ok(client, ALICE, UPDATE, {"id": view["id"], "i": {"ownerId": world["bob"]}})
        assert moved["viewUpdate"]["view"]["owner"]["id"] == world["bob"]
        # The previous owner is now just a reader.
        assert err(client, ALICE, UPDATE, {"id": view["id"], "i": {"name": "x"}})[
            "extensions"]["code"] == "FORBIDDEN"
        # Move to: a team, then personal.
        team_view = ok(client, BOB, UPDATE, {"id": view["id"], "i": {
            "scope": "team", "teamId": world["OPS"], "visibility": "team"}})
        assert team_view["viewUpdate"]["view"]["team"]["key"] == "OPS"
        personal = ok(client, BOB, UPDATE, {"id": view["id"], "i": {
            "scope": "workspace", "visibility": "private"}})
        assert personal["viewUpdate"]["view"]["visibility"] == "private"
        # An admin may hand any view over.
        ok(client, ADMIN, UPDATE, {"id": view["id"], "i": {"ownerId": world["alice"]}})
        ok(client, ALICE, UPDATE, {"id": view["id"], "i": {"visibility": "workspace"}})
        # Linear keeps the name and the visibility (linear-capture/s7-duplicate.jsonl).
        copy = ok(client, BOB, DUPLICATE, {"id": view["id"]})["viewDuplicate"]["view"]
        assert copy["name"] == "Handover"
        assert copy["visibility"] == "workspace" and copy["owner"]["id"] == world["bob"]
        assert copy["filters"] == {"priority": {"is": 1}}
        assert copy["display"] == {"grouping": "assignee"}
        shared = ok(client, BOB, DUPLICATE, {"id": view["id"], "n": "Bob's",
                                             "vis": "workspace"})["viewDuplicate"]["view"]
        assert shared["name"] == "Bob's" and shared["visibility"] == "workspace"
        secret = make_view(client, ALICE, name="Secret")
        assert err(client, BOB, DUPLICATE, {"id": secret["id"]})["extensions"]["code"] == \
            "NOT_FOUND"


# ------------------------------------------------------------- (e) CSV export

def _csv(client, token, view_id):
    return client.get(f"/api/views/{view_id}/export.csv",
                      headers={"Authorization": f"Bearer {token}"} if token else {})


def test_csv_export_runs_the_view_for_the_caller(world, monkeypatch):
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Everything", visibility="workspace",
                         display={"ordering": "title", "completedWindow": "all"})
        ok(client, ADMIN, ISSUE_UPDATE, {"id": world["Loner"], "i": {"title": "=HYPERLINK(1)"}})
        response = _csv(client, ALICE, view["slugId"])
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert f'filename="{view["slugId"]}.csv"' in response.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert list(rows[0]) == list(views_api.EXPORT_COLUMNS)
        assert [r["title"] for r in rows] == ["'=HYPERLINK(1)", "Child", "Parent", "Shipped"]
        parent = rows[2]
        assert parent["status"] == "In Progress" and parent["assignee"] == "alice"
        assert parent["labels"] == "Bug, Frontend" and parent["identifier"] == "OPS-1"
        assert parent["url"] == "https://hoja.test/parity/issue/OPS-1"
        # Members are capped lower than admins.
        monkeypatch.setattr(views_api, "MEMBER_EXPORT_LIMIT", 2)
        member = _csv(client, ALICE, view["id"])
        assert len(list(csv.DictReader(io.StringIO(member.text)))) == 2
        assert member.headers["x-total-count"] == "4"
        assert len(list(csv.DictReader(io.StringIO(_csv(client, ADMIN, view["id"]).text)))) == 4
        private = make_view(client, ADMIN, name="Mine only")
        assert _csv(client, ALICE, private["id"]).status_code == 404
        assert _csv(client, None, view["id"]).status_code == 401
        projects = make_view(client, ADMIN, name="P", kind="project", visibility="workspace")
        assert _csv(client, ADMIN, projects["id"]).status_code == 404


# ------------------------------------------------------- (f) Linear aliases

def test_linear_filter_translation_round_trip():
    linear = {"priority": {"lte": 2}, "assignee": {"isMe": {"eq": True}},
              "labels": {"some": {"name": {"in": ["Bug"]}}},
              "state": {"type": {"nin": ["completed", "canceled"]}},
              "dueDate": {"lt": "P1W"}, "estimate": {"null": True},
              "team": {"members": {"some": {"isMe": {"eq": True}}}},
              "or": [{"title": {"containsIgnoreCase": "fire"}}, {"parent": {"null": True}}]}
    hoja = view_linear.to_hoja("issue", linear)
    assert viewdocs.parse_filters("issue", hoja) == hoja
    assert hoja["priority"] == {"isLessThanOrEqual": 2}
    assert hoja["assignee"] == {"isMe": True}
    assert hoja["labels"] == {"includesAny": ["Bug"]}
    assert hoja["stateType"] == {"isNeitherOf": ["completed", "canceled"]}
    assert hoja["team"] == {"is": "myTeams"}
    assert hoja["or"][1] == {"parent": {"isNull": True}}
    back = view_linear.to_linear("issue", hoja)
    assert back["priority"] == {"lte": 2}
    assert back["assignee"] == {"isMe": {"eq": True}}
    assert back["labels"] == {"some": {"name": {"in": ["Bug"]}}}
    assert back["state"] == {"type": {"nin": ["completed", "canceled"]}}
    assert back["team"] == {"members": {"some": {"isMe": {"eq": True}}}}
    assert back["estimate"] == {"null": True}
    project = view_linear.to_hoja("project", {
        "status": {"name": {"eq": "Started"}}, "lead": {"null": True},
        "initiatives": {"some": {"id": {"eq": "abc"}}}, "health": {"in": ["onTrack"]}})
    parsed = viewdocs.parse_filters("project", project)
    assert parsed["health"] == {"isEitherOf": ["on track"]}  # Linear's enum, Hoja's value
    assert parsed["initiative"] == {"includesAny": ["abc"]}
    assert view_linear.to_linear("project", parsed)["health"] == {"in": ["onTrack"]}
    for bad, field in (({"title": {"startsWith": "x"}}, "title.startsWith"),
                       ({"labels": {"every": {"name": {"eq": "x"}}}}, "labels.every"),
                       ({"team": {"name": {"eq": "Ops"}}}, "team.name"),
                       ({"attachments": {"some": {}}}, "attachments")):
        with pytest.raises(viewdocs.ViewError) as caught:
            view_linear.to_hoja("issue", bad)
        assert caught.value.field == field


def test_custom_view_aliases_share_the_view_rows(world):
    create = f"""mutation($i: CustomViewCreateInput!) {{ customViewCreate(input: $i) {{
      success customView {{ {CV_FIELDS} }} }} }}"""
    with TestClient(world["app"]) as client:
        made = ok(client, ALICE, create, {"i": {
            "name": "Linear style", "shared": True, "teamId": world["OPS"],
            "filterData": {"assignee": {"isMe": {"eq": True}},
                           "labels": {"some": {"name": {"eq": "Bug"}}}}}})
        cv = made["customViewCreate"]["customView"]
        assert cv["modelName"] == "Issue" and cv["shared"] is True and cv["team"]["key"] == "OPS"
        assert cv["filterData"] == {"assignee": {"isMe": {"eq": True}},
                                    "labels": {"some": {"name": {"in": ["Bug"]}}}}
        assert cv["projectFilterData"] is None
        assert [n["title"] for n in cv["issues"]["nodes"]] == ["Parent"]
        assert cv["organizationViewPreferences"]["type"] == "organization"
        assert cv["userViewPreferences"] is None
        hoja = ok(client, ALICE, VIEW, {"id": cv["id"]})["view"]
        assert hoja["filters"] == {"assignee": {"isMe": True}, "labels": {"includesAny": ["Bug"]}}
        ok(client, ALICE, PREFS_SET, {"v": cv["id"], "values": {"layout": "board"}})
        read = ok(client, ALICE, f"query($id: String!) {{ customView(id: $id) {{ {CV_FIELDS} }} }}",
                  {"id": cv["slugId"]})["customView"]
        assert read["viewPreferencesValues"] == {"layout": "board"}
        assert read["userViewPreferences"] == {"type": "user", "preferences": {"layout": "board"}}
        # Linear's query-time filter is Linear-shaped too.
        narrowed = ok(client, ALICE, """query($id: String!) { customView(id: $id) {
          issues(filter: {priority: {gt: 1}}) { nodes { title } } } }""", {"id": cv["id"]})
        assert narrowed["customView"]["issues"]["nodes"] == []
        updated = ok(client, ALICE, """mutation($id: String!, $i: CustomViewUpdateInput!) {
          customViewUpdate(id: $id, input: $i) { customView { shared team { key }
          filterData } } }""", {"id": cv["id"], "i": {"teamId": None, "shared": False,
                                                       "filterData": {"priority": {"eq": 1}}}})
        assert updated["customViewUpdate"]["customView"] == {
            "shared": False, "team": None, "filterData": {"priority": {"eq": 1}}}
        project = ok(client, ADMIN, create, {"i": {
            "name": "Projects", "shared": True,
            "projectFilterData": {"health": {"eq": "atRisk"}}}})["customViewCreate"]["customView"]
        assert project["modelName"] == "Project"
        assert project["projectFilterData"] == {"health": {"eq": "atRisk"}}
        bad = err(client, ADMIN, create, {"i": {"name": "x", "filterData": {
            "title": {"notContains": "x"}}}})
        assert bad["extensions"]["field"] == "title.notContains"
        listed = ok(client, ALICE, """{ customViews(first: 1) { nodes { id } pageInfo {
          hasNextPage endCursor } } }""")["customViews"]
        assert len(listed["nodes"]) == 1 and listed["pageInfo"]["hasNextPage"]
        rest = ok(client, ALICE, """query($a: String) { customViews(first: 10, after: $a) {
          nodes { id } } }""", {"a": listed["pageInfo"]["endCursor"]})["customViews"]["nodes"]
        assert {n["id"] for n in rest} | {listed["nodes"][0]["id"]} == {cv["id"], project["id"]}
        has = """query($id: String!) { customViewHasSubscribers(id: $id) { hasSubscribers } }"""
        assert ok(client, ALICE, has, {"id": cv["id"]})["customViewHasSubscribers"] == \
            {"hasSubscribers": False}
        ok(client, ALICE, SUBSCRIBE, {"v": cv["id"]})
        assert ok(client, ALICE, has, {"id": cv["id"]})["customViewHasSubscribers"] == \
            {"hasSubscribers": True}
        ok(client, ALICE, "mutation($id: String!) { customViewDelete(id: $id) { success } }",
           {"id": cv["id"]})
        assert ok(client, ALICE, VIEW, {"id": cv["id"]})["view"]["id"] == cv["id"]  # archived


# ------------------------------------------------------------------ (g) URLs

def test_view_url_is_stable_and_slug_based(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Linkable", visibility="workspace")
        assert view["url"].endswith(f"/parity/view/{view['slugId']}")
        by_slug = ok(client, ALICE, VIEW, {"id": view["slugId"]})["view"]
        assert by_slug["id"] == view["id"] and by_slug["url"] == view["url"]
        ok(client, ADMIN, UPDATE, {"id": view["id"], "i": {"name": "Renamed"}})
        assert ok(client, ALICE, VIEW, {"id": view["id"]})["view"]["url"] == view["url"]

# ------------------------------------------------------------ review regressions
# Each test below failed before the review fix it names.

def test_health_null(world):
    with TestClient(world["app"]) as client:
        assert project_names(client, ADMIN, {"health": {"is": None}}) == ["Archive"]
        assert project_names(client, ADMIN, {"health": {"isNot": None}}) == ["Platform", "Website"]


def test_handover_requires_new_owner_to_see_team(world):
    with world["engine"].begin() as conn:
        conn.execute(update(team).where(team.c.id == world["OPS"]).values(visibility="private"))
    with TestClient(world["app"]) as client:
        view = make_view(client, ALICE, name="Ops", scope="team", teamId=world["OPS"],
                         visibility="team")
        bad = err(client, ALICE, UPDATE, {"id": view["id"], "i": {"ownerId": world["bob"]}})
        assert bad["extensions"]["field"] == "ownerId"


def test_custom_view_create_bad_owner_leaves_nothing(world):
    q = """mutation($i: CustomViewCreateInput!) { customViewCreate(input: $i) { success } }"""
    with TestClient(world["app"]) as client:
        err(client, ALICE, q, {"i": {"name": "Orphan", "ownerId": "nobody"}})
    with world["engine"].connect() as conn:
        assert conn.execute(select(saved_view.c.id).where(saved_view.c.name == "Orphan")).all() == []


def test_private_view_update_is_not_found(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ALICE, name="Secret")
        bad = err(client, BOB, UPDATE, {"id": view["id"], "i": {"name": "x"}})
        assert bad["extensions"]["code"] == "NOT_FOUND"


ROUND_TRIP = [
    {"assignee": {"isEitherOf": ["me", None]}},
    {"assignee": {"isNot": "me"}},
    {"assignee": {"isEitherOf": ["bob@parity.test", "alice"]}},
    {"labels": {"includesAll": ["Bug", "Frontend"]}},
    {"labels": {"includesAny": ["Frontend", None]}},
    {"labels": {"includesNone": ["Bug"]}},
    {"labels": {"is": None}},
    {"subscriber": {"isMe": True}},
    {"subscriber": {"includesNone": ["me"]}},
    {"estimate": {"is": None}},
    {"dueDate": {"is": None}},
    {"parent": {"isNull": True}, "priority": {"isLessThanOrEqual": 2}},
    {"or": [{"stateType": {"is": "completed"}}, {"assignee": {"isEitherOf": ["me", None]}}]},
]


def test_linear_round_trip_selects_the_same_rows(world):
    with TestClient(world["app"]) as client:
        for flt in ROUND_TRIP:
            linear = view_linear.to_linear("issue", flt)
            back = view_linear.to_hoja("issue", linear)
            assert issue_titles(client, ALICE, back) == issue_titles(client, ALICE, flt), \
                (flt, linear, back)


def test_csv_export_unicode_view_name(world):
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Привет", visibility="workspace")
        response = client.get(f"/api/views/{view['id']}/export.csv",
                              headers={"Authorization": f"Bearer {ADMIN}"})
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert "filename*=UTF-8''%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82-" in disposition
        assert disposition.isascii()




def test_view_matching_runs_once_per_view_and_respects_private_teams(world, monkeypatch):
    from hoja_server import store as store_module

    with world["engine"].begin() as conn:
        conn.execute(update(team).where(team.c.id == world["OPS"]).values(visibility="private"))
    calls = []
    real = store_module._apply_issue_filter

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module, "_apply_issue_filter", counting)
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Urgent", visibility="workspace",
                         filters={"priority": {"is": 1}})
        for token in (ALICE, BOB):
            ok(client, token, SUBSCRIBE, {"v": view["id"]})
        calls.clear()
        created = ok(client, ADMIN, ISSUE_CREATE, {"i": {
            "teamId": world["OPS"], "title": "Fire", "priority": 1}})["issueCreate"]["issue"]["id"]
        # One impersonal view, two followers: the filter ran once.
        assert len(calls) == 1
        calls.clear()
        ok(client, ADMIN, ISSUE_UPDATE, {"id": created, "i": {"title": "Fire!"}})
        # Before the write it ran once; after it, Alice already had the task and Bob cannot
        # see OPS, so nothing needed matching again.
        assert len(calls) == 1
    with world["engine"].connect() as conn:
        rows = conn.execute(select(inbox_notification.c.recipient_id).where(and_(
            inbox_notification.c.kind == "viewIssueAdded",
            inbox_notification.c.issue_id == created))).scalars().all()
    # Bob is not in the private OPS team: the task is not in his copy of the view.
    assert rows == [world["alice"]]


def test_view_list_fields_are_batched(world, monkeypatch):
    from hoja_server.store import Store

    with TestClient(world["app"]) as client:
        for n in range(3):
            v = make_view(client, ALICE, name=f"V{n}", visibility="workspace")
            ok(client, ALICE, SUBSCRIBE, {"v": v["id"]})
        calls = {"one": 0}
        for name in ("view_preference_layers", "view_subscription_for", "view_subscriber_count"):
            real = getattr(Store, name)

            def wrapper(self, *a, _real=real, **k):
                calls["one"] += 1
                return _real(self, *a, **k)
            monkeypatch.setattr(Store, name, wrapper)
        data = ok(client, ALICE, """{ views { id subscriberCount subscription { onAdded }
          preferences { effective } issues(first: 1) { nodes { id } } } }""")
        assert len(data["views"]) == 3
        assert all(v["subscriberCount"] == 1 and v["subscription"] for v in data["views"])
        assert calls["one"] == 0


# ------------------------------------------------------------- pinned view tabs

PIN = """mutation($id: String!, $p: Boolean!, $o: Float) { viewPin(id: $id, pinned: $p,
  sortOrder: $o) { success view { id pinned sortOrder } } }"""


def test_team_views_pin_as_ordered_tabs(world):
    with TestClient(world["app"]) as client:
        first = make_view(client, ALICE, name="Bugs", scope="team", teamId=world["OPS"],
                          visibility="team")
        second = make_view(client, ALICE, name="Urgent", scope="team", teamId=world["OPS"],
                           visibility="team")
        # A view starts unpinned; a new pin lands after the existing ones.
        listed = ok(client, ALICE, "{ views(scope: \"team\") { id pinned sortOrder } }")["views"]
        assert all(not v["pinned"] and v["sortOrder"] is None for v in listed)
        a = ok(client, ALICE, PIN, {"id": first["id"], "p": True})["viewPin"]["view"]
        b = ok(client, ALICE, PIN, {"id": second["slugId"], "p": True})["viewPin"]["view"]
        assert a["pinned"] and b["pinned"] and b["sortOrder"] > a["sortOrder"]
        # Reordering is an explicit sortOrder; unpinning keeps the order for a later re-pin.
        moved = ok(client, ALICE, PIN, {"id": second["id"], "p": True, "o": 0.5})
        assert moved["viewPin"]["view"]["sortOrder"] == 0.5
        assert not ok(client, ALICE, PIN, {"id": first["id"], "p": False})[
            "viewPin"]["view"]["pinned"]
        # Tabs belong to the page: any reader may pin, not only the owner. A view the
        # caller cannot read stays NOT_FOUND.
        ok(client, ADMIN, PIN, {"id": first["id"], "p": True})
        ok(client, BOB, PIN, {"id": first["id"], "p": True})
        mine = make_view(client, ALICE, name="Mine", scope="team", teamId=world["OPS"],
                         visibility="private")
        assert err(client, BOB, PIN, {"id": mine["id"], "p": True})[
            "extensions"]["code"] == "NOT_FOUND"
        # A workspace view has no page to pin onto.
        loose = make_view(client, ALICE, name="Loose", visibility="workspace")
        assert err(client, ALICE, PIN, {"id": loose["id"], "p": True})[
            "extensions"]["code"] == "BAD_USER_INPUT"
        # Moving a pinned view to another page drops the pin of the old page.
        ok(client, ALICE, UPDATE, {"id": second["id"], "i": {"scope": "workspace",
                                                             "visibility": "workspace"}})
        assert not ok(client, ALICE, "query($id: String!) { view(id: $id) { pinned } }",
                      {"id": second["id"]})["view"]["pinned"]


def test_duplicate_of_pinned_view_is_pinned_right_after_it(world):
    """Linear pins the copy as a tab next to the source (FacetCreate in
    linear-capture/s7-duplicate.jsonl); the source keeps its own tab."""
    with TestClient(world["app"]) as client:
        source = make_view(client, ALICE, name="Pinned", scope="team", teamId=world["OPS"],
                           visibility="team")
        ok(client, ALICE, PIN, {"id": source["id"], "p": True})
        later = make_view(client, ALICE, name="Later", scope="team", teamId=world["OPS"],
                          visibility="team")
        ok(client, ALICE, PIN, {"id": later["id"], "p": True})
        copy = ok(client, ALICE, """mutation($id: String!) { viewDuplicate(id: $id) {
            view { id pinned sortOrder scope visibility } } }""",
                  {"id": source["id"]})["viewDuplicate"]["view"]
        orders = {v: ok(client, ALICE, "query($id: String!) { view(id: $id) { sortOrder pinned } }",
                        {"id": v})["view"] for v in (source["id"], later["id"])}
        assert copy["scope"] == "team" and copy["pinned"] and copy["visibility"] == "team"
        assert orders[source["id"]]["sortOrder"] < copy["sortOrder"] < orders[later["id"]]["sortOrder"]
        assert orders[source["id"]]["pinned"]


def test_duplicate_carries_the_default_and_the_callers_own_override(world):
    """Linear writes both a user and an organization ViewPreferences for the copy
    (linear-capture/s7-duplicate.jsonl). Another member's override stays behind."""
    with TestClient(world["app"]) as client:
        view = make_view(client, ADMIN, name="Prefs", visibility="workspace",
                         display={"grouping": "status"})
        ok(client, ALICE, PREFS_SET, {"v": view["id"], "values": {"layout": "board"}})
        ok(client, BOB, PREFS_SET, {"v": view["id"], "values": {"ordering": "title"}})
        copy = ok(client, ALICE, DUPLICATE, {"id": view["id"]})["viewDuplicate"]["view"]
        mine = ok(client, ALICE, PREFS, {"v": copy["id"]})["viewPreferences"]
        assert mine["default"] == {"grouping": "status"}
        assert mine["user"] == {"layout": "board"}
        theirs = ok(client, BOB, PREFS, {"v": copy["id"]})["viewPreferences"]
        assert theirs["user"] is None
