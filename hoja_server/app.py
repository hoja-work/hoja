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
from urllib.parse import parse_qs, quote, urlencode, urlsplit

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
from starlette.background import BackgroundTask
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
    invite_policy,
    mail_events,
    membership_announce,
    observability,
    org_limits,
    realtime,
    signin_pages,
    sync,
    tenant_context,
    tenant_hosts,
    uploads,
    workspace_directory,
)
from hoja_server.connectors_api import connector_binding_for, connector_bindings_for, connector_routes
from hoja_server.db import (
    _seed_demo_issues,
    api_key,
    browser_session,
    identity_transfer_use,
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
    signup_identity,
    signup_session,
    team,
    team_member,
    token_hash,
    user,
)
from hoja_server.denials import DENY_HINT, READ_ONLY_HINT, hint_for, read_only_violation
from hoja_server.deployment import single_org_mode
from hoja_server.resolvers import build_schema
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
from hoja_server.store import APP_SESSION_DAYS, Store
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

# Public root assets copied from app/frontend/public into _WEB_ROOT. Served
# explicitly before the SPA catch-all so crawlers and mobile browsers receive
# real images and metadata instead of index.html.
_WEB_PUBLIC_FILES: dict[str, str] = {
    BRAND_MARK: "image/svg+xml",
    "og-image.png": "image/png",
    "og-signup.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "favicon-32x32.png": "image/png",
    "icon-192.png": "image/png",
    "site.webmanifest": "application/manifest+json",
    "robots.txt": "text/plain; charset=utf-8",
}

_SEO_MARKER_RE = re.compile(r"<!-- SEO:START -->.*?<!-- SEO:END -->", re.DOTALL)

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


#: HOJA-733: an app session's cookie outlives any one server-side expiry: the
#: server slides the session (store.APP_SESSION_DAYS), so the cookie must not
#: be the thing that signs a phone out. 400 days is the browser ceiling.
_APP_COOKIE_MAX_AGE = 400 * 24 * 60 * 60


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
                    # The request id is what lets a denial be tied to the
                    # operations line for the same request.
                    _security_log.warning("denied status=%s method=%s path=%s request_id=%s",
                                          message["status"], scope.get("method"), scope.get("path"),
                                          observability.current_request_id() or "-")
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
    issue_snapshots = sync.SingleFlightCache(8)
    body_snapshots = sync.SingleFlightCache(32)
    org_slots = org_limits.OrgConcurrency()
    meta_cache = sync.SingleFlightCache(512)
    issue_lineage: dict = {}  # (org, cutoff, visibility) -> (seq, rows) of the newest snapshot
    sync_auth_cache: dict = {}

    def _validate_creds(creds):
        keys = [h for kind, h in creds if kind == "key"]
        sessions = [h for kind, h in creds if kind == "session"]
        out = store.validate_credentials(keys, sessions)
        oauth = list({h for kind, h in creds if kind == "oauth"})
        if oauth:
            from sqlalchemy import select

            from hoja_server.db import oauth_access_token
            now = datetime.now(timezone.utc).isoformat()
            with tenant_context.system(), engine.connect() as conn:
                for th, uid, org in conn.execute(select(
                        oauth_access_token.c.token_hash, oauth_access_token.c.user_id,
                        oauth_access_token.c.organization_id).where(
                        oauth_access_token.c.token_hash.in_(oauth), oauth_access_token.c.revoked_at.is_(None),
                        oauth_access_token.c.expires_at > now)):
                    if store.membership_for_profile(org, uid):
                        out[("oauth", th)] = (uid, org)
        return out

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

    def _enter_request_context(request: Request, org: str | None, uid: str) -> None:
        tenant_context.enter(
            org,
            uid,
            getattr(request.state, "actor_kind", "human"),
            actor_id=getattr(request.state, "actor_id", None) or uid,
            on_behalf_of=getattr(request.state, "on_behalf_of", None),
            credential_kind=getattr(request.state, "credential_kind", None),
            credential_id=getattr(request.state, "credential_id", None),
            session_id=getattr(request.state, "session_id", None),
        )

    def _auth(request: Request):
        """Resolve the caller, then scope the rest of this request to its organization.

        Credential lookups run before any tenant is known, so they are a system
        operation; everything after a successful resolution is tenant-scoped.
        """
        with tenant_context.system():
            result = _resolve_auth(request)
        if result[0]:
            refusal = tenant_hosts.host_refusal(host_map, request.scope, result[1], credential=not result[3])
            if refusal:
                # Valid, but not on this host. /graphql and /mcp turn this into a
                # wrong_workspace_host answer instead of a bare 401 (which makes
                # OAuth clients start sign-in again for nothing).
                request.state.host_refusal = refusal
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
            _enter_request_context(request, result[1], result[0])
        return result

    def _oauth_graphql_credential(request: Request, token: str) -> dict | None:
        """An OAuth access token issued for this host's <origin>/graphql, or None.

        Same rules as an API key: hoja:write is read_write, anything else read.
        A token for <origin>/mcp from a third-party client is not accepted here;
        a Hoja CLI token covers both endpoints of its origin. A token that
        matched by hash but is revoked or expired marks the request so /graphql
        answers 401 invalid_token, which tells a client to refresh.
        """
        if not trust_proxy:
            return None
        from sqlalchemy import select

        from hoja_server.db import oauth_access_token, oauth_grant
        from hoja_server.mcp_oauth import FIRST_PARTY_CLIENT_ID, WRITE_SCOPE, touch_grant

        with tenant_context.system(), engine.connect() as conn:
            row = conn.execute(
                select(
                    oauth_access_token,
                    oauth_grant.c.actor_kind.label("grant_actor_kind"),
                )
                .outerjoin(
                    oauth_grant,
                    oauth_grant.c.id == oauth_access_token.c.grant_id,
                )
                .where(oauth_access_token.c.token_hash == token_hash(token))
            ).mappings().first()
        if row is None:
            return None
        if row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc).isoformat():
            request.state.oauth_invalid = True
            return None
        origin = f"https://{_origin_host(request)}"
        accepted = {origin + "/graphql"}
        if row["client_id"] == FIRST_PARTY_CLIENT_ID:
            accepted.add(origin + "/mcp")
        resource = (row["resource"] or "").rstrip("/")
        if resource not in accepted:
            own = urlsplit(resource)
            if own.scheme == "https" and own.netloc and own.netloc != _origin_host(request):
                # A valid token minted on another workspace's host: say where it works.
                request.state.host_refusal = {
                    "code": tenant_hosts.WRONG_WORKSPACE_HOST, "reason": "token_for_other_host",
                    "message": f"This token was issued for https://{own.netloc}.",
                    "origin": f"https://{own.netloc}",
                }
            return None
        if not store.membership_for_profile(row["organization_id"], row["user_id"]):
            return None
        touch_grant(engine, row["grant_id"], _client_ip(request))
        scopes = json.loads(row["scopes"] or "[]")
        default_kind = "human" if row["client_id"] == FIRST_PARTY_CLIENT_ID else "agent"
        actor_kind = row.get("grant_actor_kind") or default_kind
        return {"user_id": row["user_id"], "org_id": row["organization_id"], "grant_id": row["grant_id"],
                "actor_kind": actor_kind,
                "access": "read_write" if WRITE_SCOPE in scopes else "read"}

    def _resolve_auth(request: Request):
        raw = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        actor_hint = (request.headers.get("x-hoja-actor", "") or "").strip().lower()
        request.state.session_id = tenant_context.clean_session_id(request.headers.get("x-hoja-session-id"))
        if raw and token:
            # A valid Authorization header is authoritative; cookies are not consulted.
            key = store.resolve_token(token)
            if key:
                store.touch_api_key(key["id"], key.get("last_used_at"))
                request.state.api_key_access = key["access"]
                kind = key.get("kind") or "human"
                actor_id = key.get("actor_id") or key["user_id"]
                on_behalf_of = key.get("on_behalf_of")
                if actor_hint == "agent" and kind != "agent":
                    kind = "agent"
                    on_behalf_of = key["user_id"]
                request.state.actor_kind = kind
                request.state.actor_id = actor_id
                request.state.on_behalf_of = on_behalf_of
                request.state.credential_kind = "api_key"
                request.state.credential_id = key["id"]
                return key["user_id"], key["organization_id"], None, False
            oauth = _oauth_graphql_credential(request, token)
            if oauth:
                request.state.api_key_access = oauth["access"]
                request.state.oauth_grant = oauth["grant_id"]
                kind = oauth.get("actor_kind") or "human"
                if actor_hint == "agent":
                    kind = "agent"
                request.state.actor_kind = kind
                request.state.actor_id = oauth["user_id"]
                request.state.on_behalf_of = oauth["user_id"] if kind == "agent" else None
                request.state.credential_kind = "oauth"
                request.state.credential_id = oauth["grant_id"]
                return oauth["user_id"], oauth["org_id"], None, False
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
                request.state.actor_kind = "human"
                request.state.actor_id = session["user_id"]
                request.state.on_behalf_of = None
                request.state.credential_kind = "session"
                request.state.credential_id = session.get("id")
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
        if token and getattr(websocket.state, "oauth_grant", None) is not None:
            return uid, org, ("oauth", token_hash(token))
        session_token = websocket.cookies.get("hoja_session")
        if session_token and _is_session:
            return uid, org, ("session", token_hash(session_token))
        return uid, org, None  # loopback open mode: nothing to revoke

    def _auth_sync(request: Request):
        """_auth for the read-only /sync endpoints, cached briefly per credential."""
        key = (request.headers.get("authorization") or "", request.cookies.get("hoja_session") or "",
               request.client.host if request.client else "", request.headers.get("host", "").lower(),
               request.headers.get("x-hoja-actor") or "", request.headers.get("x-hoja-session-id") or "")
        now = time.monotonic()
        hit = sync_auth_cache.get(key)
        if hit is not None and now - hit[0] < _SYNC_AUTH_TTL:
            request.state.api_key_access = hit[2]
            meta = hit[3] if len(hit) > 3 and isinstance(hit[3], dict) else {"actor_kind": hit[3] if len(hit) > 3 else "human"}
            for attr in ("actor_kind", "actor_id", "on_behalf_of", "credential_kind", "credential_id", "session_id"):
                setattr(request.state, attr, meta.get(attr))
            # a cache hit skips _auth, so it must scope the request itself
            org_slots.enter(request.scope, hit[1][1])
            _enter_request_context(request, hit[1][1], hit[1][0])
            return hit[1]
        result = _auth(request)
        if result[0]:
            if len(sync_auth_cache) > 10000:
                sync_auth_cache.clear()
            meta = {
                "actor_kind": getattr(request.state, "actor_kind", "human"),
                "actor_id": getattr(request.state, "actor_id", None),
                "on_behalf_of": getattr(request.state, "on_behalf_of", None),
                "credential_kind": getattr(request.state, "credential_kind", None),
                "credential_id": getattr(request.state, "credential_id", None),
                "session_id": getattr(request.state, "session_id", None),
            }
            sync_auth_cache[key] = (now, result, getattr(request.state, "api_key_access", "read_write"), meta)
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

    def _wrong_host_response(refusal: dict, path: str) -> JSONResponse:
        """403, not 401: the credential is valid, so a client must not start sign-in again."""
        url = refusal["origin"] + path
        message = refusal["message"] + f" Use {url}."
        # Stable shape on /graphql and /mcp: {error, code, url}; clients follow url.
        # "errors" repeats it in GraphQL's own error shape for GraphQL clients.
        return JSONResponse(
            {
                "error": message,
                "code": refusal["code"],
                "url": url,
                "reason": refusal["reason"],
                "errors": [{"message": message, "extensions": {"code": refusal["code"], "url": url}}],
            },
            status_code=403,
        )

    def _auth_required(request: Request) -> JSONResponse:
        """The REST endpoints' 401, unless the real answer is "wrong host".

        A valid credential presented on another workspace's host (or a vanity
        workspace's credential on the apex) is not "not authenticated": clients
        such as the mobile app would tell the person their key is invalid and
        never recover. They get the same 403 wrong_workspace_host as /graphql.
        """
        refusal = getattr(request.state, "host_refusal", None)
        if refusal:
            return _wrong_host_response(refusal, request.url.path)
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    def _mcp_oauth_auth(request: Request, principal: dict | None = None):
        """Resolve a valid MCP bearer to the same pinned Hoja identity."""
        if principal is None:
            raw = request.headers.get("authorization", "")
            if not raw.lower().startswith("bearer "):
                return None
            principal = _mcp_bearer_credential(raw[7:].strip())
        if principal is None:
            return None
        if tenant_hosts.host_refusal(host_map, request.scope, principal["org_id"], credential=True):
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
    from hoja_server.mcp_oauth import (
        DEVICE_GRANT,
        REQUEST_CLIENT_IP,
        REQUEST_META,
        HojaOAuthProvider,
        build_server,
    )

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
    mcp_provider = HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy, public_host=public_host,
                                      workspace_host=host_map.host_for)
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

    def _consent_sign_in_url(request: Request, path: str) -> str:
        """Where a signed-out browser goes to sign in before the consent page, and back.

        The way back is carried server-side wherever the hosted sign-in serves
        this host: the control plane's ``/signin?next=<this consent URL>`` hands
        the browser back to ``/auth/google/finish?to=<consent path>`` on this
        host. Elsewhere (no hosted sign-in) the web app's login screen keeps the
        same path in ``next`` and resumes it after the password sign-in.
        """
        pending = request.query_params.get("pending") or ""
        consent = path + "?" + urlencode({"pending": pending})
        control_plane = signup_config.control_plane_url
        if control_plane and _is_tenant_origin(request):
            host = _signup_host(request).split(":")[0].lower()
            target = _tenant_transfer_target(f"https://{host}{consent}")
            if target is not None:
                return f"{control_plane}/signin?next={quote(target, safe='')}"
        return "/auth/login?next=" + quote(consent, safe="")

    async def _mcp_bearer_gate(request: Request, scope, receive, send):
        """Answer what the SDK would get wrong for a bearer, or return the receive to continue with.

        * A valid credential on the wrong host gets 403 wrong_workspace_host
          naming its own MCP URL, not the SDK's 401 (which starts OAuth again).
        * An OAuth token without hoja:write calling a write tool gets 403
          insufficient_scope with the scopes to ask for (MCP authorization,
          "Scope challenge handling"), so the client re-authorizes for write
          instead of the tool failing after the fact. Stateless HTTP puts the
          whole JSON-RPC message in this request body, so the tool name is known
          before anything runs.
        Returns None when a response was sent, else ``(receive, principal)``.
        """
        raw = request.headers.get("authorization", "")
        if not raw.lower().startswith("bearer "):
            return receive, None
        principal = _mcp_bearer_credential(raw[7:].strip())
        if principal is None:
            return receive, None
        refusal = tenant_hosts.host_refusal(host_map, scope, principal["org_id"], credential=True)
        if refusal:
            await _wrong_host_response(refusal, "/mcp")(scope, receive, send)
            return None
        root = scope.get("root_path", "")
        path = scope.get("path", "")
        sub = path[len(root):] if root and path.startswith(root) else path
        if scope.get("method") != "POST" or sub not in ("", "/"):
            return receive, principal
        from hoja_server.mcp_oauth import READ_SCOPE, WRITE_SCOPE
        from hoja_server.mcp_tools import write_tool_names

        if principal["client_id"] == "api-key" or WRITE_SCOPE in json.loads(principal["scopes"] or "[]"):
            return receive, principal
        chunks, more = [], True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)

        async def replay():
            nonlocal body
            if body is None:
                return await receive()
            data, body = body, None
            return {"type": "http.request", "body": data, "more_body": False}

        try:
            parsed = json.loads(body) if len(body) <= _MAX_GRAPHQL_BODY else None
        except ValueError:
            parsed = None
        messages = parsed if isinstance(parsed, list) else [parsed]
        names = write_tool_names()
        if not any(isinstance(m, dict) and m.get("method") == "tools/call"
                   and isinstance(m.get("params"), dict) and m["params"].get("name") in names
                   for m in messages):
            return replay, principal
        origin = f"https://{_origin_host(request)}"
        description = "This tool changes data and needs hoja:write; reconnect with read and write access."
        challenge = (
            f'Bearer error="insufficient_scope", scope="{READ_SCOPE} {WRITE_SCOPE}", '
            f'resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp", '
            f'error_description="{description}"'
        )
        await JSONResponse({"error": "insufficient_scope", "error_description": description},
                           status_code=403, headers={"WWW-Authenticate": challenge})(scope, replay, send)
        return None

    async def mcp_endpoint(scope, receive, send):
        # MCP operations are logged by the tool dispatcher, which is the only
        # place that knows the tool name: the SDK runs a call in its own task, so
        # anything it records there does not come back to this scope.
        observability.annotate(scope, transport="mcp")
        observability.suppress(scope)
        request = Request(scope)
        auth_context = None
        trusted_peer = _request_from_trusted_proxy(request)
        incoming_host = request.headers.get("host", "").lower()
        canonical_host = public_host.strip().lower()
        pinned_org = tenant_hosts.required_org(scope)
        host_ok = (incoming_host == canonical_host or incoming_host.startswith(canonical_host + ":")
                   or bool(pinned_org))
        if trust_proxy and (not trusted_peer or not host_ok):
            if not host_ok:
                response = JSONResponse({"error": "Untrusted Host header."}, status_code=421)
                await response(scope, receive, send)
                return
            response = JSONResponse({"error": "Request did not originate from a trusted proxy."}, status_code=403)
            await response(scope, receive, send)
            return
        # register_client has no request; it reads the caller's address from here.
        REQUEST_CLIENT_IP.set(_client_ip(request))
        REQUEST_META.set({
            "ip": _client_ip(request),
            "user_agent": request.headers.get("user-agent"),
            "device_name": request.query_params.get("device_name"),
            "actor_hint": (request.headers.get("x-hoja-actor", "") or "").strip().lower() or None,
            "session_id": tenant_context.clean_session_id(request.headers.get("x-hoja-session-id")),
        })
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        sub = path[len(root):] if root and path.startswith(root) else path
        method = scope.get("method")
        issuer = f"https://{_origin_host(request)}/mcp"
        # Metadata exists twice: this app's /.well-known routes and the SDK's own
        # copies under /mcp. Both answer with the one document.
        if method == "GET" and sub.endswith("/.well-known/oauth-authorization-server"):
            await JSONResponse(_authorization_metadata(request))(scope, receive, send)
            return
        if method == "GET" and "/.well-known/oauth-protected-resource" in sub:
            await JSONResponse(_resource_metadata(request))(scope, receive, send)
            return
        if method == "POST" and sub == "/token":
            receive, body = await _buffer_body(receive)
            form = parse_qs(body.decode("latin-1"))
            if (form.get("grant_type") or [""])[0] == DEVICE_GRANT:
                # The SDK token handler knows authorization_code and refresh_token
                # only; the RFC 8628 device grant is answered here.
                status, payload = await run_in_threadpool(
                    _provider_for(request).poll_device, {k: v[0] for k, v in form.items()})
                await JSONResponse(payload, status_code=status,
                                   headers={"cache-control": "no-store", "pragma": "no-cache"})(scope, receive, send)
                return
        if method == "POST" and sub == "/revoke":
            # RFC 7009 lets a public client (token_endpoint_auth_method "none")
            # revoke with client_id alone. The SDK's RevocationRequest declares
            # `client_secret: str | None` without a default (mcp 2.2.0), which
            # makes the field required, so every public client's logout was a
            # 400. Supply the empty secret the SDK's own "none" check expects.
            receive, body = await _buffer_body(receive)
            if "client_secret" not in parse_qs(body.decode("latin-1"), keep_blank_values=True):
                body = body + (b"&" if body else b"") + b"client_secret="
                headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"content-length"]
                scope = {**scope, "headers": headers + [(b"content-length", str(len(body)).encode())]}
                sent = False

                async def receive():  # noqa: F811 - replaces the buffered receive with the amended body
                    nonlocal sent
                    if sent:
                        return {"type": "http.disconnect"}
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
        if method == "GET" and sub == "/authorize":
            # RFC 9207: every authorization response names the issuer, including
            # the SDK's error redirects back to the client.
            send = _with_issuer_on_error_redirect(send, issuer)
        if scope.get("method") in ("GET", "POST") and path.endswith("/oauth/consent"):
            # Consent is a person at a browser: only the Hoja session cookie counts.
            # A bearer (API key or OAuth token) never approves a grant, so it is
            # not consulted here at all.
            uid, org, identity_id, is_session = _auth(request)
            if uid and is_session:
                member = store.membership_for_profile(org, uid)
                auth_context = {
                    "user_id": uid,
                    "org_id": org,
                    "identity_id": identity_id or (member or {}).get("identity_id"),
                    "source": "browser_session",
                }
            elif scope.get("method") == "GET":
                response = RedirectResponse(
                    _consent_sign_in_url(request, path), status_code=303,
                    headers={"cache-control": "no-store"})
                await response(scope, receive, send)
                return
        else:
            gated = await _mcp_bearer_gate(request, scope, receive, send)
            if gated is None:
                return
            receive, principal = gated
            # Resource calls: the SDK authenticates the bearer itself; this keeps
            # the resolved principal on the scope for the routes that read it.
            auth_context = _mcp_oauth_auth(request, principal) if principal else None
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

    def _authorization_metadata(request: Request) -> dict:
        """RFC 8414 metadata for this host's authorization server (issuer <origin>/mcp)."""
        origin = f"https://{_origin_host(request)}" if trust_proxy else "https://mcp.invalid"
        issuer = origin + "/mcp"
        return {
            "issuer": issuer, "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token", "registration_endpoint": issuer + "/register",
            "revocation_endpoint": issuer + "/revoke",
            "device_authorization_endpoint": issuer + "/device",
            "scopes_supported": ["hoja:read", "hoja:write"], "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token", DEVICE_GRANT],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": True,
        }

    def _resource_metadata(request: Request) -> dict:
        issuer = (f"https://{_origin_host(request)}" if trust_proxy else "https://mcp.invalid") + "/mcp"
        # Both scopes: spec clients request what is listed here, and the consent
        # screen lets the person narrow it to read only.
        return {"resource": issuer, "authorization_servers": [issuer],
                "scopes_supported": ["hoja:read", "hoja:write"]}

    async def authorization_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        return JSONResponse(_authorization_metadata(request))

    async def resource_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        return JSONResponse(_resource_metadata(request))

    def _provider_for(request: Request):
        """The authorization server for this request's host (pinned workspace or canonical)."""
        pinned = tenant_hosts.required_org(request.scope)
        return HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy, public_host=_origin_host(request),
                                 required_org=pinned, workspace_host=host_map.host_for)

    async def _buffer_body(receive):
        """Read a whole request body, and a receive that replays it."""
        chunks, more = [], True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return replay, body

    def _with_issuer_on_error_redirect(send, issuer: str):
        async def wrapped(message):
            if message["type"] == "http.response.start" and message["status"] in (302, 303, 307):
                headers = []
                for name, value in message.get("headers", []):
                    if name.lower() == b"location":
                        location = value.decode("latin-1")
                        query = parse_qs(urlsplit(location).query)
                        if "error" in query and "iss" not in query:
                            location += ("&" if "?" in location else "?") + urlencode({"iss": issuer})
                            value = location.encode("latin-1")
                    headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)
        return wrapped

    async def device_page(request: Request):
        """<origin>/device: a person approves an app that showed them a code (RFC 8628)."""
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        from hoja_server import consent_pages

        provider = _provider_for(request)
        raw_code = request.query_params.get("user_code") or ""
        uid, org, identity_id, is_session = await run_in_threadpool(_auth, request)
        if not (uid and is_session):
            if request.method == "POST":
                return HTMLResponse(consent_pages.denied_page(
                    title="Sign in to approve", message="Approving an app needs a signed-in browser."),
                    status_code=401)
            return RedirectResponse(_device_sign_in_url(request), status_code=303,
                                    headers={"cache-control": "no-store"})
        member = store.membership_for_profile(org, uid)
        auth = {"user_id": uid, "org_id": org, "source": "browser_session",
                "identity_id": identity_id or (member or {}).get("identity_id")}
        no_store = {"cache-control": "no-store"}
        if request.method == "POST":
            form = await request.form()
            outcome, page = await run_in_threadpool(provider.decide_device, auth, raw_code, form)
            if page:
                return HTMLResponse(page, status_code=403 if outcome == "refused" else 400, headers=no_store)
            return HTMLResponse(consent_pages.device_done_page(approved=outcome == "approved"), headers=no_store)
        if not raw_code:
            return HTMLResponse(consent_pages.device_code_page(), headers=no_store)
        row = provider.pending_device(raw_code)
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            return HTMLResponse(consent_pages.device_code_page(
                error="That code is not valid. Check it and try again."), status_code=400, headers=no_store)
        if row["status"] != "pending" or row["expires_at"] <= now:
            return HTMLResponse(consent_pages.expired_page(client_name="Hoja CLI"), status_code=400,
                                headers=no_store)
        workspaces, elsewhere = provider.consent_workspaces(auth)
        if not workspaces:
            return HTMLResponse(consent_pages.wrong_host_page(client_name="Hoja CLI", workspaces=elsewhere),
                                status_code=409, headers=no_store)
        selected = org if any(w["id"] == org for w in workspaces) else workspaces[0]["id"]
        profile = provider._member_profile(auth, selected) or uid
        with tenant_context.scoped(selected):
            viewer = store.get_user(selected, profile) or {}
        label = row["device_label"]
        return HTMLResponse(consent_pages.consent_page(
            client_name="Hoja CLI",
            redirect_host=(f"{label} · code {row['user_code']}" if label else f"code {row['user_code']}"),
            workspaces=workspaces, selected_org=selected,
            workspace_fixed=bool(tenant_hosts.required_org(request.scope)),
            host=_origin_host(request),
            can_write="hoja:write" in provider.max_scopes(selected, profile),
            write_requested="hoja:write" in json.loads(row["scopes"]),
            csrf_token=row["csrf_token"], email=viewer.get("email"),
            switch_account_url="/signout", other_hosts=elsewhere,
            returns_label="Device",
        ), headers=no_store)

    def _device_sign_in_url(request: Request) -> str:
        code = request.query_params.get("user_code") or ""
        path = "/device" + (("?" + urlencode({"user_code": code})) if code else "")
        control_plane = signup_config.control_plane_url
        if control_plane and _is_tenant_origin(request):
            host = _signup_host(request).split(":")[0].lower()
            target = _tenant_transfer_target(f"https://{host}{path}")
            if target is not None:
                return f"{control_plane}/signin?next={quote(target, safe='')}"
        return "/auth/login?next=" + quote(path, safe="")

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
            _enter_request_context(websocket, org, uid)
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
            connectors = connector_bindings_for(store, org, uid)
            connector = connectors[0] if connectors else None
            if durable_agent is not None:
                gen = durable_agent.stream_reply(store, org, uid, messages, base_context, thread_id, connector)
            elif len(connectors) > 1:
                gen = stream_reply(
                    store,
                    org,
                    uid,
                    messages,
                    _schema,
                    SCHEMA_SDL,
                    base_context,
                    thread_id,
                    connector,
                    connectors=connectors,
                )
            else:
                gen = stream_reply(
                    store,
                    org,
                    uid,
                    messages,
                    _schema,
                    SCHEMA_SDL,
                    base_context,
                    thread_id,
                    connector,
                )
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
        # What this request is, for the one operations line the edge writes. The
        # transport is known before auth; the actor and the operation are added
        # as soon as they are known, so a rejected request is still identifiable.
        observability.annotate(request.scope, transport="graphql")
        uid, org, identity_id, is_browser_session = await run_in_threadpool(_auth, request)
        if uid:  # resolved in a worker thread: its context copy is gone, bind here
            _enter_request_context(request, org, uid)
            observability.annotate(
                request.scope, org=org,
                actor_kind=getattr(request.state, "actor_kind", "human"),
                actor_id=getattr(request.state, "actor_id", None) or uid,
            )
        refusal = getattr(request.state, "host_refusal", None) if not uid else None
        if refusal:
            observability.annotate(request.scope, operation="unauthenticated", outcome="error:WRONG_WORKSPACE_HOST")
            return _wrong_host_response(refusal, "/graphql")
        if not uid:
            observability.annotate(request.scope, operation="unauthenticated", outcome="error:UNAUTHENTICATED")
            # A presented bearer that failed (unknown, expired or revoked) gets the
            # RFC 6750 challenge, so an OAuth client refreshes instead of giving up.
            bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
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
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'} if bearer else None,
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
        if isinstance(query, str):
            # Shape only: operation type, name and top-level field names. The
            # arguments and variables are never read here.
            observability.annotate(request.scope, operation=observability.describe_graphql(query))
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
            "pinned_org": tenant_hosts.required_org(request.scope),
            "org_redirect": lambda target_org: _org_redirect(request, target_org),
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
        # GraphQL answers 200 even when it failed, so the outcome is read from the
        # result rather than from the HTTP status. Only the first error's code is
        # used: a log line that names a code, not a specification of failures.
        errors = result.get("errors") if isinstance(result, dict) else None
        if errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            code = (first.get("extensions") or {}).get("code")
            observability.annotate(request.scope, outcome=f"error:{code or 'GRAPHQL'}")
        if isinstance(query, str) and "mutation" in query:
            hub.kick()
        target = context.get("switch_target")
        pinned_here = context["pinned_org"]
        if target and pinned_here and target["organization_id"] != pinned_here:
            # Never trade this host's session for one it would refuse: that is
            # how a switch on a workspace's own address used to lock it out.
            target = None
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

    def _announce_member(request: Request, email: str) -> BackgroundTask | None:
        """Tell the control plane about a membership we just proved, after the response.

        Called only where the address is established: an accepted invitation
        (holding the code proves control of the mailbox) or a completed Google
        sign-in on this host. Returns a task for the caller to attach to its
        response, so the HTTP call runs after the browser has its answer and
        never on the event loop; ``None`` when there is nothing to announce.

        Skipped in the shared tier: there the control plane reads membership
        rows directly (``workspace_directory``), and a hint could only be a
        staler copy of the same database.

        Signed with this host's own key: the configured
        ``HOJA_IDENTITY_TRANSFER_KEY`` when a dedicated tenant has one, else
        the key derived from the master for this host, which is exactly the key
        ``/internal/membership`` reconstructs to verify it.
        """
        if _shared_directory() or not signup_config.control_plane_url:
            return None
        host = _signup_host(request).split(":")[0].lower()
        slug = host.split(".")[0]
        key = signup_config.identity_transfer_key
        if not key and signup_config.identity_transfer_master and host:
            try:
                key = identity_transfer.tenant_key(signup_config.identity_transfer_master, host)
            except identity_transfer.TransferError:
                key = None
        if not key or "@" not in (email or ""):
            return None
        expires = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        return BackgroundTask(
            membership_announce.announce,
            tenant_key=key,
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
        response = JSONResponse({"status": "accepted"})
        response.background = _announce_member(
            request, str(body.get("email") or "").strip().lower())
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
        elsewhere = _org_redirect(request, identity_org)
        if elsewhere:
            # An enterprise workspace is used on its own host, never on the apex:
            # this session would be a host-only cookie in the wrong place.
            store.revoke_browser_session(session_token)
            return JSONResponse({"status": "redirect", "redirectUrl": elsewhere})
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

    def _revoke_every_session(request: Request) -> tuple[list[str], str | None]:
        """Revoke every session this browser holds, and name who they belonged to.

        A workspace session (``hoja_session``) and a hosted sign-up session
        (``hoja_signup_session``) are independent families. Signing out of one and
        leaving the other live is how a "signed out" browser signs itself back in:
        ``/signup`` reads a live sign-up session as ``google_verified`` and, when
        the person already has an active workspace, redirects them straight back
        into it. That is the "sign out doesn't kick me out" report.

        The returned address is the verified email both families agree on; it is
        what lets the caller revoke the *other* hosts' rows, which this browser
        cannot reach because the cookies are host-only.

        Runs in the explicit system scope: ``/signout`` and ``/signin/signout``
        are not under a system path prefix, and ``browser_session`` is under
        row-level security, so without it the revoke would match no rows in a
        Postgres deployment and sign-out would silently leave the session live.
        """
        cleared: list[str] = []
        address: str | None = None
        with tenant_context.system():
            workspace_token = request.cookies.get("hoja_session")
            if workspace_token:
                session = store.resolve_browser_session(workspace_token) or {}
                address = address or _identity_address(session.get("identity_id"))
                store.revoke_browser_session(workspace_token)
                cleared.append("workspace")
            signup_token = request.cookies.get(_SIGNUP_SESSION_COOKIE)
            if signup_token:
                identity = signup_store.resolve_session(signup_token) or {}
                address = address or (str(identity.get("email") or "").strip().lower() or None)
                signup_store.revoke_session(signup_token)
                cleared.append("signup")
        return cleared, address

    def _identity_address(identity_id: str | None) -> str | None:
        """The verified address behind a global identity, or None."""
        if not identity_id:
            return None
        try:
            with engine.connect() as conn:
                email = conn.execute(
                    select(global_identity.c.email).where(global_identity.c.id == identity_id)
                ).scalar()
        except Exception:  # pragma: no cover - a database that cannot answer is not a 500
            _security_log.warning("auth.identity_address failed", exc_info=True)
            return None
        return (email or "").strip().lower() or None

    def _revoke_sessions_across_hosts(address: str | None) -> dict:
        """End every Hoja session row this verified address still owns.

        The cookies are host-only (R5): a tenant host can delete only its own
        ``hoja_session``, and nothing it does removes ``app.hoja.work``'s
        ``hoja_signup_session``. Those are server-side rows, and in production one
        app process and one database serve every tenant (ADR-003), so a single
        pair of UPDATEs is all "sign out" needs to stick for the whole browser.

        The rows are joined on the address: ``browser_session`` hangs off
        ``user`` → ``organization_membership`` → ``global_identity.email``, and
        ``signup_session`` off ``signup_identity`` (provider=google, same email).
        That makes this a *global* sign-out for the account, not only for this
        browser: no cross-host row carries a browser id, so there is nothing
        narrower to match on.

        A deployment where a tenant really is an isolated database has none of
        these rows here; the statements match nothing, the caller carries on, and
        the control plane's own sign-out page still removes its cookie. A
        database that cannot answer at all is logged, never raised: sign-out must
        not fail closed into a 500.
        """
        revoked = {"workspace": 0, "signup": 0}
        if not address:
            return revoked
        stamp = now_iso()
        try:
            # System scope: browser_session and organization_membership are under
            # row-level security, and this deliberately spans every organization.
            with tenant_context.system(), engine.begin() as conn:
                identity_ids = [row[0] for row in conn.execute(
                    select(global_identity.c.id).where(global_identity.c.email == address)
                )]
                signup_ids = [row[0] for row in conn.execute(
                    select(signup_identity.c.id).where(and_(
                        signup_identity.c.provider == "google",
                        signup_identity.c.email == address,
                    ))
                )]
                if identity_ids:
                    user_ids = select(organization_membership.c.profile_user_id).where(
                        organization_membership.c.identity_id.in_(identity_ids)
                    )
                    result = conn.execute(
                        browser_session.update()
                        .where(and_(
                            browser_session.c.user_id.in_(user_ids),
                            browser_session.c.revoked_at.is_(None),
                        ))
                        .values(revoked_at=stamp)
                    )
                    revoked["workspace"] = int(result.rowcount or 0)
                if signup_ids:
                    result = conn.execute(
                        signup_session.update()
                        .where(and_(
                            signup_session.c.identity_id.in_(signup_ids),
                            signup_session.c.revoked_at.is_(None),
                        ))
                        .values(revoked_at=stamp)
                    )
                    revoked["signup"] = int(result.rowcount or 0)
        except Exception:  # pragma: no cover - degrade, never fail the sign-out
            _security_log.warning("auth.logout cross_host failed", exc_info=True)
        return revoked

    def _canonical_signout_url(request: Request) -> str | None:
        """Where *this* host signs out, or None when the deployment is unhosted.

        Returned to the SPA with the logout response so the next step is a
        top-level navigation the browser can actually complete, instead of a
        state reset on a host that still holds a live session one level up.
        """
        control_plane = signup_config.control_plane_url
        if not control_plane:
            return None
        if not _is_tenant_origin(request):
            # The control plane's own sign-out stays here; `from` names this
            # origin so "sign in again" returns to app.hoja.work, not a tenant.
            return f"/signout?from={quote(control_plane, safe='')}"
        back = _tenant_origin_for(request)
        if back is None:
            return f"{control_plane}/signout"
        return f"{control_plane}/signout?from={quote(back, safe='')}"

    async def auth_logout(request: Request):
        csrf = _cookie_csrf_error(request, json_only=False)
        if csrf:
            _security_log.info("auth.logout scope=all outcome=refused_csrf")
            return csrf
        cleared, address = _revoke_every_session(request)
        revoked = _revoke_sessions_across_hosts(address)
        _security_log.info(
            "auth.logout scope=all cleared=%s cross_host=%s,%s",
            ",".join(cleared) or "-", revoked["workspace"], revoked["signup"],
        )
        response = JSONResponse({
            "status": "logged_out",
            "cleared": cleared,
            "revoked": revoked,
            "signOutUrl": _canonical_signout_url(request),
        })
        for cookie in ("hoja_session", _SIGNUP_SESSION_COOKIE):
            response.delete_cookie(cookie, **_session_cookie_attrs(request))
        return response

    def _session_elsewhere(request: Request) -> str | None:
        """For a browser on the apex holding a session of a workspace with its own host:
        where that workspace actually lives. None everywhere else.

        Read straight from the cookie, before ``_auth``: whether the apex still
        *accepts* such a session is the credential layer's decision, while this
        only tells the browser where to go instead.
        """
        if not host_map.canonical or tenant_hosts.required_org(request.scope):
            return None
        raw = request.cookies.get("hoja_session")
        if not raw:
            return None
        with tenant_context.system():
            session = store.resolve_browser_session(raw)
        return _org_redirect(request, session["organization_id"]) if session else None

    async def auth_me(request: Request):
        elsewhere = _session_elsewhere(request)
        if elsewhere:
            return JSONResponse(
                {"error": "This workspace has its own address.",
                 "reason": "wrong_workspace_host", "redirectUrl": elsewhere},
                status_code=409, headers={"cache-control": "no-store"},
            )
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

    #: "Ask which Google account next time." Host-only, short-lived, and set only
    #: by a sign-out or a "use a different account" link: it turns the account
    #: chooser from the default back into the exception. Without a marker every
    #: ``/signin`` on a live control session would re-run Google (the extra hop
    #: this fixes); with one, resuming the identity somebody just ended is
    #: impossible (the HOJA-206 guarantee).
    _CHOOSE_ACCOUNT_COOKIE = "hoja_choose_account"
    #: One Google round-trip on a slow phone. Long enough to survive the hop from
    #: a tenant's sign-out to the control plane, short enough not to resurrect on
    #: the next day's visit.
    _CHOOSE_ACCOUNT_MAX_AGE = 15 * 60

    def _account_choice_pending(request: Request) -> bool:
        return request.cookies.get(_CHOOSE_ACCOUNT_COOKIE) == "1"

    def _mark_account_choice_pending(response: Response, request: Request) -> None:
        response.set_cookie(
            _CHOOSE_ACCOUNT_COOKIE,
            "1",
            max_age=_CHOOSE_ACCOUNT_MAX_AGE,
            **_session_cookie_attrs(request),
        )

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

    def _signout_action(request: Request) -> str:
        """The form action for "use a different account" on a server-rendered page.

        ``no_workspace_page`` renders a ``<form method="post">``, so this has to
        be a POST endpoint that exists and that ends the whole sign-out — not
        ``/auth/google/logout``, which cleared only the sign-up cookie and handed
        the same identity straight back.
        """
        return "/signout"

    def _signin_failure(
        request: Request,
        *,
        error: str,
        reason: str,
        status_code: int,
        host: str | None = None,
        email: str | None = None,
        retry_url: str | None = None,
        invited_hint: str | None = None,
        invited_url: str | None = None,
    ) -> Response:
        """One refusal, two audiences: a page a person can act on, and the JSON contract.

        The refusal stays a refusal: same status, same reason code, no statement about
        whether the address or workspace exists. The error id is the only new data, and
        it is what makes a support conversation possible instead of a dead end.

        ``retry_url`` restarts the same sign-in for the same workspace; it is the
        primary action for a transient failure (an expired link, a cancelled
        Google prompt), which must never read as "not authorised". It defaults to
        this host's sign-in entry.
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
                retry_url=retry_url or _canonical_login_url(request) or "/signin",
                invited_hint=invited_hint,
                invited_url=invited_url,
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

    #: The native app's registered scheme (mobile/app.json "scheme"). Only the app
    #: holding the matching PKCE verifier can finish a handoff sent here.
    _APP_CALLBACK = "hoja://auth/finish"

    def _app_challenge(request: Request, tenant_target: str | None) -> str | None:
        """The native app's S256 challenge, honoured only for a tenant handoff."""
        if tenant_target is None:
            return None
        return identity_transfer.valid_challenge(request.query_params.get("app_challenge"))

    def _tenant_transfer_redirect(
        *,
        email: str,
        subject: str,
        name: str | None,
        picture: str | None,
        tenant_target: str,
        source: str,
        request: Request | None = None,
        client_challenge: str | None = None,
    ) -> Response:
        """Mint the per-tenant handoff from an already verified identity.

        A native app's flow (``client_challenge`` set, HOJA-733) returns to the
        app's ``hoja://auth/finish`` callback instead of the tenant. The app then
        redeems the tenant's finish URL with its PKCE verifier, so a token taken
        from the callback by another app that registered ``hoja://`` is refused.
        """
        master = signup_config.identity_transfer_master
        if not master:
            if request is not None:
                return _signin_failure(
                    request, error="Identity handoff is not configured on this deployment.",
                    reason="transfer_unconfigured", status_code=503,
                    host=urlsplit(tenant_target).hostname,
                )
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
            client_challenge=client_challenge,
        )
        _security_log.info(
            "auth.handoff source=%s target_host=%s app=%s",
            source, target_host, bool(client_challenge),
        )
        if client_challenge:
            response = RedirectResponse(
                f"{_APP_CALLBACK}?host={quote(target_host, safe='')}&t={quote(transfer, safe='')}",
                status_code=302,
            )
            response.delete_cookie(_OIDC_FLOW_COOKIE, path="/")
            return response
        # Build the handoff from the host, never from the whole target: a target
        # may carry `?invite=<code>`, and concatenating a path onto that both
        # swallows `/auth/google/finish` into the parameter and puts the signed
        # token in a URL that lands in history and access logs.
        finish = f"https://{target_host}/auth/google/finish?t={quote(transfer, safe='')}"
        # A pending app approval is the one deep path carried through the handoff;
        # the tenant validates it again with _safe_return_to before redirecting.
        back = identity_transfer.return_path(tenant_target)
        if back:
            finish += f"&to={quote(back, safe='')}"
        response = RedirectResponse(finish, status_code=302)
        response.delete_cookie(_OIDC_FLOW_COOKIE, path="/")
        return response

    def _shared_directory() -> bool:
        """True when the control plane runs in the same deployment as the workspaces.

        The shared tier pins enterprise workspaces to their own hosts with
        ``TenantHosts``; when its canonical host is also the control plane, the
        organization and membership rows are in this database and are the
        authority on who belongs where.
        """
        return bool(host_map.canonical) and host_map.canonical == _control_plane_host()

    def _dedicated_origin(slug: str) -> str:
        """A dedicated tenant's origin: its slug under the base domain, by construction.

        Only for the dedicated tier, where a tenant's host *is* ``<slug>.<base>``
        (its announce key is derived from that name). The shared tier never
        guesses: it reads ``organization.vanity_host``.
        """
        return f"https://{slug}.{signup_config.tenant_base_domain}/"

    def _directory(identity: dict | None) -> list[dict]:
        """The workspaces this control-plane identity can be sent to, with real origins.

        Entries are ``{"name", "slug", "host", "origin", "status", "role"}`` with
        ``status`` ``active`` or ``invited``. Shared tier: the membership rows
        themselves, for the Google-verified address only. Dedicated tier: what
        the control plane recorded at sign-up and what tenants announced.
        """
        if identity is None:
            return []
        if _shared_directory():
            if not identity.get("email_verified"):
                return []
            return workspace_directory.workspaces_for_email(engine, identity.get("email"))
        entries = []
        for row in signup_store.workspaces_for(
            subject=identity.get("subject"), email=identity.get("email")
        ):
            if row["status"] != "active":
                continue
            origin = _dedicated_origin(row["slug"])
            entries.append({
                "name": row["slug"], "slug": row["slug"],
                "host": urlsplit(origin).hostname or "", "origin": origin,
                "status": "active", "role": row.get("role"),
            })
        return entries

    def _origin_for_slug(slug: str) -> str | None:
        """Where a sign-up slug's workspace lives, or None when it has no reachable host."""
        if _shared_directory():
            found = workspace_directory.workspace_for_slug(engine, slug)
            return found["origin"] if found else None
        return _dedicated_origin(slug)

    def _unknown_workspace(request: Request, target: str | None) -> Response | None:
        """A page for a tenant target that names no workspace, before Google runs.

        Only where the answer is known: the shared tier, whose control plane
        sees every pinned host. A dedicated control plane cannot tell, so it
        keeps sending the person on and lets the tenant decide.
        """
        if target is None or not _shared_directory():
            return None
        host = (urlsplit(target).hostname or "").lower()
        if host and host in host_map.hosts():
            return None
        _security_log.info("auth.unknown_workspace host=%s", host or "-")
        return HTMLResponse(
            signin_pages.unknown_workspace_page(host=host, sign_in_url="/signin"),
            status_code=404,
            headers={"cache-control": "no-store"},
        )

    def _org_redirect(request: Request, org_id: str | None) -> str | None:
        """Where the browser must go to use ``org_id``, when it is not this host.

        Shared tier only. A workspace session is a host-only cookie, and a pinned
        host accepts only its own organization; an enterprise workspace (one with
        a ``vanity_host``) is used on that host, never on the apex. Returns the
        URL that starts the Google handoff for that host (a full navigation), or
        None when this host is the right one.
        """
        if not org_id or not host_map.canonical:
            return None
        pinned = tenant_hosts.required_org(request.scope)
        if pinned == org_id:
            return None
        found = None
        with tenant_context.system(), engine.connect() as conn:
            vanity = conn.execute(
                select(organization.c.vanity_host).where(organization.c.id == org_id)
            ).scalar_one_or_none()
        if vanity:
            found = workspace_directory.origin_for_host(vanity)
        if found is None:
            if not pinned:
                return None  # an apex workspace on the apex: nothing to move
            return f"https://{host_map.canonical}/"
        if signup_config.control_plane_url and identity_transfer.tenant_target(found):
            return _handoff_start_url(found)
        return found

    def _handoff_start_url(origin: str, *, ask_account: bool = False) -> str:
        """Start the handoff for a specific workspace.

        ``choose_account`` forces a full Google sign-in instead of resuming the
        control session. It is opt-in, because resuming is the correct answer for
        someone who already proved who they are and merely picked a workspace;
        asking for it unconditionally is what made every "choose a workspace"
        click a second Google round-trip. It is set only where the person asked to
        change accounts: a complaint is "it forgot my workspace", never "it did
        not make me sign in again".
        """
        control_plane = signup_config.control_plane_url or ""
        url = f"{control_plane}/auth/google/start?return_to={quote(origin, safe='')}"
        return f"{url}&choose_account=1" if ask_account else url

    def _resume_tenant_handoff(identity: dict, tenant_target: str, *,
                               client_challenge: str | None = None) -> Response | None:
        """Mint the audience-bound handoff from a live control session.

        Google is already done on this host, so the only thing between the
        browser and the workspace is the signed token. Returns None when this
        deployment cannot mint one (no master key, or an identity whose address
        Google did not verify) so the caller can fall back to the Google start.
        """
        if not signup_config.identity_transfer_master:
            return None
        full_identity = signup_store.identity_by_subject(identity["subject"]) or identity
        if full_identity.get("email_verified") is not True:
            return None
        return _tenant_transfer_redirect(
            email=full_identity["email"],
            subject=full_identity["subject"],
            name=full_identity.get("name"),
            picture=full_identity.get("picture"),
            tenant_target=tenant_target,
            source="control_session",
            client_challenge=client_challenge,
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
        if _shared_directory() and host not in host_map.hosts():
            return HTMLResponse(
                signin_pages.no_workspace_page(
                    email=(identity or {}).get("email"),
                    sign_out_url=_signout_action(request),
                    invite_action="/signin/invited",
                    invite_error=f"There's no workspace at {host}. Check the link in your email.",
                ),
                status_code=404,
                headers={"cache-control": "no-store"},
            )
        _security_log.info("auth.invited_link host=%s", host)
        return RedirectResponse(
            f"https://{host}/?invite={quote(code, safe='')}", status_code=303
        )

    async def sign_out_entry(request: Request):
        """Sign out of Hoja on every host this browser reached, then say so.

        Two defects end here. ``/signin/signout`` used to revoke only the sign-up
        session and then 302 back to ``/signin?next=<workspace>``, which
        immediately re-handed-off the workspace session that was still live — the
        "it shows me workspace screens and then moves me" bounce. And a tenant's
        ``POST /auth/logout`` cannot delete a cookie on ``app.hoja.work`` at all,
        because the cookies are host-only; the control plane's own signup session
        survived and the next sign-in silently resumed it.

        So this route revokes server-side (this host's cookies, plus every
        session row the account still owns — see ``_revoke_sessions_across_hosts``),
        marks the browser as "ask which account next time", and renders a page
        that only states the fact. A GET, because its only effect is ending
        sessions: there is nothing else for a cross-site request to trigger.
        """
        control_plane = signup_config.control_plane_url
        if control_plane and _is_tenant_origin(request):
            back = _tenant_origin_for(request)
            suffix = f"?from={quote(back, safe='')}" if back else ""
            return RedirectResponse(f"{control_plane}/signout{suffix}", status_code=302)
        raw_from = (
            request.query_params.get("from")
            or request.query_params.get("next")
            or ""
        )
        back = _tenant_transfer_target(raw_from) if raw_from else None
        if raw_from and back is None:
            # Not one of our tenant origins (this includes app.hoja.work itself).
            # The return link falls back to the local entry; nothing is redirected
            # to a value that came in on the query string.
            _security_log.info(
                "auth.signout from_ignored=%s", urlsplit(raw_from).hostname or "-"
            )
        cleared, address = _revoke_every_session(request)
        revoked = _revoke_sessions_across_hosts(address)
        _security_log.info(
            "auth.signout cleared=%s cross_host=%s,%s",
            ",".join(cleared) or "-", revoked["workspace"], revoked["signup"],
        )
        sign_in_url = "/signin" + (f"?next={quote(back, safe='')}" if back else "")
        response = HTMLResponse(
            signin_pages.signed_out_page(sign_in_url=sign_in_url),
            headers={"cache-control": "no-store"},
        )
        for cookie in (
            "hoja_session",
            _SIGNUP_SESSION_COOKIE,
            _OIDC_FLOW_COOKIE,
            _INVITE_COOKIE,
        ):
            response.delete_cookie(cookie, path="/")
        _mark_account_choice_pending(response, request)
        return response

    async def sign_in_signout(request: Request):
        """Kept for the refusal and invitation screens: "use a different account".

        Same action as ``/signout`` — revoke, then land on the signed-out page —
        so a retry can no longer re-mint the identity it just ended.
        """
        return await sign_out_entry(request)

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
        unknown = _unknown_workspace(request, next_target)
        if unknown is not None:
            return unknown
        identity = _signup_identity(request)
        # True only after a sign-out or a "use a different account" link. Every
        # other visit is a resume: the identity is already verified, and sending
        # it through Google again is the extra hop this entry used to add.
        ask_account = _account_choice_pending(request)
        if identity is not None:
            # Invitations count: a pending invitation is a workspace this address
            # can open (its membership row already exists), and it is the one
            # place a newly invited person can be.
            active = _directory(identity)
            # The person named a workspace; the directory is a fallback for an
            # entry reached without one, never an override of their choice.
            target = next_target or (active[0]["origin"] if len(active) == 1 else None)
            if target is not None:
                if ask_account:
                    response = RedirectResponse(
                        _handoff_start_url(target, ask_account=True), status_code=302
                    )
                    # One sign-in asks, the next one resumes.
                    response.delete_cookie(_CHOOSE_ACCOUNT_COOKIE, path="/")
                    return response
                resumed = _resume_tenant_handoff(identity, target)
                if resumed is not None:
                    return resumed
                # No handoff key on this deployment: the Google start is the only
                # path left that can still succeed, so it must not resume.
                return RedirectResponse(
                    _handoff_start_url(target, ask_account=True), status_code=302
                )
            if len(active) > 1:
                return HTMLResponse(
                    signin_pages.choose_workspace_page(
                        workspaces=[
                            {
                                "name": row["name"],
                                "host": row["host"],
                                "status": row["status"],
                                "url": _handoff_start_url(row["origin"], ask_account=ask_account),
                            }
                            for row in active
                        ],
                        email=identity.get("email"),
                        sign_out_url=_switch_account_url(request),
                    ),
                    headers={"cache-control": "no-store"},
                )
            # The default sign-out control here is the account the session
            # belongs to, not an endpoint that only clears the sign-up cookie.
            return HTMLResponse(
                signin_pages.no_workspace_page(
                    email=identity.get("email"),
                    sign_out_url=_signout_action(request),
                    invite_action="/signin/invited",
                ),
                headers={"cache-control": "no-store"},
            )

        return_to = "/signin" + (
            f"?next={quote(next_target, safe='')}" if next_target is not None else ""
        )
        google_url = "/auth/google/start?return_to=" + quote(return_to, safe="")
        if next_target is not None:
            # A workspace was already chosen before the browser got here (a
            # tenant's Login card links to exactly this URL). The page whose only
            # control is "Continue with Google" is then one screen too many: go
            # to the same URL its button pointed at, with the same return_to, so
            # an invitation in flight still survives the round-trip.
            return RedirectResponse(google_url, status_code=302)
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
        unknown = _unknown_workspace(request, tenant_target)
        if unknown is not None:
            return unknown
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
            # entry asks for choose_account=1 only when the person explicitly
            # asked to change accounts (a sign-out or a refusal page), because
            # resuming would otherwise hand them the identity they just left.
            resumed = _resume_tenant_handoff(
                identity, tenant_target, client_challenge=_app_challenge(request, tenant_target))
            if resumed is not None:
                return resumed
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
            client_challenge=_app_challenge(request, tenant_target),
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
            return _signin_failure(
                request, error="Sign-up is not configured on this deployment.",
                reason="signup_unconfigured", status_code=503,
            )
        flow, flow_reason = signup_store.consume_flow(
            state=request.query_params.get("state") or "",
            binding=request.cookies.get(_OIDC_FLOW_COOKIE),
        )
        if flow is None:
            # Generic message: a caller learns that it failed, not which check.
            # The page says "start again", because none of these is a decision
            # about the person: a stale tab, a reused link, an expired attempt.
            return _signin_failure(
                request, error="This sign-in attempt is invalid or has expired.",
                reason=flow_reason, status_code=400, retry_url="/signin",
            )
        flow_target = _tenant_transfer_target(flow["return_to"])
        # Start over for the same destination: the workspace this flow was for,
        # or the local page it would have returned to.
        retry_url = "/auth/google/start?return_to=" + quote(
            flow["return_to"] or "/signin", safe="")
        flow_host = urlsplit(flow_target).hostname if flow_target else None

        def _failed(error: str, reason: str) -> Response:
            return _signin_failure(request, error=error, reason=reason, status_code=400,
                                   host=flow_host, retry_url=retry_url)

        if request.query_params.get("error"):
            return _failed("Google refused the sign-in.", "provider_error")
        code = request.query_params.get("code") or ""
        if not code:
            return _failed("Missing authorization code.", "code_missing")
        if flow["redirect_uri"] not in signup_config.redirect_uris:
            return _failed("Redirect URI is not allow-listed.", "redirect_uri_not_allowed")
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
            return _failed("Google sign-in could not be verified.", exc.reason)
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
                request=request,
                client_challenge=identity_transfer.valid_challenge(flow.get("client_challenge")),
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

    # ------------------------------------------------------------------
    # HOJA-733: mobile sign-in. Google from the app without a web view.
    # ------------------------------------------------------------------

    def _app_session_args(request: Request, via: str) -> dict:
        """A labelled 90-day sliding app session (Settings -> Connected apps shows the label)."""
        from hoja_server.mcp_oauth import device_label

        name = device_label(request.query_params.get("device")) or "Phone"
        return {"days": APP_SESSION_DAYS, "kind": "app", "label": f"{name} · Hoja app · {via}"}

    async def google_native_sign_in(request: Request):
        """Google ID token from the app's native account sheet -> a transfer bound to the app.

        The ID token's nonce must be the app's PKCE challenge, so a token lifted
        from another flow cannot be replayed here, and the transfer it buys is
        redeemable only with the matching verifier.
        """
        control_host = _control_plane_host()
        if control_host and _signup_host(request).split(":")[0].lower() != control_host:
            return JSONResponse({"error": "Not found.", "reason": "not_control_plane"}, status_code=404)
        if not signup_config.configured or not signup_config.identity_transfer_master:
            return JSONResponse({"error": "Google sign-in is not configured.", "reason": "signup_unconfigured"},
                                status_code=503)
        ip = _client_ip(request)
        if rate_limit_blocked(engine, "google_native", [ip], 20, 60.0):
            return JSONResponse({"error": "Too many attempts. Wait a minute.", "reason": "rate_limited"},
                                status_code=429, headers={"Retry-After": "60"})
        rate_limit_record(engine, "google_native", [ip], 60.0)
        try:
            body = await request.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        challenge = identity_transfer.valid_challenge(body.get("challenge"))
        tenant_target = _tenant_transfer_target(f"https://{str(body.get('host') or '').strip().lower()}/")
        if not challenge or tenant_target is None:
            return JSONResponse({"error": "host and challenge are required.", "reason": "bad_request"},
                                status_code=400)
        unknown = _unknown_workspace(request, tenant_target)
        if unknown is not None:
            return JSONResponse({"error": "There is no workspace at that address.",
                                 "reason": "unknown_workspace"}, status_code=403)
        try:
            jwks = await run_in_threadpool(google_oidc.get_jwks)
            claims = google_oidc.verify_id_token(
                str(body.get("idToken") or ""), jwks=jwks,
                client_id=signup_config.google_client_id, nonce=challenge)
        except google_oidc.OIDCError as exc:
            _security_log.info("auth.native outcome=%s", exc.reason)
            return JSONResponse({"error": "Google sign-in could not be verified.", "reason": exc.reason},
                                status_code=400)
        target_host = urlsplit(tenant_target).hostname or ""
        transfer = identity_transfer.issue(
            key=identity_transfer.tenant_key(signup_config.identity_transfer_master, target_host),
            audience=target_host, email=str(claims["email"]).strip().lower(), subject=claims["sub"],
            name=claims.get("name"), picture=claims.get("picture"), email_verified=True,
            client_challenge=challenge)
        _security_log.info("auth.handoff source=google_native target_host=%s app=True", target_host)
        return JSONResponse({"finishUrl": f"https://{target_host}/auth/google/finish", "transfer": transfer},
                            headers={"cache-control": "no-store"})

    async def android_asset_links(_request: Request):
        """Digital Asset Links for the Hoja Android app (HOJA-733).

        Lets Android verify that https://<this host>/m/... links belong to
        ``work.hoja.mobile``, so no other app can claim them. Served only when
        ``HOJA_ANDROID_CERT_SHA256`` (comma-separated signing-cert SHA-256
        fingerprints: the Play app-signing key and the upload key) is set.
        """
        prints = [p.strip().upper() for p in os.environ.get("HOJA_ANDROID_CERT_SHA256", "").split(",") if p.strip()]
        if not prints:
            return JSONResponse([], status_code=404)
        return JSONResponse([{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": "work.hoja.mobile",
                       "sha256_cert_fingerprints": prints},
        }], headers={"cache-control": "public, max-age=3600"})

    def _spend_transfer(token: str, expires_at: int) -> None:
        """Redeem a transfer at most once; a second use is refused as a replay (HOJA-733)."""
        from sqlalchemy.exc import IntegrityError

        now = time.time()
        try:
            with tenant_context.system(), engine.begin() as conn:
                conn.execute(identity_transfer_use.delete().where(
                    identity_transfer_use.c.expires_at < now))
                conn.execute(identity_transfer_use.insert().values(
                    token_hash=identity_transfer.token_id(token), expires_at=float(expires_at)))
        except IntegrityError as exc:
            raise identity_transfer.TransferError(
                "replayed", "This sign-in link was already used.") from exc

    async def google_sign_in_finish(request: Request):
        """Redeem a handoff token on this tenant origin and sign the person in."""
        host = _signup_host(request).split(":")[0].lower()
        pinned_org = tenant_hosts.required_org(request.scope)
        transfer_key = signup_config.identity_transfer_key
        if pinned_org and signup_config.identity_transfer_master:
            transfer_key = identity_transfer.tenant_key(signup_config.identity_transfer_master, host)
        if not transfer_key:
            return _signin_failure(
                request, error="Sign-in is not configured on this workspace.",
                reason="transfer_unconfigured", status_code=503, host=host,
            )
        try:
            claims = identity_transfer.redeem(
                key=transfer_key,
                token=request.query_params.get("t"),
                audience=host,
                client_verifier=request.query_params.get("v"),
            )
            # HOJA-733: every transfer is spent once, browser or app. Checked
            # after the signature so junk cannot fill the table.
            _spend_transfer(request.query_params.get("t") or "", int(claims["exp"]))
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
            return _signin_failure(
                request, error="Sign-in could not be completed.",
                reason="resolve_failed", status_code=500, host=host,
            )
        _security_log.info(
            "auth.finish host=%s action=%s", host, resolved["action"]
        )
        pending_invite = _invite_code(request.cookies.get(_INVITE_COOKIE))
        if resolved["action"] == google_login.ACTION_DENIED:
            mismatch = _invite_mismatch(
                request, pinned_org, pending_invite, resolved.get("reason"), claims, host)
            if mismatch is not None:
                return mismatch
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
        # A transfer bound to an app's challenge was finished by that app: it
        # gets a labelled 90-day sliding app session (HOJA-733).
        from_app = "cc" in claims
        session_token, _details = store.create_session_for_membership(
            resolved["identity_id"], resolved["user_id"], resolved["organization_id"],
            **(_app_session_args(request, "via Google") if from_app else {}),
        )
        if not session_token:
            return _signin_failure(
                request, error="Sign-in could not be completed.",
                reason="session_failed", status_code=500, host=host,
            )
        # An invitation in flight outranks the generic landing: the person did
        # not come here to browse, they came to accept, and the SPA only reads
        # the code from the URL. The cookie is single-use.
        landing = (
            f"/?invite={quote(pending_invite, safe='')}"
            if pending_invite
            else _safe_return_to(request.query_params.get("to"))
        )
        response = RedirectResponse(landing, status_code=302)
        response.set_cookie(
            "hoja_session",
            session_token,
            max_age=_APP_COOKIE_MAX_AGE if from_app else 14 * 24 * 60 * 60,
            **_session_cookie_attrs(request),
        )
        if pending_invite:
            response.delete_cookie(_INVITE_COOKIE, path="/")
        # Only now is the membership proven: a refused sign-in announces nothing.
        response.background = _announce_member(request, claims.get("email") or "")
        return response

    def _invite_mismatch(
        request: Request,
        pinned_org: str | None,
        code: str | None,
        reason: str | None,
        claims: dict,
        host: str,
    ) -> Response | None:
        """A refusal that holds a valid invitation for this workspace, for another address.

        Without this the person saw "not invited" while holding the invitation.
        The address the invitation went to is shown masked: holding the code
        already tells them the workspace, and a masked address tells them which
        Google account to pick without handing a full address to whoever has
        the link. Whether a different address may redeem it at all is decided by
        ``invite_policy.invite_redemption_allowed`` alone.
        """
        if not pinned_org or not code or reason not in ("not_invited", "profile_not_found"):
            return None
        with tenant_context.system():
            invite = store.pending_invitation(code)
        if invite is None or invite["organization_id"] != pinned_org:
            return None
        verified = claims.get("email") or ""
        policy = _org_security_policy(pinned_org)
        if invite_policy.invite_redemption_allowed(invite["email"], verified, policy):
            # Same mailbox: membership resolution would have admitted it, so a
            # refusal here is about something else and keeps its own reason.
            return None
        back = _tenant_origin_for(request) or f"https://{host}/"
        retry_target = f"{back.rstrip('/')}/?invite={quote(code, safe='')}"
        invited_url = _handoff_start_url(retry_target, ask_account=True)
        return _signin_failure(
            request,
            error="This invitation was sent to a different address.",
            reason=invite_policy.MISMATCH,
            status_code=403,
            host=host,
            email=verified,
            invited_hint=invite_policy.mask(invite["email"]),
            invited_url=invited_url,
        )

    def _signup_workspace_payload(row: dict) -> dict:
        """One requester's own workspace/request state, safe for the page."""
        slug = str(row.get("slug") or "")
        status = str(row.get("status") or "eligible")
        workspace_url = None
        sign_in_url = None
        if status == "active":
            origin = _origin_for_slug(slug)
            if origin:
                workspace_url = origin.rstrip("/")
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
                # Workspaces this person can already open, so the page offers
                # "Open <workspace>" before it offers to create another one.
                payload["workspaces"] = [
                    {
                        "name": entry["name"],
                        "host": entry["host"],
                        "workspaceUrl": entry["origin"].rstrip("/"),
                        "signInUrl": _handoff_start_url(entry["origin"]),
                        "status": entry["status"],
                        "role": entry.get("role"),
                    }
                    for entry in _directory(identity)
                ]
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
            return _auth_required(request)
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
        raw_ttl = form.get("ttlDays") or form.get("ttl_days") or request.query_params.get("ttlDays") or request.query_params.get("ttl_days")
        ttl_days: int | None = None
        if raw_ttl not in (None, ""):
            try:
                ttl_days = int(str(raw_ttl))
            except ValueError:
                return JSONResponse({"error": "ttlDays must be an integer"}, status_code=400)
            if ttl_days < 1 or ttl_days > 365:
                return JSONResponse({"error": "ttlDays must be between 1 and 365"}, status_code=400)
        raw_compress = (
            form.get("compressWebp")
            or request.query_params.get("compressWebp")
            or request.query_params.get("compress")
            or ""
        )
        compress_webp = str(raw_compress).strip().lower() in ("1", "true", "yes", "webp")
        info = uploads.save_blob_with_meta(
            org, original, resolved, body, ttl_days=ttl_days, compress_webp=compress_webp
        )
        payload = {"url": info["url"], "title": info["filename"], "contentType": info["content_type"]}
        if info["expires_at"] is not None:
            payload["expiresAt"] = info["expires_at"]
        return JSONResponse(payload)

    async def upload_serve(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return _auth_required(request)
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
        if uploads.is_expired(meta):
            return JSONResponse({"error": "Upload expired"}, status_code=410)
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

    async def view_export_csv(request: Request):
        """A task view's rows as CSV, run for the caller exactly as View.issues runs."""
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        from hoja_server import views_api
        from hoja_server.views import ViewError

        try:
            result = views_api.export_csv(store, org, uid, request.path_params.get("view_id", ""))
        except ViewError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if result is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        # A slug keeps the view name's letters (``привет-1a2b3c4d``): the plain filename
        # is an ASCII fallback and ``filename*`` carries the real one (RFC 6266), since a
        # header value must be Latin-1.
        from urllib.parse import quote

        filename = result["filename"]
        ascii_name = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_"
                             for c in filename)
        return Response(result["csv"], media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{ascii_name}"; '
                                   f"filename*=UTF-8''{quote(filename, safe='')}",
            "Cache-Control": "no-store",
            "X-Total-Count": str(result["total"]),
            "X-Export-Limit": str(result["limit"]),
        })

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
            _enter_request_context(request, org, uid)
        if not uid:
            return _auth_required(request)
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
            _enter_request_context(request, org, uid)
        if not uid:
            return _auth_required(request)
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

    async def perf_rum_ingest(request: Request):
        """Accept bounded, schema-validated browser RUM beacons into hoja_server.ops.

        Writes to journald via ``hoja_server.ops`` rather than Postgres so client
        telemetry adds zero database write load. Suppresses the edge ``rest`` line
        so each accepted event writes one clean ``transport=rum`` line.
        """
        observability.suppress(request.scope)
        raw = await request.body()
        if len(raw) > 8192:
            return JSONResponse({"error": "payload too large"}, status_code=413)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list) or len(events) > 20:
            return JSONResponse({"error": "expected events list (max 20)"}, status_code=400)
        org = None
        actor_kind = None
        actor_id = None
        try:
            auth_ctx = _auth(request)
            if auth_ctx is not None:
                org = auth_ctx.org_id
                actor_kind = getattr(auth_ctx, "actor_kind", None)
                actor_id = getattr(auth_ctx, "user_id", None)
        except Exception:
            pass
        accepted = 0
        for ev in events:
            if isinstance(ev, dict) and observability.log_rum_event(
                ev,
                host=request.headers.get("host"),
                org=org,
                actor_kind=actor_kind,
                actor_id=actor_id,
            ):
                accepted += 1
        return JSONResponse({"accepted": accepted}, status_code=202)

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
            {
                "version": __version__,
                "sha": sha or "unknown",
                "builtAt": built_at or None,
            },
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

    def _public_origin(request: Request) -> str:
        raw_host = _signup_host(request).strip()
        if raw_host and _valid_public_host(raw_host):
            scheme = (
                request.headers.get("x-forwarded-proto")
                if _request_from_trusted_proxy(request)
                else None
            ) or request.url.scheme or "https"
            if scheme not in ("http", "https"):
                scheme = "https"
            return f"{scheme}://{raw_host}"
        return signup_config.control_plane_url or "https://app.hoja.work"

    def _render_web_index(request: Request) -> Response:
        import html as _html

        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return HTMLResponse(_UNBUILT_HTML, status_code=503)
        raw_html = index.read_text(encoding="utf-8")
        if not _SEO_MARKER_RE.search(raw_html):
            return FileResponse(index)

        origin = _public_origin(request)
        path = request.url.path or "/"
        is_signup = path == "/signup" or path.startswith("/signup/")
        title = (
            "Request a workspace — Hoja Cloud"
            if is_signup
            else "Hoja Cloud — One shared workspace for people and agents"
        )
        description = (
            "Get a managed Hoja Cloud workspace on hoja.work for a flat $15/organization/month. "
            "No per-seat pricing."
            if is_signup
            else "Collaborate on issues, projects, weekly cycles, reviews, and coding agent workflows in Hoja."
        )
        image_url = f"{origin}/og-signup.png" if is_signup else f"{origin}/og-image.png"
        image_alt = (
            "Hoja Cloud workspace signup preview"
            if is_signup
            else "Hoja Cloud workspace board and agent activity preview"
        )
        page_url = f"{origin}{path}"
        e = lambda value: _html.escape(value, quote=True)
        seo_block = "\n".join([
            f"<title>{_html.escape(title)}</title>",
            f'    <meta name="description" content="{e(description)}" />',
            '    <meta property="og:site_name" content="Hoja" />',
            '    <meta property="og:type" content="website" />',
            f'    <meta property="og:title" content="{e(title)}" />',
            f'    <meta property="og:description" content="{e(description)}" />',
            f'    <meta property="og:url" content="{e(page_url)}" />',
            f'    <meta property="og:image" content="{e(image_url)}" />',
            f'    <meta property="og:image:secure_url" content="{e(image_url)}" />',
            '    <meta property="og:image:type" content="image/png" />',
            '    <meta property="og:image:width" content="1200" />',
            '    <meta property="og:image:height" content="630" />',
            f'    <meta property="og:image:alt" content="{e(image_alt)}" />',
            '    <meta name="twitter:card" content="summary_large_image" />',
            f'    <meta name="twitter:title" content="{e(title)}" />',
            f'    <meta name="twitter:description" content="{e(description)}" />',
            f'    <meta name="twitter:image" content="{e(image_url)}" />',
            f'    <meta name="twitter:image:alt" content="{e(image_alt)}" />',
        ])
        rendered = _SEO_MARKER_RE.sub(lambda _m: seo_block, raw_html, count=1)
        return HTMLResponse(rendered)

    async def web_index(request: Request):
        return _render_web_index(request)

    async def web_asset(request: Request):
        name = request.path_params["name"]
        asset = (_WEB_ROOT / "assets" / name).resolve()
        if _WEB_ROOT not in asset.parents or not asset.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(asset)

    def _make_web_public_handler(filename: str, media_type: str):
        async def _handler(request: Request):
            root = _WEB_ROOT.resolve()
            asset = (root / filename).resolve()
            if root not in asset.parents or not asset.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(asset, media_type=media_type)

        return _handler

    async def web_fallback(request: Request):
        # A retired flat identifier still loads: /<workspace>/issue/HOJA-517
        # resolves to the recursive identifier that replaced it (HOJA-472).
        # Only identifier-shaped issue paths are looked up, so the catch-all
        # does not turn every SPA route into a database query.
        retired = _retired_identifier_target(request)
        if retired is not None:
            return RedirectResponse(retired, status_code=302)
        return _render_web_index(request)

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
            HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy, public_host=public_host,
                              workspace_host=host_map.host_for),
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
        maintenance = asyncio.create_task(_sqlite_maintenance()) if engine.dialect.name == "sqlite" else None
        webhook_worker = None
        if os.environ.get("HOJA_WEBHOOK_WORKER", "1").strip().lower() not in ("0", "false", "no"):
            from hoja_server import webhooks

            webhook_worker = asyncio.create_task(tenant_context.as_system(webhooks.run_forever(engine)))
        # Cycle start/end notifications for every team, on time (cycle_events.py).
        cycle_sweeper = None
        if os.environ.get("HOJA_CYCLE_EVENTS", "1").strip().lower() not in ("0", "false", "no"):
            from hoja_server import cycle_events

            cycle_sweeper = asyncio.create_task(tenant_context.as_system(cycle_events.run_forever(engine)))
        # Owners of active initiatives are reminded when an update is overdue (initiative_api.py).
        reminder_sweeper = None
        if os.environ.get("HOJA_INITIATIVE_REMINDERS", "1").strip().lower() not in ("0", "false", "no"):
            from hoja_server import initiative_api

            reminder_sweeper = asyncio.create_task(
                tenant_context.as_system(initiative_api.run_reminders_forever(engine)))
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
        if maintenance is not None:
            maintenance.cancel()
        if webhook_worker is not None:
            webhook_worker.cancel()
        if cycle_sweeper is not None:
            cycle_sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cycle_sweeper
        if reminder_sweeper is not None:
            reminder_sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reminder_sweeper
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
            WebSocketRoute("/ws", realtime_socket),
            Route("/signin", sign_in_entry, methods=["GET"]),
            Route("/signin/signout", sign_in_signout, methods=["GET"]),
            # Signing out of Hoja, for the SPA's top-level navigation and for the
            # server-rendered "use a different account" forms. POST is preferred
            # (a form, never script); GET has no effect beyond ending sessions.
            Route("/signout", sign_out_entry, methods=["GET", "POST"]),
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
            Route("/auth/google/native", google_native_sign_in, methods=["POST"]),
            Route("/.well-known/assetlinks.json", android_asset_links, methods=["GET"]),
            Route("/auth/google/logout", signup_logout, methods=["POST"]),
            Route("/api/signup/config", signup_public_config, methods=["GET"]),
            Route("/api/signup/status", signup_status, methods=["GET"]),
            Route("/api/signup", signup_endpoint, methods=["GET", "POST"]),
            Route("/api/uploads", upload_create, methods=["POST"]),
            Route("/api/uploads/{file_id}", upload_serve, methods=["GET"]),
            Route("/api/perf/rum", perf_rum_ingest, methods=["POST"]),
            Route("/api/views/{view_id}/export.csv", view_export_csv, methods=["GET"]),
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
            Route("/device", device_page, methods=["GET", "POST"]),
            Route("/mcp", _BareMount(mcp_endpoint, "/mcp")),
            Mount("/mcp", app=mcp_endpoint),
            Route("/", web_index, methods=["GET"]),
            Route("/assets/{name:path}", web_asset, methods=["GET"]),
            *[
                Route(f"/{filename}", _make_web_public_handler(filename, media_type), methods=["GET"])
                for filename, media_type in _WEB_PUBLIC_FILES.items()
            ],
            Route("/{path:path}", web_fallback, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    app.state.realtime = hub
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
    # Outermost, installed after every other middleware: the request id must exist
    # before any of them runs, and the response header must be stamped on the
    # responses they generate. It wraps ``send`` rather than the body, so streamed
    # responses (MCP SSE, long GraphQL reads) are not buffered.
    app.add_middleware(observability.RequestIdMiddleware)
    app.add_exception_handler(json.JSONDecodeError, _malformed_json_handler)
    return app
