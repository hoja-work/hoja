"""Small synchronous SMTP adapter for application-originated email.

This module deliberately has no application wiring: callers provide explicit
SMTP settings and a message, making transport configuration deployment-owned.

Two submission shapes are supported because providers disagree about them:

* ``starttls=True`` (default) — plaintext connect, then STARTTLS on 587/25.
* ``implicit_tls=True`` — TLS from the first byte on 465 (SMTPS). Cloudflare
  Email Service accepts submission only this way, and refuses STARTTLS.

Messages may carry an HTML alternative; a text part is always set first so an
HTML-only body can never reach a client that refuses ``text/html``.
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr


class EmailAdapterError(RuntimeError):
    """Raised when email input is invalid or SMTP delivery fails."""


@dataclass(frozen=True)
class SMTPConfig:
    """Connection settings for an SMTP submission server."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None
    starttls: bool = True
    implicit_tls: bool = False
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("SMTP host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        if self.timeout <= 0:
            raise ValueError("SMTP timeout must be greater than zero")
        if (self.username is None) != (self.password is None):
            raise ValueError("SMTP username and password must be supplied together")
        if self.starttls and self.implicit_tls:
            raise ValueError("starttls and implicit_tls are mutually exclusive")


_DISPLAY_NAME_FORBIDDEN = set('<>@,;:"\\')

#: Headers this adapter will never accept from a caller: they are either set
#: here (From/To/Subject/Message-ID) or would let a caller forge routing.
_RESERVED_HEADERS = {
    "from", "to", "cc", "bcc", "subject", "reply-to", "sender",
    "message-id", "date", "return-path", "received", "dkim-signature",
}


def _validated_header(name: str, value: str) -> tuple[str, str]:
    if not isinstance(name, str) or not name.strip() or any(ch in name for ch in ':\r\n '):
        raise EmailAdapterError("header names must be a single token")
    if name.strip().lower() in _RESERVED_HEADERS or name.strip().lower().startswith("content-"):
        raise EmailAdapterError(f"header {name!r} is set by the adapter and cannot be overridden")
    if not isinstance(value, str) or not value.isprintable():
        raise EmailAdapterError(f"header {name!r} must be a single printable line")
    return name.strip(), value


def _validated_address(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.isprintable():
        raise EmailAdapterError(f"{field} must be a valid email address")
    display_name, address = parseaddr(value)
    if not address or address.count("@") != 1 or any(ch.isspace() for ch in address):
        raise EmailAdapterError(f"{field} must be a valid email address")
    if any(ch in _DISPLAY_NAME_FORBIDDEN for ch in display_name):
        raise EmailAdapterError(f"{field} display name contains disallowed characters")
    return formataddr((display_name, address))


def send_email(
    config: SMTPConfig,
    *,
    sender: str,
    recipients: list[str] | tuple[str, ...],
    subject: str,
    text: str,
    html: str | None = None,
    reply_to: str | None = None,
    headers: dict[str, str] | None = None,
) -> str | None:
    """Send one message; raise :class:`EmailAdapterError` on failure.

    SMTP STARTTLS is enabled by default. Provider/network exceptions are wrapped
    without embedding credentials or server response contents in the message.

    Returns the ``Message-ID`` this call assigned, so a caller can correlate a
    submission with the provider's delivery log. Acceptance by the submission
    server is *not* proof of inbox delivery.
    """
    if not isinstance(config, SMTPConfig):
        raise EmailAdapterError("config must be an SMTPConfig")
    if not isinstance(subject, str) or not subject.isprintable():
        raise EmailAdapterError("subject must be a single-line string")
    if not isinstance(text, str):
        raise EmailAdapterError("text body must be a string")
    if html is not None and not isinstance(html, str):
        raise EmailAdapterError("html body must be a string")
    clean_sender = _validated_address(sender, "sender")
    if not isinstance(recipients, (list, tuple)) or not recipients:
        raise EmailAdapterError("at least one recipient is required")
    clean_recipients = [_validated_address(item, "recipient") for item in recipients]
    clean_headers = [_validated_header(name, value) for name, value in (headers or {}).items()]

    message = EmailMessage()
    message["From"] = clean_sender
    message["To"] = ", ".join(clean_recipients)
    message["Subject"] = subject
    if reply_to is not None:
        message["Reply-To"] = _validated_address(reply_to, "reply-to")
    message.set_content(text)
    if html is not None:
        message.add_alternative(html, subtype="html")

    domain = parseaddr(clean_sender)[1].rsplit("@", 1)[-1] or "localhost"
    message_id = f"<{uuid.uuid4().hex}@{domain}>"
    message["Message-ID"] = message_id
    for name, value in clean_headers:
        message[name] = value

    try:
        if config.implicit_tls:
            client_factory = lambda: smtplib.SMTP_SSL(  # noqa: E731 - explicit branch
                config.host, config.port, timeout=config.timeout,
                context=ssl.create_default_context())
        else:
            client_factory = lambda: smtplib.SMTP(  # noqa: E731
                config.host, config.port, timeout=config.timeout)
        with client_factory() as client:
            if config.starttls:
                client.starttls(context=ssl.create_default_context())
            if config.username is not None:
                client.login(config.username, config.password or "")
            refusals = client.send_message(message)
            # smtplib returns {recipient: (code, text)} for refused recipients.
            if isinstance(refusals, dict) and refusals:
                raise EmailAdapterError("SMTP email delivery failed")
    except smtplib.SMTPResponseException as exc:
        # The numeric reply is the operator's nail ("550 sending is not enabled
        # for this domain"); the server's free text and the credentials are not.
        raise EmailAdapterError(
            f"SMTP email delivery failed ({exc.smtp_code})") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailAdapterError("SMTP email delivery failed") from exc
    return message_id
