"""Issue label edit through GraphQL."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import update
from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant, user as user_table
from cliniar_server.store import Store

CREATE = "mutation($i: IssueLabelCreateInput!) { issueLabelCreate(input: $i) { success issueLabel { id } } }"
UPDATE = ("mutation($id: String!, $i: IssueLabelUpdateInput!) { issueLabelUpdate(id: $id, input: $i)"
          " { success issueLabel { id name color description } } }")
LABELS = "{ issueLabelSettings { id name color description } }"


def _setup(tmp_path):
    db_path = tmp_path / "labels.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Labels Org", org_url_key="labels-org",
                        user_email="owner@labels.test", token="labels-token", demo_issues=False)
    return engine, create_app(str(db_path), open_mode=False), admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def test_admin_edits_name_color_description_and_values_persist(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        lid = _gql(client, admin["token"], CREATE, {"i": {"name": "bug", "color": "#ff0000"}})["data"]["issueLabelCreate"]["issueLabel"]["id"]
        res = _gql(client, admin["token"], UPDATE, {"id": lid, "i": {"name": "defect", "description": "Broken behavior"}})
        assert res["data"]["issueLabelUpdate"]["success"] is True, res
        label = res["data"]["issueLabelUpdate"]["issueLabel"]
        assert (label["name"], label["color"], label["description"]) == ("defect", "#ff0000", "Broken behavior")
        listed = _gql(client, admin["token"], LABELS)["data"]["issueLabelSettings"]
        assert [l["name"] for l in listed if l["id"] == lid] == ["defect"]


def test_rejects_blank_and_overlong_name(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        lid = _gql(client, admin["token"], CREATE, {"i": {"name": "ok"}})["data"]["issueLabelCreate"]["issueLabel"]["id"]
        blank = _gql(client, admin["token"], UPDATE, {"id": lid, "i": {"name": "   "}})
        assert blank.get("errors"), blank
        long = _gql(client, admin["token"], UPDATE, {"id": lid, "i": {"name": "x" * 65}})
        assert long.get("errors"), long


def test_non_admin_cannot_edit_labels(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        lid = _gql(client, admin["token"], CREATE, {"i": {"name": "keep"}})["data"]["issueLabelCreate"]["issueLabel"]["id"]
        with engine.begin() as conn:
            conn.execute(update(user_table).where(user_table.c.id == admin["user_id"]).values(admin=False))
        res = _gql(client, admin["token"], UPDATE, {"id": lid, "i": {"name": "hacked"}})
        assert res.get("errors") and "Admin access required" in res["errors"][0]["message"], res
        assert [l["name"] for l in Store(engine).labels(admin["org_id"]) if l["id"] == lid] == ["keep"]
