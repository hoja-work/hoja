"""`hoja me` and `hoja auth ...` commands."""

from __future__ import annotations

import asyncio
import json as _json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

import typer

from hoja import oauth
from hoja.auth import get_viewer
from hoja.cli_state import build_client, get_state
from hoja.client import GraphClient
from hoja.config import (
    AccountConfig,
    UserInfo,
    WorkspaceInfo,
    load_config,
    resolve_base_url,
    resolve_workspace,
    save_config,
)
from hoja.credentials import delete_credentials, make_record, save_new_credentials, store_for
from hoja.errors import AuthError, ExitCode, HojaError, UsageError
from hoja.models.enums import OutputFormat
from hoja.output import render

me_app = typer.Typer(help="Show the currently-authenticated the upstream API user")
auth_app = typer.Typer(help="Authentication operations")


@me_app.callback(invoke_without_command=True)
def me(ctx: typer.Context) -> None:
    """Show the currently-authenticated user (alias for `auth status`)."""
    if ctx.invoked_subcommand is not None:
        return
    asyncio.run(_run_me())


@auth_app.command("status")
def status() -> None:
    """Show the active account, credential source, workspace and user."""
    state = get_state()
    info = credential_summary(state)
    viewer: Any = None
    viewer_error: str | None = None
    try:
        viewer = asyncio.run(_fetch_viewer())
    except HojaError as e:
        viewer_error = e.message + (f" ({e.hint})" if e.hint else "")
    if state.output == OutputFormat.JSON:
        info["viewer"] = viewer.model_dump(mode="json", exclude_none=True) if viewer is not None else None
        if viewer_error:
            info["error"] = viewer_error
        print(_json.dumps(info, indent=2))
    else:
        for key, label in (
            ("account", "Account"),
            ("source_label", "Credential"),
            ("workspace", "Workspace"),
            ("user", "User"),
            ("api_url", "API URL"),
            ("origin", "Origin"),
            ("scopes", "Scopes"),
            ("expires_at", "Expires"),
            ("store", "Stored in"),
        ):
            value = info.get(key)
            if value:
                if key == "expires_at":
                    value = _fmt_time(float(value))
                elif isinstance(value, dict):
                    value = value.get("name") or value.get("email") or value.get("id")
                elif isinstance(value, list):
                    value = " ".join(value)
                typer.echo(f"{label + ':':<12} {value}")
        if viewer is not None:
            render(viewer, fmt=state.output, title="Viewer")
        if viewer_error:
            typer.echo(f"Not signed in: {viewer_error}", err=True)
    if viewer_error:
        raise typer.Exit(int(ExitCode.AUTH_ERROR))


async def _fetch_viewer() -> Any:
    state = get_state()
    async with build_client(state) as client:
        return await get_viewer(client)


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def credential_summary(state: Any) -> dict[str, Any]:
    """Describe which credential a command would use, without secrets."""
    config = state.config
    account = config.accounts.get(state.account_name) if state.account_name else None
    source = state.credential_source
    labels = {
        "--token": "--token flag",
        "oauth": "OAuth sign-in (hoja auth login)",
        "config": "API key in config file",
    }
    info: dict[str, Any] = {
        "account": state.account_name or None,
        "source": source or None,
        "source_label": labels.get(source, f"API key from {source} (environment)" if source else "none"),
        "kind": "oauth" if source == "oauth" else ("api_key" if source else None),
        "api_url": state.base_url,
    }
    if source.startswith("$") or source == "--token":
        info["overrides"] = "environment/flag credentials take precedence over stored sign-ins"
    if account is not None:
        info["origin"] = account.origin
        if account.workspace:
            info["workspace"] = account.workspace.model_dump(exclude_none=True)
        elif account.org_name:
            info["workspace"] = {"name": account.org_name}
        if account.user:
            info["user"] = account.user.model_dump(exclude_none=True)
    session = state.session
    if session is not None:
        info["mcp_url"] = session.account.mcp_url
        info["store"] = session.account.credential_store
        try:
            rec = session.record() or {}
        except HojaError:
            rec = {}
        info["scopes"] = (rec.get("scope") or "").split() or None
        info["expires_at"] = rec.get("expires_at")
    return info


@auth_app.command("whoami")
def whoami() -> None:
    """Alias of `auth status`."""
    asyncio.run(_run_me())


async def _run_me() -> None:
    state = get_state()
    async with build_client(state) as client:
        viewer = await get_viewer(client)
        render(viewer, fmt=state.output, title="Viewer")


@auth_app.command("accounts")
def accounts_list() -> None:
    """List all configured accounts with default/workspace/current markers."""
    state = get_state()
    config = state.config
    workspace = resolve_workspace()
    workspace_account = (
        config.workspaces.get(str(workspace)) if workspace else None
    )

    account_rows: list[dict[str, Any]] = []
    for name, acc in config.accounts.items():
        markers: list[str] = []
        if name == config.defaults.default_account:
            markers.append("default")
        if name == workspace_account:
            markers.append("workspace")
        if name == state.account_name:
            markers.append("current")
        account_rows.append(
            {
                "name": name,
                "org_name": acc.org_name or "(unknown)",
                "token_source": (
                    "oauth"
                    if acc.credential == "oauth"
                    else ("config" if acc.token else f"${acc.token_env}")
                ),
                "origin": acc.origin or acc.base_url,
                "teams": acc.teams,
                "markers": markers,
            }
        )

    if state.output == OutputFormat.JSON:
        print(_json.dumps(account_rows, indent=2))
        return

    # Human output
    if not account_rows:
        typer.echo("No accounts configured.")
        typer.echo("Run: hoja auth login <workspace>")
        typer.echo("(For automation: hoja auth add <name> --token <api key>)")
        return

    typer.echo("Configured accounts:")
    for row in account_rows:
        marker_str = ""
        if row["markers"]:
            marker_str = " [" + ", ".join(row["markers"]) + "]"
        typer.echo(f"  • {row['name']}{marker_str}")
        if row["org_name"] != "(unknown)":
            typer.echo(f"    org: {row['org_name']}")
        typer.echo(f"    token: {row['token_source']}")
        if row["teams"]:
            typer.echo(f"    teams: {', '.join(row['teams'])}")
    if workspace:
        typer.echo(f"\nWorkspace: {workspace}")
    else:
        typer.echo("\nWorkspace: (none — not in a git repo)")


@auth_app.command("add")
def account_add(
    name: str = typer.Argument(..., help="Account name"),
    token: str = typer.Option(..., "--token", help="Hoja API token"),
    token_env: str = typer.Option(
        "HOJA_TOKEN", "--token-env", help="Environment variable name"
    ),
    api_url: Optional[str] = typer.Option(
        None,
        "--api-url",
        help="GraphQL endpoint this token belongs to, e.g. "
        "https://acme.hoja.work/graphql. Stored on the account. Falls back to "
        "$HOJA_API_URL.",
    ),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Verify token by calling the API"
    ),
    default: bool = typer.Option(
        False, "--default", help="Set as global default account"
    ),
    teams: Optional[str] = typer.Option(
        None,
        "--teams",
        help="Comma-separated team keys this account owns (e.g. 'SWA,ENG'). "
        "Enables automatic account selection when a command targets one of "
        "these teams.",
    ),
) -> None:
    """Add a new named account."""
    config = load_config()
    if name in config.accounts:
        raise UsageError(
            f"Account '{name}' already exists",
            hint="Use 'hoja auth remove {name}' first, or pick a different name.",
        )

    base_url = (api_url or "").strip() or None
    if base_url is not None:
        _check_api_url(base_url)
    # The endpoint a token is verified against must be one the person named.
    # Falling back to the client's built-in default sent a freshly pasted token
    # to a third-party API whenever no URL was configured.
    verify_url = resolve_base_url(base_url, AccountConfig(base_url=base_url))
    org_name: str | None = None
    if verify and verify_url is None:
        typer.echo(
            "Not verified: no API URL for this token. Pass --api-url "
            "https://<your-workspace>/graphql (or set $HOJA_API_URL) to check it.",
            err=True,
        )
    elif verify:
        try:
            org_name = asyncio.run(_fetch_org_name(token, verify_url))
        except Exception as e:
            typer.echo(f"Warning: could not verify token ({e})", err=True)

    team_keys = (
        [t.strip().upper() for t in teams.split(",") if t.strip()] if teams else []
    )
    was_empty = len(config.accounts) == 0
    config.accounts[name] = AccountConfig(
        # The shared default env vars are never persisted; only a distinct
        # per-account variable stores the token inline.
        token=token if token_env in ("HOJA_TOKEN", "LINEAR_TOKEN") else None,
        token_env=token_env,
        org_name=org_name,
        base_url=base_url,
        teams=team_keys,
    )
    if default or was_empty:
        config.defaults.default_account = name
    save_config(config)
    typer.echo(f"Added account '{name}'" + (f" ({org_name})" if org_name else ""))


def _check_api_url(url: str) -> None:
    """Refuse an endpoint that would send the token in clear text or nowhere."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    loopback = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UsageError(
            f"Invalid --api-url '{url}'",
            hint="Use the full URL including https://, e.g. https://acme.hoja.work/graphql.",
        )
    if parts.scheme == "http" and not loopback:
        raise UsageError(
            f"Refusing a plain-http --api-url for '{parts.hostname}'",
            hint="Tokens are only sent over https, except to a loopback hoja-serve.",
        )


async def _fetch_org_name(token: str, base_url: str) -> str | None:
    """Make a quick API call to get the organization name."""
    from hoja.graphql import queries
    from hoja.models.user import User

    client = GraphClient(token=token, base_url=base_url, timeout=10.0)
    async with client:
        viewer = await client.execute_as(User, queries.VIEWER, path=["viewer"], operation="Viewer")
        # viewer.organization may not exist on the User model; handle gracefully
        return getattr(viewer, "organization_name", None) or getattr(
            viewer, "organization", None
        )


@auth_app.command("switch")
def account_switch(
    name: str = typer.Argument(..., help="Account name to set as default"),
) -> None:
    """Set the global default account."""
    config = load_config()
    if name not in config.accounts:
        raise AuthError(
            f"Account '{name}' not found",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    config.defaults.default_account = name
    save_config(config)
    typer.echo(f"Default account set to '{name}'")


@auth_app.command("teams")
def account_teams(
    name: str = typer.Argument(..., help="Account name to edit"),
    teams: str = typer.Argument(
        ...,
        help="Comma-separated team keys this account owns (e.g. 'SWA,ENG'). "
        "Pass an empty string to clear.",
    ),
) -> None:
    """Set the team keys an account owns (for automatic account selection)."""
    config = load_config()
    if name not in config.accounts:
        raise AuthError(
            f"Account '{name}' not found",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    team_keys = [t.strip().upper() for t in teams.split(",") if t.strip()]
    config.accounts[name].teams = team_keys
    save_config(config)
    if team_keys:
        typer.echo(f"Account '{name}' now owns teams: {', '.join(team_keys)}")
    else:
        typer.echo(f"Cleared team ownership for account '{name}'")


@auth_app.command("remove")
def account_remove(
    name: str = typer.Argument(..., help="Account name to remove"),
) -> None:
    """Remove a named account."""
    config = load_config()
    if name not in config.accounts:
        raise AuthError(
            f"Account '{name}' not found",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    removed = config.accounts[name]
    if removed.credential == "oauth":
        _revoke_and_forget(name, removed)
    del config.accounts[name]
    # Clean up workspace mappings pointing to this account
    config.workspaces = {
        k: v for k, v in config.workspaces.items() if v != name
    }
    if config.defaults.default_account == name:
        config.defaults.default_account = next(iter(config.accounts), None)
    save_config(config)
    typer.echo(f"Removed account '{name}'")


@auth_app.command("workspace")
def auth_workspace() -> None:
    """Show current workspace detection and mapped account."""
    state = get_state()
    config = state.config
    workspace = resolve_workspace()

    result: dict[str, Any] = {
        "workspace": str(workspace) if workspace else None,
        "mapped_account": None,
        "default_account": config.defaults.default_account,
    }
    if workspace:
        mapped = config.workspaces.get(str(workspace))
        result["mapped_account"] = mapped

    if state.output == OutputFormat.JSON:
        print(_json.dumps(result, indent=2))
        return

    if workspace:
        typer.echo(f"Workspace: {workspace}")
        if mapped := result["mapped_account"]:
            typer.echo(f"Mapped account: {mapped}")
        else:
            typer.echo("No workspace mapping configured.")
            typer.echo(f"Default account: {config.defaults.default_account or '(none)'}")
    else:
        typer.echo("Not inside a git repository.")
        typer.echo(f"Default account: {config.defaults.default_account or '(none)'}")


# ---------------------------------------------------------------------------
# OAuth sign-in: login / logout / token
# ---------------------------------------------------------------------------

# Seams for tests: the HTTP client, the browser opener and the device-flow sleep.
_http_factory = oauth.default_http
_open_browser = oauth.open_browser
_sleep = time.sleep


def _err(message: str) -> None:
    typer.echo(message, err=True)


def _default_workspace(config: Any, account_name: str) -> str | None:
    acc = config.accounts.get(account_name) if account_name else None
    if acc is None:
        return None
    return _account_origin(acc)


def _account_origin(acc: AccountConfig) -> str | None:
    if acc.origin:
        return acc.origin
    return oauth.origin_of(acc.base_url) if acc.base_url else None


def _choose_account_name(config: Any, explicit: str | None, origin: str, tokens: oauth.TokenSet) -> str:
    if explicit:
        existing = config.accounts.get(explicit)
        if existing is not None and _account_origin(existing) not in (None, origin):
            raise UsageError(
                f"Account '{explicit}' belongs to {_account_origin(existing)}, not {origin}",
                hint="Pick another --name, or remove that account with `hoja auth remove`.",
            )
        return explicit
    for acc_name, acc in config.accounts.items():
        if acc.credential == "oauth" and acc.origin == origin:
            return str(acc_name)
    base = str(tokens.workspace.get("url_key") or urlsplit(origin).hostname or "default")
    candidate, n = base, 2
    while candidate in config.accounts and _account_origin(config.accounts[candidate]) not in (None, origin):
        candidate, n = f"{base}-{n}", n + 1
    return candidate


@auth_app.command("login")
def login(
    workspace: str | None = typer.Argument(
        None, help="Workspace address, e.g. acme.example.com (defaults to the current account's workspace)"
    ),
    device: bool = typer.Option(
        False, "--device", help="Sign in with a code on another device instead of a local browser"
    ),
    read_only: bool = typer.Option(False, "--read-only", help="Request read access only"),
    name: str | None = typer.Option(None, "--name", help="Account name to store the sign-in under"),
) -> None:
    """Sign in through the browser and store a refreshable token."""
    state = get_state()
    config = load_config()
    if not workspace:
        workspace = _default_workspace(config, state.account_name)
    if not workspace:
        if not sys.stdin.isatty():
            raise UsageError("Which workspace?", hint="Run `hoja auth login <workspace address>`.")
        workspace = typer.prompt("Workspace address (e.g. acme.example.com)")
    origin = oauth.normalize_origin(workspace or "")
    scope = oauth.READ_SCOPE if read_only else f"{oauth.READ_SCOPE} {oauth.WRITE_SCOPE}"

    with _http_factory() as http:
        meta = oauth.discover(origin, http)
        tokens: oauth.TokenSet | None = None
        use_device = device or oauth.is_headless()
        if use_device and not meta.device_authorization_endpoint and not device:
            use_device = False  # headless without device support: print the URL and wait
        if not use_device:
            tokens = oauth.loopback_login(meta, http, scope=scope, open_browser=_open_browser, echo=_err)
            if tokens is None:
                if not meta.device_authorization_endpoint:
                    raise AuthError(
                        "Could not open a browser",
                        hint="Open the URL above on this machine, or use an API key with `hoja auth add`.",
                    )
                _err("Could not open a browser; signing in with a code instead.\n")
        if tokens is None:
            tokens = oauth.device_login(meta, http, scope=scope, echo=_err, sleep=_sleep)

    account_name = _choose_account_name(config, name, origin, tokens)
    store_kind = save_new_credentials(account_name, make_record(tokens, meta))
    acc = config.accounts.get(account_name) or AccountConfig()
    if acc.token:
        _err(f"Replaced the API key stored in account '{account_name}' with this sign-in.")
    ws = WorkspaceInfo(**tokens.workspace) if tokens.workspace else None
    config.accounts[account_name] = acc.model_copy(
        update={
            "token": None,
            "credential": "oauth",
            "origin": origin,
            "base_url": oauth.graphql_url(origin),
            "mcp_url": oauth.mcp_url(origin),
            "credential_store": store_kind,
            "workspace": ws,
            "user": UserInfo(**tokens.user) if tokens.user else None,
            "org_name": (ws.name if ws and ws.name else acc.org_name),
        }
    )
    if not config.defaults.default_account or config.defaults.default_account not in config.accounts:
        config.defaults.default_account = account_name
    save_config(config)
    who = tokens.user.get("email") or tokens.user.get("name")
    where = (ws.name if ws and ws.name else None) or origin
    typer.echo(f"Signed in to {where}" + (f" as {who}" if who else "") + f" (account '{account_name}').")
    if state.credential_source.startswith("$"):
        _err(f"Note: {state.credential_source} is set and still takes precedence over this sign-in.")


def _revoke_and_forget(account_name: str, acc: AccountConfig) -> None:
    """Revoke the stored tokens at the server (best effort) and delete them."""
    try:
        rec = store_for(acc).load(account_name)
    except HojaError as e:
        _err(f"Warning: {e.message}")
        rec = None
    endpoint = (rec or {}).get("revocation_endpoint")
    if rec and rec.get("origin") == acc.origin and endpoint and oauth.origin_of(str(endpoint)) == acc.origin:
        try:
            with _http_factory() as http:
                for kind in ("refresh_token", "access_token"):
                    if rec.get(kind):
                        oauth.revoke(str(endpoint), str(rec[kind]), kind, http=http)
        except HojaError as e:
            _err(f"Warning: could not revoke the sign-in at {acc.origin} ({e.message}); deleted it locally.")
    elif rec:
        _err(f"Warning: {acc.origin} has no revocation endpoint; the tokens were deleted locally.")
    delete_credentials(account_name, acc)


@auth_app.command("logout")
def logout(
    name: str | None = typer.Argument(None, help="Account to sign out (defaults to the current account)"),
) -> None:
    """Revoke the sign-in at the server and delete the local tokens."""
    config = load_config()
    account_name = name or get_state().account_name
    acc = config.accounts.get(account_name) if account_name else None
    if acc is None:
        raise AuthError(
            f"Account '{account_name}' not found" if account_name else "No account to sign out",
            hint="Run 'hoja auth accounts' to see available accounts.",
        )
    if acc.credential != "oauth":
        raise UsageError(
            f"Account '{account_name}' uses an API key, not a sign-in",
            hint=f"Remove it with `hoja auth remove {account_name}`, and revoke the key in Settings.",
        )
    _revoke_and_forget(account_name, acc)
    typer.echo(f"Signed out of {acc.origin} (account '{account_name}').")


@auth_app.command("token")
def token_cmd(
    as_json: bool = typer.Option(False, "--json", help="Print credentials and endpoints as JSON"),
) -> None:
    """Print a fresh access token for the current account (for plugins and scripts)."""
    state = get_state()
    acc = state.config.accounts.get(state.account_name) if state.account_name else None
    expires_at: float | None = None
    mcp: str | None
    if state.session is not None:
        token = state.session.access_token()
        expires_at = (state.session.record() or {}).get("expires_at")
        api_url: str | None = state.base_url or state.session.base_url
        mcp = state.session.account.mcp_url or oauth.mcp_url(state.session.origin)
        kind = "oauth"
    elif state.token:
        token = str(state.token)
        api_url = state.base_url
        origin = oauth.origin_of(api_url) if api_url and api_url.rstrip("/").endswith("/graphql") else None
        mcp = oauth.mcp_url(origin) if origin else None
        kind = "api_key"
    else:
        raise state.auth_error or AuthError("Not signed in", hint="Run `hoja auth login <workspace>`.")
    if not as_json:
        sys.stdout.write(token + "\n")
        return
    workspace: dict[str, Any] = {}
    if acc is not None and acc.workspace is not None:
        workspace = acc.workspace.model_dump(exclude_none=True)
    elif acc is not None and acc.org_name:
        workspace = {"name": acc.org_name}
    payload = {
        "access_token": token,
        "token_type": "Bearer" if kind == "oauth" else "api_key",
        "expires_at": (
            datetime.fromtimestamp(float(expires_at), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if expires_at
            else None
        ),
        "api_url": api_url,
        "mcp_url": mcp,
        "workspace": workspace,
        "account": state.account_name or None,
        "source": state.credential_source,
    }
    sys.stdout.write(_json.dumps(payload) + "\n")
