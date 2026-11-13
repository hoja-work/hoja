"""Runner lease: register, fence, idempotency, expiry (HOJA-602-A-6, WP07).

The acceptance list this file exists to prove, one test each:

=========================================  ==============================================
test                                        source
=========================================  ==============================================
stale fence -> 409 on every route           FR-018, acceptance "a stale-fence write is
                                            rejected with 409"
duplicate finish -> one effect, one ref     FR-019, acceptance "double finish is
                                            idempotent" / issue "a duplicate finish opens
                                            one PR"
expiry reassigns at fence + 1               FR-020, acceptance "lease expiry reassigns"
finish from an expired lease -> 409         issue acceptance "a finish from an expired or
                                            crashed lease is rejected"
cancel arrives on the heartbeat             WP07 T031, "cancel during heartbeat stops the run"
=========================================  ==============================================

Everything runs through the real HTTP routes on an app built by ``create_app``: these
are the server's own routes, not a stand-in. ``test_runner_conformance.py`` proves the
same routes against the *real* runner client; this file proves the policies.

SQLite by default, Postgres with ``HOJA_TEST_RLS=1 HOJA_TEST_POSTGRES_URL=...``; the
tenancy probe is only meaningful on Postgres and says so when it skips.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, func, inspect, select
from starlette.testclient import TestClient

from hoja_server import audit, runner_api, tenant_context
from hoja_server.app import create_app
from hoja_server.db import (
    agent_lease,
    agent_side_effect,
    audit_event,
    issue,
    issue_activity,
    seed_tenant,
)
from hoja_server.store import Store
from tests import rls_mode

GENERIC = "generic"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two workspaces on one database, plus the app over the real routes."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "runner-lease-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "runner-lease")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo,
            "store": Store(engine)}


def _task(engine, org_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(select(issue.c.id).where(
            issue.c.organization_id == org_id).order_by(issue.c.identifier)).scalars().first()


def _enroll(world, *, org: str = "alpha", name: str = "runner-1",
            harnesses: tuple[str, ...] = (GENERIC,)) -> dict:
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name,
        "enrollment_token": enrollment,
        "harnesses": list(harnesses),
        "labels": {"pool": "default"},
    })
    assert response.status_code == 200, response.text
    return response.json()


def _auth(identity: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity['token']}"}


def _queue(world, *, org: str = "alpha", harness: str = GENERIC, prompt: str = "do the task"):
    tenant = world[org]
    return runner_api.enqueue_task(
        world["engine"], organization_id=tenant["org_id"],
        task_id=_task(world["engine"], tenant["org_id"]),
        harness=harness, prompt=prompt)


def _claim(world, identity: dict, poll_ms: int = 0) -> dict | None:
    response = world["client"].post("/runner/lease", json={"runner_id": identity["runner_id"],
                                                           "poll_timeout_ms": poll_ms},
                                    headers=_auth(identity))
    assert response.status_code == 200, response.text
    return response.json().get("lease")


def _heartbeat_body(lease: dict, fence: int | None = None) -> dict:
    return {"lease_id": lease["lease_id"], "run_id": lease["run_id"],
            "fence": lease["fence"] if fence is None else fence, "token": lease["token"]}


def _finish_body(lease: dict, *, state: str = "completed", key: str | None = None,
                 fence: int | None = None, effects: list[dict] | None = None) -> dict:
    return {
        "lease_id": lease["lease_id"],
        "run_id": lease["run_id"],
        "fence": lease["fence"] if fence is None else fence,
        "token": lease["token"],
        "state": state,
        "summary": "done",
        "idempotency_key": key or f"{lease['run_id']}:1",
        "effects": effects if effects is not None else [],
        "evidence": {"backend": "test"},
    }


def _post(world, route: str, identity: dict, body: dict):
    return world["client"].post(route, json=body, headers=_auth(identity))


def _lease_row(engine, lease_id: str) -> dict:
    with engine.connect() as conn:
        return dict(conn.execute(select(agent_lease).where(
            agent_lease.c.id == lease_id)).mappings().one())


def _expire_now(engine, lease_id: str) -> None:
    """Move the server's own deadline into the past: no sleeping, no fake clock."""
    past = _iso(datetime.now(timezone.utc) - timedelta(seconds=120))
    with engine.begin() as conn:
        conn.execute(agent_lease.update().where(agent_lease.c.id == lease_id).values(
            lease_until=past))


def test_a_revision_65_database_gains_the_lease_tables(tmp_path):
    """The upgrade path is 65 -> 66 on an existing database, not a fresh create."""
    from hoja_server.db import (
        CURRENT_SCHEMA_REVISION,
        applied_schema_revision,
        make_engine,
        migrate,
    )
    from tests import legacy_schema

    engine = make_engine(tmp_path / "lease-upgrade.db")
    legacy_schema.create_schema(engine, 65)
    with engine.begin() as conn:
        # Children first: a side effect points at a lease, a lease at a task and a
        # runner, and a runner enrollment at its organization. Revision 67's
        # session tables point at a lease, so they go first; the ledger still reads
        # 65, which is what a database that has only taken 65 looks like.
        for name in ("agent_activity", "agent_session",
                     "agent_side_effect", "agent_lease", "runner_enrollment", "runner"):
            conn.exec_driver_sql(f"DROP TABLE {name}")
    assert applied_schema_revision(engine) == 65
    migrate(engine)
    # Whatever the head is now: what this test proves is that an existing database
    # upgrades through every revision instead of needing a fresh create.
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    assert {"runner", "runner_enrollment", "agent_lease", "agent_side_effect"} <= tables


def test_registration_token_is_single_use_and_gets_a_runner_token(world):
    identity = _enroll(world)
    assert identity["runner_id"] and identity["token"].startswith("hoja_")
    with world["engine"].connect() as conn:
        stored = conn.execute(select(func.count()).select_from(agent_lease)).scalar()
    assert stored == 0
    # A runner token is not a person's API key: `api_key.access` accepts only
    # read|read_write, so a runner credential must never resolve as one.
    assert world["store"].resolve_token(identity["token"]) is None

    tenant = world["alpha"]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"])
    first = world["client"].post("/runner/register", json={
        "name": "runner-2", "enrollment_token": enrollment, "harnesses": [GENERIC]})
    assert first.status_code == 200, first.text
    replay = world["client"].post("/runner/register", json={
        "name": "runner-3", "enrollment_token": enrollment, "harnesses": [GENERIC]})
    assert replay.status_code == 409, replay.text
    assert replay.json()["code"] == runner_api.ERROR_ENROLLMENT_SPENT
    unknown = world["client"].post("/runner/register", json={
        "name": "runner-4", "enrollment_token": "hoja_not-a-token", "harnesses": [GENERIC]})
    assert unknown.status_code == 401, unknown.text


def test_a_harness_outside_the_allowlist_is_never_leased(world):
    identity = _enroll(world, harnesses=(GENERIC,))
    _queue(world, harness="codex")
    response = world["client"].post("/runner/lease", json={"poll_timeout_ms": 0},
                                    headers=_auth(identity))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "lease" not in body, "a harness this runner did not declare must not be granted"
    assert body["refused"] and body["refused"][0]["harness"] == "codex"
    assert body["refused"][0]["reason"] == runner_api.ERROR_HARNESS_NOT_ALLOWED

    _queue(world, harness=GENERIC)
    granted = _claim(world, identity)
    assert granted is not None and granted["harness"] == GENERIC


def test_every_lease_route_refuses_a_stale_fence_with_409(world):
    identity = _enroll(world)
    lease = _queue(world)
    granted = _claim(world, identity)
    assert granted["fence"] == lease["fence"]
    stale = granted["fence"] + 1
    calls = {
        "/runner/heartbeat": _heartbeat_body(granted, fence=stale),
        "/runner/events": {**_heartbeat_body(granted, fence=stale),
                           "events": [{"kind": "note", "seq": 1, "payload": {}}]},
        "/runner/effect": {**_heartbeat_body(granted, fence=stale),
                           "key": f"{granted['run_id']}:1", "kind": "pull_request",
                           "payload": {"url": "https://example.test/pr/1"}},
        "/runner/finish": _finish_body(granted, fence=stale),
        "/runner/release": {**_heartbeat_body(granted, fence=stale), "reason": "bye"},
    }
    for route, body in calls.items():
        response = _post(world, route, identity, body)
        assert response.status_code == 409, (route, response.status_code, response.text)
        assert response.json()["code"] == runner_api.ERROR_STALE_FENCE, route
    # The lease is untouched: a refused write is not a state change.
    assert _lease_row(world["engine"], granted["lease_id"])["state"] == runner_api.LEASE_ACTIVE


def test_a_fence_that_disagrees_with_the_header_is_refused(world):
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    body = _heartbeat_body(granted)
    headers = {**_auth(identity), "X-Hoja-Fence": str(granted["fence"] + 7)}
    response = world["client"].post("/runner/heartbeat", json=body, headers=headers)
    assert response.status_code == 400, response.text
    assert response.json()["code"] == runner_api.ERROR_FENCE_MISMATCH


def test_a_duplicate_finish_records_one_effect_and_returns_the_first_reference(world):
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    run_id = granted["run_id"]
    # The PR is created *during* the run, before any finish exists (WP08 gap 2).
    effect = _post(world, "/runner/effect", identity, {
        **_heartbeat_body(granted), "key": f"{run_id}:2", "kind": "pull_request",
        "payload": {"url": "https://github.test/acme/hoja/pull/7"},
    })
    assert effect.status_code == 200, effect.text
    assert effect.json()["external_ref"] == "https://github.test/acme/hoja/pull/7"
    assert effect.json()["duplicate"] is False
    again = _post(world, "/runner/effect", identity, {
        **_heartbeat_body(granted), "key": f"{run_id}:2", "kind": "pull_request",
        "payload": {"url": "https://github.test/acme/hoja/pull/7"},
    })
    assert again.status_code == 200 and again.json()["duplicate"] is True

    body = _finish_body(granted, effects=[
        {"key": f"{run_id}:1", "kind": "claim", "payload": {"answer": "done"}},
        {"key": f"{run_id}:2", "kind": "pull_request",
         "payload": {"url": "https://github.test/acme/hoja/pull/7"}},
    ])
    first = _post(world, "/runner/finish", identity, body)
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    assert first.json()["external_ref"] == "https://github.test/acme/hoja/pull/7"
    second = _post(world, "/runner/finish", identity, body)
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True, "a replayed finish is the same finish"
    assert second.json()["external_ref"] == first.json()["external_ref"]
    with world["engine"].connect() as conn:
        effects = conn.execute(select(func.count()).select_from(agent_side_effect).where(
            agent_side_effect.c.run_id == run_id)).scalar()
    assert effects == 2, "one claim and one PR, however many times the finish is sent"
    row = _lease_row(world["engine"], granted["lease_id"])
    assert row["state"] == runner_api.LEASE_FINISHED and row["finish_state"] == "completed"
    assert json.loads(row["result"])["session_state"] == "awaiting_verification"


def test_the_effect_wrapper_performs_a_server_side_write_once(world):
    """The seam a server-side PR creation uses: one call, one ledger row, one ref."""
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    lease = _lease_row(world["engine"], granted["lease_id"])
    calls: list[int] = []

    def create_pull_request() -> str:
        calls.append(1)
        return f"PR#{len(calls)}"

    first = runner_api.record_effect(
        world["engine"], lease=lease, key=f"{lease['run_id']}:1", kind="pull_request",
        payload={"repo": "acme/hoja"}, perform=create_pull_request)
    second = runner_api.record_effect(
        world["engine"], lease=lease, key=f"{lease['run_id']}:1", kind="pull_request",
        payload={"repo": "acme/hoja"}, perform=create_pull_request)
    assert len(calls) == 1, "a duplicate effect must not write twice"
    assert first["external_ref"] == second["external_ref"] == "PR#1"
    assert second["duplicate"] is True
    with world["engine"].connect() as conn:
        assert conn.execute(select(func.count()).select_from(agent_side_effect).where(
            agent_side_effect.c.lease_id == lease["id"])).scalar() == 1


def test_finish_from_an_expired_lease_is_rejected(world):
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    _expire_now(world["engine"], granted["lease_id"])
    response = _post(world, "/runner/finish", identity, _finish_body(granted))
    assert response.status_code == 409, response.text
    assert response.json()["code"] == runner_api.ERROR_EXPIRED
    assert _lease_row(world["engine"], granted["lease_id"])["state"] == runner_api.LEASE_ACTIVE


def test_lease_expiry_reassigns_the_task_at_the_next_fence(world):
    identity = _enroll(world)
    lease = _queue(world)
    granted = _claim(world, identity)
    _expire_now(world["engine"], granted["lease_id"])
    with tenant_context.scoped(world["alpha"]["org_id"]):
        reaped = runner_api.reap_expired_leases(world["engine"])
    assert [r["lease_id"] for r in reaped] == [granted["lease_id"]]
    old = _lease_row(world["engine"], granted["lease_id"])
    assert old["state"] == runner_api.LEASE_LOST and old["lost_reason"] == "lease_expired"
    assert reaped[0]["requeued_fence"] == granted["fence"] + 1
    # The old runner no longer owns it: its heartbeat is a stale fence, not a retry.
    lost = _post(world, "/runner/heartbeat", identity, _heartbeat_body(granted))
    assert lost.status_code == 409 and lost.json()["code"] == runner_api.ERROR_STALE_FENCE
    # A crashed runner's finish is rejected too.
    crashed = _post(world, "/runner/finish", identity, _finish_body(granted))
    assert crashed.status_code == 409, crashed.text
    # And the task is work again, at the next fence, for whoever asks next.
    other = _enroll(world, name="runner-2")
    reclaimed = _claim(world, other)
    assert reclaimed is not None and reclaimed["task_id"] == lease["task_id"]
    assert reclaimed["fence"] == granted["fence"] + 1
    assert reclaimed["run_id"] != granted["run_id"]


def test_the_reaper_audits_every_lost_run(world):
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    _expire_now(world["engine"], granted["lease_id"])
    with tenant_context.scoped(world["alpha"]["org_id"]):
        runner_api.reap_expired_leases(world["engine"])
    with tenant_context.system(), world["engine"].connect() as conn:
        actions = list(conn.execute(select(audit_event.c.action).where(
            audit_event.c.organization_id == world["alpha"]["org_id"])).scalars())
    assert "agent.lease.grant" in actions
    assert "agent.lease.expiry" in actions
    with tenant_context.system():
        # None is the "chain intact" answer from verify_chain.
        assert audit.verify_chain(world["engine"], world["alpha"]["org_id"]) is None


def test_cancel_reaches_the_runner_on_the_next_heartbeat(world):
    identity = _enroll(world)
    lease = _queue(world)
    granted = _claim(world, identity)
    with tenant_context.scoped(world["alpha"]["org_id"]):
        canceled = runner_api.request_cancel(
            world["engine"], organization_id=world["alpha"]["org_id"],
            task_id=lease["task_id"], reason="human_stop")
    assert canceled is not None
    beat = _post(world, "/runner/heartbeat", identity, _heartbeat_body(granted))
    assert beat.status_code == 200, beat.text
    assert beat.json()["cancel"] is True
    assert beat.json()["cancel_reason"] == "human_stop"
    # The runner stops the harness and finishes as canceled; that is accepted
    # *because* the control plane asked for it.
    done = _post(world, "/runner/finish", identity, _finish_body(granted, state="canceled"))
    assert done.status_code == 200, done.text
    assert done.json()["session_state"] == "canceled"
    row = _lease_row(world["engine"], granted["lease_id"])
    assert row["state"] == runner_api.LEASE_FINISHED and row["finish_state"] == "canceled"


def test_a_canceled_finish_without_a_requested_cancel_is_refused(world):
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    response = _post(world, "/runner/finish", identity, _finish_body(granted, state="canceled"))
    assert response.status_code == 400, response.text
    assert response.json()["code"] == runner_api.ERROR_CANCEL_NOT_REQUESTED
    assert _lease_row(world["engine"], granted["lease_id"])["state"] == runner_api.LEASE_ACTIVE


def test_a_failed_finish_consumes_the_attempt_without_requeueing(world):
    identity = _enroll(world)
    lease = _queue(world)
    granted = _claim(world, identity)
    response = _post(world, "/runner/finish", identity, _finish_body(granted, state="failed"))
    assert response.status_code == 200, response.text
    assert response.json()["session_state"] == "failed"
    with world["engine"].connect() as conn:
        queued = conn.execute(select(func.count()).select_from(agent_lease).where(and_(
            agent_lease.c.task_id == lease["task_id"],
            agent_lease.c.state == runner_api.LEASE_QUEUED))).scalar()
    assert queued == 0, "retry policy is the scheduler's decision, not the runner's"


def test_release_gives_the_lease_back_and_can_requeue_it(world):
    identity = _enroll(world)
    lease = _queue(world)
    granted = _claim(world, identity)
    released = _post(world, "/runner/release", identity, {
        **_heartbeat_body(granted), "reason": "graceful stop", "requeue": True})
    assert released.status_code == 200, released.text
    assert released.json()["released"] is True
    assert released.json()["requeued"]["fence"] == granted["fence"] + 1
    row = _lease_row(world["engine"], granted["lease_id"])
    assert row["state"] == runner_api.LEASE_RELEASED
    # The fence moved to the requeued attempt, so the released lease is stale:
    # the runner must stop, and it may not finish the attempt it gave back.
    after = _post(world, "/runner/heartbeat", identity, _heartbeat_body(granted))
    assert after.status_code == 409 and after.json()["code"] == runner_api.ERROR_STALE_FENCE
    reclaimed = _claim(world, identity)
    assert reclaimed["fence"] == granted["fence"] + 1
    assert _claim(world, identity) is None
    assert lease["fence"] == 1


def test_release_without_requeue_ends_the_attempt(world):
    identity = _enroll(world)
    lease = _queue(world)
    granted = _claim(world, identity)
    released = _post(world, "/runner/release", identity, {
        **_heartbeat_body(granted), "reason": "shutting down"})
    assert released.status_code == 200, released.text
    assert released.json()["requeued"] is None
    with world["engine"].connect() as conn:
        queued = conn.execute(select(func.count()).select_from(agent_lease).where(and_(
            agent_lease.c.task_id == lease["task_id"],
            agent_lease.c.state == runner_api.LEASE_QUEUED))).scalar()
    assert queued == 0
    # No successor exists, so the lease is simply gone.
    after = _post(world, "/runner/heartbeat", identity, _heartbeat_body(granted))
    assert after.status_code == 410 and after.json()["code"] == runner_api.ERROR_LEASE_LOST


def test_the_activity_stream_is_monotonic_per_lease(world):
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    batch = {
        **_heartbeat_body(granted),
        "events": [
            {"kind": "harness.selected", "seq": 1, "payload": {"harness": GENERIC}},
            {"kind": "harness.ready", "seq": 2, "payload": {}},
        ],
    }
    first = _post(world, "/runner/events", identity, batch)
    assert first.status_code == 200 and first.json()["accepted_seq"] == 2
    replay = _post(world, "/runner/events", identity, batch)
    assert replay.status_code == 200 and replay.json()["accepted_seq"] == 2
    with world["engine"].connect() as conn:
        rows = conn.execute(select(func.count()).select_from(issue_activity).where(and_(
            issue_activity.c.issue_id == granted["task_id"],
            issue_activity.c.event_type.like("runner.%")))).scalar()
    assert rows == 2, "a re-delivered batch must not duplicate the activity stream"


def test_runner_work_is_confined_to_its_own_organization(world):
    alpha_runner = _enroll(world, org="alpha", harnesses=(GENERIC,))
    _queue(world, org="bravo", harness=GENERIC)
    response = world["client"].post("/runner/lease", json={"poll_timeout_ms": 0},
                                    headers=_auth(alpha_runner))
    assert response.status_code == 200, response.text
    assert "lease" not in response.json(), "a runner must never see another tenant's queue"


def test_a_runner_cannot_name_another_runner(world):
    mine = _enroll(world, name="runner-1")
    theirs = _enroll(world, name="runner-2")
    response = world["client"].post(
        "/runner/lease", json={"poll_timeout_ms": 0},
        headers={**_auth(mine), "X-Hoja-Runner": theirs["runner_id"]})
    assert response.status_code == 403, response.text


def test_a_revoked_runner_token_stops_working(world):
    identity = _enroll(world)
    assert runner_api.revoke_runner(world["engine"], organization_id=world["alpha"]["org_id"],
                                    runner_id=identity["runner_id"]) is True
    response = world["client"].post("/runner/lease", json={"poll_timeout_ms": 0},
                                    headers=_auth(identity))
    assert response.status_code == 401, response.text


@pytest.mark.skipif(not rls_mode.ENABLED,
                    reason="the tenancy probe needs Postgres with row-level security")
def test_the_runner_path_binds_a_tenant_under_rls(world):
    """Under RLS the routes must authenticate in system context and then bind.

    A route that forgot to bind would read no rows (fail closed) and every call
    here would 401 or come back empty, so this passing is the isolation statement.
    """
    identity = _enroll(world)
    _queue(world)
    granted = _claim(world, identity)
    assert granted is not None
    assert _post(world, "/runner/heartbeat", identity,
                 _heartbeat_body(granted)).status_code == 200
