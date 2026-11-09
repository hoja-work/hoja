"""Read-only Zoom operations. Each takes a grant resolved from the caller's principal."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from zoom.client import (
    Page,
    RecordingMeeting,
    TokenSource,
    ZoomClient,
    ZoomMeeting,
    ZoomTranscript,
    ZoomUser,
)
from zoom.errors import ConnectorAuthError


@dataclass(frozen=True)
class AccountGrant:
    """A connected Zoom account the principal may use.

    `tokens` is the server-owned token source: it refreshes the rotating grant on demand and is the
    only thing that can see the stored credential. The connector package never receives a refresh
    token it could persist itself.
    """

    account_id: str
    external_account: str
    tokens: TokenSource


def _client(http: httpx.AsyncClient, grant: AccountGrant) -> ZoomClient:
    return ZoomClient(http, grant.tokens)


async def get_current_user(http: httpx.AsyncClient, grant: AccountGrant) -> ZoomUser:
    return await _client(http, grant).get_current_user()


async def list_meetings(
    http: httpx.AsyncClient, grant: AccountGrant, *, meeting_type: str = "scheduled", max_results: int = 50
) -> Page:
    return await _client(http, grant).list_meetings(
        meeting_type=meeting_type, max_results=max_results
    )


async def get_meeting(
    http: httpx.AsyncClient, grant: AccountGrant, meeting_id: str
) -> ZoomMeeting:
    return await _client(http, grant).get_meeting(meeting_id)


async def list_recordings(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_results: int = 50,
) -> Page:
    return await _client(http, grant).list_recordings(
        from_date=from_date, to_date=to_date, max_results=max_results
    )


async def list_recording_files(
    http: httpx.AsyncClient, grant: AccountGrant, meeting_id: str
) -> RecordingMeeting:
    return await _client(http, grant).list_recording_files(meeting_id)


async def get_transcript(
    http: httpx.AsyncClient, grant: AccountGrant, meeting_id: str
) -> tuple[ZoomTranscript, str | None, bool]:
    """The transcript metadata and, when it is downloadable, its text.

    The second element is None when Zoom says the transcript cannot be downloaded; the reason is
    carried on the metadata so the caller can explain it instead of guessing.
    """
    client = _client(http, grant)
    transcript = await client.get_transcript(meeting_id)
    if not transcript.can_download or not transcript.download_url:
        return transcript, None, False
    text, truncated = await client.fetch_transcript_text(transcript.download_url)
    if not text.strip():
        raise ConnectorAuthError(
            "Zoom returned an empty transcript.",
            hint="The transcript may still be processing; try again later.",
        )
    return transcript, text, truncated
