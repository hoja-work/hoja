"""Read-only Grafana connector for Hoja.

The package is the provider half: URL validation and address pinning, a typed read-only REST client
and the six MCP tools. Hoja's server supplies the route, the sealed token and the account rows.
"""

from grafana.config import GrafanaConfig
from grafana.errors import (
    ConnectorAPIError,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    UnsafeInstanceError,
)
from grafana.tools import AccountGrant

__all__ = [
    "AccountGrant",
    "ConnectorAPIError",
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorError",
    "GrafanaConfig",
    "UnsafeInstanceError",
]
