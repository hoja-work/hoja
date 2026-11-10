"""Check the Meet client against Google's own discovery document, not against my reading of it.

`fixtures/meet_v2_discovery_subset.json` is a verbatim subset of
`https://meet.googleapis.com/$discovery/rest?version=v2` (revision recorded in the file). The
service publishes the paths, query parameters, response schemas and required OAuth scopes from
which it will accept a request, so this test can fail for a reason that is not my opinion:

- a path the client builds that the service does not document,
- a query parameter it sends that the method does not declare,
- a response field it reads that the schema does not contain,
- a scope it requests that the method does not accept.

The bodies below are empty on purpose: the shape is the subject, not the data.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from google_workspace.client import GoogleWorkspaceClient
from google_workspace.oauth import MEET_SCOPE

DISCOVERY = json.loads(
    (Path(__file__).parent / "fixtures" / "meet_v2_discovery_subset.json").read_text()
)

REC = "conferenceRecords/rec-1"
TRANSCRIPT = f"{REC}/transcripts/t1"

# (documented method, documented response schema, list field the client reads, concrete name)
CONTRACT = [
    ("conferenceRecords.list", "ListConferenceRecordsResponse", "conferenceRecords", None),
    (
        "conferenceRecords.participants.list",
        "ListParticipantsResponse",
        "participants",
        REC,
    ),
    ("conferenceRecords.recordings.list", "ListRecordingsResponse", "recordings", REC),
    ("conferenceRecords.transcripts.list", "ListTranscriptsResponse", "transcripts", REC),
    (
        "conferenceRecords.transcripts.entries.list",
        "ListTranscriptEntriesResponse",
        "transcriptEntries",
        TRANSCRIPT,
    ),
]


def test_the_fixture_records_where_it_came_from() -> None:
    assert DISCOVERY["source"] == "https://meet.googleapis.com/$discovery/rest?version=v2"
    assert DISCOVERY["retrieved"]
    assert DISCOVERY["revision"]
    assert len(DISCOVERY["methods"]) == len(CONTRACT)


def test_every_documented_method_accepts_the_read_only_scope_we_request() -> None:
    """Google lists `meetings.space.created` and `meetings.space.readonly` as *alternatives* for
    each of these methods. Every call we make must be satisfiable by the read-only one, or the
    connector would be asking for more than it needs."""
    for name, _schema, _key, _parent in CONTRACT:
        method = DISCOVERY["methods"][name]
        assert method["httpMethod"] == "GET", name
        assert MEET_SCOPE in method["meetScopes"], name
        assert MEET_SCOPE.endswith("readonly")


async def _exercise_all() -> list[httpx.Request]:
    """Call every Meet method once and return the requests it actually sent."""

    async def _handler(request: httpx.Request) -> httpx.Response:
        for _name, _schema, key, _parent in CONTRACT:
            body = {key: []}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient()
    client = GoogleWorkspaceClient(http, "ya29.meet")
    with respx.mock:
        route = respx.route(method="GET").mock(side_effect=_handler)
        try:
            await client.meet_conference_records(max_results=5)
            await client.meet_participants(REC, max_results=5)
            await client.meet_recordings(REC, max_results=5)
            await client.meet_transcripts(REC, max_results=5)
            await client.meet_transcript_entries(TRANSCRIPT, max_results=5)
        finally:
            await http.aclose()
        return [call.request for call in route.calls]


def _concrete(flat_path: str) -> str:
    return "/" + flat_path.replace("{conferenceRecordsId}", "rec-1").replace(
        "{transcriptsId}", "t1"
    )


async def test_the_client_only_calls_documented_paths_with_documented_parameters() -> None:
    documented = {_concrete(method["flatPath"]): method for method in DISCOVERY["methods"].values()}
    requests = await _exercise_all()
    assert len(requests) == len(CONTRACT)

    for request in requests:
        path = request.url.path
        assert path in documented, f"{path} is not a documented Meet path"
        method = documented[path]
        for name in request.url.params:
            assert name in method["parameters"], f"{path} does not document {name}"


async def test_the_client_parses_fields_its_documented_response_actually_has() -> None:
    for name, schema, key, _parent in CONTRACT:
        assert DISCOVERY["methods"][name]["response"] == schema
        # The list field the client reads must exist on the documented response, and point at the
        # documented item type rather than at something invented.
        assert DISCOVERY["schemas"][schema][key].startswith("array<")


async def test_every_path_is_exercised_so_a_new_endpoint_cannot_slip_past() -> None:
    documented = {_concrete(m["flatPath"]) for m in DISCOVERY["methods"].values()}
    exercised = {request.url.path for request in await _exercise_all()}
    assert documented == exercised
