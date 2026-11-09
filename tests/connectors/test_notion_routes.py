"""Notion connector routes: sealed state, token storage, workspace listing and local revoke."""

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
from hoja_server.connectors_api import NOTION_PROVIDER, connector_binding_for
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret, encrypt_secret
from hoja_server.store import Store

TOKEN_URL = "https://api.notion.com/v1/oauth/token"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_NOTION_CLIENT_ID", "413d872b-594c-81d1-98c2-0037de6b4ad5")
    monkeypatch.setenv("HOJA_NOTION_CLIENT_SECRET", "secret_route_test_value")
    monkeypatch.setenv(
        "HOJA_NOTION_REDIRECT_URI", "https://hoja.example.test/api/connectors/notion/callback"
    )


def _setup(tmp_path):
    db_path = tmp_path / "notion.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Notion Org",
        org_url_key="notion-org",
        user_email="owner@notion.test",
        token="owner-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _member_token(store, admin, client):
    from tests.test_member_management import _add_member

    member_id = _add_member(client, admin["token"], "member@notion.test")
    _, key = store.create_api_key(admin["org_id"], member_id, "member", "read_write", None)
    return key


def _start(client, token):
    return client.get(
        "/api/connectors/notion/start",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )


def _state_from(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _token_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "secret_notion_access_token",
            "token_type": "bearer",
            "refresh_token": None,
            "bot_id": "bot-1",
            "workspace_id": "workspace-1",
            "workspace_name": "Luis Alejandro Rincon's Space",
            "workspace_icon": None,
            "owner": {"type": "user", "user": {"id": "user-1"}},
        },
    )


def _account_rows(engine, org: str) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(connector_account).where(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == NOTION_PROVIDER,
                )
            )
            .mappings()
            .all()
        )


def test_start_is_user_scoped_and_hides_the_secret(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = _start(client, admin["token"])
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["owner"] == ["user"]
    assert "scope" not in query
    assert "code_challenge" not in query
    assert "secret_route_test_value" not in res.headers["location"]


def test_org_scope_is_refused(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get(
            "/api/connectors/notion/start?scope=org",
            headers={"Authorization": f"Bearer {admin['token']}"},
            follow_redirects=False,
        )
    assert res.status_code == 400


def test_callback_stores_the_sealed_token_and_lists_the_workspace(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        state = _state_from(_start(client, admin["token"]))
        with respx.mock:
            respx.post(TOKEN_URL).mock(return_value=_token_response())
            res = client.get(
                f"/api/connectors/notion/callback?code=c&state={quote(state, safe='')}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
        assert res.status_code == 302
        assert res.headers["location"] == "/?connector=notion-connected"
        listing = client.get(
            "/api/connectors/accounts",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
        payload = listing.json()["accounts"]
    assert [a["provider"] for a in payload] == ["notion"]
    assert payload[0]["externalAccount"] == "Luis Alejandro Rincon's Space"
    assert payload[0]["scopes"] == ["read_content"]
    assert payload[0]["scope"] == "user"
    rows = _account_rows(engine, admin["org_id"])
    assert len(rows) == 1
    assert rows[0]["encrypted_refresh_token"] != "secret_notion_access_token"
    assert decrypt_secret(rows[0]["encrypted_refresh_token"]) == "secret_notion_access_token"


def test_callback_rejects_forged_state(tmp_path) -> None:
    _, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.get(
            "/api/connectors/notion/callback?code=c&state=not-a-real-state",
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
            f"/api/connectors/notion/callback?code=c&state={quote(state, safe='')}",
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
            f"/api/connectors/notion/callback?code=c&state={quote(expired, safe='')}",
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
                f"/api/connectors/notion/callback?code=c&state={quote(state, safe='')}",
                headers={"Authorization": f"Bearer {admin['token']}"},
                follow_redirects=False,
            )
        second = client.get(
            f"/api/connectors/notion/callback?code=c&state={quote(state, safe='')}",
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
                        f"/api/connectors/notion/callback?code=c&state={quote(state, safe='')}",
                        headers={"Authorization": f"Bearer {admin['token']}"},
                        follow_redirects=False,
                    ).status_code
                    == 302
                )
    assert len(_account_rows(engine, admin["org_id"])) == 1


def test_revoke_discards_the_token_and_says_notion_was_not_called(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=uuid.uuid4().hex,
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider=NOTION_PROVIDER,
                external_account="Space",
                scopes="read_content",
                encrypted_refresh_token=encrypt_secret("secret_notion_access_token"),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    account_id = _account_rows(engine, admin["org_id"])[0]["id"]
    with TestClient(app) as client:
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert res.status_code == 200
    assert res.json()["revoked"] is True
    assert res.json()["providerRevoked"] is False
    assert "Remove Hoja in Notion" in res.json()["hint"]
    row = _account_rows(engine, admin["org_id"])[0]
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""


def test_config_reports_notion_separately_from_agent_readiness(tmp_path) -> None:
    _, app, _admin = _setup(tmp_path)
    with TestClient(app) as client:
        body = client.get("/api/connectors/config").json()
    assert body["notion"] is True
    assert body["notionAgent"] is False


def test_a_notion_only_account_alone_binds_the_agent_toolset(tmp_path, monkeypatch) -> None:
    """Without this, a user who connects only Notion would get no toolset at all."""
    engine, _app, admin = _setup(tmp_path)
    monkeypatch.setenv("HOJA_CONNECTOR_MCP_URL", "https://connector.example/mcp")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", "sign-key")
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=uuid.uuid4().hex,
                organization_id=admin["org_id"],
                owner_user_id=admin["user_id"],
                provider=NOTION_PROVIDER,
                external_account="Space",
                scopes="read_content",
                encrypted_refresh_token=encrypt_secret("secret_notion_access_token"),
                status="active",
                created_at="2026-11-09T00:00:00+00:00",
                updated_at="2026-11-09T00:00:00+00:00",
            )
        )
    binding = connector_binding_for(engine, admin["org_id"], admin["user_id"])
    assert binding is not None
    assert binding.url == "https://connector.example/mcp"
    assert binding.principal
