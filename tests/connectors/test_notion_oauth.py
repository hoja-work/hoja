"""Notion OAuth: owner=user, no scope parameter, Basic client auth with a JSON token body."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from notion.config import NotionOAuthConfig
from notion.errors import ConnectorAuthError, ConnectorConfigError
from notion.oauth import authorization_url, exchange_code

TOKEN_URL = "https://api.notion.com/v1/oauth/token"


def _config() -> NotionOAuthConfig:
    return NotionOAuthConfig(
        client_id="413d872b-594c-81d1-98c2-0037de6b4ad5",
        client_secret="secret_test_value",
        redirect_uri="https://hoja.example.test/api/connectors/notion/callback",
    )


def test_config_from_env_requires_all_three_keys() -> None:
    with pytest.raises(ConnectorConfigError) as excinfo:
        NotionOAuthConfig.from_env({"HOJA_NOTION_CLIENT_ID": "id"})
    assert "HOJA_NOTION_CLIENT_SECRET" in str(excinfo.value)
    assert "HOJA_NOTION_REDIRECT_URI" in str(excinfo.value)


def test_config_repr_never_contains_the_secret() -> None:
    assert "secret_test_value" not in repr(_config())


def test_authorization_url_is_user_scoped_and_has_no_scope_parameter() -> None:
    url = authorization_url(_config(), state="sealed-state")
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    assert query["owner"] == ["user"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["sealed-state"]
    assert query["redirect_uri"] == [_config().redirect_uri]
    assert "scope" not in query
    assert "code_challenge" not in query
    assert "secret_test_value" not in url


@respx.mock
async def test_exchange_code_uses_basic_auth_and_json_body() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "secret_notion_access",
                "token_type": "bearer",
                "refresh_token": None,
                "bot_id": "bot-1",
                "workspace_name": "Luis Alejandro Rincon's Space",
                "workspace_id": "workspace-1",
                "workspace_icon": None,
                "owner": {"type": "user", "user": {"id": "user-1"}},
            },
        )
    )
    async with httpx.AsyncClient() as http:
        token = await exchange_code(http, _config(), code="code-1")
    request = route.calls.last.request
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.headers["Notion-Version"] == "2022-06-28"
    assert b"grant_type" in request.content
    assert b"redirect_uri" in request.content
    assert token.workspace_name == "Luis Alejandro Rincon's Space"
    assert token.access_token == "secret_notion_access"


@respx.mock
async def test_exchange_code_rejects_a_bad_client_without_leaking_the_body() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            401, json={"error": "invalid_client", "error_description": "secret_notion_access"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as excinfo:
            await exchange_code(http, _config(), code="code-1")
    assert "invalid_client" in str(excinfo.value)
    assert "secret_test_value" not in str(excinfo.value)
    assert "secret_notion_access" not in str(excinfo.value)
