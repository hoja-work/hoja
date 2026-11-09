"""GitHub routes: a per-tenant allow-listed callback, a session-bound single-use state, and revoke."""

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
from hoja_server.connectors_api import GITHUB_PROVIDER
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
HOST = "https://testserver"
CALLBACK = f"{HOST}/api/connectors/github/callback"
GRANT_URL = "https://api.github.com/applications/Iv23li4RW9Mh8RHVfCGi/grant"
CLIENT_ID = "Iv23li4RW9Mh8RHVfCGi"
SLUG = "github-org"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_GITHUB_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("HOJA_GITHUB_CLIENT_SECRET", "secret_route_test_value")
    monkeypatch.setenv("HOJA_GITHUB_REDIRECT_URI", CALLBACK)
    monkeypatch.setenv("HOJA_GITHUB_APP_SLUG", "hoja-connector")
    monkeypatch.delenv("HOJA_GITHUB_REDIRECT_URIS", raising=False)
    monkeypatch.delenv("HOJA_CONNECTOR_SIGNING_KEY", raising=False)


def _setup(tmp_path):
    db_path = tmp_path / "github.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="GitHub Org",
        org_url_key=SLUG,
        user_email="owner@github.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _client(app, base=HOST):
    return TestClient(app, base_url=base)


def _start(client, token, extra=""):
    return client.get(
        f"/api/connectors/github/start{extra}",
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
        "provider": GITHUB_PROVIDER,
        "origin": HOST,
        "exp": int(time.time()) + 600,
        "nonce": uuid.uuid4().hex,
    }
    body.update(pending)
    return encrypt_secret(json.dumps(body))


def _token_response(**overrides) -> httpx.Response:
    body = {
        "access_token": "ghu_access_fixture",
        "token_type": "bearer",
        "refresh_token": "ghr_refresh_fixture",
        "expires_in": 28800,
        "refresh_token_expires_in": 15778476,
        "scope": "",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _user() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "login": "rinadelph",
            "id": 68679193,
            "name": "Rinadelph",
            "email": "r@hoja.work",
            "html_url": "https://github.com/rinadelph",
            "type": "User",
        },
    )


def _rows(engine, org: str) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(connector_account).where(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == GITHUB_PROVIDER,
                )
            )
            .mappings()
            .all()
        )


# --- capability flags -----------------------------------------------------------------------


def test_config_reports_github_and_its_install_page(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/config")
    body = res.json()
    assert body["github"] is True
    # A private repository is readable only where the app is installed, so the page is surfaced.
    assert body["githubInstallUrl"] == "https://github.com/apps/hoja-connector/installations/new"
    assert "secret_route_test_value" not in res.text


def test_config_reports_github_off_on_an_unconfigured_deployment(tmp_path, monkeypatch) -> None:
    _, app, _ = _setup(tmp_path)
    monkeypatch.delenv("HOJA_GITHUB_CLIENT_SECRET", raising=False)
    with _client(app) as client:
        body = client.get("/api/connectors/config").json()
    assert body["github"] is False
    assert body["githubAgent"] is False
    assert body["githubInstallUrl"] is None


# --- start ----------------------------------------------------------------------------------


def test_start_requires_a_session(tmp_path) -> None:
    _, app, _ = _setup(tmp_path)
    with _client(app) as client:
        res = client.get("/api/connectors/github/start", follow_redirects=False)
    assert res.status_code == 401


def test_start_refuses_an_organization_scope(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = _start(client, admin["token"], "?scope=org")
    assert res.status_code == 400
    assert "personal" in res.json()["error"].lower()


def test_start_redirects_to_github_with_the_tenant_callback_and_no_scope(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [CALLBACK]
    assert query["allow_signup"] == ["false"]
    # A GitHub App carries its permissions on the registration; a scope would be ignored.
    assert "scope" not in query
    assert query["state"][0]


def test_start_refuses_a_host_that_is_not_in_the_callback_allow_list(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app, base="https://ghost.hoja.work") as client:
        res = _start(client, admin["token"])
    assert res.status_code == 400
    assert "callback" in (res.json().get("hint") or "").lower()


def test_an_unconfigured_deployment_reports_503(tmp_path, monkeypatch) -> None:
    _, app, admin = _setup(tmp_path)
    monkeypatch.delenv("HOJA_GITHUB_CLIENT_SECRET", raising=False)
    with _client(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 503
    assert "HOJA_GITHUB_CLIENT_SECRET" in res.json()["error"]


# --- callback ------------------------------------------------------------------------------


def test_callback_requires_code_and_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = client.get(
            "/api/connectors/github/callback",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_refuses_a_declined_consent(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with _client(app) as client:
        res = client.get(
            "/api/connectors/github/callback?error=access_denied&state=x",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_refuses_an_expired_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal({"uid": admin["user_id"], "org": admin["org_id"], "exp": int(time.time()) - 5})
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/github/callback?code=c&state={quote(sealed, safe='')}",
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
            f"/api/connectors/github/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_callback_refuses_a_state_from_another_workspace_host(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal(
        {"uid": admin["user_id"], "org": admin["org_id"], "origin": "https://clover.hoja.work"}
    )
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/github/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_callback_refuses_a_state_for_another_provider(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    sealed = _seal({"uid": admin["user_id"], "org": admin["org_id"], "provider": "zoom"})
    with _client(app) as client:
        res = client.get(
            f"/api/connectors/github/callback?code=c&state={quote(sealed, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


@respx.mock
def test_the_happy_path_stores_a_sealed_grant_and_githubs_user_id(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    exchange = respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USER_URL).mock(return_value=_user())
    with _client(app) as client:
        start = _start(client, admin["token"])
        state = _state_from(start)
        res = client.get(
            f"/api/connectors/github/callback?code=code-1&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 302
    assert res.headers["location"].endswith("connector=github-connected")
    body = exchange.calls[0].request.content.decode()
    assert "redirect_uri=https%3A%2F%2Ftestserver%2Fapi%2Fconnectors%2Fgithub%2Fcallback" in body

    rows = _rows(engine, admin["org_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["external_account"] == "rinadelph"
    assert row["external_id"] == "68679193"
    assert row["owner_user_id"] == admin["user_id"]
    assert row["scopes"] == ""
    assert decrypt_secret(row["encrypted_refresh_token"]) == "ghr_refresh_fixture"
    assert decrypt_secret(row["encrypted_access_token"]) == "ghu_access_fixture"
    assert int(row["access_token_expires_at"]) > time.time() + 28_000


@respx.mock
def test_reconnecting_the_same_account_replaces_its_grant(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USER_URL).mock(return_value=_user())
    with _client(app) as client:
        for _ in range(2):
            state = _state_from(_start(client, admin["token"]))
            res = client.get(
                f"/api/connectors/github/callback?code=c&state={quote(state, safe='')}",
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
    respx.get(USER_URL).mock(return_value=_user())
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        first = client.get(
            f"/api/connectors/github/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
        second = client.get(
            f"/api/connectors/github/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert first.status_code == 302
    assert second.status_code == 400
    assert "already used" in second.json()["error"].lower()


@respx.mock
def test_a_rejected_code_is_reported_without_storing_a_row(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"error": "bad_verification_code"})
    )
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        res = client.get(
            f"/api/connectors/github/callback?code=spent&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400
    assert _rows(engine, admin["org_id"]) == []


# --- list and revoke ------------------------------------------------------------------------


@respx.mock
def test_the_account_list_includes_github_and_revoke_ends_the_grant_at_github(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USER_URL).mock(return_value=_user())
    grant = respx.delete(GRANT_URL).mock(return_value=httpx.Response(204))
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        client.get(
            f"/api/connectors/github/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
        accounts = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"]
        account_id = accounts[0]["id"]
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )

    assert accounts[0]["provider"] == "github"
    assert accounts[0]["externalAccount"] == "rinadelph"
    assert res.status_code == 200
    assert res.json() == {"revoked": True, "providerRevoked": True, "hint": None}
    # GitHub's endpoint takes the access token and ends that token's whole refresh chain.
    request = grant.calls[0].request
    assert json.loads(request.content) == {"access_token": "ghu_access_fixture"}
    assert request.headers["authorization"].startswith("Basic ")
    row = _rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""
    assert row["encrypted_access_token"] == ""


@respx.mock
def test_revoke_reports_when_github_could_not_confirm(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=_token_response())
    respx.get(USER_URL).mock(return_value=_user())
    respx.delete(GRANT_URL).mock(return_value=httpx.Response(503))
    with _client(app) as client:
        state = _state_from(_start(client, admin["token"]))
        client.get(
            f"/api/connectors/github/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
        account_id = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"][0]["id"]
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 502
    # The local grant is kept when GitHub could not be told, so the user can retry.
    assert _rows(engine, admin["org_id"])[0]["status"] == "active"
