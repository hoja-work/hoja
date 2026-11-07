"""Deployment-owned configuration for application email, read once, fail-closed.

The rule mirrors ``signup_config``: an unconfigured deployment sends nothing and
says so, and a *half* configuration is a startup error rather than a silent
degradation. Sending mail is never a reason for a request to fail, so callers
check ``configured`` and skip enqueueing when it is false.

Two transports are supported, both first-class:

* ``cloudflare`` — Cloudflare Email Service (Email Sending). The submission
  server is authenticated with an API token, not a mailbox password, and the
  sending domain must be onboarded in the same Cloudflare account.
* ``smtp`` — any submission server, including a Google Workspace relay.

Nothing here performs network I/O, and no value is ever included in ``repr`` or
in a log line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

TRANSPORT_ENV = "HOJA_MAIL_TRANSPORT"
FROM_ENV = "HOJA_MAIL_FROM"
DOMAIN_ENV = "HOJA_MAIL_DOMAIN"
REPLY_TO_ENV = "HOJA_MAIL_REPLY_TO"
APP_URL_ENV = "HOJA_MAIL_APP_URL"

CF_ACCOUNT_ENV = "CLOUDFLARE_ACCOUNT_ID"
CF_TOKEN_ENV = "CLOUDFLARE_EMAIL_API_TOKEN"
#: The account-wide token name most tooling already exports. Accepted as a
#: fallback so a deployment does not have to hold the same secret twice.
CF_TOKEN_FALLBACK_ENV = "CLOUDFLARE_API_TOKEN"

SMTP_HOST_ENV = "HOJA_MAIL_SMTP_HOST"
SMTP_PORT_ENV = "HOJA_MAIL_SMTP_PORT"
SMTP_USER_ENV = "HOJA_MAIL_SMTP_USERNAME"
SMTP_PASSWORD_ENV = "HOJA_MAIL_SMTP_PASSWORD"
SMTP_TLS_ENV = "HOJA_MAIL_SMTP_TLS"

TRANSPORTS = ("disabled", "cloudflare", "smtp")
DEFAULT_DOMAIN = "hoja.work"
DEFAULT_CF_HOST = "smtp.mx.cloudflare.net"
DEFAULT_CF_PORT = 465


class MailConfigError(RuntimeError):
    """Email cannot be enabled safely with this environment."""


@dataclass(frozen=True)
class MailConfig:
    transport: str = "disabled"
    from_address: str = ""
    from_domain: str = DEFAULT_DOMAIN
    reply_to: str = ""
    app_url: str = ""
    account_id: str = ""
    api_token: str = field(default="", repr=False)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_tls: str = "starttls"

    @property
    def configured(self) -> bool:
        return self.transport in ("cloudflare", "smtp")

    def invitation_url(self, code: str) -> str:
        """The one accept link shape. A code is a path fragment, never a query."""
        base = self.app_url.rstrip("/")
        return f"{base}/#invite={code}"

    def describe(self) -> dict[str, object]:
        """Safe to log or return over an admin surface: no token, no password."""
        return {
            "transport": self.transport,
            "from": self.from_address,
            "replyTo": self.reply_to or None,
            "appUrl": self.app_url or None,
            "smtpHost": self.smtp_host or None,
            "smtpTls": self.smtp_tls if self.transport == "smtp" else None,
        }


def _required(env: dict[str, str], name: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise MailConfigError(f"{name} must be set when email is enabled")
    return value


def _https_url(value: str, name: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MailConfigError(f"{name} must be an http(s) URL")
    if parts.username or parts.password:
        raise MailConfigError(f"{name} must not embed credentials")
    if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise MailConfigError(f"{name} must use https outside localhost")
    return value.rstrip("/")


def load(environ: dict[str, str] | None = None) -> MailConfig:
    env = dict(os.environ if environ is None else environ)
    transport = (env.get(TRANSPORT_ENV) or "disabled").strip().lower()
    if transport not in TRANSPORTS:
        raise MailConfigError(
            f"{TRANSPORT_ENV} must be one of {', '.join(TRANSPORTS)}")
    if transport == "disabled":
        return MailConfig(transport="disabled", from_domain=DEFAULT_DOMAIN)

    from_address = _required(env, FROM_ENV).lower()
    local, sep, domain = from_address.partition("@")
    if not sep or not local or "." not in domain or " " in from_address:
        raise MailConfigError(f"{FROM_ENV} must be a plain address, not a display name")

    expected_domain = (env.get(DOMAIN_ENV) or DEFAULT_DOMAIN).strip().lower()
    if domain != expected_domain and not domain.endswith("." + expected_domain):
        # Sending as a lookalike domain is the failure this guard exists for.
        raise MailConfigError(
            f"{FROM_ENV} must be on {expected_domain} (or a subdomain of it)")

    reply_to = (env.get(REPLY_TO_ENV) or "").strip().lower()
    if reply_to and "@" not in reply_to:
        raise MailConfigError(f"{REPLY_TO_ENV} must be an email address")

    app_url = _https_url(_required(env, APP_URL_ENV), APP_URL_ENV)

    if transport == "cloudflare":
        account_id = _required(env, CF_ACCOUNT_ENV)
        api_token = (env.get(CF_TOKEN_ENV) or env.get(CF_TOKEN_FALLBACK_ENV) or "").strip()
        if not api_token:
            raise MailConfigError(
                f"{CF_TOKEN_ENV} (or {CF_TOKEN_FALLBACK_ENV}) must be set when email is enabled")
        if len(api_token) < 20:
            raise MailConfigError(f"{CF_TOKEN_ENV} does not look like an API token")
        return MailConfig(
            transport="cloudflare",
            from_address=from_address,
            from_domain=expected_domain,
            reply_to=reply_to,
            app_url=app_url,
            account_id=account_id,
            api_token=api_token,
            smtp_host=DEFAULT_CF_HOST,
            smtp_port=DEFAULT_CF_PORT,
            smtp_username="api_token",
            smtp_password=api_token,
            smtp_tls="implicit",
        )

    host = _required(env, SMTP_HOST_ENV)
    raw_port = (env.get(SMTP_PORT_ENV) or "587").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise MailConfigError(f"{SMTP_PORT_ENV} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise MailConfigError(f"{SMTP_PORT_ENV} must be between 1 and 65535")
    tls = (env.get(SMTP_TLS_ENV) or ("implicit" if port == 465 else "starttls")).strip().lower()
    if tls not in ("starttls", "implicit"):
        raise MailConfigError(f"{SMTP_TLS_ENV} must be starttls or implicit")
    username = (env.get(SMTP_USER_ENV) or "").strip()
    password = env.get(SMTP_PASSWORD_ENV) or ""
    if bool(username) != bool(password):
        raise MailConfigError(
            f"{SMTP_USER_ENV} and {SMTP_PASSWORD_ENV} must be supplied together")
    return MailConfig(
        transport="smtp",
        from_address=from_address,
        from_domain=expected_domain,
        reply_to=reply_to,
        app_url=app_url,
        smtp_host=host,
        smtp_port=port,
        smtp_username=username,
        smtp_password=password,
        smtp_tls=tls,
    )


_CACHED: MailConfig | None = None


def current() -> MailConfig:
    """Process-wide configuration, read once. ``load`` stays available for tests."""
    global _CACHED
    if _CACHED is None:
        _CACHED = load()
    return _CACHED


def reset_cache() -> None:
    global _CACHED
    _CACHED = None
