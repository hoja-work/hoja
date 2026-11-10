"""Read-only GitHub connector: GitHub App user-to-server OAuth, client, MCP tools."""

from github.config import GitHubAppConfig
from github.errors import (
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
    "GitHubAppConfig",
]
