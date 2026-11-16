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

# Secret VALUES an agent policy injected into a lease (WP12), watched by value so
# that any log line mentioning one is scrubbed. The registry is process-local and
# bounded: it exists to catch a leak through an exception message or a
# hand-written debug line, not to be a secret store (that is ``agent_secret``,
# sealed). Redaction is by value, so nothing here needs to know which secret it
# was — which is also why an empty registry leaks nothing it was not given.
_WATCHED: list[str] = []
_WATCHED_SET: set[str] = set()
MAX_WATCHED_SECRETS = 256


def watch_secret(value: str | None) -> None:
    """Register one secret value so log lines containing it are scrubbed."""
    if not value or value in _WATCHED_SET:
        return
    if len(_WATCHED) >= MAX_WATCHED_SECRETS:
        # Bounded on purpose: a registry that grows without limit would be a
        # memory leak in every long-lived server process.
        return
    _WATCHED.append(value)
    _WATCHED_SET.add(value)


def watch_secrets(values) -> None:
    for value in values or ():
        watch_secret(value)


def forget_secrets() -> None:
    """Drop every watched value. Used by tests and by a fresh worker start."""
    _WATCHED.clear()
    _WATCHED_SET.clear()


def _scrub_watched(text: str) -> str:
    if not _WATCHED or not text:
        return text
    # Longest first: a short secret that is a substring of a longer one must not
    # win and leave the tail of the longer value in the line.
    for value in sorted(_WATCHED, key=len, reverse=True):
        if value in text:
            text = text.replace(value, REDACTED)
    return text


def scrub_secrets(text: str) -> str:
    """Scrub watched secret values only.

    Deliberately narrower than :func:`redact`, which also masks anything
    credential-shaped and every email. A stored audit detail that legitimately
    names an email must keep it; a stored detail that quotes an injected secret
    must not, and that is exactly the difference between the two functions.
    """
    return _scrub_watched(text)


def redact(text: str) -> str:
    """Remove credential-shaped values from a message.

    Belt and braces: callers are expected not to log secrets, and this catches the
    case where one arrives through a redirect target or an exception. Watched
    secret values (an agent lease's injected secrets, WP12) are scrubbed too,
    because their shape carries no marker the regex could recognise.
    """
    text = _SECRET_PARAM.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return _scrub_watched(_EMAIL.sub(REDACTED, text))


class SecretFilter(logging.Filter):
    """Scrub watched secret values out of the record itself, before any handler.

    A formatter-only redaction leaves ``caplog``, a JSON handler or a second
    handler on another logger holding the raw value. Rewriting ``msg`` and
    ``args`` on the record is what makes the promise hold for every sink at once.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _WATCHED:
            return True
        try:
            message = record.getMessage()
        except Exception:  # a broken format string is not this filter's problem
            return True
        scrubbed = _scrub_watched(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        return True


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
    if not any(isinstance(f, SecretFilter) for f in logger.filters):
        # Before the handler, so every sink that later receives this record
        # (a second handler, caplog, a JSON exporter) sees the scrubbed message.
        logger.addFilter(SecretFilter())
    if not any(getattr(h, _HANDLER_MARKER, False) for h in logger.handlers):
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)
    return logger
