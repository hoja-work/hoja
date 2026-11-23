import json

import pytest
from sqlalchemy import select

from hoja_server import cli, google_login, secrets_box, tenant_context
from hoja_server.db import (
    global_identity,
    make_engine,
    migrate,
    model_provider,
    organization,
    organization_membership,
    team,
    tenant_membership_hint,
    user,
)
from hoja_server.workspace_provision import ProvisionError, provision, validate_vanity_host


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "k" * 32)
    eng = make_engine(f"sqlite:///{tmp_path / 'shared.db'}")
    migrate(eng)
    return eng


def test_creates_workspace_with_owner_team_and_sealed_provider(engine):
    out = provision(engine, slug="acme", owner_email="Owner@Acme.io",
                    provider={"row_id": "default-trefoil", "name": "Included", "provider": "openai",
                              "model_id": "m", "base_url": "https://x", "context_window": 1000, "api_key": "sk-1"})
    org = out["organization_id"]
    with tenant_context.system(), engine.connect() as c:
        owner = c.execute(select(user).where(user.c.organization_id == org)).mappings().one()
        assert owner["email"] == "owner@acme.io" and owner["admin"]
        assert c.execute(select(team.c.default_state_id).where(team.c.organization_id == org)).scalar_one()
        row = c.execute(select(model_provider).where(model_provider.c.organization_id == org)).mappings().one()
    assert row["id"] == f"default-trefoil-{org}"
    assert secrets_box.decrypt_secret(row["encrypted_api_key"], org) == "sk-1"
    with pytest.raises(secrets_box.SecretBoxError):
        secrets_box.decrypt_secret(row["encrypted_api_key"], "other-org")


def test_repeat_is_idempotent_and_foreign_owner_is_refused(engine):
    first = provision(engine, slug="acme", owner_email="o@acme.io")
    again = provision(engine, slug="acme", owner_email="o@acme.io")
    assert again == {**first, "created": False}
    with pytest.raises(ProvisionError) as exc:
        provision(engine, slug="ACME", owner_email="intruder@x.io")
    assert exc.value.reason == "slug_taken"
    with pytest.raises(ProvisionError):
        provision(engine, slug="bad--slug", owner_email="o@acme.io")


def test_existing_identity_gains_membership_and_owner_signs_in(engine):
    first = provision(engine, slug="one", owner_email="o@acme.io")
    with tenant_context.system(), engine.begin() as c:
        result = google_login.resolve_member(c, email="o@acme.io", subject="g-1",
                                             organization_id=first["organization_id"])
    assert result["action"] == google_login.ACTION_LOGIN
    second = provision(engine, slug="two", owner_email="o@acme.io")
    with tenant_context.system(), engine.connect() as c:
        ident = c.execute(select(global_identity.c.id).where(global_identity.c.email == "o@acme.io")).scalar_one()
        orgs = set(c.execute(select(organization_membership.c.organization_id)
                             .where(organization_membership.c.identity_id == ident)).scalars())
    assert orgs == {first["organization_id"], second["organization_id"]}


# --- vanity host, owner admission, idempotency ---


def _members(engine, org_id):
    with tenant_context.system(), engine.connect() as c:
        return c.execute(select(organization_membership).where(
            organization_membership.c.organization_id == org_id)).mappings().all()


def test_owner_is_a_member_at_creation_without_any_prior_identity(engine):
    # Core, 2026-11-16: the owner had no global_identity at sign-up, so the workspace
    # was created with zero memberships. The owner must be a member from the start.
    out = provision(engine, slug="core", owner_email="Christian@Core.Shop", vanity_host="core.hoja.work")
    assert out["vanity_host"] == "core.hoja.work" and out["owner_membership"] == "active"
    rows = _members(engine, out["organization_id"])
    assert len(rows) == 1 and rows[0]["active"]
    with tenant_context.system(), engine.connect() as c:
        assert c.execute(select(organization.c.vanity_host).where(
            organization.c.id == out["organization_id"])).scalar_one() == "core.hoja.work"
        hint = c.execute(select(tenant_membership_hint).where(
            tenant_membership_hint.c.slug == "core")).mappings().one()
    assert hint["email"] == "christian@core.shop" and hint["role"] == "admin"


def test_owner_first_sign_in_on_the_vanity_host_is_admitted_and_nobody_else(engine):
    out = provision(engine, slug="core", owner_email="christian@core.shop", vanity_host="core.hoja.work")
    org = out["organization_id"]
    with tenant_context.system(), engine.begin() as c:
        owner = google_login.resolve_member(c, email="Christian@core.shop", subject="g-1", organization_id=org)
        stranger = google_login.resolve_member(c, email="eve@core.shop", subject="g-2", organization_id=org)
    assert owner["action"] == google_login.ACTION_LOGIN and owner["organization_id"] == org
    assert stranger == {"action": google_login.ACTION_DENIED, "reason": "not_invited"}
    assert len(_members(engine, org)) == 1


def test_provisioned_owner_gains_nothing_in_another_workspace(engine):
    provision(engine, slug="core", owner_email="christian@core.shop", vanity_host="core.hoja.work")
    other = provision(engine, slug="acme", owner_email="boss@acme.io", vanity_host="acme.hoja.work")
    with tenant_context.system(), engine.begin() as c:
        result = google_login.resolve_member(c, email="christian@core.shop", subject="g-1",
                                             organization_id=other["organization_id"])
    assert result["action"] == google_login.ACTION_DENIED


@pytest.mark.parametrize("host,reason", [
    ("Core.hoja.work", "invalid_vanity_host"),
    ("core.example.com", "invalid_vanity_host"),
    ("core.eu.hoja.work", "invalid_vanity_host"),
    ("hoja.work", "invalid_vanity_host"),
    ("other.hoja.work", "vanity_host_slug_mismatch"),
])
def test_vanity_host_validation(engine, host, reason):
    with pytest.raises(ProvisionError) as exc:
        provision(engine, slug="core", owner_email="o@core.shop", vanity_host=host)
    assert exc.value.reason == reason
    with tenant_context.system(), engine.connect() as c:
        assert c.execute(select(organization.c.id)).first() is None


def test_reserved_names_and_base_domain_follow_the_environment(monkeypatch):
    with pytest.raises(ProvisionError) as exc:
        validate_vanity_host("admin.hoja.work", slug="admin")
    assert exc.value.reason == "reserved_vanity_host"
    monkeypatch.setenv("HOJA_TENANT_BASE_DOMAIN", "example.dev")
    assert validate_vanity_host("acme.example.dev", slug="acme") == "acme.example.dev"
    with pytest.raises(ProvisionError):
        validate_vanity_host("acme.hoja.work", slug="acme")


def test_reserved_slug_is_refused(engine):
    with pytest.raises(ProvisionError) as exc:
        provision(engine, slug="app", owner_email="o@x.io")
    assert exc.value.reason == "reserved_slug"


def test_rerun_is_a_no_op_and_conflicts_are_refused(engine):
    first = provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    again = provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    assert again == {**first, "created": False}
    assert again["repaired"] == []
    # Omitting the host on a re-run reports the stored one rather than clearing it.
    assert provision(engine, slug="core", owner_email="o@core.shop")["vanity_host"] == "core.hoja.work"
    with tenant_context.system(), engine.begin() as c:
        c.execute(organization.update().where(organization.c.id == first["organization_id"])
                  .values(vanity_host="legacy.hoja.work"))
    with pytest.raises(ProvisionError) as exc:
        provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    assert exc.value.reason == "vanity_host_conflict"


def test_a_host_held_by_another_workspace_is_refused(engine):
    held = provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    other = provision(engine, slug="acme", owner_email="o@acme.io")
    with tenant_context.system(), engine.begin() as c:
        c.execute(organization.update().where(organization.c.id == held["organization_id"])
                  .values(vanity_host="acme.hoja.work"))
    with pytest.raises(ProvisionError) as exc:
        provision(engine, slug="acme", owner_email="o@acme.io", vanity_host="acme.hoja.work")
    assert exc.value.reason == "vanity_host_taken"
    assert other["vanity_host"] is None


def test_rerun_repairs_a_workspace_from_the_old_provisioner(engine):
    # The exact state Core was left in: no vanity host, zero memberships, no directory entry.
    out = provision(engine, slug="core", owner_email="o@core.shop")
    with tenant_context.system(), engine.begin() as c:
        c.execute(organization_membership.delete())
        c.execute(tenant_membership_hint.delete())
    repaired = provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    assert repaired["created"] is False
    assert repaired["repaired"] == ["vanity_host", "owner_membership", "directory_hint"]
    assert len(_members(engine, out["organization_id"])) == 1


def test_a_revoked_owner_membership_is_reported_not_restored(engine):
    out = provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    with tenant_context.system(), engine.begin() as c:
        c.execute(organization_membership.update().values(active=False))
    again = provision(engine, slug="core", owner_email="o@core.shop", vanity_host="core.hoja.work")
    assert again["owner_membership"] == "inactive" and again["repaired"] == []
    assert not _members(engine, out["organization_id"])[0]["active"]


def test_cli_create_workspace_with_vanity_host(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOJA_SECRET_KEY", "k" * 32)
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    migrate(make_engine(url))
    args = ["create-workspace", "--database-url", url, "--slug", "core",
            "--owner-email", "o@core.shop", "--vanity-host", "core.hoja.work"]
    cli.main(args)
    created = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert created["created"] is True and created["vanity_host"] == "core.hoja.work"
    cli.main(args)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["created"] is False
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["create-workspace", "--database-url", url, "--slug", "core",
                  "--owner-email", "o@core.shop", "--vanity-host", "elsewhere.hoja.work"])
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out.strip())["error"] == "vanity_host_slug_mismatch"
    assert "core.hoja.work" in captured.err
