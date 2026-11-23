"""`hoja-serve tenant-doctor` and the two Core incidents it exists to catch.

* 2026-11-16: the shared provisioner created Core with no vanity_host and zero
  memberships, so core.hoja.work redirected to the marketing site and the owner
  could not get in.
* 2026-11-19: a Core admin was missing from the control-plane directory, so
  app.hoja.work offered him "Name your workspace" instead of Core.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, update
from starlette.testclient import TestClient

from hoja_server import cli, identity_transfer, tenant_context
from hoja_server.app import create_app
from hoja_server.db import (
    global_identity,
    new_id,
    organization,
    organization_membership,
    tenant_membership_hint,
    user,
)
from hoja_server.tenant_doctor import diagnose
from hoja_server.workspace_provision import provision
from tests import rls_mode

HOST = "core.hoja.work"
OWNER = "christian@core.shop"
MASTER = "control-plane-master-that-is-long-enough-0001"
ENV = {
    "HOJA_IDENTITY_TRANSFER_MASTER": MASTER,
    "HOJA_CONTROL_PLANE_URL": "https://app.hoja.work",
    "HOJA_TENANT_BASE_DOMAIN": "hoja.work",
}
ROUTE = """# core.hoja.work is served by the shared app (ADR-003); the organization's vanity_host pins it.
http:
  routers:
    hoja-tenant-core:
      rule: "Host(`core.hoja.work`)"
      entryPoints: [websecure]
      service: hoja
      middlewares: [secureHeaders]
      tls: {}
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "k" * 32)
    engine, kwargs = rls_mode.workspace(tmp_path, "doctor")
    return engine, kwargs


def _status(report) -> dict:
    return {c["check"]: c["status"] for c in report.checks}


def _counts(engine) -> tuple[int, int]:
    with tenant_context.system(), engine.connect() as c:
        return (c.execute(select(func.count()).select_from(organization_membership)).scalar_one(),
                c.execute(select(func.count()).select_from(global_identity)).scalar_one())


def test_a_workspace_provisioned_with_its_host_is_healthy(db):
    engine, _ = db
    provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    report = diagnose(engine, HOST, route_text=ROUTE, environ=ENV)
    status = _status(report)
    assert report.ok, report.lines()
    assert {k: status[k] for k in ("host", "vanity_host", "slug", "admins", "members", "owner_admission",
                                   "directory", "route", "handoff_key", "control_plane")} == dict.fromkeys(
        ("host", "vanity_host", "slug", "admins", "members", "owner_admission", "directory", "route",
         "handoff_key", "control_plane"), "ok")
    assert status["mail"] == "warn"  # reported, never blocking
    assert OWNER in report.facts["owner"]


def test_core_2026_11_16_no_vanity_host_and_zero_members_is_flagged(db):
    engine, _ = db
    # The old provisioner's output: owner profile, no host, no membership, no directory entry.
    provision(engine, slug="core", owner_email=OWNER)
    with tenant_context.system(), engine.begin() as c:
        c.execute(organization_membership.delete())
        c.execute(global_identity.delete())
        c.execute(tenant_membership_hint.delete())
    report = diagnose(engine, HOST, route_text=ROUTE, environ=ENV)
    assert not report.ok
    vanity = next(c for c in report.checks if c["check"] == "vanity_host")
    assert vanity["status"] == "fail" and "vanity_host=NULL" in vanity["detail"]
    assert f"--vanity-host {HOST}" in vanity["fix"]

    # The hand repair (vanity_host only): the host now resolves but nobody is a member yet.
    with tenant_context.system(), engine.begin() as c:
        c.execute(update(organization).values(vanity_host=HOST))
    before = _counts(engine)
    report = diagnose(engine, HOST, route_text=ROUTE, environ=ENV)
    status = _status(report)
    assert status["vanity_host"] == "ok"
    assert status["members"] == "warn"
    assert status["owner_admission"] == "ok"  # through the unclaimed-profile link
    assert "linking" in next(c for c in report.checks if c["check"] == "owner_admission")["detail"]
    assert _counts(engine) == before == (0, 0), "the admission probe must be rolled back"


def test_core_2026_11_19_admin_missing_from_the_directory_is_flagged(db):
    engine, _ = db
    out = provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    with tenant_context.system(), engine.begin() as c:
        c.execute(user.insert().values(id=new_id(), organization_id=out["organization_id"], name="alvaro",
                                       display_name="alvaro", email="alvaro@core.shop", active=True, admin=True,
                                       created_at="2026-11-16T20:27:32Z", updated_at="2026-11-16T20:27:32Z"))
    report = diagnose(engine, HOST, route_text=ROUTE, environ=ENV)
    directory = next(c for c in report.checks if c["check"] == "directory")
    assert directory["status"] == "warn" and "alvaro@core.shop" in directory["detail"]
    assert OWNER not in directory["detail"]
    assert report.ok  # members can still sign in on the host itself


def test_owner_sign_in_after_provisioning_with_vanity_host_succeeds(db, monkeypatch):
    """The end-to-end regression for 2026-11-16: provision, then the real hand-off on the host."""
    engine, kwargs = db
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", "https://app.hoja.work")
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_MASTER", MASTER)
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    out = provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    provision(engine, slug="acme", owner_email="boss@acme.shop")  # the old shape: no host
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False,
                     trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host="app.hoja.work")

    def finish(client, email, host=HOST):
        token = identity_transfer.issue(key=identity_transfer.tenant_key(MASTER, host), audience=host,
                                        email=email, subject="google-sub", email_verified=True)
        return client.get(f"/auth/google/finish?t={token}", headers={"Host": host}, follow_redirects=False)

    with TestClient(app, base_url=f"https://{HOST}", client=("127.0.0.1", 40002)) as client:
        owner = finish(client, OWNER)
        assert owner.status_code == 302, owner.text
        session = owner.cookies.get("hoja_session") or client.cookies.get("hoja_session")
        viewer = client.post("/graphql", json={"query": "{ workspace { id } }"}, headers={"Host": HOST},
                             cookies={"hoja_session": session})
        assert viewer.json()["data"]["workspace"]["id"] == out["organization_id"]
        client.cookies.clear()
        assert finish(client, "eve@core.shop").status_code == 403
        # Without a vanity host the app bounces the hand-off to the canonical host: what Core hit.
        bounced = finish(client, "boss@acme.shop", host="acme.hoja.work")
        assert bounced.status_code == 308
        assert bounced.headers["location"].startswith("https://app.hoja.work/")


def test_blocking_configuration_problems(db, tmp_path):
    engine, _ = db
    provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    report = diagnose(engine, HOST, routes_dir=str(tmp_path), environ={})
    status = _status(report)
    assert status["route"] == "fail"  # directory readable, file missing
    assert status["handoff_key"] == "fail"
    assert status["control_plane"] == "fail"
    assert not report.ok

    old = ROUTE.replace("tls: {}", "tls:\n        certResolver: letsencrypt")
    shared_key_only = {**{k: v for k, v in ENV.items() if k != "HOJA_IDENTITY_TRANSFER_MASTER"},
                       "HOJA_IDENTITY_TRANSFER_KEY": "x" * 40}
    status = _status(diagnose(engine, HOST, route_text=old, environ=shared_key_only))
    assert status["route"] == "warn" and status["handoff_key"] == "warn"
    assert _status(diagnose(engine, HOST, route_text="", environ=ENV))["route"] == "fail"
    assert _status(diagnose(engine, HOST, routes_dir=str(tmp_path / "absent"), environ=ENV))["route"] == "skip"


@pytest.mark.parametrize("host", ["app.hoja.work", "core.example.com", "a.b.hoja.work", "Core.hoja.work"])
def test_a_host_that_can_never_be_a_workspace_fails(db, host):
    engine, _ = db
    report = diagnose(engine, host, route_text=ROUTE, environ=ENV)
    assert _status(report)["host"] == "fail" and not report.ok


def test_an_address_that_would_be_refused_fails(db):
    engine, _ = db
    provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    report = diagnose(engine, HOST, owner_email="eve@core.shop", route_text=ROUTE, environ=ENV)
    admission = next(c for c in report.checks if c["check"] == "owner_admission")
    assert admission["status"] == "fail" and "not_invited" in admission["detail"]


def test_no_admin_fails(db):
    engine, _ = db
    provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    with tenant_context.system(), engine.begin() as c:
        c.execute(update(user).values(admin=False))
    assert _status(diagnose(engine, HOST, route_text=ROUTE, environ=ENV))["admins"] == "fail"


def test_cli_json_and_exit_codes(db, tmp_path, monkeypatch, capsys):
    engine, kwargs = db
    if kwargs.get("db_path") is None:
        pytest.skip("CLI path is exercised against SQLite")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    route = tmp_path / "tenant-core.yml"
    route.write_text(ROUTE, encoding="utf-8")
    args = ["tenant-doctor", HOST, "--db", kwargs["db_path"], "--route-file", str(route), "--json"]
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args)
    assert exit_info.value.code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    provision(engine, slug="core", owner_email=OWNER, vanity_host=HOST)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args[:-1])
    assert exit_info.value.code == 0
    assert "verdict: OK" in capsys.readouterr().out
