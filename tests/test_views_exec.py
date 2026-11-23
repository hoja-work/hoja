"""Views: execution — what a view's filter, scope and display select, in what order, paged.

``test_views.py`` pins the document contract; these tests pin the engine behind
``View.issues/projects/initiatives`` and ``viewPreview``: every predicate kind actually
narrows the rows, scope is ANDed in, every ordering is pushed down to SQL, ``reverse`` flips
the sort rather than the page, and a keyset cursor walks the whole result exactly once.
Timestamps are fixed so orderings are deterministic; only completion times are relative to
now, because the completed-issues window is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    initiative,
    initiative_project,
    issue,
    issue_label,
    issue_label_link,
    make_engine,
    migrate,
    new_id,
    now_iso,
    project,
    project_team,
    seed_states_for_team,
    seed_tenant,
    team,
    token_hash,
    workflow_state,
)
from hoja_server.db import (
    user as user_table,
)

ADMIN = "exec-admin-token"

PREVIEW = """query($k: String!, $f: JSON, $d: JSON, $s: String, $t: String, $p: String,
  $i: String, $first: Int, $after: String) {
  viewPreview(kind: $k, filters: $f, display: $d, scope: $s, teamId: $t, projectId: $p,
              initiativeId: $i, first: $first, after: $after) {
    kind scope
    issues { nodes { title } totalCount pageInfo { hasNextPage hasPreviousPage endCursor } }
    projects { nodes { name } totalCount pageInfo { hasNextPage endCursor } }
    initiatives { nodes { name } totalCount pageInfo { hasNextPage endCursor } } } }"""
CREATE = """mutation($i: ViewCreateInput!) { viewCreate(input: $i) { success view { id } } }"""
VIEW_ISSUES = """query($id: String!, $first: Int, $after: String, $f: JSON, $d: JSON,
  $sort: String, $reverse: Boolean) { view(id: $id) {
  issues(first: $first, after: $after, filter: $f, display: $d, sort: $sort, reverse: $reverse) {
    nodes { title } totalCount pageInfo { hasNextPage endCursor } } } }"""
VIEW_PROJECTS = """query($id: String!, $first: Int, $after: String) { view(id: $id) {
  projects(first: $first, after: $after) { nodes { name } totalCount
    pageInfo { hasNextPage endCursor } } } }"""
VIEW_INITIATIVES = """query($id: String!, $first: Int, $after: String) { view(id: $id) {
  initiatives(first: $first, after: $after) { nodes { name } totalCount
    pageInfo { hasNextPage endCursor } } } }"""

ALL = {"completedWindow": "all"}


def _gql(client, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {ADMIN}"},
                       json={"query": query, "variables": variables or {}}).json()


def ok(client, query, variables=None):
    body = _gql(client, query, variables)
    assert "errors" not in body, body
    return body["data"]


def err(client, query, variables=None):
    body = _gql(client, query, variables)
    assert body.get("errors"), body
    return body["errors"][0]


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "exec.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Exec", org_url_key="exec",
                         user_email="admin@exec.test", token=ADMIN, demo_issues=False)
    org, admin = seeded["org_id"], seeded["user_id"]
    ts = now_iso()
    now = datetime.now(timezone.utc)
    ids: dict = {"org": org, "admin": admin}
    with engine.begin() as conn:
        for name in ("alice", "bob"):
            uid = new_id()
            ids[name] = uid
            conn.execute(user_table.insert().values(
                id=uid, organization_id=org, name=name, display_name=name,
                email=f"{name}@exec.test", active=True, admin=False,
                created_at=ts, updated_at=ts))
            conn.execute(api_key.insert().values(
                id=new_id(), token_hash=token_hash(f"exec-{name}"), label=name,
                user_id=uid, organization_id=org, created_at=ts))
        for key, parent in (("OPS", None), ("SUB", "OPS"), ("WEB", None)):
            tid = new_id()
            ids[key] = tid
            conn.execute(team.insert().values(
                id=tid, organization_id=org, key=key, name=key.title(), visibility="public",
                timezone="UTC", issue_counter=0, parent_team_id=ids.get(parent),
                created_at=ts, updated_at=ts))
            seed_states_for_team(conn, org, tid)

        def state(team_key, name):
            return conn.execute(select(workflow_state.c.id).where(and_(
                workflow_state.c.team_id == ids[team_key],
                workflow_state.c.name == name))).scalar()

        ids["ops_todo"] = state("OPS", "Todo")
        for name, team_key, target in (("Platform", "OPS", None),
                                       ("Website", "WEB", "2026-12-24"),
                                       ("Archive", None, "2026-11-30")):
            pid = new_id()
            ids[name] = pid
            conn.execute(project.insert().values(
                id=pid, organization_id=org, name=name, slug_id=name.lower(),
                state="started" if name != "Archive" else "backlog", progress=0,
                target_date=target, created_at=f"2026-0{len(ids) % 9 + 1}-01T00:00:00+00:00",
                updated_at=ts))
            if team_key:
                conn.execute(project_team.insert().values(project_id=pid, team_id=ids[team_key]))
        for n, name in enumerate(("Reliability", "Growth", "Quality")):
            iid = new_id()
            ids[name] = iid
            conn.execute(initiative.insert().values(
                id=iid, organization_id=org, name=name, status="active", priority=0,
                created_at=f"2026-10-0{n + 1}T00:00:00+00:00", updated_at=ts))
        conn.execute(initiative_project.insert().values(
            organization_id=org, initiative_id=ids["Reliability"], project_id=ids["Platform"]))
        for name in ("Bug", "Frontend"):
            lid = new_id()
            ids[name] = lid
            conn.execute(issue_label.insert().values(
                id=lid, organization_id=org, name=name, created_at=ts, updated_at=ts))

        def at(day):
            return f"2026-{day}+00:00"

        rows = [
            # title, team, state, priority, assignee, creator, labels, due, estimate,
            # created, updated, project, parent, completed/canceled at
            ("Delta", "OPS", "Todo", 1, "alice", "admin", ["Bug"], "2026-12-01", 3,
             at("11-01T10:00:00"), at("11-20T10:00:00"), "Platform", None, None),
            ("alpha", "OPS", "In Progress", 0, None, "admin", ["Bug", "Frontend"], None, None,
             at("11-02T23:30:00"), at("11-21T10:00:00"), None, None, None),
            ("Charlie", "OPS", "Backlog", 4, "bob", "bob", ["Frontend"], "2026-11-25", 1,
             at("11-03T00:00:00"), at("11-19T10:00:00"), None, "Delta", None),
            ("bravo", "SUB", "Todo", 2, "alice", "admin", [], None, 2,
             at("11-04T08:00:00"), at("11-18T10:00:00"), None, None, None),
            ("Echo", "WEB", "Done", 3, None, "admin", [], None, None,
             at("10-01T08:00:00"), at("10-05T10:00:00"), None, None, now - timedelta(days=2)),
            ("foxtrot", "WEB", "Done", 0, None, "admin", [], None, None,
             at("09-01T08:00:00"), at("10-04T10:00:00"), None, None, now - timedelta(days=20)),
            ("Golf", "WEB", "Canceled", 0, None, None, [], None, None,
             at("08-01T08:00:00"), at("10-03T10:00:00"), None, None, now - timedelta(days=60)),
        ]
        for n, (title, team_key, state_name, priority, assignee, creator, labels, due,
                estimate, created, updated, project_name, parent, closed) in enumerate(rows):
            iid = new_id()
            ids[title] = iid
            closed_at = closed.isoformat() if closed else None
            conn.execute(issue.insert().values(
                id=iid, organization_id=org, team_id=ids[team_key], number=n + 1,
                identifier=f"{team_key}-{n + 1}", title=title, priority=priority,
                estimate=estimate, state_id=state(team_key, state_name),
                assignee_id=ids.get(assignee), creator_id=ids.get(creator),
                project_id=ids.get(project_name), parent_id=ids.get(parent), due_date=due,
                completed_at=closed_at if state_name == "Done" else None,
                canceled_at=closed_at if state_name == "Canceled" else None,
                created_at=created, updated_at=updated, decision_needed=False))
            for label in labels:
                conn.execute(issue_label_link.insert().values(issue_id=iid, label_id=ids[label]))
    return {"engine": engine, "app": create_app(str(db_path), open_mode=False), **ids}


def preview(client, kind="issue", filters=None, display=None, first=None, after=None, **scope):
    variables = {"k": kind, "f": filters, "d": ALL if display is None else display,
                 "first": first, "after": after, "s": scope.get("scope"),
                 "t": scope.get("teamId"), "p": scope.get("projectId"),
                 "i": scope.get("initiativeId")}
    return ok(client, PREVIEW, variables)["viewPreview"]


def titles(client, filters=None, display=None, **kw):
    return [n["title"] for n in preview(client, filters=filters, display=display,
                                        first=250, **kw)["issues"]["nodes"]]


def selected(client, filters, display=None, **kw):
    return set(titles(client, filters, display, **kw))


EVERY = {"Delta", "alpha", "Charlie", "bravo", "Echo", "foxtrot", "Golf"}


def test_label_predicates_filter_by_name_and_id(world):
    with TestClient(world["app"]) as client:
        assert selected(client, {"labels": {"includesAny": ["Bug"]}}) == {"Delta", "alpha"}
        assert selected(client, {"labels": {"is": "Bug"}}) == {"Delta", "alpha"}
        assert selected(client, {"labels": {"is": world["Frontend"]}}) == {"alpha", "Charlie"}
        assert selected(client, {"labels": {"isNot": "Bug"}}) == EVERY - {"Delta", "alpha"}
        assert selected(client, {"labels": {"includesAll": ["Bug", "Frontend"]}}) == {"alpha"}
        assert selected(client, {"labels": {"includesNone": ["Bug", "Frontend"]}}) == {
            "bravo", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"labels": {"is": None}}) == {"bravo", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"labels": {"isNot": None}}) == {"Delta", "alpha", "Charlie"}
        assert selected(client, {"labels": {"isEitherOf": [None, "Bug"]}}) == EVERY - {"Charlie"}
        # A name and an id in one list are a union, not a refusal.
        assert selected(client, {"labels": {"includesAny": ["Bug", world["Frontend"]]}}) == {
            "Delta", "alpha", "Charlie"}


def test_state_accepts_id_name_and_client_key_and_state_type(world):
    with TestClient(world["app"]) as client:
        assert selected(client, {"state": {"is": "Todo"}}) == {"Delta", "bravo"}
        assert selected(client, {"state": {"is": "Todo|unstarted"}}) == {"Delta", "bravo"}
        assert selected(client, {"state": {"is": world["ops_todo"]}}) == {"Delta"}
        assert selected(client, {"state": {"isNot": "Todo|unstarted"}}) == EVERY - {"Delta", "bravo"}
        assert selected(client, {"state": {"isEitherOf": ["Done|completed", world["ops_todo"]]}}) \
            == {"Delta", "Echo", "foxtrot"}
        assert selected(client, {"stateType": {"is": "completed"}}) == {"Echo", "foxtrot"}
        assert selected(client, {"stateType": {"isEitherOf": ["completed", "canceled"]}}) == {
            "Echo", "foxtrot", "Golf"}
        assert selected(client, {"stateType": {"isNot": "started"}}) == EVERY - {"alpha"}
        # state and stateType on one group are both applied.
        assert selected(client, {"state": {"is": "Todo"}, "stateType": {"is": "started"}}) == set()


def test_date_only_values_cover_the_whole_utc_day(world):
    with TestClient(world["app"]) as client:
        assert selected(client, {"createdAt": {"is": "2026-11-02"}}) == {"alpha"}
        assert selected(client, {"createdAt": {"before": "2026-11-03"}}) == {
            "Delta", "alpha", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"createdAt": {"after": "2026-11-02"}}) == {"Charlie", "bravo"}
        assert selected(client, {"createdAt": {"onOrBefore": "2026-11-02"}}) == {
            "Delta", "alpha", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"createdAt": {"onOrAfter": "2026-11-03"}}) == {"Charlie", "bravo"}
        assert selected(client, {"createdAt": {"onOrAfter": "2026-11-02",
                                               "before": "2026-11-04"}}) == {"alpha", "Charlie"}
        assert selected(client, {"updatedAt": {"is": "2026-11-20"}}) == {"Delta"}
        # A full timestamp in another notation compares as an instant.
        assert selected(client, {"createdAt": {"after": "2026-11-02T23:00:00Z"}}) == {
            "alpha", "Charlie", "bravo"}
        assert selected(client, {"dueDate": {"before": "2026-12-01"}}) == {"Charlie"}
        assert selected(client, {"dueDate": {"is": "2026-12-01T09:00:00Z"}}) == {"Delta"}
        assert selected(client, {"dueDate": {"is": None}}) == EVERY - {"Delta", "Charlie"}


def test_user_predicates_keep_unassigned_rows_on_negation(world):
    with TestClient(world["app"]) as client:
        assert selected(client, {"assignee": {"is": world["alice"]}}) == {"Delta", "bravo"}
        assert selected(client, {"assignee": {"is": "bob@exec.test"}}) == {"Charlie"}
        assert selected(client, {"assignee": {"is": "alice"}}) == {"Delta", "bravo"}
        assert selected(client, {"assignee": {"isNot": world["alice"]}}) == {
            "alpha", "Charlie", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"assignee": {"is": None}}) == {"alpha", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"assignee": {"isNot": None}}) == {"Delta", "Charlie", "bravo"}
        assert selected(client, {"assignee": {"isEitherOf": [None, world["bob"]]}}) == {
            "alpha", "Charlie", "Echo", "foxtrot", "Golf"}
        assert selected(client, {"assignee": {"isNeitherOf": [None, world["alice"]]}}) == {"Charlie"}
        assert selected(client, {"creator": {"isNot": world["admin"]}}) == {"Charlie", "Golf"}
        assert selected(client, {"project": {"isNot": "Platform"}}) == EVERY - {"Delta"}
        assert selected(client, {"project": {"is": None}}) == EVERY - {"Delta"}
        assert selected(client, {"parent": {"is": None}}) == EVERY - {"Charlie"}
        assert selected(client, {"parent": {"isNot": world["Delta"]}}) == EVERY - {"Charlie"}


def test_saved_scope_is_anded_into_results(world):
    with TestClient(world["app"]) as client:
        def saved(**document):
            view_id = ok(client, CREATE, {"i": {"name": "scoped", "kind": "issue",
                                               "display": ALL, **document}})["viewCreate"]["view"]["id"]
            nodes = ok(client, VIEW_ISSUES, {"id": view_id, "first": 250})["view"]["issues"]["nodes"]
            return {n["title"] for n in nodes}

        # A team covers its sub-teams.
        assert saved(scope="team", teamId=world["OPS"]) == {"Delta", "alpha", "Charlie", "bravo"}
        assert saved(scope="team", teamId=world["SUB"]) == {"bravo"}
        assert saved(scope="team", teamId=world["OPS"],
                     filters={"assignee": {"is": world["alice"]}}) == {"Delta", "bravo"}
        assert saved(scope="project", projectId=world["Platform"]) == {"Delta"}
        assert saved(scope="initiative", initiativeId=world["Reliability"]) == {"Delta"}
        assert saved(scope="initiative", initiativeId=world["Growth"]) == set()
        assert saved() == EVERY
        # viewPreview takes the same scope arguments.
        assert selected(client, None, scope="team", teamId=world["WEB"]) == {
            "Echo", "foxtrot", "Golf"}


@pytest.mark.parametrize(("ordering", "expected"), [
    ("priority", ["Delta", "bravo", "Echo", "Charlie", "alpha", "foxtrot", "Golf"]),
    ("title", ["alpha", "bravo", "Charlie", "Delta", "Echo", "foxtrot", "Golf"]),
    ("created", ["bravo", "Charlie", "alpha", "Delta", "Echo", "foxtrot", "Golf"]),
    ("updated", ["alpha", "Delta", "Charlie", "bravo", "Echo", "foxtrot", "Golf"]),
    # These rows are inserted without a rank (as an import would): unranked tasks sort after
    # ranked ones, newest first, the order the revision-96 backfill gives existing tasks.
    # Ranked manual order is pinned in test_views_depth_order.py.
    ("manual", ["bravo", "Charlie", "alpha", "Delta", "Echo", "foxtrot", "Golf"]),
    ("status", ["alpha", "Delta", "bravo", "Charlie", "Echo", "foxtrot", "Golf"]),
])
def test_every_ordering_is_pushed_down(world, ordering, expected):
    with TestClient(world["app"]) as client:
        assert titles(client, display={**ALL, "ordering": ordering}) == expected


def test_orderings_with_empty_values_put_them_last(world):
    with TestClient(world["app"]) as client:
        due = titles(client, display={**ALL, "ordering": "dueDate"})
        assert due[:2] == ["Charlie", "Delta"]
        assert set(due[2:]) == EVERY - {"Charlie", "Delta"}
        due = titles(client, display={**ALL, "ordering": "dueDate", "reverse": True})
        assert due[:2] == ["Delta", "Charlie"]
        estimate = titles(client, display={**ALL, "ordering": "estimate"})
        assert estimate[:3] == ["Delta", "bravo", "Charlie"]
        # No priority stays last when reversed: Low first, Urgent fourth.
        assert titles(client, display={**ALL, "ordering": "priority", "reverse": True})[:4] == [
            "Charlie", "Echo", "bravo", "Delta"]


def test_reverse_flips_the_sort_not_the_page(world):
    with TestClient(world["app"]) as client:
        page = preview(client, display={**ALL, "ordering": "created", "reverse": True}, first=2)
        assert [n["title"] for n in page["issues"]["nodes"]] == ["Golf", "foxtrot"]
        page = preview(client, display={**ALL, "ordering": "title", "reverse": True}, first=3)
        assert [n["title"] for n in page["issues"]["nodes"]] == ["Golf", "foxtrot", "Echo"]


@pytest.mark.parametrize("ordering", ["updated", "priority", "dueDate", "status", "title"])
def test_cursor_pagination_walks_every_row_once(world, ordering):
    with TestClient(world["app"]) as client:
        display = {**ALL, "ordering": ordering}
        whole = titles(client, display=display)
        walked, after, pages = [], None, 0
        while True:
            page = preview(client, display=display, first=2, after=after)["issues"]
            assert page["totalCount"] == 7
            assert page["pageInfo"]["hasPreviousPage"] is (after is not None)
            walked += [n["title"] for n in page["nodes"]]
            pages += 1
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        assert walked == whole
        assert pages == 4


def test_cursor_is_validated_and_page_size_capped(world):
    with TestClient(world["app"]) as client:
        error = err(client, PREVIEW, {"k": "issue", "d": ALL, "after": "not-a-cursor"})
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        assert error["extensions"]["field"] == "after"
        cursor = preview(client, first=1)["issues"]["pageInfo"]["endCursor"]
        error = err(client, PREVIEW, {"k": "issue", "d": {**ALL, "ordering": "title"},
                                      "after": cursor})
        assert error["extensions"]["field"] == "after"
        page = preview(client, first=100000)["issues"]
        assert len(page["nodes"]) == 7 and page["pageInfo"]["hasNextPage"] is False


def test_display_completed_window_and_sub_issues_apply(world):
    with TestClient(world["app"]) as client:
        assert selected(client, None, display={}) == EVERY - {"foxtrot", "Golf"}  # week
        assert selected(client, None, display={"completedWindow": "day"}) == EVERY - {
            "Echo", "foxtrot", "Golf"}
        assert selected(client, None, display={"completedWindow": "month"}) == EVERY - {"Golf"}
        assert selected(client, None, display={"completedWindow": "none"}) == EVERY - {
            "Echo", "foxtrot", "Golf"}
        assert selected(client, None, display={"completedWindow": "all"}) == EVERY
        assert selected(client, None, display={**ALL, "showSubIssues": False}) == EVERY - {"Charlie"}


def test_view_issues_accepts_filter_and_sort_overrides(world):
    with TestClient(world["app"]) as client:
        view_id = ok(client, CREATE, {"i": {
            "name": "Important", "kind": "issue", "display": {**ALL, "ordering": "title"},
            "filters": {"priority": {"isEitherOf": [1, 2, 4]}}}})["viewCreate"]["view"]["id"]

        def run(**args):
            data = ok(client, VIEW_ISSUES, {"id": view_id, **args})["view"]["issues"]
            return [n["title"] for n in data["nodes"]], data["totalCount"]

        assert run() == (["bravo", "Charlie", "Delta"], 3)
        assert run(f={"assignee": {"is": world["alice"]}}) == (["bravo", "Delta"], 2)
        assert run(sort="priority") == (["Delta", "bravo", "Charlie"], 3)
        assert run(sort="priority", reverse=True) == (["Charlie", "bravo", "Delta"], 3)
        assert run(d={"ordering": "created"}) == (["bravo", "Charlie", "Delta"], 3)
        assert run(d={"completedWindow": "none", "showSubIssues": False}) == (["bravo", "Delta"], 2)
        error = err(client, VIEW_ISSUES, {"id": view_id, "sort": "mood"})
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        error = err(client, VIEW_ISSUES, {"id": view_id, "f": {"labels": {"nope": "x"}}})
        assert error["extensions"]["field"] == "labels.nope"


def test_view_preview_validates_like_view_create(world):
    with TestClient(world["app"]) as client:
        result = preview(client, filters={"priority": {"is": 1}})
        assert result["kind"] == "issue" and result["scope"] == "workspace"
        assert [n["title"] for n in result["issues"]["nodes"]] == ["Delta"]
        assert result["projects"]["nodes"] == [] and result["initiatives"]["nodes"] == []
        error = err(client, PREVIEW, {"k": "issue", "f": {"state": {"before": "x"}}})
        assert error["extensions"] == {"code": "BAD_USER_INPUT", "field": "state.before"}
        error = err(client, PREVIEW, {"k": "issue", "s": "team"})
        assert error["extensions"]["field"] == "teamId"
        error = err(client, PREVIEW, {"k": "issue", "d": {"ordering": "mood"}})
        assert error["extensions"]["field"] == "display.ordering"


def test_project_views_scope_order_filter_and_page(world):
    with TestClient(world["app"]) as client:
        def names(**kw):
            return [n["name"] for n in preview(client, kind="project", first=250, **kw)["projects"]["nodes"]]

        assert names(display={"ordering": "title"}) == ["Archive", "Platform", "Website"]
        assert names(display={"ordering": "title", "reverse": True}) == [
            "Website", "Platform", "Archive"]
        assert names(display={"ordering": "dueDate"}) == ["Archive", "Website", "Platform"]
        assert names(scope="team", teamId=world["OPS"]) == ["Platform"]
        assert names(scope="initiative", initiativeId=world["Reliability"]) == ["Platform"]
        assert names(filters={"or": [{"name": {"is": "Archive"}}, {"team": {"is": "WEB"}}]},
                     display={"ordering": "title"}) == ["Archive", "Website"]
        assert names(filters={"state": {"isNot": "started"}}) == ["Archive"]

        view_id = ok(client, CREATE, {"i": {"name": "Projects", "kind": "project",
                                           "display": {"ordering": "title"}}})["viewCreate"]["view"]["id"]
        first = ok(client, VIEW_PROJECTS, {"id": view_id, "first": 2})["view"]["projects"]
        assert [n["name"] for n in first["nodes"]] == ["Archive", "Platform"]
        assert first["totalCount"] == 3 and first["pageInfo"]["hasNextPage"]
        rest = ok(client, VIEW_PROJECTS, {"id": view_id, "first": 2,
                                          "after": first["pageInfo"]["endCursor"]})["view"]["projects"]
        assert [n["name"] for n in rest["nodes"]] == ["Website"]
        assert rest["pageInfo"]["hasNextPage"] is False


def test_project_list_display_options_validate_and_order(world):
    """The project Display panel's groupings, orderings, windows and properties are accepted,
    and its orderings run in SQL: name, target date (none last) and start date."""
    with TestClient(world["app"]) as client:
        def names(**kw):
            return [n["name"] for n in preview(client, kind="project", first=250, **kw)["projects"]["nodes"]]

        assert names(display={"ordering": "name"}) == ["Archive", "Platform", "Website"]
        assert names(display={"ordering": "targetDate"}) == ["Archive", "Website", "Platform"]
        # No fixture project has a start date: every row is "empty", so all three still page.
        assert sorted(names(display={"ordering": "startDate"})) == ["Archive", "Platform", "Website"]
        display = {"grouping": "health", "subGrouping": "lead", "ordering": "startDate",
                   "completedWindow": "halfYear",
                   "visibleProperties": ["id", "summary", "members", "issues", "completedAt"]}
        created = ok(client, CREATE, {"i": {"name": "Project display", "kind": "project",
                                           "display": display}})["viewCreate"]["view"]
        assert created["id"]
        task = ok(client, CREATE, {"i": {"name": "By status type", "display": {
            "grouping": "statusType", "completedWindow": "quarter",
            "ordering": "targetDate"}}})["viewCreate"]["view"]
        assert task["id"]


def test_initiative_views_page_with_a_cursor(world):
    with TestClient(world["app"]) as client:
        view_id = ok(client, CREATE, {"i": {"name": "Initiatives", "kind": "initiative",
                                           "filters": {"status": {"is": "active"}}}})["viewCreate"]["view"]["id"]
        first = ok(client, VIEW_INITIATIVES, {"id": view_id, "first": 2})["view"]["initiatives"]
        assert [n["name"] for n in first["nodes"]] == ["Quality", "Growth"]
        assert first["totalCount"] == 3 and first["pageInfo"]["hasNextPage"]
        rest = ok(client, VIEW_INITIATIVES, {"id": view_id, "first": 2,
                                             "after": first["pageInfo"]["endCursor"]})["view"]["initiatives"]
        assert [n["name"] for n in rest["nodes"]] == ["Reliability"]
        assert rest["pageInfo"]["hasNextPage"] is False


INITIATIVES_BY = """query($id: String!, $first: Int, $after: String, $f: JSON, $d: JSON,
  $sort: String, $reverse: Boolean) { view(id: $id) {
  initiatives(first: $first, after: $after, filter: $f, display: $d, sort: $sort,
              reverse: $reverse) { nodes { name } totalCount pageInfo { hasNextPage endCursor } } } }"""


def _initiative_view(client, **document):
    return ok(client, CREATE, {"i": {"name": "Initiatives", "kind": "initiative",
                                     "filters": {"status": {"isEitherOf": ["active", "planned"]}},
                                     **document}})["viewCreate"]["view"]["id"]


def test_initiative_views_order_by_every_initiative_ordering_and_walk_cursors(world):
    with world["engine"].begin() as conn:
        for name, values in (("Reliability", {"priority": 3, "sort_order": 2.0,
                                              "target_date": "2027-01-01"}),
                             ("Growth", {"priority": 1, "sort_order": None,
                                         "target_date": None, "status": "planned"}),
                             ("Quality", {"priority": 0, "sort_order": 1.0,
                                          "target_date": "2026-12-01"})):
            conn.execute(initiative.update().where(initiative.c.id == world[name]).values(**values))
    with TestClient(world["app"]) as client:
        view_id = _initiative_view(client)

        def run(**kw):
            page = ok(client, INITIATIVES_BY, {"id": view_id, "first": 250, **kw})["view"]["initiatives"]
            return [n["name"] for n in page["nodes"]]

        # The saved view has no ordering: manual (dragged rank first, unranked last).
        assert run() == ["Quality", "Reliability", "Growth"]
        assert run(sort="name") == ["Growth", "Quality", "Reliability"]
        assert run(sort="name", reverse=True) == ["Reliability", "Quality", "Growth"]
        # No priority and no target date sort last in both directions.
        assert run(sort="priority") == ["Growth", "Reliability", "Quality"]
        assert run(sort="priority", reverse=True) == ["Reliability", "Growth", "Quality"]
        assert run(sort="targetDate") == ["Quality", "Reliability", "Growth"]
        assert run(sort="targetDate", reverse=True) == ["Reliability", "Quality", "Growth"]
        assert run(sort="created") == ["Quality", "Growth", "Reliability"]
        assert run(d={"ordering": "health"}) == ["Quality", "Growth", "Reliability"]
        # Filters: and/or groups are evaluated, not ignored.
        assert run(f={"status": {"is": "planned"}}) == ["Growth"]
        assert run(f={"or": [{"status": {"is": "planned"}}, {"owner": {"is": "none"}}]},
                   sort="name") == ["Growth", "Quality", "Reliability"]
        assert run(f={"and": [{"status": {"is": "active"}}]}, sort="name") == [
            "Quality", "Reliability"]
        error = err(client, INITIATIVES_BY, {"id": view_id, "sort": "mood"})
        assert error["extensions"]["code"] == "BAD_USER_INPUT"

        for ordering in ("manual", "name", "priority", "targetDate", "created", "health"):
            for backwards in (False, True):
                expected = run(sort=ordering, reverse=backwards)
                walked, after = [], None
                while True:
                    page = ok(client, INITIATIVES_BY, {"id": view_id, "first": 1, "after": after,
                                                       "sort": ordering, "reverse": backwards})
                    page = page["view"]["initiatives"]
                    walked += [n["name"] for n in page["nodes"]]
                    if not page["pageInfo"]["hasNextPage"]:
                        break
                    after = page["pageInfo"]["endCursor"]
                assert walked == expected, (ordering, backwards)


def test_initiative_views_serialize_only_the_returned_page(world, monkeypatch):
    from hoja_server.store import Store

    calls = []
    original = Store.ser_initiative

    def counting(conn, row):
        calls.append(row["id"])
        return original(conn, row)

    monkeypatch.setattr(Store, "ser_initiative", staticmethod(counting))
    with TestClient(world["app"]) as client:
        view_id = _initiative_view(client, filters={"owner": {"is": "none"}})
        page = ok(client, INITIATIVES_BY, {"id": view_id, "first": 1})["view"]["initiatives"]
        assert page["totalCount"] == 3 and page["pageInfo"]["hasNextPage"]
        # One row asked for, plus the look-ahead row that sets hasNextPage.
        assert len(calls) <= 2


def test_forged_cursors_are_refused_as_input_errors(world):
    import base64
    import json as _json

    def forge(signature, keys):
        raw = _json.dumps({"s": signature, "k": keys}).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    with TestClient(world["app"]) as client:
        view_id = ok(client, CREATE, {"i": {"name": "All", "kind": "issue",
                                           "display": ALL}})["viewCreate"]["view"]["id"]
        for after in (forge("issue:updated:0", [{"x": 1}, "id"]),
                      forge("issue:updated:0", [["nested"], "id"]),
                      forge("issue:updated:0", [True, "id"]),
                      forge("issue:updated:0", ["a"]),
                      "not base64 !!"):
            error = err(client, VIEW_ISSUES, {"id": view_id, "after": after})
            assert error["extensions"] == {"code": "BAD_USER_INPUT", "field": "after"}, after
        init_id = _initiative_view(client)
        for after in (forge("initiative:manual:0", [1, "x", 1, "y"]),
                      forge("initiative:manual:0", [0])):
            error = err(client, INITIATIVES_BY, {"id": init_id, "after": after})
            assert error["extensions"] == {"code": "BAD_USER_INPUT", "field": "after"}, after


def test_project_team_is_not_drops_projects_linked_to_that_team(world):
    with world["engine"].begin() as conn:
        # Website now belongs to WEB and OPS; Archive has no team.
        conn.execute(project_team.insert().values(project_id=world["Website"], team_id=world["OPS"]))
    with TestClient(world["app"]) as client:
        def names(filters):
            return sorted(n["name"] for n in preview(client, kind="project", filters=filters,
                                                     first=250)["projects"]["nodes"])

        assert names({"team": {"is": "OPS"}}) == ["Platform", "Website"]
        assert names({"team": {"isNot": "OPS"}}) == ["Archive"]
        assert names({"team": {"isNot": "WEB"}}) == ["Archive", "Platform"]
        assert names({"team": {"isNeitherOf": ["WEB", "OPS"]}}) == ["Archive"]
