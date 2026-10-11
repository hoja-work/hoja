"""Connector principal signing and read tools: forged, expired, and cross-tenant tokens are rejected."""

from __future__ import annotations

import httpx
import pytest
import respx
from google_workspace import ConnectorAuthError, ConnectorConfigError
from google_workspace.principal import sign_principal, verify_principal
from google_workspace.tools import AccountGrant, drive_search, gmail_search

KEY = "server-signing-key-for-tests"


def test_principal_round_trips_when_fresh() -> None:
    token = sign_principal("u1", "o1", "read", key=KEY, now=1000)
    principal = verify_principal(token, key=KEY, now=1100)
    assert (principal.user_id, principal.org_id, principal.scope) == ("u1", "o1", "read")


def test_tampered_scope_is_rejected() -> None:
    token = sign_principal("u1", "o1", "read", key=KEY, now=1000)
    body, sig = token.split(".")
    import base64
    import json

    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["s"] = "write"
    forged_body = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(ConnectorAuthError, match="signature"):
        verify_principal(f"{forged_body}.{sig}", key=KEY, now=1100)


def test_token_signed_with_other_key_is_rejected() -> None:
    token = sign_principal("u1", "o1", "read", key="attacker-key", now=1000)
    with pytest.raises(ConnectorAuthError, match="signature"):
        verify_principal(token, key=KEY, now=1100)


def test_expired_token_is_rejected() -> None:
    token = sign_principal("u1", "o1", "read", key=KEY, now=1000, ttl=60)
    with pytest.raises(ConnectorAuthError, match="expired"):
        verify_principal(token, key=KEY, now=2000)


def test_missing_signing_key_is_a_config_error() -> None:
    with pytest.raises(ConnectorConfigError):
        sign_principal("u1", "o1", "read", key=None, now=1000)


def test_malformed_token_is_auth_error() -> None:
    with pytest.raises(ConnectorAuthError, match="Malformed"):
        verify_principal("not-a-token", key=KEY, now=1000)


async def _grant(token: str = "ya29.TOKEN"):
    async def fetch() -> str:
        return token

    return AccountGrant(
        account_id="acct_1", external_account="ana@example.test", access_token=fetch
    )


@respx.mock
async def test_gmail_tool_uses_grant_token_only() -> None:
    route = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as http:
        assert await gmail_search(http, await _grant(), "from:ana") == []
    assert route.calls.last.request.headers["Authorization"] == "Bearer ya29.TOKEN"


@respx.mock
async def test_empty_token_fails_closed_without_network() -> None:
    route = respx.get(host="www.googleapis.com").mock(return_value=httpx.Response(200, json={}))

    async def empty() -> str:
        return ""

    grant = AccountGrant(account_id="a", external_account="x", access_token=empty)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError):
            await drive_search(http, grant, "name contains 'x'")
    assert route.call_count == 0
