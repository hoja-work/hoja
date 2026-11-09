"""Read-only Grafana HTTP client. Every method returns typed, bounded models.

Shapes follow Grafana's own OpenAPI document (`public/api-merged.json`, grafana/grafana) and the
provisioning probes in `run-38368f5b9c3f8d03b0e0/evidence/verify/`. Four measured behaviours shape
this file:

* **Auth is a Bearer service-account token and there is no OAuth token endpoint.** A 401 means the
  token was revoked or expired and the account must be reconnected; it is never retried as if it
  were transient. A 403 carries `Permissions needed: ...`, which is surfaced verbatim.
* **Datasource secrets are masked** as `secureJsonFields: {name: true}`. Those booleans are dropped
  from the summary and `secureJsonData` is never requested or returned.
* **`/api/ds/query` executes with the data source's own credentials.** The response is summarised
  and truncated before it can reach a model.
* **Redirects are not followed.** The customer URL is validated and the resolved address is pinned;
  a 3xx is an error, because following it would escape both the pin and the origin.

Every request re-validates and re-resolves the stored origin, then connects to the checked address
with the customer's Host header and TLS SNI. See `grafana.ssrf`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from grafana.config import MAX_TEXT_CHARS
from grafana.errors import ConnectorAPIError, ConnectorAuthError
from grafana.ssrf import Instance, Resolver, pinned_target, validate_instance_url

_UID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
_REF_ID = "A"


class TokenSource(Protocol):
    """The stored credential for one connected instance, owned by the server, not this package."""

    async def access_token(self) -> str:
        """The service-account token."""

    async def invalidate(self) -> None:
        """Tell the source the token it handed out was rejected."""


class GrafanaUser(BaseModel):
    login: str = ""
    name: str | None = None
    email: str | None = None
    uid: str | None = None
    isGrafanaAdmin: bool = False

    @property
    def label(self) -> str:
        return self.login or self.name or self.uid or "grafana"


class DashboardHit(BaseModel):
    uid: str = ""
    title: str = ""
    url: str = ""
    type: str = "dash-db"
    folderTitle: str | None = None
    tags: list[str] = Field(default_factory=list)


class PanelSummary(BaseModel):
    id: int | None = None
    title: str = ""
    type: str = ""
    description: str = ""


class DashboardSummary(BaseModel):
    uid: str = ""
    title: str = ""
    folderUid: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    variables: list[str] = Field(default_factory=list)
    panels: list[PanelSummary] = Field(default_factory=list)
    panelCount: int = 0


class DataSourceSummary(BaseModel):
    uid: str = ""
    name: str = ""
    type: str = ""
    url: str | None = None
    isDefault: bool = False
    readOnly: bool = False


class AlertRuleSummary(BaseModel):
    uid: str = ""
    title: str = ""
    folderUid: str | None = None
    group: str = ""
    state: str = "unknown"
    health: str = "unknown"
    labels: dict[str, str] = Field(default_factory=dict)
    lastEvaluation: str | None = None


class AnnotationSummary(BaseModel):
    id: int | None = None
    dashboardUID: str | None = None
    panelId: int | None = None
    time: int | None = None
    timeEnd: int | None = None
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    login: str | None = None


class QuerySummary(BaseModel):
    refId: str = _REF_ID
    series: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    rowCount: int = 0
    truncated: bool = False
    rows: list[list[Any]] = Field(default_factory=list)


def _clip(value: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    text = value or ""
    return text if len(text) <= limit else text[:limit] + "…"


class GrafanaClient:
    """One client per call. `url` is the customer's origin; it is re-validated per request."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: TokenSource,
        url: str,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._url = url
        self._resolver = resolver

    def _instance(self) -> Instance:
        return validate_instance_url(self._url, resolver=self._resolver)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        instance = self._instance()
        token = await self._tokens.access_token()
        target, headers, extensions = pinned_target(instance, path)
        headers = {
            **headers,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            response = await self._http.request(
                method,
                target,
                params=params,
                json=json,
                headers=headers,
                extensions=extensions,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ConnectorAPIError(f"Could not reach Grafana: {exc}") from exc

        if response.status_code == 401:
            await self._tokens.invalidate()
            raise ConnectorAuthError(
                "Grafana rejected the service-account token.",
                hint="The token was revoked or expired. Create a new token and reconnect Grafana.",
            )
        if response.status_code == 403:
            raise ConnectorAuthError(
                "This Grafana token lacks the permission for that read.",
                hint=_permission_hint(response),
            )
        if response.status_code in (301, 302, 303, 307, 308):
            raise ConnectorAPIError(
                "Grafana answered with a redirect; it was not followed.",
                hint="Use the instance's canonical public URL.",
            )
        if response.status_code >= 400:
            raise ConnectorAPIError(
                f"Grafana returned HTTP {response.status_code}.",
                hint=_body_message(response),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorAPIError("Grafana returned a non-JSON response.") from exc

    async def get_current_user(self) -> GrafanaUser:
        return GrafanaUser.model_validate(await self._request("GET", "/api/user"))

    async def search_dashboards(self, query: str = "", limit: int = 50) -> list[DashboardHit]:
        rows = await self._request(
            "GET",
            "/api/search",
            params={"type": "dash-db", "limit": limit, **({"query": query} if query else {})},
        )
        return [
            DashboardHit(
                uid=str(row.get("uid") or ""),
                title=_clip(row.get("title")),
                url=str(row.get("url") or ""),
                type=str(row.get("type") or "dash-db"),
                folderTitle=_clip(row.get("folderTitle")) or None,
                tags=[_clip(tag, 80) for tag in (row.get("tags") or [])][:20],
            )
            for row in rows
            if isinstance(row, dict)
        ][:limit]

    async def get_dashboard(self, uid: str) -> DashboardSummary:
        if not _UID_PATTERN.match(uid):
            raise ValueError("A dashboard uid is letters, digits, '-' and '_' only.")
        payload = await self._request("GET", f"/api/dashboards/uid/{quote(uid)}")
        dashboard = payload.get("dashboard") or {}
        meta = payload.get("meta") or {}
        panels = []
        for panel in (dashboard.get("panels") or [])[:200]:
            if not isinstance(panel, dict):
                continue
            panels.append(
                PanelSummary(
                    id=panel.get("id"),
                    title=_clip(panel.get("title"), 300),
                    type=_clip(panel.get("type"), 80),
                    description=_clip(panel.get("description")),
                )
            )
        return DashboardSummary(
            uid=str(dashboard.get("uid") or uid),
            title=_clip(dashboard.get("title"), 300),
            folderUid=meta.get("folderUid"),
            tags=[_clip(tag, 80) for tag in (dashboard.get("tags") or [])][:20],
            description=_clip(dashboard.get("description")),
            variables=[
                _clip(item.get("name"), 80)
                for item in (dashboard.get("templating", {}).get("list") or [])
                if isinstance(item, dict)
            ][:50],
            panels=panels,
            panelCount=len(dashboard.get("panels") or []),
        )

    async def list_data_sources(self) -> list[DataSourceSummary]:
        rows = await self._request("GET", "/api/datasources")
        return [
            DataSourceSummary(
                uid=str(row.get("uid") or ""),
                name=_clip(row.get("name"), 300),
                type=_clip(row.get("type"), 120),
                url=_clip(row.get("url"), 500) or None,
                isDefault=bool(row.get("isDefault")),
                readOnly=bool(row.get("readOnly")),
            )
            for row in rows
            if isinstance(row, dict)
        ]

    async def list_alert_rules(self) -> list[AlertRuleSummary]:
        """Current state comes from the Prometheus-compatible rules endpoint.

        That endpoint carries `state` and `health`; the provisioning endpoint does not. A Grafana
        whose rules endpoint is unavailable falls back to provisioning rules with an explicit
        `unknown` state rather than pretending the state is healthy.
        """
        try:
            payload = await self._request("GET", "/api/prometheus/grafana/api/v1/rules")
        except ConnectorAPIError:
            payload = None
        if payload is not None:
            summaries: list[AlertRuleSummary] = []
            for group in (payload.get("data", {}).get("groups") or []):
                if not isinstance(group, dict):
                    continue
                for rule in group.get("rules") or []:
                    if not isinstance(rule, dict):
                        continue
                    summaries.append(
                        AlertRuleSummary(
                            uid=str(rule.get("uid") or ""),
                            title=_clip(rule.get("name"), 300),
                            folderUid=rule.get("folderUid"),
                            group=_clip(group.get("name"), 200),
                            state=str(rule.get("state") or "unknown"),
                            health=str(rule.get("health") or "unknown"),
                            labels={
                                _clip(k, 80): _clip(v, 200)
                                for k, v in (rule.get("labels") or {}).items()
                            },
                            lastEvaluation=rule.get("lastEvaluation"),
                        )
                    )
            return summaries
        rows = await self._request("GET", "/api/v1/provisioning/alert-rules")
        return [
            AlertRuleSummary(
                uid=str(row.get("uid") or ""),
                title=_clip(row.get("title"), 300),
                folderUid=row.get("folderUID"),
                group=_clip(row.get("ruleGroup"), 200),
                state="unknown",
                health="unknown",
                labels={_clip(k, 80): _clip(v, 200) for k, v in (row.get("labels") or {}).items()},
            )
            for row in rows
            if isinstance(row, dict)
        ]

    async def list_annotations(self, from_ms: int, to_ms: int, limit: int) -> list[AnnotationSummary]:
        rows = await self._request(
            "GET",
            "/api/annotations",
            params={"from": from_ms, "to": to_ms, "limit": limit},
        )
        return [
            AnnotationSummary(
                id=row.get("id"),
                dashboardUID=row.get("dashboardUID"),
                panelId=row.get("panelId"),
                time=row.get("time"),
                timeEnd=row.get("timeEnd"),
                text=_clip(row.get("text")),
                tags=[_clip(tag, 80) for tag in (row.get("tags") or [])][:20],
                login=row.get("login"),
            )
            for row in rows
            if isinstance(row, dict)
        ][:limit]

    async def query_data_source(
        self,
        datasource_uid: str,
        expression: str,
        from_ms: int,
        to_ms: int,
        *,
        max_points: int,
        instant: bool,
    ) -> QuerySummary:
        if not _UID_PATTERN.match(datasource_uid):
            raise ValueError("A data source uid is letters, digits, '-' and '_' only.")
        interval_ms = max(1000, (to_ms - from_ms) // max(1, max_points))
        body = {
            "queries": [
                {
                    "refId": _REF_ID,
                    "datasource": {"uid": datasource_uid},
                    "expr": expression,
                    "instant": instant,
                    "range": not instant,
                    "maxDataPoints": max_points,
                    "intervalMs": interval_ms,
                }
            ],
            "from": str(from_ms),
            "to": str(to_ms),
        }
        payload = await self._request("POST", "/api/ds/query", json=body)
        result = (payload.get("results") or {}).get(_REF_ID) or {}
        if result.get("error"):
            raise ConnectorAPIError(
                "Grafana could not run that query.",
                hint=_clip(str(result.get("error")), 500),
            )
        frames = result.get("frames") or []
        series: list[str] = []
        fields: list[str] = []
        rows: list[list[Any]] = []
        row_count = 0
        for frame in frames:
            schema = (frame.get("schema") or {}).get("fields") or []
            names = [str(field.get("name") or "") for field in schema]
            if names:
                fields.extend(name for name in names if name and name not in fields)
            series.append(str((frame.get("schema") or {}).get("name") or "series"))
            values = (frame.get("data") or {}).get("values") or []
            columns = [column for column in values if isinstance(column, list)]
            if columns:
                row_count += max(len(column) for column in columns)
                for index in range(min(len(columns[0]), 50)):
                    rows.append([column[index] if index < len(column) else None for column in columns])
        return QuerySummary(
            refId=_REF_ID,
            series=series[:50],
            fields=fields[:50],
            rowCount=row_count,
            truncated=row_count > len(rows),
            rows=rows[:50],
        )


def _permission_hint(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "Reconnect with a service-account token that has the Viewer role."
    message = str(body.get("message") or "")
    access_id = str(body.get("accessErrorId") or "")
    hint = message or "Reconnect with a service-account token that has the Viewer role."
    return f"{hint} (reference {access_id})" if access_id else hint


def _body_message(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    message = body.get("message") if isinstance(body, dict) else None
    return _clip(str(message), 500) if message else None


ClientFactory = Callable[[httpx.AsyncClient, TokenSource, str], GrafanaClient]
