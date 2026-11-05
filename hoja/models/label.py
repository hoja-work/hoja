"""Label model — surfaces from `IssueLabel` GraphQL type."""

from __future__ import annotations

from pydantic import Field

from hoja.models.base import Timestamped


class Label(Timestamped):
    """A Linear issue label."""

    id: str
    name: str
    color: str | None = None
    description: str | None = None
    team_id: str | None = Field(alias="teamId", default=None)
