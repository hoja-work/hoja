"""HOJA-757-A-2: a clean `hoja-cli[server]` install serves /graphql, and plugin session traces attach.

Before: every /graphql request imported `hoja_server.agent_chat` (the optional `agent` extra: openai, fastmcp,
pydantic-ai) and answered 500 without it; `/api/uploads` refused application/json, so the Hoja plugins' trace
upload (`POST /api/uploads` + `attachmentCreate`, their fallback where `/api/traces` does not exist) always failed.
"""
from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

TOKEN = "selfhosted-token"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    db_path = tmp_path / "selfhosted.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Self", org_url_key="self",
                         user_email="me@self.test", token=TOKEN, demo_issues=False)
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        yield client, seeded


def _gql(client, query, variables=None):
    res = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"query": query, "variables": variables or {}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert not body.get("errors"), body
    return body["data"]


def test_graphql_works_without_the_agent_extra(server, monkeypatch) -> None:
    client, _ = server
    # A None entry makes `import hoja_server.agent_chat` raise ImportError, exactly like a missing dependency.
    monkeypatch.setitem(sys.modules, "hoja_server.agent_chat", None)
    assert _gql(client, "{ viewer { email } }")["viewer"]["email"] == "me@self.test"


def test_a_plugin_trace_uploads_and_attaches(server) -> None:
    client, seeded = server
    issue = _gql(client, """mutation($t: String!) {
        issueCreate(input: { teamId: $t, title: "Traced work" }) { issue { id identifier } } }""",
                 {"t": seeded["team_id"]})["issueCreate"]["issue"]
    trace = {"schema": "hoja.trace/v1", "session": {"id": "s-1"}, "spans": []}
    up = client.post("/api/uploads", headers={"Authorization": f"Bearer {TOKEN}"},
                     files={"file": ("claude-code-s1-20261123.trace.json", json.dumps(trace), "application/json")})
    assert up.status_code == 200, up.text
    url = up.json()["url"]
    attached = _gql(client, """mutation($i: AttachmentCreateInput!) {
        attachmentCreate(input: $i) { success attachment { id url } } }""",
                    {"i": {"issueId": issue["identifier"], "url": url, "title": "claude-code-s1.trace.json",
                           "subtitle": "Claude Code trace"}})["attachmentCreate"]
    assert attached["success"] is True
    # The stored JSON is served back intact (not shadowed by its own metadata sidecar).
    got = client.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("application/json")
    assert got.json() == trace


def test_other_types_are_still_refused(server) -> None:
    client, _ = server
    res = client.post("/api/uploads", headers={"Authorization": f"Bearer {TOKEN}"},
                      files={"file": ("x.html", "<script>1</script>", "text/html")})
    assert res.status_code == 415
