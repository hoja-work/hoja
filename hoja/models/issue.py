"""Issue model — the most important Linear entity.

A Linear issue is a unit of work. It belongs to a team, has a workflow state,
optionally an assignee, project, cycle, labels, and comments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from hoja.models.base import Timestamped
from hoja.models.enums import IssuePriority
from hoja.models.user import User
from hoja.models.workflow import WorkflowState


class IssueLabel(Timestamped):
    """A label attached to an issue."""

    id: str
    name: str
    color: str | None = None
    description: str | None = None


class Comment(Timestamped):
    """A comment on an issue."""

    id: str
    body: str
    user: User | None = None
    url: str | None = None


def _flatten_connection(v: Any) -> Any:
    """Unwrap Linear's Connection {nodes: [...]} into a flat list."""
    if isinstance(v, dict) and "nodes" in v:
        return v["nodes"]
    return v


class Issue(Timestamped):
    """A Linear issue.

    GraphQL: type Issue implements Node
    """

    id: str
    identifier: str  # "ENG-123"
    title: str
    description: str | None = None
    priority: IssuePriority = IssuePriority.NO_PRIORITY
    priority_label: str | None = Field(alias="priorityLabel", default=None)
    estimate: float | None = None

    # Status / state
    state: WorkflowState | None = None

    # Relations
    assignee: User | None = None
    creator: User | None = None
    team: dict | None = None  # lightweight ref; full Team model on demand
    project: dict | None = None
    cycle: dict | None = None
    parent: dict | None = None

    # Connection-wrapped relations — flattened by validator
    subscribers: list[User] | None = None
    labels: list[IssueLabel] | None = None

    @field_validator("subscribers", "labels", mode="before")
    @classmethod
    def _flatten(cls, v: Any) -> Any:
        return _flatten_connection(v)

    # Dates
    due_date: datetime | None = Field(alias="dueDate", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)
    canceled_at: datetime | None = Field(alias="canceledAt", default=None)
    started_at: datetime | None = Field(alias="startedAt", default=None)
    snoozed_until_at: datetime | None = Field(alias="snoozedUntilAt", default=None)

    # Metadata
    branch_name: str | None = Field(alias="branchName", default=None)
    url: str | None = None
    number: int | None = None

    @property
    def state_name(self) -> str:
        """Human-readable state name, or '?' if missing."""
        return self.state.name if self.state else "?"

    @property
    def assignee_name(self) -> str:
        """Display-friendly assignee name, or '—' if unassigned."""
        return self.assignee.short if self.assignee else "—"
