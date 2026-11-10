"""Read-only Calendly operations. Each takes a grant resolved from the caller's principal."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from calendly.client import (
    CalendlyClient,
    CalendlyUser,
    EventType,
    Invitee,
    ScheduledEvent,
    TokenSource,
)
from calendly.errors import ConnectorAuthError


@dataclass(frozen=True)
class AccountGrant:
    """A connected Calendly account the principal may use.

    `tokens` is the server-owned token source: it refreshes the rotating grant on demand and is
    the only thing that can see the stored credential. The connector package never receives a
    refresh token it could persist itself.
    """

    account_id: str
    external_account: str
    tokens: TokenSource


def _client(http: httpx.AsyncClient, grant: AccountGrant) -> CalendlyClient:
    return CalendlyClient(http, grant.tokens)


async def get_current_user(http: httpx.AsyncClient, grant: AccountGrant) -> CalendlyUser:
    return await _client(http, grant).get_current_user()


async def _user_uri(http: httpx.AsyncClient, grant: AccountGrant) -> str:
    """The authorizing user's own URI. Every list filter is scoped to it, so a tool call can
    never name a different user or organization than the one that consented."""
    user = await _client(http, grant).get_current_user()
    if not user.uri:
        raise ConnectorAuthError(
            "Calendly did not identify the connected account.",
            hint="Reconnect Calendly in Settings > Connections.",
        )
    return user.uri


async def list_event_types(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    max_results: int = 100,
    active: bool | None = True,
) -> list[EventType]:
    client = _client(http, grant)
    return await client.list_event_types(
        user_uri=await _user_uri(http, grant), max_results=max_results, active=active
    )


async def list_scheduled_events(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    max_results: int = 100,
    status: str | None = None,
    min_start_time: str | None = None,
    max_start_time: str | None = None,
) -> list[ScheduledEvent]:
    client = _client(http, grant)
    return await client.list_scheduled_events(
        user_uri=await _user_uri(http, grant),
        max_results=max_results,
        status=status,
        min_start_time=min_start_time,
        max_start_time=max_start_time,
    )


async def get_scheduled_event(
    http: httpx.AsyncClient, grant: AccountGrant, event_uuid: str
) -> ScheduledEvent:
    return await _client(http, grant).get_scheduled_event(event_uuid)


async def list_invitees(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    event_uuid: str,
    *,
    max_results: int = 100,
    status: str | None = None,
) -> list[Invitee]:
    return await _client(http, grant).list_invitees(
        event_uuid, max_results=max_results, status=status
    )
