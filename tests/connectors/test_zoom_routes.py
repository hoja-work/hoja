"""Zoom routes: a per-tenant allow-listed callback, a session-bound single-use state, and revoke."""

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
from hoja_server.connectors_api import ZOOM_PROVIDER
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

TOKEN_URL = "https://zoom.us/oauth/token"
REVOKE_URL = "https://zoom.us/oauth/revoke"
USERS_ME = "https://api.zoom.us/v2/users/me"
HOST = "https://testserver"
CALLBACK = f"{HOST}/api/connectors/zoom/callback"
CLIENT_ID = "5kXHQtScS8WhD2lgmuedKQ"
SLUG = "zoom-org"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_ZOOM_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("HOJA_ZOOM_CLIENT_SECRET", "secret_route_test_value")
    monkeypatch.setenv("HOJA_ZOOM_REDIRECT_URI", CALLBACK)
    monkeypatch.delenv("HOJA_ZOOM_REDIRECT_URIS", raising=False)
    monkeypatch.delenv("HOJA_CONNECTOR_SIGNING_KEY", raising=False)


def _setup(tmp_path):
    db_path = tmp_path / "zoom.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Zoom Org",
        org_url_key=SLUG,
        user_email="owner@zoom.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _client(app, base=HOST):
    return TestClient(app, base_url=base)


def _start(client, token, extra=""):
    return client.get(
        f"/api/connectors/zoom/start{extra}",
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
        "provider": ZOOM_PROVIDER,
        "origin": HOST,
        "exp": int(time.time()) + 600,
        "nonce": uuid.uuid4().hex,
    }
    body.update(pending)
    return encrypt_secret(json.dumps(body))


def _token_response(**overrides) -> httpx.Response:
    body = {
        "access_token": "access-zoom-fixture",
        "token_type": "bearer",
        "refresh_token": "refresh-zoom-fixture",
        "expires_in": 3600,
        "scope": (
            "user:read:user meeting:read:list_meetings meeting:read:meeting "
            "cloud_recording:read:list_user_recordings "
            "cloud_recording:read:list_recording_files "
            "cloud_recording:read:meeting_transcript"
        ),
        "api_url": "https://api.zoom.us",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _users_me() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "APv5EPHiSvitxgPAw0DbaQ",
            "email": "luis@hoja.work",
            "first_name": "Luis",
            "last_name": "Rincon",
            "type": 1,
            "account_id": "Cx3wERazSgup7ZWRHQM8-w",
        },
    )


def _rows(engine, org: str) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(connector_account).where(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == ZOOM_PROVIDER,
                )
            )
            .mappings()
            .all()
        )


# --- start ----------------------------------------------------------------------------------


def test_start_requires_a_session(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/zoom/start", follow_redirects=False)
    assert res.status_code == 401


def test_start_refuses_an_organization_scope(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = _start(client, admin["token"], "?scope=org")
    assert res.status_code == 400
    assert "personal" in res.json()["error"].lower()


def test_start_redirects_to_zoom_with_the_tenant_callback_and_no_pkce(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith("https://zoom.us/oauth/authorize?")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == [CALLBACK]
    assert "code_challenge" not in query
    assert query["state"][0]


def test_start_refuses_a_host_that_is_not_in_the_zoom_allow_list(tmp_path) -> None:
    """Refusing here is the difference between a clear configuration error and Zoom's opaque
    consent-screen refusal."""
    _, app, admin = _setup(tmp_path)
    with _client(app, base="https://ghost.hoja.work") as client:
        res = _start(client, admin["token"])
    assert res.status_code == 400
    assert "allow list" in (res.json().get("hint") or "").lower()


def test_an_unconfigured_deployment_reports_503(tmp_path, monkeypatch) -> None:
    _, app, admin = _setup(tmp_path)
    monkeypatch.delenv("HOJA_ZOOM_CLIENT_SECRET", raising=False)
    with _client(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 503
    assert "HOJA_ZOOM_CLIENT_SECRET" in res.json()["error"]


# --- callback ------------------------------------------------------------------------------


def test_callback_requires_code_and_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = client.get(
            "/api/connectors/zoom/callback",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_refuses_a_provider_error(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = client.get(
            "/api/connectors/zoom/callback?error=access_denied&state=x",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_refuses_an_expired_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal({"uid": admin["user_id"], "org": admin["org_id"], "exp": int(time.time()) - 5})
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400
    assert "expired" in res.json()["error"].lower()


def test_callback_refuses_a_state_belonging_to_another_user(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal({"uid": "someone-else", "org": admin["org_id"]})
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_callback_refuses_a_state_from_another_workspace_host(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal({"uid": admin["user_id"], "org": admin["org_id"], "origin": "https://clover.hoja.work"})
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_callback_refuses_a_state_for_another_provider(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal({"uid": admin["user_id"], "org": admin["org_id"], "provider": "calendly"})
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


@respx.mock
def test_the_happy_path_stores_a_sealed_grant_and_zooms_user_id(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    exchange = respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USERS_ME).mock(return_value=_users_me())
    with _client(app) as client:
        start = _start(client, admin["token"])
        state = _state_from(start)
        res = client.get(
            f"/api/connectors/zoom/callback?code=code-1&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 302
    assert res.headers["location"].endswith("connector=zoom-connected")
    # The exchange repeats the same redirect URI the authorize leg used.
    body = exchange.calls[0].request.content.decode()
    assert "redirect_uri=https%3A%2F%2Ftestserver%2Fapi%2Fconnectors%2Fzoom%2Fcallback" in body

    rows = _rows(engine, admin["org_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["external_account"] == "luis@hoja.work"
    assert row["external_id"] == "APv5EPHiSvitxgPAw0DbaQ"
    assert row["owner_user_id"] == admin["user_id"]
    # What Zoom granted is what is recorded, not what was asked for.
    assert row["scopes"].startswith("user:read:user")
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-zoom-fixture"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-zoom-fixture"
    assert int(row["access_token_expires_at"]) > time.time()


@respx.mock
def test_reconnecting_the_same_account_replaces_its_grant(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USERS_ME).mock(return_value=_users_me())
    with _client(app) as client:
        for _ in range(2):
            state = _state_from(_start(client, admin["token"]))
            res = client.get(
                f"/api/connectors/zoom/callback?code=c&state={quote(state, safe='')}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
            assert res.status_code == 302
    assert len(_rows(engine, admin["org_id"])) == 1


@respx.mock
def test_a_state_cannot_be_replayed(tmp_path) -> None:
    """The nonce table is what makes a captured callback useless a second time."""
    _engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USERS_ME).mock(return_value=_users_me())
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        first = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
        second = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert first.status_code == 302
    assert second.status_code == 400
    assert "already used" in second.json()["error"].lower()


@respx.mock
def test_a_rejected_exchange_reports_the_grant_failure(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "reason": "Invalid authorization code"}
        )
    )
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        res = client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400
    assert "Invalid authorization code" in res.json()["error"]
    assert _rows(engine, admin["org_id"]) == []


# --- revoke --------------------------------------------------------------------------------


@respx.mock
def test_revoke_calls_zoom_and_clears_the_stored_grant(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USERS_ME).mock(return_value=_users_me())
    revoke = respx.post(REVOKE_URL).mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        client.get(
            f"/api/connectors/zoom/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
        account_id = _rows(engine, admin["org_id"])[0]["id"]
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 200
    assert res.json() == {"revoked": True, "providerRevoked": True, "hint": None}
    assert len(revoke.calls) == 1
    # The refresh token is what was revoked: revoking only the access token would leave the
    # grant renewable.
    assert "token=refresh-zoom-fixture" in revoke.calls[0].request.content.decode()
    row = _rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""
    assert row["encrypted_access_token"] == ""


# --- config --------------------------------------------------------------------------------


def test_config_reports_zoom_can_connect_but_not_yet_reach_an_agent(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        payload = client.get("/api/connectors/config").json()
    assert payload["zoom"] is True
    assert payload["zoomAgent"] is False  # no signing key on this deployment


def test_config_reports_zoom_absent_without_credentials(tmp_path, monkeypatch) -> None:
    _, app, _ = _setup(tmp_path)
    monkeypatch.delenv("HOJA_ZOOM_CLIENT_ID", raising=False)
    with _client(app) as client:
        payload = client.get("/api/connectors/config").json()
    assert payload["zoom"] is False
    assert payload["zoomAgent"] is False
