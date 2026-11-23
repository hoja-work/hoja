"""Durable outbox for application email, with an idempotent enqueue.

Sending inside a request is how an invite ends up half-sent: the row is written,
the SMTP call hangs, the browser retries, and the teammate gets three links.
So the database is the queue and delivery is a separate, repeatable step:

1. ``enqueue`` writes one row keyed by an idempotency key. A duplicate is not an
   error -- it returns ``None`` and sends nothing.
2. ``deliver_row`` renders and submits that row, recording attempts and the
   provider's message id. A failure leaves the row queued for the next drain.
3. ``deliver_pending`` drains the queue; it is safe to call from a cron, a
   startup task, or right after enqueueing.

The enqueue is part of the caller's transaction only when it shares the
connection; callers here pass the engine so a mail failure can never roll back
an invite that already exists.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from hoja_server import db, mail_templates
from hoja_server.email_adapter import EmailAdapterError, SMTPConfig, send_email
from hoja_server.mail_config import MailConfig

log = logging.getLogger("hoja.mail")

MAX_ATTEMPTS = 5
_CF_ENDPOINT = (
    "https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send"
)


class MailDeliveryError(RuntimeError):
    """A provider refused or could not accept the message."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(engine, *, template: str, recipient: str, payload: dict[str, Any],
            idempotency_key: str, organization_id: str | None = None) -> str | None:
    """Write one queued message. Returns its id, or ``None`` if it already exists."""
    address = (recipient or "").strip().lower()
    if "@" not in address or " " in address:
        raise ValueError("recipient must be a single email address")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    row_id = db.new_id()
    try:
        with engine.begin() as conn:
            conn.execute(db.mail_outbox.insert().values(
                id=row_id,
                organization_id=organization_id,
                template=template,
                recipient=address,
                payload=json.dumps(payload, ensure_ascii=False),
                idempotency_key=idempotency_key.strip(),
                status="queued",
                attempts=0,
                created_at=_now(),
            ))
    except IntegrityError:
        # Same logical message already queued (or sent): this is the contract.
        return None
    return row_id


def _cloudflare_send(config: MailConfig, *, to: str, subject: str, text: str,
                     html: str) -> str | None:
    body: dict[str, Any] = {
        "to": to,
        "from": config.from_address,
        "subject": subject,
        "text": text,
        "html": html,
    }
    if config.reply_to:
        body["reply_to"] = config.reply_to
    request = urllib.request.Request(
        _CF_ENDPOINT.format(account=config.account_id),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "authorization": f"Bearer {config.api_token}",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            parsed = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        code = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8") or "{}")
            errors = parsed.get("errors") or []
            if errors and isinstance(errors[0], dict):
                code = f" {errors[0].get('code', '')} {errors[0].get('message', '')}".rstrip()
        except (ValueError, OSError):
            pass
        raise MailDeliveryError(f"cloudflare rejected the message:{code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MailDeliveryError(f"cloudflare transport failed ({type(exc).__name__})") from None

    result = parsed.get("result") or {}
    bounced = result.get("permanent_bounces") or []
    if to in bounced:
        raise MailDeliveryError("cloudflare reported a permanent bounce")
    message_id = result.get("message_id") or result.get("id")
    return str(message_id) if message_id else None


def _smtp_send(config: MailConfig, *, to: str, subject: str, text: str,
               html: str) -> str | None:
    message_id = send_email(
        SMTPConfig(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username or None,
            password=config.smtp_password or None,
            starttls=config.smtp_tls == "starttls",
            implicit_tls=config.smtp_tls == "implicit",
        ),
        sender=config.from_address,
        recipients=[to],
        subject=subject,
        text=text,
        html=html,
        reply_to=config.reply_to or None,
    )
    return message_id


def send_rendered(config: MailConfig, *, to: str, rendered: mail_templates.Rendered) -> str | None:
    if not config.configured:
        raise MailDeliveryError("email is not configured")
    if config.transport == "cloudflare":
        return _cloudflare_send(config, to=to, subject=rendered.subject,
                               text=rendered.text, html=rendered.html)
    return _smtp_send(config, to=to, subject=rendered.subject,
                      text=rendered.text, html=rendered.html)


def deliver_row(engine, config: MailConfig, row) -> bool:
    """Render and submit one queued row. Never raises for an ordinary failure."""
    payload = json.loads(row.payload or "{}")
    try:
        rendered = mail_templates.render(row.template, **payload)
        provider_id = send_rendered(config, to=row.recipient, rendered=rendered)
    except (mail_templates.MailTemplateError, MailDeliveryError,
            EmailAdapterError, ValueError) as exc:
        attempts = int(row.attempts or 0) + 1
        permanent = attempts >= MAX_ATTEMPTS
        with engine.begin() as conn:
            conn.execute(update(db.mail_outbox).where(db.mail_outbox.c.id == row.id).values(
                status="failed" if permanent else "queued",
                attempts=attempts,
                last_attempt_at=_now(),
                last_error=f"{type(exc).__name__}: {str(exc)[:300]}",
            ))
        # Template/recipient identity only: no body, no token, no link.
        log.warning("mail.delivery_failed template=%s row=%s attempts=%s error=%s",
                    row.template, row.id, attempts, type(exc).__name__)
        return False

    with engine.begin() as conn:
        conn.execute(update(db.mail_outbox).where(db.mail_outbox.c.id == row.id).values(
            status="sent",
            attempts=int(row.attempts or 0) + 1,
            last_attempt_at=_now(),
            sent_at=_now(),
            provider_message_id=provider_id,
            last_error=None,
        ))
    log.info("mail.sent template=%s row=%s", row.template, row.id)
    return True


def deliver_pending(engine, config: MailConfig, *, limit: int = 25) -> dict[str, int]:
    """Drain queued rows. Safe to run concurrently: rows are claimed by status."""
    if not config.configured:
        return {"claimed": 0, "sent": 0, "failed": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    stmt = select(db.mail_outbox).where(
        db.mail_outbox.c.status == "queued",
        db.mail_outbox.c.attempts < MAX_ATTEMPTS,
        (db.mail_outbox.c.last_attempt_at.is_(None))
        | (db.mail_outbox.c.last_attempt_at < cutoff),
    ).order_by(db.mail_outbox.c.created_at).limit(limit)
    if engine.dialect.name == "postgresql":
        # A second drain skips claimed rows instead of sending them again.
        stmt = stmt.with_for_update(skip_locked=True)
    with engine.begin() as conn:
        rows = list(conn.execute(stmt).mappings())
    sent = 0
    for row in rows:
        if deliver_row(engine, config, row):
            sent += 1
    return {"claimed": len(rows), "sent": sent, "failed": len(rows) - sent}


def send_now(engine, config: MailConfig, *, template: str, recipient: str,
             payload: dict[str, Any], idempotency_key: str,
             organization_id: str | None = None) -> bool:
    """Enqueue then attempt delivery once. Returns True only if it was submitted.

    A False result is not an error: the row stays queued for ``deliver_pending``.
    """
    row_id = enqueue(engine, template=template, recipient=recipient, payload=payload,
                     idempotency_key=idempotency_key, organization_id=organization_id)
    if row_id is None:
        return False
    if not config.configured:
        return False
    with engine.connect() as conn:
        row = conn.execute(
            select(db.mail_outbox).where(db.mail_outbox.c.id == row_id)
        ).mappings().first()
    if row is None:
        return False
    return deliver_row(engine, config, row)
