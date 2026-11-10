"""Notion OAuth 2.0 authorization-code flow.

Notion has no OAuth `scope` parameter: capabilities are fixed on the integration object and
shown on the consent screen. There is also no PKCE; the CSRF protection is the AES-GCM sealed
`state` the caller passes in (see connectors_api), which is single-use and bound to the user.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from notion.config import NotionOAuthConfig
from notion.errors import ConnectorAPIError, ConnectorAuthError

AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_VERSION = "2022-06-28"


class NotionToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Notion currently returns a nullable refresh token. This connector deliberately keeps the
    # long-lived access token only and treats a 401 as "reconnect"; rotation can be added later
    # without a schema change because the credential column stores a single sealed value.
    refresh_token: str | None = None
    bot_id: str = ""
    workspace_id: str = ""
    workspace_name: str | None = None
    workspace_icon: str | None = None
    owner: dict = Field(default_factory=dict)


def authorization_url(config: NotionOAuthConfig, *, state: str) -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "owner": "user",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    config: NotionOAuthConfig,
    *,
    code: str,
) -> NotionToken:
    """Exchange the authorization code. Notion requires HTTP Basic client authentication and a
    JSON body; the redirect URI must repeat the one used to start the flow."""
    response = await http.post(
        TOKEN_URL,
        auth=(config.client_id, config.client_secret),
        json={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
        },
        headers={"Notion-Version": NOTION_VERSION},
    )
    if response.status_code in (400, 401, 403):
        raise ConnectorAuthError(
            f"Notion rejected the authorization code: {_error_code(response)}.",
            hint="Start the connection again from Settings > Connections.",
        )
    if response.status_code >= 400:
        raise ConnectorAPIError(f"Notion token request returned HTTP {response.status_code}.")
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ConnectorAPIError("Notion token response had an unexpected shape.")
    return NotionToken.model_validate(payload)


def _error_code(response: httpx.Response) -> str:
    # Only the machine-readable code is surfaced; the body may echo request values.
    try:
        body = response.json()
    except ValueError:
        return "unknown error"
    code = body.get("error") if isinstance(body, dict) else None
    if isinstance(code, dict):
        code = code.get("code")
    return str(code) if code else "unknown error"
