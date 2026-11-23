"""One sign-in hop into a workspace, one hop out of Hoja (D1-D5 → R1-R4).

Five reports, one shape: the browser is asked for something it already answered.

* a tenant's Login card links to ``{control}/signin?next=<tenant>``, and that
  URL rendered a page whose only control was "Continue with Google" — a screen
  for a workspace the person had already chosen (D1);
* the same entry then forced ``choose_account=1``, so a live control session
  re-ran Google instead of minting the handoff it already had the identity for
  (D2);
* "sign out" reset the SPA in place on a tenant host, which cannot delete
  ``app.hoja.work``'s host-only ``hoja_signup_session``; the next sign-in
  silently resumed (D3);
* ``/signin/signout`` revoked only the signup cookie and 302'd back into
  ``/signin?next=<tenant>``, which re-handed-off the live workspace session — the
  "workspace screens then it moves me" bounce (D4);
* the chooser never said whose session it was or offered a way out (D5).

The harness is the real ASGI app, the real schema and the real cookies, on TWO
bases: ``app.hoja.work`` and a tenant, sharing one SQLite file, which is the
production shape (ADR-003: one app process and one database for every tenant).
That sharing is the only reason a tenant's logout can end the control plane's
session at all, so it is asserted rather than assumed.
"""

from __future__ import annotations

from urllib.parse import parse_qs, quote, urlsplit

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import identity_transfer
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, signup_flow, signup_session, token_hash
from hoja_server.signup_store import SignupStore

CONTROL = "https://app.hoja.work"
TENANT = "acme.hoja.work"
TENANT_ORIGIN = f"https://{TENANT}/"
MASTER = "m" * 48
EMAIL = "owner@example.test"


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    """The control plane and one tenant, same process, same database."""
    for name, value in {
        "HOJA_CONTROL_PLANE_URL": CONTROL,
        "HOJA_IDENTITY_TRANSFER_MASTER": MASTER,
        # A tenant redeems the key the control plane derived from the master, so
        # the handoff in these tests is a real token, not just a redirect shape.
        "HOJA_IDENTITY_TRANSFER_KEY": identity_transfer.tenant_key(MASTER, TENANT),
        "HOJA_TENANT_BASE_DOMAIN": "hoja.work",
        # A fully configured deployment: ``configured`` needs all five, and the
        # Google start is what R1 short-circuits to.
        "GOOGLE_CLIENT_ID": "hoja-test-client.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": f"{CONTROL}/auth/google/callback",
        "TURNSTILE_SECRET_KEY": "turnstile-secret",
        "TURNSTILE_EXPECTED_HOSTNAME": "app.hoja.work",
        "HOJA_SIGNUP_MODE": "open",
    }.items():
        monkeypatch.setenv(name, value)

    db_path = tmp_path / "shared.db"
    engine = make_engine(db_path)
    migrate(engine)
    control = TestClient(create_app(str(db_path), open_mode=False), base_url=CONTROL)
    tenant = TestClient(create_app(str(db_path), open_mode=False), base_url=f"https://{TENANT}")
    with control, tenant:
        yield control, tenant, engine


def _control_session(engine, *, subject="google-sub-owner", email=EMAIL):
    """A live control-plane sign-up session, as Google's callback leaves one."""
    store = SignupStore(engine)
    identity = store.upsert_identity(
        subject=subject, email=email, email_verified=True, name="Owner")
    raw, _ = store.create_session(
        identity_id=identity["id"], client_ip=None, user_agent=None, days=1)
    return raw, identity


def _path_and_query(absolute: str) -> str:
    """TestClient is bound to one base_url; keep the path, drop the origin."""
    parts = urlsplit(absolute)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _workspace_handoff(control, engine):
    """Sign the control session in as ``EMAIL`` and land the tenant's cookie."""
    raw, _identity = _control_session(engine)
    control.cookies.set("hoja_signup_session", raw)
    entry = control.get(
        "/signin", params={"next": TENANT_ORIGIN}, follow_redirects=False)
    assert entry.status_code == 302, entry.text
    assert entry.headers["location"].startswith(f"https://{TENANT}/auth/google/finish?t=")
    return entry.headers["location"]


def _live_signup_sessions(engine):
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(
            select(signup_session).where(signup_session.c.revoked_at.is_(None)))]


# --------------------------------------------------------------------------- #
# R1 / D1 — a chosen workspace is not a screen
# --------------------------------------------------------------------------- #


def test_no_identity_and_a_workspace_goes_straight_to_google(hosts):
    control, _tenant, engine = hosts

    entry = control.get(
        "/signin", params={"next": TENANT_ORIGIN}, follow_redirects=False)

    assert entry.status_code == 302
    assert "text/html" not in entry.headers.get("content-type", "")
    assert entry.headers["location"].startswith("/auth/google/start?return_to=")

    started = control.get(
        _path_and_query(entry.headers["location"]), follow_redirects=False)
    assert started.status_code == 302
    location = started.headers["location"]
    assert location.startswith("https://accounts.google.com/"), location
    query = parse_qs(urlsplit(location).query)
    assert query["prompt"] == ["select_account"]
    assert query["redirect_uri"] == [f"{CONTROL}/auth/google/callback"]
    # The workspace the person came from survives the round-trip: the start was
    # given the same return_to the page's button carried, not a bare /signin.
    assert query["state"] and query["code_challenge_method"] == ["S256"]
    assert _return_to_from_flow(engine, query["state"][0]) == (
        f"/signin?next={quote(TENANT_ORIGIN, safe='')}")


def test_a_bare_sign_in_entry_without_an_identity_keeps_its_page(hosts):
    control, _tenant, _engine = hosts

    page = control.get("/signin", follow_redirects=False)

    assert page.status_code == 200
    assert "Sign in to Hoja" in page.text


def _return_to_from_flow(engine, state: str) -> str:
    """What the start handler stored as the place to come back to."""
    with engine.connect() as conn:
        return conn.execute(
            select(signup_flow.c.return_to)
            .where(signup_flow.c.state_hash == token_hash(state))
        ).scalar_one()


# --------------------------------------------------------------------------- #
# R2 / D2 — a live identity mints the handoff instead of re-running Google
# --------------------------------------------------------------------------- #


def test_a_live_identity_mints_the_handoff_without_google(hosts):
    control, tenant, engine = hosts

    location = _workspace_handoff(control, engine)

    assert "accounts.google.com" not in location
    finished = tenant.get(_path_and_query(location), follow_redirects=False)
    assert finished.status_code == 302, finished.text
    assert "hoja_session" in finished.headers.get("set-cookie", "")
    assert tenant.get("/auth/me").status_code == 200


def test_a_handoff_minted_for_another_workspace_still_cannot_open_this_one(hosts):
    """R5: the token crypto and its single audience are untouched."""
    control, tenant, engine = hosts

    location = _workspace_handoff(control, engine)
    other = TestClient(tenant.app, base_url="https://other.hoja.work")
    with other:
        refused = other.get(_path_and_query(location), follow_redirects=False)
    assert refused.status_code == 400
    assert b"wrong_audience" in refused.content


# --------------------------------------------------------------------------- #
# R3 / D3 / D4 — sign out ends Hoja, and says so
# --------------------------------------------------------------------------- #


def test_after_a_sign_out_the_next_sign_in_asks_for_the_account(hosts):
    control, _tenant, engine = hosts
    raw, _identity = _control_session(engine)
    control.cookies.set("hoja_signup_session", raw)

    signed_out = control.get(
        "/signout", params={"from": TENANT_ORIGIN}, follow_redirects=False)

    assert signed_out.status_code == 200
    assert "You're signed out" in signed_out.text
    assert control.cookies.get("hoja_choose_account") == "1"
    assert _live_signup_sessions(engine) == []

    entry = control.get(
        "/signin", params={"next": TENANT_ORIGIN}, follow_redirects=False)
    assert entry.status_code == 302
    assert "/auth/google/start" in entry.headers["location"]

    started = control.get(
        _path_and_query(entry.headers["location"]), follow_redirects=False)
    assert started.status_code == 302
    location = started.headers["location"]
    assert "acme.hoja.work" not in location, (
        "the control session resumed the identity that was just signed out: "
        f"{location}"
    )
    assert parse_qs(urlsplit(location).query)["prompt"] == ["select_account"]


def test_a_tenant_sign_out_ends_the_control_plane_session_too(hosts):
    control, tenant, engine = hosts
    location = _workspace_handoff(control, engine)
    assert tenant.get(_path_and_query(location), follow_redirects=False).status_code == 302
    assert tenant.get("/auth/me").status_code == 200
    assert control.get("/api/signup").json()["googleVerified"] is True

    # The tenant's own logout: this host can only delete its own cookie, so the
    # control-plane rows have to be ended server-side or the next sign-in
    # resumes the same person (D3).
    logout = tenant.post(
        "/auth/logout", headers={"origin": f"https://{TENANT}"})
    assert logout.status_code == 200, logout.text
    body = logout.json()
    assert "workspace" in body["cleared"]
    assert body["revoked"]["signup"] >= 1, body
    assert body["signOutUrl"] == (
        f"{CONTROL}/signout?from={quote(TENANT_ORIGIN, safe='')}")

    # The SPA's next step is this top-level navigation.
    page = control.get(
        "/signout", params={"from": TENANT_ORIGIN}, follow_redirects=False)
    assert page.status_code == 200
    assert "You're signed out" in page.text

    assert tenant.get("/auth/me").status_code == 401
    assert control.get("/auth/me").status_code == 401
    assert control.get("/api/signup").json()["googleVerified"] is False
    assert _live_signup_sessions(engine) == []


def test_the_control_plane_sign_out_ends_its_own_workspace_session(hosts):
    """D4: the old /signin/signout never touched hoja_session."""
    control, _tenant, engine = hosts
    raw, _identity = _control_session(engine)
    control.cookies.set("hoja_signup_session", raw)
    control.cookies.set("hoja_session", "a-workspace-session")

    response = control.get("/signin/signout", follow_redirects=False)

    assert response.status_code == 200
    assert "You're signed out" in response.text
    deleted = " ".join(response.headers.get_list("set-cookie")).lower()
    assert "hoja_session=" in deleted
    assert "hoja_signup_session=" in deleted
    assert control.cookies.get("hoja_choose_account") == "1"


def test_the_signed_out_page_is_inert(hosts):
    control, _tenant, _engine = hosts

    page = control.get(
        "/signout", params={"from": TENANT_ORIGIN}, follow_redirects=False)
    body = page.text

    assert "You're signed out" in body
    # No chooser, no workspace list, no meta refresh, no script: the only thing
    # that can move the browser is the one button.
    assert "Choose a workspace" not in body
    assert "http-equiv" not in body
    assert "refresh" not in body.lower()
    assert "<script" not in body.lower()
    assert body.count("<a ") == 1
    assert f'href="/signin?next={quote(TENANT_ORIGIN, safe="")}"' in body
    assert "location" not in page.headers


def test_an_open_redirect_in_from_is_refused(hosts):
    control, _tenant, _engine = hosts

    page = control.get(
        "/signout", params={"from": "https://evil.example/steal"}, follow_redirects=False)

    assert page.status_code == 200
    assert "evil.example" not in page.text
    assert 'href="/signin"' in page.text
    assert "location" not in page.headers


def test_an_open_redirect_in_next_is_refused(hosts):
    control, _tenant, _engine = hosts

    page = control.get(
        "/signin", params={"next": "https://evil.example/"}, follow_redirects=False)
    assert page.status_code == 200
    assert "evil.example" not in page.text

    signed_out = control.get(
        "/signin/signout", params={"next": "https://evil.example/"},
        follow_redirects=False)
    assert signed_out.status_code == 200
    assert "evil.example" not in signed_out.text
    assert "location" not in signed_out.headers


# --------------------------------------------------------------------------- #
# R4 / D5 — the chooser names the account and offers a way out
# --------------------------------------------------------------------------- #


def test_the_chooser_names_the_account_and_offers_a_sign_out(hosts):
    control, _tenant, engine = hosts
    raw, _identity = _control_session(engine)
    control.cookies.set("hoja_signup_session", raw)
    store = SignupStore(engine)
    store.record_membership_hint(slug="acme", email=EMAIL)
    store.record_membership_hint(slug="beta", email=EMAIL)

    page = control.get("/signin", follow_redirects=False)

    assert page.status_code == 200
    assert "Choose a workspace" in page.text
    assert EMAIL in page.text
    assert 'href="/signin/signout"' in page.text
    # Both workspaces are offered, and neither link asks Google again.
    assert "acme" in page.text and "beta" in page.text
    assert "choose_account=1" not in page.text
