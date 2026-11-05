"""Google-only sign-in and the server-side sign-up gate.

These tests drive the real ASGI app, the real SQLite schema, the real
``verify_id_token`` and the real ``verify_turnstile``. Only the two network
seams are replaced: ``turnstile._http_post_form`` (siteverify) and
``google_oidc._http_post_form`` / ``google_oidc._http_get_json`` (token endpoint
and JWKS). An autouse guard makes any accidental outbound HTTP call fail loudly,
so "never call the network" is an assertion, not a promise.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")
crypto = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import func, select, update
from starlette.testclient import TestClient

from hoja_server import google_oidc, signup_gate, turnstile
from hoja_server.app import create_app
from hoja_server.db import (
    make_engine,
    migrate,
    organization,
    signup_eligibility,
    signup_flow,
    signup_identity,
    signup_session,
    token_hash,
    turnstile_use,
    user,
)
from hoja_server.signup_config import REQUIRED_ENV, SignupConfigError, load_signup_config
from hoja_server.signup_gate import SignupReason
from hoja_server.signup_policy import RESERVED_SLUGS, SLUG_RE, SlugRefused, validate_slug
from hoja_server.signup_store import SignupStore

CLIENT_ID = "hoja-test-client.apps.googleusercontent.com"
EXPECTED_HOST = "localhost"
KID = "test-key-1"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()


def _public_jwk() -> dict:
    def encode(value: int) -> str:
        return google_oidc.b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": KID,
        "n": encode(_PUBLIC_NUMBERS.n),
        "e": encode(_PUBLIC_NUMBERS.e),
    }


JWKS_DOCUMENT = {"keys": [_public_jwk()]}
JWKS = google_oidc.Jwks.from_document(JWKS_DOCUMENT)


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "person@example.test",
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
        "nonce": "nonce-1",
        "name": "A Person",
        "picture": "https://example.test/p.png",
    }
    claims.update(overrides)
    return claims


def _sign(claims: dict, *, key=None, alg: str = "RS256", kid: str | None = KID,
          secret: bytes = b"shared-secret") -> str:
    """Build a compact JWS. ``alg`` may be RS256, HS256 or none, on purpose."""
    header = {"alg": alg, "typ": "JWT"}
    if kid:
        header["kid"] = kid
    segments = [
        google_oidc.b64url_encode(json.dumps(header).encode()),
        google_oidc.b64url_encode(json.dumps(claims).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    if alg == "RS256":
        signature = (key or _PRIVATE_KEY).sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    elif alg == "HS256":
        signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    else:
        signature = b""
    return ".".join(segments) + "." + (google_oidc.b64url_encode(signature) if signature else "")


def _assert_refused(token: str, reason: str, *, nonce: str = "nonce-1") -> None:
    with pytest.raises(google_oidc.OIDCError) as caught:
        google_oidc.verify_id_token(
            token, jwks=JWKS, client_id=CLIENT_ID, nonce=nonce)
    assert caught.value.reason == reason


# --------------------------------------------------------------------------- #
# fixtures / harness
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    """No test in this module may open an HTTP connection."""
    import httpx

    def refuse(*_args, **_kwargs):
        raise AssertionError("this test tried to reach the network")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    google_oidc.clear_jwks_cache()


def _clear_signup_env(monkeypatch) -> None:
    for name in (
        "HOJA_SIGNUP_MODE",
        "GOOGLE_REDIRECT_URI_ALLOWLIST",
        "HOJA_GOOGLE_HD",
        "HOJA_SIGNUP_IDENTITY_CAP",
        "HOJA_SIGNUP_IP_CAP",
        "HOJA_SIGNUP_IP_WINDOW_SECONDS",
        "HOJA_TENANT_REGISTRY",
        *REQUIRED_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


class FakeGoogle:
    """The token endpoint and JWKS, minus the socket."""

    def __init__(self) -> None:
        self.claim_overrides: dict = {}
        self.token_calls: list[dict] = []
        self.jwks_calls = 0
        self.jwks_queue: list[dict] = []

    def post(self, url, data, *, timeout=10.0):
        self.token_calls.append({"url": url, "data": dict(data)})
        return {
            "access_token": "access-token",
            "id_token": _sign(_claims(**self.claim_overrides)),
            "expires_in": 3600,
        }

    def get_json(self, url, *, timeout=5.0):
        self.jwks_calls += 1
        if self.jwks_queue:
            return self.jwks_queue.pop(0)
        return JWKS_DOCUMENT


class FakeSiteverify:
    """Cloudflare's siteverify, canned."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, data, *, timeout):
        self.calls.append({"url": url, "data": dict(data)})
        token = data.get("response")
        if token == "fail-token":
            return {"success": False, "error-codes": ["invalid-input-response"]}
        if token == "wrong-host-token":
            return {"success": True, "hostname": "evil.example"}
        return {"success": True, "hostname": EXPECTED_HOST, "action": "signup"}


class Harness:
    def __init__(self, client: TestClient, engine, google: FakeGoogle,
                 siteverify: FakeSiteverify) -> None:
        self.client = client
        self.engine = engine
        self.google = google
        self.siteverify = siteverify
        self.store = SignupStore(engine)
        self.host = EXPECTED_HOST

    # -- sign in -----------------------------------------------------------
    def start_flow(self, *, host: str | None = None, return_to: str | None = None):
        params = {"return_to": return_to} if return_to else None
        return self.client.get(
            "/auth/google/start",
            params=params,
            headers={"host": host or self.host},
            follow_redirects=False,
        )

    def sign_in(self, *, subject: str = "google-sub-1", email: str = "person@example.test",
                host: str | None = None, **claim_overrides):
        """Run the real start → callback flow with a token the fake signs."""
        self.google.claim_overrides = {
            "sub": subject, "email": email, **claim_overrides}
        start = self.start_flow(host=host)
        assert start.status_code == 302, start.text
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        with self.engine.connect() as conn:
            nonce = conn.execute(
                select(signup_flow.c.nonce).where(signup_flow.c.state_hash == token_hash(state))
            ).scalar_one()
        self.google.claim_overrides.setdefault("nonce", nonce)
        response = self.client.get(
            "/auth/google/callback",
            params={"code": "auth-code", "state": state},
            headers={"host": host or self.host},
            follow_redirects=False,
        )
        return response, state

    # -- gate --------------------------------------------------------------
    def signup(self, slug, token="good-token"):
        return self.client.post(
            "/api/signup", json={"slug": slug, "turnstileToken": token})

    def eligibility(self):
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(select(signup_eligibility))]

    def turnstile_uses(self):
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(select(turnstile_use))]

    def count(self, table) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(table)).scalar_one() or 0)


def _build_harness(tmp_path, monkeypatch, *, mode="open", env=None, base_url="http://localhost"):
    _clear_signup_env(monkeypatch)
    defaults = {
        "GOOGLE_CLIENT_ID": CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": f"{base_url}/auth/google/callback",
        "TURNSTILE_SECRET_KEY": "turnstile-secret",
        "TURNSTILE_EXPECTED_HOSTNAME": EXPECTED_HOST,
        "HOJA_SIGNUP_IP_CAP": "50",
    }
    for name, value in {**defaults, **(env or {})}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOJA_SIGNUP_MODE", mode)

    google = FakeGoogle()
    siteverify = FakeSiteverify()
    monkeypatch.setattr(google_oidc, "_http_post_form", google.post)
    monkeypatch.setattr(google_oidc, "_http_get_json", google.get_json)
    monkeypatch.setattr(turnstile, "_http_post_form", siteverify, raising=True)

    db_path = tmp_path / "signup.db"
    engine = make_engine(db_path)
    migrate(engine)
    app = create_app(str(db_path), open_mode=False)
    client = TestClient(app, base_url=base_url)
    client.__enter__()
    return Harness(client, engine, google, siteverify)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    built = _build_harness(tmp_path, monkeypatch)
    yield built
    built.client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# 1. ID token validation — every forged shape is refused
# --------------------------------------------------------------------------- #


def test_a_well_formed_token_is_accepted_so_the_refusals_mean_something():
    claims = google_oidc.verify_id_token(
        _sign(_claims()), jwks=JWKS, client_id=CLIENT_ID, nonce="nonce-1")
    assert claims["sub"] == "google-sub-1"
    assert claims["email_verified"] is True


def test_id_token_signed_by_another_key_is_rejected():
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _assert_refused(_sign(_claims(), key=other_key), "bad_signature")


def test_id_token_for_another_client_is_rejected():
    _assert_refused(_sign(_claims(aud="someone-else")), "audience_mismatch")


def test_expired_id_token_is_rejected():
    now = int(time.time())
    _assert_refused(_sign(_claims(exp=now - 3600)), "token_expired")


def test_id_token_issued_in_the_future_is_rejected():
    now = int(time.time())
    _assert_refused(_sign(_claims(iat=now + 7200)), "token_not_yet_valid")


def test_alg_none_is_rejected_before_any_key_lookup():
    _assert_refused(_sign(_claims(), alg="none"), "alg_none")
    _assert_refused(_sign(_claims(), alg="none", kid=None), "alg_none")


def test_hs256_symmetric_confusion_is_rejected():
    _assert_refused(_sign(_claims(), alg="HS256"), "alg_not_allowed")
    # Even when the HMAC key is the public JWK's modulus — the classic attack.
    modulus = JWKS_DOCUMENT["keys"][0]["n"].encode()
    forged = _sign(_claims(), alg="HS256", secret=modulus)
    _assert_refused(forged, "alg_not_allowed")


def test_wrong_issuer_is_rejected():
    _assert_refused(_sign(_claims(iss="https://evil.example")), "issuer_mismatch")


def test_id_token_with_another_nonce_is_rejected():
    _assert_refused(_sign(_claims(nonce="a-different-flow")), "nonce_mismatch")
    _assert_refused(_sign(_claims()), "nonce_mismatch", nonce="expected-other")


def test_email_verified_false_is_rejected():
    _assert_refused(_sign(_claims(email_verified=False)), "email_unverified")
    # The string "true" is not the boolean Google sends; refuse it too.
    _assert_refused(_sign(_claims(email_verified="true")), "email_unverified")
    _assert_refused(_sign(_claims(email_verified=None)), "email_unverified")


def test_missing_subject_is_rejected():
    claims = _claims()
    claims.pop("sub")
    _assert_refused(_sign(claims), "claim_missing")
    _assert_refused(_sign(_claims(sub="  ")), "subject_missing")


def test_unknown_key_id_is_rejected():
    _assert_refused(_sign(_claims(), kid="not-in-the-jwks"), "unknown_key")


def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = google_oidc.pkce_pair()
    assert len(verifier) >= 43
    expected = google_oidc.b64url_encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected


def test_authorization_url_is_an_exact_code_flow_with_pkce():
    url = google_oidc.authorization_url(
        client_id=CLIENT_ID,
        redirect_uri="https://signup.example/auth/google/callback",
        state="state-1",
        nonce="nonce-1",
        code_challenge="challenge-1",
    )
    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == ["https://signup.example/auth/google/callback"]
    assert query["state"] == ["state-1"]
    assert query["nonce"] == ["nonce-1"]
    assert "access_type" not in query  # no offline access, so no refresh token


# --------------------------------------------------------------------------- #
# 2. Turnstile — server-side, hostname-bound, fail-closed
# --------------------------------------------------------------------------- #


def _verify_token(token, *, hostname=EXPECTED_HOST, ip="203.0.113.9"):
    return turnstile.verify_turnstile(
        secret="turnstile-secret",
        token=token,
        remote_ip=ip,
        expected_hostname=hostname,
    )


def test_siteverify_is_called_with_the_secret_token_and_client_ip(monkeypatch):
    fake = FakeSiteverify()
    monkeypatch.setattr(turnstile, "_http_post_form", fake)
    result = _verify_token("good-token", ip="203.0.113.9")
    assert result.success is True
    assert fake.calls[-1]["url"] == turnstile.SITEVERIFY_URL
    assert fake.calls[-1]["data"] == {
        "secret": "turnstile-secret",
        "response": "good-token",
        "remoteip": "203.0.113.9",
    }


def test_failed_siteverify_is_refused(monkeypatch):
    monkeypatch.setattr(turnstile, "_http_post_form", FakeSiteverify())
    result = _verify_token("fail-token")
    assert result.success is False
    assert result.reason == "verification_failed"


def test_token_minted_for_another_hostname_is_refused(monkeypatch):
    monkeypatch.setattr(turnstile, "_http_post_form", FakeSiteverify())
    result = _verify_token("wrong-host-token")
    assert result.success is False
    assert result.reason == "hostname_mismatch"
    assert result.hostname == "evil.example"


def test_missing_token_never_reaches_siteverify(monkeypatch):
    fake = FakeSiteverify()
    monkeypatch.setattr(turnstile, "_http_post_form", fake)
    result = _verify_token("")
    assert result.success is False
    assert result.reason == "missing_token"
    assert fake.calls == []


def test_network_failure_fails_closed(monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(turnstile, "_http_post_form", explode)
    result = _verify_token("good-token")
    assert result.success is False
    assert result.reason == "network_error"


# --------------------------------------------------------------------------- #
# 3. Slug policy mirrors the provisioner
# --------------------------------------------------------------------------- #


def test_reserved_slugs_include_the_names_the_brief_names():
    for name in ("www", "app", "api", "admin", "mail"):
        assert name in RESERVED_SLUGS
    assert len(RESERVED_SLUGS) >= 27
    assert SLUG_RE.pattern == r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"


def test_reserved_slugs_match_the_provisioner_script():
    """Parity with Hoja-Cloud/scripts/hoja-tenant when that repo is present."""
    script = Path(
        "/home/robert/Work/Hoja-Work/Hoja-Cloud/scripts/hoja-tenant")
    if not script.is_file():
        pytest.skip("Hoja-Cloud scripts/hoja-tenant is not present")
    source = script.read_text(encoding="utf-8")
    block = re.search(r"RESERVED_SLUGS = \{([^}]*)\}", source)
    assert block is not None
    provisioner = ast.literal_eval("{" + block.group(1) + "}")
    assert set(provisioner) == set(RESERVED_SLUGS)


@pytest.mark.parametrize(
    "slug, reason",
    [
        ("", "slug_required"),
        ("   ", "slug_required"),
        ("www", "reserved_slug"),
        ("admin", "reserved_slug"),
        ("Paypal", "invalid_slug"),
        ("a" * 64, "invalid_slug"),
        ("-leading", "invalid_slug"),
        ("trailing-", "invalid_slug"),
        ("double--hyphen", "invalid_slug"),
        ("xn--paypal", "invalid_slug"),
        ("has space", "invalid_slug"),
        ("under_score", "invalid_slug"),
        ("paypal.work", "invalid_slug"),
    ],
)
def test_slug_policy_refuses(slug, reason):
    with pytest.raises(SlugRefused) as caught:
        validate_slug(slug)
    assert caught.value.reason == reason


@pytest.mark.parametrize("slug", ["acme", "acme-2", "a1", "x" * 63])
def test_slug_policy_accepts(slug):
    assert validate_slug(slug) == slug


# --------------------------------------------------------------------------- #
# 4. The OIDC flow: state, binding, redirect_uri, session cookie
# --------------------------------------------------------------------------- #


def test_start_sets_a_short_lived_httponly_lax_flow_cookie(harness):
    response = harness.start_flow(return_to="/welcome")
    assert response.status_code == 302
    cookie = response.headers["set-cookie"].lower()
    assert "hoja_oidc_flow=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = parse_qs(urlsplit(location).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://localhost/auth/google/callback"]


def test_start_refuses_a_host_outside_the_redirect_allowlist(harness):
    response = harness.start_flow(host="evil.example")
    assert response.status_code == 400
    assert response.json()["reason"] == "redirect_uri_not_allowed"


def test_callback_sets_an_opaque_session_cookie(harness):
    response, _state = harness.sign_in()
    assert response.status_code == 302
    cookie = response.headers["set-cookie"]
    assert "hoja_signup_session=" in cookie
    value = harness.client.cookies.get("hoja_signup_session")
    assert len(value) > 40
    assert value.count(".") == 0  # opaque, not a JWT
    with harness.engine.connect() as conn:
        stored = conn.execute(select(signup_session.c.token_hash)).scalar_one()
    assert stored == token_hash(value)  # only the hash is at rest
    assert stored != value
    assert harness.store.resolve_session(value) is not None


def test_callback_session_cookie_is_secure_on_https(tmp_path, monkeypatch):
    harness = _build_harness(
        tmp_path, monkeypatch,
        base_url="https://signup.hoja.test",
        env={
            "GOOGLE_REDIRECT_URI": "https://signup.hoja.test/auth/google/callback",
        },
    )
    try:
        harness.host = "signup.hoja.test"
        response, _state = harness.sign_in(host="signup.hoja.test")
        assert response.status_code == 302
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Path=/" in cookie
        assert "Domain=" not in cookie
    finally:
        harness.client.__exit__(None, None, None)


def test_login_rotates_the_session_and_revokes_the_previous_one(harness):
    harness.sign_in(subject="google-sub-1", email="person@example.test")
    first = harness.client.cookies.get("hoja_signup_session")
    harness.sign_in(subject="google-sub-1", email="person@example.test")
    second = harness.client.cookies.get("hoja_signup_session")
    assert first and second and first != second
    assert harness.store.resolve_session(first) is None
    assert harness.store.resolve_session(second) is not None


def test_replayed_state_is_rejected(harness):
    harness.google.claim_overrides = {}
    start = harness.start_flow()
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    flow_cookie = harness.client.cookies.get("hoja_oidc_flow")
    with harness.engine.connect() as conn:
        nonce = conn.execute(
            select(signup_flow.c.nonce).where(signup_flow.c.state_hash == token_hash(state))
        ).scalar_one()
    harness.google.claim_overrides = {"nonce": nonce}

    first = harness.client.get(
        "/auth/google/callback", params={"code": "auth-code", "state": state},
        headers={"host": harness.host}, follow_redirects=False)
    assert first.status_code == 302

    harness.client.cookies.set("hoja_oidc_flow", flow_cookie)
    replay = harness.client.get(
        "/auth/google/callback", params={"code": "auth-code", "state": state},
        headers={"host": harness.host},
        follow_redirects=False)
    assert replay.status_code == 400
    assert replay.json()["reason"] == "state_replayed"


def test_state_without_the_browser_binding_cookie_is_rejected(harness):
    start = harness.start_flow()
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    harness.client.cookies.clear()  # a different browser, or a stolen link
    response = harness.client.get(
        "/auth/google/callback", params={"code": "auth-code", "state": state},
        headers={"host": harness.host},
        follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["reason"] == "state_unknown"
    # The rightful browser can still complete the flow: a bad binding does not consume it.
    with harness.engine.connect() as conn:
        consumed = conn.execute(
            select(signup_flow.c.consumed_at).where(signup_flow.c.state_hash == token_hash(state))
        ).scalar_one()
    assert consumed is None


def test_a_redirect_uri_outside_the_allowlist_is_rejected(harness):
    harness.google.claim_overrides = {}
    start = harness.start_flow()
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    with harness.engine.begin() as conn:
        conn.execute(
            update(signup_flow)
            .where(signup_flow.c.state_hash == token_hash(state))
            .values(redirect_uri="https://evil.example/auth/google/callback")
        )
    response = harness.client.get(
        "/auth/google/callback", params={"code": "auth-code", "state": state},
        headers={"host": harness.host}, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["reason"] == "redirect_uri_not_allowed"
    assert harness.google.token_calls == []  # the code was never exchanged


def test_the_pkce_verifier_and_exact_redirect_uri_are_sent_to_the_token_endpoint(harness):
    harness.sign_in()
    call = harness.google.token_calls[-1]
    assert call["url"] == google_oidc.GOOGLE_TOKEN_ENDPOINT
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["redirect_uri"] == "http://localhost/auth/google/callback"
    assert call["data"]["code_verifier"]
    with harness.engine.connect() as conn:
        verifier = conn.execute(select(signup_flow.c.code_verifier)).scalar_one()
    assert call["data"]["code_verifier"] == verifier


def _jwk_for(kid: str, key) -> dict:
    numbers = key.public_key().public_numbers()

    def encode(value: int) -> str:
        return google_oidc.b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
            "n": encode(numbers.n), "e": encode(numbers.e)}


def test_a_rotated_signing_key_is_refetched_once(harness):
    """A cached JWKS one key behind must not lock people out of sign-in."""
    retired = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    harness.google.jwks_queue.append({"keys": [_jwk_for("retired-kid", retired)]})
    response, _state = harness.sign_in()
    assert response.status_code == 302, response.text
    assert harness.google.jwks_calls == 2  # cached set, then one refetch
    assert harness.store.resolve_session(
        harness.client.cookies.get("hoja_signup_session")) is not None


def test_callback_rejects_an_id_token_with_the_wrong_nonce(harness):
    response, _state = harness.sign_in(nonce="not-this-flow")
    assert response.status_code == 400
    assert response.json()["reason"] == "nonce_mismatch"
    assert harness.client.cookies.get("hoja_signup_session") is None
    assert harness.count(signup_identity) == 0


def test_callback_rejects_an_unverified_email(harness):
    response, _state = harness.sign_in(email_verified=False)
    assert response.status_code == 400
    assert response.json()["reason"] == "email_unverified"
    assert harness.count(signup_identity) == 0


def test_callback_without_a_google_error_never_exchanges(harness):
    start = harness.start_flow()
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = harness.client.get(
        "/auth/google/callback", params={"error": "access_denied", "state": state},
        headers={"host": harness.host}, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["reason"] == "provider_error"
    assert harness.google.token_calls == []


def test_return_to_must_stay_on_this_host(harness):
    response = harness.start_flow(return_to="https://evil.example/phish")
    assert response.status_code == 302
    with harness.engine.connect() as conn:
        return_to = conn.execute(select(signup_flow.c.return_to)).scalar_one()
    assert return_to == "/"


# --------------------------------------------------------------------------- #
# 5. The gate: every refusal code, in the documented order
# --------------------------------------------------------------------------- #


def test_anonymous_cannot_reach_the_gate(harness):
    response = harness.signup("acme")
    assert response.status_code == 401
    body = response.json()
    assert body["reason"] == "not_authenticated"
    assert body["state"] == "anonymous"
    assert harness.eligibility() == []


def test_signup_is_closed_by_default(tmp_path, monkeypatch):
    harness = _build_harness(tmp_path, monkeypatch, mode="closed")
    try:
        harness.sign_in()
        response = harness.signup("acme")
        assert response.status_code == 403
        assert response.json()["reason"] == "signup_closed"
        assert harness.eligibility() == []
    finally:
        harness.client.__exit__(None, None, None)


def test_missing_turnstile_token_never_reaches_eligible(harness):
    harness.sign_in()
    response = harness.client.post("/api/signup", json={"slug": "acme"})
    assert response.status_code == 403
    body = response.json()
    assert body["reason"] == "turnstile_missing"
    assert body["googleVerified"] is True
    assert body["humanVerified"] is False
    assert harness.eligibility() == []
    assert harness.siteverify.calls == []


def test_failed_turnstile_never_reaches_eligible(harness):
    harness.sign_in()
    response = harness.signup("acme", token="fail-token")
    assert response.status_code == 403
    body = response.json()
    assert body["reason"] == "turnstile_failed"
    assert body["humanVerified"] is False
    assert harness.eligibility() == []


def test_a_token_minted_for_another_hostname_never_reaches_eligible(harness):
    harness.sign_in()
    response = harness.signup("acme", token="wrong-host-token")
    assert response.status_code == 403
    assert response.json()["reason"] == "turnstile_failed"
    assert harness.eligibility() == []


def test_a_replayed_turnstile_token_is_rejected(harness):
    harness.sign_in()
    first = harness.signup("acme", token="reused-token")
    assert first.status_code == 201
    # Same token, different workspace: a replay, and it must not pass.
    second = harness.signup("acme-two", token="reused-token")
    assert second.status_code == 403
    assert second.json()["reason"] == "turnstile_replayed"
    assert [row["slug"] for row in harness.eligibility()] == ["acme"]
    assert len(harness.turnstile_uses()) == 1


def test_reserved_slug_is_refused_before_a_token_is_spent(harness):
    harness.sign_in()
    response = harness.signup("www")
    assert response.status_code == 403
    body = response.json()
    assert body["reason"] == "reserved_slug"
    assert body["state"] == "google_verified"
    assert harness.siteverify.calls == []
    assert harness.turnstile_uses() == []


@pytest.mark.parametrize("slug, reason", [("", "slug_required"), ("Bad_Name", "invalid_slug")])
def test_invalid_slug_is_refused(harness, slug, reason):
    harness.sign_in()
    response = harness.signup(slug)
    assert response.json()["reason"] == reason
    assert harness.eligibility() == []


def test_a_taken_slug_is_refused(harness):
    harness.sign_in(subject="google-sub-1", email="first@example.test")
    assert harness.signup("acme").status_code == 201
    harness.sign_in(subject="google-sub-2", email="second@example.test")
    response = harness.signup("acme", token="fresh-token")
    assert response.status_code == 409
    assert response.json()["reason"] == "slug_taken"
    assert len(harness.eligibility()) == 1


def test_per_identity_cap_refuses_a_second_workspace(harness):
    harness.sign_in()
    assert harness.signup("acme").status_code == 201
    response = harness.signup("acme-two", token="fresh-token")
    assert response.status_code == 429
    assert response.json()["reason"] == "identity_cap_exceeded"
    assert len(harness.eligibility()) == 1


def test_per_ip_cap_refuses_a_third_identity_from_one_address(tmp_path, monkeypatch):
    harness = _build_harness(
        tmp_path, monkeypatch,
        env={"HOJA_SIGNUP_IP_CAP": "2", "HOJA_SIGNUP_IDENTITY_CAP": "5"})
    try:
        harness.sign_in(subject="sub-1", email="one@example.test")
        assert harness.signup("acme-one", token="token-1").status_code == 201
        harness.sign_in(subject="sub-2", email="two@example.test")
        assert harness.signup("acme-two", token="token-2").status_code == 201
        harness.sign_in(subject="sub-3", email="three@example.test")
        response = harness.signup("acme-three", token="token-3")
        assert response.status_code == 429
        assert response.json()["reason"] == "ip_cap_exceeded"
        assert len(harness.eligibility()) == 2
    finally:
        harness.client.__exit__(None, None, None)


def test_invite_mode_requires_an_invitation(tmp_path, monkeypatch):
    harness = _build_harness(tmp_path, monkeypatch, mode="invite")
    try:
        harness.sign_in(email="stranger@example.test")
        response = harness.signup("acme")
        assert response.status_code == 403
        assert response.json()["reason"] == "invite_required"
        assert harness.eligibility() == []

        harness.store.add_invite(email="invited@example.test", note="beta")
        harness.sign_in(subject="sub-invited", email="invited@example.test")
        accepted = harness.signup("acme", token="invited-token")
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["state"] == "eligible"
        assert harness.eligibility()[0]["email"] == "invited@example.test"
    finally:
        harness.client.__exit__(None, None, None)


def test_workspace_domain_policy_refuses_another_domain(tmp_path, monkeypatch):
    harness = _build_harness(
        tmp_path, monkeypatch, env={"HOJA_GOOGLE_HD": "acme.example"})
    try:
        harness.sign_in(email="person@gmail.example")
        response = harness.signup("acme")
        assert response.status_code == 403
        assert response.json()["reason"] == "domain_not_allowed"

        harness.sign_in(subject="sub-workspace", email="person@acme.example",
                        hd="acme.example")
        assert harness.signup("acme").status_code == 201
    finally:
        harness.client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# 6. The happy path, and what it must not do
# --------------------------------------------------------------------------- #


def test_happy_path_reaches_eligible_exactly_once_and_is_idempotent(harness):
    harness.sign_in(email="founder@example.test")
    first = harness.signup("acme", token="token-one")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body == {
        "ok": True,
        "state": "eligible",
        "reason": None,
        "message": "Your workspace request is queued.",
        "googleVerified": True,
        "humanVerified": True,
        "eligibilityId": body["eligibilityId"],
        "slug": "acme",
        "status": "eligible",
        "alreadyEligible": False,
    }

    rows = harness.eligibility()
    assert len(rows) == 1
    assert rows[0]["status"] == "eligible"
    assert rows[0]["claimed_at"] is None  # queued for the provisioner, not provisioned
    assert rows[0]["google_sub"] == "google-sub-1"
    assert rows[0]["email"] == "founder@example.test"
    assert harness.count(user) == 0
    assert harness.count(organization) == 0

    second = harness.signup("acme", token="token-two")
    assert second.status_code == 200
    assert second.json()["alreadyEligible"] is True
    assert second.json()["eligibilityId"] == body["eligibilityId"]
    assert len(harness.eligibility()) == 1  # no duplicate row


def test_the_gate_creates_no_workspace_and_calls_no_provisioner(harness):
    harness.sign_in()
    before_users = harness.count(user)
    before_orgs = harness.count(organization)
    response = harness.signup("acme")
    assert response.status_code == 201
    assert harness.count(user) == before_users
    assert harness.count(organization) == before_orgs
    # The only outbound call in the whole request was siteverify, via the seam.
    assert len(harness.siteverify.calls) == 1


def test_gate_module_has_no_provisioning_machinery():
    source = inspect.getsource(signup_gate).lower()
    for verb in ("subprocess", "docker ", "psql", "cloudflare.com", "socket.", "requests."):
        assert verb not in source
    assert "httpx" not in source


def test_signup_state_endpoint_reports_the_state_machine(harness):
    assert harness.client.get("/api/signup").json()["state"] == "anonymous"
    harness.sign_in(email="person@example.test")
    body = harness.client.get("/api/signup").json()
    assert body["state"] == "google_verified"
    assert body["googleVerified"] is True
    assert body["humanVerified"] is False
    assert body["email"] == "person@example.test"


def test_logout_revokes_the_signup_session(harness):
    harness.sign_in()
    cookie = harness.client.cookies.get("hoja_signup_session")
    assert harness.store.resolve_session(cookie) is not None
    response = harness.client.post("/auth/google/logout")
    assert response.status_code == 200
    assert harness.store.resolve_session(cookie) is None
    assert harness.client.get("/api/signup").json()["state"] == "anonymous"


def test_cross_site_post_is_refused(harness):
    harness.sign_in()
    response = harness.client.post(
        "/api/signup",
        json={"slug": "acme", "turnstileToken": "token"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["reason"] == "cross_site_request"
    assert harness.eligibility() == []


# --------------------------------------------------------------------------- #
# 7. Configuration fails closed
# --------------------------------------------------------------------------- #


def test_unconfigured_deployment_refuses_instead_of_allowing(tmp_path, monkeypatch):
    _clear_signup_env(monkeypatch)
    engine = make_engine(tmp_path / "unconfigured.db")
    migrate(engine)
    app = create_app(str(tmp_path / "unconfigured.db"), open_mode=False)
    with TestClient(app, base_url="http://localhost") as client:
        gate = client.post("/api/signup", json={"slug": "acme"})
        assert gate.status_code == 503
        assert gate.json()["reason"] == "signup_unconfigured"
        start = client.get("/auth/google/start", follow_redirects=False)
        assert start.status_code == 503
        assert start.json()["reason"] == "signup_unconfigured"


@pytest.mark.parametrize("mode", ["open", "invite"])
def test_enabled_mode_without_credentials_fails_at_startup(tmp_path, monkeypatch, mode):
    _clear_signup_env(monkeypatch)
    monkeypatch.setenv("HOJA_SIGNUP_MODE", mode)
    with pytest.raises(SignupConfigError) as caught:
        create_app(str(tmp_path / "missing-config.db"), open_mode=False)
    message = str(caught.value)
    assert "GOOGLE_CLIENT_ID" in message
    assert "TURNSTILE_SECRET_KEY" in message


def test_half_configured_deployment_fails_at_startup(tmp_path, monkeypatch):
    _clear_signup_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    with pytest.raises(SignupConfigError) as caught:
        create_app(str(tmp_path / "half-config.db"), open_mode=False)
    assert "incomplete" in str(caught.value)


def test_an_unknown_mode_is_a_startup_error(tmp_path, monkeypatch):
    _clear_signup_env(monkeypatch)
    monkeypatch.setenv("HOJA_SIGNUP_MODE", "open-sesame")
    with pytest.raises(SignupConfigError):
        create_app(str(tmp_path / "bad-mode.db"), open_mode=False)


def test_default_mode_is_closed(monkeypatch):
    _clear_signup_env(monkeypatch)
    config = load_signup_config()
    assert config.mode == "closed"
    assert config.configured is False
    assert config.identity_cap == 1


def test_non_https_redirect_uri_is_a_startup_error(tmp_path, monkeypatch):
    _clear_signup_env(monkeypatch)
    monkeypatch.setenv("HOJA_SIGNUP_MODE", "open")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://public.example/auth/google/callback")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "turnstile-secret")
    monkeypatch.setenv("TURNSTILE_EXPECTED_HOSTNAME", "public.example")
    with pytest.raises(SignupConfigError) as caught:
        load_signup_config()
    assert "https" in str(caught.value)


def test_the_redirect_allowlist_is_exact_and_per_host(tmp_path, monkeypatch):
    _clear_signup_env(monkeypatch)
    monkeypatch.setenv("HOJA_SIGNUP_MODE", "open")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI", "https://signup.hoja.test/auth/google/callback")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI_ALLOWLIST",
                       "https://signup.hoja.test/auth/google/callback,"
                       "https://beta.hoja.test/auth/google/callback")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "turnstile-secret")
    monkeypatch.setenv("TURNSTILE_EXPECTED_HOSTNAME", "signup.hoja.test")
    config = load_signup_config()
    assert config.redirect_uri_for("signup.hoja.test") == (
        "https://signup.hoja.test/auth/google/callback")
    assert config.redirect_uri_for("BETA.hoja.test:443") == (
        "https://beta.hoja.test/auth/google/callback")
    assert config.redirect_uri_for("signup.hoja.test.evil.example") is None
    assert config.redirect_uri_for("evil.example") is None


def test_signup_reason_codes_are_unique_and_stable():
    values = [reason.value for reason in SignupReason]
    assert len(values) == len(set(values))
    for expected in (
        "signup_unconfigured",
        "not_authenticated",
        "signup_closed",
        "reserved_slug",
        "turnstile_missing",
        "turnstile_failed",
        "turnstile_replayed",
        "invite_required",
        "identity_cap_exceeded",
        "ip_cap_exceeded",
        "slug_taken",
    ):
        assert expected in values
