"""The control plane learning what a tenant knows.

Before this, an invited member was a stranger to `/signin`: the directory read
`signup_eligibility` only, and an invitation lived in the tenant. A tenant now
announces its invitations, signed with the key derived for its own host, and the
control plane stores a hint it can route on and refuse on.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from hoja_server import identity_transfer, membership_announce
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate
from hoja_server.signup_config import SignupConfig
from hoja_server.signup_gate import SignupGate, SignupReason
from hoja_server.signup_store import SignupStore
from hoja_server.turnstile import TurnstileResult

MASTER = "m" * 40
CONTROL = "https://app.hoja.work"
SLUG = "hoja"


@pytest.fixture
def store(tmp_path):
    engine = make_engine(tmp_path / "control.db")
    migrate(engine)
    return SignupStore(engine)


def _tenant_host() -> str:
    return f"{SLUG}.hoja.work"


def _tenant_key() -> str:
    return identity_transfer.tenant_key(MASTER, _tenant_host())


# HOJA-26: the directory has to know --------------------------------------- #


def test_an_announced_workspace_appears_in_the_directory(store):
    store.record_membership_hint(slug=SLUG, email="kevin@hoja.work")

    rows = store.workspaces_for(subject="sub-kevin", email="kevin@hoja.work")

    assert rows == [{"slug": SLUG, "status": "active", "role": "member"}]


def test_announcing_twice_does_not_duplicate_the_row(store):
    store.record_membership_hint(slug=SLUG, email="kevin@hoja.work")
    store.record_membership_hint(slug=SLUG, email="kevin@hoja.work", role="admin")

    rows = store.workspaces_for(subject=None, email="kevin@hoja.work")

    assert len(rows) == 1
    assert rows[0]["role"] == "admin"


def test_an_expired_hint_is_not_a_membership(store):
    store.record_membership_hint(slug=SLUG, email="kevin@hoja.work",
                                 expires_at="2020-01-01T00:00:00+00:00")

    assert store.workspaces_for(subject=None, email="kevin@hoja.work") == []


def test_a_hint_for_one_address_is_invisible_to_another(store):
    store.record_membership_hint(slug=SLUG, email="kevin@hoja.work")

    assert store.workspaces_for(subject=None, email="someone@else.work") == []


# HOJA-32: and the gate has to refuse -------------------------------------- #


def _gate(store):
    config = SignupConfig(
        mode="open", google_client_id="cid", google_client_secret="cs",
        redirect_uris=(f"{CONTROL}/auth/google/callback",),
        turnstile_secret_key="secret", turnstile_expected_hostname="app.hoja.work",
        identity_cap=1, control_plane_url=CONTROL)
    ok = TurnstileResult(success=True, reason="ok", hostname="app.hoja.work")
    return SignupGate(config=config, store=store, verifier=lambda **kw: ok)


def test_a_member_cannot_request_a_second_workspace(store):
    store.record_membership_hint(slug=SLUG, email="kevin@hoja.work")
    identity = store.upsert_identity(
        subject="sub-kevin", email="kevin@hoja.work", email_verified=True)

    decision = _gate(store).evaluate(
        identity=identity, slug="kevin-workspace",
        turnstile_token="tok-1", client_ip="203.0.113.9")

    assert decision.reason == SignupReason.ALREADY_MEMBER
    assert SLUG in decision.message
    assert store.eligibility_by_slug("kevin-workspace") is None


def test_a_stranger_can_still_request_a_workspace(store):
    identity = store.upsert_identity(
        subject="sub-new", email="new@example.test", email_verified=True)

    decision = _gate(store).evaluate(
        identity=identity, slug="new-workspace",
        turnstile_token="tok-1", client_ip="203.0.113.9")

    assert decision.reason is None
    assert decision.eligibility["slug"] == "new-workspace"


# the wire ----------------------------------------------------------------- #


@pytest.fixture
def control_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", CONTROL)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_MASTER", MASTER)
    monkeypatch.setenv("HOJA_TENANT_BASE_DOMAIN", "hoja.work")
    path = tmp_path / "control.db"
    migrate(make_engine(path))

    from starlette.testclient import TestClient

    with TestClient(create_app(str(path), open_mode=False), base_url=CONTROL) as client:
        yield client


def _announcement_body(**over):
    payload = {"slug": SLUG, "email": "kevin@hoja.work", "role": "member", "expires_at": None}
    payload.update(over)
    return json.dumps(payload).encode()


def test_a_correctly_signed_announcement_is_accepted(control_client):
    body = _announcement_body()
    signed = control_client.post(
        "/internal/membership", content=body,
        headers={"content-type": "application/json",
                 "x-hoja-signature": membership_announce.signature(_tenant_key(), body)})

    assert signed.status_code == 200
    assert signed.json() == {"ok": True}


def test_an_unsigned_or_misdirected_announcement_is_refused(control_client):
    body = _announcement_body()
    unsigned = control_client.post(
        "/internal/membership", content=body,
        headers={"content-type": "application/json"})
    assert unsigned.status_code == 401

    # A key derived for a different host must not admit this one.
    other_key = identity_transfer.tenant_key(MASTER, "evil.hoja.work")
    forged = control_client.post(
        "/internal/membership", content=body,
        headers={"content-type": "application/json",
                 "x-hoja-signature": membership_announce.signature(other_key, body)})
    assert forged.status_code == 401


def test_announcing_never_raises_when_the_control_plane_is_down(monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("httpx.post", explode)

    assert membership_announce.announce(
        tenant_key=_tenant_key(), control_plane_url="https://app.hoja.work",
        slug=SLUG, email="kevin@hoja.work") is False


def test_malformed_announcements_are_refused_not_crashed(control_client):
    """A junk header must be a 401, never a 500."""
    body = _announcement_body()
    cases = [
        (b"null", membership_announce.signature(_tenant_key(), b"null")),
        (b"[1,2]", membership_announce.signature(_tenant_key(), b"[1,2]")),
    ]
    for raw, sig in cases:
        r = control_client.post("/internal/membership", content=raw,
                                headers={"content-type": "application/json",
                                         "x-hoja-signature": sig})
        assert r.status_code == 400

    # A non-hex signature is refused before any comparison. (A non-ASCII header
    # cannot even be sent by this client, so the guard is what is under test.)
    junk = control_client.post(
        "/internal/membership", content=body,
        headers={"content-type": "application/json", "x-hoja-signature": "not-a-digest"})
    assert junk.status_code == 401


def test_a_non_string_expiry_is_refused(control_client):
    body = _announcement_body(expires_at=99999999999)
    r = control_client.post(
        "/internal/membership", content=body,
        headers={"content-type": "application/json",
                 "x-hoja-signature": membership_announce.signature(_tenant_key(), body)})
    assert r.status_code == 400
