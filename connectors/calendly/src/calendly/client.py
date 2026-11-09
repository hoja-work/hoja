"""Read-only Calendly v2 REST client. Every method returns Pydantic models, never raw dicts.

Shapes come from the published OpenAPI document (`developer.calendly.com/openapi/calendly-api.yaml`),
not from sample responses:

* list endpoints answer `{"collection": [...], "pagination": {...}}`, single resources answer
  `{"resource": {...}}`;
* pagination is cursor-based per endpoint: `count` (default 20, **maximum 100**) plus
  `page_token`, and the next cursor is `pagination.next_page_token`;
* `/event_types` and `/scheduled_events` both *require* a `user` or `organization` filter in
  practice; the connector always scopes to the authorizing user's own URI, so one tenant's token
  cannot be pointed at another's collection.

A 401 means the access token expired or was revoked. The client asks the token source to
invalidate and retries exactly once, because a second failure is a real credential problem and
retrying would turn one broken grant into a refresh storm against a rotating refresh token.
"""

from __future__ import annotations

import uuid as uuidlib
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from calendly.errors import ConnectorAPIError, ConnectorAuthError

API_BASE = "https://api.calendly.com"
_MAX_PAGE = 100
_MAX_PAGES = 10
_HARD_LIMIT = 500


class TokenSource(Protocol):
    """The stored grant for one connected account, owned by the server, not by this package."""

    async def access_token(self) -> str:
        """A currently-valid access token, refreshing if needed."""

    async def invalidate(self) -> None:
        """Tell the source that the token it handed out was rejected."""


class CalendlyUser(BaseModel):
    uri: str
    name: str
    email: str
    scheduling_url: str | None = None
    timezone: str | None = None
    organization: str | None = None


class EventType(BaseModel):
    uri: str
    name: str
    active: bool = True
    scheduling_url: str | None = None
    duration: int | None = None
    kind: str | None = None
    description: str | None = None
    booking_method: str | None = None


class InviteeCounters(BaseModel):
    active: int = 0
    limit: int | None = None


class ScheduledEvent(BaseModel):
    uri: str
    name: str
    status: str
    start_time: str | None = None
    end_time: str | None = None
    event_type: str | None = None
    location: str | None = None
    created_at: str | None = None
    invitees: InviteeCounters = Field(default_factory=InviteeCounters)


class Invitee(BaseModel):
    uri: str
    email: str
    name: str | None = None
    status: str | None = None
    timezone: str | None = None
    created_at: str | None = None
    canceled: bool = False


class CalendlyClient:
    """Holds one connected account's token source and talks to api.calendly.com."""

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens

    async def get_current_user(self) -> CalendlyUser:
        payload = await self._get("/users/me")
        return _user(payload.get("resource"))

    async def list_event_types(
        self, *, user_uri: str, max_results: int = 100, active: bool | None = True
    ) -> list[EventType]:
        params: dict[str, Any] = {"user": _checked_uri(user_uri, "user")}
        if active is not None:
            params["active"] = "true" if active else "false"
        return await self._pages(
            "/event_types", params, extract=lambda item: _event_type(item), max_results=max_results
        )

    async def list_scheduled_events(
        self,
        *,
        user_uri: str,
        max_results: int = 100,
        status: str | None = None,
        min_start_time: str | None = None,
        max_start_time: str | None = None,
    ) -> list[ScheduledEvent]:
        params: dict[str, Any] = {"user": _checked_uri(user_uri, "user")}
        if status is not None:
            if status not in {"active", "canceled"}:
                raise ValueError("status must be 'active' or 'canceled'.")
            params["status"] = status
        if min_start_time is not None:
            params["min_start_time"] = min_start_time
        if max_start_time is not None:
            params["max_start_time"] = max_start_time
        return await self._pages(
            "/scheduled_events",
            params,
            extract=_scheduled_event,
            max_results=max_results,
            sort="start_time:asc",
        )

    async def get_scheduled_event(self, event_uuid: str) -> ScheduledEvent:
        payload = await self._get(f"/scheduled_events/{_checked_uuid(event_uuid)}")
        return _scheduled_event(payload.get("resource"))

    async def list_invitees(
        self,
        event_uuid: str,
        *,
        max_results: int = 100,
        status: str | None = None,
    ) -> list[Invitee]:
        params: dict[str, Any] = {}
        if status is not None:
            if status not in {"active", "canceled"}:
                raise ValueError("status must be 'active' or 'canceled'.")
            params["status"] = status
        return await self._pages(
            f"/scheduled_events/{_checked_uuid(event_uuid)}/invitees",
            params,
            extract=_invitee,
            max_results=max_results,
        )

    async def _pages(
        self,
        path: str,
        params: dict[str, Any],
        *,
        extract: Callable[[dict[str, Any]], Any],
        max_results: int,
        sort: str | None = None,
    ) -> list[Any]:
        """Walk `pagination.next_page_token` with both a result cap and a page cap, so a provider
        that always returns a cursor cannot turn one tool call into an endless loop."""
        wanted = _clamp(max_results)
        collected: list[Any] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            page_params["count"] = min(_MAX_PAGE, max(1, wanted - len(collected)))
            if sort is not None:
                page_params["sort"] = sort
            if cursor:
                page_params["page_token"] = cursor
            payload = await self._get(path, page_params)
            items = payload.get("collection")
            items = items if isinstance(items, list) else []
            collected.extend(extract(item) for item in items if isinstance(item, dict))
            cursor = _next_token(payload.get("pagination"))
            if not cursor or not items or len(collected) >= wanted:
                break
        return collected[:wanted]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._tokens.access_token()
        response = await self._http.get(
            f"{API_BASE}{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            # Exactly one retry: the source refreshes on the next access_token() call.
            await self._tokens.invalidate()
            token = await self._tokens.access_token()
            response = await self._http.get(
                f"{API_BASE}{path}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code == 429:
            raise ConnectorAPIError(
                "Calendly rate-limited the request.",
                hint=f"Wait {_retry_after(response)} seconds and try again.",
            )
        if response.status_code >= 500:
            raise ConnectorAPIError(f"Calendly API returned HTTP {response.status_code}.")
        if response.status_code == 401:
            raise ConnectorAuthError(
                "Calendly rejected the stored credential.",
                hint="Reconnect Calendly in Settings > Connections.",
            )
        if response.status_code == 403:
            raise ConnectorAPIError(
                "This Calendly grant lacks a scope the tool needs.",
                hint="Reconnect Calendly and allow the read scopes shown on the consent screen.",
            )
        if response.status_code == 404:
            raise ConnectorAPIError("Calendly has no such resource for this account's grant.")
        if response.status_code >= 400:
            raise ConnectorAPIError(f"Calendly API returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorAPIError("Calendly returned a body that is not JSON.") from exc
        if not isinstance(payload, dict):
            raise ConnectorAPIError("Calendly returned an unexpected response shape.")
        return payload


def _user(resource: Any) -> CalendlyUser:
    resource = resource if isinstance(resource, dict) else {}
    return CalendlyUser(
        uri=str(resource.get("uri") or ""),
        name=str(resource.get("name") or ""),
        email=str(resource.get("email") or ""),
        scheduling_url=_text(resource.get("scheduling_url")),
        timezone=_text(resource.get("timezone")),
        organization=_text(resource.get("current_organization")),
    )


def _event_type(item: dict[str, Any]) -> EventType:
    duration = item.get("duration")
    return EventType(
        uri=str(item.get("uri") or ""),
        name=str(item.get("name") or ""),
        active=bool(item.get("active", True)),
        scheduling_url=_text(item.get("scheduling_url")),
        duration=duration if isinstance(duration, int) else None,
        kind=_text(item.get("kind")),
        description=_text(item.get("description_plain")),
        booking_method=_text(item.get("booking_method")),
    )


def _scheduled_event(item: dict[str, Any]) -> ScheduledEvent:
    location = item.get("location")
    location_text: str | None = None
    if isinstance(location, dict):
        location_text = _text(location.get("location")) or _text(location.get("join_url"))
    counters = item.get("invitees_counter")
    counters = counters if isinstance(counters, dict) else {}
    return ScheduledEvent(
        uri=str(item.get("uri") or ""),
        name=str(item.get("name") or ""),
        status=str(item.get("status") or ""),
        start_time=_text(item.get("start_time")),
        end_time=_text(item.get("end_time")),
        event_type=_text(item.get("event_type")),
        location=location_text,
        created_at=_text(item.get("created_at")),
        invitees=InviteeCounters(
            active=counters.get("active") if isinstance(counters.get("active"), int) else 0,
            limit=counters.get("limit") if isinstance(counters.get("limit"), int) else None,
        ),
    )


def _invitee(item: dict[str, Any]) -> Invitee:
    status = _text(item.get("status"))
    return Invitee(
        uri=str(item.get("uri") or ""),
        email=str(item.get("email") or ""),
        name=_text(item.get("name")),
        status=status,
        timezone=_text(item.get("timezone")),
        created_at=_text(item.get("created_at")),
        canceled=status == "canceled",
    )


def _next_token(pagination: Any) -> str | None:
    if not isinstance(pagination, dict):
        return None
    token = pagination.get("next_page_token")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _checked_uuid(value: str) -> str:
    """Reject anything that is not a canonical UUID before it reaches a URL path."""
    try:
        return str(uuidlib.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Invalid Calendly event id; pass the uuid from a listed event.") from exc


def _checked_uri(value: str, what: str) -> str:
    """Calendly filters take a full api.calendly.com URI, which this connector originates."""
    text = str(value or "")
    if not text.startswith(f"{API_BASE}/"):
        raise ValueError(f"Invalid Calendly {what} URI.")
    return text


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _clamp(value: int) -> int:
    return max(1, min(int(value), _HARD_LIMIT))


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
