"""Team model (Linear's Team entity)."""

from __future__ import annotations

from pydantic import Field

from cliniar.models.base import Timestamped


class Team(Timestamped):
    """A Linear team.

    GraphQL: type Team implements Node
    Notes:
    - `key` is the short identifier (e.g. "ENG") used in issue IDs like ENG-123.
    - Teams own issues, workflows, cycles, and labels.
    """

    id: str
    name: str
    key: str
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    visibility: str = "public"
    parent_team_id: str | None = Field(alias="parentTeamId", default=None)
    timezone: str | None = None

    # Counts (often included for at-a-glance views)
    issue_count: int | None = Field(alias="issueCount", default=None)
    member_count: int | None = Field(alias="memberCount", default=None)
