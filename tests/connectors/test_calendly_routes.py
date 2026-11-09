"""Calendly routes: one registered redirect URI, browser-bound callback, sealed grant, revoke."""

from __future__ import annotations

import time
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
from hoja_server.secrets_box import decrypt_secret

TOKEN_URL = "https://calendly.com/oauth/token"
REVOKE_URL = "https://calendly.com/oauth/revoke"
USERS_ME = "https://api.calendly.com/users/me"
CALLBACK = "https://hoja.example.test/api/connectors/calendly/callback"
CLIENT_ID = "v_vI3AB0wpc6kfKia09I8HCLzqVIVm47KEtfdUGvcrE"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_CALENDLY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("HOJA_CALENDLY_CLIENT_SECRET", "secret_route_test_value")
    monkeypatch.setenv("HOJA_CALENDLY_REDIRECT_URI", CALLBACK)


def _setup(tmp_path):
    db_path = tmp_path / "calendly.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Calendly Org",
        org_url_key="calendly-org",
        user_email="owner@calendly.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _start(client, token, extra=""):
    return client.get(
        f"/api/connectors/calendly/start{extra}",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )


def _state_from(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


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


def _to_handoff(client, token) -> str:
    res = _start(client, token)
    assert res.status_code == 302
    return res.headers["location"]


def test_start_requires_a_session(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        res = client.get("/api/connectors/calendly/start", follow_redirects=False)
    assert res.status_code == 401


def test_start_refuses_an_organization_scope(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        res = _start(client, admin["token"], "?scope=org")
    assert res.status_code == 400


def test_start_hands_off_to_the_registered_host_and_leaks_nothing(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        location = _to_handoff(client, admin["token"])
    parsed = urlparse(location)
    # One redirect URI per app means the flow must continue on that host, not the tenant's own.
    assert parsed.netloc == "hoja.example.test"
    assert parsed.path == "/api/connectors/calendly/handoff"
    assert "secret_route_test_value" not in location
    state = parse_qs(parsed.query)["state"][0]
    # The state is sealed: reading it must not reveal the user or organization.
    assert admin["user_id"] not in state
    assert "org" not in state


def test_handoff_sets_the_browser_binding_cookie_and_authorizes_with_s256(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        location = _to_handoff(client, admin["token"])
        res = client.get(urlparse(location).path + "?" + urlparse(location).query,
                         follow_redirects=False)
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [CALLBACK]
    assert set(query["scope"][0].split()) == {
        "users:read",
        "event_types:read",
        "scheduled_events:read",
    }
    cookie = res.cookies.get("hoja_calendly_link")
    assert cookie
    assert res.headers["set-cookie"].lower().find("httponly") != -1


def test_handoff_rejects_a_tampered_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        state = _state_from(_start(client, admin["token"]))
        tampered = state[:-4] + ("AAAA" if not state.endswith("AAAA") else "BBBB")
        res = client.get(
            f"/api/connectors/calendly/handoff?state={quote(tampered)}", follow_redirects=False
        )
    assert res.status_code == 400


def test_callback_without_the_binding_cookie_is_refused(tmp_path) -> None:
    """Account-linking CSRF: a state minted elsewhere cannot be completed in this browser."""
    _, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        state = _state_from(_start(client, admin["token"]))
        client.cookies.clear()
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            res = client.get(
                f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
                follow_redirects=False,
            )
    assert res.status_code == 403


def test_full_connect_stores_a_sealed_grant_owned_by_the_consenting_user(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        handoff = _to_handoff(client, admin["token"])
        client.get(urlparse(handoff).path + "?" + urlparse(handoff).query, follow_redirects=False)
        state = parse_qs(urlparse(handoff).query)["state"][0]
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            res = client.get(
                f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
                follow_redirects=False,
            )
    assert res.status_code == 302
    assert res.headers["location"].startswith("https://hoja.example.test/?connector=calendly-connected")
    rows = _rows(engine, admin["org_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["external_account"] == "luis@hoja.work"
    assert row["owner_user_id"] == admin["user_id"]
    assert row["status"] == "active"
    assert set(row["scopes"].split()) == {
        "users:read",
        "event_types:read",
        "scheduled_events:read",
    }
    # Sealed at rest: neither half of the grant is readable in the column.
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-calendly-fixture"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-calendly-fixture"
    assert int(row["access_token_expires_at"]) > time.time()


def test_a_replayed_state_is_refused(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        handoff = _to_handoff(client, admin["token"])
        client.get(urlparse(handoff).path + "?" + urlparse(handoff).query, follow_redirects=False)
        state = parse_qs(urlparse(handoff).query)["state"][0]
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            first = client.get(
                f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
                follow_redirects=False,
            )
            assert first.status_code == 302
            # The cookie was cleared; set it back to prove the nonce, not the cookie, is spent.
            client.cookies.set("hoja_calendly_link", "whatever", path="/api/connectors/calendly")
            second = client.get(
                f"/api/connectors/calendly/callback?code=c2&state={quote(state)}",
                follow_redirects=False,
            )
    assert second.status_code in (400, 403)
    assert len(_rows(engine, admin["org_id"])) == 1


def test_revoke_tells_calendly_and_clears_the_stored_grant(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        handoff = _to_handoff(client, admin["token"])
        client.get(urlparse(handoff).path + "?" + urlparse(handoff).query, follow_redirects=False)
        state = parse_qs(urlparse(handoff).query)["state"][0]
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            client.get(
                f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
                follow_redirects=False,
            )
        account_id = _rows(engine, admin["org_id"])[0]["id"]
        with respx.mock:
            revoke_route = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200, json={}))
            res = client.post(
                f"/api/connectors/accounts/{account_id}/revoke",
                headers={"Authorization": f"Bearer {admin['token']}"},
            )
    assert res.status_code == 200
    body = res.json()
    assert body["revoked"] is True
    assert body["providerRevoked"] is True
    sent = parse_qs(revoke_route.calls.last.request.content.decode())
    assert sent["token"] == ["refresh-calendly-fixture"]  # the whole grant, not just the access token
    row = _rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""
    assert row["encrypted_access_token"] == ""


def test_revoke_without_provider_confirmation_is_reported(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        handoff = _to_handoff(client, admin["token"])
        client.get(urlparse(handoff).path + "?" + urlparse(handoff).query, follow_redirects=False)
        state = parse_qs(urlparse(handoff).query)["state"][0]
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            respx.get(USERS_ME).mock(return_value=_users_me())
            client.get(
                f"/api/connectors/calendly/callback?code=c1&state={quote(state)}",
                follow_redirects=False,
            )
        account_id = _rows(engine, admin["org_id"])[0]["id"]
        with respx.mock:
            respx.post(REVOKE_URL).mock(return_value=httpx.Response(502, json={}))
            res = client.post(
                f"/api/connectors/accounts/{account_id}/revoke",
                headers={"Authorization": f"Bearer {admin['token']}"},
            )
    assert res.status_code == 502
    # A failed provider call must not silently discard the local grant.
    assert _rows(engine, admin["org_id"])[0]["status"] == "active"


def test_config_reports_calendly_without_exposing_a_credential(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        res = client.get("/api/connectors/config")
    body = res.json()
    assert body["calendly"] is True
    assert body["calendlyAgent"] is False  # no signing key in this environment
    assert "secret_route_test_value" not in res.text
    assert CLIENT_ID not in res.text
