"""Grafana routes: live validation, sealed storage, list, reconnect and disconnect.

The resolver and Grafana itself are stubbed, so the suite is offline; the assertions are about what
Hoja stores and returns, and about the requests it makes.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import connector_account, make_engine, migrate, seed_tenant
from hoja_server.secrets_box import decrypt_secret

PUBLIC = "93.184.216.34"
URL = "https://grafana.example.com"
TOKEN = "glsa_abcdefghijklmnopqrstuvwxyz0123456789AB"
USER_ENDPOINT = f"https://{PUBLIC}/api/user"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.delenv("HOJA_CONNECTOR_SIGNING_KEY", raising=False)
    monkeypatch.setattr("grafana.ssrf._system_resolver", lambda host, port: [PUBLIC])


def _setup(tmp_path):
    db_path = tmp_path / "grafana.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Grafana Org",
        org_url_key="grafana-org",
        user_email="owner@grafana.test",
        token="owner-token",
        demo_issues=False,
    )
    return engine, create_app(str(db_path), open_mode=False), admin


def _connect(client, token: str, url: str = URL, secret: str = TOKEN):
    return client.post(
        "/api/connectors/grafana/connect",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": url, "token": secret},
    )


@respx.mock
def test_connect_validates_live_then_stores_a_sealed_token_and_the_origin(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    route = respx.get(USER_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={"login": "sa-1-hoja-connector", "uid": "service-account:20", "isGrafanaAdmin": False},
        )
    )
    with TestClient(app) as client:
        res = _connect(client, admin["token"])
        assert res.status_code == 200, res.text
        assert res.json()["instance"] == URL
        listed = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"]

    assert route.call_count == 1
    # The request went to the pinned address with the customer's host, never the hostname.
    assert route.calls[0].request.url.host == PUBLIC
    assert route.calls[0].request.headers["host"] == "grafana.example.com"

    row = listed[0]
    assert row["provider"] == "grafana"
    assert row["externalAccount"] == "sa-1-hoja-connector"
    assert row["externalUrl"] == URL
    # Nothing secret is in the API response.
    assert TOKEN not in str(listed)

    with engine.connect() as conn:
        stored = conn.execute(select(connector_account)).mappings().one()
    assert stored["encrypted_refresh_token"] != TOKEN
    assert decrypt_secret(stored["encrypted_refresh_token"]) == TOKEN
    assert stored["external_url"] == URL


@respx.mock
def test_validate_checks_without_storing_anything(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.get(USER_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"login": "sa-1-hoja-connector"})
    )
    with TestClient(app) as client:
        res = client.post(
            "/api/connectors/grafana/validate",
            headers={"Authorization": f"Bearer {admin['token']}"},
            json={"url": URL, "token": TOKEN},
        )
    assert res.status_code == 200
    assert res.json() == {
        "valid": True,
        "instance": URL,
        "account": "sa-1-hoja-connector",
        "isGrafanaAdmin": False,
    }
    with engine.connect() as conn:
        assert conn.execute(select(connector_account)).first() is None


@respx.mock
def test_a_plain_http_url_is_refused_before_grafana_is_contacted(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    route = respx.get(USER_ENDPOINT).mock(return_value=httpx.Response(200, json={}))
    with TestClient(app) as client:
        res = _connect(client, admin["token"], url="http://grafana.example.com")
    assert res.status_code == 400
    assert "https" in res.json()["error"]
    assert route.call_count == 0


@respx.mock
def test_a_private_address_is_refused(tmp_path, monkeypatch) -> None:
    _engine, app, admin = _setup(tmp_path)
    monkeypatch.setattr("grafana.ssrf._system_resolver", lambda host, port: ["10.0.0.5"])
    with TestClient(app) as client:
        res = _connect(client, admin["token"])
    assert res.status_code == 400
    assert "private" in res.json()["error"].lower()


@respx.mock
def test_a_rejected_token_is_not_stored(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.get(USER_ENDPOINT).mock(
        return_value=httpx.Response(401, json={"message": "Invalid API key"})
    )
    with TestClient(app) as client:
        res = _connect(client, admin["token"])
    assert res.status_code == 400
    assert "reconnect" in res.json()["hint"].lower()
    with engine.connect() as conn:
        assert conn.execute(select(connector_account)).first() is None


@respx.mock
def test_reconnecting_the_same_instance_replaces_the_row(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.get(USER_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"login": "sa-1-hoja-connector"})
    )
    with TestClient(app) as client:
        assert _connect(client, admin["token"]).status_code == 200
        assert _connect(client, admin["token"], secret=TOKEN[:-1] + "Z").status_code == 200
    with engine.connect() as conn:
        rows = conn.execute(select(connector_account)).mappings().all()
    assert len(rows) == 1
    assert decrypt_secret(rows[0]["encrypted_refresh_token"]).endswith("Z")


@respx.mock
def test_disconnect_clears_the_sealed_token_and_tells_the_user_to_revoke_in_grafana(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    respx.get(USER_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"login": "sa-1-hoja-connector"})
    )
    with TestClient(app) as client:
        _connect(client, admin["token"])
        account_id = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"][0]["id"]
        res = client.post(
            f"/api/connectors/accounts/{account_id}/revoke",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    body = res.json()
    assert body["revoked"] is True
    assert body["providerRevoked"] is False
    assert "Delete the service-account token in Grafana" in body["hint"]
    with engine.connect() as conn:
        row = conn.execute(select(connector_account)).mappings().one()
    assert row["status"] == "revoked"
    assert row["encrypted_refresh_token"] == ""


def test_config_reports_grafana_and_needs_only_the_sealing_key(tmp_path, monkeypatch) -> None:
    _engine, app, _admin = _setup(tmp_path)
    with TestClient(app) as client:
        body = client.get("/api/connectors/config").json()
    assert body["grafana"] is True
    assert body["grafanaAgent"] is False
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", "signing")
    with TestClient(app) as client:
        assert client.get("/api/connectors/config").json()["grafanaAgent"] is True


def test_another_users_account_is_invisible(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="other",
                organization_id=admin["org_id"],
                owner_user_id="someone-else",
                provider="grafana",
                external_account="sa-other",
                scopes="viewer",
                encrypted_refresh_token="sealed",
                external_url=URL,
                status="active",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
    with TestClient(app) as client:
        accounts = client.get(
            "/api/connectors/accounts", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()["accounts"]
    assert accounts == []
