"""FastAPI + Ariadne ASGI app with token auth middleware.

- POST /graphql : the Linear-compatible endpoint the CLI talks to.
- Auth: raw token in Authorization header (with or without 'Bearer').
  Unknown/revoked token → HTTP 401 + {errors:[{message}]}.
- Never emits 429. GraphQL errors → HTTP 200 + {errors:[...], data:null}.
- Optional OFFLINE mode: if CLINIAR_SERVER_OPEN=1, any token maps to the only
  seeded identity in an unambiguous SQLite database.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ariadne import format_error, graphql, graphql_sync
from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    InlineFragmentNode,
    OperationDefinitionNode,
    parse,
)
from sqlalchemy import select, text
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocket, WebSocketDisconnect

from cliniar_server.connectors_api import connector_binding_for, connector_routes
from cliniar_server.db import (
    _seed_demo_issues,
    api_key,
    browser_session,
    gen_session_token,
    gen_token,
    global_identity,
    identity_credential,
    make_engine,
    new_id,
    now_iso,
    organization,
    organization_membership,
    password_credential,
    rate_limit_blocked,
    rate_limit_record,
    resolve_database_target,
    resolve_token_identity,
    schema_is_current,
    seed_states_for_team,
    team,
    team_member,
    token_hash,
    user,
)
from cliniar_server.denials import DENY_HINT, hint_for
from cliniar_server.resolvers import build_schema
from cliniar_server.secrets_box import SecretBoxError
from cliniar_server.store import Store
from cliniar_server.writer import Writer

_schema = build_schema()
_WEB_ROOT = Path(
    os.environ.get("CLINIAR_WEB_ROOT", Path(__file__).resolve().parent.parent / "frontend" / "dist")
)


# Content-hashed brand mark: the URL changes with the bytes, so CDN caches cannot pin an old response.
BRAND_MARK = "hoja-clover.8371400e.svg"


def _first_identity(engine):
    """Offline convenience for an unambiguous isolated SQLite database."""
    if engine.dialect.name != "sqlite":
        return None, None
    try:
        return resolve_token_identity(engine)
    except ValueError:
        return None, None


def _valid_public_host(value: str) -> bool:
    """Accept configured DNS names/IP literals, but not URL syntax or userinfo."""
    import re
    from urllib.parse import urlsplit

    candidate = value.strip()
    if not candidate or any(ch in candidate for ch in "/\\@?#"):
        return False
    parsed = urlsplit("//" + candidate)
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        return False
    try:
        ipaddress.ip_address(parsed.hostname)
        return True
    except ValueError:
        labels = parsed.hostname.rstrip(".").split(".")
        return len(parsed.hostname) <= 253 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        )


_MAX_SEQ = 2**63 - 1
_MAX_GRAPHQL_BODY = 1_000_000
_MAX_QUERY_DEPTH = 15
_MAX_QUERY_FIELDS = 100
_MAX_QUERY_VISITS = 10_000
_WS_REVALIDATE_SECONDS = 5
_WS_BROADCAST_INTERVAL = 0.25
_WS_MAX_REPLAY = 500
_org_wake: dict = {}
_broadcaster_seq = {"last": 0}
_LOGIN_FAILURE_LIMIT = 10
_LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_CSP_HTML = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
_graphql_log = logging.getLogger("cliniar_server.graphql")
_security_log = logging.getLogger("cliniar_server.security")


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(
        {"errors": [{"message": message, "extensions": {"code": "BAD_REQUEST"}}]}, status_code=400
    )


def _too_large() -> JSONResponse:
    return JSONResponse(
        {"errors": [{"message": "Request body is too large.", "extensions": {"code": "PAYLOAD_TOO_LARGE"}}]},
        status_code=413,
    )


def _query_problem(query: str) -> str | None:
    """Return a user-facing rejection for over-deep or over-wide documents, else None."""
    try:
        document = parse(query)
    except GraphQLError:
        return None  # syntax errors are reported by graphql() itself
    except RecursionError:
        return "Query is nested too deeply."
    fragments = {d.name.value: d for d in document.definitions if isinstance(d, FragmentDefinitionNode)}
    counts = {"aliases": 0, "deepest": 0, "visits": 0}

    def walk(selection_set, depth, spreads):
        counts["deepest"] = max(counts["deepest"], depth)
        for selection in selection_set.selections:
            counts["visits"] += 1
            if isinstance(selection, FieldNode):
                if selection.alias:
                    counts["aliases"] += 1
                if selection.selection_set:
                    walk(selection.selection_set, depth + 1, spreads)
            elif isinstance(selection, InlineFragmentNode):
                walk(selection.selection_set, depth, spreads)
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                if name in fragments and name not in spreads:
                    walk(fragments[name].selection_set, depth, spreads | {name})

    roots = 0
    for definition in document.definitions:
        if isinstance(definition, OperationDefinitionNode):
            roots += sum(isinstance(s, FieldNode) for s in definition.selection_set.selections)
            walk(definition.selection_set, 1, frozenset())
    if counts["deepest"] > _MAX_QUERY_DEPTH:
        return f"Query depth exceeds {_MAX_QUERY_DEPTH}."
    if roots > _MAX_QUERY_FIELDS or counts["aliases"] > _MAX_QUERY_FIELDS:
        return f"Query exceeds {_MAX_QUERY_FIELDS} root fields or aliases."
    if counts["visits"] > _MAX_QUERY_VISITS:
        return "Query is too complex."
    return None


async def _read_json_object(request: Request, limit: int):
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            return None, _bad_request("Invalid Content-Length.")
        if int(declared) > limit:
            return None, _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return None, _too_large()
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None, _bad_request("Request body must be valid JSON.")
    if not isinstance(data, dict):
        return None, _bad_request("Request body must be a JSON object.")
    return data, None


def _format_graphql_error(error, debug=False):
    """Surface GraphQL and intentional ValueError messages; mask unexpected server exceptions."""
    original = error.original_error
    if original is None or isinstance(original, (GraphQLError, ValueError, SecretBoxError)):
        formatted = format_error(error, debug)
        message = formatted.get("message", "")
        suggestion = message.find(" Did you mean")
        if suggestion != -1:
            # graphql-core embeds schema hints ("Did you mean 'viewer'?") into
            # validation error messages; strip them so errors do not enumerate
            # the schema to probing clients.
            stripped = message[:suggestion].rstrip()
            formatted["message"] = stripped if stripped.endswith(".") else stripped + "."
        extensions = formatted.get("extensions") or {}
        if extensions.get("code") in ("FORBIDDEN", "AUTHENTICATION_ERROR"):
            formatted["extensions"] = {**extensions, "agentHint": DENY_HINT}
        return formatted
    _graphql_log.error("GraphQL resolver failed", exc_info=original)
    return {"message": "Internal error", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}


def _password_matches(stored: str, candidate: str) -> bool:
    return Store._verify_password(candidate, stored)


class _FailureLimiter:
    """Sliding window of failed sign-ins per client IP and email, persisted in rate_limit_event.

    Shared across workers and restarts because state lives in the database.
    """

    def __init__(self, engine, bucket: str, limit: int, window_seconds: float):
        self._engine = engine
        self._bucket = bucket
        self._limit = limit
        self._window = window_seconds

    def blocked(self, keys: list[str]) -> bool:
        return rate_limit_blocked(self._engine, self._bucket, keys, self._limit, self._window)

    def record(self, keys: list[str]) -> None:
        rate_limit_record(self._engine, self._bucket, keys, self._window)


def _too_many_attempts() -> JSONResponse:
    return JSONResponse(
        {"error": "Too many failed sign-in attempts. Try again later."},
        status_code=429,
        headers={"Retry-After": str(_LOGIN_FAILURE_WINDOW_SECONDS)},
    )


def _with_agent_hint(body: bytes, status: int) -> bytes:
    """Attach the model-facing denial hint to a JSON error body (GraphQL-shaped or plain)."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body
    if "errors" in data:
        extensions = data.get("extensions")
        data["extensions"] = {**(extensions if isinstance(extensions, dict) else {}), "agentHint": hint_for(status)}
    else:
        data["agentHint"] = hint_for(status)
    return json.dumps(data).encode()


class _SecurityHeaders:
    """Adds baseline security headers to every HTTP response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        denied_start = None
        denied_body = b""

        async def send_with_headers(message):
            nonlocal denied_start, denied_body
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
                if headers.get("content-type", "").startswith("text/html"):
                    headers["Content-Security-Policy"] = _CSP_HTML
                if message["status"] in (401, 403, 429):
                    _security_log.warning("denied status=%s method=%s path=%s",
                                          message["status"], scope.get("method"), scope.get("path"))
                    if headers.get("content-type", "").startswith("application/json"):
                        denied_start = message
                        return
            elif message["type"] == "http.response.body" and denied_start is not None:
                denied_body += message.get("body", b"")
                if message.get("more_body"):
                    return
                denied_body = _with_agent_hint(denied_body, denied_start["status"])
                MutableHeaders(scope=denied_start)["content-length"] = str(len(denied_body))
                await send(denied_start)
                await send({"type": "http.response.body", "body": denied_body})
                return
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    db_path: str | None = None,
    *,
    database_url: str | None = None,
    tenant: str = "default",
    open_mode: bool | None = None,
    trusted_proxy_cidrs: str | None = None,
    trusted_public_host: str | None = None,
) -> Starlette:
    target = resolve_database_target(database_url=database_url, db_path=db_path, tenant=tenant)
    engine = make_engine(target)
    store = Store(engine)
    writer = Writer(engine)
    broadcaster_task = None
    gql_slots = asyncio.Semaphore(8)

    async def _change_broadcaster():
        """Single global poller: wake per-org waiters instead of per-client DB polling."""
        while True:
            try:
                events = await asyncio.to_thread(
                    store.change_org_ids_after, _broadcaster_seq["last"])
                touched = set()
                for row in events:
                    _broadcaster_seq["last"] = max(_broadcaster_seq["last"], row["seq"])
                    touched.add(row["organization_id"])
                for org_id in touched:
                    ev = _org_wake.get(org_id)
                    if ev is not None:
                        ev.set()
            except Exception:
                pass
            await asyncio.sleep(_WS_BROADCAST_INTERVAL)

    if open_mode is None:
        open_mode = os.environ.get("CLINIAR_SERVER_OPEN", "0") == "1"

    limiter = _FailureLimiter(engine, "login_failure", _LOGIN_FAILURE_LIMIT, _LOGIN_FAILURE_WINDOW_SECONDS)

    def _same_origin(origin: str | None, headers) -> bool:
        if origin is None:
            return True
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        allowed = {(headers.get("host") or "").strip().lower()}
        if public_host.strip():
            allowed.add(public_host.strip().lower())
        return parts.netloc.lower() in allowed

    def _cookie_csrf_error(request: Request, *, json_only: bool):
        """Cookie-authenticated writes must come from the same origin (and be JSON for API routes)."""
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        if request.headers.get("authorization") or not request.cookies.get("hoja_session"):
            return None
        if not _same_origin(request.headers.get("origin"), request.headers):
            return JSONResponse(
                {"errors": [{"message": "Cross-site request rejected.", "extensions": {"code": "FORBIDDEN"}}]},
                status_code=403,
            )
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if json_only and content_type != "application/json":
            return JSONResponse(
                {"errors": [{"message": "Content-Type must be application/json.", "extensions": {"code": "FORBIDDEN"}}]},
                status_code=403,
            )
        return None

    def _cookie_secure(request: Request) -> bool:
        if _request_from_trusted_proxy(request):
            # The trusted proxy terminates TLS; the proxy-to-app hop never
            # leaves the host. Do not let a forwarded proto value downgrade
            # the Secure flag on session cookies.
            return True
        if request.url.scheme == "https":
            return True
        return request.url.hostname not in ("localhost", "127.0.0.1", "::1")

    def _is_loopback(request: Request) -> bool:
        host = request.client.host if request.client else ""
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if _request_from_trusted_proxy(request):
            # Behind Cloudflare the connecting client's real address is carried
            # in CF-Connecting-IP (set by the edge, overriding any client-supplied
            # value). X-Forwarded-For's FIRST entry is the original client; the
            # last entry is only the most recent proxy hop (a shared address).
            client_ip = request.headers.get("cf-connecting-ip", "").strip()
            if client_ip:
                return client_ip
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return peer

    def _limit_keys(request: Request, email: str) -> list[str]:
        keys = [f"ip:{_client_ip(request)}"]
        if email:
            keys.append(f"email:{email}")
        return keys

    def _open_mode_identity(request: Request):
        if not open_mode or not _is_loopback(request):
            return None, None, None, False
        uid, org = _first_identity(engine)
        if uid:
            request.state.api_key_access = "read_write"
            return uid, org, None, False
        return None, None, None, False

    def _auth(request: Request):
        raw = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if raw and token:
            # A valid Authorization header is authoritative; cookies are not consulted.
            key = store.resolve_token(token)
            if key:
                store.touch_api_key(key["id"])
                request.state.api_key_access = key["access"]
                return key["user_id"], key["organization_id"], None, False
            # The presented credential did not resolve (e.g. the browser SPA's
            # "session" sentinel). Fall through to the browser session cookie
            # instead of rejecting the request outright.
        session_token = request.cookies.get("hoja_session")
        if session_token:
            session = store.resolve_browser_session(session_token)
            if session:
                member = store.membership_for_profile(
                    session["organization_id"], session["user_id"]
                )
                request.state.api_key_access = "read_write"
                return (
                    session["user_id"],
                    session["organization_id"],
                    (member or {}).get("identity_id"),
                    True,
                )
        return _open_mode_identity(request)

    def _credential_slot(uid: str, org: str):
        """Return (table, key column, key value, stored hash) for the account's password."""
        member = store.membership_for_profile(org, uid)
        with engine.connect() as conn:
            if member and member.get("identity_id"):
                stored = conn.execute(
                    select(identity_credential.c.password_hash).where(
                        identity_credential.c.identity_id == member["identity_id"])
                ).scalar_one_or_none()
                return identity_credential, identity_credential.c.identity_id, member["identity_id"], stored
            stored = conn.execute(
                select(password_credential.c.password_hash).where(password_credential.c.user_id == uid)
            ).scalar_one_or_none()
        return password_credential, password_credential.c.user_id, uid, stored

    def _password_on_file(uid: str, org: str) -> bool:
        return _credential_slot(uid, org)[3] is not None

    def _password_matches_account(uid: str, org: str, candidate: str) -> bool:
        stored = _credential_slot(uid, org)[3]
        return stored is not None and _password_matches(stored, candidate)

    def _replace_password(uid: str, org: str, password: str) -> None:
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters.")
        table, key_column, key_value, _stored = _credential_slot(uid, org)
        encoded = Store._password_digest(password, secrets.token_bytes(16))
        revoked_at = now_iso()
        with engine.begin() as conn:
            conn.execute(table.update().where(key_column == key_value).values(
                password_hash=encoded, updated_at=revoked_at))
            # A password change ends every browser session of the identity's profiles.
            member = store.membership_for_profile(org, uid)
            profile_ids = [uid]
            if member and member.get("identity_id"):
                profile_ids = [row[0] for row in conn.execute(
                    select(organization_membership.c.profile_user_id).where(
                        organization_membership.c.identity_id == member["identity_id"]))] or [uid]
            conn.execute(browser_session.update().where(
                browser_session.c.user_id.in_(profile_ids),
                browser_session.c.revoked_at.is_(None)).values(revoked_at=revoked_at))

    def _email_password_ok(email: str, password: str) -> bool:
        for row in store.workspaces_for_email(email):
            stored = _credential_slot(row["profile_user_id"], row["organization_id"])[3]
            if stored is not None and _password_matches(stored, password):
                return True
        return False

    def _mcp_oauth_auth(request: Request):
        """Resolve a valid MCP access token to the same pinned Hoja identity."""
        raw = request.headers.get("authorization", "")
        if not raw.lower().startswith("bearer "):
            return None
        from datetime import datetime, timezone

        from sqlalchemy import select

        from cliniar_server.db import oauth_access_token

        digest = token_hash(raw[7:].strip())
        with engine.connect() as conn:
            row = (
                conn.execute(
                    select(oauth_access_token).where(
                        oauth_access_token.c.token_hash == digest,
                        oauth_access_token.c.revoked_at.is_(None),
                        oauth_access_token.c.expires_at > datetime.now(timezone.utc).isoformat(),
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        member = store.membership_for_profile(row["organization_id"], row["user_id"])
        if not member:
            return None
        return {
            "user_id": row["user_id"],
            "org_id": row["organization_id"],
            "identity_id": member.get("identity_id"),
            "scopes": row["scopes"],
            "client_id": row["client_id"],
            "org_name": (store.workspace(row["organization_id"]) or {}).get("name"),
        }

    from cliniar_server.mcp_oauth import HojaOAuthProvider, build_server

    # OAuth stays fully disabled unless an explicit public origin and trusted
    # proxy CIDR configuration are provided by the deployment.
    proxy_cidrs = (
        os.environ.get("HOJA_TRUSTED_PROXY_CIDRS", "")
        if trusted_proxy_cidrs is None
        else trusted_proxy_cidrs
    )
    public_host = (
        os.environ.get("HOJA_TRUSTED_PUBLIC_HOST", "")
        if trusted_public_host is None
        else trusted_public_host
    )
    proxy_networks = []
    if proxy_cidrs.strip() and public_host.strip():
        try:
            proxy_networks = [
                ipaddress.ip_network(item.strip(), strict=False)
                for item in proxy_cidrs.split(",")
                if item.strip()
            ]
        except ValueError as exc:
            raise ValueError("HOJA_TRUSTED_PROXY_CIDRS must be comma-separated IP networks.") from exc
    trust_proxy = bool(proxy_networks) and _valid_public_host(public_host)

    def _request_from_trusted_proxy(request: Request) -> bool:
        if not trust_proxy:
            return False
        peer = request.client.host if request.client else None
        try:
            address = ipaddress.ip_address(peer) if peer else None
        except ValueError:
            return False
        return bool(address and any(address in network for network in proxy_networks))
    mcp_provider = HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy)
    mcp_server = build_server(mcp_provider, public_host=public_host)
    # SDK auth and protected-resource routes are root-relative to its Starlette
    # app; Mount strips /mcp while preserving it in root_path for metadata URLs.
    from mcp.server.transport_security import TransportSecuritySettings

    mcp_asgi = mcp_server.streamable_http_app(
        streamable_http_path="/", stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host.strip(), public_host.strip() + ":*"],
        ),
    )

    async def mcp_endpoint(scope, receive, send):
        request = Request(scope)
        auth_context = _mcp_oauth_auth(request)
        trusted_peer = _request_from_trusted_proxy(request)
        incoming_host = request.headers.get("host", "").lower()
        canonical_host = public_host.strip().lower()
        host_ok = incoming_host == canonical_host or incoming_host.startswith(canonical_host + ":")
        if trust_proxy and (not trusted_peer or not host_ok):
            auth_context = None
            if not host_ok:
                response = JSONResponse({"error": "Untrusted Host header."}, status_code=421)
                await response(scope, receive, send)
                return
            response = JSONResponse({"error": "Request did not originate from a trusted proxy."}, status_code=403)
            await response(scope, receive, send)
            return
        # The browser authorize/consent endpoints authenticate via the existing
        # Hoja session cookie; only MCP resource calls use the OAuth bearer.
        if scope.get("method") in ("GET", "POST") and scope.get("path", "").endswith("/oauth/consent"):
            uid, org, identity_id, is_session = _auth(request)
            if uid and is_session:
                member = store.membership_for_profile(org, uid)
                auth_context = {
                    "user_id": uid,
                    "org_id": org,
                    "identity_id": identity_id or (member or {}).get("identity_id"),
                    "org_name": (store.workspace(org) or {}).get("name"),
                    "source": "browser_session",
                }
        scope["hoja_auth"] = auth_context
        root_path = scope.get("root_path", "")
        if root_path.endswith("/.well-known/oauth-authorization-server/mcp"):
            scope["path"] = "/.well-known/oauth-authorization-server"
            scope["root_path"] = ""
        elif root_path.endswith("/.well-known/oauth-protected-resource/mcp"):
            scope["path"] = "/.well-known/oauth-protected-resource/mcp"
            scope["root_path"] = ""
        await mcp_asgi(scope, receive, send)

    async def authorization_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        origin = f"https://{public_host}" if trust_proxy else "https://mcp.invalid"
        issuer = origin + "/mcp"
        return JSONResponse({"issuer": issuer, "authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token", "registration_endpoint": issuer + "/register", "revocation_endpoint": issuer + "/revoke", "scopes_supported": ["hoja:read", "hoja:write"], "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"]})

    async def resource_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        issuer = (f"https://{public_host}" if trust_proxy else "https://mcp.invalid") + "/mcp"
        return JSONResponse({"resource": issuer, "authorization_servers": [issuer], "scopes_supported": ["hoja:read"]})

    from starlette.routing import Mount

    async def realtime_socket(websocket: WebSocket):
        from starlette.datastructures import Headers

        raw_headers = Headers(scope=websocket.scope)
        if not _same_origin(raw_headers.get("origin"), raw_headers):
            await websocket.close(code=1008)
            return
        offered = websocket.scope.get("subprotocols") or []
        subprotocol = "bearer" if "bearer" in offered else None
        token_header = ""
        if subprotocol and len(offered) > 1:
            token_header = f"Bearer {offered[1]}"
        websocket.scope["headers"] = [
            *websocket.scope["headers"],
            *([(b"authorization", token_header.encode())] if token_header else []),
        ]
        uid, org, _identity_id, _is_session = _auth(websocket)
        await websocket.accept(subprotocol=subprotocol)
        if not uid:
            await websocket.close(code=4401)
            return
        try:
            cursor = int(websocket.query_params.get("after", "0"))
        except ValueError:
            await websocket.close(code=4400)
            return
        if not 0 <= cursor <= _MAX_SEQ:
            await websocket.close(code=4400)
            return
        # Clamp ancient cursors: replaying the full history floods the event
        # loop with per-event JSON encodes/sends; browser clients refetch
        # state on any event anyway, so skip stale history beyond a window.
        latest = await asyncio.to_thread(store.latest_change_seq, org)
        if latest - cursor > _WS_MAX_REPLAY:
            cursor = max(0, latest - _WS_MAX_REPLAY)
        last_seq = cursor
        last_check = time.monotonic()
        wake = _org_wake.setdefault(org, asyncio.Event())
        try:
            # Deliver any backlog immediately on connect, then wait for wakes.
            events = await asyncio.to_thread(store.change_events_after, org, last_seq, viewer_id=uid)
            if events:
                await websocket.send_text(json.dumps(events))
                last_seq = events[-1]["seq"]
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=_WS_REVALIDATE_SECONDS)
                except asyncio.TimeoutError:
                    pass
                wake.clear()
                if time.monotonic() - last_check >= _WS_REVALIDATE_SECONDS:
                    last_check = time.monotonic()
                    current_uid, current_org, _i, _s = await asyncio.to_thread(_auth, websocket)
                    if current_uid != uid or current_org != org:
                        await websocket.close(code=4401)
                        return
                events = await asyncio.to_thread(store.change_events_after, org, last_seq, viewer_id=uid)
                if events:
                    try:
                        await websocket.send_text(json.dumps(events))
                    except RuntimeError:
                        return
                    last_seq = events[-1]["seq"]
        except WebSocketDisconnect:
            return

    async def agent_chat_server(request: Request):
        uid, org, _identity_id, _is_browser = _auth(request)
        if not uid:
            return JSONResponse(
                {"errors": [{"message": "Authentication required", "extensions": {"code": "AUTHENTICATION_ERROR"}}]},
                status_code=401,
            )
        csrf = _cookie_csrf_error(request, json_only=True)
        if csrf:
            return csrf
        body = await request.json()
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"error": "messages is required"}, status_code=400)
        from cliniar_server.agent_chat import sse_response, stream_reply
        from cliniar_server.schema_sdl import SCHEMA_SDL
        base_context = {
            "request": request,
            "store": store,
            "writer": writer,
            "org_id": org,
            "user_id": uid,
            "identity_id": _identity_id,
            "browser_session": _is_browser,
            "access": getattr(request.state, "api_key_access", "read_write"),
        }
        try:
            thread_id = body.get("threadId") if isinstance(body, dict) else None
            connector = connector_binding_for(engine, org, uid)
            gen = stream_reply(store, org, uid, messages, _schema, SCHEMA_SDL, base_context, thread_id, connector)
            first = await gen.__anext__()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        async def _resume():
            yield first
            async for chunk in gen:
                yield chunk

        return sse_response(_resume())

    async def _agent_auth(request: Request):
        uid, org, _identity_id, _is_browser = _auth(request)
        if not uid:
            return None, None, JSONResponse(
                {"errors": [{"message": "Authentication required", "extensions": {"code": "AUTHENTICATION_ERROR"}}]},
                status_code=401,
            )
        if request.method != "GET" and getattr(request.state, "api_key_access", "read_write") == "read":
            return None, None, JSONResponse(
                {"errors": [{"message": "This API key is read-only.", "extensions": {"code": "FORBIDDEN"}}]},
                status_code=403,
            )
        csrf = _cookie_csrf_error(request, json_only=True)
        if csrf:
            return None, None, csrf
        return uid, org, None

    async def agent_threads_list(request: Request):
        from cliniar_server import agent_threads
        uid, org, err = await _agent_auth(request)
        if err:
            return err
        return JSONResponse(agent_threads.list_threads(store, org, uid))

    async def agent_threads_create(request: Request):
        from cliniar_server import agent_threads
        uid, org, err = await _agent_auth(request)
        if err:
            return err
        body = await request.json()
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list):
            return JSONResponse({"error": "messages is required"}, status_code=400)
        return JSONResponse(agent_threads.create_thread(store, org, uid, messages))

    async def agent_thread_get(request: Request):
        from cliniar_server import agent_threads
        uid, org, err = await _agent_auth(request)
        if err:
            return err
        messages = agent_threads.get_messages(store, org, uid, request.path_params["thread_id"])
        if messages is None:
            return JSONResponse({"error": "Thread not found"}, status_code=404)
        return JSONResponse(messages)

    async def agent_thread_update(request: Request):
        from cliniar_server import agent_threads
        uid, org, err = await _agent_auth(request)
        if err:
            return err
        body = await request.json()
        title = body.get("title") if isinstance(body, dict) else None
        thread = agent_threads.update_thread(store, org, uid, request.path_params["thread_id"], title if isinstance(title, str) else None)
        if thread is None:
            return JSONResponse({"error": "Thread not found"}, status_code=404)
        return JSONResponse(thread)

    async def agent_thread_delete(request: Request):
        from cliniar_server import agent_threads
        uid, org, err = await _agent_auth(request)
        if err:
            return err
        if not agent_threads.delete_thread(store, org, uid, request.path_params["thread_id"]):
            return JSONResponse({"error": "Thread not found"}, status_code=404)
        return JSONResponse({"success": True})

    async def graphql_server(request: Request):
        uid, org, identity_id, is_browser_session = _auth(request)
        if not uid:
            return JSONResponse(
                {
                    "errors": [
                        {
                            "message": "Authentication required",
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        }
                    ]
                },
                status_code=401,
            )
        csrf = _cookie_csrf_error(request, json_only=True)
        if csrf:
            return csrf
        data, error = await _read_json_object(request, _MAX_GRAPHQL_BODY)
        if error:
            return error
        query = data.get("query")
        variables = data.get("variables")
        if query is not None and not isinstance(query, str):
            return _bad_request("query must be a string.")
        if variables is not None and not isinstance(variables, dict):
            return _bad_request("variables must be an object.")
        access = getattr(request.state, "api_key_access", "read_write")
        if isinstance(query, str):
            problem = _query_problem(query)
            if problem:
                return _bad_request(problem)
            from cliniar_server.agent_chat import read_only_violation

            if read_only_violation(query, access):
                return JSONResponse(
                    {"errors": [{"message": "This API key is read-only.",
                                 "extensions": {"code": "FORBIDDEN"}}]},
                    status_code=403,
                )
        context = {
            "request": request,
            "store": store,
            "writer": writer,
            "org_id": org,
            "user_id": uid,
            "identity_id": identity_id,
            "browser_session": is_browser_session,
            "access": access,
        }
        # Sync resolvers would otherwise run on the event loop and block every
        # WS client; execute the whole GraphQL operation in a worker thread.
        async with gql_slots:
            _success, result = await run_in_threadpool(
                graphql_sync,
                _schema,
                data,
                context_value=context,
                error_formatter=_format_graphql_error,
                debug=(os.environ.get("CLINIAR_SERVER_DEBUG", "0") == "1"),
            )
        target = context.get("switch_target")
        if target:
            session_token, _details = store.create_session_for_membership(
                target.get("identity_id", identity_id), target["user_id"], target["organization_id"]
            )
            if not session_token:
                return JSONResponse(
                    {"errors": [{"message": "Workspace access revoked"}]}, status_code=403
                )
            raw = request.cookies.get("hoja_session")
            if raw:
                store.revoke_browser_session(raw)
            response = JSONResponse(result, status_code=200)
            response.set_cookie(
                "hoja_session",
                session_token,
                httponly=True,
                secure=_cookie_secure(request),
                samesite="lax",
                max_age=14 * 24 * 60 * 60,
                path="/",
            )
            return response
        return JSONResponse(result, status_code=200)

    async def auth_setup(request: Request):
        if request.method == "GET":
            return JSONResponse(
                {"setup": True, "message": "Set a password using a valid local API token."}
            )
        csrf = _cookie_csrf_error(request, json_only=False)
        if csrf:
            return csrf
        body = await request.json()
        password = str(body.get("password", ""))
        setup_token = str(body.get("token", ""))
        setup_email = str(body.get("email", "")).strip().lower()
        session_token = request.cookies.get("hoja_session")
        session = store.resolve_browser_session(session_token) if session_token else None
        try:
            if session and _password_on_file(session["user_id"], session["organization_id"]):
                if not _password_matches_account(
                    session["user_id"], session["organization_id"], str(body.get("currentPassword", ""))
                ):
                    return JSONResponse({"error": "Current password is incorrect."}, status_code=401)
                _replace_password(session["user_id"], session["organization_id"], password)
                return JSONResponse({"status": "password_set"}, status_code=201)
            identity = store.resolve_token(setup_token)
            if identity and _password_on_file(identity["user_id"], identity["organization_id"]):
                return JSONResponse(
                    {"error": "A password is already set. Sign in and change it with your current password."},
                    status_code=403,
                )
            if identity and identity["access"] == "read":
                return JSONResponse(
                    {"error": "Read-only API keys cannot set a password."}, status_code=403
                )
            if setup_email and identity:
                with engine.connect() as conn:
                    account_email = conn.execute(
                        select(user.c.email).where(user.c.id == identity["user_id"])
                    ).scalar_one_or_none()
                if not account_email or account_email.strip().lower() != setup_email:
                    return JSONResponse(
                        {"error": "This invite does not match that email address."}, status_code=401
                    )
            ok = store.set_password(setup_token, password)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not ok:
            return JSONResponse({"error": "Invalid API token"}, status_code=401)
        return JSONResponse({"status": "password_set"}, status_code=201)

    async def auth_invite_accept(request: Request):
        body = await request.json()
        try:
            result = store.accept_new_member_invitation(
                str(body.get("code", "")), str(body.get("email", "")), str(body.get("password", ""))
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not result:
            return JSONResponse(
                {"error": "Invite code is invalid, expired, or does not match that email."},
                status_code=401,
            )
        session_token, _identity = result
        response = JSONResponse({"status": "accepted"})
        response.set_cookie(
            "hoja_session",
            session_token,
            httponly=True,
            secure=_cookie_secure(request),
            samesite="lax",
            max_age=14 * 24 * 60 * 60,
            path="/",
        )
        return response

    async def onboarding_status(_request: Request):
        try:
            initialized = store.is_initialized()
        except Exception:
            return JSONResponse({"error": "Setup status unavailable"}, status_code=503)
        return JSONResponse({"initialized": initialized})

    async def onboarding_bootstrap(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        workspace_name = str(body.get("workspaceName", "")).strip()
        workspace_key = str(body.get("workspaceKey", "")).strip().lower()
        team_name = str(body.get("teamName", "")).strip()
        team_key = str(body.get("teamKey", "")).strip().upper()
        if not all((name, workspace_name, workspace_key, team_name, team_key)) or "@" not in email:
            return JSONResponse(
                {"error": "Complete all fields with a valid email."}, status_code=400
            )
        if len(password) < 12:
            return JSONResponse(
                {"error": "Password must be at least 12 characters."}, status_code=400
            )
        if not workspace_key.replace("-", "").isalnum() or not team_key.isalnum():
            return JSONResponse(
                {
                    "error": "Workspace and team keys may contain only letters, numbers, and workspace hyphens."
                },
                status_code=400,
            )

        raw_api_key = gen_token()
        session_token = gen_session_token()
        now = now_iso()
        identity_id, org_id, uid, tid = new_id(), new_id(), new_id(), new_id()
        password_hash = Store._password_digest(password, secrets.token_bytes(16))
        try:
            # Serialize fresh-instance check and all initial rows in one transaction.
            with engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.execute(text("SELECT pg_advisory_xact_lock(721946031)"))
                elif conn.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                if conn.execute(select(organization.c.id).limit(1)).first():
                    return JSONResponse(
                        {"error": "This Hoja instance is already set up."}, status_code=409
                    )
                conn.execute(
                    organization.insert().values(
                        id=org_id,
                        name=workspace_name,
                        url_key=workspace_key,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    user.insert().values(
                        id=uid,
                        organization_id=org_id,
                        name=name,
                        display_name=name,
                        email=email,
                        active=True,
                        admin=True,
                        timezone="UTC",
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    global_identity.insert().values(id=identity_id, email=email, created_at=now)
                )
                conn.execute(
                    organization_membership.insert().values(
                        id=new_id(),
                        identity_id=identity_id,
                        organization_id=org_id,
                        profile_user_id=uid,
                        active=True,
                        created_at=now,
                    )
                )
                conn.execute(
                    identity_credential.insert().values(
                        identity_id=identity_id,
                        password_hash=password_hash,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    api_key.insert().values(
                        id=new_id(),
                        token_hash=token_hash(raw_api_key),
                        label="initial admin",
                        user_id=uid,
                        organization_id=org_id,
                        created_at=now,
                    )
                )
                conn.execute(
                    team.insert().values(
                        id=tid,
                        organization_id=org_id,
                        key=team_key,
                        name=team_name,
                        description=f"{team_name} team",
                        color="#5e6ad2",
                        private=False,
                        timezone="UTC",
                        issue_counter=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(team_member.insert().values(team_id=tid, user_id=uid))
                default_state = seed_states_for_team(conn, org_id, tid)
                conn.execute(
                    team.update().where(team.c.id == tid).values(default_state_id=default_state)
                )
                _seed_demo_issues(conn, org_id, tid, team_key, uid, default_state, workspace_key)
                conn.execute(
                    browser_session.insert().values(
                        id=new_id(),
                        token_hash=token_hash(session_token),
                        user_id=uid,
                        organization_id=org_id,
                        created_at=now,
                        expires_at=(datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    )
                )
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            if isinstance(exc, IntegrityError):
                return JSONResponse(
                    {"error": "Workspace key or email is already in use."}, status_code=409
                )
            raise
        response = JSONResponse(
            {
                "status": "created",
                "apiKey": raw_api_key,
                "teamId": tid,
                "workspace": {"id": org_id, "name": workspace_name, "urlKey": workspace_key},
            }
        )
        response.set_cookie(
            "hoja_session",
            session_token,
            httponly=True,
            secure=_cookie_secure(request),
            samesite="lax",
            max_age=14 * 24 * 60 * 60,
            path="/",
        )
        return response

    async def auth_login(request: Request):
        body = await request.json()
        keys = _limit_keys(request, str(body.get("email", "")).strip().lower())
        if limiter.blocked(keys):
            return _too_many_attempts()
        response = await _login(request)
        if response.status_code == 401:
            limiter.record(keys)
        return response

    async def _login(request: Request):
        body = await request.json()
        email = str(body.get("email", ""))
        password = str(body.get("password", ""))
        organization_ref = body.get("organizationId")
        api_key_value = str(body.get("apiKey", ""))
        if not api_key_value and not organization_ref:
            try:
                matches = store.matching_login_workspaces(email)
            except Exception as exc:
                # Old databases may not have the identity/membership schema yet.
                # Do not disguise unrelated database errors as invalid credentials.
                from sqlalchemy.exc import OperationalError

                if not isinstance(exc, OperationalError) or "organization_membership" not in str(
                    exc
                ):
                    raise
                return JSONResponse(
                    {
                        "error": "Workspace sign-in is unavailable until the database schema is migrated."
                    },
                    status_code=503,
                )
            if len(matches) > 1:
                if not _email_password_ok(email, password):
                    return JSONResponse({"error": "Invalid credentials"}, status_code=401)
                return JSONResponse(
                    {"error": "Choose a workspace", "workspaces": matches}, status_code=409
                )
            if len(matches) == 1:
                organization_ref = matches[0]["organizationId"]
        if api_key_value:
            identity = store.resolve_token(api_key_value)
            if not identity:
                return JSONResponse({"error": "Invalid API key"}, status_code=401)
            if identity["access"] == "read":
                return JSONResponse(
                    {"error": "Read-only API keys cannot open a browser session."}, status_code=403
                )
            # When this identity belongs to several workspaces, an API key is
            # scoped to only one of them. Honor an explicit workspace selection
            # rather than silently creating a session in the key's workspace.
            if organization_ref:
                member = store.membership_for_profile(
                    identity["organization_id"], identity["user_id"]
                )
                selected = store.switch_membership(
                    member.get("identity_id") if member else None, organization_ref
                )
                if selected:
                    session_token, _details = store.create_session_for_membership(
                        member["identity_id"], selected["user_id"], selected["organization_id"]
                    )
                    result = (session_token, selected) if session_token else None
                else:
                    result = None
            else:
                result = store.create_browser_session_for_token(api_key_value)
        else:
            result = store.create_browser_session(
                email, password, organization_ref=organization_ref
            )
        if not result:
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        session_token, identity = result
        response = JSONResponse({"status": "authenticated", "expiresAt": identity["expires_at"]})
        response.set_cookie(
            "hoja_session",
            session_token,
            httponly=True,
            secure=_cookie_secure(request),
            samesite="lax",
            max_age=14 * 24 * 60 * 60,
            path="/",
        )
        return response

    async def auth_workspaces(request: Request):
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
        keys = _limit_keys(request, email)
        if limiter.blocked(keys):
            return _too_many_attempts()
        if not _email_password_ok(email, str(body.get("password", ""))):
            limiter.record(keys)
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        return JSONResponse({"workspaces": store.matching_login_workspaces(email)})

    async def auth_logout(request: Request):
        csrf = _cookie_csrf_error(request, json_only=False)
        if csrf:
            return csrf
        session_token = request.cookies.get("hoja_session")
        if session_token:
            store.revoke_browser_session(session_token)
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie("hoja_session", path="/")
        return response

    async def auth_me(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return JSONResponse({"userId": uid, "organizationId": org})

    # ---- Attachment file uploads (images/files/videos for issue attachments)
    _UPLOAD_MAX_BYTES = 10 * 1024 * 1024
    _UPLOAD_TYPE_PREFIXES = ("image/", "video/")
    _UPLOAD_TYPE_EXACT = {"application/pdf", "text/plain", "text/markdown"}

    def _uploads_dir() -> Path:
        from cliniar_server.db import default_db_dir
        d = default_db_dir() / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def upload_create(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        csrf = _cookie_csrf_error(request, json_only=False)
        if csrf:
            return csrf
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "Invalid multipart body"}, status_code=400)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse({"error": "Missing 'file' field"}, status_code=400)
        content_type = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
        allowed = content_type in _UPLOAD_TYPE_EXACT or any(
            content_type.startswith(p) for p in _UPLOAD_TYPE_PREFIXES)
        if not allowed:
            return JSONResponse({"error": "File type not allowed"}, status_code=415)
        body = await upload.read()
        if len(body) > _UPLOAD_MAX_BYTES:
            return JSONResponse({"error": "File too large (10 MB max)"}, status_code=413)
        original = (getattr(upload, "filename", "") or "file").split("/")[-1].split("\\")[-1]
        ext = "".join(c for c in Path(original).suffix.lower() if c.isalnum() or c == ".")[:12]
        file_id = secrets.token_hex(16)
        stored = _uploads_dir() / f"{file_id}{ext}"
        stored.write_bytes(body)
        (_uploads_dir() / f"{file_id}.json").write_text(
            json.dumps({"org": org, "filename": original[:120],
                        "content_type": content_type, "size": len(body)}))
        return JSONResponse({"url": f"/api/uploads/{file_id}",
                             "title": original[:120] or "file"})

    async def upload_serve(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        file_id = request.path_params.get("file_id", "")
        if not file_id.replace("_", "").replace("-", "").isalnum() or len(file_id) > 64:
            return JSONResponse({"error": "Not found"}, status_code=404)
        meta_path = _uploads_dir() / f"{file_id}.json"
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            return JSONResponse({"error": "Not found"}, status_code=404)
        if meta.get("org") != org:
            return JSONResponse({"error": "Not found"}, status_code=404)
        candidates = [p for p in _uploads_dir().glob(f"{file_id}.*")
                      if p.suffix != ".json" and p.name != f"{file_id}.json"]
        if not candidates:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(
            candidates[0],
            media_type=meta.get("content_type") or "application/octet-stream",
            filename=meta.get("filename") or "file",
            content_disposition_type="inline",
        )

    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    async def ready(request: Request):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1")).scalar_one()
            from cliniar_server.db import inspect

            if not inspect(engine).has_table("schema_revision"):
                return JSONResponse({"status": "unavailable", "reason": "schema_outdated"}, status_code=503)
            if not schema_is_current(engine):
                return JSONResponse(
                    {"status": "unavailable", "reason": "schema_outdated"},
                    status_code=503,
                )
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    async def web_index(request: Request):
        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return JSONResponse({"error": "Web UI has not been built"}, status_code=503)
        return FileResponse(index)

    async def web_asset(request: Request):
        name = request.path_params["name"]
        asset = (_WEB_ROOT / "assets" / name).resolve()
        if _WEB_ROOT not in asset.parents or not asset.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(asset)

    async def web_brand_mark(request: Request):
        root = _WEB_ROOT.resolve()
        asset = (root / BRAND_MARK).resolve()
        if root not in asset.parents or not asset.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(asset, media_type="image/svg+xml")

    async def web_fallback(request: Request):
        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return JSONResponse({"error": "Web UI has not been built"}, status_code=503)
        return FileResponse(index)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal mcp_asgi
        # Each TestClient/server lifespan gets a fresh SDK session manager;
        # the SDK manager is intentionally single-use after shutdown.
        from cliniar_server.mcp_oauth import HojaOAuthProvider, build_server

        runtime_server = build_server(
            HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy), public_host=public_host
        )
        runtime_app = runtime_server.streamable_http_app(
            streamable_http_path="/", stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[public_host.strip(), public_host.strip() + ":*"],
            ),
        )
        mcp_asgi = runtime_app
        nonlocal broadcaster_task
        broadcaster_task = asyncio.create_task(_change_broadcaster())
        async with runtime_app.router.lifespan_context(runtime_app):
            if agent_dbos_url := os.environ.get("CLINIAR_AGENT_DBOS_URL"):
                from dbos import DBOS

                DBOS(config={"name": "cliniar-agent", "system_database_url": agent_dbos_url})
                DBOS.launch()
                try:
                    yield
                finally:
                    DBOS.destroy()
            else:
                yield
        if broadcaster_task is not None:
            broadcaster_task.cancel()

    async def _malformed_json_handler(request: Request, exc: Exception):
        return JSONResponse({"error": "Malformed JSON body."}, status_code=400)

    app = Starlette(
        routes=[
            Route("/graphql", graphql_server, methods=["POST", "GET"]),
            Route("/api/agent/chat", agent_chat_server, methods=["POST"]),
            Route("/api/agent/threads/get", agent_threads_list, methods=["GET"]),
            Route("/api/agent/threads/create", agent_threads_create, methods=["POST"]),
            Route("/api/agent/threads/get/{thread_id}", agent_thread_get, methods=["GET"]),
            Route("/api/agent/threads/update/{thread_id}", agent_thread_update, methods=["PATCH"]),
            Route("/api/agent/threads/delete/{thread_id}", agent_thread_delete, methods=["DELETE"]),
            *connector_routes(engine, store, _auth),
            WebSocketRoute("/ws", realtime_socket),
            Route("/auth/setup", auth_setup, methods=["GET", "POST"]),
            Route("/auth/invite/accept", auth_invite_accept, methods=["POST"]),
            Route("/onboarding/status", onboarding_status, methods=["GET"]),
            Route("/onboarding/bootstrap", onboarding_bootstrap, methods=["POST"]),
            Route("/auth/login", auth_login, methods=["POST"]),
            Route("/auth/workspaces", auth_workspaces, methods=["POST"]),
            Route("/auth/logout", auth_logout, methods=["POST"]),
            Route("/auth/me", auth_me, methods=["GET"]),
            Route("/api/uploads", upload_create, methods=["POST"]),
            Route("/api/uploads/{file_id}", upload_serve, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/ready", ready, methods=["GET"]),
            Route(
                "/.well-known/oauth-authorization-server/mcp",
                authorization_metadata_endpoint,
                methods=["GET", "OPTIONS"],
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                resource_metadata_endpoint,
                methods=["GET", "OPTIONS"],
            ),
            Mount("/mcp", app=mcp_endpoint),
            Route("/", web_index, methods=["GET"]),
            Route("/assets/{name:path}", web_asset, methods=["GET"]),
            Route(f"/{BRAND_MARK}", web_brand_mark, methods=["GET"]),
            Route("/{path:path}", web_fallback, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(_SecurityHeaders)
    app.add_exception_handler(json.JSONDecodeError, _malformed_json_handler)
    return app
