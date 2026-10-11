"""Google connector routes: OAuth state binding, admin gate for org accounts, ownership on list and revoke."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx

pytest.importorskip("starlette")

from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import connector_account, make_engine, migrate, seed_tenant
from cliniar_server.secrets_box import decrypt_secret, encrypt_secret
from cliniar_server.store import Store

TOKEN_URL = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("CLINIAR_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("CLINIAR_GOOGLE_CLIENT_SECRET", "GOCSPX-route-test-secret")
    monkeypatch.setenv(
        "CLINIAR_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback"
    )


def _setup(tmp_path):
    db_path = tmp_path / "connectors.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Conn Org",
        org_url_key="conn-org",
        user_email="owner@conn.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _member_token(store, admin, client):
    from tests.test_member_management import _add_member

    member_id = _add_member(client, admin["token"], "member@conn.test")
    _, key = store.create_api_key(admin["org_id"], member_id, "member", "read_write", None)
    return key


def _start(client, token, scope="user"):
    return client.get(
        f"/api/connectors/google/start?scope={scope}",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )


def _state_from(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


@respx.mock
def test_start_redirects_with_pkce_and_read_only_scopes(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert "gmail.send" not in query["scope"][0]
    assert "client_secret" not in res.headers["location"]


def test_org_scope_requires_admin(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        token = _member_token(store, admin, client)
        res = _start(client, token, scope="org")
    assert res.status_code == 403


def test_callback_rejects_state_from_another_user(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member_token = _member_token(store, admin, client)
        state = _state_from(_start(client, admin["token"]))
        res = client.get(
            f"/api/connectors/google/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {member_token}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_callback_rejects_expired_state(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    expired = encrypt_secret(
        json.dumps(
            {
                "uid": admin["user_id"],
                "org": admin["org_id"],
                "scope": "user",
                "verifier": "v",
                "exp": int(time.time()) - 1,
                "nonce": "n",
            }
        )
    )
    with TestClient(app) as client:
        res = client.get(
            f"/api/connectors/google/callback?code=c&state={quote(expired, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400
    assert "expired" in res.json()["error"]


def test_callback_rejects_forged_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get(
            "/api/connectors/google/callback?code=c&state=not-a-real-state",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


@respx.mock
def test_callback_stores_encrypted_account_and_hides_token(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.SECRET",
                "refresh_token": "1//REFRESH-SECRET",
                "expires_in": 3599,
                "scope": "gmail.readonly",
            },
        )
    )
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(200, json={"emailAddress": "ana@example.test"})
    )
    with TestClient(app) as client:
        state = _state_from(_start(client, admin["token"]))
        res = client.get(
            f"/api/connectors/google/callback?code=auth-code&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
        listing = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()
    assert res.status_code == 302
    assert "REFRESH-SECRET" not in json.dumps(listing)
    assert "ya29.SECRET" not in json.dumps(listing)
    assert listing["accounts"][0]["externalAccount"] == "ana@example.test"
    with engine.connect() as conn:
        raw = conn.execute(connector_account.select()).mappings().one()
    assert "REFRESH-SECRET" not in raw["encrypted_refresh_token"]
    assert decrypt_secret(raw["encrypted_refresh_token"]) == "1//REFRESH-SECRET"


@respx.mock
def test_callback_without_refresh_token_is_rejected(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "expires_in": 3599})
    )
    with TestClient(app) as client:
        state = _state_from(_start(client, admin["token"]))
        res = client.get(
            f"/api/connectors/google/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400
    assert "refresh token" in res.json()["error"]


@respx.mock
def test_member_cannot_revoke_another_members_account(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_owner",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="google_workspace",
                external_account="owner@conn.test",
                scopes="s",
                encrypted_refresh_token=encrypt_secret("1//R"),
                status="active",
                created_at="t",
                updated_at="t",
            )
        )
    with TestClient(app) as client:
        member_token = _member_token(store, admin, client)
        res = client.post(
            "/api/connectors/accounts/acct_owner/revoke",
            headers={"Authorization": f"Bearer {member_token}"},
        )
    assert res.status_code == 404


@respx.mock
def test_owner_revoke_calls_google_and_clears_token(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    route = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_mine",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="google_workspace",
                external_account="owner@conn.test",
                scopes="s",
                encrypted_refresh_token=encrypt_secret("1//R-REVOKE"),
                status="active",
                created_at="t",
                updated_at="t",
            )
        )
    with TestClient(app) as client:
        res = client.post(
            "/api/connectors/accounts/acct_mine/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 200
    assert route.calls.last.request.content.decode().endswith("token=1%2F%2FR-REVOKE")
    with engine.connect() as conn:
        row = conn.execute(connector_account.select()).mappings().one()
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""


@respx.mock
def test_revoke_keeps_account_active_when_google_fails(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(REVOKE_URL).mock(return_value=httpx.Response(503))
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_keep",
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider="google_workspace",
                external_account="owner@conn.test",
                scopes="s",
                encrypted_refresh_token=encrypt_secret("1//R"),
                status="active",
                created_at="t",
                updated_at="t",
            )
        )
    with TestClient(app) as client:
        res = client.post(
            "/api/connectors/accounts/acct_keep/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 502
    with engine.connect() as conn:
        row = conn.execute(connector_account.select()).mappings().one()
    assert row["status"] == "active"


def test_unauthenticated_request_is_rejected(tmp_path) -> None:
    _, app, _admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get("/api/connectors/accounts")
    assert res.status_code == 401
