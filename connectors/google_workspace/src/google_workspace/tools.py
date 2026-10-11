"""Read-only connector operations. Each takes a verified Principal and resolves accounts from the database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from google_workspace.client import (
    CalendarEvent,
    Document,
    DriveFile,
    GmailMessage,
    GoogleWorkspaceClient,
)
from google_workspace.errors import ConnectorAuthError
from google_workspace.principal import Principal


@dataclass(frozen=True)
class AccountGrant:
    """A connected account the principal may use. `access_token` returns a fresh token."""

    account_id: str
    external_account: str
    access_token: Callable[[], Awaitable[str]]


async def _client_for(http: httpx.AsyncClient, grant: AccountGrant) -> GoogleWorkspaceClient:
    token = await grant.access_token()
    if not token:
        raise ConnectorAuthError(
            "Google account has no usable access token.", hint="Reconnect the account."
        )
    return GoogleWorkspaceClient(http, token)


async def gmail_search(
    http: httpx.AsyncClient, grant: AccountGrant, query: str, max_results: int = 10
) -> list[GmailMessage]:
    return await (await _client_for(http, grant)).gmail_search(query, max_results)


async def drive_search(
    http: httpx.AsyncClient, grant: AccountGrant, query: str, max_results: int = 10
) -> list[DriveFile]:
    return await (await _client_for(http, grant)).drive_search(query, max_results)


async def calendar_events(
    http: httpx.AsyncClient, grant: AccountGrant, start: datetime, end: datetime
) -> list[CalendarEvent]:
    return await (await _client_for(http, grant)).calendar_events(start, end)


async def docs_get(http: httpx.AsyncClient, grant: AccountGrant, document_id: str) -> Document:
    return await (await _client_for(http, grant)).docs_get(document_id)


def require_read(principal: Principal) -> None:
    if principal.scope not in {"read", "write"}:
        raise ConnectorAuthError("Principal lacks read scope.")
