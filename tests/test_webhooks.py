"""Outgoing webhooks: Linear wire contract, lifecycle events, delivery guarantees."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import webhooks
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

CREATE_HOOK = """
mutation($input: WebhookCreateInput!) {
  webhookCreate(input: $input) { success webhook { id url enabled secret resourceTypes allPublicTeams team { key } } }
}"""
UPDATE_HOOK = """
mutation($id: String!, $input: WebhookUpdateInput!) { webhookUpdate(id: $id, input: $input) { success webhook { id enabled } } }"""
DELETE_HOOK = "mutation($id: String!) { webhookDelete(id: $id) { success } }"
LIST_HOOKS = "{ webhooks { nodes { id url failures { httpStatus responseOrError } } } }"
CREATE_ISSUE = """
mutation($input: IssueCreateInput!) { issueCreate(input: $input) { issue { id identifier } } }"""
UPDATE_ISSUE = """
mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }"""
STATES = "{ workflowStates { nodes { id name type team { key } } } }"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    monkeypatch.setenv(webhooks.ALLOW_PRIVATE_ENV, "1")
    path = tmp_path / "webhooks.db"
    engine = make_engine(path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Hooks", org_url_key="hooks", team_key="ENG",
                         user_name="Admin", user_email="admin@example.test", demo_issues=False)
    app = create_app(db_path=str(path))
    with TestClient(app) as client:
        yield client, seeded["token"], engine, path


def gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def state_id(client, token, stype, team="ENG"):
    nodes = gql(client, token, STATES)["data"]["workflowStates"]["nodes"]
    return next(n["id"] for n in nodes if n["type"] == stype and n["team"]["key"] == team)


def make_hook(client, token, **extra):
    inp = {"url": "http://127.0.0.1:9/hook", "teamId": "ENG", "resourceTypes": ["Issue"], **extra}
    body = gql(client, token, CREATE_HOOK, {"input": inp})
    assert "errors" not in body, body
    return body["data"]["webhookCreate"]["webhook"]


def new_issue(client, token, title="Spin up env", team="ENG"):
    return gql(client, token, CREATE_ISSUE, {"input": {"teamId": team, "title": title}})["data"]["issueCreate"]["issue"]


class Recorder:
    def __init__(self, codes=None):
        self.calls: list[tuple[str, bytes, dict]] = []
        self.codes = list(codes or [])

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return self.codes.pop(0) if self.codes else 200

    def payloads(self):
        return [json.loads(body) for _u, body, _h in self.calls]


def test_ticket_create_and_close_are_signed_linear_events(world):
    client, token, engine, _ = world
    hook = make_hook(client, token)
    assert hook["enabled"] and hook["secret"].startswith("hoja_wh_") and hook["team"]["key"] == "ENG"
    todo, done = state_id(client, token, "unstarted"), state_id(client, token, "completed")
    created = new_issue(client, token)  # the worker dispatches every second; do it per write
    assert webhooks.dispatch_once(engine) == 1
    gql(client, token, UPDATE_ISSUE, {"id": created["id"], "input": {"title": "Spin up env v2"}})
    assert webhooks.dispatch_once(engine) == 1
    gql(client, token, UPDATE_ISSUE, {"id": created["id"], "input": {"stateId": done}})
    assert webhooks.dispatch_once(engine) == 1
    recorder = Recorder()
    for _ in range(3):  # strict per-webhook FIFO: one per pass
        webhooks.deliver_due(engine, post=recorder)
    create, retitle, close = recorder.payloads()

    assert create["action"] == "create" and create["type"] == "Issue"
    assert create["data"]["identifier"] == created["identifier"]
    assert create["data"]["state"]["type"] == "unstarted" and "updatedFrom" not in create
    assert retitle["action"] == "update" and retitle["updatedFrom"]["title"] == "Spin up env"
    assert "stateId" not in retitle["updatedFrom"]
    assert close["updatedFrom"]["stateId"] == todo
    assert close["data"]["stateId"] == done and close["data"]["state"]["type"] == "completed"
    assert close["data"]["completedAt"] and close["updatedFrom"]["completedAt"] is None
    for payload in (create, retitle, close):
        assert payload["webhookId"] == hook["id"] and payload["organizationId"]
        assert payload["url"].endswith(created["identifier"])
        assert payload["data"]["branchName"] is None or isinstance(payload["data"]["branchName"], str)
    for _url, body, headers in recorder.calls:
        expected = hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(headers["Linear-Signature"], expected)
        assert headers["Linear-Event"] == "Issue" and headers["Linear-Delivery"]
        assert abs(json.loads(body)["webhookTimestamp"] - time.time() * 1000) < 60_000
        assert headers["Linear-Timestamp"] == str(json.loads(body)["webhookTimestamp"])
    assert webhooks.dispatch_once(engine) == 0  # cursor advanced: no duplicates


def test_failures_retry_in_order_then_give_up(world):
    client, token, engine, _ = world
    hook = make_hook(client, token)
    first = new_issue(client, token, "first")
    new_issue(client, token, "second")
    webhooks.dispatch_once(engine)
    now = datetime.now(timezone.utc)
    flaky = Recorder([500])
    assert webhooks.deliver_due(engine, post=flaky, now=now) == 0
    # The failed head blocks later events of this webhook until it is retried.
    assert webhooks.deliver_due(engine, post=flaky, now=now + timedelta(seconds=30)) == 0
    assert len(flaky.calls) == 1
    assert webhooks.deliver_due(engine, post=flaky, now=now + timedelta(minutes=2)) == 1
    assert webhooks.deliver_due(engine, post=flaky, now=now + timedelta(minutes=2)) == 1
    titles = [p["data"]["title"] for p in flaky.payloads()]
    assert titles == ["first", "first", "second"]
    first_delivery_ids = {h["Linear-Delivery"] for _u, _b, h in flaky.calls[:2]}
    assert len(first_delivery_ids) == 1  # stable id across retries for de-duplication

    gql(client, token, UPDATE_ISSUE, {"id": first["id"], "input": {"title": "doomed"}})
    webhooks.dispatch_once(engine)
    down = Recorder([503] * 10)
    for hours in (0, 1, 2, 9, 20):
        webhooks.deliver_due(engine, post=down, now=now + timedelta(hours=hours, minutes=5))
    assert len(down.calls) == 1 + len(webhooks.RETRY_DELAYS)
    failures = gql(client, token, LIST_HOOKS)["data"]["webhooks"]["nodes"][0]["failures"]
    assert failures == [{"httpStatus": 503.0, "responseOrError": "HTTP 503"}]
    assert hook["id"]


def test_dispatch_is_lossless_across_restart(world, monkeypatch):
    client, token, engine, path = world
    make_hook(client, token)
    new_issue(client, token, "before restart")
    assert webhooks.dispatch_once(engine) == 1
    issue = new_issue(client, token, "while down")
    gql(client, token, UPDATE_ISSUE, {"id": issue["id"], "input": {"stateId": state_id(client, token, "canceled")}})

    fresh = make_engine(path)  # a new process: only the database survives
    assert webhooks.dispatch_once(fresh) == 1  # coalesced: one create reflecting the latest row
    recorder = Recorder()
    while webhooks.deliver_due(fresh, post=recorder):
        pass
    assert [p["data"]["title"] for p in recorder.payloads()] == ["before restart", "while down"]
    assert webhooks.dispatch_once(fresh) == 0


def test_scope_disable_delete(world):
    client, token, engine, _ = world
    gql(client, token, "mutation { teamCreate(input: {name: \"Ops\", key: \"OPS\"}) { success } }")
    gql(client, token, "mutation { teamCreate(input: {name: \"Sec\", key: \"SEC\", visibility: \"private\"}) { success } }")
    eng = make_hook(client, token)
    public = make_hook(client, token, teamId=None, allPublicTeams=True)
    new_issue(client, token, "eng", "ENG")
    new_issue(client, token, "ops", "OPS")
    new_issue(client, token, "sec", "SEC")
    webhooks.dispatch_once(engine)
    recorder = Recorder()
    while webhooks.deliver_due(engine, post=recorder):
        pass
    seen = sorted((p["webhookId"] == eng["id"], p["data"]["title"]) for p in recorder.payloads())
    assert seen == [(False, "eng"), (False, "ops"), (True, "eng")]

    gql(client, token, UPDATE_HOOK, {"id": public["id"], "input": {"enabled": False}})
    assert gql(client, token, DELETE_HOOK, {"id": eng["id"]})["data"]["webhookDelete"]["success"]
    new_issue(client, token, "quiet")
    assert webhooks.dispatch_once(engine) == 0
    gql(client, token, UPDATE_HOOK, {"id": public["id"], "input": {"enabled": True}})
    assert webhooks.dispatch_once(engine) == 0  # re-enabling does not replay the gap


def test_admin_only_and_input_validation(world, monkeypatch):
    client, token, _engine, _ = world
    invited = gql(client, token, 'mutation { memberInvite(input: {email: "m@example.test", name: "M"}) { apiKey } }')
    member = invited["data"]["memberInvite"]["apiKey"]
    denied = gql(client, member, LIST_HOOKS)
    assert denied["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    denied = gql(client, member, CREATE_HOOK, {"input": {"url": "https://example.com", "teamId": "ENG", "resourceTypes": ["Issue"]}})
    assert denied["errors"][0]["extensions"]["code"] == "FORBIDDEN"

    def bad(inp):
        body = gql(client, token, CREATE_HOOK, {"input": {"teamId": "ENG", "resourceTypes": ["Issue"], **inp}})
        assert body["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT", body
        return body["errors"][0]["extensions"]["field"]

    assert bad({"url": "https://x.test", "resourceTypes": ["Billing"]}) == "resourceTypes"
    assert bad({"url": "https://x.test", "allPublicTeams": True}) == "teamId"
    assert bad({"url": "https://u:p@x.test"}) == "url"
    monkeypatch.delenv(webhooks.ALLOW_PRIVATE_ENV)
    for target in ("http://example.com/h", "https://127.0.0.1/h", "https://10.1.2.3/h",
                   "https://169.254.169.254/latest", "https://[::1]/h", "https://[::ffff:127.0.0.1]/h"):
        assert bad({"url": target}) == "url", target


def test_private_networks_refused_at_send_time_too(world, monkeypatch):
    client, token, engine, _ = world
    make_hook(client, token)  # created while private networks were allowed
    new_issue(client, token)
    webhooks.dispatch_once(engine)
    monkeypatch.delenv(webhooks.ALLOW_PRIVATE_ENV)
    recorder = Recorder()
    assert webhooks.deliver_due(engine, post=recorder) == 0
    assert recorder.calls == []


def test_public_target_check_uses_resolution():
    os.environ.pop(webhooks.ALLOW_PRIVATE_ENV, None)
    webhooks.check_target("https://hooks.example.com/x", resolve=lambda h, p: ["93.184.216.34"])
    with pytest.raises(webhooks.WebhookError):
        webhooks.check_target("https://rebind.example.com/x", resolve=lambda h, p: ["93.184.216.34", "192.168.1.4"])


class _Receiver(BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[dict, bytes]]] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Receiver.received.append(({k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_background_worker_delivers_to_a_real_http_receiver(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "1")
    monkeypatch.setenv(webhooks.ALLOW_PRIVATE_ENV, "1")
    _Receiver.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    path = tmp_path / "e2e.db"
    engine = make_engine(path)
    migrate(engine)
    token = seed_tenant(engine, org_name="E2E", org_url_key="e2e", demo_issues=False)["token"]
    try:
        with TestClient(create_app(db_path=str(path))) as client:
            hook = make_hook(client, token, url=f"http://127.0.0.1:{server.server_port}/linear")
            issue = new_issue(client, token, "worktree me")
            deadline = time.time() + 15
            while len(_Receiver.received) < 1 and time.time() < deadline:
                time.sleep(0.1)
            gql(client, token, UPDATE_ISSUE, {"id": issue["id"], "input": {"stateId": state_id(client, token, "completed")}})
            while len(_Receiver.received) < 2 and time.time() < deadline:
                time.sleep(0.1)
    finally:
        server.shutdown()
    assert len(_Receiver.received) == 2
    actions = []
    for headers, body in _Receiver.received:
        assert headers["linear-signature"] == hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
        payload = json.loads(body)
        actions.append((payload["action"], payload["data"]["state"]["type"], "stateId" in payload.get("updatedFrom", {})))
    assert actions == [("create", "unstarted", False), ("update", "completed", True)]
    sdk = os.environ.get("HOJA_LINEAR_SDK_DIR")
    if sdk:  # independent check: Linear's own verifier must accept Hoja's requests
        script = Path(__file__).with_name("linear_sdk_verify.mjs")
        for headers, body in _Receiver.received:
            out = subprocess.run(["node", str(script), sdk, hook["secret"], headers["linear-signature"]],
                                 input=body, capture_output=True, timeout=30)
            assert out.returncode == 0, out.stderr.decode()
