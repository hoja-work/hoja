"""Read-only Slack connector: OAuth, client, MCP tools."""

from slack.config import SlackOAuthConfig
from slack.errors import (
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
    "SlackOAuthConfig",
]
