"""Read-only Notion client: version header, title/text extraction, and auth/rate-limit mapping."""

from __future__ import annotations

import httpx
import pytest
import respx
from notion.client import NotionClient
from notion.errors import ConnectorAPIError, ConnectorAuthError


def _client() -> NotionClient:
    return NotionClient(httpx.AsyncClient(), "secret_notion_access")


@respx.mock
async def test_search_sends_the_pinned_version_and_returns_titles() -> None:
    route = respx.post("https://api.notion.com/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "page",
                        "id": "page-1",
                        "url": "https://notion.so/page-1",
                        "last_edited_time": "2026-11-09T00:00:00.000Z",
                        "properties": {
                            "Name": {
                                "type": "title",
                                "title": [{"plain_text": "Roadmap"}],
                            }
                        },
                    },
                    {
                        "object": "database",
                        "id": "db-1",
                        "url": "https://notion.so/db-1",
                        "last_edited_time": "2026-11-08T00:00:00.000Z",
                        "title": [{"plain_text": "Tasks"}],
                    },
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        results = await NotionClient(http, "secret_notion_access").search("road")
    request = route.calls.last.request
    assert request.headers["Notion-Version"] == "2022-06-28"
    assert request.headers["Authorization"] == "Bearer secret_notion_access"
    assert [item.title for item in results] == ["Roadmap", "Tasks"]
    assert [item.object for item in results] == ["page", "database"]


@respx.mock
async def test_read_page_flattens_block_text() -> None:
    respx.get("https://api.notion.com/v1/pages/page-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "last_edited_time": "2026-11-09T00:00:00.000Z",
                "properties": {"Name": {"type": "title", "title": [{"plain_text": "Roadmap"}]}},
            },
        )
    )
    respx.get("https://api.notion.com/v1/blocks/page-1/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "block-1",
                        "type": "heading_1",
                        "has_children": False,
                        "heading_1": {"rich_text": [{"plain_text": "Q4"}]},
                    },
                    {
                        "id": "block-2",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": [{"plain_text": "Ship the connector."}]},
                    },
                ],
                "has_more": False,
            },
        )
    )
    async with httpx.AsyncClient() as http:
        page = await NotionClient(http, "secret_notion_access").read_page("page-1")
    assert page.title == "Roadmap"
    assert page.text == "Q4\nShip the connector."
    assert page.truncated is False


@respx.mock
async def test_query_database_renders_property_values() -> None:
    respx.post("https://api.notion.com/v1/databases/db-1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "row-1",
                        "url": "https://notion.so/row-1",
                        "properties": {
                            "Name": {"type": "title", "title": [{"plain_text": "Publish"}]},
                            "Status": {"type": "select", "select": {"name": "Doing"}},
                            "Due": {"type": "date", "date": {"start": "2026-11-10"}},
                        },
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        rows = await NotionClient(http, "secret_notion_access").query_database("db-1")
    assert rows[0].properties == {"Name": "Publish", "Status": "Doing", "Due": "2026-11-10"}


@respx.mock
async def test_401_means_reconnect() -> None:
    respx.post("https://api.notion.com/v1/search").mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError):
            await NotionClient(http, "stale").search("x")


@respx.mock
async def test_403_asks_the_user_to_share_the_resource() -> None:
    respx.post("https://api.notion.com/v1/search").mock(return_value=httpx.Response(403, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await NotionClient(http, "token").search("x")
    assert "Share the page or database" in (excinfo.value.hint or "")


def test_invalid_resource_id_is_rejected_before_a_request() -> None:
    import asyncio

    async def run() -> None:
        with pytest.raises(ValueError):
            await _client().read_page("../../etc/passwd")

    asyncio.run(run())
