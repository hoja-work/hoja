"""Two real browsers on one server: a change in one must reach the other without reload."""

from __future__ import annotations

import time

import pytest

ISSUES_PATH = "/workspace/team/eng/active"


def _open_issues(page, base: str) -> None:
    page.goto(base + "/")
    page.wait_for_load_state("networkidle")
    page.goto(base + ISSUES_PATH)
    page.wait_for_load_state("networkidle")
    # Wait for the change feed to be connected before the test mutates data.
    page.wait_for_function("() => localStorage.getItem('hoja_change_cursor') !== null "
                           "|| performance.getEntriesByType('resource').some(e => e.name.includes('/ws'))",
                           timeout=10000)
    time.sleep(1.0)


def _wait_for_text(page, text: str, *, present: bool, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = page.get_by_text(text).count() > 0
        if found == present:
            return True
        time.sleep(0.2)
    return False


def _team_id(gql) -> str:
    return gql("{teams{nodes{id}}}")["teams"]["nodes"][0]["id"]


def test_new_issue_appears_in_other_browser_without_reload(browser_pair, gql):
    base, (writer, viewer) = browser_pair
    _open_issues(viewer, base)
    title = f"Realtime create {time.time_ns()}"

    gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
        {"i": {"teamId": _team_id(gql), "title": title}})

    assert _wait_for_text(viewer, title, present=True), "viewer did not receive the new issue"


def test_title_change_appears_in_other_browser(browser_pair, gql):
    base, (_, viewer) = browser_pair
    team_id = _team_id(gql)
    before = f"Rename before {time.time_ns()}"
    after = f"Rename after {time.time_ns()}"
    issue_id = gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id}}}",
                   {"i": {"teamId": team_id, "title": before}})["issueCreate"]["issue"]["id"]
    _open_issues(viewer, base)
    assert _wait_for_text(viewer, before, present=True)

    gql("mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}",
        {"id": issue_id, "i": {"title": after}})

    assert _wait_for_text(viewer, after, present=True), "viewer did not receive the rename"


def test_archived_issue_disappears_in_other_browser(browser_pair, gql):
    base, (_, viewer) = browser_pair
    title = f"Archive me {time.time_ns()}"
    issue_id = gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id}}}",
                   {"i": {"teamId": _team_id(gql), "title": title}})["issueCreate"]["issue"]["id"]
    _open_issues(viewer, base)
    assert _wait_for_text(viewer, title, present=True)

    gql("mutation($id:String!){issueArchive(id:$id){success}}", {"id": issue_id})

    assert _wait_for_text(viewer, title, present=False), "archived issue still shown"


def _socket_events(base: str, token: str | None, after: int, count: int) -> list[dict]:
    import asyncio
    import json

    from websockets.asyncio.client import connect

    url = base.replace("http://", "ws://") + f"/ws?after={after}"
    protocols = ["bearer", token] if token else None

    async def collect():
        async with connect(url, subprotocols=protocols) as ws:
            events = []
            while len(events) < count:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                # v1 frames are JSON arrays of ChangeEvent objects
                # (hoja_server/realtime.py "Wire protocol"); the first frame after
                # connect may be empty, so flatten the frames into events instead
                # of treating a frame as one event.
                events.extend(frame)
            return events[:count]

    return asyncio.run(collect())


def test_socket_rejects_bad_token(server):
    from websockets.exceptions import InvalidStatus

    with pytest.raises((InvalidStatus, Exception)) as rejected:
        _socket_events(server, "not-a-real-token", after=0, count=1)
    assert "401" in str(rejected.value) or "4401" in str(rejected.value) or "rejected" in str(rejected.value).lower()


def test_reconnect_resumes_from_cursor_without_gaps(server, gql):
    gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
        {"i": {"teamId": _team_id(gql), "title": f"Resume A {time.time_ns()}"}})
    first = _socket_events(server, "e2e-realtime-token", after=0, count=1)[0]
    gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
        {"i": {"teamId": _team_id(gql), "title": f"Resume B {time.time_ns()}"}})

    resumed = _socket_events(server, "e2e-realtime-token", after=first["seq"], count=3)
    seqs = [e["seq"] for e in resumed]

    assert all(s > first["seq"] for s in seqs)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def _create_project(gql, name: str) -> None:
    gql("mutation($i:ProjectCreateInput!){projectCreate(input:$i){success}}",
        {"i": {"name": name, "teamIds": [_team_id(gql)]}})


def test_new_project_appears_in_team_projects_without_reload(browser_pair, gql):
    base, (writer, viewer) = browser_pair
    viewer.goto(base + "/")
    viewer.wait_for_load_state("networkidle")
    viewer.goto(base + "/workspace/team/eng/projects")
    viewer.wait_for_load_state("networkidle")
    viewer.wait_for_function("() => localStorage.getItem('hoja_change_cursor') !== null", timeout=10000)
    time.sleep(1.0)
    name = f"Realtime project {time.time_ns()}"

    _create_project(gql, name)

    assert _wait_for_text(viewer, name, present=True), "team projects view did not refresh"
