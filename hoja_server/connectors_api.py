"""Connector routes: connect, callback, list, and revoke connected accounts.

Pending OAuth state (user, org, scope, PKCE verifier, expiry) is carried in an AES-GCM sealed
`state` value, so the callback cannot be forged; a hashed nonce in `oauth_state_nonce` makes each state single-use.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from google_workspace import ConnectorAuthError, ConnectorConfigError, GoogleOAuthConfig
from google_workspace.oauth import (
    ADDON_SCOPES,
    READ_SCOPES,
    authorization_url,
    capabilities_for,
    exchange_code,
    merge_scopes,
    pkce_pair,
)
from google_workspace.principal import sign_principal
from notion import ConnectorConfigError as NotionConfigError
from notion import NotionOAuthConfig
from notion.errors import ConnectorAPIError as NotionAPIError
from notion.errors import ConnectorAuthError as NotionAuthError
from notion.oauth import authorization_url as notion_authorization_url
from notion.oauth import exchange_code as notion_exchange_code
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

from hoja_server.connector_actions import Executor, approve_action, reject_action
from hoja_server.db import connector_account, connector_action, oauth_state_nonce
from hoja_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret

PROVIDER = "google_workspace"
NOTION_PROVIDER = "notion"
SLACK_PROVIDER = "slack"
STATE_TTL_SECONDS = 600
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

def _consume_state(engine: Engine, nonce: str, expires_at: int) -> bool:
    """Mark an OAuth state nonce as used. False if it was already used (by any worker)."""
    if not nonce:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(oauth_state_nonce.delete().where(oauth_state_nonce.c.expires_at < time.time()))
            conn.execute(oauth_state_nonce.insert().values(
                nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(), expires_at=float(expires_at)))
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
        "scope": "org" if row["owner_user_id"] is None else "user",
        "ownerUserId": row["owner_user_id"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def connector_binding_for(engine: Engine, org: str, uid: str):
    """Return the MCP binding for this user when they have an active connector account.

    The connector MCP service registers the Google, Notion and Slack tools on one server, so one
    binding covers any provider (or several).
    """
    url = os.environ.get("HOJA_CONNECTOR_MCP_URL", "").strip()
    key = os.environ.get("HOJA_CONNECTOR_SIGNING_KEY", "").strip()
    if not url or not key:
        return None
    with engine.connect() as conn:
        active = conn.execute(
            select(connector_account.c.id).where(
                and_(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider.in_(
                        (PROVIDER, NOTION_PROVIDER, SLACK_PROVIDER)
                    ),
                    connector_account.c.status == "active",
                    or_(
                        connector_account.c.owner_user_id == uid,
                        connector_account.c.owner_user_id.is_(None),
                    ),
                )
            ).limit(1)
        ).first()
    if active is None:
        return None
    from hoja_server.agent_chat import ConnectorBinding

    return ConnectorBinding(
        url=url,
        principal=sign_principal(uid, org, "read", key=key),
    )


def make_write_executor(engine: Engine, http: httpx.AsyncClient, config: GoogleOAuthConfig):
    """Build the approval executor. It runs an approved write only with the account the action was
    queued against, and only while that account is still active and owned by the same requester."""

    async def execute(tool: str, args: dict[str, Any], account_id: str, requester: str) -> str:
        from google_workspace.oauth import refresh_access_token
        from google_workspace.tools import AccountGrant
        from google_workspace.writes import run_write

        with engine.connect() as conn:
            row = conn.execute(
                select(connector_account).where(
                    and_(
                        connector_account.c.id == account_id,
                        connector_account.c.status == "active",
                    )
                )
            ).mappings().one_or_none()
        if row is None or row["owner_user_id"] != requester:
            raise ConnectorAuthError("The account for this action is no longer available.")
        refresh = decrypt_secret(row["encrypted_refresh_token"])

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


def connector_routes(engine: Engine, store, auth, execute_write: Executor | None = None) -> list[Route]:
    """Build connector routes. `auth(request)` returns (user_id, org_id, identity_id, is_browser)."""

    async def _require_user(request: Request):
        uid, org, _identity, _browser = auth(request)
        if not uid:
            return None, None, _error("Authentication required", 401)
        return uid, org, None

    def _is_admin(org: str, uid: str) -> bool:
        actor = store.get_user(org, uid)
        return bool(actor and actor.get("admin"))

    async def google_start(request: Request):
        """Begin the Google OAuth flow.

        With `?addon=meet` this is an *upgrade* of the Google grant the user already has, not a
        second account: the request carries only the add-on's scopes, and `include_granted_scopes`
        makes Google extend the existing grant. That is what keeps one Google account to one row,
        one token and one Disconnect.
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
                    "Connect your Google account before adding Google Meet access.",
                    409,
                    hint="Use Connect Google first; the Meet permission is added to that grant.",
                )
        try:
            config = GoogleOAuthConfig.from_env()
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
                )
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
        try:
            pending = json.loads(decrypt_secret(sealed))
            config = GoogleOAuthConfig.from_env()
        except (SecretBoxError, ValueError, ConnectorConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        if pending["scope"] == "org" and not _is_admin(org, uid):
            return _error("Only workspace admins can connect organization accounts.", 403)
        if not _consume_state(engine, str(pending.get("nonce", "")), int(pending["exp"])):
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
                    "Google Meet access was not granted. Tick the Meet permission and try again.",
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
                    f"Meet access was added to {email}, which is not a connected account here."
                    " Connect that Google account first, then add Meet access.",
                    409,
                    hint="Use Connect Google for that account, then add Meet access to it.",
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
                "encrypted_refresh_token": encrypt_secret(tokens.refresh_token),
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
        marker = "meet-connected" if addon else "google-connected"
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
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                )
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
        try:
            pending = json.loads(decrypt_secret(sealed))
            config = NotionOAuthConfig.from_env()
        except (SecretBoxError, ValueError, NotionConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        if not _consume_state(engine, str(pending.get("nonce", "")), int(pending["exp"])):
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
            "encrypted_refresh_token": encrypt_secret(token.access_token),
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
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "exp": int(time.time()) + STATE_TTL_SECONDS,
                        "nonce": uuid.uuid4().hex,
                    }
                )
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
        try:
            pending = json.loads(decrypt_secret(sealed))
            config = SlackOAuthConfig.from_env()
        except (SecretBoxError, ValueError, SlackConfigError) as exc:
            return _error("Invalid or expired connection request.", 400, getattr(exc, "hint", None))
        if pending.get("exp", 0) < time.time():
            return _error("Connection request expired. Start again.", 400)
        if pending.get("uid") != uid or pending.get("org") != org:
            return _error("Connection request belongs to a different session.", 403)
        if not _consume_state(engine, str(pending.get("nonce", "")), int(pending["exp"])):
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
            "encrypted_refresh_token": encrypt_secret(token.access_token),
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
        return JSONResponse(
            {
                "googleWorkspace": google_available,
                "googleWorkspaceAgent": agent_available,
                "notion": notion_available,
                "notionAgent": notion_agent,
                "slack": slack_available,
                "slackAgent": slack_agent,
            },
            headers={"cache-control": "no-store"},
        )

    async def accounts_list(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        with engine.connect() as conn:
            rows = conn.execute(
                select(connector_account).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.status == "active",
                        or_(
                            connector_account.c.owner_user_id == uid,
                            connector_account.c.owner_user_id.is_(None),
                        ),
                    )
                ).order_by(connector_account.c.created_at)
            ).mappings().all()
        return JSONResponse({"accounts": [_serialize(row) for row in rows]})

    async def account_revoke(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        account_id = request.path_params["account_id"]
        with engine.connect() as conn:
            row = conn.execute(
                select(connector_account).where(
                    and_(
                        connector_account.c.id == account_id,
                        connector_account.c.organization_id == org,
                        connector_account.c.status == "active",
                    )
                )
            ).mappings().one_or_none()
        if row is None:
            return _error("Connected account not found.", 404)
        is_owner = row["owner_user_id"] == uid
        if not (is_owner or (row["owner_user_id"] is None and _is_admin(org, uid))):
            # Same response as a missing account, so callers cannot probe for other users' accounts.
            return _error("Connected account not found.", 404)

        try:
            refresh = decrypt_secret(row["encrypted_refresh_token"])
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
        # Notion exposes no revocation endpoint: the credential can only be discarded here, and the
        # user must remove Hoja from the workspace's connections in Notion to invalidate it there.

        with engine.begin() as conn:
            conn.execute(
                update(connector_account)
                .where(connector_account.c.id == account_id)
                .values(status="revoked", revoked_at=_now(), updated_at=_now(), encrypted_refresh_token="")
            )
        return JSONResponse(
            {
                "revoked": True,
                "providerRevoked": provider_revoked,
                "hint": None
                if provider_revoked
                else "Remove Hoja in Notion Settings > Connections to invalidate the token there.",
            }
        )

    async def actions_list(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        with engine.connect() as conn:
            rows = conn.execute(
                select(connector_action)
                .where(
                    and_(
                        connector_action.c.organization_id == org,
                        connector_action.c.requested_by_user_id == uid,
                        connector_action.c.status == "pending",
                    )
                )
                .order_by(connector_action.c.created_at)
            ).mappings().all()
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
        Route("/api/connectors/accounts", accounts_list, methods=["GET"]),
        Route("/api/connectors/accounts/{account_id}/revoke", account_revoke, methods=["POST"]),
        Route("/api/connectors/actions", actions_list, methods=["GET"]),
        Route("/api/connectors/actions/{action_id}/approve", action_approve, methods=["POST"]),
        Route("/api/connectors/actions/{action_id}/reject", action_reject, methods=["POST"]),
    ]
