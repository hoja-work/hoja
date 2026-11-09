"""The server-owned token source for Sentry installations.

Sentry public integrations issue an access token that lives **eight hours** and a refresh token the
docs describe as a fallback. Three failure modes are handled here, and each one has a cost if it is
not:

1. **Double refresh.** Two concurrent tool calls with an expiring token would both refresh. The
   assertion is signed per call so both may succeed, but the second response can rotate the stored
   refresh token out from under the first. A per-account asyncio lock means one caller refreshes and
   the rest wait and reuse the result.
2. **Lost rotation across processes.** If another worker refreshed first, our stored refresh token
   is stale. The row is re-read; if its credential differs from the one we used, the rotation came
   from someone else and the new value is adopted instead of declaring the account dead.
3. **Expiry ignored.** Refreshing on every call would spend an assertion per tool call. The access
   token and its `expiresAt` are cached and only refreshed inside a small skew of expiry.

The plaintext credential never leaves this module except as a bearer token header. Every write is
sealed with `secrets_box`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sentry import ConnectorAPIError, ConnectorConfigError
from sentry.errors import ConnectorAuthError
from sentry.oauth import (
    SentryToken,
    refresh_installation,
    refresh_with_token,
)
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from hoja_server.db import connector_account
from hoja_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret

log = logging.getLogger("hoja_server.sentry_tokens")

# Refresh this long before the real expiry, so a call that starts just under the wire does not race
# the clock between the check and the request.
_EXPIRY_SKEW_SECONDS = 300
_UNKNOWN_EXPIRY = 0.0


@dataclass(frozen=True)
class _Cached:
    access_token: str
    expires_at: float


def _now() -> float:
    return time.time()


def _iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SentryTokenStore:
    """One instance per process. Holds the per-account refresh lock and the access cache."""

    def __init__(self, engine: Engine, http: httpx.AsyncClient, config: Any) -> None:
        self._engine = engine
        self._http = http
        self._config = config
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, _Cached] = {}

    def source(self, account_id: str) -> AccountTokenSource:
        return AccountTokenSource(self, account_id)

    def _lock_for(self, account_id: str) -> asyncio.Lock:
        lock = self._locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_id] = lock
        return lock

    def forget(self, account_id: str) -> None:
        self._cache.pop(account_id, None)

    async def access_token(self, account_id: str) -> str:
        cached = self._cache.get(account_id)
        if cached is not None and cached.expires_at - _EXPIRY_SKEW_SECONDS > _now():
            return cached.access_token
        async with self._lock_for(account_id):
            # Re-check under the lock: the caller that just finished may have refreshed.
            cached = self._cache.get(account_id)
            if cached is not None and cached.expires_at - _EXPIRY_SKEW_SECONDS > _now():
                return cached.access_token
            return await self._refresh(account_id)

    async def _refresh(self, account_id: str) -> str:
        row = self._row(account_id)
        if row is None:
            raise ConnectorAuthError(
                "The Sentry connection is no longer available.",
                hint="Connect Sentry again in Settings > Connections.",
            )
        installation_id = str(row["external_id"] or "")
        if not installation_id:
            raise ConnectorAuthError(
                "This Sentry account has no stored installation.",
                hint="Reconnect Sentry in Settings > Connections.",
            )
        # Another worker (or the connect callback) may already have stored a valid access token.
        stored = self._read_access(row)
        if stored is not None and stored.expires_at - _EXPIRY_SKEW_SECONDS > _now():
            self._cache[account_id] = stored
            return stored.access_token
        used_refresh = self._decrypt(row["encrypted_refresh_token"])
        try:
            token = await refresh_installation(
                self._http, self._config, installation_id=installation_id
            )
        except ConnectorConfigError:
            # The client pair itself is refused: a deployer fault, never a reconnect prompt.
            raise
        except (ConnectorAuthError, ConnectorAPIError) as exc:
            adopted = await self._adopt_concurrent_rotation(account_id, used_refresh, installation_id)
            if adopted is not None:
                return adopted
            if used_refresh:
                # Fallback the docs call "not recommended" but keep: a refresh token that still
                # works beats making the user reinstall over one failed assertion.
                try:
                    token = await refresh_with_token(
                        self._http,
                        self._config,
                        installation_id=installation_id,
                        refresh_token=used_refresh,
                    )
                except (ConnectorAuthError, ConnectorAPIError, ConnectorConfigError):
                    raise ConnectorAuthError(
                        "Sentry no longer accepts this installation's grant.",
                        hint="Connect Sentry again in Settings > Connections.",
                    ) from exc
            else:
                raise ConnectorAuthError(
                    "Sentry no longer accepts this installation's grant.",
                    hint="Connect Sentry again in Settings > Connections.",
                ) from exc
        return self._persist(account_id, token)

    async def _adopt_concurrent_rotation(
        self, account_id: str, used_refresh: str, installation_id: str
    ) -> str | None:
        """Another worker may have refreshed first. If the stored credential changed, use theirs."""
        row = self._row(account_id)
        if row is None:
            return None
        cached = self._read_access(row)
        if cached is not None and cached.expires_at - _EXPIRY_SKEW_SECONDS > _now():
            self._cache[account_id] = cached
            return cached.access_token
        current = self._decrypt(row["encrypted_refresh_token"])
        if not current or current == used_refresh:
            return None  # unchanged: nothing was rotated in the meantime
        try:
            token = await refresh_with_token(
                self._http,
                self._config,
                installation_id=installation_id,
                refresh_token=current,
            )
        except (ConnectorAuthError, ConnectorAPIError, ConnectorConfigError):
            return None
        return self._persist(account_id, token)

    def _persist(self, account_id: str, token: SentryToken) -> str:
        expires_at = float(token.expires_at)
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
    """The narrow `sentry.client.TokenSource` the connector package sees."""

    def __init__(self, store: SentryTokenStore, account_id: str) -> None:
        self._store = store
        self._account_id = account_id

    async def access_token(self) -> str:
        return await self._store.access_token(self._account_id)

    async def invalidate(self) -> None:
        # Drop the cache so the next access_token() call refreshes instead of replaying a token
        # Sentry just rejected.
        self._store.forget(self._account_id)

    def organization(self) -> str:
        row = self._store._row(self._account_id)
        return str(row["external_account"] or "") if row is not None else ""


__all__ = ["AccountTokenSource", "SentryTokenStore"]
