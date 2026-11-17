"""An idle runner that is polling is online, and the printed enroll command starts it.

HOJA-602-A-20, found by the live run against production (HOJA-641, ``live-runner/REPORT.md``
§7 items 5 and 6). Two gaps, both about the same thing — a machine that is up and working
being described as broken:

1. ``runner.last_seen`` was written only by ``/runner/heartbeat``, and a heartbeat only
   happens while a lease is held. A daemon with nothing to do long-polls ``/runner/lease``
   forever, so its ``last_seen`` froze and Settings → Runners drew a live machine as
   ``offline``. The poll is liveness — it holds an open request for the whole window — so
   the lease route now records it too, rate limited to one write per heartbeat interval.
2. ``hoja-runnerd`` refused to start on a host with several harness profiles installed
   unless ``--default-harness`` was passed, and the admin screen's enroll command does not
   print that flag. The control plane now names the default at enroll (first entry of the
   allowlist that same call registered, sorted) so the daemon records a fact instead of
   guessing.

Each test here fails on ``main`` before the change: the poll writes nothing (``offline``),
the register response names no default, and nothing pins the printed command's flags.

SQLite by default; Postgres with ``HOJA_TEST_RLS=1 HOJA_TEST_POSTGRES_URL=…``, where the
last test proves the poll's write is tenant-scoped rather than accidental.
"""
from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from starlette.testclient import TestClient

from hoja_server import agent_runner_admin, runner_api
from hoja_server.app import create_app
from hoja_server.db import runner, seed_tenant
from hoja_server.store import Store
from tests import rls_mode

RUNNER_FIELDS = """id name organizationId status enabled harnesses lastHeartbeatAt"""


def _data(client: TestClient, token: str, query: str) -> dict:
    body = client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two workspaces on one database, plus the app over the real routes."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "runner-online-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "runner-online")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "alpha": alpha,
            "bravo": bravo, "store": Store(engine)}


def _enroll(world, *, org: str = "alpha", name: str = "mac-mini-01",
            harnesses: tuple[str, ...] = ("pi", "generic")) -> dict:
    tenant = world[org]
    _row, enrollment = runner_api.mint_enrollment_token(
        world["engine"], organization_id=tenant["org_id"], created_by=tenant["user_id"])
    response = world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": enrollment, "harnesses": list(harnesses)})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(identity: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity['token']}"}


def _poll(world, identity: dict, poll_ms: int = 0):
    return world["client"].post("/runner/lease",
                                json={"runner_id": identity["runner_id"],
                                      "poll_timeout_ms": poll_ms},
                                headers=_auth(identity))


def _row(engine, runner_id: str) -> dict:
    with engine.connect() as conn:
        return dict(conn.execute(select(runner).where(
            runner.c.id == runner_id)).mappings().one())


def _backdate(engine, runner_id: str, seconds: int) -> None:
    """Move ``last_seen`` into the past: no sleeping, no fake clock."""
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with engine.begin() as conn:
        conn.execute(runner.update().where(runner.c.id == runner_id).values(last_seen=stale))


@contextlib.contextmanager
def _last_seen_writes():
    """Every UPDATE that sets ``runner.last_seen``, in order: the rate limit is a count.

    Listened on the ``Engine`` class, not on one instance: the app builds its own engine
    from ``create_app``'s arguments, so the test's engine would see nothing.
    """
    seen: list[str] = []

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        text = str(clauseelement)
        if text.startswith("UPDATE runner") and "last_seen" in text:
            seen.append(text)

    event.listen(Engine, "before_execute", before_execute)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_execute", before_execute)


# --------------------------------------------------- 1. an idle poll is liveness
def test_an_idle_long_poll_makes_the_runner_online(world):
    """The defect: nothing is queued, the daemon polls, the screen says offline forever."""
    identity = _enroll(world)
    assert _row(world["engine"], identity["runner_id"])["last_seen"] is None
    before = _data(world["client"], world["alpha"]["token"], f'''
        query {{ runners(first: 50) {{ nodes {{ {RUNNER_FIELDS} }} }} }}''')["runners"]["nodes"]
    assert [node["status"] for node in before] == ["offline"], before

    # One poll, nothing to grant: the only thing it carries is "I am here".
    response = _poll(world, identity)
    assert response.status_code == 200, response.text
    assert "lease" not in response.json(), "the queue is empty: this is the idle case"

    after = _data(world["client"], world["alpha"]["token"], f'''
        query {{ runners(first: 50) {{ nodes {{ {RUNNER_FIELDS} }} }} }}''')["runners"]["nodes"]
    assert len(after) == 1
    assert after[0]["lastHeartbeatAt"], after
    assert after[0]["status"] == "online", after
    # The derived status is what the filter reads too, so the Runners screen's own
    # "online" tab finds it (agent_runners.runner_status is the single rule).
    online = _data(world["client"], world["alpha"]["token"], '''
        query { runners(first: 50, status: "online") { nodes { name } } }''')["runners"]["nodes"]
    assert [node["name"] for node in online] == ["mac-mini-01"]


def test_a_long_poll_writes_last_seen_once_per_interval(world):
    """The poll sleeps in 250 ms slices, so the write has to be rate limited, not per slice."""
    identity = _enroll(world)
    # 1.5 s of polling is six turns of the loop and one liveness window.
    with _last_seen_writes() as writes:
        assert _poll(world, identity, poll_ms=1500).status_code == 200
        assert len(writes) == 1, writes
    first = _row(world["engine"], identity["runner_id"])["last_seen"]
    assert first

    # Still inside the window: another 1.5 s poll writes nothing.
    with _last_seen_writes() as writes:
        assert _poll(world, identity, poll_ms=1500).status_code == 200
        assert writes == [], writes
    assert _row(world["engine"], identity["runner_id"])["last_seen"] == first

    # Past the window: the next poll moves it, once.
    _backdate(world["engine"], identity["runner_id"], seconds=30)
    with _last_seen_writes() as writes:
        assert _poll(world, identity, poll_ms=1500).status_code == 200
        assert len(writes) == 1, writes
    moved = _row(world["engine"], identity["runner_id"])["last_seen"]
    assert moved > first


def test_a_poll_never_touches_another_workspaces_runner(world):
    """The write is the tenant's: bravo polling leaves alpha's row at its own evidence."""
    ours = _enroll(world, org="alpha", name="alpha-runner")
    theirs = _enroll(world, org="bravo", name="bravo-runner")
    assert _poll(world, theirs).status_code == 200
    assert _row(world["engine"], theirs["runner_id"])["last_seen"]
    assert _row(world["engine"], ours["runner_id"])["last_seen"] is None
    # And alpha's row still reads offline, whatever bravo's poll did.
    rows = _data(world["client"], world["alpha"]["token"], f'''
        query {{ runners(first: 50) {{ nodes {{ {RUNNER_FIELDS} }} }} }}''')["runners"]["nodes"]
    assert [(node["name"], node["status"]) for node in rows] == [("alpha-runner", "offline")]


def test_a_revoked_runner_cannot_become_online_by_polling(world):
    """The refusal comes first: a revoked token is 401, so no liveness row is written."""
    identity = _enroll(world)
    assert runner_api.revoke_runner(world["engine"], organization_id=world["alpha"]["org_id"],
                                    runner_id=identity["runner_id"]) is True
    assert _poll(world, identity).status_code == 401
    assert _row(world["engine"], identity["runner_id"])["last_seen"] is None


@pytest.mark.skipif(not rls_mode.ENABLED,
                    reason="the tenancy probe needs Postgres with row-level security")
def test_the_poll_writes_liveness_under_a_bound_tenant(world):
    """Under RLS an unbound write would fail closed, so this passing is the isolation claim."""
    identity = _enroll(world)
    assert _poll(world, identity).status_code == 200
    assert _row(world["engine"], identity["runner_id"])["last_seen"]


# ------------------------------------- 2. the enrolled default harness is named, once
def test_registration_names_the_first_harness_of_the_enrolled_allowlist(world):
    """Deterministic: sorted, so the operator's flag order cannot change the answer."""
    forwards = _enroll(world, name="forwards", harnesses=("pi", "generic", "codex"))
    assert forwards["harnesses"] == ["codex", "generic", "pi"]
    assert forwards["default_harness"] == "codex"
    backwards = _enroll(world, name="backwards", harnesses=("codex", "generic", "pi"))
    assert backwards["default_harness"] == "codex"
    single = _enroll(world, name="single", harnesses=("PI",))
    assert single["default_harness"] == "pi", "the allowlist is normalised, not taken verbatim"
    # The rule is one function, and it says the same thing the column does.
    assert runner_api.default_harness_for(["pi", "codex"]) == "codex"
    assert runner_api.default_harness_for([]) is None
    stored = _row(world["engine"], forwards["runner_id"])["harnesses"]
    assert stored == '["codex", "generic", "pi"]', stored


def test_the_printed_command_carries_no_harness_flag(world):
    """Why the default has to be recorded: the command an operator runs cannot pass one.

    ``runner/tests/test_enroll_command.py`` drives the daemon with exactly these flags;
    if this string ever grows a ``--default-harness`` the two halves of A-20 disagree,
    so the flag set is pinned here.
    """
    command = agent_runner_admin.enroll_command(
        "https://hoja.example", name="mac-mini-01", token="tok-once")
    assert command == ("HOJA_ENROLLMENT_TOKEN=tok-once hoja-runnerd --enroll "
                       "--base-url https://hoja.example --name mac-mini-01 "
                       "--state-dir ~/.hoja/runner"), command
    flags = [part for part in command.split() if part.startswith("--")]
    assert flags == ["--enroll", "--base-url", "--name", "--state-dir"], flags
