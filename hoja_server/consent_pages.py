"""Server-rendered pages of the OAuth consent step (MCP clients and other apps).

Same visual language and helpers as the sign-in pages: escaped, self-contained,
no script. A person reaches these from an app that opened the browser, so every
page names that app and says what happens next.
"""

from __future__ import annotations

from hoja_server.signin_pages import _e, _page

_STYLE = """
.facts { margin: 0 0 18px; display: grid; gap: 6px; font-size: 14px; }
.facts div { display: flex; gap: 10px; justify-content: space-between; }
.facts dt { color: #6a6a6a; }
.facts dd { margin: 0; font-weight: 600; text-align: right; overflow-wrap: anywhere; }
fieldset { border: 0; padding: 0; margin: 0 0 18px; display: grid; gap: 8px; }
legend { font-size: 13px; font-weight: 600; margin-bottom: 6px; padding: 0; }
label.choice {
  display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; align-items: start;
  padding: 11px 14px; border: 1px solid #d8d8d8; border-radius: 10px; cursor: pointer;
}
label.choice:has(input:checked) { border-color: #171717; }
label.choice input { margin: 4px 0 0; accent-color: #171717; }
label.choice span { font-weight: 550; }
label.choice small { grid-column: 2; font-size: 13px; color: #6a6a6a; }
.note { font-size: 13px; color: #6a6a6a; margin: -4px 0 18px; }
.actions.two { grid-template-columns: 1fr 1fr; }
@media (prefers-color-scheme: dark) {
  .facts dt, label.choice small, .note { color: #9a9a9a; }
  label.choice { border-color: #3a3a3a; }
  label.choice:has(input:checked) { border-color: #f2f2f2; }
  label.choice input { accent-color: #f2f2f2; }
  button.secondary { color: #f2f2f2; border-color: #3a3a3a; }
}
"""


def _facts(rows: list[tuple[str, str]]) -> str:
    items = "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in rows)
    return f"<dl class=\"facts\">{items}</dl>"


def consent_page(
    *,
    client_name: str,
    redirect_host: str,
    workspaces: list[dict],
    selected_org: str,
    workspace_fixed: bool,
    host: str,
    can_write: bool,
    write_requested: bool,
    csrf_token: str,
    email: str | None,
    switch_account_url: str,
    other_hosts: list[tuple[str, str]] | None = None,
    returns_label: str = "Returns to",
) -> str:
    """The approval screen. ``workspaces`` items carry ``id``, ``name``."""
    selected = next((w for w in workspaces if w["id"] == selected_org), workspaces[0])
    facts = [("App", client_name), (returns_label, redirect_host)]
    if workspace_fixed or len(workspaces) == 1:
        facts.append(("Workspace", f"{selected['name']} · {host}"))
    body = [
        f"<h1>Allow {_e(client_name)} to use Hoja?</h1>",
        "<p>It will act as you, with the access you choose below.</p>",
        _facts(facts),
        "<form method=\"post\">",
        f"<input type=\"hidden\" name=\"csrf_token\" value=\"{_e(csrf_token)}\">",
    ]
    if not workspace_fixed and len(workspaces) > 1:
        options = "".join(
            "<label class=\"choice\">"
            f"<input type=\"radio\" name=\"workspace\" value=\"{_e(w['id'])}\""
            f"{' checked' if w['id'] == selected['id'] else ''}>"
            f"<span>{_e(w['name'])}</span></label>"
            for w in workspaces
        )
        body.append(f"<fieldset><legend>Workspace</legend>{options}</fieldset>")
    else:
        body.append(f"<input type=\"hidden\" name=\"workspace\" value=\"{_e(selected['id'])}\">")
        if workspace_fixed:
            body.append(
                f"<p class=\"note\">This connection is for {_e(selected['name'])} only, "
                f"because it was started on {_e(host)}.</p>"
            )
    if other_hosts:
        listed = ", ".join(f"{_e(name)} at https://{_e(h)}/mcp" for name, h in other_hosts)
        body.append(
            "<p class=\"note\">Workspaces with their own address connect there instead: "
            f"{listed}.</p>"
        )
    if write_requested and can_write:
        body.append(
            "<fieldset><legend>Access</legend>"
            "<label class=\"choice\"><input type=\"radio\" name=\"access\" value=\"write\" checked>"
            "<span>Read and write</span><small>Read tasks and projects, and create or change them.</small></label>"
            "<label class=\"choice\"><input type=\"radio\" name=\"access\" value=\"read\">"
            "<span>Read only</span><small>Read tasks and projects. Nothing can be changed.</small></label>"
            "</fieldset>"
        )
    else:
        reason = (
            "Read only. The app asked for read access."
            if not write_requested
            else "Read only. Your account cannot make changes in this workspace."
        )
        body.append("<input type=\"hidden\" name=\"access\" value=\"read\">")
        body.append(f"<p class=\"note\">{_e(reason)}</p>")
    body.append(
        "<div class=\"actions two\">"
        "<button class=\"secondary\" type=\"submit\" name=\"decision\" value=\"deny\">Deny</button>"
        "<button class=\"button\" type=\"submit\" name=\"decision\" value=\"allow\">Allow<span>→</span></button>"
        "</div></form>"
    )
    who = f"Signed in as <code>{_e(email)}</code>. " if email else ""
    body.append(
        f"<div class=\"meta\">{who}Not you? "
        f"<a href=\"{_e(switch_account_url)}\">Switch account</a>. "
        "You can revoke this access in Settings at any time.</div>"
    )
    return _page(f"Allow {client_name} · Hoja", "".join(body), style=_STYLE)


def expired_page(*, client_name: str | None) -> str:
    """A consent request that is gone: expired, already used, or never existed."""
    app = _e(client_name) if client_name else "the app you were connecting"
    body = (
        "<h1>This connection request expired</h1>"
        f"<p>Start the connection again from {app}. It will open a fresh approval "
        "page.</p>"
        "<div class=\"meta\">Approval links work once and for 15 minutes.</div>"
    )
    return _page("Connection request expired · Hoja", body, style=_STYLE)


def wrong_host_page(*, client_name: str, workspaces: list[tuple[str, str]]) -> str:
    """Every workspace of this account has its own address; the app must connect there."""
    items = "".join(
        f"<li><code>https://{_e(h)}/mcp</code> for {_e(name)}</li>" for name, h in workspaces
    )
    body = (
        f"<h1>Connect {_e(client_name)} to your workspace's address</h1>"
        "<p>Your workspace has its own Hoja address, and apps connect to it there, "
        "not here. Remove this connection from the app and add the address below "
        "instead.</p>"
        f"<ul>{items}</ul>"
    )
    return _page("Use your workspace address · Hoja", body, style=_STYLE)


def device_code_page(*, error: str | None = None) -> str:
    """<origin>/device without a code: ask for the one the app is showing."""
    banner = f"<div class=\"error\">{_e(error)}</div>" if error else ""
    body = (
        f"{banner}<h1>Connect a device</h1>"
        "<p>Enter the code your app or terminal is showing.</p>"
        "<form class=\"invite-form\" method=\"get\" action=\"/device\">"
        "<label for=\"user-code\">Code</label>"
        "<input id=\"user-code\" name=\"user_code\" required autocomplete=\"one-time-code\" "
        "autocapitalize=\"characters\" spellcheck=\"false\" placeholder=\"XXXX-XXXX\">"
        "<button class=\"button\" type=\"submit\">Continue<span>→</span></button>"
        "</form>"
    )
    return _page("Connect a device · Hoja", body, style=_STYLE)


def device_done_page(*, approved: bool) -> str:
    if approved:
        body = ("<h1>Device connected</h1>"
                "<p>You can close this tab and return to your terminal. You can revoke this "
                "access in Settings, under Connected apps.</p>")
    else:
        body = ("<h1>Request denied</h1>"
                "<p>The device was not connected. You can close this tab.</p>")
    return _page("Device · Hoja", body, style=_STYLE)


def denied_page(*, title: str, message: str) -> str:
    return _page(f"{title} · Hoja", f"<h1>{_e(title)}</h1><p>{_e(message)}</p>", style=_STYLE)
