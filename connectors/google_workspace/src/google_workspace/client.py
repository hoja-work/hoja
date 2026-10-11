"""Read-only Google Workspace API client. Every method returns Pydantic models, never raw dicts."""

from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from google_workspace.errors import ConnectorAPIError, ConnectorAuthError

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
DOCS_BASE = "https://docs.googleapis.com/v1"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")
_MAX_RESULTS = 50


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

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        if response.status_code == 401:
            raise ConnectorAuthError("Google rejected the access token.", hint="Reconnect the account.")
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

    async def calendar_event_create(
        self, summary: str, start: datetime, end: datetime
    ) -> str:
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

    async def _get(self, url: str, params: Any = None) -> dict[str, Any]:
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
