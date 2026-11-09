"""Read-only Slack Web API client. Every method returns Pydantic models, never raw dicts.

Slack answers **HTTP 200** for most failures, including an unusable token: measured live on
2026-11-09, `conversations.list`, `conversations.history`, `search.messages`, `users.list` and
`auth.revoke` all returned `200 {"ok":false,"error":"invalid_auth"}` for a bogus `xoxp-` value.
Every response therefore passes through the same envelope check before any field is read; the
HTTP status is only consulted for 429 and 5xx, where Slack does not send a Slack envelope.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

from slack.errors import ConnectorAPIError, ConnectorAuthError

API_BASE = "https://slack.com/api/"
# Slack conversation ids are an uppercase type letter (C public, G private/group, D DM) followed
# by uppercase alphanumerics. Anchoring on the type letter is what stops a channel *name* from
# being sent as an id and quietly becoming a different request.
_ID_PATTERN = re.compile(r"^[CGD][A-Z0-9]{1,63}$")
_TS_PATTERN = re.compile(r"^\d{1,12}(\.\d{1,9})?$")
_MAX_PAGE = 200
_MAX_PAGES = 10
_HARD_LIMIT = 500

# Slack error codes that mean the stored grant is dead and only a new consent can fix it.
_AUTH_ERRORS = frozenset(
    {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive", "no_token"}
)
_GONE_ERRORS = frozenset({"invalid_auth", "not_authed", "token_revoked", "token_expired"})


class Channel(BaseModel):
    id: str
    name: str
    is_private: bool = False
    is_archived: bool = False
    is_member: bool = False
    num_members: int | None = None
    topic: str | None = None
    purpose: str | None = None


class Message(BaseModel):
    ts: str
    text: str
    user: str | None = None
    user_name: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    permalink: str | None = None
    thread_ts: str | None = None
    reply_count: int | None = None


class SearchResult(BaseModel):
    total: int = 0
    messages: list[Message] = Field(default_factory=list)


class User(BaseModel):
    id: str
    name: str
    real_name: str | None = None
    display_name: str | None = None
    is_bot: bool = False
    deleted: bool = False
    tz: str | None = None


class SlackClient:
    """Holds one Slack user token (`xoxp-`). The token is long-lived: rotation is off, so there
    is nothing to refresh and a rejection always means "reconnect"."""

    def __init__(self, http: httpx.AsyncClient, access_token: str) -> None:
        self._http = http
        self._access_token = access_token

    async def list_channels(
        self, *, max_results: int = 100, include_private: bool = True
    ) -> list[Channel]:
        types = "public_channel,private_channel" if include_private else "public_channel"
        return await self._cursor_pages(
            "conversations.list",
            {"types": types, "exclude_archived": "true"},
            extract=lambda data: [_channel(item) for item in data.get("channels") or []],
            max_results=max_results,
        )

    async def read_channel_history(
        self,
        channel: str,
        *,
        max_results: int = 50,
        oldest: str | None = None,
        latest: str | None = None,
    ) -> list[Message]:
        params: dict[str, Any] = {"channel": _checked_channel(channel)}
        if oldest is not None:
            params["oldest"] = _checked_ts(oldest)
        if latest is not None:
            params["latest"] = _checked_ts(latest)
        return await self._cursor_pages(
            "conversations.history",
            params,
            extract=lambda data: [_message(item, channel) for item in data.get("messages") or []],
            max_results=max_results,
        )

    async def search_messages(
        self, query: str, *, max_results: int = 20, sort: str = "score"
    ) -> SearchResult:
        """`search.messages` pages by number, not by cursor, and only returns what the consenting
        user can see. It is a lookup, not a backfill: history per channel is the backfill path."""
        if not query.strip():
            raise ValueError("query must not be empty.")
        if sort not in {"score", "timestamp"}:
            raise ValueError("sort must be 'score' or 'timestamp'.")
        wanted = _clamp(max_results)
        per_page = min(100, wanted)
        found: list[Message] = []
        total = 0
        for page in range(1, _MAX_PAGES + 1):
            data = await self._request(
                "search.messages",
                {"query": query, "count": per_page, "page": page, "sort": sort},
            )
            block = data.get("messages")
            block = block if isinstance(block, dict) else {}
            if isinstance(block.get("total"), int):
                total = int(block["total"])
            batch = [_message(item, None) for item in block.get("matches") or []]
            found.extend(batch)
            paging = block.get("paging")
            pages = int(paging.get("pages") or 1) if isinstance(paging, dict) else 1
            if not batch or page >= pages or len(found) >= wanted:
                break
        return SearchResult(total=total, messages=found[:wanted])

    async def lookup_users(self, *, max_results: int = 100, query: str | None = None) -> list[User]:
        """Directory lookup. A filtered query pages up to the hard cap before filtering, because
        filtering only the first page would answer "no such person" for anyone past it."""
        wanted = _clamp(max_results)
        needle = (query or "").strip().lower()
        users = await self._cursor_pages(
            "users.list",
            {},
            extract=lambda data: [_user(item) for item in data.get("members") or []],
            max_results=_HARD_LIMIT if needle else wanted,
        )
        if not needle:
            return users[:wanted]
        matches = [
            user
            for user in users
            if needle
            in " ".join(
                part for part in (user.name, user.real_name, user.display_name) if part
            ).lower()
        ]
        return matches[:wanted]

    async def _cursor_pages(
        self,
        method: str,
        params: dict[str, Any],
        *,
        extract: Callable[[dict[str, Any]], list[Any]],
        max_results: int,
    ) -> list[Any]:
        """Walk `response_metadata.next_cursor` with both a result cap and a page cap, so a
        workspace that always returns a cursor cannot turn one tool call into an endless loop."""
        wanted = _clamp(max_results)
        collected: list[Any] = []
        cursor = ""
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            page_params["limit"] = min(_MAX_PAGE, max(1, wanted - len(collected)))
            if cursor:
                page_params["cursor"] = cursor
            data = await self._request(method, page_params)
            items = extract(data)
            collected.extend(items)
            cursor = _next_cursor(data)
            if not cursor or not items or len(collected) >= wanted:
                break
        return collected[:wanted]

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.get(
            f"{API_BASE}{method}",
            params=params,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        if response.status_code == 429:
            raise ConnectorAPIError(
                "Slack rate-limited the request.",
                hint=f"Wait {_retry_after(response)} seconds and try again.",
            )
        if response.status_code >= 500:
            raise ConnectorAPIError(f"Slack API returned HTTP {response.status_code}.")
        payload = _payload(response)
        if payload.get("ok") is not True:
            raise _error_for(payload)
        return payload


async def revoke(http: httpx.AsyncClient, access_token: str) -> bool:
    """Revoke the user token on Slack's side. `auth.revoke` needs no scope: the token revokes
    itself. True means the grant is gone, including the case where Slack reports the token was
    already invalid."""
    response = await http.post(
        f"{API_BASE}auth.revoke", headers={"Authorization": f"Bearer {access_token}"}
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            "Slack rate-limited the revocation.",
            hint=f"Wait {_retry_after(response)} seconds and try again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Slack revocation returned HTTP {response.status_code}.")
    payload = _payload(response)
    if payload.get("ok") is True and payload.get("revoked") is True:
        return True
    error = str(payload.get("error") or "")
    if error in _GONE_ERRORS:
        return True
    raise ConnectorAPIError(f"Slack did not revoke the token: {error or 'unknown error'}.")


def _payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError("Slack returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError("Slack returned an unexpected response shape.")
    return payload


def _error_for(payload: dict[str, Any]) -> Exception:
    error = str(payload.get("error") or "unknown_error")
    if error in _AUTH_ERRORS:
        return ConnectorAuthError(
            f"Slack rejected the stored credential ({error}).",
            hint="Reconnect the Slack workspace in Settings > Connections.",
        )
    if error == "missing_scope":
        needed = str(payload.get("needed") or "unknown")
        provided = str(payload.get("provided") or "none")
        return ConnectorAPIError(
            "The Slack token lacks a scope this tool needs.",
            hint=f"Slack asked for {needed} and the grant has {provided}. Reconnect to consent.",
        )
    if error == "ratelimited":
        return ConnectorAPIError(
            "Slack rate-limited the request.", hint="Wait a moment and try again."
        )
    if error in {"channel_not_found", "not_in_channel", "is_archived"}:
        return ConnectorAPIError(
            f"Slack cannot read that channel ({error}).",
            hint=(
                "Check the channel id. The consenting user must be a member to read a private "
                "channel, and archived channels are not readable."
            ),
        )
    return ConnectorAPIError(f"Slack returned error {error}.")


def _next_cursor(data: dict[str, Any]) -> str:
    meta = data.get("response_metadata")
    if not isinstance(meta, dict):
        return ""
    cursor = meta.get("next_cursor")
    return cursor.strip() if isinstance(cursor, str) else ""


def _channel(item: dict[str, Any]) -> Channel:
    return Channel(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("id") or ""),
        is_private=bool(item.get("is_private") or item.get("is_group")),
        is_archived=bool(item.get("is_archived")),
        is_member=bool(item.get("is_member")),
        num_members=item.get("num_members") if isinstance(item.get("num_members"), int) else None,
        topic=_text_block(item.get("topic")),
        purpose=_text_block(item.get("purpose")),
    )


def _message(item: dict[str, Any], channel: str | None) -> Message:
    """Search results nest their channel as an object; history results are flat."""
    nested = item.get("channel")
    nested = nested if isinstance(nested, dict) else {}
    return Message(
        ts=str(item.get("ts") or ""),
        text=str(item.get("text") or ""),
        user=str(item.get("user") or "") or None,
        user_name=str(item.get("username") or "") or None,
        channel_id=str(nested.get("id") or channel or "") or None,
        channel_name=str(nested.get("name") or "") or None,
        permalink=str(item.get("permalink") or "") or None,
        thread_ts=str(item.get("thread_ts") or "") or None,
        reply_count=item.get("reply_count") if isinstance(item.get("reply_count"), int) else None,
    )


def _user(item: dict[str, Any]) -> User:
    profile = item.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    return User(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or ""),
        real_name=str(item.get("real_name") or "") or None,
        display_name=str(profile.get("display_name") or "") or None,
        is_bot=bool(item.get("is_bot")),
        deleted=bool(item.get("deleted")),
        tz=str(item.get("tz") or "") or None,
    )


def _text_block(value: Any) -> str | None:
    if isinstance(value, dict):
        text = value.get("value")
        return str(text) if isinstance(text, str) and text else None
    return None


def _checked_channel(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value or ""):
        raise ValueError("Invalid Slack channel id.")
    return value


def _checked_ts(value: str) -> str:
    if not _TS_PATTERN.fullmatch(str(value or "")):
        raise ValueError("Invalid Slack timestamp; use epoch seconds like 1700000000.000100.")
    return str(value)


def _clamp(value: int) -> int:
    return max(1, min(int(value), _HARD_LIMIT))


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
