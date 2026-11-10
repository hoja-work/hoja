"""Read-only Grafana operations. Each takes a grant resolved from the caller's principal.

The bounds live here, not in the client: a caller cannot widen a time range, raise the point cap or
pass an oversized search by reaching the client directly. Every string Grafana returns is content
written by whoever owns that Grafana, so it is truncated on the way out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from grafana.client import (
    AlertRuleSummary,
    AnnotationSummary,
    DashboardHit,
    DashboardSummary,
    DataSourceSummary,
    GrafanaClient,
    GrafanaUser,
    QuerySummary,
    TokenSource,
)
from grafana.config import (
    DEFAULT_DATA_POINTS,
    MAX_ANNOTATION_RANGE_DAYS,
    MAX_ANNOTATIONS,
    MAX_DATA_POINTS,
    MAX_QUERY_RANGE_HOURS,
    MAX_SEARCH_RESULTS,
)

_MAX_EXPRESSION_CHARS = 2000


@dataclass(frozen=True)
class AccountGrant:
    """A connected Grafana instance the principal may use.

    `tokens` is the server-owned token source; this package never receives the sealed token itself.
    `url` is the customer's origin, re-validated and pinned on every request.
    """

    account_id: str
    external_account: str
    url: str
    tokens: TokenSource


def _client(http: httpx.AsyncClient, grant: AccountGrant) -> GrafanaClient:
    return GrafanaClient(http, grant.tokens, grant.url)


async def get_current_user(http: httpx.AsyncClient, grant: AccountGrant) -> GrafanaUser:
    return await _client(http, grant).get_current_user()


async def search_dashboards(
    http: httpx.AsyncClient, grant: AccountGrant, *, query: str = "", limit: int = 50
) -> list[DashboardHit]:
    capped = max(1, min(int(limit), MAX_SEARCH_RESULTS))
    return await _client(http, grant).search_dashboards(query=query.strip()[:200], limit=capped)


async def get_dashboard(
    http: httpx.AsyncClient, grant: AccountGrant, uid: str
) -> DashboardSummary:
    return await _client(http, grant).get_dashboard(uid.strip())


async def list_data_sources(
    http: httpx.AsyncClient, grant: AccountGrant
) -> list[DataSourceSummary]:
    return await _client(http, grant).list_data_sources()


async def list_alert_rules(
    http: httpx.AsyncClient, grant: AccountGrant
) -> list[AlertRuleSummary]:
    return await _client(http, grant).list_alert_rules()


def _epoch(value: int | None, name: str) -> int:
    if value is None:
        raise ValueError(f"`{name}` is required (milliseconds since the Unix epoch).")
    if value < 0:
        raise ValueError(f"`{name}` must be a positive epoch-millisecond value.")
    return int(value)


def _bounded_range(from_ms: int | None, to_ms: int | None, *, hours: int) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    end = _epoch(to_ms, "to_ms") if to_ms is not None else int(now.timestamp() * 1000)
    start = (
        _epoch(from_ms, "from_ms")
        if from_ms is not None
        else int((now - timedelta(hours=hours)).timestamp() * 1000)
    )
    if start >= end:
        raise ValueError("`from_ms` must be before `to_ms`.")
    if end - start > hours * 3600 * 1000:
        raise ValueError(f"The range is limited to {hours} hours.")
    return start, end


async def list_annotations(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    from_ms: int | None = None,
    to_ms: int | None = None,
    limit: int = 100,
) -> list[AnnotationSummary]:
    start, end = _bounded_range(from_ms, to_ms, hours=MAX_ANNOTATION_RANGE_DAYS * 24)
    capped = max(1, min(int(limit), MAX_ANNOTATIONS))
    return await _client(http, grant).list_annotations(start, end, capped)


async def query_data_source(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    datasource_uid: str,
    expression: str,
    from_ms: int | None = None,
    to_ms: int | None = None,
    max_points: int = DEFAULT_DATA_POINTS,
    instant: bool = False,
) -> QuerySummary:
    expr = (expression or "").strip()
    if not expr:
        raise ValueError("`expression` is required.")
    if len(expr) > _MAX_EXPRESSION_CHARS:
        raise ValueError(f"`expression` is limited to {_MAX_EXPRESSION_CHARS} characters.")
    start, end = _bounded_range(from_ms, to_ms, hours=MAX_QUERY_RANGE_HOURS)
    points = max(1, min(int(max_points), MAX_DATA_POINTS))
    return await _client(http, grant).query_data_source(
        datasource_uid.strip(),
        expr,
        start,
        end,
        max_points=points,
        instant=bool(instant),
    )
