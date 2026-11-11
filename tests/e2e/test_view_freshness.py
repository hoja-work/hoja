"""Every realtime-relevant view must reflect a remote change without reload."""

from __future__ import annotations

import time

from tests.e2e.test_realtime_sync import _team_id, _wait_for_text


def _settle(page, base: str, path: str) -> None:
    page.goto(base + "/")
    page.wait_for_load_state("networkidle")
    page.goto(base + path)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => localStorage.getItem('hoja_change_cursor') !== null", timeout=10000)
    time.sleep(1.0)


def _create_issue(gql, title: str, team_id: str, assignee_id: str | None = None) -> str:
    payload = {"teamId": team_id, "title": title}
    if assignee_id:
        payload["assigneeId"] = assignee_id
    return gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id}}}",
               {"i": payload})["issueCreate"]["issue"]["id"]


def test_team_home_reflects_new_issue(browser_pair, gql):
    base, (_, viewer) = browser_pair
    _settle(viewer, base, "/workspace/team/eng/home")
    title = f"Home fresh {time.time_ns()}"
    _create_issue(gql, title, _team_id(gql))
    assert _wait_for_text(viewer, title, present=True), "team home did not refresh"


def test_team_views_reflects_new_issue(browser_pair, gql):
    base, (_, viewer) = browser_pair
    team_id = _team_id(gql)
    view_id = "e2e-all-tasks"
    viewer.goto(base + "/")
    viewer.wait_for_load_state("networkidle")
    # Views are browser-local saved views: frontend/src/main.tsx ViewsPage renders
    # results only when a saved view is active (`{active && ...results...}`), and the
    # page otherwise shows its empty state. Seed an "All tasks" view so this test
    # measures realtime freshness rather than the empty state.
    viewer.evaluate(
        "([key, id]) => localStorage.setItem(key, JSON.stringify(["
        "{id, name: 'All tasks', query: '', state: '', kind: 'issues'}]))",
        [f"hoja_saved_views_{team_id}", view_id],
    )
    _settle(viewer, base, f"/workspace/team/eng/views/issues/{view_id}")
    title = f"Views fresh {time.time_ns()}"
    _create_issue(gql, title, team_id)
    assert _wait_for_text(viewer, title, present=True), "team views did not refresh"


def test_inbox_reflects_notification_from_another_actor(browser_pair, gql, other_gql):
    base, (_, viewer) = browser_pair
    _settle(viewer, base, "/workspace/inbox")
    title = f"Inbox trigger {time.time_ns()}"
    viewer_id = gql("{viewer{id}}")["viewer"]["id"]
    _create_issue(other_gql, title, _team_id(gql), assignee_id=viewer_id)
    time.sleep(2)
    assert _wait_for_text(viewer, title, present=True, timeout=10), "inbox did not refresh"


def test_cycle_detail_reflects_issue_added_to_cycle(browser_pair, gql):
    base, (_, viewer) = browser_pair
    gql("mutation($t:String!,$w:Int!){teamCycleCadenceEnable(teamId:$t,weekday:$w){enabled}}",
        {"t": _team_id(gql), "w": 1})
    cycle_id = gql("{cycles(first:1){nodes{id name}}}")["cycles"]["nodes"][0]["id"]
    _settle(viewer, base, f"/workspace/cycle/{cycle_id}")
    title = f"Cycle detail fresh {time.time_ns()}"
    issue_id = _create_issue(gql, title, _team_id(gql))
    gql("mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}",
        {"id": issue_id, "i": {"cycleId": cycle_id}})
    assert _wait_for_text(viewer, title, present=True), "cycle detail did not refresh"


def test_project_views_reflect_new_issue_in_project(browser_pair, gql):
    base, (_, viewer) = browser_pair
    project_id = gql("mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
                     {"i": {"name": f"Fresh project {time.time_ns()}", "teamIds": [_team_id(gql)]}}
                     )["projectCreate"]["project"]["id"]
    _settle(viewer, base, f"/workspace/project/{project_id}/view/new")
    title = f"Project view fresh {time.time_ns()}"
    issue_id = _create_issue(gql, title, _team_id(gql))
    gql("mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}",
        {"id": issue_id, "i": {"projectId": project_id}})
    assert _wait_for_text(viewer, title, present=True), "project view did not refresh"


def test_members_page_reflects_new_member(browser_pair, gql, server):
    base, (_, viewer) = browser_pair
    _settle(viewer, base, "/workspace/members")
    name = f"Fresh Member {time.time_ns()}"
    gql("mutation($i:MemberInviteInput!){memberInvite(input:$i){success}}",
        {"i": {"name": name, "email": f"fresh{time.time_ns()}@example.test", "teamIds": [_team_id(gql)]}})
    assert _wait_for_text(viewer, name, present=True), "members page did not refresh"


def test_initiatives_page_reflects_new_initiative(browser_pair, gql):
    base, (_, viewer) = browser_pair
    _settle(viewer, base, "/workspace/initiatives")
    name = f"Fresh initiative {time.time_ns()}"
    gql("mutation($input:InitiativeCreateInput!){initiativeCreate(input:$input){success}}",
        {"input": {"name": name}})
    assert _wait_for_text(viewer, name, present=True), "initiatives page did not refresh"


def test_project_detail_overview_reflects_rename(browser_pair, gql):
    base, (_, viewer) = browser_pair
    project_id = gql("mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
                     {"i": {"name": f"Rename me {time.time_ns()}", "teamIds": [_team_id(gql)]}}
                     )["projectCreate"]["project"]["id"]
    _settle(viewer, base, f"/workspace/project/{project_id}")
    new_name = f"Renamed {time.time_ns()}"
    gql("mutation($id:String!,$i:ProjectUpdateInput!){projectUpdate(id:$id,input:$i){success}}",
        {"id": project_id, "i": {"name": new_name}})
    assert _wait_for_text(viewer, new_name, present=True), "project detail did not refresh"
