"""Settings-side behaviour of Chat: a second add-on on the same Google grant, never a second row.

The Meet tests already pin the shared upgrade mechanics. These pin what Chat adds: three scopes
requested together, a capability that needs all three, and an upgrade that leaves Meet's own
capability alone.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx

pytest.importorskip("starlette")

from google_workspace.oauth import CHAT_SCOPES, READ_SCOPES
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

TOKEN_URL = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
WORKSPACE_SCOPES = " ".join(READ_SCOPES.values())
CHAT_SCOPE_STRING = " ".join(CHAT_SCOPES)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-route-test-secret")
    monkeypatch.setenv(
        "HOJA_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )


def _setup(tmp_path):
    db_path = tmp_path / "chat.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Chat Org",
        org_url_key="chat-org",
        user_email="owner@chat.test",
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
                external_account="owner@chat.test",
                scopes=scopes,
                encrypted_refresh_token=encrypt_secret("1//OLD-REFRESH"),
                status="active",
                created_at="t0",
                updated_at="t0",
            )
        )
    return account_id


def _start(client, token, *, addon=None):
    url = "/api/connectors/google/start?scope=user"
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


def test_a_chat_upgrade_without_a_google_account_is_refused_by_name(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = _start(client, admin["token"], addon="chat")
    assert res.status_code == 409
    assert "before adding Google Chat access" in res.json()["error"]


def test_a_chat_upgrade_asks_for_the_three_chat_scopes_and_nothing_else(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    with TestClient(app) as client:
        res = _start(client, admin["token"], addon="chat")
        state = _state(res)
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["scope"] == [CHAT_SCOPE_STRING]
    assert query["include_granted_scopes"] == ["true"]
    # A Meet or Workspace scope here would re-ask for permissions Chat does not use.
    for scope in READ_SCOPES.values():
        assert scope not in query["scope"][0]
    assert json.loads(decrypt_secret(state))["addon"] == "chat"


@respx.mock
def test_a_chat_upgrade_extends_the_same_row_and_keeps_meet(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    meet_scope = "https://www.googleapis.com/auth/meetings.space.readonly"
    original = _grant(engine, admin, scopes=f"{WORKSPACE_SCOPES} {meet_scope}")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW-REFRESH",
                "expires_in": 3599,
                "scope": f"{WORKSPACE_SCOPES} {meet_scope} {CHAT_SCOPE_STRING}",
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@chat.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="chat"))
        )
    assert res.status_code == 302
    assert res.headers["location"].endswith("connector=chat-connected")
    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0]["id"] == original
    stored = set(rows[0]["scopes"].split())
    assert stored == set(WORKSPACE_SCOPES.split()) | {meet_scope} | set(CHAT_SCOPES)


@respx.mock
def test_a_chat_upgrade_with_one_scope_unticked_is_refused(tmp_path) -> None:
    """Two of the three scopes cannot serve the third API's calls, so a partial grant would show a
    Chat row whose tools answer 403 for messages only."""
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW-REFRESH",
                "expires_in": 3599,
                "scope": f"{WORKSPACE_SCOPES} {CHAT_SCOPES[0]}",
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "owner@chat.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="chat"))
        )
    assert res.status_code == 400
    assert "Google Chat access was not granted" in res.json()["error"]
    assert CHAT_SCOPES[1] not in _rows(engine)[0]["scopes"]


@respx.mock
def test_a_chat_upgrade_consented_with_another_google_account_is_refused(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.NEW",
                "refresh_token": "1//NEW",
                "expires_in": 3599,
                "scope": CHAT_SCOPE_STRING,
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "someone-else@chat.test"})
    )
    with TestClient(app) as client:
        res = _callback(
            client, admin["token"], _state(_start(client, admin["token"], addon="chat"))
        )
    assert res.status_code == 409
    assert "Google Chat access was added to someone-else@chat.test" in res.json()["error"]
    assert len(_rows(engine)) == 1


def test_capabilities_report_chat_only_when_all_three_scopes_are_present(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    _grant(engine, admin, scopes=f"{WORKSPACE_SCOPES} {CHAT_SCOPES[0]} {CHAT_SCOPES[1]}")
    with TestClient(app) as client:
        accounts = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"]
    assert accounts[0]["capabilities"]["chat"] is False
    assert accounts[0]["capabilities"]["gmail"] is True

    _grant(engine, admin, scopes=f"{WORKSPACE_SCOPES} {CHAT_SCOPE_STRING}", account_id="acct-full")
    with TestClient(app) as client:
        accounts = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"]
    full = next(a for a in accounts if a["id"] == "acct-full")
    assert full["capabilities"]["chat"] is True
