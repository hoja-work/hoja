"""GitHub App user-to-server OAuth: authorize, exchange, refresh, revoke, and the error taxonomy.

The taxonomy matters because GitHub answers HTTP 200 with an `error` body for a wrong code, secret
or redirect, and 404 for an unknown client. Collapsing those into one message sends an operator
looking for the wrong fault (measured 2026-11-09, see the connector README).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from github.config import GitHubAppConfig
from github.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError
from github.oauth import (
    authorization_url,
    exchange_code,
    refresh_access_token,
    revoke_grant,
)

CLIENT_ID = "Iv23li4RW9Mh8RHVfCGi"
CALLBACK = "https://hoja.hoja.work/api/connectors/github/callback"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT_URL = f"https://api.github.com/applications/{CLIENT_ID}/grant"


def _config(**over: str) -> GitHubAppConfig:
    env = {
        "HOJA_GITHUB_CLIENT_ID": CLIENT_ID,
        "HOJA_GITHUB_CLIENT_SECRET": "client-secret-value",
        "HOJA_GITHUB_REDIRECT_URI": CALLBACK,
        "HOJA_GITHUB_APP_SLUG": "hoja-connector",
        "HOJA_GITHUB_APP_ID": "5688507",
    }
    env.update(over)
    return GitHubAppConfig.from_env(env)


def test_authorize_url_sends_no_scope_and_pins_the_tenant_callback() -> None:
    url = authorization_url(_config(), state="sealed-state")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [CALLBACK]
    assert query["state"] == ["sealed-state"]
    # A GitHub App takes no scopes; permissions live on the registration.
    assert "scope" not in query
    # No account creation from the consent screen.
    assert query["allow_signup"] == ["false"]


def test_authorize_url_can_target_an_allow_listed_tenant_callback() -> None:
    extra = "https://clover.hoja.work/api/connectors/github/callback"
    url = authorization_url(
        _config(HOJA_GITHUB_REDIRECT_URIS=extra), state="s", redirect_uri=extra
    )
    assert parse_qs(urlparse(url).query)["redirect_uri"] == [extra]


def test_config_accepts_a_comma_separated_allow_list_and_builds_the_install_url() -> None:
    extra = "https://dev.hoja.work/api/connectors/github/callback"
    config = _config(HOJA_GITHUB_REDIRECT_URIS=f"{extra},https://pmoell.hoja.work/api/connectors/github/callback")
    assert config.redirect_uri_for("https://dev.hoja.work") == extra
    assert config.redirect_uri_for("https://unknown.example") is None
    assert config.install_url() == "https://github.com/apps/hoja-connector/installations/new"


def test_config_rejects_a_redirect_that_is_not_an_https_callback() -> None:
    with pytest.raises(ConnectorConfigError):
        _config(HOJA_GITHUB_REDIRECT_URI="http://insecure.example/api/connectors/github/callback")
    with pytest.raises(ConnectorConfigError):
        _config(HOJA_GITHUB_REDIRECT_URI="https://hoja.example/other/path")


def test_config_rejects_a_client_id_that_is_not_a_github_app_id() -> None:
    with pytest.raises(ConnectorConfigError):
        _config(HOJA_GITHUB_CLIENT_ID="not-a-github-client")


def test_config_reports_missing_variables_without_printing_values() -> None:
    with pytest.raises(ConnectorConfigError) as caught:
        GitHubAppConfig.from_env({"HOJA_GITHUB_CLIENT_ID": CLIENT_ID})
    assert "HOJA_GITHUB_CLIENT_SECRET" in str(caught.value)
    assert "client-secret-value" not in str(caught.value)


@respx.mock
async def test_exchange_sends_json_accept_and_keeps_the_refresh_pair() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ghu_access",
                "expires_in": 28800,
                "refresh_token": "ghr_refresh",
                "refresh_token_expires_in": 15778476,
                "token_type": "bearer",
                "scope": "",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        token = await exchange_code(http, _config(), code="the-code")

    request = route.calls[0].request
    assert request.headers["accept"] == "application/json"
    body = parse_qs(request.content.decode())
    assert body["client_secret"] == ["client-secret-value"]
    assert body["code"] == ["the-code"]
    assert body["redirect_uri"] == [CALLBACK]
    assert token.access_token == "ghu_access"
    assert token.refresh_token == "ghr_refresh"
    assert token.expires_in == 28800
    assert token.refresh_token_expires_in == 15778476


@respx.mock
async def test_exchange_reports_a_bad_code_as_an_auth_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"error": "bad_verification_code", "error_description": "The code passed is incorrect or expired."}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as caught:
            await exchange_code(http, _config(), code="spent")
    assert "connect github again" in (caught.value.hint or "").lower()


@respx.mock
async def test_a_wrong_secret_is_a_deployment_fault_not_a_user_one() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"error": "incorrect_client_credentials", "error_description": "bad secret"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorConfigError):
            await exchange_code(http, _config(), code="code")


@respx.mock
async def test_an_unknown_client_is_not_mistaken_for_bad_credentials() -> None:
    """Measured: GitHub answers 404 {"error":"Not Found"} before validating the client."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(404, json={"error": "Not Found"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as caught:
            await exchange_code(http, _config(), code="code")
    assert not isinstance(caught.value, ConnectorConfigError)


@respx.mock
async def test_a_response_without_a_refresh_token_is_refused() -> None:
    """Expiring tokens are on; accepting this would create an account that dies in eight hours."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "ghu_only", "token_type": "bearer"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as caught:
            await exchange_code(http, _config(), code="code")
    assert "refresh token" in str(caught.value)


@respx.mock
async def test_refresh_posts_the_rotating_grant_and_returns_the_new_pair() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ghu_second",
                "expires_in": 28800,
                "refresh_token": "ghr_second",
                "refresh_token_expires_in": 15778476,
                "token_type": "bearer",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        token = await refresh_access_token(http, _config(), refresh_token="ghr_first")
    body = parse_qs(route.calls[0].request.content.decode())
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["ghr_first"]
    assert token.refresh_token == "ghr_second"


@respx.mock
async def test_a_rejected_refresh_is_an_auth_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"error": "bad_refresh_token"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError):
            await refresh_access_token(http, _config(), refresh_token="dead")


@respx.mock
async def test_revoke_calls_the_application_grant_endpoint_with_basic_auth() -> None:
    route = respx.delete(GRANT_URL).mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        assert await revoke_grant(http, _config(), access_token="ghu_access") is True
    request = route.calls[0].request
    assert request.headers["authorization"].startswith("Basic ")
    assert json.loads(request.content) == {"access_token": "ghu_access"}


@respx.mock
async def test_revoke_of_an_already_deleted_grant_is_success() -> None:
    respx.delete(GRANT_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    async with httpx.AsyncClient() as http:
        assert await revoke_grant(http, _config(), access_token="ghu_access") is True


@respx.mock
async def test_revoke_with_bad_client_credentials_raises_config_error() -> None:
    respx.delete(GRANT_URL).mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorConfigError):
            await revoke_grant(http, _config(), access_token="ghu_access")


@respx.mock
async def test_a_non_json_token_body_is_an_api_error_not_a_crash() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, text="access_token=x&foo=bar"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError):
            await exchange_code(http, _config(), code="code")
