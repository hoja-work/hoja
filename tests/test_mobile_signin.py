"""Mobile sign-in (HOJA-733): QR handoff and Google from the app, through the real ASGI app.

What is asserted is what would be a security bug if wrong:

* a QR code is one phone's: a second claim burns it, and redeeming needs the
  PKCE verifier of the phone that claimed it;
* only the browser session that made a code can see or approve it, and an API
  key or OAuth token can never make one;
* the workspace's IP allowlist applies to the phone, and a member removed
  mid-flow gets nothing;
* a Google transfer works once, an app's transfer only with the app's
  verifier, and the native sheet's ID token only with the app's challenge as
  its nonce.

Contract: docs/mobile-signin.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select, update
from starlette.testclient import TestClient

from hoja_server import google_oidc, identity_transfer, permissions
from hoja_server import session_handoff as flow
from hoja_server.app import create_app
from hoja_server.db import (
    browser_session,
    make_engine,
    migrate,
    organization_membership,
    session_handoff,
    signup_flow,
    user,
    workspace_security_policy,
)
from hoja_server.signup_store import SignupStore

MASTER = "m" * 40
CONTROL = "https://app.hoja.work"
TENANT = "acme.hoja.work"
KEY = identity_transfer.tenant_key(MASTER, TENANT)
GOOGLE_CLIENT = "cid.apps.googleusercontent.com"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _token(**over) -> str:
    kwargs = dict(key=KEY, audience=TENANT, email="luis@hoja.work", subject="sub-1",
                  name="Luis", email_verified=True)
    kwargs.update(over)
    return identity_transfer.issue(**kwargs)


# =========================================================================== #
# Google: identity_transfer                                                    #
# =========================================================================== #

def test_the_challenge_is_rfc7636_s256():
    # RFC 7636 appendix B test vector.
    assert identity_transfer.challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


def test_a_bound_token_needs_its_verifier():
    verifier, challenge = _pkce()
    token = _token(client_challenge=challenge)
    with pytest.raises(identity_transfer.TransferError) as missing:
        identity_transfer.redeem(key=KEY, token=token, audience=TENANT)
    assert missing.value.reason == "verifier_missing"
    with pytest.raises(identity_transfer.TransferError) as wrong:
        identity_transfer.redeem(key=KEY, token=token, audience=TENANT, client_verifier=_pkce()[0])
    assert wrong.value.reason == "verifier_mismatch"
    claims = identity_transfer.redeem(key=KEY, token=token, audience=TENANT, client_verifier=verifier)
    assert claims["cc"] == challenge


def test_a_malformed_challenge_is_never_minted():
    with pytest.raises(identity_transfer.TransferError) as caught:
        _token(client_challenge="not-a-challenge")
    assert caught.value.reason == "challenge_invalid"


def test_every_token_is_distinct_so_it_can_be_spent_once():
    assert _token(now=1000) != _token(now=1000)


# =========================================================================== #
# Google: control plane                                                        #
# =========================================================================== #

@pytest.fixture
def control(tmp_path, monkeypatch):
    for name, value in {
        "HOJA_CONTROL_PLANE_URL": CONTROL,
        "HOJA_IDENTITY_TRANSFER_MASTER": MASTER,
        "HOJA_TENANT_BASE_DOMAIN": "hoja.work",
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT,
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": f"{CONTROL}/auth/google/callback",
        "TURNSTILE_SECRET_KEY": "ts",
        "TURNSTILE_EXPECTED_HOSTNAME": "app.hoja.work",
    }.items():
        monkeypatch.setenv(name, value)
    path = tmp_path / "control.db"
    engine = make_engine(path)
    migrate(engine)
    with TestClient(create_app(str(path), open_mode=False), base_url=CONTROL) as client:
        yield client, engine


def _control_signed_in(client, engine) -> None:
    store = SignupStore(engine)
    identity = store.upsert_identity(subject="sub-1", email="luis@hoja.work", email_verified=True)
    raw, _ = store.rotate_session(previous_raw=None, identity_id=identity["id"],
                                  client_ip=None, user_agent=None, days=1)
    client.cookies.set("hoja_signup_session", raw)


def test_an_app_handoff_returns_to_the_app_bound_to_its_challenge(control):
    client, engine = control
    _control_signed_in(client, engine)
    verifier, challenge = _pkce()
    r = client.get("/auth/google/start", params={"return_to": f"https://{TENANT}/",
                                                 "app_challenge": challenge},
                   follow_redirects=False)
    assert r.status_code == 302
    location = urlsplit(r.headers["location"])
    assert (location.scheme, location.netloc, location.path) == ("hoja", "auth", "/finish")
    query = parse_qs(location.query)
    assert query["host"] == [TENANT]
    claims = identity_transfer.redeem(key=KEY, token=query["t"][0], audience=TENANT,
                                      client_verifier=verifier)
    assert claims["cc"] == challenge


def test_a_browser_handoff_is_unchanged(control):
    client, engine = control
    _control_signed_in(client, engine)
    r = client.get("/auth/google/start", params={"return_to": f"https://{TENANT}/"},
                   follow_redirects=False)
    assert r.headers["location"].startswith(f"https://{TENANT}/auth/google/finish?t=")


def test_a_challenge_without_a_workspace_target_is_ignored(control):
    # Only a tenant handoff can go to the app; nothing else ever redirects to hoja://.
    client, engine = control
    _control_signed_in(client, engine)
    r = client.get("/auth/google/start", params={"return_to": "/", "app_challenge": _pkce()[1]},
                   follow_redirects=False)
    assert not r.headers["location"].startswith("hoja:")


def test_the_full_google_flow_keeps_the_challenge_server_side(control):
    client, engine = control
    _verifier, challenge = _pkce()
    r = client.get("/auth/google/start", params={"return_to": f"https://{TENANT}/",
                                                 "app_challenge": challenge},
                   follow_redirects=False)
    assert r.headers["location"].startswith("https://accounts.google.com/")
    assert challenge not in r.headers["location"]
    with engine.connect() as conn:
        assert conn.execute(select(signup_flow.c.client_challenge)).scalar_one() == challenge


# --- the native account sheet (Android Credential Manager) ---------------- #

_GOOGLE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> google_oidc.Jwks:
    numbers = _GOOGLE_KEY.public_key().public_numbers()

    def enc(value: int) -> str:
        return google_oidc.b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return google_oidc.Jwks.from_document({"keys": [
        {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "k1", "n": enc(numbers.n), "e": enc(numbers.e)}]})


def _id_token(**over) -> str:
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": GOOGLE_CLIENT, "sub": "sub-1",
              "email": "luis@hoja.work", "email_verified": True, "iat": now, "exp": now + 300,
              "nonce": "unset", "name": "Luis"}
    claims.update(over)
    segments = [google_oidc.b64url_encode(json.dumps(part).encode())
                for part in ({"alg": "RS256", "kid": "k1", "typ": "JWT"}, claims)]
    signature = _GOOGLE_KEY.sign(".".join(segments).encode(), padding.PKCS1v15(), hashes.SHA256())
    return ".".join([*segments, google_oidc.b64url_encode(signature)])


@pytest.fixture
def native(control, monkeypatch):
    monkeypatch.setattr(google_oidc, "get_jwks", lambda *a, **k: _jwks())
    return control


def test_the_native_sheet_buys_a_transfer_bound_to_the_app(native):
    client, _ = native
    verifier, challenge = _pkce()
    r = client.post("/auth/google/native",
                    json={"idToken": _id_token(nonce=challenge), "host": TENANT, "challenge": challenge})
    assert r.status_code == 200, r.text
    assert r.json()["finishUrl"] == f"https://{TENANT}/auth/google/finish"
    claims = identity_transfer.redeem(key=KEY, token=r.json()["transfer"], audience=TENANT,
                                      client_verifier=verifier)
    assert claims["email"] == "luis@hoja.work" and claims["cc"] == challenge


def test_an_id_token_from_another_flow_is_refused(native):
    # The nonce is what ties Google's answer to this app's challenge.
    client, _ = native
    _verifier, challenge = _pkce()
    r = client.post("/auth/google/native",
                    json={"idToken": _id_token(nonce=_pkce()[1]), "host": TENANT, "challenge": challenge})
    assert r.status_code == 400
    assert r.json()["reason"] == "nonce_mismatch"


@pytest.mark.parametrize("over, reason", [
    ({"aud": "someone-else.apps.googleusercontent.com"}, "audience_mismatch"),
    ({"email_verified": False}, "email_unverified"),
    ({"exp": int(time.time()) - 3600}, "token_expired"),
])
def test_a_bad_id_token_is_refused(native, over, reason):
    client, _ = native
    _verifier, challenge = _pkce()
    r = client.post("/auth/google/native",
                    json={"idToken": _id_token(nonce=challenge, **over), "host": TENANT, "challenge": challenge})
    assert r.status_code == 400
    assert r.json()["reason"] == reason


@pytest.mark.parametrize("host", ["evil.example", "acme.hoja.work.evil.example", ""])
def test_the_native_sheet_only_hands_over_to_our_workspaces(native, host):
    client, _ = native
    _verifier, challenge = _pkce()
    r = client.post("/auth/google/native",
                    json={"idToken": _id_token(nonce=challenge), "host": host, "challenge": challenge})
    assert r.status_code == 400


# =========================================================================== #
# Google: tenant finish                                                        #
# =========================================================================== #

@pytest.fixture
def tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", CONTROL)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", KEY)
    path = tmp_path / "tenant.db"
    engine = make_engine(path)
    migrate(engine)
    app = create_app(str(path), open_mode=False)
    with TestClient(app, base_url=f"https://{TENANT}") as client:
        yield client, engine, app


def test_the_app_finishes_with_its_verifier_and_gets_a_90_day_app_session(tenant):
    client, engine, _ = tenant
    verifier, challenge = _pkce()
    r = client.get("/auth/google/finish",
                   params={"t": _token(client_challenge=challenge), "v": verifier, "device": "Pixel 8"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "hoja_session" in r.headers.get("set-cookie", "")
    with engine.connect() as conn:
        row = conn.execute(select(browser_session)).mappings().one()
    assert row["kind"] == "app"
    assert row["label"] == "Pixel 8 · Hoja app · via Google"
    lifetime = (_iso(row["expires_at"]) - _iso(row["created_at"])).days
    assert lifetime == 90


def test_a_browser_finish_is_still_a_14_day_browser_session(tenant):
    client, engine, _ = tenant
    assert client.get("/auth/google/finish", params={"t": _token()}, follow_redirects=False).status_code == 302
    with engine.connect() as conn:
        row = conn.execute(select(browser_session)).mappings().one()
    assert row["kind"] is None and row["label"] is None
    assert (_iso(row["expires_at"]) - _iso(row["created_at"])).days == 14


def test_a_token_taken_from_the_callback_is_useless_without_the_verifier(tenant):
    client, engine, _ = tenant
    _verifier, challenge = _pkce()
    token = _token(client_challenge=challenge)
    for params in ({"t": token}, {"t": token, "v": _pkce()[0]}):
        r = client.get("/auth/google/finish", params=params, follow_redirects=False)
        assert r.status_code == 400
        assert "hoja_session" not in r.headers.get("set-cookie", "")
    with engine.connect() as conn:
        assert conn.execute(select(user.c.id)).first() is None


@pytest.mark.parametrize("bound", [False, True])
def test_a_transfer_is_spent_once(tenant, bound):
    client, _, _ = tenant
    verifier, challenge = _pkce()
    params = {"t": _token(client_challenge=challenge), "v": verifier} if bound else {"t": _token()}
    assert client.get("/auth/google/finish", params=params, follow_redirects=False).status_code == 302
    client.cookies.clear()
    again = client.get("/auth/google/finish", params=params, follow_redirects=False)
    assert again.status_code == 400
    assert b"replayed" in again.content
    assert "hoja_session" not in again.headers.get("set-cookie", "")


# =========================================================================== #
# QR handoff                                                                  #
# =========================================================================== #

def _iso(value: str):
    from datetime import datetime
    return datetime.fromisoformat(value)


@pytest.fixture
def qr(tenant):
    """A signed-in computer (the first member, an admin) and a phone with no cookies."""
    computer, engine, app = tenant
    assert computer.get("/auth/google/finish", params={"t": _token()}, follow_redirects=False).status_code == 302
    phone = TestClient(app, base_url=f"https://{TENANT}")
    return computer, phone, engine


def _create(computer, **body):
    r = computer.post("/auth/handoff", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _claim(phone, code: dict, challenge: str, **over):
    body = {"id": code["id"], "secret": code["secret"], "challenge": challenge,
            "deviceName": "Pixel 8", "platform": "android"}
    body.update(over)
    return phone.post("/auth/handoff/claim", json=body)


def _signed_in_phone(computer, phone):
    code = _create(computer)
    verifier, challenge = _pkce()
    assert _claim(phone, code, challenge).status_code == 200
    assert computer.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "approve"}).status_code == 200
    return code, verifier


def test_the_whole_handoff_signs_the_phone_in_as_the_same_person(qr):
    computer, phone, engine = qr
    code = _create(computer)
    assert code["qrUrl"] == f"https://{TENANT}/m/pair#{code['id']}.{code['secret']}"
    assert len(code["code"]) == 9 and code["code"][4] == "-"
    verifier, challenge = _pkce()

    claimed = _claim(phone, code, challenge)
    assert claimed.status_code == 200, claimed.text
    body = claimed.json()
    assert body["account"]["email"] == "luis@hoja.work"
    assert body["workspace"]["host"] == TENANT

    seen = computer.get(f"/auth/handoff/{code['id']}").json()
    assert seen["status"] == "claimed"
    assert seen["device"]["name"] == "Pixel 8" and seen["device"]["platform"] == "android"

    waiting = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert waiting.status_code == 202 and waiting.json()["status"] == "claimed"

    assert computer.post(f"/auth/handoff/{code['id']}/decision",
                         json={"decision": "approve"}).json()["status"] == "approved"
    done = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert done.status_code == 200, done.text
    assert "hoja_session" in done.headers.get("set-cookie", "")

    me = phone.get("/auth/me")
    assert me.status_code == 200
    with engine.connect() as conn:
        rows = conn.execute(select(browser_session).order_by(browser_session.c.created_at)).mappings().all()
    assert len(rows) == 2
    assert rows[1]["user_id"] == rows[0]["user_id"]
    assert rows[1]["kind"] == "app" and rows[1]["label"] == "Pixel 8 · Hoja app · via QR"
    assert computer.get(f"/auth/handoff/{code['id']}").json()["status"] == "consumed"


def test_the_typed_code_works_like_the_qr(qr):
    computer, phone, _ = qr
    code = _create(computer)
    _verifier, challenge = _pkce()
    r = phone.post("/auth/handoff/claim", json={"code": code["code"].lower().replace("-", " "),
                                                "challenge": challenge})
    assert r.status_code == 200, r.text


def test_a_second_phone_burns_the_code_and_nobody_gets_in(qr):
    computer, phone, _ = qr
    code = _create(computer)
    verifier, challenge = _pkce()
    assert _claim(phone, code, challenge).status_code == 200
    thief = _claim(phone, code, _pkce()[1], deviceName="Attacker")
    assert thief.status_code == 409 and thief.json()["reason"] == "already_claimed"
    assert computer.get(f"/auth/handoff/{code['id']}").json()["status"] == "burned"
    refused = computer.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "approve"})
    assert refused.status_code == 409
    r = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert r.status_code == 410 and "hoja_session" not in r.headers.get("set-cookie", "")


def test_the_same_phone_retrying_its_claim_is_not_a_thief(qr):
    computer, phone, _ = qr
    code = _create(computer)
    _verifier, challenge = _pkce()
    assert _claim(phone, code, challenge).status_code == 200
    assert _claim(phone, code, challenge).status_code == 200
    assert computer.get(f"/auth/handoff/{code['id']}").json()["status"] == "claimed"


def test_a_wrong_secret_is_an_unknown_code(qr):
    computer, phone, _ = qr
    code = _create(computer)
    r = _claim(phone, {**code, "secret": "x" * 43}, _pkce()[1])
    assert r.status_code == 404
    assert computer.get(f"/auth/handoff/{code['id']}").json()["status"] == "pending"


def test_redeem_needs_the_claiming_phones_verifier(qr):
    computer, phone, _ = qr
    code, verifier = _signed_in_phone(computer, phone)
    for wrong in (None, "short", _pkce()[0], "é" * 50):
        r = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": wrong})
        assert r.status_code == 400 and r.json()["reason"] == "verifier_mismatch"
    assert phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier}).status_code == 200


def test_a_code_is_redeemed_once(qr):
    computer, phone, _ = qr
    code, verifier = _signed_in_phone(computer, phone)
    assert phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier}).status_code == 200
    phone.cookies.clear()
    again = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert again.status_code == 410 and "hoja_session" not in again.headers.get("set-cookie", "")


def test_a_form_post_from_the_web_view_lands_on_the_app(qr):
    computer, phone, _ = qr
    code, verifier = _signed_in_phone(computer, phone)
    r = phone.post("/auth/handoff/redeem", data={"id": code["id"], "verifier": verifier}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert "hoja_session" in r.headers.get("set-cookie", "")


def test_a_denied_code_tells_the_phone(qr):
    computer, phone, _ = qr
    code = _create(computer)
    verifier, challenge = _pkce()
    _claim(phone, code, challenge)
    assert computer.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "deny"}).status_code == 200
    r = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert r.status_code == 403 and r.json()["reason"] == "denied"


def test_approval_needs_a_claim_first(qr):
    computer, _, _ = qr
    code = _create(computer)
    r = computer.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "approve"})
    assert r.status_code == 409


@pytest.mark.parametrize("stage", ["pending", "claimed", "approved"])
def test_every_stage_expires(qr, stage):
    computer, phone, engine = qr
    code = _create(computer)
    verifier, challenge = _pkce()
    if stage != "pending":
        _claim(phone, code, challenge)
    if stage == "approved":
        computer.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "approve"})
    with engine.begin() as conn:
        conn.execute(update(session_handoff).where(session_handoff.c.id == code["id"])
                     .values(expires_at=time.time() - 1))
    assert computer.get(f"/auth/handoff/{code['id']}").json()["status"] == "expired"
    if stage == "pending":
        assert _claim(phone, code, challenge).status_code == 410
    else:
        r = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
        assert r.status_code == 410 and "hoja_session" not in r.headers.get("set-cookie", "")


def test_rotation_retires_the_code_on_screen(qr):
    computer, phone, _ = qr
    first = _create(computer)
    second = _create(computer, replaces=first["id"])
    assert computer.get(f"/auth/handoff/{first['id']}").json()["status"] == "cancelled"
    assert _claim(phone, first, _pkce()[1]).status_code == 410
    assert _claim(phone, second, _pkce()[1]).status_code == 200


def test_another_session_cannot_see_or_approve_a_code(qr):
    computer, phone, _ = qr
    code = _create(computer)
    _claim(phone, code, _pkce()[1])
    other = TestClient(computer.app, base_url=f"https://{TENANT}")
    assert other.get("/auth/google/finish", params={"t": _token()}, follow_redirects=False).status_code == 302
    assert other.get(f"/auth/handoff/{code['id']}").status_code == 404
    assert other.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "approve"}).status_code == 404
    assert computer.get(f"/auth/handoff/{code['id']}").json()["status"] == "claimed"


def test_an_api_token_can_never_mint_a_phone_session(qr):
    computer, phone, _ = qr
    r = phone.post("/auth/handoff", json={}, headers={"Authorization": "Bearer hoja_something"})
    assert r.status_code == 403 and r.json()["reason"] == "browser_session_required"
    r = computer.post("/auth/handoff", json={}, headers={"Authorization": "Bearer hoja_something"})
    assert r.status_code == 403
    assert phone.post("/auth/handoff", json={}).status_code == 403


def test_a_cross_site_page_cannot_make_a_code(qr):
    computer, _, _ = qr
    r = computer.post("/auth/handoff", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_making_codes_is_rate_limited(qr):
    computer, _, _ = qr
    for _ in range(flow.CREATE_LIMIT):
        _create(computer)
    r = computer.post("/auth/handoff", json={})
    assert r.status_code == 429 and r.json()["reason"] == "rate_limited"


def test_guessing_codes_is_rate_limited(qr):
    _, phone, _ = qr
    statuses = [phone.post("/auth/handoff/claim", json={"code": "BCDFGHJK", "challenge": _pkce()[1]}).status_code
                for _ in range(21)]
    assert statuses[:20] == [404] * 20 and statuses[20] == 429


def test_a_member_removed_mid_flow_gets_nothing(qr):
    computer, phone, engine = qr
    code, verifier = _signed_in_phone(computer, phone)
    with engine.begin() as conn:
        conn.execute(update(organization_membership).values(active=False))
    r = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert r.status_code == 403 and r.json()["reason"] == "membership_gone"
    assert "hoja_session" not in r.headers.get("set-cookie", "")


def test_the_ip_allowlist_is_checked_against_the_phone_not_the_computer(tenant):
    _computer, engine, app = tenant
    computer = TestClient(app, base_url=f"https://{TENANT}", client=("198.51.100.7", 1))
    phone = TestClient(app, base_url=f"https://{TENANT}", client=("203.0.113.9", 1))
    assert computer.get("/auth/google/finish", params={"t": _token()}, follow_redirects=False).status_code == 302
    with engine.begin() as conn:
        org = conn.execute(select(user.c.organization_id)).scalar_one()
        # Admins are exempt from the allowlist (they must be able to fix it), so
        # make the person an ordinary member on an office-only workspace.
        conn.execute(update(user).values(admin=False))
        conn.execute(workspace_security_policy.insert().values(
            organization_id=org, ip_allowlist=json.dumps(["198.51.100.0/24"])))
    for invalidate in permissions.POLICY_INVALIDATORS:
        invalidate(org)
    code = _create(computer)
    verifier, challenge = _pkce()
    assert _claim(phone, code, challenge).status_code == 200
    refused = computer.post(f"/auth/handoff/{code['id']}/decision", json={"decision": "approve"})
    assert refused.status_code == 403 and refused.json()["reason"] == "ip_not_allowed"
    r = phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    assert r.status_code == 403 and "hoja_session" not in r.headers.get("set-cookie", "")


def test_an_app_session_slides_and_a_browser_session_does_not(qr):
    computer, phone, engine = qr
    code, verifier = _signed_in_phone(computer, phone)
    phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    # Pretend both sessions were made 30 days ago.
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=30)
    with engine.begin() as conn:
        for row in conn.execute(select(browser_session)).mappings().all():
            days = 90 if row["kind"] == "app" else 14
            conn.execute(update(browser_session).where(browser_session.c.id == row["id"]).values(
                created_at=old.isoformat(), expires_at=(old + timedelta(days=days)).isoformat()))
    assert phone.get("/auth/me").status_code == 200
    with engine.connect() as conn:
        rows = {r["kind"]: r for r in conn.execute(select(browser_session)).mappings().all()}
    remaining = _iso(rows["app"]["expires_at"]) - datetime.now(timezone.utc)
    assert remaining > timedelta(days=89)
    # The browser session is untouched (and in fact long expired at 14 days).
    assert _iso(rows[None]["expires_at"]) < datetime.now(timezone.utc)


def test_connected_apps_lists_the_phone_with_its_label(qr):
    computer, phone, _ = qr
    code, verifier = _signed_in_phone(computer, phone)
    phone.post("/auth/handoff/redeem", json={"id": code["id"], "verifier": verifier})
    r = computer.post("/graphql", json={"query": "{mySessions{label kind current}}"})
    sessions = r.json()["data"]["mySessions"]
    assert {"label": "Pixel 8 · Hoja app · via QR", "kind": "app", "current": False} in sessions
    assert {"label": None, "kind": "browser", "current": True} in sessions


def test_the_pair_page_never_needs_the_secret(qr):
    _, phone, _ = qr
    r = phone.get("/m/pair")
    assert r.status_code == 200
    assert "hoja://pair" in r.text and "location.hash" in r.text


def test_asset_links_name_the_app_only_when_configured(tenant, monkeypatch):
    client, _, _ = tenant
    assert client.get("/.well-known/assetlinks.json").status_code == 404
    monkeypatch.setenv("HOJA_ANDROID_CERT_SHA256", "aa:bb, CC:DD")
    body = client.get("/.well-known/assetlinks.json").json()
    assert body[0]["target"]["package_name"] == "work.hoja.mobile"
    assert body[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB", "CC:DD"]
