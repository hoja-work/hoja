"""Saved view model — the fields ``hoja issue list --view`` needs to find one."""

from __future__ import annotations

from pydantic import Field

from hoja.models.base import LinearModel


class SavedView(LinearModel):
    """A saved view (``View`` in the GraphQL API)."""

    id: str
    name: str
    slug_id: str = Field(alias="slugId")
    kind: str = "issue"
