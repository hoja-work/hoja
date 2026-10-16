"""Meetings: ABAC policy, guest links, waiting room, moderation, SFU authorization and the room socket."""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from cliniar_server.abac import Policy, deny, permit
from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant
from cliniar_server.store import Store

INVITE = "mutation($i: MemberInviteInput!) { memberInvite(input: $i) { success user { id } } }"


class FakeSFU:
    def __init__(self):
        self.calls = []
        self.n = 0

    async def new_session(self):
        self.n += 1
        return {"sessionId": f"sess{self.n}"}

    async def ice_servers(self, ttl=3600):
        return [{"urls": ["stun:stun.test:3478"]}]

    async def add_tracks(self, sid, body):
        self.calls.append(("new", sid, body))
        return {"sessionDescription": {"type": "answer", "sdp": "v=0"},
                "tracks": [{"mid": t.get("mid", "0"), "trackName": t.get("trackName", "")} for t in body["tracks"]]}

    async def update_tracks(self, sid, body):
        self.calls.append(("update", sid, body))
        return {"tracks": []}

    async def close_tracks(self, sid, body):
        self.calls.append(("close", sid, body))
        return {"tracks": []}

    async def renegotiate(self, sid, body):
        self.calls.append(("renegotiate", sid, body))
        return {}

    async def aclose(self):
        pass


POSTGRES_URL = os.environ.get("CLINIAR_TEST_POSTGRES_URL")


class World:
    """One disposable workspace. With CLINIAR_TEST_POSTGRES_URL set it runs on PostgreSQL (own schema)."""

    def __init__(self, tmp_path, sfu="fake"):
        self.db = tmp_path / "meet.db"
        self.pg_url = None
        self._pg_admin = None
        if POSTGRES_URL:
            self.schema = f"meet_{uuid.uuid4().hex[:10]}"
            self._pg_admin = make_engine(POSTGRES_URL)
            with self._pg_admin.begin() as conn:
                conn.exec_driver_sql(f"CREATE SCHEMA {self.schema}")
            sep = "&" if "?" in POSTGRES_URL else "?"
            self.pg_url = f"{POSTGRES_URL}{sep}options=-csearch_path%3D{self.schema}"
            self.engine = make_engine(self.pg_url)
        else:
            self.engine = make_engine(self.db)
        migrate(self.engine)
        self.admin = seed_tenant(self.engine, org_name="Org", org_url_key="org", user_email="host@meet.test",
                                 token="host-token", demo_issues=False)
        self.store = Store(self.engine)
        self.sfu = FakeSFU() if sfu == "fake" else sfu
        self.app = create_app(None if self.pg_url else str(self.db), database_url=self.pg_url, open_mode=False,
                              meeting_sfu=self.sfu)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def close(self):
        self.client.__exit__(None, None, None)
        if self._pg_admin is not None:
            self.engine.dispose()
            with self._pg_admin.begin() as conn:
                conn.exec_driver_sql(f"DROP SCHEMA {self.schema} CASCADE")
            self._pg_admin.dispose()

    def member(self, email, access="read_write"):
        res = self.client.post("/graphql", headers=_h(self.admin["token"]), json={
            "query": INVITE, "variables": {"i": {"name": email.split("@")[0], "email": email}}}).json()
        uid = res["data"]["memberInvite"]["user"]["id"]
        _, key = self.store.create_api_key(self.admin["org_id"], uid, "k", access, None)
        return uid, key

    def req(self, method, path, token=None, guest=None, json=None):
        headers = _h(token) if token else {}
        if guest:
            headers["X-Hoja-Guest-Token"] = guest
        return self.client.request(method, path, headers=headers, json=json)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.close()


def _create(w, token="host-token", **body):
    res = w.req("POST", "/api/meetings", token, json={"title": "Standup", **body})
    assert res.status_code == 201, res.text
    return res.json()["meeting"]["code"], res.json()


def _join(w, code, token=None, guest=None, **body):
    return w.req("POST", f"/api/meetings/{code}/join", token, guest, json=body)


def _act(w, code, action, token=None, guest=None, **payload):
    return w.req("POST", f"/api/meetings/{code}/act", token, guest, json={"action": action, "payload": payload})


# --------------------------------------------------------------------------- engine
def test_abac_default_deny_and_deny_overrides():
    policy = Policy([
        permit("p", "read", lambda c: c.s.role == "staff"),
        deny("d", "read", lambda c: c.r.secret, "secret"),
    ])
    assert not policy.decide("read", {"role": "guest"}, {})
    assert policy.decide("read", {"role": "staff"}, {}).allowed
    blocked = policy.decide("read", {"role": "staff"}, {"secret": True})
    assert not blocked.allowed and blocked.rule == "d"
    assert not policy.decide("write", {"role": "staff"}, {})


def test_abac_broken_predicate_fails_closed():
    boom = lambda c: 1 / 0  # noqa: E731
    assert not Policy([permit("p", "a", boom)]).decide("a")
    assert not Policy([permit("p", "a", lambda c: True), deny("d", "a", boom, "x")]).decide("a")


# --------------------------------------------------------------------------- lifecycle
def test_create_validates_and_lists(world):
    code, body = _create(world)
    assert len(code) == 13 and code.count("-") == 2
    assert body["meeting"]["settings"]["waitingRoom"] is True and body["meeting"]["linkEnabled"] is False
    assert world.req("POST", "/api/meetings", "host-token", json={"title": "x", "access": "bogus"}).status_code == 400
    assert world.req("POST", "/api/meetings", "host-token", json={"title": "x", "maxParticipants": 100000}
                     ).status_code == 400
    assert world.req("POST", "/api/meetings", None, json={"title": "x"}).status_code == 401
    listed = world.req("GET", "/api/meetings/list", "host-token").json()["meetings"]
    assert [m["code"] for m in listed] == [code]


def test_read_only_key_cannot_create_or_join(world):
    _, ro = world.member("ro@meet.test", access="read")
    assert world.req("POST", "/api/meetings", ro, json={"title": "x"}).status_code == 403
    code, _ = _create(world, access="org")
    res = _join(world, code, ro)
    assert res.status_code == 403 and res.json()["rule"] == "d-read-only-key"


def test_host_join_goes_live_and_ends(world):
    code, _ = _create(world)
    joined = _join(world, code, "host-token").json()
    assert joined["state"] == "admitted" and joined["participant"]["role"] == "host"
    assert world.req("GET", f"/api/meetings/{code}", "host-token").json()["meeting"]["status"] == "live"
    assert _act(world, code, "end", "host-token").status_code == 200
    assert _join(world, code, "host-token").status_code == 403
    assert world.req("GET", f"/api/meetings/list?scope=past", "host-token").json()["meetings"][0]["code"] == code


# --------------------------------------------------------------------------- access
def test_invite_only_then_waiting_room_then_admit(world):
    uid, key = world.member("guestuser@meet.test")
    code, _ = _create(world)
    assert world.req("GET", f"/api/meetings/{code}", key).status_code == 403
    assert _join(world, code, key).status_code == 403
    assert world.req("POST", f"/api/meetings/{code}/invitees", "host-token",
                     json={"invitees": ["guestuser@meet.test"]}).status_code == 201
    host = _join(world, code, "host-token").json()
    waiting = _join(world, code, key).json()
    assert waiting["state"] == "waiting"
    assert _act(world, code, "admit", "host-token", target=waiting["participant"]["id"]).status_code == 200
    assert world.req("GET", f"/api/meetings/{code}", key).json()["meeting"]["participantCount"] == 2
    assert host["participant"]["role"] == "host"


def test_org_access_without_waiting_room_joins_directly(world):
    _, key = world.member("peer@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    # host absent and join-before-host off: still parked
    assert _join(world, code, key).json()["state"] == "waiting"
    _join(world, code, "host-token")
    assert world.req("GET", f"/api/meetings/{code}", key).json()["meeting"]["participantCount"] == 2  # released


def test_guest_link_flow(world):
    code, _ = _create(world)
    assert world.req("GET", f"/api/meetings/{code}/preview").status_code == 404
    assert _join(world, code, name="Visitor").status_code == 404  # link off: indistinguishable from unknown code
    world.req("PATCH", f"/api/meetings/{code}", "host-token", json={"linkEnabled": True})
    assert world.req("GET", f"/api/meetings/{code}/preview").json()["meeting"]["title"] == "Standup"
    assert _join(world, code).status_code == 400  # name required
    guest = _join(world, code, name="Visitor").json()
    assert guest["state"] == "waiting" and guest["guestToken"]
    token = guest["guestToken"]
    # a waiting guest can't chat or use media
    assert _act(world, code, "chat", guest=token, body="hi").status_code == 403
    _join(world, code, "host-token")
    _act(world, code, "admit", "host-token", target=guest["participant"]["id"])
    assert _act(world, code, "chat", guest=token, body="hello").status_code == 200
    # guests never get member powers
    assert world.req("PATCH", f"/api/meetings/{code}", guest=token, json={"title": "x"}).status_code == 401
    assert _act(world, code, "settings", guest=token, locked=True).status_code == 403
    assert _act(world, code, "end", guest=token).status_code == 403
    # a refresh with the same token resumes the same seat
    again = _join(world, code, guest=token, name="Visitor").json()
    assert again["state"] == "admitted" and again["participant"]["id"] == guest["participant"]["id"]


def test_public_link_without_waiting_room_gets_a_passcode(world):
    code, body = _create(world, linkEnabled=True, waitingRoom=False)
    passcode = body["passcode"]
    assert passcode and f"?pwd={passcode}" in body["joinUrl"]
    _join(world, code, "host-token")
    assert _join(world, code, name="No Code").status_code == 403
    ok = _join(world, code, name="Right", passcode=passcode).json()
    assert ok["state"] == "admitted"


def test_passcode_failures_are_rate_limited(world):
    code, _ = _create(world, linkEnabled=True, passcode="secret1")
    for _ in range(8):
        assert _join(world, code, name="Guesser", passcode="wrong").status_code == 403
    assert _join(world, code, name="Guesser", passcode="secret1").status_code == 429


def test_lock_and_remove(world):
    code, _ = _create(world, linkEnabled=True, waitingRoom=False, passcode="pw1234")
    _join(world, code, "host-token")
    keep = _join(world, code, name="Keep", passcode="pw1234").json()
    kick = _join(world, code, name="Kick", passcode="pw1234").json()
    assert _act(world, code, "lock", "host-token", locked=True).status_code == 200
    late = _join(world, code, name="Late", passcode="pw1234")
    assert late.status_code == 403 and late.json()["rule"] == "d-locked"
    assert _join(world, code, guest=keep["guestToken"], name="Keep").json()["state"] == "admitted"  # rejoin allowed
    assert _act(world, code, "remove", "host-token", target=kick["participant"]["id"]).status_code == 200
    again = _join(world, code, guest=kick["guestToken"], name="Kick", passcode="pw1234")
    assert again.status_code == 403 and again.json()["rule"] == "d-excluded"


def test_moderation_limits(world):
    _, co_key = world.member("co@meet.test")
    _, at_key = world.member("at@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    host = _join(world, code, "host-token").json()["participant"]
    co = _join(world, code, co_key).json()["participant"]
    at = _join(world, code, at_key).json()["participant"]
    assert _act(world, code, "mute", at_key, target=co["id"]).status_code == 403  # attendees don't moderate
    assert _act(world, code, "promote", "host-token", target=co["id"], role="cohost").status_code == 200
    assert _act(world, code, "mute", co_key, target=at["id"]).status_code == 200
    denied = _act(world, code, "remove", co_key, target=host["id"])
    assert denied.status_code == 403 and denied.json()["rule"] == "d-target-host"
    assert _act(world, code, "promote", co_key, target=at["id"]).status_code == 403  # host-only
    assert _act(world, code, "end", co_key).status_code == 403
    assert _act(world, code, "transfer_host", "host-token", target=co["id"]).status_code == 200
    assert _act(world, code, "end", co_key).status_code == 200  # co is host now


def test_cross_workspace_member_is_just_a_guest(world):
    other = seed_tenant(world.engine, org_name="Other", org_url_key="other", user_email="x@other.test",
                        token="other-token", demo_issues=False)
    code, _ = _create(world, linkEnabled=True)
    assert world.req("GET", f"/api/meetings/{code}", "other-token").status_code == 403
    assert world.req("PATCH", f"/api/meetings/{code}", "other-token", json={"title": "x"}).status_code == 403
    res = _join(world, code, "other-token", name="Outsider").json()
    assert res["participant"]["kind"] == "guest" and res["guestToken"] and other["org_id"] != world.admin["org_id"]


def test_chat_private_messages_and_history(world):
    _, a_key = world.member("a@meet.test")
    _, b_key = world.member("b@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    a = _join(world, code, a_key).json()["participant"]
    b = _join(world, code, b_key).json()["participant"]
    assert _act(world, code, "chat", a_key, body="to b only", to=b["id"]).status_code == 200
    assert _act(world, code, "chat", a_key, body="everyone").status_code == 200
    assert _act(world, code, "chat", a_key, body="").status_code == 400
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", "host-token"]) as ws:
        snap = ws.receive_json()
        assert [c["body"] for c in snap["chat"]] == ["everyone"]  # host never sees a's private note
    assert a["id"] != b["id"]


# --------------------------------------------------------------------------- settings
def test_settings_permissions(world):
    _, key = world.member("s@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    _join(world, code, key)
    assert _act(world, code, "settings", key, chatEnabled=False).status_code == 403
    assert _act(world, code, "settings", "host-token", chatEnabled=False).status_code == 200
    assert _act(world, code, "chat", key, body="x").status_code == 403
    assert _act(world, code, "settings", "host-token", title="nope").status_code == 400
    assert world.req("PATCH", f"/api/meetings/{code}", key, json={"title": "x"}).status_code == 403
    assert world.req("PATCH", f"/api/meetings/{code}", "host-token", json={"nope": 1}).status_code == 400
    assert world.req("DELETE", f"/api/meetings/{code}", key).status_code == 403
    assert world.req("DELETE", f"/api/meetings/{code}", "host-token").status_code == 200
    assert _join(world, code, key).status_code == 403


# --------------------------------------------------------------------------- SFU
def _admitted_pair(world):
    _, key = world.member("pub@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    _join(world, code, key)
    return code, key


def test_sfu_not_configured(tmp_path):
    w = World(tmp_path, sfu=None)
    try:
        code, _ = _create(w)
        _join(w, code, "host-token")
        assert w.req("POST", f"/api/meetings/{code}/sfu/pub/session", "host-token").status_code == 503
    finally:
        w.close()


def test_sfu_requires_admission(world):
    code, _ = _create(world)
    world.member("w@meet.test")
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/session", "host-token").status_code == 403  # not joined
    _, key = world.member("late@meet.test")
    world.req("POST", f"/api/meetings/{code}/invitees", "host-token", json={"invitees": ["late@meet.test"]})
    _join(world, code, "host-token")
    assert _join(world, code, key).json()["state"] == "waiting"
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/session", key).status_code == 403


def test_sfu_publish_and_subscribe_are_scoped(world):
    code, key = _admitted_pair(world)
    host_session = world.req("POST", f"/api/meetings/{code}/sfu/pub/session", "host-token").json()
    assert host_session["iceServers"][0]["urls"] == ["stun:stun.test:3478"]
    other_session = world.req("POST", f"/api/meetings/{code}/sfu/sub/session", key).json()
    assert host_session["sessionId"] != other_session["sessionId"]

    sdp = {"type": "offer", "sdp": "v=0"}
    pushed = world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", "host-token", json={
        "sessionDescription": sdp, "tracks": [{"location": "local", "mid": "0", "trackName": "mic-abcd1234"},
                                              {"location": "local", "mid": "1", "trackName": "cam-abcd1234"}]})
    assert pushed.status_code == 200
    # the call went to the caller's own session, whatever the body says
    assert world.sfu.calls[-1][1] == host_session["sessionId"]
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", "host-token", json={
        "tracks": [{"location": "local", "mid": "2", "trackName": "weird name"}]}).status_code == 400
    # smuggled fields never reach Cloudflare
    world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", "host-token", json={
        "dataChannels": [{"location": "local", "dataChannelName": "x"}],
        "tracks": [{"location": "local", "mid": "3", "trackName": "mic-zzzz9999", "endpoint": "wss://evil"}]})
    assert "dataChannels" not in world.sfu.calls[-1][2]
    assert "endpoint" not in world.sfu.calls[-1][2]["tracks"][0]

    # subscribing: only tracks the server saw published in this meeting
    ok = world.req("POST", f"/api/meetings/{code}/sfu/sub/tracks", key, json={"tracks": [
        {"location": "remote", "sessionId": host_session["sessionId"], "trackName": "mic-abcd1234"}]})
    assert ok.status_code == 200 and world.sfu.calls[-1][1] == other_session["sessionId"]
    for bad in ({"sessionId": "someone-elses", "trackName": "mic-abcd1234"},
                {"sessionId": host_session["sessionId"], "trackName": "mic-notpublished"}):
        res = world.req("POST", f"/api/meetings/{code}/sfu/sub/tracks", key, json={"tracks": [
            {"location": "remote", **bad}]})
        assert res.status_code == 400
    # roster shows the published tracks
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", key]) as ws:
        roster = {p["role"]: p for p in ws.receive_json()["participants"]}
        assert {t["name"] for t in roster["host"]["tracks"]} == {"mic-abcd1234", "cam-abcd1234", "mic-zzzz9999"}


def test_screen_share_policy_and_forced_mute(world):
    code, key = _admitted_pair(world)
    world.req("POST", f"/api/meetings/{code}/sfu/pub/session", key)
    track = {"tracks": [{"location": "local", "mid": "0", "trackName": "screen-abcd1234"}]}
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", key, json=track).status_code == 200
    _act(world, code, "settings", "host-token", screenShare="host")
    denied = world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", key, json=track)
    assert denied.status_code == 403 and denied.json()["rule"] == "d-screen-host-only"
    # host mutes everyone and forbids unmuting: attendee audio is refused, video still fine
    _act(world, code, "mute_all", "host-token", allowUnmute=False)
    mic = {"tracks": [{"location": "local", "mid": "1", "trackName": "mic-abcd1234"}]}
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", key, json=mic).status_code == 403


# --------------------------------------------------------------------------- socket
def test_socket_lifecycle_waiting_admit_chat_end(world):
    code, _ = _create(world, linkEnabled=True)
    host = _join(world, code, "host-token").json()["participant"]
    guest = _join(world, code, name="Visitor").json()
    gid, gtoken = guest["participant"]["id"], guest["guestToken"]
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", "host-token"]) as hws, \
            world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["guest", gtoken]) as gws:
        hsnap = hws.receive_json()
        assert hsnap["t"] == "snapshot" and [w["id"] for w in hsnap["waiting"]] == [gid]
        gsnap = gws.receive_json()
        assert gsnap["you"]["state"] == "waiting" and gsnap["participants"] == []  # waiting sees nothing of the room

        hws.send_json({"t": "act", "id": 1, "a": "admit", "p": {"target": gid}})
        assert hws.receive_json()["ok"] is True
        assert hws.receive_json()["t"] == "joined"
        assert hws.receive_json()["t"] == "waiting_done"
        resynced = gws.receive_json()
        assert resynced["t"] == "snapshot" and resynced["you"]["state"] == "admitted"
        assert {p["id"] for p in resynced["participants"]} == {host["id"], gid}

        gws.send_json({"t": "chat", "id": 2, "body": "hello room"})
        assert gws.receive_json()["ok"] is True
        got = hws.receive_json()
        assert got["t"] == "chat" and got["message"]["body"] == "hello room"
        gws.receive_json()  # own copy

        gws.send_json({"t": "act", "id": 3, "a": "settings", "p": {"locked": True}})
        refused = gws.receive_json()
        assert refused["ok"] is False and refused["rule"] is None

        hws.send_json({"t": "act", "id": 4, "a": "end"})
        assert hws.receive_json()["ok"] is True
        assert gws.receive_json()["t"] == "ended"


def test_socket_rejects_strangers_and_unjoined(world):
    code, _ = _create(world)
    _, key = world.member("nobody@meet.test")
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", key]) as ws:
        with pytest.raises(Exception):
            ws.receive_json()
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["guest", "forged"]) as ws:
        with pytest.raises(Exception):
            ws.receive_json()
