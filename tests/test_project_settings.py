"""HOJA-489: workspace project statuses and project templates.

The acceptance bar from the issue, as tests:

  * a workspace admin reorders statuses and the order is enforced on project
    transitions (the first status is the default for a new project; an
    unconfigured state is refused by the API);
  * creating a project from a template yields the same project the API would
    produce field-for-field;
  * a member is refused by the API, not merely hidden from the UI;
  * the migration survives a second boot with its rows intact (durability).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import applied_schema_revision, make_engine, migrate, seed_tenant
from hoja_server.store import Store

STATUSES = "{ projectStatuses { id name category position } }"
CREATE_STATUS = (
    "mutation($i: ProjectStatusInput!) { projectStatusCreate(input: $i) "
    "{ success projectStatus { id name category position } } }"
)
REORDER = "mutation($ids: [String!]!) { projectStatusReorder(ids: $ids) }"
UPDATE_STATUS = (
    "mutation($id: String!, $i: ProjectStatusInput!) { projectStatusUpdate(id: $id, input: $i) "
    "{ success projectStatus { id name color } } }"
)
DELETE_STATUS = "mutation($id: String!) { projectStatusDelete(id: $id) }"

TEMPLATES = "{ projectTemplates { id name description defaultState taskSet icon color " \
            "lead { name } teams { id name key } } }"
CREATE_TEMPLATE = (
    "mutation($i: ProjectTemplateInput!) { projectTemplateCreate(input: $i) "
    "{ success projectTemplate { id name } } }"
)
CREATE_PROJECT = (
    "mutation($i: ProjectCreateInput!) { projectCreate(input: $i) "
    "{ success project { id name description state icon color "
    "lead { name } teams { nodes { id key } } } } }"
)
UPDATE_PROJECT = (
    "mutation($id: String!, $i: ProjectUpdateInput!) { projectUpdate(id: $id, input: $i) "
    "{ success project { id state } } }"
)
PROJECT_ISSUES = (
    "query($pid: ID!) { issues(filter: {project: {id: {eq: $pid}}}, first: 50) "
    "{ nodes { id title } } }"
)


def _setup(tmp_path):
    db_path = tmp_path / "project-settings.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Settings Org", org_url_key="settings-org",
                        user_email="owner@settings.test", token="owner-token", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _member_token(store, admin, client):
    from tests.test_member_management import _add_member
    member_id = _add_member(client, admin["token"], "member@settings.test")
    _, key = store.create_api_key(admin["org_id"], member_id, "member key", "read_write", None)
    return key


def _team_id(client, token):
    res = _gql(client, token, "{ teams { nodes { id key } } }")
    return res["data"]["teams"]["nodes"][0]["id"]


def test_statuses_seeded_in_order_and_mutating_them_is_admin_only(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        listed = _gql(client, admin["token"], STATUSES)
        assert "errors" not in listed, listed
        rows = listed["data"]["projectStatuses"]
        # The six state names the frontend already wrote into project.state.
        assert [r["name"] for r in rows] == [
            "backlog", "planned", "started", "paused", "completed", "canceled"]
        assert [r["position"] for r in rows] == list(range(6))

        member = _member_token(store, admin, client)
        refused = _gql(client, member, STATUSES)
        assert refused.get("errors"), refused
        refused_create = _gql(client, member, CREATE_STATUS, {"i": {"name": "x", "category": "started"}})
        assert refused_create.get("errors"), refused_create
        assert "FORBIDDEN" in str(refused_create)


def test_creating_and_renaming_a_status_returns_it(tmp_path) -> None:
    """Regression: create/update must return the row, not a raw SQLAlchemy Row.

    The UI probe caught this as "Internal error" while every list/reorder test
    stayed green, so the mutation path gets its own assertion.
    """
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        made = _gql(client, admin["token"], CREATE_STATUS,
                    {"i": {"name": "QA review", "category": "started", "color": "#00aa00"}})
        assert "errors" not in made, made
        created = made["data"]["projectStatusCreate"]["projectStatus"]
        assert created["name"] == "QA review" and created["position"] == 6
        renamed = _gql(client, admin["token"], UPDATE_STATUS,
                       {"id": created["id"], "i": {"name": "QA", "category": "started"}})
        assert "errors" not in renamed, renamed
        assert renamed["data"]["projectStatusUpdate"]["projectStatus"]["name"] == "QA"
        # A duplicate name is refused by the API, not stored twice.
        dup = _gql(client, admin["token"], CREATE_STATUS,
                   {"i": {"name": "QA", "category": "started"}})
        assert dup.get("errors"), dup


def test_reorder_defines_the_default_and_transitions_are_enforced(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        rows = _gql(client, admin["token"], STATUSES)["data"]["projectStatuses"]
        ids = [r["id"] for r in rows]
        # Move 'started' to the front: the first status is the new default.
        started = next(r for r in rows if r["name"] == "started")
        new_order = [started["id"]] + [i for i in ids if i != started["id"]]
        ok = _gql(client, admin["token"], REORDER, {"ids": new_order})
        assert ok["data"]["projectStatusReorder"] is True, ok

        after = _gql(client, admin["token"], STATUSES)["data"]["projectStatuses"]
        assert [r["name"] for r in after][0] == "started"

        # A new project takes the first status when none is given.
        created = _gql(client, admin["token"], CREATE_PROJECT, {"i": {"name": "Default state"}})
        assert "errors" not in created, created
        assert created["data"]["projectCreate"]["project"]["state"] == "started"
        pid = created["data"]["projectCreate"]["project"]["id"]

        # An unconfigured transition is refused by the API, not silently stored.
        bad = _gql(client, admin["token"], UPDATE_PROJECT,
                   {"id": pid, "i": {"state": "archived"}})
        assert bad.get("errors"), bad
        assert "not a configured project status" in str(bad)

        # A configured transition is accepted and stored under its own spelling.
        good = _gql(client, admin["token"], UPDATE_PROJECT,
                    {"id": pid, "i": {"state": "COMPLETED"}})
        assert "errors" not in good, good
        assert good["data"]["projectUpdate"]["project"]["state"] == "completed"

        # Reorder must list exactly the current set, or it is refused.
        partial = _gql(client, admin["token"], REORDER, {"ids": ids[:2]})
        assert partial.get("errors"), partial


def test_template_expands_the_same_project_the_api_would_produce(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id = _team_id(client, admin["token"])
        created = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Launch plan",
            "description": "Everything a launch needs.",
            "state": "planned",
            "icon": "rocket",
            "color": "#123456",
            "teamIds": [team_id],
            "taskSet": ["Write the brief", "Ship the build"],
        }})
        assert "errors" not in created, created
        template = created["data"]["projectTemplateCreate"]["projectTemplate"]

        # From the template: only the template id and an empty name, so every
        # other field must come from the template.
        from_template = _gql(client, admin["token"], CREATE_PROJECT,
                             {"i": {"name": "", "templateId": template["id"]}})
        assert "errors" not in from_template, from_template
        a = from_template["data"]["projectCreate"]["project"]
        assert a["name"] == "Launch plan"
        assert a["description"] == "Everything a launch needs."
        assert a["state"] == "planned"
        assert a["icon"] == "rocket" and a["color"] == "#123456"
        assert [t["key"] for t in a["teams"]["nodes"]]

        # Explicitly: the same fields the API would take directly.
        explicit = _gql(client, admin["token"], CREATE_PROJECT, {"i": {
            "name": "Launch plan",
            "description": "Everything a launch needs.",
            "state": "planned",
            "icon": "rocket",
            "color": "#123456",
            "teamIds": [team_id],
        }})
        assert "errors" not in explicit, explicit
        b = explicit["data"]["projectCreate"]["project"]

        for field in ("name", "description", "state", "icon", "color"):
            assert a[field] == b[field], (field, a[field], b[field])
        assert sorted(t["id"] for t in a["teams"]["nodes"]) == \
            sorted(t["id"] for t in b["teams"]["nodes"])

        # The optional task set became real tasks in the template-created project.
        issues = _gql(client, admin["token"], PROJECT_ISSUES, {"pid": a["id"]})
        titles = sorted(i["title"] for i in issues["data"]["issues"]["nodes"])
        assert titles == ["Ship the build", "Write the brief"], issues
        assert _gql(client, admin["token"], PROJECT_ISSUES, {"pid": b["id"]})[
            "data"]["issues"]["nodes"] == []

        # A task set without a team is a configuration error, not a silent skip.
        no_team = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {
            "name": "Orphan", "taskSet": ["A task"]}})
        assert no_team.get("errors"), no_team


def test_templates_are_admin_only_and_archivable(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member = _member_token(store, admin, client)
        assert _gql(client, member, TEMPLATES).get("errors")
        assert _gql(client, member, CREATE_TEMPLATE, {"i": {"name": "Nope"}}).get("errors")

        created = _gql(client, admin["token"], CREATE_TEMPLATE, {"i": {"name": "Temp"}})
        tid = created["data"]["projectTemplateCreate"]["projectTemplate"]["id"]
        archived = _gql(client, admin["token"],
                        "mutation($id: String!) { projectTemplateArchive(id: $id) }",
                        {"id": tid})
        assert archived["data"]["projectTemplateArchive"] is True
        remaining = _gql(client, admin["token"], TEMPLATES)["data"]["projectTemplates"]
        assert tid not in [t["id"] for t in remaining]


def test_migration_is_idempotent_and_rows_survive_a_second_boot(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        created = _gql(client, admin["token"], CREATE_TEMPLATE,
                       {"i": {"name": "Durable template"}})
        assert "errors" not in created, created
    # Second boot on the same file: migration is a no-op and the row is there.
    engine2 = make_engine(tmp_path / "project-settings.db")
    migrate(engine2)
    assert applied_schema_revision(engine2) == 58
    migrate(engine2)  # idempotent by construction (ledger at revision 57)
    app2 = create_app(str(tmp_path / "project-settings.db"), open_mode=False)
    with TestClient(app2) as client:
        rows = _gql(client, admin["token"], TEMPLATES)["data"]["projectTemplates"]
        assert [t["name"] for t in rows] == ["Durable template"]
        statuses = _gql(client, admin["token"], STATUSES)["data"]["projectStatuses"]
        assert len(statuses) == 6


def test_last_status_cannot_be_deleted(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        rows = _gql(client, admin["token"], STATUSES)["data"]["projectStatuses"]
        for row in rows[:-1]:
            assert _gql(client, admin["token"], DELETE_STATUS, {"id": row["id"]})[
                "data"]["projectStatusDelete"] is True
        last = _gql(client, admin["token"], DELETE_STATUS, {"id": rows[-1]["id"]})
        assert last.get("errors"), last
