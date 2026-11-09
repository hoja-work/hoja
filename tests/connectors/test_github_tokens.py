"""The rotating GitHub token store: one refresh per expiry, adoption of another worker's rotation.

Two hazards are specific to this store and both are tested here: an eight-hour access token that
would burn the rotation chain if refreshed per call, and a refresh token that is single-use, so two
concurrent callers must not both spend it.
"""

from __future__ import annotations

import asyncio
import time
import urllib.parse

import httpx
import pytest
import respx
from github import GitHubAppConfig
from github.errors import ConnectorAuthError
from sqlalchemy import select, update

from hoja_server.db import connector_account, make_engine, migrate
from hoja_server.github_tokens import GitHubTokenStore
from hoja_server.secrets_box import decrypt_secret, encrypt_secret

TOKEN_URL = "https://github.com/login/oauth/access_token"
ACCOUNT_ID = "acct-github-1"
CALLBACK = "https://hoja.example.test/api/connectors/github/callback"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _config() -> GitHubAppConfig:
    return GitHubAppConfig(
        client_id="Iv23li4RW9Mh8RHVfCGi",
        client_secret="secret_test_value",
        redirect_uri=CALLBACK,
    )


def _engine(tmp_path):
    engine = make_engine(tmp_path / "github.db")
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
                provider="github",
                external_account="rinadelph",
                scopes="",
                encrypted_refresh_token=encrypt_secret(refresh),
                encrypted_access_token=encrypt_secret(access),
                access_token_expires_at=str(expires_at if expires_at is not None else now - 10),
                status="active",
                created_at="2026-11-09T00:00:00Z",
                updated_at="2026-11-09T00:00:00Z",
            )
        )


def _row(engine):
    with engine.connect() as conn:
        return (
            conn.execute(select(connector_account).where(connector_account.c.id == ACCOUNT_ID))
            .mappings()
            .one()
        )


def _response(access: str, refresh: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access,
            "expires_in": 28800,
            "refresh_token": refresh,
            "refresh_token_expires_in": 15778476,
            "token_type": "bearer",
        },
    )


@respx.mock
async def test_an_expired_access_token_is_refreshed_once_and_both_secrets_are_persisted(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine)
    route = respx.post(TOKEN_URL).mock(return_value=_response("access-2", "refresh-2"))
    store = GitHubTokenStore(engine, httpx.AsyncClient(), _config())

    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        token = await store.access_token(ACCOUNT_ID)
        # A second call inside the eight-hour window must reuse the cache, not rotate again.
        again = await store.access_token(ACCOUNT_ID)

    assert token == "access-2"
    assert again == "access-2"
    assert route.call_count == 1
    row = _row(engine)
    assert decrypt_secret(row["encrypted_refresh_token"]) == "refresh-2"
    assert decrypt_secret(row["encrypted_access_token"]) == "access-2"
    assert int(row["access_token_expires_at"]) > time.time() + 28_000


@respx.mock
async def test_a_fresh_access_token_is_used_without_a_refresh(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine, access="access-fresh", expires_at=int(time.time()) + 20_000)
    route = respx.post(TOKEN_URL).mock(return_value=_response("unused", "unused"))
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        assert await store.access_token(ACCOUNT_ID) == "access-fresh"
    assert route.call_count == 0


@respx.mock
async def test_two_concurrent_callers_spend_the_refresh_token_once(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine)
    route = respx.post(TOKEN_URL).mock(return_value=_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        a, b = await asyncio.gather(
            store.access_token(ACCOUNT_ID), store.access_token(ACCOUNT_ID)
        )
    assert a == b == "access-2"
    assert route.call_count == 1


@respx.mock
async def test_a_rejection_after_another_worker_rotated_adopts_the_stored_pair(tmp_path):
    """The loser of a race must not declare the account dead: the row already holds the new pair.

    The race is reproduced inside the mock: by the time our rejected refresh comes back, the row
    holds a *different* refresh token, which is the only evidence that someone else rotated.
    """
    engine = _engine(tmp_path)
    _insert(engine)
    calls: list[str] = []

    def _rotate_elsewhere(request: httpx.Request) -> httpx.Response:
        body = dict(urllib.parse.parse_qsl(request.content.decode()))
        calls.append(body["refresh_token"])
        if len(calls) == 1:
            # Another worker rotated first and stored its pair; our token is now dead.
            with engine.begin() as conn:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == ACCOUNT_ID)
                    .values(
                        encrypted_refresh_token=encrypt_secret("refresh-2"),
                        access_token_expires_at="0",
                        encrypted_access_token=encrypt_secret("access-2"),
                    )
                )
            return httpx.Response(200, json={"error": "bad_refresh_token"})
        return _response("access-3", "refresh-3")

    respx.post(TOKEN_URL).mock(side_effect=_rotate_elsewhere)
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        token = await store.access_token(ACCOUNT_ID)

    assert calls == ["refresh-1", "refresh-2"]
    assert token == "access-3"
    assert decrypt_secret(_row(engine)["encrypted_refresh_token"]) == "refresh-3"


@respx.mock
async def test_a_rejected_refresh_with_an_unchanged_row_means_reconnect(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"error": "bad_refresh_token"})
    )
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        with pytest.raises(ConnectorAuthError) as caught:
            await store.access_token(ACCOUNT_ID)
    assert "six-month" in str(caught.value)
    assert caught.value.hint and "Settings > Connections" in caught.value.hint


@respx.mock
async def test_an_account_with_no_stored_grant_is_not_refreshed(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine, refresh="")
    route = respx.post(TOKEN_URL).mock(return_value=_response("x", "y"))
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        with pytest.raises(ConnectorAuthError):
            await store.access_token(ACCOUNT_ID)
    assert route.call_count == 0


async def test_a_missing_account_row_is_reported_as_disconnected(tmp_path):
    engine = _engine(tmp_path)
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        with pytest.raises(ConnectorAuthError):
            await store.access_token("acct-that-does-not-exist")


@respx.mock
async def test_invalidate_forces_the_next_call_to_refresh(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine, access="access-1", expires_at=int(time.time()) + 20_000)
    route = respx.post(TOKEN_URL).mock(return_value=_response("access-2", "refresh-2"))
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        source = store.source(ACCOUNT_ID)
        assert await source.access_token() == "access-1"
        await source.invalidate()
        assert await source.access_token() == "access-2"
    assert route.call_count == 1


@respx.mock
async def test_seed_populates_the_cache_from_a_fresh_callback(tmp_path):
    engine = _engine(tmp_path)
    _insert(engine, access="access-callback", expires_at=int(time.time()) + 20_000)
    route = respx.post(TOKEN_URL).mock(return_value=_response("unused", "unused"))
    async with httpx.AsyncClient() as http:
        store = GitHubTokenStore(engine, http, _config())
        store.seed(ACCOUNT_ID, _row(engine))
        assert await store.access_token(ACCOUNT_ID) == "access-callback"
    assert route.call_count == 0
