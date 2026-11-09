import pytest
from sqlalchemy import select

from hoja_server import google_login, secrets_box, tenant_context
from hoja_server.db import global_identity, make_engine, migrate, model_provider, organization_membership, team, user
from hoja_server.workspace_provision import ProvisionError, provision


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
