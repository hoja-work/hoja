"""Google Workspace connector: PKCE, OAuth exchange, read-only API client, and error hygiene."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx
from google_workspace import (
    ConnectorAPIError,
    ConnectorAuthError,
    ConnectorConfigError,
    GoogleOAuthConfig,
    GoogleWorkspaceClient,
)
from google_workspace.oauth import (
    _challenge_for,
    authorization_url,
    exchange_code,
    pkce_pair,
    refresh_access_token,
)

CONFIG = GoogleOAuthConfig(
    client_id="client-123.apps.googleusercontent.com",
    client_secret="GOCSPX-test-secret-value",
    redirect_uri="https://hoja.example.test/api/connectors/google/callback",
)


def test_pkce_matches_rfc7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert _challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_pair_is_s256_and_url_safe() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge
    assert challenge == _challenge_for(verifier)


def test_authorization_url_carries_pkce_state_and_offline_access() -> None:
    url = authorization_url(
        CONFIG,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        state="state-abc",
        code_challenge="challenge-xyz",
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state=state-abc" in url
    assert "code_challenge=challenge-xyz" in url
    assert "code_challenge_method=S256" in url
    assert "access_type=offline" in url
    assert "client_secret" not in url


def test_config_error_names_variables_but_not_values(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConnectorConfigError) as info:
        GoogleOAuthConfig.from_env({"CLINIAR_GOOGLE_CLIENT_ID": "id-only"})
    message = str(info.value)
    assert "CLINIAR_GOOGLE_CLIENT_SECRET" in message
    assert "CLINIAR_GOOGLE_REDIRECT_URI" in message
    assert "id-only" not in message


def test_config_repr_hides_secret() -> None:
    assert "GOCSPX" not in repr(CONFIG)


@respx.mock
async def test_exchange_code_sends_verifier_and_returns_tokens() -> None:
    route = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.A",
                "refresh_token": "1//R",
                "expires_in": 3599,
                "scope": "x",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        tokens = await exchange_code(http, CONFIG, code="auth-code", code_verifier="ver")
    body = route.calls.last.request.content.decode()
    assert "code_verifier=ver" in body
    assert "grant_type=authorization_code" in body
    assert tokens.refresh_token == "1//R"
    assert tokens.access_token == "ya29.A"


@respx.mock
async def test_refresh_without_new_refresh_token_is_allowed() -> None:
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.B", "expires_in": 3599})
    )
    async with httpx.AsyncClient() as http:
        tokens = await refresh_access_token(http, CONFIG, refresh_token="1//R")
    assert tokens.refresh_token is None


@respx.mock
async def test_token_failure_surfaces_only_error_code() -> None:
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": f"echo {CONFIG.client_secret}"},
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as info:
            await exchange_code(http, CONFIG, code="bad", code_verifier="ver")
    assert "invalid_grant" in str(info.value)
    assert CONFIG.client_secret not in str(info.value)


@respx.mock
async def test_gmail_search_fetches_metadata_with_bearer_token() -> None:
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "18f-a", "threadId": "t1"}]})
    )
    detail = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/18f-a").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "18f-a",
                "threadId": "t1",
                "snippet": "Hello there",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Q3 plan"},
                        {"name": "From", "value": "ana@example.test"},
                        {"name": "Date", "value": "Mon, 6 Oct 2026 10:00:00 +0000"},
                    ]
                },
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = GoogleWorkspaceClient(http, "ya29.A")
        messages = await client.gmail_search("from:ana", max_results=5)
    assert messages[0].subject == "Q3 plan"
    assert messages[0].sender == "ana@example.test"
    request = detail.calls.last.request
    assert request.headers["Authorization"] == "Bearer ya29.A"
    assert request.url.params.get_list("metadataHeaders") == ["Subject", "From", "Date"]


@respx.mock
async def test_gmail_search_empty_mailbox_returns_empty_list() -> None:
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"resultSizeEstimate": 0})
    )
    async with httpx.AsyncClient() as http:
        assert await GoogleWorkspaceClient(http, "t").gmail_search("none") == []


@respx.mock
async def test_drive_search_maps_files() -> None:
    respx.get("https://www.googleapis.com/drive/v3/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "1abc_-",
                        "name": "Plan",
                        "mimeType": "application/vnd.google-apps.document",
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        files = await GoogleWorkspaceClient(http, "t").drive_search("name contains 'Plan'")
    assert files[0].id == "1abc_-"
    assert files[0].mime_type == "application/vnd.google-apps.document"
    assert files[0].web_view_link is None


@respx.mock
async def test_calendar_handles_timed_and_all_day_events() -> None:
    respx.get("https://www.googleapis.com/calendar/v3/calendars/primary/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "e1",
                        "summary": "Standup",
                        "start": {"dateTime": "2026-10-06T09:00:00Z"},
                        "end": {"dateTime": "2026-10-06T09:15:00Z"},
                    },
                    {"id": "e2", "start": {"date": "2026-10-07"}, "end": {"date": "2026-10-08"}},
                ]
            },
        )
    )
    start = datetime(2026, 10, 6, tzinfo=timezone.utc)
    async with httpx.AsyncClient() as http:
        events = await GoogleWorkspaceClient(http, "t").calendar_events(start, start)
    assert events[0].start == "2026-10-06T09:00:00Z"
    assert events[1].summary == "(no title)"
    assert events[1].start == "2026-10-07"


@respx.mock
async def test_docs_text_is_extracted_from_paragraph_runs() -> None:
    respx.get("https://docs.googleapis.com/v1/documents/doc_1-x").mock(
        return_value=httpx.Response(
            200,
            json={
                "documentId": "doc_1-x",
                "title": "Notes",
                "body": {
                    "content": [
                        {"paragraph": {"elements": [{"textRun": {"content": "Hello "}}]}},
                        {"sectionBreak": {}},
                        {"paragraph": {"elements": [{"textRun": {"content": "world\n"}}]}},
                    ]
                },
            },
        )
    )
    async with httpx.AsyncClient() as http:
        doc = await GoogleWorkspaceClient(http, "t").docs_get("doc_1-x")
    assert doc.text == "Hello world\n"


@respx.mock
async def test_invalid_id_is_rejected_before_any_network_call() -> None:
    route = respx.get(host="www.googleapis.com").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError):
            await GoogleWorkspaceClient(http, "t").drive_get_file("../../etc")
    assert route.call_count == 0


@respx.mock
async def test_unauthorized_response_asks_for_reconnect() -> None:
    respx.get("https://www.googleapis.com/drive/v3/files/abc").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAuthError) as info:
            await GoogleWorkspaceClient(http, "t").drive_get_file("abc")
    assert info.value.hint == "Reconnect the Google account."


@respx.mock
async def test_forbidden_response_is_api_error_without_body() -> None:
    respx.get("https://www.googleapis.com/drive/v3/files/abc").mock(
        return_value=httpx.Response(403, json={"error": {"message": "secret-echo"}})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorAPIError) as info:
            await GoogleWorkspaceClient(http, "t").drive_get_file("abc")
    assert "403" in str(info.value)
    assert "secret-echo" not in str(info.value)
