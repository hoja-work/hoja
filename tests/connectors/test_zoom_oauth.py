"""Zoom OAuth: the authorize URL, the rotating refresh grant, and an honest error split."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from zoom import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError, ZoomOAuthConfig
from zoom.oauth import (
    READ_SCOPES,
    authorization_url,
    exchange_code,
    refresh_access_token,
    revoke_token,
)

TOKEN_URL = "https://zoom.us/oauth/token"
REVOKE_URL = "https://zoom.us/oauth/revoke"
CLIENT_ID = "5kXHQtScS8WhD2lgmuedKQ"
REDIRECT = "https://hoja.hoja.work/api/connectors/zoom/callback"


def _env(**overrides):
    env = {
        "HOJA_ZOOM_CLIENT_ID": CLIENT_ID,
        "HOJA_ZOOM_CLIENT_SECRET": "client-secret-test-value",
        "HOJA_ZOOM_REDIRECT_URI": REDIRECT,
    }
    env.update(overrides)
    return env


def _config(**overrides) -> ZoomOAuthConfig:
    return ZoomOAuthConfig.from_env(_env(**overrides))


def _token_json(**overrides):
    payload = {
        "access_token": "access-1",
        "token_type": "bearer",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scope": " ".join(READ_SCOPES),
        "api_url": "https://api.zoom.us",
    }
    payload.update(overrides)
    return payload


# --- configuration -------------------------------------------------------------------------


def test_missing_variables_are_named() -> None:
    with pytest.raises(ConnectorConfigError) as excinfo:
        ZoomOAuthConfig.from_env({"HOJA_ZOOM_CLIENT_ID": CLIENT_ID})
    assert "HOJA_ZOOM_CLIENT_SECRET" in str(excinfo.value)
    assert "HOJA_ZOOM_REDIRECT_URI" in str(excinfo.value)


def test_a_client_id_that_cannot_be_a_zoom_id_is_refused() -> None:
    with pytest.raises(ConnectorConfigError):
        ZoomOAuthConfig.from_env(_env(HOJA_ZOOM_CLIENT_ID="not a zoom id"))


def test_a_plain_http_redirect_is_refused() -> None:
    with pytest.raises(ConnectorConfigError):
        ZoomOAuthConfig.from_env(
            _env(HOJA_ZOOM_REDIRECT_URI="http://hoja.hoja.work/api/connectors/zoom/callback")
        )


def test_a_redirect_without_the_callback_path_is_refused() -> None:
    with pytest.raises(ConnectorConfigError):
        ZoomOAuthConfig.from_env(_env(HOJA_ZOOM_REDIRECT_URI="https://hoja.hoja.work/elsewhere"))


def test_the_allow_list_holds_the_canonical_callback_and_every_tenant() -> None:
    config = _config(
        HOJA_ZOOM_REDIRECT_URIS=(
            "https://clover.hoja.work/api/connectors/zoom/callback,"
            " https://dev.hoja.work/api/connectors/zoom/callback "
            "https://pmoell.hoja.work/api/connectors/zoom/callback"
        )
    )
    assert config.redirect_uri_for("https://hoja.hoja.work") == REDIRECT
    assert (
        config.redirect_uri_for("https://clover.hoja.work")
        == "https://clover.hoja.work/api/connectors/zoom/callback"
    )
    assert (
        config.redirect_uri_for("https://dev.hoja.work")
        == "https://dev.hoja.work/api/connectors/zoom/callback"
    )
    assert (
        config.redirect_uri_for("https://pmoell.hoja.work")
        == "https://pmoell.hoja.work/api/connectors/zoom/callback"
    )


def test_an_unregistered_tenant_gets_no_redirect_uri() -> None:
    """This is the check that stops the flow before Zoom, instead of at Zoom."""
    config = _config()
    assert config.redirect_uri_for("https://ghost.hoja.work") is None
    assert config.redirect_uri_for("https://evil.example.com") is None


def test_the_config_never_reprs_the_secret() -> None:
    assert "client-secret-test-value" not in repr(_config())


def test_the_api_base_defaults_to_the_global_cluster_and_is_overridable() -> None:
    assert _config().api_base == "https://api.zoom.us/v2"
    assert (
        _config(HOJA_ZOOM_API_BASE="https://eu01api.zoom.us/v2").api_base
        == "https://eu01api.zoom.us/v2"
    )


# --- authorize -----------------------------------------------------------------------------


def test_the_authorize_url_carries_the_state_and_no_pkce() -> None:
    url = authorization_url(_config(), state="sealed-state", redirect_uri=REDIRECT)
    assert url.startswith("https://zoom.us/oauth/authorize?")
    assert "response_type=code" in url
    assert f"client_id={CLIENT_ID}" in url
    assert "state=sealed-state" in url
    assert "redirect_uri=https%3A%2F%2Fhoja.hoja.work%2Fapi%2Fconnectors%2Fzoom%2Fcallback" in url
    # The provisioned app has PKCE disabled; a challenge it did not expect is rejected.
    assert "code_challenge" not in url


def test_the_per_tenant_redirect_overrides_the_configured_one() -> None:
    other = "https://clover.hoja.work/api/connectors/zoom/callback"
    url = authorization_url(_config(), state="s", redirect_uri=other)
    assert "clover.hoja.work" in url


# --- exchange and refresh ------------------------------------------------------------------


@respx.mock
async def test_the_exchange_sends_basic_auth_and_the_same_redirect_uri() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=_token_json()))
    async with httpx.AsyncClient() as http:
        token = await exchange_code(http, _config(), code="code-1", redirect_uri=REDIRECT)
    request = route.calls[0].request
    expected = base64.b64encode(f"{CLIENT_ID}:client-secret-test-value".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    body = request.content.decode()
    assert "grant_type=authorization_code" in body
    assert "code=code-1" in body
    assert "redirect_uri=https%3A%2F%2Fhoja.hoja.work" in body
    assert token.access_token == "access-1"
    assert token.refresh_token == "refresh-1"
    assert token.expires_in == 3600
    assert token.scopes == list(READ_SCOPES)
    assert token.api_url == "https://api.zoom.us"


@respx.mock
async def test_the_refresh_uses_the_refresh_token_grant() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=_token_json(access_token="a2", refresh_token="r2"))
    )
    async with httpx.AsyncClient() as http:
        token = await refresh_access_token(http, _config(), refresh_token="r1")
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=r1" in body
    assert token.access_token == "a2"
    assert token.refresh_token == "r2"


@respx.mock
async def test_a_bad_secret_is_a_deployment_fault_not_a_reconnect() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_client"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorConfigError) as excinfo:
            await exchange_code(http, _config(), code="c")
    assert "secret" in (excinfo.value.hint or "").lower()


@respx.mock
async def test_a_dead_grant_asks_the_user_to_reconnect() -> None:
    """Zoom's own shape: {"error": "invalid_grant", "reason": "Invalid authorization code"}."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "reason": "Invalid authorization code"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await exchange_code(http, _config(), code="c")
    assert "Invalid authorization code" in str(excinfo.value)
    assert "connect zoom again" in (excinfo.value.hint or "").lower()


@respx.mock
async def test_a_response_without_a_refresh_token_is_refused() -> None:
    """Without it the grant dies in an hour with no way to renew: accepting it would create a
    silently expiring account."""
    payload = _token_json()
    payload.pop("refresh_token")
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=payload))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError):
            await exchange_code(http, _config(), code="c")


@respx.mock
async def test_a_non_https_api_url_is_not_kept() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=_token_json(api_url="http://insecure.example"))
    )
    async with httpx.AsyncClient() as http:
        token = await exchange_code(http, _config(), code="c")
    assert token.api_url is None


@respx.mock
async def test_rate_limiting_surfaces_retry_after() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "42"}, json={})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await exchange_code(http, _config(), code="c")
    assert "42" in (excinfo.value.hint or "")


# --- revoke --------------------------------------------------------------------------------


@respx.mock
async def test_revoke_posts_the_token_with_basic_auth() -> None:
    route = respx.post(REVOKE_URL).mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    async with httpx.AsyncClient() as http:
        assert await revoke_token(http, _config(), token="refresh-1") is True
    request = route.calls[0].request
    assert request.headers["authorization"].startswith("Basic ")
    assert "token=refresh-1" in request.content.decode()


@respx.mock
async def test_an_already_invalid_token_counts_as_revoked() -> None:
    respx.post(REVOKE_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_request"})
    )
    async with httpx.AsyncClient() as http:
        assert await revoke_token(http, _config(), token="t") is True


@respx.mock
async def test_a_rejected_client_during_revocation_is_a_deployment_fault() -> None:
    respx.post(REVOKE_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorConfigError):
            await revoke_token(http, _config(), token="t")


@respx.mock
async def test_a_provider_failure_during_revocation_is_reported() -> None:
    respx.post(REVOKE_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError):
            await revoke_token(http, _config(), token="t")
