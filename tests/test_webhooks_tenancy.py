"""The webhook worker serves every organization, but does each one's work bound to it."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from starlette.testclient import TestClient

from hoja_server import tenant_context, webhooks
from hoja_server.app import create_app
from hoja_server.db import seed_tenant
from tests import rls_mode

CREATE_HOOK = """
mutation($input: WebhookCreateInput!) { webhookCreate(input: $input) { success webhook { id secret } } }"""
CREATE_ISSUE = """
mutation($input: IssueCreateInput!) { issueCreate(input: $input) { issue { id } } }"""


def _gql(client, token, query, variables):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables}).json()
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def orgs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    monkeypatch.setenv(webhooks.ALLOW_PRIVATE_ENV, "1")
    engine, kwargs = rls_mode.workspace(tmp_path, "hooks")
    app_engine = engine
    if kwargs.get("database_url"):
        from hoja_server.db import make_engine
        app_engine = make_engine(kwargs["database_url"])
    client = TestClient(create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url")))
    out = {}
    for key in ("a", "b"):
        seeded = seed_tenant(engine, org_name=key.upper(), org_url_key=key, team_key="ENG",
                             user_email=f"o@{key}.test", token=f"{key}-tok", demo_issues=False)
        hook = _gql(client, seeded["token"], CREATE_HOOK, {"input": {
            "url": f"http://127.0.0.1:9/{key}", "teamId": "ENG", "resourceTypes": ["Issue"]}})
        _gql(client, seeded["token"], CREATE_ISSUE, {"input": {"teamId": "ENG", "title": f"issue {key}"}})
        out[key] = (seeded["org_id"], hook["webhookCreate"]["webhook"]["secret"])
    return app_engine, out


def test_dispatch_and_delivery_run_bound_to_one_organization(orgs, monkeypatch):
    engine, seeded = orgs
    seen = []
    dispatch, send = webhooks._dispatch_org, webhooks._send_one

    def spy_dispatch(engine_, org_id):
        seen.append(("dispatch", org_id, tenant_context.current()))
        return dispatch(engine_, org_id)

    def spy_send(engine_, row, post):
        seen.append(("send", row["organization_id"], tenant_context.current()))
        return send(engine_, row, post)

    monkeypatch.setattr(webhooks, "_dispatch_org", spy_dispatch)
    monkeypatch.setattr(webhooks, "_send_one", spy_send)
    calls = []
    assert webhooks.dispatch_once(engine) == 2
    assert webhooks.deliver_due(engine, post=lambda url, body, headers: calls.append((url, body, headers)) or 200) == 2

    assert {(kind, org) for kind, org, _ in seen} == {
        (kind, seeded[k][0]) for kind in ("dispatch", "send") for k in ("a", "b")}
    assert all(ctx == (org, False) for _kind, org, ctx in seen)
    for url, body, headers in calls:
        key = url.rsplit("/", 1)[1]
        assert json.loads(body)["organizationId"] == seeded[key][0]
        expected = hmac.new(seeded[key][1].encode(), body, hashlib.sha256).hexdigest()
        assert headers["Linear-Signature"] == expected
