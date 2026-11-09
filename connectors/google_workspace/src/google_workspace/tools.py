"""Read-only connector operations. Each takes a verified Principal and resolves accounts from the database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from google_workspace.client import (
    CHAT_DEFAULT_PAGE,
    CalendarEvent,
    ChatMembership,
    ChatMessage,
    ChatSpace,
    ConferenceRecord,
    Document,
    DriveFile,
    GmailMessage,
    GoogleWorkspaceClient,
    Participant,
    Recording,
    Transcript,
    TranscriptEntry,
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


async def meet_conference_records(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    filter: str | None = None,
    max_results: int = 25,
) -> list[ConferenceRecord]:
    return await (await _client_for(http, grant)).meet_conference_records(filter, max_results)


async def meet_participants(
    http: httpx.AsyncClient, grant: AccountGrant, conference_record: str, max_results: int = 100
) -> list[Participant]:
    return await (await _client_for(http, grant)).meet_participants(conference_record, max_results)


async def meet_recordings(
    http: httpx.AsyncClient, grant: AccountGrant, conference_record: str, max_results: int = 50
) -> list[Recording]:
    return await (await _client_for(http, grant)).meet_recordings(conference_record, max_results)


async def meet_transcripts(
    http: httpx.AsyncClient, grant: AccountGrant, conference_record: str, max_results: int = 50
) -> list[Transcript]:
    return await (await _client_for(http, grant)).meet_transcripts(conference_record, max_results)


async def meet_transcript_entries(
    http: httpx.AsyncClient, grant: AccountGrant, transcript: str, max_results: int = 100
) -> list[TranscriptEntry]:
    return await (await _client_for(http, grant)).meet_transcript_entries(transcript, max_results)


async def chat_spaces(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    filter: str | None = None,
    max_results: int = CHAT_DEFAULT_PAGE["spaces"],
) -> list[ChatSpace]:
    return await (await _client_for(http, grant)).chat_spaces(filter, max_results)


async def chat_messages(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    space: str,
    filter: str | None = None,
    order_by: str | None = None,
    max_results: int = CHAT_DEFAULT_PAGE["messages"],
) -> list[ChatMessage]:
    return await (await _client_for(http, grant)).chat_messages(
        space, filter=filter, order_by=order_by, max_results=max_results
    )


async def chat_message(http: httpx.AsyncClient, grant: AccountGrant, name: str) -> ChatMessage:
    return await (await _client_for(http, grant)).chat_message(name)


async def chat_members(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    space: str,
    filter: str | None = None,
    max_results: int = CHAT_DEFAULT_PAGE["members"],
) -> list[ChatMembership]:
    return await (await _client_for(http, grant)).chat_members(space, filter, max_results)


def require_read(principal: Principal) -> None:
    if principal.scope not in {"read", "write"}:
        raise ConnectorAuthError("Principal lacks read scope.")
