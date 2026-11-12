"""Issue.githubLinks resolves the linked GitHub objects for one issue."""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import github_link, issue, make_engine, migrate, new_id, now_iso, seed_tenant

QUERY = """
query($id: String!) {
  issue(id: $id) {
    identifier
    githubLinks { kind externalId url state author sha environment }
  }
}
"""

# What the Reviews detail pane asks for. Kept in the test so a field that stops being
# resolvable fails here rather than in the pane.
PULL_DETAIL_QUERY = """
query($id: String!) {
  issue(id: $id) {
    githubLinks {
      id externalId state
      additions deletions baseRef headRef behind ahead
      mergeable mergeState reviewDecision reviewers
      checksState checksTotal checksPassing checksFailing
      body mergedAt githubUpdatedAt
    }
  }
}
"""

# What the Reviews queue asks for. It reads links, not tasks, so it must keep returning a
# linked pull request no matter how many other tasks were touched afterwards.
REVIEWS_QUERY = """
query { pullRequests(first: 50) { id kind externalId state issue { identifier } } }
"""


def _setup(tmp_path):
    db_path = tmp_path / "github-links-graphql.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Links GraphQL", org_url_key="links-graphql",
        token="owner-token", demo_issues=True,
    )
    with engine.connect() as conn:
        issue_id = conn.execute(
            select(issue.c.id).where(issue.c.identifier == "ENG-1")
        ).scalar_one()
    with engine.begin() as conn:
        conn.execute(github_link.insert().values(
            id=new_id(), organization_id=seeded["org_id"], issue_id=issue_id,
            kind="pr", external_id="4242",
            url="https://github.com/hoja-work/hoja/pull/67",
            title="detect linked issue identifiers", state="open",
            author="rinadelph", sha="343458f", environment=None,
            created_at=now_iso(), updated_at=now_iso(),
        ))
    app = create_app(str(db_path), open_mode=False)
    return engine, app, seeded


def test_issue_github_links_returns_the_linked_objects(tmp_path):
    _engine, app, seeded = _setup(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/graphql",
        headers={"Authorization": f"Bearer {seeded['token']}"},
        json={"query": QUERY, "variables": {"id": "ENG-1"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    issue_body = body["data"]["issue"]
    assert issue_body["identifier"] == "ENG-1"
    links = issue_body["githubLinks"]
    assert len(links) == 1
    assert links[0]["kind"] == "pr"
    assert links[0]["externalId"] == "4242"
    assert links[0]["author"] == "rinadelph"
    assert links[0]["sha"] == "343458f"


def test_issue_without_links_returns_an_empty_list(tmp_path):
    _engine, app, seeded = _setup(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/graphql",
        headers={"Authorization": f"Bearer {seeded['token']}"},
        json={"query": QUERY, "variables": {"id": "ENG-2"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    assert body["data"]["issue"]["githubLinks"] == []


def test_pull_request_detail_is_queryable_for_the_reviews_pane(tmp_path):
    """Revision 54 columns: enriched values come back, and an un-enriched link stays NULL."""
    engine, app, seeded = _setup(tmp_path)
    with engine.begin() as conn:
        conn.execute(github_link.update().where(
            github_link.c.external_id == "4242").values(
                additions=126, deletions=18, base_ref="main",
                head_ref="feat/reviews", behind=3, ahead=1, mergeable=True,
                merge_state="clean", review_decision="APPROVED",
                reviewers='[{"login":"rinadelph","state":"APPROVED"}]',
                checks_state="passing", checks_total=4, checks_passing=4,
                checks_failing=0, body="Fixes HOJA-1.",
                merged_at=None, github_updated_at="2026-11-09T00:11:43Z",
            ))
    client = TestClient(app)

    response = client.post(
        "/graphql",
        headers={"Authorization": f"Bearer {seeded['token']}"},
        json={"query": PULL_DETAIL_QUERY, "variables": {"id": "ENG-1"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    link = body["data"]["issue"]["githubLinks"][0]
    assert (link["additions"], link["deletions"]) == (126, 18)
    assert (link["baseRef"], link["headRef"]) == ("main", "feat/reviews")
    assert (link["behind"], link["ahead"]) == (3, 1)
    assert link["mergeable"] is True
    assert link["mergeState"] == "clean"
    assert link["reviewDecision"] == "APPROVED"
    assert link["reviewers"] == [{"login": "rinadelph", "state": "APPROVED"}]
    assert (link["checksTotal"], link["checksPassing"], link["checksFailing"]) == (4, 4, 0)
    assert link["checksState"] == "passing"
    assert link["body"] == "Fixes HOJA-1."
    assert link["mergedAt"] is None
    assert link["githubUpdatedAt"] == "2026-11-09T00:11:43Z"


FILES_QUERY = """
query($id: String!) {
  issue(id: $id) {
    githubLinks {
      externalId
      files { path additions deletions status patch previousPath }
    }
  }
}
"""


def _post(app, token, query, variables):
    return TestClient(app).post(
        "/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables},
    )


def test_files_returns_the_stored_per_file_diff(tmp_path):
    """Revision 55: the pane's file list, including a rename and a patch GitHub omitted."""
    engine, app, seeded = _setup(tmp_path)
    stored = (
        '[{"path":"clinear/cli.py","additions":27,"deletions":1,"status":"modified",'
        '"patch":"@@ -1 +1 @@","previousPath":null},'
        '{"path":"docs/api.md","additions":3,"deletions":0,"status":"renamed",'
        '"patch":null,"previousPath":"docs/http.md"}]'
    )
    with engine.begin() as conn:
        conn.execute(github_link.update().where(
            github_link.c.external_id == "4242").values(files=stored))

    response = _post(app, seeded["token"], FILES_QUERY, {"id": "ENG-1"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    files = body["data"]["issue"]["githubLinks"][0]["files"]
    assert [f["path"] for f in files] == ["clinear/cli.py", "docs/api.md"]
    assert (files[0]["additions"], files[0]["deletions"]) == (27, 1)
    assert files[0]["status"] == "modified"
    assert files[0]["patch"] == "@@ -1 +1 @@"
    assert files[0]["previousPath"] is None
    # A binary or oversized file legitimately has no patch; it is still a file row.
    assert files[1]["status"] == "renamed"
    assert files[1]["patch"] is None
    assert files[1]["previousPath"] == "docs/http.md"


def test_files_is_an_empty_list_when_the_link_was_not_enriched(tmp_path):
    """The field is non-null: an un-enriched link must answer [] and never null."""
    _engine, app, seeded = _setup(tmp_path)

    response = _post(app, seeded["token"], FILES_QUERY, {"id": "ENG-1"})

    body = response.json()
    assert "errors" not in body, body
    assert body["data"]["issue"]["githubLinks"][0]["files"] == []


def test_files_survives_a_malformed_stored_payload(tmp_path):
    """Hand-edited JSON must not take the whole task query down with it."""
    engine, app, seeded = _setup(tmp_path)
    with engine.begin() as conn:
        conn.execute(github_link.update().where(
            github_link.c.external_id == "4242").values(files="{not json"))

    response = _post(app, seeded["token"], FILES_QUERY, {"id": "ENG-1"})

    body = response.json()
    assert "errors" not in body, body
    assert body["data"]["issue"]["githubLinks"][0]["files"] == []


# --- the Reviews queue ---------------------------------------------------------------------


def _bulk_issues(engine, seeded, count):
    """`count` tasks touched after the linked one, to push it out of any newest-N window."""
    team_id = engine.connect().execute(
        select(issue.c.team_id).where(issue.c.identifier == "ENG-1")
    ).scalar_one()
    stamp = now_iso()
    with engine.begin() as conn:
        conn.execute(issue.insert(), [
            {
                "id": new_id(), "organization_id": seeded["org_id"], "team_id": team_id,
                "number": 900_000 + i, "identifier": f"ENG-{900_000 + i}", "title": f"filler {i}",
                "priority": 0, "created_at": stamp, "updated_at": stamp,
            }
            for i in range(count)
        ])


def test_reviews_queue_returns_linked_pull_requests(tmp_path):
    _engine, app, seeded = _setup(tmp_path)

    response = _post(app, seeded["token"], REVIEWS_QUERY, None)

    body = response.json()
    assert "errors" not in body, body
    links = body["data"]["pullRequests"]
    assert len(links) == 1
    assert links[0]["kind"] == "pr"
    # The pane labels a row with the task it belongs to, so the link must carry it.
    assert links[0]["issue"]["identifier"] == "ENG-1"


def test_reviews_queue_survives_a_busy_workspace(tmp_path):
    """The regression this pins: the queue used to be issues(first:100, orderBy updatedAt).

    Once 100+ tasks were touched after the linked one, the pull request fell outside the
    window and the pane reported an empty queue while the link still existed.
    """
    engine, app, seeded = _setup(tmp_path)
    _bulk_issues(engine, seeded, 120)

    response = _post(app, seeded["token"], REVIEWS_QUERY, None)

    body = response.json()
    assert "errors" not in body, body
    links = body["data"]["pullRequests"]
    assert [link["externalId"] for link in links] == ["4242"]


def test_reviews_queue_excludes_commit_links(tmp_path):
    """Only pull requests belong in the review queue; a commit link is task detail."""
    engine, app, seeded = _setup(tmp_path)
    with engine.begin() as conn:
        issue_id = conn.execute(
            select(github_link.c.issue_id).where(github_link.c.external_id == "4242")
        ).scalar_one()
        conn.execute(github_link.insert().values(
            id=new_id(), organization_id=seeded["org_id"], issue_id=issue_id,
            kind="commit", external_id="hoja-work/hoja@abc1234",
            url="https://github.com/hoja-work/hoja/commit/abc1234",
            title="hoja-work/hoja@abc1234", state=None, author=None, sha="abc1234",
            environment=None, created_at=now_iso(), updated_at=now_iso(),
        ))

    response = _post(app, seeded["token"], REVIEWS_QUERY, None)

    body = response.json()
    assert "errors" not in body, body
    assert [link["kind"] for link in body["data"]["pullRequests"]] == ["pr"]
