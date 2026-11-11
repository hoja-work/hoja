from __future__ import annotations

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import issue, make_engine, migrate, seed_tenant
from hoja_server.store import Store


def test_states_by_ids_returns_same_states_and_never_another_org():
    engine = make_engine("sqlite:///:memory:")
    migrate(engine)
    a = seed_tenant(engine, org_name="Alpha", org_url_key="alpha", demo_issues=False)
    b = seed_tenant(engine, org_name="Beta", org_url_key="beta", demo_issues=False)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO workflow_state (id, organization_id, team_id, name, type, position) "
            "VALUES ('a-state', ?, ?, 'Started', 'started', 1), ('b-state', ?, ?, 'Started', 'started', 1)",
            (a["org_id"], a["team_id"], b["org_id"], b["team_id"]),
        )
    store = Store(engine)
    batch = store.states_by_ids(a["org_id"], ["a-state", "b-state", "missing", "a-state", ""])
    assert batch == {"a-state": store.state_by_id(a["org_id"], "a-state")}


def test_search_issues_limits_sql_results_and_reports_next_page(tmp_path):
    engine = make_engine(tmp_path / "search.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Search", org_url_key="search", token="search-token", demo_issues=False)
    from hoja_server.db import workflow_state
    state_id = engine.connect().execute(
        workflow_state.select().where(workflow_state.c.organization_id == seeded["org_id"])
    ).mappings().first()["id"]
    with engine.begin() as conn:
        for i in range(5):
            conn.execute(issue.insert().values(id=f"search-{i}", organization_id=seeded["org_id"],
                team_id=seeded["team_id"], number=i + 1, identifier=f"ENG-{i + 1}",
                title=f"shared needle {i}", state_id=state_id, created_at=f"2026-01-01T00:00:0{i}Z",
                updated_at=f"2026-01-01T00:00:0{i}Z"))
    app = create_app(db_path=str(tmp_path / "search.db"), open_mode=False)
    with TestClient(app) as client:
        query = "query($n:Int!){searchIssues(term:\"shared needle\",first:$n){nodes{id} pageInfo{hasNextPage}}}"
        response = client.post("/graphql", json={"query": query, "variables": {"n": 3}},
                               headers={"Authorization": "Bearer search-token"})
        first = response.json()["data"]["searchIssues"]
        assert response.status_code == 200 and len(first["nodes"]) == 3 and first["pageInfo"]["hasNextPage"]
        response = client.post("/graphql", json={"query": query, "variables": {"n": 10}},
                               headers={"Authorization": "Bearer search-token"})
        last = response.json()["data"]["searchIssues"]
        assert response.status_code == 200 and len(last["nodes"]) == 5 and not last["pageInfo"]["hasNextPage"]
