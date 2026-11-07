"""The one sign-in entry, the owner it must never lock out, and its refusals.

These pin the behaviour that production needed and did not have:

* the address the provisioner recorded as owner is admitted to its own
  workspace even after that workspace already has an organization (the
  clover.hoja.work failure returned ``not_invited`` to the owner);
* a refused sign-in is a page a person can act on, with an error id, while the
  JSON contract and the security boundary stay intact;
* a tenant host has no local credential surface and points at the one entry.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import google_login, identity_transfer
from hoja_server.app import create_app
from hoja_server.db import (
    global_identity,
    make_engine,
    migrate,
    organization,
    organization_membership,
    user,
)

KEY = "signin-entry-key-that-is-long-enough-0123"
TENANT = "acme.hoja.work"
CONTROL = "https://app.hoja.work"
OWNER = "luisalejandroclover@gmail.com"


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "app.db")
    migrate(eng)
    return eng


def _seed_workspace(engine, *, profile_email: str, active: bool = True) -> str:
    """An organization that already exists, with one profile that is not the owner."""
    org_id = "org-1"
    with engine.begin() as conn:
        conn.execute(
            organization.insert().values(
                id=org_id,
                name="Clover4",
                url_key="clover4",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        conn.execute(
            user.insert().values(
                id="user-1",
                organization_id=org_id,
                name="Existing",
                display_name="Existing",
                email=profile_email,
                active=active,
                admin=False,
                timezone="UTC",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
    return org_id


def _resolve(engine, email: str, subject: str = "sub-owner") -> dict:
    with engine.begin() as conn:
        return google_login.resolve_verified_email(
            conn, email=email, subject=subject, name="Luis"
        )


# --------------------------------------------------------------------------- #
# The owner is admitted to the workspace that named them
# --------------------------------------------------------------------------- #


def test_the_provisioned_owner_is_admitted_to_a_workspace_that_already_has_an_org(
    engine, monkeypatch
):
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    org_id = _seed_workspace(engine, profile_email="luisrincon@clover4.app")

    result = _resolve(engine, OWNER)

    assert result["action"] == google_login.ACTION_LOGIN
    assert result["organization_id"] == org_id
    assert result.get("adopted") is True
    with engine.connect() as conn:
        row = conn.execute(
            select(user.c.email, user.c.admin, user.c.active).where(user.c.email == OWNER)
        ).first()
        assert row is not None and bool(row[1]) and bool(row[2])
        identity = conn.execute(
            select(global_identity.c.id).where(global_identity.c.email == OWNER)
        ).first()
        assert identity is not None
        membership = conn.execute(
            select(organization_membership.c.active).where(
                organization_membership.c.identity_id == identity[0]
            )
        ).first()
        assert bool(membership[0])


def test_the_owner_address_is_compared_case_insensitively(engine, monkeypatch):
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    _seed_workspace(engine, profile_email="luisrincon@clover4.app")

    assert _resolve(engine, OWNER.upper())["action"] == google_login.ACTION_LOGIN


def test_a_deactivated_owner_profile_is_not_resurrected(engine, monkeypatch):
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    _seed_workspace(engine, profile_email=OWNER, active=False)

    result = _resolve(engine, OWNER)

    assert result["action"] == google_login.ACTION_DENIED
    assert result["reason"] == "account_disabled"


def test_a_stale_identity_link_is_repaired_instead_of_failing(engine, monkeypatch):
    """A profile whose membership still points at an old identity must not 500."""
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    org_id = _seed_workspace(engine, profile_email=OWNER)
    with engine.begin() as conn:
        conn.execute(
            global_identity.insert().values(
                id="stale-identity", email="old@clover4.app",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        conn.execute(
            organization_membership.insert().values(
                id="stale-membership", identity_id="stale-identity",
                organization_id=org_id, profile_user_id="user-1",
                active=True, created_at="2026-01-01T00:00:00+00:00",
            )
        )

    result = _resolve(engine, OWNER)

    assert result["action"] == google_login.ACTION_LOGIN
    assert result["organization_id"] == org_id
    assert result["identity_id"] != "stale-identity"
    with engine.connect() as conn:
        linked = conn.execute(
            select(organization_membership.c.identity_id).where(
                organization_membership.c.profile_user_id == "user-1"
            )
        ).scalar_one()
        assert linked == result["identity_id"]


def test_a_revoked_owner_membership_stays_revoked(engine, monkeypatch):
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    org_id = _seed_workspace(engine, profile_email=OWNER)
    with engine.begin() as conn:
        conn.execute(
            global_identity.insert().values(
                id="identity-1", email=OWNER, created_at="2026-01-01T00:00:00+00:00"
            )
        )
        conn.execute(
            organization_membership.insert().values(
                id="membership-1", identity_id="identity-1", organization_id=org_id,
                profile_user_id="user-1", active=False,
                created_at="2026-01-01T00:00:00+00:00",
            )
        )

    result = _resolve(engine, OWNER)

    assert result["action"] == google_login.ACTION_DENIED
    assert result["reason"] == "no_membership"
    with engine.connect() as conn:
        assert not conn.execute(
            select(organization_membership.c.active).where(
                organization_membership.c.id == "membership-1"
            )
        ).scalar_one()


def test_a_stranger_is_still_refused_when_the_owner_is_configured(engine, monkeypatch):
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    _seed_workspace(engine, profile_email="luisrincon@clover4.app")

    result = _resolve(engine, "someone@elsewhere.test", subject="sub-stranger")

    assert result["action"] == google_login.ACTION_DENIED
    assert result["reason"] == "not_invited"
    with engine.connect() as conn:
        assert conn.execute(
            select(user.c.id).where(user.c.email == "someone@elsewhere.test")
        ).first() is None


# --------------------------------------------------------------------------- #
# The one entry, and no credential surface on a tenant
# --------------------------------------------------------------------------- #


@pytest.fixture
def tenant_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", CONTROL)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", KEY)
    db_path = tmp_path / "tenant.db"
    eng = make_engine(db_path)
    migrate(eng)
    app = create_app(str(db_path), open_mode=False)
    with TestClient(app, base_url=f"https://{TENANT}") as client:
        yield client, eng


def _token(**over):
    kwargs = dict(
        key=KEY, audience=TENANT, email=OWNER, subject="sub-1",
        name="Luis", email_verified=True,
    )
    kwargs.update(over)
    return identity_transfer.issue(**kwargs)


def test_the_tenant_config_points_at_the_control_plane_and_hides_credentials(tenant_client):
    client, _ = tenant_client
    body = client.get("/api/signup/config").json()

    assert body["loginUrl"] == f"{CONTROL}/signin?next=https%3A%2F%2F{TENANT}%2F"
    assert body["credentialSignIn"] is False


def test_the_tenant_has_no_password_or_api_key_sign_in(tenant_client):
    client, _ = tenant_client
    r = client.post(
        "/auth/login", json={"email": "someone@example.com", "password": "irrelevant"}
    )

    assert r.status_code == 403
    assert r.json()["reason"] == "sign_in_moved"
    assert r.json()["signInUrl"] == f"{CONTROL}/signin?next=https%3A%2F%2F{TENANT}%2F"


@pytest.fixture
def control_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", CONTROL)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", KEY)
    db_path = tmp_path / "control.db"
    eng = make_engine(db_path)
    migrate(eng)
    app = create_app(str(db_path), open_mode=False)
    with TestClient(app, base_url=CONTROL) as client:
        yield client, eng


def test_the_control_plane_serves_the_one_sign_in_page(control_client):
    client, _ = control_client
    r = client.get("/signin")

    assert r.status_code == 200
    assert "Sign in to Hoja" in r.text
    assert "/auth/google/start?return_to=%2Fsignin" in r.text
    assert client.get("/api/signup/config").json()["credentialSignIn"] is True


def test_the_sign_in_page_keeps_the_requested_workspace(control_client):
    client, _ = control_client
    r = client.get(f"/signin?next=https://{TENANT}/")

    assert "return_to=%2Fsignin%3Fnext%3Dhttps%253A%252F%252Facme.hoja.work%252F" in r.text


def test_another_host_on_the_entry_bounces_to_the_canonical_page(tenant_client):
    client, _ = tenant_client
    r = client.get("/signin", follow_redirects=False)

    assert r.status_code == 302
    assert r.headers["location"] == f"{CONTROL}/signin?next=https%3A%2F%2Facme.hoja.work%2F"


# --------------------------------------------------------------------------- #
# A refusal is answerable
# --------------------------------------------------------------------------- #


def test_a_refused_browser_sign_in_gets_a_page_with_an_error_id(tenant_client):
    client, eng = tenant_client
    _seed_workspace(eng, profile_email=OWNER)
    r = client.get(
        f"/auth/google/finish?t={_token(email='someone@elsewhere.test', subject='s2')}",
        headers={"accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )

    assert r.status_code == 403
    assert r.headers["content-type"].startswith("text/html")
    assert "member of this workspace" in r.text
    assert "someone@elsewhere.test" in r.text
    assert "Error id" in r.text
    assert r.headers.get("x-hoja-error-id")


def test_the_refusal_page_offers_a_retry_that_ends_the_session(tenant_client):
    """Otherwise the retry re-mints the same handoff and loops on this page."""
    client, eng = tenant_client
    _seed_workspace(eng, profile_email=OWNER)
    r = client.get(
        f"/auth/google/finish?t={_token(email='someone@elsewhere.test', subject='s2')}",
        headers={"accept": "text/html"},
        follow_redirects=False,
    )

    assert r.status_code == 403
    assert f"{CONTROL}/signin/signout?next=https%3A%2F%2Facme.hoja.work%2F" in r.text


def test_a_refused_api_call_keeps_the_json_contract(tenant_client):
    client, eng = tenant_client
    _seed_workspace(eng, profile_email=OWNER)
    r = client.get(
        f"/auth/google/finish?t={_token(email='someone@elsewhere.test', subject='s2')}",
        follow_redirects=False,
    )

    assert r.status_code == 403
    assert r.json()["reason"] == "not_invited"
    assert r.json()["errorId"] == r.headers["x-hoja-error-id"]


def test_the_owner_gets_in_through_the_finish_route(tenant_client, monkeypatch):
    client, eng = tenant_client
    monkeypatch.setenv("HOJA_OWNER_EMAIL", OWNER)
    _seed_workspace(eng, profile_email="luisrincon@clover4.app")

    r = client.get(f"/auth/google/finish?t={_token()}", follow_redirects=False)

    assert r.status_code == 302
    assert "hoja_session" in r.headers.get("set-cookie", "")
