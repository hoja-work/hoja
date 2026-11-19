"""HOJA-704: a configurable, cycle-aware SLA clock.

The owner's sentence — "activate a thorough SLA that works with our cycle …
make it so people can configure it whatever way they want" — as tests. Each
one pins one option of the policy to observable behaviour:

  * a cycle-bound policy's resolution deadline is the end of the task's cycle;
  * moving a task to the next cycle after its cycle ended is a breach, never an
    erasure of one;
  * a priority change re-targets the clock to the policy that now covers it;
  * a triage policy (``stops_on=prioritized``) resolves when the priority
    arrives, and the priority's own policy then takes the task over;
  * ``business_days_only`` skips the weekend, in the team's zone;
  * a pause in a pause state still shifts both deadlines;
  * ``applyToOpen`` starts clocks at the moment it runs — never back-dated and
    never a breach nobody could have avoided.

Nothing here waits: a real hour of waiting is a row aged by one line of SQL, the
same technique ``test_slas.py`` uses for a breach.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import db, sla
from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    cycle,
    issue_activity,
    make_engine,
    metadata,
    migrate,
    new_id,
    now_iso,
    schema_revision,
    sla_clock,
    sla_policy,
    team,
)
from tests.test_slas import _gql, _setup, _team_and_states

CREATE_POLICY = (
    "mutation($i: SlaPolicyInput!) { slaPolicyCreate(input: $i) "
    "{ success startedClocks slaPolicy { id name cycleBound stopsOn "
    "businessDaysOnly appliesPriorities firstResponseMinutes resolutionMinutes } } }"
)
APPLY_POLICY = (
    "mutation($id: String!) { slaPolicyApply(id: $id) "
    "{ success startedClocks slaPolicy { id name } } }"
)
CREATE_TASK = (
    "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success "
    "issue { id identifier sla { state resolutionDueAt firstResponseDueAt "
    "cycleEndsAt retargetedAt breachedAt startedAt policy { id name } } } } }"
)
READ_TASK = (
    "query($ref: String!) { issue(id: $ref) { id sla { state overdueMinutes "
    "pausedMinutes breachedAt startedAt retargetedAt cycleEndsAt "
    "resolutionDueAt policy { id name } } } }"
)
UPDATE_TASK = (
    "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) "
    "{ success } }"
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _insert_cycle(engine, org_id, team_id, number, starts_at, ends_at, name=None):
    cid = new_id()
    with engine.begin() as conn:
        conn.execute(cycle.insert().values(
            id=cid, organization_id=org_id, team_id=team_id, number=number,
            name=name or f"Cycle {number}", starts_at=_iso(starts_at),
            ends_at=_iso(ends_at), created_at=now_iso(), updated_at=now_iso()))
    return cid


def _events(engine, issue_id):
    with engine.connect() as conn:
        return list(conn.execute(select(issue_activity.c.event_type).where(
            issue_activity.c.issue_id == issue_id)).scalars())


def _changes(engine, issue_id, event_type):
    import json
    with engine.connect() as conn:
        rows = list(conn.execute(select(issue_activity.c.changes).where(and_(
            issue_activity.c.issue_id == issue_id,
            issue_activity.c.event_type == event_type))).scalars())
    return [json.loads(r) for r in rows]


def _clock(client, token, ref):
    data = _gql(client, token, READ_TASK, {"ref": ref})
    assert "errors" not in data, data
    return data["data"]["issue"]["sla"]


def _create(client, token, inp):
    made = _gql(client, token, CREATE_TASK, {"i": inp})
    assert "errors" not in made, made
    return made["data"]["issueCreate"]["issue"]


def _policy(client, token, inp):
    made = _gql(client, token, CREATE_POLICY, {"i": inp})
    assert "errors" not in made, made
    return made["data"]["slaPolicyCreate"]["slaPolicy"]


# --------------------------------------------------------------------- cycle-bound


def test_cycle_bound_deadline_is_the_cycle_end(tmp_path) -> None:
    """The resolution target follows the cycle, and falls back to minutes alone."""
    engine, app, admin = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        ends = now + timedelta(days=3)
        cycle_id = _insert_cycle(engine, admin["org_id"], team_id, 1,
                                 now - timedelta(days=4), ends)
        policy = _policy(client, admin["token"], {
            "name": "Cycle", "teamId": team_id, "resolutionMinutes": 60,
            "cycleBound": True})
        assert policy["cycleBound"] is True
        # An option nobody set keeps today's value rather than inventing one.
        assert policy["stopsOn"] == "completed"
        assert policy["businessDaysOnly"] is False

        bounded = _create(client, admin["token"],
                          {"teamId": team_id, "title": "In a cycle", "cycleId": cycle_id})
        clock = bounded["sla"]
        assert _at(clock["resolutionDueAt"]) == ends, clock
        assert _at(clock["cycleEndsAt"]) == ends, clock

        loose = _create(client, admin["token"], {"teamId": team_id, "title": "No cycle"})
        assert loose["sla"]["cycleEndsAt"] is None, loose
        minutes = (_at(loose["sla"]["resolutionDueAt"]) - now).total_seconds() / 60
        assert 59 <= minutes <= 61, loose["sla"]


def test_moving_to_the_next_cycle_after_a_cycle_ended_is_a_breach(tmp_path) -> None:
    """The deadline follows the new cycle; the breach the old one earned stays.

    The read happens only after the move, and by then the deadline is in the
    future — so a stamped ``breachedAt`` can only have come from the re-target,
    which is exactly the rule under test.
    """
    engine, app, admin = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        ended = _insert_cycle(engine, admin["org_id"], team_id, 1,
                              now - timedelta(days=7), now - timedelta(minutes=1))
        upcoming = _insert_cycle(engine, admin["org_id"], team_id, 2,
                                 now - timedelta(minutes=1), now + timedelta(days=6))
        _policy(client, admin["token"], {
            "name": "Cycle", "teamId": team_id, "resolutionMinutes": 60,
            "cycleBound": True})
        task = _create(client, admin["token"],
                       {"teamId": team_id, "title": "Carried over", "cycleId": ended})

        moved = _gql(client, admin["token"], UPDATE_TASK,
                     {"id": task["id"], "i": {"cycleId": upcoming}})
        assert "errors" not in moved, moved

        clock = _clock(client, admin["token"], task["id"])
        assert _at(clock["resolutionDueAt"]) == now + timedelta(days=6), clock
        assert clock["breachedAt"] is not None, (
            "a cycle that ended under the task is a breach, not an erasure", clock)
        assert clock["state"] == "breached", clock
        assert clock["retargetedAt"] is not None, clock
        assert "slaRetargeted" in _events(engine, task["id"])


# ------------------------------------------------------------------ re-targeting


def test_a_priority_change_retargets_the_clock(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        _policy(client, admin["token"], {
            "name": "Urgent", "teamId": team_id, "appliesPriorities": [1],
            "resolutionMinutes": 30})
        _policy(client, admin["token"], {
            "name": "Medium", "teamId": team_id, "appliesPriorities": [3],
            "resolutionMinutes": 240})
        task = _create(client, admin["token"],
                       {"teamId": team_id, "title": "Escalated", "priority": 3})
        before = _clock(client, admin["token"], task["id"])
        assert before["policy"]["name"] == "Medium", before

        started = datetime.now(timezone.utc)
        _gql(client, admin["token"], UPDATE_TASK,
             {"id": task["id"], "i": {"priority": 1}})
        after = _clock(client, admin["token"], task["id"])
        assert after["policy"]["name"] == "Urgent", after
        assert after["retargetedAt"] is not None, after
        minutes = (_at(after["resolutionDueAt"]) - started).total_seconds() / 60
        assert 29 <= minutes <= 31, after
        assert "slaRetargeted" in _events(engine, task["id"])
        changes = _changes(engine, task["id"], "slaRetargeted")
        assert changes[-1]["toPolicyId"] == after["policy"]["id"], changes


def test_a_triage_clock_resolves_when_the_priority_arrives(tmp_path) -> None:
    """``stops_on=prioritized`` is the "untriaged within N" policy.

    The triage clock's question is answered the moment a priority lands, and the
    priority's own policy starts a new clock from that instant.
    """
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        triage = _policy(client, admin["token"], {
            "name": "Triage", "teamId": team_id, "appliesPriorities": [0],
            "firstResponseMinutes": 2880, "stopsOn": "prioritized"})
        assert triage["stopsOn"] == "prioritized"
        _policy(client, admin["token"], {
            "name": "Urgent", "teamId": team_id, "appliesPriorities": [1],
            "resolutionMinutes": 30})
        task = _create(client, admin["token"], {"teamId": team_id, "title": "Untriaged"})
        before = _clock(client, admin["token"], task["id"])
        assert before["policy"]["name"] == "Triage", before
        assert before["state"] == "ok", before

        started = datetime.now(timezone.utc)
        _gql(client, admin["token"], UPDATE_TASK,
             {"id": task["id"], "i": {"priority": 1}})
        after = _clock(client, admin["token"], task["id"])
        assert after["policy"]["name"] == "Urgent", after
        # The new clock starts when the priority arrived, not when the task was made.
        assert (_at(after["startedAt"]) - started).total_seconds() < 5, after
        resolved = _changes(engine, task["id"], "slaResolved")
        assert resolved and resolved[-1]["reason"] == "prioritized", resolved
        assert "slaRetargeted" in _events(engine, task["id"])


def test_stops_on_started_ends_the_clock_when_work_begins(tmp_path) -> None:
    """A policy can measure "somebody picked it up", which is not completion."""
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, states = _team_and_states(client, admin["token"])
        backlog = next(s for s in states if s["type"] == "backlog")
        started_state = next(s for s in states if s["type"] == "started")
        _policy(client, admin["token"], {
            "name": "Pickup", "teamId": team_id, "resolutionMinutes": 60,
            "stopsOn": "started"})
        task = _create(client, admin["token"],
                       {"teamId": team_id, "title": "Pick me up",
                        "stateId": backlog["id"]})
        assert _clock(client, admin["token"], task["id"])["state"] == "ok"
        _gql(client, admin["token"], UPDATE_TASK,
             {"id": task["id"], "i": {"stateId": started_state["id"]}})
        clock = _clock(client, admin["token"], task["id"])
        assert clock["state"] == "met", clock
        assert clock["overdueMinutes"] == 0, clock


# ----------------------------------------------------------------- business days


def test_business_days_arithmetic_skips_the_weekend() -> None:
    zone = ZoneInfo("UTC")
    friday_noon = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    # One day of minutes from Friday noon is Monday noon.
    assert sla._add_minutes(friday_noon, 1440, True, zone) == \
        datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    # Without the option the same start lands on Saturday, which is the bug the
    # option exists to avoid.
    assert sla._add_minutes(friday_noon, 1440, False, zone) == \
        datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    # A weekend start counts from Monday.
    saturday = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
    assert sla._add_minutes(saturday, 60, True, zone) == \
        datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    # A target that ends before the weekend does not move at all.
    assert sla._add_minutes(friday_noon, 60, True, zone) == \
        datetime(2026, 11, 20, 13, 0, tzinfo=timezone.utc)


def test_business_days_only_counts_in_the_teams_timezone(tmp_path) -> None:
    """The zone decides which days are weekend days — a team east of UTC proves it."""
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        _policy(client, admin["token"], {
            "name": "Weekdays", "teamId": team_id, "resolutionMinutes": 1440,
            "businessDaysOnly": True})
        with engine.connect() as conn:
            policy = dict(conn.execute(select(sla_policy).where(
                sla_policy.c.name == "Weekdays")).mappings().one())
        # Thursday 22:00 UTC: in Auckland that is already Friday 11:00, so the
        # same 24 business hours run out on a different local day.
        thursday_evening = datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc)
        issue_row = {"team_id": team_id, "cycle_id": None, "state_id": None}

        # UTC: one business day from Thursday 22:00 is Friday 22:00.
        with engine.begin() as conn:
            conn.execute(team.update().where(team.c.id == team_id).values(timezone=None))
        with engine.connect() as conn:
            _fr, res_utc = sla._deadlines(conn, admin["org_id"], issue_row,
                                          policy, thursday_evening)
        assert res_utc == datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc), res_utc

        # Auckland is already Friday 11:00 then, so the same 24 business hours
        # run out on Monday 11:00 NZDT — still Sunday in UTC.
        with engine.begin() as conn:
            conn.execute(team.update().where(team.c.id == team_id).values(
                timezone="Pacific/Auckland"))
        with engine.connect() as conn:
            _fr, res_nz = sla._deadlines(conn, admin["org_id"], issue_row,
                                         policy, thursday_evening)
        assert res_nz == datetime(2026, 11, 22, 22, 0, tzinfo=timezone.utc), res_nz
        # An unknown zone falls back to UTC rather than failing a task read.
        with engine.begin() as conn:
            conn.execute(team.update().where(team.c.id == team_id).values(
                timezone="Not/AZone"))
        with engine.connect() as conn:
            _fr, res_fallback = sla._deadlines(conn, admin["org_id"], issue_row,
                                               policy, thursday_evening)
        assert res_fallback == res_utc, res_fallback


# ------------------------------------------------------------------------ pauses


def test_a_pause_in_backlog_shifts_the_deadlines(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, states = _team_and_states(client, admin["token"])
        backlog = next(s for s in states if s["type"] == "backlog")
        started_state = next(s for s in states if s["type"] == "started")
        _policy(client, admin["token"], {
            "name": "Pausing", "teamId": team_id, "resolutionMinutes": 60,
            "pauseStateIds": [backlog["id"]]})
        task = _create(client, admin["token"],
                       {"teamId": team_id, "title": "Waiting", "stateId": started_state["id"]})
        before = _at(_clock(client, admin["token"], task["id"])["resolutionDueAt"])

        _gql(client, admin["token"], UPDATE_TASK,
             {"id": task["id"], "i": {"stateId": backlog["id"]}})
        assert _clock(client, admin["token"], task["id"])["state"] == "paused"
        # Two hours in the pause state, written as a row aged by SQL: the same
        # thing two real hours would have done.
        with engine.begin() as conn:
            conn.execute(sla_clock.update().where(
                sla_clock.c.issue_id == task["id"]).values(
                    paused_since=_iso(datetime.now(timezone.utc) - timedelta(hours=2))))

        _gql(client, admin["token"], UPDATE_TASK,
             {"id": task["id"], "i": {"stateId": started_state["id"]}})
        clock = _clock(client, admin["token"], task["id"])
        assert clock["state"] == "ok", clock
        assert (_at(clock["resolutionDueAt"]) - before) >= timedelta(minutes=119), clock
        assert clock["pausedMinutes"] >= 119, clock


# --------------------------------------------------------------------- applyToOpen


def test_apply_to_open_starts_clocks_at_now_and_never_back_dates(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, states = _team_and_states(client, admin["token"])
        completed = next(s for s in states if s["type"] == "completed")
        open_tasks = [
            _create(client, admin["token"], {"teamId": team_id, "title": f"Open {n}"})
            for n in range(3)
        ]
        done = _create(client, admin["token"], {
            "teamId": team_id, "title": "Already done", "stateId": completed["id"]})
        for task in open_tasks + [done]:
            assert task["sla"] is None, ("no policy yet, no clock", task)

        started = datetime.now(timezone.utc)
        made = _gql(client, admin["token"], CREATE_POLICY, {"i": {
            "name": "Applied", "teamId": team_id, "resolutionMinutes": 480,
            "applyToOpen": True}})
        assert "errors" not in made, made
        payload = made["data"]["slaPolicyCreate"]
        assert payload["startedClocks"] == 3, payload

        for task in open_tasks:
            clock = _clock(client, admin["token"], task["id"])
            # Day one is not a breach: the clock starts now, so nothing is late.
            assert clock["breachedAt"] is None, clock
            assert clock["state"] == "ok", clock
            assert (_at(clock["startedAt"]) - started).total_seconds() < 5, clock
            assert _at(clock["resolutionDueAt"]) > started, clock

        assert _clock(client, admin["token"], done["id"]) is None, (
            "a completed task is not open work")

        # Applying again starts nothing: a running clock is never moved.
        policy_id = payload["slaPolicy"]["id"]
        again = _gql(client, admin["token"], APPLY_POLICY, {"id": policy_id})
        assert again["data"]["slaPolicyApply"]["startedClocks"] == 0, again


def test_apply_to_open_starts_a_waiting_task_paused(tmp_path) -> None:
    """A task already in a pause state starts paused, not already counting.

    Otherwise applying a policy to a backlog charges every waiting task for the
    time it waited before the policy existed — a breach nobody could avoid.
    """
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        team_id, states = _team_and_states(client, admin["token"])
        backlog = next(s for s in states if s["type"] == "backlog")
        waiting = _create(client, admin["token"],
                          {"teamId": team_id, "title": "Waiting", "stateId": backlog["id"]})
        made = _gql(client, admin["token"], CREATE_POLICY, {"i": {
            "name": "Preset", "teamId": team_id, "resolutionMinutes": 60,
            "pauseStateIds": [backlog["id"]], "applyToOpen": True}})
        assert made["data"]["slaPolicyCreate"]["startedClocks"] == 1, made
        clock = _clock(client, admin["token"], waiting["id"])
        assert clock["state"] == "paused", clock
        assert clock["overdueMinutes"] == 0, clock
        assert clock["breachedAt"] is None, clock


def test_applying_a_policy_is_admin_only(tmp_path) -> None:
    from hoja_server.store import Store
    from tests.test_slas import _member_token

    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        team_id, _states = _team_and_states(client, admin["token"])
        policy = _policy(client, admin["token"], {
            "name": "Standard", "teamId": team_id, "resolutionMinutes": 60})
        member = _member_token(store, admin, client)
        refused = _gql(client, member, APPLY_POLICY, {"id": policy["id"]})
        assert "FORBIDDEN" in str(refused.get("errors")), refused


# ---------------------------------------------------------------------- migration


def test_revision_74_upgrades_a_revision_73_database_twice(tmp_path) -> None:
    """73 -> 74 twice: the ALTER probe makes a replayed deploy a no-op."""
    engine = make_engine(tmp_path / "sla-options.db")
    metadata.create_all(engine)
    with engine.begin() as conn:
        # A database that ran every older revision, as revision 73 left it: the
        # five columns are absent and the ledger stops there.
        for table, columns in (
            ("sla_policy", ("cycle_bound", "stops_on", "business_days_only")),
            ("sla_clock", ("retargeted_at", "detached_at")),
        ):
            for column in columns:
                conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        for revision in range(1, 74):
            conn.execute(schema_revision.insert().values(
                revision=revision, applied_at=now_iso()))
    assert applied_schema_revision(engine) == 73

    migrate(engine)
    # Later revisions run too; the point is that 74 applies cleanly and replays.
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION >= 74
    migrate(engine)  # the second boot is a no-op, not a replayed ALTER
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.begin() as conn:
        db._apply_sla_options(conn)  # and so is a raced owner

    from sqlalchemy import inspect
    policy_columns = {c["name"]: c for c in inspect(engine).get_columns("sla_policy")}
    for column in ("cycle_bound", "stops_on", "business_days_only"):
        assert policy_columns[column]["nullable"] is True, column
    clock_columns = {c["name"]: c for c in inspect(engine).get_columns("sla_clock")}
    for column in ("retargeted_at", "detached_at"):
        assert clock_columns[column]["nullable"] is True, column
