"""Initiative structure (revision 88): sub-initiatives, labels, resources, lead team,
readable ids, health roll-up, progress history, reactions and replies on updates,
update drafts, reminders and saved-view filters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import inspect, select
from starlette.testclient import TestClient

from hoja_server import initiative_api
from hoja_server.app import create_app
from hoja_server.db import inbox_notification, initiative, make_engine, migrate, seed_tenant
from hoja_server.store import Store


@pytest.fixture()
def ws(tmp_path):
    db_path = tmp_path / "structure.db"
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


def create(ws, **inp):
    inp.setdefault("name", "Grow")
    inp.setdefault("status", "active")
    d = gql(ws, "mutation($i:InitiativeCreateInput!){initiativeCreate(input:$i){initiative{id identifier number}}}",
            {"i": inp})["data"]
    return d["initiativeCreate"]["initiative"]


def update(ws, ident, ok=True, **inp):
    return gql(ws, "mutation($id:String!,$i:InitiativeUpdateInput!){initiativeUpdate(id:$id,input:$i){success}}",
               {"id": ident, "i": inp}, ok=ok)


def test_readable_ids_number_in_creation_order_and_resolve(ws):
    a, b = create(ws, name="A"), create(ws, name="B")
    assert (a["identifier"], b["identifier"]) == ("I-1", "I-2")
    got = gql(ws, '{initiative(id:"I-2"){id name}}')["data"]["initiative"]
    assert got == {"id": b["id"], "name": "B"}
    update(ws, "I-2", name="B renamed")
    assert gql(ws, f'{{initiative(id:"{b["id"]}"){{identifier name}}}}')["data"]["initiative"] == {
        "identifier": "I-2", "name": "B renamed"}


def test_nesting_labels_icon_colour_lead_team_and_reminder(ws):
    parent = create(ws, name="Company goals")
    label = gql(ws, 'mutation{initiativeLabelCreate(name:"Growth",color:"#5e6ad2"){id name}}')["data"]["initiativeLabelCreate"]
    again = gql(ws, 'mutation{initiativeLabelCreate(name:"growth"){id}}')["data"]["initiativeLabelCreate"]
    assert again["id"] == label["id"], "label names are unique per workspace, ignoring case"
    child = create(ws, name="Launch", parentId=parent["identifier"], labelIds=[label["id"]], icon="🚀",
                   color="#ff0000", leadTeamId=ws["seed"]["team_id"], updateReminder="weekly")
    got = gql(ws, f'{{initiative(id:"{child["id"]}"){{parent{{id}} labels{{name}} icon color leadTeam{{id}} updateReminder}}}}')["data"]["initiative"]
    assert got == {"parent": {"id": parent["id"]}, "labels": [{"name": "Growth"}], "icon": "🚀",
                   "color": "#ff0000", "leadTeam": {"id": ws["seed"]["team_id"]}, "updateReminder": "weekly"}
    kids = gql(ws, f'{{initiative(id:"{parent["id"]}"){{children{{id}}}}}}')["data"]["initiative"]["children"]
    assert kids == [{"id": child["id"]}]
    # A parent cannot move under its own child, nor under itself.
    for target in (child["id"], parent["id"]):
        body = update(ws, parent["id"], ok=False, parentId=target)
        assert "errors" in body
    for bad in ({"color": "red"}, {"updateReminder": "hourly"}):
        assert "errors" in update(ws, child["id"], ok=False, **bad)
    update(ws, child["id"], parentId=None, labelIds=[])
    got = gql(ws, f'{{initiative(id:"{child["id"]}"){{parent{{id}} labels{{name}}}}}}')["data"]["initiative"]
    assert got == {"parent": None, "labels": []}


def test_resources_add_and_remove(ws):
    i = create(ws)
    bad = gql(ws, f'mutation{{initiativeResourceCreate(initiativeId:"{i["id"]}",url:"javascript:alert(1)"){{success}}}}', ok=False)
    assert "errors" in bad
    res = gql(ws, f'mutation{{initiativeResourceCreate(initiativeId:"{i["identifier"]}",url:"https://example.test/spec"){{resource{{id title url}}}}}}')["data"]["initiativeResourceCreate"]["resource"]
    assert res["title"] == "example.test/spec"
    denied = gql(ws, f'mutation{{initiativeResourceDelete(id:"{res["id"]}"){{success}}}}', who="ana", ok=False)
    assert "errors" in denied, "a member who did not add it cannot remove it"
    assert gql(ws, f'mutation{{initiativeResourceDelete(id:"{res["id"]}"){{success}}}}')["data"]["initiativeResourceDelete"]["success"]
    assert gql(ws, f'{{initiative(id:"{i["id"]}"){{resources{{id}}}}}}')["data"]["initiative"]["resources"] == []


def test_reactions_toggle_and_replies_notify_and_go_with_the_update(ws):
    i = create(ws)
    up = gql(ws, f'mutation{{initiativeUpdateCreate(input:{{initiativeId:"{i["id"]}",body:"Going well",health:"on track"}}){{initiativeUpdate{{id}}}}}}')["data"]["initiativeUpdateCreate"]["initiativeUpdate"]["id"]
    react = f'mutation{{initiativeUpdateReact(updateId:"{up}",emoji:"🎉"){{reactions{{emoji count viewerReacted}}}}}}'
    assert gql(ws, react, who="ana")["data"]["initiativeUpdateReact"]["reactions"] == [
        {"emoji": "🎉", "count": 1, "viewerReacted": True}]
    assert gql(ws, react)["data"]["initiativeUpdateReact"]["reactions"][0]["count"] == 2
    assert gql(ws, react)["data"]["initiativeUpdateReact"]["reactions"][0]["count"] == 1, "a second press removes it"
    gql(ws, f'mutation{{commentCreate(input:{{initiativeUpdateId:"{up}",body:"Nice work"}}){{success}}}}', who="ana")
    comments = gql(ws, f'{{initiativeUpdates(filter:{{initiative:{{id:{{eq:"{i["id"]}"}}}}}}){{nodes{{comments{{nodes{{body}}}}}}}}}}')
    assert comments["data"]["initiativeUpdates"]["nodes"][0]["comments"]["nodes"] == [{"body": "Nice work"}]
    with ws["engine"].connect() as conn:
        kinds = set(conn.execute(select(inbox_notification.c.kind).where(
            inbox_notification.c.recipient_id == ws["seed"]["user_id"])).scalars())
    assert "initiativeUpdateReply" in kinds
    gql(ws, f'mutation{{initiativeUpdateDelete(id:"{up}"){{success}}}}')
    with ws["engine"].connect() as conn:
        left = conn.exec_driver_sql("SELECT count(*) FROM comment WHERE initiative_update_id = ?", (up,)).scalar()
        reactions = conn.exec_driver_sql("SELECT count(*) FROM initiative_update_reaction WHERE update_id = ?", (up,)).scalar()
    assert (left, reactions) == (0, 0)


def test_health_rolls_up_from_projects_of_sub_initiatives(ws):
    team = ws["seed"]["team_id"]
    pids = []
    for name in ("Alpha", "Beta", "Gamma"):
        d = gql(ws, "mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
                {"i": {"name": name, "teamIds": [team]}})["data"]
        pids.append(d["projectCreate"]["project"]["id"])
    parent = create(ws, name="Parent", projectIds=[pids[0]])
    create(ws, name="Child", parentId=parent["id"], projectIds=[pids[1], pids[2]])
    for pid, health in ((pids[0], "on track"), (pids[1], "off track")):
        gql(ws, "mutation($i:ProjectUpdateCreateInput!){projectUpdateCreate(input:$i){success}}",
            {"i": {"projectId": pid, "body": "status", "health": health}})
    got = gql(ws, f'{{initiative(id:"{parent["id"]}"){{allProjects{{id health}} projectHealth{{onTrack atRisk offTrack noUpdate}} progressHistory(weeks:4){{scope points{{weekStart completed}}}}}}}}')["data"]["initiative"]
    assert {p["id"] for p in got["allProjects"]} == set(pids)
    assert got["projectHealth"] == {"onTrack": 1, "atRisk": 0, "offTrack": 1, "noUpdate": 1}
    assert len(got["progressHistory"]) == 3 and all(len(s["points"]) == 4 for s in got["progressHistory"])


def test_draft_without_a_model_lists_what_changed(ws):
    i = create(ws, name="Ship")
    draft = gql(ws, f'mutation{{initiativeUpdateDraft(initiativeId:"{i["id"]}"){{body health source}}}}')["data"]["initiativeUpdateDraft"]
    assert draft["source"] == "changes" and draft["health"] == "on track"
    assert "No projects are linked yet." in draft["body"]


def test_reminder_reaches_the_owner_once_per_overdue_period(ws):
    owner = ws["seed"]["user_id"]
    i = create(ws, ownerId=owner, updateReminder="weekly")
    with ws["engine"].begin() as conn:
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        conn.execute(initiative.update().where(initiative.c.id == i["id"]).values(created_at=old))
    assert initiative_api.sweep_reminders(ws["engine"]) == 1
    assert initiative_api.sweep_reminders(ws["engine"]) == 0, "the period is claimed once"
    due = gql(ws, f'{{initiative(id:"{i["id"]}"){{updateDueAt}}}}')["data"]["initiative"]["updateDueAt"]
    assert due is not None
    with ws["engine"].connect() as conn:
        kinds = list(conn.execute(select(inbox_notification.c.kind).where(
            inbox_notification.c.recipient_id == owner)).scalars())
    assert kinds.count("initiativeUpdateDue") == 1


def test_saved_view_filters_on_health_and_owner(ws):
    owner = ws["seed"]["user_id"]
    a = create(ws, name="Owned", ownerId=owner)
    create(ws, name="Unowned")
    gql(ws, f'mutation{{initiativeUpdateCreate(input:{{initiativeId:"{a["id"]}",body:"x",health:"at risk"}}){{success}}}}')
    view = gql(ws, "mutation($i:ViewCreateInput!){viewCreate(input:$i){view{initiatives{nodes{name}}}}}",
               {"i": {"name": "Risky", "kind": "initiative",
                      "filters": {"health": {"is": "at risk"}, "owner": {"is": owner}},
                      "display": {"grouping": "health", "ordering": "manual", "nested": True,
                                  "summary": False}}})
    assert view["data"]["viewCreate"]["view"]["initiatives"]["nodes"] == [{"name": "Owned"}]


def test_migration_backfills_numbers_and_adds_tables(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Old", org_url_key="old", user_email="o@example.test",
                         token="tok", demo_issues=False)
    with engine.begin() as conn:
        for n, name in enumerate(("first", "second")):
            conn.execute(initiative.insert().values(
                id=name, organization_id=seeded["org_id"], name=name, status="active", priority=0,
                created_at=f"2026-01-0{n + 1}T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00"))
        # Re-run 88 and every later revision (which must be idempotent too).
        conn.exec_driver_sql("DELETE FROM schema_revision WHERE revision >= 88")
    migrate(engine)
    with engine.connect() as conn:
        numbers = dict(conn.execute(select(initiative.c.id, initiative.c.number)).all())
    assert numbers == {"first": 1, "second": 2}
    assert {"initiative_label", "initiative_resource", "initiative_update_reaction"} <= set(
        inspect(engine).get_table_names())
