"""Connector routes: connect, callback, list, and revoke connected accounts.

Pending OAuth state (user, org, scope, PKCE verifier, expiry) is carried in an AES-GCM sealed
`state` value, so the callback cannot be forged. The seal is bound to the organization (HKDF key
plus AAD, `secrets_box`), so a state minted for one workspace cannot be opened for another, and a
hashed nonce in `connector_oauth_state` -- an organization-scoped, RLS-policed table -- makes each
state single-use within its workspace.

The redirect URI is per request, not per deployment: one shared app serves every organization, so
`connector_hosts` derives it from the host the request arrived on after resolving that host to a
real workspace. A host with no registered callback reports "not available on this workspace host"
instead of failing at the provider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from calendly import CalendlyOAuthConfig
from calendly import ConnectorConfigError as CalendlyConfigError
from calendly.client import CalendlyClient
from calendly.errors import ConnectorAPIError as CalendlyAPIError
from calendly.errors import ConnectorAuthError as CalendlyAuthError
from calendly.oauth import READ_SCOPES as CALENDLY_READ_SCOPES
from calendly.oauth import authorization_url as calendly_authorization_url
from calendly.oauth import exchange_code as calendly_exchange_code
from calendly.oauth import pkce_pair as calendly_pkce_pair
from calendly.oauth import revoke_token as calendly_revoke_token
from calendly.oauth import s256 as calendly_s256
from github import ConnectorConfigError as GitHubConfigError
from github import GitHubAppConfig
from github.client import GitHubClient
from github.errors import ConnectorAPIError as GitHubAPIError
from github.errors import ConnectorAuthError as GitHubAuthError
from github.oauth import authorization_url as github_authorization_url
from github.oauth import exchange_code as github_exchange_code
from github.oauth import revoke_grant as github_revoke_grant
from google_workspace import ConnectorAuthError, ConnectorConfigError, GoogleOAuthConfig
from google_workspace.oauth import (
    ADDON_LABELS,
    ADDON_SCOPES,
    READ_SCOPES,
    authorization_url,
    capabilities_for,
    exchange_code,
    merge_scopes,
    pkce_pair,
)
from google_workspace.principal import sign_principal
from grafana import ConnectorConfigError as GrafanaConfigError
from grafana import GrafanaConfig, UnsafeInstanceError
from grafana.client import GrafanaClient
from grafana.errors import ConnectorAPIError as GrafanaAPIError
from grafana.errors import ConnectorAuthError as GrafanaAuthError
from grafana.ssrf import validate_instance_url
from notion import ConnectorConfigError as NotionConfigError
from notion import NotionOAuthConfig
from notion.errors import ConnectorAPIError as NotionAPIError
from notion.errors import ConnectorAuthError as NotionAuthError
from notion.oauth import authorization_url as notion_authorization_url
from notion.oauth import exchange_code as notion_exchange_code
from sentry import ConnectorConfigError as SentryConfigError
from sentry import SentryOAuthConfig
from sentry.client import SentryClient
from sentry.errors import ConnectorAPIError as SentryAPIError
from sentry.errors import ConnectorAuthError as SentryAuthError
from sentry.oauth import READ_SCOPES as SENTRY_READ_SCOPES
from sentry.oauth import exchange_installation_code as sentry_exchange_code
from sentry.oauth import installation_url as sentry_installation_url
from sentry.oauth import uninstall as sentry_uninstall
from slack import ConnectorConfigError as SlackConfigError
from slack import SlackOAuthConfig
from slack.client import revoke as slack_revoke
from slack.errors import ConnectorAPIError as SlackAPIError
from slack.errors import ConnectorAuthError as SlackAuthError
from slack.oauth import USER_READ_SCOPES
from slack.oauth import authorization_url as slack_authorization_url
from slack.oauth import exchange_code as slack_exchange_code
from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route
from zoom import ConnectorConfigError as ZoomConfigError
from zoom import ZoomOAuthConfig
from zoom.client import ZoomClient
from zoom.errors import ConnectorAPIError as ZoomAPIError
from zoom.errors import ConnectorAuthError as ZoomAuthError
from zoom.oauth import READ_SCOPES as ZOOM_READ_SCOPES
from zoom.oauth import authorization_url as zoom_authorization_url
from zoom.oauth import exchange_code as zoom_exchange_code
from zoom.oauth import revoke_token as zoom_revoke_token

from hoja_server import connector_hosts
from hoja_server.connector_actions import Executor, approve_action, reject_action
from hoja_server.db import connector_account, connector_action, connector_oauth_state
from hoja_server.github_webhook import github_webhook_routes
from hoja_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret

PROVIDER = "google_workspace"
NOTION_PROVIDER = "notion"
SLACK_PROVIDER = "slack"
CALENDLY_PROVIDER = "calendly"
SENTRY_PROVIDER = "sentry"
ZOOM_PROVIDER = "zoom"
GITHUB_PROVIDER = "github"
GRAFANA_PROVIDER = "grafana"
# A Grafana service-account token is `glsa_` + 41 URL-safe characters. Accept a slightly wider
# charset and length so a self-managed instance with a legacy token still works, but never allow
# whitespace or control characters into an Authorization header.
GRAFANA_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_\-.]{16,512}$")
STATE_TTL_SECONDS = 600
# Calendly accepts exactly ONE redirect URI per app, so a multi-tenant deployment cannot register
# a callback per tenant. It registers this path on a dedicated broker host instead; the broker
# forwards to the tenant, which redeems the code in the session that started the flow.
CALENDLY_REDIRECT_PATH = "/api/connectors/calendly/callback"
CALENDLY_FINISH_PATH = "/api/connectors/calendly/finish"
# Sentry's public-integration form also accepts exactly one Redirect URL, so the same broker
# split applies. The difference is the callback payload: Sentry adds `installationId` and
# `orgSlug`, and the code is redeemed per installation rather than at /oauth/token/.
SENTRY_REDIRECT_PATH = "/api/connectors/sentry/callback"
SENTRY_FINISH_PATH = "/api/connectors/sentry/finish"
SENTRY_WEBHOOK_PATH = "/api/connectors/sentry/webhook"
# Zoom's General App accepts an **allow list** of redirect URIs, so no broker is needed: every
# tenant returns to its own registered callback. The path is fixed and the origin is checked
# against the configured list before the browser is ever sent to Zoom.
ZOOM_REDIRECT_PATH = "/api/connectors/zoom/callback"
# A GitHub App also holds an allow list of up to ten callback URLs, so GitHub needs no broker
# either: every tenant returns to its own registered callback and the origin is checked against the
# configured list before the browser is sent to GitHub.
GITHUB_REDIRECT_PATH = "/api/connectors/github/callback"
BROKER_URL_ENV = "HOJA_CONNECTOR_BROKER_URL"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _consume_state(engine: Engine, org: str, nonce: str, expires_at: int) -> bool:
    """Mark an OAuth state nonce as used in ``org``. False if it was already used.

    The primary key is the nonce hash, and the row carries the organization, so
    two workspaces holding the same nonce cannot collide, and row-level security
    keeps one workspace from even seeing the other's rows.
    """
    if not nonce or not org:
        return False
    digest = hashlib.sha256(nonce.encode()).hexdigest()
    try:
        with engine.begin() as conn:
            conn.execute(
                connector_oauth_state.delete().where(connector_oauth_state.c.expires_at < time.time())
            )
            conn.execute(
                connector_oauth_state.insert().values(
                    nonce_hash=digest,
                    organization_id=org,
                    expires_at=float(expires_at),
                )
            )
    except IntegrityError:
        return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(message: str, status: int, hint: str | None = None) -> JSONResponse:
    body: dict = {"error": message}
    if hint:
        body["hint"] = hint
    return JSONResponse(body, status_code=status)


def _serialize(row) -> dict:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "externalAccount": row["external_account"],
        "scopes": row["scopes"].split(),
        # A Google grant covers one capability per scope, and a user may untick individual scopes
        # on the consent screen, so the Settings page needs the per-API answer rather than "the
        # account exists". Non-Google providers report nothing here.
        "capabilities": capabilities_for(row["scopes"]) if row["provider"] == PROVIDER else {},
        # The customer-supplied origin for instance-addressed connectors (Grafana). It is not a
        # secret: the UI needs it to show which instance an account belongs to.
        "externalUrl": row["external_url"],
        "scope": "org" if row["owner_user_id"] is None else "user",
        "ownerUserId": row["owner_user_id"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


CONNECTOR_PROVIDERS: tuple[tuple[str, str], ...] = (
    (PROVIDER, "HOJA_GOOGLE_WORKSPACE_MCP_URL"),
    (GITHUB_PROVIDER, "HOJA_GITHUB_MCP_URL"),
    (SLACK_PROVIDER, "HOJA_SLACK_MCP_URL"),
    (NOTION_PROVIDER, "HOJA_NOTION_MCP_URL"),
    (SENTRY_PROVIDER, "HOJA_SENTRY_MCP_URL"),
    (GRAFANA_PROVIDER, "HOJA_GRAFANA_MCP_URL"),
    (CALENDLY_PROVIDER, "HOJA_CALENDLY_MCP_URL"),
    (ZOOM_PROVIDER, "HOJA_ZOOM_MCP_URL"),
)


def connector_bindings_for(store_or_engine, org_id: str, user_id: str) -> list:
    """Return MCP bindings for ALL active connectors configured in this workspace.

    Checks each supported connector's dedicated MCP URL environment variable
    (e.g. ``HOJA_GITHUB_MCP_URL``, ``HOJA_SLACK_MCP_URL``) as well as the unified
    ``HOJA_CONNECTOR_MCP_URL`` service fallback, and emits a ``ConnectorBinding``
    only when the org or user has an active account for that provider.

    Accepts either a ``Store`` instance (using ``store.engine``) or a SQLAlchemy ``Engine``.
    """
    key = os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
    if not key:
        return []
    shared_url = os.environ.get("HOJA_CONNECTOR_MCP_URL", "").strip()
    engine = store_or_engine.engine if hasattr(store_or_engine, "engine") else store_or_engine
    all_providers = tuple(provider for provider, _env in CONNECTOR_PROVIDERS)
    with engine.connect() as conn:
        active_providers = {
            row[0]
            for row in conn.execute(
                select(connector_account.c.provider)
                .where(
                    and_(
                        connector_account.c.organization_id == org_id,
                        connector_account.c.provider.in_(all_providers),
                        connector_account.c.status == "active",
                        or_(
                            connector_account.c.owner_user_id == user_id,
                            connector_account.c.owner_user_id.is_(None),
                        ),
                    )
                )
                .distinct()
            ).all()
        }
    if not active_providers:
        return []

    from hoja_server.agent_chat import ConnectorBinding

    principal = sign_principal(user_id, org_id, "read", key=key)
    bindings: list[ConnectorBinding] = []
    seen_urls: set[str] = set()
    shared_providers: list[str] = []

    for provider, env_var in CONNECTOR_PROVIDERS:
        if provider not in active_providers:
            continue
        dedicated_url = os.environ.get(env_var, "").strip()
        if dedicated_url:
            if dedicated_url not in seen_urls:
                seen_urls.add(dedicated_url)
                bindings.append(
                    ConnectorBinding(
                        url=dedicated_url,
                        principal=principal,
                        provider=provider,
                        toolset_id=provider.replace("_", "-"),
                    )
                )
        elif shared_url:
            shared_providers.append(provider)

    if shared_providers and shared_url and shared_url not in seen_urls:
        # One unified MCP server serves all providers without a dedicated URL;
        # mount it once to avoid duplicate tool registration.
        first_provider = PROVIDER if PROVIDER in shared_providers else shared_providers[0]
        bindings.append(
            ConnectorBinding(
                url=shared_url,
                principal=principal,
                provider=first_provider,
                toolset_id="google-workspace",
            )
        )

    return bindings


def connector_binding_for(engine: Engine, org: str, uid: str):
    """Return the primary MCP binding for this user when they have an active connector account.

    Backward-compatible single-binding wrapper over ``connector_bindings_for``.
    """
    bindings = connector_bindings_for(engine, org, uid)
    return bindings[0] if bindings else None


def make_write_executor(engine: Engine, http: httpx.AsyncClient, config: GoogleOAuthConfig):
    """Build the approval executor. It runs an approved write only with the account the action was
    queued against, and only while that account is still active and owned by the same requester."""

    async def execute(tool: str, args: dict[str, Any], account_id: str, requester: str) -> str:
        from google_workspace.oauth import refresh_access_token
        from google_workspace.tools import AccountGrant
        from google_workspace.writes import run_write

        with engine.connect() as conn:
            row = (
                conn.execute(
                    select(connector_account).where(
                        and_(
                            connector_account.c.id == account_id,
                            connector_account.c.status == "active",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["owner_user_id"] != requester:
            raise ConnectorAuthError("The account for this action is no longer available.")
        refresh = decrypt_secret(row["encrypted_refresh_token"], row["organization_id"])

        async def access_token() -> str:
            tokens = await refresh_access_token(http, config, refresh_token=refresh)
            return tokens.access_token

        grant = AccountGrant(
            account_id=row["id"],
            external_account=row["external_account"],
            access_token=access_token,
        )
        return await run_write(http, grant, tool, args)

    return execute


def connector_routes(
    engine: Engine, store, auth, execute_write: Executor | None = None
) -> list[Route]:
    """Build connector routes. `auth(request)` returns (user_id, org_id, identity_id, is_browser)."""

    async def _require_user(request: Request):
        uid, org, _identity, _browser = auth(request)
        if not uid:
            return None, None, _error("Authentication required", 401)
        return uid, org, None

    def _unavailable(provider: str, request: Request) -> JSONResponse:
        """Settings shows this instead of a consent screen that would fail at the provider.

        Which hosts have a registered callback is a provider-side fact, so an
        unregistered host is a configuration gap, not a user error: it is answered
        here, before the browser is sent anywhere.
        """
        return _error(
            connector_hosts.UNAVAILABLE,
            409,
            hint=connector_hosts.unavailable_detail(
                provider, connector_hosts.request_host(request), engine
            ),
        )

    def _redirect_for(provider: str, request: Request) -> str | None:
        """This request's registered callback URI for ``provider`` (see ``connector_hosts``)."""
        return connector_hosts.callback_url(engine, request, provider)

    def _is_admin(org: str, uid: str) -> bool:
        actor = store.get_user(org, uid)
        return bool(actor and actor.get("admin"))

    async def google_start(request: Request):
        """Begin the Google OAuth flow.

        With `?addon=meet` or `?addon=chat` this is an *upgrade* of the Google grant the user
        already has, not a second account: the request carries only the add-on's scopes, and
        `include_granted_scopes` makes Google extend the existing grant. That is what keeps one
        Google account to one row, one token and one Disconnect.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        scope = request.query_params.get("scope", "user")
        if scope not in {"user", "org"}:
            return _error("scope must be 'user' or 'org'", 400)
        if scope == "org" and not _is_admin(org, uid):
            return _error("Only workspace admins can connect organization accounts.", 403)
        addon = request.query_params.get("addon", "").strip()
        if addon and addon not in ADDON_SCOPES:
            return _error("Unknown Google scope upgrade.", 400)
        addon_label = ADDON_LABELS[addon] if addon else ""
        if addon:
            # An upgrade extends a grant that exists. Asking for Meet alone on a fresh account
            # would produce a Google account whose grant covers Meet and nothing else, which is a
            # worse outcome than telling the user to connect first.
            with engine.connect() as conn:
                existing = conn.execute(
                    select(connector_account.c.id)
                    .where(
                        and_(
                            connector_account.c.organization_id == org,
                            connector_account.c.provider == PROVIDER,
                            connector_account.c.status == "active",
                            connector_account.c.owner_user_id.is_(None)
                            if scope == "org"
                            else connector_account.c.owner_user_id == uid,
                        )
                    )
                    .limit(1)
                ).first()
            if existing is None:
                return _error(
                    f"Connect your Google account before adding {addon_label} access.",
                    409,
                    hint=(
                        f"Use Connect Google first; the {addon_label} permission is added to that "
                        "grant."
                    ),
                )
        try:
            config = GoogleOAuthConfig.from_env()
        except (ConnectorConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        # Configuration first, then reachability: an unconfigured provider is a 503 the
        # operator fixes, an unregistered host is the 409 Settings renders.
        redirect_uri = _redirect_for(PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(PROVIDER, request)
        config = replace(config, redirect_uri=redirect_uri)
        try:
            verifier, challenge = pkce_pair()
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "scope": scope,
                        "addon": addon,
                        "verifier": verifier,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                ),
                org,
            )
        except (ConnectorConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        url = authorization_url(
            config,
            # Least privilege at request time: an upgrade asks for the add-on's scopes only, so the
            # consent screen shows one new permission instead of re-asking for everything.
            scopes=list(ADDON_SCOPES[addon]) if addon else list(READ_SCOPES.values()),
            state=state,
            code_challenge=challenge,
        )
        return RedirectResponse(url, status_code=302)

    async def google_callback(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            return _error("Google did not grant access.", 400)
        code = request.query_params.get("code", "")
        sealed = request.query_params.get("state", "")
        if not code or not sealed:
            return _error("code and state are required", 400)
        redirect_uri = _redirect_for(PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(PROVIDER, request)
        try:
            # The seal is bound to this organization: a state minted in another
            # workspace does not open here, even with the same master key.
            pending = json.loads(decrypt_secret(sealed, org))
            config = replace(GoogleOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (SecretBoxError, ValueError, ConnectorConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        if pending["scope"] == "org" and not _is_admin(org, uid):
            return _error("Only workspace admins can connect organization accounts.", 403)
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                tokens = await exchange_code(
                    http, config, code=code, code_verifier=pending["verifier"]
                )
                if not tokens.refresh_token:
                    raise ConnectorAuthError(
                        "Google did not return a refresh token.",
                        hint="Remove the app from your Google account permissions and connect again.",
                    )
                profile = await http.get(
                    GMAIL_PROFILE_URL,
                    headers={"Authorization": f"Bearer {tokens.access_token}"},
                )
            except ConnectorAuthError as exc:
                return _error(str(exc), 400, exc.hint)

        if profile.status_code != 200:
            return _error("Could not read the connected Google account.", 502)
        email = profile.json().get("emailAddress")
        if not isinstance(email, str) or not email:
            return _error("Could not read the connected Google account.", 502)

        addon = str(pending.get("addon") or "")
        addon_label = ADDON_LABELS[addon] if addon else ""
        requested = list(ADDON_SCOPES[addon]) if addon else list(READ_SCOPES.values())
        granted = tokens.scope.split() if tokens.scope else []
        if addon:
            # Google lets a user untick any permission. Recording Meet as available when the
            # consent screen returned without it would show a capability the token cannot use and
            # turn every Meet call into a 403, so the upgrade fails here instead.
            if not granted:
                return _error(
                    "Google did not report which permissions it granted.",
                    502,
                    hint=(
                        "Try again. If it repeats, remove Hoja from your Google account "
                        "permissions and connect once more."
                    ),
                )
            if any(scope not in granted for scope in ADDON_SCOPES[addon]):
                return _error(
                    f"{addon_label} access was not granted. Tick the {addon_label} permission and "
                    "try again.",
                    400,
                )

        # One row per (owner, Google account), whichever scope set was last consented. A second row
        # for a Meet upgrade would hold the same Google grant under a second id, so Disconnect would
        # free only one of them and leave the other silently usable.
        owner_filter = (
            connector_account.c.owner_user_id.is_(None)
            if pending["scope"] == "org"
            else connector_account.c.owner_user_id == uid
        )
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id, connector_account.c.scopes).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == PROVIDER,
                        connector_account.c.external_account == email,
                        connector_account.c.status == "active",
                        owner_filter,
                    )
                )
            ).first()
            prior_scopes = existing[1] if existing is not None else ""
            if addon and existing is None:
                # The consent chooser lets the user pick a different Google account than the one
                # they started from. That account has no grant to extend, so this would create a
                # row holding Meet and nothing else - the state `start` refuses up front.
                return _error(
                    f"{addon_label} access was added to {email}, which is not a connected account "
                    "here. Connect that Google account first, then add this access.",
                    409,
                    hint=(
                        f"Use Connect Google for that account, then add {addon_label} access to it."
                    ),
                )
            # `include_granted_scopes=true` makes Google report the *whole* grant in `scope`, so
            # that list is authoritative whenever it is present. Unioning with the stored row
            # unconditionally would keep claiming a permission the user just removed on the
            # consent screen; the union is only the fallback for a response that omits `scope`.
            scopes = merge_scopes(granted) if granted else merge_scopes(requested, prior_scopes)
            row = {
                "id": uuid.uuid4().hex,
                "organization_id": org,
                "owner_user_id": None if pending["scope"] == "org" else uid,
                "provider": PROVIDER,
                "external_account": email,
                "scopes": " ".join(scopes),
                "encrypted_refresh_token": encrypt_secret(tokens.refresh_token, org),
                "status": "active",
                "created_at": _now(),
                "updated_at": _now(),
            }
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        scopes=row["scopes"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        updated_at=row["updated_at"],
                    )
                )
        # `?connector=<addon>-connected` is what Settings reads to say which grant was just widened.
        marker = f"{addon}-connected" if addon else "google-connected"
        return RedirectResponse(f"/?connector={marker}", status_code=302)

    async def notion_start(request: Request):
        """Begin the Notion OAuth flow. Notion grants are always user-scoped (`owner=user`).

        There is no PKCE and no `scope` parameter: least privilege lives on the integration
        object, and the consent screen shows "read content" only.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("scope", "user") != "user":
            return _error("Notion connections are personal to the installing user.", 400)
        try:
            config = NotionOAuthConfig.from_env()
        except (NotionConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        redirect_uri = _redirect_for(NOTION_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(NOTION_PROVIDER, request)
        config = replace(config, redirect_uri=redirect_uri)
        try:
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                ),
                org,
            )
        except (NotionConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        return RedirectResponse(notion_authorization_url(config, state=state), status_code=302)

    async def notion_callback(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            return _error("Notion did not grant access.", 400)
        code = request.query_params.get("code", "")
        sealed = request.query_params.get("state", "")
        if not code or not sealed:
            return _error("code and state are required", 400)
        redirect_uri = _redirect_for(NOTION_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(NOTION_PROVIDER, request)
        try:
            pending = json.loads(decrypt_secret(sealed, org))
            config = replace(NotionOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (SecretBoxError, ValueError, NotionConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                token = await notion_exchange_code(http, config, code=code)
            except NotionAuthError as exc:
                return _error(str(exc), 400, exc.hint)
            except (NotionAPIError, httpx.HTTPError) as exc:
                # Provider failure, not the user's fault. The message carries no credential.
                return _error(f"Could not reach Notion: {exc}", 502)

        workspace = (token.workspace_name or "").strip() or token.workspace_id
        if not workspace:
            return _error("Notion did not identify the connected workspace.", 502)
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": NOTION_PROVIDER,
            "external_account": workspace,
            # Notion has no OAuth scope parameter. This records the integration capability the
            # consent screen showed, so a future write path cannot claim more than was granted.
            "scopes": "read_content",
            "encrypted_refresh_token": encrypt_secret(token.access_token, org),
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Reconnecting the same workspace replaces its grant rather than adding a second row.
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == NOTION_PROVIDER,
                        connector_account.c.external_account == workspace,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        scopes=row["scopes"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        updated_at=row["updated_at"],
                    )
                )
        return RedirectResponse("/?connector=notion-connected", status_code=302)

    async def slack_start(request: Request):
        """Begin the Slack OAuth flow. The grant is a **user** token, so it carries one person's
        visibility; an organization-wide grant would hand that visibility to everyone and is
        refused rather than silently downgraded."""
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("scope", "user") != "user":
            return _error("Slack connections are personal to the installing user.", 400)
        try:
            config = SlackOAuthConfig.from_env()
        except (SlackConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        redirect_uri = _redirect_for(SLACK_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(SLACK_PROVIDER, request)
        config = replace(config, redirect_uri=redirect_uri)
        try:
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                ),
                org,
            )
        except (SlackConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        return RedirectResponse(slack_authorization_url(config, state=state), status_code=302)

    async def slack_callback(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            return _error("Slack did not grant access.", 400)
        code = request.query_params.get("code", "")
        sealed = request.query_params.get("state", "")
        if not code or not sealed:
            return _error("code and state are required", 400)
        redirect_uri = _redirect_for(SLACK_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(SLACK_PROVIDER, request)
        try:
            pending = json.loads(decrypt_secret(sealed, org))
            config = replace(SlackOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (SecretBoxError, ValueError, SlackConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                token = await slack_exchange_code(http, config, code=code)
            except SlackAuthError as exc:
                return _error(str(exc), 400, exc.hint)
            except (SlackAPIError, httpx.HTTPError) as exc:
                # Provider failure, not the user's fault. The message carries no credential.
                return _error(f"Could not reach Slack: {exc}", 502)

        workspace = token.workspace
        if not workspace:
            return _error("Slack did not identify the connected workspace.", 502)
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": SLACK_PROVIDER,
            "external_account": workspace,
            # Record the scopes Slack actually granted, not the ones we asked for, so a later
            # write path cannot claim more than the consent screen showed.
            "scopes": " ".join(token.scopes) if token.scopes else " ".join(USER_READ_SCOPES),
            # Rotation is off, so Slack returns a long-lived `xoxp-` user token and no refresh
            # token. It is sealed into the same credential column every connector uses.
            "encrypted_refresh_token": encrypt_secret(token.access_token, org),
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Reconnecting the same workspace replaces its grant rather than adding a second row.
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == SLACK_PROVIDER,
                        connector_account.c.external_account == workspace,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        scopes=row["scopes"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        updated_at=row["updated_at"],
                    )
                )
        return RedirectResponse("/?connector=slack-connected", status_code=302)

    def _calendly_broker_origin() -> str:
        """The shared return address, or "" when this deployment has no broker.

        A multi-tenant deployment registers ONE Calendly callback on a host that is not any
        tenant (`api.hoja.work`), because Calendly accepts exactly one redirect URI per app. A
        self-hosted install registers its own callback and gets no broker. The broker only
        forwards, so it holds no credential and stores nothing.
        """
        return connector_hosts.broker_origin()

    def _calendly_tenant_origin(value: str | None) -> str | None:
        """A workspace origin this deployment really serves, or None.

        `connector_hosts.tenant_origin` answers both halves: the name must be an https host under
        our apex and not a reserved control-plane name (`identity_transfer.tenant_target`), and it
        must be a live ``organization.vanity_host`` row. Under ADR-003 the vanity host, not the
        url_key, is what identifies a workspace, so `clover.hoja.work` resolves even though the
        organization's key is `clover4`.
        """
        resolved = connector_hosts.tenant_origin(engine, value)
        return resolved[0] if resolved else None

    async def calendly_start(request: Request):
        """Begin the Calendly flow under the tenant's session.

        With a broker configured the tenant's own host is never registered with Calendly, so the
        host to return to is carried in the state instead: the broker forwards to it and the
        tenant redeems the code there, in the same session that started the flow.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("scope", "user") != "user":
            # A Calendly token is one person's calendar. Calling it an organization grant would
            # hand that person's meetings to every member, so it is refused rather than downgraded.
            return _error("Calendly connections are personal to the connecting user.", 400)
        # Without a broker this install registers its own callback, so there is no
        # workspace to route through and nothing to bind the return address to.
        tenant: str | None = None
        if _calendly_broker_origin():
            tenant = connector_hosts.workspace_origin(engine, request)
            if tenant is None:
                return _unavailable(CALENDLY_PROVIDER, request)
        redirect_uri = _redirect_for(CALENDLY_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(CALENDLY_PROVIDER, request)
        try:
            config = replace(CalendlyOAuthConfig.from_env(), redirect_uri=redirect_uri)
            verifier, _challenge = calendly_pkce_pair()
            # Sealed under this workspace's key; the readable hint tells the broker (which has
            # no session) which key to try. A rewritten hint fails the AAD check instead of
            # redirecting a grant somewhere else.
            state = connector_hosts.wrap_state(
                encrypt_secret(
                    json.dumps(
                        {
                            "uid": uid,
                            "org": org,
                            "provider": CALENDLY_PROVIDER,
                            "verifier": verifier,
                            "tenant": tenant,
                            "exp": int(time.time()) + STATE_TTL_SECONDS,
                            "nonce": uuid.uuid4().hex,
                        }
                    ),
                    org,
                ),
                tenant,
            )
        except (CalendlyConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        return RedirectResponse(
            calendly_authorization_url(
                config,
                state=state,
                code_challenge=calendly_s256(verifier),
                redirect_uri=redirect_uri,
            ),
            status_code=302,
        )

    async def calendly_callback(request: Request):
        """The registered redirect URI: the broker when one is configured, terminal otherwise."""
        if _calendly_broker_origin():
            return await _calendly_forward(request)
        return await _calendly_complete(request)

    async def _calendly_forward(request: Request):
        """Verify the sealed state and hand the code to the tenant. Stores nothing.

        The broker never sees a client secret and never calls Calendly: the code is redeemed by
        the tenant that started the flow, with the same redirect URI, so a compromised broker
        could redirect a grant but not mint one.
        """
        if request.query_params.get("error"):
            return _error("Calendly did not grant access.", 400)
        code = request.query_params.get("code", "")
        raw_state = request.query_params.get("state", "")
        if not code or not raw_state:
            return _error("code and state are required", 400)
        sealed, hint = connector_hosts.unwrap_state(raw_state)
        resolved = connector_hosts.tenant_origin(engine, hint)
        if resolved is None:
            # Not a workspace of ours, a reserved control-plane name, or not registered at all:
            # never bounce a code to an address the state merely claims.
            return _error("Connection request names no workspace on this deployment.", 403)
        _tenant, target_org = resolved
        try:
            # The hint only selected the key; the ciphertext must authenticate for it.
            pending = json.loads(decrypt_secret(sealed, target_org))
        except (SecretBoxError, ValueError):
            return _error("Invalid or expired connection request.", 400)
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("provider") != CALENDLY_PROVIDER:
            return _error("Invalid connection request.", 400)
        if pending.get("org") != target_org:
            return _error("Connection request belongs to a different workspace.", 403)
        tenant = _calendly_tenant_origin(pending.get("tenant"))
        if tenant is None:
            return _error("Connection request names no workspace on this deployment.", 403)
        query = urlencode({"code": code, "state": raw_state})
        return RedirectResponse(f"{tenant}{CALENDLY_FINISH_PATH}?{query}", status_code=302)

    async def calendly_finish(request: Request):
        return await _calendly_complete(request)

    async def _calendly_complete(request: Request):
        """Redeem the code on the tenant, in the session that started the flow."""
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            return _error("Calendly did not grant access.", 400)
        code = request.query_params.get("code", "")
        sealed = request.query_params.get("state", "")
        if not code or not sealed:
            return _error("code and state are required", 400)
        redirect_uri = _redirect_for(CALENDLY_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(CALENDLY_PROVIDER, request)
        sealed, _hint = connector_hosts.unwrap_state(sealed)
        try:
            pending = json.loads(decrypt_secret(sealed, org))
            config = replace(CalendlyOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (SecretBoxError, ValueError, CalendlyConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("provider") != CALENDLY_PROVIDER:
            return _error("Invalid connection request.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            # The state is bound to the person and workspace that started it. This is what
            # replaces a cookie: the broker cannot complete a flow on someone else's behalf.
            return _error("Connection request belongs to a different session.", 403)
        tenant = pending.get("tenant")
        if tenant is not None:
            # The state pinned `https://<workspace host>` at start; comparing that against
            # `request.base_url` made the workspace's own callback look foreign, because behind the
            # proxy that field is still http unless uvicorn trusts the forwarding headers.
            here = connector_hosts.workspace_origin(engine, request)
            if here is None or tenant != here:
                return _error("Connection request belongs to a different workspace.", 403)
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                tokens = await calendly_exchange_code(
                    http,
                    config,
                    code=code,
                    code_verifier=str(pending.get("verifier", "")),
                    redirect_uri=redirect_uri,
                )

                class _StaticToken:
                    async def access_token(self) -> str:
                        return tokens.access_token

                    async def invalidate(self) -> None:
                        return None

                user = await CalendlyClient(http, _StaticToken()).get_current_user()
            except CalendlyAuthError as exc:
                return _error(str(exc), 400, exc.hint)
            except (CalendlyAPIError, httpx.HTTPError) as exc:
                return _error(f"Could not reach Calendly: {exc}", 502)

        label = user.email or user.uri
        if not label:
            return _error("Calendly did not identify the connected account.", 502)
        expires_at = int(time.time()) + tokens.expires_in
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": CALENDLY_PROVIDER,
            "external_account": label,
            # Record what Calendly granted, so a later code path cannot claim more than the
            # consent screen showed.
            "scopes": " ".join(tokens.scopes) if tokens.scopes else " ".join(CALENDLY_READ_SCOPES),
            "encrypted_refresh_token": encrypt_secret(tokens.refresh_token, org),
            "encrypted_access_token": encrypt_secret(tokens.access_token, org),
            "access_token_expires_at": str(expires_at),
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Reconnecting the same Calendly account replaces its grant rather than adding a row.
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == CALENDLY_PROVIDER,
                        connector_account.c.external_account == label,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        scopes=row["scopes"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        encrypted_access_token=row["encrypted_access_token"],
                        access_token_expires_at=row["access_token_expires_at"],
                        updated_at=row["updated_at"],
                    )
                )
        back = f"{tenant}/?connector=calendly-connected" if tenant else "/?connector=calendly-connected"
        return RedirectResponse(back, status_code=302)

    def _zoom_origin(request: Request) -> str | None:
        """The https origin this request's registered callback lives on, or None.

        Zoom's app holds an allow list of full callback URLs, so the origin is the only part that
        varies per workspace; the path is fixed. It is read off the callback this deployment
        derived for the request rather than off ``request.base_url``: behind the reverse proxy
        uvicorn only rewrites that field's scheme when it trusts the forwarding headers, so an
        untrusted ``X-Forwarded-Proto`` left every origin looking like ``http://`` and refused Zoom
        on a deployment that serves https only. Taking it from the derived callback also means a
        one-host install pins the URI it actually registered, not the address the request arrived
        on, and that start and callback always agree.
        """
        callback = _redirect_for(ZOOM_PROVIDER, request)
        path = connector_hosts.CALLBACK_PATHS[ZOOM_PROVIDER]
        if not callback or not callback.endswith(path):
            return None
        return callback[: -len(path)]

    async def zoom_start(request: Request):
        """Begin the Zoom flow on the tenant that owns the registered callback.

        A Zoom token is one person's meeting history, so the connection is personal and an
        organization-wide grant is refused rather than silently downgraded.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("scope", "user") != "user":
            return _error("Zoom connections are personal to the connecting user.", 400)
        origin = _zoom_origin(request)
        # Refuse before Zoom: an unregistered redirect is rejected by Zoom with a consent-screen
        # error the user cannot act on, which looks like an outage rather than a configuration gap.
        redirect_uri = _redirect_for(ZOOM_PROVIDER, request)
        if redirect_uri is None or origin is None:
            return _unavailable(ZOOM_PROVIDER, request)
        try:
            config = replace(ZoomOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (ZoomConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        try:
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "provider": ZOOM_PROVIDER,
                        "origin": origin,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                ),
                org,
            )
        except SecretBoxError as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        return RedirectResponse(
            zoom_authorization_url(config, state=state, redirect_uri=redirect_uri),
            status_code=302,
        )

    async def zoom_callback(request: Request):
        """The registered redirect URI: redeem the code in the session that started the flow."""
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            return _error("Zoom did not grant access.", 400)
        code = request.query_params.get("code", "")
        sealed = request.query_params.get("state", "")
        if not code or not sealed:
            return _error("code and state are required", 400)
        redirect_uri = _redirect_for(ZOOM_PROVIDER, request)
        if redirect_uri is None:
            return _unavailable(ZOOM_PROVIDER, request)
        try:
            pending = json.loads(decrypt_secret(sealed, org))
            config = replace(ZoomOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (SecretBoxError, ValueError, ZoomConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("provider") != ZOOM_PROVIDER:
            return _error("Invalid connection request.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            # The state is bound to the person and workspace that started it. This is what
            # replaces a cookie: a callback cannot complete a flow on someone else's behalf.
            return _error("Connection request belongs to a different session.", 403)
        origin = _zoom_origin(request)
        if origin is None or pending.get("origin") != origin:
            return _error("Connection request belongs to a different workspace.", 403)
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                tokens = await zoom_exchange_code(
                    http, config, code=code, redirect_uri=redirect_uri
                )

                class _StaticToken:
                    async def access_token(self) -> str:
                        return tokens.access_token

                    async def invalidate(self) -> None:
                        return None

                    def api_base(self) -> str:
                        return config.api_base

                user = await ZoomClient(http, _StaticToken()).get_current_user()
            except ZoomAuthError as exc:
                return _error(str(exc), 400, exc.hint)
            except (ZoomAPIError, httpx.HTTPError) as exc:
                return _error(f"Could not reach Zoom: {exc}", 502)

        label = user.email or user.id
        if not label:
            return _error("Zoom did not identify the connected account.", 502)
        expires_at = int(time.time()) + tokens.expires_in
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": ZOOM_PROVIDER,
            "external_account": label,
            # Record what Zoom granted, so a later code path cannot claim more than the consent
            # screen showed.
            "scopes": " ".join(tokens.scopes) if tokens.scopes else " ".join(ZOOM_READ_SCOPES),
            "encrypted_refresh_token": encrypt_secret(tokens.refresh_token, org),
            "encrypted_access_token": encrypt_secret(tokens.access_token, org),
            "access_token_expires_at": str(expires_at),
            # Zoom's own user id for the grant. Nothing addresses it, but it is what an operator
            # or a support request needs to identify the account inside Zoom.
            "external_id": user.id or None,
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Reconnecting the same Zoom account replaces its grant rather than adding a second row.
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == ZOOM_PROVIDER,
                        connector_account.c.external_account == label,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        scopes=row["scopes"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        encrypted_access_token=row["encrypted_access_token"],
                        access_token_expires_at=row["access_token_expires_at"],
                        external_id=row["external_id"],
                        updated_at=row["updated_at"],
                    )
                )
        return RedirectResponse("/?connector=zoom-connected", status_code=302)

    class _GrafanaStaticToken:
        """The stored token as a source the package can ask for, never a string it can persist."""

        def __init__(self, token: str) -> None:
            self._token = token

        async def access_token(self) -> str:
            return self._token

        async def invalidate(self) -> None:
            return None

    async def _grafana_probe(url: str, token: str):
        """Validate a customer URL and token with one live read. Returns (user, instance)."""
        instance = validate_instance_url(url)
        async with httpx.AsyncClient(timeout=15) as http:
            user = await GrafanaClient(http, _GrafanaStaticToken(token), instance.url).get_current_user()
        return user, instance

    async def grafana_validate(request: Request):
        """Check a URL and token without storing either. The UI uses this before Connect."""
        _uid, _org, failure = await _require_user(request)
        if failure:
            return failure
        try:
            body = await request.json()
        except ValueError:
            return _error("A JSON body with url and token is required.", 400)
        url = str(body.get("url") or "").strip()
        token = str(body.get("token") or "").strip()
        if not GRAFANA_TOKEN_PATTERN.match(token):
            return _error(
                "That does not look like a Grafana service-account token.",
                400,
                hint="Create a token under Administration > Users and access > Service accounts.",
            )
        try:
            GrafanaConfig.from_env()
        except GrafanaConfigError as exc:
            return _error(str(exc), 503, exc.hint)
        try:
            user, instance = await _grafana_probe(url, token)
        except UnsafeInstanceError as exc:
            return _error(str(exc), 400, exc.hint)
        except GrafanaAuthError as exc:
            return _error(str(exc), 400, exc.hint)
        except GrafanaAPIError as exc:
            return _error(str(exc), 502, exc.hint)
        return JSONResponse(
            {
                "valid": True,
                "instance": instance.url,
                "account": user.label,
                "isGrafanaAdmin": user.isGrafanaAdmin,
            }
        )

    async def grafana_connect(request: Request):
        """Store a customer instance URL and sealed service-account token.

        There is no OAuth flow to start: the customer pastes the two values. The live read is what
        proves both are usable before a row exists, and reconnecting the same instance replaces its
        grant rather than adding a second row.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        try:
            body = await request.json()
        except ValueError:
            return _error("A JSON body with url and token is required.", 400)
        url = str(body.get("url") or "").strip()
        token = str(body.get("token") or "").strip()
        if not GRAFANA_TOKEN_PATTERN.match(token):
            return _error(
                "That does not look like a Grafana service-account token.",
                400,
                hint="Create a token under Administration > Users and access > Service accounts.",
            )
        try:
            GrafanaConfig.from_env()
        except GrafanaConfigError as exc:
            return _error(str(exc), 503, exc.hint)
        try:
            user, instance = await _grafana_probe(url, token)
        except UnsafeInstanceError as exc:
            return _error(str(exc), 400, exc.hint)
        except GrafanaAuthError as exc:
            return _error(str(exc), 400, exc.hint)
        except GrafanaAPIError as exc:
            return _error(str(exc), 502, exc.hint)

        label = user.label
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": GRAFANA_PROVIDER,
            "external_account": label,
            # No OAuth scopes exist; the role the customer granted is the only meaningful scope.
            "scopes": "viewer",
            "encrypted_refresh_token": encrypt_secret(token, org),
            "external_id": user.uid,
            "external_url": instance.url,
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == GRAFANA_PROVIDER,
                        connector_account.c.external_url == instance.url,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        external_account=row["external_account"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        external_id=row["external_id"],
                        updated_at=row["updated_at"],
                    )
                )
        return JSONResponse(
            {
                "connected": True,
                "instance": instance.url,
                "account": label,
            }
        )

    def _github_origin(request: Request) -> str | None:
        """The https origin this request's registered callback lives on, or None.

        GitHub matches the redirect URI by string against the app's registered list, so the origin
        is the only part the deployment has to derive; the path is fixed. It is read off the
        callback ``connector_hosts`` derived for this request, not off ``request.base_url``: behind
        the reverse proxy uvicorn only rewrites that field's scheme when it trusts the forwarding
        headers, so an untrusted ``X-Forwarded-Proto`` makes every origin look like ``http://`` and
        refuses a deployment that serves https only. Taking it from the derived callback also means
        a host that is not a workspace this deployment serves, or that has no registered callback,
        is refused here instead of at GitHub.
        """
        callback = _redirect_for(GITHUB_PROVIDER, request)
        path = connector_hosts.CALLBACK_PATHS[GITHUB_PROVIDER]
        if not callback or not callback.endswith(path):
            return None
        return callback[: -len(path)]

    async def github_start(request: Request):
        """Begin the GitHub App user-to-server flow on the tenant that owns the callback.

        A GitHub token is one person's repository access, so the connection is personal and an
        organization-wide grant is refused rather than silently downgraded.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("scope", "user") != "user":
            return _error("GitHub connections are personal to the connecting user.", 400)
        origin = _github_origin(request)
        if origin is None:
            # Not a host we serve, or no callback registered for it: refuse before GitHub so the
            # person reads a sentence instead of `redirect_uri_mismatch`.
            return _unavailable(GITHUB_PROVIDER, request)
        try:
            config = GitHubAppConfig.from_env()
        except (GitHubConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        # Refuse before GitHub: an unregistered redirect is answered with redirect_uri_mismatch on
        # a page the user cannot act on, which reads as an outage rather than a configuration gap.
        redirect_uri = config.redirect_uri_for(origin)
        if redirect_uri is None:
            return _error(
                "This address is not a registered GitHub callback on this deployment.",
                400,
                hint=(
                    "Open your workspace URL and connect GitHub from there, or register "
                    f"{origin}{GITHUB_REDIRECT_PATH} in the app's callback URLs."
                ),
            )
        try:
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "provider": GITHUB_PROVIDER,
                        "origin": origin,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                ),
                org,
            )
        except SecretBoxError as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        return RedirectResponse(
            github_authorization_url(config, state=state, redirect_uri=redirect_uri),
            status_code=302,
        )

    async def github_callback(request: Request):
        """The registered callback: redeem the code in the session that started the flow."""
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            # GitHub reports a declined consent as error=access_denied on the callback.
            return _error("GitHub did not grant access.", 400)
        code = request.query_params.get("code", "")
        sealed = request.query_params.get("state", "")
        if not code or not sealed:
            return _error("code and state are required", 400)
        try:
            pending = json.loads(decrypt_secret(sealed, org))
            config = GitHubAppConfig.from_env()
        except (SecretBoxError, ValueError, GitHubConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("provider") != GITHUB_PROVIDER:
            return _error("Invalid connection request.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            # The state is bound to the person and workspace that started it. This is what
            # replaces a cookie: a callback cannot complete a flow on someone else's behalf.
            return _error("Connection request belongs to a different session.", 403)
        origin = _github_origin(request)
        if origin is None or pending.get("origin") != origin:
            return _error("Connection request belongs to a different workspace.", 403)
        redirect_uri = config.redirect_uri_for(origin)
        if redirect_uri is None:
            return _error(
                "This address is not a registered GitHub callback on this deployment.", 403
            )
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                tokens = await github_exchange_code(
                    http, config, code=code, redirect_uri=redirect_uri
                )

                class _StaticToken:
                    async def access_token(self) -> str:
                        return tokens.access_token

                    async def invalidate(self) -> None:
                        return None

                    def api_base(self) -> str:
                        return config.api_base

                user = await GitHubClient(http, _StaticToken()).get_current_user()
            except (GitHubAuthError, GitHubAPIError, httpx.HTTPError) as exc:
                hint = getattr(exc, "hint", None)
                status = 400 if isinstance(exc, GitHubAuthError) else 502
                return _error(str(exc), status, hint)

        label = user.login
        if not label:
            return _error("GitHub did not identify the connected account.", 502)
        expires_at = int(time.time()) + tokens.expires_in
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": GITHUB_PROVIDER,
            "external_account": label,
            # A GitHub App's permissions live on the registration, not in a scope string; keep the
            # column empty rather than inventing one, and store the app's read set nowhere the UI
            # could mistake for a per-grant scope.
            "scopes": "",
            "encrypted_refresh_token": encrypt_secret(tokens.refresh_token, org),
            "encrypted_access_token": encrypt_secret(tokens.access_token, org),
            "access_token_expires_at": str(expires_at),
            # GitHub's own numeric user id. Nothing addresses it, but it is what an operator or a
            # support request needs to identify the account inside GitHub.
            "external_id": str(user.id or "") or None,
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Reconnecting the same GitHub account replaces its grant rather than adding a second row.
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == GITHUB_PROVIDER,
                        connector_account.c.external_account == label,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        encrypted_access_token=row["encrypted_access_token"],
                        access_token_expires_at=row["access_token_expires_at"],
                        external_id=row["external_id"],
                        updated_at=row["updated_at"],
                    )
                )
        return RedirectResponse("/?connector=github-connected", status_code=302)

    def _sentry_redirect_uri(config: SentryOAuthConfig) -> str:
        """The one string registered on the Sentry integration.

        `_calendly_broker_origin` is the deployment-wide answer to "is there a control-plane
        return host", and it is the same host for every provider that only accepts one redirect;
        the path differs. Reusing the helper keeps one definition of the broker.
        """
        broker = _calendly_broker_origin()
        return f"{broker}{SENTRY_REDIRECT_PATH}" if broker else config.redirect_uri

    def _sentry_redirect_uri(request: Request) -> str | None:
        """Sentry's registered Redirect URL for this request (broker when configured)."""
        return _redirect_for(SENTRY_PROVIDER, request)

    async def sentry_start(request: Request):
        """Begin the Sentry install under the tenant's session.

        There is no PKCE leg to prepare: Sentry's public-integration flow hands the code back to
        the registered Redirect URL and the exchange carries the client secret. What still has to
        be bound is *who* and *where*: the sealed state names the user, the workspace and the
        tenant to return to, and Sentry echoes it through the install untouched.
        """
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("scope", "user") != "user":
            # A Sentry installation is one organization's grant, created by the session that
            # installed it. Calling it a workspace-wide grant here would let any member read that
            # organization's issues with the installer's authority, so it is refused.
            return _error("Sentry connections are personal to the user who installs them.", 400)
        # Same shape as Calendly: the broker needs a workspace to hand the grant to;
        # a self-hosted install registers its own Redirect URL and needs none.
        tenant: str | None = None
        if _calendly_broker_origin():
            tenant = connector_hosts.workspace_origin(engine, request)
            if tenant is None:
                return _unavailable(SENTRY_PROVIDER, request)
        redirect_uri = _sentry_redirect_uri(request)
        if redirect_uri is None:
            return _unavailable(SENTRY_PROVIDER, request)
        try:
            config = replace(SentryOAuthConfig.from_env(), redirect_uri=redirect_uri)
            state = connector_hosts.wrap_state(
                encrypt_secret(
                    json.dumps(
                        {
                            "uid": uid,
                            "org": org,
                            "provider": SENTRY_PROVIDER,
                            "tenant": tenant,
                            "exp": int(time.time()) + STATE_TTL_SECONDS,
                            "nonce": uuid.uuid4().hex,
                        }
                    ),
                    org,
                ),
                tenant,
            )
        except (SentryConfigError, SecretBoxError) as exc:
            return _error(str(exc), 503, getattr(exc, "hint", None))
        return RedirectResponse(sentry_installation_url(config, state=state), status_code=302)

    async def sentry_callback(request: Request):
        """The registered Redirect URL: the broker when one is configured, terminal otherwise."""
        if _calendly_broker_origin():
            return await _sentry_forward(request)
        return await _sentry_complete(request)

    async def _sentry_forward(request: Request):
        """Verify the sealed state and hand the install to the tenant. Stores nothing."""
        if request.query_params.get("error"):
            return _error("Sentry did not install the integration.", 400)
        code = request.query_params.get("code", "")
        installation_id = request.query_params.get("installationId", "")
        org_slug = request.query_params.get("orgSlug", "")
        raw_state = request.query_params.get("state", "")
        if not code or not installation_id or not raw_state:
            return _error("code, installationId and state are required", 400)
        sealed, hint = connector_hosts.unwrap_state(raw_state)
        resolved = connector_hosts.tenant_origin(engine, hint)
        if resolved is None:
            return _error("Connection request names no workspace on this deployment.", 403)
        _tenant, target_org = resolved
        try:
            pending = json.loads(decrypt_secret(sealed, target_org))
        except (SecretBoxError, ValueError):
            return _error("Invalid or expired connection request.", 400)
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("provider") != SENTRY_PROVIDER:
            return _error("Invalid connection request.", 400)
        if pending.get("org") != target_org:
            return _error("Connection request belongs to a different workspace.", 403)
        tenant = _calendly_tenant_origin(pending.get("tenant"))
        if tenant is None:
            return _error("Connection request names no workspace on this deployment.", 403)
        query = urlencode(
            {
                "code": code,
                "installationId": installation_id,
                "orgSlug": org_slug,
                "state": raw_state,
            }
        )
        return RedirectResponse(f"{tenant}{SENTRY_FINISH_PATH}?{query}", status_code=302)

    async def sentry_finish(request: Request):
        return await _sentry_complete(request)

    async def _sentry_complete(request: Request):
        """Redeem the installation code on the tenant, in the session that started the flow."""
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        if request.query_params.get("error"):
            return _error("Sentry did not install the integration.", 400)
        code = request.query_params.get("code", "")
        installation_id = request.query_params.get("installationId", "")
        org_slug = request.query_params.get("orgSlug", "")
        sealed = request.query_params.get("state", "")
        if not code or not installation_id or not sealed:
            return _error("code, installationId and state are required", 400)
        redirect_uri = _sentry_redirect_uri(request)
        if redirect_uri is None:
            return _unavailable(SENTRY_PROVIDER, request)
        sealed, _hint = connector_hosts.unwrap_state(sealed)
        try:
            pending = json.loads(decrypt_secret(sealed, org))
            config = replace(SentryOAuthConfig.from_env(), redirect_uri=redirect_uri)
        except (SecretBoxError, ValueError, SentryConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("provider") != SENTRY_PROVIDER:
            return _error("Invalid connection request.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        tenant = pending.get("tenant")
        if tenant is not None:
            here = connector_hosts.workspace_origin(engine, request)
            if here is None or tenant != here:
                return _error("Connection request belongs to a different workspace.", 403)
        if not _consume_state(engine, org, str(pending.get("nonce", "")), int(pending["exp"])):
            return _error("Connection request was already used. Start again.", 400)

        async with httpx.AsyncClient(timeout=15) as http:
            try:
                tokens = await sentry_exchange_code(
                    http, config, code=code, installation_id=installation_id
                )

                class _StaticToken:
                    """The token just minted, plus the org slug the callback claimed.

                    `get_organization` then reads the org from Sentry itself: the token is scoped to
                    exactly one organization, so its own answer is the authoritative one and the
                    callback's `orgSlug` is only a claim to check it against.
                    """

                    def organization(self) -> str:
                        return org_slug

                    async def access_token(self) -> str:
                        return tokens.access_token

                    async def invalidate(self) -> None:
                        return None

                connected = await SentryClient(http, _StaticToken()).get_organization()
            except SentryAuthError as exc:
                return _error(str(exc), 400, exc.hint)
            except (SentryAPIError, httpx.HTTPError) as exc:
                return _error(f"Could not reach Sentry: {exc}", 502)

        if not connected.slug:
            return _error("Sentry did not identify the connected organization.", 502)
        if org_slug and connected.slug != org_slug:
            # The token is bound to one org; if the callback named another, refuse rather than
            # store a label that disagrees with the credential.
            return _error("Sentry returned a different organization than the install named.", 400)
        label = connected.slug
        expires_at = int(tokens.expires_at)
        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": uid,
            "provider": SENTRY_PROVIDER,
            "external_account": label,
            "external_id": str(tokens.installation_id),
            # Record what Sentry granted, so a later code path cannot claim more than the install
            # page showed.
            "scopes": " ".join(tokens.scopes) if tokens.scopes else " ".join(SENTRY_READ_SCOPES),
            "encrypted_refresh_token": encrypt_secret(tokens.refresh_token, org),
            "encrypted_access_token": encrypt_secret(tokens.access_token, org),
            "access_token_expires_at": str(expires_at),
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Reinstalling the same Sentry organization replaces its grant rather than adding a row:
        # one installation is one grant, and two rows would mean two different timer chains.
        with engine.begin() as conn:
            existing = conn.execute(
                select(connector_account.c.id).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == SENTRY_PROVIDER,
                        connector_account.c.external_account == label,
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.status == "active",
                    )
                )
            ).first()
            if existing is None:
                conn.execute(connector_account.insert().values(**row))
            else:
                conn.execute(
                    update(connector_account)
                    .where(connector_account.c.id == existing[0])
                    .values(
                        external_id=row["external_id"],
                        scopes=row["scopes"],
                        encrypted_refresh_token=row["encrypted_refresh_token"],
                        encrypted_access_token=row["encrypted_access_token"],
                        access_token_expires_at=row["access_token_expires_at"],
                        updated_at=row["updated_at"],
                    )
                )
        back = f"{tenant}/?connector=sentry-connected" if tenant else "/?connector=sentry-connected"
        return RedirectResponse(back, status_code=302)

    async def sentry_webhook(request: Request):
        """Sentry's lifecycle webhook: `installation.created` and `installation.deleted`.

        The subscribe-only events are empty on this integration, but lifecycle events are delivered
        regardless. Without this route a user who removes Hoja in Sentry leaves a row that still
        looks connected, and the next tool call fails with a 403 instead of asking them to
        reconnect. The body is authenticated with `sentry-hook-signature`:
        `hex(HMAC-SHA256(client_secret, raw_body))`.
        """
        try:
            config = SentryOAuthConfig.from_env()
        except SentryConfigError as exc:
            return _error(str(exc), 503, exc.hint)
        raw = await request.body()
        given = (request.headers.get("sentry-hook-signature") or "").strip()
        expected = hmac.new(
            config.client_secret.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        if not given or not hmac.compare_digest(given, expected):
            return _error("Invalid webhook signature.", 401)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return _error("Webhook body is not JSON.", 400)
        action = str(payload.get("action") or "")
        installation = payload.get("installation") if isinstance(payload, dict) else None
        installation = installation if isinstance(installation, dict) else {}
        installation_id = str(installation.get("uuid") or "")
        if action == "deleted" and installation_id:
            with engine.begin() as conn:
                conn.execute(
                    update(connector_account)
                    .where(
                        and_(
                            connector_account.c.provider == SENTRY_PROVIDER,
                            connector_account.c.external_id == installation_id,
                            connector_account.c.status == "active",
                        )
                    )
                    .values(
                        status="revoked",
                        revoked_at=_now(),
                        updated_at=_now(),
                        encrypted_refresh_token="",
                        encrypted_access_token="",
                        access_token_expires_at="",
                    )
                )
        return JSONResponse({"ok": True})

    async def connector_config(request: Request):
        """Which connectors this deployment can actually run.

        The setup wizard must not offer a connection that could never complete,
        and it must not make the deployment's secrets public to answer that.
        Invitations never leave this endpoint: they are shared as links.
        """
        # Public on purpose: one boolean that lets a pre-session setup page hide
        # an impossible step. No client id, secret, tenant or account is exposed.
        try:
            GoogleOAuthConfig.from_env()
            google_available = True
        except (ConnectorConfigError, SecretBoxError):
            google_available = False
        # Connecting needs only the OAuth client; handing the account to an agent also needs a
        # signing key, because that is what authenticates the principal on HOJA_CONNECTOR_MCP_URL.
        # One flag for both reported a deployment as ready while every turn silently got no toolset.
        agent_available = google_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        try:
            NotionOAuthConfig.from_env()
            notion_available = True
        except (NotionConfigError, SecretBoxError):
            notion_available = False
        notion_agent = notion_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        try:
            SlackOAuthConfig.from_env()
            slack_available = True
        except (SlackConfigError, SecretBoxError):
            slack_available = False
        slack_agent = slack_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        try:
            CalendlyOAuthConfig.from_env()
            calendly_available = True
        except (CalendlyConfigError, SecretBoxError):
            calendly_available = False
        calendly_agent = calendly_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        try:
            SentryOAuthConfig.from_env()
            sentry_available = True
        except (SentryConfigError, SecretBoxError):
            sentry_available = False
        sentry_agent = sentry_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        try:
            ZoomOAuthConfig.from_env()
            zoom_available = True
        except (ZoomConfigError, SecretBoxError):
            zoom_available = False
        zoom_agent = zoom_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        # Configured is not the same as reachable from the host this person is on. Under
        # shared tenancy one deployment serves many hosts and a provider only accepts the
        # callbacks it has registered, so Settings needs the per-host answer: otherwise the
        # Connect button sends the browser to a consent screen that fails with
        # `redirect_uri_mismatch`, which reads as an outage rather than a missing registration.
        try:
            github_config = GitHubAppConfig.from_env()
            github_available = True
        except (GitHubConfigError, SecretBoxError):
            github_config = None
            github_available = False
        github_agent = github_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        # Grafana has no app credential: connecting needs only the sealing key that protects the
        # stored token, so the flag is true on any deployment that can seal secrets at all. It is
        # also the only provider here with no callback registered at the provider, so it is
        # available on every host this deployment serves rather than on a list of registered ones.
        try:
            GrafanaConfig.from_env()
            grafana_available = True
        except (GrafanaConfigError, SecretBoxError):
            grafana_available = False
        grafana_agent = grafana_available and bool(
            os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
        )
        host = connector_hosts.request_host(request)
        host_available = {
            provider: connector_hosts.callback_url(engine, request, provider) is not None
            for provider in connector_hosts.CALLBACK_PATHS
        }
        host_available[GRAFANA_PROVIDER] = True
        unavailable_reason = {
            provider: (
                None if host_available[provider]
                else connector_hosts.unavailable_detail(provider, host, engine)
            )
            for provider in host_available
        }
        return JSONResponse(
            {
                "googleWorkspace": google_available,
                "googleWorkspaceAgent": agent_available,
                "notion": notion_available,
                "notionAgent": notion_agent,
                "slack": slack_available,
                "slackAgent": slack_agent,
                "calendly": calendly_available,
                "calendlyAgent": calendly_agent,
                "sentry": sentry_available,
                "sentryAgent": sentry_agent,
                "zoom": zoom_available,
                "zoomAgent": zoom_agent,
                "github": github_available,
                "githubAgent": github_agent,
                # The app's public install page is not a secret and is what makes a *private*
                # repository readable: the user token is the intersection of user and app access,
                # so an app that is not installed on the repository's account cannot read it.
                "githubInstallUrl": github_config.install_url() if github_config else None,
                "grafana": grafana_available,
                "grafanaAgent": grafana_agent,
                # Per-host reachability: `available` is (configured AND a registered callback
                # on this host); `reason` is what Settings shows when it is not.
                "host": host,
                "availableOnThisHost": host_available,
                "unavailableReason": unavailable_reason,
            },
            headers={"cache-control": "no-store"},
        )

    async def accounts_list(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    select(connector_account)
                    .where(
                        and_(
                            connector_account.c.organization_id == org,
                            connector_account.c.status == "active",
                            or_(
                                connector_account.c.owner_user_id == uid,
                                connector_account.c.owner_user_id.is_(None),
                            ),
                        )
                    )
                    .order_by(connector_account.c.created_at)
                )
                .mappings()
                .all()
            )
        return JSONResponse({"accounts": [_serialize(row) for row in rows]})

    async def account_revoke(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        account_id = request.path_params["account_id"]
        with engine.connect() as conn:
            row = (
                conn.execute(
                    select(connector_account).where(
                        and_(
                            connector_account.c.id == account_id,
                            connector_account.c.organization_id == org,
                            connector_account.c.status == "active",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return _error("Connected account not found.", 404)
        is_owner = row["owner_user_id"] == uid
        if not (is_owner or (row["owner_user_id"] is None and _is_admin(org, uid))):
            # Same response as a missing account, so callers cannot probe for other users' accounts.
            return _error("Connected account not found.", 404)

        try:
            refresh = decrypt_secret(row["encrypted_refresh_token"], row["organization_id"])
        except SecretBoxError:
            return _error("Stored credential is unreadable; contact an administrator.", 500)
        provider_revoked = False
        if row["provider"] == PROVIDER:
            async with httpx.AsyncClient(timeout=15) as http:
                response = await http.post(GOOGLE_REVOKE_URL, data={"token": refresh})
            # Google answers 200 on success and 400 if the token is already invalid; both end the grant.
            if response.status_code not in (200, 400):
                return _error("Google did not confirm revocation. Try again.", 502)
            provider_revoked = True
        elif row["provider"] == SLACK_PROVIDER:
            # Slack stores its long-lived user token in the same sealed column; `auth.revoke`
            # needs no scope because the token revokes itself.
            async with httpx.AsyncClient(timeout=15) as http:
                try:
                    provider_revoked = await slack_revoke(http, refresh)
                except SlackAPIError as exc:
                    return _error(f"Slack did not confirm revocation: {exc}", 502)
        elif row["provider"] == CALENDLY_PROVIDER:
            # Revoking the refresh token ends the whole grant (RFC 7009), which is what the user
            # asked for; revoking only the access token would leave the grant renewable.
            async with httpx.AsyncClient(timeout=15) as http:
                try:
                    provider_revoked = await calendly_revoke_token(
                        http, CalendlyOAuthConfig.from_env(), token=refresh
                    )
                except CalendlyConfigError as exc:
                    return _error(str(exc), 503, exc.hint)
                except CalendlyAPIError as exc:
                    return _error(f"Calendly did not confirm revocation: {exc}", 502)
        elif row["provider"] == SENTRY_PROVIDER:
            # A Sentry public integration has no revocation endpoint for the grant itself: removing
            # the installation is what revokes it. False means Sentry still has it in
            # `pending_deletion` after an uninstall performed inside Sentry; the local grant is
            # discarded either way and the remote one is already unusable.
            installation_id = str(row["external_id"] or "")
            if not installation_id:
                provider_revoked = False
            else:
                async with httpx.AsyncClient(timeout=15) as http:
                    try:
                        provider_revoked = await sentry_uninstall(
                            http, SentryOAuthConfig.from_env(), installation_id=installation_id
                        )
                    except SentryConfigError as exc:
                        return _error(str(exc), 503, exc.hint)
                    except SentryAPIError as exc:
                        return _error(f"Sentry did not confirm revocation: {exc}", 502)
        elif row["provider"] == ZOOM_PROVIDER:
            # Revoking the refresh token ends the whole grant: Zoom invalidates the access and the
            # refresh token together, which is what the user asked for. Revoking only the access
            # token would leave the grant renewable.
            async with httpx.AsyncClient(timeout=15) as http:
                try:
                    provider_revoked = await zoom_revoke_token(
                        http, ZoomOAuthConfig.from_env(), token=refresh
                    )
                except ZoomConfigError as exc:
                    return _error(str(exc), 503, exc.hint)
                except ZoomAPIError as exc:
                    return _error(f"Zoom did not confirm revocation: {exc}", 502)
        elif row["provider"] == GITHUB_PROVIDER:
            # GitHub's revocation endpoint is an *application* endpoint: it takes the access token
            # and ends that token's whole refresh chain, which is what the user asked for.
            try:
                access = decrypt_secret(
                    row["encrypted_access_token"] or "", row["organization_id"]
                )
            except SecretBoxError:
                return _error("Stored credential is unreadable; contact an administrator.", 500)
            if not access:
                provider_revoked = True  # nothing usable is stored; the local row is the only grant
            else:
                async with httpx.AsyncClient(timeout=15) as http:
                    try:
                        provider_revoked = await github_revoke_grant(
                            http, GitHubAppConfig.from_env(), access_token=access
                        )
                    except GitHubConfigError as exc:
                        return _error(str(exc), 503, exc.hint)
                    except GitHubAPIError as exc:
                        return _error(f"GitHub did not confirm revocation: {exc}", 502)
        elif row["provider"] == GRAFANA_PROVIDER:
            # A Viewer token cannot revoke the service-account token it is using: that needs
            # Admin on the Grafana side. Discarding the sealed copy here is all Hoja can do, so the
            # user is told, in the same response, to delete the token in Grafana.
            provider_revoked = False
        # Notion exposes no revocation endpoint: the credential can only be discarded here, and the
        # user must remove Hoja from the workspace's connections in Notion to invalidate it there.

        with engine.begin() as conn:
            conn.execute(
                update(connector_account)
                .where(connector_account.c.id == account_id)
                .values(
                    status="revoked",
                    revoked_at=_now(),
                    updated_at=_now(),
                    encrypted_refresh_token="",
                    encrypted_access_token="",
                    access_token_expires_at="",
                )
            )
        return JSONResponse(
            {
                "revoked": True,
                "providerRevoked": provider_revoked,
                "hint": None
                if provider_revoked
                else (
                    "Sentry is still finishing the uninstall; it clears itself in about ten "
                    "minutes. The connection is already unused here."
                    if row["provider"] == SENTRY_PROVIDER
                    else "Delete the service-account token in Grafana (Administration > Users and "
                    "access > Service accounts) to invalidate it there."
                    if row["provider"] == GRAFANA_PROVIDER
                    else "Remove Hoja in Notion Settings > Connections to invalidate the token there."
                ),
            }
        )

    async def actions_list(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    select(connector_action)
                    .where(
                        and_(
                            connector_action.c.organization_id == org,
                            connector_action.c.requested_by_user_id == uid,
                            connector_action.c.status == "pending",
                        )
                    )
                    .order_by(connector_action.c.created_at)
                )
                .mappings()
                .all()
            )
        return JSONResponse(
            {
                "actions": [
                    {
                        "id": row["id"],
                        "tool": row["tool"],
                        "args": json.loads(row["args_json"]),
                        "createdAt": row["created_at"],
                    }
                    for row in rows
                ]
            }
        )

    async def action_approve(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        action_id = request.path_params["action_id"]
        if execute_write is None:
            return _error("No connector write tools are enabled on this server.", 409)
        status = await approve_action(engine, org, uid, action_id, execute_write)
        if status is None:
            return _error("Action not found or no longer pending.", 404)
        return JSONResponse({"status": status})

    async def action_reject(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        action_id = request.path_params["action_id"]
        if not reject_action(engine, org, uid, action_id):
            return _error("Action not found or no longer pending.", 404)
        return JSONResponse({"status": "rejected"})

    return [
        Route("/api/connectors/config", connector_config, methods=["GET"]),
        Route("/api/connectors/google/start", google_start, methods=["GET"]),
        Route("/api/connectors/google/callback", google_callback, methods=["GET"]),
        Route("/api/connectors/notion/start", notion_start, methods=["GET"]),
        Route("/api/connectors/notion/callback", notion_callback, methods=["GET"]),
        Route("/api/connectors/slack/start", slack_start, methods=["GET"]),
        Route("/api/connectors/slack/callback", slack_callback, methods=["GET"]),
        Route("/api/connectors/calendly/start", calendly_start, methods=["GET"]),
        # The registered callback (broker in a multi-tenant deployment) and the tenant's redeem.
        Route(CALENDLY_REDIRECT_PATH, calendly_callback, methods=["GET"]),
        Route("/api/connectors/calendly/finish", calendly_finish, methods=["GET"]),
        Route("/api/connectors/sentry/start", sentry_start, methods=["GET"]),
        # The registered Redirect URL (broker in a multi-tenant deployment) and the tenant redeem.
        Route(SENTRY_REDIRECT_PATH, sentry_callback, methods=["GET"]),
        Route(SENTRY_FINISH_PATH, sentry_finish, methods=["GET"]),
        # Lifecycle events only (installation.created/deleted); no event subscriptions exist.
        Route(SENTRY_WEBHOOK_PATH, sentry_webhook, methods=["POST"]),
        Route("/api/connectors/zoom/start", zoom_start, methods=["GET"]),
        # Zoom's allow list carries one callback per tenant, so the tenant redeems its own code.
        Route(ZOOM_REDIRECT_PATH, zoom_callback, methods=["GET"]),
        Route("/api/connectors/github/start", github_start, methods=["GET"]),
        # GitHub's callback allow list also carries one URL per tenant.
        Route(GITHUB_REDIRECT_PATH, github_callback, methods=["GET"]),
        # Grafana has no OAuth callback: the customer pastes an instance URL and a token.
        Route("/api/connectors/grafana/validate", grafana_validate, methods=["POST"]),
        Route("/api/connectors/grafana/connect", grafana_connect, methods=["POST"]),
        Route("/api/connectors/accounts", accounts_list, methods=["GET"]),
        Route("/api/connectors/accounts/{account_id}/revoke", account_revoke, methods=["POST"]),
        Route("/api/connectors/actions", actions_list, methods=["GET"]),
        Route("/api/connectors/actions/{action_id}/approve", action_approve, methods=["POST"]),
        Route("/api/connectors/actions/{action_id}/reject", action_reject, methods=["POST"]),
        # Incoming GitHub App deliveries (linking), not part of the OAuth connect flow.
        *github_webhook_routes(engine),
    ]
