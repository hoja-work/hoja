"""Read-only Google Workspace API client. Every method returns Pydantic models, never raw dicts."""

from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from google_workspace.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
DOCS_BASE = "https://docs.googleapis.com/v1"
MEET_BASE = "https://meet.googleapis.com/v2"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")
_MAX_RESULTS = 50

# A Meet resource name is the API's own path, such as
# "conferenceRecords/{id}/transcripts/{id}/entries/{id}". It arrives from tool arguments, so it is
# matched against these shapes before being put in a URL: an argument cannot add a path segment.
_MEET_NAME = re.compile(
    r"^(?:"
    r"spaces/[A-Za-z0-9_-]{1,128}"
    r"|conferenceRecords/[A-Za-z0-9_-]{1,128}"
    r"(?:"
    r"/participants/[A-Za-z0-9_-]{1,128}"
    r"|/recordings/[A-Za-z0-9_-]{1,128}"
    r"|/transcripts/[A-Za-z0-9_-]{1,128}(?:/entries/[A-Za-z0-9_-]{1,128})?"
    r")?"
    r")$"
)

# Page sizes the service documents, and a page cap so an unbounded walk can never run away.
_MEET_PAGE_SIZE = {
    "conference_records": 100,
    "participants": 250,
    "recordings": 100,
    "transcripts": 100,
    "entries": 100,
}
_MEET_PAGE_LIMIT = 10

# Google answers a usable-looking 403 for two very different failures. Both are worth telling apart:
# one is the deployer's project, the other is the user's consent.
_SERVICE_DISABLED = "SERVICE_DISABLED"
_SCOPE_INSUFFICIENT = "ACCESS_TOKEN_SCOPE_INSUFFICIENT"


class GmailMessage(BaseModel):
    id: str
    thread_id: str
    subject: str | None
    sender: str | None
    date: str | None
    snippet: str


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str
    modified_time: str | None
    web_view_link: str | None


class CalendarEvent(BaseModel):
    id: str
    summary: str
    start: str | None
    end: str | None
    html_link: str | None


class Document(BaseModel):
    document_id: str
    title: str
    text: str


class ConferenceRecord(BaseModel):
    """One past or ongoing meeting the account can see (Meet `conferenceRecords`)."""

    name: str
    space: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    expire_time: str | None = None


class Participant(BaseModel):
    """A person in one meeting. `kind` is which of Google's three user shapes was present."""

    name: str
    kind: str
    display_name: str | None = None
    user_id: str | None = None
    earliest_start_time: str | None = None
    latest_end_time: str | None = None


class Recording(BaseModel):
    """A recording artifact. The bytes live in Drive; this connector only reports the metadata and
    the Drive playback link, and deliberately holds no Drive content scope of its own."""

    name: str
    state: str
    start_time: str | None = None
    end_time: str | None = None
    drive_file_id: str | None = None
    drive_export_uri: str | None = None


class Transcript(BaseModel):
    """A transcript artifact. Google mirrors it to Docs; the text itself is read through the Meet
    API (`transcripts.entries`), so no Docs scope is involved either."""

    name: str
    state: str
    start_time: str | None = None
    end_time: str | None = None
    docs_document_id: str | None = None
    docs_export_uri: str | None = None


class TranscriptEntry(BaseModel):
    name: str
    participant: str | None = None
    text: str = ""
    language_code: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class GoogleWorkspaceClient:
    """Holds one access token. Callers obtain tokens via oauth.refresh_access_token."""

    def __init__(self, http: httpx.AsyncClient, access_token: str) -> None:
        self._http = http
        self._access_token = access_token

    async def gmail_search(self, query: str, max_results: int = 10) -> list[GmailMessage]:
        listing = await self._get(
            f"{GMAIL_BASE}/messages",
            params={"q": query, "maxResults": _clamp(max_results)},
        )
        messages: list[GmailMessage] = []
        for item in listing.get("messages", []):
            detail = await self._get(
                f"{GMAIL_BASE}/messages/{_checked_id(item['id'])}",
                params=[
                    ("format", "metadata"),
                    ("metadataHeaders", "Subject"),
                    ("metadataHeaders", "From"),
                    ("metadataHeaders", "Date"),
                ],
            )
            headers = {
                h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])
            }
            messages.append(
                GmailMessage(
                    id=detail["id"],
                    thread_id=detail["threadId"],
                    subject=headers.get("subject"),
                    sender=headers.get("from"),
                    date=headers.get("date"),
                    snippet=detail.get("snippet", ""),
                )
            )
        return messages

    async def drive_search(self, query: str, max_results: int = 10) -> list[DriveFile]:
        data = await self._get(
            f"{DRIVE_BASE}/files",
            params={
                "q": query,
                "pageSize": _clamp(max_results),
                "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            },
        )
        return [_drive_file(item) for item in data.get("files", [])]

    async def drive_get_file(self, file_id: str) -> DriveFile:
        data = await self._get(
            f"{DRIVE_BASE}/files/{_checked_id(file_id)}",
            params={"fields": "id,name,mimeType,modifiedTime,webViewLink"},
        )
        return _drive_file(data)

    async def calendar_events(
        self, time_min: datetime, time_max: datetime, max_results: int = 25
    ) -> list[CalendarEvent]:
        data = await self._get(
            f"{CALENDAR_BASE}/calendars/primary/events",
            params={
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": _clamp(max_results),
            },
        )
        return [
            CalendarEvent(
                id=item["id"],
                summary=item.get("summary", "(no title)"),
                start=_event_time(item.get("start")),
                end=_event_time(item.get("end")),
                html_link=item.get("htmlLink"),
            )
            for item in data.get("items", [])
        ]

    async def docs_get(self, document_id: str) -> Document:
        data = await self._get(f"{DOCS_BASE}/documents/{_checked_id(document_id)}")
        return Document(
            document_id=data["documentId"],
            title=data.get("title", ""),
            text=_document_text(data.get("body", {}).get("content", [])),
        )

    # --- Google Meet -------------------------------------------------------------------------
    # Every method below is a read. They all need the one Meet scope
    # (`meetings.space.readonly`); a grant without it answers 403 and is reported as "add Meet
    # access", not as a generic failure.

    async def meet_conference_records(
        self, filter: str | None = None, max_results: int = 25
    ) -> list[ConferenceRecord]:
        """List conference records the account can see.

        `filter` uses Meet's EBNF syntax over `space.meeting_code`, `space.name`, `start_time`
        and `end_time`; for example
        `start_time>="2026-11-01T00:00:00.000Z" AND start_time<="2026-11-09T00:00:00.000Z"`.
        """
        rows = await self._meet_paged(
            f"{MEET_BASE}/conferenceRecords",
            params={"filter": filter} if filter else {},
            key="conferenceRecords",
            max_results=max_results,
            page_size_cap=_MEET_PAGE_SIZE["conference_records"],
        )
        return [
            ConferenceRecord(
                name=item.get("name", ""),
                space=item.get("space"),
                start_time=item.get("startTime"),
                end_time=item.get("endTime"),
                expire_time=item.get("expireTime"),
            )
            for item in rows
        ]

    async def meet_participants(
        self, conference_record: str, max_results: int = 100
    ) -> list[Participant]:
        """List everyone recorded in one meeting.

        The API also accepts a `latest_end_time IS NULL` filter for participants still connected;
        this connector does not expose it, because on a past conference record it can only return
        nobody or everybody.
        """
        name = _checked_meet_name(conference_record)
        rows = await self._meet_paged(
            f"{MEET_BASE}/{name}/participants",
            params={},
            key="participants",
            max_results=max_results,
            page_size_cap=_MEET_PAGE_SIZE["participants"],
        )
        return [_participant(item) for item in rows]

    async def meet_recordings(
        self, conference_record: str, max_results: int = 50
    ) -> list[Recording]:
        name = _checked_meet_name(conference_record)
        rows = await self._meet_paged(
            f"{MEET_BASE}/{name}/recordings",
            params={},
            key="recordings",
            max_results=max_results,
            page_size_cap=_MEET_PAGE_SIZE["recordings"],
        )
        return [
            Recording(
                name=item.get("name", ""),
                state=item.get("state", "STATE_UNSPECIFIED"),
                start_time=item.get("startTime"),
                end_time=item.get("endTime"),
                drive_file_id=(item.get("driveDestination") or {}).get("file"),
                drive_export_uri=(item.get("driveDestination") or {}).get("exportUri"),
            )
            for item in rows
        ]

    async def meet_transcripts(
        self, conference_record: str, max_results: int = 50
    ) -> list[Transcript]:
        name = _checked_meet_name(conference_record)
        rows = await self._meet_paged(
            f"{MEET_BASE}/{name}/transcripts",
            params={},
            key="transcripts",
            max_results=max_results,
            page_size_cap=_MEET_PAGE_SIZE["transcripts"],
        )
        return [
            Transcript(
                name=item.get("name", ""),
                state=item.get("state", "STATE_UNSPECIFIED"),
                start_time=item.get("startTime"),
                end_time=item.get("endTime"),
                docs_document_id=(item.get("docsDestination") or {}).get("document"),
                docs_export_uri=(item.get("docsDestination") or {}).get("exportUri"),
            )
            for item in rows
        ]

    async def meet_transcript_entries(
        self, transcript: str, max_results: int = 100
    ) -> list[TranscriptEntry]:
        """Read the spoken text of one transcript. The Meet API serves it; Docs is only a mirror."""
        name = _checked_meet_name(transcript)
        rows = await self._meet_paged(
            f"{MEET_BASE}/{name}/entries",
            params={},
            key="transcriptEntries",
            max_results=max_results,
            page_size_cap=_MEET_PAGE_SIZE["entries"],
        )
        return [
            TranscriptEntry(
                name=item.get("name", ""),
                participant=item.get("participant"),
                text=item.get("text", ""),
                language_code=item.get("languageCode"),
                start_time=item.get("startTime"),
                end_time=item.get("endTime"),
            )
            for item in rows
        ]

    async def _meet_paged(
        self,
        url: str,
        *,
        params: dict[str, Any],
        key: str,
        max_results: int,
        page_size_cap: int,
    ) -> list[dict[str, Any]]:
        """Walk a Meet list method, bounded twice: by the caller's result cap and by a page cap.

        A repeated `nextPageToken` also stops the walk: trusting it would loop until the page cap
        on a misbehaving response, which is a slow way to fail.
        """
        page_limit = _MEET_PAGE_LIMIT
        wanted = max(1, min(max_results, page_size_cap * page_limit))
        collected: list[dict[str, Any]] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(page_limit):
            remaining = wanted - len(collected)
            if remaining <= 0:
                break
            page_params: dict[str, Any] = dict(params)
            page_params["pageSize"] = min(remaining, page_size_cap)
            if token:
                page_params["pageToken"] = token
            data = await self._get(url, params=page_params, requires="Google Meet")
            items = data.get(key) or []
            if not isinstance(items, list):
                raise ConnectorAPIError("Google Meet API returned an unexpected response shape.")
            collected.extend(item for item in items if isinstance(item, dict))
            token = data.get("nextPageToken") or None
            if not token or token in seen:
                break
            seen.add(token)
        return collected[:wanted]

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        if response.status_code == 401:
            raise ConnectorAuthError(
                "Google rejected the access token.", hint="Reconnect the account."
            )
        if response.status_code >= 400:
            raise ConnectorAPIError(f"Google API returned HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConnectorAPIError("Google API returned an unexpected response shape.")
        return payload

    async def gmail_send(self, to: str, subject: str, body: str) -> str:
        """Send a plain-text email. Returns the Gmail message id."""
        if not _ADDRESS_PATTERN.fullmatch(to):
            raise ValueError("Recipient must be a single email address.")
        if "\r" in subject or "\n" in subject:
            raise ValueError("Subject must not contain line breaks.")
        raw = (
            f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{body}"
        ).encode()
        encoded = base64.urlsafe_b64encode(raw).decode("ascii")
        payload = await self._post(f"{GMAIL_BASE}/messages/send", {"raw": encoded})
        return str(payload.get("id", ""))

    async def calendar_event_create(self, summary: str, start: datetime, end: datetime) -> str:
        """Create an event on the primary calendar. Returns the event id."""
        if end <= start:
            raise ValueError("Event end must be after its start.")
        payload = await self._post(
            f"{CALENDAR_BASE}/calendars/primary/events",
            {
                "summary": summary,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            },
        )
        return str(payload.get("id", ""))

    async def docs_append_text(self, document_id: str, text: str) -> None:
        """Append text at the end of a document."""
        await self._post(
            f"{DOCS_BASE}/documents/{_checked_id(document_id)}:batchUpdate",
            {"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": text}}]},
        )

    async def _get(
        self, url: str, params: Any = None, *, requires: str | None = None
    ) -> dict[str, Any]:
        """One authenticated GET. `requires` names the capability the call needs, so the two 403
        shapes Google uses are reported as what they are:

        - `SERVICE_DISABLED` - the deployer never enabled the API in the OAuth client's project.
          Nothing the user can do fixes it.
        - `ACCESS_TOKEN_SCOPE_INSUFFICIENT` - this account's grant does not cover the API. The user
          can fix it by adding the scope in Settings > Connections.

        Collapsing both into "HTTP 403" leaves a user clicking Disconnect and Connect again forever.
        """
        response = await self._http.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        if response.status_code == 401:
            raise ConnectorAuthError(
                "Google rejected the access token.",
                hint="Reconnect the Google account.",
            )
        if response.status_code == 403:
            body = response.text
            if _SERVICE_DISABLED in body:
                raise ConnectorConfigError(
                    f"The {requires or 'Google'} API is not enabled for this deployment.",
                    hint="Enable it in the Google Cloud project that owns the OAuth client.",
                )
            if requires and _SCOPE_INSUFFICIENT in body:
                raise ConnectorAuthError(
                    f"This Google account has not granted {requires} access.",
                    hint=(
                        f"Add {requires} access in Settings > Connections, then retry the request."
                    ),
                )
        if response.status_code >= 400:
            raise ConnectorAPIError(f"Google API returned HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConnectorAPIError("Google API returned an unexpected response shape.")
        return payload


def _checked_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid Google resource id.")
    return value


def _checked_meet_name(value: str) -> str:
    """Accept only the resource-name shapes Meet returns, so an argument cannot add a path segment."""
    if not _MEET_NAME.fullmatch(value or ""):
        raise ValueError(
            "Invalid Google Meet resource name. Use a name returned by another Meet tool."
        )
    return value


def _participant(item: dict[str, Any]) -> Participant:
    """Flatten Google's three participant shapes (`signedinUser`, `anonymousUser`, `phoneUser`)
    into one row. Only one is ever set; `kind` tells the reader which, so a name is never presented
    as an identity it is not."""
    for kind, key in (
        ("signedin", "signedinUser"),
        ("anonymous", "anonymousUser"),
        ("phone", "phoneUser"),
    ):
        user = item.get(key)
        if isinstance(user, dict):
            return Participant(
                name=item.get("name", ""),
                kind=kind,
                display_name=user.get("displayName"),
                user_id=user.get("user"),
                earliest_start_time=item.get("earliestStartTime"),
                latest_end_time=item.get("latestEndTime"),
            )
    return Participant(
        name=item.get("name", ""),
        kind="unknown",
        earliest_start_time=item.get("earliestStartTime"),
        latest_end_time=item.get("latestEndTime"),
    )


def _clamp(value: int) -> int:
    return max(1, min(value, _MAX_RESULTS))


def _drive_file(item: dict[str, Any]) -> DriveFile:
    return DriveFile(
        id=item["id"],
        name=item.get("name", ""),
        mime_type=item.get("mimeType", ""),
        modified_time=item.get("modifiedTime"),
        web_view_link=item.get("webViewLink"),
    )


def _event_time(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return value.get("dateTime") or value.get("date")


def _document_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for element in content:
        for part in element.get("paragraph", {}).get("elements", []):
            run = part.get("textRun")
            if run:
                parts.append(run.get("content", ""))
    return "".join(parts)


def decode_gmail_body(encoded: str) -> str:
    """Gmail returns body data as URL-safe base64 without padding."""
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
