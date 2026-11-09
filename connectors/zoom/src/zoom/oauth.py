"""Zoom OAuth 2.0 authorization-code flow for a user-managed (General) app.

Four provider facts drive this module, each taken from Zoom's published OAuth documentation and
OpenAPI document rather than inferred from the provisioning notes:

* **Access tokens last about an hour.** Every tool call therefore refreshes at most once an hour,
  and a connector that refreshed per call would spend the rotation chain for nothing. Measured
  2026-11-09: `expires_in` is `3599`, not the documented `3600`, so the value is taken from the
  response rather than assumed.
* **Refresh tokens rotate.** Every refresh returns a new `refresh_token`. Zoom's documentation says
  the previous one immediately becomes invalid, but that was **not** what this connector measured:
  replaying the just-spent token was still accepted seconds later. So rotation is treated as
  "the new value must be persisted, and a rejection means reconnect" — and **never** as "a replay
  working proves the grant is healthy". A revoked refresh token is rejected (`invalid_grant`,
  "Invalid Token!").
* **Refresh tokens expire after about 90 days.** That is the ceiling on an unrefreshed account,
  and it is why the error path must say "reconnect" rather than quietly retrying.
* **The token response carries `api_url`**, the cluster the account belongs to. Measured: the
  provisioning account reports `https://api-us.zoom.us`, and both that cluster and the global
  `https://api.zoom.us` answered 200, so the global base is a working default rather than an
  assumption; a region that is not reachable globally is handled with `HOJA_ZOOM_API_BASE`.

Errors are separated on purpose: `invalid_client` is a deployment fault (a rotated secret) that no
user action can fix, while `invalid_grant` means the stored grant is dead and the user must
reconnect. Collapsing them would send an operator looking for the wrong fault.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from zoom.config import ZoomOAuthConfig
from zoom.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError

# From https://developers.zoom.us/docs/integrations/oauth/
AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
TOKEN_URL = "https://zoom.us/oauth/token"
REVOKE_URL = "https://zoom.us/oauth/revoke"

# Exactly the six read-only granular scopes the provisioned app holds, each mapped to the endpoint
# that needs it:
#   user:read:user                             -> GET /users/me
#   meeting:read:list_meetings                 -> GET /users/me/meetings
#   meeting:read:meeting                       -> GET /meetings/{meetingId}
#   cloud_recording:read:list_user_recordings  -> GET /users/me/recordings
#   cloud_recording:read:list_recording_files  -> GET /meetings/{meetingId}/recordings
#   cloud_recording:read:meeting_transcript    -> GET /meetings/{meetingId}/transcript
# `cloud_recording:read:recording` does not exist; the provisioning run recorded that correction.
READ_SCOPES: tuple[str, ...] = (
    "user:read:user",
    "meeting:read:list_meetings",
    "meeting:read:meeting",
    "cloud_recording:read:list_user_recordings",
    "cloud_recording:read:list_recording_files",
    "cloud_recording:read:meeting_transcript",
)

# Zoom's token endpoint rejects the grant with these; `invalid_client` is deliberately absent
# because it describes the deployment, not the grant.
_AUTH_ERRORS = frozenset({"invalid_grant", "invalid_request", "unauthorized_client"})
_DEFAULT_EXPIRES_IN = 3600


class ZoomToken(BaseModel):
    """The part of a token response this connector keeps. Never contains the client secret."""

    access_token: str
    refresh_token: str
    expires_in: int = _DEFAULT_EXPIRES_IN
    scopes: list[str] = Field(default_factory=list)
    token_type: str = "bearer"
    # The cluster the account belongs to (`https://api.zoom.us` for most). Kept so a call can be
    # addressed to the right host instead of assuming the global one.
    api_url: str | None = None


def authorization_url(
    config: ZoomOAuthConfig, *, state: str, redirect_uri: str | None = None
) -> str:
    """The authorize URL.

    `redirect_uri` is passed explicitly because the callback belongs to the tenant that started the
    flow; it must be one of the app's registered URIs, and the same string is repeated at the token
    endpoint or Zoom refuses the exchange.

    No `code_challenge`: the provisioned app does not have PKCE enabled, and a challenge it did not
    expect is rejected at the token endpoint. A confidential client with an HTTP Basic secret is
    the documented shape for this app type.
    """
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri or config.redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    config: ZoomOAuthConfig,
    *,
    code: str,
    redirect_uri: str | None = None,
) -> ZoomToken:
    """Redeem the authorization code. Basic auth carries the confidential client."""
    return await _token_request(
        http,
        config,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or config.redirect_uri,
        },
        what="token request",
    )


async def refresh_access_token(
    http: httpx.AsyncClient, config: ZoomOAuthConfig, *, refresh_token: str
) -> ZoomToken:
    """Rotate the grant.

    The caller MUST persist the returned `refresh_token` before using the access token: Zoom kills
    the value passed in as soon as this call succeeds, so a crash between the two leaves the
    account unrecoverable without a new consent.
    """
    return await _token_request(
        http,
        config,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        what="refresh",
    )


async def revoke_token(http: httpx.AsyncClient, config: ZoomOAuthConfig, *, token: str) -> bool:
    """Revoke the grant at Zoom (RFC 7009).

    Zoom answers `{"status": "success"}` and invalidates the access **and** refresh token
    together, which is the outcome the user asked for. Revoking only the access token would leave
    the grant renewable, so the refresh token is what gets revoked.

    True means the grant is gone, including the case where Zoom reports the token is already
    invalid: the caller's need is identical either way. A rejected *client* is a deployment fault
    and raises instead.
    """
    response = await http.post(
        REVOKE_URL,
        auth=(config.client_id, config.client_secret),
        data={"token": token},
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            "Zoom rate-limited the revocation.",
            hint=f"Wait {_retry_after(response)} seconds and try again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Zoom revocation returned HTTP {response.status_code}.")
    if response.status_code == 200:
        return True
    payload = _payload(response, "revocation", allow_empty=True)
    error = str(payload.get("error") or "")
    if error in {"invalid_client", "unauthorized_client"}:
        raise ConnectorConfigError(
            "Zoom rejected this deployment's client credentials during revocation.",
            hint="The client secret is wrong or was rotated. Update the server's environment.",
        )
    if response.status_code in (400, 401):
        # RFC 7009: an already-invalid token is not an error for the caller.
        return True
    raise ConnectorAPIError(f"Zoom did not revoke the token: {error or 'unknown error'}.")


async def _token_request(
    http: httpx.AsyncClient,
    config: ZoomOAuthConfig,
    data: dict[str, str],
    *,
    what: str,
) -> ZoomToken:
    response = await http.post(
        TOKEN_URL, auth=(config.client_id, config.client_secret), data=data
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            f"Zoom rate-limited the {what}.",
            hint=f"Wait {_retry_after(response)} seconds and start the connection again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Zoom {what} returned HTTP {response.status_code}.")
    payload = _payload(response, what)
    if response.status_code >= 400:
        # Zoom puts the machine-readable token in `error` and the human sentence in `reason`
        # (measured: {"error": "invalid_grant", "reason": "Invalid authorization code"}).
        error = str(payload.get("error") or "unknown_error")
        reason = str(payload.get("reason") or "").strip()
        if error == "invalid_client":
            raise ConnectorConfigError(
                "Zoom rejected this deployment's client credentials.",
                hint="The client secret is wrong or was rotated. Update the server's environment.",
            )
        if error in _AUTH_ERRORS:
            raise ConnectorAuthError(
                f"Zoom rejected the grant: {error}{f' ({reason})' if reason else ''}.",
                hint="Connect Zoom again from Settings > Connections.",
            )
        raise ConnectorAPIError(f"Zoom {what} failed: {error}.")

    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise ConnectorAPIError(
            "Zoom returned no access token.",
            hint="The app must be a General App with user-managed OAuth enabled.",
        )
    if not isinstance(refresh, str) or not refresh:
        # Rotation is on. Without the new refresh token the grant dies in an hour with no way to
        # renew it, so accepting this response would create a silently expiring account.
        raise ConnectorAPIError(
            "Zoom returned no refresh token.",
            hint="Re-authorize the app; user-managed OAuth must issue a refresh token.",
        )
    expires_in = payload.get("expires_in")
    scope_text = payload.get("scope")
    scopes = [part for part in str(scope_text or "").replace(",", " ").split() if part]
    api_url = payload.get("api_url")
    return ZoomToken(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in if isinstance(expires_in, int) and expires_in > 0 else _DEFAULT_EXPIRES_IN,
        scopes=scopes,
        token_type=str(payload.get("token_type") or "bearer"),
        api_url=api_url if isinstance(api_url, str) and api_url.startswith("https://") else None,
    )


def _payload(
    response: httpx.Response, what: str, *, allow_empty: bool = False
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        if allow_empty and not (response.text or "").strip():
            return {}
        raise ConnectorAPIError(f"Zoom {what} returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError(f"Zoom {what} returned an unexpected response shape.")
    return payload


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
