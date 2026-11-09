"""The eight-hour token store: one refresh per expiry, adoption of another worker's refresh."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sentry import SentryOAuthConfig
from sentry.errors import ConnectorAuthError, ConnectorConfigError
from sqlalchemy import select, update

from hoja_server.db import connector_account, make_engine, migrate
from hoja_server.secrets_box import decrypt_secret, encrypt_secret
from hoja_server.sentry_tokens import SentryTokenStore

INSTALLATION = "cd6170b4-8ad6-403e-b078-8eb7d7688c28"
AUTHZ = f"https://sentry.io/api/0/sentry-app-installations/{INSTALLATION}/authorizations/"
ACCOUNT_ID = "acct-sentry-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _config() -> SentryOAuthConfig:
    return SentryOAuthConfig(
        client_id="63f307c32617f9445b9deba149d733004d49495ce7b07fb142017482bbf67167",
        client_secret="secret_test_value",
        redirect_uri="https://hoja.example.test/api/connectors/sentry/callback",
    )


def _engine(tmp_path):
    engine = make_engine(tmp_path / "sentry.db")
    migrate(engine)
    return engine


def _insert(engine, *, refresh="refresh-1", access="access-1", expires_at=None):
    now = int(time.time())
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=ACCOUNT_ID,
                organization_id="org-1",
                owner_user_id="user-1",
                provider="sentry",
                external_account="hoja",
                external_id=INSTALLATION,
                scopes="event:read org:read project:read",
                encrypted_refresh_token=encrypt_secret(refresh),
                encrypted_access_token=encrypt_secret(access),
                access_token_expires_at=str(expires_at if expires_at is not None else now - 10),
                status="active",
                created_at="2026-11-09T00:00:00Z",
                updated_at="2026-11-09T00:00:00Z",
            )
        )


def _stored(engine) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(select(connector_account).where(connector_account.c.id == ACCOUNT_ID))
            .mappings()
            .one()
        )


def _token_response(access: str, refresh: str) -> httpx.Response:
    """A fresh eight-hour grant, relative to now: a fixed date would be in the past on any run."""
    expires = datetime.now(timezone.utc) + timedelta(hours=8)
    return httpx.Response(
        200,
        json={
            "id": "38",
            "token": access,
            "refreshToken": refresh,
            "scopes": ["event:read", "org:read", "project:read"],
            "dateCreated": "2026-11-09T00:00:00Z",
            "expiresAt": expires.isoformat().replace("+00:00", "Z"),
            "state": None,
            "application": None,
        },
    )


@respx.mock
async def test_a_valid_stored_access_token_is_reused_without_refreshing(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, expires_at=int(time.time()) + 3600)
    route = respx.post(AUTHZ).mock(return_value=_token_response("new", "new-refresh"))
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-1"
    assert route.calls == []


@respx.mock
async def test_a_token_inside_the_expiry_skew_is_refreshed(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, expires_at=int(time.time()) + 60)  # inside the 300 s skew
    route = respx.post(AUTHZ).mock(return_value=_token_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-2"
    assert len(route.calls) == 1


@respx.mock
async def test_two_concurrent_calls_spend_exactly_one_refresh(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine)
    route = respx.post(AUTHZ).mock(return_value=_token_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        first, second = await asyncio.gather(
            store.access_token(ACCOUNT_ID), store.access_token(ACCOUNT_ID)
        )
    assert first == second == "access-2"
    assert len(route.calls) == 1
    row = _stored(engine)
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-2"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-2"


@respx.mock
async def test_a_refresh_by_another_worker_is_adopted_not_declared_dead(tmp_path) -> None:
    """Simulates a second process refreshing first.

    Our assertion is refused because the installation moved on, and the row already holds a newer
    refresh token. The grant is healthy: adopt it instead of telling the user to reconnect.
    """
    engine = _engine(tmp_path)
    _insert(engine, refresh="refresh-1")

    def _rotate_first(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "refresh_token" not in body:
            with engine.begin() as conn:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == ACCOUNT_ID)
                    .values(
                        encrypted_refresh_token=encrypt_secret("refresh-2"),
                        encrypted_access_token=encrypt_secret(""),
                        access_token_expires_at=str(int(time.time()) - 5),
                    )
                )
            return httpx.Response(403, json={"detail": "installation not found"})
        assert "refresh-2" in body
        return _token_response("access-3", "refresh-3")

    respx.post(AUTHZ).mock(side_effect=_rotate_first)
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-3"
    assert decrypt_secret(_stored(engine)["encrypted_refresh_token"]) == "refresh-3"


@respx.mock
async def test_the_refresh_token_grant_is_the_fallback_after_a_refused_assertion(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, refresh="refresh-1")
    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        seen.append("assertion" if "refresh_token" not in body else "refresh_token")
        if "refresh_token" not in body:
            return httpx.Response(403, json={"detail": "installation not found"})
        return _token_response("access-2", "refresh-2")

    respx.post(AUTHZ).mock(side_effect=_handler)
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-2"
    assert seen == ["assertion", "refresh_token"]


@respx.mock
async def test_a_refused_client_pair_never_falls_back_and_never_asks_for_a_reconnect(
    tmp_path,
) -> None:
    engine = _engine(tmp_path)
    _insert(engine, refresh="refresh-1")
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content.decode())
        return httpx.Response(401, json={"detail": "Invalid client credentials"})

    respx.post(AUTHZ).mock(side_effect=_handler)
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        with pytest.raises(ConnectorConfigError):
            await store.access_token(ACCOUNT_ID)
    # One attempt: a wrong secret is not fixed by spending the fallback too.
    assert len(calls) == 1


@respx.mock
async def test_a_really_removed_installation_raises_and_keeps_the_row(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, refresh="refresh-1")
    respx.post(AUTHZ).mock(return_value=httpx.Response(403, json={"detail": "not found"}))
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        with pytest.raises(ConnectorAuthError) as excinfo:
            await store.access_token(ACCOUNT_ID)
    assert "connect sentry again" in (excinfo.value.hint or "").lower()
    assert decrypt_secret(_stored(engine)["encrypted_refresh_token"]) == "refresh-1"


@respx.mock
async def test_tokens_are_sealed_at_rest(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine)
    respx.post(AUTHZ).mock(return_value=_token_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        await store.access_token(ACCOUNT_ID)
    row = _stored(engine)
    assert "refresh-2" not in row["encrypted_refresh_token"]
    assert "access-2" not in row["encrypted_access_token"]


async def test_the_source_exposes_the_bound_organization(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine)
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        assert store.source(ACCOUNT_ID).organization() == "hoja"


async def test_a_row_without_an_installation_id_asks_for_a_reconnect(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine)
    with engine.begin() as conn:
        conn.execute(
            update(connector_account)
            .where(connector_account.c.id == ACCOUNT_ID)
            .values(external_id="")
        )
    async with httpx.AsyncClient() as http:
        store = SentryTokenStore(engine, http, _config())
        with pytest.raises(ConnectorAuthError):
            await store.access_token(ACCOUNT_ID)


# --- grant resolution ----------------------------------------------------------------------


async def test_the_grant_resolver_reports_nothing_when_sentry_is_unconfigured(tmp_path, monkeypatch) -> None:
    """A deployment without Sentry credentials still starts the shared MCP server."""
    for name in (
        "HOJA_SENTRY_CLIENT_ID",
        "HOJA_SENTRY_CLIENT_SECRET",
        "HOJA_SENTRY_REDIRECT_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    from types import SimpleNamespace

    from hoja_server.connector_mcp_service import make_sentry_grant_resolver

    engine = _engine(tmp_path)
    async with httpx.AsyncClient() as http:
        resolve = make_sentry_grant_resolver(engine, http=http)
        grants = await resolve(SimpleNamespace(org_id="org-1", user_id="user-1"), "")
    assert grants == []


async def test_the_grant_resolver_skips_a_row_without_an_installation(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from hoja_server.connector_mcp_service import make_sentry_grant_resolver

    monkeypatch.setenv(
        "HOJA_SENTRY_CLIENT_ID", "63f307c32617f9445b9deba149d733004d49495ce7b07fb142017482bbf67167"
    )
    monkeypatch.setenv("HOJA_SENTRY_CLIENT_SECRET", "secret_test_value")
    monkeypatch.setenv("HOJA_SENTRY_REDIRECT_URI", "https://hoja.example.test/api/connectors/sentry/callback")
    engine = _engine(tmp_path)
    _insert(engine)
    with engine.begin() as conn:
        conn.execute(
            update(connector_account)
            .where(connector_account.c.id == ACCOUNT_ID)
            .values(external_id="")
        )
    async with httpx.AsyncClient() as http:
        resolve = make_sentry_grant_resolver(engine, http=http)
        principal = SimpleNamespace(org_id="org-1", user_id="user-1")
        assert await resolve(principal, "") == []


async def test_the_grant_resolver_returns_a_live_token_source(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from hoja_server.connector_mcp_service import make_sentry_grant_resolver

    monkeypatch.setenv(
        "HOJA_SENTRY_CLIENT_ID", "63f307c32617f9445b9deba149d733004d49495ce7b07fb142017482bbf67167"
    )
    monkeypatch.setenv("HOJA_SENTRY_CLIENT_SECRET", "secret_test_value")
    monkeypatch.setenv("HOJA_SENTRY_REDIRECT_URI", "https://hoja.example.test/api/connectors/sentry/callback")
    engine = _engine(tmp_path)
    _insert(engine, expires_at=int(time.time()) + 3600)
    async with httpx.AsyncClient() as http:
        resolve = make_sentry_grant_resolver(engine, http=http)
        principal = SimpleNamespace(org_id="org-1", user_id="user-1")
        grants = await resolve(principal, "")
        assert len(grants) == 1
        assert grants[0].external_account == "hoja"
        # The organization travels with the grant, never with the tool argument.
        assert grants[0].tokens.organization() == "hoja"
