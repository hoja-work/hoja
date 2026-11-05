"""Base Pydantic models shared across Linear entities."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class LinearModel(BaseModel):
    """Base class for all Linear entities.

    Configuration:
    - extra="ignore": Linear adds new fields often; don't fail on unknown.
    - populate_by_name: Accept both alias and field name.
    - frozen=False: Allow mutation; mutations happen via API not local edits.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class PageInfo(BaseModel):
    """GraphQL Relay-style pagination info."""

    model_config = ConfigDict(extra="ignore")

    has_next_page: bool = Field(alias="hasNextPage", default=False)
    has_previous_page: bool = Field(alias="hasPreviousPage", default=False)
    start_cursor: str | None = Field(alias="startCursor", default=None)
    end_cursor: str | None = Field(alias="endCursor", default=None)


class Connection(BaseModel, Generic[T]):
    """Generic GraphQL connection wrapper.

    Linear paginates all list queries using this Relay-style envelope:
      { nodes: [T], pageInfo: PageInfo }
    """

    model_config = ConfigDict(extra="ignore")

    nodes: list[T] = Field(default_factory=list)
    page_info: PageInfo = Field(alias="pageInfo", default_factory=PageInfo)


class Timestamped(LinearModel):
    """Mixin for entities with createdAt/updatedAt."""

    created_at: datetime | None = Field(alias="createdAt", default=None)
    updated_at: datetime | None = Field(alias="updatedAt", default=None)
    archived_at: datetime | None = Field(alias="archivedAt", default=None)


class Identified(LinearModel):
    """Mixin for entities with stable id."""

    id: str
