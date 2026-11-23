"""OAuth 2.0 sign-in for the first-party ``hoja-cli`` public client.

Implements the client half of ``hoja auth login``:

* workspace URL normalisation to an origin (``https://acme.example``),
* authorization-server metadata discovery from
  ``<origin>/.well-known/oauth-authorization-server/mcp`` with an issuer check,
* the authorization code flow with PKCE (S256) through a one-shot loopback
  redirect on ``http://127.0.0.1:<ephemeral>/callback`` (RFC 8252), validating
  ``state`` and the RFC 9207 ``iss`` parameter,
* the device authorization grant (RFC 8628) for headless sessions,
* refresh and revocation (RFC 7009).

Every request goes to the origin the person named; nothing here has a default
host. The HTTP client is injectable so tests can run a fake authorization
server in process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import os
import secrets
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from hoja import __version__
from hoja.errors import AuthError, NetworkError, UsageError

CLIENT_ID = "hoja-cli"
READ_SCOPE = "hoja:read"
WRITE_SCOPE = "hoja:write"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
METADATA_PATH = "/.well-known/oauth-authorization-server/mcp"
LOOPBACK_TIMEOUT = 300.0
# The device_name parameter is capped at 64 characters by the server.
DEVICE_NAME_MAX = 64

_ACCEPTED_PATHS = ("", "/", "/graphql", "/mcp")


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------


def _is_loopback_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_origin(value: str) -> str:
    """Turn what a person typed into a workspace origin.

    Accepts ``acme.example``, ``https://acme.example``, the GraphQL endpoint
    ``https://acme.example/graphql`` or the MCP endpoint, with or without a
    trailing slash. Plain ``http`` is only accepted for loopback hosts, and a
    bare loopback host (``localhost:8787``) defaults to ``http``.
    """
    raw = (value or "").strip()
    if not raw:
        raise UsageError("A workspace URL is required", hint="For example: hoja auth login acme.example.com")
    if "://" not in raw:
        bare_host = raw.split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
        raw = ("http://" if _is_loopback_host(bare_host) else "https://") + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise UsageError(f"Not a valid workspace URL: {value!r}") from exc
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise UsageError(f"Not a valid workspace URL: {value!r}", hint="Use https://<your workspace host>")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UsageError(
            f"Not a valid workspace URL: {value!r}",
            hint="Give only the workspace address, without credentials, query or fragment.",
        )
    path = parts.path.rstrip("/")
    if path not in _ACCEPTED_PATHS:
        raise UsageError(
            f"Not a workspace URL: {value!r}",
            hint="Use the workspace address, e.g. https://acme.example.com",
        )
    if scheme == "http" and not _is_loopback_host(host):
        raise UsageError(
            f"Refusing to sign in over plain http to {host}",
            hint="Use https://. Plain http is only allowed for a local server on 127.0.0.1 or localhost.",
        )
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}"


def origin_of(url: str) -> str | None:
    """Return the normalised origin of *url*, or None if it is not one."""
    try:
        return normalize_origin(url)
    except UsageError:
        try:
            parts = urlsplit(url.strip())
            if parts.scheme and parts.hostname:
                return normalize_origin(f"{parts.scheme}://{parts.netloc}")
        except (UsageError, ValueError):
            return None
    return None


def graphql_url(origin: str) -> str:
    return origin + "/graphql"


def mcp_url(origin: str) -> str:
    return origin + "/mcp"


def device_label() -> str:
    try:
        host = socket.gethostname() or "unknown host"
    except OSError:  # pragma: no cover - platform specific
        host = "unknown host"
    return f"hoja-cli on {host}"[:DEVICE_NAME_MAX]


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def default_http(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": f"hoja/{__version__}", "Accept": "application/json"},
    )


class OAuthError(AuthError):
    """An OAuth error response (RFC 6749 §5.2 / RFC 8628 §3.5)."""

    def __init__(self, error: str, description: str | None = None, *, hint: str | None = None) -> None:
        self.error = error
        self.description = description
        text = f"Sign-in failed: {error}" + (f" ({description})" if description else "")
        super().__init__(text, hint=hint)


def _post_form(http: httpx.Client, url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        resp = http.post(url, data=data)
    except httpx.TimeoutException as exc:
        raise NetworkError(f"Request to {url} timed out") from exc
    except httpx.RequestError as exc:
        raise NetworkError(f"Could not reach {url}: {exc}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code >= 400 or not isinstance(body, dict):
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            raise OAuthError(body["error"], body.get("error_description"))
        if resp.status_code >= 500:
            raise NetworkError(f"{url} returned HTTP {resp.status_code}")
        raise OAuthError("invalid_response", f"HTTP {resp.status_code} from {url}")
    return body


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerMetadata:
    origin: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    device_authorization_endpoint: str | None
    iss_parameter_supported: bool


def _same_origin_endpoint(origin: str, url: Any, name: str) -> str:
    if not isinstance(url, str) or origin_of(url) != origin:
        raise AuthError(
            f"The authorization server's {name} is not on {origin}",
            hint="The workspace's sign-in metadata is misconfigured; contact its administrator.",
        )
    return url


def discover(origin: str, http: httpx.Client) -> ServerMetadata:
    """Fetch and validate the authorization-server metadata for *origin*."""
    url = origin + METADATA_PATH
    try:
        resp = http.get(url)
    except httpx.RequestError as exc:
        raise NetworkError(f"Could not reach {origin}: {exc}") from exc
    if resp.status_code != 200:
        raise AuthError(
            f"{origin} does not offer Hoja sign-in (HTTP {resp.status_code} from {METADATA_PATH})",
            hint="Check the workspace address, or use `hoja auth add NAME --token KEY` with an API key.",
        )
    try:
        doc = resp.json()
    except ValueError as exc:
        raise AuthError(f"{origin} returned invalid sign-in metadata") from exc
    if not isinstance(doc, dict):
        raise AuthError(f"{origin} returned invalid sign-in metadata")
    expected_issuer = mcp_url(origin)
    if doc.get("issuer") != expected_issuer:
        raise AuthError(
            f"Sign-in metadata issuer {doc.get('issuer')!r} does not match {expected_issuer}",
            hint="The workspace address may be wrong, or a proxy is rewriting the response.",
        )
    device = doc.get("device_authorization_endpoint")
    revoke = doc.get("revocation_endpoint")
    return ServerMetadata(
        origin=origin,
        issuer=expected_issuer,
        authorization_endpoint=_same_origin_endpoint(origin, doc.get("authorization_endpoint"), "authorization endpoint"),
        token_endpoint=_same_origin_endpoint(origin, doc.get("token_endpoint"), "token endpoint"),
        revocation_endpoint=_same_origin_endpoint(origin, revoke, "revocation endpoint") if revoke else None,
        device_authorization_endpoint=(
            _same_origin_endpoint(origin, device, "device authorization endpoint") if device else None
        ),
        iss_parameter_supported=doc.get("authorization_response_iss_parameter_supported") is True,
    )


# ---------------------------------------------------------------------------
# Token responses
# ---------------------------------------------------------------------------


@dataclass
class TokenSet:
    access_token: str
    expires_at: float | None
    refresh_token: str | None
    scope: str
    workspace: dict[str, Any] = field(default_factory=dict)
    user: dict[str, Any] = field(default_factory=dict)


def parse_token_response(
    body: dict[str, Any], *, origin: str, now: float, previous_refresh: str | None = None
) -> TokenSet:
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise OAuthError("invalid_response", "token response has no access_token")
    token_type = body.get("token_type")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise OAuthError("invalid_response", f"unsupported token_type {token_type!r}")
    expires_in = body.get("expires_in")
    expires_at = now + float(expires_in) if isinstance(expires_in, (int, float)) and expires_in > 0 else None
    refresh = body.get("refresh_token")
    raw_ws, raw_user, raw_scope = body.get("hoja_workspace"), body.get("hoja_user"), body.get("scope")
    workspace: dict[str, Any] = raw_ws if isinstance(raw_ws, dict) else {}
    user: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
    ws_origin = workspace.get("origin")
    if ws_origin is not None and origin_of(str(ws_origin)) != origin:
        raise OAuthError(
            "invalid_response",
            f"token is for workspace origin {ws_origin!r}, not {origin}",
        )
    return TokenSet(
        access_token=access,
        expires_at=expires_at,
        refresh_token=refresh if isinstance(refresh, str) and refresh else previous_refresh,
        scope=raw_scope if isinstance(raw_scope, str) else "",
        workspace={k: workspace.get(k) for k in ("id", "name", "url_key", "origin") if workspace.get(k) is not None},
        user={k: user.get(k) for k in ("id", "email", "name") if user.get(k) is not None},
    )


# ---------------------------------------------------------------------------
# Authorization code + PKCE over a loopback redirect
# ---------------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Hoja CLI</title>
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.25rem">{title}</h1><p>{body}</p></body></html>"""


@dataclass
class _Callback:
    params: dict[str, str] | None = None


class _LoopbackServer(HTTPServer):
    callback: _Callback
    allow_reuse_address = False
    # The state this login expects. A request without it (a prefetch, a stray
    # local process, a stale tab from an earlier attempt) is answered and ignored,
    # so it can neither end the wait nor fail the sign-in.
    expected_state: str = ""


class _Handler(BaseHTTPRequestHandler):
    server: _LoopbackServer

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != "/callback" or self.server.callback.params is not None:
            self._page(404, "Not found", "This address only accepts the Hoja sign-in redirect.")
            return
        query = parse_qs(parts.query, keep_blank_values=True)
        params = {k: v[0] for k, v in query.items() if v}
        expected = self.server.expected_state
        if expected and not hmac.compare_digest(
            params.get("state", "").encode(), expected.encode()
        ):
            self._page(400, "Not this sign-in", "This link belongs to a different sign-in attempt.")
            return
        self.server.callback.params = params
        if "error" in self.server.callback.params:
            self._page(400, "Sign-in was not completed", "Return to the terminal for details.")
        else:
            self._page(200, "Signed in to Hoja", "You can close this tab and return to the terminal.")

    def _page(self, status: int, title: str, body: str) -> None:
        payload = _PAGE.format(title=html.escape(title), body=html.escape(body)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _wait_for_callback(server: _LoopbackServer, timeout: float) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    server.timeout = 0.5
    while server.callback.params is None:
        if time.monotonic() >= deadline:
            raise AuthError(
                "Timed out waiting for the browser sign-in",
                hint="Run `hoja auth login` again, or use `--device` on a machine without a browser.",
            )
        server.handle_request()
    return server.callback.params


def check_callback(params: dict[str, str], *, state: str, meta: ServerMetadata) -> str:
    """Validate the redirect parameters and return the authorization code."""
    got_state = params.get("state", "")
    if not hmac.compare_digest(got_state.encode(), state.encode()):
        raise AuthError("Sign-in rejected: the browser returned a different state", hint="Run `hoja auth login` again.")
    iss = params.get("iss")
    if (iss is not None or meta.iss_parameter_supported) and (
        iss is None or not hmac.compare_digest(iss.encode(), meta.issuer.encode())
    ):
        raise AuthError(
            "Sign-in rejected: the response came from a different issuer",
            hint="Run `hoja auth login` again.",
        )
    if err := params.get("error"):
        if err == "access_denied":
            raise OAuthError(err, params.get("error_description"), hint="The request was denied in the browser.")
        raise OAuthError(err, params.get("error_description"))
    code = params.get("code")
    if not code:
        raise AuthError("Sign-in failed: the browser returned no authorization code")
    return code


def loopback_login(
    meta: ServerMetadata,
    http: httpx.Client,
    *,
    scope: str,
    open_browser: Callable[[str], bool],
    echo: Callable[[str], None],
    timeout: float = LOOPBACK_TIMEOUT,
    now: Callable[[], float] = time.time,
) -> TokenSet | None:
    """Run the browser flow. Returns None when no browser could be opened."""
    server = _LoopbackServer(("127.0.0.1", 0), _Handler)
    server.callback = _Callback()
    try:
        port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        server.expected_state = state
        resource = graphql_url(meta.origin)
        url = meta.authorization_endpoint + (
            "&" if "?" in meta.authorization_endpoint else "?"
        ) + urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": scope,
                "resource": resource,
                "device_name": device_label(),
            }
        )
        echo(f"Opening your browser to sign in to {meta.origin}.")
        echo(f"If it does not open, visit:\n\n  {url}\n")
        if not open_browser(url):
            return None
        params = _wait_for_callback(server, timeout)
    finally:
        server.server_close()
    code = check_callback(params, state=state, meta=meta)
    body = _post_form(
        http,
        meta.token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    return parse_token_response(body, origin=meta.origin, now=now())


# ---------------------------------------------------------------------------
# Device authorization grant (RFC 8628)
# ---------------------------------------------------------------------------


def device_login(
    meta: ServerMetadata,
    http: httpx.Client,
    *,
    scope: str,
    echo: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> TokenSet:
    if not meta.device_authorization_endpoint:
        raise AuthError(
            f"{meta.origin} does not support signing in from a device without a browser",
            hint="Run `hoja auth login` on a machine with a browser, or use an API key with `hoja auth add`.",
        )
    resource = graphql_url(meta.origin)
    start = _post_form(
        http,
        meta.device_authorization_endpoint,
        {"client_id": CLIENT_ID, "scope": scope, "resource": resource, "device_name": device_label()},
    )
    device_code = start.get("device_code")
    user_code = start.get("user_code")
    verification_uri = start.get("verification_uri")
    if not all(isinstance(v, str) and v for v in (device_code, user_code, verification_uri)):
        raise OAuthError("invalid_response", "device authorization response is incomplete")
    complete = start.get("verification_uri_complete")
    for link in (verification_uri, complete):
        if link is not None and origin_of(str(link)) != meta.origin:
            raise OAuthError("invalid_response", f"verification link {link!r} is not on {meta.origin}")
    interval = float(start.get("interval") or 5)
    expires_in = float(start.get("expires_in") or 600)
    deadline = now() + expires_in

    echo(f"To sign in to {meta.origin}, open:\n\n  {complete or verification_uri}\n")
    echo(f"and confirm this code:\n\n    {user_code}\n")
    if complete:
        echo(f"(Or open {verification_uri} and type the code.)")
    echo("Waiting for approval... (Ctrl-C to cancel)")

    while True:
        sleep(interval)
        if now() >= deadline:
            raise AuthError("The sign-in code expired before it was approved", hint="Run `hoja auth login --device` again.")
        try:
            body = _post_form(
                http,
                meta.token_endpoint,
                {"grant_type": DEVICE_GRANT, "device_code": str(device_code), "client_id": CLIENT_ID},
            )
        except OAuthError as exc:
            if exc.error == "authorization_pending":
                continue
            if exc.error == "slow_down":
                interval += 5
                continue
            if exc.error == "access_denied":
                raise AuthError("Sign-in was denied in the browser") from exc
            if exc.error == "expired_token":
                raise AuthError(
                    "The sign-in code expired before it was approved",
                    hint="Run `hoja auth login --device` again.",
                ) from exc
            raise
        return parse_token_response(body, origin=meta.origin, now=now())


# ---------------------------------------------------------------------------
# Refresh and revocation
# ---------------------------------------------------------------------------


def refresh(
    token_endpoint: str, refresh_token: str, *, origin: str, http: httpx.Client, now: Callable[[], float] = time.time
) -> TokenSet:
    body = _post_form(
        http,
        token_endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "resource": graphql_url(origin),
        },
    )
    return parse_token_response(body, origin=origin, now=now(), previous_refresh=refresh_token)


def revoke(revocation_endpoint: str, token: str, hint: str, *, http: httpx.Client) -> None:
    """Revoke *token*; RFC 7009 servers answer 200 even for unknown tokens."""
    try:
        resp = http.post(
            revocation_endpoint, data={"token": token, "token_type_hint": hint, "client_id": CLIENT_ID}
        )
    except httpx.RequestError as exc:
        raise NetworkError(f"Could not reach {revocation_endpoint}: {exc}") from exc
    if resp.status_code >= 400:
        raise AuthError(f"Revocation failed with HTTP {resp.status_code}")


# ---------------------------------------------------------------------------
# Browser detection
# ---------------------------------------------------------------------------


def is_headless(env: dict[str, str] | None = None, platform: str | None = None) -> bool:
    """True when a local browser is unlikely to be reachable."""
    env = dict(os.environ) if env is None else env
    platform = platform or sys.platform
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return True
    return platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url, new=1, autoraise=True))
    except webbrowser.Error:
        return False

