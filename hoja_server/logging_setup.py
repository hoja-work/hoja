"""One place that decides what a log line is.

The auth funnel is already instrumented with ``hoja_server.security`` events, but
nothing ever configured a handler for that logger, so every ``info`` record was
discarded before it was formatted: uvicorn's ``log_level`` configures the
``uvicorn.*`` loggers only, and the root logger stays at ``WARNING``. The events
existed and were invisible, which is indistinguishable from having no telemetry.

Turning them on is deliberately scoped. Raising uvicorn to ``info`` would also
switch on its access log, which prints the request line — including ``?code=`` on
the OAuth callback and ``?t=`` on the identity handoff. Those are single-use
credentials. So this module enables the application logger and leaves uvicorn's
access log alone, and it redacts credential-shaped values that reach a message
anyway.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import IO

_HANDLER_MARKER = "_hoja_security_handler"

#: Query-style credentials that can reach a log line through an exception message
#: or a hand-built string. ``t`` is the identity-handoff ticket, ``code``/``state``
#: are OAuth parameters, the cookie names are session identifiers.
_SECRET_PARAM = re.compile(
    r"(?i)\b(code|state|nonce|t|token|access_token|id_token|refresh_token"
    r"|turnstile_token|turnstile|hoja_session|hoja_signup_session|hoja_oidc_flow"
    r"|ticket)=([^&\s\"']+)"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

REDACTED = "<redacted>"


def redact(text: str) -> str:
    """Remove credential-shaped values from a message.

    Belt and braces: callers are expected not to log secrets, and this catches the
    case where one arrives through a redirect target or an exception.
    """
    text = _SECRET_PARAM.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return _EMAIL.sub(REDACTED, text)


class RedactingFormatter(logging.Formatter):
    """Format, then redact the finished line.

    Redaction runs after ``%``-formatting so that values passed as ``args`` are
    covered too, not just literals in the message.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib name
        return redact(super().format(record))


def configure_logging(
    level: str = "info",
    *,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Attach one redacting handler to the application logger.

    Idempotent: calling it twice (or from a second worker) does not stack handlers.
    """
    logger = logging.getLogger("hoja_server")
    logger.setLevel(logging.getLevelName(level.upper()))
    # Do not hand records to the root logger: it has no handlers, and the last
    # resort handler only emits WARNING and above.
    logger.propagate = False
    if not any(getattr(h, _HANDLER_MARKER, False) for h in logger.handlers):
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)
    return logger
