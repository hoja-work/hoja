"""Google sign-in for the app itself: transfer across origins, then identity.

The transfer token is the only thing that crosses from the control plane to a
tenant origin, so it is tested as an attacker would use it: replayed, retargeted,
aged out, tampered with, and signed with something too short to be a secret.

The resolver is tested for the property that actually matters: Google proves an
address, it does not hand out access. Only a brand-new database may *create* a
user, and only as its admin.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select

from hoja_server import google_login, identity_transfer
from hoja_server.db import (
    global_identity,
    identity_credential,
    make_engine,
    migrate,
    organization_membership,
    organization,
    team,
    user,
)

KEY = "transfer-key-that-is-long-enough-0123456789"
HOST = "acme.hoja.work"


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "app.db")
    migrate(eng)
    return eng


def _issue(**over):
    kwargs = dict(
        key=KEY, audience=HOST, email="luis@hoja.work", subject="sub-1",
        name="Luis", email_verified=True, now=1_000_000.0,
    )
    kwargs.update(over)
    return identity_transfer.issue(**kwargs)


# --------------------------------------------------------------------------- #
# The transfer token
# --------------------------------------------------------------------------- #


def test_a_fresh_token_redeems_on_its_own_host():
    claims = identity_transfer.redeem(
        key=KEY, token=_issue(), audience=HOST, now=1_000_010.0
    )
    assert claims["email"] == "luis@hoja.work"
    assert claims["aud"] == HOST
    assert claims["ev"] is True


def test_a_token_for_one_tenant_is_useless_on_another():
    with pytest.raises(identity_transfer.TransferError) as exc:
        identity_transfer.redeem(
            key=KEY, token=_issue(), audience="evil.hoja.work", now=1_000_010.0
        )
    assert exc.value.reason == "wrong_audience"


def test_an_old_token_is_refused_even_though_its_signature_is_good():
    token = _issue(ttl_seconds=120)
    with pytest.raises(identity_transfer.TransferError) as exc:
        identity_transfer.redeem(
            key=KEY, token=token, audience=HOST, now=1_000_000.0 + 121
        )
    assert exc.value.reason == "expired"


def test_a_tampered_payload_does_not_keep_its_signature():
    prefix, body, signature = _issue().split(".")
    forged = prefix + "." + body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + signature
    with pytest.raises(identity_transfer.TransferError) as exc:
        identity_transfer.redeem(key=KEY, token=forged, audience=HOST, now=1_000_010.0)
    assert exc.value.reason == "bad_signature"


def test_a_short_key_is_not_treated_as_a_secret():
    with pytest.raises(identity_transfer.TransferError) as exc:
        identity_transfer.issue(
            key="short", audience=HOST, email="a@b.co", subject="s", email_verified=True
        )
    assert exc.value.reason == "key_missing"


def test_an_unverified_address_is_never_transferred():
    with pytest.raises(identity_transfer.TransferError) as exc:
        _issue(email_verified=False)
    assert exc.value.reason == "email_unverified"


def test_garbage_is_refused_without_raising_anything_else():
    for token in ("", None, "a.b", "hjt1.notbase64.@@@", "other.x.y"):
        with pytest.raises(identity_transfer.TransferError):
            identity_transfer.redeem(key=KEY, token=token, audience=HOST, now=1.0)


# --------------------------------------------------------------------------- #
# Turning a verified address into an app user
# --------------------------------------------------------------------------- #


def _resolve(engine, **over):
    kwargs = dict(email="luis@hoja.work", subject="sub-1", name="Luis")
    kwargs.update(over)
    with engine.begin() as conn:
        result = google_login.resolve_verified_email(conn, **kwargs)
    return result


def test_the_first_user_of_an_empty_instance_becomes_the_admin(engine):
    result = _resolve(engine, workspace_slug="acme-demo")
    assert result["action"] == google_login.ACTION_BOOTSTRAPPED
    with engine.connect() as conn:
        admins = conn.execute(select(user.c.email, user.c.admin)).all()
        assert [(r[0], bool(r[1])) for r in admins] == [("luis@hoja.work", True)]
        org = conn.execute(select(organization.c.url_key)).first()
        assert org[0] == "acme-demo"
        # No password is ever stored for a Google account.
        assert conn.execute(select(identity_credential.c.identity_id)).first() is None
        team_row = conn.execute(select(team.c.id, team.c.default_state_id)).first()
        assert team_row[1], "the first team must have a default workflow state"


def test_a_second_sign_in_logs_the_same_person_in(engine):
    first = _resolve(engine)
    second = _resolve(engine)
    assert first["action"] == google_login.ACTION_BOOTSTRAPPED
    assert second["action"] == google_login.ACTION_LOGIN
    assert second["user_id"] == first["user_id"]
    assert second["organization_id"] == first["organization_id"]


def test_a_provisioned_owner_address_is_the_only_address_that_can_bootstrap(engine, monkeypatch):
    monkeypatch.setenv("HOJA_OWNER_EMAIL", "owner@hoja.work")
    stranger = _resolve(engine, email="stranger@hoja.work", subject="sub-stranger")
    assert stranger["action"] == google_login.ACTION_DENIED
    assert stranger["reason"] == "owner_only"
    with engine.connect() as conn:
        assert conn.execute(select(user.c.id)).first() is None

    owner = _resolve(engine, email="OWNER@hoja.work", subject="sub-owner")
    assert owner["action"] == google_login.ACTION_BOOTSTRAPPED
    with engine.connect() as conn:
        assert conn.execute(select(user.c.email)).scalar_one() == "owner@hoja.work"


def test_a_stranger_is_refused_on_a_workspace_that_already_exists(engine):
    _resolve(engine)
    stranger = _resolve(engine, email="someone@elsewhere.test", subject="sub-2")
    assert stranger["action"] == google_login.ACTION_DENIED
    assert stranger["reason"] == "not_invited"
    with engine.connect() as conn:
        assert conn.execute(select(user.c.id).where(user.c.email == "someone@elsewhere.test")).first() is None


def test_an_existing_password_user_is_linked_by_verified_address(engine):
    _resolve(engine)  # sets the instance up
    with engine.begin() as conn:
        from hoja_server.db import new_id
        conn.execute(organization_membership.delete())
        conn.execute(global_identity.delete())
        conn.execute(user.insert().values(
            id=new_id(), organization_id=conn.execute(select(organization.c.id)).first()[0],
            name="Invited", display_name="Invited", email="invited@hoja.work", active=True,
            admin=False, timezone="UTC", created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00"))
    linked = _resolve(engine, email="invited@hoja.work", subject="sub-3")
    assert linked["action"] == google_login.ACTION_LOGIN
    assert linked["linked"] is True


def test_an_inactive_membership_does_not_let_someone_back_in(engine):
    first = _resolve(engine)
    with engine.begin() as conn:
        conn.execute(organization_membership.update().values(active=False))
    again = _resolve(engine)
    assert again["action"] == google_login.ACTION_DENIED
    assert again["reason"] == "no_membership"
    assert first["user_id"]


# --------------------------------------------------------------------------- #
# Which hosts a handoff may bounce back to
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value,expected", [
    ("https://acme.hoja.work/", "https://acme.hoja.work/"),
    ("https://acme.hoja.work/signin?x=1", "https://acme.hoja.work/signin"),
    ("https://ACME.hoja.work/", "https://acme.hoja.work/"),
    ("https://app.hoja.work/", None),          # the control plane is not a tenant
    ("https://www.hoja.work/", None),
    ("https://hoja.work/", None),              # the apex itself
    ("https://evil.example/", None),           # not ours
    ("https://evil-hoja.work/", None),         # suffix trick
    ("http://acme.hoja.work/", None),          # not https
    ("/relative", None),
    ("", None),
    (None, None),
])
def test_only_our_tenant_hosts_can_be_a_handoff_target(value, expected):
    assert identity_transfer.tenant_target(value) == expected
