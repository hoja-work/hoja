"""Calendly OAuth: mandatory PKCE, rotating refresh tokens, and the client-vs-grant error split."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from calendly.config import CalendlyOAuthConfig
from calendly.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError
from calendly.oauth import (
    READ_SCOPES,
    authorization_url,
    exchange_code,
    pkce_pair,
    refresh_access_token,
    revoke_token,
    s256,
)

TOKEN_URL = "https://calendly.com/oauth/token"
REVOKE_URL = "https://calendly.com/oauth/revoke"
CLIENT_ID = "v_vI3AB0wpc6kfKia09I8HCLzqVIVm47KEtfdUGvcrE"
REDIRECT = "https://hoja.example.test/api/connectors/calendly/callback"


def _config() -> CalendlyOAuthConfig:
    return CalendlyOAuthConfig(
        client_id=CLIENT_ID, client_secret="secret_test_value", redirect_uri=REDIRECT
    )


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "HOJA_CALENDLY_CLIENT_ID": CLIENT_ID,
        "HOJA_CALENDLY_CLIENT_SECRET": "secret_test_value",
        "HOJA_CALENDLY_REDIRECT_URI": REDIRECT,
    }
    env.update(overrides)
    return env


def _token_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "token_type": "Bearer",
        "access_token": "access-fixture",
        "refresh_token": "refresh-fixture-1",
        "expires_in": 7200,
        "scope": "users:read event_types:read scheduled_events:read",
    }
    body.update(overrides)
    return body


# --- configuration -------------------------------------------------------------------------


def test_config_requires_the_three_oauth_values() -> None:
    with pytest.raises(ConnectorConfigError) as excinfo:
        CalendlyOAuthConfig.from_env({"HOJA_CALENDLY_CLIENT_ID": CLIENT_ID})
    assert "HOJA_CALENDLY_CLIENT_SECRET" in str(excinfo.value)
    assert "HOJA_CALENDLY_REDIRECT_URI" in str(excinfo.value)


def test_config_rejects_a_client_id_that_cannot_be_calendly() -> None:
    with pytest.raises(ConnectorConfigError):
        CalendlyOAuthConfig.from_env(_env(HOJA_CALENDLY_CLIENT_ID="not a calendly id"))


def test_config_rejects_a_plain_http_redirect() -> None:
    # Calendly compares the redirect byte for byte at both the authorize and token endpoints, so
    # a typo here is a deployment that can never complete a connection.
    with pytest.raises(ConnectorConfigError) as excinfo:
        CalendlyOAuthConfig.from_env(
            _env(HOJA_CALENDLY_REDIRECT_URI="http://hoja.example.test/callback")
        )
    assert "https" in str(excinfo.value)


def test_config_repr_never_shows_the_secret_or_signing_key() -> None:
    shown = repr(
        CalendlyOAuthConfig(
            client_id=CLIENT_ID,
            client_secret="secret_test_value",
            redirect_uri=REDIRECT,
            webhook_signing_key="signing_key_value",
        )
    )
    assert "secret_test_value" not in shown
    assert "signing_key_value" not in shown


# --- PKCE ----------------------------------------------------------------------------------


def test_pkce_challenge_matches_the_rfc_7636_test_vector() -> None:
    """An outside document, not our own implementation, says this is correct."""
    assert (
        s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_pkce_pair_is_43_characters_and_consistent() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert challenge == s256(verifier)
    assert set(verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert "=" not in verifier and "=" not in challenge


def test_authorize_url_sends_s256_and_only_read_scopes() -> None:
    verifier, challenge = pkce_pair()
    url = authorization_url(_config(), state="sealed-state", code_challenge=challenge)
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://calendly.com/oauth/authorize?")
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge]
    assert query["state"] == ["sealed-state"]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["client_id"] == [CLIENT_ID]
    assert query["scope"] == [" ".join(READ_SCOPES)]
    # Least privilege: no write scope, and no availability scope for a toolset that never reads it.
    assert set(READ_SCOPES) == {"users:read", "event_types:read", "scheduled_events:read"}
    assert "secret_test_value" not in url
    assert verifier not in url


# --- code exchange -------------------------------------------------------------------------


@respx.mock
async def test_exchange_sends_basic_auth_verifier_and_redirect() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=_token_body())
    )
    async with httpx.AsyncClient() as http:
        token = await exchange_code(
            http, _config(), code="code-1", code_verifier="verifier-value"
        )
    request = route.calls.last.request
    raw = request.headers["authorization"].removeprefix("Basic ")
    assert base64.b64decode(raw).decode() == f"{CLIENT_ID}:secret_test_value"
    body = parse_qs(request.content.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["code-1"]
    assert body["code_verifier"] == ["verifier-value"]
    assert body["redirect_uri"] == [REDIRECT]
    assert token.access_token == "access-fixture"
    assert token.refresh_token == "refresh-fixture-1"
    assert token.expires_in == 7200
    assert token.scopes == ["users:read", "event_types:read", "scheduled_events:read"]


@respx.mock
async def test_exchange_sends_the_overridden_redirect_uri() -> None:
    broker = "https://api.hoja.work/api/connectors/calendly/callback"
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=_token_body()))
    async with httpx.AsyncClient() as http:
        await exchange_code(http, _config(), code="c", code_verifier="v", redirect_uri=broker)
    assert parse_qs(route.calls.last.request.content.decode())["redirect_uri"] == [broker]


@respx.mock
async def test_exchange_refuses_a_response_without_a_refresh_token() -> None:
    """Rotation is on: a grant with no refresh token dies in two hours with no way to renew it."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=_token_body(refresh_token=None))
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as excinfo:
            await exchange_code(http, _config(), code="c", code_verifier="v")
    assert "refresh token" in str(excinfo.value).lower()


@respx.mock
async def test_a_bad_client_secret_is_a_deployment_fault_not_a_reconnect() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            401, json={"error": "invalid_client", "error_description": "bad secret"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorConfigError) as excinfo:
            await exchange_code(http, _config(), code="c", code_verifier="v")
    assert "client secret" in (excinfo.value.hint or "").lower()


@respx.mock
async def test_an_expired_or_used_code_is_an_auth_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "code expired"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await exchange_code(http, _config(), code="c", code_verifier="v")
    assert "invalid_grant" in str(excinfo.value)


@respx.mock
async def test_missing_verifier_is_reported_as_a_grant_error() -> None:
    """The live provider's exact refusal when PKCE is omitted, shaped as Calendly sends it."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_request",
                "error_description": "Missing required parameter: code_verifier.",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await exchange_code(http, _config(), code="c", code_verifier="")
    assert "code_verifier" in str(excinfo.value)


# --- refresh -------------------------------------------------------------------------------


@respx.mock
async def test_refresh_returns_the_rotated_token_pair() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json=_token_body(access_token="access-2", refresh_token="refresh-2")
        )
    )
    async with httpx.AsyncClient() as http:
        token = await refresh_access_token(http, _config(), refresh_token="refresh-1")
    body = parse_qs(route.calls.last.request.content.decode())
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["refresh-1"]
    assert token.access_token == "access-2"
    assert token.refresh_token == "refresh-2"
    assert token.refresh_token != "refresh-1"


@respx.mock
async def test_a_replayed_refresh_token_is_an_auth_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError):
            await refresh_access_token(http, _config(), refresh_token="already-used")


# --- revocation ----------------------------------------------------------------------------


@respx.mock
async def test_revoke_posts_the_refresh_token_with_basic_auth() -> None:
    route = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        assert await revoke_token(http, _config(), token="refresh-1") is True
    request = route.calls.last.request
    body = parse_qs(request.content.decode())
    # RFC 7009: revoking the refresh token ends the whole grant.
    assert body["token"] == ["refresh-1"]
    assert body["token_type_hint"] == ["refresh_token"]
    raw = request.headers["authorization"].removeprefix("Basic ")
    assert base64.b64decode(raw).decode().startswith(CLIENT_ID)


@respx.mock
async def test_revoking_an_already_invalid_token_counts_as_revoked() -> None:
    respx.post(REVOKE_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_request"})
    )
    async with httpx.AsyncClient() as http:
        assert await revoke_token(http, _config(), token="gone") is True


@respx.mock
async def test_revoke_with_a_bad_client_raises_a_config_error() -> None:
    respx.post(REVOKE_URL).mock(
        return_value=httpx.Response(403, json={"error": "unauthorized_client"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorConfigError):
            await revoke_token(http, _config(), token="refresh-1")


def test_an_explicit_redirect_overrides_the_configured_one_on_both_legs() -> None:
    """A multi-tenant deployment registers one broker callback, not one per tenant. Calendly
    compares the redirect byte for byte at the token endpoint, so both legs must carry it."""
    broker = "https://api.hoja.work/api/connectors/calendly/callback"
    _verifier, challenge = pkce_pair()
    url = authorization_url(
        _config(), state="s", code_challenge=challenge, redirect_uri=broker
    )
    assert parse_qs(urlparse(url).query)["redirect_uri"] == [broker]
