"""CORS is off by default and opt-in per exact origin for browser clients."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.cors import parse_cors_origins
from hoja_server.db import make_engine, migrate, seed_tenant

ORIGIN = "http://127.0.0.1:8082"


def _app(tmp_path):
    db_path = tmp_path / "cors.db"
    engine = make_engine(db_path)
    migrate(engine)
    seed_tenant(
        engine,
        org_name="CORS Org",
        org_url_key="cors-org",
        user_email="owner@cors.test",
        token="owner-token",
        demo_issues=False,
    )
    return create_app(str(db_path), open_mode=False)


def test_no_origins_means_no_cors(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HOJA_CORS_ORIGINS", raising=False)
    app = _app(tmp_path)
    with TestClient(app) as client:
        res = client.get("/health", headers={"Origin": ORIGIN})
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_preflight_from_a_configured_origin_is_allowed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOJA_CORS_ORIGINS", ORIGIN)
    app = _app(tmp_path)
    with TestClient(app) as client:
        res = client.options(
            "/sync/bootstrap",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,if-none-match",
            },
        )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in res.headers["access-control-allow-headers"].lower()
    assert "GET" in res.headers["access-control-allow-methods"]
    # Credentials are carried as a bearer header, never as a cookie.
    assert "access-control-allow-credentials" not in res.headers


def test_actual_response_exposes_etag_to_the_configured_origin(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOJA_CORS_ORIGINS", ORIGIN)
    app = _app(tmp_path)
    with TestClient(app) as client:
        res = client.get("/health", headers={"Origin": ORIGIN})
    assert res.headers["access-control-allow-origin"] == ORIGIN
    assert "etag" in res.headers["access-control-expose-headers"].lower()


def test_auth_failure_still_carries_cors_headers(monkeypatch, tmp_path) -> None:
    """A browser must be able to read the 401 body so it can say why it refused."""
    monkeypatch.setenv("HOJA_CORS_ORIGINS", ORIGIN)
    app = _app(tmp_path)
    with TestClient(app) as client:
        res = client.get("/sync/bootstrap", headers={"Origin": ORIGIN})
    assert res.status_code == 401
    assert res.headers["access-control-allow-origin"] == ORIGIN


def test_an_unconfigured_origin_gets_no_header(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOJA_CORS_ORIGINS", ORIGIN)
    app = _app(tmp_path)
    with TestClient(app) as client:
        res = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in res.headers


def test_origins_are_parsed_and_deduplicated() -> None:
    assert parse_cors_origins(
        " http://127.0.0.1:8082/ ,https://preview.example.com,http://127.0.0.1:8082"
    ) == [
        "http://127.0.0.1:8082",
        "https://preview.example.com",
    ]


@pytest.mark.parametrize(
    "raw", ["*", "preview.example.com", "https://example.com/app", "https://example.com?x=1"]
)
def test_malformed_entries_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cors_origins(raw)
