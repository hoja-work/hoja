"""FastAPI + Ariadne ASGI app with token auth middleware.

- POST /graphql : the wire-compatible endpoint the CLI talks to.
- Auth: raw token in Authorization header (with or without 'Bearer').
  Unknown/revoked token → HTTP 401 + {errors:[{message}]}.
- Never emits 429. GraphQL errors → HTTP 200 + {errors:[...], data:null}.
- Optional OFFLINE mode: if HOJA_SERVER_OPEN=1, any token maps to the only
  seeded identity in an unambiguous SQLite database.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from ariadne import format_error, graphql_sync
from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    InlineFragmentNode,
    OperationDefinitionNode,
    parse,
)
from sqlalchemy import and_, or_, select, text
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from hoja_server import (
    cors,
    google_login,
    google_oidc,
    identity_transfer,
    mail_events,
    membership_announce,
    org_limits,
    realtime,
    signin_pages,
    sync,
    tenant_context,
    tenant_hosts,
    uploads,
)
from hoja_server.cloudflare_realtime import CloudflareRealtime
from hoja_server.connectors_api import connector_binding_for, connector_routes
from hoja_server.db import (
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
from hoja_server.denials import DENY_HINT, READ_ONLY_HINT, hint_for, read_only_violation
from hoja_server.deployment import single_org_mode
from hoja_server.meetings import MeetingService
from hoja_server.meetings_api import MeetingHub, meeting_routes
from hoja_server.resolvers import build_schema
from hoja_server.runner_api import runner_routes
from hoja_server.secrets_box import SecretBoxError
from hoja_server.signup_config import (
    TURNSTILE_SCRIPT_URL,
    SignupConfigError,
    load_signup_config,
)
from hoja_server.signup_gate import (
    CROSS_SITE_REQUEST,
    INVALID_REQUEST,
    SignupGate,
)
from hoja_server.signup_store import SignupStore
from hoja_server.store import Store
from hoja_server.writer import Writer

_schema = build_schema()
_WEB_ROOT = Path(
    os.environ.get("HOJA_WEB_ROOT", Path(__file__).resolve().parent.parent / "frontend" / "dist")
)


# Packages owned by an optional extra. Checked before importing their modules
# so a documented install that omits one fails with the extra's name instead of
# a bare ModuleNotFoundError.
_SERVER_REQUIRED_PACKAGES = ("mcp",)
_AGENT_REQUIRED_PACKAGES = ("openai", "fastmcp", "pydantic_ai")


def _missing_packages(names: tuple[str, ...]) -> list[str]:
    """Names from ``names`` that are not installed, without importing them."""
    import importlib.util

    missing = []
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):  # a broken or half-installed parent
            missing.append(name)
    return missing


# Content-hashed brand mark: the URL changes with the bytes, so CDN caches cannot pin an old response.
BRAND_MARK = "hoja-clover.8371400e.svg"

#: The shape ``secrets.token_urlsafe(32)`` produces. Anything else is not an
#: invitation code and must never be reflected into a link or a cookie.
_INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


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
# Read-only sync endpoints reuse a resolved credential this long (matches the
# realtime socket revalidation interval) instead of re-querying it per request.
_SYNC_AUTH_TTL = 5.0
_LOGIN_FAILURE_LIMIT = 10
_LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_CSP_HTML = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"

# Served when the SPA bundle is absent. It is HTML on purpose: the security-headers
# middleware only attaches Content-Security-Policy to text/html responses, and a
# browser hitting an unbuilt deployment must still get clickjacking protection.
_UNBUILT_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    "<title>Hoja</title></head><body><h1>Web UI has not been built</h1>"
    "<p>Run <code>npm run build</code> in <code>frontend/</code>, or point "
    "<code>HOJA_WEB_ROOT</code> at a built bundle.</p></body></html>"
)
_graphql_log = logging.getLogger("hoja_server.graphql")
# Ariadne logs every resolver error, expected user errors included, with a full
# traceback; under a rich handler that costs seconds per bad request. Unexpected
# errors are logged once by _format_graphql_error, so Ariadne's copy is muted.
_ARIADNE_LOGGER = "hoja_server.graphql.ariadne"
logging.getLogger(_ARIADNE_LOGGER).addHandler(logging.NullHandler())
logging.getLogger(_ARIADNE_LOGGER).propagate = False
_security_log = logging.getLogger("hoja_server.security")


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


def _forwarded_client_ip(forwarded: str | None, peer: str,
                         networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> str:
    """The address the nearest proxy saw — never one the client typed.

    nginx's ``$proxy_add_x_forwarded_for`` *appends*, and Cloudflare appends the
    address it saw, so the leftmost entry of ``X-Forwarded-For`` is exactly what
    the caller put there. Walking from the right (the nearest hop, appended last)
    and stopping at the first address that is not one of our own proxies yields
    the first address a client could not have written.

    ``networks`` must list every hop between the edge and this process, including
    the Cloudflare ranges when the edge is Cloudflare; a hop left out of the list
    becomes "the client", which is wrong but still not spoofable.
    """
    hops = [hop.strip() for hop in (forwarded or "").split(",") if hop.strip()]
    for hop in reversed(hops):
        try:
            address = ipaddress.ip_address(hop)
        except ValueError:
            # An unparsable chain proves nothing; bucket by the peer we can see.
            return peer
        if not any(address in network for network in networks):
            return str(address)
    return hops[0] if hops else peer


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


class _BareMount:
    """Serve a mount's bare prefix (``/mcp``) as if it were requested as ``/mcp/``.

    Starlette's Mount keeps ``path`` and appends the matched prefix to
    ``root_path``; this builds the same child scope for the slash-less URL.
    """

    def __init__(self, app, prefix: str) -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope, receive, send) -> None:
        child = dict(scope)
        child["path"] = self.prefix + "/"
        if "raw_path" in child:
            child["raw_path"] = child["path"].encode()
        child["root_path"] = scope.get("root_path", "") + self.prefix
        await self.app(child, receive, send)


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
    meeting_sfu: object = "env",
) -> Starlette:
    target = resolve_database_target(database_url=database_url, db_path=db_path, tenant=tenant)
    engine = make_engine(target)
    store = Store(engine)
    writer = Writer(engine)
    # Set during lifespan when HOJA_AGENT_DBOS_URL enables durable agent turns.
    durable_agent = None
    # Sign-up is configured (or refused) once, at startup. An enabled mode with a
    # missing credential raises here — a clear error beats a silent allow.
    try:
        signup_config = load_signup_config()
    except SignupConfigError as exc:
        raise SignupConfigError(f"Sign-up configuration is invalid: {exc}") from exc
    signup_store = SignupStore(engine)
    signup_gate = SignupGate(config=signup_config, store=signup_store)
    # GraphQL/sync work runs in worker threads. SQLite serializes on the GIL per
    # fetched row, so more than a handful of concurrent readers only adds
    # contention; PostgreSQL drivers release the GIL while waiting on the server.
    default_slots = 8 if engine.dialect.name == "sqlite" else 24
    gql_slots = asyncio.Semaphore(int(os.environ.get("HOJA_GRAPHQL_CONCURRENCY", default_slots)))
    hub = realtime.RealtimeHub(store)
    meeting_service = MeetingService(engine)
    meeting_hub = MeetingHub(meeting_service)
    sfu = CloudflareRealtime.from_env() if meeting_sfu == "env" else meeting_sfu
    issue_snapshots = sync.SingleFlightCache(8)
    body_snapshots = sync.SingleFlightCache(32)
    org_slots = org_limits.OrgConcurrency()
    meta_cache = sync.SingleFlightCache(512)
    issue_lineage: dict = {}  # (org, cutoff, visibility) -> (seq, rows) of the newest snapshot
    sync_auth_cache: dict = {}

    def _validate_creds(creds):
        keys = [h for kind, h in creds if kind == "key"]
        sessions = [h for kind, h in creds if kind == "session"]
        return store.validate_credentials(keys, sessions)

    hub.validate = _validate_creds

    if open_mode is None:
        open_mode = os.environ.get("HOJA_SERVER_OPEN", "0") == "1"

    limiter = _FailureLimiter(engine, "login_failure", _LOGIN_FAILURE_LIMIT, _LOGIN_FAILURE_WINDOW_SECONDS)
    #: Probing is unauthenticated and names a workspace for a valid code, so it
    #: gets its own small budget rather than sharing the sign-in bucket.
    probe_limiter = _FailureLimiter(engine, "invite_probe", 20, 60.0)

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
        """Who to charge the attempt to.

        Forwarded headers are honoured only when the peer is one of our proxies,
        and then interpreted right-to-left so a client cannot pick its own bucket
        by typing a header. ``cf-connecting-ip`` is deliberately not read: it is
        just another header a client may send, and Cloudflare already appends the
        address it saw to ``x-forwarded-for`` (which the walk below finds).
        """
        peer = request.client.host if request.client else "unknown"
        if not _request_from_trusted_proxy(request):
            return peer
        return _forwarded_client_ip(
            request.headers.get("x-forwarded-for"), peer, proxy_networks,
        )

    def _limit_keys(request: Request, email: str) -> list[str]:
        keys = [f"ip:{_client_ip(request)}"]
        if email:
            keys.append(f"email:{email}")
        return keys

    security_policy_cache: dict[str, tuple[float, dict]] = {}

    def _org_security_policy(org_id: str) -> dict:
        """The workspace's security policy, cached for a few seconds.

        Every authenticated request needs it for the IP allowlist, and a settings
        save is rare. The TTL only bounds how long a revoked allowlist entry can
        still be honoured by this process; the mutation invalidates locally, and
        a restart or the next TTL tick covers the other workers.
        """
        from hoja_server import permissions as permission_rules
        now = time.monotonic()
        hit = security_policy_cache.get(org_id)
        if hit and now - hit[0] < 10:
            return hit[1]
        policy = permission_rules.security_policy(engine, org_id)
        security_policy_cache[org_id] = (now, policy)
        return policy

    def _drop_cached_policy(org_id: str) -> None:
        security_policy_cache.pop(org_id, None)

    from hoja_server import permissions as _permission_rules
    if _drop_cached_policy not in _permission_rules.POLICY_INVALIDATORS:
        _permission_rules.POLICY_INVALIDATORS.append(_drop_cached_policy)

    def _open_mode_identity(request: Request):
        if not open_mode or not _is_loopback(request):
            return None, None, None, False
        uid, org = _first_identity(engine)
        if uid:
            request.state.api_key_access = "read_write"
            return uid, org, None, False
        return None, None, None, False

    def _auth(request: Request):
        """Resolve the caller, then scope the rest of this request to its organization.

        Credential lookups run before any tenant is known, so they are a system
        operation; everything after a successful resolution is tenant-scoped.
        """
        with tenant_context.system():
            result = _resolve_auth(request)
        if result[0]:
            pinned = tenant_hosts.required_org(request.scope)
            if pinned and result[1] != pinned:
                return None, None, None, False
            # HOJA-490: an IP allowlist is enforced here, not in the UI. An
            # admin is exempt on purpose: a wrong allowlist must not lock the
            # workspace out of the screen that fixes it.
            from hoja_server import permissions as permission_rules
            policy = _org_security_policy(result[1])
            if policy.get("ipAllowlist"):
                actor = store.get_user(result[1], result[0])
                if not (actor and actor.get("admin")) and not permission_rules.ip_allowed(
                        policy, _client_ip(request)):
                    return None, None, None, False
            org_slots.enter(request.scope, result[1])
            tenant_context.enter(result[1], result[0], getattr(request.state, "actor_kind", "human"))
        return result

    def _resolve_auth(request: Request):
        raw = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if raw and token:
            # A valid Authorization header is authoritative; cookies are not consulted.
            key = store.resolve_token(token)
            if key:
                store.touch_api_key(key["id"], key.get("last_used_at"))
                request.state.api_key_access = key["access"]
                # An agent principal's key writes as the agent (HOJA-602-A-3).
                request.state.actor_kind = key.get("kind") or "human"
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

    def _socket_identity(websocket):
        """_auth for a realtime socket, plus the credential to revalidate it by."""
        uid, org, _identity_id, _is_session = _auth(websocket)
        if not uid:
            return None, None, None
        raw = websocket.headers.get("authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if token and store.resolve_token(token):
            return uid, org, ("key", token_hash(token))
        session_token = websocket.cookies.get("hoja_session")
        if session_token and _is_session:
            return uid, org, ("session", token_hash(session_token))
        return uid, org, None  # loopback open mode: nothing to revoke

    def _auth_sync(request: Request):
        """_auth for the read-only /sync endpoints, cached briefly per credential."""
        key = (request.headers.get("authorization") or "", request.cookies.get("hoja_session") or "",
               request.client.host if request.client else "", request.headers.get("host", "").lower())
        now = time.monotonic()
        hit = sync_auth_cache.get(key)
        if hit is not None and now - hit[0] < _SYNC_AUTH_TTL:
            request.state.api_key_access = hit[2]
            request.state.actor_kind = hit[3] if len(hit) > 3 else "human"
            # a cache hit skips _auth, so it must scope the request itself
            org_slots.enter(request.scope, hit[1][1])
            tenant_context.enter(hit[1][1], hit[1][0], request.state.actor_kind)
            return hit[1]
        result = _auth(request)
        if result[0]:
            if len(sync_auth_cache) > 10000:
                sync_auth_cache.clear()
            sync_auth_cache[key] = (now, result, getattr(request.state, "api_key_access", "read_write"),
                                    getattr(request.state, "actor_kind", "human"))
        return result

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

    def _mcp_bearer_credential(token: str):
        """Enumerated cross-tenant lookup of one MCP bearer, by token hash.

        An MCP request presents its credential before any tenant is known, so these
        are the only reads here that may run in system context: an OAuth access
        token matched by hash, then an API key matched the same way. The result is
        an identity and the organization to bind, never another tenant's rows.
        """
        from datetime import datetime, timezone

        from sqlalchemy import select

        from hoja_server.db import oauth_access_token

        with tenant_context.system():
            with engine.connect() as conn:
                row = (
                    conn.execute(
                        select(oauth_access_token).where(
                            oauth_access_token.c.token_hash == token_hash(token),
                            oauth_access_token.c.revoked_at.is_(None),
                            oauth_access_token.c.expires_at > datetime.now(timezone.utc).isoformat(),
                        )
                    )
                    .mappings()
                    .first()
                )
            if row is not None:
                return {
                    "user_id": row["user_id"],
                    "org_id": row["organization_id"],
                    "scopes": row["scopes"],
                    "client_id": row["client_id"],
                }
            # An API key authenticates the same identity as an OAuth access token, so
            # the MCP surface does not require a second credential type.
            # read/read_write maps onto hoja:read/hoja:write, keeping write tools
            # gated by scope.
            key = store.resolve_token(token)
            if key is None:
                return None
            from hoja_server.mcp_oauth import ALL_SCOPES, READ_SCOPE

            scopes = [READ_SCOPE] if key.get("access") == "read" else list(ALL_SCOPES)
            return {
                "user_id": key["user_id"],
                "org_id": key["organization_id"],
                "scopes": json.dumps(scopes),
                "client_id": "api-key",
            }

    def _mcp_oauth_auth(request: Request):
        """Resolve a valid MCP bearer to the same pinned Hoja identity."""
        raw = request.headers.get("authorization", "")
        if not raw.lower().startswith("bearer "):
            return None
        principal = _mcp_bearer_credential(raw[7:].strip())
        if principal is None:
            return None
        pinned = tenant_hosts.required_org(request.scope)
        if pinned and principal["org_id"] != pinned:
            return None
        # Bind before the membership and workspace reads: those are tenant rows, and
        # under RLS an unbound read returns no rows instead of raising, so a missing
        # bind reads as "not a member" rather than as an error.
        tenant_context.bind(principal["org_id"])
        member = store.membership_for_profile(principal["org_id"], principal["user_id"])
        if not member:
            return None
        return {
            **principal,
            "identity_id": member.get("identity_id"),
            "org_name": (store.workspace(principal["org_id"]) or {}).get("name"),
        }

    missing = _missing_packages(_SERVER_REQUIRED_PACKAGES)
    if missing:
        raise RuntimeError(
            "hoja-serve cannot start: the MCP endpoint at /mcp is mounted by the "
            f"server, but {', '.join(missing)} is not installed. "
            "Install the server extra: python -m pip install 'hoja-cli[server]' "
            "(from a checkout: uv sync --extra dev --extra server)."
        )
    from hoja_server.mcp_oauth import HojaOAuthProvider, build_server

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
    # api.hoja.work is the connector broker in a shared-tenancy deployment: a real
    # host this app serves, and not a workspace of anybody. Without it the apex rule
    # would redirect the one callback Calendly and Sentry can register.
    broker_host = os.environ.get("HOJA_CONNECTOR_BROKER_HOST", "").strip()
    host_map = tenant_hosts.TenantHosts(
        engine, public_host if trust_proxy else "", broker=broker_host
    )

    def _request_from_trusted_proxy(request: Request) -> bool:
        if not trust_proxy:
            return False
        peer = request.client.host if request.client else None
        try:
            address = ipaddress.ip_address(peer) if peer else None
        except ValueError:
            return False
        return bool(address and any(address in network for network in proxy_networks))
    mcp_provider = HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy, public_host=public_host)
    mcp_server = build_server(mcp_provider, public_host=public_host)
    mcp_pinned: dict = {}

    async def _serve_pinned_mcp(ready: asyncio.Event) -> None:
        """Run one MCP app per pinned workspace host, in this task so their lifespans open and close together."""
        async with contextlib.AsyncExitStack() as stack:
            while True:
                try:
                    hosts = await run_in_threadpool(host_map.hosts)
                    for host, org in hosts.items():
                        if (host, org) in mcp_pinned:
                            continue
                        pinned_app = build_server(
                            HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy, public_host=host,
                                              required_org=org),
                            public_host=host,
                        ).streamable_http_app(
                            streamable_http_path="/", stateless_http=True,
                            transport_security=TransportSecuritySettings(
                                enable_dns_rebinding_protection=True, allowed_hosts=[host, host + ":*"]),
                        )
                        await stack.enter_async_context(pinned_app.router.lifespan_context(pinned_app))
                        mcp_pinned[(host, org)] = pinned_app
                except Exception:
                    logging.getLogger("hoja_server").warning("pinned MCP hosts refresh failed", exc_info=True)
                ready.set()
                await asyncio.sleep(host_map.ttl)

    def _origin_host(request: Request) -> str:
        """The public host OAuth metadata names: a pinned workspace host, else the canonical one."""
        if tenant_hosts.required_org(request.scope):
            return tenant_hosts.bare(request.headers.get("host"))
        return public_host
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
        pinned_org = tenant_hosts.required_org(scope)
        host_ok = (incoming_host == canonical_host or incoming_host.startswith(canonical_host + ":")
                   or bool(pinned_org))
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
        if pinned_org:
            pinned_app = mcp_pinned.get((tenant_hosts.bare(incoming_host), pinned_org))
            if pinned_app is None:
                response = JSONResponse({"error": "MCP is starting for this workspace."}, status_code=503,
                                        headers={"Retry-After": "5"})
                await response(scope, receive, send)
                return
            await pinned_app(scope, receive, send)
            return
        await mcp_asgi(scope, receive, send)

    async def authorization_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        origin = f"https://{_origin_host(request)}" if trust_proxy else "https://mcp.invalid"
        issuer = origin + "/mcp"
        return JSONResponse({"issuer": issuer, "authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token", "registration_endpoint": issuer + "/register", "revocation_endpoint": issuer + "/revoke", "scopes_supported": ["hoja:read", "hoja:write"], "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"]})

    async def resource_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        issuer = (f"https://{_origin_host(request)}" if trust_proxy else "https://mcp.invalid") + "/mcp"
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
        uid, org, cred = await run_in_threadpool(_socket_identity, websocket)
        if uid:  # resolved in a worker thread: its context copy is gone, bind here
            tenant_context.enter(org, uid, getattr(websocket.state, "actor_kind", "human"))
        await websocket.accept(subprotocol=subprotocol)
        if not uid:
            await websocket.close(code=4401)
            return
        raw_after = websocket.query_params.get("after")
        cursor = None
        if raw_after is not None:
            try:
                cursor = int(raw_after)
            except ValueError:
                await websocket.close(code=4400)
                return
            if not 0 <= cursor <= _MAX_SEQ:
                await websocket.close(code=4400)
                return
        proto = 2 if websocket.query_params.get("v") == "2" else 1
        await hub.serve(websocket, uid, org, cred, cursor, proto)

    async def agent_chat_server(request: Request):
        # HOJA-602-A-1 (F6): this route authenticates like every other agent route, so a
        # read-only credential cannot open a conversation or spend model budget, and the
        # CSRF rule is the same one the thread routes apply.
        uid, org, identity_id, is_browser, err = await _agent_auth(request)
        if err:
            return err
        body = await request.json()
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"error": "messages is required"}, status_code=400)
        missing = _missing_packages(_AGENT_REQUIRED_PACKAGES)
        if missing:
            return JSONResponse(
                {
                    "error": "Agent chat is unavailable on this server: the 'agent' extra is "
                    f"not installed ({', '.join(missing)} missing). Install it with "
                    "python -m pip install 'hoja-cli[server,agent]' "
                    "(from a checkout: uv sync --extra dev --extra server --extra agent)."
                },
                status_code=503,
            )
        from hoja_server.agent_chat import sse_response, stream_reply
        from hoja_server.schema_sdl import SCHEMA_SDL
        base_context = {
            "request": request,
            "store": store,
            "writer": writer,
            "org_id": org,
            "user_id": uid,
            "identity_id": identity_id,
            "browser_session": is_browser,
            "access": getattr(request.state, "api_key_access", "read_write"),
        }
        try:
            thread_id = body.get("threadId") if isinstance(body, dict) else None
            connector = connector_binding_for(engine, org, uid)
            if durable_agent is not None:
                gen = durable_agent.stream_reply(store, org, uid, messages, base_context, thread_id, connector)
            else:
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
        """Authenticate an agent route: a caller, a non-read credential for a write, CSRF."""
        uid, org, identity_id, is_browser = _auth(request)
        if not uid:
            return None, None, None, None, JSONResponse(
                {"errors": [{"message": "Authentication required", "extensions": {"code": "AUTHENTICATION_ERROR"}}]},
                status_code=401,
            )
        if request.method != "GET" and getattr(request.state, "api_key_access", "read_write") == "read":
            return None, None, None, None, JSONResponse(
                {"errors": [{"message": "This API key is read-only.",
                             "extensions": {"code": "FORBIDDEN", "agentHint": READ_ONLY_HINT}}]},
                status_code=403,
            )
        csrf = _cookie_csrf_error(request, json_only=True)
        if csrf:
            return None, None, None, None, csrf
        return uid, org, identity_id, is_browser, None

    async def agent_threads_list(request: Request):
        from hoja_server import agent_threads
        uid, org, _identity_id, _is_browser, err = await _agent_auth(request)
        if err:
            return err
        return JSONResponse(agent_threads.list_threads(store, org, uid))

    async def agent_threads_create(request: Request):
        from hoja_server import agent_threads
        uid, org, _identity_id, _is_browser, err = await _agent_auth(request)
        if err:
            return err
        body = await request.json()
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list):
            return JSONResponse({"error": "messages is required"}, status_code=400)
        return JSONResponse(agent_threads.create_thread(store, org, uid, messages))

    async def agent_thread_get(request: Request):
        from hoja_server import agent_threads
        uid, org, _identity_id, _is_browser, err = await _agent_auth(request)
        if err:
            return err
        messages = agent_threads.get_messages(store, org, uid, request.path_params["thread_id"])
        if messages is None:
            return JSONResponse({"error": "Thread not found"}, status_code=404)
        return JSONResponse(messages)

    async def agent_thread_update(request: Request):
        from hoja_server import agent_threads
        uid, org, _identity_id, _is_browser, err = await _agent_auth(request)
        if err:
            return err
        body = await request.json()
        title = body.get("title") if isinstance(body, dict) else None
        thread = agent_threads.update_thread(store, org, uid, request.path_params["thread_id"], title if isinstance(title, str) else None)
        if thread is None:
            return JSONResponse({"error": "Thread not found"}, status_code=404)
        return JSONResponse(thread)

    async def agent_thread_delete(request: Request):
        from hoja_server import agent_threads
        uid, org, _identity_id, _is_browser, err = await _agent_auth(request)
        if err:
            return err
        if not agent_threads.delete_thread(store, org, uid, request.path_params["thread_id"]):
            return JSONResponse({"error": "Thread not found"}, status_code=404)
        return JSONResponse({"success": True})

    async def graphql_server(request: Request):
        uid, org, identity_id, is_browser_session = await run_in_threadpool(_auth, request)
        if uid:  # resolved in a worker thread: its context copy is gone, bind here
            tenant_context.enter(org, uid, getattr(request.state, "actor_kind", "human"))
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
            if read_only_violation(query, access):
                return JSONResponse(
                    {"errors": [{"message": "This API key is read-only.",
                                 "extensions": {"code": "FORBIDDEN", "agentHint": READ_ONLY_HINT}}]},
                    status_code=403,
                )
        context = {
            "request": request,
            "store": store,
            "cache": {},
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
                logger=_ARIADNE_LOGGER,
                debug=(os.environ.get("HOJA_SERVER_DEBUG", "0") == "1"),
            )
        if isinstance(query, str) and "mutation" in query:
            hub.kick()
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
        if _is_tenant_origin(request):
            # A workspace subdomain has no local credential surface: there is one
            # sign-in page, on the control plane.
            return JSONResponse(
                {"error": "Sign in at the workspace entry.",
                 "reason": "sign_in_moved",
                 "signInUrl": _canonical_login_url(request)},
                status_code=403,
            )
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

    async def internal_membership(request: Request):
        """A tenant announcing that an address belongs to it.

        Authenticated by the tenant's own derived key: the control plane holds
        the master, so it can reconstruct the only key that host may sign with,
        and a compromised tenant cannot forge an announcement for another.
        """
        import hashlib
        import hmac as hmac_module

        master = signup_config.identity_transfer_master
        if not master:
            return JSONResponse({"error": "Federation is not configured."}, status_code=503)
        raw = await request.body()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "Invalid body."}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "Invalid body."}, status_code=400)
        expires_at = payload.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, str):
            return JSONResponse({"error": "expires_at must be an ISO timestamp."}, status_code=400)
        slug = str(payload.get("slug") or "").strip().lower()
        email = str(payload.get("email") or "").strip().lower()
        if not slug or "@" not in email:
            return JSONResponse({"error": "slug and email are required."}, status_code=400)
        key = identity_transfer.tenant_key(
            master, f"{slug}.{signup_config.tenant_base_domain}").encode()
        expected = hmac_module.new(key, raw, hashlib.sha256).hexdigest()
        given = request.headers.get("x-hoja-signature") or ""
        # Compare digests only on a hex string: compare_digest raises on
        # non-ASCII input, which turned a junk header into a 500.
        if not re.fullmatch(r"[0-9a-f]{64}", given) or not hmac_module.compare_digest(expected, given):
            _security_log.warning("membership.announce_rejected slug=%s", slug)
            return JSONResponse({"error": "Bad signature."}, status_code=401)
        signup_store.record_membership_hint(
            slug=slug, email=email,
            role=str(payload.get("role") or "member"),
            expires_at=expires_at,
        )
        return JSONResponse({"ok": True})

    async def auth_invite_probe(request: Request):
        """Is this invitation code still usable, and where does it lead?

        Deliberately answers with the workspace name only: a valid code is a
        secret the holder already has, so naming its workspace tells them
        nothing they could not read off the link. An invalid code never says
        whether it existed.
        """
        keys = [f"ip:{_client_ip(request)}"]
        payload, body_error = await _read_json_object(request, _SIGNUP_BODY_LIMIT)
        if body_error is not None:
            return JSONResponse({"valid": False, "reason": "invalid_request"},
                                status_code=body_error.status_code)
        found = store.peek_membership_invitation(str((payload or {}).get("code") or ""))
        if found is None:
            # The budget is spent by misses only, and asked after the lookup, so
            # a shared address never loses the answer to its own invitation.
            if probe_limiter.blocked(keys):
                return JSONResponse(
                    {"valid": False, "reason": "too_many_attempts"}, status_code=429,
                    headers={"cache-control": "no-store", "Retry-After": "60"},
                )
            probe_limiter.record(keys)
            return JSONResponse(
                {"valid": False, "reason": "invite_invalid_or_expired"},
                status_code=404, headers={"cache-control": "no-store"},
            )
        return JSONResponse(
            {"valid": True, "workspace": {"name": found.get("name"),
                                          "urlKey": found.get("url_key")}},
            headers={"cache-control": "no-store"},
        )

    def _announce_member(request: Request, email: str) -> None:
        """Tell the control plane about a membership we just proved.

        Called only where the address is established: an accepted invitation
        (holding the code proves control of the mailbox) or a completed Google
        sign-in on this host. Best effort, and never on the request path's
        critical section.
        """
        host = _signup_host(request).split(":")[0].lower()
        slug = host.split(".")[0]
        expires = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        membership_announce.announce(
            tenant_key=signup_config.identity_transfer_key,
            control_plane_url=signup_config.control_plane_url,
            slug=slug,
            email=email,
            expires_at=expires,
        )

    async def auth_invite_accept(request: Request):
        body = await request.json()
        code = str(body.get("code", ""))
        if body.get("password") in (None, ""):
            # A workspace that signs people in with Google has no password to
            # create. The code plus the verified session is the whole proof, and
            # the store re-checks that the session's address is the invited one.
            session_token = request.cookies.get("hoja_session")
            session = store.resolve_browser_session(session_token) if session_token else None
            identity_id = None
            if session:
                member = store.membership_for_profile(
                    session["organization_id"], session["user_id"]
                )
                identity_id = member.get("identity_id") if member else None
            organization = store.accept_membership_invitation_by_identity(identity_id, code)
            if not organization:
                return JSONResponse(
                    {
                        "error": "Invite code is invalid, expired, or does not match this account.",
                        "reason": "invite_refused",
                    },
                    status_code=401,
                )
            return JSONResponse({"status": "accepted", "organization": organization})
        try:
            result = store.accept_new_member_invitation(
                code, str(body.get("email", "")), str(body.get("password", ""))
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not result:
            return JSONResponse(
                {"error": "Invite code is invalid, expired, or does not match that email."},
                status_code=401,
            )
        session_token, identity = result
        _announce_member(request, str(body.get("email") or "").strip().lower())
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
        # `ownerBound` says only that a provisioner pinned this instance to one
        # address; it never returns the address itself.
        return JSONResponse(
            {
                "initialized": initialized,
                "singleOrg": single_org_mode(),
                "ownerBound": google_login.owner_email() is not None,
            }
        )

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
        # A hosted tenant is provisioned with an owner address before it has a
        # user. The onboarding form must not let another address claim it first.
        owner = google_login.owner_email()
        if owner and email != owner:
            return JSONResponse(
                {"error": "This workspace is reserved for its owner."}, status_code=403
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
                        visibility="public",
                        timezone="UTC",
                        issue_counter=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(team_member.insert().values(team_id=tid, user_id=uid, role="owner"))
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
        # Best-effort: the workspace already exists, so a mail failure is logged
        # and leaves a queued row, never a failed sign-up.
        mail_events.send_welcome(
            engine,
            organization_id=org_id,
            organization_name=workspace_name,
            user_id=uid,
            name=name,
            email=email,
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
        method = "api_key" if body.get("apiKey") else "password"
        if limiter.blocked(keys):
            _security_log.info("auth.login method=%s outcome=rate_limited", method)
            return _too_many_attempts()
        response = await _login(request)
        if response.status_code == 401:
            limiter.record(keys)
        _security_log.info(
            "auth.login method=%s status=%s outcome=%s",
            method,
            response.status_code,
            {
                200: "authenticated",
                409: "workspace_choice",
                401: "invalid_credentials",
                403: "forbidden",
                503: "schema_outdated",
            }.get(response.status_code, "refused"),
        )
        return response

    async def _login(request: Request):
        if _is_tenant_origin(request):
            return JSONResponse(
                {"error": "Sign in at the workspace entry.",
                 "reason": "sign_in_moved",
                 "signInUrl": _canonical_login_url(request)},
                status_code=403,
            )
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
        from hoja_server import permissions as permission_rules
        identity_org = result[1].get("organization_id")
        if identity_org:
            policy = _org_security_policy(identity_org)
            if not api_key_value and not permission_rules.signin_method_allowed(policy, "password"):
                return JSONResponse(
                    {"error": "Password sign-in is turned off for this workspace.",
                     "reason": "signin_method_disabled"},
                    status_code=403,
                )
            if policy.get("ipAllowlist"):
                actor = store.get_user(identity_org, result[1]["user_id"])
                if not (actor and actor.get("admin")) and not permission_rules.ip_allowed(
                        policy, _client_ip(request)):
                    return JSONResponse(
                        {"error": "This network is not allowed for this workspace.",
                         "reason": "ip_not_allowed"},
                        status_code=403,
                    )
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
        if _is_tenant_origin(request):
            return JSONResponse(
                {"error": "Sign in at the workspace entry.",
                 "reason": "sign_in_moved",
                 "signInUrl": _canonical_login_url(request)},
                status_code=403,
            )
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
        keys = _limit_keys(request, email)
        if limiter.blocked(keys):
            return _too_many_attempts()
        if not _email_password_ok(email, str(body.get("password", ""))):
            limiter.record(keys)
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        return JSONResponse({"workspaces": store.matching_login_workspaces(email)})

    def _revoke_every_session(request: Request) -> list[str]:
        """Revoke every session this browser holds, and name the ones it held.

        A workspace session (``hoja_session``) and a hosted sign-up session
        (``hoja_signup_session``) are independent families. Signing out of one and
        leaving the other live is how a "signed out" browser signs itself back in:
        ``/signup`` reads a live sign-up session as ``google_verified`` and, when
        the person already has an active workspace, redirects them straight back
        into it. That is the "sign out doesn't kick me out" report.
        """
        cleared: list[str] = []
        workspace_token = request.cookies.get("hoja_session")
        if workspace_token:
            store.revoke_browser_session(workspace_token)
            cleared.append("workspace")
        signup_token = request.cookies.get(_SIGNUP_SESSION_COOKIE)
        if signup_token:
            signup_store.revoke_session(signup_token)
            cleared.append("signup")
        return cleared

    async def auth_logout(request: Request):
        csrf = _cookie_csrf_error(request, json_only=False)
        if csrf:
            _security_log.info("auth.logout scope=all outcome=refused_csrf")
            return csrf
        cleared = _revoke_every_session(request)
        _security_log.info("auth.logout scope=all cleared=%s", ",".join(cleared) or "-")
        response = JSONResponse({"status": "logged_out", "cleared": cleared})
        for cookie in ("hoja_session", _SIGNUP_SESSION_COOKIE):
            response.delete_cookie(cookie, **_session_cookie_attrs(request))
        return response

    async def auth_me(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return JSONResponse({"userId": uid, "organizationId": org})

    # ---- Hosted sign-up: Google-only identity, then the verification gate ----
    #
    # The three layers live in their own modules (google_oidc, turnstile,
    # signup_gate); these handlers only move cookies, calls and JSON. No handler
    # here creates a database, container, route or DNS record, and none calls a
    # provisioner: success is one signup_eligibility row for it to pick up.
    _SIGNUP_SESSION_COOKIE = "hoja_signup_session"
    _OIDC_FLOW_COOKIE = "hoja_oidc_flow"
    _SIGNUP_BODY_LIMIT = 8 * 1024
    #: Carries an invitation code across the Google round-trip. The browser
    #: leaves this origin for the control plane and comes back, so the code has
    #: to live somewhere that survives that hop without being trusted as a
    #: return URL: a short-lived, httpOnly cookie on this host.
    _INVITE_COOKIE = "hoja_invite_pending"
    #: Long enough for one Google round-trip on a slow phone, short enough that
    #: a stale code is not resurrected days later.
    _INVITE_COOKIE_MAX_AGE = 15 * 60
    #: Where the page starts Google sign-in: same origin, back to the page itself.
    _GOOGLE_SIGN_IN_URL = "/auth/google/start?return_to=%2Fsignup"

    def _session_cookie_attrs(request: Request) -> dict:
        """HttpOnly, Secure, SameSite=Lax, Path=/ — the browser_session rule."""
        return {
            "httponly": True,
            "secure": _cookie_secure(request),
            "samesite": "lax",
            "path": "/",
        }

    def _safe_return_to(value: str | None) -> str:
        """Only a path on this host: an absolute URL here would be an open redirect."""
        candidate = (value or "").strip()
        if not candidate.startswith("/") or candidate.startswith("//"):
            return "/"
        if "\\" in candidate or any(ch.isspace() for ch in candidate):
            return "/"
        parts = urlsplit(candidate)
        if parts.scheme or parts.netloc:
            return "/"
        return candidate

    def _tenant_transfer_target(value: str | None) -> str | None:
        """A return target on one of OUR tenant hosts, normalised, or None."""
        return identity_transfer.tenant_target(value)

    def _signup_host(request: Request) -> str:
        forwarded = (
            request.headers.get("x-forwarded-host")
            if _request_from_trusted_proxy(request)
            else None
        )
        return (forwarded or request.headers.get("host") or "").split(",")[0].strip()

    def _wants_html(request: Request) -> bool:
        """Content negotiation: a browser gets a page, a client keeps the JSON contract."""
        if request.query_params.get("format") == "json":
            return False
        accept = request.headers.get("accept", "")
        first = accept.split(",")[0].lower()
        return "text/html" in first

    def _signup_identity(request: Request):
        """The google_verified person behind the sign-up cookie, or None."""
        return signup_store.resolve_session(request.cookies.get(_SIGNUP_SESSION_COOKIE))

    def _invite_code(value: object) -> str | None:
        """An invitation code in the only shape we issue, or None.

        Codes come from ``secrets.token_urlsafe(32)``, so requiring that
        alphabet is not a limit on real users: it is what keeps an arbitrary
        string from being reflected into a link or a cookie.
        """
        candidate = str(value or "").strip()
        return candidate if _INVITE_CODE_RE.fullmatch(candidate) else None

    def _control_plane_host() -> str:
        return (urlsplit(signup_config.control_plane_url or "").hostname or "").lower()

    def _is_tenant_origin(request: Request) -> bool:
        """True on a provisioned tenant host, false on the control plane or bare OSS."""
        if not signup_config.control_plane_url:
            return False
        return _signup_host(request).split(":")[0].lower() != _control_plane_host()

    def _tenant_origin_for(request: Request) -> str | None:
        host = _signup_host(request).split(":")[0].lower()
        return _tenant_transfer_target(f"https://{host}/")

    def _canonical_login_url(request: Request) -> str | None:
        """The one sign-in entry for this request's host, or None if unhosted."""
        control_plane = signup_config.control_plane_url
        if not control_plane:
            return None
        if not _is_tenant_origin(request):
            return "/signin"
        back = _tenant_origin_for(request)
        if back is None:
            return f"{control_plane}/signin"
        return f"{control_plane}/signin?next={quote(back, safe='')}"

    def _switch_account_url(request: Request) -> str:
        """A retry that actually changes something: it ends the control session first.

        Linking straight back to /signin re-mints a handoff for the same verified
        identity, so a refused person would loop forever between the entry and the
        refusal. Signing out is the only action that can produce a different
        Google account.
        """
        control_plane = signup_config.control_plane_url or ""
        next_param = request.query_params.get("next") or ""
        back = _tenant_transfer_target(next_param) if next_param else None
        if back is None:
            back = _tenant_origin_for(request)
        suffix = f"?next={quote(back, safe='')}" if back else ""
        if _is_tenant_origin(request):
            return f"{control_plane}/signin/signout{suffix}"
        return f"/signin/signout{suffix}"

    def _signin_failure(
        request: Request,
        *,
        error: str,
        reason: str,
        status_code: int,
        host: str | None = None,
        email: str | None = None,
    ) -> Response:
        """One refusal, two audiences: a page a person can act on, and the JSON contract.

        The refusal stays a refusal: same status, same reason code, no statement about
        whether the address or workspace exists. The error id is the only new data, and
        it is what makes a support conversation possible instead of a dead end.
        """
        error_id = secrets.token_hex(6)
        _security_log.warning(f"auth.denied id={error_id} host={host or '-'} reason={reason}")
        headers = {"X-Hoja-Error-Id": error_id}
        if _wants_html(request):
            page = signin_pages.denial_page(
                host=host or _signup_host(request),
                email=email,
                reason=reason,
                error_id=error_id,
                entry_url=_switch_account_url(request),
            )
            return HTMLResponse(
                page, status_code=status_code, headers={**headers, "cache-control": "no-store"}
            )
        return JSONResponse(
            {"error": error, "reason": reason, "errorId": error_id},
            status_code=status_code,
            headers=headers,
        )

    async def signup_public_config(request: Request):
        """Everything the sign-up page needs, and nothing it must not have.

        Public by contract: the Turnstile *site* key, which mode is on, and where
        Google sign-in starts. The site key is public by design (it ships in the
        page bundle); the siteverify secret, the OIDC client secret, the client id
        and the expected hostname are never in this response, and the test asserts
        that the body contains no secret and no unexpected field.

        ``available`` is the fail-closed flag: with no site key the page renders
        "sign-up unavailable" instead of a widget that could never be verified.
        ``no-store`` because a stale mode or a stale key is a trap after a deploy.
        """
        host = _signup_host(request).split(":")[0].lower()
        control_plane = signup_config.control_plane_url
        tenant_origin = bool(
            control_plane and host != (urlsplit(control_plane).hostname or "").lower()
        )
        if tenant_origin:
            # A tenant borrows the control plane's registered Google redirect.
            # Returning to `/` means the verified handoff lands in the app, not
            # on the tenant's closed sign-up page.
            sign_in_url = "/auth/google/start?return_to=%2F"
        elif signup_config.configured:
            sign_in_url = _GOOGLE_SIGN_IN_URL
        else:
            sign_in_url = None
        return JSONResponse(
            {
                "mode": signup_config.mode,
                "available": signup_config.public_widget_available,
                "sitekey": signup_config.turnstile_site_key or None,
                "signInUrl": sign_in_url,
            # The one sign-in entry. On a tenant host it points at the control
            # plane and carries the tenant to return to; on the control plane it
            # is the local page. `credentialSignIn` is false on a tenant: there
            # is no email/password or API-key form on a workspace subdomain.
            "loginUrl": _canonical_login_url(request),
            "credentialSignIn": not tenant_origin,
                "hosted": bool(control_plane),
                "widgetScriptUrl": TURNSTILE_SCRIPT_URL,
            },
            headers={"cache-control": "no-store"},
        )

    def _tenant_transfer_redirect(
        *,
        email: str,
        subject: str,
        name: str | None,
        picture: str | None,
        tenant_target: str,
        source: str,
    ) -> Response:
        """Mint the per-tenant handoff from an already verified identity."""
        master = signup_config.identity_transfer_master
        if not master:
            return JSONResponse(
                {"error": "Identity handoff is not configured on this deployment.",
                 "reason": "transfer_unconfigured"},
                status_code=503,
            )
        target_host = urlsplit(tenant_target).hostname or ""
        transfer = identity_transfer.issue(
            key=identity_transfer.tenant_key(master, target_host),
            audience=target_host,
            email=email.strip().lower(),
            subject=subject,
            name=name,
            picture=picture,
            email_verified=True,
        )
        _security_log.info(
            "auth.handoff source=%s target_host=%s", source, target_host
        )
        # Build the handoff from the host, never from the whole target: a target
        # may carry `?invite=<code>`, and concatenating a path onto that both
        # swallows `/auth/google/finish` into the parameter and puts the signed
        # token in a URL that lands in history and access logs.
        response = RedirectResponse(
            f"https://{target_host}/auth/google/finish?t={quote(transfer, safe='')}",
            status_code=302,
        )
        response.delete_cookie(_OIDC_FLOW_COOKIE, path="/")
        return response

    def _workspace_origin(slug: str) -> str:
        return f"https://{slug}.{signup_config.tenant_base_domain}/"

    def _handoff_start_url(origin: str) -> str:
        """Start the handoff for a specific workspace, asking who is signing in.

        ``choose_account`` is what makes "Continue to sign in" an actual sign-in
        rather than a resume: without it the start handler mints the tenant
        handoff from the control session and never contacts Google, so a person
        who signed out lands back in the previous identity.
        """
        control_plane = signup_config.control_plane_url or ""
        return (
            f"{control_plane}/auth/google/start?return_to={quote(origin, safe='')}"
            "&choose_account=1"
        )

    async def sign_in_invited(request: Request):
        """Send a person to the workspace their invitation link names.

        The control plane keeps no membership directory, so this does not try to
        guess who invited whom. The link itself names the host, and
        ``_tenant_transfer_target`` refuses anything that is not one of our own
        tenant origins — which is what keeps a pasted link from becoming an open
        redirect. Identity is not asserted here at all: the tenant still runs its
        own sign-in and its own membership check on arrival.
        """
        if not _same_origin(request.headers.get("origin"), request.headers):
            # A form post from anywhere could otherwise bounce a signed-in person
            # to a workspace of someone else's choosing. It grants nothing, but a
            # redirect is still something we should not perform on request.
            return HTMLResponse(
                signin_pages.no_workspace_page(
                    email=None,
                    sign_out_url="/auth/google/logout",
                    invite_action="/signin/invited",
                    invite_error="That form came from another site. Paste the link again.",
                ),
                status_code=403,
                headers={"cache-control": "no-store"},
            )
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > 8 * 1024:
            return JSONResponse({"error": "Request too large."}, status_code=413)
        form = await request.form()
        raw = str(form.get("link") or "").strip()
        candidate = raw if "://" in raw else f"https://{raw}"
        parts = urlsplit(candidate)
        code = ""
        for chunk in (parts.query, parts.fragment):
            code = code or (parse_qs(chunk).get("invite") or [""])[0]
        target = _tenant_transfer_target(f"https://{parts.hostname or ''}/")
        identity = _signup_identity(request)
        if target is None or _invite_code(code) is None:
            page = signin_pages.no_workspace_page(
                email=(identity or {}).get("email"),
                sign_out_url="/auth/google/logout",
                invite_action="/signin/invited",
                invite_error=(
                    "That doesn't look like an invitation link. Paste the whole "
                    "link from the email, including the code after #invite=."
                ),
            )
            return HTMLResponse(
                page, status_code=400, headers={"cache-control": "no-store"}
            )
        host = urlsplit(target).hostname or ""
        _security_log.info("auth.invited_link host=%s", host)
        return RedirectResponse(
            f"https://{host}/?invite={quote(code, safe='')}", status_code=303
        )

    async def sign_in_signout(request: Request):
        """End the control session, then offer the entry again.

        This is the action behind "try a different account" on a refusal page.
        Without it the page's primary link re-mints the same handoff and the
        person bounces between ``/signin`` and the same refusal.
        """
        control_plane = signup_config.control_plane_url
        if control_plane and _is_tenant_origin(request):
            back = _tenant_origin_for(request)
            suffix = f"?next={quote(back, safe='')}" if back else ""
            return RedirectResponse(f"{control_plane}/signin/signout{suffix}", status_code=302)
        signup_store.revoke_session(request.cookies.get(_SIGNUP_SESSION_COOKIE))
        back = _tenant_transfer_target(request.query_params.get("next") or "")
        target = "/signin" + (f"?next={quote(back, safe='')}" if back else "")
        response = RedirectResponse(target, status_code=302)
        response.delete_cookie(_SIGNUP_SESSION_COOKIE, path="/")
        return response

    async def sign_in_entry(request: Request):
        """One sign-in entry: verify identity once, then open the right workspace."""
        control_plane = signup_config.control_plane_url
        if not control_plane:
            return JSONResponse(
                {"error": "This deployment has no hosted sign-in.", "reason": "signin_unconfigured"},
                status_code=404,
            )
        host = _signup_host(request).split(":")[0].lower()
        if host != _control_plane_host():
            back = _tenant_origin_for(request)
            if back is None:
                return JSONResponse(
                    {"error": "Unknown host.", "reason": "unknown_host"}, status_code=400
                )
            return RedirectResponse(
                f"{control_plane}/signin?next={quote(back, safe='')}", status_code=302
            )

        next_target = _tenant_transfer_target(request.query_params.get("next") or "")
        identity = _signup_identity(request)
        if identity is not None:
            rows = signup_store.workspaces_for(
                subject=identity.get("subject"), email=identity.get("email")
            )
            active = [row for row in rows if row["status"] == "active"]
            if next_target is not None:
                # The person named a workspace. The directory is a fallback for
                # an entry reached without one, not an override of their choice.
                return RedirectResponse(_handoff_start_url(next_target), status_code=302)
            if len(active) == 1:
                return RedirectResponse(
                    _handoff_start_url(_workspace_origin(active[0]["slug"])), status_code=302
                )
            if len(active) > 1:
                return HTMLResponse(
                    signin_pages.choose_workspace_page(
                        workspaces=[
                            (row["slug"], _handoff_start_url(_workspace_origin(row["slug"])))
                            for row in active
                        ]
                    )
                )
            return HTMLResponse(
                signin_pages.no_workspace_page(
                    email=identity.get("email"),
                    sign_out_url="/auth/google/logout",
                    invite_action="/signin/invited",
                ),
                headers={"cache-control": "no-store"},
            )

        return_to = "/signin" + (
            f"?next={quote(next_target, safe='')}" if next_target is not None else ""
        )
        google_url = "/auth/google/start?return_to=" + quote(return_to, safe="")
        return HTMLResponse(
            signin_pages.sign_in_page(google_url=google_url),
            headers={"cache-control": "no-store"},
        )

    async def google_sign_in_start(request: Request):
        host = _signup_host(request).split(":")[0].lower()
        control_plane = signup_config.control_plane_url
        raw_return_to = request.query_params.get("return_to")
        tenant_target = _tenant_transfer_target(raw_return_to)
        identity = _signup_identity(request)
        _security_log.info(
            "auth.start host=%s control_session=%s tenant_target=%s",
            host,
            bool(identity),
            urlsplit(tenant_target).hostname if tenant_target else "-",
        )
        if control_plane and host != urlsplit(control_plane).hostname:
            # A tenant origin has no sign-in of its own. Send the browser to the one
            # canonical entry, carrying this workspace as the place to return to.
            # The control plane borrows the only registered Google redirect.
            back = _tenant_transfer_target(f"https://{host}/") or f"https://{host}/"
            target = f"{control_plane}/signin?next={quote(back, safe='')}"
            response = RedirectResponse(target, status_code=302)
            invite = _invite_code(request.query_params.get("invite"))
            if invite:
                # The code has to survive the control-plane hop and come back:
                # it is the only thing that will still be true when the browser
                # returns to this host, signed in but not yet a member.
                response.set_cookie(
                    _INVITE_COOKIE,
                    invite,
                    max_age=_INVITE_COOKIE_MAX_AGE,
                    httponly=True,
                    secure=_cookie_secure(request),
                    samesite="lax",
                    path="/",
                )
            return response
        choose_account = request.query_params.get("choose_account") == "1"
        # Asking for the chooser is only worth anything if this deployment can
        # start a Google flow. Where it cannot, refusing to resume the control
        # session would leave the person on a 503 with no way forward, so the
        # handoff is the only path that can still succeed.
        resume_allowed = not choose_account or not signup_config.configured
        if identity is not None and tenant_target is not None and resume_allowed:
            # The browser already completed Google OIDC on this control plane.
            # Do not send it through the account chooser again: the tenant only
            # needs a freshly minted, audience-bound handoff token. The sign-in
            # entry sets choose_account=1 because there the person asked to sign
            # in, and resuming would hand them the identity they just left.
            full_identity = signup_store.identity_by_subject(identity["subject"]) or identity
            if full_identity.get("email_verified") is True:
                return _tenant_transfer_redirect(
                    email=full_identity["email"],
                    subject=full_identity["subject"],
                    name=full_identity.get("name"),
                    picture=full_identity.get("picture"),
                    tenant_target=tenant_target,
                    source="control_session",
                )
        if not signup_config.configured:
            return JSONResponse(
                {"error": "Sign-up is not configured on this deployment.",
                 "reason": "signup_unconfigured"},
                status_code=503,
            )
        redirect_uri = signup_config.redirect_uri_for(_signup_host(request))
        if redirect_uri is None:
            return JSONResponse(
                {"error": "This host is not on the redirect allow-list.",
                 "reason": "redirect_uri_not_allowed"},
                status_code=400,
            )
        state = google_oidc.new_secret()
        nonce = google_oidc.new_secret()
        binding = google_oidc.new_secret()
        verifier, challenge = google_oidc.pkce_pair()
        signup_store.start_flow(
            state=state,
            binding=binding,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            return_to=(_tenant_transfer_target(request.query_params.get("return_to"))
                       or _safe_return_to(request.query_params.get("return_to"))),
            ttl_seconds=signup_config.flow_ttl_seconds,
        )
        location = google_oidc.authorization_url(
            client_id=signup_config.google_client_id,
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            hosted_domain=signup_config.google_hd,
            # Without this Google reuses the browser's single signed-in account,
            # so two people on one machine cannot hand over.
            prompt="select_account",
        )
        response = RedirectResponse(location, status_code=302)
        response.set_cookie(
            _OIDC_FLOW_COOKIE,
            binding,
            max_age=signup_config.flow_ttl_seconds,
            **_session_cookie_attrs(request),
        )
        return response

    async def google_sign_in_callback(request: Request):
        if not signup_config.configured:
            return JSONResponse(
                {"error": "Sign-up is not configured on this deployment.",
                 "reason": "signup_unconfigured"},
                status_code=503,
            )
        flow, flow_reason = signup_store.consume_flow(
            state=request.query_params.get("state") or "",
            binding=request.cookies.get(_OIDC_FLOW_COOKIE),
        )
        if flow is None:
            # Generic message: a caller learns that it failed, not which check.
            return JSONResponse(
                {"error": "This sign-in attempt is invalid or has expired.",
                 "reason": flow_reason},
                status_code=400,
            )
        if request.query_params.get("error"):
            return JSONResponse(
                {"error": "Google refused the sign-in.", "reason": "provider_error"},
                status_code=400,
            )
        code = request.query_params.get("code") or ""
        if not code:
            return JSONResponse(
                {"error": "Missing authorization code.", "reason": "code_missing"},
                status_code=400,
            )
        if flow["redirect_uri"] not in signup_config.redirect_uris:
            return JSONResponse(
                {"error": "Redirect URI is not allow-listed.",
                 "reason": "redirect_uri_not_allowed"},
                status_code=400,
            )
        try:
            tokens = await run_in_threadpool(
                google_oidc.exchange_code,
                code=code,
                code_verifier=flow["code_verifier"],
                client_id=signup_config.google_client_id,
                client_secret=signup_config.google_client_secret,
                redirect_uri=flow["redirect_uri"],
            )
            jwks = await run_in_threadpool(google_oidc.get_jwks)
            try:
                claims = google_oidc.verify_id_token(
                    tokens["id_token"],
                    jwks=jwks,
                    client_id=signup_config.google_client_id,
                    nonce=flow["nonce"],
                )
            except google_oidc.OIDCError as exc:
                # Google rotates signing keys; a cached JWKS can lag by a `kid`.
                # Refetch once before refusing a token we might otherwise accept.
                if exc.reason != "unknown_key":
                    raise
                google_oidc.clear_jwks_cache()
                jwks = await run_in_threadpool(google_oidc.get_jwks)
                claims = google_oidc.verify_id_token(
                    tokens["id_token"],
                    jwks=jwks,
                    client_id=signup_config.google_client_id,
                    nonce=flow["nonce"],
                )
            email = claims.get("email")
            if not isinstance(email, str) or not email.strip():
                raise google_oidc.OIDCError("claim_missing", "ID token has no email.")
        except google_oidc.OIDCError as exc:
            return JSONResponse(
                {"error": "Google sign-in could not be verified.", "reason": exc.reason},
                status_code=400,
            )
        tenant_target = _tenant_transfer_target(flow["return_to"])
        if tenant_target is not None:
            # This origin is the control plane for a tenant sign-in. The address
            # is verified; the tenant decides whether it belongs. The workspace
            # the person asked for is honoured: the tenant is the authority on
            # its own members, and a wrong guess here would sign them in
            # somewhere they did not ask for.
            return _tenant_transfer_redirect(
                email=email,
                subject=claims["sub"],
                name=claims.get("name"),
                picture=claims.get("picture"),
                tenant_target=tenant_target,
                source="google_callback",
            )

        identity = signup_store.upsert_identity(
            subject=claims["sub"],
            email=email.strip().lower(),
            email_verified=claims.get("email_verified") is True,
            name=claims.get("name"),
            picture=claims.get("picture"),
            hosted_domain=claims.get("hd"),
        )
        raw, _session = signup_store.rotate_session(
            previous_raw=request.cookies.get(_SIGNUP_SESSION_COOKIE),
            identity_id=identity["id"],
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            days=signup_config.session_days,
        )
        _security_log.info("auth.callback action=control_session")
        response = RedirectResponse(flow["return_to"] or "/", status_code=302)
        response.set_cookie(
            _SIGNUP_SESSION_COOKIE,
            raw,
            max_age=signup_config.session_days * 24 * 60 * 60,
            **_session_cookie_attrs(request),
        )
        response.delete_cookie(_OIDC_FLOW_COOKIE, path="/")
        return response

    async def google_sign_in_finish(request: Request):
        """Redeem a handoff token on this tenant origin and sign the person in."""
        host = _signup_host(request).split(":")[0].lower()
        pinned_org = tenant_hosts.required_org(request.scope)
        transfer_key = signup_config.identity_transfer_key
        if pinned_org and signup_config.identity_transfer_master:
            transfer_key = identity_transfer.tenant_key(signup_config.identity_transfer_master, host)
        if not transfer_key:
            return JSONResponse(
                {"error": "Sign-in is not configured on this workspace.",
                 "reason": "transfer_unconfigured"},
                status_code=503,
            )
        try:
            claims = identity_transfer.redeem(
                key=transfer_key,
                token=request.query_params.get("t"),
                audience=host,
            )
        except identity_transfer.TransferError as exc:
            return _signin_failure(
                request,
                error="This sign-in link is invalid or has expired.",
                reason=exc.reason,
                status_code=400,
                host=host,
            )
        try:
            with tenant_context.system(), engine.begin() as conn:
                if pinned_org:
                    resolved = google_login.resolve_member(
                        conn, email=claims["email"], subject=claims["sub"], organization_id=pinned_org)
                else:
                    resolved = google_login.resolve_verified_email(
                        conn,
                        email=claims["email"],
                        subject=claims["sub"],
                        name=claims.get("name"),
                        picture=claims.get("picture"),
                        workspace_slug=host.split(".")[0],
                    )
        except Exception:
            _security_log.exception("sign-in resolve failed host=%s", host)
            return JSONResponse(
                {"error": "Sign-in could not be completed.", "reason": "resolve_failed"},
                status_code=500,
            )
        _security_log.info(
            "auth.finish host=%s action=%s", host, resolved["action"]
        )
        _announce_member(request, claims.get("email") or "")
        if resolved["action"] == google_login.ACTION_DENIED:
            return _signin_failure(
                request,
                error="This Google account is not a member of this workspace.",
                reason=resolved.get("reason", "denied"),
                status_code=403,
                host=host,
                email=claims.get("email"),
            )
        from hoja_server import permissions as permission_rules
        if not resolved.get("owner"):
            policy = _org_security_policy(resolved["organization_id"])
            if not permission_rules.signin_method_allowed(policy, "google"):
                return _signin_failure(
                    request,
                    error="Google sign-in is turned off for this workspace.",
                    reason="signin_method_disabled",
                    status_code=403,
                    host=host,
                    email=claims.get("email"),
                )
            if policy.get("ipAllowlist") and not permission_rules.ip_allowed(
                    policy, _client_ip(request)):
                return _signin_failure(
                    request,
                    error="This network is not allowed for this workspace.",
                    reason="ip_not_allowed",
                    status_code=403,
                    host=host,
                    email=claims.get("email"),
                )
        if resolved.get("owner") or resolved.get("adopted"):
            _security_log.warning(
                f"auth.owner_admitted host={host} adopted={bool(resolved.get('adopted'))}"
                f" linked={bool(resolved.get('linked'))}"
            )
        session_token, _details = store.create_session_for_membership(
            resolved["identity_id"], resolved["user_id"], resolved["organization_id"]
        )
        if not session_token:
            return JSONResponse(
                {"error": "Sign-in could not be completed.", "reason": "session_failed"},
                status_code=500,
            )
        # An invitation in flight outranks the generic landing: the person did
        # not come here to browse, they came to accept, and the SPA only reads
        # the code from the URL. The cookie is single-use.
        pending_invite = _invite_code(request.cookies.get(_INVITE_COOKIE))
        landing = (
            f"/?invite={quote(pending_invite, safe='')}"
            if pending_invite
            else _safe_return_to(request.query_params.get("to"))
        )
        response = RedirectResponse(landing, status_code=302)
        response.set_cookie(
            "hoja_session",
            session_token,
            max_age=14 * 24 * 60 * 60,
            **_session_cookie_attrs(request),
        )
        if pending_invite:
            response.delete_cookie(_INVITE_COOKIE, path="/")
        return response

    def _signup_workspace_payload(row: dict) -> dict:
        """One requester's own workspace/request state, safe for the page."""
        slug = str(row.get("slug") or "")
        status = str(row.get("status") or "eligible")
        workspace_url = None
        sign_in_url = None
        if status == "active":
            workspace_url = f"https://{slug}.{signup_config.tenant_base_domain}"
            sign_in_url = f"{workspace_url}/auth/google/start?return_to=%2F"
            message = "Your workspace is ready."
        elif status == "provisioning":
            message = "Your workspace is being created."
        elif status == "failed":
            message = "We could not finish creating this workspace."
        else:
            message = "Your workspace request is queued for creation."
        return {
            "slug": slug,
            "status": status,
            "workspaceUrl": workspace_url,
            "signInUrl": sign_in_url,
            "message": message,
        }

    async def signup_endpoint(request: Request):
        if request.method == "GET":
            identity = _signup_identity(request)
            payload = {
                "state": "google_verified" if identity else "anonymous",
                "googleVerified": bool(identity),
                "humanVerified": False,
                "email": (identity or {}).get("email"),
            }
            if identity is not None:
                row = signup_store.latest_eligibility_for_identity(identity["identity_id"])
                if row is not None:
                    payload["workspace"] = _signup_workspace_payload(row)
            return JSONResponse(payload, headers={"cache-control": "no-store"})
        if not _same_origin(request.headers.get("origin"), request.headers):
            return JSONResponse(
                {"ok": False, "reason": CROSS_SITE_REQUEST,
                 "message": "Cross-site request rejected."},
                status_code=403,
            )
        body, body_error = await _read_json_object(request, _SIGNUP_BODY_LIMIT)
        if body_error is not None:
            return JSONResponse(
                {"ok": False, "reason": INVALID_REQUEST,
                 "message": body_error.body.decode("utf-8", "replace")},
                status_code=body_error.status_code,
            )
        token = body.get("turnstileToken")
        if token is None:
            token = body.get("turnstile_token")
        decision = signup_gate.evaluate(
            identity=_signup_identity(request),
            slug=body.get("slug"),
            turnstile_token=token if isinstance(token, str) else None,
            client_ip=_client_ip(request),
        )
        # One line per gate decision: the funnel is the sequence of these states
        # (anonymous → google_verified → human_verified → eligible), and the reason
        # code says which wall a person hit. No token, cookie or email is logged.
        # The field is ``signup_state``, not ``state``: ``state=`` is an OAuth
        # parameter that the log formatter redacts, and reusing the name would
        # silently blank the one field that identifies the funnel step.
        _security_log.info(
            "auth.signup.decision signup_state=%s reason=%s status=%s",
            decision.state.value,
            decision.reason.value if decision.reason else "-",
            decision.status_code,
        )
        return JSONResponse(decision.as_dict(), status_code=decision.status_code)

    async def signup_status(request: Request):
        """The signed-in requester's own workspace state, for the sign-up page.

        Deliberately not public: the sign-up session identifies the requester,
        and the slug must belong to that identity. It answers no secrets and no
        other person's queue position.
        """
        identity = _signup_identity(request)
        if identity is None:
            return JSONResponse(
                {"ok": False, "reason": "not_authenticated"}, status_code=401
            )
        slug = (request.query_params.get("slug") or "").strip().lower()
        if not slug or len(slug) > 63:
            return JSONResponse({"ok": False, "reason": "invalid_slug"}, status_code=400)
        row = signup_store.eligibility_for(identity["identity_id"], slug)
        if row is None:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
        return JSONResponse(
            {"ok": True, **_signup_workspace_payload(row)},
            headers={"cache-control": "no-store"},
        )

    async def signup_logout(request: Request):
        if not _same_origin(request.headers.get("origin"), request.headers):
            return JSONResponse({"error": "Cross-site request rejected."}, status_code=403)
        _security_log.info("auth.logout scope=signup")
        signup_store.revoke_session(request.cookies.get(_SIGNUP_SESSION_COOKIE))
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(_SIGNUP_SESSION_COOKIE, path="/")
        return response

    # ---- Attachment file uploads (images/files/videos for issue attachments)
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
        original = (getattr(upload, "filename", "") or "file")
        # A concrete declared type wins; octet-stream/empty falls back to the extension
        # so `notes.md` from a client that sends no type is still a Markdown document.
        resolved = uploads.resolve_content_type(original, content_type)
        if not resolved:
            return JSONResponse({"error": "File type not allowed"}, status_code=415)
        body = await upload.read()
        if len(body) > uploads.MAX_BYTES:
            return JSONResponse({"error": "File too large (10 MB max)"}, status_code=413)
        url, title = uploads.save_blob(org, original, resolved, body)
        return JSONResponse({"url": url, "title": title, "contentType": resolved})

    async def upload_serve(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        # The URL carries the extension (`<id>.png`); the id itself is the part before it.
        file_id = uploads.blob_id_from_segment(request.path_params.get("file_id", ""))
        if not file_id:
            return JSONResponse({"error": "Not found"}, status_code=404)
        meta = uploads.read_meta(file_id) or {}
        if meta.get("org") != org:
            return JSONResponse({"error": "Not found"}, status_code=404)
        # An uploaded file inherits its issue's team visibility: knowing the (random)
        # file id is not authority to read a private team's material.
        if not store.upload_visible(org, uid, file_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        stored = uploads.blob_for(file_id)
        if stored is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        content_type = meta.get("content_type") or "application/octet-stream"
        return FileResponse(
            stored,
            media_type=content_type,
            filename=meta.get("filename") or "file",
            # SVG (and anything else not on the inline list) downloads instead of
            # rendering as a document on the app origin, where its script would run.
            content_disposition_type="inline" if uploads.inline_disposition(content_type) else "attachment",
            headers=dict(uploads.BLOB_HEADERS),
        )

    def _sync_meta(request: Request, uid: str, org: str, identity_id, is_browser: bool) -> dict:
        context = {"request": request, "store": store, "cache": {}, "writer": writer,
                   "org_id": org, "user_id": uid, "identity_id": identity_id,
                   "browser_session": is_browser,
                   "access": getattr(request.state, "api_key_access", "read_write")}
        _ok, result = graphql_sync(_schema, {"query": sync.META_QUERY}, context_value=context,
                                   error_formatter=_format_graphql_error, logger=_ARIADNE_LOGGER)
        if result.get("errors"):
            raise RuntimeError(result["errors"][0].get("message", "metadata query failed"))
        return {**result["data"], "users": sync.org_users(store, org)}

    def _sync_json(body: dict, **kwargs) -> Response:
        return Response(json.dumps(body, separators=(",", ":")), media_type="application/json", **kwargs)

    async def sync_bootstrap(request: Request):
        uid, org, identity_id, is_browser = await run_in_threadpool(_auth_sync, request)
        if uid:  # resolved in a worker thread: its context copy is gone, bind here
            tenant_context.enter(org, uid, getattr(request.state, "actor_kind", "human"))
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        try:
            closed_days = int(request.query_params.get("closedDays", sync.DEFAULT_CLOSED_DAYS))
        except ValueError:
            return _bad_request("closedDays must be an integer.")
        if not 0 <= closed_days <= sync.MAX_CLOSED_DAYS:
            return _bad_request(f"closedDays must be between 0 and {sync.MAX_CLOSED_DAYS}.")

        def validator():
            # Seq first: anything committed while the snapshot is read is replayed by the next delta.
            seq = store.latest_change_seq(org)
            cutoff = sync.closed_cutoff(closed_days)
            visible = store.visible_team_ids(org, uid)
            key = sync.issues_key(org, seq, cutoff, visible)
            return seq, cutoff, visible, key, sync.snapshot_etag(uid, key)

        seq, cutoff, visible, key, etag = await run_in_threadpool(validator)
        headers = {"ETag": etag, "Cache-Control": "private, no-cache",
                   "Vary": "Cookie, Authorization, Accept-Encoding"}
        if_none_match = request.headers.get("if-none-match") or ""
        if etag in [t.strip() for t in if_none_match.split(",")]:
            return Response(status_code=304, headers=headers)

        async def build_issues():
            # Derive from the previous snapshot of the same (org, window, visibility)
            # plus the rows the realtime feed already fetched, instead of re-reading
            # every open issue after each write.
            lineage = (org, cutoff, key[3])
            prev = issue_lineage.get(lineage)
            rows = None
            if prev is not None and prev[0] < seq and await hub.wait_head(org, seq):
                changes = hub.issue_changes(org, prev[0], seq)
                if changes is not None:
                    prev_rows = prev[1]
                    async with gql_slots:
                        rows = await run_in_threadpool(lambda: sync.derive_issues(
                            prev_rows, changes, visible, cutoff, sync.closed_state_ids(store, org)))
            elif prev is not None and prev[0] == seq:
                rows = prev[1]
            hub.stats["snapshot_derived" if rows is not None else "snapshot_full"] += 1
            async with gql_slots:
                if rows is None:
                    rows = await run_in_threadpool(sync.bootstrap_issues, store, org, uid, cutoff)
                data = await run_in_threadpool(lambda: json.dumps(rows, separators=(",", ":")).encode())
            current = issue_lineage.get(lineage)
            if current is None or current[0] < seq:
                issue_lineage[lineage] = (seq, rows)
                while len(issue_lineage) > 64:
                    issue_lineage.pop(next(iter(issue_lineage)))
            return data

        async def build_body():
            issues_json = await issue_snapshots.get(key, build_issues)

            def assemble():
                head = {"v": sync.SYNC_VERSION, "seq": seq, "closedCutoff": cutoff,
                        "scope": sync.sync_scope(org, uid),
                        "meta": _sync_meta(request, uid, org, identity_id, is_browser),
                        "columns": sync.ISSUE_COLUMNS}
                raw = json.dumps(head, separators=(",", ":")).encode()[:-1] + b',"issues":' + issues_json + b"}"
                return raw, gzip.compress(raw, 6)

            async with gql_slots:
                return await run_in_threadpool(assemble)

        raw, packed = await body_snapshots.get(etag, build_body)
        if "gzip" in request.headers.get("accept-encoding", ""):
            return Response(packed, media_type="application/json",
                            headers={**headers, "Content-Encoding": "gzip"})
        return Response(raw, media_type="application/json", headers=headers)

    async def sync_delta(request: Request):
        uid, org, identity_id, is_browser = await run_in_threadpool(_auth_sync, request)
        if uid:  # resolved in a worker thread: its context copy is gone, bind here
            tenant_context.enter(org, uid, getattr(request.state, "actor_kind", "human"))
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        try:
            after = int(request.query_params.get("after", ""))
        except ValueError:
            return _bad_request("after must be an integer.")
        if not 0 <= after <= _MAX_SEQ:
            return _bad_request("after is out of range.")
        scope = request.query_params.get("scope")

        async def meta_for(meta_seq):
            async def build():
                async with gql_slots:
                    return await run_in_threadpool(_sync_meta, request, uid, org, identity_id, is_browser)
            return await meta_cache.get((org, uid, identity_id, meta_seq), build)

        delta = None
        if scope is None or scope == sync.sync_scope(org, uid):
            # Served from the realtime ring: after a broadcast every client pulls at
            # once, and none of them needs to touch the database.
            delta = await hub.delta(org, uid, after)
            if delta is not None and delta.get("meta"):
                delta["meta"] = await meta_for(delta["seq"])
        if delta is None:
            def build():
                return sync.build_delta(store, org, uid, after, scope)

            async with gql_slots:
                delta = await run_in_threadpool(build)
            if delta.get("meta"):
                delta["meta"] = await meta_for(delta["seq"])
        return _sync_json(delta, headers={"Cache-Control": "no-store"})

    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    async def build_version(request: Request):
        # Which build this instance is running. A phone install and the site
        # drift invisibly without it: the APK names a version, and until now
        # nothing on this origin said which one the server was. Answers JSON
        # rather than the SPA shell, so a probe can tell a version from a page.
        from hoja_server import __version__

        # Same three fields the homepage origin serves. `version` is read from
        # app/VERSION when the package is imported, so it is the build's own
        # value and never a literal in this file. `sha` only exists if the
        # deploy stamped it; `unknown` is the honest answer otherwise, and is
        # deliberately not a plausible-looking commit id.
        sha = os.environ.get("HOJA_RELEASE_SHA", "").strip()
        built_at = os.environ.get("HOJA_BUILT_AT", "").strip()
        if not built_at:
            # The image build writes app/BUILD_TIME itself, so builtAt survives
            # a deploy that forgot to pass the environment variable.
            try:
                built_at = (_WEB_ROOT.parent / "BUILD_TIME").read_text().strip()
            except OSError:
                built_at = ""

        return JSONResponse(
            {"version": __version__, "sha": sha or "unknown", "builtAt": built_at or None},
            headers={"Cache-Control": "no-store"},
        )

    async def api_not_found(request: Request):
        # /api is a JSON surface. Without this, an unknown /api path fell into
        # web_fallback and answered 200 text/html with the SPA shell — the exact
        # trap that made a version probe look satisfied when nothing was there.
        # Registered after every real /api route, so it only ever sees misses.
        return JSONResponse(
            {"error": "Not found", "path": request.url.path},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )

    async def ready(request: Request):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1")).scalar_one()
            from hoja_server.db import inspect

            if not inspect(engine).has_table("schema_revision"):
                return JSONResponse({"status": "unavailable", "reason": "schema_outdated"}, status_code=503)
            if not schema_is_current(engine):
                return JSONResponse(
                    {"status": "unavailable", "reason": "schema_outdated"},
                    status_code=503,
                )
            # A report names the reason (a stable token) and the detail (the role
            # or every table that made it fail), so a deployment that is not bound
            # fails closed here instead of serving traffic with inert policies —
            # and says why, which is what a rolling deploy needs in its logs.
            report = tenant_context.probe_report(engine)
            if report:
                return JSONResponse({"status": "unavailable", **report}, status_code=503)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    async def web_index(request: Request):
        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return HTMLResponse(_UNBUILT_HTML, status_code=503)
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
        # A retired flat identifier still loads: /<workspace>/issue/HOJA-517
        # resolves to the recursive identifier that replaced it (HOJA-472).
        # Only identifier-shaped issue paths are looked up, so the catch-all
        # does not turn every SPA route into a database query.
        retired = _retired_identifier_target(request)
        if retired is not None:
            return RedirectResponse(retired, status_code=302)
        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return HTMLResponse(_UNBUILT_HTML, status_code=503)
        return FileResponse(index)

    def _retired_identifier_target(request: Request) -> str | None:
        parts = [part for part in request.url.path.split("/") if part]
        if len(parts) < 2 or parts[-2] not in ("issue", "task"):
            return None
        candidate = parts[-1]
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$", candidate):
            return None
        from hoja_server.db import issue_identifier_alias, organization

        # One database serves several organizations, so the workspace comes
        # from the path (or the vanity host), never from "the first org".
        org_key = parts[0]
        if org_key == "workspace" and len(parts) >= 3:
            org_key = parts[1]
        # The fallback runs before authentication, so no tenant is bound and
        # row-level security would hide both tables. This is an enumerated
        # pre-auth lookup, the same reason /auth/ runs as system.
        with tenant_context.system(), engine.connect() as conn:
            org_id = conn.execute(select(organization.c.id).where(or_(
                organization.c.url_key == org_key,
                organization.c.vanity_host == request.url.hostname,
            )).limit(1)).scalar()
            if not org_id:
                return None
            replacement = conn.execute(select(
                issue_identifier_alias.c.new_identifier).where(and_(
                    issue_identifier_alias.c.organization_id == org_id,
                    issue_identifier_alias.c.old_identifier == candidate.upper(),
                ))).scalar()
        if not replacement or replacement.upper() == candidate.upper():
            return None
        parts[-1] = replacement
        target = "/" + "/".join(parts)
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return target

    def _sqlite_analyze():
        # Without planner statistics SQLite prefers the (single-valued, in a
        # one-workspace database) organization_id index over the id/identifier
        # indexes and scans every issue for a single-issue lookup.
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA analysis_limit=1000")
            conn.exec_driver_sql("ANALYZE")

    async def _sqlite_maintenance():
        while True:
            try:
                await asyncio.to_thread(_sqlite_analyze)
            except Exception:
                logging.getLogger("hoja_server").warning("SQLite ANALYZE failed", exc_info=True)
            await asyncio.sleep(6 * 3600)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal mcp_asgi, durable_agent
        # Each TestClient/server lifespan gets a fresh SDK session manager;
        # the SDK manager is intentionally single-use after shutdown.
        from hoja_server.mcp_oauth import HojaOAuthProvider, build_server

        runtime_server = build_server(
            HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy, public_host=public_host),
            public_host=public_host,
        )
        runtime_app = runtime_server.streamable_http_app(
            streamable_http_path="/", stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[public_host.strip(), public_host.strip() + ":*"],
            ),
        )
        mcp_asgi = runtime_app
        mcp_pinned.clear()
        pinned_mcp = None
        if host_map.canonical:
            pinned_ready = asyncio.Event()
            pinned_mcp = asyncio.create_task(tenant_context.as_system(_serve_pinned_mcp(pinned_ready)))
            await asyncio.wait_for(pinned_ready.wait(), timeout=30)
        from concurrent.futures import ThreadPoolExecutor
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=32, thread_name_prefix="hoja"))
        await hub.ensure_started()
        meeting_hub.start()
        maintenance = asyncio.create_task(_sqlite_maintenance()) if engine.dialect.name == "sqlite" else None
        # The lease reaper (WP07 T033): Hoja's own clock decides when a run is
        # lost. It never asks a runner or the DBOS process whether it is alive,
        # because a database outage blocks DBOS silently (plan D9).
        lease_reaper = None
        if os.environ.get("HOJA_LEASE_REAPER", "1").strip().lower() not in ("0", "false", "no"):
            from hoja_server import runner_api as runner_lease_api

            lease_reaper = asyncio.create_task(
                tenant_context.as_system(runner_lease_api.run_reaper_forever(engine)))
        webhook_worker = None
        if os.environ.get("HOJA_WEBHOOK_WORKER", "1").strip().lower() not in ("0", "false", "no"):
            from hoja_server import webhooks

            webhook_worker = asyncio.create_task(tenant_context.as_system(webhooks.run_forever(engine)))
        async with runtime_app.router.lifespan_context(runtime_app):
            if agent_dbos_url := os.environ.get("HOJA_AGENT_DBOS_URL"):
                missing = _missing_packages(("dbos", *_AGENT_REQUIRED_PACKAGES))
                if missing:
                    raise RuntimeError(
                        "HOJA_AGENT_DBOS_URL requires the durable agent, but the 'agent' "
                        f"extra is not installed ({', '.join(missing)} missing). Install it with "
                        "python -m pip install 'hoja-cli[server,agent]'."
                    )
                from dbos import DBOS

                # Imported before launch: DBOS registers workflows at import and recovers them at launch.
                from hoja_server import agent_durable

                agent_durable.configure(store, writer, _schema)
                DBOS(config={"name": "hoja-agent", "system_database_url": agent_dbos_url})
                DBOS.launch()
                # HOJA-602-A-7: the queue is registered after launch, because DBOS reads
                # its system database inside register_queue and refuses before then.
                await agent_durable.register_agent_queue(
                    agent_durable.max_configured_capacity(store))
                durable_agent = agent_durable
                try:
                    yield
                finally:
                    durable_agent = None
                    DBOS.destroy()
                    agent_durable.shutdown()
            else:
                yield
        await hub.stop()
        await meeting_hub.close_all()
        if sfu is not None:
            await sfu.aclose()
        if maintenance is not None:
            maintenance.cancel()
        if lease_reaper is not None:
            lease_reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lease_reaper
        if webhook_worker is not None:
            webhook_worker.cancel()
        if pinned_mcp is not None:
            pinned_mcp.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinned_mcp

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
            # The runner lease API (HOJA-602-A-6). Each route authenticates its
            # own runner bearer token, so it sits before the /api catch-all and
            # outside the browser session/cookie path.
            *runner_routes(engine),
            *meeting_routes(
                engine, meeting_service, meeting_hub, auth=_auth,
                csrf=lambda request: _cookie_csrf_error(request, json_only=True),
                read_json=_read_json_object, client_ip=_client_ip, same_origin=_same_origin, sfu=sfu),
            WebSocketRoute("/ws", realtime_socket),
            Route("/signin", sign_in_entry, methods=["GET"]),
            Route("/signin/signout", sign_in_signout, methods=["GET"]),
            Route("/signin/invited", sign_in_invited, methods=["POST"]),
            Route("/auth/setup", auth_setup, methods=["GET", "POST"]),
            Route("/auth/invite/accept", auth_invite_accept, methods=["POST"]),
            Route("/auth/invite/probe", auth_invite_probe, methods=["POST"]),
            Route("/internal/membership", internal_membership, methods=["POST"]),
            Route("/onboarding/status", onboarding_status, methods=["GET"]),
            Route("/onboarding/bootstrap", onboarding_bootstrap, methods=["POST"]),
            Route("/auth/login", auth_login, methods=["POST"]),
            Route("/auth/workspaces", auth_workspaces, methods=["POST"]),
            Route("/auth/logout", auth_logout, methods=["POST"]),
            Route("/auth/me", auth_me, methods=["GET"]),
            Route("/auth/google/start", google_sign_in_start, methods=["GET"]),
            Route("/auth/google/callback", google_sign_in_callback, methods=["GET"]),
            Route("/auth/google/finish", google_sign_in_finish, methods=["GET"]),
            Route("/auth/google/logout", signup_logout, methods=["POST"]),
            Route("/api/signup/config", signup_public_config, methods=["GET"]),
            Route("/api/signup/status", signup_status, methods=["GET"]),
            Route("/api/signup", signup_endpoint, methods=["GET", "POST"]),
            Route("/api/uploads", upload_create, methods=["POST"]),
            Route("/api/uploads/{file_id}", upload_serve, methods=["GET"]),
            Route("/sync/bootstrap", sync_bootstrap, methods=["GET"]),
            Route("/sync/delta", sync_delta, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/api/version", build_version, methods=["GET"]),
            # Every real /api route above; anything else under /api is a miss
            # and must say so in JSON instead of serving the SPA shell.
            Route(
                "/api/{path:path}",
                api_not_found,
                methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            ),
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
            # The protected-resource metadata advertises https://<host>/mcp, and
            # spec-following clients POST to exactly that URL. Mount("/mcp") only
            # matches /mcp/..., so the bare path fell through to the SPA fallback
            # (POST 405, GET returned the web page). Serve it as the mount would.
            Route("/mcp", _BareMount(mcp_endpoint, "/mcp")),
            Mount("/mcp", app=mcp_endpoint),
            Route("/", web_index, methods=["GET"]),
            Route("/assets/{name:path}", web_asset, methods=["GET"]),
            Route(f"/{BRAND_MARK}", web_brand_mark, methods=["GET"]),
            Route("/{path:path}", web_fallback, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    app.state.realtime = hub
    app.state.meetings = meeting_hub
    app.add_middleware(_SecurityHeaders)
    # Compress JSON and static assets; SSE streams are excluded by Starlette.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    # Outermost: enumerated pre-tenant paths run in system context; every other
    # request has no tenant until _auth binds one (fail closed under RLS).
    app.add_middleware(org_limits.OrgConcurrencyMiddleware, limiter=org_slots)
    app.add_middleware(tenant_context.TenantContextMiddleware)
    app.add_middleware(tenant_hosts.TenantHostMiddleware, hosts=host_map)
    # Outermost of all when configured: preflight OPTIONS and error responses
    # must carry CORS headers even when no tenant has been bound yet.
    cors.install_cors(app)
    app.add_exception_handler(json.JSONDecodeError, _malformed_json_handler)
    return app
