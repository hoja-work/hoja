"""Request ids and one operations line per operation.

The incident these tests answer: a production client could not be reconstructed.
No log line said what it sent, and ``audit_event.request_id`` — a column that had
existed since the audit ledger landed — was empty in every row because nothing
ever bound an id. The four properties pinned here are the ones an operator needs:

1. every response carries an ``X-Request-Id``, adopted from the client when it is
   well formed and minted when it is not;
2. a GraphQL operation and an MCP ``tools/call`` each write **exactly one** line,
   with the fields that identify the operation and none of the payload;
3. an audit row records the id of the request that caused it;
4. the id is on the request and nowhere else — no token, no variable, no argument
   reaches the line.
"""

from __future__ import annotations

import logging
import re
import shlex
from contextlib import contextmanager

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import audit_event, make_engine, migrate, seed_tenant
from tests.test_mcp_agent_tools import hoja as mcp_world  # noqa: F401 - the MCP fixture

OPS_LOGGER = "hoja_server.ops"
TOKEN = "obs-token"
GENERATED_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def world(tmp_path):
    """One seeded workspace and a client, with no logging configuration assumed."""
    db_path = tmp_path / "observability.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Observability", org_url_key="observability",
        user_email="ops@example.test", token=TOKEN, demo_issues=False,
    )
    return TestClient(create_app(str(db_path), open_mode=False)), engine, seeded


@contextmanager
def ops_lines():
    """Capture what ``hoja_server.ops`` emits, on that logger, into a list.

    Deliberately not ``caplog``: ``test_auth_telemetry`` runs uvicorn's
    ``logging.config.dictConfig``, which leaves two capture handlers on the root
    logger for the rest of the session, so a root-level capture reports one line
    twice. The line was emitted once — a handler on the logger under test proves
    that, and is immune to whatever another test does to the root logger.
    """
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger(OPS_LOGGER)
    saved = (logger.level, logger.propagate, logger.disabled)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # The handler above is the only sink: no propagation to a root logger that
    # may or may not still be capturing.
    logger.propagate = False
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.level, logger.propagate, logger.disabled = saved


def _fields(message: str) -> dict[str, str]:
    """``op k=v k="quoted value"`` → ``{"k": "v", ...}``."""
    words = shlex.split(message)
    assert words[0] == "op", message
    fields = {}
    for word in words[1:]:
        key, _, value = word.partition("=")
        fields[key] = value
    return fields


def _auth(extra: dict | None = None) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", **(extra or {})}


def test_response_echoes_a_well_formed_request_id(world):
    client, _engine, _seeded = world
    response = client.post("/graphql", headers=_auth({"X-Request-Id": "edge-7.f:abc_1"}),
                           json={"query": "{ viewer { id } }"})
    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == "edge-7.f:abc_1"


def test_missing_request_id_is_generated(world):
    client, _engine, _seeded = world
    response = client.post("/graphql", headers=_auth(), json={"query": "{ viewer { id } }"})
    assert GENERATED_ID.fullmatch(response.headers["x-request-id"]), response.headers


@pytest.mark.parametrize(
    "hostile",
    [
        "spaces are not allowed",
        "quote\"breaks=\tthe line",
        "x" * 129,
        "../../etc/passwd",
    ],
)
def test_a_hostile_request_id_is_replaced_not_sanitised(world, hostile):
    """A half-adopted id would silently stop matching the caller's own logs."""
    client, _engine, _seeded = world
    response = client.post("/graphql", headers=_auth({"X-Request-Id": hostile}),
                           json={"query": "{ viewer { id } }"})
    assert response.headers["x-request-id"] != hostile
    assert GENERATED_ID.fullmatch(response.headers["x-request-id"]), response.headers


def test_graphql_writes_exactly_one_line_without_the_payload(world):
    client, _engine, seeded = world
    query = "query ViewerName { viewer { id email } }"
    with ops_lines() as lines:
        response = client.post("/graphql", headers=_auth({"X-Request-Id": "req-graphql-1"}),
                               json={"query": query, "variables": {"secret": "do-not-log"}})
    assert response.status_code == 200, response.text
    assert len(lines) == 1, [record.getMessage() for record in lines]
    message = lines[0].getMessage()
    fields = _fields(message)
    assert fields["request_id"] == "req-graphql-1"
    assert fields["transport"] == "graphql"
    assert fields["operation"] == "query ViewerName [viewer]"
    assert fields["status"] == "200"
    assert fields["outcome"] == "ok"
    assert fields["actor"] == f"human:{seeded['user_id']}"
    assert fields["org"]
    assert fields["host"]
    assert float(fields["duration_ms"]) >= 0
    # The promise the incident needed: nothing from the request body, and no
    # credential, is in the line.
    assert "do-not-log" not in message
    assert TOKEN not in message
    assert "Authorization" not in message


def test_a_failed_graphql_operation_says_which_error(world):
    """GraphQL answers 200 on failure, so the outcome cannot come from the status."""
    client, _engine, _seeded = world
    with ops_lines() as lines:
        response = client.post(
            "/graphql", headers=_auth(),
            json={"query": 'mutation { nope: teamCreate(name: "") { team { id } } }'},
        )
    assert response.status_code == 200
    assert len(lines) == 1, [record.getMessage() for record in lines]
    fields = _fields(lines[0].getMessage())
    assert fields["operation"] == "mutation - [nope]"
    assert fields["outcome"].startswith("error:")


def test_rest_requests_are_covered_too(world):
    client, _engine, _seeded = world
    with ops_lines() as lines:
        response = client.get("/auth/me", headers=_auth({"X-Request-Id": "req-rest-1"}))
    assert response.status_code == 200, response.text
    assert len(lines) == 1, [record.getMessage() for record in lines]
    fields = _fields(lines[0].getMessage())
    assert fields["transport"] == "rest"
    assert fields["operation"] == "GET /auth/me"
    assert fields["request_id"] == "req-rest-1"


def test_static_assets_and_probes_are_not_operations(world):
    """The SPA's own asset fetches would otherwise drown the operations log."""
    client, _engine, _seeded = world
    with ops_lines() as lines:
        client.get("/health")
        client.get("/favicon.ico")
    assert lines == [], [record.getMessage() for record in lines]


def test_audit_rows_record_the_request_id(world):
    client, engine, _seeded = world
    response = client.post(
        "/graphql",
        headers=_auth({"X-Request-Id": "req-audit-1"}),
        json={"query": 'mutation { apiKeyCreate(label: "ops") { success } }'},
    )
    assert response.json()["data"]["apiKeyCreate"]["success"] is True, response.text
    with engine.connect() as conn:
        rows = conn.execute(select(audit_event.c.action, audit_event.c.request_id)).all()
    audit_rows = [row for row in rows if row.action == "api_key.create"]
    assert len(audit_rows) == 1
    assert audit_rows[0].request_id == "req-audit-1"


def test_an_mcp_tool_call_writes_exactly_one_line(mcp_world):  # noqa: F811 - fixture
    """The tool dispatcher logs; the MCP endpoint must not log a second line."""
    pytest.importorskip("mcp")
    mcp = mcp_world()
    with ops_lines() as lines:
        ok, _payload = mcp.call("hoja_viewer")
    assert ok
    assert len(lines) == 1, [record.getMessage() for record in lines]
    message = lines[0].getMessage()
    fields = _fields(message)
    assert fields["transport"] == "mcp"
    assert fields["operation"] == "tools/call hoja_viewer"
    assert fields["outcome"] == "ok"
    assert fields["actor"].startswith(("api_key:", "human:", "agent:"))
    # The dispatcher runs in the SDK's own task: the id and host it writes must
    # come from the context the edge bound, not from a scope it cannot see.
    assert GENERATED_ID.fullmatch(fields["request_id"]), fields
    assert fields["host"] == "mcp.example.test"
    assert "Authorization" not in message


def test_the_streamed_mcp_response_carries_the_request_id(mcp_world):  # noqa: F811 - fixture
    """The MCP endpoint answers over SSE: the header must survive the stream."""
    pytest.importorskip("mcp")
    mcp = mcp_world()
    request_id = "req-mcp-stream-1"
    response = mcp.client.post(
        "/mcp/",
        headers={
            **mcp.headers,
            "X-Request-Id": request_id,
            "MCP-Protocol-Version": mcp.version,
            "Mcp-Method": "tools/list",
        },
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {**mcp.envelope}},
    )
    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == request_id


def test_server_timing_and_db_fields_maintain_closure(world):
    """Every DB-backed request emits Server-Timing and db_ms/db_queries/pool_ms with closure."""
    client, _engine, _seeded = world
    with ops_lines() as lines:
        response = client.post(
            "/graphql",
            headers=_auth({"X-Request-Id": "req-timing-1"}),
            json={"query": "query ViewerTiming { viewer { id email } }"},
        )
    assert response.status_code == 200, response.text
    st = response.headers.get("server-timing", "")
    assert "app;dur=" in st, st
    assert "db;dur=" in st, st
    assert 'desc="q=' in st, st
    assert len(lines) == 1
    fields = _fields(lines[0].getMessage())
    duration_ms = float(fields["duration_ms"])
    db_ms = float(fields["db_ms"])
    db_queries = int(fields["db_queries"])
    db_max_ms = float(fields["db_max_ms"])
    pool_ms = float(fields["pool_ms"])
    assert db_queries >= 1
    assert db_ms >= 0
    assert db_max_ms <= db_ms + 0.2
    # Closure: wall-clock app duration must cover SQL execution (allow 1ms rounding).
    assert duration_ms + 1.0 >= db_ms
    assert pool_ms >= 0


def test_rum_ingest_logs_normalized_route_and_rejects_bad_payloads(world):
    """Browser RUM beacons write normalized transport=rum lines without PII."""
    client, _engine, _seeded = world
    with ops_lines() as lines:
        ok_resp = client.post(
            "/api/perf/rum",
            headers=_auth(),
            json={
                "events": [
                    {
                        "kind": "nav",
                        "route": "/workspace/issue/HOJA-754",
                        "ttfb_ms": 42.3,
                        "lcp_ms": 510.7,
                        "server_app_ms": 38.1,
                        "server_db_ms": 12.4,
                        "slowest_request_id": "req-timing-1",
                        "secret_extra": "must-not-appear",
                    }
                ]
            },
        )
    assert ok_resp.status_code == 202, ok_resp.text
    assert ok_resp.json()["accepted"] == 1
    assert len(lines) == 1
    msg = lines[0].getMessage()
    fields = _fields(msg)
    assert fields["transport"] == "rum"
    assert fields["operation"] == "nav /workspace/issue/:issue"
    assert fields["request_id"] == "req-timing-1"
    assert fields["lcp_ms"] == "510.7"
    assert "must-not-appear" not in msg
    assert "HOJA-754" not in msg

    # Oversized payload rejected cleanly
    big = client.post("/api/perf/rum", headers=_auth(), content=b"x" * 9000)
    assert big.status_code == 413
