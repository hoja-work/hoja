"""Google Workspace connector routes: connect, callback, list, and revoke connected accounts.

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
    READ_SCOPES,
    authorization_url,
    exchange_code,
    pkce_pair,
)
from google_workspace.principal import sign_principal
from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from cliniar_server.connector_actions import Executor, approve_action, reject_action
from cliniar_server.db import connector_account, connector_action, oauth_state_nonce
from cliniar_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret

PROVIDER = "google_workspace"
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
        "scope": "org" if row["owner_user_id"] is None else "user",
        "ownerUserId": row["owner_user_id"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def connector_binding_for(engine: Engine, org: str, uid: str):
    """Return the MCP binding for this user when they have an active Google account, else None."""
    url = os.environ.get("CLINIAR_CONNECTOR_MCP_URL", "").strip()
    key = os.environ.get("CLINIAR_CONNECTOR_SIGNING_KEY", "").strip()
    if not url or not key:
        return None
    with engine.connect() as conn:
        active = conn.execute(
            select(connector_account.c.id).where(
                and_(
                    connector_account.c.organization_id == org,
                    connector_account.c.provider == PROVIDER,
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
    from cliniar_server.agent_chat import ConnectorBinding

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
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        scope = request.query_params.get("scope", "user")
        if scope not in {"user", "org"}:
            return _error("scope must be 'user' or 'org'", 400)
        if scope == "org" and not _is_admin(org, uid):
            return _error("Only workspace admins can connect organization accounts.", 403)
        try:
            config = GoogleOAuthConfig.from_env()
            verifier, challenge = pkce_pair()
            state = encrypt_secret(
                json.dumps(
                    {
                        "uid": uid,
                        "org": org,
                        "scope": scope,
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
            scopes=list(READ_SCOPES.values()),
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

        row = {
            "id": uuid.uuid4().hex,
            "organization_id": org,
            "owner_user_id": None if pending["scope"] == "org" else uid,
            "provider": PROVIDER,
            "external_account": email,
            "scopes": tokens.scope or " ".join(READ_SCOPES.values()),
            "encrypted_refresh_token": encrypt_secret(tokens.refresh_token),
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
        }
        with engine.begin() as conn:
            conn.execute(connector_account.insert().values(**row))
        return RedirectResponse("/?connector=google-connected", status_code=302)

    async def accounts_list(request: Request):
        uid, org, failure = await _require_user(request)
        if failure:
            return failure
        with engine.connect() as conn:
            rows = conn.execute(
                select(connector_account).where(
                    and_(
                        connector_account.c.organization_id == org,
                        connector_account.c.provider == PROVIDER,
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
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.post(GOOGLE_REVOKE_URL, data={"token": refresh})
        # Google answers 200 on success and 400 if the token is already invalid; both end the grant.
        if response.status_code not in (200, 400):
            return _error("Google did not confirm revocation. Try again.", 502)

        with engine.begin() as conn:
            conn.execute(
                update(connector_account)
                .where(connector_account.c.id == account_id)
                .values(status="revoked", revoked_at=_now(), updated_at=_now(), encrypted_refresh_token="")
            )
        return JSONResponse({"revoked": True})

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
        Route("/api/connectors/google/start", google_start, methods=["GET"]),
        Route("/api/connectors/google/callback", google_callback, methods=["GET"]),
        Route("/api/connectors/accounts", accounts_list, methods=["GET"]),
        Route("/api/connectors/accounts/{account_id}/revoke", account_revoke, methods=["POST"]),
        Route("/api/connectors/actions", actions_list, methods=["GET"]),
        Route("/api/connectors/actions/{action_id}/approve", action_approve, methods=["POST"]),
        Route("/api/connectors/actions/{action_id}/reject", action_reject, methods=["POST"]),
    ]
