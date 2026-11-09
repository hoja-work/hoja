"""Calendly routes: one registered redirect URI, a forwarding broker, a session-bound redeem."""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx
from sqlalchemy import select

pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.connectors_api import CALENDLY_PROVIDER
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

TOKEN_URL = "https://calendly.com/oauth/token"
REVOKE_URL = "https://calendly.com/oauth/revoke"
USERS_ME = "https://api.calendly.com/users/me"
BROKER = "https://api.hoja.work"
BROKER_CALLBACK = f"{BROKER}/api/connectors/calendly/callback"
CLIENT_ID = "v_vI3AB0wpc6kfKia09I8HCLzqVIVm47KEtfdUGvcrE"
SLUG = "calendly-org"
TENANT = f"https://{SLUG}.hoja.work"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CALENDLY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("HOJA_CALENDLY_CLIENT_SECRET", "secret_route_test_value")
    monkeypatch.setenv("HOJA_CALENDLY_REDIRECT_URI", BROKER_CALLBACK)
    monkeypatch.setenv("HOJA_TENANT_BASE_DOMAIN", "hoja.work")
    monkeypatch.delenv("HOJA_CONNECTOR_BROKER_URL", raising=False)


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("HOJA_CONNECTOR_BROKER_URL", BROKER)


def _setup(tmp_path):
    db_path = tmp_path / "calendly.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Calendly Org",
        org_url_key=SLUG,
        user_email="owner@calendly.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _client(app, base="https://testserver"):
    return TestClient(app, base_url=base)


def _start(client, token, extra=""):
    return client.get(
        f"/api/connectors/calendly/start{extra}",
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
        "provider": CALENDLY_PROVIDER,
        "verifier": "v" * 43,
        "tenant": None,
        "exp": int(time.time()) + 600,
        "nonce": uuid.uuid4().hex,
    }
    body.update(pending)
    return encrypt_secret(json.dumps(body))


def _token_response(**overrides) -> httpx.Response:
    body = {
        "token_type": "Bearer",
        "access_token": "access-calendly-fixture",
        "refresh_token": "refresh-calendly-fixture",
        "expires_in": 7200,
        "scope": "users:read event_types:read scheduled_events:read",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _users_me() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "resource": {
                "uri": "https://api.calendly.com/users/U1",
                "name": "Luis",
                "email": "luis@hoja.work",
                "scheduling_url": "https://calendly.com/luis",
                "timezone": "Europe/Berlin",
                "current_organization": "https://api.calendly.com/organizations/ORG1",
            }
        },
    )


def _rows(engine, org: str) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(connector_account).where(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == CALENDLY_PROVIDER,
                )
            )
            .mappings()
            .all()
        )


# --- start ----------------------------------------------------------------------------------


def test_start_requires_a_session(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/calendly/start", follow_redirects=False)
    assert res.status_code == 401


def test_start_refuses_an_organization_scope(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        assert _start(client, admin["token"], "?scope=org").status_code == 400


def test_start_asks_for_s256_and_the_three_read_scopes(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = _start(client, admin["token"])
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == {
        "users:read",
        "event_types:read",
        "scheduled_events:read",
    }
    assert "secret_route_test_value" not in res.headers["location"]


def test_start_registers_the_broker_callback_when_one_is_configured(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as client:
        res = _start(client, admin["token"])
    query = parse_qs(urlparse(res.headers["location"]).query)
    # The tenant's own host is NOT what Calendly is told to return to.
    assert query["redirect_uri"] == [BROKER_CALLBACK]


def test_start_refuses_a_host_that_is_not_a_workspace(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, "https://localhost") as client:
        res = _start(client, admin["token"])
    assert res.status_code == 400


# --- broker ---------------------------------------------------------------------------------


def test_broker_forwards_to_the_tenant_and_stores_nothing(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
    with _client(app, BROKER) as edge:
        res = edge.get(
            f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
            follow_redirects=False,
        )
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith(f"{TENANT}/api/connectors/calendly/finish?")
    query = parse_qs(urlparse(location).query)
    assert query["code"] == ["c1"]
    assert query["state"] == [state]
    # The broker saw no credential and wrote nothing.
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
        "http://calendly-org.hoja.work",
        "https://calendly-org.hoja.work.evil.com",
        None,
    ],
)
def test_broker_never_forwards_to_a_host_the_state_merely_claims(tmp_path, broker, tenant) -> None:
    _, app, _ = _setup(tmp_path)
    state = _seal({"tenant": tenant})
    with _client(app, BROKER) as edge:
        res = edge.get(
            f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
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
            f"/api/connectors/calendly/callback?code=c1&state={quote(tampered)}",
            follow_redirects=False,
        )
    assert res.status_code == 400


# --- finish ---------------------------------------------------------------------------------


def test_finish_requires_the_starting_session(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        res = tenant.get(
            f"/api/connectors/calendly/finish?code=c1&state={quote(state)}",
            follow_redirects=False,
        )
    assert res.status_code == 401


def test_finish_refuses_a_state_issued_to_someone_else(tmp_path, broker) -> None:
    _, app, admin = _setup(tmp_path)
    state = _seal({"tenant": TENANT, "uid": "someone-else", "org": admin["org_id"]})
    with _client(app, TENANT) as tenant:
        res = tenant.get(
            f"/api/connectors/calendly/finish?code=c1&state={quote(state)}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_full_broker_flow_stores_a_sealed_grant(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            token_route = respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            res = tenant.get(
                f"/api/connectors/calendly/finish?code=c1&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert res.status_code == 302
    assert res.headers["location"] == f"{TENANT}/?connector=calendly-connected"
    # The token exchange repeats the broker callback, or Calendly would refuse the code.
    body = parse_qs(token_route.calls.last.request.content.decode())
    assert body["redirect_uri"] == [BROKER_CALLBACK]
    assert body["code_verifier"]
    rows = _rows(engine, admin["org_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["external_account"] == "luis@hoja.work"
    assert row["owner_user_id"] == admin["user_id"]
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-calendly-fixture"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-calendly-fixture"


def test_a_replayed_state_is_refused(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            first = tenant.get(
                f"/api/connectors/calendly/finish?code=c1&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
            second = tenant.get(
                f"/api/connectors/calendly/finish?code=c2&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert first.status_code == 302
    assert second.status_code == 400
    assert len(_rows(engine, admin["org_id"])) == 1


# --- single-host installs (OSS default, no broker) -------------------------------------------


def test_without_a_broker_the_callback_is_the_install_itself(tmp_path, monkeypatch) -> None:
    """The OSS default: a single-tenant install registers its own callback and needs no broker."""
    install_callback = "https://hoja.example.test/api/connectors/calendly/callback"
    monkeypatch.setenv("HOJA_CALENDLY_REDIRECT_URI", install_callback)
    engine, app, admin = _setup(tmp_path)
    with _client(app, "https://hoja.example.test") as client:
        res = _start(client, admin["token"])
        query = parse_qs(urlparse(res.headers["location"]).query)
        assert query["redirect_uri"] == [install_callback]
        state = query["state"][0]
        with respx.mock:
            token_route = respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            done = client.get(
                f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert done.status_code == 302
    assert done.headers["location"] == "/?connector=calendly-connected"
    body = parse_qs(token_route.calls.last.request.content.decode())
    assert body["redirect_uri"] == [install_callback]
    assert len(_rows(engine, admin["org_id"])) == 1


# --- revoke ---------------------------------------------------------------------------------


def _connect(engine, app, admin) -> str:
    with _client(app, TENANT) as tenant:
        state = _state_from(_start(tenant, admin["token"]))
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            tenant.get(
                f"/api/connectors/calendly/finish?code=c1&state={quote(state)}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    return _rows(engine, admin["org_id"])[0]["id"]


def test_revoke_revokes_the_whole_grant_at_calendly(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    account_id = _connect(engine, app, admin)
    with _client(app, TENANT) as client, respx.mock:
        revoke_route = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200, json={}))
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 200
    assert res.json()["providerRevoked"] is True
    sent = parse_qs(revoke_route.calls.last.request.content.decode())
    assert sent["token"] == ["refresh-calendly-fixture"]
    row = _rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""
    assert row["encrypted_access_token"] == ""


def test_a_failed_provider_revocation_keeps_the_local_grant(tmp_path, broker) -> None:
    engine, app, admin = _setup(tmp_path)
    account_id = _connect(engine, app, admin)
    with _client(app, TENANT) as client, respx.mock:
        respx.post(REVOKE_URL).mock(return_value=httpx.Response(502, json={}))
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 502
    assert _rows(engine, admin["org_id"])[0]["status"] == "active"


def test_config_reports_calendly_without_exposing_a_credential(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/config")
    body = res.json()
    assert body["calendly"] is True
    assert body["calendlyAgent"] is False
    assert "secret_route_test_value" not in res.text
    assert CLIENT_ID not in res.text
