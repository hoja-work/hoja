"""Cross-origin access for browser clients.

Hoja's own web UI is served by hoja-serve (same origin), so it needs no CORS.
The Expo app's web build runs on Metro on a different origin, and its fetch
calls to ``/sync/bootstrap`` and ``/graphql`` are cross-origin. CORS stays off
unless the operator opts in, one exact origin at a time:

    HOJA_CORS_ORIGINS="http://localhost:8082,https://preview.example.com"

Credentials are sent as ``Authorization: Bearer`` rather than cookies, so the
middleware never allows credentialed (cookie-bearing) cross-origin requests.
That keeps a hostile origin from riding a signed-in browser session.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware

CORS_ORIGINS_ENV = "HOJA_CORS_ORIGINS"

#: Headers the Expo client sends; If-None-Match carries the sync ETag.
#: X-Request-Id is both sent (opt-in correlation) and exposed, so a cross-origin
#: client can report the id of a request it wants investigated.
ALLOWED_HEADERS = ["Authorization", "Content-Type", "Accept", "If-None-Match", "X-Request-Id"]
#: ETag is read by the sync client to make conditional bootstrap requests.
#: Server-Timing exposes app/db/pool breakdown to PerformanceResourceTiming.
EXPOSED_HEADERS = ["ETag", "X-Request-Id", "Server-Timing"]
ALLOWED_METHODS = ["GET", "POST", "OPTIONS"]


def parse_cors_origins(raw: str | None = None) -> list[str]:
    """Return the configured origins, or [] when CORS is off (the default).

    A malformed entry raises instead of being skipped: a typo must not silently
    leave the browser surface broken, and ``*`` is refused because it would let
    any site read API responses.
    """
    value = os.environ.get(CORS_ORIGINS_ENV, "") if raw is None else raw
    origins: list[str] = []
    for item in value.replace("\n", ",").split(","):
        entry = item.strip().rstrip("/")
        if not entry:
            continue
        if entry == "*":
            raise ValueError(
                f"{CORS_ORIGINS_ENV} must name exact origins; a wildcard is not allowed."
            )
        parts = urlsplit(entry)
        if (
            parts.scheme not in ("http", "https")
            or not parts.netloc
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                f"{CORS_ORIGINS_ENV} entry {entry!r} must be an origin such as "
                f"http://localhost:8082 or https://preview.example.com."
            )
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in origins:
            origins.append(origin)
    return origins


def install_cors(app, origins: list[str] | None = None) -> bool:
    """Add CORS middleware when origins are configured; return whether it did."""
    configured = parse_cors_origins() if origins is None else origins
    if not configured:
        return False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=EXPOSED_HEADERS,
        allow_credentials=False,
        max_age=600,
    )
    return True
