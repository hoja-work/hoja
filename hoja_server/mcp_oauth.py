"""OAuth-backed remote Hoja MCP server.

Authorization and tokens are persisted by the existing SQLAlchemy database. The
MCP SDK owns protocol serialization, metadata, DCR, PKCE verification, and HTTP
transport; this module owns Hoja identity binding and consent.
"""

from __future__ import annotations

import contextvars
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
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull, OAuthToken
from sqlalchemy import case, func, select, update
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from hoja_server import consent_pages, db, tenant_context
from hoja_server.db import FIRST_PARTY_CLIENT_ID
from hoja_server.denials import DENY_HINT
from hoja_server.mcp_tools import INSTRUCTIONS as MCP_INSTRUCTIONS

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
#: Dynamic registrations one client address may make per hour. Per address, not
#: per deployment: one noisy network must not lock every workspace out of DCR.
MAX_REGISTRATIONS_PER_IP_HOUR = 20
#: A backstop across all addresses, far above legitimate use.
MAX_REGISTRATIONS_PER_HOUR = 1000
_REGISTRATION_WINDOW_SECONDS = 3600
#: A registered client that never completed an authorization is deleted after this.
UNUSED_CLIENT_TTL_DAYS = 7
#: How long a person has between the client opening the browser and approving.
#: Covers a hosted sign-in round-trip (Google plus the workspace handoff).
PENDING_TTL_MINUTES = 15
#: After a refresh token is rotated, the immediately previous token still works
#: once within this window, so a client that lost the response (network drop,
#: two processes racing) is not treated as a replay that revokes the grant.
REFRESH_GRACE_SECONDS = 60

#: The caller's address for the request being handled, set by the HTTP endpoint
#: (which knows the trusted-proxy rules). The SDK calls ``register_client``
#: without the request, in the same task, so a context variable carries it.
REQUEST_CLIENT_IP: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hoja_mcp_client_ip", default=None
)


#: Facts about the HTTP request the SDK does not pass to the provider: the
#: caller's address, its User-Agent, and the ``device_name`` an app sent on
#: /authorize (the SDK's request model drops unknown parameters).
REQUEST_META: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hoja_mcp_request_meta", default=None)


def _meta() -> dict:
    return REQUEST_META.get() or {}

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_TTL = 600
DEVICE_INTERVAL = 5
#: No vowels (no words), no digits, nothing that reads like another letter.
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
MAX_DEVICE_LABEL = 64
#: last_used_at on an authorized app is written at most this often.
GRANT_TOUCH_SECONDS = 60


class HojaOAuthToken(OAuthToken):
    """The RFC 6749 token response plus who and where, so an app need not ask again."""

    hoja_workspace: dict | None = None
    hoja_user: dict | None = None


def _is_cli_loopback(uri: str) -> bool:
    """RFC 8252 7.3: a loopback redirect matches whatever port the app opened."""
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    return (
        parsed.scheme == "http"
        and parsed.hostname in ("127.0.0.1", "::1")
        and parsed.port is not None
        and parsed.path == "/callback"
        and not parsed.username and not parsed.password and not parsed.fragment
    )


class FirstPartyClient(OAuthClientInformationFull):
    """Hoja's own CLI: pre-registered, public, PKCE, any loopback port."""

    def validate_redirect_uri(self, redirect_uri):
        if redirect_uri is not None and _is_cli_loopback(str(redirect_uri)):
            return redirect_uri
        raise InvalidRedirectUriError(
            "The Hoja CLI redirects only to http://127.0.0.1:<port>/callback or http://[::1]:<port>/callback."
        )


FIRST_PARTY_CLIENT = FirstPartyClient(
    client_id=FIRST_PARTY_CLIENT_ID,
    client_name="Hoja CLI",
    redirect_uris=["http://127.0.0.1/callback"],
    token_endpoint_auth_method="none",
    grant_types=["authorization_code", "refresh_token", DEVICE_GRANT],
    response_types=["code"],
    scope=" ".join(ALL_SCOPES),
)


def device_label(raw: object) -> str | None:
    """The app's name for the device, made safe to store and show: one line, at most 64 characters."""
    if not isinstance(raw, str):
        return None
    text = " ".join("".join(ch for ch in raw if ch.isprintable()).split())
    return text[:MAX_DEVICE_LABEL] or None


def _user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(raw: object) -> str:
    text = "".join(ch for ch in str(raw or "").upper() if ch in USER_CODE_ALPHABET)
    return f"{text[:4]}-{text[4:]}" if len(text) == 8 else ""


def _take_registration_slot(engine, client_ip: str | None) -> None:
    """Sliding-window limit on dynamic client registrations, per client address (shared across workers)."""
    now = time.time()
    key_hash = db.rate_limit_key_hash(f"ip:{client_ip or 'unknown'}")
    with engine.begin() as conn:
        conn.execute(
            db.rate_limit_event.delete().where(
                db.rate_limit_event.c.bucket == "oauth_dcr",
                db.rate_limit_event.c.created_at <= now - _REGISTRATION_WINDOW_SECONDS,
            )
        )
        total, mine = conn.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((db.rate_limit_event.c.key_hash == key_hash, 1), else_=0)), 0),
            )
            .select_from(db.rate_limit_event)
            .where(db.rate_limit_event.c.bucket == "oauth_dcr")
        ).one()
        if mine >= MAX_REGISTRATIONS_PER_IP_HOUR or total >= MAX_REGISTRATIONS_PER_HOUR:
            raise RegistrationError(
                "invalid_client_metadata", "Too many client registrations; try again later."
            )
        conn.execute(
            db.rate_limit_event.insert().values(bucket="oauth_dcr", key_hash=key_hash, created_at=now)
        )


def _prune_unused_clients(conn) -> None:
    """Delete registrations that never reached an authorization code, once they are old.

    A client that completed one authorization has an ``oauth_authorization`` row
    (codes are kept after use), so it is never touched; neither is one with a
    consent still pending.
    """
    cutoff = _iso(_now() - timedelta(days=UNUSED_CLIENT_TTL_DAYS))
    used = select(db.oauth_authorization.c.client_id)
    tokens = select(db.oauth_refresh_token.c.client_id)
    stale = (
        select(db.oauth_client.c.client_id)
        .where(
            db.oauth_client.c.created_at < cutoff,
            db.oauth_client.c.client_id != FIRST_PARTY_CLIENT_ID,
            db.oauth_client.c.client_id.not_in(used),
            db.oauth_client.c.client_id.not_in(tokens),
        )
        .scalar_subquery()
    )
    conn.execute(db.oauth_pending.delete().where(
        db.oauth_pending.c.client_id.in_(stale), db.oauth_pending.c.expires_at < _iso(_now())))
    conn.execute(db.oauth_client.delete().where(
        db.oauth_client.c.client_id.in_(stale),
        db.oauth_client.c.client_id.not_in(select(db.oauth_pending.c.client_id)),
    ))


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

    def __init__(self, engine, store, trusted_proxy: bool = False, public_host: str | None = None,
                 required_org: str | None = None, workspace_host=None):
        self.engine = engine
        # org id -> its own host (organization.vanity_host) or None. A workspace
        # with its own host is connected there, never on the canonical host.
        self.workspace_host = workspace_host or (lambda _org: None)
        self.store = store
        self.trusted_proxy = trusted_proxy
        # On a workspace's own host only that workspace's credentials authenticate.
        self.required_org = required_org
        # The same host the metadata and token audience use; None falls back to the env.
        self.public_host = public_host
        from hoja_server.writer import Writer

        self.writer = Writer(engine)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id == FIRST_PARTY_CLIENT_ID:
            return FIRST_PARTY_CLIENT
        with self.engine.connect() as conn:
            raw = conn.execute(
                select(db.oauth_client.c.client_json).where(
                    db.oauth_client.c.client_id == client_id
                )
            ).scalar_one_or_none()
        if not raw:
            return None
        client = OAuthClientInformationFull.model_validate_json(raw)
        # The scope a public client registered is self-asserted, so it is not the
        # boundary: consent and the person's own access are. Widening it here lets
        # a client registered with "hoja:read" (or with the old read-only default)
        # step up to hoja:write after an insufficient_scope answer, instead of the
        # SDK refusing the new authorize request with invalid_scope.
        return client.model_copy(update={"scope": " ".join(ALL_SCOPES)})

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
        _take_registration_slot(self.engine, REQUEST_CLIENT_IP.get())
        with self.engine.begin() as conn:
            _prune_unused_clients(conn)
            total = conn.execute(select(func.count()).select_from(db.oauth_client).where(
                db.oauth_client.c.client_id != FIRST_PARTY_CLIENT_ID)).scalar_one()
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
        if params.resource and params.resource.rstrip("/") not in self.resources_for(client.client_id):
            raise AuthorizeError("invalid_target", "Unknown resource.")
        # RFC 8707 makes `resource` optional, but the SDK's bearer check compares
        # the token's resource with this server's URL and refuses None
        # (mcp/server/auth/middleware/bearer_auth.py, _issued_for_this_resource:
        # AnyHttpUrl("") fails validation). A token minted without one was refused
        # on first use and the client started OAuth again, forever. The only
        # resource this server protects is its own URL, so default to it.
        resource = params.resource.rstrip("/") if params.resource else self._resource_url()
        # No scope requested: ask for both, and let the person choose on the
        # consent screen. Read-only stays one click away.
        scopes = params.scopes or list(ALL_SCOPES)
        if not set(scopes).issubset(ALL_SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scope.")
        if READ_SCOPE not in scopes:
            scopes = [READ_SCOPE, *scopes]
        # OAuth SDK validates exact registered redirect and requires S256 before this hook.
        pending_id, csrf_token = _secret(), _secret()
        expires = _now() + timedelta(minutes=PENDING_TTL_MINUTES)
        with self.engine.begin() as conn:
            conn.execute(db.oauth_pending.insert().values(
                pending_hash=_digest(pending_id), client_id=client.client_id,
                scopes=json.dumps(scopes), code_challenge=params.code_challenge,
                redirect_uri=str(params.redirect_uri),
                redirect_explicit=params.redirect_uri_provided_explicitly,
                state=params.state, csrf_token=csrf_token, resource=resource,
                expires_at=_iso(expires), consumed_at=None,
                device_label=device_label(_meta().get("device_name")),
                user_agent=(_meta().get("user_agent") or "")[:512] or None,
            ))
        # Absolute, under the MCP mount: a root-relative "/oauth/consent" sends the
        # browser to the web app's fallback page and the consent screen never shows.
        return f"{self._resource_url()}/oauth/consent?{urlencode({'pending': pending_id})}"

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
                row["resource"] or self._resource_url(),
                grant_id=row.get("grant_id"),
            )

    def _in_grace(self, conn, row, now: datetime) -> bool:
        """True when ``row`` is the token a rotation replaced moments ago.

        Three facts together: it was revoked within the grace window; its grace
        has not been spent (spending it sets ``expires_at`` to ``revoked_at``);
        and its grant still holds a live refresh token. The last one is what
        tells a rotation apart from a revocation or a detected replay, both of
        which revoke every token of the grant.
        """
        revoked = row["revoked_at"]
        if not revoked or _parse_dt(revoked) < now - timedelta(seconds=REFRESH_GRACE_SECONDS):
            return False
        if row["expires_at"] <= revoked:
            return False
        live = conn.execute(select(db.oauth_refresh_token.c.token_hash).where(
            db.oauth_refresh_token.c.grant_id == row["grant_id"],
            db.oauth_refresh_token.c.revoked_at.is_(None),
            db.oauth_refresh_token.c.expires_at > _iso(now),
        )).first()
        return live is not None

    def _revoke_grant(self, conn, grant_id: str) -> None:
        revoke_grant(conn, grant_id)

    async def load_refresh_token(self, client, refresh_token: str):
        now = _now()
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
            if row and row["revoked_at"] is not None and not self._in_grace(conn, row, now):
                # The SDK rejects revoked tokens before exchange_refresh_token runs, so
                # reuse of a rotated/revoked token must be handled here.
                self._revoke_grant(conn, row["grant_id"])
                return None
        if not row or (row["revoked_at"] is None and row["expires_at"] <= _iso(now)):
            return None
        subject = json.dumps({"user_id": row["user_id"], "org_id": row["organization_id"], "grant_id": row["grant_id"]})
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]),
            # A token in its grace window reads as live until the window closes.
            expires_at=int((now + timedelta(seconds=REFRESH_GRACE_SECONDS)).timestamp())
            if row["revoked_at"] else int(_parse_dt(row["expires_at"]).timestamp()),
            resource=row["resource"] or self._resource_url(),
            subject=subject,
        )

    async def exchange_refresh_token(
        self, client, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        original = set(refresh_token.scopes)
        requested = set(scopes or refresh_token.scopes)
        if not requested.issubset(original):
            raise TokenError("invalid_scope", "Refresh cannot increase granted scopes.")
        token = None
        token_hash = _digest(refresh_token.token)
        with self.engine.begin() as conn:
            now = _now()
            row = conn.execute(select(db.oauth_refresh_token).where(
                db.oauth_refresh_token.c.token_hash == token_hash,
                db.oauth_refresh_token.c.client_id == client.client_id,
            ).with_for_update()).mappings().first()
            if row is None:
                raise TokenError("invalid_grant", "Refresh token is invalid or already used.")
            if row["revoked_at"] is None and row["expires_at"] > _iso(now):
                # Normal rotation: this token is spent, its successor is minted.
                spent = conn.execute(
                    update(db.oauth_refresh_token)
                    .where(db.oauth_refresh_token.c.token_hash == token_hash,
                           db.oauth_refresh_token.c.revoked_at.is_(None))
                    .values(revoked_at=_iso(now))
                ).rowcount == 1
            elif row["revoked_at"] is not None and self._in_grace(conn, row, now):
                # The previous token, retried inside the grace window: one fresh
                # pair, once. Spending the grace makes a second retry a replay.
                spent = conn.execute(
                    update(db.oauth_refresh_token)
                    .where(db.oauth_refresh_token.c.token_hash == token_hash,
                           db.oauth_refresh_token.c.expires_at > db.oauth_refresh_token.c.revoked_at)
                    .values(expires_at=row["revoked_at"])
                ).rowcount == 1
            else:
                spent = False
            if spent:
                claims = json.loads(refresh_token.subject or "{}")
                token = self._mint_pair(
                    conn,
                    client.client_id,
                    claims["user_id"],
                    claims["org_id"],
                    sorted(requested),
                    row["resource"] or self._resource_url(),
                    grant_id=claims["grant_id"],
                )
            else:
                self._revoke_grant(conn, row["grant_id"])
        if token is None:
            raise TokenError("invalid_grant", "Refresh token is invalid or already used.")
        return token

    async def load_access_token(self, token: str):
        # The bearer is looked up before any tenant is known (system context);
        # a valid token then scopes the rest of the MCP request to its org.
        with tenant_context.system(), self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(
                        db.oauth_access_token,
                        db.oauth_grant.c.actor_kind.label("grant_actor_kind"),
                    )
                    .outerjoin(
                        db.oauth_grant,
                        db.oauth_grant.c.id == db.oauth_access_token.c.grant_id,
                    )
                    .where(
                        db.oauth_access_token.c.token_hash == _digest(token),
                        db.oauth_access_token.c.revoked_at.is_(None),
                    )
                )
                .mappings()
                .first()
            )
        if not row or row["expires_at"] <= _iso(_now()):
            return await self._load_api_key(token)
        if self.required_org and row["organization_id"] != self.required_org:
            return None
        resource = row["resource"] or self._resource_url()
        if row["client_id"] == FIRST_PARTY_CLIENT_ID and resource.rstrip("/") in self.resources_for(row["client_id"]):
            # A Hoja CLI grant is for the whole workspace origin: one token for
            # <origin>/graphql and <origin>/mcp. Third-party clients stay bound
            # to the one resource they asked for.
            resource = self._resource_url()
        meta = _meta()
        touch_grant(self.engine, row["grant_id"], meta.get("ip"))
        default_kind = "human" if row["client_id"] == FIRST_PARTY_CLIENT_ID else "agent"
        actor_kind = row.get("grant_actor_kind") or default_kind
        if meta.get("actor_hint") == "agent":
            actor_kind = "agent"
        on_behalf_of = row["user_id"] if actor_kind == "agent" else None
        session_id = tenant_context.clean_session_id(meta.get("session_id"))
        tenant_context.enter(
            row["organization_id"], row["user_id"], actor_kind,
            actor_id=row["user_id"], on_behalf_of=on_behalf_of,
            credential_kind="oauth", credential_id=row["grant_id"], session_id=session_id,
        )
        subject = json.dumps({
            "user_id": row["user_id"],
            "org_id": row["organization_id"],
            "actor_kind": actor_kind,
            "actor_id": row["user_id"],
            "on_behalf_of": on_behalf_of,
            "credential_kind": "oauth",
            "credential_id": row["grant_id"],
            "session_id": session_id,
        })
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]),
            expires_at=int(_parse_dt(row["expires_at"]).timestamp()),
            # Tokens minted before `resource` was defaulted at authorize carry
            # None; they can only have come from this server, so they name it.
            resource=resource,
            subject=subject,
        )

    async def _load_api_key(self, token: str):
        """Accept a Hoja API key at the MCP endpoint.

        A key authenticates the same identity as an OAuth access token, so the MCP
        surface does not require a second credential type. Its read/read_write access
        becomes hoja:read [hoja:write], which keeps write tools gated by scope rather
        than by a separate code path.
        """
        with tenant_context.system():
            key = self.store.resolve_token(token)
        if not key or (self.required_org and key["organization_id"] != self.required_org):
            return None
        meta = _meta()
        actor_kind = key.get("kind") or "human"
        actor_id = key.get("actor_id") or key["user_id"]
        on_behalf_of = key.get("on_behalf_of")
        if meta.get("actor_hint") == "agent" and actor_kind != "agent":
            actor_kind = "agent"
            on_behalf_of = key["user_id"]
        session_id = tenant_context.clean_session_id(meta.get("session_id"))
        tenant_context.enter(
            key["organization_id"], key["user_id"], actor_kind,
            actor_id=actor_id, on_behalf_of=on_behalf_of,
            credential_kind="api_key", credential_id=key["id"], session_id=session_id,
        )
        scopes = [READ_SCOPE] if key.get("access") == "read" else list(ALL_SCOPES)
        return AccessToken(
            token=token,
            client_id="api-key",
            scopes=scopes,
            expires_at=None,
            resource=self._resource_url(),
            subject=json.dumps({
                "user_id": key["user_id"],
                "org_id": key["organization_id"],
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "on_behalf_of": on_behalf_of,
                "credential_kind": "api_key",
                "credential_id": key["id"],
                "session_id": session_id,
            }),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        table = db.oauth_access_token if isinstance(token, AccessToken) else db.oauth_refresh_token
        grant_id = None
        with self.engine.begin() as conn:
            grant_id = conn.execute(
                select(table.c.grant_id).where(table.c.token_hash == _digest(token.token))
            ).scalar_one_or_none()
            if grant_id:
                # Either token revokes the whole grant: its access tokens, its
                # refresh tokens, and the authorized app listed in Settings.
                revoke_grant(conn, grant_id)

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
        workspace, person = self._who(org_id, user_id)
        return HojaOAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=ACCESS_TTL,
            scope=" ".join(scopes),
            hoja_workspace=workspace,
            hoja_user=person,
        )

    def _who(self, org_id: str, user_id: str) -> tuple[dict | None, dict | None]:
        """The token response's hoja_workspace and hoja_user. Origin is this server's own."""
        try:
            origin = self._origin()
        except RuntimeError:
            origin = None
        with tenant_context.scoped(org_id):
            org = self.store.workspace(org_id) or {}
            person = self.store.get_user(org_id, user_id) or {}
        workspace = {"id": org_id, "name": org.get("name"), "url_key": org.get("urlKey"), "origin": origin}
        return workspace, {"id": user_id, "email": person.get("email"), "name": person.get("name")}

    def _origin(self) -> str:
        return _public_origin(self.trusted_proxy, self.public_host)

    def _resource_url(self) -> str:
        return self._origin() + "/mcp"

    def graphql_resource(self) -> str:
        return self._origin() + "/graphql"

    def resources_for(self, client_id: str) -> set[str]:
        """Resources a client may ask for here: Hoja's own CLI may use both endpoints."""
        if client_id == FIRST_PARTY_CLIENT_ID:
            return {self._resource_url(), self.graphql_resource()}
        return {self._resource_url()}

    def consent_workspaces(self, auth: dict) -> tuple[list[dict], list[tuple[str, str]]]:
        """Workspaces this browser's person may bind a grant to here, and those they may not.

        On a workspace's own host that is the one workspace the session belongs
        to. On the canonical host it is every active membership of the person's
        identity, minus workspaces that have their own host: those are returned
        separately as (name, host) so the page can send the person there.
        """
        if self.required_org:
            name = (self.store.workspace(auth["org_id"]) or {}).get("name") or auth["org_id"]
            return [{"id": auth["org_id"], "name": name, "user_id": auth["user_id"]}], []
        rows: list[dict] = []
        identity_id = auth.get("identity_id")
        if identity_id:
            # Enumerated by the authenticated identity, across its own memberships:
            # the only cross-tenant read here, keyed on the session's identity.
            with tenant_context.system():
                rows = [{"id": r["organizationId"], "name": r["name"]}
                        for r in self.store.identity_workspaces(identity_id)]
        if not any(r["id"] == auth["org_id"] for r in rows):
            name = (self.store.workspace(auth["org_id"]) or {}).get("name") or auth["org_id"]
            rows.insert(0, {"id": auth["org_id"], "name": name})
        here, elsewhere = [], []
        for row in rows:
            host = self.workspace_host(row["id"])
            if host:
                elsewhere.append((row["name"], host))
            else:
                here.append(row)
        return here, elsewhere

    def _member_profile(self, auth: dict, org_id: str) -> str | None:
        """The person's profile user id in ``org_id``, verified against their identity."""
        if org_id == auth["org_id"]:
            return auth["user_id"]
        if self.required_org or not auth.get("identity_id"):
            return None
        with tenant_context.system():
            target = self.store.switch_membership(auth["identity_id"], org_id)
        return target["user_id"] if target else None

    def max_scopes(self, org_id: str, user_id: str) -> list[str]:
        """The most a grant for this person may carry: never more than their own access.

        Write access for a person is decided per credential, not per role: every
        active member's browser session is read-write (``_resolve_auth`` in
        app.py sets ``api_key_access = "read_write"`` for a session), and an API
        key carries its own ``access``. So an active, unarchived human profile
        may grant hoja:write; anything else gets nothing beyond read, and an
        inactive profile gets nothing at all.
        """
        with tenant_context.scoped(org_id):
            row = self.store.get_user(org_id, user_id)
        if not row or not row.get("active") or row.get("archived_at"):
            return []
        if (row.get("kind") or "human") != "human":
            return [READ_SCOPE]
        return list(ALL_SCOPES)

    def _client_redirect(self, row, query: dict) -> RedirectResponse:
        if row["state"]:
            query["state"] = row["state"]
        # RFC 9207: name the issuer so a client talking to several servers can
        # tell which one answered (mix-up defence).
        query["iss"] = self._resource_url()
        # 303, never Starlette's default 307: after the consent form POST a 307 makes
        # the browser re-POST csrf_token and decision to the client (RFC 9700 4.11).
        return RedirectResponse(
            row["redirect_uri"] + ("&" if "?" in row["redirect_uri"] else "?") + urlencode(query),
            status_code=303,
        )

    async def finish_consent(self, request: Request, pending_id: str, form) -> Response:
        auth = request.scope.get("hoja_auth")
        if not auth or auth.get("source") != "browser_session":
            # Only a person at a browser approves. A bearer token (API key or OAuth)
            # must never be able to mint a grant, least of all a wider one.
            return HTMLResponse(consent_pages.denied_page(
                title="Sign in to approve",
                message="Approving an app needs a signed-in browser."), status_code=401)
        now = _iso(_now())
        csrf_token = str(form.get("csrf_token", ""))
        # System context: the pending request is pre-tenant, and the grant may bind
        # to a workspace other than the session's (verified through the identity
        # below), which a tenant-bound transaction could not write under RLS.
        with tenant_context.system(), self.engine.begin() as conn:
            row = conn.execute(select(db.oauth_pending).where(
                db.oauth_pending.c.pending_hash == _digest(pending_id),
                db.oauth_pending.c.consumed_at.is_(None),
                db.oauth_pending.c.expires_at > now,
            )).mappings().first()
            client = await self.get_client(row["client_id"]) if row else None
            name = (client.client_name or client.client_id) if client else None
            if not row or not secrets.compare_digest(csrf_token, row["csrf_token"]):
                return HTMLResponse(consent_pages.expired_page(client_name=name), status_code=400)
            if row["user_id"] and (row["user_id"] != auth.get("user_id") or row["organization_id"] != auth.get("org_id")):
                return HTMLResponse(consent_pages.expired_page(client_name=name), status_code=400)
            changed = conn.execute(update(db.oauth_pending).where(
                db.oauth_pending.c.pending_hash == row["pending_hash"],
                db.oauth_pending.c.consumed_at.is_(None), db.oauth_pending.c.expires_at > now,
            ).values(consumed_at=now, user_id=auth["user_id"], organization_id=auth["org_id"])).rowcount
            if changed != 1:
                return HTMLResponse(consent_pages.expired_page(client_name=name), status_code=400)
            if form.get("decision") != "allow":
                # RFC 6749 4.1.2.1: tell the client, so it stops waiting.
                return self._client_redirect(row, {"error": "access_denied"})
            allowed, _elsewhere = self.consent_workspaces(auth)
            org_id = str(form.get("workspace") or auth["org_id"])
            user_id = self._member_profile(auth, org_id) if any(w["id"] == org_id for w in allowed) else None
            ceiling = self.max_scopes(org_id, user_id) if user_id else []
            if not user_id or READ_SCOPE not in ceiling:
                return HTMLResponse(consent_pages.denied_page(
                    title="That workspace is not available here",
                    message="Start the connection again and pick one of the workspaces listed."),
                    status_code=403)
            refused = self.client_refused(row["client_id"], org_id)
            if refused:
                return HTMLResponse(refused, status_code=403)
            requested = json.loads(row["scopes"])
            granted = [READ_SCOPE]
            if form.get("access") == "write" and WRITE_SCOPE in requested and WRITE_SCOPE in ceiling:
                granted.append(WRITE_SCOPE)
            default_actor = "human" if row["client_id"] == FIRST_PARTY_CLIENT_ID else "agent"
            chosen_actor = str(form.get("actor_kind") or default_actor)
            actor_kind = chosen_actor if chosen_actor in ("agent", "human") else default_actor
            grant_id = upsert_grant(
                conn, org_id=org_id, user_id=user_id, client_id=row["client_id"], client_name=name,
                label=row.get("device_label"), scopes=granted, user_agent=row.get("user_agent"),
                actor_kind=actor_kind)
            code = _secret()
            conn.execute(db.oauth_authorization.insert().values(
                code_hash=_digest(code), client_id=row["client_id"], user_id=user_id,
                organization_id=org_id, scopes=json.dumps(granted),
                code_challenge=row["code_challenge"], redirect_uri=row["redirect_uri"],
                resource=row["resource"] or self._resource_url(), grant_id=grant_id,
                expires_at=_iso(_now()+timedelta(seconds=CODE_TTL)), consumed_at=None,
            ))
        return self._client_redirect(row, {"code": code})

    def client_refused(self, client_id: str, org_id: str) -> str | None:
        """A refusal page when the workspace allows only Hoja's own apps, else None."""
        if client_id == FIRST_PARTY_CLIENT_ID or oauth_clients_policy(self.engine, org_id) != "first_party":
            return None
        return consent_pages.denied_page(
            title="This workspace allows only Hoja's own apps",
            message="A workspace admin has restricted which apps can connect. Ask an admin, "
                    "or use the Hoja CLI, which is always allowed.")

    # ------------------------------------------------------------ device flow (RFC 8628)

    def start_device(self, form) -> tuple[int, dict]:
        """POST <origin>/mcp/device: a device code for an app without a browser."""
        client_id = str(form.get("client_id") or "")
        if client_id != FIRST_PARTY_CLIENT_ID:
            return 400, {"error": "unauthorized_client",
                         "error_description": "Only the Hoja CLI may use the device grant."}
        scopes = str(form.get("scope") or "").split() or list(ALL_SCOPES)
        if not set(scopes).issubset(ALL_SCOPES):
            return 400, {"error": "invalid_scope", "error_description": "Unsupported scope."}
        if READ_SCOPE not in scopes:
            scopes = [READ_SCOPE, *scopes]
        resource = str(form.get("resource") or "").rstrip("/") or self.graphql_resource()
        if resource not in self.resources_for(client_id):
            return 400, {"error": "invalid_target", "error_description": "Unknown resource."}
        device_code = _secret()
        now = _now()
        meta = _meta()
        with self.engine.begin() as conn:
            conn.execute(db.oauth_device_code.delete().where(
                db.oauth_device_code.c.expires_at < _iso(now - timedelta(days=1))))
            for _attempt in range(5):
                code = _user_code()
                if not conn.execute(select(db.oauth_device_code.c.user_code).where(
                        db.oauth_device_code.c.user_code == code)).first():
                    break
            conn.execute(db.oauth_device_code.insert().values(
                device_code_hash=_digest(device_code), user_code=code, client_id=client_id,
                scopes=json.dumps(scopes), resource=resource,
                device_label=device_label(form.get("device_name")),
                user_agent=(meta.get("user_agent") or "")[:512] or None,
                csrf_token=_secret(), interval=DEVICE_INTERVAL, last_polled_at=None,
                status="pending", created_at=_iso(now),
                expires_at=_iso(now + timedelta(seconds=DEVICE_TTL))))
        verify = self._origin() + "/device"
        return 200, {
            "device_code": device_code, "user_code": code,
            "verification_uri": verify,
            "verification_uri_complete": f"{verify}?{urlencode({'user_code': code})}",
            "expires_in": DEVICE_TTL, "interval": DEVICE_INTERVAL,
        }

    def poll_device(self, form) -> tuple[int, dict]:
        """The device grant at /mcp/token: RFC 8628 3.5 answers until approved, then tokens once."""
        if str(form.get("client_id") or "") != FIRST_PARTY_CLIENT_ID:
            return 400, {"error": "invalid_client", "error_description": "Unknown client."}
        code_hash = _digest(str(form.get("device_code") or ""))
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(select(db.oauth_device_code).where(
                db.oauth_device_code.c.device_code_hash == code_hash).with_for_update()).mappings().first()
            if row is None:
                return 400, {"error": "invalid_grant", "error_description": "Unknown device code."}
            if row["status"] == "consumed":
                return 400, {"error": "invalid_grant", "error_description": "This device code was already used."}
            if row["status"] == "denied":
                return 400, {"error": "access_denied", "error_description": "The request was denied."}
            if row["expires_at"] <= _iso(now):
                return 400, {"error": "expired_token", "error_description": "The device code expired."}
            if row["status"] == "pending":
                last, interval = row["last_polled_at"], row["interval"]
                if last is not None and now.timestamp() - last < interval:
                    interval += 5
                    conn.execute(update(db.oauth_device_code).where(
                        db.oauth_device_code.c.device_code_hash == code_hash).values(
                        interval=interval, last_polled_at=now.timestamp()))
                    return 400, {"error": "slow_down", "interval": interval,
                                 "error_description": f"Poll at most every {interval} seconds."}
                conn.execute(update(db.oauth_device_code).where(
                    db.oauth_device_code.c.device_code_hash == code_hash).values(last_polled_at=now.timestamp()))
                return 400, {"error": "authorization_pending",
                             "error_description": "Waiting for approval in the browser."}
            spent = conn.execute(update(db.oauth_device_code).where(
                db.oauth_device_code.c.device_code_hash == code_hash,
                db.oauth_device_code.c.status == "approved").values(status="consumed")).rowcount
            if spent != 1:
                return 400, {"error": "invalid_grant", "error_description": "This device code was already used."}
            token = self._mint_pair(conn, row["client_id"], row["user_id"], row["organization_id"],
                                    json.loads(row["granted_scopes"]), row["resource"], grant_id=row["grant_id"])
        return 200, token.model_dump(exclude_none=True)

    def pending_device(self, user_code: str):
        code = normalize_user_code(user_code)
        if not code:
            return None
        with tenant_context.system(), self.engine.connect() as conn:
            return conn.execute(select(db.oauth_device_code).where(
                db.oauth_device_code.c.user_code == code)).mappings().first()

    def decide_device(self, auth: dict, user_code: str, form) -> tuple[str, str | None]:
        """Approve or deny a device code from the /device page. Returns (outcome, error page)."""
        row = self.pending_device(user_code)
        now = _iso(_now())
        if (row is None or row["status"] != "pending" or row["expires_at"] <= now
                or not secrets.compare_digest(str(form.get("csrf_token", "")), row["csrf_token"])):
            return "expired", consent_pages.expired_page(client_name="Hoja CLI")
        if form.get("decision") != "allow":
            with tenant_context.system(), self.engine.begin() as conn:
                conn.execute(update(db.oauth_device_code).where(
                    db.oauth_device_code.c.device_code_hash == row["device_code_hash"],
                    db.oauth_device_code.c.status == "pending").values(status="denied"))
            return "denied", None
        allowed, _elsewhere = self.consent_workspaces(auth)
        org_id = str(form.get("workspace") or auth["org_id"])
        user_id = self._member_profile(auth, org_id) if any(w["id"] == org_id for w in allowed) else None
        ceiling = self.max_scopes(org_id, user_id) if user_id else []
        if not user_id or READ_SCOPE not in ceiling:
            return "refused", consent_pages.denied_page(
                title="That workspace is not available here",
                message="Start again from the app and pick one of the workspaces listed.")
        refused = self.client_refused(row["client_id"], org_id)
        if refused:
            return "refused", refused
        requested = json.loads(row["scopes"])
        granted = [READ_SCOPE]
        if form.get("access") == "write" and WRITE_SCOPE in requested and WRITE_SCOPE in ceiling:
            granted.append(WRITE_SCOPE)
        with tenant_context.system(), self.engine.begin() as conn:
            chosen_actor = str(form.get("actor_kind") or "human")
            actor_kind = chosen_actor if chosen_actor in ("agent", "human") else "human"
            grant_id = upsert_grant(conn, org_id=org_id, user_id=user_id, client_id=row["client_id"],
                                    client_name=FIRST_PARTY_CLIENT.client_name, label=row["device_label"],
                                    scopes=granted, user_agent=row["user_agent"], actor_kind=actor_kind)
            changed = conn.execute(update(db.oauth_device_code).where(
                db.oauth_device_code.c.device_code_hash == row["device_code_hash"],
                db.oauth_device_code.c.status == "pending").values(
                status="approved", user_id=user_id, organization_id=org_id,
                granted_scopes=json.dumps(granted), grant_id=grant_id, actor_kind=actor_kind)).rowcount
        if changed != 1:
            return "expired", consent_pages.expired_page(client_name="Hoja CLI")
        return "approved", None


def revoke_grant(conn, grant_id: str) -> None:
    """Revoke every access and refresh token of a grant, and its authorized-app row."""
    now = _iso(_now())
    for target in (db.oauth_access_token, db.oauth_refresh_token):
        conn.execute(
            update(target)
            .where(target.c.grant_id == grant_id, target.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    conn.execute(update(db.oauth_grant).where(
        db.oauth_grant.c.id == grant_id, db.oauth_grant.c.revoked_at.is_(None)).values(revoked_at=now))


def touch_grant(engine, grant_id: str | None, ip: str | None) -> None:
    """Record use of an authorized app, at most once a minute (a write per request is waste)."""
    if not grant_id:
        return
    now = _now()
    try:
        with tenant_context.system(), engine.begin() as conn:
            conn.execute(update(db.oauth_grant).where(
                db.oauth_grant.c.id == grant_id,
                (db.oauth_grant.c.last_used_at.is_(None))
                | (db.oauth_grant.c.last_used_at < _iso(now - timedelta(seconds=GRANT_TOUCH_SECONDS))),
            ).values(last_used_at=_iso(now), last_used_ip=(ip or None)))
    except Exception:  # bookkeeping must never fail the request it records
        import logging

        logging.getLogger("hoja_server").warning("authorized app touch failed", exc_info=True)


def upsert_grant(conn, *, org_id: str, user_id: str, client_id: str, client_name: str | None,
                 label: str | None, scopes: list[str], user_agent: str | None,
                 actor_kind: str | None = None) -> str:
    """One authorized app per (person, workspace, client, device label); returns its id (the grant id)."""
    now = _iso(_now())
    resolved_kind = actor_kind if actor_kind in ("agent", "human") else (
        "human" if client_id == FIRST_PARTY_CLIENT_ID else "agent"
    )
    row = conn.execute(select(db.oauth_grant.c.id).where(
        db.oauth_grant.c.organization_id == org_id, db.oauth_grant.c.user_id == user_id,
        db.oauth_grant.c.client_id == client_id,
        (db.oauth_grant.c.device_label == label) if label is not None else db.oauth_grant.c.device_label.is_(None),
        db.oauth_grant.c.revoked_at.is_(None),
    )).first()
    if row:
        conn.execute(update(db.oauth_grant).where(db.oauth_grant.c.id == row[0]).values(
            scopes=json.dumps(scopes), client_name=client_name, updated_at=now,
            user_agent=user_agent, actor_kind=resolved_kind))
        return row[0]
    grant_id = _secret()
    conn.execute(db.oauth_grant.insert().values(
        id=grant_id, organization_id=org_id, user_id=user_id, client_id=client_id,
        client_name=client_name, device_label=label, scopes=json.dumps(scopes),
        user_agent=user_agent, actor_kind=resolved_kind, created_at=now, updated_at=now))
    return grant_id


def oauth_clients_policy(engine, org_id: str) -> str:
    """The workspace's oauthClients setting: "all" (default) or "first_party"."""
    with tenant_context.scoped(org_id), engine.connect() as conn:
        value = conn.execute(select(db.workspace_security_policy.c.oauth_clients).where(
            db.workspace_security_policy.c.organization_id == org_id)).scalar_one_or_none()
    return "first_party" if value == "first_party" else "all"


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
    from hoja import __version__

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
                enabled=True, valid_scopes=ALL_SCOPES, default_scopes=list(ALL_SCOPES)
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    from mcp.server.mcpserver.exceptions import ToolError

    from hoja_server.mcp_tools import build_tools
    from hoja_server.resolvers import build_schema

    def principal(write: bool) -> dict:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access = get_access_token()
        if access is None or not access.subject:
            raise ToolError("OAuth authentication is required. " + DENY_HINT)
        granted = set(access.scopes)
        if write and WRITE_SCOPE not in granted:
            # An OAuth connection normally never gets here: the HTTP endpoint
            # answers a write tool call on a read-only token with 403
            # insufficient_scope, and the client asks the person for write access.
            if access.client_id == "api-key":
                raise ToolError(
                    "This Hoja API key is read-only, so nothing was changed. Create a read-write key "
                    "in Hoja Settings > API keys and configure the MCP client with it. " + DENY_HINT
                )
            raise ToolError(
                f"This connection is read-only: the {WRITE_SCOPE} scope was not granted, so nothing was changed. "
                "Ask the user to disconnect Hoja in the MCP client and connect again, choosing "
                "\"Read and write\" on the Hoja approval page. " + DENY_HINT
            )
        if READ_SCOPE not in granted:
            raise ToolError(f"OAuth scope {READ_SCOPE} is required. " + DENY_HINT)
        try:
            identity = json.loads(access.subject)
            user_id, org_id = identity["user_id"], identity["org_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolError("OAuth identity is invalid. " + DENY_HINT) from exc
        if not provider.store.membership_for_profile(org_id, user_id):
            raise ToolError("Active organization membership is required. " + DENY_HINT)
        # WP12 (finding F10): an agent authenticates with its own user row, and this
        # is where the dispatcher learns that the caller is an agent rather than the
        # human who delegated the work. A human has no member row and gets None.
        from hoja_server import agent_members, tenant_context

        try:
            with tenant_context.scoped(org_id), provider.store.engine.connect() as conn:
                member = agent_members.member_row_for_user(conn, org_id, user_id)
        except Exception as exc:  # fail closed: an unresolvable principal is not a human
            raise ToolError("The caller's identity could not be resolved. " + DENY_HINT) from exc
        agent_member_id = member["id"] if member else None
        actor_kind = identity.get("actor_kind") or ("agent" if agent_member_id else "human")
        actor_id = identity.get("actor_id") or user_id
        on_behalf_of = identity.get("on_behalf_of")
        credential_kind = identity.get("credential_kind") or ("api_key" if access.client_id == "api-key" else "oauth")
        credential_id = identity.get("credential_id")
        session_id = identity.get("session_id")
        tenant_context.enter(
            org_id, user_id, actor_kind,
            actor_id=actor_id, on_behalf_of=on_behalf_of,
            credential_kind=credential_kind, credential_id=credential_id, session_id=session_id,
        )
        return {
            "store": provider.store,
            "writer": provider.writer,
            "org_id": org_id,
            "user_id": user_id,
            "identity_id": None,
            "browser_session": False,
            "agent_member_id": agent_member_id,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "on_behalf_of": on_behalf_of,
            "credential_kind": credential_kind,
            "credential_id": credential_id,
            "session_id": session_id,
            # Read-only grants must not trigger resolver-side reconciliation writes.
            "read_only": WRITE_SCOPE not in granted,
        }

    for tool in build_tools(build_schema(), principal):
        server._tool_manager._tools[tool.name] = tool

    @server.custom_route("/device", methods=["POST"])
    async def device_authorization(request: Request):
        """RFC 8628 3.1 device authorization endpoint (<origin>/mcp/device)."""
        status, body = provider.start_device(await request.form())
        return JSONResponse(body, status_code=status, headers={"cache-control": "no-store"})

    @server.custom_route("/oauth/consent", methods=["GET", "POST"])
    async def consent(request: Request):
        pending_id = request.query_params.get("pending") or ""
        auth = request.scope.get("hoja_auth")
        if not auth or auth.get("source") != "browser_session":
            # The HTTP endpoint answers signed-out browsers before this point (it
            # knows the hosted sign-in); this is the backstop for any other caller.
            return HTMLResponse(consent_pages.denied_page(
                title="Sign in to approve",
                message="Approving an app needs a signed-in browser."), status_code=401)
        if request.method == "POST":
            form = await request.form()
            return await provider.finish_consent(request, pending_id, form)
        now = _iso(_now())
        with provider.engine.begin() as conn:
            pending = conn.execute(select(db.oauth_pending).where(
                db.oauth_pending.c.pending_hash == _digest(pending_id))).mappings().first()
            live = bool(pending) and pending["consumed_at"] is None and pending["expires_at"] > now
            if live:
                conn.execute(update(db.oauth_pending).where(
                    db.oauth_pending.c.pending_hash == pending["pending_hash"],
                    db.oauth_pending.c.consumed_at.is_(None),
                    db.oauth_pending.c.user_id.is_(None),
                ).values(user_id=auth["user_id"], organization_id=auth["org_id"]))
                pending = conn.execute(select(db.oauth_pending).where(
                    db.oauth_pending.c.pending_hash == pending["pending_hash"]
                )).mappings().first()
        client = await provider.get_client(pending["client_id"]) if pending else None
        name = (client.client_name or client.client_id) if client else None
        no_store = {"cache-control": "no-store"}
        if (not live or not client or pending["user_id"] != auth["user_id"]
                or pending["organization_id"] != auth["org_id"]):
            return HTMLResponse(consent_pages.expired_page(client_name=name), status_code=400, headers=no_store)
        workspaces, elsewhere = provider.consent_workspaces(auth)
        if not workspaces:
            return HTMLResponse(consent_pages.wrong_host_page(client_name=name, workspaces=elsewhere),
                                status_code=409, headers=no_store)
        selected = auth["org_id"] if any(w["id"] == auth["org_id"] for w in workspaces) else workspaces[0]["id"]
        profile = provider._member_profile(auth, selected) or auth["user_id"]
        with tenant_context.scoped(selected):
            viewer = provider.store.get_user(selected, profile) or {}
        from urllib.parse import urlsplit

        host = urlsplit(provider._resource_url()).hostname or ""
        return HTMLResponse(consent_pages.consent_page(
            client_name=name,
            redirect_host=urlsplit(pending["redirect_uri"]).netloc,
            workspaces=workspaces,
            selected_org=selected,
            workspace_fixed=bool(provider.required_org),
            host=host,
            can_write=WRITE_SCOPE in provider.max_scopes(selected, profile),
            write_requested=WRITE_SCOPE in json.loads(pending["scopes"]),
            csrf_token=pending["csrf_token"],
            email=viewer.get("email"),
            switch_account_url="/signout",
            other_hosts=elsewhere,
            default_actor_kind="human" if pending["client_id"] == FIRST_PARTY_CLIENT_ID else "agent",
        ), headers=no_store)

    return server
