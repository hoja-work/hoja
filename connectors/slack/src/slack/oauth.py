"""Slack OAuth 2.0 authorization-code flow for a user-token-only app.

Two provider-specific shapes are easy to get wrong and are handled here on purpose:

* This app has **no bot scopes**, so the authorize URL carries `user_scope=` and an empty
  `scope=`, and the usable credential is `authed_user.access_token` (an `xoxp-` user token),
  not the top-level `access_token`, which is a bot token and is empty for this app shape.
* `oauth.v2.access` answers **HTTP 200 for failures too**, with `{"ok": false, "error": "..."}`
  (measured live 2026-11-09). The error code is the only signal; a status-code check alone would
  read a rejected grant as a success.

Token rotation is disabled on the app (provisioning contract §2), so the grant returns a
long-lived token and no refresh token. There is no refresh path to get wrong.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from slack.config import SlackOAuthConfig
from slack.errors import ConnectorAPIError, ConnectorAuthError

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"

# Exactly the scopes the provisioned app holds (app A0CFYSV19A7, 6 read-only user scopes).
# Adding one here without adding it to the app makes every consent fail with `invalid_scope`,
# and subtracting one silently removes a tool's ability to work.
USER_READ_SCOPES: tuple[str, ...] = (
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "search:read",
    "users:read",
)

_AUTH_ERRORS = frozenset(
    {
        "invalid_code",
        "invalid_grant",
        "invalid_client_id",
        "bad_client_secret",
        "oauth_authorization_code_invalid",
    }
)


class SlackUserToken(BaseModel):
    """The part of `oauth.v2.access` this connector keeps. Never contains the client secret."""

    access_token: str
    user_id: str
    team_id: str = ""
    team_name: str | None = None
    scopes: list[str] = Field(default_factory=list)

    @property
    def workspace(self) -> str:
        """The label Settings shows. `team.name` is not guaranteed: Slack omits it when the app
        has no `team:read` scope, and the provisioning contract deliberately did not request it."""
        return (self.team_name or "").strip() or self.team_id


def authorization_url(config: SlackOAuthConfig, *, state: str) -> str:
    params = {
        "client_id": config.client_id,
        "user_scope": ",".join(USER_READ_SCOPES),
        # Empty on purpose: a user-scope-only app sends no bot scopes at all. The provisioning
        # contract records this exact request shape (§4).
        "scope": "",
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    config: SlackOAuthConfig,
    *,
    code: str,
) -> SlackUserToken:
    """Exchange the authorization code. Client credentials go in HTTP Basic, not in the body:
    Slack's own reference recommends Basic so the secret stays out of form payloads, and the
    redirect URI must repeat the one used to start the flow."""
    response = await http.post(
        TOKEN_URL,
        auth=(config.client_id, config.client_secret),
        data={"code": code, "redirect_uri": config.redirect_uri},
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            "Slack rate-limited the token exchange.",
            hint=f"Wait {_retry_after(response)} seconds and start the connection again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Slack token request returned HTTP {response.status_code}.")
    payload = _payload(response, "token request")
    if payload.get("ok") is not True:
        error = str(payload.get("error") or "unknown_error")
        if response.status_code in (400, 401, 403) or error in _AUTH_ERRORS:
            raise ConnectorAuthError(
                f"Slack rejected the authorization code: {error}.",
                hint="Start the connection again from Settings > Connections.",
            )
        raise ConnectorAPIError(f"Slack token request failed: {error}.")

    authed_user = payload.get("authed_user")
    token = authed_user.get("access_token") if isinstance(authed_user, dict) else None
    if not isinstance(token, str) or not token:
        # With bot scopes present Slack returns a top-level bot token instead. Consuming it would
        # silently widen the grant past what the consent screen showed, so this fails closed.
        raise ConnectorAPIError(
            "Slack returned no user token.",
            hint=(
                "This app must request user scopes only. Remove any bot scopes from the Slack "
                "app and connect again."
            ),
        )
    if not token.startswith("xoxp-"):
        raise ConnectorAPIError(
            "Slack returned a credential that is not a user token.",
            hint="Disable token rotation or bot scopes on the Slack app, then connect again.",
        )
    team = payload.get("team")
    team = team if isinstance(team, dict) else {}
    scope_text = authed_user.get("scope") if isinstance(authed_user, dict) else ""
    return SlackUserToken(
        access_token=token,
        user_id=str(authed_user.get("id") or ""),
        team_id=str(team.get("id") or ""),
        team_name=team.get("name") if isinstance(team.get("name"), str) else None,
        scopes=[part for part in str(scope_text or "").split(",") if part],
    )


def _payload(response: httpx.Response, what: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError(f"Slack {what} returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError(f"Slack {what} returned an unexpected response shape.")
    return payload


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
