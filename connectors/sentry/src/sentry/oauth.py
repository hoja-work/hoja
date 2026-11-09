"""Sentry public-integration OAuth: the installation callback, and refresh.

Four provider facts shape this module, each taken from Sentry's own documentation (§ cited inline)
and confirmed against the live API on 2026-11-09:

* **A public integration does not use `/oauth/authorize/`.** Installing it redirects the browser to
  the registered Redirect URL with `code`, `installationId`, `orgSlug` and (if supplied) the same
  `state`. There is no PKCE parameter on this flow. Measured: `/oauth/authorize/` refuses the
  registered redirect with "Missing or invalid redirect_uri parameter."
  <https://docs.sentry.io/organization/integrations/integration-platform/public-integration/>
* **The code is redeemed per installation**, not at `/oauth/token/`:
  `POST /api/0/sentry-app-installations/{installationId}/authorizations/` with
  `{"grant_type": "authorization_code", "code", "client_id", "client_secret"}`. The JSON response is
  camelCase — `token`, `refreshToken`, `expiresAt` — not the snake_case of the generic token endpoint.
* **Tokens expire after eight hours**, so refresh is mandatory rather than optional. The
  documented refresh is a **JWT bearer assertion** signed HS256 with the client secret
  (`iss`/`sub` = client id, `iat`, `exp`, `jti`) posted as
  `{"grant_type": "urn:sentry:params:oauth:grant-type:jwt-bearer"}` in the `Authorization` header.
  The refresh-token grant also exists but the docs call it "not recommended", and a refresh token
  lost in transmission is exactly the failure the assertion avoids.
* **A token is scoped to the organization chosen at install time.** `GET /api/0/organizations/`
  returns only that organization, so the org slug is discovered from the token, never accepted from
  a caller. <https://docs.sentry.io/api/auth/#organization-scoping>

`invalid_client` is a deployment problem (wrong or rotated secret) and is not fixable by the user.
An installation that is gone (403/404 on refresh) means the user removed the app in Sentry: the
account must be reconnected. The two are kept apart on purpose.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from sentry.config import SentryOAuthConfig
from sentry.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError

INSTALL_URL_TEMPLATE = "https://sentry.io/sentry-apps/{slug}/external-install/"
AUTHORIZATIONS_URL = "https://sentry.io/api/0/sentry-app-installations/{installation_id}/authorizations/"
INSTALLATIONS_URL = "https://sentry.io/api/0/sentry-app-installations/{installation_id}/"
API_BASE = "https://sentry.io/api/0"

# Exactly the scopes the four read tools need, and the three the provisioned integration holds:
#   project:read -> /organizations/{org}/projects/
#   event:read   -> /organizations/{org}/issues/, /issues/{id}/, /issues/{id}/events/latest/
#   org:read     -> /organizations/ (discover the org the installation is bound to)
# No write scope is requested; an unrequested scope cannot be silently widened into later.
READ_SCOPES: tuple[str, ...] = ("event:read", "org:read", "project:read")

_JWT_BEARER_GRANT = "urn:sentry:params:oauth:grant-type:jwt-bearer"
_DEFAULT_EXPIRES_IN = 8 * 60 * 60
_JWT_TTL_SECONDS = 60


class SentryToken(BaseModel):
    """The part of an installation authorization this connector keeps. Never the client secret."""

    access_token: str
    refresh_token: str
    expires_at: int
    scopes: list[str] = Field(default_factory=list)
    installation_id: str

    @property
    def expires_in(self) -> int:
        return max(0, self.expires_at - int(time.time()))


def installation_url(config: SentryOAuthConfig, *, state: str | None = None) -> str:
    """The fixed external install URL. Sentry echoes `state` back to the redirect URL unchanged.

    There is no client_id in this URL on purpose: an unpublished public integration is visible only
    to the organization that created it, and Sentry resolves the integration from the slug.
    """
    url = INSTALL_URL_TEMPLATE.format(slug=config.integration_slug)
    return f"{url}?{urlencode({'state': state})}" if state else url


async def exchange_installation_code(
    http: httpx.AsyncClient,
    config: SentryOAuthConfig,
    *,
    code: str,
    installation_id: str,
) -> SentryToken:
    """Redeem an installation's grant code. The client secret is in the JSON body: this endpoint
    has no HTTP Basic variant."""
    return await _authorizations_request(
        http,
        config,
        installation_id,
        {"grant_type": "authorization_code", "code": code},
        what="installation exchange",
    )


async def refresh_installation(
    http: httpx.AsyncClient,
    config: SentryOAuthConfig,
    *,
    installation_id: str,
) -> SentryToken:
    """Refresh with the documented JWT-bearer assertion.

    The assertion is signed with the client secret and lives one minute. Nothing about the previous
    token is required, which is what makes this the recovery path when a refresh response was lost
    in transmission — the documented reason this method exists at all.
    """
    assertion = _jwt_assertion(config)
    return await _authorizations_request(
        http,
        config,
        installation_id,
        {"grant_type": _JWT_BEARER_GRANT},
        what="token refresh",
        headers={"Authorization": f"Bearer {assertion}"},
    )


async def refresh_with_token(
    http: httpx.AsyncClient,
    config: SentryOAuthConfig,
    *,
    installation_id: str,
    refresh_token: str,
) -> SentryToken:
    """Refresh with the stored refresh token. Used only as the fallback when the assertion is
    refused; the caller persists the rotated token before using the access token."""
    return await _authorizations_request(
        http,
        config,
        installation_id,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        what="refresh-token grant",
    )


async def uninstall(
    http: httpx.AsyncClient, config: SentryOAuthConfig, *, installation_id: str
) -> bool:
    """Remove the installation. True means Sentry confirms it is gone (or already gone).

    Measured 2026-11-09: `DELETE` answers 403 while the installation sits in `pending_deletion`
    after an in-Sentry uninstall; it clears itself. That is reported as *not yet* revoked rather
    than as an error, because the grant is unusable either way and retrying later succeeds.
    """
    response = await http.delete(
        INSTALLATIONS_URL.format(installation_id=_checked_installation_id(installation_id)),
        headers={"Authorization": f"Bearer {_jwt_assertion(config)}"},
    )
    if response.status_code in (200, 202, 204, 404):
        return True
    if response.status_code in (401, 403):
        return False
    if response.status_code >= 500:
        raise ConnectorAPIError(
            f"Sentry uninstall returned HTTP {response.status_code}.",
            hint="Try disconnecting again; the local grant is discarded either way.",
        )
    raise ConnectorAPIError(
        f"Sentry did not confirm the uninstall (HTTP {response.status_code}).",
        hint="Remove Hoja in Sentry's organization settings to revoke the grant there.",
    )


async def _authorizations_request(
    http: httpx.AsyncClient,
    config: SentryOAuthConfig,
    installation_id: str,
    payload: dict[str, str],
    *,
    what: str,
    headers: dict[str, str] | None = None,
) -> SentryToken:
    body = {"client_id": config.client_id, "client_secret": config.client_secret, **payload}
    response = await http.post(
        AUTHORIZATIONS_URL.format(
            installation_id=_checked_installation_id(installation_id)
        ),
        json=body,
        headers=headers or {},
    )
    if response.status_code == 429:
        raise ConnectorAPIError(
            f"Sentry rate-limited the {what}.",
            hint=f"Wait {_retry_after(response)} seconds and try again.",
        )
    if response.status_code >= 500:
        raise ConnectorAPIError(f"Sentry {what} returned HTTP {response.status_code}.")
    data = _payload(response, what)
    if response.status_code >= 400:
        detail = str(data.get("detail") or data.get("error") or "").strip()
        if response.status_code == 401:
            # The client pair itself was refused: a deployment fault, not a user one.
            raise ConnectorConfigError(
                "Sentry rejected this deployment's integration credentials.",
                hint=(
                    "The client secret is wrong or was rotated. Update the server's environment "
                    "and reconnect the organization."
                ),
            )
        if response.status_code in (403, 404):
            raise ConnectorAuthError(
                "Sentry no longer has this installation.",
                hint="Connect Sentry again in Settings > Connections.",
            )
        raise ConnectorAuthError(
            f"Sentry rejected the {what}{f': {detail}' if detail else ''}.",
            hint=(
                "The installation code is single-use and expires quickly. Connect Sentry again "
                "from Settings > Connections."
            ),
        )
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ConnectorAPIError(
            "Sentry returned no access token.",
            hint="The integration must be a Public Integration with OAuth enabled.",
        )
    refresh = data.get("refreshToken")
    if not isinstance(refresh, str) or not refresh:
        raise ConnectorAPIError(
            "Sentry returned no refresh token.",
            hint="Without it the grant dies after eight hours with no way to renew it.",
        )
    return SentryToken(
        access_token=token,
        refresh_token=refresh,
        expires_at=_expires_at(data.get("expiresAt")),
        scopes=_scopes(data.get("scopes")),
        installation_id=_checked_installation_id(installation_id),
    )


def _expires_at(value: Any) -> int:
    """`expiresAt` is an ISO-8601 instant. A token with no readable expiry is treated as expiring
    now, so the next call refreshes instead of using a token of unknown age."""
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return int(time.time())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    return int(time.time()) + _DEFAULT_EXPIRES_IN


def _scopes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item]
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    return []


def _jwt_assertion(config: SentryOAuthConfig) -> str:
    """HS256 JWT for the jwt-bearer grant, built with hmac rather than a new dependency.

    Claims are exactly the documented set: `iss` and `sub` are the client id, `iat`/`exp` bound it
    to one minute, `jti` keeps two assertions from being identical.
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": config.client_id,
        "sub": config.client_id,
        "iat": now,
        "exp": now + _JWT_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
    }
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')))}.{_b64(json.dumps(claims, separators=(',', ':')))}"
    signature = hmac.new(
        config.client_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64_bytes(signature)}"


def _b64(value: str) -> str:
    return _b64_bytes(value.encode("utf-8"))


def _b64_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _checked_installation_id(value: str) -> str:
    """The installation id becomes a URL path segment, so anything but a canonical UUID is refused
    before it reaches Sentry."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Invalid Sentry installation id.") from exc


def _payload(response: httpx.Response, what: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError(f"Sentry {what} returned a body that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAPIError(f"Sentry {what} returned an unexpected response shape.")
    return payload


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
