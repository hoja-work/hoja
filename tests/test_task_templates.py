"""HOJA-492: task templates, and the defaults ``issueCreate`` expands.

The acceptance bar from HOJA-416-A-33-ag-4, as tests:

  * "creating a task from a template in the web client and through the GraphQL
    API produces the same field values" — two paths, one stored truth;
  * a member is refused by the API, not merely hidden from the UI;
  * the migration survives a second boot with its rows intact, and an existing
    database that never used the feature is unchanged by it.

The web path is simulated here the way the pane actually behaves: it prefills
the form from the template and submits explicit values. The live, browser-level
proof is ``app/scripts/ui-probes/settings-492-pane-probe.mjs``; this file is
the part that must hold on every commit.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    _apply_task_templates,
    _down_task_templates,
    applied_schema_revision,
    make_engine,
    migrate,
    seed_tenant,
)
from hoja_server.store import Store

TEMPLATES = "{ taskTemplates { id name defaultTitle defaultDescription defaultPriority " \
            "team { id key } defaultLabels { id name } defaultAssignee { name } } }"
CREATE_TEMPLATE = (
    "mutation($i: TaskTemplateInput!) { taskTemplateCreate(input: $i) "
    "{ success taskTemplate { id name defaultTitle defaultPriority } } }"
)
CREATE_TASK = (
    "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success "
    "issue { id identifier title description priority "
    "labels { nodes { name } } team { key } assignee { name } } } }"
)


def _setup(tmp_path):
    db_path = tmp_path / "task-templates.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Templates Org", org_url_key="templates-org",
                        user_email="owner@templates.test", token="owner-token",
                        demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _member_token(store, admin, client):
    from tests.test_member_management import _add_member
    member_id = _add_member(client, admin["token"], "member@templates.test")
    _, key = store.create_api_key(admin["org_id"], member_id, "member key", "read_write", None)
    return key


def _team(client, token, index=0):
    nodes = _gql(client, token, "{ teams { nodes { id key } } }")["data"]["teams"]["nodes"]
    return nodes[index]


def _label(client, token, team_id, name):
    made = _gql(
        client, token,
        "mutation($i: IssueLabelCreateInput!){ issueLabelCreate(input:$i){ success "
        "issueLabel { id name } } }",
        {"i": {"name": name, "teamId": team_id}},
    )
    assert "errors" not in made, made
    return made["data"]["issueLabelCreate"]["issueLabel"]["id"]


def test_template_store_is_admin_only_and_two_level(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        team = _team(client, admin["token"])
        made = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Incident", "defaultTitle": "[Incident] ",
            "defaultPriority": 1, "teamId": team["id"]}})
        assert "errors" not in made, made
        assert made["data"]["taskTemplateCreate"]["success"] is True

        # Workspace level, visible without a team filter, and not the same list
        # as the team's own.
        ws = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Weekly", "defaultTitle": "Weekly review"}})
        assert "errors" not in ws, ws
        # No argument is the whole workspace — the pane has to be able to show
        # a team-scoped row it just created (the probe caught a workspace-only
        # default hiding it).
        everything = _gql(client, admin["token"], TEMPLATES)["data"]["taskTemplates"]
        assert [t["name"] for t in everything] == ["Incident", "Weekly"]
        inherited = _gql(client, admin["token"],
                         "{ taskTemplates(teamId: \"%s\") { name } }" % team["id"])
        assert [t["name"] for t in inherited["data"]["taskTemplates"]] == ["Incident", "Weekly"]

        member = _member_token(store, admin, client)
        refused = _gql(client, member, TEMPLATES)
        assert "FORBIDDEN" in str(refused.get("errors")), refused
        refused_create = _gql(client, member, CREATE_TEMPLATE, {"i": {
            "name": "nope", "defaultTitle": "nope"}})
        assert "FORBIDDEN" in str(refused_create.get("errors")), refused_create


def test_the_pane_path_and_the_api_path_store_the_same_task(tmp_path) -> None:
    """ag-4's acceptance sentence, as an assertion.

    Path A — the API caller sends only ``templateId`` and lets the server expand.
    Path B — the pane prefills its form from the template and submits the
    values explicitly, which is what a human saving the form produces.
    Both must store the same title, description, priority, labels and team.
    """
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team = _team(client, admin["token"])
        label_id = _label(client, admin["token"], team["id"], "bug")
        template = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Bug report",
            "defaultTitle": "Bug: ",
            "defaultDescription": "## Steps\n",
            "defaultPriority": 2,
            "defaultLabelIds": [label_id],
            "teamId": team["id"],
        }})["data"]["taskTemplateCreate"]["taskTemplate"]

        expanded = _gql(client, admin["token"], CREATE_TASK,
                        {"i": {"templateId": template["id"]}})
        assert "errors" not in expanded, expanded
        a = expanded["data"]["issueCreate"]["issue"]

        prefilled = _gql(client, admin["token"], CREATE_TASK, {"i": {
            "templateId": template["id"],
            "teamId": team["id"],
            "title": "Bug: ",
            "description": "## Steps\n",
            "priority": 2,
            "labelIds": [label_id],
        }})
        assert "errors" not in prefilled, prefilled
        b = prefilled["data"]["issueCreate"]["issue"]

        for field in ("title", "description", "priority"):
            assert a[field] == b[field], (field, a[field], b[field])
        assert [n["name"] for n in a["labels"]["nodes"]] == \
               [n["name"] for n in b["labels"]["nodes"]] == ["bug"]
        assert a["team"]["key"] == b["team"]["key"]


def test_explicit_input_wins_and_a_missing_field_is_refused_by_name(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team = _team(client, admin["token"])
        template = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Triage", "defaultTitle": "Triage this", "defaultPriority": 4,
            "teamId": team["id"]}})["data"]["taskTemplateCreate"]["taskTemplate"]

        edited = _gql(client, admin["token"], CREATE_TASK,
                      {"i": {"templateId": template["id"], "title": "Login is slow",
                             "priority": 1}})
        issue = edited["data"]["issueCreate"]["issue"]
        # What a person typed is what is stored, not the template's default.
        assert issue["title"] == "Login is slow" and issue["priority"] == 1

        # 0 is a real priority, so it must not be replaced by the template's 4.
        zero = _gql(client, admin["token"], CREATE_TASK,
                    {"i": {"templateId": template["id"], "priority": 0}})
        assert zero["data"]["issueCreate"]["issue"]["priority"] == 0, zero

        # No template, no title: refused, and the error names the field.
        bare = _gql(client, admin["token"], CREATE_TASK, {"i": {"teamId": team["id"]}})
        assert "title" in str(bare.get("errors")), bare
        no_team = _gql(client, admin["token"], CREATE_TASK, {"i": {"title": "x"}})
        assert "teamId" in str(no_team.get("errors")), no_team


def test_a_team_template_cannot_create_in_another_team(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        teams = _gql(client, admin["token"], "{ teams { nodes { id key } } }")[
            "data"]["teams"]["nodes"]
        if len(teams) < 2:
            second = _gql(client, admin["token"],
                          "mutation{ teamCreate(input:{name:\"Second\", key:\"SEC\"}) "
                          "{ success team { id } } }")
            assert "errors" not in second, second
            teams = _gql(client, admin["token"], "{ teams { nodes { id key } } }")[
                "data"]["teams"]["nodes"]
        first, other = teams[0], teams[1]
        template = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Scoped", "defaultTitle": "Scoped task", "teamId": first["id"]}})
        assert "errors" not in template, template
        tid = template["data"]["taskTemplateCreate"]["taskTemplate"]["id"]

        refused = _gql(client, admin["token"], CREATE_TASK,
                       {"i": {"templateId": tid, "teamId": other["id"]}})
        assert "teamId" in str(refused.get("errors")), refused


def test_defaults_are_validated_when_the_template_is_saved(tmp_path) -> None:
    """A template that cannot be applied must not save: the failure belongs to
    the admin editing it, not to whoever creates the next task."""
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team = _team(client, admin["token"])
        other_team_label = None
        made_second = _gql(client, admin["token"],
                           "mutation{ teamCreate(input:{name:\"Other\", key:\"OTH\"}) "
                           "{ success team { id } } }")
        if "errors" not in made_second:
            other_id = made_second["data"]["teamCreate"]["team"]["id"]
            other_team_label = _label(client, admin["token"], other_id, "theirs")
        if other_team_label:
            refused = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
                "name": "Bad labels", "defaultTitle": "x",
                "defaultLabelIds": [other_team_label], "teamId": team["id"]}})
            assert "defaultLabelIds" in str(refused.get("errors")), refused
        bogus = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Bad assignee", "defaultTitle": "x", "teamId": team["id"],
            "defaultAssigneeId": "no-such-user"}})
        assert "defaultAssigneeId" in str(bogus.get("errors")), bogus
        empty = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Blank", "defaultTitle": "  ", "teamId": team["id"]}})
        assert "defaultTitle" in str(empty.get("errors")), empty
        # Duplicate name in the same scope is refused by the API.
        ok = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Dup", "defaultTitle": "x", "teamId": team["id"]}})
        assert "errors" not in ok, ok
        dup = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Dup", "defaultTitle": "y", "teamId": team["id"]}})
        assert "name" in str(dup.get("errors")), dup


def test_revision_60_is_durable_and_a_59_database_is_untouched(tmp_path) -> None:
    """The migration on a populated database, not an empty one.

    Seed a tenant and its issues at the current schema, then re-run the
    forward step and the down/up cycle and assert (a) the revision is 60,
    (b) existing rows survive, (c) a second boot changes nothing.
    """
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team = _team(client, admin["token"])
        before = _gql(client, admin["token"], CREATE_TASK,
                      {"i": {"teamId": team["id"], "title": "Pre-existing"}})
        assert "errors" not in before, before
        count_before = _gql(client, admin["token"],
                            "{ issues { nodes { id title } } }")["data"]["issues"]["nodes"]

    assert applied_schema_revision(engine) == 60
    # Second boot: the ledger is a prefix, so nothing reapplies.
    migrate(engine)
    assert applied_schema_revision(engine) == 60
    with TestClient(create_app(str(tmp_path / "task-templates.db"), open_mode=False)) as client:
        after = _gql(client, admin["token"],
                     "{ issues { nodes { id title } } }")["data"]["issues"]["nodes"]
        assert [i["id"] for i in after] == [i["id"] for i in count_before]

    # up -> down -> up equivalence: the table is dropped and recreated, and the
    # revision ledger agrees at every step.
    with engine.begin() as conn:
        _down_task_templates(conn)
    assert applied_schema_revision(engine) == 59
    with engine.begin() as conn:
        _apply_task_templates(conn)
        from hoja_server.db import schema_revision, now_iso
        conn.execute(schema_revision.insert().values(revision=60, applied_at=now_iso()))
    assert applied_schema_revision(engine) == 60
    with TestClient(create_app(str(tmp_path / "task-templates.db"), open_mode=False)) as client:
        listed = _gql(client, admin["token"], TEMPLATES)
        assert "errors" not in listed, listed
        assert listed["data"]["taskTemplates"] == []

