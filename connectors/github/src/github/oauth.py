"""GitHub App user-to-server OAuth: authorization, code exchange, refresh, revocation.

Five provider facts drive this module. Each comes from GitHub's own documentation and was checked
against the provisioned app (see the README's "What was verified live" section):

* **The user token is the intersection of user and app access.** GitHub: "a user access token can
  only access resources that both the user and app can access". Repository permissions reach only
  repositories owned by the account where the app is installed. A private repository is therefore
  readable only after the app is installed *and* the person can already see it; the connector
  surfaces an install link instead of pretending the authorization alone is enough.
* **User tokens expire.** With expiring tokens enabled the token response carries `expires_in`
  (measured: 28800, i.e. 8 h) and a `refresh_token` (6 months). Hoja stores both and refreshes
  before expiry.
* **Refresh rotates.** Each refresh returns a new refresh token. The caller MUST persist the new
  pair before using the access token; a rejection is `bad_refresh_token` and means reconnect.
* **Failures are HTTP 200 with an `error` body**, not an OAuth error status, when the request is
  accepted but the code, secret or redirect is wrong. They are checked from the payload, and
  `Accept: application/json` is sent so the body is JSON rather than form-encoded.
* **Revocation is an application endpoint, not a token endpoint.** `DELETE
  /applications/{client_id}/grant` with HTTP Basic (`client_id:client_secret`) and
  `{"access_token": ...}` ends the token *and* its refresh chain. There is no `/login/oauth/revoke`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from github.config import GitHubAppConfig
from github.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError

# From https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"

# The permissions the app registration carries, all read. Not sent at authorize time: a GitHub App
# does not take scopes, and sending one is ignored rather than granted.
READ_PERMISSIONS: tuple[str, ...] = ("metadata", "contents", "issues", "pull_requests")

# Measured on the provisioned app: 28800 s. GitHub's documented default for expiring user tokens.
DEFAULT_EXPIRES_IN = 28_800

# The grant is dead and only a new consent fixes it.
_AUTH_ERRORS = frozenset({"bad_verification_code", "bad_refresh_token", "expired_token"})
# The deployment is wrong; no user action can fix it.
_CONFIG_ERRORS = frozenset(
    {"incorrect_client_credentials", "redirect_uri_mismatch", "unsupported_grant_type"}
)


class GitHubToken(BaseModel):
    """The part of a token response this connector keeps. Never contains the client secret."""

    access_token: str
    refresh_token: str
    expires_in: int = DEFAULT_EXPIRES_IN
    refresh_token_expires_in: int | None = None
    token_type: str = "bearer"
    # GitHub Apps carry permissions on the registration, so this is normally the empty string.
    scope: str = ""


def authorization_url(
    config: GitHubAppConfig, *, state: str, redirect_uri: str | None = None
) -> str:
    """The authorize URL for one tenant callback.

    No `scope`: the app's registered permissions are the whole authorization surface. No
    `code_challenge`: GitHub Apps do not support PKCE on the user-to-server flow, so a challenge
    would be ignored at best and refuse the exchange at worst.

    `allow_signup=false` keeps the consent screen from offering account creation to someone who is
    not signed in; the connection belongs to the identity Hoja already knows.
    """
    params = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri or config.redirect_uri,
        "state": state,
        "allow_signup": "false",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    config: GitHubAppConfig,
    *,
    code: str,
    redirect_uri: str | None = None,
) -> GitHubToken:
    """Redeem the authorization code. Basic auth is NOT used here: GitHub expects the client
    secret in the body for `/login/oauth/access_token`."""
    return await _token_request(
        http,
        config,
        {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "code": code,
            "redirect_uri": redirect_uri or config.redirect_uri,
        },
        what="token request",
    )


async def refresh_access_token(
    http: httpx.AsyncClient, config: GitHubAppConfig, *, refresh_token: str
) -> GitHubToken:
    """Rotate the grant.

    The caller MUST persist the returned `refresh_token` before using the access token: the value
    passed in is single-use on the documented flow, so a crash between the two leaves the account
    recoverable only by a new consent.
    """
    return await _token_request(
        http,
        config,
        {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        what="refresh",
    )


async def revoke_grant(
    http: httpx.AsyncClient, config: GitHubAppConfig, *, access_token: str
) -> bool:
    """Revoke the user grant at GitHub (`DELETE /applications/{client_id}/grant`).

    GitHub ends the access token **and** every refresh token issued with it, which is the outcome
    the user asked for. Revoking only the access token would leave the grant renewable within the
    six-month refresh window.

    True means the grant is gone, including when GitHub answers 404: it reports an already-deleted
    grant that way, and the caller's need is identical either way. Rejected client credentials are
    a deployment fault and raise instead.
    """
    url = f"{config.api_base}/applications/{config.client_id}/grant"
    response = await http.request(
        "DELETE",
        url,
        auth=(config.client_id, config.client_secret),
        json={"access_token": access_token},
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    if response.status_code in (204, 200, 404):
        return True
    if response.status_code in (401, 403):
        raise ConnectorConfigError(
            "GitHub rejected this deployment's client credentials during revocation.",
            hint="The client secret is wrong or was rotated. Update the server's environment.",
        )
    if response.status_code == 429:
        raise ConnectorAPIError(
            "GitHub rate-limited the revocation.",
            hint=f"Wait {_retry_after(response)} seconds and try again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"GitHub revocation returned HTTP {response.status_code}.")
    payload = _payload(response, "revocation", allow_empty=True)
    raise ConnectorAPIError(
        f"GitHub did not revoke the token: {payload.get('message') or response.status_code}."
    )


async def _token_request(
    http: httpx.AsyncClient,
    config: GitHubAppConfig,
    data: dict[str, str],
    *,
    what: str,
) -> GitHubToken:
    response = await http.post(
        TOKEN_URL,
        data=data,
        # Without this GitHub answers form-encoded, which the JSON path below would reject as a
        # body-shape error instead of the real OAuth error.
        headers={"Accept": "application/json"},
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            f"GitHub rate-limited the {what}.",
            hint=f"Wait {_retry_after(response)} seconds and start the connection again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"GitHub {what} returned HTTP {response.status_code}.")
    payload = _payload(response, what)
    error = payload.get("error")
    if error:
        # Measured 2026-11-09: an unknown client is answered 404 {"error":"Not Found"} before any
        # client validation, so the taxonomy below only applies to a real client id.
        _raise_token_error(str(error), str(payload.get("error_description") or ""), what)
    if response.status_code >= 400:
        raise ConnectorAPIError(f"GitHub {what} returned HTTP {response.status_code}.")

    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise ConnectorAPIError(
            "GitHub returned no access token.",
            hint="The app must be a GitHub App with user authorization; an OAuth App returns one too.",
        )
    if not isinstance(refresh, str) or not refresh:
        # Expiring tokens are on, so a response without a refresh token describes a grant that
        # dies in eight hours with no way to renew it. Accepting it would create a silently
        # expiring account.
        raise ConnectorAPIError(
            "GitHub returned no refresh token.",
            hint=(
                "Enable 'Expire user authorization tokens' on the app and re-authorize; "
                "without a refresh token the connection stops working after eight hours."
            ),
        )
    expires_in = payload.get("expires_in")
    refresh_expires = payload.get("refresh_token_expires_in")
    return GitHubToken(
        access_token=access,
        refresh_token=refresh,
        expires_in=(
            expires_in if isinstance(expires_in, int) and expires_in > 0 else DEFAULT_EXPIRES_IN
        ),
        refresh_token_expires_in=(
            refresh_expires if isinstance(refresh_expires, int) and refresh_expires > 0 else None
        ),
        token_type=str(payload.get("token_type") or "bearer"),
        scope=str(payload.get("scope") or ""),
    )


def _raise_token_error(error: str, description: str, what: str) -> None:
    detail = f"{error}{f': {description}' if description else ''}"
    if error in _CONFIG_ERRORS:
        raise ConnectorConfigError(
            f"GitHub rejected this deployment's client settings ({detail}).",
            hint=(
                "Compare the client id, client secret and registered callback URLs with the app's "
                "settings page."
            ),
        )
    if error in _AUTH_ERRORS:
        raise ConnectorAuthError(
            f"GitHub rejected the grant ({detail}).",
            hint="Connect GitHub again from Settings > Connections.",
        )
    raise ConnectorAPIError(f"GitHub {what} failed: {detail}.")


def _payload(
    response: httpx.Response, what: str, *, allow_empty: bool = False
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        if allow_empty and not (response.text or "").strip():
            return {}
        raise ConnectorAPIError(f"GitHub {what} returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError(f"GitHub {what} returned an unexpected response shape.")
    return payload


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
