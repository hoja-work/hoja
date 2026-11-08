"""The MCP endpoint accepts a Hoja API key as well as an OAuth access token.

An agent should not need a second credential type to reach the same identity, and a
read-only key must stay read-only through the MCP surface.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from hoja_server.db import make_engine, migrate, seed_tenant
from hoja_server.mcp_oauth import ALL_SCOPES, READ_SCOPE, HojaOAuthProvider
from hoja_server.store import Store


def _provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_TRUSTED_PUBLIC_HOST", "hoja.test")
    db_path = tmp_path / "mcp-api-key.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(
        engine,
        org_name="MCP Keys",
        org_url_key="mcp-keys",
        user_email="mcp-keys@example.test",
        token="mcp-keys-seed",
        demo_issues=False,
    )
    store = Store(engine)
    provider = HojaOAuthProvider(engine, store, trusted_proxy=True)
    return provider, store, seeded


def test_read_write_api_key_yields_read_and_write_scopes(tmp_path, monkeypatch) -> None:
    provider, store, seeded = _provider(tmp_path, monkeypatch)
    _, secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "CLI", "read_write")

    token = asyncio.run(provider.load_access_token(secret))

    assert token is not None
    assert set(token.scopes) == set(ALL_SCOPES)
    assert token.client_id == "api-key"


def test_read_api_key_yields_read_scope_only(tmp_path, monkeypatch) -> None:
    provider, store, seeded = _provider(tmp_path, monkeypatch)
    _, secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "Dashboard", "read")

    token = asyncio.run(provider.load_access_token(secret))

    assert token is not None
    assert token.scopes == [READ_SCOPE]


def test_unknown_bearer_is_still_rejected(tmp_path, monkeypatch) -> None:
    provider, _store, _seeded = _provider(tmp_path, monkeypatch)

    assert asyncio.run(provider.load_access_token("not-a-real-credential")) is None
