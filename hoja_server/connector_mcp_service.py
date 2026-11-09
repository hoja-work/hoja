"""The process that answers on `HOJA_CONNECTOR_MCP_URL`.

`connector_binding_for` hands the agent two things: this URL and a server-signed principal.
Until something serves that URL the toolset can never be called, so the connector is only
half-integrated. This module is that something.

Identity comes only from the signed principal header (each connector's `mcp_server` verifies it);
no tool accepts a user id, so one user's Gmail or Notion workspace cannot be read by another.
Grants are resolved from `connector_account` for the principal's own organization. Google access
tokens are refreshed from the stored refresh token at call time; Notion and Slack tokens are long-lived.
The connector packages stay free of Hoja's database; this adapter is the only place they meet.

One server serves every provider (`google_workspace.mcp_server.build_server` plus
`notion.mcp_server.register_tools` and `slack.mcp_server.register_tools`), because the agent
runtime holds one toolset URL per turn.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from google_workspace.config import GoogleOAuthConfig
from google_workspace.errors import ConnectorConfigError
from google_workspace.principal import SECRET_ENV, Principal
from google_workspace.tools import AccountGrant
from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine

from hoja_server.connectors_api import (
    CALENDLY_PROVIDER,
    GITHUB_PROVIDER,
    GRAFANA_PROVIDER,
    NOTION_PROVIDER,
    PROVIDER,
    SENTRY_PROVIDER,
    SLACK_PROVIDER,
    ZOOM_PROVIDER,
)
from hoja_server.db import connector_account
from hoja_server.secrets_box import SecretBoxError, decrypt_secret

log = logging.getLogger("hoja_server.connector_mcp_service")

DEFAULT_PATH = "/mcp"


def _active_rows(engine: Engine, principal: Principal, account: str, provider: str):
    """Active accounts this principal may use, narrowed to `account` (email or id) when given."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(connector_account)
            .where(
                and_(
                    connector_account.c.organization_id == principal.org_id,
                    connector_account.c.provider == provider,
                    connector_account.c.status == "active",
                    or_(
                        connector_account.c.owner_user_id == principal.user_id,
                        connector_account.c.owner_user_id.is_(None),
                    ),
                )
            )
            .order_by(connector_account.c.created_at)
        ).mappings().all()
    wanted = (account or "").strip()
    if not wanted:
        return rows
    lowered = wanted.lower()
    return [row for row in rows if row["external_account"].lower() == lowered or row["id"] == wanted]


def _token_for(
    http: httpx.AsyncClient, config: GoogleOAuthConfig, refresh_token: str
) -> Callable[[], Awaitable[str]]:
    async def access_token() -> str:
        from google_workspace.oauth import refresh_access_token

        tokens = await refresh_access_token(http, config, refresh_token=refresh_token)
        return tokens.access_token

    return access_token


def make_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient, config: GoogleOAuthConfig
) -> Callable[[Principal, str], Awaitable[list[AccountGrant]]]:
    """Resolve the grants a principal may use. A row that cannot be decrypted is skipped, not fatal:
    one corrupt credential must not hide a user's other accounts."""

    async def resolve_grants(principal: Principal, account: str) -> list[AccountGrant]:
        grants: list[AccountGrant] = []
        for row in _active_rows(engine, principal, account, PROVIDER):
            try:
                refresh_token = decrypt_secret(row["encrypted_refresh_token"])
            except (SecretBoxError, ValueError):
                log.warning("connector account %s has an unreadable token; skipping", row["id"])
                continue
            if not refresh_token:
                continue
            grants.append(
                AccountGrant(
                    account_id=row["id"],
                    external_account=row["external_account"],
                    access_token=_token_for(http, config, refresh_token),
                )
            )
        return grants

    return resolve_grants


def _long_lived_grants(
    engine: Engine, provider: str, make_grant: Callable[..., Any]
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Grants for providers whose stored credential is the token itself (no refresh flow).
    Notion and Slack both work this way; Google does not and keeps `make_grant_resolver`."""

    async def resolve_grants(principal: Principal, account: str) -> list[Any]:
        grants: list[Any] = []
        for row in _active_rows(engine, principal, account, provider):
            try:
                token = decrypt_secret(row["encrypted_refresh_token"])
            except (SecretBoxError, ValueError):
                log.warning("%s account %s has an unreadable token; skipping", provider, row["id"])
                continue
            if not token:
                continue

            async def access_token(value: str = token) -> str:
                return value

            grants.append(make_grant(row["id"], row["external_account"], access_token))
        return grants

    return resolve_grants


def make_notion_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve Notion grants. Notion tokens are long-lived and stored sealed; no refresh flow."""
    from notion.tools import AccountGrant as NotionGrant

    return _long_lived_grants(engine, NOTION_PROVIDER, NotionGrant)


def make_slack_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve Slack grants. The `xoxp-` user token is long-lived (rotation is off), stored
    sealed, and a revoked token surfaces as a tool error asking the user to reconnect."""
    from slack.tools import AccountGrant as SlackGrant

    return _long_lived_grants(engine, SLACK_PROVIDER, SlackGrant)


def make_calendly_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve Calendly grants through the rotating-token store.

    Calendly's access token expires in two hours and its refresh token is single-use, so the
    grant cannot be a captured string like Slack's: each account gets a live token source that
    refreshes under a per-account lock. A deployment without Calendly credentials still starts;
    it simply reports no Calendly accounts instead of failing the whole service.
    """
    from calendly import CalendlyOAuthConfig
    from calendly import ConnectorConfigError as CalendlyConfigError
    from calendly.tools import AccountGrant as CalendlyGrant

    from hoja_server.calendly_tokens import CalendlyTokenStore

    try:
        config = CalendlyOAuthConfig.from_env()
    except CalendlyConfigError:
        log.info("Calendly is not configured on this deployment; its tools resolve no accounts")

        async def unavailable(_principal: Principal, _account: str) -> list[Any]:
            return []

        return unavailable

    store = CalendlyTokenStore(engine, http, config)

    async def resolve_grants(principal: Principal, account: str) -> list[Any]:
        grants: list[Any] = []
        for row in _active_rows(engine, principal, account, CALENDLY_PROVIDER):
            if not row["encrypted_refresh_token"]:
                continue
            grants.append(
                CalendlyGrant(
                    account_id=row["id"],
                    external_account=row["external_account"],
                    tokens=store.source(row["id"]),
                )
            )
        return grants

    return resolve_grants


def make_sentry_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve Sentry grants through the eight-hour token store.

    Sentry's token lives eight hours and is refreshed with a JWT assertion signed by the client
    secret, so the grant cannot be a captured string like Slack's: each account gets a live token
    source that refreshes under a per-account lock. A deployment without Sentry credentials still
    starts; it simply reports no Sentry accounts instead of failing the whole service.
    """
    from sentry import ConnectorConfigError as SentryConfigError
    from sentry import SentryOAuthConfig
    from sentry.tools import AccountGrant as SentryGrant

    from hoja_server.sentry_tokens import SentryTokenStore

    try:
        config = SentryOAuthConfig.from_env()
    except SentryConfigError:
        log.info("Sentry is not configured on this deployment; its tools resolve no accounts")

        async def unavailable(_principal: Principal, _account: str) -> list[Any]:
            return []

        return unavailable

    store = SentryTokenStore(engine, http, config)

    async def resolve_grants(principal: Principal, account: str) -> list[Any]:
        grants: list[Any] = []
        for row in _active_rows(engine, principal, account, SENTRY_PROVIDER):
            if not row["external_id"] or not row["encrypted_refresh_token"]:
                continue
            grants.append(
                SentryGrant(
                    account_id=row["id"],
                    external_account=row["external_account"],
                    tokens=store.source(row["id"]),
                )
            )
        return grants

    return resolve_grants


def make_zoom_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve Zoom grants through the one-hour token store.

    Zoom's access token lives 3600 s and its refresh token is single-use, so the grant cannot be a
    captured string like Slack's: each account gets a live token source that refreshes under a
    per-account lock. A deployment without Zoom credentials still starts; it simply reports no
    Zoom accounts instead of failing the whole service.
    """
    from zoom import ConnectorConfigError as ZoomConfigError
    from zoom import ZoomOAuthConfig
    from zoom.tools import AccountGrant as ZoomGrant

    from hoja_server.zoom_tokens import ZoomTokenStore

    try:
        config = ZoomOAuthConfig.from_env()
    except ZoomConfigError:
        log.info("Zoom is not configured on this deployment; its tools resolve no accounts")

        async def unavailable(_principal: Principal, _account: str) -> list[Any]:
            return []

        return unavailable

    store = ZoomTokenStore(engine, http, config)

    async def resolve_grants(principal: Principal, account: str) -> list[Any]:
        grants: list[Any] = []
        for row in _active_rows(engine, principal, account, ZOOM_PROVIDER):
            if not row["encrypted_refresh_token"]:
                continue
            grants.append(
                ZoomGrant(
                    account_id=row["id"],
                    external_account=row["external_account"],
                    tokens=store.source(row["id"]),
                )
            )
        return grants

    return resolve_grants


def make_grafana_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve Grafana grants. The token is long-lived and stored sealed; each row also carries the
    customer's instance origin, which the package re-validates and pins on every request."""
    from grafana.tools import AccountGrant as GrafanaGrant

    async def resolve_grants(principal: Principal, account: str) -> list[Any]:
        grants: list[Any] = []
        for row in _active_rows(engine, principal, account, GRAFANA_PROVIDER):
            if not row["encrypted_refresh_token"] or not row["external_url"]:
                continue
            try:
                token = decrypt_secret(row["encrypted_refresh_token"])
            except (SecretBoxError, ValueError):
                log.warning("grafana account %s has an unreadable token; skipping", row["id"])
                continue
            if not token:
                continue

            async def access_token(value: str = token) -> str:
                return value

            grants.append(
                GrafanaGrant(
                    account_id=row["id"],
                    external_account=row["external_account"],
                    url=row["external_url"],
                    tokens=_StaticTokenSource(access_token),
                )
            )
        return grants

    return resolve_grants


class _StaticTokenSource:
    """A long-lived token exposed as the source protocol the Grafana package expects."""

    def __init__(self, access_token: Callable[[], Awaitable[str]]) -> None:
        self._access_token = access_token

    async def access_token(self) -> str:
        return await self._access_token()

    async def invalidate(self) -> None:
        return None


def make_github_grant_resolver(
    engine: Engine, *, http: httpx.AsyncClient
) -> Callable[[Principal, str], Awaitable[list[Any]]]:
    """Resolve GitHub grants through the eight-hour token store.

    A GitHub App user token expires (8 h measured) and its refresh token rotates, so the grant
    cannot be a captured string like Slack's: each account gets a live token source that refreshes
    under a per-account lock. A deployment without GitHub credentials still starts; it simply
    reports no GitHub accounts instead of failing the whole service.
    """
    from github import ConnectorConfigError as GitHubConfigError
    from github import GitHubAppConfig
    from github.tools import AccountGrant as GitHubGrant

    from hoja_server.github_tokens import GitHubTokenStore

    try:
        config = GitHubAppConfig.from_env()
    except GitHubConfigError:
        log.info("GitHub is not configured on this deployment; its tools resolve no accounts")

        async def unavailable(_principal: Principal, _account: str) -> list[Any]:
            return []

        return unavailable

    store = GitHubTokenStore(engine, http, config)

    async def resolve_grants(principal: Principal, account: str) -> list[Any]:
        grants: list[Any] = []
        for row in _active_rows(engine, principal, account, GITHUB_PROVIDER):
            if not row["encrypted_refresh_token"]:
                continue
            grants.append(
                GitHubGrant(
                    account_id=row["id"],
                    external_account=row["external_account"],
                    tokens=store.source(row["id"]),
                )
            )
        return grants

    return resolve_grants


def build_connector_mcp_app(
    engine: Engine,
    *,
    http: httpx.AsyncClient | None = None,
    config: GoogleOAuthConfig | None = None,
    signing_key: str | None = None,
    public_host: str | None = None,
    path: str = DEFAULT_PATH,
) -> Any:
    """Build the ASGI app. Fails closed: without a signing key no principal can be verified, so the
    service refuses to start rather than serving tools nobody can authenticate to."""
    from google_workspace.mcp_server import build_server

    key = (signing_key if signing_key is not None else os.environ.get(SECRET_ENV, "")).strip()
    if not key:
        raise ConnectorConfigError(
            f"{SECRET_ENV} must be set to serve the connector.",
            hint="Use the same value on this service and on the Hoja server that signs principals.",
        )
    client = http or httpx.AsyncClient(timeout=20)
    options: dict[str, Any] = {"streamable_http_path": path, "stateless_http": True}
    if public_host:
        from mcp.server.transport_security import TransportSecuritySettings

        options["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host.strip(), public_host.strip() + ":*"],
        )
    server = build_server(
        signing_key=key,
        resolve_grants=make_grant_resolver(
            engine, http=client, config=config or GoogleOAuthConfig.from_env()
        ),
        http=client,
    )
    from notion.mcp_server import register_tools as register_notion_tools

    register_notion_tools(
        server,
        signing_key=key,
        resolve_grants=make_notion_grant_resolver(engine, http=client),
        http=client,
    )
    from slack.mcp_server import register_tools as register_slack_tools

    register_slack_tools(
        server,
        signing_key=key,
        resolve_grants=make_slack_grant_resolver(engine, http=client),
        http=client,
    )
    from calendly.mcp_server import register_tools as register_calendly_tools

    register_calendly_tools(
        server,
        signing_key=key,
        resolve_grants=make_calendly_grant_resolver(engine, http=client),
        http=client,
    )
    from sentry.mcp_server import register_tools as register_sentry_tools

    register_sentry_tools(
        server,
        signing_key=key,
        resolve_grants=make_sentry_grant_resolver(engine, http=client),
        http=client,
    )
    from zoom.mcp_server import register_tools as register_zoom_tools

    register_zoom_tools(
        server,
        signing_key=key,
        resolve_grants=make_zoom_grant_resolver(engine, http=client),
        http=client,
    )
    from grafana.mcp_server import register_tools as register_grafana_tools

    register_grafana_tools(
        server,
        signing_key=key,
        resolve_grants=make_grafana_grant_resolver(engine, http=client),
        http=client,
    )
    from github.mcp_server import register_tools as register_github_tools

    register_github_tools(
        server,
        signing_key=key,
        resolve_grants=make_github_grant_resolver(engine, http=client),
        http=client,
    )
    return server.streamable_http_app(**options)
