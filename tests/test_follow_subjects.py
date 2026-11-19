"""Following projects, initiatives and cycles: who hears about what, how a person
follows or stops following, and how the inbox treats a notification whose subject
is not a task."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import inspect, select, text
from starlette.testclient import TestClient

from hoja_server import db as db_module
from hoja_server.app import create_app
from hoja_server.db import (
    cycle,
    cycle_subscriber,
    inbox_notification,
    initiative_subscriber,
    issue,
    make_engine,
    migrate,
    project_subscriber,
    seed_tenant,
    team_member,
    user,
    workflow_state,
)
from hoja_server.store import Store
from hoja_server.writer import Writer


@pytest.fixture()
def ws(tmp_path):
    db_path = tmp_path / "follow.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Follow", org_url_key="follow",
                         user_email="owner@example.test", token="owner-token", demo_issues=False)
    store = Store(engine)
    people = {"owner": (seeded["user_id"], seeded["token"])}
    for handle in ("ana", "ben", "cy"):
        member, token = store.invite_member(seeded["org_id"], handle.title(), f"{handle}@example.test")
        people[handle] = (member["id"], token)
    with engine.begin() as conn:
        conn.execute(user.update().where(user.c.id == people["ana"][0]).values(display_name="ana"))
        for handle in ("ana", "ben", "cy"):
            conn.execute(team_member.insert().values(team_id=seeded["team_id"], user_id=people[handle][0]))
        states = {row.type: row.id for row in conn.execute(select(
            workflow_state.c.id, workflow_state.c.type).where(
            workflow_state.c.team_id == seeded["team_id"]))}
    return {"engine": engine, "db_path": db_path, "org": seeded["org_id"], "team": seeded["team_id"],
            "store": store, "writer": Writer(engine), "people": people, "states": states}


def uid(ws, handle):
    return ws["people"][handle][0]


def rows_for(ws, handle):
    with ws["engine"].connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(inbox_notification).where(
            inbox_notification.c.recipient_id == uid(ws, handle)).order_by(
            inbox_notification.c.created_at, inbox_notification.c.id))]


def kinds(ws, handle):
    return [r["kind"] for r in rows_for(ws, handle)]


def subscribers(ws, table, column, subject_id):
    with ws["engine"].connect() as conn:
        return set(conn.execute(select(table.c.user_id).where(table.c[column] == subject_id)).scalars())


def handles(ws, ids):
    back = {v[0]: k for k, v in ws["people"].items()}
    return {back[i] for i in ids}


def gql(ws, handle, query, variables=None):
    client = TestClient(create_app(str(ws["db_path"])))
    response = client.post("/graphql", json={"query": query, "variables": variables or {}},
                           headers={"Authorization": ws["people"][handle][1]})
    body = response.json()
    assert "errors" not in body, body
    return body["data"]


def project(ws, actor="owner", **inp):
    return ws["writer"].project_create(ws["org"], uid(ws, actor), {"name": inp.pop("name", "Atlas"), **inp})


def initiative(ws, actor="owner", **inp):
    return ws["writer"].initiative_create(ws["org"], uid(ws, actor), {
        "name": inp.pop("name", "Grow"), "status": "active", **inp})


# ------------------------------------------------------------------ projects

def test_project_create_subscribes_creator_and_lead_and_tells_the_lead(ws):
    pid = project(ws, leadId=uid(ws, "ana"))
    assert handles(ws, subscribers(ws, project_subscriber, "project_id", pid)) == {"owner", "ana"}
    [row] = rows_for(ws, "ana")
    assert row["kind"] == "projectLeadAssigned" and row["project_id"] == pid and row["issue_id"] is None
    assert kinds(ws, "owner") == []


def test_project_update_post_reaches_subscribers_and_mentions_directly(ws):
    pid = project(ws, leadId=uid(ws, "ana"))
    assert ws["writer"].subject_subscribe(ws["org"], uid(ws, "ben"), "project", pid, True)
    upd = ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {
        "body": "On track. @cy can you review the **launch** plan?", "health": "onTrack"})
    ana = rows_for(ws, "ana")[-1]
    assert ana["kind"] == "projectUpdatePosted" and ana["project_update_id"] == upd
    import json
    assert json.loads(ana["data"]) == {"updateId": upd, "health": "onTrack",
                                       "snippet": "On track. @cy can you review the launch plan?"}
    assert kinds(ws, "ben") == ["projectUpdatePosted"]
    assert kinds(ws, "cy") == ["projectMention"]  # not subscribed, reached directly
    assert "cy" in handles(ws, subscribers(ws, project_subscriber, "project_id", pid))
    assert kinds(ws, "owner") == []


def test_project_reply_reaches_author_and_earlier_repliers_only(ws):
    pid = project(ws)
    ws["writer"].subject_subscribe(ws["org"], uid(ws, "cy"), "project", pid, True)
    upd = ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {"body": "Week 1"})
    first = ws["writer"].comment_create(ws["org"], uid(ws, "ana"), {"projectUpdateId": upd, "body": "Nice"})
    ws["writer"].comment_create(ws["org"], uid(ws, "ben"), {"projectUpdateId": upd, "body": "Agreed @ana"})
    owner_rows = [r for r in rows_for(ws, "owner") if r["kind"] == "projectUpdateReply"]
    assert len(owner_rows) == 2 and owner_rows[0]["comment_id"] == first
    assert owner_rows[0]["project_update_id"] == upd and owner_rows[0]["project_id"] == pid
    # ana replied first, so ben's reply mentions her: a mention, not a second reply row.
    assert [k for k in kinds(ws, "ana") if k != "projectUpdatePosted"] == ["projectMention"]
    # cy follows the project but never joined the thread: the post, not the replies.
    assert kinds(ws, "cy") == ["projectUpdatePosted"]
    assert {"ana", "ben"} <= handles(ws, subscribers(ws, project_subscriber, "project_id", pid))


def test_project_state_target_and_lead_changes(ws):
    import json
    pid = project(ws)
    ws["writer"].subject_subscribe(ws["org"], uid(ws, "ben"), "project", pid, True)
    statuses = ws["store"].project_statuses(ws["org"]) if hasattr(ws["store"], "project_statuses") else None
    target = next((s["name"] for s in (statuses or []) if s["name"].lower() == "completed"), None)
    inp = {"targetDate": "2026-12-01", "leadId": uid(ws, "ana")}
    if target:
        inp["state"] = target
    ws["writer"].project_update(ws["org"], uid(ws, "owner"), pid, inp)
    assert kinds(ws, "ana") == ["projectLeadAssigned"]
    [ben] = rows_for(ws, "ben")
    changes = json.loads(ben["data"])["changes"]
    assert ben["kind"] == "projectUpdated"
    assert changes["targetDate"] == {"from": None, "to": "2026-12-01"}
    assert changes["lead"] == {"from": None, "to": {"id": uid(ws, "ana"), "name": "Ana"}}
    # Renaming and redescribing are silent.
    ws["writer"].project_update(ws["org"], uid(ws, "owner"), pid, {"name": "Atlas 2", "description": "x"})
    assert len(rows_for(ws, "ben")) == 1


def test_unfollowing_a_project_stops_posts_but_not_mentions_or_lead(ws):
    pid = project(ws)
    ws["writer"].subject_subscribe(ws["org"], uid(ws, "ana"), "project", pid, True)
    ws["writer"].subject_subscribe(ws["org"], uid(ws, "ana"), "project", pid, False)
    ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {"body": "quiet"})
    assert kinds(ws, "ana") == []
    ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {"body": "@ana loud"})
    assert kinds(ws, "ana") == ["projectMention"]
    ws["writer"].subject_subscribe(ws["org"], uid(ws, "ana"), "project", pid, False)
    ws["writer"].project_update(ws["org"], uid(ws, "owner"), pid, {"leadId": uid(ws, "ana")})
    assert kinds(ws, "ana") == ["projectMention", "projectLeadAssigned"]


def test_deleting_a_project_update_removes_its_notifications(ws):
    pid = project(ws, leadId=uid(ws, "ana"))
    upd = ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {"body": "temp"})
    assert "projectUpdatePosted" in kinds(ws, "ana")
    assert ws["writer"].project_update_delete(ws["org"], uid(ws, "owner"), upd)
    assert kinds(ws, "ana") == ["projectLeadAssigned"]


# --------------------------------------------------------------- initiatives

def test_initiative_owner_updates_and_posts(ws):
    import json
    iid = initiative(ws, ownerId=uid(ws, "ana"))
    assert kinds(ws, "ana") == ["initiativeOwnerAssigned"]
    assert handles(ws, subscribers(ws, initiative_subscriber, "initiative_id", iid)) == {"owner", "ana"}
    ws["writer"].subject_subscribe(ws["org"], uid(ws, "ben"), "initiative", iid, True)
    ws["writer"].initiative_update(ws["org"], uid(ws, "owner"), iid, {"status": "completed", "ownerId": uid(ws, "cy")})
    assert kinds(ws, "cy") == ["initiativeOwnerAssigned"]
    [ben] = rows_for(ws, "ben")
    changes = json.loads(ben["data"])["changes"]
    assert ben["kind"] == "initiativeUpdated" and ben["initiative_id"] == iid
    assert changes["status"] == {"from": "active", "to": "completed"}
    assert changes["owner"]["to"]["id"] == uid(ws, "cy")
    upd = ws["writer"].initiative_update_create(ws["org"], iid, uid(ws, "owner"), {"body": "Shipped", "health": "onTrack"})
    assert kinds(ws, "ben")[-1] == "initiativeUpdatePosted"
    assert rows_for(ws, "ana")[-1]["initiative_update_id"] == upd
    ws["writer"].initiative_update(ws["org"], uid(ws, "owner"), iid, {"name": "Grow more"})
    assert kinds(ws, "ben")[-1] == "initiativeUpdatePosted"  # a rename is silent


# -------------------------------------------------------------------- cycles

def test_cycle_completion_and_start_reach_cycle_subscribers(ws):
    import json
    w, org, team_id = ws["writer"], ws["org"], ws["team"]
    t0 = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)  # a Monday
    w.enable_team_cadence(org, team_id, t0.weekday(), now=t0)
    with ws["engine"].connect() as conn:
        cycles = [dict(r._mapping) for r in conn.execute(select(cycle).where(
            cycle.c.team_id == team_id).order_by(cycle.c.number))]
    first, second = cycles[0], cycles[1]
    assert w.subject_subscribe(org, uid(ws, "ana"), "cycle", first["id"], True)
    assert w.subject_subscribe(org, uid(ws, "ben"), "cycle", second["id"], True)
    w.issue_create(org, uid(ws, "owner"), {"teamId": team_id, "title": "done", "cycleId": first["id"],
                                          "stateId": ws["states"]["completed"]})
    w.issue_create(org, uid(ws, "owner"), {"teamId": team_id, "title": "open", "cycleId": first["id"],
                                          "stateId": ws["states"]["started"]})
    w.reconcile_team_cadence(org, team_id, now=t0 + timedelta(days=8))
    [ana] = [r for r in rows_for(ws, "ana") if r["kind"].startswith("cycle")]
    assert ana["kind"] == "cycleCompleted" and ana["cycle_id"] == first["id"] and ana["actor_id"] is None
    data = json.loads(ana["data"])
    assert (data["completed"], data["total"], data["carriedOver"]) == (1, 2, 1)
    assert data["number"] == first["number"]
    [ben] = [r for r in rows_for(ws, "ben") if r["kind"].startswith("cycle")]
    assert ben["kind"] == "cycleStarted" and ben["cycle_id"] == second["id"]
    # Unfollowing a cycle is the only way out of its notifications.
    assert w.subject_subscribe(org, uid(ws, "ben"), "cycle", second["id"], False)
    assert subscribers(ws, cycle_subscriber, "cycle_id", second["id"]) == set()


# ---------------------------------------------------------------- GraphQL

def test_follow_through_graphql_and_viewer_subscribed(ws):
    pid = project(ws)
    iid = initiative(ws)
    q = "query($p:String!,$i:String!){project(id:$p){viewerSubscribed subscribers{nodes{id}}} initiative(id:$i){viewerSubscribed}}"
    assert gql(ws, "ana", q, {"p": pid, "i": iid}) == {
        "project": {"viewerSubscribed": False, "subscribers": {"nodes": [{"id": uid(ws, "owner")}]}},
        "initiative": {"viewerSubscribed": False}}
    assert gql(ws, "ana", "mutation($p:String!,$i:String!){projectSubscribe(id:$p) initiativeSubscribe(id:$i)}",
               {"p": pid, "i": iid}) == {"projectSubscribe": True, "initiativeSubscribe": True}
    got = gql(ws, "ana", q, {"p": pid, "i": iid})
    assert got["project"]["viewerSubscribed"] and got["initiative"]["viewerSubscribed"]
    assert gql(ws, "ana", "mutation($p:String!){projectUnsubscribe(id:$p)}", {"p": pid})["projectUnsubscribe"]
    assert not gql(ws, "ana", q, {"p": pid, "i": iid})["project"]["viewerSubscribed"]
    assert gql(ws, "ana", "mutation{projectSubscribe(id:\"nope\")}") == {"projectSubscribe": False}


def test_inbox_lists_project_rows_with_subject_fields_and_acts_per_subject(ws):
    pid = project(ws, leadId=uid(ws, "ana"))
    upd = ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {"body": "Week 2"})
    task = ws["writer"].issue_create(ws["org"], uid(ws, "owner"), {
        "teamId": ws["team"], "title": "t", "assigneeId": uid(ws, "ana")})
    q = ("{inboxUnreadCount inboxNotifications{nodes{kind subjectType subjectId issue{id} "
         "project{id name} projectUpdate{id body} initiative{id} cycle{id}}}}")
    data = gql(ws, "ana", q)
    assert data["inboxUnreadCount"] == 3
    by_kind = {n["kind"]: n for n in data["inboxNotifications"]["nodes"]}
    posted = by_kind["projectUpdatePosted"]
    assert posted["subjectType"] == "project" and posted["subjectId"] == pid
    assert posted["issue"] is None and posted["project"]["name"] == "Atlas"
    assert posted["projectUpdate"] == {"id": upd, "body": "Week 2"}
    assert by_kind["issueAssigned"]["subjectType"] == "issue" and by_kind["issueAssigned"]["subjectId"] == task
    # Read, snooze and archive act on every row about the project, and only on those.
    assert gql(ws, "ana", "mutation($s:String!){notificationsMarkReadForSubject(subjectId:$s)}",
               {"s": pid})["notificationsMarkReadForSubject"]
    assert gql(ws, "ana", "{inboxUnreadCount}")["inboxUnreadCount"] == 1
    until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert gql(ws, "ana", "mutation($s:String!,$u:DateTime){notificationsSnoozeForSubject(subjectId:$s,until:$u)}",
               {"s": pid, "u": until})["notificationsSnoozeForSubject"]
    assert {n["subjectType"] for n in gql(ws, "ana", q)["inboxNotifications"]["nodes"]} == {"issue"}
    assert gql(ws, "ana", "mutation($s:String!){notificationsSnoozeForSubject(subjectId:$s,until:null)}",
               {"s": pid})["notificationsSnoozeForSubject"]
    assert gql(ws, "ana", "mutation($s:String!){notificationsArchiveForSubject(subjectId:$s)}",
               {"s": pid})["notificationsArchiveForSubject"]
    assert {n["subjectType"] for n in gql(ws, "ana", q)["inboxNotifications"]["nodes"]} == {"issue"}
    assert gql(ws, "ana", "mutation{notificationsArchiveForSubject(subjectId:\"missing\")}") == {
        "notificationsArchiveForSubject": False}
    # The issue-scoped mutation still works by identifier.
    ident = gql(ws, "ana", "query($id:String!){issue(id:$id){identifier}}", {"id": task})["issue"]["identifier"]
    assert gql(ws, "ana", "mutation($s:String!){notificationsArchiveForSubject(subjectId:$s)}",
               {"s": ident})["notificationsArchiveForSubject"]
    assert gql(ws, "ana", q)["inboxNotifications"]["nodes"] == []


def test_notification_preferences_cover_the_new_kinds(ws):
    from hoja_server.notifications import KINDS
    fields = " ".join(KINDS)
    got = gql(ws, "ana", f"mutation{{notificationPreferenceSet(kind:\"projectUpdatePosted\",enabled:false){{success {fields}}}}}")
    prefs = got["notificationPreferenceSet"]
    assert prefs["success"] and prefs["projectUpdatePosted"] is False and prefs["cycleCompleted"] is True
    pid = project(ws, leadId=uid(ws, "ana"))
    ws["writer"].project_update_create(ws["org"], pid, uid(ws, "owner"), {"body": "hidden"})
    kinds_seen = [n["kind"] for n in gql(ws, "ana", "{inboxNotifications{nodes{kind}}}")["inboxNotifications"]["nodes"]]
    assert kinds_seen == ["projectLeadAssigned"]


def test_project_rows_from_another_workspace_or_recipient_stay_private(ws):
    pid = project(ws, leadId=uid(ws, "ana"))
    other = seed_tenant(ws["engine"], org_name="Other", org_url_key="other",
                        user_email="o@example.test", token="other-token", demo_issues=False)
    client = TestClient(create_app(str(ws["db_path"])))

    def ask(token, query, variables=None):
        return client.post("/graphql", json={"query": query, "variables": variables or {}},
                           headers={"Authorization": token}).json()

    assert ask("other-token", "{inboxNotifications{nodes{id}}}")["data"]["inboxNotifications"]["nodes"] == []
    assert ask("other-token", "mutation($p:String!){projectSubscribe(id:$p)}", {"p": pid})["data"] == {
        "projectSubscribe": False}
    assert ask("other-token", "mutation($s:String!){notificationsArchiveForSubject(subjectId:$s)}",
               {"s": pid})["data"] == {"notificationsArchiveForSubject": False}
    # Ben is in the workspace but the rows are Ana's: his archive touches nothing of hers.
    assert gql(ws, "ben", "mutation($s:String!){notificationsArchiveForSubject(subjectId:$s)}", {"s": pid})
    assert [r["archived_at"] for r in rows_for(ws, "ana")] == [None]
    assert other["org_id"] != ws["org"]


# --------------------------------------------------------------- migration

_REV77_TABLE = """CREATE TABLE inbox_notification (
    id VARCHAR NOT NULL PRIMARY KEY,
    organization_id VARCHAR NOT NULL REFERENCES organization (id),
    recipient_id VARCHAR NOT NULL REFERENCES user (id),
    issue_id VARCHAR NOT NULL REFERENCES issue (id),
    actor_id VARCHAR REFERENCES user (id),
    kind VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    read_at VARCHAR,
    archived_at VARCHAR,
    data TEXT,
    comment_id VARCHAR,
    snoozed_until VARCHAR)"""


def test_revision_78_rebuilds_a_revision_77_sqlite_table_and_keeps_its_triggers(ws):
    engine = ws["engine"]
    task = ws["writer"].issue_create(ws["org"], uid(ws, "owner"), {
        "teamId": ws["team"], "title": "kept", "assigneeId": uid(ws, "ana")})
    [kept] = rows_for(ws, "ana")
    with engine.begin() as conn:
        rows = [dict(r._mapping) for r in conn.execute(select(inbox_notification))]
        conn.exec_driver_sql("DROP TABLE inbox_notification")
        conn.exec_driver_sql(_REV77_TABLE)
        conn.exec_driver_sql("CREATE INDEX ix_inbox_notification_recipient_read ON inbox_notification (recipient_id, read_at)")
        old_cols = [c for c in rows[0] if c not in db_module._INBOX_SUBJECT_COLUMNS]
        for row in rows:
            conn.execute(text(f"INSERT INTO inbox_notification ({', '.join(old_cols)}) VALUES "
                              f"({', '.join(':' + c for c in old_cols)})"), {c: row[c] for c in old_cols})
        db_module._install_sqlite_inbox_triggers(conn)
        for table in ("project_subscriber", "initiative_subscriber", "cycle_subscriber"):
            conn.exec_driver_sql(f"DROP TABLE {table}")
        conn.exec_driver_sql("DELETE FROM schema_revision WHERE revision >= 78")
    assert not {c["name"]: c for c in inspect(engine).get_columns("inbox_notification")}["issue_id"]["nullable"]
    for _ in range(2):  # a replayed deploy is a no-op
        migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION == 79
    columns = {c["name"]: c for c in inspect(engine).get_columns("inbox_notification")}
    assert columns["issue_id"]["nullable"] and set(db_module._INBOX_SUBJECT_COLUMNS) <= set(columns)
    index_names = {i["name"] for i in inspect(engine).get_indexes("inbox_notification")}
    assert "ix_inbox_notification_recipient_read" in index_names
    assert "ix_inbox_notification_project_id" in index_names
    [after] = rows_for(ws, "ana")
    assert after["id"] == kept["id"] and after["issue_id"] == task and after["project_id"] is None
    # The rebuilt table still publishes recipient-scoped change events.
    with engine.connect() as conn:
        before = conn.execute(text("SELECT COUNT(*) FROM change_event WHERE entity='notification'")).scalar()
    pid = project(ws, leadId=uid(ws, "ana"))
    with engine.connect() as conn:
        events = conn.execute(text(
            "SELECT recipient_id FROM change_event WHERE entity='notification' ORDER BY seq DESC LIMIT 1")).all() \
            if "seq" in {c["name"] for c in inspect(engine).get_columns("change_event")} else \
            conn.execute(text("SELECT recipient_id FROM change_event WHERE entity='notification'")).all()
        after_count = conn.execute(text("SELECT COUNT(*) FROM change_event WHERE entity='notification'")).scalar()
    assert after_count == before + 1 and events[-1][0] == uid(ws, "ana")
    assert rows_for(ws, "ana")[-1]["project_id"] == pid
    assert handles(ws, subscribers(ws, project_subscriber, "project_id", pid)) == {"owner", "ana"}


def test_deleting_a_team_takes_its_cycle_notifications_and_follows(ws):
    w, org = ws["writer"], ws["org"]
    tid = w.team_create(org, uid(ws, "owner"), {"name": "Ops", "key": "OPS"})
    t0 = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    w.enable_team_cadence(org, tid, t0.weekday(), now=t0)
    with ws["engine"].connect() as conn:
        first = conn.execute(select(cycle.c.id).where(cycle.c.team_id == tid).order_by(cycle.c.number)).scalars().first()
    w.subject_subscribe(org, uid(ws, "owner"), "cycle", first, True)
    w.reconcile_team_cadence(org, tid, now=t0 + timedelta(days=8))
    assert any(r["cycle_id"] == first for r in rows_for(ws, "owner"))
    assert w.team_delete(org, uid(ws, "owner"), tid, delete_issues=True)
    assert not any(r["cycle_id"] for r in rows_for(ws, "owner"))
    assert subscribers(ws, cycle_subscriber, "cycle_id", first) == set()
