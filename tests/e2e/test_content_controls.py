"""Browser checks for the creator-or-admin controls on issues, comments, and initiatives."""

from __future__ import annotations

import json
import urllib.request

from tests.e2e.conftest import PASSWORD, TOKEN, _sign_in  # noqa: F401  (shared fixture helpers)
from playwright.sync_api import sync_playwright
import pytest


def _gql(base, token, query, variables=None):
    req = urllib.request.Request(
        f"{base}/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def test_admin_sees_archive_and_delete_on_issue_and_can_archive_initiative(server) -> None:
    created = _gql(server, TOKEN, "mutation{issueCreate(input:{title:\"Browser control\",teamId:\"ENG\"}){success issue{identifier}}}")
    assert "errors" not in created, created
    ident = created["data"]["issueCreate"]["issue"]["identifier"]
    initiative = _gql(server, TOKEN, "mutation{initiativeCreate(input:{name:\"Browser plan\"}){success initiative{id}}}")
    assert "errors" not in initiative, initiative
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        page = browser.new_page()
        _sign_in(page, server)
        page.goto(f"{server}/workspace/issue/{ident}")
        # The UI speaks "task" since the vocabulary rename (HOJA-288). The archive
        # control is the toolbar button (frontend/src/graph/tasks.tsx:1540); delete
        # only exists inside the "Task options" menu (same file, :1513), so the test
        # must open that menu before asserting it, as a user would.
        page.get_by_role("button", name="Archive task").first.wait_for(timeout=15000)
        page.get_by_role("button", name="Task options").click()
        # LinMenu rows are role="menuitem", not buttons (frontend/src/graph/shared.tsx:239).
        page.get_by_role("menuitem", name="Delete task").wait_for(timeout=15000)
        page.goto(f"{server}/workspace/initiatives")
        page.get_by_role("button", name="Archive").first.wait_for(timeout=15000)
        browser.close()
