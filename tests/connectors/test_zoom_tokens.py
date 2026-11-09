"""The rotating Zoom token store: one rotation per expiry, adoption of another worker's rotation."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
from sqlalchemy import select, update
from zoom import ZoomOAuthConfig
from zoom.errors import ConnectorAuthError

from hoja_server.db import connector_account, make_engine, migrate
from hoja_server.secrets_box import decrypt_secret, encrypt_secret
from hoja_server.zoom_tokens import ZoomTokenStore

TOKEN_URL = "https://zoom.us/oauth/token"
ACCOUNT_ID = "acct-zoom-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _config() -> ZoomOAuthConfig:
    return ZoomOAuthConfig(
        client_id="5kXHQtScS8WhD2lgmuedKQ",
        client_secret="secret_test_value",
        redirect_uri="https://hoja.example.test/api/connectors/zoom/callback",
    )


def _engine(tmp_path):
    engine = make_engine(tmp_path / "zoom.db")
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
                provider="zoom",
                external_account="luis@hoja.work",
                scopes=" ".join(
                    (
                        "user:read:user",
                        "meeting:read:list_meetings",
                        "meeting:read:meeting",
                        "cloud_recording:read:list_user_recordings",
                        "cloud_recording:read:list_recording_files",
                        "cloud_recording:read:meeting_transcript",
                    )
                ),
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
    return httpx.Response(
        200,
        json={
            "token_type": "bearer",
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "scope": "user:read:user meeting:read:list_meetings",
            "api_url": "https://api.zoom.us",
        },
    )


@respx.mock
async def test_a_valid_stored_access_token_is_reused_without_refreshing(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, expires_at=int(time.time()) + 3000)
    route = respx.post(TOKEN_URL).mock(return_value=_token_response("new", "new-refresh"))
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-1"
    assert route.calls == []  # refreshing here would spend a rotation for nothing


@respx.mock
async def test_an_access_token_inside_the_skew_is_refreshed_early(tmp_path) -> None:
    """Zoom tokens last an hour; a call starting just under the wire must not race the clock."""
    engine = _engine(tmp_path)
    _insert(engine, expires_at=int(time.time()) + 30)
    route = respx.post(TOKEN_URL).mock(return_value=_token_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-2"
    assert len(route.calls) == 1


@respx.mock
async def test_two_concurrent_calls_spend_exactly_one_rotation(tmp_path) -> None:
    """The failure this prevents: two refreshes with the same token, one rejection, and an
    account that looks revoked while the grant is healthy."""
    engine = _engine(tmp_path)
    _insert(engine)
    route = respx.post(TOKEN_URL).mock(return_value=_token_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        first, second = await asyncio.gather(
            store.access_token(ACCOUNT_ID), store.access_token(ACCOUNT_ID)
        )
    assert first == second == "access-2"
    assert len(route.calls) == 1
    # The rotated pair is what lands in storage, never the one that was spent.
    row = _stored(engine)
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-2"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-2"


@respx.mock
async def test_a_rotation_by_another_worker_is_adopted_not_declared_dead(tmp_path) -> None:
    """Simulates a second process rotating first: our refresh is rejected, the row already holds
    a newer refresh token, so the grant is healthy and must not be reported as revoked."""
    engine = _engine(tmp_path)
    _insert(engine, refresh="refresh-1")

    def _rotate_first(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "refresh-1" in body:
            # Someone else rotated in the meantime.
            with engine.begin() as conn:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == ACCOUNT_ID)
                    .values(
                        encrypted_refresh_token=encrypt_secret("refresh-2"),
                        encrypted_access_token=encrypt_secret("access-2"),
                        access_token_expires_at=str(int(time.time()) - 5),
                    )
                )
            # Zoom's measured rejection for a spent refresh token.
            return httpx.Response(
                400, json={"error": "invalid_grant", "reason": "Invalid refresh token"}
            )
        return _token_response("access-3", "refresh-3")

    respx.post(TOKEN_URL).mock(side_effect=_rotate_first)
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-3"
    assert decrypt_secret(_stored(engine)["encrypted_refresh_token"]) == "refresh-3"


@respx.mock
async def test_a_worker_that_already_stored_a_valid_access_token_is_trusted(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, access="access-1", expires_at=int(time.time()) - 10)
    # Between our row read and our refresh, another worker stored a fresh pair.
    with engine.begin() as conn:
        conn.execute(
            update(connector_account)
            .where(connector_account.c.id == ACCOUNT_ID)
            .values(
                encrypted_access_token=encrypt_secret("access-9"),
                access_token_expires_at=str(int(time.time()) + 3000),
            )
        )
    route = respx.post(TOKEN_URL).mock(return_value=_token_response("unused", "unused"))
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-9"
    assert route.calls == []


@respx.mock
async def test_a_really_revoked_grant_raises_and_keeps_the_row_untouched(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine, refresh="refresh-1")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "reason": "Invalid refresh token"}
        )
    )
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        with pytest.raises(ConnectorAuthError) as excinfo:
            await store.access_token(ACCOUNT_ID)
    assert "reconnect zoom" in (excinfo.value.hint or "").lower()
    # The stored token is left as-is: a transient provider fault must not erase the grant.
    assert decrypt_secret(_stored(engine)["encrypted_refresh_token"]) == "refresh-1"


@respx.mock
async def test_tokens_are_sealed_at_rest(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine)
    respx.post(TOKEN_URL).mock(return_value=_token_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = ZoomTokenStore(engine, http, _config())
        await store.access_token(ACCOUNT_ID)
    row = _stored(engine)
    assert "refresh-2" not in row["encrypted_refresh_token"]
    assert "access-2" not in row["encrypted_access_token"]


def test_the_source_reports_the_configured_api_base(tmp_path) -> None:
    engine = _engine(tmp_path)
    _insert(engine)
    config = ZoomOAuthConfig(
        client_id="5kXHQtScS8WhD2lgmuedKQ",
        client_secret="s",
        redirect_uri="https://hoja.example.test/api/connectors/zoom/callback",
        api_base="https://eu01api.zoom.us/v2",
    )
    store = ZoomTokenStore(engine, httpx.AsyncClient(), config)
    assert store.source(ACCOUNT_ID).api_base() == "https://eu01api.zoom.us/v2"
