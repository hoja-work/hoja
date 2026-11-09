"""Deployer-supplied Slack OAuth settings. Each deployment brings its own Slack app.

Only three values are required to run the connector. The signing secret is accepted but never
required: nothing in this connector consumes Slack events, so demanding it would make a
deployment that can work refuse to start. It is validated when present (32 hex characters) so a
typo is caught here rather than at the future Request URL that will use it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from slack.errors import ConnectorConfigError

CLIENT_ID_ENV = "HOJA_SLACK_CLIENT_ID"
CLIENT_SECRET_ENV = "HOJA_SLACK_CLIENT_SECRET"
REDIRECT_URI_ENV = "HOJA_SLACK_REDIRECT_URI"
SIGNING_SECRET_ENV = "HOJA_SLACK_SIGNING_SECRET"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)

# Slack client ids look like `<app digits>.<bot digits>` (the provisioning contract recorded the
# live app as `12530628441030.12542913043347`). Both secrets are 32 lowercase hex characters.
_CLIENT_ID_PATTERN = re.compile(r"^\d{10,}\.\d{10,}$")
_HEX32_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class SlackOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    signing_secret: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SlackOAuthConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint=(
                    "Create a Slack app with user scopes only, then set these variables on the "
                    "Hoja server."
                ),
            )
        client_id = source[CLIENT_ID_ENV]
        if not _CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ConnectorConfigError(
                f"{CLIENT_ID_ENV} is not a Slack client id (`<app digits>.<bot digits>`).",
                hint="Copy the Client ID from the Slack app's Basic Information page.",
            )
        signing_secret = (source.get(SIGNING_SECRET_ENV) or "").strip() or None
        if signing_secret is not None and not _HEX32_PATTERN.fullmatch(signing_secret):
            raise ConnectorConfigError(
                f"{SIGNING_SECRET_ENV} is set but is not 32 hex characters.",
                hint="Copy the Signing Secret from the Slack app's Basic Information page.",
            )
        return cls(
            client_id=client_id,
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=source[REDIRECT_URI_ENV],
            signing_secret=signing_secret,
        )

    def __repr__(self) -> str:
        return (
            f"SlackOAuthConfig(client_id={self.client_id!r}, "
            f"client_secret='***', redirect_uri={self.redirect_uri!r}, "
            f"signing_secret={'***' if self.signing_secret else None})"
        )
