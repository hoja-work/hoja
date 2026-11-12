"""The review write path through the real HTTP schema.

These tests exist because the schema executes every operation through `graphql_sync` in a worker
thread: a resolver that returned a coroutine would come back as a null field with no error, and
the unit tests in `test_review_actions.py` would not notice. Here the wiring itself is exercised.

The no-account case is also the honest state of this deployment: nothing is connected, so the
merge must be refused by name and the linked row must stay exactly as it was.
"""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import github_link, issue, make_engine, migrate, new_id, now_iso, seed_tenant
from hoja_server.store import Store

MERGE = """
mutation($id: String!, $sha: String!) {
  reviewMerge(linkId: $id, sha: $sha) { action sha mergeSha actor }
}
"""

GATE = """
query($id: String!) {
  reviewGate(linkId: $id) { sha mergeable checksState refusalCode refusalMessage }
}
"""


def _setup(tmp_path):
    db_path = tmp_path / "review-write.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Review Write", org_url_key="review-write",
        token="owner-token", demo_issues=True,
    )
    with engine.connect() as conn:
        issue_id = conn.execute(
            select(issue.c.id).where(issue.c.identifier == "ENG-1")
        ).scalar_one()
    link_id = new_id()
    with engine.begin() as conn:
        conn.execute(github_link.insert().values(
            id=link_id, organization_id=seeded["org_id"], issue_id=issue_id,
            kind="pr", external_id="12",
            url="https://github.com/hoja-work/hoja-cloud/pull/12",
            title="wire review writes", state="open", author="rinadelph",
            sha="abc123", environment=None,
            created_at=now_iso(), updated_at=now_iso(),
        ))
    return engine, create_app(str(db_path), open_mode=False), seeded, link_id


def _post(client, token, query, variables):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables})


def test_merge_without_a_connected_account_is_refused_by_name(tmp_path):
    engine, app, seeded, link_id = _setup(tmp_path)
    body = _post(TestClient(app), seeded["token"], MERGE,
                 {"id": link_id, "sha": "abc123"}).json()
    assert body.get("errors"), "an unconnected merge must be an error, not a success payload"
    assert body["errors"][0]["extensions"]["code"] == "not_connected"
    # The mutation returns a non-null payload, so a refusal nulls `data` entirely: there is no
    # field left for a UI to read as a success.
    assert body.get("data") is None


def test_a_refused_merge_leaves_the_row_open(tmp_path):
    """The optimistic-flip guard, asserted on the row rather than on the response."""
    engine, app, seeded, link_id = _setup(tmp_path)
    _post(TestClient(app), seeded["token"], MERGE, {"id": link_id, "sha": "abc123"})
    with engine.connect() as conn:
        state = conn.execute(
            select(github_link.c.state).where(github_link.c.id == link_id)
        ).scalar_one()
    assert state == "open", "a refused merge must not write 'merged' into the link row"


def test_gate_query_reports_the_same_refusal_code(tmp_path):
    engine, app, seeded, link_id = _setup(tmp_path)
    body = _post(TestClient(app), seeded["token"], GATE, {"id": link_id}).json()
    assert body.get("errors"), body
    assert body["errors"][0]["extensions"]["code"] == "not_connected"


def test_an_unknown_link_is_not_found_rather_than_a_crash(tmp_path):
    engine, app, seeded, _ignored = _setup(tmp_path)
    body = _post(TestClient(app), seeded["token"], MERGE,
                 {"id": "ghl_missing", "sha": "abc123"}).json()
    assert body.get("errors"), body
    assert body["errors"][0]["extensions"]["code"] == "NOT_FOUND"


def test_a_merge_keeps_the_head_sha_and_records_the_merge_sha_in_the_feed(tmp_path):
    """The row's `sha` is the head commit that was merged, never the merge commit.

    Overwriting it would make the pane report a commit the pull request never had, and the next
    gate read would compare against the wrong sha. The merge commit belongs in the activity event.
    """
    engine, _app, seeded, link_id = _setup(tmp_path)
    store = Store(engine)
    link = store.github_link_by_id(seeded["org_id"], link_id)
    assert link is not None
    updated = store.github_review_recorded(
        seeded["org_id"], link, event_type="githubReviewMerged", actor_id=seeded["user_id"],
        changes={"mergeSha": "def456"}, merged_sha="def456")
    assert updated is not None
    assert updated["state"] == "merged"
    assert updated["sha"] == "abc123", "the head sha must survive a merge"
