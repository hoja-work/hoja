"""The one sign-in page, and the page a refused sign-in deserves.

A refusal used to be a raw JSON body:

    {"error": "This Google account is not a member of this workspace.",
     "reason": "not_invited", "agentHint": "..."}

That is an answer for a client, not for a person: it names no next step and
carries nothing support can trace. These pages keep the same security boundary
(they never say whether an account or a workspace exists beyond the host the
person is already on) and add the two things a human needs: which verified
address was used, an error id to quote, and a way forward.

Everything here is server-rendered, escaped, and self-contained: no build step,
no external asset, no script.
"""

from __future__ import annotations

import html

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center;
  font: 16px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Inter, sans-serif;
  background: #f6f6f5; color: #171717; padding: 32px 20px;
}
main { width: 100%; max-width: 440px; }
.brand { display: flex; align-items: center; gap: 8px; font-weight: 650; letter-spacing: -.02em; margin-bottom: 28px; }
.mark { width: 22px; height: 22px; border-radius: 6px; background: #171717; }
h1 { font-size: 26px; line-height: 1.2; letter-spacing: -.03em; margin: 0 0 12px; }
p { margin: 0 0 14px; color: #4a4a4a; }
.who { font-weight: 600; color: #171717; overflow-wrap: anywhere; }
.actions { display: grid; gap: 10px; margin: 26px 0 18px; }
/* One button rule for both elements: a <button> that inherits the default UA
   font resets the weight a sibling <a> keeps, which is how the same visual
   role ended up looking like two different controls. */
a.button, button.button, a.secondary, button.secondary {
  display: flex; align-items: center; justify-content: space-between;
  padding: 13px 16px; border-radius: 10px; text-decoration: none;
  font-family: inherit; font-size: inherit; line-height: inherit;
  font-weight: 550; border: 1px solid #171717; cursor: pointer;
}
button.button, button.secondary { width: 100%; }
a.button, button.button { background: #171717; color: #fff; }
a.secondary, button.secondary { background: transparent; color: inherit; border-color: #d8d8d8; }
a.secondary:hover, button.secondary:hover { border-color: #171717; }
.meta { border-top: 1px solid #e2e2e2; padding-top: 14px; font-size: 13px; color: #6a6a6a; }
.meta code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #171717; }
.error { border: 1px solid #d8d8d8; background: #fff; border-radius: 10px; padding: 10px 12px; margin-bottom: 16px; font-size: 14px; }
ul { margin: 0 0 18px; padding: 0; list-style: none; display: grid; gap: 8px; }
h2 { font-size: 15px; letter-spacing: -.01em; margin: 26px 0 6px; }
.invite-form { display: grid; gap: 8px; margin: 0 0 20px; }
.invite-form label { font-size: 13px; font-weight: 600; }
.invite-form input {
  width: 100%; padding: 12px 14px; border-radius: 10px;
  font: inherit; background: #fff; color: #171717;
  border: 1px solid #d8d8d8;
}
.invite-form input:focus { outline: 2px solid #171717; outline-offset: 1px; }
.invite-form button { margin-top: 2px; }
.or { display: flex; align-items: center; gap: 10px; margin: 22px 0 4px; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #6a6a6a; }
.or::before, .or::after { content: ""; height: 1px; background: #e2e2e2; flex: 1; }
@media (prefers-color-scheme: dark) {
  body { background: #111; color: #f2f2f2; }
  p { color: #b9b9b9; }
  a.button { background: #f2f2f2; color: #111; border-color: #f2f2f2; }
  button.button { background: #f2f2f2; color: #111; border-color: #f2f2f2; }
  a.secondary { color: #f2f2f2; border-color: #3a3a3a; }
  .meta { border-color: #2b2b2b; color: #9a9a9a; }
  .meta code { color: #f2f2f2; }
  .error { background: #1a1a1a; border-color: #3a3a3a; }
  .mark { background: #f2f2f2; }
  .invite-form input { background: #1a1a1a; border: 1px solid #3a3a3a; color: #f2f2f2; }
  .or::before, .or::after { background: #2b2b2b; }
}
"""


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>{_e(title)}</title><style>{_STYLE}</style></head>"
        "<body><main>"
        "<div class=\"brand\"><span class=\"mark\" aria-hidden=\"true\"></span>Hoja</div>"
        f"{body}</main></body></html>"
    )


#: What a person can do about each refusal. The copy never claims an account or
#: workspace exists, and never suggests retrying the same credentials.
_DENIAL_COPY = {
    "not_invited": (
        "This account isn't a member of this workspace",
        "The Google account you used hasn't been added to this workspace. "
        "Ask a workspace admin to invite it, or sign in with the Google account "
        "that was invited.",
    ),
    "no_membership": (
        "This account no longer has access",
        "The Google account you used is not an active member of this workspace. "
        "A workspace admin can restore access.",
    ),
    "account_disabled": (
        "This account is deactivated",
        "The Google account you used belongs to a profile that has been deactivated. "
        "A workspace admin can re-enable it.",
    ),
    "owner_only": (
        "This workspace can only be set up by its owner",
        "Sign in with the Google account this workspace was created with.",
    ),
    "invalid_email": (
        "Google didn't return a usable address",
        "Try again and pick an account with a verified email address.",
    ),
    "missing_subject": (
        "Google didn't return a stable account id",
        "Try again, or use a different Google account.",
    ),
}

_DEFAULT_DENIAL = (
    "This account can't open this workspace",
    "The Google account you used is not authorised for this workspace.",
)


def denial_page(
    *,
    host: str,
    email: str | None,
    reason: str,
    error_id: str,
    entry_url: str,
    support_url: str | None = None,
) -> str:
    """The page shown to a person whose verified identity was refused."""
    title, guidance = _DENIAL_COPY.get(reason, _DEFAULT_DENIAL)
    who = (
        f"<p>You signed in as <span class=\"who\">{_e(email)}</span>.</p>"
        if email
        else ""
    )
    support = (
        f"<a class=\"secondary\" href=\"{_e(support_url)}\">Contact support<span>→</span></a>"
        if support_url
        else ""
    )
    body = (
        f"<h1>{_e(title)}</h1>"
        f"<p>{_e(guidance)}</p>{who}"
        "<div class=\"actions\">"
        f"<a class=\"button\" href=\"{_e(entry_url)}\">Try a different account<span>→</span></a>"
        f"{support}"
        "</div>"
        f"<div class=\"meta\">Workspace <code>{_e(host)}</code><br>"
        f"Error id <code>{_e(error_id)}</code> — quote this if you contact support.</div>"
    )
    return _page(f"{title} · Hoja", body)


def sign_in_page(*, google_url: str, error: str | None = None) -> str:
    """The single entry point. One identity provider, no local credential form."""
    banner = f"<div class=\"error\">{_e(error)}</div>" if error else ""
    body = (
        f"{banner}<h1>Sign in to Hoja</h1>"
        "<p>Use the Google account your workspace knows. We'll take you to it.</p>"
        "<div class=\"actions\">"
        f"<a class=\"button\" href=\"{_e(google_url)}\">Continue with Google<span>→</span></a>"
        "</div>"
        "<div class=\"meta\">One sign-in for every workspace. Your Google password "
        "is never shared with us.</div>"
    )
    return _page("Sign in · Hoja", body)


def choose_workspace_page(
    *,
    workspaces: list[tuple[str, str]],
    email: str | None = None,
    sign_out_url: str | None = None,
) -> str:
    """More than one workspace for this identity: let the person pick one.

    The page names the account it is about to hand over. Without that line the
    chooser is the one Hoja screen where a person cannot tell whose session they
    are looking at, and there is no way out of it (the sign-out lives three
    screens away, on a workspace they have not opened yet).
    """
    items = "".join(
        f"<li><a class=\"secondary\" href=\"{_e(url)}\">{_e(name)}<span>→</span></a></li>"
        for name, url in workspaces
    )
    who = (
        f"<p>Signed in as <span class=\"who\">{_e(email)}</span>.</p>" if email else ""
    )
    sign_out = (
        "<div class=\"actions\">"
        f"<a class=\"secondary\" href=\"{_e(sign_out_url)}\">Sign out<span>→</span></a>"
        "</div>"
        if sign_out_url
        else ""
    )
    body = (
        "<h1>Choose a workspace</h1>"
        "<p>This Google account belongs to more than one workspace.</p>"
        f"{who}<ul>{items}</ul>{sign_out}"
    )
    return _page("Choose a workspace · Hoja", body)


def signed_out_page(*, sign_in_url: str) -> str:
    """Where a finished sign-out lands: a statement, and one way forward.

    Deliberately inert. No chooser, no workspace list, no meta refresh and no
    script: every one of those turns "you're signed out" into "here is another
    screen that moves you", which is the report this page answers. The single
    button goes back to the workspace's sign-in entry, which on a signed-out
    browser goes straight to Google's account chooser.
    """
    body = (
        "<h1>You're signed out</h1>"
        "<p>You're signed out of Hoja in this browser.</p>"
        "<div class=\"actions\">"
        f"<a class=\"button\" href=\"{_e(sign_in_url)}\">Sign in again<span>→</span></a>"
        "</div>"
        "<div class=\"meta\">Signing out ended this browser's Hoja sessions, "
        "including the control plane's. Signing in again asks which Google "
        "account to use.</div>"
    )
    return _page("Signed out · Hoja", body)


def no_workspace_page(
    *,
    email: str | None,
    sign_out_url: str,
    invite_action: str | None = None,
    invite_error: str | None = None,
) -> str:
    """Verified identity with no workspace behind it.

    Two people reach this page and they need different things. Someone who was
    invited already holds a link that names their workspace, so the way out is
    to use it — not to create a second workspace beside the one that invited
    them. Someone who was not invited needs an admin. The invitation path comes
    first because it is the one that was previously a dead end; "ask an admin"
    survives underneath it for the case that actually needs it.
    """
    who = f" as <span class=\"who\">{_e(email)}</span>" if email else ""
    invite_block = ""
    if invite_action:
        problem = f"<div class=\"error\">{_e(invite_error)}</div>" if invite_error else ""
        invite_block = (
            f"{problem}"
            "<h2>Were you invited?</h2>"
            "<p>Paste the invitation link from your email. It already names the "
            "workspace you were added to.</p>"
            f"<form class=\"invite-form\" method=\"post\" action=\"{_e(invite_action)}\">"
            "<label for=\"invite-link\">Invitation link</label>"
            "<input id=\"invite-link\" name=\"link\" required autocomplete=\"off\" "
            "spellcheck=\"false\" placeholder=\"https://acme.hoja.work/#invite=…\">"
            "<button class=\"button\" type=\"submit\">Open my invitation<span>→</span></button>"
            "</form>"
            "<h2>Not invited yet?</h2>"
        )
    body = (
        f"<h1>No workspace yet</h1>"
        f"<p>You're signed in{who}, but this account isn't a member of a workspace "
        "yet.</p>"
        f"{invite_block}"
        "<p>Ask a workspace admin to invite this address, then sign in again.</p>"
        "<div class=\"actions\">"
        f"<form method=\"post\" action=\"{_e(sign_out_url)}\">"
        "<button class=\"secondary\" type=\"submit\">Use a different account<span>→</span></button>"
        "</form>"
        "<a class=\"secondary\" href=\"https://hoja.work\">About Hoja<span>→</span></a>"
        "</div>"
    )
    return _page("No workspace yet · Hoja", body)
