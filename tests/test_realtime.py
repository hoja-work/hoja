"""Realtime feed: v2 frames with rows, visibility, per-user events, cursors, memory deltas."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from cliniar_server import sync
from cliniar_server.app import create_app
from cliniar_server.db import (
    api_key,
    inbox_notification,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    user,
)
from cliniar_server.store import Store

ADMIN = "rt-admin-token"
MEMBER = "rt-member-token"
CREATE = "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success issue { id } } }"
UPDATE = "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }"


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "rt.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="RT", org_url_key="rt", user_email="admin@rt.test",
                         token=ADMIN, demo_issues=False)
    ts = now_iso()
    with engine.begin() as conn:
        public_id = conn.execute(select(team.c.id).where(team.c.organization_id == seeded["org_id"])).scalar()
        private_id = new_id()
        conn.execute(team.insert().values(id=private_id, organization_id=seeded["org_id"], key="SEC",
                                          name="Secret", private=True, created_at=ts, updated_at=ts))
        seed_states_for_team(conn, seeded["org_id"], private_id)
        conn.execute(team_member.insert().values(team_id=private_id, user_id=seeded["user_id"]))
        member_id = new_id()
        conn.execute(user.insert().values(id=member_id, organization_id=seeded["org_id"], name="Member",
                                          email="member@rt.test", active=True, admin=False,
                                          created_at=ts, updated_at=ts))
        conn.execute(team_member.insert().values(team_id=public_id, user_id=member_id))
        conn.execute(api_key.insert().values(id=new_id(), token_hash=token_hash(MEMBER), label="m",
                                             user_id=member_id, organization_id=seeded["org_id"],
                                             created_at=ts))
    app = create_app(str(db_path), open_mode=False)
    return {"engine": engine, "app": app, "org": seeded["org_id"], "admin": seeded["user_id"],
            "member": member_id, "public": public_id, "private": private_id}


def _gql(client, query, variables=None, token=ADMIN):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert not body.get("errors"), body
    return body["data"]


def _create(client, team_id, title):
    return _gql(client, CREATE, {"i": {"title": title, "teamId": team_id}})["issueCreate"]["issue"]["id"]


def _ws(client, token, query="v=2"):
    return client.websocket_connect(f"/ws?{query}", headers={"Authorization": f"Bearer {token}"})


def _frames_until(ws, issue_id):
    """Read v2 frames until one carries issue_id's row; return every frame read."""
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if any(r[0] == issue_id for r in frame.get("issues", [])):
            return frames


def test_v2_frames_push_visible_rows_and_never_private_ones(world) -> None:
    with TestClient(world["app"]) as client:
        with _ws(client, MEMBER) as member_ws, _ws(client, ADMIN) as admin_ws:
            hello = member_ws.receive_json()
            assert hello["v"] == 2 and hello["events"] == [] and hello["prev"] == hello["seq"]
            admin_ws.receive_json()
            secret = _create(client, world["private"], "Secret plan")
            sentinel = _create(client, world["public"], "Public sentinel")

            member_frames = _frames_until(member_ws, sentinel)
            admin_frames = _frames_until(admin_ws, sentinel)

    member_ids = {e["entityId"] for f in member_frames for e in f["events"]}
    member_rows = {r[0] for f in member_frames for r in f["issues"]}
    member_removed = {i for f in member_frames for i in f["removed"]}
    assert secret not in member_ids | member_rows | member_removed
    assert {secret, sentinel} <= {r[0] for f in admin_frames for r in f["issues"]}
    frame = member_frames[-1]
    assert frame["full"] is True and frame["columns"] == list(sync.ISSUE_COLUMNS)
    row = dict(zip(frame["columns"], next(r for r in frame["issues"] if r[0] == sentinel), strict=True))
    assert row["title"] == "Public sentinel" and row["teamId"] == world["public"]
    # Frames chain: each frame's prev is the previous frame's seq.
    seqs = [hello["seq"]] + [f["seq"] for f in member_frames]
    assert [f["prev"] for f in member_frames] == seqs[:-1]


def test_notifications_reach_only_their_recipient(world) -> None:
    store = Store(world["engine"])
    with TestClient(world["app"]) as client:
        issue_id = _create(client, world["public"], "Watched")
        start = store.latest_change_seq(world["org"])
        with _ws(client, MEMBER) as member_ws:
            member_ws.receive_json()
            with world["engine"].begin() as conn:
                for recipient in (world["admin"], world["member"]):
                    conn.execute(inbox_notification.insert().values(
                        id=f"n-{recipient}", organization_id=world["org"], recipient_id=recipient,
                        issue_id=issue_id, kind="assigned", created_at=now_iso()))
            sentinel = _create(client, world["public"], "After notifications")
            frames = _frames_until(member_ws, sentinel)
        notes = [e for f in frames for e in f["events"] if e["entity"] == "notification"]
        assert [e["entityId"] for e in notes] == [f"n-{world['member']}"]
        assert notes[0]["recipientId"] == world["member"]

        replay = store.change_events_after(world["org"], start, viewer_id=world["member"])
        assert {e["entityId"] for e in replay if e["entity"] == "notification"} == {f"n-{world['member']}"}
        assert not [e for e in store.change_events_after(world["org"], start) if e["entity"] == "notification"]
        # The backlog replay applies the same rule.
        with _ws(client, MEMBER, f"v=2&after={start}") as ws:
            backlog = ws.receive_json()
        assert {e["entityId"] for e in backlog["events"] if e["entity"] == "notification"} == {
            f"n-{world['member']}"}


def test_connect_without_cursor_does_not_replay_history(world) -> None:
    with TestClient(world["app"]) as client:
        for n in range(5):
            _create(client, world["public"], f"Old {n}")
        with _ws(client, ADMIN) as ws:
            hello = ws.receive_json()
            assert hello["events"] == [] and hello["issues"] == []
            fresh = _create(client, world["public"], "Fresh")
            frames = _frames_until(ws, fresh)
        titles = {r[2] for f in frames for r in f["issues"]}
        assert titles == {"Fresh"}
        # A v1 client with a cursor still gets the exact legacy array replay.
        with _ws(client, ADMIN, f"after={hello['seq'] - 1}") as ws:
            legacy = ws.receive_json()
        assert isinstance(legacy, list) and legacy[0]["seq"] == hello["seq"]


def test_memory_delta_matches_database_delta(world) -> None:
    store = Store(world["engine"])
    with TestClient(world["app"]) as client:
        with _ws(client, MEMBER) as ws:  # a watcher makes the hub fetch rows for this org
            ws.receive_json()
            base = store.latest_change_seq(world["org"])
            a = _create(client, world["public"], "A")
            secret = _create(client, world["private"], "S")
            _gql(client, UPDATE, {"id": a, "i": {"title": "A2"}})
            _frames_until(ws, a)
            while True:  # wait until the rename has been broadcast too
                frame = ws.receive_json()
                if any(r[2] == "A2" for r in frame["issues"]):
                    break
            hub = world["app"].state.realtime
            for uid, token in ((world["member"], MEMBER), (world["admin"], ADMIN)):
                memory = client.portal.call(hub.delta, world["org"], uid, base)
                assert memory is not None and not memory["reload"], memory
                db = sync.build_delta(store, world["org"], uid, base)
                assert memory["seq"] == db["seq"]
                assert sorted(memory["issues"]) == sorted(db["issues"])
                assert sorted(memory["removed"]) == sorted(db["removed"])
                served = client.get(f"/sync/delta?after={base}", headers={"Authorization": f"Bearer {token}"})
                ids = {r[0] for r in served.json()["issues"]}
                assert (secret in ids) == (uid == world["admin"])


def test_visibility_map_matches_per_viewer_queries(world) -> None:
    store = Store(world["engine"])
    uids = [world["admin"], world["member"], "nobody"]
    mapped = store.visibility_map(world["org"], uids)
    for uid in uids[:2]:
        assert mapped[uid] == store.visible_team_ids(world["org"], uid)
    assert mapped["nobody"] == frozenset()


def test_bootstrap_derived_from_previous_snapshot_matches_full_read(world) -> None:
    store = Store(world["engine"])
    with TestClient(world["app"]) as client:
        ids = [_create(client, world["public"], f"Issue {n}") for n in range(6)]
        secret = _create(client, world["private"], "Hidden")
        with _ws(client, MEMBER) as ws:  # a watcher makes the feed carry rows
            ws.receive_json()
            first = client.get("/sync/bootstrap", headers={"Authorization": f"Bearer {MEMBER}"}).json()
            _gql(client, UPDATE, {"id": ids[0], "i": {"title": "Renamed"}})
            _gql(client, "mutation($id: String!) { issueArchive(id: $id) { success } }", {"id": ids[1]})
            _gql(client, UPDATE, {"id": secret, "i": {"title": "Hidden renamed"}})
            fresh = _create(client, world["public"], "Brand new")
            _frames_until(ws, fresh)
            second = client.get("/sync/bootstrap", headers={"Authorization": f"Bearer {MEMBER}"}).json()
        assert second["seq"] > first["seq"]
        assert world["app"].state.realtime.stats["snapshot_derived"] >= 1
        expected = sync.bootstrap_issues(store, world["org"], world["member"], second["closedCutoff"])
        assert sorted(second["issues"]) == sorted(expected)
        titles = {r[2] for r in second["issues"]}
        assert "Renamed" in titles and "Brand new" in titles and "Issue 1" not in titles
        assert not titles & {"Hidden", "Hidden renamed"}
