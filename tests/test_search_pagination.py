"""searchIssues must be pageable with the cursor it advertises.

The field reports ``pageInfo.hasNextPage``; the cloud agent prompt (agent_chat.py)
tells the reader to follow that flag with ``after: pageInfo.endCursor``. These
tests hold the field to that contract, and hold the bounded query to a total
ordering so a page boundary cannot drop rows that share a timestamp.
"""
from __future__ import annotations

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import issue, make_engine, migrate, seed_tenant, workflow_state

SEARCH = """
query($n:Int!, $after:String){
  searchIssues(term:"shared needle", first:$n, after:$after){
    nodes{ id identifier }
    pageInfo{ hasNextPage hasPreviousPage startCursor endCursor }
  }
}
"""


def _seed(tmp_path, timestamps, order=None):
    engine = make_engine(tmp_path / "search.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Search", org_url_key="search",
                         token="search-token", demo_issues=False)
    with engine.connect() as conn:
        state_id = conn.execute(workflow_state.select().where(
            workflow_state.c.organization_id == seeded["org_id"])).mappings().first()["id"]
    with engine.begin() as conn:
        # `order` lets a test make physical row order disagree with id order,
        # which is what turns a non-total ORDER BY into lost pages.
        for i in (order if order is not None else range(len(timestamps))):
            stamp = timestamps[i]
            conn.execute(issue.insert().values(
                id=f"needle-{i}", organization_id=seeded["org_id"], team_id=seeded["team_id"],
                number=i + 1, identifier=f"ENG-{i + 1}", title=f"shared needle {i}",
                state_id=state_id, created_at=stamp, updated_at=stamp))
    return seeded


def _page(client, first, after=None):
    response = client.post("/graphql", json={"query": SEARCH, "variables": {"n": first, "after": after}},
                           headers={"Authorization": "Bearer search-token"})
    body = response.json()
    assert "errors" not in body, body["errors"]
    return body["data"]["searchIssues"]


def _walk(tmp_path, timestamps, first, order=None):
    _seed(tmp_path, timestamps, order=order)
    app = create_app(db_path=str(tmp_path / "search.db"), open_mode=False)
    seen, cursor, pages, page_infos = [], None, 0, []
    with TestClient(app) as client:
        while True:
            page = _page(client, first, cursor)
            page_infos.append(page["pageInfo"])
            seen.extend(node["id"] for node in page["nodes"])
            pages += 1
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
            assert cursor, "hasNextPage must come with an endCursor to follow"
            assert pages < 20, "paging did not terminate"
    return seen, pages, page_infos


def test_next_page_flag_is_actionable_with_after(tmp_path):
    """hasNextPage must be followable: pass endCursor back as `after`."""
    stamps = [f"2026-01-01T00:00:0{i}Z" for i in range(5)]
    seen, pages, infos = _walk(tmp_path, stamps, first=2)
    assert pages == 3, f"5 rows at 2 per page is 3 pages, walked {pages}"
    assert sorted(seen) == [f"needle-{i}" for i in range(5)], sorted(seen)
    assert infos[0]["hasPreviousPage"] is False
    assert infos[-1]["hasPreviousPage"] is True


def test_rows_sharing_a_timestamp_survive_every_page_boundary(tmp_path):
    """A single timestamp for all rows: the cursor order must still be total.

    Rows are inserted 1, 0, 2 so that physical order disagrees with id order.
    A keyset cursor that compares on id while the ORDER BY ties on updated_at
    alone then walks past rows it never returned.
    """
    stamps = ["2026-01-01T00:00:00Z"] * 3
    seen, pages, _ = _walk(tmp_path, stamps, first=1, order=[1, 0, 2])
    assert sorted(seen) == ["needle-0", "needle-1", "needle-2"], f"lost or repeated rows: {seen}"
    assert pages == 3


def test_single_page_reports_no_next_page(tmp_path):
    stamps = ["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"]
    seen, pages, infos = _walk(tmp_path, stamps, first=10)
    assert pages == 1 and sorted(seen) == ["needle-0", "needle-1"]
    assert infos[0]["hasNextPage"] is False and infos[0]["hasPreviousPage"] is False
