"""Settings-side behaviour of Meet: an upgrade of the existing Google grant, never a second account.

The interesting failures here are not "does the redirect work". They are: a second row appearing
(so Disconnect frees only half a grant), a Meet permission recorded when the user unticked it, and
a Meet capability silently lost when the same account is reconnected for Workspace.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx

pytest.importorskip("starlette")

from google_workspace.oauth import MEET_SCOPE, READ_SCOPES
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

TOKEN_URL = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
WORKSPACE_SCOPES = " ".join(READ_SCOPES.values())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-route-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )


def _setup(tmp_path):
    db_path = tmp_path / "meet.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Meet Org",
        org_url_key="meet-org",
        user_email="owner@meet.test",
        token="owner-token",
        demo_issues=False,
    )
    return engine, create_app(str(db_path), open_mode=False), admin


def _grant(engine, admin, scopes=WORKSPACE_SCOPES, account_id="acct-google"):
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=account_id,
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="google_workspace",
                external_account="owner@meet.test",
                scopes=scopes,
                encrypted_refresh_token=encrypt_secret("1//OLD-REFRESH"),
                status="active",
                created_at="t0",
                updated_at="t0",
            )
        )
    return account_id


def _start(client, token, *, addon=None, scope="user"):
    url = f"/api/connectors/google/start?scope={scope}"
    if addon:
        url += f"&addon={addon}"
    return client.get(url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)


def _state(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _callback(client, token, state, code="auth-code"):
    return client.get(
        f"/api/connectors/google/callback?code={code}&state={quote(state, safe='')}",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(connector_account.select()).mappings().all()


def test_an_upgrade_without_a_google_account_is_refused(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = _start(client, admin["token"], addon="meet")
    assert res.status_code == 409
    assert "Connect your Google account" in res.json()["error"]


def test_an_unknown_upgrade_is_refused(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    with TestClient(app) as client:
        res = _start(client, admin["token"], addon="calendar-write")
    assert res.status_code == 400


def test_an_upgrade_asks_google_for_the_meet_scope_only(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    with TestClient(app) as client:
        res = _start(client, admin["token"], addon="meet")
        state = _state(res)
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["scope"] == [MEET_SCOPE]
    assert query["include_granted_scopes"] == ["true"]
    pending = json.loads(decrypt_secret(state))
    assert pending["addon"] == "meet"


@respx.mock
def test_an_upgrade_extends_the_existing_row_instead_of_adding_a_second(tmp_path) -> None:
    """One Google account, one grant, one Disconnect. A second row would hold the same grant twice
    and a revoke would free only one of them."""
    engine, app, admin = _setup(tmp_path)
    original = _grant(engine, admin)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW-REFRESH",
                "expires_in": 3599,
                "scope": f"{WORKSPACE_SCOPES} {MEET_SCOPE}",
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@meet.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="meet"))
        )
    assert res.status_code == 302
    assert res.headers["location"].endswith("connector=meet-connected")
    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0]["id"] == original
    assert set(rows[0]["scopes"].split()) == set(WORKSPACE_SCOPES.split()) | {MEET_SCOPE}
    assert decrypt_secret(rows[0]["encrypted_refresh_token"]) == "1//NEW-REFRESH"


@respx.mock
def test_an_upgrade_the_user_refused_leaves_the_grant_untouched(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW-REFRESH",
                "expires_in": 3599,
                "scope": WORKSPACE_SCOPES,  # the Meet permission was unticked
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@meet.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="meet"))
        )
    assert res.status_code == 400
    assert "not granted" in res.json()["error"]
    rows = _rows(engine)
    assert len(rows) == 1
    assert MEET_SCOPE not in rows[0]["scopes"]
    assert decrypt_secret(rows[0]["encrypted_refresh_token"]) == "1//OLD-REFRESH"


@respx.mock
def test_an_upgrade_without_a_reported_scope_is_rejected(tmp_path) -> None:
    """Google always reports `scope` on an authorization-code exchange. Treating silence as success
    would record a capability nobody verified."""
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "a", "refresh_token": "1//R", "expires_in": 3599},
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@meet.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="meet"))
        )
    assert res.status_code == 502
    assert MEET_SCOPE not in _rows(engine)[0]["scopes"]


@respx.mock
def test_reconnecting_for_workspace_keeps_the_meet_capability(tmp_path) -> None:
    """With `include_granted_scopes=true` Google reports the whole grant on any consent, so a plain
    Workspace reconnect must not look like a downgrade."""
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin, scopes=f"{WORKSPACE_SCOPES} {MEET_SCOPE}")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW",
                "expires_in": 3599,
                "scope": f"{WORKSPACE_SCOPES} {MEET_SCOPE}",
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@meet.test"})
    )
    with TestClient(app) as client:
        res = _callback(client, admin["token"], _state(_start(client, admin["token"])))
    assert res.status_code == 302
    rows = _rows(engine)
    assert len(rows) == 1
    assert MEET_SCOPE in rows[0]["scopes"]


@respx.mock
def test_unchecking_meet_on_a_later_consent_drops_the_capability(tmp_path) -> None:
    """The stored row must follow Google, not the memory of an earlier consent: otherwise Settings
    shows Meet as available for a token that cannot use it."""
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin, scopes=f"{WORKSPACE_SCOPES} {MEET_SCOPE}")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW",
                "expires_in": 3599,
                "scope": WORKSPACE_SCOPES,
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@meet.test"})
    )
    with TestClient(app) as client:
        _callback(client, admin["token"], _state(_start(client, admin["token"])))
    assert MEET_SCOPE not in _rows(engine)[0]["scopes"]


def test_the_accounts_list_reports_which_capabilities_the_grant_covers(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin, scopes=f"{READ_SCOPES['gmail']} {MEET_SCOPE}")
    with TestClient(app) as client:
        accounts = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"]
    assert accounts[0]["capabilities"] == {
        "gmail": True,
        "drive": False,
        "calendar": False,
        "docs": False,
        "meet": True,
        "chat": False,
    }


@respx.mock
def test_disconnect_revokes_the_whole_google_grant_and_says_so(tmp_path) -> None:
    """Meet rides on the Google grant, so its Disconnect is the Google Disconnect: revoking the
    refresh token ends Workspace access too. The response reports that Google confirmed."""
    engine, app, admin = _setup(tmp_path)
    account_id = _grant(engine, admin, scopes=f"{WORKSPACE_SCOPES} {MEET_SCOPE}")
    revoke = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    with TestClient(app) as client:
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
        listing = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()
    assert res.status_code == 200
    assert res.json()["revoked"] is True
    assert res.json()["providerRevoked"] is True
    assert revoke.call_count == 1
    assert listing["accounts"] == []
    rows = _rows(engine)
    assert rows[0]["status"] == "revoked"
    assert rows[0]["encrypted_refresh_token"] == ""


@respx.mock
def test_an_upgrade_consented_with_a_different_google_account_is_refused(tmp_path) -> None:
    """The consent chooser can pick another Google account than the one the upgrade started from.
    That account has no grant to extend, so accepting it would create the Meet-only row that
    `start` refuses up front."""
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)  # owner@meet.test
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW",
                "expires_in": 3599,
                "scope": MEET_SCOPE,
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "someone-else@meet.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="meet"))
        )
    assert res.status_code == 409
    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0]["external_account"] == "owner@meet.test"
