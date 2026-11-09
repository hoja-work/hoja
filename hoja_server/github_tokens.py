"""The server-owned token source for GitHub accounts.

GitHub App user tokens expire (8 h on the provisioned app) and refresh rotates the refresh token,
so the same three hazards Zoom's store documents apply here, plus one GitHub-specific one:

1. **Double refresh.** Two concurrent tool calls with an expired access token would both POST the
   same refresh token; GitHub answers `bad_refresh_token` for the loser and the account looks
   revoked. A per-account asyncio lock makes the first caller refresh and the rest wait.
2. **Lost rotation across processes.** If another worker rotated first, our refresh is rejected even
   though the grant is healthy. On that error the row is re-read; if its refresh token differs from
   the one we used, the rotation came from someone else and the new pair is adopted.
3. **Expiry ignored.** An 8-hour token refreshed on every call would spend the rotation chain for
   nothing, so the access token and its expiry are cached and refreshed only inside a skew window.
4. **The refresh chain has its own clock.** GitHub's refresh token lasts six months
   (`refresh_token_expires_in`, measured at 15,778,476 s). The schema has no column for it, so the
   store does not pretend to know it: an expired chain surfaces as `bad_refresh_token` and the
   error path says reconnect, which is the only action that helps.

The plaintext credential never leaves this module except as a bearer token header. Every write is
sealed with `secrets_box`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from github import ConnectorConfigError, GitHubAppConfig
from github.errors import ConnectorAPIError, ConnectorAuthError
from github.oauth import GitHubToken, refresh_access_token
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from hoja_server.db import connector_account
from hoja_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret

log = logging.getLogger("hoja_server.github_tokens")

# Refresh this long before the real expiry. Eight hours is long enough that the skew only matters
# when a call starts just under the wire; five minutes absorbs slow requests and clock drift.
_EXPIRY_SKEW_SECONDS = 300
_UNKNOWN_EXPIRY = 0.0


@dataclass(frozen=True)
class _Cached:
    access_token: str
    expires_at: float


def _now() -> float:
    return time.time()


class GitHubTokenStore:
    """One instance per process. Holds the per-account refresh lock and the access cache."""

    def __init__(self, engine: Engine, http: httpx.AsyncClient, config: Any) -> None:
        self._engine = engine
        self._http = http
        self._config = config
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, _Cached] = {}
        # Accounts whose last access token was rejected by GitHub (401). The next call must rotate
        # instead of reusing the stored access token, even though its recorded expiry has not
        # passed: a token can be revoked or the app suspended long before then.
        self._forced: set[str] = set()

    def source(self, account_id: str) -> AccountTokenSource:
        return AccountTokenSource(self, account_id)

    def api_base(self) -> str:
        return str(getattr(self._config, "api_base", "") or "https://api.github.com")

    def _lock_for(self, account_id: str) -> asyncio.Lock:
        lock = self._locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_id] = lock
        return lock

    def forget(self, account_id: str) -> None:
        self._cache.pop(account_id, None)

    def invalidate(self, account_id: str) -> None:
        """Drop the cache *and* refuse the stored access token on the next call."""
        self._cache.pop(account_id, None)
        self._forced.add(account_id)

    async def access_token(self, account_id: str) -> str:
        cached = self._cache.get(account_id)
        if cached is not None and cached.expires_at - _EXPIRY_SKEW_SECONDS > _now():
            return cached.access_token
        async with self._lock_for(account_id):
            # Re-check under the lock: the caller that just finished may have refreshed.
            cached = self._cache.get(account_id)
            if cached is not None and cached.expires_at - _EXPIRY_SKEW_SECONDS > _now():
                return cached.access_token
            forced = account_id in self._forced
            self._forced.discard(account_id)
            return await self._refresh(account_id, force=forced)

    async def _refresh(self, account_id: str, *, force: bool = False) -> str:
        row = self._row(account_id)
        if row is None:
            raise ConnectorAuthError(
                "The GitHub account is no longer connected.",
                hint="Connect GitHub again in Settings > Connections.",
            )
        # Another worker (or the connect callback) may have already stored a valid access token.
        # Reusing it before refreshing is what keeps a rotating refresh chain from being spent —
        # except after a 401, where the stored token is exactly what must not be replayed.
        if not force:
            stored = self._read_access(row)
            if stored is not None and stored.expires_at - _EXPIRY_SKEW_SECONDS > _now():
                self._cache[account_id] = stored
                return stored.access_token
        used_refresh = self._decrypt(row["encrypted_refresh_token"])
        if not used_refresh:
            raise ConnectorAuthError(
                "The GitHub account has no stored grant.",
                hint="Reconnect GitHub in Settings > Connections.",
            )
        try:
            token = await refresh_access_token(
                self._http, self._config, refresh_token=used_refresh
            )
        except ConnectorAuthError as exc:
            adopted = await self._adopt_concurrent_rotation(account_id, used_refresh)
            if adopted is not None:
                return adopted
            raise ConnectorAuthError(
                "GitHub no longer accepts this account's grant (the refresh token was revoked, "
                "already used, or reached its six-month limit).",
                hint="Reconnect GitHub in Settings > Connections.",
            ) from exc
        return self._persist(account_id, token)

    async def _adopt_concurrent_rotation(self, account_id: str, used_refresh: str) -> str | None:
        """Another worker may have rotated first. If the stored token changed, use theirs."""
        row = self._row(account_id)
        if row is None:
            return None
        current = self._decrypt(row["encrypted_refresh_token"])
        if not current or current == used_refresh:
            return None  # unchanged: the grant really is dead
        cached = self._read_access(row)
        if cached is not None and cached.expires_at - _EXPIRY_SKEW_SECONDS > _now():
            self._cache[account_id] = cached
            return cached.access_token
        try:
            token = await refresh_access_token(self._http, self._config, refresh_token=current)
        except (ConnectorAuthError, ConnectorAPIError, ConnectorConfigError):
            return None
        return self._persist(account_id, token)

    def _persist(self, account_id: str, token: GitHubToken) -> str:
        expires_at = _now() + token.expires_in
        with self._engine.begin() as conn:
            conn.execute(
                update(connector_account)
                .where(connector_account.c.id == account_id)
                .values(
                    encrypted_refresh_token=encrypt_secret(token.refresh_token),
                    encrypted_access_token=encrypt_secret(token.access_token),
                    access_token_expires_at=str(int(expires_at)),
                    updated_at=_iso(),
                )
            )
        self._cache[account_id] = _Cached(access_token=token.access_token, expires_at=expires_at)
        return token.access_token

    def seed(self, account_id: str, row: Any) -> None:
        """Populate the cache from a freshly stored grant (right after the callback)."""
        cached = self._read_access(row)
        if cached is not None:
            self._cache[account_id] = cached

    def _read_access(self, row: Any) -> _Cached | None:
        try:
            access = decrypt_secret(row["encrypted_access_token"] or "")
        except (SecretBoxError, ValueError, KeyError):
            return None
        if not access:
            return None
        raw = row["access_token_expires_at"] or str(int(_UNKNOWN_EXPIRY))
        try:
            expires_at = float(raw)
        except ValueError:
            expires_at = _UNKNOWN_EXPIRY
        return _Cached(access_token=access, expires_at=expires_at)

    def _row(self, account_id: str) -> Any:
        with self._engine.connect() as conn:
            return (
                conn.execute(
                    select(connector_account).where(connector_account.c.id == account_id)
                )
                .mappings()
                .one_or_none()
            )

    def _decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return decrypt_secret(value)
        except (SecretBoxError, ValueError):
            return ""


class AccountTokenSource:
    """The narrow `github.client.TokenSource` the connector package sees."""

    def __init__(self, store: GitHubTokenStore, account_id: str) -> None:
        self._store = store
        self._account_id = account_id

    async def access_token(self) -> str:
        return await self._store.access_token(self._account_id)

    async def invalidate(self) -> None:
        # Drop the cache and force the next access_token() call to refresh instead of replaying a
        # token GitHub just rejected.
        self._store.invalidate(self._account_id)

    def api_base(self) -> str:
        return self._store.api_base()


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["AccountTokenSource", "GitHubAppConfig", "GitHubTokenStore"]
