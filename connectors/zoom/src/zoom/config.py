"""Deployer-supplied Zoom OAuth settings.

A Zoom **General App** with user-managed OAuth is a confidential client: one client id, one client
secret, HTTP Basic at the token endpoint, no PKCE (`code_challenge` is supported by `/oauth/token`
but is not enabled on the provisioned app, so sending one would be rejected).

Unlike Calendly, Zoom's console accepts an **allow list** of redirect URIs, so a multi-tenant
deployment does not need a broker: each tenant returns to its own
`https://<tenant>/api/connectors/zoom/callback`, and that exact string must be registered in the
app. The list is configuration rather than an assumption, because Zoom refuses an unregistered
redirect server-side and the failure would otherwise surface as an opaque consent-screen error.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from zoom.errors import ConnectorConfigError

CLIENT_ID_ENV = "HOJA_ZOOM_CLIENT_ID"
CLIENT_SECRET_ENV = "HOJA_ZOOM_CLIENT_SECRET"
REDIRECT_URI_ENV = "HOJA_ZOOM_REDIRECT_URI"
REDIRECT_URIS_ENV = "HOJA_ZOOM_REDIRECT_URIS"
SECRET_TOKEN_ENV = "HOJA_ZOOM_SECRET_TOKEN"
API_BASE_ENV = "HOJA_ZOOM_API_BASE"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)

# The path every tenant callback must end with. Zoom compares the redirect URI byte for byte at
# both the authorize and the token endpoint, so it is built in one place.
REDIRECT_PATH = "/api/connectors/zoom/callback"
# Zoom's documented global base. A region-pinned account (EU, AU) is served from a different
# cluster and the token response carries the right one in `api_url`; see the README.
DEFAULT_API_BASE = "https://api.zoom.us/v2"

# Zoom client ids are 22-character URL-safe tokens; the shape is not documented, so the check only
# rejects values that cannot be a Zoom id rather than inventing one.
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass(frozen=True)
class ZoomOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    redirect_uris: frozenset[str] = frozenset()
    secret_token: str | None = None
    api_base: str = DEFAULT_API_BASE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ZoomOAuthConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint=(
                    "Create a Zoom General App with user-managed OAuth, then set these "
                    "variables on the Hoja server."
                ),
            )
        client_id = source[CLIENT_ID_ENV]
        if not _CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ConnectorConfigError(
                f"{CLIENT_ID_ENV} is not a Zoom client id.",
                hint="Copy the Client ID from the Zoom app's App Credentials page.",
            )
        canonical = _checked_redirect(source[REDIRECT_URI_ENV])
        extra_raw = (source.get(REDIRECT_URIS_ENV) or "").replace(",", " ").split()
        allow = {canonical}
        for value in extra_raw:
            allow.add(_checked_redirect(value))
        api_base = (source.get(API_BASE_ENV) or "").strip() or DEFAULT_API_BASE
        if urlparse(api_base).scheme != "https":
            raise ConnectorConfigError(
                f"{API_BASE_ENV} must be an absolute https URL.",
                hint="Leave it unset to use https://api.zoom.us/v2.",
            )
        return cls(
            client_id=client_id,
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=canonical,
            redirect_uris=frozenset(allow),
            secret_token=(source.get(SECRET_TOKEN_ENV) or "").strip() or None,
            api_base=api_base,
        )

    def redirect_uri_for(self, origin: str) -> str | None:
        """The allow-listed callback for one tenant origin, or None.

        Returning None is what stops the flow *before* Zoom: sending an unregistered redirect
        would be refused by Zoom with a message the user cannot act on.
        """
        candidate = f"{origin.rstrip('/')}{REDIRECT_PATH}"
        return candidate if candidate in self.redirect_uris else None

    def __repr__(self) -> str:
        return (
            f"ZoomOAuthConfig(client_id={self.client_id!r}, client_secret='***', "
            f"redirect_uri={self.redirect_uri!r}, redirect_uris={len(self.redirect_uris)}, "
            f"secret_token={'***' if self.secret_token else None}, api_base={self.api_base!r})"
        )


def _checked_redirect(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConnectorConfigError(
            f"{REDIRECT_URI_ENV} entries must be absolute https URLs.",
            hint="Zoom accepts only registered redirect URIs, and compares them byte for byte.",
        )
    if not parsed.path.endswith(REDIRECT_PATH):
        raise ConnectorConfigError(
            f"Redirect URIs must end with {REDIRECT_PATH}.",
            hint="Register the same path Zoom will return the browser to.",
        )
    return value
