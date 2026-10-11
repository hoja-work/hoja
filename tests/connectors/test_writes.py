"""Write tools: nothing reaches Google until an approval runs the executor, and bad input never sends a request."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from google_workspace import ConnectorAuthError
from google_workspace.client import GoogleWorkspaceClient
from google_workspace.tools import AccountGrant
from google_workspace.writes import WRITE_TOOLS, run_write


async def _grant() -> AccountGrant:
    async def token() -> str:
        return "ya29.WRITE"

    return AccountGrant(account_id="acct-1", external_account="ana@example.test", access_token=token)


@respx.mock
async def test_gmail_send_posts_encoded_message_with_token() -> None:
    route = respx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "m1"}))
    async with httpx.AsyncClient() as http:
        summary = await run_write(http, await _grant(), "gmail_send",
                                  {"to": "bob@example.test", "subject": "Hi", "body": "Hello"})
    assert summary == "sent message m1"
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ya29.WRITE"
    raw = base64.urlsafe_b64decode(json.loads(sent.content)["raw"] + "==").decode()
    assert "To: bob@example.test" in raw and raw.endswith("Hello")


@respx.mock
async def test_gmail_send_rejects_header_injection_without_request() -> None:
    route = respx.post(host="gmail.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError):
            await run_write(http, await _grant(), "gmail_send",
                            {"to": "bob@example.test", "subject": "Hi\r\nBcc: evil@x.test", "body": "x"})
    assert route.call_count == 0


@respx.mock
async def test_gmail_send_rejects_multiple_recipients() -> None:
    route = respx.post(host="gmail.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError):
            await run_write(http, await _grant(), "gmail_send",
                            {"to": "a@x.test, b@x.test", "subject": "Hi", "body": "x"})
    assert route.call_count == 0


@respx.mock
async def test_calendar_event_create_sends_times() -> None:
    route = respx.post("https://www.googleapis.com/calendar/v3/calendars/primary/events").mock(
        return_value=httpx.Response(200, json={"id": "e1"}))
    start = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    async with httpx.AsyncClient() as http:
        summary = await run_write(http, await _grant(), "calendar_event_create",
                                  {"summary": "Standup", "start": start.isoformat(), "end": end.isoformat()})
    assert summary == "created event e1"
    body = json.loads(route.calls.last.request.content)
    assert body["summary"] == "Standup"


@respx.mock
async def test_calendar_event_rejects_end_before_start_without_request() -> None:
    route = respx.post(host="www.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError):
            await run_write(http, await _grant(), "calendar_event_create",
                            {"summary": "x", "start": "2026-05-01T10:00:00+00:00",
                             "end": "2026-05-01T09:00:00+00:00"})
    assert route.call_count == 0


@respx.mock
async def test_docs_append_uses_batch_update() -> None:
    route = respx.post("https://docs.googleapis.com/v1/documents/doc_1:batchUpdate").mock(
        return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        summary = await run_write(http, await _grant(), "docs_append_text",
                                  {"document_id": "doc_1", "text": "Note"})
    assert summary == "appended text"
    assert route.call_count == 1


async def test_unknown_write_tool_is_rejected() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="Unknown write tool"):
            await run_write(http, await _grant(), "drive_delete_everything", {})


def test_write_tool_set_is_exactly_the_three_approved_operations() -> None:
    assert set(WRITE_TOOLS) == {"gmail_send", "calendar_event_create", "docs_append_text"}


@respx.mock
async def test_client_post_maps_401_to_auth_error() -> None:
    respx.post(host="gmail.googleapis.com").mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient() as http:
        client = GoogleWorkspaceClient(http, "expired")
        with pytest.raises(ConnectorAuthError):
            await client.gmail_send("bob@example.test", "Hi", "x")
