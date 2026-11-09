"""Read-only Notion API client. Every method returns Pydantic models, never raw dicts."""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from notion.errors import ConnectorAPIError, ConnectorAuthError
from notion.oauth import NOTION_VERSION

API_BASE = "https://api.notion.com/v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_MAX_RESULTS = 100


class SearchResult(BaseModel):
    id: str
    object: str
    title: str
    url: str | None = None
    last_edited_time: str | None = None


class PageContent(BaseModel):
    id: str
    title: str
    url: str | None = None
    last_edited_time: str | None = None
    text: str
    truncated: bool = False


class DatabaseInfo(BaseModel):
    id: str
    title: str
    url: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)


class DatabaseRow(BaseModel):
    id: str
    url: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)


class NotionClient:
    """Holds one Notion access token. Tokens are long-lived and workspace-scoped."""

    def __init__(self, http: httpx.AsyncClient, access_token: str) -> None:
        self._http = http
        self._access_token = access_token

    async def search(
        self,
        query: str,
        *,
        object_type: str | None = None,
        max_results: int = 10,
    ) -> list[SearchResult]:
        body: dict[str, Any] = {"query": query, "page_size": _clamp(max_results)}
        if object_type:
            if object_type not in {"page", "database"}:
                raise ValueError("object_type must be 'page' or 'database'.")
            body["filter"] = {"property": "object", "value": object_type}
        data = await self._request("POST", f"{API_BASE}/search", json=body)
        return [_search_result(item) for item in data.get("results", [])]

    async def read_page(self, page_id: str, *, max_blocks: int = 100) -> PageContent:
        """Page metadata plus the plain text of its blocks, depth-first, bounded by max_blocks."""
        page = await self._request("GET", f"{API_BASE}/pages/{_checked_id(page_id)}")
        blocks, truncated = await self._children(page_id, max_blocks)
        return PageContent(
            id=page["id"],
            title=_title(page.get("properties", {})),
            url=page.get("url"),
            last_edited_time=page.get("last_edited_time"),
            text="\n".join(_block_lines(blocks)).strip(),
            truncated=truncated,
        )

    async def read_database(self, database_id: str) -> DatabaseInfo:
        data = await self._request("GET", f"{API_BASE}/databases/{_checked_id(database_id)}")
        return DatabaseInfo(
            id=data["id"],
            title=_rich_text(data.get("title", [])),
            url=data.get("url"),
            properties={
                name: str(spec.get("type", "unknown"))
                for name, spec in (data.get("properties") or {}).items()
            },
        )

    async def query_database(
        self,
        database_id: str,
        *,
        filter: dict[str, Any] | None = None,
        max_results: int = 10,
    ) -> list[DatabaseRow]:
        body: dict[str, Any] = {"page_size": _clamp(max_results)}
        if filter:
            body["filter"] = filter
        data = await self._request(
            "POST", f"{API_BASE}/databases/{_checked_id(database_id)}/query", json=body
        )
        return [
            DatabaseRow(
                id=item["id"],
                url=item.get("url"),
                properties=_properties(item.get("properties", {})),
            )
            for item in data.get("results", [])
        ]

    async def _children(
        self, block_id: str, budget: int, depth: int = 0
    ) -> tuple[list[dict], bool]:
        if budget <= 0 or depth > 2:
            return [], budget <= 0
        data = await self._request(
            "GET",
            f"{API_BASE}/blocks/{_checked_id(block_id)}/children",
            params={"page_size": min(100, budget)},
        )
        results = data.get("results", [])
        collected: list[dict] = []
        for block in results:
            collected.append(block)
            if block.get("has_children") and depth < 2:
                children, exhausted = await self._children(
                    block["id"], budget - len(collected), depth + 1
                )
                collected.extend(children)
                if exhausted:
                    return collected, True
        return collected, bool(data.get("has_more"))

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._http.request(
            method,
            url,
            json=json,
            params=params,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Notion-Version": NOTION_VERSION,
            },
        )
        if response.status_code == 401:
            raise ConnectorAuthError(
                "Notion rejected the access token.",
                hint="Reconnect the Notion workspace in Settings > Connections.",
            )
        if response.status_code == 403:
            raise ConnectorAPIError(
                "Notion denied access to this resource.",
                hint="Share the page or database with the Hoja connection in Notion.",
            )
        if response.status_code == 429:
            raise ConnectorAPIError(
                "Notion rate-limited the request.",
                hint="Wait a moment and try again.",
            )
        if response.status_code >= 400:
            raise ConnectorAPIError(f"Notion API returned HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConnectorAPIError("Notion API returned an unexpected response shape.")
        return payload


def _checked_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid Notion resource id.")
    return value


def _clamp(value: int) -> int:
    return max(1, min(value, _MAX_RESULTS))


def _rich_text(items: list[dict[str, Any]] | None) -> str:
    return "".join(str(item.get("plain_text", "")) for item in items or [])


def _title(properties: dict[str, Any]) -> str:
    for spec in properties.values():
        if spec.get("type") == "title":
            return _rich_text(spec.get("title"))
    return ""


def _search_result(item: dict[str, Any]) -> SearchResult:
    if item.get("object") == "database":
        title = _rich_text(item.get("title"))
    else:
        title = _title(item.get("properties", {}))
    return SearchResult(
        id=item["id"],
        object=str(item.get("object", "unknown")),
        title=title,
        url=item.get("url"),
        last_edited_time=item.get("last_edited_time"),
    )


def _properties(properties: dict[str, Any]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for name, spec in properties.items():
        kind = spec.get("type")
        value = spec.get(kind) if kind else None
        if kind == "title" or kind == "rich_text":
            rendered[name] = _rich_text(value)
        elif kind in {"number", "checkbox", "url", "email", "phone_number"}:
            rendered[name] = "" if value is None else str(value)
        elif kind in {"select", "status"}:
            rendered[name] = "" if not value else str(value.get("name", ""))
        elif kind == "multi_select":
            rendered[name] = ", ".join(str(option.get("name", "")) for option in value or [])
        elif kind == "date":
            rendered[name] = "" if not value else str(value.get("start", ""))
        elif kind == "people":
            rendered[name] = ", ".join(str(person.get("name", "")) for person in value or [])
        else:
            rendered[name] = "" if value is None else str(value)
    return rendered


def _block_lines(blocks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        kind = str(block.get("type", ""))
        payload = block.get(kind) or {}
        text = _rich_text(payload.get("rich_text") or payload.get("caption"))
        if kind == "child_page":
            text = str(payload.get("title", ""))
        elif kind == "code":
            text = _rich_text(payload.get("rich_text"))
        if text:
            lines.append(text)
    return lines
