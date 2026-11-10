"""Typed connector errors. Messages never contain tokens, secrets or raw response bodies."""


class ConnectorError(RuntimeError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class ConnectorConfigError(ConnectorError):
    """Deployment configuration is missing or invalid (a deployer problem, not a user one)."""


class ConnectorAuthError(ConnectorError):
    """Grafana rejected the credential; the account must be reconnected."""


class ConnectorAPIError(ConnectorError):
    """Grafana returned an error or an unexpected response."""


class UnsafeInstanceError(ConnectorError):
    """The customer-supplied instance URL is not safe to fetch (SSRF guard)."""
