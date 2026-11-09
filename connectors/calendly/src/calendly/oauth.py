"""Calendly OAuth 2.0 authorization-code flow, PKCE mandatory, confidential client.

Three provider-specific facts drive this module, each measured against the live provider on
2026-11-09 rather than inferred:

* **PKCE is mandatory at the token endpoint.** `POST /oauth/token` without `code_verifier`
  answers `400 invalid_request "Missing required parameter: code_verifier."` before it looks at
  the client credentials, and with a verifier it next demands `redirect_uri`. The developer
  console only *recommends* S256; the token endpoint enforces it.
* **Refresh tokens are single-use and rotate.** Every successful refresh returns a new
  `refresh_token` and invalidates the previous one, so a lost or replayed refresh token is an
  `invalid_grant` and a re-consent. Callers must persist the new value before using the token.
* **Access tokens last 7200 s.** A connector that refreshes on every call will burn the
  rotation chain: two concurrent refreshes leave one caller holding a dead token.

Errors are separated on purpose. `invalid_client` is a deployment problem (wrong or rotated
secret) and is not fixable by the user, while `invalid_grant` means the stored grant is dead and
the user must reconnect; collapsing them would tell an operator to reconnect an account when the
real fault is a secret that was rotated out from under the deployment.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from calendly.config import CalendlyOAuthConfig
from calendly.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError

# From https://calendly.com/.well-known/oauth-authorization-server (RFC 8414).
AUTHORIZE_URL = "https://calendly.com/oauth/authorize"
TOKEN_URL = "https://calendly.com/oauth/token"
REVOKE_URL = "https://calendly.com/oauth/revoke"
API_BASE = "https://api.calendly.com"

# Exactly the scopes the five read tools need, all read-only:
#   users:read            -> /users/me (the authorizing user and its organization)
#   event_types:read      -> /event_types
#   scheduled_events:read -> /scheduled_events, /scheduled_events/{uuid},
#                            /scheduled_events/{uuid}/invitees
# `availability:read` is held by the provisioned app but no tool in this connector reads
# availability, so it is deliberately NOT requested here: least privilege is about what is asked
# for, and an unrequested scope cannot be silently widened into later.
READ_SCOPES: tuple[str, ...] = ("users:read", "event_types:read", "scheduled_events:read")

_AUTH_ERRORS = frozenset({"invalid_grant", "invalid_request", "unauthorized_client"})
_PKCE_VERIFIER_BYTES = 32  # 43 base64url characters, inside RFC 7636's 43..128 range.


class CalendlyToken(BaseModel):
    """The part of a token response this connector keeps. Never contains the client secret."""

    access_token: str
    refresh_token: str
    expires_in: int = 7200
    scopes: list[str] = Field(default_factory=list)
    token_type: str = "Bearer"
    owner: str | None = None
    organization: str | None = None


def pkce_pair() -> tuple[str, str]:
    """A fresh (verifier, S256 challenge). The verifier is never logged and lives only in the
    sealed OAuth state until the code is exchanged."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(_PKCE_VERIFIER_BYTES)).decode().rstrip("=")
    return verifier, s256(verifier)


def s256(verifier: str) -> str:
    """The S256 challenge for a verifier.

    The challenge is recomputed from the verifier wherever it is needed rather than carried
    separately, so the two halves can never disagree in the sealed state.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorization_url(
    config: CalendlyOAuthConfig, *, state: str, code_challenge: str, scope: str | None = None
) -> str:
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "scope": scope or " ".join(READ_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    config: CalendlyOAuthConfig,
    *,
    code: str,
    code_verifier: str,
) -> CalendlyToken:
    """Exchange the authorization code. The client secret goes in HTTP Basic (the app is a
    confidential Web client) and the redirect URI repeats the authorize value exactly."""
    return await _token_request(
        http,
        config,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "code_verifier": code_verifier,
        },
        what="token request",
    )


async def refresh_access_token(
    http: httpx.AsyncClient, config: CalendlyOAuthConfig, *, refresh_token: str
) -> CalendlyToken:
    """Rotate the grant. The caller MUST persist the returned refresh_token before using the
    access token: the value passed in is dead the moment this call succeeds."""
    return await _token_request(
        http,
        config,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        what="refresh",
    )


async def revoke_token(
    http: httpx.AsyncClient, config: CalendlyOAuthConfig, *, token: str, hint: str = "refresh_token"
) -> bool:
    """Revoke the grant at Calendly (RFC 7009). True means the grant is gone, including the case
    where Calendly reports the token was already invalid — the outcome the caller needs is the
    same either way. A rejected *client* is a deployment fault and raises instead."""
    response = await http.post(
        REVOKE_URL,
        auth=(config.client_id, config.client_secret),
        data={"token": token, "token_type_hint": hint},
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            "Calendly rate-limited the revocation.",
            hint=f"Wait {_retry_after(response)} seconds and try again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Calendly revocation returned HTTP {response.status_code}.")
    if response.status_code == 200:
        return True
    payload = _payload(response, "revocation")
    error = str(payload.get("error") or "")
    # Measured live: the revoke endpoint answers `403 unauthorized_client` when the client cannot
    # authenticate, while the token endpoint uses `401 invalid_client` for the same fault.
    if error in {"invalid_client", "unauthorized_client"}:
        raise ConnectorConfigError(
            "Calendly rejected this deployment's client credentials during revocation.",
            hint="The client secret is wrong or was rotated. Update the server's environment.",
        )
    if response.status_code in (400, 401) and error in {"invalid_request", "invalid_grant", ""}:
        # RFC 7009: an already-invalid token is not an error for the caller.
        return True
    raise ConnectorAPIError(f"Calendly did not revoke the token: {error or 'unknown error'}.")


async def _token_request(
    http: httpx.AsyncClient,
    config: CalendlyOAuthConfig,
    data: dict[str, str],
    *,
    what: str,
) -> CalendlyToken:
    response = await http.post(
        TOKEN_URL, auth=(config.client_id, config.client_secret), data=data
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            f"Calendly rate-limited the {what}.",
            hint=f"Wait {_retry_after(response)} seconds and start the connection again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Calendly {what} returned HTTP {response.status_code}.")
    payload = _payload(response, what)
    if response.status_code >= 400:
        error = str(payload.get("error") or "unknown_error")
        description = str(payload.get("error_description") or "").strip()
        if error == "invalid_client":
            raise ConnectorConfigError(
                "Calendly rejected this deployment's client credentials.",
                hint="The client secret is wrong or was rotated. Update the server's environment.",
            )
        if error in _AUTH_ERRORS:
            raise ConnectorAuthError(
                f"Calendly rejected the grant: {error}{f' ({description})' if description else ''}.",
                hint="Connect Calendly again from Settings > Connections.",
            )
        raise ConnectorAPIError(f"Calendly {what} failed: {error}.")

    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise ConnectorAPIError(
            "Calendly returned no access token.",
            hint="The app must be a Web client with the authorization-code grant enabled.",
        )
    if not isinstance(refresh, str) or not refresh:
        # Rotation is on. Without the new refresh token the grant dies in two hours with no way
        # to renew it, so accepting this response would create a silently expiring account.
        raise ConnectorAPIError(
            "Calendly returned no refresh token.",
            hint="Check that the Calendly app has the refresh_token grant enabled.",
        )
    expires_in = payload.get("expires_in")
    scope_text = payload.get("scope")
    scopes = [part for part in str(scope_text or "").replace(",", " ").split() if part]
    return CalendlyToken(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in if isinstance(expires_in, int) and expires_in > 0 else 7200,
        scopes=scopes,
        token_type=str(payload.get("token_type") or "Bearer"),
        owner=payload.get("owner") if isinstance(payload.get("owner"), str) else None,
        organization=(
            payload.get("organization") if isinstance(payload.get("organization"), str) else None
        ),
    )


def _payload(response: httpx.Response, what: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError(f"Calendly {what} returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError(f"Calendly {what} returned an unexpected response shape.")
    return payload


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
