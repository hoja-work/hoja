"""Invitations, RSVP, ringing, calendar, reminders, usage and the personal inbox socket."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from tests.test_meetings import FakeSFU, World, _act, _create, _h, _join  # noqa: E402

from cliniar_server.meetings import MeetingService, Principal  # noqa: E402


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.close()


def _inbox(world, key):
    return world.client.websocket_connect("/ws/meetings-inbox", subprotocols=["bearer", key])


def _soon(minutes=3):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_invite_notifies_invitee_inbox_and_lists_pending(world):
    uid, key = world.member("ann@meet.test")
    with _inbox(world, key) as ws:
        hello = ws.receive_json()
        assert hello["t"] == "hello" and hello["invitations"] == [] and hello["rings"] == []
        code, _ = _create(world, scheduledStart=_soon(60), invitees=["ann@meet.test", "outsider@example.com"])
        note = ws.receive_json()
        assert note["t"] == "invited" and note["meeting"]["code"] == code and note["by"]
    pending = world.req("GET", "/api/meetings/invitations", key).json()["invitations"]
    assert [i["code"] for i in pending] == [code] and pending[0]["invitedBy"]
    assert world.req("GET", "/api/meetings/invitations", "host-token").json()["invitations"] == []  # host is not invited
    # a reconnecting client sees the unanswered invitation in its hello
    with _inbox(world, key) as ws:
        assert [i["code"] for i in ws.receive_json()["invitations"]] == [code]
    assert uid


def test_rsvp_updates_host_and_clears_pending(world):
    _, key = world.member("bo@meet.test")
    code, _ = _create(world, scheduledStart=_soon(60), invitees=["bo@meet.test"])
    with _inbox(world, "host-token") as ws:
        ws.receive_json()
        assert world.req("POST", f"/api/meetings/{code}/rsvp", key, json={"response": "accepted"}).status_code == 200
        got = ws.receive_json()
        assert got["t"] == "rsvp" and got["response"] == "accepted" and got["code"] == code
    assert world.req("GET", "/api/meetings/invitations", key).json()["invitations"] == []
    assert world.req("GET", f"/api/meetings/{code}", key).json()["meeting"]["myResponse"] == "accepted"
    assert world.req("POST", f"/api/meetings/{code}/rsvp", key, json={"response": "maybe"}).status_code == 400
    _, stranger = world.member("cy@meet.test")
    assert world.req("POST", f"/api/meetings/{code}/rsvp", stranger, json={"response": "accepted"}).status_code == 403
    assert world.req("POST", f"/api/meetings/{code}/rsvp", "host-token", json={"response": "accepted"}).status_code == 403


def test_declined_invitee_gets_no_updates(world):
    _, key = world.member("di@meet.test")
    code, _ = _create(world, scheduledStart=_soon(60), invitees=["di@meet.test"])
    world.req("POST", f"/api/meetings/{code}/rsvp", key, json={"response": "declined"})
    with _inbox(world, key) as ws:
        ws.receive_json()
        world.req("PATCH", f"/api/meetings/{code}", "host-token", json={"title": "Renamed"})
        world.req("PATCH", f"/api/meetings/{code}", "host-token", json={"waitingRoom": False})  # no notice either
        # nothing is queued for a decliner: the next frame we get is our own ping reply
        ws.send_json({"t": "ping"})
        assert ws.receive_json()["t"] == "pong"


def test_reschedule_and_cancel_notify_invitees(world):
    _, key = world.member("ed@meet.test")
    code, _ = _create(world, scheduledStart=_soon(60), invitees=["ed@meet.test"])
    with _inbox(world, key) as ws:
        ws.receive_json()
        world.req("PATCH", f"/api/meetings/{code}", "host-token", json={"title": "Moved", "scheduledStart": _soon(120)})
        upd = ws.receive_json()
        assert upd["t"] == "meeting_updated" and upd["meeting"]["title"] == "Moved"
        assert world.req("DELETE", f"/api/meetings/{code}", "host-token").status_code == 200
        assert ws.receive_json()["t"] == "meeting_cancelled"


def test_ring_flow_accept(world):
    callee_id, key = world.member("callee@meet.test")
    code, _ = _create(world, access="invited", waitingRoom=False)
    _join(world, code, "host-token")
    with _inbox(world, key) as callee, _inbox(world, "host-token") as caller:
        callee.receive_json(), caller.receive_json()
        res = world.req("POST", f"/api/meetings/{code}/ring", "host-token", json={"userIds": [callee_id]})
        assert res.status_code == 201 and res.json()["ringSeconds"] == 45
        ring = callee.receive_json()
        assert ring["t"] == "ring" and ring["ring"]["meeting"]["code"] == code and ring["ring"]["from"]["name"]
        # the callee was auto-invited, so the call can actually be joined
        ok = world.req("POST", f"/api/meetings/rings/{ring['ring']['id']}/respond", key, json={"answer": "accept"})
        assert ok.status_code == 200 and ok.json()["joinPath"] == f"/meet/{code}"
        answered = caller.receive_json()
        assert answered["t"] == "ring_answered" and answered["answer"] == "accept"
        assert callee.receive_json()["t"] == "ring_ended"
    assert _join(world, code, key).json()["state"] == "admitted"
    # answered calls cannot be answered twice
    again = world.req("POST", f"/api/meetings/rings/{ring['ring']['id']}/respond", key, json={"answer": "accept"})
    assert again.status_code == 400


def test_ring_decline_missed_and_reload(world):
    callee_id, key = world.member("late@meet.test")
    code, _ = _create(world)
    _join(world, code, "host-token")
    ring_id = world.req("POST", f"/api/meetings/{code}/ring", "host-token",
                        json={"userIds": [callee_id]}).json()["rings"][0]["id"]
    with _inbox(world, key) as ws:  # a reload during the ring still shows the call
        assert [r["id"] for r in ws.receive_json()["rings"]] == [ring_id]
    other_id, other = world.member("other@meet.test")
    assert world.req("POST", f"/api/meetings/rings/{ring_id}/respond", other, json={"answer": "accept"}).status_code == 404
    assert world.req("POST", f"/api/meetings/rings/{ring_id}/respond", key, json={"answer": "decline"}).status_code == 200
    # the host ending the meeting cancels any call still ringing
    ring2 = world.req("POST", f"/api/meetings/{code}/ring", "host-token", json={"userIds": [other_id]}).json()["rings"][0]["id"]
    with _inbox(world, other) as ws:
        assert [r["id"] for r in ws.receive_json()["rings"]] == [ring2]
        _act(world, code, "end", "host-token")
        assert ws.receive_json() == {"t": "ring_ended", "id": ring2, "reason": "cancelled"}


def test_ring_permissions(world):
    callee_id, key = world.member("rp@meet.test")
    other_id, other = world.member("rp2@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    _join(world, code, key)
    assert world.req("POST", f"/api/meetings/{code}/ring", key, json={"userIds": [other_id]}).status_code == 403
    assert world.req("POST", f"/api/meetings/{code}/ring", "host-token", json={"userIds": []}).status_code == 400
    assert world.req("POST", f"/api/meetings/{code}/ring", "host-token",
                     json={"userIds": [world.admin.get("user_id", "nobody")]}).status_code == 400  # cannot call yourself
    assert callee_id and other


def test_calendar_range_visibility_and_ics(world):
    _, key = world.member("cal@meet.test")
    _, stranger = world.member("nope@meet.test")
    day = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=2)
    code, _ = _create(world, title="Planning, Q4; kickoff", scheduledStart=day.isoformat(), durationMinutes=45,
                      invitees=["cal@meet.test"], description="Agenda\nline two")
    org_code, _ = _create(world, title="All hands", access="org", scheduledStart=(day + timedelta(hours=1)).isoformat())
    far, _ = _create(world, title="Later", scheduledStart=(day + timedelta(days=60)).isoformat())
    window = {"from": (day - timedelta(days=1)).isoformat(), "to": (day + timedelta(days=1)).isoformat()}
    q = f"from={window['from'].replace('+', '%2B')}&to={window['to'].replace('+', '%2B')}"
    mine = world.req("GET", f"/api/meetings/calendar?{q}", key).json()["meetings"]
    assert [m["code"] for m in mine] == [code, org_code]
    assert mine[0]["myResponse"] == "needsAction" and mine[0]["isHost"] is False
    assert [m["code"] for m in world.req("GET", f"/api/meetings/calendar?{q}", stranger).json()["meetings"]] == [org_code]
    host_view = world.req("GET", f"/api/meetings/calendar?{q}", "host-token").json()["meetings"]
    assert host_view[0]["isHost"] is True and host_view[0]["myResponse"] == "accepted" and far not in {m["code"] for m in host_view}
    assert world.req("GET", "/api/meetings/calendar?from=bad&to=worse", key).status_code == 400
    wide = f"from={window['from'].replace('+', '%2B')}&to={(day + timedelta(days=400)).isoformat().replace('+', '%2B')}"
    assert world.req("GET", f"/api/meetings/calendar?{wide}", key).status_code == 400

    ics = world.req("GET", f"/api/meetings/{code}/ics", key)
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    body = ics.text
    assert "BEGIN:VCALENDAR" in body and r"SUMMARY:Planning\, Q4\; kickoff" in body
    assert f"DTSTART:{day.strftime('%Y%m%dT%H%M%SZ')}" in body
    assert f"DTEND:{(day + timedelta(minutes=45)).strftime('%Y%m%dT%H%M%SZ')}" in body
    assert f"/meet/{code}" in body and "mailto:cal@meet.test" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert world.req("GET", f"/api/meetings/{code}/ics", stranger).status_code == 403


def test_people_directory_is_scoped_to_the_workspace(world):
    world.member("pp@meet.test")
    people = world.req("GET", "/api/meetings/people", "host-token").json()["people"]
    assert {p["email"] for p in people} >= {"host@meet.test", "pp@meet.test"}
    assert world.req("GET", "/api/meetings/people").status_code == 401


def test_starting_soon_reminder_fires_once(world):
    _, key = world.member("rem@meet.test")
    code, _ = _create(world, scheduledStart=_soon(3), invitees=["rem@meet.test"])
    _create(world, scheduledStart=_soon(90))  # not due yet
    service = MeetingService(world.engine)
    first = service.due_reminders()
    assert len(first) == 1 and first[0].msg["meeting"]["code"] == code and first[0].msg["t"] == "starting_soon"
    assert len(first[0].users) == 2  # host + invitee
    assert service.due_reminders() == []


def test_usage_reports_estimated_cost_to_the_host(world):
    _, key = world.member("use@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    _join(world, code, key)
    assert world.req("POST", f"/api/meetings/{code}/usage", key, json={"bytesIn": 4_000_000_000, "bytesOut": 10}).status_code == 200
    world.req("POST", f"/api/meetings/{code}/usage", key, json={"bytesIn": 1, "bytesOut": 1})  # counters never go backwards
    usage = world.req("GET", f"/api/meetings/{code}", "host-token").json()["meeting"]["usage"]
    assert usage["egressBytes"] == 4_000_000_000 and usage["estimatedCostUsd"] == 0.2
    assert "usage" not in world.req("GET", f"/api/meetings/{code}", key).json()["meeting"]
    assert world.req("POST", f"/api/meetings/{code}/usage", key, json={"bytesIn": -1}).status_code == 400


def test_media_state_and_forced_mute(world):
    _, key = world.member("ms@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    me = _join(world, code, key).json()["participant"]
    assert _act(world, code, "media", key, audio=True, video=True).status_code == 200
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", "host-token"]) as ws:
        roster = {p["id"]: p for p in ws.receive_json()["participants"]}
        assert roster[me["id"]]["audio"] is True and roster[me["id"]]["video"] is True
    _act(world, code, "mute_all", "host-token", allowUnmute=False)
    assert _act(world, code, "media", key, audio=True).status_code == 400
    assert _act(world, code, "media", key, video=False).status_code == 200


def test_sessions_are_role_separated(world):
    _, key = world.member("rs@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    _join(world, code, key)
    world.req("POST", f"/api/meetings/{code}/sfu/pub/session", "host-token")
    world.req("POST", f"/api/meetings/{code}/sfu/sub/session", key)
    # a subscribe session may only pull; the publish session may only push
    push = {"tracks": [{"location": "local", "mid": "0", "trackName": "mic-abcd1234"}]}
    assert world.req("POST", f"/api/meetings/{code}/sfu/sub/tracks", key, json=push).status_code == 400
    pull = {"tracks": [{"location": "remote", "sessionId": "x", "trackName": "mic-abcd1234"}]}
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", "host-token", json=pull).status_code == 400
    assert world.req("POST", f"/api/meetings/{code}/sfu/other/session", key).status_code == 404
    assert world.req("POST", f"/api/meetings/{code}/sfu/pub/tracks", key, json=push).status_code == 409  # no pub session yet


def test_inbox_requires_authentication(world):
    with world.client.websocket_connect("/ws/meetings-inbox") as ws:
        with pytest.raises(Exception):
            ws.receive_json()
    assert isinstance(FakeSFU(), FakeSFU) and _h("x") and Principal("guest")


def test_usage_can_be_reported_after_the_meeting_ends(world):
    _, key = world.member("late@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False)
    _join(world, code, "host-token")
    _join(world, code, key)
    _act(world, code, "end", "host-token")
    assert world.req("POST", f"/api/meetings/{code}/usage", key, json={"bytesIn": 5, "bytesOut": 6}).status_code == 200
    assert world.req("GET", f"/api/meetings/{code}", "host-token").json()["meeting"]["usage"]["egressBytes"] == 5
    _, stranger = world.member("who@meet.test")
    assert world.req("POST", f"/api/meetings/{code}/usage", stranger, json={"bytesIn": 5}).status_code == 403


# --------------------------------------------------------------------------- second device
def _device_join(world, code, key, device, mode=None, label="Phone", seat=None, **extra):
    headers = _h(key)
    if seat:
        headers["X-Hoja-Seat"] = seat
    body = {"deviceId": device, "deviceLabel": label, **({"mode": mode} if mode else {}), **extra}
    return world.client.post(f"/api/meetings/{code}/join", headers=headers, json=body)


def test_second_device_must_choose_and_same_device_just_rejoins(world):
    code, _ = _create(world, access="org", waitingRoom=False)
    first = _device_join(world, code, "host-token", "laptop", label="Computer")
    assert first.json()["state"] == "admitted"
    assert _device_join(world, code, "host-token", "laptop", label="Computer").status_code == 200  # a refresh
    blocked = _device_join(world, code, "host-token", "phone")
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["code"] == "already_joined" and body["device"] == "Computer" and body["canCompanion"] is True
    assert _device_join(world, code, "host-token", "phone", mode="nonsense").status_code == 400
    assert _join(world, code, "host-token").status_code == 409  # clients that send no device id are asked too
    _join(world, code, "host-token", deviceId="laptop")


def test_switch_moves_the_seat_and_tells_the_old_device(world):
    code, _ = _create(world, access="org", waitingRoom=False)
    first = _device_join(world, code, "host-token", "laptop", label="Computer").json()["participant"]
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", "host-token"]) as old:
        old.receive_json()
        moved = _device_join(world, code, "host-token", "phone", mode="switch")
        assert moved.status_code == 200 and moved.json()["participant"]["id"] == first["id"]
        assert old.receive_json() == {"t": "taken_over"}
    # the phone is now the device of record, so a refresh on it is not a conflict, but the laptop would be
    assert _device_join(world, code, "host-token", "phone", label="Phone").status_code == 200
    assert _device_join(world, code, "host-token", "laptop").status_code == 409


def test_companion_device_is_its_own_seat_without_moderation_powers(world):
    code, _ = _create(world, access="org", waitingRoom=True)  # the waiting room must not hold a second device back
    laptop = _device_join(world, code, "host-token", "laptop", label="Computer").json()["participant"]
    res = _device_join(world, code, "host-token", "phone", mode="companion", label="Phone")
    assert res.status_code == 200
    out = res.json()
    phone, seat = out["participant"], out["seatToken"]
    assert seat and out["state"] == "admitted"
    assert phone["id"] != laptop["id"] and phone["role"] == "attendee" and phone["name"].endswith("(Phone)")
    assert phone["deviceOf"] == laptop["id"] and phone["device"] == "Phone"
    # the phone acts as itself, the laptop stays the host
    assert _act(world, code, "hand", "host-token").status_code == 200  # (laptop seat)
    h = {**_h("host-token"), "X-Hoja-Seat": seat}
    assert world.client.post(f"/api/meetings/{code}/act", headers=h, json={"action": "media", "payload": {"audio": True}}).status_code == 200
    assert world.client.post(f"/api/meetings/{code}/act", headers=h, json={"action": "mute_all", "payload": {}}).status_code == 403
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", "host-token"]) as ws:
        roster = {p["id"]: p for p in ws.receive_json()["participants"]}
        assert roster[laptop["id"]]["role"] == "host" and roster[laptop["id"]]["audio"] is False
        assert roster[phone["id"]]["audio"] is True and roster[phone["id"]]["handRaised"] is False
    with world.client.websocket_connect(f"/ws/meetings/{code}", subprotocols=["bearer", "host-token", "seat", seat]) as ws:
        snap = ws.receive_json()
        assert snap["you"]["id"] == phone["id"]
    # the phone can publish mic through the SFU on its own session
    pub = world.client.post(f"/api/meetings/{code}/sfu/pub/session", headers=h, json={})
    assert pub.status_code == 200
    track = {"tracks": [{"location": "local", "mid": "0", "trackName": "mic-phone1234"}]}
    assert world.client.post(f"/api/meetings/{code}/sfu/pub/tracks", headers=h, json=track).status_code == 200
    # leaving the phone leaves the laptop in the call
    assert world.client.post(f"/api/meetings/{code}/act", headers=h, json={"action": "leave", "payload": {}}).status_code == 200
    assert world.req("GET", f"/api/meetings/{code}", "host-token").json()["meeting"]["participantCount"] == 1
    # without a seat header the API key still means the main seat
    assert _act(world, code, "hand", "host-token").status_code == 200


def test_companion_needs_the_main_device_in_first(world):
    code, _ = _create(world, access="org", waitingRoom=False)
    assert _device_join(world, code, "host-token", "phone", mode="companion").status_code == 400
    _device_join(world, code, "host-token", "laptop")
    again = _device_join(world, code, "host-token", "phone", mode="companion")
    seat = again.json()["seatToken"]
    # refreshing the phone with its seat is a rejoin, not a third device
    back = _device_join(world, code, "host-token", "phone", seat=seat)
    assert back.status_code == 200 and back.json()["participant"]["id"] == again.json()["participant"]["id"]


def test_ended_meeting_reports_who_came_and_for_how_long(world):
    _, key = world.member("recap@meet.test")
    code, _ = _create(world, access="org", waitingRoom=False, linkEnabled=True)
    _join(world, code, "host-token")
    _join(world, code, key)
    guest = _join(world, code, name="Gus Guest", passcode=None)
    assert guest.status_code in (200, 403)  # a passcode may be required; the recap must work either way
    _act(world, code, "end", "host-token")
    detail = world.req("GET", f"/api/meetings/{code}", key).json()["meeting"]
    assert detail["status"] == "ended" and detail["durationSeconds"] is not None
    recap = detail["recap"]
    assert recap["durationSeconds"] == detail["durationSeconds"] and recap["peopleCount"] >= 2
    assert {a["name"] for a in recap["attendees"]} >= {"Local User", "recap"}
    assert all(a["joinedAt"] and a["leftAt"] for a in recap["attendees"])
    # visitors with the link are told it ended instead of "invalid link"
    prev = world.req("GET", f"/api/meetings/{code}/preview").json()["meeting"]
    assert prev["status"] == "ended" and prev["durationSeconds"] == detail["durationSeconds"] and "attendees" not in prev
    assert _join(world, code, "host-token").status_code == 403  # and there is no rejoining
