"""OAuth-backed remote Hoja MCP server.

Authorization and tokens are persisted by the existing SQLAlchemy database. The
MCP SDK owns protocol serialization, metadata, DCR, PKCE verification, and HTTP
transport; this module owns Hoja identity binding and consent.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import func, select, update
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from cliniar_server import db
from cliniar_server.mcp_tools import INSTRUCTIONS as MCP_INSTRUCTIONS

READ_SCOPE = "hoja:read"
WRITE_SCOPE = "hoja:write"
ALL_SCOPES = [READ_SCOPE, WRITE_SCOPE]
ACCESS_TTL = 900
REFRESH_TTL = 60 * 60 * 24 * 30
CODE_TTL = 300
MAX_REDIRECT_URIS = 5
MAX_REDIRECT_URI_LENGTH = 2048
MAX_CLIENT_NAME_LENGTH = 200
MAX_OAUTH_CLIENTS = 10_000
MAX_REGISTRATIONS_PER_HOUR = 100
_REGISTRATION_WINDOW_SECONDS = 3600


def _take_registration_slot(engine) -> None:
    """Database-wide sliding-window limit on dynamic client registrations (shared across workers)."""
    now = time.time()
    key_hash = db.rate_limit_key_hash("global")
    with engine.begin() as conn:
        conn.execute(
            db.rate_limit_event.delete().where(
                db.rate_limit_event.c.bucket == "oauth_dcr",
                db.rate_limit_event.c.created_at <= now - _REGISTRATION_WINDOW_SECONDS,
            )
        )
        count = conn.execute(
            select(func.count())
            .select_from(db.rate_limit_event)
            .where(db.rate_limit_event.c.bucket == "oauth_dcr")
        ).scalar_one()
        if count >= MAX_REGISTRATIONS_PER_HOUR:
            raise RegistrationError(
                "invalid_client_metadata", "Too many client registrations; try again later."
            )
        conn.execute(
            db.rate_limit_event.insert().values(bucket="oauth_dcr", key_hash=key_hash, created_at=now)
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _secret() -> str:
    return secrets.token_urlsafe(48)


class HojaOAuthProvider(OAuthAuthorizationServerProvider):
    """SDK OAuth provider. Raw bearer values are never persisted."""

    def __init__(self, engine, store, trusted_proxy: bool = False):
        self.engine = engine
        self.store = store
        self.trusted_proxy = trusted_proxy
        from cliniar_server.writer import Writer

        self.writer = Writer(engine)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.engine.connect() as conn:
            raw = conn.execute(
                select(db.oauth_client.c.client_json).where(
                    db.oauth_client.c.client_id == client_id
                )
            ).scalar_one_or_none()
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not self.trusted_proxy:
            raise RegistrationError(
                "invalid_client_metadata", "MCP OAuth is not enabled on this deployment."
            )
        if not client_info.redirect_uris:
            raise RegistrationError(
                "invalid_redirect_uri", "At least one redirect URI is required."
            )
        if len(client_info.redirect_uris) > MAX_REDIRECT_URIS:
            raise RegistrationError("invalid_redirect_uri", "Too many redirect URIs.")
        if client_info.client_name and len(client_info.client_name) > MAX_CLIENT_NAME_LENGTH:
            raise RegistrationError("invalid_client_metadata", "client_name is too long.")
        if client_info.token_endpoint_auth_method not in (None, "none"):
            raise RegistrationError(
                "invalid_client_metadata", "Only public OAuth clients are supported."
            )
        for uri in client_info.redirect_uris:
            text = str(uri)
            if len(text) > MAX_REDIRECT_URI_LENGTH:
                raise RegistrationError("invalid_redirect_uri", "Redirect URI is too long.")
            if uri.scheme != "https" and not _is_loopback_uri(text):
                raise RegistrationError(
                    "invalid_redirect_uri", "Redirect URI must use HTTPS or native loopback."
                )
            if uri.username or uri.password or uri.fragment:
                raise RegistrationError(
                    "invalid_redirect_uri", "Redirect URI contains a forbidden component."
                )
        if client_info.scope:
            requested = set(client_info.scope.split())
            if not requested.issubset(ALL_SCOPES):
                raise RegistrationError("invalid_client_metadata", "Unsupported OAuth scope.")
        clean = client_info.model_copy(
            update={"client_secret": None, "token_endpoint_auth_method": "none"}
        )
        _take_registration_slot(self.engine)
        with self.engine.begin() as conn:
            total = conn.execute(select(func.count()).select_from(db.oauth_client)).scalar_one()
            if total >= MAX_OAUTH_CLIENTS:
                raise RegistrationError(
                    "invalid_client_metadata", "Client registration limit reached."
                )
            conn.execute(
                db.oauth_client.insert().values(
                    client_id=clean.client_id,
                    client_json=clean.model_dump_json(),
                    created_at=db.now_iso(),
                )
            )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not self.trusted_proxy:
            raise AuthorizeError(
                "temporarily_unavailable", "MCP OAuth is not enabled on this deployment."
            )
        if params.resource and self._resource_url() != params.resource.rstrip("/"):
            raise AuthorizeError("invalid_target", "Unknown resource.")
        scopes = params.scopes or [READ_SCOPE]
        if not set(scopes).issubset(ALL_SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scope.")
        # OAuth SDK validates exact registered redirect and requires S256 before this hook.
        pending_id, csrf_token = _secret(), _secret()
        expires = _now() + timedelta(minutes=5)
        with self.engine.begin() as conn:
            conn.execute(db.oauth_pending.insert().values(
                pending_hash=_digest(pending_id), client_id=client.client_id,
                scopes=json.dumps(scopes), code_challenge=params.code_challenge,
                redirect_uri=str(params.redirect_uri),
                redirect_explicit=params.redirect_uri_provided_explicitly,
                state=params.state, csrf_token=csrf_token, resource=params.resource,
                expires_at=_iso(expires), consumed_at=None,
            ))
        return f"/oauth/consent?{urlencode({'pending': pending_id})}"

    async def load_authorization_code(self, client, authorization_code: str):
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(db.oauth_authorization).where(
                        db.oauth_authorization.c.code_hash == _digest(authorization_code),
                        db.oauth_authorization.c.client_id == client.client_id,
                        db.oauth_authorization.c.consumed_at.is_(None),
                    )
                )
                .mappings()
                .first()
            )
        if not row or row["expires_at"] <= _iso(_now()):
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=json.loads(row["scopes"]),
            expires_at=_parse_dt(row["expires_at"]).timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=True,
            subject=json.dumps({"user_id": row["user_id"], "org_id": row["organization_id"]}),
            resource=row["resource"],
        )

    async def exchange_authorization_code(
        self, client, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        now = _now()
        code_hash = _digest(authorization_code.code)
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.oauth_authorization)
                    .where(
                        db.oauth_authorization.c.code_hash == code_hash,
                        db.oauth_authorization.c.client_id == client.client_id,
                        db.oauth_authorization.c.consumed_at.is_(None),
                        db.oauth_authorization.c.expires_at > _iso(now),
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not row:
                raise TokenError("invalid_grant", "Authorization code is invalid or already used.")
            consumed = conn.execute(
                update(db.oauth_authorization)
                .where(
                    db.oauth_authorization.c.code_hash == code_hash,
                    db.oauth_authorization.c.consumed_at.is_(None),
                )
                .values(consumed_at=_iso(now))
            )
            if consumed.rowcount != 1:
                raise TokenError("invalid_grant", "Authorization code is invalid or already used.")
            return self._mint_pair(
                conn,
                row["client_id"],
                row["user_id"],
                row["organization_id"],
                json.loads(row["scopes"]),
                authorization_code.resource,
            )

    async def load_refresh_token(self, client, refresh_token: str):
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.oauth_refresh_token).where(
                        db.oauth_refresh_token.c.token_hash == _digest(refresh_token),
                        db.oauth_refresh_token.c.client_id == client.client_id,
                    )
                )
                .mappings()
                .first()
            )
            if row and row["revoked_at"] is not None:
                # The SDK rejects revoked tokens before exchange_refresh_token runs, so
                # reuse of a rotated/revoked token must be handled here.
                now = _iso(_now())
                for target in (db.oauth_access_token, db.oauth_refresh_token):
                    conn.execute(
                        update(target)
                        .where(target.c.grant_id == row["grant_id"], target.c.revoked_at.is_(None))
                        .values(revoked_at=now)
                    )
                return None
        if not row or row["expires_at"] <= _iso(_now()):
            return None
        subject = json.dumps({"user_id": row["user_id"], "org_id": row["organization_id"], "grant_id": row["grant_id"]})
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]),
            expires_at=int(_parse_dt(row["expires_at"]).timestamp()),
            resource=row["resource"],
            subject=subject,
        )

    async def exchange_refresh_token(
        self, client, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        original = set(refresh_token.scopes)
        requested = set(scopes or refresh_token.scopes)
        if not requested.issubset(original):
            raise TokenError("invalid_scope", "Refresh cannot increase granted scopes.")
        replay_detected = False
        token = None
        with self.engine.begin() as conn:
            existing_grant = conn.execute(select(db.oauth_refresh_token.c.grant_id).where(
                db.oauth_refresh_token.c.token_hash == _digest(refresh_token.token),
                db.oauth_refresh_token.c.client_id == client.client_id,
            )).scalar_one_or_none()
            active_grant = conn.execute(select(db.oauth_refresh_token.c.grant_id).where(
                db.oauth_refresh_token.c.token_hash == _digest(refresh_token.token),
                db.oauth_refresh_token.c.client_id == client.client_id,
                db.oauth_refresh_token.c.revoked_at.is_(None),
                db.oauth_refresh_token.c.expires_at > _iso(_now()),
            )).scalar_one_or_none()
            result = conn.execute(
                update(db.oauth_refresh_token)
                .where(
                    db.oauth_refresh_token.c.token_hash == _digest(refresh_token.token),
                    db.oauth_refresh_token.c.client_id == client.client_id,
                    db.oauth_refresh_token.c.revoked_at.is_(None),
                    db.oauth_refresh_token.c.expires_at > _iso(_now()),
                )
                .values(revoked_at=_iso(_now()))
            )
            if active_grant is None and existing_grant:
                now = _iso(_now())
                conn.execute(update(db.oauth_access_token).where(
                    db.oauth_access_token.c.grant_id == existing_grant,
                    db.oauth_access_token.c.revoked_at.is_(None),
                ).values(revoked_at=now))
                conn.execute(update(db.oauth_refresh_token).where(
                    db.oauth_refresh_token.c.grant_id == existing_grant,
                    db.oauth_refresh_token.c.revoked_at.is_(None),
                ).values(revoked_at=now))
                replay_detected = True
            elif result.rowcount == 1:
                claims = json.loads(refresh_token.subject or "{}")
                token = self._mint_pair(
                    conn,
                    client.client_id,
                    claims["user_id"],
                    claims["org_id"],
                    sorted(requested),
                    refresh_token.resource,
                    grant_id=claims["grant_id"],
                )
        if replay_detected or token is None:
            raise TokenError("invalid_grant", "Refresh token is invalid or already used.")
        return token

    async def load_access_token(self, token: str):
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(db.oauth_access_token).where(
                        db.oauth_access_token.c.token_hash == _digest(token),
                        db.oauth_access_token.c.revoked_at.is_(None),
                    )
                )
                .mappings()
                .first()
            )
        if not row or row["expires_at"] <= _iso(_now()):
            return None
        subject = json.dumps({"user_id": row["user_id"], "org_id": row["organization_id"]})
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]),
            expires_at=int(_parse_dt(row["expires_at"]).timestamp()),
            resource=row["resource"],
            subject=subject,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        table = db.oauth_access_token if isinstance(token, AccessToken) else db.oauth_refresh_token
        grant_id = None
        with self.engine.begin() as conn:
            grant_id = conn.execute(
                select(table.c.grant_id).where(table.c.token_hash == _digest(token.token))
            ).scalar_one_or_none()
            if grant_id:
                for target in (db.oauth_access_token, db.oauth_refresh_token):
                    conn.execute(
                        update(target)
                        .where(target.c.grant_id == grant_id, target.c.revoked_at.is_(None))
                        .values(revoked_at=_iso(_now()))
                    )

    def _mint_pair(
        self,
        conn,
        client_id: str,
        user_id: str,
        org_id: str,
        scopes: list[str],
        resource: str | None,
        *,
        grant_id: str | None = None,
    ) -> OAuthToken:
        access, refresh = _secret(), _secret()
        grant_id = grant_id or _secret()
        access_expires, refresh_expires = (
            _now() + timedelta(seconds=ACCESS_TTL),
            _now() + timedelta(seconds=REFRESH_TTL),
        )
        common = dict(
            client_id=client_id,
            user_id=user_id,
            organization_id=org_id,
            scopes=json.dumps(scopes),
            grant_id=grant_id,
            resource=resource,
        )
        conn.execute(
            db.oauth_access_token.insert().values(
                token_hash=_digest(access), **common, expires_at=_iso(access_expires)
            )
        )
        conn.execute(
            db.oauth_refresh_token.insert().values(
                token_hash=_digest(refresh), **common, expires_at=_iso(refresh_expires)
            )
        )
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=ACCESS_TTL,
            scope=" ".join(scopes),
        )

    def _resource_url(self) -> str:
        return _public_origin(self.trusted_proxy) + "/mcp"

    async def finish_consent(self, request: Request, pending_id: str, accepted: bool, csrf_token: str):
        auth = request.scope.get("hoja_auth")
        now = _iso(_now())
        with self.engine.begin() as conn:
            row = conn.execute(select(db.oauth_pending).where(
                db.oauth_pending.c.pending_hash == _digest(pending_id),
                db.oauth_pending.c.consumed_at.is_(None),
                db.oauth_pending.c.expires_at > now,
            )).mappings().first()
            if not auth or not row or not secrets.compare_digest(csrf_token, row["csrf_token"]):
                return JSONResponse({"error": "Authorization session expired; restart from the MCP client."}, status_code=400)
            if row["user_id"] and (row["user_id"] != auth.get("user_id") or row["organization_id"] != auth.get("org_id")):
                return JSONResponse({"error": "Authorization session expired; restart from the MCP client."}, status_code=400)
            changed = conn.execute(update(db.oauth_pending).where(
                db.oauth_pending.c.pending_hash == row["pending_hash"],
                db.oauth_pending.c.consumed_at.is_(None), db.oauth_pending.c.expires_at > now,
            ).values(consumed_at=now, user_id=auth["user_id"], organization_id=auth["org_id"])).rowcount
            if changed != 1:
                return JSONResponse({"error": "Authorization request already used."}, status_code=400)
            if not accepted:
                return JSONResponse({"error": "Authorization was denied."}, status_code=403)
            code = _secret()
            conn.execute(db.oauth_authorization.insert().values(
                code_hash=_digest(code), client_id=row["client_id"], user_id=auth["user_id"],
                organization_id=auth["org_id"], scopes=row["scopes"],
                code_challenge=row["code_challenge"], redirect_uri=row["redirect_uri"],
                resource=row["resource"], expires_at=_iso(_now()+timedelta(seconds=CODE_TTL)), consumed_at=None,
            ))
        query = {"code": code}
        if row["state"]:
            query["state"] = row["state"]
        return RedirectResponse(row["redirect_uri"] + ("&" if "?" in row["redirect_uri"] else "?") + urlencode(query))


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _is_loopback_uri(uri: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme != "http" or parsed.hostname is None:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _public_origin(trust_proxy: bool, public_host: str | None = None) -> str:
    # Proxy headers are only honored when operator explicitly enables trust.
    import os

    if not trust_proxy:
        raise RuntimeError("Hoja MCP OAuth requires explicit trusted proxy configuration.")
    host = (public_host if public_host is not None else os.environ.get("HOJA_TRUSTED_PUBLIC_HOST", "")).strip()
    scheme = os.environ.get("HOJA_TRUSTED_PUBLIC_SCHEME", "https").strip().lower()
    if not host or "/" in host or "@" in host or scheme != "https":
        raise RuntimeError("Trusted proxy public origin is not configured safely.")
    return f"https://{host}"


def build_server(provider: HojaOAuthProvider, public_host: str | None = None) -> MCPServer:
    from cliniar import __version__

    # Keep ordinary Hoja API/test startup independent of OAuth deployment config.
    # OAuth metadata is only served when trusted proxy origin config is supplied.
    public_origin = (
        _public_origin(provider.trusted_proxy, public_host) if provider.trusted_proxy else "https://mcp.invalid"
    )
    issuer = public_origin + "/mcp"
    server = MCPServer(
        name="hoja",
        instructions=MCP_INSTRUCTIONS,
        version=__version__,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=issuer,
            resource_server_url=issuer,
            validate_token_resource=True,
            required_scopes=[READ_SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=ALL_SCOPES, default_scopes=[READ_SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    from mcp.server.mcpserver.exceptions import ToolError

    from cliniar_server.mcp_tools import build_tools
    from cliniar_server.resolvers import build_schema

    def principal(write: bool) -> dict:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access = get_access_token()
        if access is None or not access.subject:
            raise ToolError("OAuth authentication is required.")
        granted = set(access.scopes)
        if write and WRITE_SCOPE not in granted:
            raise ToolError(
                f"This connection is read-only: the {WRITE_SCOPE} scope was not granted, so nothing was changed. "
                "Ask the user to reconnect the Hoja MCP with write access."
            )
        if READ_SCOPE not in granted:
            raise ToolError(f"OAuth scope {READ_SCOPE} is required.")
        try:
            identity = json.loads(access.subject)
            user_id, org_id = identity["user_id"], identity["org_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolError("OAuth identity is invalid.") from exc
        if not provider.store.membership_for_profile(org_id, user_id):
            raise ToolError("Active organization membership is required.")
        return {
            "store": provider.store,
            "writer": provider.writer,
            "org_id": org_id,
            "user_id": user_id,
            "identity_id": None,
            "browser_session": False,
            # Read-only grants must not trigger resolver-side reconciliation writes.
            "read_only": WRITE_SCOPE not in granted,
        }

    for tool in build_tools(build_schema(), principal):
        server._tool_manager._tools[tool.name] = tool

    @server.custom_route("/oauth/consent", methods=["GET", "POST"])
    async def consent(request: Request):
        pending_id = request.query_params.get("pending")
        auth = request.scope.get("hoja_auth")
        if not auth:
            response = RedirectResponse(
                "/auth/login?next=" + request.url.path + "%3Fpending%3D" + (pending_id or "")
            )
            return response
        if request.method == "POST":
            form = await request.form()
            return await provider.finish_consent(
                request,
                pending_id or "",
                form.get("decision") == "allow",
                str(form.get("csrf_token", "")),
            )
        with provider.engine.connect() as conn:
            pending = conn.execute(select(db.oauth_pending).where(
                db.oauth_pending.c.pending_hash == _digest(pending_id or ""),
                db.oauth_pending.c.consumed_at.is_(None), db.oauth_pending.c.expires_at > _iso(_now()),
            )).mappings().first()
        if pending and request.method == "GET":
            with provider.engine.begin() as conn:
                conn.execute(update(db.oauth_pending).where(
                    db.oauth_pending.c.pending_hash == pending["pending_hash"],
                    db.oauth_pending.c.consumed_at.is_(None),
                    db.oauth_pending.c.user_id.is_(None),
                ).values(user_id=auth["user_id"], organization_id=auth["org_id"]))
                pending = conn.execute(select(db.oauth_pending).where(
                    db.oauth_pending.c.pending_hash == pending["pending_hash"]
                )).mappings().first()
        client = await provider.get_client(pending["client_id"]) if pending else None
        if not pending or not client or pending["user_id"] != auth["user_id"] or pending["organization_id"] != auth["org_id"]:
            return JSONResponse({"error": "Authorization request expired."}, status_code=400)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Authorize Hoja MCP</title>"
            "<h1>Authorize MCP client</h1><p>Client: "
            + _escape(client.client_name or client.client_id)
            + "</p>"
            "<p>Workspace: " + _escape(auth.get("org_name", auth["org_id"])) + "</p>"
            "<p>Requested access: " + _escape(", ".join(pending["scopes"])) + "</p>"
            "<form method=post><input type=hidden name=csrf_token value=\""
            + _escape(pending["csrf_token"])
            + "\"><button name=decision value=allow>Allow</button>"
            "<button name=decision value=deny>Deny</button></form>"
        )

    return server


def _escape(value: str) -> str:
    import html

    return html.escape(value, quote=True)
