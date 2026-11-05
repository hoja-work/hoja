"""Authentication helpers.

Token resolution lives in `config.py`. This module wraps viewer caching
so we can resolve "me" without an extra API call when the user passes
`--assignee me`.
"""

from __future__ import annotations

import asyncio

from hoja.client import LinearClient
from hoja.graphql import queries
from hoja.models.user import User

_viewer_cache: User | None = None
_viewer_lock = asyncio.Lock()


async def get_viewer(client: LinearClient, *, refresh: bool = False) -> User:
    """Return the currently-authenticated user.

    Cached per-process so repeated --assignee me resolutions don't
    cost an API call each.
    """
    global _viewer_cache
    if _viewer_cache is not None and not refresh:
        return _viewer_cache

    async with _viewer_lock:
        if _viewer_cache is not None and not refresh:
            return _viewer_cache
        _viewer_cache = await client.execute_as(
            User, queries.VIEWER, path=["viewer"], operation="Viewer"
        )
        return _viewer_cache


def reset_viewer_cache() -> None:
    """Reset cached viewer (used after logout/token change)."""
    global _viewer_cache
    _viewer_cache = None
