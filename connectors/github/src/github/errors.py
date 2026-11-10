"""Typed connector errors. Messages never contain tokens, secrets, or raw response bodies."""

from __future__ import annotations


class ConnectorError(RuntimeError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class ConnectorConfigError(ConnectorError):
    """Deployment configuration is missing or invalid (a deployer problem, not a user one)."""


class ConnectorAuthError(ConnectorError):
    """GitHub rejected the grant; the account must be reconnected."""


class ConnectorAPIError(ConnectorError):
    """GitHub returned an error or an unexpected response."""
