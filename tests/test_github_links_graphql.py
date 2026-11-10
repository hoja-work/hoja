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
