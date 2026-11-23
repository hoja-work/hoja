"""workspaceUpdate validates like workspaceCreate and refuses with typed errors (HOJA-734)."""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant
from hoja_server.db import user as user_table

UPDATE = ("mutation($input: WorkspaceUpdateInput!)"
          "{workspaceUpdate(input:$input){success organization{name urlKey}}}")


@pytest.fixture()
def env(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    db = tmp_path / "workspace-update.db"
    engine = make_engine(db)
    migrate(engine)
    one = seed_tenant(engine, org_name="One", org_url_key=f"one-{suffix}",
                      user_name="Admin One", user_email=f"a1-{suffix}@example.test",
                      demo_issues=False)
    two = seed_tenant(engine, org_name="Two", org_url_key=f"two-{suffix}",
                      user_name="Admin Two", user_email=f"a2-{suffix}@example.test",
                      demo_issues=False)
    client = TestClient(create_app(str(db), open_mode=False))

    def gql(token, variables):
        return client.post("/graphql", headers={"Authorization": token},
                           json={"query": UPDATE, "variables": variables}).json()

    return {"engine": engine, "one": one, "two": two, "suffix": suffix, "gql": gql}


def _error(result):
    assert result["data"]["workspaceUpdate"] is None
    return result["errors"][0]


@pytest.mark.parametrize("key", [
    "-leading", "trailing-", "double--hyphen", "has space", "under_score", "x" * 49, "  ", "é-accent",
])
def test_malformed_url_key_is_bad_user_input(env, key) -> None:
    err = _error(env["gql"](env["one"]["token"], {"input": {"urlKey": key}}))
    assert err["extensions"]["code"] == "BAD_USER_INPUT"
    assert err["extensions"]["field"] == "urlKey"
    assert err["message"] == "Workspace key must contain letters, numbers, and hyphens"


def test_valid_key_is_lowercased_and_48_chars_is_allowed(env) -> None:
    key = ("Ok-" + env["suffix"] + "x" * 48)[:48]
    result = env["gql"](env["one"]["token"], {"input": {"urlKey": key}})
    assert "errors" not in result
    assert result["data"]["workspaceUpdate"]["organization"]["urlKey"] == key.lower()


def test_duplicate_key_is_refused_before_the_database(env) -> None:
    taken = f"two-{env['suffix']}"
    err = _error(env["gql"](env["one"]["token"], {"input": {"urlKey": taken.upper()}}))
    assert err["extensions"] == {"code": "BAD_USER_INPUT", "field": "urlKey"}
    assert err["message"] == "Workspace key already exists"


def test_keeping_its_own_key_is_not_a_duplicate(env) -> None:
    own = f"one-{env['suffix']}"
    result = env["gql"](env["one"]["token"], {"input": {"name": "Renamed", "urlKey": own}})
    assert "errors" not in result
    assert result["data"]["workspaceUpdate"]["organization"] == {"name": "Renamed", "urlKey": own}


def test_blank_name_is_bad_user_input(env) -> None:
    err = _error(env["gql"](env["one"]["token"], {"input": {"name": "   "}}))
    assert err["extensions"] == {"code": "BAD_USER_INPUT", "field": "name"}


def test_non_admin_is_forbidden_and_nothing_changes(env) -> None:
    with env["engine"].begin() as conn:
        conn.execute(user_table.update().where(user_table.c.id == env["one"]["user_id"])
                     .values(admin=False))
    err = _error(env["gql"](env["one"]["token"], {"input": {"name": "Nope"}}))
    assert err["extensions"]["code"] == "FORBIDDEN"
    with env["engine"].begin() as conn:
        conn.execute(user_table.update().where(user_table.c.id == env["one"]["user_id"])
                     .values(admin=True))
    ok = env["gql"](env["one"]["token"], {"input": {}})
    assert ok["data"]["workspaceUpdate"]["organization"]["name"] == "One"
