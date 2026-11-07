"""Extensions: revertible installs, keyed upgrades, hooks, actions, bot credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import func, select
from starlette.testclient import TestClient

from hoja_server import db, extensions, webhooks
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

INSTALL = """mutation($m: JSON!) { extensionInstall(manifest: $m) {
  success apiKey signingSecret extension { id key access enabled botUser { id name }
  hooks { key on failurePolicy position } actions { id key title } failures { kind key message } } } }"""
UPDATE = """mutation($id: String!, $m: JSON!) { extensionUpdate(id: $id, manifest: $m) {
  success extension { version access hooks { key } actions { id key title } } } }"""
UNINSTALL = "mutation($id: String!) { extensionUninstall(id: $id) { success } }"
LIST = "{ extensions { id key version failures { kind key httpStatus message } } }"
ACTIONS = '{ extensionActions(entity: "Issue") { id key title extensionName } }'
RUN = """mutation($a: String!, $e: String!) { extensionActionRun(actionId: $a, entityId: $e) {
  success runId status message } }"""
CREATE = """mutation($input: IssueCreateInput!) { issueCreate(input: $input) {
  success issue { id identifier title priority } } }"""
UPDATE_ISSUE = """mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) {
  success issue { id title priority } } }"""
MEMBERS = "{ organizationMembers { nodes { id name } } }"


class Ext(BaseHTTPRequestHandler):
    """A real extension server: per-path behaviour, records every signed request."""

    calls: ClassVar[list[tuple[str, dict, bytes]]] = []
    routes: ClassVar[dict] = {}

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Ext.calls.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        status, reply, delay = Ext.routes.get(self.path, (200, {}, 0))
        if callable(reply):
            reply = reply(json.loads(body))
        if delay:
            time.sleep(delay)
        data = json.dumps(reply).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass

    @classmethod
    def paths(cls):
        return [p for p, _h, _b in cls.calls]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    monkeypatch.setenv(webhooks.ALLOW_PRIVATE_ENV, "1")
    Ext.calls, Ext.routes = [], {}
    extensions._BREAKER._state.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Ext)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    path = tmp_path / "ext.db"
    engine = make_engine(path)
    migrate(engine)
    token = seed_tenant(engine, org_name="Ext", org_url_key="ext", team_key="ENG",
                        user_name="Admin", user_email="admin@example.test", demo_issues=False)["token"]
    try:
        with TestClient(create_app(db_path=str(path))) as client:
            yield client, token, engine, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}})


def ok(client, token, query, variables=None):
    body = gql(client, token, query, variables).json()
    assert "errors" not in body, body
    return body["data"]


def err(client, token, query, variables=None):
    body = gql(client, token, query, variables).json()
    assert body.get("errors"), body
    return body["errors"][0]


def manifest(url, **over):
    m = {"key": "worktrees", "name": "Worktree provisioner", "version": "1.0.0", "url": url,
         "access": "read_write",
         "events": [{"key": "issues", "resourceTypes": ["Issue"], "teamId": "ENG"}],
         "hooks": [{"key": "guard", "on": "issue.beforeCreate", "path": "/hooks/guard"}],
         "actions": [{"key": "provision", "title": "Provision environment", "path": "/actions/provision"}]}
    m.update(over)
    return m


def install(client, token, m):
    return ok(client, token, INSTALL, {"m": m})["extensionInstall"]


def counts(engine):
    tables = (db.extension_hook, db.extension_action, db.extension_effect, db.webhook)
    with engine.connect() as conn:
        out = {t.name: conn.execute(select(func.count()).select_from(t)).scalar_one() for t in tables}
        out["active_bots"] = conn.execute(select(func.count()).select_from(db.user).where(
            db.user.c.kind == "bot", db.user.c.active.is_(True))).scalar_one()
        out["live_keys"] = conn.execute(select(func.count()).select_from(db.api_key).where(
            db.api_key.c.revoked_at.is_(None))).scalar_one()
    return out


def new_issue(client, token, title="Spin up env", **extra):
    return gql(client, token, CREATE, {"input": {"teamId": "ENG", "title": title, **extra}}).json()


def test_install_uninstall_is_exactly_reverted_and_reinstallable(world):
    client, token, engine, url = world
    before = counts(engine)
    first = install(client, token, manifest(url))
    ext = first["extension"]
    assert first["apiKey"].startswith("lin_api_") and first["signingSecret"].startswith("hoja_ext_")
    assert ext["access"] == "read_write" and ext["botUser"]["name"] == "Worktree provisioner"
    after = counts(engine)
    assert after["extension_effect"] == 5  # bot, key, webhook, hook, action
    assert after["active_bots"] == before["active_bots"] + 1
    # The bot is an identity, not a member, and its webhook is not manageable via webhooks.
    assert all(n["name"] != "Worktree provisioner" for n in ok(client, token, MEMBERS)["organizationMembers"]["nodes"])
    assert ok(client, token, "{ webhooks { nodes { id } } }")["webhooks"]["nodes"] == []
    assert ok(client, first["apiKey"], "{ viewer { name } }")["viewer"]["name"] == "Worktree provisioner"

    assert ok(client, token, UNINSTALL, {"id": ext["id"]})["extensionUninstall"]["success"]
    assert counts(engine) == before  # every registration reverted
    assert gql(client, first["apiKey"], "{ viewer { name } }").status_code == 401
    assert ok(client, token, LIST)["extensions"] == []
    with engine.connect() as conn:  # tombstoned for the audit trail, not deleted
        assert conn.execute(select(db.extension.c.uninstalled_at)).scalar() is not None
    again = install(client, token, manifest(url))
    assert again["extension"]["id"] != ext["id"]


def test_install_is_all_or_nothing(world):
    client, token, engine, url = world
    before = counts(engine)
    bad = manifest(url, events=[{"key": "a", "teamId": "ENG"}, {"key": "b", "teamId": "NOPE"}])
    error = err(client, token, INSTALL, {"m": bad})
    assert error["extensions"]["code"] == "BAD_USER_INPUT" and error["extensions"]["field"] == "events[1].teamId"
    assert counts(engine) == before
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.extension)).scalar_one() == 0


@pytest.mark.parametrize(("patch", "field"), [
    ({"key": "Bad Key"}, "key"),
    ({"surprise": 1}, "surprise"),
    ({"access": "admin"}, "access"),
    ({"hooks": [{"key": "h", "on": "issue.afterDelete", "path": "/h"}]}, "hooks[0].on"),
    ({"hooks": [{"key": "h", "on": "issue.beforeCreate", "path": "https://evil.test/h"}]}, "hooks[0].path"),
    ({"actions": [{"key": "a", "title": "A", "path": "/../a"}]}, "actions[0].path"),
    ({"actions": [{"key": "a", "title": "A", "path": "/a"}, {"key": "a", "title": "B", "path": "/b"}]},
     "actions[1].key"),
])
def test_manifest_validation_names_the_field(world, patch, field):
    client, token, _engine, url = world
    error = err(client, token, INSTALL, {"m": manifest(url, **patch)})
    assert error["extensions"] == {"code": "BAD_USER_INPUT", "field": field}, error


def test_private_networks_and_ports_need_opt_in(world, monkeypatch):
    client, token, _engine, url = world
    monkeypatch.delenv(webhooks.ALLOW_PRIVATE_ENV)
    assert err(client, token, INSTALL, {"m": manifest(url)})["extensions"]["field"] == "url"
    with pytest.raises(webhooks.WebhookError, match="port 443"):
        webhooks.check_target("https://hooks.example.com:8443/x", resolve=lambda h, p: ["93.184.216.34"])


def test_management_is_admin_only_but_members_can_list_and_run_actions(world):
    client, token, _engine, url = world
    ext = install(client, token, manifest(url, hooks=[]))
    member = ok(client, token, 'mutation { memberInvite(input: {email: "m@example.test", name: "M"}) { apiKey } }')
    member = member["memberInvite"]["apiKey"]
    assert err(client, member, LIST)["extensions"]["code"] == "FORBIDDEN"
    assert err(client, member, INSTALL, {"m": manifest(url, key="other")})["extensions"]["code"] == "FORBIDDEN"
    assert err(client, member, UNINSTALL, {"id": ext["extension"]["id"]})["extensions"]["code"] == "FORBIDDEN"
    actions = ok(client, member, ACTIONS)["extensionActions"]
    assert [(a["key"], a["extensionName"]) for a in actions] == [("provision", "Worktree provisioner")]
    # A bot can never be promoted to admin.
    bot_id = ext["extension"]["botUser"]["id"]
    promoted = ok(client, token, "mutation($u: String!) { memberRoleUpdate(userId: $u, admin: true) { success } }",
                  {"u": bot_id})
    assert promoted["memberRoleUpdate"]["success"] is False


def test_read_access_bot_cannot_mutate(world):
    client, token, _engine, url = world
    ext = install(client, token, manifest(url, access="read", hooks=[]))
    assert ok(client, ext["apiKey"], "{ issues { nodes { id } } }")
    response = gql(client, ext["apiKey"], CREATE, {"input": {"teamId": "ENG", "title": "nope"}})
    assert response.status_code == 403


def test_update_reconciles_by_key_and_rolls_back_on_error(world):
    client, token, engine, url = world
    ext = install(client, token, manifest(url))
    ext_id, action_id = ext["extension"]["id"], ext["extension"]["actions"][0]["id"]
    new = manifest(url, version="1.1.0", access="read", hooks=[],
                   events=[{"key": "issues", "allPublicTeams": True}, {"key": "more", "teamId": "ENG"}],
                   actions=[{"key": "provision", "title": "Provision env v2", "path": "/actions/p2"},
                            {"key": "teardown", "title": "Tear down", "path": "/actions/teardown"}])
    updated = ok(client, token, UPDATE, {"id": ext_id, "m": new})["extensionUpdate"]["extension"]
    assert updated["version"] == "1.1.0" and updated["access"] == "read" and updated["hooks"] == []
    titles = {a["key"]: (a["id"], a["title"]) for a in updated["actions"]}
    assert titles["provision"] == (action_id, "Provision env v2")  # same row, new details
    assert set(titles) == {"provision", "teardown"}
    assert gql(client, ext["apiKey"], CREATE, {"input": {"teamId": "ENG", "title": "x"}}).status_code == 403
    with engine.connect() as conn:
        hooks = conn.execute(select(db.webhook.c.event_key, db.webhook.c.all_public_teams)
                             .order_by(db.webhook.c.event_key)).all()
    assert hooks == [("issues", True), ("more", False)]

    before = counts(engine)
    broken = dict(new, version="2.0.0", events=[{"key": "issues", "teamId": "NOPE"}])
    assert err(client, token, UPDATE, {"id": ext_id, "m": broken})["extensions"]["field"] == "events[0].teamId"
    assert counts(engine) == before
    assert ok(client, token, LIST)["extensions"][0]["version"] == "1.1.0"
    assert err(client, token, UPDATE, {"id": ext_id, "m": dict(new, key="renamed")})["extensions"]["field"] == "key"


def _secret_ok(secret, headers, body):
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(headers["linear-signature"], expected) and \
        abs(json.loads(body)["webhookTimestamp"] - time.time() * 1000) < 60_000


def test_hooks_patch_veto_and_attribute(world):
    client, token, engine, url = world
    Ext.routes["/hooks/guard"] = (200, lambda p: (
        {"allow": False, "message": "titles need a ticket prefix"} if not p["input"]["title"].startswith("[")
        else {"allow": True, "patch": {"priority": 2}}), 0)
    ext = install(client, token, manifest(url))

    vetoed = new_issue(client, token, "no prefix")
    assert vetoed["errors"][0]["extensions"]["code"] == "EXTENSION_REJECTED"
    assert "titles need a ticket prefix" in vetoed["errors"][0]["message"]
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.issue)).scalar_one() == 0

    created = new_issue(client, token, "[ENV] spin up")["data"]["issueCreate"]["issue"]
    assert created["priority"] == 2
    path, headers, body = Ext.calls[-1]
    sent = json.loads(body)
    assert path == "/hooks/guard" and sent["on"] == "issue.beforeCreate" and sent["actor"]["type"] == "user"
    assert _secret_ok(ext["signingSecret"], headers, body)
    with engine.connect() as conn:
        patched = conn.execute(select(db.issue_activity.c.actor_id, db.issue_activity.c.changes).where(
            db.issue_activity.c.event_type == "extensionPatched")).one()
    assert patched[0] == ext["extension"]["botUser"]["id"]
    assert json.loads(patched[1]) == {"hook": "guard", "fields": ["priority"]}


def test_hook_chain_order_and_update_context(world):
    client, token, _engine, url = world
    install(client, token, manifest(url, key="first", events=[], actions=[], hooks=[
        {"key": "a", "on": "issue.beforeUpdate", "path": "/a"}]))
    install(client, token, manifest(url, key="second", events=[], actions=[], hooks=[
        {"key": "b", "on": "issue.beforeUpdate", "path": "/b"}]))
    Ext.routes["/a"] = (200, {"allow": True, "patch": {"title": "from a"}}, 0)
    Ext.routes["/b"] = (200, lambda p: {"allow": True, "patch": {"description": "saw " + p["input"]["title"]}}, 0)
    issue = new_issue(client, token, "original")["data"]["issueCreate"]["issue"]
    ok(client, token, UPDATE_ISSUE, {"id": issue["id"], "input": {"priority": 1}})
    a, b = [json.loads(body) for _p, _h, body in Ext.calls]
    assert a["data"]["title"] == "original" and a["input"] == {"priority": 1}
    assert b["input"] == {"priority": 1, "title": "from a"}
    detail = ok(client, token, '{ issue(id: "%s") { title description } }' % issue["id"])["issue"]
    assert detail == {"title": "from a", "description": "saw from a"}


def test_hook_failures_follow_policy_and_trip_the_breaker(world):
    client, token, _engine, url = world
    Ext.routes["/slow"] = (200, {"allow": True}, 2.5)
    Ext.routes["/broken"] = (500, {}, 0)
    Ext.routes["/sneaky"] = (200, {"allow": True, "patch": {"teamId": "other"}}, 0)
    install(client, token, manifest(url, key="soft", events=[], actions=[], hooks=[
        {"key": "slow", "on": "issue.beforeCreate", "path": "/slow", "failurePolicy": "allow"},
        {"key": "sneaky", "on": "issue.beforeCreate", "path": "/sneaky", "failurePolicy": "allow"}]))
    started = time.monotonic()
    created = new_issue(client, token, "survives")
    assert created["data"]["issueCreate"]["success"] and time.monotonic() - started < 5
    failures = {(f["key"], f["kind"]) for f in ok(client, token, LIST)["extensions"][0]["failures"]}
    assert failures == {("slow", "hook"), ("sneaky", "hook")}

    install(client, token, manifest(url, key="strict", events=[], actions=[], hooks=[
        {"key": "broken", "on": "issue.beforeCreate", "path": "/broken", "failurePolicy": "block"}]))
    Ext.routes["/slow"] = (200, {"allow": True}, 0)
    blocked = new_issue(client, token, "blocked")
    assert blocked["errors"][0]["extensions"]["code"] == "EXTENSION_REJECTED"
    assert "unavailable" in blocked["errors"][0]["message"]

    for _ in range(extensions.BREAKER_THRESHOLD):
        new_issue(client, token, "again")
    calls_before = Ext.paths().count("/broken")
    still_blocked = new_issue(client, token, "breaker open")
    assert "circuit open" in still_blocked["errors"][0]["message"]
    assert Ext.paths().count("/broken") == calls_before  # no call while the breaker is open


def test_actions_run_once_audited_and_bot_callbacks_skip_own_events(world):
    client, token, engine, url = world
    Ext.routes["/actions/provision"] = (200, lambda p: {"message": f"provisioned {p['data']['identifier']}"}, 0)
    ext = install(client, token, manifest(url, hooks=[]))
    issue = new_issue(client, token, "needs env")["data"]["issueCreate"]["issue"]
    action_id = ext["extension"]["actions"][0]["id"]
    run = ok(client, token, RUN, {"a": action_id, "e": issue["identifier"]})["extensionActionRun"]
    assert run["success"] and run["status"] == "succeeded" and run["message"] == f"provisioned {issue['identifier']}"
    path, headers, body = Ext.calls[-1]
    sent = json.loads(body)
    assert path == "/actions/provision" and sent["runId"] == run["runId"] and sent["data"]["id"] == issue["id"]
    assert headers["linear-event"] == "IssueAction" and _secret_ok(ext["signingSecret"], headers, body)
    with engine.connect() as conn:
        audit = conn.execute(select(db.extension_action_run.c.status, db.extension_action_run.c.actor_id)).one()
    assert audit.status == "succeeded"

    Ext.routes["/actions/provision"] = (502, {"message": "simulator farm is down"}, 0)
    failed = ok(client, token, RUN, {"a": action_id, "e": issue["id"]})["extensionActionRun"]
    assert failed == {"success": False, "runId": failed["runId"], "status": "failed",
                      "message": "simulator farm is down"}
    assert Ext.paths().count("/actions/provision") == 2  # no automatic retry

    # Events: the issue creation reaches the extension; its own callback edit does not.
    webhooks.dispatch_once(engine)
    ok(client, ext["apiKey"], UPDATE_ISSUE, {"id": issue["id"], "input": {"title": "env ready"}})
    ok(client, token, "mutation($i: WebhookCreateInput!) { webhookCreate(input: $i) { success } }",
       {"i": {"url": url + "/plain", "teamId": "ENG", "resourceTypes": ["Issue"]}})
    ok(client, ext["apiKey"], UPDATE_ISSUE, {"id": issue["id"], "input": {"title": "env ready!"}})
    webhooks.dispatch_once(engine)
    while webhooks.deliver_due(engine):
        pass
    events = {p: json.loads(b) for p, _h, b in Ext.calls if p in ("/events", "/plain")}  # FIFO is per webhook
    assert sorted((p, e["action"]) for p, e in events.items()) == [("/events", "create"), ("/plain", "update")]
    assert len([p for p in Ext.paths() if p == "/events"]) == 1  # the bot's edits never reached its own extension
    assert events["/plain"]["actor"] == {"id": ext["extension"]["botUser"]["id"], "type": "integration",
                                     "name": "Worktree provisioner", "email": None}


def test_new_tables_are_tenant_scoped():
    for table in (db.extension, db.extension_effect, db.extension_hook, db.extension_action,
                  db.extension_action_run, db.extension_failure):
        assert "organization_id" in table.c, table.name  # rls_mode classifies these as "direct"


def test_issue_writes_only_flow_through_hooked_resolvers():
    root = Path(__file__).resolve().parents[1] / "hoja_server"
    callers = sorted(p.name for p in root.glob("*.py")
                     if re.search(r"\.issue_(create|update)\(", p.read_text()) and p.name != "writer.py")
    assert callers == ["resolvers.py"], callers
