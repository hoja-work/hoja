"""Persistent, cross-worker rate limits backed by the rate_limit_event table."""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("starlette")
pytest.importorskip("mcp")

from sqlalchemy import select
from starlette.testclient import TestClient

from cliniar_server import db, mcp_oauth
from cliniar_server.app import _FailureLimiter, create_app
from cliniar_server.db import make_engine, migrate, rate_limit_event, seed_tenant
from cliniar_server.store import Store

EMAIL = "ratelimit-persist@sec.test"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _setup(tmp_path):
    db_path = tmp_path / "rl.db"
    engine = make_engine(db_path)
    migrate(engine)
    seed_tenant(engine, org_name="RL Org", org_url_key="rl-org", team_key="ENG",
                user_name="Admin", user_email=EMAIL, token="rl-admin-token", demo_issues=False)
    return db_path, engine


def _client(db_path) -> TestClient:
    return TestClient(create_app(str(db_path), open_mode=False), base_url="http://localhost")


def _bad_login(client) -> int:
    return client.post("/auth/login", json={"email": EMAIL, "password": "wrong wrong wrong"}).status_code


def _rows(engine, bucket=None) -> list:
    stmt = select(rate_limit_event)
    if bucket is not None:
        stmt = stmt.where(rate_limit_event.c.bucket == bucket)
    with engine.connect() as conn:
        return conn.execute(stmt).all()


def test_login_limit_survives_app_restart(tmp_path) -> None:
    db_path, _engine = _setup(tmp_path)
    first = _client(db_path)
    assert [_bad_login(first) for _ in range(10)] == [401] * 10

    restarted = _client(db_path)  # simulated restart: fresh app, same DB file
    assert _bad_login(restarted) == 429


def test_two_app_instances_share_failure_count(tmp_path) -> None:
    db_path, _engine = _setup(tmp_path)
    worker_a = _client(db_path)
    worker_b = _client(db_path)
    for _ in range(5):
        assert _bad_login(worker_a) == 401
    for _ in range(5):
        assert _bad_login(worker_b) == 401
    assert _bad_login(worker_a) == 429
    assert _bad_login(worker_b) == 429


def test_expired_rows_do_not_count(tmp_path) -> None:
    engine = make_engine(tmp_path / "expired.db")
    migrate(engine)
    limiter = _FailureLimiter(engine, "login_failure", limit=2, window_seconds=60)
    old = time.time() - 3600
    key_hash = db.rate_limit_key_hash("email:old@x")
    with engine.begin() as conn:
        conn.execute(rate_limit_event.insert(), [
            {"bucket": "login_failure", "key_hash": key_hash, "created_at": old},
            {"bucket": "login_failure", "key_hash": key_hash, "created_at": old},
        ])
    assert limiter.blocked(["email:old@x"]) is False
    limiter.record(["email:old@x"])
    assert limiter.blocked(["email:old@x"]) is False
    assert len(_rows(engine, "login_failure")) == 1  # expired rows pruned on record


def test_keys_are_stored_hashed(tmp_path) -> None:
    db_path, engine = _setup(tmp_path)
    _bad_login(_client(db_path))
    rows = _rows(engine, "login_failure")
    assert rows
    for row in rows:
        assert len(row.key_hash) == 64
        assert int(row.key_hash, 16) >= 0
        assert EMAIL not in row.key_hash
        assert "ip:" not in row.key_hash and "email:" not in row.key_hash
    dump = repr(_rows(engine))
    assert EMAIL not in dump
    assert "testclient" not in dump


def test_registration_limit_persists_across_provider_instances(tmp_path, monkeypatch) -> None:
    from mcp.server.auth.provider import RegistrationError
    from mcp.shared.auth import OAuthClientInformationFull

    engine = make_engine(tmp_path / "dcr.db")
    migrate(engine)
    monkeypatch.setattr(mcp_oauth, "MAX_REGISTRATIONS_PER_HOUR", 2)

    def _register(client_id):
        provider = mcp_oauth.HojaOAuthProvider(engine, Store(engine), True)
        return asyncio.run(provider.register_client(
            OAuthClientInformationFull(client_id=client_id, redirect_uris=["https://client.test/cb"])))

    _register("p1")
    _register("p2")
    with pytest.raises(RegistrationError):
        _register("p3")
    assert len(_rows(engine, "oauth_dcr")) == 2


def test_registration_window_expires(tmp_path, monkeypatch) -> None:
    from mcp.server.auth.provider import RegistrationError

    engine = make_engine(tmp_path / "dcr-expire.db")
    migrate(engine)
    monkeypatch.setattr(mcp_oauth, "MAX_REGISTRATIONS_PER_HOUR", 1)
    with engine.begin() as conn:
        conn.execute(rate_limit_event.insert().values(
            bucket="oauth_dcr", key_hash=db.rate_limit_key_hash("global"),
            created_at=time.time() - mcp_oauth._REGISTRATION_WINDOW_SECONDS - 10))
    mcp_oauth._take_registration_slot(engine)  # expired slot must not block
    with pytest.raises(RegistrationError):
        mcp_oauth._take_registration_slot(engine)
