"""Deployer-supplied OAuth client settings. Each deployment brings its own Notion connection."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from notion.errors import ConnectorConfigError

CLIENT_ID_ENV = "HOJA_NOTION_CLIENT_ID"
CLIENT_SECRET_ENV = "HOJA_NOTION_CLIENT_SECRET"
REDIRECT_URI_ENV = "HOJA_NOTION_REDIRECT_URI"
_REQUIRED = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV)


@dataclass(frozen=True)
class NotionOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> NotionOAuthConfig:
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name)]
        if missing:
            raise ConnectorConfigError(
                f"Missing environment variables: {', '.join(missing)}.",
                hint="Create an OAuth connection in the Notion developer portal and set these variables.",
            )
        return cls(
            client_id=source[CLIENT_ID_ENV],
            client_secret=source[CLIENT_SECRET_ENV],
            redirect_uri=source[REDIRECT_URI_ENV],
        )

    def __repr__(self) -> str:
        return (
            f"NotionOAuthConfig(client_id={self.client_id!r}, "
            f"client_secret='***', redirect_uri={self.redirect_uri!r})"
        )
