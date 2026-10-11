"""Deployer-supplied OAuth client settings. Each deployment brings its own Google Cloud client."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from google_workspace.errors import ConnectorConfigError

CLIENT_ID_ENV = "CLINIAR_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "CLINIAR_GOOGLE_CLIENT_SECRET"
REDIRECT_URI_ENV = "CLINIAR_GOOGLE_REDIRECT_URI"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GoogleOAuthConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint="Create an OAuth client in Google Cloud Console and set these variables.",
            )
        return cls(
            client_id=source[CLIENT_ID_ENV],
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=source[REDIRECT_URI_ENV],
        )

    def __repr__(self) -> str:
        return f"GoogleOAuthConfig(client_id={self.client_id!r}, client_secret='***', redirect_uri={self.redirect_uri!r})"
