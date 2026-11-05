"""Cycle model (Linear's sprint/iteration entity)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from hoja.models.base import Timestamped


class Cycle(Timestamped):
    """A Linear cycle (sprint)."""

    id: str
    name: str | None = None
    number: int
    description: str | None = None

    # Dates
    starts_at: datetime | None = Field(alias="startsAt", default=None)
    ends_at: datetime | None = Field(alias="endsAt", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)

    # Stats
    progress: float | None = None
    issue_count_history: list[int] | None = Field(
        alias="issueCountHistory", default=None
    )

    # Relations
    team_id: str | None = Field(alias="teamId", default=None)
