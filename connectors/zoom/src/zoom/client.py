"""Read-only Zoom v2 REST client. Every method returns Pydantic models, never raw dicts.

Shapes come from Zoom's published OpenAPI documents
(`developers.zoom.us/api/zoap/meetings/methods/`, `.../users/methods/`), not from sample
responses. Four documented behaviours shape this file:

* **Every user endpoint is addressed as `me`.** A user-managed app may only read the authorising
  user; passing another `userId` is refused by Zoom with an invalid-token error. The client
  therefore never accepts a user id, which is also what keeps one tenant's token from being
  pointed at another's data.
* **Pagination is `next_page_token`**, and the token expires after 15 minutes. Both a result cap
  and a page cap are enforced so a provider that always returns a cursor cannot turn one tool call
  into an endless loop; the caller is told when the walk stopped early.
* **`GET /users/me/recordings` has a one-day default window.** Omitting `from`/`to` does not mean
  "everything": Zoom defaults to today and tomorrow. The day range is therefore always explicit
  and never wider than a month, which is Zoom's documented maximum.
* **`GET /meetings/{id}/transcript` returns metadata, not text.** The transcript (VTT) is a second,
  separately authenticated GET of the returned `download_url`.

A 401 means the access token expired or was revoked. The client asks the token source to
invalidate and retries exactly once: a second failure is a real credential problem, and retrying
would turn one broken grant into a refresh storm against a rotating refresh token.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from zoom.config import DEFAULT_API_BASE
from zoom.errors import ConnectorAPIError, ConnectorAuthError

_MAX_PAGE = 300
_MAX_PAGES = 10
_HARD_LIMIT = 500
# A transcript is provider-supplied text going into a model context window. The cap is a safety
# bound, not a formatting choice: an hour of VTT can run to megabytes.
_MAX_TRANSCRIPT_CHARS = 200_000
# Zoom caps a recordings range at one month; ask for a little less so a clock skew at the edge of
# the range cannot push the request over the limit.
_DEFAULT_WINDOW_DAYS = 30
# Meeting ids are numeric, or UUIDs that may contain `/`, `+`, `=` and `==`. Anything else is a
# caller error and is rejected before it reaches a URL path.
_MEETING_ID_PATTERN = re.compile(r"^[A-Za-z0-9=+/_\-]{1,128}$")


class TokenSource(Protocol):
    """The stored grant for one connected account, owned by the server, not by this package."""

    async def access_token(self) -> str:
        """A currently-valid access token, refreshing if needed."""

    async def invalidate(self) -> None:
        """Tell the source that the token it handed out was rejected."""

    def api_base(self) -> str:
        """The API cluster to address (`https://api.zoom.us/v2` unless configured otherwise)."""


class ZoomUser(BaseModel):
    id: str = ""
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    email: str = ""
    type: int | None = None
    role_name: str | None = None
    timezone: str | None = None
    account_id: str | None = None
    status: str | None = None
    pmi: str | None = None
    created_at: str | None = None
    verified: int | None = None

    @property
    def name(self) -> str:
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) or (self.display_name or self.email)


class ZoomMeeting(BaseModel):
    id: int | None = None
    uuid: str | None = None
    topic: str = ""
    type: int | None = None
    start_time: str | None = None
    duration: int | None = None
    timezone: str | None = None
    join_url: str | None = None
    agenda: str | None = None
    status: str | None = None
    host_id: str | None = None
    created_at: str | None = None


class RecordingFile(BaseModel):
    id: str | None = None
    meeting_id: str | None = None
    recording_start: str | None = None
    recording_end: str | None = None
    file_type: str | None = None
    file_extension: str | None = None
    file_size: int | None = None
    status: str | None = None
    recording_type: str | None = None
    download_url: str | None = None
    play_url: str | None = None
    participant_email: str | None = None


class RecordingMeeting(BaseModel):
    id: int | None = None
    uuid: str | None = None
    topic: str = ""
    host_id: str | None = None
    start_time: str | None = None
    duration: int | None = None
    recording_count: int | None = None
    total_size: int | None = None
    recording_files: list[RecordingFile] = Field(default_factory=list)


class ZoomTranscript(BaseModel):
    meeting_id: str = ""
    meeting_topic: str | None = None
    host_id: str | None = None
    transcript_created_time: str | None = None
    can_download: bool = False
    download_url: str | None = None
    download_restriction_reason: str | None = None
    auto_delete: bool | None = None
    auto_delete_date: str | None = None


class Page(BaseModel):
    """A bounded page. `truncated` says the walk stopped at the cap, not at the provider's end."""

    items: list[Any]
    next_page_token: str | None = None
    truncated: bool = False


class ZoomClient:
    """Holds one connected account's token source and talks to the account's cluster."""

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens

    def _base(self) -> str:
        try:
            base = self._tokens.api_base()
        except AttributeError:  # a test double that predates api_base()
            base = DEFAULT_API_BASE
        return (base or DEFAULT_API_BASE).rstrip("/")

    async def get_current_user(self) -> ZoomUser:
        return _user(await self._get("/users/me"))

    async def list_meetings(
        self,
        *,
        meeting_type: str = "scheduled",
        max_results: int = 50,
    ) -> Page:
        if meeting_type not in {"scheduled", "live", "upcoming", "upcoming_meetings", "previous_meetings"}:
            raise ValueError(
                "meeting_type must be scheduled, live, upcoming, upcoming_meetings or previous_meetings."
            )
        return await self._pages(
            "/users/me/meetings",
            {"type": meeting_type},
            "meetings",
            _meeting,
            max_results=max_results,
        )

    async def get_meeting(self, meeting_id: str) -> ZoomMeeting:
        return _meeting(await self._get(f"/meetings/{_checked_meeting_id(meeting_id)}"))

    async def list_recordings(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        max_results: int = 50,
    ) -> Page:
        start, end = _window(from_date, to_date)
        return await self._pages(
            "/users/me/recordings",
            {"from": start, "to": end, "trash": "false"},
            "meetings",
            _recording_meeting,
            max_results=max_results,
        )

    async def list_recording_files(self, meeting_id: str) -> RecordingMeeting:
        payload = await self._get(f"/meetings/{_checked_meeting_id(meeting_id)}/recordings")
        return _recording_meeting(payload)

    async def get_transcript(self, meeting_id: str) -> ZoomTranscript:
        payload = await self._get(f"/meetings/{_checked_meeting_id(meeting_id)}/transcript")
        return _transcript(payload)

    async def fetch_transcript_text(self, download_url: str) -> tuple[str, bool]:
        """Download a transcript the provider pointed at. Returns (text, truncated).

        The URL is not a tool argument: it comes from Zoom's own transcript metadata, so the model
        cannot aim this at an arbitrary host. It is re-checked anyway, because a URL that leaves
        the provider's own domains would put a bearer token in front of a third party.
        """
        if not download_url.startswith("https://") or ".zoom.us/" not in download_url:
            raise ConnectorAPIError(
                "Zoom returned a transcript URL outside its own domains.",
                hint="Nothing was downloaded; reconnect the account and try again.",
            )
        token = await self._tokens.access_token()
        response = await self._http.get(
            download_url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True
        )
        if response.status_code == 401:
            await self._tokens.invalidate()
            token = await self._tokens.access_token()
            response = await self._http.get(
                download_url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True
            )
        if response.status_code == 403:
            raise ConnectorAPIError(
                "Zoom refused the transcript download; the grant lacks the recording scope.",
                hint="Reconnect Zoom and allow the recording scopes on the consent screen.",
            )
        if response.status_code == 404:
            raise ConnectorAPIError("Zoom has no transcript file at that address any more.")
        if response.status_code >= 400:
            raise ConnectorAPIError(f"Zoom transcript download returned HTTP {response.status_code}.")
        text = response.text or ""
        if len(text) > _MAX_TRANSCRIPT_CHARS:
            # Cut on a line boundary so the last cue is not a half sentence.
            head = text[:_MAX_TRANSCRIPT_CHARS]
            cut = head.rfind("\n")
            return (head[:cut] if cut > 0 else head), True
        return text, False

    async def _pages(
        self,
        path: str,
        params: dict[str, Any],
        key: str,
        extract: Callable[[dict[str, Any]], Any],
        *,
        max_results: int,
    ) -> Page:
        wanted = _clamp(max_results)
        collected: list[Any] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            page_params["page_size"] = min(_MAX_PAGE, max(1, wanted - len(collected)))
            if cursor:
                page_params["next_page_token"] = cursor
            payload = await self._get(path, page_params)
            raw = payload.get(key)
            raw = raw if isinstance(raw, list) else []
            collected.extend(extract(item) for item in raw if isinstance(item, dict))
            cursor = _next_token(payload.get("next_page_token"))
            if not cursor or not raw or len(collected) >= wanted:
                break
        truncated = bool(cursor) and len(collected) >= wanted
        return Page(items=collected[:wanted], next_page_token=cursor or None, truncated=truncated)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._tokens.access_token()
        url = f"{self._base()}{path}"
        response = await self._http.get(
            url, params=params or {}, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 401:
            # Exactly one retry: the source refreshes on the next access_token() call.
            await self._tokens.invalidate()
            token = await self._tokens.access_token()
            response = await self._http.get(
                url, params=params or {}, headers={"Authorization": f"Bearer {token}"}
            )
        if response.status_code == 429:
            raise ConnectorAPIError(
                "Zoom rate-limited the request.",
                hint=f"Wait {_retry_after(response)} seconds and try again.",
            )
        if response.status_code >= 500:
            raise ConnectorAPIError(f"Zoom API returned HTTP {response.status_code}.")
        if response.status_code == 401:
            raise ConnectorAuthError(
                "Zoom rejected the stored credential.",
                hint="Reconnect Zoom in Settings > Connections.",
            )
        if response.status_code == 403:
            raise ConnectorAPIError(
                "This Zoom grant lacks a scope the tool needs.",
                hint="Reconnect Zoom and allow the read scopes shown on the consent screen.",
            )
        if response.status_code == 404:
            payload = _json(response)
            code = payload.get("code")
            if code == 3322:
                raise ConnectorAPIError(
                    "This meeting has no transcript.",
                    hint="Only meetings recorded to the cloud with transcription enabled have one.",
                )
            raise ConnectorAPIError("Zoom has no such resource for this account's grant.")
        if response.status_code == 400:
            payload = _json(response)
            message = str(payload.get("message") or "")
            # Measured 2026-11-09: a missing scope is NOT a 403. Zoom answers 400 with code 4711
            # and a message naming the scopes it wanted, e.g.
            #   {"code": 4711, "message": "Invalid access token, does not contain scopes:[...]"}
            # Reporting that as a bare "HTTP 400" would send the user looking for a bug instead of
            # reconnecting with the scope they declined.
            if payload.get("code") == 4711 or "does not contain scopes" in message:
                raise ConnectorAPIError(
                    "This Zoom grant lacks a scope the tool needs.",
                    hint=(
                        "Reconnect Zoom and allow the read scopes shown on the consent screen. "
                        "Zoom reports this as HTTP 400 code 4711, not 403."
                    ),
                )
            raise ConnectorAPIError(
                f"Zoom API returned HTTP 400{': ' + message if message else '.'}"
            )
        if response.status_code >= 400:
            payload = _json(response)
            message = payload.get("message")
            raise ConnectorAPIError(
                f"Zoom API returned HTTP {response.status_code}"
                f"{f': {message}' if isinstance(message, str) and message else '.'}"
            )
        return _json(response)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError("Zoom returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError("Zoom returned an unexpected response shape.")
    return payload


def _user(payload: dict[str, Any]) -> ZoomUser:
    return ZoomUser(
        id=str(payload.get("id") or ""),
        first_name=_text(payload.get("first_name")),
        last_name=_text(payload.get("last_name")),
        display_name=_text(payload.get("display_name")),
        email=str(payload.get("email") or ""),
        type=payload.get("type") if isinstance(payload.get("type"), int) else None,
        role_name=_text(payload.get("role_name")),
        timezone=_text(payload.get("timezone")),
        account_id=_text(payload.get("account_id")),
        status=_text(payload.get("status")),
        pmi=str(payload["pmi"]) if isinstance(payload.get("pmi"), (int, str)) else None,
        created_at=_text(payload.get("created_at")),
        verified=payload.get("verified") if isinstance(payload.get("verified"), int) else None,
    )


def _meeting(item: dict[str, Any]) -> ZoomMeeting:
    return ZoomMeeting(
        id=item.get("id") if isinstance(item.get("id"), int) else None,
        uuid=_text(item.get("uuid")),
        topic=str(item.get("topic") or ""),
        type=item.get("type") if isinstance(item.get("type"), int) else None,
        start_time=_text(item.get("start_time")),
        duration=item.get("duration") if isinstance(item.get("duration"), int) else None,
        timezone=_text(item.get("timezone")),
        join_url=_text(item.get("join_url")),
        # List responses truncate the agenda to 250 characters; the single-meeting call does not.
        agenda=_text(item.get("agenda")),
        status=_text(item.get("status")),
        host_id=_text(item.get("host_id")),
        created_at=_text(item.get("created_at")),
    )


def _recording_meeting(item: dict[str, Any]) -> RecordingMeeting:
    files = item.get("recording_files")
    files = files if isinstance(files, list) else []
    return RecordingMeeting(
        id=item.get("id") if isinstance(item.get("id"), int) else None,
        uuid=_text(item.get("uuid")),
        topic=str(item.get("topic") or ""),
        host_id=_text(item.get("host_id")),
        start_time=_text(item.get("start_time")),
        duration=item.get("duration") if isinstance(item.get("duration"), int) else None,
        recording_count=item.get("recording_count")
        if isinstance(item.get("recording_count"), int)
        else None,
        total_size=item.get("total_size") if isinstance(item.get("total_size"), int) else None,
        recording_files=[_recording_file(f) for f in files if isinstance(f, dict)],
    )


def _recording_file(item: dict[str, Any]) -> RecordingFile:
    size = item.get("file_size")
    return RecordingFile(
        id=_text(item.get("id")),
        meeting_id=_text(item.get("meeting_id")),
        recording_start=_text(item.get("recording_start")),
        recording_end=_text(item.get("recording_end")),
        file_type=_text(item.get("file_type")),
        file_extension=_text(item.get("file_extension")),
        file_size=size if isinstance(size, (int, float)) else None,
        status=_text(item.get("status")),
        recording_type=_text(item.get("recording_type")),
        # Never returned as a tool argument: the model must not be able to choose a URL.
        download_url=_text(item.get("download_url")),
        play_url=_text(item.get("play_url")),
        participant_email=_text(item.get("participant_email")),
    )


def _transcript(payload: dict[str, Any]) -> ZoomTranscript:
    can_download = payload.get("can_download")
    return ZoomTranscript(
        meeting_id=str(payload.get("meeting_id") or ""),
        meeting_topic=_text(payload.get("meeting_topic")),
        host_id=_text(payload.get("host_id")),
        transcript_created_time=_text(payload.get("transcript_created_time")),
        can_download=bool(can_download),
        download_url=_text(payload.get("download_url")),
        download_restriction_reason=_text(payload.get("download_restriction_reason")),
        auto_delete=payload.get("auto_delete") if isinstance(payload.get("auto_delete"), bool) else None,
        auto_delete_date=_text(payload.get("auto_delete_date")),
    )


def _next_token(value: Any) -> str:
    """Zoom returns an empty string on the last page; anything else is the next cursor."""
    return value.strip() if isinstance(value, str) else ""


def _checked_meeting_id(value: str) -> str:
    """A meeting id or UUID, encoded for a URL path. Never a free-form string from the model."""
    text = str(value or "").strip()
    if not text or not _MEETING_ID_PATTERN.fullmatch(text):
        raise ValueError(
            "Invalid Zoom meeting id; pass the id or uuid from a listed meeting or recording."
        )
    if text.startswith("/") or "//" in text:
        # Zoom's documented rule for UUIDs that start with `/` or contain `//`.
        return quote(quote(text, safe=""), safe="")
    return quote(text, safe="")


def _window(from_date: str | None, to_date: str | None) -> tuple[str, str]:
    """An explicit `from`/`to` in yyyy-mm-dd, never wider than Zoom's one-month cap.

    Omitting both is not "everything": Zoom would default to a single day. The default here is the
    last 30 days ending today (UTC).
    """
    today = datetime.now(timezone.utc).date()
    start = _parse_day(from_date, "from_date") if from_date else None
    end = _parse_day(to_date, "to_date") if to_date else None
    if start is None and end is None:
        start, end = today - timedelta(days=_DEFAULT_WINDOW_DAYS), today
    elif start is None:
        start = end - timedelta(days=_DEFAULT_WINDOW_DAYS)  # type: ignore[operator]
    elif end is None:
        end = min(start + timedelta(days=_DEFAULT_WINDOW_DAYS), today)
    if end < start:  # type: ignore[operator]
        raise ValueError("to_date must not be earlier than from_date.")
    if (end - start).days > _DEFAULT_WINDOW_DAYS:  # type: ignore[operator]
        raise ValueError(
            f"Zoom caps a recordings range at one month; ask for at most {_DEFAULT_WINDOW_DAYS} days."
        )
    return start.isoformat(), end.isoformat()


def _parse_day(value: str, what: str) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{what} must be a yyyy-mm-dd date.") from exc


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _clamp(value: int) -> int:
    return max(1, min(int(value), _HARD_LIMIT))


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
