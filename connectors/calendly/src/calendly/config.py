"""Deployer-supplied Calendly OAuth settings.

Endpoint choices are taken from Calendly's RFC 8414 metadata
(`https://calendly.com/.well-known/oauth-authorization-server`), not from the developer console
prose: the metadata advertises `calendly.com/oauth/{authorize,token}` and `auth.calendly.com`
answers 301 to those. Both hosts work today; the advertised one is used so a future change in the
former cannot silently break the connector.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from calendly.errors import ConnectorConfigError

CLIENT_ID_ENV = "HOJA_CALENDLY_CLIENT_ID"
CLIENT_SECRET_ENV = "HOJA_CALENDLY_CLIENT_SECRET"
REDIRECT_URI_ENV = "HOJA_CALENDLY_REDIRECT_URI"
WEBHOOK_SIGNING_KEY_ENV = "HOJA_CALENDLY_WEBHOOK_SIGNING_KEY"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)

# Calendly client ids are URL-safe tokens (`v_` + base62/base64url). Length is not documented, so
# the check only rejects values that cannot be a Calendly id, rather than inventing a shape.
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True)
class CalendlyOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    webhook_signing_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CalendlyOAuthConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint=(
                    "Create a Calendly OAuth app at developer.calendly.com/console/apps, then "
                    "set these variables on the Hoja server."
                ),
            )
        client_id = source[CLIENT_ID_ENV]
        if not _CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ConnectorConfigError(
                f"{CLIENT_ID_ENV} is not a Calendly client id.",
                hint="Copy the Client ID from the Calendly app's settings page.",
            )
        redirect_uri = source[REDIRECT_URI_ENV]
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConnectorConfigError(
                f"{REDIRECT_URI_ENV} must be an absolute https URL.",
                hint=(
                    "Calendly accepts exactly one redirect URI per app, and it must match the "
                    "authorize and token requests byte for byte."
                ),
            )
        key = (source.get(WEBHOOK_SIGNING_KEY_ENV) or "").strip() or None
        return cls(
            client_id=client_id,
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=redirect_uri,
            webhook_signing_key=key,
        )

    def __repr__(self) -> str:
        return (
            f"CalendlyOAuthConfig(client_id={self.client_id!r}, client_secret='***', "
            f"redirect_uri={self.redirect_uri!r}, "
            f"webhook_signing_key={'***' if self.webhook_signing_key else None})"
        )
