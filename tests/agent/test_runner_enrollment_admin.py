"""The admin surface that mints and revokes runner credentials (HOJA-635).

HOJA-602-A-6 shipped the data plane and left two gaps in its PR body: "no admin
HTTP/GraphQL surface to mint enrollment tokens or revoke runners
(``mint_enrollment_token``/``revoke_runner`` are the functions it should call)" and
"the daemon does not yet adopt the server's ``heartbeat_interval_s``". This file is
the proof for the first, on the surface a person actually uses.

The acceptance list this file exists to prove, one test group each:

=============================================================  ============================
test                                                           acceptance
=============================================================  ============================
the token is returned once and only its hash is stored         "token shown once and
                                                               hash-only at rest"
a revoked enrollment token is refused at /runner/enroll        "revoked token ... refused at
                                                               /runner/enroll"
a revoked runner is refused at /runner/claim                   "revoked ... runner is refused
                                                               at ... /runner/claim"
a member is refused with FORBIDDEN                             "member gets FORBIDDEN"
one workspace cannot revoke another's rows                     "cross-tenant isolation under
                                                               HOJA_TEST_RLS=1"
every write writes exactly one audit_event                     "every write is audited via
                                                               audit.record"
=============================================================  ============================

SQLite by default, PostgreSQL with ``HOJA_TEST_RLS=1 HOJA_TEST_POSTGRES_URL=...``.
Under RLS the app runs as a role without BYPASSRLS, so the tenancy tests here are
also the probe that ``runner_enrollment`` and ``runner`` stay bound to their tenant.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import audit, runner_api
from hoja_server.app import create_app
from hoja_server.db import audit_event, issue, runner, runner_enrollment, seed_tenant
from hoja_server.store import Store
from tests import rls_mode

GENERIC = "generic"
ENROLLMENT_FIELDS = ("id", "organizationId", "label", "status", "usable", "expiresAt",
                     "usedAt", "usedByRunner", "createdAt")
ENROLLMENT_SELECTION = " ".join(ENROLLMENT_FIELDS)


def _post(client: TestClient, token: str, query: str) -> dict:
    return client.post("/graphql", json={"query": query},
                       headers={"Authorization": f"Bearer {token}"}).json()


def _data(client: TestClient, token: str, query: str) -> dict:
    body = _post(client, token, query)
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "runner-enrollment-master")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "runner-enrollment")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="admin@alpha.test", token="alpha-tok", demo_issues=True)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="admin@bravo.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False)
    return {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo,
            "store": Store(engine)}


# --------------------------------------------------------------------------- helpers


def _create(world, *, org: str = "alpha", name: str = "mac-mini-01",
            ttl_minutes: int | None = 60) -> dict:
    ttl = "" if ttl_minutes is None else f", ttlMinutes: {ttl_minutes}"
    return _data(world["client"], world[org]["token"], f'''
        mutation {{
          runnerEnrollmentCreate(name: "{name}"{ttl}) {{
            enrollment {{ {ENROLLMENT_SELECTION} }}
            token command installCommand startCommand downloadUrl heartbeatIntervalS
          }}
        }}''')["runnerEnrollmentCreate"]


def _enrollments(world, *, org: str = "alpha") -> list[dict]:
    return _data(world["client"], world[org]["token"],
                 f"query {{ runnerEnrollments {{ {ENROLLMENT_SELECTION} }} }}"
                 )["runnerEnrollments"]


def _register(world, token: str, *, name: str = "runner-1",
              harnesses: tuple[str, ...] = (GENERIC,)):
    return world["client"].post("/runner/register", json={
        "name": name, "enrollment_token": token,
        "harnesses": list(harnesses), "labels": {"pool": "default"},
    })


def _task(engine, org_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(select(issue.c.id).where(
            issue.c.organization_id == org_id).order_by(issue.c.identifier)).scalars().first()


def _queue(world, *, org: str = "alpha") -> dict:
    tenant = world[org]
    return runner_api.enqueue_task(
        world["engine"], organization_id=tenant["org_id"],
        task_id=_task(world["engine"], tenant["org_id"]),
        harness=GENERIC, prompt="do the task")


def _claim(world, identity: dict, poll_ms: int = 0):
    return world["client"].post(
        "/runner/lease", json={"runner_id": identity["runner_id"], "poll_timeout_ms": poll_ms},
        headers={"Authorization": f"Bearer {identity['token']}"})


def _enrollment_row(engine, enrollment_id: str) -> dict:
    with engine.connect() as conn:
        return dict(conn.execute(select(runner_enrollment).where(
            runner_enrollment.c.id == enrollment_id)).mappings().one())


def _audit_rows(engine, org_id: str, action: str) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(
            select(audit_event).where(and_(
                audit_event.c.organization_id == org_id,
                audit_event.c.action == action,
            )).order_by(audit_event.c.seq)).mappings().all()]


def _invite(client: TestClient, token: str, email: str = "second@alpha.test") -> str:
    return _data(client, token, f'''
        mutation {{ memberInvite(input: {{name: "Second", email: "{email}"}}) {{
          success user {{ id }} }} }}''')["memberInvite"]["user"]["id"]


def _member_token(world, org: str = "alpha") -> str:
    tenant = world[org]
    member_id = _invite(world["client"], tenant["token"])
    _metadata, token = world["store"].create_api_key(tenant["org_id"], member_id,
                                                     "member-key", "read_write")
    return token


# ------------------------------------------------------- the token exists exactly once


def test_the_token_is_returned_once_and_only_its_hash_is_stored(world):
    """The plaintext lives in one response; the row keeps a sha256 and nothing else.

    "Hash-only at rest" is checked against the whole row, not against a named column:
    a future column that copied the token would be caught by reading every value.
    """
    created = _create(world, name="mac-mini-01")
    token = created["token"]
    assert token and len(token) >= 20, created
    enrollment = created["enrollment"]
    assert enrollment["status"] == "pending" and enrollment["usable"] is True
    assert enrollment["label"] == "mac-mini-01"

    row = _enrollment_row(world["engine"], enrollment["id"])
    assert row["token_hash"] == runner_api.token_hash(token)
    # The plaintext appears in no column of the stored row.
    assert all(str(value) != token for value in row.values()), row
    assert token not in json.dumps(row, default=str)

    # Every read path afterwards omits it, by name and in the shape.
    listed = _enrollments(world)
    assert [item["id"] for item in listed] == [enrollment["id"]]
    assert set(listed[0]) == set(ENROLLMENT_FIELDS), listed[0]
    # ``hint`` (the token's last four characters) is a fragment of a credential and is
    # not served either - the same omission agent_runners.py makes for token_hint.
    assert "hint" not in json.dumps(listed)
    assert token[-4:] not in json.dumps(listed), listed

    # The command is the exact line an operator runs, carrying this token once.
    assert token in created["command"]
    assert created["command"].startswith(
        f"HOJA_ENROLLMENT_TOKEN={token} hoja-runnerd --enroll ")
    assert "--name mac-mini-01" in created["command"]
    assert created["command"].count(token) == 1
    assert created["heartbeatIntervalS"] == runner_api.HEARTBEAT_INTERVAL_S


def test_the_response_carries_all_three_commands_in_the_order_they_are_run(world, tmp_path,
                                                                          monkeypatch):
    """HOJA-712: install, enroll, start — and the URL the install line names is served.

    The enroll line registers and exits, so a response that carried only that line left
    the machine idle with a spent token. And it began with a bare ``hoja-runnerd`` that no
    machine had, because the package is published nowhere: the deployment serves the wheel
    instead, and ``installCommand`` names that URL.
    """
    root = tmp_path / "downloads"
    root.mkdir()
    wheel = root / "hoja_runnerd-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    monkeypatch.setenv("HOJA_DOWNLOAD_ROOT", str(root))

    created = _create(world, name="mac-mini-01")
    token = created["token"]
    assert created["downloadUrl"].endswith("/downloads/hoja_runnerd-0.1.0-py3-none-any.whl")
    assert created["installCommand"] == f"pipx install {created['downloadUrl']}"
    assert created["startCommand"] == (
        f"hoja-runnerd --base-url {created['command'].split('--base-url ')[1].split(' ')[0]}"
        " --state-dir ~/.hoja/runner")

    # The three lines are the same flags the daemon parses, and only the enroll one spends
    # the token: a start that carried it would be a credential in a shell history and on
    # `/proc/<pid>/cmdline`.
    assert "--enroll" in created["command"] and token in created["command"]
    assert token not in created["startCommand"]
    assert "--enroll" not in created["startCommand"]
    assert "--state-dir ~/.hoja/runner" in created["command"], \
        "the start line reads the identity from the directory enroll wrote it to"
    # A start does not name a harness: the default is the one the control plane recorded at
    # enroll (HOJA-602-A-20), and a second answer here would silently diverge from it.
    assert "--default-harness" not in created["startCommand"]

    # And the artifact the install line names is actually served, without a credential.
    served = world["client"].get("/downloads/hoja_runnerd-0.1.0-py3-none-any.whl")
    assert served.status_code == 200, served.text
    assert served.content == wheel.read_bytes()


def test_an_unpublished_wheel_is_null_rather_than_a_url_that_would_404(world, tmp_path,
                                                                      monkeypatch):
    """A checkout that never built the wheel still mints tokens; it must say so."""
    monkeypatch.setenv("HOJA_DOWNLOAD_ROOT", str(tmp_path / "never-built"))
    created = _create(world, name="mac-mini-02")
    assert created["installCommand"] is None
    assert created["downloadUrl"] is None
    # The other two steps are unaffected: they are strings the server composes from flags
    # and this request's URL, not from a file on disk.
    assert created["startCommand"].startswith("hoja-runnerd --base-url ")
    assert created["command"].startswith(f"HOJA_ENROLLMENT_TOKEN={created['token']} ")


def test_the_enrollment_becomes_spent_once_a_runner_uses_it(world):
    created = _create(world)
    registered = _register(world, created["token"], name="runner-1")
    assert registered.status_code == 200, registered.text
    identity = registered.json()
    # The enroll response publishes the cadence too (HOJA-635 item 3).
    assert identity["heartbeat_interval_s"] == runner_api.HEARTBEAT_INTERVAL_S

    listed = _enrollments(world)
    assert listed[0]["status"] == "spent" and listed[0]["usable"] is False
    assert listed[0]["usedByRunner"] == identity["runner_id"]
    assert listed[0]["usedAt"]


def test_revoking_an_unspent_enrollment_closes_the_enroll_route(world):
    """``runnerEnrollmentRevoke`` is what "revoked token is refused at /runner/enroll" means."""
    created = _create(world)
    enrollment_id = created["enrollment"]["id"]
    revoked = _data(world["client"], world["alpha"]["token"],
                    f'mutation {{ runnerEnrollmentRevoke(id: "{enrollment_id}") }}')
    assert revoked["runnerEnrollmentRevoke"] is True

    refused = _register(world, created["token"])
    assert refused.status_code == 401, refused.text
    assert refused.json()["code"] == runner_api.ERROR_UNAUTHORIZED
    assert "revoked" in refused.json()["message"], refused.json()

    listed = _enrollments(world)
    assert listed[0]["status"] == "expired" and listed[0]["usable"] is False
    # The row survives the revocation: the table is the ledger of "this token existed
    # and was withdrawn", and the audit row says why it stopped working.
    assert _enrollment_row(world["engine"], enrollment_id)["used_at"] is None
    # Revoking twice is a no-op, not an error, and writes no second audit row.
    again = _data(world["client"], world["alpha"]["token"],
                  f'mutation {{ runnerEnrollmentRevoke(id: "{enrollment_id}") }}')
    assert again["runnerEnrollmentRevoke"] is False
    assert len(_audit_rows(world["engine"], world["alpha"]["org_id"],
                           "runner.enrollment.revoke")) == 1


# ------------------------------------------------------- a revoked runner cannot claim


def test_a_revoked_runner_is_refused_at_claim(world):
    """``runnerRevoke`` turns the machine off, and /runner/claim stops serving it work."""
    created = _create(world)
    identity = _register(world, created["token"], name="runner-1").json()
    queued = _queue(world)
    claimed = _claim(world, identity)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["lease"]["task_id"] == queued["task_id"]

    revoked = _data(world["client"], world["alpha"]["token"],
                    f'mutation {{ runnerRevoke(id: "{identity["runner_id"]}") }}')
    assert revoked["runnerRevoke"] is True

    # The token it holds is refused on the claim route itself: ``_runner_from`` resolves
    # the bearer against runner.enabled/revoked_at before the queue is consulted.
    refused = _claim(world, identity)
    assert refused.status_code == 401, refused.text
    assert refused.json()["code"] == runner_api.ERROR_UNAUTHORIZED

    with world["engine"].connect() as conn:
        row = dict(conn.execute(select(runner).where(
            runner.c.id == identity["runner_id"])).mappings().one())
    assert row["enabled"] is False and row["revoked_at"]

    # A second revoke is refused rather than reported as a second revocation.
    again = _post(world["client"], world["alpha"]["token"],
                  f'mutation {{ runnerRevoke(id: "{identity["runner_id"]}") }}')
    assert again["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT", again
    assert len(_audit_rows(world["engine"], world["alpha"]["org_id"], "runner.revoke")) == 1


# ------------------------------------------------------------------- authority and tenancy


def test_a_member_gets_forbidden_on_every_part_of_the_surface(world):
    created = _create(world)
    enrollment_id = created["enrollment"]["id"]
    member = _member_token(world)
    for query in (
        "query { runnerEnrollments { id } }",
        'mutation { runnerEnrollmentCreate(name: "x") { token } }',
        f'mutation {{ runnerEnrollmentRevoke(id: "{enrollment_id}") }}',
        'mutation { runnerRevoke(id: "whatever") }',
    ):
        refused = _post(world["client"], member, query)
        assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN", refused
        assert "admin" in refused["errors"][0]["message"].lower(), refused
    # Nothing the member attempted changed the row.
    assert _enrollments(world)[0]["usable"] is True


def test_one_workspace_cannot_see_or_revoke_anothers_rows(world):
    """The cross-tenant probe: under RLS this runs as a role without BYPASSRLS."""
    alpha_created = _create(world, org="alpha", name="alpha-machine")
    _create(world, org="bravo", name="bravo-machine")

    # The list is this tenant's only.
    assert [row["label"] for row in _enrollments(world, org="alpha")] == ["alpha-machine"]
    assert [row["label"] for row in _enrollments(world, org="bravo")] == ["bravo-machine"]

    # Bravo cannot revoke alpha's enrollment by id: it is not in bravo's workspace, and
    # the answer is the same "no such row" a fabricated id would get.
    refused = _post(world["client"], world["bravo"]["token"],
                    f'mutation {{ runnerEnrollmentRevoke(id: "{alpha_created["enrollment"]["id"]}") }}')
    assert refused["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT", refused
    assert _enrollments(world, org="alpha")[0]["usable"] is True
    assert _register(world, alpha_created["token"], name="alpha-runner").status_code == 200

    # The same for a runner: bravo cannot turn alpha's machine off.
    with world["engine"].connect() as conn:
        runner_id = conn.execute(select(runner.c.id).where(
            runner.c.organization_id == world["alpha"]["org_id"])).scalars().first()
    refused_runner = _post(world["client"], world["bravo"]["token"],
                           f'mutation {{ runnerRevoke(id: "{runner_id}") }}')
    assert refused_runner["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT", refused_runner
    with world["engine"].connect() as conn:
        row = dict(conn.execute(select(runner).where(
            runner.c.id == runner_id)).mappings().one())
    assert row["enabled"] is True and row["revoked_at"] is None


# ----------------------------------------------------------------------------- auditing


def test_every_write_writes_exactly_one_audit_event(world):
    """FR-036 applied to credentials: minting, withdrawing and disabling are all audited."""
    created = _create(world, name="audited-machine")
    minted = _audit_rows(world["engine"], world["alpha"]["org_id"], "runner.enrollment.create")
    assert len(minted) == 1, minted
    assert minted[0]["target_kind"] == "runner_enrollment"
    assert minted[0]["target_id"] == created["enrollment"]["id"]
    # The audit row names the enrollment and never its secret.
    assert created["token"] not in (minted[0]["detail"] or "")
    assert "audited-machine" in (minted[0]["detail"] or "")

    _data(world["client"], world["alpha"]["token"],
          f'mutation {{ runnerEnrollmentRevoke(id: "{created["enrollment"]["id"]}") }}')
    withdrawn = _audit_rows(world["engine"], world["alpha"]["org_id"],
                            "runner.enrollment.revoke")
    assert len(withdrawn) == 1 and withdrawn[0]["target_id"] == created["enrollment"]["id"]

    second = _create(world, name="second-machine")
    identity = _register(world, second["token"], name="second").json()
    _data(world["client"], world["alpha"]["token"],
          f'mutation {{ runnerRevoke(id: "{identity["runner_id"]}") }}')
    revoked = _audit_rows(world["engine"], world["alpha"]["org_id"], "runner.revoke")
    assert len(revoked) == 1 and revoked[0]["target_id"] == identity["runner_id"]

    # The chain still verifies with three new event kinds in it.
    assert audit.verify_chain(world["engine"], world["alpha"]["org_id"]) is None


# ---------------------------------------------------------------------- input handling


def test_a_bad_name_or_ttl_is_a_field_error_not_a_500(world):
    for query, field in (
        ('mutation { runnerEnrollmentCreate(name: "  ") { token } }', "name"),
        ('mutation { runnerEnrollmentCreate(name: "x", ttlMinutes: -1) { token } }',
         "ttlMinutes"),
        (f'mutation {{ runnerEnrollmentCreate(name: "x", ttlMinutes: {10 ** 9}) {{ token }} }}',
         "ttlMinutes"),
    ):
        refused = _post(world["client"], world["alpha"]["token"], query)
        assert refused["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT", refused
        assert refused["errors"][0]["extensions"]["field"] == field, refused
    assert _enrollments(world) == []


def test_a_zero_ttl_means_no_expiry(world):
    """``ttlMinutes: 0`` is "never" — an air-gapped install may only be visited once.

    Omitting the argument is not the same request: the field defaults to a day, so a
    client that wants no expiry has to say so.
    """
    created = _create(world, ttl_minutes=0)
    assert created["enrollment"]["expiresAt"] is None
    assert created["enrollment"]["status"] == "pending"


def test_an_omitted_ttl_defaults_to_a_day(world):
    created = _create(world, ttl_minutes=None)
    assert created["enrollment"]["expiresAt"]
    assert created["enrollment"]["status"] == "pending"
