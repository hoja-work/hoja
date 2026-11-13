"""The real runner client against the real runner routes, on Postgres (WP07).

PR #218 shipped ``runner/hoja_runnerd/client.py`` with a typed ``ControlPlane``
and tested it against ``runner/tests/fake_control_plane.py``. A fake proves the
daemon; it cannot prove the server, because the fake *is* the server's opinions
written down twice. This file closes that gap: the unmodified runner client,
talking HTTP over a socket to ``create_app``'s own ``/runner/*`` routes, driving
one full lifecycle and every refusal the acceptance names.

What it establishes that the fake cannot:

* the paths, headers, body keys and error codes the client sends are the ones
  the server reads (``runner/hoja_runnerd/protocol.py`` is mirrored in
  ``app/hoja_server/runner_api.py`` and the two are asserted equal below);
* a stale fence arrives at the client as ``StaleFence``/``LeaseLost``, not as a
  retried request;
* a duplicate ``finish`` returns the first effect's reference and records it
  once, with the real ledger and the real unique constraint behind it;
* the whole thing works on **Postgres**, including under ``HOJA_TEST_RLS=1``
  where every query runs as a role that owns nothing.

It skips without ``HOJA_TEST_POSTGRES_URL`` instead of pretending SQLite is the
deployment: the acceptance says Postgres.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from hoja_server import runner_api
from hoja_server.app import create_app
from hoja_server.db import agent_lease, agent_side_effect, issue, seed_tenant
from tests import rls_mode

RUNNER_ROOT = Path(__file__).resolve().parents[3] / "runner"
if str(RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNNER_ROOT))

from hoja_runnerd import protocol as runner_protocol  # noqa: E402
from hoja_runnerd.client import (  # noqa: E402
    ActivityEvent,
    Claim,
    ControlPlane,
    Effect,
    LeaseLost,
    StaleFence,
    UrllibTransport,
)

uvicorn = pytest.importorskip("uvicorn")

POSTGRES = bool(os.environ.get("HOJA_TEST_POSTGRES_URL"))
pytestmark = pytest.mark.skipif(
    not POSTGRES,
    reason="the conformance run needs Postgres (HOJA_TEST_POSTGRES_URL); the fake already covers SQLite",
)


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # it runs in a thread, not as a process
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A migrated Postgres workspace, the app on a real socket, and a runner."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "runner-conformance-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    # Postgres either way: with HOJA_TEST_RLS the app connects as a restricted
    # role under the production policies, without it as the schema owner. Never
    # SQLite -- the acceptance names Postgres, and a SQLite pass would not mean it.
    from hoja_server.db import make_engine, migrate

    owner_url, schema = rls_mode.new_schema_url()
    engine = make_engine(owner_url)
    migrate(engine)
    database_url = owner_url
    if rls_mode.ENABLED:
        # The schema argument is not optional: without it the policies land on
        # "public" and the restricted role cannot even resolve the tables.
        database_url = rls_mode.restrict(owner_url, schema)
    kwargs = {"db_path": None, "database_url": database_url}
    tenant = seed_tenant(engine, org_name="Conformance", org_url_key="conformance",
                         user_email="admin@conformance.test", token="conf-tok",
                         demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    port = _free_port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                   lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield {"engine": engine, "tenant": tenant, "base_url": f"http://127.0.0.1:{port}"}
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _task(engine, org_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(select(issue.c.id).where(
            issue.c.organization_id == org_id).order_by(issue.c.identifier)).scalars().first()


def _register(live, name: str = "conformance-runner") -> ControlPlane:
    _row, enrollment = runner_api.mint_enrollment_token(
        live["engine"], organization_id=live["tenant"]["org_id"],
        created_by=live["tenant"]["user_id"], label="conformance")
    client = ControlPlane(UrllibTransport(live["base_url"]))
    identity = client.register(name=name, enrollment_token=enrollment,
                               labels={"pool": "conformance"}, harnesses=["generic"])
    assert identity.runner_id and identity.token
    return client


def _enqueue(live, *, harness: str = "generic", prompt: str | None = None):
    return runner_api.enqueue_task(
        live["engine"], organization_id=live["tenant"]["org_id"],
        task_id=_task(live["engine"], live["tenant"]["org_id"]), harness=harness, prompt=prompt)


def test_the_wire_contract_is_the_same_on_both_sides():
    """The runner package must not import ``app/``, so the contract is asserted, not shared."""
    assert runner_protocol.CP_ROUTE_REGISTER == runner_api.ROUTE_REGISTER
    assert runner_protocol.CP_ROUTE_LEASE == runner_api.ROUTE_LEASE
    assert runner_protocol.CP_ROUTE_HEARTBEAT == runner_api.ROUTE_HEARTBEAT
    assert runner_protocol.CP_ROUTE_EVENTS == runner_api.ROUTE_EVENTS
    assert runner_protocol.CP_ROUTE_FINISH == runner_api.ROUTE_FINISH
    assert runner_protocol.CP_ROUTE_EFFECT == runner_api.ROUTE_EFFECT
    assert runner_protocol.CP_ROUTE_RELEASE == runner_api.ROUTE_RELEASE
    assert runner_protocol.CP_HEADER_RUNNER == runner_api.HEADER_RUNNER
    assert runner_protocol.CP_HEADER_LEASE == runner_api.HEADER_LEASE
    assert runner_protocol.CP_HEADER_FENCE == runner_api.HEADER_FENCE
    assert runner_protocol.CP_HEADER_LEASE_TOKEN == runner_api.HEADER_LEASE_TOKEN
    assert runner_protocol.CP_ERROR_STALE_FENCE == runner_api.ERROR_STALE_FENCE
    assert runner_protocol.CP_ERROR_LEASE_LOST == runner_api.ERROR_LEASE_LOST
    assert runner_protocol.CP_ERROR_EXPIRED == runner_api.ERROR_EXPIRED
    assert runner_protocol.CP_ERROR_CANCEL_NOT_REQUESTED == runner_api.ERROR_CANCEL_NOT_REQUESTED
    assert runner_protocol.CP_ERROR_FENCE_MISMATCH == runner_api.ERROR_FENCE_MISMATCH
    assert runner_protocol.CP_ERROR_HARNESS_NOT_ALLOWED == runner_api.ERROR_HARNESS_NOT_ALLOWED
    assert runner_protocol.CP_HEARTBEAT_INTERVAL_S == runner_api.HEARTBEAT_INTERVAL_S
    assert runner_protocol.CP_LEASE_TTL_S == runner_api.LEASE_TTL_S
    assert runner_protocol.CP_MIN_LEASE_TTL_S == runner_api.MIN_LEASE_TTL_S
    assert runner_protocol.CP_REAP_GRACE_S == runner_api.REAP_GRACE_S
    assert runner_protocol.CP_FINISH_STATES == runner_api.FINISH_STATES
    assert runner_protocol.CP_SESSION_STATE == runner_api.SESSION_STATE


def test_one_full_lifecycle_through_the_real_client(live):
    client = _register(live)
    queued = _enqueue(live)

    lease = client.claim(poll_timeout_s=5.0)
    assert lease is not None, "the queued task was never handed out"
    assert lease.task_id == queued["task_id"] and lease.fence == queued["fence"]
    assert lease.harness == "generic" and lease.prompt.startswith("ENG-")
    # The fence travels in the body and in the headers (WP08 gap 4).
    headers = client._headers(lease)
    assert headers["X-Hoja-Fence"] == str(lease.fence)
    assert headers["X-Hoja-Lease"] == lease.lease_id
    assert headers["X-Hoja-Lease-Token"] == lease.token

    beat = client.heartbeat(lease)
    assert beat.fence == lease.fence and beat.cancel is False
    assert beat.ttl_s == lease.ttl_s

    accepted = client.post_activity(lease, [
        ActivityEvent(kind="harness.selected", seq=1, payload={"harness": "generic"}),
        ActivityEvent(kind="harness.ready", seq=2, payload={}),
    ])
    assert accepted == 2

    pull = Effect(kind="pull_request", seq=2,
                  payload={"url": "https://github.test/acme/hoja/pull/42"})
    first_effect = client.record_effect(lease, pull)
    assert first_effect.external_ref == "https://github.test/acme/hoja/pull/42"
    assert first_effect.duplicate is False
    second_effect = client.record_effect(lease, pull)
    assert second_effect.duplicate is True
    assert second_effect.external_ref == first_effect.external_ref

    claim = Claim(run_id=lease.run_id, state="completed", summary="conformance run",
                  effects=[Effect(kind="claim", seq=1, payload={"answer": "ok"}), pull],
                  evidence={"backend": "conformance"})
    ack = client.finish(lease, claim)
    assert ack.accepted and ack.duplicate is False
    assert ack.external_ref == first_effect.external_ref
    assert ack.session_state == "awaiting_verification"
    replay = client.finish(lease, claim)
    assert replay.duplicate is True
    assert replay.idempotency_key == ack.idempotency_key
    assert replay.external_ref == ack.external_ref

    with live["engine"].connect() as conn:
        effects = conn.execute(select(func.count()).select_from(agent_side_effect).where(
            agent_side_effect.c.run_id == lease.run_id)).scalar()
        row = conn.execute(select(agent_lease).where(
            agent_lease.c.id == lease.lease_id)).mappings().one()
    assert effects == 2, "one claim and one PR, two finishes"
    assert row["state"] == runner_api.LEASE_FINISHED and row["finish_state"] == "completed"


def test_a_reaped_lease_raises_lease_lost_in_the_real_client(live):
    client = _register(live)
    _enqueue(live)
    lease = client.claim(poll_timeout_s=5.0)
    assert lease is not None
    past = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    with live["engine"].begin() as conn:
        conn.execute(agent_lease.update().where(
            agent_lease.c.id == lease.lease_id).values(lease_until=past))
    reaped = runner_api.reap_expired_leases(live["engine"])
    assert [row["lease_id"] for row in reaped] == [lease.lease_id]

    with pytest.raises(StaleFence):
        client.heartbeat(lease)
    with pytest.raises(LeaseLost):
        client.finish(lease, Claim(run_id=lease.run_id, state="completed", summary="late"))


def test_cancel_and_release_through_the_real_client(live):
    client = _register(live)
    queued = _enqueue(live, prompt="cancel me")
    lease = client.claim(poll_timeout_s=5.0)
    assert lease is not None
    runner_api.request_cancel(live["engine"], organization_id=live["tenant"]["org_id"],
                              task_id=queued["task_id"], reason="human_stop")
    beat = client.heartbeat(lease)
    assert beat.cancel is True and beat.cancel_reason == "human_stop"
    ack = client.finish(lease, Claim(run_id=lease.run_id, state="canceled",
                                     summary="stopped by a human"))
    assert ack.session_state == "canceled"

    _enqueue(live, prompt="release me")
    second = client.claim(poll_timeout_s=5.0)
    assert second is not None
    released = client.release(second, reason="conformance", requeue=False)
    assert released["released"] is True
    assert client.claim(poll_timeout_s=0.2) is None


def test_a_harness_outside_the_allowlist_is_never_leased_to_the_real_client(live):
    client = _register(live)  # registered with generic only
    _enqueue(live, harness="codex", prompt="not for this runner")
    assert client.claim(poll_timeout_s=0.5) is None
