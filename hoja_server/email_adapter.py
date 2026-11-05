"""Small synchronous SMTP adapter for application-originated email.

This module deliberately has no application wiring: callers provide explicit
SMTP settings and a message, making transport configuration deployment-owned.
"""

from __future__ import annotations

import smtplib
import ssl
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


_DISPLAY_NAME_FORBIDDEN = set('<>@,;:"\\')


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
) -> None:
    """Send one plain-text message; raise :class:`EmailAdapterError` on failure.

    SMTP STARTTLS is enabled by default. Provider/network exceptions are wrapped
    without embedding credentials or server response contents in the message.
    """
    if not isinstance(config, SMTPConfig):
        raise EmailAdapterError("config must be an SMTPConfig")
    if not isinstance(subject, str) or not subject.isprintable():
        raise EmailAdapterError("subject must be a single-line string")
    if not isinstance(text, str):
        raise EmailAdapterError("text body must be a string")
    clean_sender = _validated_address(sender, "sender")
    if not isinstance(recipients, (list, tuple)) or not recipients:
        raise EmailAdapterError("at least one recipient is required")
    clean_recipients = [_validated_address(item, "recipient") for item in recipients]

    message = EmailMessage()
    message["From"] = clean_sender
    message["To"] = ", ".join(clean_recipients)
    message["Subject"] = subject
    message.set_content(text)

    try:
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as client:
            if config.starttls:
                client.starttls(context=ssl.create_default_context())
            if config.username is not None:
                client.login(config.username, config.password or "")
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailAdapterError("SMTP email delivery failed") from exc
