"""Deployer-supplied GitHub App settings.

A GitHub **App** is a confidential client: one client id, one client secret, HTTP Basic on the
application endpoints, and **no scopes at authorization time**. The permissions live on the app
registration (`metadata`, `contents`, `issues`, `pull_requests`, all read), so `/login/oauth/authorize`
is sent without a `scope` parameter and a user cannot decline an individual permission.

Two settings are deployment facts rather than assumptions, because GitHub refuses an unregistered
redirect server-side:

* the app holds an **allow list of up to ten callback URLs**, so a multi-tenant deployment pins one
  `https://<tenant>/api/connectors/github/callback` per tenant and needs no broker;
* the app is **private** until its owner makes it public, and a private app can only be authorized
  by members of the organization that owns it. That is why the install URL is surfaced: a private
  repository is readable only where the app is installed *and* the user can already see it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from github.errors import ConnectorConfigError

APP_ID_ENV = "HOJA_GITHUB_APP_ID"
APP_SLUG_ENV = "HOJA_GITHUB_APP_SLUG"
CLIENT_ID_ENV = "HOJA_GITHUB_CLIENT_ID"
CLIENT_SECRET_ENV = "HOJA_GITHUB_CLIENT_SECRET"
REDIRECT_URI_ENV = "HOJA_GITHUB_REDIRECT_URI"
REDIRECT_URIS_ENV = "HOJA_GITHUB_REDIRECT_URIS"
INSTALLATION_ID_ENV = "HOJA_GITHUB_INSTALLATION_ID"
API_BASE_ENV = "HOJA_GITHUB_API_BASE"
WEB_BASE_ENV = "HOJA_GITHUB_WEB_BASE"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)

# The path every tenant callback must end with. GitHub compares the redirect URI by string at both
# the authorize and the token endpoint, so it is built in one place.
REDIRECT_PATH = "/api/connectors/github/callback"
# GitHub.com's documented bases. GitHub Enterprise Server needs both overridden.
DEFAULT_API_BASE = "https://api.github.com"
DEFAULT_WEB_BASE = "https://github.com"

# App client ids look like `Iv1.0123456789abcdef` or `Iv23li4RW9Mh8RHVfCGi`. The shape is not a
# documented contract, so this only rejects values that cannot be one rather than inventing one.
_CLIENT_ID_PATTERN = re.compile(r"^Iv[0-9][A-Za-z0-9._-]{6,80}$")
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,99}$")


@dataclass(frozen=True)
class GitHubAppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    redirect_uris: frozenset[str] = frozenset()
    app_id: str | None = None
    slug: str | None = None
    api_base: str = DEFAULT_API_BASE
    web_base: str = DEFAULT_WEB_BASE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GitHubAppConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint=(
                    "Create a GitHub App under the owning organization, install it, then set "
                    "these variables on the Hoja server."
                ),
            )
        client_id = source[CLIENT_ID_ENV].strip()
        if not _CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ConnectorConfigError(
                f"{CLIENT_ID_ENV} is not a GitHub App client id.",
                hint="Copy the Client ID from the app's settings page (it starts with Iv).",
            )
        canonical = _checked_redirect(source[REDIRECT_URI_ENV])
        extra_raw = (source.get(REDIRECT_URIS_ENV) or "").replace(",", " ").split()
        allow = {canonical}
        for value in extra_raw:
            allow.add(_checked_redirect(value))
        slug = (source.get(APP_SLUG_ENV) or "").strip() or None
        if slug is not None and not _SLUG_PATTERN.fullmatch(slug):
            raise ConnectorConfigError(
                f"{APP_SLUG_ENV} is not a GitHub App slug.",
                hint="Use the last segment of the app's public URL: github.com/apps/<slug>.",
            )
        app_id = (source.get(APP_ID_ENV) or "").strip() or None
        if app_id is not None and not app_id.isdigit():
            raise ConnectorConfigError(
                f"{APP_ID_ENV} must be the numeric app id.",
                hint="The App ID is on the app's settings page, above the client id.",
            )
        api_base = _https_base(source.get(API_BASE_ENV), API_BASE_ENV, DEFAULT_API_BASE)
        web_base = _https_base(source.get(WEB_BASE_ENV), WEB_BASE_ENV, DEFAULT_WEB_BASE)
        return cls(
            client_id=client_id,
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=canonical,
            redirect_uris=frozenset(allow),
            app_id=app_id,
            slug=slug,
            api_base=api_base,
            web_base=web_base,
        )

    def redirect_uri_for(self, origin: str) -> str | None:
        """The allow-listed callback for one tenant origin, or None.

        Returning None stops the flow *before* GitHub: an unregistered redirect is answered with
        `redirect_uri_mismatch` on a page the user cannot act on.
        """
        candidate = f"{origin.rstrip('/')}{REDIRECT_PATH}"
        return candidate if candidate in self.redirect_uris else None

    def install_url(self) -> str | None:
        """Where a user grants the app access to repositories.

        Private repositories are readable only through an installation, and the user token is the
        intersection of user and app access, so this link is part of the connect flow rather than a
        nicety. None when the deployment did not record the slug.
        """
        if not self.slug:
            return None
        return f"{self.web_base}/apps/{self.slug}/installations/new"

    def __repr__(self) -> str:
        return (
            f"GitHubAppConfig(client_id={self.client_id!r}, client_secret='***', "
            f"redirect_uri={self.redirect_uri!r}, redirect_uris={len(self.redirect_uris)}, "
            f"app_id={self.app_id!r}, slug={self.slug!r}, api_base={self.api_base!r})"
        )


def _checked_redirect(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConnectorConfigError(
            f"{REDIRECT_URI_ENV} entries must be absolute https URLs.",
            hint="GitHub accepts only registered callback URLs and compares them by string.",
        )
    if not parsed.path.endswith(REDIRECT_PATH):
        raise ConnectorConfigError(
            f"Redirect URIs must end with {REDIRECT_PATH}.",
            hint="Register the same path GitHub will return the browser to.",
        )
    return value


def _https_base(value: str | None, name: str, default: str) -> str:
    base = (value or "").strip() or default
    if urlparse(base).scheme != "https" or not urlparse(base).netloc:
        raise ConnectorConfigError(
            f"{name} must be an absolute https URL.",
            hint=f"Leave it unset to use {default}.",
        )
    return base.rstrip("/")
