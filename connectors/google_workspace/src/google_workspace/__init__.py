"""Google Workspace connector: OAuth helpers and read-only Gmail, Drive, Calendar, and Docs access."""

from google_workspace.client import GoogleWorkspaceClient
from google_workspace.config import GoogleOAuthConfig
from google_workspace.errors import (
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
    "GoogleOAuthConfig",
    "GoogleWorkspaceClient",
]
