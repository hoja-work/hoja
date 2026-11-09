"""Read-only Sentry connector: installation OAuth, client, MCP tools."""

from sentry.config import SentryOAuthConfig
from sentry.errors import (
    ConnectorAPIError,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
)

__all__ = [
    "ConnectorAPIError",
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorError",
    "SentryOAuthConfig",
]
