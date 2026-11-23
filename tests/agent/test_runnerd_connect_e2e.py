"""Hoja Connect, end to end: the runnerd client against the real server record (HOJA-602-A-10).

The runner-side unit tests (``runner/tests/test_runnerd_connect.py``) run against a fake control
plane, and a fake is only a copy of the server's opinions written down twice. This file closes that
gap the way ``test_runner_conformance.py`` does for the lease contract: the *unmodified*
``runner/hoja_runnerd`` client and exposure publisher, talking HTTP over a socket to
``create_app``'s own ``/runner/connect/open`` and ``/runner/connect/close`` routes, with the real
``connect_endpoint`` table and the real ``connectEndpoints`` / ``connectEndpointClose`` GraphQL
surface behind them.

What only the real server can show:

* the lease payload the runner reads really is the ``policy.connect`` the admin's policy grants
  (``agent_policy.lease_payload``), so "per the lease payload's Connect grant" is a fact and not a
  fixture;
* a port the policy does not grant is refused by the server, with the code WP09 names, and then
  nothing is published and nothing is stored;
* an admin's revoke reaches the row, the next heartbeat carries ``connect_revoke``, the runner's
  acknowledgement clears ``revokeRequestedAt`` — the whole delivery loop, three processes wide;
* it all works on **Postgres under ``HOJA_TEST_RLS=1``**, where the app role owns nothing.

It skips without ``HOJA_TEST_POSTGRES_URL`` rather than pretending SQLite is the deployment.
"""
from __future__ import annotations

import base64
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hoja_server import runner_api
from hoja_server.app import create_app
from hoja_server.db import seed_tenant
from tests import rls_mode

RUNNER_ROOT = Path(__file__).resolve().parents[3] / "runner"
if str(RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNNER_ROOT))

from hoja_runnerd import connect as connect_mod  # noqa: E402
from hoja_client.client import (  # noqa: E402
    ConnectGrant,
    ControlPlane,
    ControlPlaneError as ClientError,
    UrllibTransport,
)
from hoja_runnerd.exposures import LeaseExposures, exposure_id_for  # noqa: E402

uvicorn = pytest.importorskip("uvicorn")

POSTGRES = bool(os.environ.get("HOJA_TEST_POSTGRES_URL"))
pytestmark = pytest.mark.skipif(
    not POSTGRES,
    reason="the end-to-end run needs Postgres (HOJA_TEST_POSTGRES_URL); the fake covers the daemon",
)

#: The worker's node key: public key material, and the entire ACL of the tunnel.
PEER = "nodekey:" + "a" * 64
ENDPOINT_FIELDS = """id exposureId runnerId runnerName leaseId sessionId agentId port host
  pinnedPeer state closeReason openedAt expiresAt closedAt revokeRequestedAt"""


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # it runs in a thread, not as a process
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- a stand-in tailcat -------------------------------------------------------------------
def _cbor_head(major: int, length: int) -> bytes:
    if length < 24:
        return bytes([(major << 5) | length])
    if length < 256:
        return bytes([(major << 5) | 24, length])
    return bytes([(major << 5) | 25]) + length.to_bytes(2, "big")


def synthetic_address() -> str:
    """A structurally valid v0.7.0 address, so ``TailcatAddress`` accepts it without a binary.

    ``TailcatAddress`` walks the CBOR of the address and requires one map of 3-6 non-empty scalar
    fields; filler would be a type error. The supervisor never dials anything here, and the address
    never leaves the runner process — which the test asserts rather than assumes.
    """
    def text(value: str) -> bytes:
        raw = value.encode()
        return _cbor_head(3, len(raw)) + raw

    def blob(value: bytes) -> bytes:
        return _cbor_head(2, len(value)) + value

    entries = b"".join([
        text("kp") + blob(b"k" * 32),
        text("dp") + blob(b"d" * 32),
        text("ps") + blob(b"p" * 32),
        text("rg") + _cbor_head(0, 1),
    ])
    body = _cbor_head(5, 4) + entries
    return "tc" + base64.urlsafe_b64encode(body).decode().rstrip("=")


def write_tailcat(directory: Path, address: str) -> str:
    path = directory / "tailcat"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        f"print('Serving client on {address}', flush=True)\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - the child is ours
        return True
    return os.path.exists(f"/proc/{int(pid)}")


# -- fixtures -----------------------------------------------------------------------------
@pytest.fixture
def live(tmp_path, monkeypatch):
    """A migrated Postgres workspace, the app on a real socket, and a throwaway tailcat."""
    monkeypatch.setenv("HOJA_SECRET_KEY", "runnerd-connect-e2e-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "runnerd-connect-e2e")
    tenant = seed_tenant(engine, org_name="Connect", org_url_key="connect",
                         user_email="admin@connect.test", token="connect-tok",
                         demo_issues=False)
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
    address = synthetic_address()
    connect_mod.TailcatAddress(address)  # a valid address or nothing below could open
    try:
        yield {
            "engine": engine,
            "tenant": tenant,
            "base_url": f"http://127.0.0.1:{port}",
            "graphql": TestClient(app),
            "tailcat": write_tailcat(tmp_path, address),
            "address": address,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=15)


# -- GraphQL helpers ----------------------------------------------------------------------
def _graphql(live, query: str) -> dict:
    response = live["graphql"].post(
        "/graphql", json={"query": query},
        headers={"Authorization": f"Bearer {live['tenant']['token']}"})
    body = response.json()
    assert "errors" not in body, body
    return body["data"]


def _endpoints(live) -> list[dict]:
    return _graphql(live, f"{{ connectEndpoints {{ nodes {{ {ENDPOINT_FIELDS} }} }} }}")[
        "connectEndpoints"]["nodes"]


def _agent(live, name: str = "Scout") -> dict:
    return _graphql(live, f'''
        mutation {{
          agentMemberCreate(input: {{name: "{name}", capacity: 4}}) {{
            success agentMember {{ id userId }} }} }}''')["agentMemberCreate"]["agentMember"]


def _policy(live, member_id: str, *, connect: str) -> None:
    _graphql(live, f'''
        mutation {{
          agentPolicyUpdate(agentMemberId: "{member_id}",
                            input: {{teams: ["{live['tenant']['team_id']}"], maxConcurrency: 4,
                                    connect: {connect}}}) {{ success }} }}''')


def _delegate(live, user_id: str, title: str = "Expose the preview") -> dict:
    return _graphql(live, f'''
        mutation {{
          issueCreate(input: {{teamId: "{live['tenant']['team_id']}", title: "{title}",
                               delegateId: "{user_id}"}}) {{
            success issue {{ id identifier }} }} }}''')["issueCreate"]["issue"]


# -- runner helpers -----------------------------------------------------------------------
def _register(live) -> ControlPlane:
    _row, enrollment = runner_api.mint_enrollment_token(
        live["engine"], organization_id=live["tenant"]["org_id"],
        created_by=live["tenant"]["user_id"], label="connect-e2e")
    client = ControlPlane(UrllibTransport(live["base_url"]))
    identity = client.register(name="connect-e2e-runner", enrollment_token=enrollment,
                               harnesses=["generic"])
    assert identity.runner_id and identity.token
    return client


def _leased_task(live, client: ControlPlane, *, connect: str) -> tuple[dict, object]:
    """Delegate a task to a policy that grants (or does not grant) the port, then claim it."""
    agent = _agent(live)
    _policy(live, agent["id"], connect=connect)
    issue = _delegate(live, agent["userId"])
    runner_api.enqueue_task(
        live["engine"], organization_id=live["tenant"]["org_id"], task_id=issue["id"],
        harness="generic")
    lease = client.claim(poll_timeout_s=5.0)
    assert lease is not None, "the delegated task never queued a lease for this runner"
    return agent, lease


def _publisher(live, client: ControlPlane) -> LeaseExposures:
    return LeaseExposures(
        client,
        supervisor=connect_mod.ConnectSupervisor(tailcat_bin=live["tailcat"]),
        peer_node_key=PEER,
        workspace_id=live["tenant"]["org_id"],
    )


# -- the acceptance -----------------------------------------------------------------------
def test_the_runner_publishes_lists_and_answers_a_revoke(live):
    client = _register(live)
    _agent_row, lease = _leased_task(
        live, client, connect='[{host: "localhost", port: 3000}]')

    # 1. The grant the runner acts on is the one the lease payload carried.
    assert lease.connect_grants == (ConnectGrant("localhost", 3000),)
    exposure_id = exposure_id_for(lease, ConnectGrant("localhost", 3000))

    publisher = _publisher(live, client)
    opened = publisher.open_for_lease(lease)
    try:
        assert [row.exposure_id for row in opened] == [exposure_id]
        pid = opened[0].pid
        assert process_alive(pid)

        # 2. The admin's read shows it, live, with the pin and the port — and no address.
        rows = _endpoints(live)
        assert len(rows) == 1, rows
        row = rows[0]
        assert (row["exposureId"], row["port"], row["host"], row["state"]) == (
            exposure_id, 3000, "localhost", "open")
        assert row["pinnedPeer"] == PEER
        assert row["leaseId"] and row["runnerName"] == "connect-e2e-runner"
        assert live["address"] not in repr(rows)

        # 3. Nothing is revoked yet, so the beat says nothing about Connect.
        assert client.heartbeat(lease).connect_revoke == ()

        # 4. An admin revokes: the row closes, audited, and the flag is set for delivery.
        revoked = _graphql(live, f'''
            mutation {{
              connectEndpointClose(exposureId: "{exposure_id}") {{
                success closed auditSeq endpoints {{ state closeReason revokeRequestedAt }} }} }}''')[
            "connectEndpointClose"]
        assert revoked["success"] is True and revoked["closed"] == 1
        assert revoked["auditSeq"] is not None
        assert revoked["endpoints"][0]["state"] == "closed"
        assert revoked["endpoints"][0]["closeReason"] == "revoked"
        assert revoked["endpoints"][0]["revokeRequestedAt"] is not None

        # 5. The next heartbeat carries the instruction, the runner kills the process, and its
        #    own close is the acknowledgement.
        beat = client.heartbeat(lease)
        assert beat.connect_revoke == (exposure_id,)
        publisher.handle_revoke(beat.connect_revoke, lease=lease)
        assert not process_alive(pid), "the revoked serve process kept running"

        # 6. The acknowledgement cleared the flag: the beat stops asking, and the row keeps the
        #    reason it was actually closed with.
        assert client.heartbeat(lease).connect_revoke == ()
        after = _endpoints(live)
        assert len(after) == 1
        assert after[0]["state"] == "closed" and after[0]["closeReason"] == "revoked"
        assert after[0]["revokeRequestedAt"] is None
    finally:
        publisher.close_all(reason="runner_close")


def test_the_server_refuses_a_port_the_lease_did_not_grant(live):
    """The grant is the *lease's*, and a runner that asks for more is refused by the server.

    ``LeaseExposures`` only ever sends the ports its lease names, so this drives the client
    directly: the point is that the check exists on the server side and cannot be talked past by a
    runner that decides to expose something else.
    """
    client = _register(live)
    _agent_row, lease = _leased_task(
        live, client, connect='[{host: "localhost", port: 3000}]')
    assert lease.connect_grants == (ConnectGrant("localhost", 3000),)

    with pytest.raises(ClientError) as refused:
        client.report_exposure(lease, exposure_id="forged-exposure", ports=[9999],
                               host="localhost", pinned_peer=PEER)
    assert refused.value.code == "connect_not_granted"
    assert _endpoints(live) == [], "a refused exposure was recorded anyway"

    # The granted port is accepted, so the refusal above is about the port and not the caller.
    client.report_exposure(lease, exposure_id="real-exposure", ports=[3000],
                           host="localhost", pinned_peer=PEER)
    rows = _endpoints(live)
    assert [(row["exposureId"], row["port"], row["state"]) for row in rows] == [
        ("real-exposure", 3000, "open")]
    client.report_exposure_closed(lease, exposure_id="real-exposure", reason="runner_close")


def test_a_lease_whose_policy_grants_nothing_opens_nothing(live):
    client = _register(live)
    _agent_row, lease = _leased_task(live, client, connect="[]")
    assert lease.connect_grants == ()
    publisher = _publisher(live, client)
    assert publisher.open_for_lease(lease) == []
    assert _endpoints(live) == []
