"""Read-only Slack operations. Each takes a grant resolved from the caller's principal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from slack.client import Channel, Message, SearchResult, SlackClient, User
from slack.errors import ConnectorAuthError


@dataclass(frozen=True)
class AccountGrant:
    """A connected workspace the principal may use. `access_token` returns the stored token."""

    account_id: str
    external_account: str
    access_token: Callable[[], Awaitable[str]]


async def _client_for(http: httpx.AsyncClient, grant: AccountGrant) -> SlackClient:
    token = await grant.access_token()
    if not token:
        raise ConnectorAuthError(
            "Slack workspace has no usable access token.", hint="Reconnect the workspace."
        )
    return SlackClient(http, token)


async def list_channels(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    max_results: int = 100,
    include_private: bool = True,
) -> list[Channel]:
    return await (await _client_for(http, grant)).list_channels(
        max_results=max_results, include_private=include_private
    )


async def read_channel_history(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    channel: str,
    *,
    max_results: int = 50,
    oldest: str | None = None,
    latest: str | None = None,
) -> list[Message]:
    return await (await _client_for(http, grant)).read_channel_history(
        channel, max_results=max_results, oldest=oldest, latest=latest
    )


async def search_messages(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    query: str,
    *,
    max_results: int = 20,
    sort: str = "score",
) -> SearchResult:
    return await (await _client_for(http, grant)).search_messages(
        query, max_results=max_results, sort=sort
    )


async def lookup_users(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    max_results: int = 100,
    query: str | None = None,
) -> list[User]:
    return await (await _client_for(http, grant)).lookup_users(
        max_results=max_results, query=query
    )
