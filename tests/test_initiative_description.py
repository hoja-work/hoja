"""An initiative page has a Markdown description beside its one-line summary."""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import inspect
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

FIELDS = "id name summary description"


@pytest.fixture()
def ws(tmp_path):
    db_path = tmp_path / "initiative.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Plans", org_url_key="plans",
                         user_email="owner@example.test", token="owner-token", demo_issues=False)
    return {"engine": engine, "client": TestClient(create_app(str(db_path))), "token": seeded["token"]}


def gql(ws, query, variables=None):
    body = ws["client"].post("/graphql", json={"query": query, "variables": variables or {}},
                             headers={"Authorization": ws["token"]}).json()
    assert "errors" not in body, body
    return body["data"]


def test_description_round_trips_through_create_read_and_update(ws):
    created = gql(ws, f"mutation($i:InitiativeCreateInput!){{initiativeCreate(input:$i){{success initiative{{{FIELDS}}}}}}}",
                  {"i": {"name": "Grow", "summary": "One line", "description": "## Why\n\nBecause."}})
    row = created["initiativeCreate"]["initiative"]
    assert (row["summary"], row["description"]) == ("One line", "## Why\n\nBecause.")

    gql(ws, "mutation($id:String!,$i:InitiativeUpdateInput!){initiativeUpdate(id:$id,input:$i){success}}",
        {"id": row["id"], "i": {"description": "Rewritten"}})
    read = gql(ws, f"query($id:String!){{initiative(id:$id){{{FIELDS}}}}}", {"id": row["id"]})["initiative"]
    assert read["description"] == "Rewritten"
    assert read["summary"] == "One line", "editing the description leaves the summary alone"

    gql(ws, "mutation($id:String!,$i:InitiativeUpdateInput!){initiativeUpdate(id:$id,input:$i){success}}",
        {"id": row["id"], "i": {"description": None}})
    cleared = gql(ws, f"query($id:String!){{initiative(id:$id){{{FIELDS}}}}}", {"id": row["id"]})["initiative"]
    assert cleared["description"] is None


def test_migration_adds_the_column_to_an_existing_database(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE initiative DROP COLUMN description")
        conn.exec_driver_sql("DELETE FROM schema_revision WHERE revision >= 87")
    migrate(engine)
    assert "description" in {c["name"] for c in inspect(engine).get_columns("initiative")}
