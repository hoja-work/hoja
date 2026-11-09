"""Slack connector routes: sealed state, sealed user token, workspace listing and revocation."""

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
from hoja_server.connectors_api import SLACK_PROVIDER, connector_binding_for
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret
from hoja_server.store import Store

TOKEN_URL = "https://slack.com/api/oauth.v2.access"
REVOKE_URL = "https://slack.com/api/auth.revoke"
GRANTED_SCOPES = (
    "channels:history,channels:read,groups:history,groups:read,search:read,users:read"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_SLACK_CLIENT_ID", "12530628441030.12542913043347")
    monkeypatch.setenv("HOJA_SLACK_CLIENT_SECRET", "secret_route_test_value")
    monkeypatch.setenv(
        "HOJA_SLACK_REDIRECT_URI", "https://hoja.example.test/api/connectors/slack/callback"
    )


def _setup(tmp_path):
    db_path = tmp_path / "slack.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Slack Org",
        org_url_key="slack-org",
        user_email="owner@slack.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _member_token(store, admin, client):
    from tests.test_member_management import _add_member

    member_id = _add_member(client, admin["token"], "member@slack.test")
    _, key = store.create_api_key(admin["org_id"], member_id, "member", "read_write", None)
    return key


def _start(client, token):
    return client.get(
        "/api/connectors/slack/start",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )


def _state_from(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _token_response(**overrides) -> httpx.Response:
    body = {
        "ok": True,
        "app_id": "A0CFYSV19A7",
        "authed_user": {
            "id": "U0CFLJGCZ0W",
            "scope": GRANTED_SCOPES,
            "access_token": "xoxp-fixture",
            "token_type": "user",
        },
        "team": {"id": "T0CFLJGCZ0W", "name": "Hoja"},
        "enterprise": None,
        "is_enterprise_install": False,
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _account_rows(engine, org: str) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(connector_account).where(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == SLACK_PROVIDER,
                )
            )
            .mappings()
            .all()
        )


def test_start_asks_for_user_scopes_and_hides_the_secret(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 302
    location = res.headers["location"]
    query = parse_qs(urlparse(location).query, keep_blank_values=True)
    assert set(query["user_scope"][0].split(",")) == set(GRANTED_SCOPES.split(","))
    assert query["scope"] == [""]
    assert "secret_route_test_value" not in location
    assert query["client_id"] == ["12530628441030.12542913043347"]


def test_org_scope_is_refused(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get(
            "/api/connectors/slack/start?scope=org",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_stores_the_sealed_user_token_and_lists_the_workspace(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        state = _state_from(_start(client, admin["token"]))
        with respx.mock:
            token_route = respx.post(TOKEN_URL).mock(return_value=_token_response())
            res = client.get(
                f"/api/connectors/slack/callback?code=c&state={quote(state, safe='')}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
        assert res.status_code == 302
        assert res.headers["location"] == "/?connector=slack-connected"
        listing = client.get(
            "/api/connectors/accounts",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
        payload = listing.json()["accounts"]
    # The client secret travels in HTTP Basic, not in the form body.
    assert token_route.calls.last.request.headers["Authorization"].startswith("Basic ")
    assert b"client_secret" not in token_route.calls.last.request.content
    assert [a["provider"] for a in payload] == ["slack"]
    assert payload[0]["externalAccount"] == "Hoja"
    assert payload[0]["scopes"] == GRANTED_SCOPES.split(",")
    assert payload[0]["scope"] == "user"
    rows = _account_rows(engine, admin["org_id"])
    assert len(rows) == 1
    assert rows[0]["encrypted_refresh_token"] != "xoxp-fixture"
    assert decrypt_secret(rows[0]["encrypted_refresh_token"]) == "xoxp-fixture"


def test_callback_refuses_a_bot_token_grant(tmp_path) -> None:
    """If the app ever gains bot scopes, the grant must not be stored as a user connection."""
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        state = _state_from(_start(client, admin["token"]))
        with respx.mock:
            respx.post(TOKEN_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True, "access_token": "xoxb-bot", "token_type": "bot"},
                )
            )
            res = client.get(
                f"/api/connectors/slack/callback?code=c&state={quote(state, safe='')}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
    assert res.status_code == 502
    assert _account_rows(engine, admin["org_id"]) == []


def test_callback_rejects_an_access_denied_redirect(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get(
            "/api/connectors/slack/callback?error=access_denied&state=x",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_rejects_forged_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get(
            "/api/connectors/slack/callback?code=c&state=not-a-real-state",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_rejects_state_from_another_user(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member_token = _member_token(store, admin, client)
        state = _state_from(_start(client, admin["token"]))
        res = client.get(
            f"/api/connectors/slack/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {member_token}"},
            follow_redirects=False,
        )
    assert res.status_code == 403


def test_callback_rejects_an_expired_state(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    expired = encrypt_secret(
        json.dumps(
            {
                "uid": admin["user_id"],
                "org": admin["org_id"],
                "exp": int(time.time()) - 1,
                "nonce": "n",
            }
        )
    )
    with TestClient(app) as client:
        res = client.get(
            f"/api/connectors/slack/callback?code=c&state={quote(expired, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400
    assert "expired" in res.json()["error"]


def test_callback_state_is_single_use(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        state = _state_from(_start(client, admin["token"]))
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            first = client.get(
                f"/api/connectors/slack/callback?code=c&state={quote(state, safe='')}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
        second = client.get(
            f"/api/connectors/slack/callback?code=c&state={quote(state, safe='')}",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert first.status_code == 302
    assert second.status_code == 400
    assert "already used" in second.json()["error"]
    assert len(_account_rows(engine, admin["org_id"])) == 1


def test_reconnect_replaces_the_single_row(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        for _ in range(2):
            state = _state_from(_start(client, admin["token"]))
            with respx.mock:
                respx.post(TOKEN_URL).mock(return_value=_token_response())
                assert (
                    client.get(
                        f"/api/connectors/slack/callback?code=c&state={quote(state, safe='')}",
                        headers={"Authorization": f"Bearer {admin['token']}"},
                        follow_redirects=False,
                    ).status_code
                    == 302
                )
    assert len(_account_rows(engine, admin["org_id"])) == 1


def test_revoke_calls_slack_and_clears_the_stored_token(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=uuid.uuid4().hex,
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider=SLACK_PROVIDER,
                external_account="Hoja",
                scopes=GRANTED_SCOPES,
                encrypted_refresh_token=encrypt_secret("xoxp-fixture"),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    account_id = _account_rows(engine, admin["org_id"])[0]["id"]
    with TestClient(app) as client, respx.mock:
        route = respx.post(REVOKE_URL).mock(
            return_value=httpx.Response(200, json={"ok": True, "revoked": True})
        )
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 200
    assert res.json() == {"revoked": True, "providerRevoked": True, "hint": None}
    assert route.calls.last.request.headers["Authorization"] == "Bearer xoxp-fixture"
    row = _account_rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""


def test_revoke_reports_a_slack_failure_instead_of_claiming_success(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=uuid.uuid4().hex,
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider=SLACK_PROVIDER,
                external_account="Hoja",
                scopes=GRANTED_SCOPES,
                encrypted_refresh_token=encrypt_secret("xoxp-fixture"),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    account_id = _account_rows(engine, admin["org_id"])[0]["id"]
    with TestClient(app) as client, respx.mock:
        respx.post(REVOKE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "fatal_error"})
        )
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 502
    assert _account_rows(engine, admin["org_id"])[0]["status"] == "active"


def test_config_reports_slack_separately_from_agent_readiness(tmp_path) -> None:
    _, app, _admin = _setup(tmp_path)
    with TestClient(app) as client:
        body = client.get("/api/connectors/config").json()
    assert body["slack"] is True
    assert body["slackAgent"] is False


def test_a_slack_only_account_alone_binds_the_agent_toolset(tmp_path, monkeypatch) -> None:
    """Without this, a user who connects only Slack would get no toolset at all."""
    engine, _app, admin = _setup(tmp_path)
    monkeypatch.setenv("HOJA_CONNECTOR_MCP_URL", "https://connector.example/mcp")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", "sign-key")
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=uuid.uuid4().hex,
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider=SLACK_PROVIDER,
                external_account="Hoja",
                scopes=GRANTED_SCOPES,
                encrypted_refresh_token=encrypt_secret("xoxp-fixture"),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    binding = connector_binding_for(engine, admin["org_id"], admin["user_id"])
    assert binding is not None
    assert binding.url == "https://connector.example/mcp"
    assert binding.principal
