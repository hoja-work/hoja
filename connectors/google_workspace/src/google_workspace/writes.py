"""Write operations available through the approval queue. Only the names in WRITE_TOOLS can run,
and each one validates its arguments before any request reaches Google."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from google_workspace.client import GoogleWorkspaceClient
from google_workspace.tools import AccountGrant

WRITE_TOOLS = ("gmail_send", "calendar_event_create", "docs_append_text")


def _str(args: dict[str, Any], key: str, limit: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{key} must be a non-empty string up to {limit} characters.")
    return value


async def run_write(
    http: httpx.AsyncClient, grant: AccountGrant, tool: str, args: dict[str, Any]
) -> str:
    """Execute one approved write. Returns a short, non-sensitive summary for the audit row."""
    if tool not in WRITE_TOOLS:
        raise ValueError(f"Unknown write tool: {tool}")
    client = GoogleWorkspaceClient(http, await grant.access_token())
    if tool == "gmail_send":
        message_id = await client.gmail_send(
            to=_str(args, "to", 320),
            subject=_str(args, "subject", 200),
            body=_str(args, "body", 20000),
        )
        return f"sent message {message_id}"
    if tool == "calendar_event_create":
        start = datetime.fromisoformat(_str(args, "start", 64))
        end = datetime.fromisoformat(_str(args, "end", 64))
        event_id = await client.calendar_event_create(_str(args, "summary", 200), start, end)
        return f"created event {event_id}"
    await client.docs_append_text(_str(args, "document_id", 200), _str(args, "text", 20000))
    return "appended text"
