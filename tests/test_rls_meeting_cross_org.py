"""A meeting link is shared across workspaces, so system mode is one request away.

On a meeting route the caller binds to its own organization, then, when the meeting
belongs to another workspace, the route escalates with ``enter_system()`` and never
resets. From that point isolation rests on MeetingService's own predicates rather
than on the policies. These tests pin that boundary from the outside: an API-key
caller in another workspace must get the meeting policy's view and nothing else,
even though the database is no longer confined to a tenant for the rest of the
request.
"""
from __future__ import annotations

import pytest
from sqlalchemy import insert
from starlette.testclient import TestClient

from hoja_server import db
from hoja_server.db import seed_tenant
from tests import rls_mode

pytestmark = pytest.mark.skipif(not rls_mode.ENABLED, reason="needs HOJA_TEST_RLS + Postgres")

# Rows that exist only in Alpha. None of them may appear in any response Bravo gets.
CANARY_ISSUE = "ALPHA-ONLY-CANARY-ISSUE"
CANARY_PROVIDER = "alpha-only-canary-provider"

# What an anonymous visitor with the link is allowed to see, and nothing more.
PREVIEW_FIELDS = {"code", "title", "status", "endedAt", "durationSeconds", "hostName",
                  "scheduledStart", "passcodeRequired", "waitingRoom"}


@pytest.fixture
def two_workspaces(tmp_path):
    from hoja_server.app import create_app

    owner, kwargs = rls_mode.workspace(tmp_path, "meetings-cross-org")
    alpha = seed_tenant(owner, org_name="Alpha", org_url_key="alpha", team_key="ALP",
                        user_email="host@alpha.test", token="meeting-alpha-key", demo_issues=False)
    bravo = seed_tenant(owner, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="caller@bravo.test", token="meeting-bravo-key", demo_issues=False)
    ts = db.now_iso()
    with owner.begin() as conn:
        conn.execute(insert(db.issue).values(
            id="canary-issue", organization_id=alpha["org_id"], team_id=alpha["team_id"],
            identifier=CANARY_ISSUE, title=CANARY_ISSUE, number=999, slot="A", depth=0,
            created_at=ts, updated_at=ts))
        conn.execute(insert(db.model_provider).values(
            id="canary-provider", organization_id=alpha["org_id"], name=CANARY_PROVIDER,
            provider="openai", model_id="gpt-4o-mini", encrypted_api_key="v2:canary",
            enabled=True, created_at=ts, updated_at=ts))

    app = create_app(**kwargs, open_mode=False)
    with TestClient(app) as client:
        created = client.post("/api/meetings", headers=_h(alpha["token"]),
                              json={"title": "Alpha standup", "teamId": alpha["team_id"],
                                    "linkEnabled": True})
        assert created.status_code == 201, created.text
        yield {"client": client, "code": created.json()["meeting"]["code"],
               "alpha": alpha, "bravo": bravo, "owner": owner}


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _bravo(world, method, path, **kwargs):
    return world["client"].request(method, path, headers=_h(world["bravo"]["token"]), **kwargs)


def _alpha(world, method, path, **kwargs):
    return world["client"].request(method, path, headers=_h(world["alpha"]["token"]), **kwargs)


def _assert_no_canary(resp, where: str) -> None:
    for canary in (CANARY_ISSUE, CANARY_PROVIDER):
        assert canary not in resp.text, f"{where} leaked {canary}: {resp.text[:400]}"


def test_another_workspace_key_cannot_read_a_foreign_meeting(two_workspaces):
    code = two_workspaces["code"]
    resp = _bravo(two_workspaces, "GET", f"/api/meetings/{code}")
    assert resp.status_code in (403, 404), (resp.status_code, resp.text)
    _assert_no_canary(resp, "meeting detail")


def test_another_workspace_key_cannot_join_a_foreign_meeting(two_workspaces):
    """The link admits anyone, so a foreign key joins as a guest and never as a member."""
    code = two_workspaces["code"]
    resp = _bravo(two_workspaces, "POST", f"/api/meetings/{code}/join", json={"name": "Bravo caller"})
    assert resp.status_code in (200, 403, 404), (resp.status_code, resp.text)
    if resp.status_code == 200:
        participant = resp.json()["participant"]
        assert participant["kind"] == "guest", participant
        assert participant["role"] != "host", participant
        assert resp.json()["reason"], "a foreign caller waits; it is not admitted as a member"
    _assert_no_canary(resp, "meeting join")


def test_another_workspace_key_cannot_read_host_only_surfaces(two_workspaces):
    code = two_workspaces["code"]
    for method, path, body in (
        ("GET", f"/api/meetings/{code}/invitees", None),
        ("POST", f"/api/meetings/{code}/usage", {}),
        ("GET", f"/api/meetings/{code}/ics", None),
        ("GET", "/api/meetings/list", None),
    ):
        resp = _bravo(two_workspaces, method, path, json=body)
        assert resp.status_code != 200 or b"Alpha standup" not in resp.content, (path, resp.text[:200])
        _assert_no_canary(resp, path)


def test_the_public_preview_is_the_policy_view_and_no_more(two_workspaces):
    code = two_workspaces["code"]
    resp = _bravo(two_workspaces, "GET", f"/api/meetings/{code}/preview")
    assert resp.status_code == 200, resp.text
    meeting = resp.json()["meeting"]
    assert set(meeting) <= PREVIEW_FIELDS, set(meeting) - PREVIEW_FIELDS
    assert meeting["title"] == "Alpha standup"
    _assert_no_canary(resp, "meeting preview")


def test_the_owning_workspace_still_reads_its_meeting(two_workspaces):
    """The control: the same route, the same meeting, the workspace that owns it."""
    code = two_workspaces["code"]
    resp = _alpha(two_workspaces, "GET", f"/api/meetings/{code}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["meeting"]["title"] == "Alpha standup"
    assert resp.json()["meeting"]["isHost"] is True


def test_a_foreign_key_gets_a_uniform_not_found_for_an_unknown_code(two_workspaces):
    """A wrong code must not become an oracle that distinguishes workspaces."""
    resp = _bravo(two_workspaces, "GET", "/api/meetings/no-such-code")
    assert resp.status_code in (403, 404)
    _assert_no_canary(resp, "unknown meeting")
