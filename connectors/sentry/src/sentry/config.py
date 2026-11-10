"""Deployer-supplied Sentry integration settings.

A Sentry **public integration** is one server-side OAuth client (`client_id` + `client_secret`) that
any number of Sentry organizations install. The integration slug is a public identifier: it appears
in the install URL (`https://sentry.io/sentry-apps/{slug}/external-install/`), so it is configuration
rather than a secret.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from sentry.errors import ConnectorConfigError

CLIENT_ID_ENV = "HOJA_SENTRY_CLIENT_ID"
CLIENT_SECRET_ENV = "HOJA_SENTRY_CLIENT_SECRET"
REDIRECT_URI_ENV = "HOJA_SENTRY_REDIRECT_URI"
INTEGRATION_SLUG_ENV = "HOJA_SENTRY_INTEGRATION_SLUG"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)

# Sentry client ids are 64 hex characters on the public-integration settings page. The check only
# rejects values that cannot be one, rather than inventing a stricter shape.
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
# The slug is a path segment of the install URL; anything else would build a different URL.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class SentryOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    integration_slug: str = "hoja"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SentryOAuthConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint=(
                    "Create a public integration at sentry.io/settings/developer-settings/, then "
                    "set these variables on the Hoja server."
                ),
            )
        client_id = source[CLIENT_ID_ENV]
        if not _CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ConnectorConfigError(
                f"{CLIENT_ID_ENV} is not a Sentry client id.",
                hint="Copy the Client ID from the public integration's settings page.",
            )
        redirect_uri = source[REDIRECT_URI_ENV]
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConnectorConfigError(
                f"{REDIRECT_URI_ENV} must be an absolute https URL.",
                hint=(
                    "A public integration has exactly one Redirect URL field; it must be the URL "
                    "this deployment serves at /api/connectors/sentry/callback."
                ),
            )
        slug = (source.get(INTEGRATION_SLUG_ENV) or "hoja").strip()
        if not _SLUG_PATTERN.fullmatch(slug):
            raise ConnectorConfigError(
                f"{INTEGRATION_SLUG_ENV} is not a Sentry integration slug.",
                hint="Use the slug shown in the integration's settings page, e.g. 'hoja'.",
            )
        return cls(
            client_id=client_id,
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=redirect_uri,
            integration_slug=slug,
        )

    def __repr__(self) -> str:
        return (
            f"SentryOAuthConfig(client_id={self.client_id!r}, client_secret='***', "
            f"redirect_uri={self.redirect_uri!r}, integration_slug={self.integration_slug!r})"
        )
