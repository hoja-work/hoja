"""OAuth 2.0 authorization-code flow with PKCE (S256) for Google."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Sequence
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from google_workspace.config import GoogleOAuthConfig
from google_workspace.errors import ConnectorAuthError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Read-only scopes only. Write scopes are added with the approval queue.
READ_SCOPES: dict[str, str] = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "drive": "https://www.googleapis.com/auth/drive.readonly",
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "docs": "https://www.googleapis.com/auth/documents.readonly",
}

# Google Meet is a capability of the same Google grant, not a second account. One OAuth client
# serves both APIs, and Google's incremental authorization extends an existing grant instead of
# replacing it (`include_granted_scopes=true` below). So Meet is requested as a named add-on: the
# authorization request carries *only* these scopes, and Google returns a token covering the union
# of what the user already granted and what is asked for here.
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"

# Google Chat is the second capability of that same grant, and it needs three scopes because the
# Chat API authorizes each resource family separately: a token that can list spaces cannot read
# messages. Each one sits on its own method reference page (cited in the connector README), and
# none of them permits a write.
CHAT_SPACES_SCOPE = "https://www.googleapis.com/auth/chat.spaces.readonly"
CHAT_MESSAGES_SCOPE = "https://www.googleapis.com/auth/chat.messages.readonly"
CHAT_MEMBERSHIPS_SCOPE = "https://www.googleapis.com/auth/chat.memberships.readonly"
CHAT_SCOPES: tuple[str, ...] = (
    CHAT_SPACES_SCOPE,
    CHAT_MESSAGES_SCOPE,
    CHAT_MEMBERSHIPS_SCOPE,
)

# Scope upgrades Settings > Connections can offer on an existing Google account, by add-on name.
ADDON_SCOPES: dict[str, tuple[str, ...]] = {
    "meet": (MEET_SCOPE,),
    "chat": CHAT_SCOPES,
}

# What the user reads on the button and in an error message. Kept beside the scopes so a new add-on
# cannot be added to the registry without naming itself in the UI.
ADDON_LABELS: dict[str, str] = {
    "meet": "Google Meet",
    "chat": "Google Chat",
}

# Every scope this connector can ever hold on one Google account, keyed by the capability it
# grants. Used to report what a stored grant actually covers; a user may untick individual
# scopes on the consent screen, so the answer is per capability, not "all or nothing".
# Values are the full set a capability needs: Chat is available only when all three of its scopes
# are present, because two of them cannot serve the third API's calls.
CAPABILITY_SCOPES: dict[str, tuple[str, ...]] = {
    "gmail": (READ_SCOPES["gmail"],),
    "drive": (READ_SCOPES["drive"],),
    "calendar": (READ_SCOPES["calendar"],),
    "docs": (READ_SCOPES["docs"],),
    "meet": (MEET_SCOPE,),
    "chat": CHAT_SCOPES,
}

# Write scopes are requested only for accounts that opt into writes. Each one widens consent.
WRITE_SCOPES: dict[str, str] = {
    "gmail_send": "https://www.googleapis.com/auth/gmail.send",
    "drive": "https://www.googleapis.com/auth/drive",
    "calendar_events": "https://www.googleapis.com/auth/calendar.events",
    "docs": "https://www.googleapis.com/auth/documents",
}


def merge_scopes(*groups: str | Sequence[str]) -> list[str]:
    """Order-preserving union of scope lists (each entry is a space-separated string or a list).

    A Google token obtained with `include_granted_scopes=true` covers the union of the scopes
    already granted and the ones just requested, so the grant recorded for an account must be a
    union too. Replacing it would claim, after a Meet upgrade, that the account no longer covers
    Workspace - and the reverse after a plain Workspace reconnect.
    """
    seen: list[str] = []
    for group in groups:
        items = group.split() if isinstance(group, str) else list(group)
        for item in items:
            scope = item.strip()
            if scope and scope not in seen:
                seen.append(scope)
    return seen


def capabilities_for(scopes: Sequence[str] | str) -> dict[str, bool]:
    """Which capabilities a stored grant covers, per API.

    A capability is available only if every scope it needs is present: reporting Chat as ready
    when the user unticked one of its three permissions would show a connector that answers 403.
    """
    granted = set(scopes.split() if isinstance(scopes, str) else scopes)
    return {
        name: all(scope in granted for scope in required)
        for name, required in CAPABILITY_SCOPES.items()
    }


class TokenSet(BaseModel):
    access_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: str = ""
    refresh_token: str | None = None


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 with method S256."""
    verifier = secrets.token_urlsafe(64)
    return verifier, _challenge_for(verifier)


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    config: GoogleOAuthConfig,
    *,
    scopes: Sequence[str],
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    config: GoogleOAuthConfig,
    *,
    code: str,
    code_verifier: str,
) -> TokenSet:
    return await _post_token(
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
        },
    )


async def refresh_access_token(
    http: httpx.AsyncClient,
    config: GoogleOAuthConfig,
    *,
    refresh_token: str,
) -> TokenSet:
    return await _post_token(
        http,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        },
    )


async def _post_token(http: httpx.AsyncClient, form: dict[str, str]) -> TokenSet:
    response = await http.post(TOKEN_URL, data=form)
    if response.status_code != 200:
        raise ConnectorAuthError(
            f"Google token request failed: {_google_error_code(response)}.",
            hint="Reconnect the Google account. If this persists, check the OAuth client settings.",
        )
    return TokenSet.model_validate(response.json())


def _google_error_code(response: httpx.Response) -> str:
    # Only the machine-readable error code is surfaced; the body may echo request values.
    try:
        code = response.json().get("error")
    except ValueError:
        return "unknown error"
    return str(code) if isinstance(code, str) else "unknown error"
