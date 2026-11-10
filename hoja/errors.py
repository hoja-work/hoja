"""Error hierarchy and exit codes for hoja.

Exit codes are stable across versions — scripts can rely on them.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """Stable CLI exit codes. Do not reorder."""

    SUCCESS = 0
    GENERIC_ERROR = 1
    USAGE_ERROR = 2
    AUTH_ERROR = 3
    NOT_FOUND = 4
    VALIDATION_ERROR = 5
    API_ERROR = 6
    NETWORK_ERROR = 7
    RATE_LIMITED = 8


class HojaError(Exception):
    """Base class for all hoja errors."""

    exit_code: ExitCode = ExitCode.GENERIC_ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        """Serialize for `-o json` output on error."""
        return {
            "error": {
                "type": self.__class__.__name__,
                "code": int(self.exit_code),
                "message": self.message,
                "hint": self.hint,
            }
        }


class AuthError(HojaError):
    """Missing or invalid the upstream API token."""

    exit_code = ExitCode.AUTH_ERROR


class NotFoundError(HojaError):
    """Requested entity does not exist."""

    exit_code = ExitCode.NOT_FOUND


class ValidationError(HojaError):
    """Response did not match expected Pydantic model."""

    exit_code = ExitCode.VALIDATION_ERROR


class APIError(HojaError):
    """The GraphQL API returned an error."""

    exit_code = ExitCode.API_ERROR

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["error"]["linear_errors"] = self.errors
        return d


class NetworkError(HojaError):
    """HTTP transport error: timeout, DNS, TLS."""

    exit_code = ExitCode.NETWORK_ERROR


class RateLimitError(HojaError):
    """The API rate limit was exceeded."""

    exit_code = ExitCode.RATE_LIMITED

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        hint = f"Retry after {retry_after} seconds" if retry_after else None
        super().__init__(message, hint=hint)
        self.retry_after = retry_after


class UsageError(HojaError):
    """User supplied invalid arguments."""

    exit_code = ExitCode.USAGE_ERROR
