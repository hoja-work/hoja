"""The two messages Hoja sends by email, rendered as text *and* HTML.

Design constraints come from mail clients, not from the browser: no external
CSS, no web fonts, no flexbox. Everything is a table with inline styles, one
600px column, a single black call-to-action, and system fonts. The text part is
written first and is not a stripped version of the HTML -- it carries the same
link and the same expiry, because a text-only client must be able to act.

Both bodies escape every interpolation. A workspace name is user input, and it
reaches an email body.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

INVITE_EXPIRY_HOURS = 72

_INK = "#171717"
_MUTED = "#626262"
_LINE = "#e2e2e2"
_PAGE = "#f5f5f5"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"


class MailTemplateError(ValueError):
    """A template was asked to render without the values it needs."""


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


def _clean(value: object, field: str, *, required: bool = True) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise MailTemplateError(f"{field} is required")
    return text


def _link(value: object) -> str:
    url = _clean(value, "link")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MailTemplateError("link must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise MailTemplateError("link must not embed credentials")
    return url


def _first_name(name: str) -> str:
    return name.split(" ")[0] if name else "there"


def _html_document(*, preheader: str, heading: str, paragraphs: list[str],
                   cta_label: str, cta_url: str, meta: str, footer: str) -> str:
    body = "".join(
        f'<p style="margin:0 0 16px 0;font-size:15px;line-height:24px;color:{_INK}">{p}</p>'
        for p in paragraphs
    )
    safe_url = escape(cta_url, quote=True)
    return (
        '<!doctype html>\n'
        '<html lang="en"><head><meta charset="utf-8"/>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>\n'
        '<meta name="color-scheme" content="light only"/>\n'
        f"<title>{escape(heading)}</title></head>\n"
        f'<body style="margin:0;padding:0;background:{_PAGE};">\n'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>\n'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_PAGE};">\n'
        '<tr><td align="center" style="padding:32px 16px;">\n'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid {_LINE};border-radius:14px;">\n'
        f'<tr><td style="padding:28px 32px 0 32px;"><div style="font-family:{_FONT};font-size:14px;font-weight:700;letter-spacing:-.2px;color:{_INK};">Hoja</div></td></tr>\n'
        f'<tr><td style="padding:20px 32px 0 32px;"><h1 style="margin:0 0 18px 0;font-family:{_FONT};font-size:26px;line-height:32px;letter-spacing:-.6px;color:{_INK};">{escape(heading)}</h1><div style="font-family:{_FONT};">{body}</div></td></tr>\n'
        f'<tr><td style="padding:6px 32px 0 32px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td style="background:{_INK};border-radius:10px;"><a href="{safe_url}" style="display:inline-block;padding:14px 22px;font-family:{_FONT};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">{escape(cta_label)}</a></td></tr></table></td></tr>\n'
        f'<tr><td style="padding:18px 32px 0 32px;"><p style="margin:0;font-family:{_FONT};font-size:13px;line-height:20px;color:{_MUTED}">{escape(meta)}</p></td></tr>\n'
        f'<tr><td style="padding:18px 32px 0 32px;"><p style="margin:0;font-family:{_FONT};font-size:13px;line-height:20px;color:{_MUTED};word-break:break-all;">If the button does not work, paste this link into your browser:<br/><a href="{safe_url}" style="color:{_MUTED};">{escape(cta_url)}</a></p></td></tr>\n'
        f'<tr><td style="padding:24px 32px 28px 32px;"><div style="border-top:1px solid {_LINE};padding-top:16px;font-family:{_FONT};font-size:12px;line-height:19px;color:{_MUTED};">{footer}</div></td></tr>\n'
        '</table>\n'
        f'<div style="font-family:{_FONT};font-size:12px;line-height:18px;color:#8a8a8a;padding-top:16px;">Sent by Hoja. Replies reach a monitored mailbox.</div>\n'
        '</td></tr></table>\n'
        '</body></html>'
    )


def render(template: str, **context: object) -> Rendered:
    """Render ``invite`` or ``welcome``. Unknown templates are an error, not a guess."""
    if template == "invite":
        return _render_invite(**context)
    if template == "welcome":
        return _render_welcome(**context)
    raise MailTemplateError(f"unknown template {template!r}")


def _render_invite(*, invitee_name: object = "", inviter_name: object = "",
                   workspace_name: object = "", invite_url: object = "",
                   expires_hours: object = INVITE_EXPIRY_HOURS) -> Rendered:
    invitee = _clean(invitee_name, "invitee_name")
    inviter = _clean(inviter_name, "inviter_name")
    workspace = _clean(workspace_name, "workspace_name")
    url = _link(invite_url)
    try:
        hours = int(expires_hours)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MailTemplateError("expires_hours must be an integer") from exc
    if not 1 <= hours <= 24 * 30:
        raise MailTemplateError("expires_hours is outside the allowed window")

    subject = f"{inviter} invited you to {workspace} on Hoja"
    text = (
        f"Hi {invitee},\n\n"
        f"{inviter} set up {workspace} on Hoja and added you to it.\n\n"
        f"Join the workspace: {url}\n\n"
        f"Choose a password when you open the link. It works once and expires "
        f"in {hours} hours.\n\n"
        f"If you were not expecting this, ignore this email. The link expires on "
        f"its own and nothing is created until you use it.\n\n"
        f"-- Hoja\n"
    )
    footer = (
        f"You received this because {escape(inviter)} invited {escape(invitee)} "
        f"to a Hoja workspace. If that was not expected, ignore this email."
    )
    html = _html_document(
        preheader=f"{inviter} invited you to {workspace} on Hoja.",
        heading=f"Join {workspace} on Hoja",
        paragraphs=[
            f"Hi {escape(invitee)},",
            f"<strong>{escape(inviter)}</strong> set up {escape(workspace)} on "
            f"Hoja and added you to it. Choose a password to open the workspace.",
        ],
        cta_label=f"Join {workspace}",
        cta_url=url,
        meta=f"This invite works once and expires in {hours} hours.",
        footer=footer,
    )
    return Rendered(subject=subject, text=text, html=html)


def _render_welcome(*, name: object = "", workspace_name: object = "",
                    app_url: object = "", workspace_url: object = "") -> Rendered:
    person = _clean(name, "name")
    workspace = _clean(workspace_name, "workspace_name")
    link = _link(workspace_url or app_url)
    first = _first_name(person)

    subject = f"Your Hoja workspace {workspace} is ready"
    text = (
        f"Hi {first},\n\n"
        f"{workspace} is ready on Hoja.\n\n"
        f"Open it: {link}\n\n"
        f"Two things worth doing first:\n"
        f"  1. Invite your team from Members. Every invite is single-use and "
        f"expires in {INVITE_EXPIRY_HOURS} hours.\n"
        f"  2. Create a team and a first project so work has a home.\n\n"
        f"-- Hoja\n"
    )
    footer = (
        f"This message was sent to the owner of {escape(workspace)}. If you did "
        f"not create it, reply to this email and we will investigate."
    )
    html = _html_document(
        preheader=f"{workspace} is ready. Invite your team and start working.",
        heading=f"Welcome to Hoja, {first}",
        paragraphs=[
            f"<strong>{escape(workspace)}</strong> is set up and yours to run.",
            "Invite your team from <strong>Members</strong>. Each invite is "
            "single-use and expires on its own.",
            "Then create a team and a project so the first week has somewhere to land.",
        ],
        cta_label=f"Open {workspace}",
        cta_url=link,
        meta="You are receiving this because you created this workspace.",
        footer=footer,
    )
    return Rendered(subject=subject, text=text, html=html)
