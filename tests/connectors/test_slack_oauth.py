"""Slack OAuth: user_scope only, `authed_user.access_token`, and HTTP-200 error envelopes."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from slack.config import SlackOAuthConfig
from slack.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError
from slack.oauth import USER_READ_SCOPES, authorization_url, exchange_code

TOKEN_URL = "https://slack.com/api/oauth.v2.access"
CLIENT_ID = "12530628441030.12542913043347"


def _config() -> SlackOAuthConfig:
    return SlackOAuthConfig(
        client_id=CLIENT_ID,
        client_secret="0123456789abcdef0123456789abcdef",
        redirect_uri="https://hoja.example.test/api/connectors/slack/callback",
    )


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "HOJA_SLACK_CLIENT_ID": CLIENT_ID,
        "HOJA_SLACK_CLIENT_SECRET": "secret_test_value",
        "HOJA_SLACK_REDIRECT_URI": "https://hoja.example.test/api/connectors/slack/callback",
    }
    env.update(overrides)
    return env


def test_config_from_env_requires_the_three_oauth_keys() -> None:
    with pytest.raises(ConnectorConfigError) as excinfo:
        SlackOAuthConfig.from_env({"HOJA_SLACK_CLIENT_ID": CLIENT_ID})
    assert "HOJA_SLACK_CLIENT_SECRET" in str(excinfo.value)
    assert "HOJA_SLACK_REDIRECT_URI" in str(excinfo.value)


def test_config_rejects_a_client_id_that_is_not_slack_shaped() -> None:
    with pytest.raises(ConnectorConfigError) as excinfo:
        SlackOAuthConfig.from_env(_env(HOJA_SLACK_CLIENT_ID="not-a-slack-id"))
    assert "Slack client id" in str(excinfo.value)


def test_signing_secret_is_optional_and_validated_when_present() -> None:
    assert SlackOAuthConfig.from_env(_env()).signing_secret is None
    kept = SlackOAuthConfig.from_env(_env(HOJA_SLACK_SIGNING_SECRET="a" * 32))
    assert kept.signing_secret == "a" * 32
    with pytest.raises(ConnectorConfigError) as excinfo:
        SlackOAuthConfig.from_env(_env(HOJA_SLACK_SIGNING_SECRET="too-short"))
    assert "32 hex" in str(excinfo.value)


def test_config_repr_never_contains_a_secret() -> None:
    shown = repr(
        SlackOAuthConfig(
            client_id=CLIENT_ID,
            client_secret="secret_test_value",
            redirect_uri="https://hoja.example.test/api/connectors/slack/callback",
            signing_secret="b" * 32,
        )
    )
    assert "secret_test_value" not in shown
    assert "b" * 32 not in shown


def test_authorization_url_asks_for_user_scopes_only() -> None:
    url = authorization_url(_config(), state="sealed-state")
    # keep_blank_values: a user-scope-only app sends `scope=` with no value at all.
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert query["user_scope"] == [",".join(USER_READ_SCOPES)]
    # A user-scope-only app sends no bot scopes at all: `scope` is present and empty.
    assert query["scope"] == [""]
    assert query["state"] == ["sealed-state"]
    assert query["redirect_uri"] == [_config().redirect_uri]
    assert query["client_id"] == [CLIENT_ID]
    assert "code_challenge" not in query
    assert "0123456789abcdef0123456789abcdef" not in url
    granted = set(query["user_scope"][0].split(","))
    assert granted == {
        "channels:read",
        "channels:history",
        "groups:read",
        "groups:history",
        "users:read",
        "search:read",
    }
    assert not any(
        scope.startswith(("chat:write", "files:", "reactions:", "admin.")) for scope in granted
    )


@respx.mock
async def test_exchange_code_uses_basic_auth_and_reads_the_user_token() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "app_id": "A0CFYSV19A7",
                "authed_user": {
                    "id": "U0CFLJGCZ0W",
                    "scope": "channels:read,search:read",
                    "access_token": "xoxp-fixture",
                    "token_type": "user",
                },
                "team": {"id": "T0CFLJGCZ0W", "name": "Hoja"},
                "enterprise": None,
                "is_enterprise_install": False,
            },
        )
    )
    async with httpx.AsyncClient() as http:
        token = await exchange_code(http, _config(), code="code-1")
    request = route.calls.last.request
    # The client secret travels in HTTP Basic, never as a form field.
    assert request.headers["Authorization"].startswith("Basic ")
    assert b"client_secret" not in request.content
    assert b"code=code-1" in request.content
    assert b"redirect_uri" in request.content
    assert token.access_token == "xoxp-fixture"
    assert token.workspace == "Hoja"
    assert token.scopes == ["channels:read", "search:read"]


@respx.mock
async def test_workspace_falls_back_to_the_team_id_when_the_name_is_absent() -> None:
    """Slack omits `team.name` when the app has no `team:read` scope; the connector must still
    have a label to show in Settings."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "authed_user": {"id": "U1", "access_token": "xoxp-fixture"},
                "team": {"id": "T0CFLJGCZ0W"},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        token = await exchange_code(http, _config(), code="c")
    assert token.team_name is None
    assert token.workspace == "T0CFLJGCZ0W"


@respx.mock
async def test_http_200_with_ok_false_is_a_rejection_not_a_success() -> None:
    """Measured live: oauth.v2.access answers 200 for a bad code. A status check alone would
    parse this body as a token."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await exchange_code(http, _config(), code="code-1")
    assert "invalid_code" in str(excinfo.value)
    assert "0123456789abcdef0123456789abcdef" not in str(excinfo.value)


@respx.mock
async def test_a_bot_only_grant_is_refused() -> None:
    """If bot scopes are added to the app, Slack returns a top-level bot token instead. Using it
    would widen the grant past what the consent screen showed, so the exchange fails closed."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-bot",
                "token_type": "bot",
                "team": {"id": "T1", "name": "Hoja"},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await exchange_code(http, _config(), code="c")
    assert "no user token" in str(excinfo.value)
    assert "xoxb-bot" not in str(excinfo.value)


@respx.mock
async def test_a_non_xoxp_credential_is_refused() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "authed_user": {"id": "U1", "access_token": "xoxe.xoxp-rot"},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await exchange_code(http, _config(), code="c")
    assert "not a user token" in str(excinfo.value)
