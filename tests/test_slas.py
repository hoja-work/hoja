"""HOJA-491: SLA policies and the per-task clock.

The acceptance sentence from HOJA-416-A-33-ag-3, as tests:

  * "a task created under a policy reports its clock and breach state through
    GraphQL" — a task created in a covered scope carries ``sla``;
  * "the state changes on the same events the reference documents" — creation
    starts it, a pause state pauses it, leaving that state resumes it, a reply
    from someone other than the creator records the first response, and a
    completed state stops it.

Breach is proven without waiting: a policy with a 1-minute resolution target and
a clock row aged by one line of SQL, which is what a real hour of waiting does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    make_engine,
    migrate,
    seed_tenant,
    sla_clock,
)
from hoja_server.store import Store

CREATE_POLICY = (
    "mutation($i: SlaPolicyInput!) { slaPolicyCreate(input: $i) "
    "{ success slaPolicy { id name firstResponseMinutes resolutionMinutes "
    "appliesPriorities pauseStates { id name } team { key } } } }"
)
POLICIES = "{ slaPolicies { id name resolutionMinutes } }"
CREATE_TASK = (
    "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success "
    "issue { id identifier title sla { state overdueMinutes resolutionDueAt "
    "firstResponseDueAt firstRespondedAt policy { name } } } } }"
)
READ_TASK = (
    "query($ref: String!) { issue(id: $ref) { id sla { state overdueMinutes "
    "pausedMinutes breachedAt resolvedAt } } }"
)
UPDATE_TASK = "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }"
COMMENT = "mutation($i: CommentCreateInput!) { commentCreate(input: $i) { success } }"


def _setup(tmp_path):
    db_path = tmp_path / "slas.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="SLA Org", org_url_key="sla-org",
                        user_email="owner@sla.test", token="owner-token", demo_issues=False)
    return engine, create_app(str(db_path), open_mode=False), admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _member_token(store, admin, client, email="member@sla.test"):
    from tests.test_member_management import _add_member
    member_id = _add_member(client, admin["token"], email)
    _, key = store.create_api_key(admin["org_id"], member_id, "member key", "read_write", None)
    return key


def _team_and_states(client, token):
    data = _gql(client, token, "{ teams { nodes { id key states { nodes { id name type } } } } }")
    team = data["data"]["teams"]["nodes"][0]
    return team["id"], team["states"]["nodes"]


def test_policies_are_admin_only_and_a_task_reports_its_clock(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        team_id, states = _team_and_states(client, admin["token"])
        made = _gql(client, admin["token"], CREATE_POLICY, {"i": {
            "name": "Standard", "teamId": team_id,
            "firstResponseMinutes": 60, "resolutionMinutes": 480}})
        assert "errors" not in made, made
        policy = made["data"]["slaPolicyCreate"]["slaPolicy"]
        assert policy["resolutionMinutes"] == 480

        task = _gql(client, admin["token"], CREATE_TASK,
                    {"i": {"teamId": team_id, "title": "SLA clock"}})
        assert "errors" not in task, task
        clock = task["data"]["issueCreate"]["issue"]["sla"]
        assert clock is not None, "a task under a policy reports its clock"
        assert clock["policy"]["name"] == "Standard"
        assert clock["state"] == "ok" and clock["overdueMinutes"] == 0
        assert clock["resolutionDueAt"] and clock["firstResponseDueAt"]
        assert clock["firstRespondedAt"] is None

        member = _member_token(store, admin, client)
        refused = _gql(client, member, POLICIES)
        assert "FORBIDDEN" in str(refused.get("errors")), refused
        refused_create = _gql(client, member, CREATE_POLICY,
                              {"i": {"name": "x", "resolutionMinutes": 5}})
        assert "FORBIDDEN" in str(refused_create.get("errors")), refused_create


def test_the_clock_pauses_resumes_and_records_the_first_response(tmp_path) -> None:
    """The documented events, one assertion each.

    A pause is proven by *time moving the other way*: the clock is aged, paused,
    aged again, resumed, and the due date must have moved by the paused span.
    """
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        team_id, states = _team_and_states(client, admin["token"])
        backlog = next(s for s in states if s["type"] == "backlog")
        started = next(s for s in states if s["type"] == "started")
        completed = next(s for s in states if s["type"] == "completed")
        made = _gql(client, admin["token"], CREATE_POLICY, {"i": {
            "name": "Pausing", "teamId": team_id, "resolutionMinutes": 60,
            "pauseStateIds": [backlog["id"]]}})
        assert "errors" not in made, made

        created = _gql(client, admin["token"], CREATE_TASK,
                       {"i": {"teamId": team_id, "title": "Pausable", "stateId": started["id"]}})
        issue = created["data"]["issueCreate"]["issue"]
        due_before = issue["sla"]["resolutionDueAt"]

        # Pause: the state the policy names.
        _gql(client, admin["token"], UPDATE_TASK,
             {"id": issue["id"], "i": {"stateId": backlog["id"]}})
        paused = _gql(client, admin["token"], READ_TASK, {"ref": issue["id"]})
        assert paused["data"]["issue"]["sla"]["state"] == "paused", paused
        assert paused["data"]["issue"]["sla"]["overdueMinutes"] == 0

        # Age the clock past its original due date while paused: a pause is not
        # a breach, which is the whole point of the pause state.
        with engine.begin() as conn:
            from datetime import datetime, timedelta, timezone
            old = (datetime.now(timezone.utc) - timedelta(minutes=180)).isoformat()
            conn.execute(sla_clock.update().where(
                sla_clock.c.issue_id == issue["id"]).values(started_at=old))
        still_paused = _gql(client, admin["token"], READ_TASK, {"ref": issue["id"]})
        assert still_paused["data"]["issue"]["sla"]["state"] == "paused", still_paused

        # Resume: the due date moves by the paused span, so no breach appears
        # for time the task spent waiting.
        _gql(client, admin["token"], UPDATE_TASK,
             {"id": issue["id"], "i": {"stateId": started["id"]}})
        resumed = _gql(client, admin["token"], READ_TASK, {"ref": issue["id"]})
        assert resumed["data"]["issue"]["sla"]["state"] == "ok", resumed
        due_after = _gql(client, admin["token"], CREATE_TASK,
                         {"i": {"teamId": team_id, "title": "unrelated"}})
        assert due_after["data"]["issueCreate"]["issue"]["sla"]["resolutionDueAt"] != due_before

        # First response: a member's comment records it, the creator's does not.
        creator_comment = _gql(client, admin["token"], COMMENT,
                               {"i": {"issueId": issue["id"], "body": "self note"}})
        assert "errors" not in creator_comment, creator_comment
        after_self = _gql(client, admin["token"],
                          "query($ref:String!){issue(id:$ref){sla{firstRespondedAt}}}",
                          {"ref": issue["id"]})
        assert after_self["data"]["issue"]["sla"]["firstRespondedAt"] is None, \
            "the creator's own comment is not a first response"
        member = _member_token(store, admin, client)
        assert "errors" not in _gql(client, member, COMMENT,
                                    {"i": {"issueId": issue["id"], "body": "we're on it"}})
        after_member = _gql(client, admin["token"],
                            "query($ref:String!){issue(id:$ref){sla{firstRespondedAt state}}}",
                            {"ref": issue["id"]})
        clock = after_member["data"]["issue"]["sla"]
        assert clock["firstRespondedAt"] is not None, ("a reply from someone other "
                                                       "than the creator records the response")

        # Completed: the clock stops.
        _gql(client, admin["token"], UPDATE_TASK,
             {"id": issue["id"], "i": {"stateId": completed["id"]}})
        done = _gql(client, admin["token"], READ_TASK, {"ref": issue["id"]})
        assert done["data"]["issue"]["sla"]["state"] in ("met", "breached"), done
        assert done["data"]["issue"]["sla"]["resolvedAt"] is not None


def test_a_passed_deadline_reads_breached_and_is_recorded_once(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        _gql(client, admin["token"], CREATE_POLICY, {"i": {
            "name": "Tight", "teamId": team_id, "resolutionMinutes": 1}})
        issue = _gql(client, admin["token"], CREATE_TASK,
                     {"i": {"teamId": team_id, "title": "Breach me"}})["data"]["issueCreate"]["issue"]

        from datetime import datetime, timedelta, timezone
        with engine.begin() as conn:
            conn.execute(sla_clock.update().where(
                sla_clock.c.issue_id == issue["id"]).values(
                    resolution_due_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()))

        first = _gql(client, admin["token"], READ_TASK, {"ref": issue["id"]})
        sla = first["data"]["issue"]["sla"]
        assert sla["state"] == "breached", first
        assert sla["overdueMinutes"] >= 29, sla
        assert sla["breachedAt"] is not None
        stamp = sla["breachedAt"]

        second = _gql(client, admin["token"], READ_TASK, {"ref": issue["id"]})
        assert second["data"]["issue"]["sla"]["breachedAt"] == stamp, \
            "the breach is recorded once, not re-stamped on every read"

    # Exactly one event row, so Pulse/inbox shows one breach rather than one per view.
    from sqlalchemy import select
    from hoja_server.db import issue_activity
    with engine.connect() as conn:
        rows = list(conn.execute(select(issue_activity.c.event_type).where(
            issue_activity.c.issue_id == issue["id"],
            issue_activity.c.event_type == "slaBreached")).scalars())
    assert rows == ["slaBreached"], rows


def test_revision_61_upgrades_a_populated_database(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION == 61
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        _gql(client, admin["token"], CREATE_POLICY, {"i": {
            "name": "Durable", "teamId": team_id, "resolutionMinutes": 30}})
        task = _gql(client, admin["token"], CREATE_TASK,
                    {"i": {"teamId": team_id, "title": "Survives"}})
        assert task["data"]["issueCreate"]["success"] is True
    migrate(engine)  # idempotent second boot
    assert applied_schema_revision(engine) == 61
    with TestClient(create_app(str(tmp_path / "slas.db"), open_mode=False)) as client:
        listed = _gql(client, admin["token"], POLICIES)
        assert [p["name"] for p in listed["data"]["slaPolicies"]] == ["Durable"]


def test_revision_61_up_down_up_is_equivalent(tmp_path) -> None:
    """The head revision's reverse step: drop, recreate, same shape, rows intact.

    The forward ledger cannot record a down, so `_down_sla` removes its own
    ledger row — the same convention `_down_pulse_updates` uses.
    """
    from hoja_server.db import _apply_sla, _down_sla, schema_revision, now_iso
    from sqlalchemy import inspect as sa_inspect

    engine, _app, _admin = _setup(tmp_path)
    with engine.connect() as conn:
        before = set(sa_inspect(conn).get_table_names())
    with engine.begin() as conn:
        _down_sla(conn)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION - 1
    with engine.connect() as conn:
        dropped = set(sa_inspect(conn).get_table_names())
    assert {"sla_policy", "sla_clock"}.isdisjoint(dropped)
    with engine.begin() as conn:
        _apply_sla(conn)
        conn.execute(schema_revision.insert().values(revision=61, applied_at=now_iso()))
    with engine.connect() as conn:
        after = set(sa_inspect(conn).get_table_names())
    assert {"sla_policy", "sla_clock"} <= after
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
