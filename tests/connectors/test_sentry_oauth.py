"""Sentry installation OAuth: the documented exchange, the JWT-bearer refresh, and the errors
that must not be confused with each other."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from sentry.config import SentryOAuthConfig
from sentry.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError
from sentry.oauth import (
    READ_SCOPES,
    exchange_installation_code,
    installation_url,
    refresh_installation,
    refresh_with_token,
    uninstall,
)

CLIENT_ID = "63f307c32617f9445b9deba149d733004d49495ce7b07fb142017482bbf67167"
SECRET = "s" * 64
INSTALLATION = "cd6170b4-8ad6-403e-b078-8eb7d7688c28"
AUTHZ = f"https://sentry.io/api/0/sentry-app-installations/{INSTALLATION}/authorizations/"


def _config(**overrides) -> SentryOAuthConfig:
    values = {
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "redirect_uri": "https://api.hoja.work/api/connectors/sentry/callback",
        "integration_slug": "hoja",
    }
    values.update(overrides)
    return SentryOAuthConfig(**values)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# The provider mints an installation grant as `dateCreated` plus an `expiresAt` *instant* eight
# hours later; the live 201 body recorded while provisioning the connector reads
# 2026-11-09T19:52:40.889879Z -> 2026-11-10T03:52:40.889879Z. Derives both from one reading of the
# clock so the fixture keeps that shape without becoming a time bomb: a literal date here goes red
# on every branch the moment it passes, and the failure then looks like a broken exchange rather
# than a stale fixture.
GRANT_LIFETIME_SECONDS = 8 * 60 * 60


def _grant_window(lifetime: int = GRANT_LIFETIME_SECONDS) -> tuple[int, int]:
    """(dateCreated, expiresAt) as epochs, for a grant the provider has just minted."""
    created = int(time.time())
    return created, created + lifetime


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _token_body(**overrides) -> dict:
    created, expires = _grant_window()
    body = {
        "id": "38",
        "token": "access-sentry-fixture",
        "refreshToken": "refresh-sentry-fixture",
        "scopes": ["event:read", "org:read", "project:read"],
        "dateCreated": _iso(created),
        "expiresAt": _iso(expires),
        "state": None,
        "application": None,
    }
    body.update(overrides)
    return body


# --- config ---------------------------------------------------------------------------------


def test_config_reads_the_four_variables() -> None:
    config = SentryOAuthConfig.from_env(
        {
            "HOJA_SENTRY_CLIENT_ID": CLIENT_ID,
            "HOJA_SENTRY_CLIENT_SECRET": SECRET,
            "HOJA_SENTRY_REDIRECT_URI": "https://api.hoja.work/api/connectors/sentry/callback",
            "HOJA_SENTRY_INTEGRATION_SLUG": "hoja",
        }
    )
    assert config.integration_slug == "hoja"
    assert config.redirect_uri.endswith("/api/connectors/sentry/callback")


def test_config_defaults_the_slug_and_redacts_the_secret_in_repr() -> None:
    config = SentryOAuthConfig.from_env(
        {
            "HOJA_SENTRY_CLIENT_ID": CLIENT_ID,
            "HOJA_SENTRY_CLIENT_SECRET": SECRET,
            "HOJA_SENTRY_REDIRECT_URI": "https://api.hoja.work/api/connectors/sentry/callback",
        }
    )
    assert config.integration_slug == "hoja"
    assert SECRET not in repr(config)


def test_config_names_every_missing_variable() -> None:
    with pytest.raises(ConnectorConfigError) as excinfo:
        SentryOAuthConfig.from_env({})
    message = str(excinfo.value)
    assert "HOJA_SENTRY_CLIENT_ID" in message
    assert "HOJA_SENTRY_CLIENT_SECRET" in message
    assert "HOJA_SENTRY_REDIRECT_URI" in message
    assert excinfo.value.hint


@pytest.mark.parametrize(
    "redirect_uri",
    ["http://api.hoja.work/api/connectors/sentry/callback", "not-a-url", ""],
)
def test_config_refuses_a_redirect_that_sentry_could_not_match(redirect_uri: str) -> None:
    env = {
        "HOJA_SENTRY_CLIENT_ID": CLIENT_ID,
        "HOJA_SENTRY_CLIENT_SECRET": SECRET,
        "HOJA_SENTRY_REDIRECT_URI": redirect_uri,
    }
    if not redirect_uri:
        env.pop("HOJA_SENTRY_REDIRECT_URI")
    with pytest.raises(ConnectorConfigError):
        SentryOAuthConfig.from_env(env)


def test_config_refuses_a_slug_that_would_not_build_the_install_url() -> None:
    with pytest.raises(ConnectorConfigError):
        SentryOAuthConfig.from_env(
            {
                "HOJA_SENTRY_CLIENT_ID": CLIENT_ID,
                "HOJA_SENTRY_CLIENT_SECRET": SECRET,
                "HOJA_SENTRY_REDIRECT_URI": "https://api.hoja.work/x",
                "HOJA_SENTRY_INTEGRATION_SLUG": "../evil",
            }
        )


# --- install URL ----------------------------------------------------------------------------


def test_install_url_is_the_fixed_external_install_page() -> None:
    assert installation_url(_config()) == "https://sentry.io/sentry-apps/hoja/external-install/"


def test_install_url_carries_only_the_state() -> None:
    url = installation_url(_config(), state="sealed-state")
    assert url.endswith("?state=sealed-state")
    # No client id and no secret on an install URL that a user can copy.
    assert CLIENT_ID not in url
    assert SECRET not in url


# --- exchange -------------------------------------------------------------------------------


async def test_exchange_posts_the_documented_installation_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=_token_body())

    async with _client(handler) as http:
        token = await exchange_installation_code(
            http, _config(), code="c1", installation_id=INSTALLATION
        )
    assert seen["url"] == AUTHZ
    assert seen["body"]["grant_type"] == "authorization_code"
    assert seen["body"]["code"] == "c1"
    assert seen["body"]["client_id"] == CLIENT_ID
    assert seen["body"]["client_secret"] == SECRET
    assert token.access_token == "access-sentry-fixture"
    assert token.refresh_token == "refresh-sentry-fixture"
    assert token.installation_id == INSTALLATION


async def test_exchange_reads_the_camel_case_expiry_as_an_instant() -> None:
    _, eight_hours_out = _grant_window()
    body = _token_body(expiresAt=_iso(eight_hours_out))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=body)

    async with _client(handler) as http:
        token = await exchange_installation_code(
            http, _config(), code="c1", installation_id=INSTALLATION
        )
    # The absolute instant the body named, not "eight hours from now".
    assert token.expires_at == eight_hours_out
    assert GRANT_LIFETIME_SECONDS - 60 <= token.expires_in <= GRANT_LIFETIME_SECONDS

    # A shorter-lived grant must be read as itself, not replaced by the eight-hour default.
    _, forty_minutes_out = _grant_window(40 * 60)
    short_body = _token_body(expiresAt=_iso(forty_minutes_out))

    def short_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=short_body)

    async with _client(short_handler) as http:
        short = await exchange_installation_code(
            http, _config(), code="c1", installation_id=INSTALLATION
        )
    assert short.expires_at == forty_minutes_out
    assert 0 < short.expires_in <= 40 * 60


def test_the_token_fixture_keeps_its_expiry_relative_to_now(monkeypatch) -> None:
    """The time-bomb guard: a calendar literal here ignores the clock and rots the day it passes."""
    for frozen in (0, 4_102_444_800):  # 1970-01-01 and 2100-01-01
        monkeypatch.setattr(time, "time", lambda frozen=frozen: float(frozen))
        body = _token_body()
        minted = datetime.fromisoformat(body["expiresAt"].replace("Z", "+00:00"))
        assert minted.timestamp() == frozen + GRANT_LIFETIME_SECONDS
        created = datetime.fromisoformat(body["dateCreated"].replace("Z", "+00:00"))
        assert created.timestamp() == frozen


async def test_exchange_records_the_granted_scopes() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=_token_body())

    async with _client(handler) as http:
        token = await exchange_installation_code(
            http, _config(), code="c1", installation_id=INSTALLATION
        )
    assert set(token.scopes) == set(READ_SCOPES)


async def test_a_missing_refresh_token_is_an_error_not_an_eight_hour_account() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=_token_body(refreshToken=""))

    async with _client(handler) as http:
        with pytest.raises(ConnectorAPIError):
            await exchange_installation_code(
                http, _config(), code="c1", installation_id=INSTALLATION
            )


async def test_a_refused_client_pair_is_a_deployment_fault() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid client credentials"})

    async with _client(handler) as http:
        with pytest.raises(ConnectorConfigError):
            await exchange_installation_code(
                http, _config(), code="c1", installation_id=INSTALLATION
            )


async def test_a_used_code_asks_the_user_to_connect_again() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Invalid grant code"})

    async with _client(handler) as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await exchange_installation_code(
                http, _config(), code="c1", installation_id=INSTALLATION
            )
    assert "Settings > Connections" in (excinfo.value.hint or "")


async def test_a_removed_installation_asks_the_user_to_connect_again() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not found"})

    async with _client(handler) as http:
        with pytest.raises(ConnectorAuthError):
            await refresh_installation(http, _config(), installation_id=INSTALLATION)


async def test_a_non_uuid_installation_id_never_reaches_sentry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    async with _client(handler) as http:
        with pytest.raises(ValueError):
            await exchange_installation_code(
                http, _config(), code="c1", installation_id="../../organizations/"
            )


# --- refresh --------------------------------------------------------------------------------


def _decode(jwt: str) -> tuple[dict, dict]:
    header, claims, _signature = jwt.split(".")
    pad = "=" * (-len(header) % 4)
    head = json.loads(base64.urlsafe_b64decode(header + pad))
    pad = "=" * (-len(claims) % 4)
    body = json.loads(base64.urlsafe_b64decode(claims + pad))
    return head, body


async def test_refresh_sends_a_jwt_bearer_assertion_signed_with_the_client_secret() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_token_body())

    async with _client(handler) as http:
        await refresh_installation(http, _config(), installation_id=INSTALLATION)
    authorization = seen["auth"]
    assert authorization.startswith("Bearer ")
    assertion = authorization.removeprefix("Bearer ")
    head, claims = _decode(assertion)
    assert head["alg"] == "HS256"
    assert claims["iss"] == CLIENT_ID
    assert claims["sub"] == CLIENT_ID
    assert claims["exp"] - claims["iat"] == 60
    assert claims["jti"]
    # The signature verifies with the client secret and nothing else.
    signing_input = ".".join(assertion.split(".")[:2])
    expected = hmac.new(
        SECRET.encode(), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    given = assertion.split(".")[2]
    pad = "=" * (-len(given) % 4)
    assert base64.urlsafe_b64decode(given + pad) == expected
    assert seen["body"] == {"client_id": CLIENT_ID, "client_secret": SECRET,
                            "grant_type": "urn:sentry:params:oauth:grant-type:jwt-bearer"}


async def test_two_assertions_are_never_identical() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["authorization"])
        return httpx.Response(200, json=_token_body())

    async with _client(handler) as http:
        await refresh_installation(http, _config(), installation_id=INSTALLATION)
        await refresh_installation(http, _config(), installation_id=INSTALLATION)
    assert sent[0] != sent[1]  # a fresh jti, even inside the same second


async def test_the_refresh_token_fallback_uses_the_plain_grant() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_token_body())

    async with _client(handler) as http:
        await refresh_with_token(
            http, _config(), installation_id=INSTALLATION, refresh_token="r1"
        )
    assert seen["body"]["grant_type"] == "refresh_token"
    assert seen["body"]["refresh_token"] == "r1"


# --- uninstall ------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 202, 204, 404])
async def test_uninstall_reports_gone(status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with _client(handler) as http:
        assert await uninstall(http, _config(), installation_id=INSTALLATION) is True


@pytest.mark.parametrize("status", [401, 403])
async def test_uninstall_reports_the_pending_window_instead_of_failing(status: int) -> None:
    """Measured: DELETE answers 403 while the installation sits in `pending_deletion`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "pending deletion"})

    async with _client(handler) as http:
        assert await uninstall(http, _config(), installation_id=INSTALLATION) is False


async def test_uninstall_raises_on_a_server_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _client(handler) as http:
        with pytest.raises(ConnectorAPIError):
            await uninstall(http, _config(), installation_id=INSTALLATION)
