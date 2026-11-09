"""Read-only Notion operations. Each takes a grant resolved from the caller's principal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from notion.client import DatabaseInfo, DatabaseRow, NotionClient, PageContent, SearchResult
from notion.errors import ConnectorAuthError


@dataclass(frozen=True)
class AccountGrant:
    """A connected workspace the principal may use. `access_token` returns the stored token."""

    account_id: str
    external_account: str
    access_token: Callable[[], Awaitable[str]]


async def _client_for(http: httpx.AsyncClient, grant: AccountGrant) -> NotionClient:
    token = await grant.access_token()
    if not token:
        raise ConnectorAuthError(
            "Notion workspace has no usable access token.", hint="Reconnect the workspace."
        )
    return NotionClient(http, token)


async def search(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    query: str,
    *,
    object_type: str | None = None,
    max_results: int = 10,
) -> list[SearchResult]:
    return await (await _client_for(http, grant)).search(
        query, object_type=object_type, max_results=max_results
    )


async def read_page(http: httpx.AsyncClient, grant: AccountGrant, page_id: str) -> PageContent:
    return await (await _client_for(http, grant)).read_page(page_id)


async def read_database(
    http: httpx.AsyncClient, grant: AccountGrant, database_id: str
) -> DatabaseInfo:
    return await (await _client_for(http, grant)).read_database(database_id)


async def query_database(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    database_id: str,
    *,
    filter: dict[str, Any] | None = None,
    max_results: int = 10,
) -> list[DatabaseRow]:
    return await (await _client_for(http, grant)).query_database(
        database_id, filter=filter, max_results=max_results
    )
