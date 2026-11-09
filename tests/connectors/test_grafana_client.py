"""The typed Grafana client: request shape, error mapping and bounded summaries.

Every HTTP call goes through `httpx.MockTransport`, so the suite is offline. The address the client
connects to is the pinned public address from the stub resolver, never a second DNS answer.
"""

from __future__ import annotations

import json

import httpx
import pytest
from grafana.client import GrafanaClient
from grafana.errors import ConnectorAPIError, ConnectorAuthError
from grafana.tools import (
    AccountGrant,
    get_dashboard,
    list_annotations,
    query_data_source,
    search_dashboards,
)

PUBLIC = "93.184.216.34"
URL = "https://grafana.example.com"
TOKEN = "glsa_abcdefghijklmnopqrstuvwxyz0123456789AB"


def _resolver(host: str, port: int) -> list[str]:
    return [PUBLIC]


@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch):
    """Tools construct the client themselves; pin the system resolver for the whole module."""
    monkeypatch.setattr("grafana.ssrf._system_resolver", _resolver)


class _Token:
    def __init__(self) -> None:
        self.invalidated = 0

    async def access_token(self) -> str:
        return TOKEN

    async def invalidate(self) -> None:
        self.invalidated += 1


def _client(handler, token: _Token | None = None) -> GrafanaClient:
    return GrafanaClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        token or _Token(),
        URL,
        resolver=_resolver,
    )


def _grant() -> AccountGrant:
    return AccountGrant(
        account_id="acct", external_account="sa-1-hoja-connector", url=URL, tokens=_Token()
    )


def _grant_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_every_request_carries_the_pinned_host_and_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"login": "sa-1-hoja-connector", "uid": "service-account:20"})

    user = await _client(handler).get_current_user()
    assert user.label == "sa-1-hoja-connector"
    request = seen[0]
    assert request.url.host == PUBLIC
    assert request.url.path == "/api/user"
    assert request.headers["host"] == "grafana.example.com"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


async def test_a_401_is_a_reconnect_not_a_retry() -> None:
    token = _Token()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"message": "Invalid API key", "messageId": "api-key.invalid"})

    with pytest.raises(ConnectorAuthError) as caught:
        await _client(handler, token).get_current_user()
    assert "reconnect" in (caught.value.hint or "").lower()
    assert len(calls) == 1
    assert token.invalidated == 1


async def test_a_403_surfaces_the_missing_permission() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "accessErrorId": "ACE123",
                "message": "You'll need additional permissions. Permissions needed: dashboards:create",
                "title": "Access denied",
            },
        )

    with pytest.raises(ConnectorAuthError) as caught:
        await _client(handler).get_current_user()
    assert "dashboards:create" in (caught.value.hint or "")
    assert "ACE123" in (caught.value.hint or "")


async def test_search_uses_the_dashboard_filter_and_returns_typed_rows() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"uid": "abc", "title": "Checkout", "url": "/d/abc", "tags": ["prod"], "type": "dash-db"},
                {"uid": "def", "title": "Ignore", "type": "dash-folder"},
            ],
        )

    rows = await _client(handler).search_dashboards(query="checkout", limit=25)
    assert [row.uid for row in rows] == ["abc", "def"]
    assert seen[0].url.params["type"] == "dash-db"
    assert seen[0].url.params["query"] == "checkout"
    assert seen[0].url.params["limit"] == "25"


async def test_dashboard_summary_keeps_panels_but_not_queries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "dashboard": {
                    "uid": "abc",
                    "title": "Checkout",
                    "description": "Latency",
                    "tags": ["prod"],
                    "templating": {"list": [{"name": "env"}]},
                    "panels": [
                        {
                            "id": 1,
                            "title": "p95",
                            "type": "timeseries",
                            "description": "desc",
                            "targets": [{"expr": "secret-looking-query"}],
                        }
                    ],
                },
                "meta": {"folderUid": "folder-1"},
            },
        )

    summary = await _client(handler).get_dashboard("abc")
    assert summary.title == "Checkout"
    assert summary.folderUid == "folder-1"
    assert [panel.title for panel in summary.panels] == ["p95"]
    assert "secret-looking-query" not in summary.model_dump_json()


async def test_a_dashboard_uid_cannot_inject_a_path() -> None:
    with pytest.raises(ValueError):
        await _client(lambda request: httpx.Response(200, json={})).get_dashboard(
            "../../api/admin/settings"
        )


async def test_data_source_summary_never_emits_secret_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "uid": "prom",
                    "name": "grafanacloud-prom",
                    "type": "prometheus",
                    "url": "https://prom.example",
                    "isDefault": True,
                    "readOnly": False,
                    "secureJsonFields": {"basicAuthPassword": True},
                    "jsonData": {"httpHeaderName1": "X-Scope-OrgID"},
                }
            ],
        )

    rows = await _client(handler).list_data_sources()
    payload = json.dumps([row.model_dump() for row in rows])
    assert rows[0].uid == "prom"
    assert "secureJson" not in payload
    assert "httpHeaderName1" not in payload


async def test_alert_rules_come_from_the_rules_endpoint_with_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/prometheus/grafana/api/v1/rules"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "groups": [
                        {
                            "name": "checkout",
                            "rules": [
                                {
                                    "uid": "r1",
                                    "name": "HighErrorRate",
                                    "state": "alerting",
                                    "health": "ok",
                                    "labels": {"severity": "page"},
                                    "lastEvaluation": "2026-11-09T22:00:00Z",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    rows = await _client(handler).list_alert_rules()
    assert [(row.title, row.state, row.health) for row in rows] == [
        ("HighErrorRate", "alerting", "ok")
    ]


async def test_alert_rules_fall_back_to_provisioning_with_unknown_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/prometheus/grafana/api/v1/rules":
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(
            200, json=[{"uid": "r1", "title": "CPU", "ruleGroup": "g", "folderUID": "f"}]
        )

    rows = await _client(handler).list_alert_rules()
    assert rows[0].state == "unknown"
    assert rows[0].title == "CPU"


async def test_annotations_are_bounded_and_text_is_clipped() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": 1, "text": "x" * 5000, "tags": ["deploy"], "time": 1},
                {"id": 2, "text": "short", "time": 2},
            ],
        )

    now = 1_760_000_000_000
    rows = await _client(handler).list_annotations(now - 3600_000, now, 10)
    assert len(rows) == 2
    assert len(rows[0].text) <= 2001
    assert seen[0].url.params["from"] == str(now - 3600_000)
    assert seen[0].url.params["limit"] == "10"


async def test_query_is_range_and_point_capped_and_the_response_is_summarised() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {
                                    "name": "vector(1)",
                                    "fields": [{"name": "Time"}, {"name": "Value"}],
                                },
                                "data": {
                                    "values": [
                                        [1, 2, 3],
                                        [1.0, 1.0, 1.0],
                                    ]
                                },
                            }
                        ]
                    }
                }
            },
        )

    summary = await _client(handler).query_data_source(
        "prom", "vector(1)", 1000, 2000, max_points=100, instant=True
    )
    assert summary.rowCount == 3
    assert summary.fields == ["Time", "Value"]
    body = json.loads(seen[0].content)
    assert body["queries"][0]["datasource"] == {"uid": "prom"}
    assert body["queries"][0]["maxDataPoints"] == 100
    assert body["queries"][0]["instant"] is True


async def test_a_query_error_is_reported_not_swallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"A": {"error": "bad query"}}})

    with pytest.raises(ConnectorAPIError) as caught:
        await _client(handler).query_data_source("prom", "nope", 1, 2, max_points=10, instant=True)
    assert caught.value.hint == "bad query"


async def test_tool_bounds_refuse_a_window_that_is_too_wide() -> None:
    http = _grant_http(lambda request: httpx.Response(200, json=[]))
    day = 24 * 3600 * 1000
    with pytest.raises(ValueError):
        await list_annotations(http, _grant(), from_ms=0, to_ms=31 * day)
    with pytest.raises(ValueError):
        await query_data_source(http, _grant(), datasource_uid="prom", expression="up", from_ms=0, to_ms=25 * 3600 * 1000)


async def test_tool_bounds_clip_search_and_points() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/search":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"results": {"A": {"frames": []}}})

    http = _grant_http(handler)
    await search_dashboards(http, _grant(), query="x", limit=9999)
    assert seen[0].url.params["limit"] == "100"
    await query_data_source(http, _grant(), datasource_uid="prom", expression="up", max_points=99999)
    assert json.loads(seen[1].content)["queries"][0]["maxDataPoints"] == 1000


async def test_get_dashboard_is_reached_through_the_tools_boundary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"dashboard": {"uid": "abc", "title": "T"}, "meta": {}})

    summary = await get_dashboard(_grant_http(handler), _grant(), "abc")
    assert summary.title == "T"
