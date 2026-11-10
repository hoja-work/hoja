"""User model (the upstream contract's User entity)."""

from __future__ import annotations

from pydantic import Field

from hoja.models.base import Timestamped


class User(Timestamped):
    """A the upstream API user (member of the workspace).

    GraphQL: type User implements Node
    """

    id: str
    name: str
    display_name: str | None = Field(alias="displayName", default=None)
    email: str | None = None
    active: bool = True
    admin: bool = False
    is_me: bool = Field(alias="isMe", default=False)
    avatar_url: str | None = Field(alias="avatarUrl", default=None)
    url: str | None = None
    timezone: str | None = None
    status_emoji: str | None = Field(alias="statusEmoji", default=None)
    status_label: str | None = Field(alias="statusLabel", default=None)

    @property
    def short(self) -> str:
        """Best one-line identifier for display."""
        return self.display_name or self.name or self.email or self.id
