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

# Write scopes are requested only for accounts that opt into writes. Each one widens consent.
WRITE_SCOPES: dict[str, str] = {
    "gmail_send": "https://www.googleapis.com/auth/gmail.send",
    "drive": "https://www.googleapis.com/auth/drive",
    "calendar_events": "https://www.googleapis.com/auth/calendar.events",
    "docs": "https://www.googleapis.com/auth/documents",
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
