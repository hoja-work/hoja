"""Project model.

A the upstream API project groups related issues across one or more teams toward a goal
(e.g. "Q2 Roadmap", "Auth Refactor"). Has lead, members, target date, and progress.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import Field

from hoja.models.base import Timestamped
from hoja.models.user import User


class ProjectState(str, Enum):
    """the upstream API project lifecycle states."""

    BACKLOG = "backlog"
    PLANNED = "planned"
    STARTED = "started"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"


class Project(Timestamped):
    """A the upstream API project."""

    id: str
    name: str
    description: str | None = None
    slug_id: str | None = Field(alias="slugId", default=None)
    icon: str | None = None
    color: str | None = None
    state: ProjectState | str | None = None
    progress: float | None = None  # 0.0 - 1.0

    # Relations
    lead: User | None = None
    creator: User | None = None
    members: list[User] = Field(default_factory=list)

    # Dates
    start_date: date | None = Field(alias="startDate", default=None)
    target_date: date | None = Field(alias="targetDate", default=None)
    started_at: str | None = Field(alias="startedAt", default=None)
    completed_at: str | None = Field(alias="completedAt", default=None)
    canceled_at: str | None = Field(alias="canceledAt", default=None)

    # Metadata
    url: str | None = None
    issue_count_history: list[int] | None = Field(
        alias="issueCountHistory", default=None
    )
    completed_issue_count_history: list[int] | None = Field(
        alias="completedIssueCountHistory", default=None
    )
