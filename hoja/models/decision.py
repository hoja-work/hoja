"""Decision model: a question an agent asks on an issue (docs/decisions.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from hoja.models.base import Timestamped
from hoja.models.user import User


class IssueDecision(Timestamped):
    """A decision, as `hoja decision list|get|ask|wait` renders it."""

    id: str
    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    context: str | None = None
    status: str = "open"
    answered: bool = False
    asked_by_agent: str | None = Field(alias="askedByAgent", default=None)
    asked_by: User | None = Field(alias="askedBy", default=None)
    answer: str | None = None
    chosen_option: str | None = Field(alias="chosenOption", default=None)
    answered_by: User | None = Field(alias="answeredBy", default=None)
    answered_at: datetime | None = Field(alias="answeredAt", default=None)
