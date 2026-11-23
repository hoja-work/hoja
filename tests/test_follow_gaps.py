"""Project members follow their project, and every cycle is announced on time.

- ``memberIds`` on project create and update: added people follow the project and
  hear "added you to the project"; removing someone leaves their subscription alone.
- ``cycle_events.sweep_once`` announces a cycle's start and end from its own dates,
  for teams with hand-made cycles too, exactly once even when the cadence rollover
  reaches the same edge; revision 80 stamps history so a deploy announces nothing old.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select, text
from starlette.testclient import TestClient

from hoja_server import cycle_events, notifications
from hoja_server import db as db_module
from hoja_server.app import create_app
from hoja_server.db import (
    cycle,
    cycle_subscriber,
    inbox_notification,
    make_engine,
    migrate,
    new_id,
    project_member,
    project_subscriber,
    seed_tenant,
    team,
    team_member,
    user,
    workflow_state,
)
from hoja_server.store import Store
from hoja_server.writer import Writer


@pytest.fixture()
def ws(tmp_path):
    db_path = tmp_path / "gaps.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Gaps", org_url_key="gaps",
                         user_email="owner@example.test", token="owner-token", demo_issues=False)
    store = Store(engine)
    people = {"owner": (seeded["user_id"], seeded["token"])}
    for handle in ("ana", "ben"):
        member, token = store.invite_member(seeded["org_id"], handle.title(), f"{handle}@example.test")
        people[handle] = (member["id"], token)
    with engine.begin() as conn:
        for handle in ("ana", "ben"):
            conn.execute(team_member.insert().values(team_id=seeded["team_id"], user_id=people[handle][0]))
        states = {row.type: row.id for row in conn.execute(select(
            workflow_state.c.id, workflow_state.c.type).where(
            workflow_state.c.team_id == seeded["team_id"]))}
    return {"engine": engine, "db_path": str(db_path), "org": seeded["org_id"], "team": seeded["team_id"],
            "writer": Writer(engine), "people": people, "states": states, "tmp": tmp_path}


def uid(ws, handle):
    return ws["people"][handle][0]


def rows(ws, handle, prefix=""):
    with ws["engine"].connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(inbox_notification).where(
            inbox_notification.c.recipient_id == uid(ws, handle)).order_by(
            inbox_notification.c.created_at)) if r.kind.startswith(prefix)]


def members(ws, pid):
    with ws["engine"].connect() as conn:
        return set(conn.execute(select(project_member.c.user_id).where(
            project_member.c.project_id == pid)).scalars())


def followers(ws, pid):
    with ws["engine"].connect() as conn:
        return set(conn.execute(select(project_subscriber.c.user_id).where(
            project_subscriber.c.project_id == pid)).scalars())


# ------------------------------------------------------------- project members

def test_members_on_create_follow_and_are_told(ws):
    pid = ws["writer"].project_create(ws["org"], uid(ws, "owner"), {
        "name": "Atlas", "teamIds": [ws["team"]], "memberIds": [uid(ws, "ana")]})
    assert members(ws, pid) == {uid(ws, "owner"), uid(ws, "ana")}
    assert {uid(ws, "owner"), uid(ws, "ana")} <= followers(ws, pid)
    assert [r["kind"] for r in rows(ws, "ana", "project")] == ["projectMemberAdded"]
    assert rows(ws, "owner", "project") == []  # the creator is not told about themself


def test_member_updates_add_follow_and_removal_keeps_subscription(ws):
    w, org = ws["writer"], ws["org"]
    pid = w.project_create(org, uid(ws, "owner"), {"name": "Atlas", "teamIds": [ws["team"]]})
    assert w.project_update(org, uid(ws, "owner"), pid, {"memberIds": [uid(ws, "owner"), uid(ws, "ben")]})
    assert members(ws, pid) == {uid(ws, "owner"), uid(ws, "ben")}
    assert uid(ws, "ben") in followers(ws, pid)
    assert [r["kind"] for r in rows(ws, "ben", "project")] == ["projectMemberAdded"]
    # Re-sending the same list tells nobody again.
    w.project_update(org, uid(ws, "owner"), pid, {"memberIds": [uid(ws, "owner"), uid(ws, "ben")]})
    assert len(rows(ws, "ben", "projectMemberAdded")) == 1
    # Removing a member leaves the subscription: following is the person's own choice.
    w.project_update(org, uid(ws, "owner"), pid, {"memberIds": [uid(ws, "owner")]})
    assert members(ws, pid) == {uid(ws, "owner")}
    assert uid(ws, "ben") in followers(ws, pid)


def test_member_ids_must_be_active_people_in_the_org(ws):
    from hoja_server.writer import InvalidReferenceError

    with pytest.raises(InvalidReferenceError):
        ws["writer"].project_create(ws["org"], uid(ws, "owner"), {
            "name": "Atlas", "teamIds": [ws["team"]], "memberIds": ["no-such-user"]})


def test_member_ids_over_graphql(ws):
    with TestClient(create_app(ws["db_path"])) as client:
        auth = {"Authorization": ws["people"]["owner"][1]}
        created = client.post("/graphql", headers=auth, json={
            "query": "mutation($i:ProjectCreateInput!){projectCreate(input:$i){success project{id members{nodes{id}}}}}",
            "variables": {"i": {"name": "Atlas", "teamIds": [ws["team"]], "memberIds": [uid(ws, "ana")]}},
        }).json()["data"]["projectCreate"]
        assert {m["id"] for m in created["project"]["members"]["nodes"]} == {uid(ws, "owner"), uid(ws, "ana")}
        pid = created["project"]["id"]
        updated = client.post("/graphql", headers=auth, json={
            "query": "mutation($id:String!,$i:ProjectUpdateInput!){projectUpdate(id:$id,input:$i){success project{members{nodes{id}}}}}",
            "variables": {"id": pid, "i": {"memberIds": [uid(ws, "ben")]}},
        }).json()["data"]["projectUpdate"]
        assert updated["success"] is True
        assert [m["id"] for m in updated["project"]["members"]["nodes"]] == [uid(ws, "ben")]
        prefs = client.post("/graphql", headers={"Authorization": ws["people"]["ben"][1]}, json={
            "query": "{myNotificationPreferences{projectMemberAdded}}"}).json()
        assert prefs["data"]["myNotificationPreferences"]["projectMemberAdded"] is True


# ---------------------------------------------------------------- cycle events

def iso(dt):
    return dt.isoformat()


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def manual_cycle(ws, starts, ends, number=1, **extra):
    cid = new_id()
    with ws["engine"].begin() as conn:
        conn.execute(cycle.insert().values(
            id=cid, organization_id=ws["org"], team_id=ws["team"], number=number,
            name=f"Cycle {number}", starts_at=starts, ends_at=ends, progress=0,
            created_at=iso(NOW), updated_at=iso(NOW), **extra))
        conn.execute(cycle_subscriber.insert().values(cycle_id=cid, user_id=uid(ws, "ana")))
    return cid


def test_sweep_announces_hand_made_cycle_start_and_end_once(ws):
    cid = manual_cycle(ws, iso(NOW + timedelta(hours=1)), iso(NOW + timedelta(days=7)))
    for state in ("completed", "started"):
        ws["writer"].issue_create(ws["org"], uid(ws, "owner"), {
            "teamId": ws["team"], "title": state, "cycleId": cid, "stateId": ws["states"][state]})
    assert cycle_events.sweep_once(ws["engine"], iso(NOW))["started"] == 0  # not yet
    assert cycle_events.sweep_once(ws["engine"], iso(NOW + timedelta(hours=2)))["started"] == 1
    assert cycle_events.sweep_once(ws["engine"], iso(NOW + timedelta(hours=3)))["started"] == 0
    assert [r["kind"] for r in rows(ws, "ana", "cycle")] == ["cycleStarted"]
    assert cycle_events.sweep_once(ws["engine"], iso(NOW + timedelta(days=8)))["ended"] == 1
    assert cycle_events.sweep_once(ws["engine"], iso(NOW + timedelta(days=9)))["ended"] == 0
    ended = rows(ws, "ana", "cycleCompleted")
    assert len(ended) == 1
    import json
    data = json.loads(ended[0]["data"])
    assert (data["completed"], data["total"], data["carriedOver"], data["unfinished"]) == (1, 2, 0, 1)


def test_cycle_created_in_the_past_is_not_announced_as_started(ws):
    manual_cycle(ws, iso(NOW - timedelta(days=14)), iso(NOW - timedelta(days=7)))
    counts = cycle_events.sweep_once(ws["engine"], iso(NOW))
    assert counts["started"] == 0 and counts["skipped"] == 1
    assert [r["kind"] for r in rows(ws, "ana", "cycle")] == ["cycleCompleted"]


@pytest.mark.parametrize("starts", ["2026-11-19", "2026-11-19T11:00:00Z", "2026-11-19T13:00:00+02:00"])
def test_sweep_reads_dates_however_they_were_stored(ws, starts):
    manual_cycle(ws, starts, "2026-11-26T00:00:00Z")
    assert cycle_events.sweep_once(ws["engine"], iso(NOW))["started"] == 1


def test_future_offset_is_not_early(ws):
    # 13:00+00:00 is an hour after NOW even though "13:00-05:00" sorts later as text.
    manual_cycle(ws, "2026-11-19T08:00:00-05:00", "2026-11-26T00:00:00Z")
    assert cycle_events.sweep_once(ws["engine"], iso(NOW))["started"] == 0
    assert cycle_events.sweep_once(ws["engine"], iso(NOW + timedelta(hours=2)))["started"] == 1


def test_rollover_and_sweep_never_announce_the_same_edge_twice(ws):
    cid = manual_cycle(ws, iso(NOW - timedelta(days=1)), iso(NOW + timedelta(days=6)))
    with ws["engine"].begin() as conn:
        row = dict(conn.execute(select(cycle).where(cycle.c.id == cid)).mappings().one())
        assert notifications.on_cycle_started(conn, ws["org"], row, "ENG", iso(NOW)) is True
        assert notifications.on_cycle_started(conn, ws["org"], row, "ENG", iso(NOW)) is False
    assert cycle_events.sweep_once(ws["engine"], iso(NOW))["started"] == 0
    assert len(rows(ws, "ana", "cycleStarted")) == 1


def test_archived_cycles_are_ignored(ws):
    manual_cycle(ws, iso(NOW - timedelta(hours=1)), iso(NOW + timedelta(days=6)),
                 archived_at=iso(NOW))
    assert cycle_events.sweep_once(ws["engine"], iso(NOW)) == {"started": 0, "ended": 0, "skipped": 0}


def test_revision_80_stamps_history_so_a_deploy_announces_nothing_old(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "upgrade.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Up", org_url_key="up", user_email="u@example.test",
                         token="t", demo_issues=False)
    now = datetime.now(timezone.utc)
    past, current, future = new_id(), new_id(), new_id()
    with engine.begin() as conn:
        for n, (cid, starts, ends) in enumerate((
                (past, now - timedelta(days=14), now - timedelta(days=7)),
                (current, now - timedelta(days=1), now + timedelta(days=6)),
                (future, now + timedelta(days=6), now + timedelta(days=13))), start=1):
            conn.execute(cycle.insert().values(
                id=cid, organization_id=seeded["org_id"], team_id=seeded["team_id"], number=n,
                starts_at=iso(starts), ends_at=iso(ends), created_at=iso(now), updated_at=iso(now)))
        # Pretend this database predates revision 80.
        conn.execute(cycle.update().values(start_notified_at=None, end_notified_at=None))
        conn.execute(text("DELETE FROM schema_revision WHERE revision >= 80"))
    migrate(engine)
    with engine.connect() as conn:
        stamps = {r.id: (r.start_notified_at is not None, r.end_notified_at is not None)
                  for r in conn.execute(select(cycle))}
        assert max(conn.execute(text("SELECT revision FROM schema_revision")).scalars()) == db_module.CURRENT_SCHEMA_REVISION
    assert stamps == {past: (True, True), current: (True, False), future: (False, False)}
    assert cycle_events.sweep_once(engine) == {"started": 0, "ended": 0, "skipped": 0}


def test_sweeper_runs_from_the_app_lifespan_and_can_be_turned_off(ws, monkeypatch):
    started = []

    async def fake_run_forever(engine, interval_s=60):
        started.append(engine)

    monkeypatch.setattr(cycle_events, "run_forever", fake_run_forever)
    with TestClient(create_app(ws["db_path"])):
        pass
    assert len(started) == 1
    monkeypatch.setenv("HOJA_CYCLE_EVENTS", "0")
    with TestClient(create_app(ws["db_path"])):
        pass
    assert len(started) == 1


def test_team_column_is_used_for_the_team_key(ws):
    with ws["engine"].connect() as conn:
        key = conn.execute(select(team.c.key).where(team.c.id == ws["team"])).scalar()
    manual_cycle(ws, iso(NOW - timedelta(hours=1)), iso(NOW + timedelta(days=6)))
    cycle_events.sweep_once(ws["engine"], iso(NOW))
    import json
    [row] = rows(ws, "ana", "cycleStarted")
    assert json.loads(row["data"])["teamKey"] == key
