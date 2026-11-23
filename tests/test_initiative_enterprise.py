"""Linear's Enterprise initiative features, on by default (revision 90): several parents,
member editing, private lead teams, label groups, the workspace update schedule with
follow-ups, an initiative Slack channel, team initiatives and insights."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import initiative_api
from hoja_server.app import create_app
from hoja_server.db import inbox_notification, initiative, make_engine, migrate, seed_tenant
from hoja_server.store import Store

UTC = timezone.utc


@pytest.fixture()
def ws(tmp_path):
    db_path = tmp_path / "enterprise.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Plans", org_url_key="plans",
                         user_email="owner@example.test", token="owner-token", demo_issues=False)
    store = Store(engine)
    member, member_token = store.invite_member(seeded["org_id"], "Ana", "ana@example.test")
    return {"engine": engine, "client": TestClient(create_app(str(db_path))), "seed": seeded,
            "tokens": {"owner": seeded["token"], "ana": member_token}, "ana": member["id"]}


def gql(ws, query, variables=None, who="owner", ok=True):
    body = ws["client"].post("/graphql", json={"query": query, "variables": variables or {}},
                             headers={"Authorization": ws["tokens"][who]}).json()
    if ok:
        assert "errors" not in body, body
    return body


def create(ws, who="owner", **inp):
    inp.setdefault("name", "Grow")
    inp.setdefault("status", "active")
    body = gql(ws, "mutation($i:InitiativeCreateInput!){initiativeCreate(input:$i){initiative{id identifier}}}",
               {"i": inp}, who=who)
    return body["data"]["initiativeCreate"]["initiative"]


def update(ws, ident, who="owner", ok=True, **inp):
    return gql(ws, "mutation($id:String!,$i:InitiativeUpdateInput!){initiativeUpdate(id:$id,input:$i){success}}",
               {"id": ident, "i": inp}, who=who, ok=ok)


def read(ws, ident, fields, who="owner"):
    return gql(ws, f'{{initiative(id:"{ident}"){{{fields}}}}}', who=who)["data"]["initiative"]


def test_an_initiative_can_have_several_parents_without_loops_or_deep_chains(ws):
    a, b = create(ws, name="A"), create(ws, name="B")
    c = create(ws, name="C", parentIds=[a["id"], b["identifier"]])
    assert {p["id"] for p in read(ws, c["id"], "parents{id}")["parents"]} == {a["id"], b["id"]}
    for parent in (a, b):
        assert read(ws, parent["id"], "children{id}")["children"] == [{"id": c["id"]}]
    assert "errors" in update(ws, a["id"], ok=False, addParentId=c["id"]), "a loop is refused"
    update(ws, c["id"], removeParentId=b["id"])
    assert [p["id"] for p in read(ws, c["id"], "parents{id}")["parents"]] == [a["id"]]
    chain = [a]
    for n in range(4):
        chain.append(create(ws, name=f"L{n}", parentIds=[chain[-1]["id"]]))
    deep = create(ws, name="too deep")
    assert "errors" in update(ws, deep["id"], ok=False, parentIds=[chain[-1]["id"]])


def test_projects_roll_up_through_every_parent(ws):
    team = ws["seed"]["team_id"]
    pid = gql(ws, "mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
              {"i": {"name": "Shared", "teamIds": [team]}})["data"]["projectCreate"]["project"]["id"]
    a, b = create(ws, name="A"), create(ws, name="B")
    create(ws, name="C", parentIds=[a["id"], b["id"]], projectIds=[pid])
    for parent in (a, b):
        assert [p["id"] for p in read(ws, parent["id"], "allProjects{id}")["allProjects"]] == [pid]


def test_any_member_edits_but_only_owner_creator_or_admin_archives(ws):
    i = create(ws, name="Shared goal")
    assert update(ws, i["id"], who="ana", name="Renamed by Ana")["data"]["initiativeUpdate"]["success"]
    denied = gql(ws, f'mutation{{initiativeArchive(id:"{i["id"]}"){{success}}}}', who="ana", ok=False)
    assert "errors" in denied or not denied["data"]["initiativeArchive"]["success"]
    update(ws, i["id"], ownerId=ws["ana"])
    assert gql(ws, f'mutation{{initiativeArchive(id:"{i["id"]}"){{success}}}}', who="ana")["data"]["initiativeArchive"]["success"]


def test_initiatives_led_by_a_private_team_are_hidden_from_non_members(ws):
    team = gql(ws, 'mutation{teamCreate(input:{name:"Secret",key:"SEC",visibility:"private"}){team{id}}}')
    team_id = team["data"]["teamCreate"]["team"]["id"]
    i = create(ws, name="Hidden", leadTeamId=team_id)
    assert read(ws, i["id"], "id") == {"id": i["id"]}
    assert gql(ws, f'{{initiative(id:"{i["id"]}"){{id}}}}', who="ana")["data"]["initiative"] is None
    names = [n["name"] for n in gql(ws, "{initiatives{nodes{name}}}", who="ana")["data"]["initiatives"]["nodes"]]
    assert "Hidden" not in names
    assert not update(ws, i["id"], who="ana", name="nope")["data"]["initiativeUpdate"]["success"]


def test_label_groups_are_exclusive_by_default(ws):
    mk = 'mutation($n:String!,$p:String,$g:Boolean,$x:Boolean){initiativeLabelCreate(name:$n,parentId:$p,isGroup:$g,exclusive:$x){id isGroup exclusive parent{id}}}'
    group = gql(ws, mk, {"n": "Horizon", "g": True})["data"]["initiativeLabelCreate"]
    assert group["isGroup"] and group["exclusive"]
    now = gql(ws, mk, {"n": "Now", "p": group["id"]})["data"]["initiativeLabelCreate"]
    later = gql(ws, mk, {"n": "Later", "p": group["id"]})["data"]["initiativeLabelCreate"]
    assert now["parent"] == {"id": group["id"]}
    i = create(ws, labelIds=[now["id"]])
    assert "errors" in update(ws, i["id"], ok=False, labelIds=[now["id"], later["id"]])
    assert "errors" in update(ws, i["id"], ok=False, labelIds=[group["id"]]), "a group is not a label"
    gql(ws, f'mutation{{initiativeLabelUpdate(id:"{group["id"]}",exclusive:false){{exclusive}}}}')
    update(ws, i["id"], labelIds=[now["id"], later["id"]])
    assert {lab["id"] for lab in read(ws, i["id"], "labels{id}")["labels"]} == {now["id"], later["id"]}


def test_schedule_slots_follow_the_owners_timezone():
    berlin = initiative_api._zone("Europe/Berlin")
    friday_9 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)  # 09:00 in Berlin
    assert initiative_api.schedule_slot(friday_9 + timedelta(minutes=1), "weekly", 4, 9, berlin) == friday_9
    assert initiative_api.schedule_slot(friday_9 - timedelta(minutes=1), "weekly", 4, 9, berlin) == friday_9 - timedelta(days=7)
    assert initiative_api.schedule_slot(friday_9, "weekly", 4, 9, berlin, upcoming=True) == friday_9 + timedelta(days=7)
    monthly = initiative_api.schedule_slot(datetime(2026, 11, 23, tzinfo=UTC), "monthly", 0, 9, UTC)
    assert monthly == datetime(2026, 11, 2, 9, tzinfo=UTC), "first Monday of November"
    assert initiative_api.add_working_days(datetime(2026, 11, 20, 9, tzinfo=UTC), 1, UTC) == datetime(2026, 11, 23, 9, tzinfo=UTC)


def test_workspace_schedule_reminds_then_follows_up_twice(ws):
    owner = ws["seed"]["user_id"]
    denied = gql(ws, 'mutation{initiativeReminderPolicyUpdate(input:{cadence:"weekly"}){cadence}}', who="ana", ok=False)
    assert "errors" in denied
    gql(ws, 'mutation{initiativeReminderPolicyUpdate(input:{cadence:"weekly",weekday:4,hour:9,followUps:true}){cadence}}')
    i = create(ws, ownerId=owner)
    with ws["engine"].begin() as conn:
        conn.execute(initiative.update().where(initiative.c.id == i["id"]).values(
            created_at="2026-11-01T00:00:00+00:00"))
    slot = datetime(2026, 11, 20, 9, tzinfo=UTC)  # a Friday
    sweep = lambda at: initiative_api.sweep_reminders(ws["engine"], now=at.isoformat())  # noqa: E731
    assert sweep(slot - timedelta(minutes=5)) == 1, "the previous Friday's slot was missed"
    assert sweep(slot + timedelta(minutes=5)) == 1, "this Friday's slot"
    assert sweep(slot + timedelta(hours=2)) == 0
    assert sweep(datetime(2026, 11, 23, 9, 30, tzinfo=UTC)) == 1, "first follow-up a working day later"
    assert sweep(datetime(2026, 11, 24, 9, 30, tzinfo=UTC)) == 0
    assert sweep(datetime(2026, 11, 25, 9, 30, tzinfo=UTC)) == 1, "second follow-up two working days later"
    assert sweep(datetime(2026, 11, 26, 9, 30, tzinfo=UTC)) == 0, "no third follow-up"
    with ws["engine"].connect() as conn:
        rows = conn.execute(select(inbox_notification.c.kind).where(
            inbox_notification.c.recipient_id == owner)).scalars().all()
    assert rows.count("initiativeUpdateDue") == 4
    update(ws, i["id"], updateReminder="never")
    assert initiative_api.sweep_reminders(ws["engine"], now=(slot + timedelta(days=7, minutes=5)).isoformat()) == 0


def test_posting_an_update_settles_the_reminder(ws):
    owner = ws["seed"]["user_id"]
    gql(ws, 'mutation{initiativeReminderPolicyUpdate(input:{cadence:"weekly"}){cadence}}')
    i = create(ws, ownerId=owner)
    with ws["engine"].begin() as conn:
        conn.execute(initiative.update().where(initiative.c.id == i["id"]).values(
            created_at="2020-01-01T00:00:00+00:00"))
    assert read(ws, i["id"], "updateDueAt")["updateDueAt"] is not None
    gql(ws, f'mutation{{initiativeUpdateCreate(input:{{initiativeId:"{i["id"]}",body:"on it",health:"on track"}}){{success}}}}')
    assert initiative_api.sweep_reminders(ws["engine"]) == 0
    due = datetime.fromisoformat(read(ws, i["id"], "updateDueAt")["updateDueAt"])
    assert due > datetime.now(UTC), "the next slot, not an overdue one"


def test_slack_channel_is_stored_and_drafts_still_work_without_a_connection(ws):
    assert gql(ws, "{slackChannels{id}}")["data"]["slackChannels"] == []
    i = create(ws, slackChannelId="C0123ABCD", slackChannelName="launch")
    assert read(ws, i["id"], "slackChannel{id name}")["slackChannel"] == {"id": "C0123ABCD", "name": "launch"}
    assert "errors" in update(ws, i["id"], ok=False, slackChannelId="not a channel")
    draft = gql(ws, f'mutation{{initiativeUpdateDraft(initiativeId:"{i["id"]}"){{source}}}}')
    assert draft["data"]["initiativeUpdateDraft"]["source"] == "changes"


def test_team_initiatives_toggle_and_insights(ws):
    team = ws["seed"]["team_id"]
    shown = gql(ws, f'mutation{{teamShowInitiatives(teamId:"{team}",show:true){{showInitiatives}}}}')
    assert shown["data"]["teamShowInitiatives"]["showInitiatives"] is True
    pid = gql(ws, "mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
              {"i": {"name": "Work", "teamIds": [team]}})["data"]["projectCreate"]["project"]["id"]
    for n in range(3):
        gql(ws, "mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
            {"i": {"teamId": team, "title": f"t{n}", "projectId": pid}})
    i = create(ws, projectIds=[pid], leadTeamId=team)
    got = read(ws, i["id"], "insights{scope completed byStatus{label total} byProject{label total} byAssignee{label total}}")["insights"]
    assert got["scope"] == 3 and got["byProject"] == [{"label": "Work", "total": 3}]
    assert sum(r["total"] for r in got["byStatus"]) == 3
    assert got["byAssignee"] == [{"label": "No assignee", "total": 3}]
