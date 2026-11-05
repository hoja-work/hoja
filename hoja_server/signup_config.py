"""Configuration for the hosted sign-up surface, read once, fail-closed.

The gate must never be permissive because a variable was forgotten. Three
rules follow, and they are the whole point of this module:

1. ``HOJA_SIGNUP_MODE`` defaults to ``closed``. An unconfigured deployment
   refuses every sign-up with ``signup_closed`` instead of allowing one.
2. A mode other than ``closed`` **requires** all five Google/Turnstile
   variables. Missing any of them raises :class:`SignupConfigError` at app
   startup — a clear error, not a silent allow.
3. A *partially* configured deployment (some set, some empty) raises too,
   whatever the mode: half a flow is a mistake, not a configuration.

Nothing in this module talks to the network or to the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

SIGNUP_MODES = ("closed", "invite", "open")

#: The exact environment variable names the gate reads. These are the contract.
REQUIRED_ENV = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "TURNSTILE_SECRET_KEY",
    "TURNSTILE_EXPECTED_HOSTNAME",
)

MODE_ENV = "HOJA_SIGNUP_MODE"
REDIRECT_ALLOWLIST_ENV = "GOOGLE_REDIRECT_URI_ALLOWLIST"
HD_ENV = "HOJA_GOOGLE_HD"
IDENTITY_CAP_ENV = "HOJA_SIGNUP_IDENTITY_CAP"
IP_CAP_ENV = "HOJA_SIGNUP_IP_CAP"
IP_WINDOW_ENV = "HOJA_SIGNUP_IP_WINDOW_SECONDS"
TENANT_REGISTRY_ENV = "HOJA_TENANT_REGISTRY"


class SignupConfigError(RuntimeError):
    """The sign-up surface cannot be enabled safely with this environment."""


def _int_env(environ, name: str, default: int, *, minimum: int = 1) -> int:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SignupConfigError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < minimum:
        raise SignupConfigError(f"{name} must be at least {minimum}, got {value}.")
    return value


def _validate_redirect_uri(uri: str) -> str:
    """An absolute URI with no query or fragment, https (or loopback http)."""
    parts = urlsplit(uri)
    if not parts.scheme or not parts.netloc:
        raise SignupConfigError(f"GOOGLE_REDIRECT_URI must be an absolute URI, got {uri!r}.")
    if parts.query or parts.fragment:
        raise SignupConfigError(
            f"GOOGLE_REDIRECT_URI must not carry a query or fragment, got {uri!r}.")
    loopback = (parts.hostname or "") in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise SignupConfigError(
            f"GOOGLE_REDIRECT_URI must be https (http only for loopback), got {uri!r}.")
    return uri


def _validate_hostname(value: str) -> str:
    if any(ch in value for ch in "/?#@") or "://" in value:
        raise SignupConfigError(
            f"TURNSTILE_EXPECTED_HOSTNAME must be a bare hostname, got {value!r}.")
    return value.strip().lower()


@dataclass(frozen=True)
class SignupConfig:
    """The validated sign-up configuration. ``configured`` gates the endpoints."""

    mode: str = "closed"
    google_client_id: str = ""
    google_client_secret: str = ""
    redirect_uris: tuple[str, ...] = ()
    turnstile_secret_key: str = ""
    turnstile_expected_hostname: str = ""
    google_hd: str | None = None
    identity_cap: int = 1
    ip_cap: int = 2
    ip_window_seconds: int = 24 * 60 * 60
    tenant_registry: str | None = None
    flow_ttl_seconds: int = 600
    session_days: int = 30

    @property
    def configured(self) -> bool:
        """True when every credential the OIDC + Turnstile path needs is present."""
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.redirect_uris
            and self.turnstile_secret_key
            and self.turnstile_expected_hostname
        )

    def redirect_uri_for(self, host: str) -> str | None:
        """The exact allow-listed redirect URI for ``host``, or None.

        One hostname, one URI: the URI is compared as a whole string later, so a
        host that is not on the list simply has no redirect URI and the flow
        cannot start.
        """
        wanted = (host or "").strip().lower().split(":")[0]
        for uri in self.redirect_uris:
            if (urlsplit(uri).hostname or "").lower() == wanted:
                return uri
        return None


def load_signup_config(environ: dict[str, str] | None = None) -> SignupConfig:
    """Parse and validate the environment. Raises :class:`SignupConfigError`."""
    env = os.environ if environ is None else environ

    mode = (env.get(MODE_ENV) or "closed").strip().lower()
    if mode not in SIGNUP_MODES:
        raise SignupConfigError(
            f"{MODE_ENV} must be one of {', '.join(SIGNUP_MODES)}; got {mode!r}.")

    values = {name: (env.get(name) or "").strip() for name in REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]

    if mode != "closed" and missing:
        raise SignupConfigError(
            f"{MODE_ENV}={mode} requires {', '.join(missing)}. "
            "Sign-up stays closed until every one is set "
            f"(set {MODE_ENV}=closed to run without them).")
    if missing and len(missing) != len(REQUIRED_ENV):
        raise SignupConfigError(
            "The sign-up configuration is incomplete: missing "
            f"{', '.join(missing)} (present: "
            f"{', '.join(n for n in REQUIRED_ENV if n not in missing)}). "
            "Refusing to start rather than half-enable the gate.")

    allowlist = (env.get(REDIRECT_ALLOWLIST_ENV) or "").strip()
    raw_uris = [item.strip() for item in allowlist.split(",") if item.strip()]
    if not raw_uris and values["GOOGLE_REDIRECT_URI"]:
        raw_uris = [values["GOOGLE_REDIRECT_URI"]]
    redirect_uris = tuple(dict.fromkeys(_validate_redirect_uri(uri) for uri in raw_uris))

    hostname = _validate_hostname(values["TURNSTILE_EXPECTED_HOSTNAME"]) if values[
        "TURNSTILE_EXPECTED_HOSTNAME"] else ""

    return SignupConfig(
        mode=mode,
        google_client_id=values["GOOGLE_CLIENT_ID"],
        google_client_secret=values["GOOGLE_CLIENT_SECRET"],
        redirect_uris=redirect_uris,
        turnstile_secret_key=values["TURNSTILE_SECRET_KEY"],
        turnstile_expected_hostname=hostname,
        google_hd=(env.get(HD_ENV) or "").strip().lower() or None,
        identity_cap=_int_env(env, IDENTITY_CAP_ENV, 1),
        ip_cap=_int_env(env, IP_CAP_ENV, 2),
        ip_window_seconds=_int_env(env, IP_WINDOW_ENV, 24 * 60 * 60, minimum=60),
        tenant_registry=(env.get(TENANT_REGISTRY_ENV) or "").strip() or None,
    )
