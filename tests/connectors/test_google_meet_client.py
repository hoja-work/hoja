"""The Meet half of the Google client: request shape, response mapping, and the two 403s.

Every response body below is shaped after Google's own discovery document for Meet v2, which is
vendored in `fixtures/meet_v2_discovery_subset.json` and cross-checked by
`test_google_meet_discovery.py`.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from google_workspace.client import MEET_BASE, GoogleWorkspaceClient
from google_workspace.errors import ConnectorAPIError, ConnectorAuthError, ConnectorConfigError


async def _client() -> tuple[httpx.AsyncClient, GoogleWorkspaceClient]:
    http = httpx.AsyncClient()
    return http, GoogleWorkspaceClient(http, "ya29.meet")


@respx.mock
async def test_conference_records_are_metadata_only_and_mapped_to_snake_case() -> None:
    route = respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(
            200,
            json={
                "conferenceRecords": [
                    {
                        "name": "conferenceRecords/rec-1",
                        "space": "spaces/space-1",
                        "startTime": "2026-11-08T10:00:00Z",
                        "endTime": "2026-11-08T10:30:00Z",
                        "expireTime": "2026-12-08T10:30:00Z",
                    }
                ]
            },
        )
    )
    http, client = await _client()
    try:
        records = await client.meet_conference_records(
            filter='start_time>="2026-11-01T00:00:00.000Z"', max_results=10
        )
    finally:
        await http.aclose()
    assert [r.name for r in records] == ["conferenceRecords/rec-1"]
    assert records[0].end_time == "2026-11-08T10:30:00Z"
    sent = route.calls.last.request
    assert sent.url.params["pageSize"] == "10"
    assert sent.url.params["filter"] == 'start_time>="2026-11-01T00:00:00.000Z"'
    assert sent.headers["Authorization"] == "Bearer ya29.meet"


@respx.mock
async def test_no_filter_is_omitted_rather_than_sent_empty() -> None:
    route = respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(200, json={"conferenceRecords": []})
    )
    http, client = await _client()
    try:
        await client.meet_conference_records()
    finally:
        await http.aclose()
    assert "filter" not in route.calls.last.request.url.params


@respx.mock
async def test_participants_keep_the_three_identity_shapes_apart() -> None:
    """A display name from an anonymous or phone participant is not a verified identity, and the
    tool must not present it as one."""
    respx.get(f"{MEET_BASE}/conferenceRecords/rec-1/participants").mock(
        return_value=httpx.Response(
            200,
            json={
                "participants": [
                    {
                        "name": "conferenceRecords/rec-1/participants/p1",
                        "signedinUser": {"user": "users/123", "displayName": "Ana"},
                        "earliestStartTime": "2026-11-08T10:00:00Z",
                        "latestEndTime": "2026-11-08T10:30:00Z",
                    },
                    {
                        "name": "conferenceRecords/rec-1/participants/p2",
                        "anonymousUser": {"displayName": "Guest"},
                        "earliestStartTime": "2026-11-08T10:05:00Z",
                    },
                    {
                        "name": "conferenceRecords/rec-1/participants/p3",
                        "phoneUser": {"displayName": "+1 555"},
                    },
                ]
            },
        )
    )
    http, client = await _client()
    try:
        people = await client.meet_participants("conferenceRecords/rec-1")
    finally:
        await http.aclose()
    assert [(p.kind, p.display_name) for p in people] == [
        ("signedin", "Ana"),
        ("anonymous", "Guest"),
        ("phone", "+1 555"),
    ]
    assert people[0].user_id == "users/123"
    assert people[1].user_id is None
    assert people[1].latest_end_time is None


@respx.mock
async def test_recordings_report_the_drive_destination_without_fetching_bytes() -> None:
    respx.get(f"{MEET_BASE}/conferenceRecords/rec-1/recordings").mock(
        return_value=httpx.Response(
            200,
            json={
                "recordings": [
                    {
                        "name": "conferenceRecords/rec-1/recordings/r1",
                        "state": "FILE_GENERATED",
                        "startTime": "2026-11-08T10:00:00Z",
                        "endTime": "2026-11-08T10:30:00Z",
                        "driveDestination": {
                            "file": "1kuceFZohVoCh6FulBHxwy6I15Ogpc4hP",
                            "exportUri": "https://drive.google.com/file/d/1kuceFZohVo/view",
                        },
                    }
                ]
            },
        )
    )
    http, client = await _client()
    try:
        recordings = await client.meet_recordings("conferenceRecords/rec-1")
    finally:
        await http.aclose()
    assert recordings[0].state == "FILE_GENERATED"
    assert recordings[0].drive_file_id == "1kuceFZohVoCh6FulBHxwy6I15Ogpc4hP"
    assert recordings[0].drive_export_uri == "https://drive.google.com/file/d/1kuceFZohVo/view"


@respx.mock
async def test_transcripts_report_the_docs_mirror_and_entries_serve_the_text() -> None:
    """Transcript text comes from the Meet API; the Docs document is a mirror, which is why no
    Docs scope is involved."""
    respx.get(f"{MEET_BASE}/conferenceRecords/rec-1/transcripts").mock(
        return_value=httpx.Response(
            200,
            json={
                "transcripts": [
                    {
                        "name": "conferenceRecords/rec-1/transcripts/t1",
                        "state": "FILE_GENERATED",
                        "docsDestination": {
                            "document": "1abcDOC",
                            "exportUri": "https://docs.google.com/document/d/1abcDOC/view",
                        },
                    }
                ]
            },
        )
    )
    respx.get(f"{MEET_BASE}/conferenceRecords/rec-1/transcripts/t1/entries").mock(
        return_value=httpx.Response(
            200,
            json={
                "transcriptEntries": [
                    {
                        "name": "conferenceRecords/rec-1/transcripts/t1/entries/e1",
                        "participant": "conferenceRecords/rec-1/participants/p1",
                        "text": "we ship on Friday",
                        "languageCode": "en-US",
                        "startTime": "2026-11-08T10:01:00Z",
                        "endTime": "2026-11-08T10:01:05Z",
                    }
                ]
            },
        )
    )
    http, client = await _client()
    try:
        transcripts = await client.meet_transcripts("conferenceRecords/rec-1")
        entries = await client.meet_transcript_entries(transcripts[0].name)
    finally:
        await http.aclose()
    assert transcripts[0].docs_document_id == "1abcDOC"
    assert entries[0].text == "we ship on Friday"
    assert entries[0].language_code == "en-US"
    assert entries[0].participant.endswith("/participants/p1")


@respx.mock
async def test_pagination_follows_the_token_and_respects_the_result_cap() -> None:
    route = respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "conferenceRecords": [{"name": "conferenceRecords/a"}],
                    "nextPageToken": "page-2",
                },
            ),
            httpx.Response(
                200,
                json={
                    "conferenceRecords": [{"name": "conferenceRecords/b"}],
                    "nextPageToken": "page-3",
                },
            ),
            httpx.Response(200, json={"conferenceRecords": [{"name": "conferenceRecords/c"}]}),
        ]
    )
    http, client = await _client()
    try:
        records = await client.meet_conference_records(max_results=2)
    finally:
        await http.aclose()
    assert [r.name for r in records] == ["conferenceRecords/a", "conferenceRecords/b"]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageToken"] == "page-2"


@respx.mock
async def test_a_repeated_page_token_stops_the_walk() -> None:
    """A server that repeats its own token must not turn one tool call into ten."""
    route = respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(
            200,
            json={"conferenceRecords": [{"name": "conferenceRecords/a"}], "nextPageToken": "same"},
        )
    )
    http, client = await _client()
    try:
        records = await client.meet_conference_records(max_results=100)
    finally:
        await http.aclose()
    assert len(records) == 2
    assert route.call_count == 2


@pytest.mark.parametrize(
    "name",
    [
        "conferenceRecords/../../spaces/x",
        "conferenceRecords/a/participants/../../x",
        "spaces/x?pageSize=1",
        "",
        "conferenceRecords/a/recordings/b/entries/c",
    ],
)
async def test_a_resource_name_that_is_not_a_meet_path_never_reaches_the_request(name) -> None:
    http, client = await _client()
    try:
        with pytest.raises(ValueError):
            await client.meet_participants(name)
    finally:
        await http.aclose()


@respx.mock
async def test_a_grant_without_the_meet_scope_asks_the_user_to_add_it() -> None:
    respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}],
                }
            },
        )
    )
    http, client = await _client()
    try:
        with pytest.raises(ConnectorAuthError) as caught:
            await client.meet_conference_records()
    finally:
        await http.aclose()
    assert "Google Meet access" in str(caught.value)
    assert "Settings > Connections" in (caught.value.hint or "")


@respx.mock
async def test_an_api_that_was_never_enabled_is_a_deployment_error_not_a_user_error() -> None:
    """The provisioning run measured this split: a disabled API answers 403 SERVICE_DISABLED,
    while a missing token answers 401. Telling a user to reconnect cannot fix the first one."""
    respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "details": [{"reason": "SERVICE_DISABLED"}],
                }
            },
        )
    )
    http, client = await _client()
    try:
        with pytest.raises(ConnectorConfigError) as caught:
            await client.meet_conference_records()
    finally:
        await http.aclose()
    assert "not enabled" in str(caught.value)
    assert "Google Cloud project" in (caught.value.hint or "")


@respx.mock
async def test_a_rejected_token_is_reported_as_a_reconnect() -> None:
    respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}})
    )
    http, client = await _client()
    try:
        with pytest.raises(ConnectorAuthError):
            await client.meet_conference_records()
    finally:
        await http.aclose()


@respx.mock
async def test_an_unexpected_shape_is_an_api_error_not_a_crash() -> None:
    respx.get(f"{MEET_BASE}/conferenceRecords").mock(
        return_value=httpx.Response(200, json={"conferenceRecords": "not-a-list"})
    )
    http, client = await _client()
    try:
        with pytest.raises(ConnectorAPIError):
            await client.meet_conference_records()
    finally:
        await http.aclose()
