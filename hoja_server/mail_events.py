"""The two places Hoja sends email, expressed as named events.

Keeping the event here -- not in a resolver and not in a route handler -- is
what lets the same invite be sent from onboarding, from Members, or later from
an API without three copies of the same wording and the same idempotency key.

Every function returns a bool and never raises for a delivery problem: a mail
failure must not fail an invite that already exists in the database.
"""

from __future__ import annotations

import hashlib
import logging

from hoja_server import mail_config, mail_outbox

log = logging.getLogger("hoja.mail")


def _key(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{parts[0]}:{digest}"


def send_invite(engine, *, organization_id: str, organization_name: str,
                inviter_name: str, invitee_name: str, invitee_email: str,
                code: str, expires_hours: int = 72) -> str:
    """One of ``sent``, ``not_configured`` or ``failed``.

    A single boolean could not tell an admin which of those happened, so the
    screen said "invite ready" for a send that never left.
    """
    config = mail_config.current()
    if not config.configured:
        return "not_configured"
    try:
        sent = mail_outbox.send_now(
            engine,
            config,
            template="invite",
            recipient=invitee_email,
            payload={
                "invitee_name": invitee_name or invitee_email,
                "inviter_name": inviter_name or organization_name,
                "workspace_name": organization_name,
                "invite_url": config.invitation_url(code),
                "expires_hours": expires_hours,
            },
            # A re-issued invitation has a new code and therefore a new key: the
            # teammate gets a fresh link, and the old one is already invalid.
            idempotency_key=_key("invite", organization_id, invitee_email, code),
            organization_id=organization_id,
        )
        return "sent" if sent else "failed"
    except Exception:
        log.exception("mail.invite_failed")
        return "failed"


def send_welcome(engine, *, organization_id: str, organization_name: str,
                 user_id: str, name: str, email: str, workspace_url: str = "") -> bool:
    config = mail_config.current()
    if not config.configured:
        return False
    try:
        return mail_outbox.send_now(
            engine,
            config,
            template="welcome",
            recipient=email,
            payload={
                "name": name or email,
                "workspace_name": organization_name,
                "app_url": config.app_url,
                "workspace_url": workspace_url or config.app_url,
            },
            idempotency_key=_key("welcome", organization_id, user_id),
            organization_id=organization_id,
        )
    except Exception:
        log.exception("mail.welcome_failed")
        return False
