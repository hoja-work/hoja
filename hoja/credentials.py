"""Secret storage, token refresh and credential precedence for the CLI.

OAuth secrets (access token, refresh token) are stored outside the main config
file: in the OS keyring when the optional ``keyring`` package is importable and
has a usable backend, otherwise in ``credentials.json`` next to ``config.toml``
with mode 0600, replaced atomically on every write. ``HOJA_CREDENTIAL_STORE=file``
forces the file store.

Refreshing rotates the refresh token, so concurrent ``hoja`` processes serialise
refresh-and-write behind an exclusive lock on ``credentials.lock`` and re-read
storage once if their refresh is rejected (another process may have rotated it).

Credential precedence for a command:
  1. ``--token``
  2. ``$<account token_env>`` (default ``$HOJA_TOKEN``, deprecated ``$LINEAR_TOKEN``)
  3. OAuth credentials of an account signed in with ``hoja auth login``
  4. An API key stored inline in the account (``token = ...``)
``$HOJA_API_URL`` overrides the endpoint for 1, 2 and 4. An OAuth token is only
ever sent to its own workspace origin, never to ``$HOJA_API_URL`` on another
host and never to the client's built-in default endpoint.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from hoja import oauth
from hoja.config import (
    LEGACY_ENV_ALIASES,
    AccountConfig,
    config_path,
    env_with_legacy,
    resolve_base_url,
    resolve_token,
)
from hoja.errors import AuthError

REFRESH_SKEW = 60.0
KEYRING_SERVICE = "hoja-cli"
CREDENTIALS_FILE = "credentials.json"
LOCK_FILE = "credentials.lock"


def credentials_dir() -> Path:
    return config_path().parent


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def credential_lock(directory: Path | None = None) -> Iterator[None]:
    """Exclusive inter-process lock guarding refresh-and-write."""
    d = directory or credentials_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(d / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import msvcrt

            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class FileStore:
    """``credentials.json`` (0600) keyed by account name."""

    kind = "file"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or credentials_dir() / CREDENTIALS_FILE

    def _read_all(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise AuthError(
                f"Cannot read {self.path}: {exc}",
                hint=f"Delete {self.path} and run `hoja auth login` again.",
            ) from exc
        accounts = data.get("accounts") if isinstance(data, dict) else None
        return accounts if isinstance(accounts, dict) else {}

    def _write_all(self, accounts: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=".credentials-", suffix=".tmp", dir=self.path.parent)
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "accounts": accounts}, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def load(self, account: str) -> dict[str, Any] | None:
        rec = self._read_all().get(account)
        return rec if isinstance(rec, dict) else None

    def save(self, account: str, record: dict[str, Any]) -> None:
        accounts = self._read_all()
        accounts[account] = record
        self._write_all(accounts)

    def delete(self, account: str) -> None:
        accounts = self._read_all()
        if accounts.pop(account, None) is not None:
            self._write_all(accounts)


class KeyringStore:
    """OS keyring entry per (config directory, account)."""

    kind = "keyring"

    def __init__(self, module: Any) -> None:
        self._kr = module

    @staticmethod
    def _user(account: str) -> str:
        return f"{credentials_dir()}:{account}"

    def load(self, account: str) -> dict[str, Any] | None:
        try:
            raw = self._kr.get_password(KEYRING_SERVICE, self._user(account))
        except Exception as exc:  # backend failures are environment problems
            raise AuthError(f"Cannot read the OS keyring: {exc}") from exc
        if not raw:
            return None
        try:
            rec = json.loads(raw)
        except ValueError:
            return None
        return rec if isinstance(rec, dict) else None

    def save(self, account: str, record: dict[str, Any]) -> None:
        self._kr.set_password(KEYRING_SERVICE, self._user(account), json.dumps(record))

    def delete(self, account: str) -> None:
        with contextlib.suppress(Exception):
            self._kr.delete_password(KEYRING_SERVICE, self._user(account))


def keyring_module() -> Any | None:
    """The ``keyring`` module when it is installed and has a real backend."""
    if os.environ.get("HOJA_CREDENTIAL_STORE", "").strip().lower() == "file":
        return None
    try:
        import keyring
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    if type(backend).__module__.startswith("keyring.backends.fail") or getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


def store_for(account: AccountConfig) -> FileStore | KeyringStore:
    if account.credential_store == "keyring":
        kr = keyring_module()
        if kr is None:
            raise AuthError(
                "This account's credentials are in the OS keyring, which is not available now",
                hint="Install `keyring`, or run `hoja auth login` again to store them in a file.",
            )
        return KeyringStore(kr)
    return FileStore()


def save_new_credentials(account_name: str, record: dict[str, Any]) -> str:
    """Store a fresh sign-in, preferring the keyring. Returns the store kind."""
    kr = keyring_module()
    if kr is not None:
        try:
            KeyringStore(kr).save(account_name, record)
            FileStore().delete(account_name)
            return "keyring"
        except Exception:
            pass  # e.g. no D-Bus secret service in this session: use the file
    with credential_lock():
        FileStore().save(account_name, record)
    return "file"


def delete_credentials(account_name: str, account: AccountConfig) -> None:
    kr = keyring_module()
    if kr is not None:
        KeyringStore(kr).delete(account_name)
    if account.credential_store != "keyring" or kr is None:
        with credential_lock():
            FileStore().delete(account_name)


def make_record(tokens: oauth.TokenSet, meta: oauth.ServerMetadata) -> dict[str, Any]:
    return {
        "origin": meta.origin,
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
        "scope": tokens.scope,
        "token_endpoint": meta.token_endpoint,
        "revocation_endpoint": meta.revocation_endpoint,
    }


# ---------------------------------------------------------------------------
# Session with automatic refresh
# ---------------------------------------------------------------------------


class OAuthSession:
    """Supplies a valid access token for one OAuth account, refreshing as needed."""

    def __init__(
        self,
        account_name: str,
        account: AccountConfig,
        *,
        store: FileStore | KeyringStore | None = None,
        http_factory: Callable[[], httpx.Client] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        if account.credential != "oauth" or not account.origin:
            raise ValueError("OAuthSession needs an OAuth account with an origin")
        self.account_name = account_name
        self.account = account
        self.origin = account.origin
        self.base_url = account.base_url or oauth.graphql_url(account.origin)
        self._store = store
        self._http_factory = http_factory
        self._now = now
        self._record: dict[str, Any] | None = None

    @property
    def store(self) -> FileStore | KeyringStore:
        if self._store is None:
            self._store = store_for(self.account)
        return self._store

    @property
    def workspace_label(self) -> str:
        ws = self.account.workspace
        return (ws.name if ws and ws.name else None) or self.origin

    def signed_out(self) -> AuthError:
        return AuthError(
            f"Signed out of {self.workspace_label}",
            hint=f"Run `hoja auth login {self.origin}`",
        )

    def _load(self) -> dict[str, Any] | None:
        rec = self.store.load(self.account_name)
        if not rec or rec.get("origin") != self.origin or not rec.get("access_token"):
            return None
        return rec

    def record(self) -> dict[str, Any] | None:
        """The stored record without refreshing (for status output)."""
        return self._record or self._load()

    def _fresh(self, rec: dict[str, Any]) -> bool:
        exp = rec.get("expires_at")
        return exp is None or float(exp) - self._now() > REFRESH_SKEW

    def access_token(self, *, force: bool = False, rejected: str | None = None) -> str:
        """Return an access token, refreshing when it expires within 60s.

        ``force`` with ``rejected`` is used after a 401: refresh unless another
        process has already replaced the rejected token.
        """
        rec = self._record or self._load()
        if rec is None:
            raise self.signed_out()
        if not force and self._fresh(rec):
            self._record = rec
            return str(rec["access_token"])
        with credential_lock():
            rec = self._load()
            if rec is None:
                raise self.signed_out()
            if self._fresh(rec) and not (force and rec["access_token"] == rejected):
                self._record = rec
                return str(rec["access_token"])
            rec = self._refresh_locked(rec)
            self._record = rec
            return str(rec["access_token"])

    def _refresh_locked(self, rec: dict[str, Any]) -> dict[str, Any]:
        rt = rec.get("refresh_token")
        endpoint = rec.get("token_endpoint")
        if not rt or not endpoint:
            raise self.signed_out()
        try:
            tokens = self._do_refresh(str(endpoint), str(rt))
        except oauth.OAuthError as first:
            # Another process (or an older hoja without the lock) may have
            # rotated the refresh token already: re-read storage once.
            again = self._load()
            if again is None:
                raise self.signed_out() from first
            if again.get("access_token") != rec.get("access_token") and self._fresh(again):
                return again
            if again.get("refresh_token") and again.get("refresh_token") != rt:
                try:
                    tokens = self._do_refresh(str(again.get("token_endpoint") or endpoint), str(again["refresh_token"]))
                except oauth.OAuthError as second:
                    raise self.signed_out() from second
                rec = again
            else:
                raise self.signed_out() from first
        new = dict(rec)
        new.update(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scope=tokens.scope or rec.get("scope", ""),
        )
        self.store.save(self.account_name, new)
        return new

    def _do_refresh(self, endpoint: str, refresh_token: str) -> oauth.TokenSet:
        factory = self._http_factory or oauth.default_http
        with factory() as http:
            return oauth.refresh(endpoint, refresh_token, origin=self.origin, http=http, now=self._now)


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


@dataclass
class ResolvedAuth:
    token: str
    base_url: str | None
    source: str  # "--token", "$HOJA_TOKEN" (or the account's token_env), "oauth", "config"
    session: OAuthSession | None = None


def resolve_auth(cli_token: str | None, account_name: str, account: AccountConfig) -> ResolvedAuth:
    """Pick the credential and endpoint for a command (see module docstring)."""
    env_var = account.token_env or "HOJA_TOKEN"
    if cli_token:
        return ResolvedAuth(cli_token, resolve_base_url(None, account), "--token")
    if env_with_legacy(env_var):
        used = env_var if os.environ.get(env_var) else LEGACY_ENV_ALIASES.get(env_var, env_var)
        return ResolvedAuth(resolve_token(None, account), resolve_base_url(None, account), f"${used}")
    if account.credential == "oauth" and account.origin:
        session = OAuthSession(account_name, account)
        env_url = env_with_legacy("HOJA_API_URL")
        if env_url and oauth.origin_of(env_url) != account.origin:
            raise AuthError(
                f"$HOJA_API_URL points at {env_url}, but account '{account_name}' is signed in to {account.origin}",
                hint="Unset $HOJA_API_URL, or set $HOJA_TOKEN to an API key for that endpoint.",
            )
        return ResolvedAuth("", env_url or session.base_url, "oauth", session)
    return ResolvedAuth(resolve_token(None, account), resolve_base_url(None, account), "config")
