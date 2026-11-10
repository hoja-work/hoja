"""Sentry routes: install URL, forwarding broker, session-bound redeem, webhook, revoke."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx
from sqlalchemy import select

pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.connectors_api import SENTRY_PROVIDER
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

CLIENT_ID = "63f307c32617f9445b9deba149d733004d49495ce7b07fb142017482bbf67167"
CLIENT_SECRET = "secret_route_test_value"
SLUG = "sentry-org"
INSTALLATION = "cd6170b4-8ad6-403e-b078-8eb7d7688c28"
AUTHZ = f"https://sentry.io/api/0/sentry-app-installations/{INSTALLATION}/authorizations/"
INSTALLATION_URL = f"https://sentry.io/api/0/sentry-app-installations/{INSTALLATION}/"
ORGS = "https://sentry.io/api/0/organizations/"
BROKER = "https://api.hoja.work"
BROKER_CALLBACK = f"{BROKER}/api/connectors/sentry/callback"
FINISH = "/api/connectors/sentry/finish"
TENANT = f"https://{SLUG}.hoja.work"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_SENTRY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("HOJA_SENTRY_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("HOJA_SENTRY_REDIRECT_URI", BROKER_CALLBACK)
    monkeypatch.setenv("HOJA_SENTRY_INTEGRATION_SLUG", "hoja")
    monkeypatch.setenv("HOJA_TENANT_BASE_DOMAIN", "hoja.work")
    monkeypatch.delenv("HOJA_CONNECTOR_BROKER_URL", raising=False)


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("HOJA_CONNECTOR_BROKER_URL", BROKER)


def _setup(tmp_path):
    db_path = tmp_path / "sentry.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Sentry Org",
        org_url_key=SLUG,
        user_email="owner@sentry.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _client(app, base="https://testserver"):
    return TestClient(app, base_url=base)


def _start(client, token, extra=""):
    return client.get(
        f"/api/connectors/sentry/start{extra}",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )


def _state_from(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _seal(pending: dict) -> str:
    """Craft a state the way the server would, so a guard can be probed directly."""
    body = {
        "uid": "user-1",
        "org": "org-1",
        "provider": SENTRY_PROVIDER,
        "tenant": None,
        "exp": int(time.time()) + 600,
        "nonce": uuid.uuid4().hex,
    }
    body.update(pending)
    return encrypt_secret(json.dumps(body))


GRANT_LIFETIME_SECONDS = 8 * 60 * 60


def _grant_window(lifetime: int = GRANT_LIFETIME_SECONDS) -> tuple[int, int]:
    """(dateCreated, expiresAt) as epochs, for a grant the provider has just minted."""
    created = int(time.time())
    return created, created + lifetime


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _token_response(**overrides) -> httpx.Response:
    created, expires = _grant_window()
    body = {
        "id": "38",
        "token": "access-sentry-fixture",
        "refreshToken": "refresh-sentry-fixture",
        "scopes": ["event:read", "org:read", "project:read"],
        "dateCreated": _iso(created),
        "expiresAt": _iso(expires),
        "state": None,
        "application": None,
    }
    body.update(overrides)
    return httpx.Response(201, json=body)


def _organization(slug: str = "hoja") -> httpx.Response:
    return httpx.Response(200, json=[{"id": "1", "slug": slug, "name": "Hoja"}])


def _rows(engine, org: str) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(connector_account).where(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == SENTRY_PROVIDER,
                )
            )
            .mappings()
            .all()
        )


# --- start ----------------------------------------------------------------------------------


def test_start_requires_a_session(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/sentry/start", follow_redirects=False)
    assert res.status_code == 401


def test_start_refuses_an_organization_scope(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        assert _start(client, admin["token"], "?scope=org").status_code == 400


def test_start_redirects_to_the_fixed_install_url_with_only_the_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith("https://sentry.io/sentry-apps/hoja/external-install/?")
    query = parse_qs(urlparse(location).query)
    assert set(query) == {"state"}
    assert CLIENT_SECRET not in location


def test_start_registers_the_broker_callback_when_one_is_live(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as client:
        assert _start(client, admin["token"]).status_code == 302


def test_start_refuses_a_host_that_is_not_a_workspace(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, "https://localhost") as client:
        assert _start(client, admin["token"]).status_code == 400


# --- broker ---------------------------------------------------------------------------------


def test_broker_forwards_the_install_to_the_tenant_and_stores_nothing(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
    with _client(app, BROKER) as edge:
        res = edge.get(
            f"/api/connectors/sentry/callback?code=c1&installationId={INSTALLATION}"
            f"&orgSlug=hoja&state={quote(state)}",
            follow_redirects=False,
        )
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith(f"{TENANT}{FINISH}?")
    query = parse_qs(urlparse(location).query)
    assert query["code"] == ["c1"]
    assert query["installationId"] == [INSTALLATION]
    assert query["orgSlug"] == ["hoja"]
    assert query["state"] == [state]
    assert _rows(engine, admin["org_id"]) == []


@pytest.mark.parametrize(
    "tenant",
    [
        "https://evil.com",
        "https://api.hoja.work",
        "https://app.hoja.work",
        "https://www.hoja.work",
        "https://admin.hoja.work",
        "https://ghost.hoja.work",  # well formed, but not a registered tenant
        "http://sentry-org.hoja.work",
        "https://sentry-org.hoja.work.evil.com",
        None,
    ],
)
def test_broker_never_forwards_to_a_host_the_state_merely_claims(tmp_path, broker, tenant) -> None:
    _, app, _ = _setup(tmp_path)
    state = _seal({"tenant": tenant})
    with _client(app, BROKER) as edge:
        res = edge.get(
            f"/api/connectors/sentry/callback?code=c1&installationId={INSTALLATION}"
            f"&orgSlug=hoja&state={quote(state)}",
            follow_redirects=False,
        )
    assert res.status_code == 403
    assert "location" not in res.headers


def test_broker_refuses_a_tampered_state(tmp_path, broker) -> None:
    _, app, _ = _setup(tmp_path)
    state = _seal({"tenant": TENANT})
    tampered = state[:-4] + ("AAAA" if not state.endswith("AAAA") else "BBBB")
    with _client(app, BROKER) as edge:
        res = edge.get(
            f"/api/connectors/sentry/callback?code=c1&installationId={INSTALLATION}"
            f"&orgSlug=hoja&state={quote(tampered)}",
            follow_redirects=False,
        )
    assert res.status_code == 400


# --- finish ---------------------------------------------------------------------------------


def test_finish_requires_the_starting_session(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        res = tenant.get(
            f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja&state={quote(state)}",
            follow_redirects=False,
        )
    assert res.status_code == 401


def test_finish_refuses_a_state_issued_to_someone_else(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    state = _seal({"tenant": TENANT, "uid": "someone-else", "org": admin["org_id"]})
    with _client(app, TENANT) as tenant:
        res = tenant.get(
            f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja&state={quote(state)}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_full_broker_flow_stores_a_sealed_installation_grant(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    _, expires_at = _grant_window()
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            exchange = respx.post(AUTHZ).mock(return_value=_token_response(expiresAt=_iso(expires_at)))
            respx.get(ORGS).mock(return_value=_organization("hoja"))
            res = tenant.get(
                f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja"
                f"&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert res.status_code == 302
    assert res.headers["location"] == f"{TENANT}/?connector=sentry-connected"
    sent = json.loads(exchange.calls.last.request.content)
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "c1"
    rows = _rows(engine, admin["org_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["external_account"] == "hoja"
    assert row["external_id"] == INSTALLATION
    assert row["owner_user_id"] == admin["user_id"]
    assert set(row["scopes"].split()) == {"event:read", "org:read", "project:read"}
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-sentry-fixture"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-sentry-fixture"
    # The exact instant the provider named, not merely "some time after now".
    assert int(row["access_token_expires_at"]) == expires_at


def test_an_organization_that_disagrees_with_the_callback_is_refused(tmp_path, broker) -> None:
    """The token is the authority; a callback naming another org must not label the row."""
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            respx.post(AUTHZ).mock(return_value=_token_response())
            respx.get(ORGS).mock(return_value=_organization("someone-else"))
            res = tenant.get(
                f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja"
                f"&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert res.status_code == 400
    assert _rows(engine, admin["org_id"]) == []


def test_a_replayed_state_is_refused(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            respx.post(AUTHZ).mock(return_value=_token_response())
            respx.get(ORGS).mock(return_value=_organization("hoja"))
            first = tenant.get(
                f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja"
                f"&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
            second = tenant.get(
                f"{FINISH}?code=c2&installationId={INSTALLATION}&orgSlug=hoja"
                f"&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert first.status_code == 302
    assert second.status_code == 400
    assert len(_rows(engine, admin["org_id"])) == 1


def test_reconnecting_the_same_org_replaces_the_row(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant, respx.mock:
        respx.post(AUTHZ).mock(return_value=_token_response())
        respx.get(ORGS).mock(return_value=_organization("hoja"))
        for _ in range(2):
            state = _state_from(_start(tenant, admin["token"]))
            tenant.get(
                f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja"
                f"&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert len(_rows(engine, admin["org_id"])) == 1


# --- single-host installs (OSS default, no broker) -------------------------------------------


def test_without_a_broker_the_callback_is_the_install_itself(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, "https://hoja.example.test") as client:
        state = _state_from(_start(client, admin["token"]))
        with respx.mock:
            respx.post(AUTHZ).mock(return_value=_token_response())
            respx.get(ORGS).mock(return_value=_organization("hoja"))
            done = client.get(
                f"/api/connectors/sentry/callback?code=c1&installationId={INSTALLATION}"
                f"&orgSlug=hoja&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert done.status_code == 302
    assert done.headers["location"] == "/?connector=sentry-connected"
    assert len(_rows(engine, admin["org_id"])) == 1


# --- revoke ---------------------------------------------------------------------------------


def _connect(engine, app, admin) -> str:
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            respx.post(AUTHZ).mock(return_value=_token_response())
            respx.get(ORGS).mock(return_value=_organization("hoja"))
            tenant.get(
                f"{FINISH}?code=c1&installationId={INSTALLATION}&orgSlug=hoja"
                f"&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    return _rows(engine, admin["org_id"])[0]["id"]


def test_revoke_removes_the_installation_at_sentry(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    account_id = _connect(engine, app, admin)
    with _client(app, TENANT) as client, respx.mock:
        route = respx.delete(INSTALLATION_URL).mock(return_value=httpx.Response(204))
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 200
    assert res.json()["providerRevoked"] is True
    assert len(route.calls) == 1
    row = _rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""
    assert row["encrypted_access_token"] == ""


def test_revoke_reports_the_pending_deletion_window(tmp_path, broker) -> None:
    """Measured: DELETE answers 403 while Sentry finishes an uninstall started in its own UI."""
    engine, app, admin = _setup(tmp_path)
    account_id = _connect(engine, app, admin)
    with _client(app, TENANT) as client, respx.mock:
        respx.delete(INSTALLATION_URL).mock(return_value=httpx.Response(403))
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    body = res.json()
    assert res.status_code == 200
    assert body["providerRevoked"] is False
    assert "ten" in (body["hint"] or "")
    assert _rows(engine, admin["org_id"])[0]["status"] == "revoked"


# --- webhook --------------------------------------------------------------------------------


def _signed(body: bytes, secret: str = CLIENT_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_the_lifecycle_webhook_marks_a_removed_installation_revoked(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _connect(engine, app, admin)
    payload = json.dumps(
        {"action": "deleted", "installation": {"uuid": INSTALLATION, "status": "deleted"}}
    ).encode()
    with _client(app, BROKER) as edge:
        res = edge.post(
            "/api/connectors/sentry/webhook",
            content=payload,
            headers={
                "sentry-hook-signature": _signed(payload),
                "sentry-hook-resource": "installation",
            },
        )
    assert res.status_code == 200
    assert _rows(engine, admin["org_id"])[0]["status"] == "revoked"


def test_the_webhook_refuses_an_unsigned_body(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _connect(engine, app, admin)
    payload = json.dumps({"action": "deleted", "installation": {"uuid": INSTALLATION}}).encode()
    with _client(app, BROKER) as edge:
        bad = edge.post(
            "/api/connectors/sentry/webhook",
            content=payload,
            headers={"sentry-hook-signature": _signed(payload, "wrong-secret")},
        )
        missing = edge.post("/api/connectors/sentry/webhook", content=payload)
    assert bad.status_code == 401
    assert missing.status_code == 401
    assert _rows(engine, admin["org_id"])[0]["status"] == "active"


# --- config ---------------------------------------------------------------------------------


def test_config_reports_sentry_without_exposing_a_credential(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/config")
    body = res.json()
    assert body["sentry"] is True
    assert body["sentryAgent"] is False
    assert CLIENT_SECRET not in res.text
    assert CLIENT_ID not in res.text


def test_config_reports_sentry_agent_only_with_a_signing_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", "signing-key-for-test")
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        body = client.get("/api/connectors/config").json()
    assert body["sentryAgent"] is True
