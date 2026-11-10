"""Read-only Sentry API client. Every method returns Pydantic models, never raw dicts.

Shapes come from Sentry's published API reference
(<https://docs.sentry.io/api/events/list-an-organizations-issues/>) and were confirmed against a
live installation token on 2026-11-09:

* lists answer a bare JSON **array**, not a `{"data": ...}` envelope;
* pagination is the RFC 5988 `Link` header — `<url>; rel="next"; results="true"` — *not* a body
  cursor, so the client reads the header and stops when `results="false"`
  (<https://docs.sentry.io/api/pagination/>);
* issue and issue-event paths are **organization-scoped**. The org slug comes from the stored
  grant, never from a tool argument, so one tenant's token cannot be aimed at another org;
* the org issue list applies a default `is:unresolved` query unless a `query` is supplied; an
  explicitly empty query returns everything.

A 401 means the access token expired. The client asks the token source to invalidate and retries
exactly once: a second 401 is a real credential problem, and retrying again would turn one broken
grant into a refresh storm against an eight-hour token.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from sentry.errors import ConnectorAPIError, ConnectorAuthError

API_BASE = "https://sentry.io/api/0"
_MAX_PAGE = 100
_MAX_PAGES = 10
_HARD_LIMIT = 500
_ISSUE_ID = re.compile(r"^[0-9]{1,32}$")
# Provider text is data, and data that arrives from a customer's error tracker can be long. Every
# string that reaches a tool result is cut here, so one issue cannot flood the agent's context.
_MAX_TEXT = 2000


class TokenSource(Protocol):
    """The stored grant for one connected organization, owned by the server, not by this package."""

    async def access_token(self) -> str:
        """A currently-valid access token, refreshing if needed."""

    async def invalidate(self) -> None:
        """Tell the source that the token it handed out was rejected."""

    def organization(self) -> str:
        """The Sentry organization slug this installation is bound to."""


class Organization(BaseModel):
    id: str
    slug: str
    name: str


class Project(BaseModel):
    id: str
    slug: str
    name: str
    platform: str | None = None
    date_created: str | None = None


class IssueProject(BaseModel):
    id: str | None = None
    slug: str | None = None
    name: str | None = None


class Issue(BaseModel):
    id: str
    short_id: str | None = None
    title: str
    culprit: str | None = None
    level: str | None = None
    status: str | None = None
    count: int | None = None
    user_count: int | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    permalink: str | None = None
    project: IssueProject | None = None


class EventTag(BaseModel):
    key: str
    value: str


class LatestEvent(BaseModel):
    """A bounded summary of one event.

    `message`, `title` and tag values are whatever the reporting application sent. They are
    untrusted text, and the tool description says so.
    """

    event_id: str
    date_created: str | None = None
    title: str | None = None
    message: str | None = None
    platform: str | None = None
    culprit: str | None = None
    level: str | None = None
    tags: list[EventTag] = Field(default_factory=list)
    exception_types: list[str] = Field(default_factory=list)


class SentryClient:
    """Holds one connected organization's token source and talks to sentry.io/api/0."""

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens

    async def get_organization(self) -> Organization:
        """The organization this token is scoped to. Sentry returns exactly one."""
        items = await self._get("/organizations/")
        rows = items if isinstance(items, list) else []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        return Organization(
            id=str(first.get("id") or ""),
            slug=str(first.get("slug") or ""),
            name=str(first.get("name") or ""),
        )

    async def list_projects(self, *, max_results: int = 100) -> list[Project]:
        org = self._org()
        items = await self._pages(f"/organizations/{org}/projects/", {}, max_results=max_results)
        return [_project(item) for item in items]

    async def list_issues(
        self,
        *,
        query: str | None = None,
        project: str | None = None,
        sort: str | None = None,
        stats_period: str | None = None,
        max_results: int = 50,
    ) -> list[Issue]:
        params: dict[str, Any] = {}
        if query is not None:
            # An explicit empty string means "all issues"; omitting the parameter leaves Sentry's
            # own `is:unresolved` default in place, which is the useful default for a reader.
            params["query"] = query
        if project is not None:
            params["project"] = _project_slug(project)
        if sort is not None:
            if sort not in {"date", "freq", "inbox", "new", "recommended", "trends", "user"}:
                raise ValueError(
                    "sort must be one of date, freq, inbox, new, recommended, trends, user."
                )
            params["sort"] = sort
        if stats_period is not None:
            params["statsPeriod"] = _period(stats_period)
        items = await self._pages(
            f"/organizations/{self._org()}/issues/", params, max_results=max_results
        )
        return [_issue(item) for item in items]

    async def get_issue(self, issue_id: str) -> Issue:
        org = self._org()
        item = await self._get(f"/organizations/{org}/issues/{_checked_issue_id(issue_id)}/")
        return _issue(item if isinstance(item, dict) else {})

    async def get_latest_event(self, issue_id: str) -> LatestEvent:
        org = self._org()
        item = await self._get(
            f"/organizations/{org}/issues/{_checked_issue_id(issue_id)}/events/latest/"
        )
        return _event(item if isinstance(item, dict) else {})

    def _org(self) -> str:
        org = (self._tokens.organization() or "").strip()
        if not org:
            raise ConnectorAuthError(
                "The Sentry connection has no organization.",
                hint="Reconnect Sentry in Settings > Connections.",
            )
        return _slug(org)

    async def _pages(
        self, path: str, params: dict[str, Any], *, max_results: int
    ) -> list[dict[str, Any]]:
        """Walk the `Link: rel="next"` cursor with a page cap *and* a result cap.

        A provider that always advertises another page cannot turn one tool call into an endless
        loop, and a caller asking for 5 results never downloads 500.
        """
        wanted = _clamp(max_results)
        collected: list[dict[str, Any]] = []
        next_url: str | None = None
        for _ in range(_MAX_PAGES):
            page_params: dict[str, Any] | None
            if next_url:
                page_params = None
            else:
                page_params = dict(params)
                page_params["limit"] = min(_MAX_PAGE, max(1, wanted - len(collected)))
            response = await self._request(
                "GET", next_url or f"{API_BASE}{path}", params=page_params
            )
            items = _json_list(response)
            collected.extend(item for item in items if isinstance(item, dict))
            next_url = _next_link(response)
            if not next_url or len(collected) >= wanted:
                break
        return collected[:wanted]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._request("GET", f"{API_BASE}{path}", params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorAPIError("Sentry returned a body that is not JSON.") from exc

    @staticmethod
    def _call_options(token: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """The keyword arguments for one call.

        `params` is omitted rather than emptied when there is none: httpx rebuilds the query string
        from `params`, so passing `{}` against a full `Link: rel="next"` URL would silently drop
        the cursor and fetch page one forever.
        """
        options: dict[str, Any] = {"headers": {"Authorization": f"Bearer {token}"}}
        if params is not None:
            options["params"] = params
        return options

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        token = await self._tokens.access_token()
        response = await self._http.request(method, url, **self._call_options(token, params))
        if response.status_code == 401:
            # Exactly one retry: the source refreshes on the next access_token() call.
            await self._tokens.invalidate()
            token = await self._tokens.access_token()
            response = await self._http.request(method, url, **self._call_options(token, params))
        if response.status_code == 401:
            raise ConnectorAuthError(
                "Sentry rejected the stored credential.",
                hint="Reconnect Sentry in Settings > Connections.",
            )
        if response.status_code == 403:
            raise ConnectorAPIError(
                "This Sentry installation lacks a scope this tool needs.",
                hint=(
                    "Reconnect Sentry and allow the read permissions shown on the install page "
                    "(Project, Issue & Event, Organization)."
                ),
            )
        if response.status_code == 404:
            raise ConnectorAPIError("Sentry has no such resource for this organization.")
        if response.status_code == 429:
            raise ConnectorAPIError(
                "Sentry rate-limited the request.",
                hint=f"Wait {_retry_after(response)} seconds and try again.",
            )
        if response.status_code >= 500:
            raise ConnectorAPIError(f"Sentry API returned HTTP {response.status_code}.")
        if response.status_code >= 400:
            raise ConnectorAPIError(f"Sentry API returned HTTP {response.status_code}.")
        return response


def _json_list(response: httpx.Response) -> list[Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError("Sentry returned a body that is not JSON.") from exc
    if not isinstance(payload, list):
        raise ConnectorAPIError("Sentry returned an unexpected response shape.")
    return payload


def _next_link(response: httpx.Response) -> str | None:
    """The `rel="next"` URL, but only while `results="true"` says there is anything to fetch.

    Sentry always emits a next cursor; the `results` flag is what distinguishes "there is a next
    page" from "there might be one".
    """
    header = response.headers.get("link") or ""
    for part in header.split(","):
        segments = [segment.strip() for segment in part.split(";")]
        if not segments or not segments[0].startswith("<"):
            continue
        url = segments[0][1 : segments[0].find(">")] if ">" in segments[0] else ""
        attrs = {}
        for segment in segments[1:]:
            if "=" in segment:
                key, _, value = segment.partition("=")
                attrs[key.strip().lower()] = value.strip().strip('"')
        if attrs.get("rel") == "next" and attrs.get("results", "false") == "true" and url:
            return url
    return None


def _project(item: dict[str, Any]) -> Project:
    return Project(
        id=str(item.get("id") or ""),
        slug=str(item.get("slug") or ""),
        name=str(item.get("name") or ""),
        platform=_text(item.get("platform")),
        date_created=_text(item.get("dateCreated")),
    )


def _issue(item: dict[str, Any]) -> Issue:
    project = item.get("project")
    project = project if isinstance(project, dict) else {}
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return Issue(
        id=str(item.get("id") or ""),
        short_id=_text(item.get("shortId")),
        # `title` is usually a copy of metadata.title; fall back so an old issue is never nameless.
        title=_text(item.get("title")) or _text(metadata.get("title")) or "(untitled issue)",
        culprit=_text(item.get("culprit")),
        level=_text(item.get("level")),
        status=_text(item.get("status")),
        count=_int(item.get("count")),
        user_count=_int(item.get("userCount")),
        first_seen=_text(item.get("firstSeen")),
        last_seen=_text(item.get("lastSeen")),
        permalink=_text(item.get("permalink")),
        project=IssueProject(
            id=str(project.get("id")) if project.get("id") is not None else None,
            slug=_text(project.get("slug")),
            name=_text(project.get("name")),
        )
        if project
        else None,
    )


def _event(item: dict[str, Any]) -> LatestEvent:
    tags: list[EventTag] = []
    raw_tags = item.get("tags")
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, dict) and tag.get("key") is not None:
                tags.append(
                    EventTag(key=_cut(str(tag.get("key"))), value=_cut(str(tag.get("value") or "")))
                )
    types: list[str] = []
    entries = item.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "exception":
                continue
            values = (entry.get("data") or {}).get("values") if isinstance(entry.get("data"), dict) else None
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict) and isinstance(value.get("type"), str):
                    types.append(_cut(value["type"]))
    return LatestEvent(
        event_id=str(item.get("eventID") or item.get("id") or ""),
        date_created=_text(item.get("dateCreated")),
        title=_cut_opt(item.get("title")),
        message=_cut_opt(item.get("message")),
        platform=_text(item.get("platform")),
        culprit=_cut_opt(item.get("culprit")),
        level=_text(item.get("level")),
        tags=tags,
        exception_types=types,
    )


def _checked_issue_id(value: str) -> str:
    text = str(value or "").strip()
    if not _ISSUE_ID.fullmatch(text):
        raise ValueError("Invalid Sentry issue id; pass the numeric `id` from a listed issue.")
    return text


def _slug(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", text):
        raise ValueError("Invalid Sentry organization slug in the stored grant.")
    return text


def _project_slug(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", text):
        raise ValueError("Invalid Sentry project slug.")
    return text


def _period(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,4}[dhms]", text):
        raise ValueError("stats_period must look like 24h, 14d, 60m or 30s.")
    return text


def _text(value: Any) -> str | None:
    return _cut(value) if isinstance(value, str) and value else None


def _cut_opt(value: Any) -> str | None:
    return _cut(value) if isinstance(value, str) and value else None


def _cut(value: str) -> str:
    return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "…"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _clamp(value: int) -> int:
    return max(1, min(int(value), _HARD_LIMIT))


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
