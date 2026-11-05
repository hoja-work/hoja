"""Workflow state model.

A workflow state is a team-defined column like "Todo", "In Progress", "Done".
Each maps to a broader StateType (backlog / started / completed / etc.).
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator

from hoja.models.base import Timestamped


class WorkflowStateType(str, Enum):
    """Linear's documented workflow state categories.

    Linear can return additional, undocumented state types (e.g. ``"duplicate"``)
    for teams that enable extra workflow behaviors. Those values must NOT crash
    the CLI — see ``WorkflowState.type`` for how unknown types are preserved.
    """

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"

    @classmethod
    def coerce(cls, value: object) -> "WorkflowStateType | str":
        """Return the enum member for known types, else the raw string.

        Both branches are ``str`` subclasses, so downstream formatting,
        equality checks and JSON/YAML serialization behave identically.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                return value
        return str(value)


class WorkflowState(Timestamped):
    """A team's workflow state (e.g. 'Todo', 'In Progress', 'Done')."""

    id: str
    name: str
    color: str | None = None
    description: str | None = None
    position: float | None = None
    # Smart union: Linear may emit state types outside the documented enum
    # (e.g. "duplicate"). Keep those as a plain string instead of raising a
    # pydantic ValidationError that would break `team states` / `issue state`.
    type: WorkflowStateType | str
    team_id: str | None = Field(alias="teamId", default=None)

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: object) -> "WorkflowStateType | str":
        return WorkflowStateType.coerce(v)
