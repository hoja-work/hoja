"""Request ids and one operations log line per operation.

Production had two blind spots while an incident was being reconstructed: no
correlation id anywhere, and no line that said what a client actually did. The
only application logs were security denials (``hoja_server.security``) and the
MCP SDK's session lifecycle chatter, so "what did this client send before it
started failing" had no answer. ``audit_event.request_id`` had existed since the
audit ledger landed and was empty in every row, because nothing ever bound one.

This module is the missing seam, and it is deliberately the only one:

* **The id is minted at the edge and travels in a ``ContextVar``.** A
  ``ContextVar`` follows the request through ``await`` and into
  ``run_in_threadpool`` (Starlette copies the context), so a GraphQL resolver, an
  MCP tool and an audit insert all see the same id without threading it through
  every signature. It is set and reset by the middleware, per request, so one
  request can never inherit another's id.
* **One line per operation, written at the edge.** The middleware wraps the
  whole request, so it knows the duration and the final status; the endpoint
  fills in what only it knows (transport, GraphQL operation, actor, workspace)
  through :func:`annotate`, which mutates ``scope`` — the one object both layers
  share. The MCP tool dispatcher is the exception: the SDK runs a tool call in
  its own task, so a write there does not come back. That path writes its own
  line and suppresses the edge line (:func:`suppress`), which keeps exactly one
  line per ``tools/call``.
* **Nothing that identifies a payload is logged.** No variables, no arguments, no
  values, no headers, no bodies. A GraphQL line names the operation type, its
  name and its top-level field names; an MCP line names the JSON-RPC method and
  the tool. Operation names and tool names are schema identifiers chosen by the
  server, not data chosen by the caller.

The logger is ``hoja_server.ops``, a child of ``hoja_server``, so the redacting
handler installed by :mod:`hoja_server.logging_setup` also covers these lines.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar, Token
from typing import Any

from starlette.datastructures import Headers, MutableHeaders

#: The header a client may send to have its own correlation id echoed back.
REQUEST_ID_HEADER = "X-Request-Id"
#: Bounded so a hostile header cannot become a log-flooding field.
MAX_REQUEST_ID_LENGTH = 128

#: What a caller-supplied id may contain: URL-safe, log-safe, no controls, no
#: whitespace, no quotes. Anything else is replaced rather than sanitised, since
#: a mangled id would silently stop matching the client's own logs.
_REQUEST_ID = re.compile(rf"[A-Za-z0-9._:-]{{1,{MAX_REQUEST_ID_LENGTH}}}")

#: Every operations line is written here and nowhere else.
LOG = logging.getLogger("hoja_server.ops")

#: The id of the operation being served. Read-only for everyone below the edge.
_CURRENT: ContextVar[str | None] = ContextVar("hoja_request_id", default=None)
#: The request's Host header, for the paths that log without a ``scope`` — the
#: MCP tool dispatcher runs in the SDK's own task and only has context.
_HOST: ContextVar[str | None] = ContextVar("hoja_request_host", default=None)

#: ``scope`` key holding what the endpoint learned about this operation.
_ANNOTATION = "hoja_ops"
#: ``scope`` key set by an endpoint that writes its own operations line.
_SUPPRESSED = "hoja_ops_suppressed"

#: Bounded top-level field list: enough to recognise the operation, not a
#: transcript of the document.
MAX_OPERATION_FIELDS = 8

# A value printed bare when it is unambiguously one token; JSON-quoted otherwise,
# so a field that contains a space can never split into two fields.
_BARE = re.compile(r"[A-Za-z0-9._:/@+=+-]+")

#: Paths that are not operations: liveness probes and the built frontend.
_QUIET_PATHS = frozenset({"/health", "/ready", "/favicon.ico"})
_QUIET_PREFIXES = ("/assets/", "/static/", "/.well-known/")
_ASSET_SUFFIXES = (
    ".js", ".mjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".webp", ".woff", ".woff2", ".ttf", ".txt", ".webmanifest",
)


def new_request_id() -> str:
    """A fresh correlation id: 32 hex characters, no punctuation to escape."""
    return uuid.uuid4().hex


def valid_request_id(value: Any) -> str | None:
    """The caller's id when it is safe to echo and log, else ``None``.

    ``None`` means "mint a new one"; the caller's value is never trimmed,
    re-encoded or partially kept, because an id that half-survives is worse than
    an id the client can see was not adopted.
    """
    if not isinstance(value, str):
        return None
    return value if _REQUEST_ID.fullmatch(value) else None


def current_request_id() -> str | None:
    """The id bound to this operation, or ``None`` outside a request."""
    return _CURRENT.get()


def current_host() -> str | None:
    """The Host of the request being served, or ``None`` outside a request."""
    return _HOST.get()


def bind(request_id: str) -> Token:
    """Bind ``request_id`` to the current context (the middleware's job)."""
    return _CURRENT.set(request_id)


def reset(token: Token) -> None:
    """Undo :func:`bind`. Must run even when the request raised."""
    _CURRENT.reset(token)


def annotate(
    scope: dict,
    *,
    transport: str | None = None,
    operation: str | None = None,
    org: str | None = None,
    actor_kind: str | None = None,
    actor_id: str | None = None,
    outcome: str | None = None,
) -> None:
    """Record what this endpoint knows about the operation on the ASGI ``scope``.

    Called from the endpoint because only it knows the authenticated actor and
    the parsed operation; the line itself is written once, at the edge.
    """
    if scope.get("type") != "http":
        return
    info = scope.setdefault(_ANNOTATION, {})
    for key, value in (
        ("transport", transport),
        ("operation", operation),
        ("org", org),
        ("actor_kind", actor_kind),
        ("actor_id", actor_id),
        ("outcome", outcome),
    ):
        if value:
            info[key] = value


def suppress(scope: dict) -> None:
    """Mark the request as logged elsewhere (the MCP tool dispatcher)."""
    scope[_SUPPRESSED] = True


def describe_graphql(query: str) -> str:
    """``mutation CreateIssue [issueCreate]`` — names only, never values.

    The document is parsed for its shape; arguments are never read, so a variable
    or a literal in the query cannot reach the log line.
    """
    from graphql import FieldNode, OperationDefinitionNode, parse

    try:
        document = parse(query)
    except Exception:  # unparseable is graphql()'s error to report, not ours
        return "unparsed"
    for definition in document.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue
        fields = [
            (selection.alias.value if selection.alias else selection.name.value)
            for selection in definition.selection_set.selections
            if isinstance(selection, FieldNode)
        ]
        shown = ", ".join(fields[:MAX_OPERATION_FIELDS]) or "-"
        if len(fields) > MAX_OPERATION_FIELDS:
            shown += f", +{len(fields) - MAX_OPERATION_FIELDS}"
        name = definition.name.value if definition.name else "-"
        return f"{definition.operation.value} {name} [{shown}]"
    return "no-operation"


def _value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    text = str(value)
    return text if _BARE.fullmatch(text) else json.dumps(text, ensure_ascii=False)


def log_operation(
    *,
    transport: str,
    operation: str,
    outcome: str = "ok",
    status: int | None = None,
    duration_ms: float | None = None,
    started: float | None = None,
    request_id: str | None = None,
    host: str | None = None,
    org: str | None = None,
    actor_kind: str | None = None,
    actor_id: str | None = None,
) -> None:
    """Write one ``key=value`` operations line.

    ``actor`` is ``kind:id`` in a single field so a person reading a log cannot
    mistake one for the other, and a missing half stays visible as ``-``.
    """
    if duration_ms is None:
        duration_ms = (time.perf_counter() - started) * 1000 if started is not None else 0.0
    actor = f"{actor_kind}:{actor_id}" if actor_kind and actor_id else (actor_kind or actor_id)
    fields = (
        ("request_id", request_id or current_request_id()),
        ("host", host or current_host()),
        ("org", org),
        ("actor", actor),
        ("transport", transport),
        ("operation", operation),
        ("status", status),
        ("outcome", outcome),
        ("duration_ms", f"{duration_ms:.1f}"),
    )
    LOG.info("op %s", " ".join(f"{key}={_value(value)}" for key, value in fields))


def _is_asset(path: str) -> bool:
    lowered = path.lower()
    return (lowered.endswith(_ASSET_SUFFIXES) or lowered in _QUIET_PATHS
            or lowered.startswith(_QUIET_PREFIXES))


def _is_quiet(scope: dict) -> bool:
    """True for requests that are not operations: static files, probes, preflight."""
    return scope.get("method") == "OPTIONS" or _is_asset(scope.get("path", ""))


class RequestIdMiddleware:
    """Adopt or mint ``X-Request-Id``, echo it, and write one operations line.

    Pure ASGI, not ``BaseHTTPMiddleware``: the MCP endpoint streams and GraphQL
    responses can be long, and the base middleware's queue/task indirection both
    delays a streamed body and hands the inner app a *copy* of the context, so a
    ``ContextVar`` set at the edge would not be visible inside — which is the
    whole mechanism here.

    It is installed outermost, so its ``send`` wrapper also stamps the id on
    error responses produced by Starlette's own exception handling.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host")
        request_id = valid_request_id(headers.get(REQUEST_ID_HEADER)) or new_request_id()
        token = bind(request_id)
        host_token = _HOST.set(host)
        started = time.perf_counter()
        responded: dict[str, int | None] = {"status": None}

        async def send_with_request_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
                responded["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            try:
                info = scope.get(_ANNOTATION) or {}
                path = scope.get("path", "")
                if not scope.get(_SUPPRESSED) and not _is_quiet(scope):
                    status = responded["status"]
                    outcome = info.get("outcome") or (
                        "ok" if status is not None and status < 400 else "error")
                    log_operation(
                        request_id=request_id,
                        host=host,
                        org=info.get("org"),
                        actor_kind=info.get("actor_kind"),
                        actor_id=info.get("actor_id"),
                        transport=info.get("transport") or "rest",
                        # No query string: ``?code=`` and ``?t=`` are one-shot
                        # credentials on the OAuth and handoff routes.
                        operation=info.get("operation")
                        or f"{scope.get('method')} {path}".strip(),
                        status=status,
                        outcome=outcome,
                        started=started,
                    )
            finally:
                _HOST.reset(host_token)
                reset(token)
