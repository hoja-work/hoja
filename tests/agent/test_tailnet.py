"""Machines through the workspace's tailnet (HOJA-602). Tailscale itself is faked at the seams
(``tailnet.api_factory``, ``node_factory``, ``ssh``); ``tailnet.py``'s real binary handling is
exercised by hand, not here.

==============================================  ==============================================
test                                            proves
==============================================  ==============================================
connecting_checks_the_client_and_seals_it       a bad secret is refused before it is stored;
                                                the stored one is sealed; Hoja's node joins
the_tailnet_lists_its_machines                  online first, Hoja's own node left out, a
                                                runner already there is matched by hostname
allowing_a_machine_tags_it_and_installs         tag:hoja-machine added (existing tags kept),
                                                the installer runs over SSH as the named user
                                                with a fresh enrollment token, state connected,
                                                the log never shows the token, all audited
just_this_once_is_used_up_and_revoke_sticks     once: no second connect; revoke: none at all
a_failed_install_says_why_everywhere            state failed, the log, the run log, the audit
input_and_permissions                           root refused, bad mode refused, members refused
disconnecting_revokes_and_leaves_the_tailnet    machines revoked, node down, secret gone
the_policy_edit_keeps_everything_and_is_idempot  Tailscale's default policy: comments kept,
                                                only what is missing added, twice = once
setting_up_with_an_access_token                 plan changes nothing; setup writes the policy
                                                (If-Match), creates Hoja's own OAuth client
                                                (scopes, tags), stores that, never the token
a_machine_with_tailscale_ssh_off_is_not_tagged  refused before anything changes
revision_84_adds_the_tables_and_replays         migration
==============================================  ==============================================
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from hoja_server import tailnet, tailnet_ops
from hoja_server.db import (
    agent_run_log,
    api_key,
    audit_event,
    new_id,
    now_iso,
    runner,
    tailnet_machine,
    token_hash,
    user,
)
from hoja_server.db import tailnet as tailnet_table
from tests.agent.test_auto_verify_and_pools import (  # noqa: F401 - `world` is a fixture
    _enroll,
    _gql,
    world,
)

SECRET = "tskey-client-kabc123-SECRETVALUE"
TOKEN = "tskey-api-kperson-TOKENVALUE"
FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "tailscale_default_policy.hujson"
DEVICES = [
    {"nodeId": "n-surface", "name": "surface.tail1234.ts.net", "hostname": "surface",
     "os": "linux", "addresses": ["100.64.0.7", "fd7a::7"], "connectedToControl": True,
     "lastSeen": "2026-11-19T20:00:00Z", "tags": []},
    {"nodeId": "n-laptop", "name": "laptop.tail1234.ts.net", "hostname": "laptop",
     "os": "macOS", "addresses": ["100.64.0.8"], "connectedToControl": False,
     "lastSeen": "2026-11-18T10:00:00Z", "tags": ["tag:dev"], "sshEnabled": True},
    {"nodeId": "n-nossh", "name": "nossh.tail1234.ts.net", "hostname": "nossh",
     "os": "linux", "addresses": ["100.64.0.10"], "connectedToControl": True,
     "tags": [], "sshEnabled": False, "user": "Ada.Lovelace@example.com"},
    {"nodeId": "n-pc", "name": "gamepc.tail1234.ts.net", "hostname": "gamepc",
     "os": "windows", "addresses": ["100.64.0.11"], "connectedToControl": True, "tags": []},
    {"nodeId": "n-hoja", "name": "hoja-alpha.tail1234.ts.net", "hostname": "hoja-alpha",
     "os": "linux", "addresses": ["100.64.0.9"], "connectedToControl": True,
     "tags": ["tag:hoja"]},
]


class FakeTailnet:
    def __init__(self):
        self.devices = [dict(d) for d in DEVICES]
        self.tag_calls: list[tuple[str, list[str]]] = []
        self.ups: list[tuple[str, str]] = []
        self.downs = 0
        self.ssh_calls: list[dict] = []
        self.ssh_result: object = 0
        self.refuse_secret = False
        self.policy = open(FIXTURE).read()
        self.etag = '"e1"'
        self.policy_writes: list[tuple[str, str]] = []
        self.clients: list[dict] = []

    # api_factory
    def api(self, client_id, secret):
        fake = self

        class API:
            def devices(self):
                if fake.refuse_secret or secret != SECRET:
                    raise tailnet.TailnetError("Tailscale refused this OAuth client.")
                return [tailnet._device(d) for d in fake.devices]

            def set_tags(self, device_id, tags):
                fake.tag_calls.append((device_id, sorted(tags)))
                for d in fake.devices:
                    if d["nodeId"] == device_id:
                        d["tags"] = sorted(tags)
        return API()

    # token_api_factory: a person's access token, which may also edit the policy and make clients
    def token_api(self, token):
        fake, base = self, self.api(None, SECRET)

        class TokenAPI:
            def devices(self):
                if token != TOKEN:
                    raise tailnet.TailnetError("Tailscale refused the token: it may have expired.")
                return base.devices()

            def get_policy(self):
                return fake.policy, fake.etag

            def validate_policy(self, text):
                tailnet.standard_json(text)

            def set_policy(self, text, etag):
                assert etag == fake.etag
                fake.policy_writes.append((text, etag))
                fake.policy, fake.etag = text, '"e2"'

            def create_oauth_client(self, description, scopes, tags):
                fake.clients.append({"description": description, "scopes": scopes, "tags": tags})
                return "kmade1", SECRET
        return TokenAPI()

    # node_factory
    def node(self, org):
        fake = self

        class Node:
            def status(self):
                return {"state": "running" if fake.ups and not fake.downs else "stopped",
                        "name": "hoja-alpha.tail1234.ts.net" if fake.ups else None}

            def up(self, secret, hostname):
                fake.ups.append((secret, hostname))
                return self.status()

            def down(self):
                fake.downs += 1
        return Node()

    # ssh
    def ssh(self, node, address, user_name, command, *, on_output, timeout=0):
        self.ssh_calls.append({"address": address, "user": user_name, "command": command})
        on_output(f"echo {command}")  # an installer that echoes its own command line
        on_output("Hoja runner installed and running.")
        if isinstance(self.ssh_result, Exception):
            raise self.ssh_result
        return self.ssh_result, "ssh-ed25519 abc"


@pytest.fixture
def fake(monkeypatch):
    f = FakeTailnet()
    monkeypatch.setattr(tailnet, "api_factory", f.api)
    monkeypatch.setattr(tailnet, "token_api_factory", f.token_api)
    monkeypatch.setattr(tailnet, "node_factory", f.node)
    monkeypatch.setattr(tailnet, "ssh", f.ssh)
    monkeypatch.setattr(tailnet, "in_background", lambda work: work())
    tailnet_ops._devices_cache.clear()
    tailnet_ops._node_errors.clear()
    return f


TAILNET = """connected name clientId nodeState nodeName nodeError policy devicesError
  devices { id hostname online tags reachable sshEnabled sshSupport suggestedUser runnerId runnerName runnerStatus
            access { mode sshUser workRoot state log hostKey usedAt } }"""


def _token(world):
    return world["alpha"]["token"]


def _connect(world, secret=SECRET):
    return _gql(world, _token(world), f'''mutation {{ tailnetConnect(clientId: "kabc123",
        clientSecret: {json.dumps(secret)}) {{ {TAILNET} }} }}''')


def _allow(world, device="n-surface", user_name="ada", mode="standing", work_root=None):
    extra = f", workRoot: {json.dumps(work_root)}" if work_root else ""
    return _gql(world, _token(world), f'''mutation {{ tailnetMachineAllow(deviceId: "{device}",
        sshUser: {json.dumps(user_name)}, mode: "{mode}"{extra}) {{ id reachable
        access {{ state mode }} }} }}''')


def _tailnet(world):
    body = _gql(world, _token(world), f"query {{ tailnet {{ {TAILNET} }} }}")
    assert "errors" not in body, body
    return body["data"]["tailnet"]


def _audits(world, action):
    with world["engine"].connect() as conn:
        return [json.loads(d) for d in conn.execute(select(audit_event.c.detail).where(
            audit_event.c.action == action)).scalars()]


def test_connecting_checks_the_client_and_seals_it(world, fake):
    assert not _tailnet(world)["connected"]
    bad = _connect(world, secret="not-a-secret")
    assert bad["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"
    refused = _connect(world, secret="tskey-client-kabc123-WRONG")
    assert "refused" in refused["errors"][0]["message"]
    with world["engine"].connect() as conn:
        assert conn.execute(select(tailnet_table)).first() is None, "nothing stored on refusal"

    body = _connect(world)
    assert "errors" not in body, body
    connected = body["data"]["tailnetConnect"]
    assert connected["connected"] and connected["name"] == "tail1234.ts.net"
    assert connected["nodeState"] == "running", "Hoja's node joined the tailnet"
    assert fake.ups == [(SECRET, "hoja-alpha")] or fake.ups[0][0] == SECRET
    assert "tag:hoja-machine" in json.loads(connected["policy"])["ssh"][0]["dst"]
    with world["engine"].connect() as conn:
        stored = conn.execute(select(tailnet_table.c.client_secret)).scalar()
    assert SECRET not in stored
    assert _audits(world, "tailnet.connect")[0]["tailnet"] == "tail1234.ts.net"


def test_the_tailnet_lists_its_machines(world, fake):
    _connect(world)
    identity = _enroll(world)
    with world["engine"].begin() as conn:
        conn.execute(runner.update().where(runner.c.id == identity["runner_id"]).values(
            name="surface", last_seen=now_iso()))
    devices = _tailnet(world)["devices"]
    assert [d["hostname"] for d in devices] == ["nossh", "surface", "laptop", "gamepc"], (
        "online first, no Hoja, the ones Tailscale SSH cannot reach last")
    surface = devices[1]
    assert surface["online"] and not surface["reachable"]
    assert surface["runnerId"] == identity["runner_id"] and surface["runnerStatus"] == "online"
    assert devices[2]["runnerId"] is None and devices[2]["access"] is None


def test_allowing_a_machine_tags_it_and_installs(world, fake):
    _connect(world)
    body = _allow(world, work_root="~/code")
    assert "errors" not in body, body
    assert fake.tag_calls == [("n-surface", ["tag:hoja-machine"])]
    assert _allow(world, device="n-laptop")["data"]["tailnetMachineAllow"]["reachable"]
    assert fake.tag_calls[1] == ("n-laptop", ["tag:dev", "tag:hoja-machine"]), "tags kept"

    call = fake.ssh_calls[0]
    assert call["address"] == "100.64.0.7" and call["user"] == "ada"
    assert "/install.sh | HOJA_ENROLLMENT_TOKEN=" in call["command"]
    assert call["command"].endswith(" HOJA_WORK_ROOT=~/code sh")
    surface = next(d for d in _tailnet(world)["devices"] if d["hostname"] == "surface")
    access = surface["access"]
    assert access["state"] == "connected" and access["sshUser"] == "ada"
    assert access["workRoot"] == "~/code" and access["hostKey"] == "ssh-ed25519 abc"
    assert "Hoja runner installed" in access["log"]
    token = call["command"].split("HOJA_ENROLLMENT_TOKEN=")[1].split()[0]
    assert token not in access["log"], "the log never shows the enrollment token"
    assert {"hostname": "surface", "ssh_user": "ada", "mode": "standing",
            "work_root": "~/code"} in _audits(world, "tailnet.machine.allow")
    assert any(a["state"] == "connected" for a in _audits(world, "tailnet.machine.install"))


def test_just_this_once_is_used_up_and_revoke_sticks(world, fake):
    _connect(world)
    org = world["alpha"]["org_id"]
    _allow(world, mode="once")
    assert not tailnet_ops.may_connect(world["engine"], org, "n-surface"), "once is used up"
    _allow(world, mode="standing")
    assert tailnet_ops.may_connect(world["engine"], org, "n-surface")
    revoked = _gql(world, _token(world), '''mutation { tailnetMachineRevoke(deviceId: "n-surface") {
        access { state } } }''')
    assert revoked["data"]["tailnetMachineRevoke"]["access"]["state"] == "revoked"
    assert not tailnet_ops.may_connect(world["engine"], org, "n-surface")
    assert _audits(world, "tailnet.machine.revoke")


def test_a_failed_install_says_why_everywhere(world, fake):
    _connect(world)
    fake.ssh_result = tailnet.TailnetError(
        "The machine answered, but not with Tailscale SSH. Turn it on once there: "
        "sudo tailscale set --ssh")
    _allow(world)
    access = next(d for d in _tailnet(world)["devices"] if d["hostname"] == "surface")["access"]
    assert access["state"] == "failed" and "sudo tailscale set --ssh" in access["log"]
    with world["engine"].connect() as conn:
        notes = conn.execute(select(agent_run_log.c.level, agent_run_log.c.message).where(
            agent_run_log.c.kind == "tailnet.install")).all()
    assert notes and notes[0][0] == "error" and "tailscale set --ssh" in notes[0][1]
    assert any(a["state"] == "failed" for a in _audits(world, "tailnet.machine.install"))

    fake.ssh_result = 3
    _allow(world)
    assert "exited with 3" in next(d for d in _tailnet(world)["devices"]
                                   if d["hostname"] == "surface")["access"]["log"]


def test_input_and_permissions(world, fake):
    _connect(world)
    assert _allow(world, user_name="root")["errors"][0]["extensions"]["field"] == "sshUser"
    assert _allow(world, user_name="ada; rm -rf /")["errors"][0]["extensions"]["field"] == "sshUser"
    assert _allow(world, mode="forever")["errors"][0]["extensions"]["field"] == "mode"
    assert _allow(world, device="n-nope")["errors"][0]["extensions"]["code"] == "NOT_FOUND"
    assert not fake.ssh_calls

    stamp, member = now_iso(), new_id()
    with world["engine"].begin() as conn:
        conn.execute(user.insert().values(id=member, organization_id=world["alpha"]["org_id"],
                                          name="Sam", display_name="Sam", email="sam@alpha.test",
                                          active=True, admin=False, timezone="UTC",
                                          created_at=stamp, updated_at=stamp))
        conn.execute(api_key.insert().values(id=new_id(), token_hash=token_hash("sam-tok"),
                                             label="m", user_id=member,
                                             organization_id=world["alpha"]["org_id"],
                                             created_at=stamp))
    for doc in ("query { tailnet { connected } }",
                'mutation { tailnetMachineAllow(deviceId: "n-surface", sshUser: "ada") { id } }'):
        assert _gql(world, "sam-tok", doc)["errors"][0]["extensions"]["code"] == "FORBIDDEN"


def test_disconnecting_revokes_and_leaves_the_tailnet(world, fake):
    _connect(world)
    _allow(world)
    body = _gql(world, _token(world), "mutation { tailnetDisconnect { connected } }")
    assert body["data"]["tailnetDisconnect"]["connected"] is False
    assert fake.downs == 1
    with world["engine"].connect() as conn:
        assert conn.execute(select(tailnet_table)).first() is None
        assert conn.execute(select(tailnet_machine.c.state)).scalars().all() == ["revoked"]


def test_the_policy_edit_keeps_everything_and_is_idempotent():
    original = FIXTURE.read_text()
    new, notes = tailnet.plan_policy(original)
    assert len(notes) == 4
    for line in original.splitlines():
        assert line in new, f"lost: {line!r}"
    data = tailnet.standard_json(new)
    assert len(data["ssh"]) == 2 and data["ssh"][1]["action"] == "check", "theirs kept, in order"
    assert data["grants"] == tailnet.POLICY["grants"]
    assert set(data["tagOwners"]) == {"tag:hoja", "tag:hoja-machine"}
    assert data["hosts"] == {'weird "key" // not a comment': "100.64.0.1"}
    assert tailnet.plan_policy(new) == (new, []), "nothing added twice"
    partial = '{"tagOwners": {"tag:hoja": ["alice@example.com"]}, // mine\n}'
    out, notes = tailnet.plan_policy(partial)
    assert tailnet.standard_json(out)["tagOwners"]["tag:hoja"] == ["alice@example.com"]
    assert "// mine" in out and len(notes) == 3
    assert sorted(tailnet.standard_json(tailnet.plan_policy("")[0])) == ["grants", "ssh", "tagOwners"]


def test_setting_up_with_an_access_token(world, fake):
    plan = _gql(world, _token(world), f'''mutation {{ tailnetSetupPlan(accessToken: "{TOKEN}") {{
        name devices additions addedText problem }} }}''')
    assert "errors" not in plan, plan
    plan = plan["data"]["tailnetSetupPlan"]
    assert plan["name"] == "tail1234.ts.net" and plan["devices"] == 4 and plan["problem"] is None
    assert any("may SSH into tag:hoja-machine" in a for a in plan["additions"])
    assert plan["addedText"].startswith('"tagOwners": {') and '"ssh": [' in plan["addedText"]
    assert "Allow all" not in plan["addedText"]
    assert not fake.policy_writes and not fake.clients, "a plan changes nothing"
    assert not _tailnet(world)["connected"]

    assert _gql(world, _token(world), '''mutation { tailnetSetup(accessToken: "nope") {
        connected } }''')["errors"][0]["extensions"]["field"] == "accessToken"
    body = _gql(world, _token(world), f'''mutation {{ tailnetSetup(accessToken: "{TOKEN}") {{
        {TAILNET} }} }}''')
    assert "errors" not in body, body
    assert body["data"]["tailnetSetup"]["connected"]
    assert body["data"]["tailnetSetup"]["clientId"] == "kmade1"
    assert len(fake.policy_writes) == 1 and fake.policy_writes[0][1] == '"e1"', "If-Match"
    assert fake.clients == [{"description": "Hoja agents", "scopes": ["devices:core", "auth_keys"],
                             "tags": ["tag:hoja", "tag:hoja-machine"]}]
    with world["engine"].connect() as conn:
        stored = conn.execute(select(tailnet_table.c.client_secret)).scalar()
        assert TOKEN not in json.dumps([dict(r._mapping) for r in conn.execute(
            select(audit_event))], default=str)
    assert SECRET not in stored
    assert _audits(world, "tailnet.policy.change")[0]["added"]
    assert _audits(world, "tailnet.connect")[0]["method"] == "access token"

    _gql(world, _token(world), f'''mutation {{ tailnetSetup(accessToken: "{TOKEN}") {{ connected }} }}''')
    assert len(fake.policy_writes) == 1, "a complete policy is not written again"


def test_a_machine_with_tailscale_ssh_off_is_not_tagged(world, fake):
    _connect(world)
    nossh = next(d for d in _tailnet(world)["devices"] if d["hostname"] == "nossh")
    assert nossh["sshEnabled"] is False and nossh["suggestedUser"] == "ada.lovelace"
    assert nossh["sshSupport"] == "yes"
    laptop = next(d for d in _tailnet(world)["devices"] if d["hostname"] == "laptop")
    assert laptop["sshSupport"] == "cli", "macOS: only with the open-source tailscaled"
    windows = _allow(world, device="n-pc")
    assert "Connect it with the command instead" in windows["errors"][0]["message"]
    refused = _allow(world, device="n-nossh")
    assert "sudo tailscale set --ssh" in refused["errors"][0]["message"]
    assert not fake.tag_calls and not fake.ssh_calls


def test_revision_84_adds_the_tables_and_replays(tmp_path):
    from sqlalchemy import inspect, text

    from hoja_server import db as db_module

    engine = db_module.make_engine(tmp_path / "rev83.db")
    db_module.migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE tailnet_machine")
        conn.exec_driver_sql("DROP TABLE tailnet")
        conn.execute(text("DELETE FROM schema_revision WHERE revision >= 84"))
    for _ in range(2):
        db_module.migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION
    assert {"tailnet", "tailnet_machine"} <= set(inspect(engine).get_table_names())
