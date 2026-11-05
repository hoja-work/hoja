"""Linear enums.

Generated from introspection output (schema/linear-schema.json).
Only the ones actually used in v1 are included.
"""

from __future__ import annotations

from enum import Enum, IntEnum


class IssuePriority(IntEnum):
    """Linear issue priority levels."""

    NO_PRIORITY = 0
    URGENT = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4

    @classmethod
    def parse(cls, value: str | int) -> "IssuePriority":
        """Lenient parser: accepts int (0-4) or name (case-insensitive)."""
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, str):
            v = value.strip().upper().replace(" ", "_").replace("-", "_")
            if v.isdigit():
                return cls(int(v))
            try:
                return cls[v]
            except KeyError as e:
                raise ValueError(
                    f"Invalid priority: {value!r}. "
                    f"Expected 0-4 or one of: {[p.name for p in cls]}"
                ) from e
        raise TypeError(f"Cannot parse priority from {type(value).__name__}")


class StateType(str, Enum):
    """Linear workflow state categories.

    These are the broad "buckets" — actual states are user-defined per team.
    """

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


class OutputFormat(str, Enum):
    """CLI output format selector."""

    HUMAN = "human"
    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "md"
    PLAIN = "plain"
    IDS = "ids"
