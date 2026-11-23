"""An invited person must be able to get into the workspace that invited them.

Three separate failures made that impossible, and each one gets a test here:

* the control plane's "No workspace yet" page was a dead end, so the invitation
  link had nowhere to be used;
* the invitation code was dropped by the return-target validator, so the Google
  round-trip landed on the workspace root with nothing to accept;
* acceptance required a 12-character password, which a Google-only workspace
  never gives anyone.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from hoja_server import identity_transfer
from hoja_server.app import create_app
from hoja_server.db import (
    global_identity,
    make_engine,
    migrate,
    organization,
    organization_membership,
    membership_invitation,
    user,
)
from hoja_server.store import Store

CONTROL = "https://app.hoja.work"
TENANT_HOST = "hoja.hoja.work"
CODE = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_ab"


def _engine(tmp_path, name):
    eng = make_engine(tmp_path / name)
    migrate(eng)
    return eng


def _seed(engine):
    """An existing workspace with one admin, then the invitation under test."""
    now = "2026-11-08T12:00:00+00:00"
    with engine.begin() as conn:
        conn.execute(organization.insert().values(
            id="org-hoja", name="Hoja", url_key="hoja",
            created_at=now, updated_at=now))
        conn.execute(user.insert().values(
            id="u-luis", organization_id="org-hoja", name="Luis", display_name="Luis",
            email="luis@hoja.work", active=True, admin=True, timezone="UTC",
            created_at=now, updated_at=now))
        conn.execute(global_identity.insert().values(
            id="i-luis", email="luis@hoja.work", created_at=now))
        conn.execute(organization_membership.insert().values(
            id="m-luis", identity_id="i-luis", organization_id="org-hoja",
            profile_user_id="u-luis", active=True, created_at=now))
    store = Store(engine)
    assert store.invite_member("org-hoja", "Kevin", "kevin@hoja.work", False, [])
    with engine.connect() as conn:
        kevin = conn.execute(select(user.c.id).where(
            user.c.email == "kevin@hoja.work")).scalar_one()
        identity = conn.execute(select(global_identity.c.id).where(
            global_identity.c.email == "kevin@hoja.work")).scalar_one()
    code = store.create_membership_invitation("org-hoja", kevin, "u-luis")
    return store, kevin, identity, code


# --------------------------------------------------------------------------- #
# Acceptance without a password
# --------------------------------------------------------------------------- #


def test_a_verified_identity_accepts_its_invitation_without_a_password(tmp_path):
    engine = _engine(tmp_path, "tenant.db")
    store, _kevin, identity, code = _seed(engine)

    accepted = store.accept_membership_invitation_by_identity(identity, code)

    assert accepted == {"id": "org-hoja", "name": "Hoja", "urlKey": "hoja"}
    with engine.connect() as conn:
        row = conn.execute(select(membership_invitation.c.accepted_at,
                                  membership_invitation.c.accepted_identity_id)).first()
        assert row[0] is not None and row[1] == identity


def test_a_code_used_once_cannot_be_used_again(tmp_path):
    engine = _engine(tmp_path, "tenant.db")
    store, _kevin, identity, code = _seed(engine)

    assert store.accept_membership_invitation_by_identity(identity, code)
    assert store.accept_membership_invitation_by_identity(identity, code) is None


def test_a_different_verified_address_cannot_spend_someone_elses_code(tmp_path):
    engine = _engine(tmp_path, "tenant.db")
    store, _kevin, _identity, code = _seed(engine)
    with engine.begin() as conn:
        conn.execute(global_identity.insert().values(
            id="i-stranger", email="stranger@example.com",
            created_at="2026-11-08T12:00:00+00:00"))

    assert store.accept_membership_invitation_by_identity("i-stranger", code) is None


def test_an_expired_code_is_refused(tmp_path):
    engine = _engine(tmp_path, "tenant.db")
    store, _kevin, identity, code = _seed(engine)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with engine.begin() as conn:
        conn.execute(update(membership_invitation).values(expires_at=past))

    assert store.accept_membership_invitation_by_identity(identity, code) is None


def test_no_identity_means_no_acceptance(tmp_path):
    engine = _engine(tmp_path, "tenant.db")
    store, _kevin, _identity, code = _seed(engine)

    assert store.accept_membership_invitation_by_identity(None, code) is None
    assert store.accept_membership_invitation_by_identity("i-nobody", code) is None


# --------------------------------------------------------------------------- #
# The code survives the redirect that carries it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shape", [
    f"https://{TENANT_HOST}/?invite={CODE}",
    f"https://{TENANT_HOST}/somewhere?invite={CODE}",
])
def test_an_invitation_code_survives_a_return_target(shape):
    assert CODE in (identity_transfer.tenant_target(shape) or "")


@pytest.mark.parametrize("shape", [
    f"https://evil.example/?invite={CODE}",
    f"https://app.hoja.work/?invite={CODE}",
    f"http://{TENANT_HOST}/?invite={CODE}",
])
def test_a_foreign_return_target_is_still_refused(shape):
    assert identity_transfer.tenant_target(shape) is None


def test_an_unshaped_query_parameter_is_not_carried():
    carried = identity_transfer.tenant_target(
        f"https://{TENANT_HOST}/?invite=short&next=https://evil.example")
    assert carried == f"https://{TENANT_HOST}/"


# --------------------------------------------------------------------------- #
# The control-plane page is no longer a dead end
# --------------------------------------------------------------------------- #


@pytest.fixture
def control_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", CONTROL)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", "k" * 40)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_MASTER", "m" * 40)
    monkeypatch.setenv("HOJA_TENANT_BASE_DOMAIN", "hoja.work")
    path = tmp_path / "control.db"
    engine = make_engine(path)
    migrate(engine)
    from starlette.testclient import TestClient

    app = create_app(str(path), open_mode=False)
    with TestClient(app, base_url=CONTROL) as client:
        yield client, engine


def test_pasting_our_invitation_link_lands_on_the_workspace(control_client):
    r = control_client[0].post(
        "/signin/invited",
        data={"link": f"https://{TENANT_HOST}/#invite={CODE}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"https://{TENANT_HOST}/?invite={CODE}"


def test_pasting_a_foreign_link_is_refused_with_a_page_not_a_redirect(control_client):
    r = control_client[0].post(
        "/signin/invited",
        data={"link": f"https://evil.example/#invite={CODE}"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "location" not in r.headers
    assert "invitation link" in r.text.lower()


def test_a_link_without_a_code_is_refused(control_client):
    r = control_client[0].post(
        "/signin/invited",
        data={"link": f"https://{TENANT_HOST}/"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_the_scheme_may_be_omitted_by_a_person_pasting_it(control_client):
    r = control_client[0].post(
        "/signin/invited",
        data={"link": f"{TENANT_HOST}/#invite={CODE}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"https://{TENANT_HOST}/?invite=")


def test_the_no_workspace_page_offers_the_invitation_path():
    from hoja_server import signin_pages

    page = signin_pages.no_workspace_page(
        email="kevin@hoja.work",
        sign_out_url="/auth/google/logout",
        invite_action="/signin/invited",
    )
    assert "Were you invited?" in page
    assert 'action="/signin/invited"' in page
    assert 'name="link"' in page


def test_the_no_workspace_page_still_works_without_the_invite_action():
    from hoja_server import signin_pages

    page = signin_pages.no_workspace_page(
        email="kevin@hoja.work", sign_out_url="/auth/google/logout")
    assert "Were you invited?" not in page
    assert "Ask a workspace admin" in page


# --------------------------------------------------------------------------- #
# Wiring defects found in review
# --------------------------------------------------------------------------- #


def _signed_in_control_session(engine):
    """A live control-plane session for an invited person."""
    from hoja_server.signup_store import SignupStore

    store = SignupStore(engine)
    ident = store.upsert_identity(
        subject="google-sub-kevin", email="kevin@hoja.work", email_verified=True, name="Kevin")
    raw, _ = store.create_session(
        identity_id=ident["id"], client_ip=None, user_agent=None, days=1)
    return raw, ident


def test_the_handoff_path_is_not_swallowed_by_an_invite_query(control_client):
    """Regression: the finish path was concatenated onto a target that had a query."""
    client, engine = control_client
    raw, _ident = _signed_in_control_session(engine)
    client.cookies.set("hoja_signup_session", raw)

    # With a live control session this is now one hop: the entry mints the
    # handoff itself, and that is where the path used to be concatenated onto a
    # target carrying `?invite=`.
    first = client.get(
        f"/signin?next=https%3A%2F%2F{TENANT_HOST}%2F%3Finvite%3D{CODE}",
        follow_redirects=False,
    )
    assert first.status_code == 302
    location = first.headers["location"]
    assert location.startswith(f"https://{TENANT_HOST}/auth/google/finish?t=")
    assert "invite=" not in location.split("?", 1)[0]


def test_accepting_without_a_session_is_a_401_not_a_500(control_client):
    client, _engine = control_client
    r = client.post("/auth/invite/accept", json={"code": CODE})
    assert r.status_code == 401
    assert r.json()["reason"] == "invite_refused"


def test_a_foreign_origin_cannot_use_the_invite_form(control_client):
    client, _engine = control_client
    r = client.post(
        "/signin/invited",
        data={"link": f"https://{TENANT_HOST}/#invite={CODE}"},
        headers={"origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert "location" not in r.headers


def test_an_oversized_body_is_refused_before_it_is_parsed(control_client):
    client, _engine = control_client
    r = client.post(
        "/signin/invited",
        content=b"link=" + b"a" * (9 * 1024),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert r.status_code == 413


def test_surrounding_whitespace_from_a_paste_is_tolerated(control_client):
    """Copying a link out of an email usually brings a newline with it."""
    client, _engine = control_client
    r = client.post(
        "/signin/invited",
        data={"link": f"  https://{TENANT_HOST}/#invite={CODE}\n"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_the_probe_route_tells_the_screen_whether_the_code_is_still_good(control_client):
    """An expired code is refused on arrival, not after a password is chosen."""
    client, _engine = control_client

    missing = client.post("/auth/invite/probe", json={"code": "z" * 43})
    assert missing.status_code == 404
    assert missing.json()["valid"] is False
