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
.ws { display: grid; gap: 2px; text-align: left; }
.ws b { font-weight: 600; }
.ws small { font-size: 13px; color: #6a6a6a; font-weight: 400; }
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
p a { color: inherit; text-decoration: underline; text-underline-offset: 3px; }
@media (prefers-color-scheme: dark) {
  body { background: #111; color: #f2f2f2; }
  p { color: #b9b9b9; }
  .who { color: #f2f2f2; }
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


def _page(
    title: str,
    body: str,
    *,
    style: str = "",
    description: str = "Sign in with Google to open your Hoja Cloud workspace.",
    image_url: str = "https://app.hoja.work/og-image.png",
) -> str:
    """One server-rendered Hoja page. ``style`` adds page-specific rules after the shared ones."""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        "<meta name=\"theme-color\" content=\"#ffffff\">"
        "<link rel=\"icon\" type=\"image/svg+xml\" href=\"/hoja-clover.8371400e.svg\">"
        "<link rel=\"icon\" type=\"image/png\" sizes=\"32x32\" href=\"/favicon-32x32.png\">"
        "<link rel=\"apple-touch-icon\" sizes=\"180x180\" href=\"/apple-touch-icon.png\">"
        f"<title>{_e(title)}</title>"
        f"<meta name=\"description\" content=\"{_e(description)}\">"
        "<meta property=\"og:site_name\" content=\"Hoja\">"
        "<meta property=\"og:type\" content=\"website\">"
        f"<meta property=\"og:title\" content=\"{_e(title)}\">"
        f"<meta property=\"og:description\" content=\"{_e(description)}\">"
        f"<meta property=\"og:image\" content=\"{_e(image_url)}\">"
        "<meta property=\"og:image:type\" content=\"image/png\">"
        "<meta property=\"og:image:width\" content=\"1200\">"
        "<meta property=\"og:image:height\" content=\"630\">"
        "<meta name=\"twitter:card\" content=\"summary_large_image\">"
        f"<meta name=\"twitter:title\" content=\"{_e(title)}\">"
        f"<meta name=\"twitter:description\" content=\"{_e(description)}\">"
        f"<meta name=\"twitter:image\" content=\"{_e(image_url)}\">"
        f"<style>{_STYLE}{style}</style></head>"
        "<body><main>"
        "<div class=\"brand\"><span class=\"mark\" aria-hidden=\"true\"></span>Hoja</div>"
        f"{body}</main></body></html>"
    )


#: How a refusal is resolved, which decides the page's primary action.
#:
#: ``DENIED``: the identity was checked and is not allowed here. The way forward
#: is another account or an admin, never retrying the same one.
#: ``RETRY``: nothing was decided about the person. A link expired, a tab was
#: reused, Google was cancelled. The way forward is to start again.
#: ``OURS``: the deployment is misconfigured or failed. Not the person's fault;
#: the error id is what support needs.
DENIED, RETRY, OURS = "denied", "retry", "ours"

_RETRY_GUIDANCE = (
    "Nothing is wrong with your account. Sign-in links only last a couple of "
    "minutes and work once, in the browser tab that started them. Start again "
    "and you'll be taken through it."
)
_OURS_GUIDANCE = (
    "This is a problem on our side, not with your account. Please try again in a "
    "few minutes. If it keeps happening, contact support and quote the error id "
    "below."
)

#: Every reason a sign-in failure can carry, with what the person sees.
#: ``tests/test_signin_failure_pages.py`` collects the reason codes the code can
#: emit and fails when one is missing here: a refusal must never fall through to
#: a generic "not authorised" when it was really an expired link.
REASONS: dict[str, tuple[str, str, str]] = {
    # --- membership decisions (google_login) -----------------------------
    "not_invited": (DENIED, "This account isn't a member of this workspace",
        "The Google account you used hasn't been added to this workspace. "
        "Ask a workspace admin to invite it, or sign in with the Google account "
        "that was invited."),
    "no_membership": (DENIED, "This account no longer has access",
        "The Google account you used is not an active member of this workspace. "
        "A workspace admin can restore access."),
    "membership_inactive": (DENIED, "This account's access was removed",
        "The Google account you used was a member of this workspace, but its access "
        "has been turned off. A workspace admin can restore it."),
    "account_disabled": (DENIED, "This account is deactivated",
        "The Google account you used belongs to a profile that has been deactivated. "
        "A workspace admin can re-enable it."),
    "profile_linked_elsewhere": (DENIED, "This profile is already linked to another sign-in",
        "The workspace profile for this address is already connected to a different "
        "account. Sign in with that account, or ask a workspace admin to check the "
        "member's sign-in."),
    "profile_not_found": (DENIED, "We couldn't find your profile in this workspace",
        "Your sign-in is linked to a profile that no longer exists here. A workspace "
        "admin can invite this address again."),
    "owner_only": (DENIED, "This workspace can only be set up by its owner",
        "Sign in with the Google account this workspace was created with."),
    "owner_profile_ambiguous": (OURS, "We couldn't tell which owner profile to use",
        "This workspace has more than one profile that could be its owner, so we "
        "stopped instead of guessing. Contact support with the error id below and "
        "we'll sort it out."),
    "invalid_email": (DENIED, "Google didn't return a usable address",
        "Try again and pick an account with a verified email address."),
    "missing_subject": (DENIED, "Google didn't return a stable account id",
        "Try again, or use a different Google account."),
    "invite_address_mismatch": (DENIED, "This invitation was sent to a different address",
        "Your invitation is valid, but it is for a different Google account than "
        "the one you used. Sign in with the invited account to accept it."),
    # --- workspace policy ------------------------------------------------
    "signin_method_disabled": (DENIED, "Google sign-in is turned off here",
        "This workspace doesn't allow signing in with Google. Ask a workspace admin "
        "how members sign in."),
    "ip_not_allowed": (DENIED, "This network isn't allowed for this workspace",
        "This workspace only accepts sign-ins from approved networks. Connect from an "
        "approved network (for example your office or VPN) and start again, or ask a "
        "workspace admin."),
    "email_unverified": (DENIED, "Google hasn't verified this address",
        "Hoja only accepts addresses Google has verified. Verify the address with "
        "Google, or use a different account."),
    # --- the handoff token (identity_transfer) ---------------------------
    "expired": (RETRY, "That sign-in link expired", _RETRY_GUIDANCE),
    # HOJA-733: every transfer works once; an app's transfer needs the app's proof key.
    "replayed": (RETRY, "That sign-in link was already used", _RETRY_GUIDANCE),
    "challenge_invalid": (OURS, "The Hoja app sent a sign-in we couldn't use", _OURS_GUIDANCE),
    "verifier_missing": (RETRY, "Finish signing in from the Hoja app",
        "This sign-in was started in the Hoja app and can only be finished there. "
        "Open the app and sign in again."),
    "verifier_mismatch": (RETRY, "Finish signing in from the Hoja app",
        "This sign-in was started by another copy of the app. Open the Hoja app "
        "on this device and sign in again."),
    "malformed": (RETRY, "That sign-in link is incomplete", _RETRY_GUIDANCE),
    "wrong_audience": (OURS, "That sign-in was meant for a different workspace",
        "The sign-in was issued for another workspace address. " + _OURS_GUIDANCE),
    "bad_signature": (OURS, "We couldn't verify that sign-in", _OURS_GUIDANCE),
    "key_missing": (OURS, "Sign-in isn't set up correctly here", _OURS_GUIDANCE),
    "claims_missing": (OURS, "That sign-in was missing details", _OURS_GUIDANCE),
    "transfer_unconfigured": (OURS, "Sign-in isn't set up correctly here", _OURS_GUIDANCE),
    "resolve_failed": (OURS, "We couldn't finish signing you in", _OURS_GUIDANCE),
    "session_failed": (OURS, "We couldn't finish signing you in", _OURS_GUIDANCE),
    # --- the Google round-trip (signup_store.consume_flow, callback) ------
    "state_unknown": (RETRY, "This sign-in attempt isn't recognised", _RETRY_GUIDANCE),
    "state_binding_mismatch": (RETRY, "This sign-in started in another browser tab",
        "For your safety, a sign-in has to finish in the same browser that started "
        "it. Start again here."),
    "state_replayed": (RETRY, "This sign-in was already used", _RETRY_GUIDANCE),
    "state_expired": (RETRY, "This sign-in attempt expired", _RETRY_GUIDANCE),
    "provider_error": (RETRY, "Google sign-in was cancelled",
        "Google didn't complete the sign-in, usually because it was cancelled or "
        "an account wasn't chosen. Start again when you're ready."),
    "code_missing": (RETRY, "Google didn't finish the sign-in", _RETRY_GUIDANCE),
    "redirect_uri_not_allowed": (OURS, "Sign-in isn't set up correctly here", _OURS_GUIDANCE),
    "signup_unconfigured": (OURS, "Sign-in isn't set up correctly here", _OURS_GUIDANCE),
    # --- Google ID token verification (google_oidc.OIDCError) ------------
    "token_expired": (RETRY, "Google's answer arrived too late", _RETRY_GUIDANCE),
    "nonce_mismatch": (RETRY, "This sign-in attempt doesn't match", _RETRY_GUIDANCE),
    "exchange_failed": (RETRY, "Google didn't answer in time", _RETRY_GUIDANCE),
    "unknown_key": (RETRY, "We couldn't check Google's answer yet", _RETRY_GUIDANCE),
    "claim_missing": (DENIED, "Google didn't return the details we need",
        "The account you chose didn't share an email address. Try again with an "
        "account that has one."),
    "subject_missing": (DENIED, "Google didn't return a stable account id",
        "Try again, or use a different Google account."),
    "token_not_yet_valid": (OURS, "Our clock and Google's disagree", _OURS_GUIDANCE),
    "pkce_missing": (OURS, "Sign-in isn't set up correctly here", _OURS_GUIDANCE),
    "jwks_malformed": (OURS, "We couldn't check Google's answer", _OURS_GUIDANCE),
    "alg_missing": (OURS, "We couldn't check Google's answer", _OURS_GUIDANCE),
    "alg_none": (OURS, "We couldn't check Google's answer", _OURS_GUIDANCE),
    "alg_not_allowed": (OURS, "We couldn't check Google's answer", _OURS_GUIDANCE),
    "issuer_mismatch": (OURS, "We couldn't check Google's answer", _OURS_GUIDANCE),
    "audience_mismatch": (OURS, "We couldn't check Google's answer", _OURS_GUIDANCE),
}

_DEFAULT_DENIAL = (
    DENIED,
    "This account can't open this workspace",
    "The Google account you used is not authorised for this workspace.",
)


def reason_kind(reason: str) -> str:
    return REASONS.get(reason, _DEFAULT_DENIAL)[0]


def denial_page(
    *,
    host: str,
    email: str | None,
    reason: str,
    error_id: str,
    entry_url: str,
    retry_url: str | None = None,
    invited_hint: str | None = None,
    invited_url: str | None = None,
    support_url: str | None = None,
) -> str:
    """The page shown when a sign-in fails, shaped by what the person can do.

    ``entry_url`` signs out and asks for another account; ``retry_url`` starts
    the same sign-in again for the same workspace (used for transient and
    our-side failures); ``invited_hint`` is the masked address an invitation
    went to, and ``invited_url`` restarts sign-in asking for that account.
    """
    kind, title, guidance = REASONS.get(reason, _DEFAULT_DENIAL)
    who = (
        f"<p>You signed in as <span class=\"who\">{_e(email)}</span>.</p>"
        if email and kind == DENIED
        else ""
    )
    invited = (
        f"<p>The invitation was sent to <span class=\"who\">{_e(invited_hint)}</span>.</p>"
        if invited_hint
        else ""
    )
    support = (
        f"<a class=\"secondary\" href=\"{_e(support_url)}\">Contact support<span>→</span></a>"
        if support_url
        else ""
    )
    if invited_hint and invited_url:
        actions = (
            f"<a class=\"button\" href=\"{_e(invited_url)}\">Choose the invited account"
            "<span>→</span></a>"
            f"<a class=\"secondary\" href=\"{_e(entry_url)}\">Use a different account"
            "<span>→</span></a>"
        )
    elif kind in (RETRY, OURS) and retry_url:
        label = "Start again" if kind == RETRY else "Try again"
        actions = (
            f"<a class=\"button\" href=\"{_e(retry_url)}\">{label}<span>→</span></a>"
            f"<a class=\"secondary\" href=\"{_e(entry_url)}\">Use a different account"
            "<span>→</span></a>"
        )
    else:
        actions = (
            f"<a class=\"button\" href=\"{_e(entry_url)}\">Try a different account"
            "<span>→</span></a>"
        )
    body = (
        f"<h1>{_e(title)}</h1>"
        f"<p>{_e(guidance)}</p>{invited}{who}"
        f"<div class=\"actions\">{actions}{support}</div>"
        f"<div class=\"meta\">Workspace <code>{_e(host)}</code><br>"
        f"Error id <code>{_e(error_id)}</code> — quote this if you contact support.</div>"
    )
    return _page(f"{title} · Hoja", body)


def unknown_workspace_page(*, host: str, sign_in_url: str) -> str:
    """A workspace address that does not exist, caught before Google is asked."""
    body = (
        f"<h1>There's no workspace at {_e(host)}</h1>"
        "<p>Check the address for a typo. Workspace addresses look like "
        "<span class=\"who\">yourteam.hoja.work</span>; your invitation email has "
        "the exact one.</p>"
        "<div class=\"actions\">"
        f"<a class=\"button\" href=\"{_e(sign_in_url)}\">Sign in and find my workspace"
        "<span>→</span></a>"
        "</div>"
    )
    return _page("No such workspace · Hoja", body)


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
    workspaces: list[dict],
    email: str | None = None,
    sign_out_url: str | None = None,
) -> str:
    """More than one workspace for this identity: let the person pick one.

    Each entry is ``{"name", "host", "url"}`` plus an optional ``status`` of
    ``invited``. A workspace is shown by its display name with its address
    underneath, because a bare slug is not what anybody calls their workspace.

    The page names the account it is about to hand over. Without that line the
    chooser is the one Hoja screen where a person cannot tell whose session they
    are looking at, and there is no way out of it (the sign-out lives three
    screens away, on a workspace they have not opened yet).
    """
    def _item(entry: dict) -> str:
        badge = " · invitation" if entry.get("status") == "invited" else ""
        host = entry.get("host") or ""
        return (
            f"<li><a class=\"secondary\" href=\"{_e(entry['url'])}\">"
            f"<span class=\"ws\"><b>{_e(entry.get('name') or host)}</b>"
            f"<small>{_e(host)}{_e(badge)}</small></span><span>→</span></a></li>"
        )

    items = "".join(_item(entry) for entry in workspaces)
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
