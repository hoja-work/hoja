"""Read-only Zoom connector: OAuth, client, MCP tools."""

from zoom.config import ZoomOAuthConfig
from zoom.errors import (
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
    "ZoomOAuthConfig",
]
