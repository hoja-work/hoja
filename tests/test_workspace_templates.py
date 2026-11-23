"""HOJA-742: workspace templates — portable settings bundles an admin previews and applies.

What these tests hold the feature to:

* every built-in applies cleanly to a fresh workspace, and applying it again changes nothing;
* preview writes nothing, and apply executes exactly the plan preview showed;
* apply is additive: settings the template does not name survive, fields it leaves out keep
  their value, and nothing is archived;
* a bad document is refused with the path of the bad field, and a half-valid one writes nothing;
* the defaults the template sets take effect: a member who never chose follows the workspace's
  notification default (their own choice still wins), and a new agent starts with the default
  policy;
* export produces a document that rebuilds the same settings in another workspace;
* only admins can use any of it, and an apply that changes something leaves an audit row.
"""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import audit_event, make_engine, migrate, seed_tenant, sla_clock
from hoja_server.store import Store
from hoja_server.workspace_template_builtins import (
    AGENTS_DO_THE_WRITING,
    BUILTINS,
    SMALL_PRODUCT_TEAM,
)

PLAN_FIELDS = "name creates updates unchanged startedClocks steps { section name action fields }"
PREVIEW = f"query($d: JSON!) {{ workspaceTemplatePreview(document: $d) {{ {PLAN_FIELDS} }} }}"
APPLY = (f"mutation($d: JSON!, $s: Boolean) {{ workspaceTemplateApply(document: $d, "
         f"startSlaClocks: $s) {{ {PLAN_FIELDS} }} }}")
BUILTIN_LIST = "{ workspaceTemplates { id name description document } }"
EXPORT = "{ workspaceTemplateExport { document omitted } }"
POLICIES = ("{ slaPolicies { name appliesPriorities firstResponseMinutes resolutionMinutes "
            "stopsOn cycleBound pauseStates { type } } }")
NOTIFS = "{ myNotificationPreferences { issueUpdated issueAssigned issueStatusChanged } }"


def _setup(tmp_path, name="tmpl"):
    db_path = tmp_path / f"{name}.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name=f"{name} org", org_url_key=f"{name}-org",
                        user_email=f"owner@{name}.test", token=f"{name}-owner",
                        demo_issues=False)
    return engine, create_app(str(db_path), open_mode=False), admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _ok(body, field):
    assert "errors" not in body, body
    return body["data"][field]


def _member(store, admin, client, email):
    from tests.test_member_management import _add_member
    member_id = _add_member(client, admin["token"], email)
    _, key = store.create_api_key(admin["org_id"], member_id, "member key", "read_write", None)
    return key


@pytest.mark.parametrize("builtin", [b["document"] for b in BUILTINS], ids=[b["id"] for b in BUILTINS])
def test_each_builtin_applies_cleanly_and_reapplying_changes_nothing(tmp_path, builtin) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        listed = _ok(_gql(client, admin["token"], BUILTIN_LIST), "workspaceTemplates")
        assert builtin["id"] in {t["id"] for t in listed}

        preview = _ok(_gql(client, admin["token"], PREVIEW, {"d": builtin}), "workspaceTemplatePreview")
        assert preview["updates"] == 0 and preview["creates"] == len(preview["steps"]) > 0
        # Preview wrote nothing: previewing again still plans the same creates.
        again = _ok(_gql(client, admin["token"], PREVIEW, {"d": builtin}), "workspaceTemplatePreview")
        assert again == preview

        applied = _ok(_gql(client, admin["token"], APPLY, {"d": builtin}), "workspaceTemplateApply")
        assert applied["steps"] == preview["steps"], "apply executes exactly the previewed plan"

        second = _ok(_gql(client, admin["token"], APPLY, {"d": builtin}), "workspaceTemplateApply")
        assert second["creates"] == 0 and second["updates"] == 0
        assert second["unchanged"] == len(second["steps"])


def test_agent_template_removes_backlog_pause_and_sets_the_triage_target(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        _ok(_gql(client, admin["token"], APPLY, {"d": AGENTS_DO_THE_WRITING}), "workspaceTemplateApply")
        policies = {p["name"]: p for p in _ok(_gql(client, admin["token"], POLICIES), "slaPolicies")}
        assert all(p["pauseStates"] == [] for p in policies.values()), \
            "agent-filed work must not sit paused in Backlog (HOJA-741)"
        triage = policies["Triage: needs a person"]
        assert triage["appliesPriorities"] == [0]
        assert triage["stopsOn"] == "prioritized" and triage["resolutionMinutes"] == 2 * 24 * 60


def test_backlog_pause_types_resolve_to_every_backlog_state(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        _ok(_gql(client, admin["token"], APPLY, {"d": SMALL_PRODUCT_TEAM}), "workspaceTemplateApply")
        policies = _ok(_gql(client, admin["token"], POLICIES), "slaPolicies")
        for p in policies:
            assert p["pauseStates"] and {s["type"] for s in p["pauseStates"]} == {"backlog"}


def test_apply_is_additive_and_only_changes_named_fields(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        made = _gql(client, admin["token"],
                    'mutation { issueLabelCreate(input: { name: "Keep me", color: "#111111" }) { success } }')
        assert "errors" not in made, made
        doc = copy.deepcopy(SMALL_PRODUCT_TEAM)
        _ok(_gql(client, admin["token"], APPLY, {"d": doc}), "workspaceTemplateApply")

        edited = {"format": doc["format"], "version": 1, "name": "Tweak",
                  "labels": [{"name": "bug", "color": "#000000"}],
                  "slaPolicies": [{"name": "Urgent", "resolutionMinutes": 600}]}
        preview = _ok(_gql(client, admin["token"], PREVIEW, {"d": edited}), "workspaceTemplatePreview")
        steps = {(s["section"], s["name"]): s for s in preview["steps"]}
        assert steps[("labels", "bug")]["action"] == "update", "labels match by name, any case"
        assert steps[("labels", "bug")]["fields"] == ["color"]
        assert steps[("slaPolicies", "Urgent")]["fields"] == ["resolution_minutes"]
        _ok(_gql(client, admin["token"], APPLY, {"d": edited}), "workspaceTemplateApply")

        labels = _ok(_gql(client, admin["token"], "{ issueLabels { nodes { name color } } }"),
                     "issueLabels")["nodes"]
        names = {label["name"]: label["color"] for label in labels}
        assert names["Keep me"] == "#111111", "a label the template does not name survives"
        assert names["Bug"] == "#000000" and "Feature" in names, "nothing is deleted"
        urgent = next(p for p in _ok(_gql(client, admin["token"], POLICIES), "slaPolicies")
                      if p["name"] == "Urgent")
        assert urgent["resolutionMinutes"] == 600
        assert urgent["firstResponseMinutes"] == 120, "a field the template leaves out keeps its value"
        assert {s["type"] for s in urgent["pauseStates"]} == {"backlog"}


@pytest.mark.parametrize("mutate, path", [
    (lambda d: d.update(format="something-else"), "format"),
    (lambda d: d.update(version=2), "version"),
    (lambda d: d.update(extra=[]), "extra"),
    (lambda d: d["taskTemplates"][0].update(defaultLabels=["No such label"]),
     "taskTemplates[0].defaultLabels"),
    (lambda d: d["projectTemplates"][0].update(state="Nowhere"), "projectTemplates[0].state"),
    (lambda d: d["slaPolicies"][0].update(pauseStateTypes=["waiting"]), "slaPolicies[0].pauseStateTypes"),
    (lambda d: d["slaPolicies"][1].update(firstResponseMinutes=0), "slaPolicies[1]"),
    (lambda d: d["notificationDefaults"].update(notAKind=True), "notificationDefaults.notAKind"),
    (lambda d: d["labels"].append({"name": "bug"}), "labels[6].name"),
])
def test_a_bad_document_is_refused_with_its_path_and_writes_nothing(tmp_path, mutate, path) -> None:
    _engine, app, admin = _setup(tmp_path)
    doc = copy.deepcopy(AGENTS_DO_THE_WRITING)
    mutate(doc)
    with TestClient(app) as client:
        refused = _gql(client, admin["token"], APPLY, {"d": doc})
        assert refused.get("errors"), refused
        error = refused["errors"][0]
        assert error["extensions"]["code"] == "BAD_USER_INPUT"
        assert error["extensions"]["field"] == path, error
        # Every section is valid except one, so a partial write would show up here.
        assert _ok(_gql(client, admin["token"], POLICIES), "slaPolicies") == []
        assert _ok(_gql(client, admin["token"], "{ issueLabels { nodes { name } } }"),
                   "issueLabels")["nodes"] == []


def test_notification_defaults_apply_to_members_who_never_chose(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member = _member(store, admin, client, "member@tmpl.test")
        before = _ok(_gql(client, member, NOTIFS), "myNotificationPreferences")
        assert before["issueUpdated"] is True

        _ok(_gql(client, admin["token"], APPLY, {"d": AGENTS_DO_THE_WRITING}), "workspaceTemplateApply")
        after = _ok(_gql(client, member, NOTIFS), "myNotificationPreferences")
        assert after["issueUpdated"] is False and after["issueStatusChanged"] is False
        assert after["issueAssigned"] is True

        chose = _gql(client, member, 'mutation { notificationPreferenceSet(kind: "issueUpdated", '
                                     "enabled: true) { success } }")
        assert "errors" not in chose, chose
        mine = _ok(_gql(client, member, NOTIFS), "myNotificationPreferences")
        assert mine["issueUpdated"] is True, "a person's own choice beats the workspace default"


def test_cloud_only_sections_are_accepted_and_ignored(tmp_path) -> None:
    """A Hoja Cloud template carries knowledge and an agent policy default. This edition has
    neither, so it loads the template, plans no step for them, and never serves them."""
    _engine, app, admin = _setup(tmp_path)
    doc = copy.deepcopy(AGENTS_DO_THE_WRITING)
    assert doc.get("knowledge") and doc.get("agentPolicyDefault"), "the fixture must carry both"
    with TestClient(app) as client:
        applied = _ok(_gql(client, admin["token"], APPLY, {"d": doc}), "workspaceTemplateApply")
        sections = {step["section"] for step in applied["steps"]}
        assert not sections & {"knowledge", "agentPolicyDefault"}, sections
        listed = _ok(_gql(client, admin["token"], "{ workspaceTemplates { id document } }"),
                     "workspaceTemplates")
        for template in listed:
            assert "knowledge" not in template["document"], template["id"]
            assert "agentPolicyDefault" not in template["document"], template["id"]


def test_export_rebuilds_the_same_settings_in_another_workspace(tmp_path) -> None:
    _e1, app_a, admin_a = _setup(tmp_path, "alpha")
    _e2, app_b, admin_b = _setup(tmp_path, "beta")
    with TestClient(app_a) as a, TestClient(app_b) as b:
        _ok(_gql(a, admin_a["token"], APPLY, {"d": AGENTS_DO_THE_WRITING}), "workspaceTemplateApply")
        exported = _ok(_gql(a, admin_a["token"], EXPORT), "workspaceTemplateExport")
        assert exported["omitted"] == []
        document = exported["document"]

        applied = _ok(_gql(b, admin_b["token"], APPLY, {"d": document}), "workspaceTemplateApply")
        assert applied["creates"] > 0
        reexport = _ok(_gql(b, admin_b["token"], EXPORT), "workspaceTemplateExport")["document"]
        assert reexport == document
        # And the original template is fully in place in the second workspace.
        check = _ok(_gql(b, admin_b["token"], PREVIEW, {"d": AGENTS_DO_THE_WRITING}),
                    "workspaceTemplatePreview")
        assert check["creates"] == 0 and check["updates"] == 0


def test_start_sla_clocks_covers_open_tasks(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team = _ok(_gql(client, admin["token"], "{ teams { nodes { id } } }"), "teams")["nodes"][0]
        made = _gql(client, admin["token"],
                    "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success } }",
                    {"i": {"teamId": team["id"], "title": "Open before the template"}})
        assert "errors" not in made, made
        result = _ok(_gql(client, admin["token"], APPLY, {"d": AGENTS_DO_THE_WRITING, "s": True}),
                     "workspaceTemplateApply")
        assert result["startedClocks"] == 1
        with engine.connect() as conn:
            assert len(conn.execute(select(sla_clock)).all()) == 1


def test_admin_only_and_audited(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member = _member(store, admin, client, "member2@tmpl.test")
        for query, variables in ((BUILTIN_LIST, None), (EXPORT, None),
                                 (PREVIEW, {"d": SMALL_PRODUCT_TEAM}),
                                 (APPLY, {"d": SMALL_PRODUCT_TEAM})):
            refused = _gql(client, member, query, variables)
            assert "FORBIDDEN" in str(refused.get("errors")), refused

        _ok(_gql(client, admin["token"], APPLY, {"d": SMALL_PRODUCT_TEAM}), "workspaceTemplateApply")
        _ok(_gql(client, admin["token"], APPLY, {"d": SMALL_PRODUCT_TEAM}), "workspaceTemplateApply")
        with engine.connect() as conn:
            rows = conn.execute(select(audit_event).where(
                audit_event.c.action == "workspace.template.apply")).all()
        assert len(rows) == 1, "an apply that changes nothing leaves no audit row"
