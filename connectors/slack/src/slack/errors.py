"""Typed connector errors. Messages never contain tokens, secrets, or raw response bodies."""


class ConnectorError(RuntimeError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class ConnectorConfigError(ConnectorError):
    """Deployment configuration is missing or invalid."""


class ConnectorAuthError(ConnectorError):
    """Slack rejected the credential; the workspace must be reconnected."""


class ConnectorAPIError(ConnectorError):
    """Slack returned an error or an unexpected response."""
