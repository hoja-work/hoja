"""Inbox fan-out and triage: who hears about a task, what the row says, and what a
recipient can do with it (read/unread, archive/unarchive, snooze, per-task actions)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    inbox_notification,
    issue_subscriber,
    make_engine,
    migrate,
    seed_tenant,
    team_member,
    user,
    workflow_state,
)
from hoja_server.notifications import mentioned_user_ids, snippet
from hoja_server.store import Store
from hoja_server.writer import Writer


@pytest.fixture()
def ws(tmp_path):
    db_path = tmp_path / "inbox.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Inbox", org_url_key="inbox",
                         user_email="owner@example.test", token="owner-token", demo_issues=False)
    store = Store(engine)
    people = {"owner": (seeded["user_id"], seeded["token"])}
    for handle in ("ana", "ben", "cy"):
        member, token = store.invite_member(seeded["org_id"], handle.title(), f"{handle}@example.test")
        people[handle] = (member["id"], token)
    with engine.begin() as conn:
        # Display names are what an @mention is most likely to use.
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


def create(ws, actor="owner", **fields):
    return ws["writer"].issue_create(ws["org"], uid(ws, actor), {
        "teamId": ws["team"], "title": fields.pop("title", "Inbox task"), **fields})


def test_assignment_on_create_notifies_assignee_and_subscribes_both(ws):
    iid = create(ws, assigneeId=uid(ws, "ana"))
    assert kinds(ws, "ana") == ["issueAssigned"]
    assert kinds(ws, "owner") == []  # the actor never notifies themself
    with ws["engine"].connect() as conn:
        subs = set(conn.execute(select(issue_subscriber.c.user_id).where(
            issue_subscriber.c.issue_id == iid)).scalars())
    assert subs == {uid(ws, "owner"), uid(ws, "ana")}


def test_status_change_reaches_subscribers_with_from_and_to(ws):
    iid = create(ws, assigneeId=uid(ws, "ana"))
    ws["writer"].issue_subscribe(ws["org"], uid(ws, "ben"), iid)
    ws["writer"].issue_update(ws["org"], iid, {"stateId": ws["states"]["completed"]}, actor_id=uid(ws, "ana"))
    assert kinds(ws, "owner") == ["issueStatusChanged"]
    assert kinds(ws, "ben") == ["issueStatusChanged"]
    assert kinds(ws, "ana") == ["issueAssigned"]  # ana moved it herself
    import json
    data = json.loads(rows_for(ws, "ben")[0]["data"])
    assert data["to"]["id"] == ws["states"]["completed"] and data["to"]["type"] == "completed"
    assert data["to"]["name"] and data["from"]["id"]


def test_unsubscribe_stops_follow_notifications(ws):
    iid = create(ws)
    ws["writer"].issue_subscribe(ws["org"], uid(ws, "ben"), iid)
    ws["writer"].issue_unsubscribe(ws["org"], uid(ws, "ben"), iid)
    ws["writer"].issue_update(ws["org"], iid, {"priority": 1}, actor_id=uid(ws, "owner"))
    assert kinds(ws, "ben") == []


def test_noisy_field_edits_are_silent_and_priority_due_are_not(ws):
    iid = create(ws)
    ws["writer"].issue_subscribe(ws["org"], uid(ws, "ben"), iid)
    for change in ({"title": "Renamed"}, {"description": "New words"}, {"estimate": 3}):
        ws["writer"].issue_update(ws["org"], iid, change, actor_id=uid(ws, "owner"))
    assert kinds(ws, "ben") == []
    ws["writer"].issue_update(ws["org"], iid, {"priority": 1, "dueDate": "2026-12-01"},
                              actor_id=uid(ws, "owner"))
    import json
    (row,) = rows_for(ws, "ben")
    assert row["kind"] == "issueUpdated"
    assert set(json.loads(row["data"])["changes"]) == {"priority", "dueDate"}


def test_reassignment_sends_assigned_only_to_new_assignee(ws):
    iid = create(ws, assigneeId=uid(ws, "ana"))
    ws["writer"].issue_update(ws["org"], iid, {"assigneeId": uid(ws, "ben"),
                                               "stateId": ws["states"]["started"]},
                              actor_id=uid(ws, "owner"))
    assert kinds(ws, "ben") == ["issueAssigned"]
    # The previous assignee and other followers hear the state move.
    assert kinds(ws, "ana") == ["issueAssigned", "issueStatusChanged"]


def test_comment_notifies_followers_and_mentions_directly(ws):
    iid = create(ws, assigneeId=uid(ws, "ana"))
    cid = ws["writer"].comment_create(ws["org"], uid(ws, "ben"), {
        "issueId": iid, "body": "**Heads up** @ana and @cy, see [the doc](https://x.test)"})
    assert kinds(ws, "ana")[-1] == "issueMention"
    assert kinds(ws, "cy") == ["issueMention"]
    assert kinds(ws, "owner") == ["issueComment"]
    assert kinds(ws, "ben") == []  # the commenter
    row = rows_for(ws, "owner")[0]
    assert row["comment_id"] == cid
    import json
    assert json.loads(row["data"])["snippet"] == "Heads up @ana and @cy, see the doc"
    # Commenting and being mentioned both subscribe, so the next comment reaches ben and cy.
    ws["writer"].comment_create(ws["org"], uid(ws, "owner"), {"issueId": iid, "body": "Thanks"})
    assert kinds(ws, "ben") == ["issueComment"]
    assert kinds(ws, "cy") == ["issueMention", "issueComment"]


def test_mentions_match_names_and_email_local_part_in_this_org_only(ws, tmp_path):
    with ws["engine"].connect() as conn:
        found = mentioned_user_ids(conn, ws["org"], "ping @Ben, @owner and @nobody; mail me@x.test")
    assert found == {uid(ws, "ben"), uid(ws, "owner")}
    assert snippet("x" * 300).endswith("…") and len(snippet("x" * 300)) == 200


def test_snooze_hides_from_list_and_count_then_returns_unread(ws):
    store, org, ana = ws["store"], ws["org"], uid(ws, "ana")
    iid = create(ws, assigneeId=ana)
    assert store.inbox_unread_count(org, ana) == 1
    (row,) = rows_for(ws, "ana")
    store.inbox_mark(org, ana, row["id"], "read_at")
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    assert store.inbox_issue_update(org, ana, iid, "snooze", until=future)
    assert store.inbox_notifications(org, ana) == []
    assert [r["id"] for r in store.inbox_notifications(org, ana, snoozed=True)] == [row["id"]]
    assert store.inbox_unread_count(org, ana) == 0
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.inbox_snooze(org, ana, row["id"], past)
    (back,) = store.inbox_notifications(org, ana)
    assert back["read_at"] is None
    assert store.inbox_unread_count(org, ana) == 1


def test_issue_scoped_read_unread_archive(ws):
    store, org, ana = ws["store"], ws["org"], uid(ws, "ana")
    iid = create(ws, assigneeId=ana)
    for body in ("one", "two"):
        ws["writer"].comment_create(ws["org"], uid(ws, "owner"), {"issueId": iid, "body": body})
    assert store.inbox_unread_count(org, ana) == 3
    assert store.inbox_issue_update(org, ana, iid, "read")
    assert store.inbox_unread_count(org, ana) == 0
    assert store.inbox_issue_update(org, ana, iid, "read", read=False)
    assert store.inbox_unread_count(org, ana) == 1  # only the latest event is unread again
    assert store.inbox_issue_update(org, ana, iid, "archive")
    assert store.inbox_notifications(org, ana) == []
    assert len(store.inbox_notifications(org, ana, archived=True)) == 3
    assert not store.inbox_issue_update(org, ana, "NOPE-999", "archive")


def test_unread_unarchive_and_archive_all_read(ws):
    store, org, ana = ws["store"], ws["org"], uid(ws, "ana")
    create(ws, assigneeId=ana, title="first")
    create(ws, assigneeId=ana, title="second")
    first, second = rows_for(ws, "ana")
    store.inbox_mark(org, ana, first["id"], "read_at")
    assert store.inbox_archive_all_read(org, ana)
    assert [r["id"] for r in store.inbox_notifications(org, ana)] == [second["id"]]
    assert store.inbox_clear(org, ana, first["id"], "archived_at")
    assert store.inbox_clear(org, ana, first["id"], "read_at")
    assert store.inbox_unread_count(org, ana) == 2


def test_graphql_contract_and_recipient_isolation(ws):
    ana_id, ana_token = ws["people"]["ana"]
    _ben_id, ben_token = ws["people"]["ben"]
    iid = create(ws, assigneeId=ana_id)
    ws["writer"].comment_create(ws["org"], uid(ws, "owner"), {"issueId": iid, "body": "Hello @ana"})
    with TestClient(create_app(str(ws["db_path"]), open_mode=False)) as client:
        def gql(token, query, variables=None):
            body = client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                               json={"query": query, "variables": variables or {}}).json()
            assert "errors" not in body, body
            return body["data"]
        listing = gql(ana_token, "{inboxNotifications{nodes{id kind data snoozedUntil comment{id body} issue{identifier}}} inboxUnreadCount}")
        nodes = listing["inboxNotifications"]["nodes"]
        assert [n["kind"] for n in nodes] == ["issueMention", "issueAssigned"]
        assert nodes[0]["comment"]["body"] == "Hello @ana" and nodes[0]["data"]["snippet"] == "Hello @ana"
        assert listing["inboxUnreadCount"] == 2
        identifier = nodes[0]["issue"]["identifier"]
        # Another user cannot touch ana's rows: the per-row mutations report false.
        assert gql(ben_token, "mutation($id:String!){notificationMarkUnread(id:$id)}",
                   {"id": nodes[0]["id"]}) == {"notificationMarkUnread": False}
        gql(ben_token, "mutation($i:String!){notificationsArchiveForIssue(issueId:$i)}", {"i": identifier})
        assert gql(ana_token, "{inboxUnreadCount}")["inboxUnreadCount"] == 2
        until = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert gql(ana_token, "mutation($i:String!,$u:DateTime){notificationsSnoozeForIssue(issueId:$i,until:$u)}",
                   {"i": identifier, "u": until})["notificationsSnoozeForIssue"] is True
        snoozed = gql(ana_token, "{inboxNotifications(snoozed:true){nodes{snoozedUntil}} inboxUnreadCount}")
        assert len(snoozed["inboxNotifications"]["nodes"]) == 2 and snoozed["inboxUnreadCount"] == 0
        assert snoozed["inboxNotifications"]["nodes"][0]["snoozedUntil"].startswith(until[:16])
        gql(ana_token, "mutation($i:String!){notificationsSnoozeForIssue(issueId:$i,until:null)}", {"i": identifier})
        assert gql(ana_token, "{inboxUnreadCount}")["inboxUnreadCount"] == 2
        assert gql(ana_token, "mutation($i:String!){notificationsMarkReadForIssue(issueId:$i)}", {"i": identifier})
        gql(ana_token, "mutation{notificationsArchiveAllRead}")
        archived = gql(ana_token, "{inboxNotifications(archived:true){nodes{id}}}")
        assert len(archived["inboxNotifications"]["nodes"]) == 2
        prefs = gql(ana_token, "mutation{notificationPreferenceSet(kind:\"issueComment\",enabled:false){success issueAssigned issueMention issueComment issueStatusChanged issueUpdated issueDecision issueCreated}}")
        assert prefs["notificationPreferenceSet"] == {
            "success": True, "issueAssigned": True, "issueMention": True, "issueComment": False,
            "issueStatusChanged": True, "issueUpdated": True, "issueDecision": True, "issueCreated": True}


def test_revision_77_adds_the_columns_to_an_older_database_and_replays(tmp_path):
    from sqlalchemy import inspect

    from hoja_server import db as db_module

    engine = make_engine(tmp_path / "rev76.db")
    migrate(engine)
    with engine.begin() as conn:
        for column in ("data", "comment_id", "snoozed_until"):
            conn.exec_driver_sql(f"ALTER TABLE inbox_notification DROP COLUMN {column}")
        conn.exec_driver_sql("DELETE FROM schema_revision WHERE revision >= 77")
    for _ in range(2):
        migrate(engine)
        assert db_module.applied_schema_revision(engine) == db_module.CURRENT_SCHEMA_REVISION >= 77
    columns = {c["name"] for c in inspect(engine).get_columns("inbox_notification")}
    assert {"data", "comment_id", "snoozed_until"} <= columns
