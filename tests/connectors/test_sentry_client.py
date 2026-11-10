"""Sentry REST client: array bodies, Link-header pagination, org pinning, one-shot 401 recovery."""

from __future__ import annotations

import httpx
import pytest
from sentry.client import SentryClient
from sentry.errors import ConnectorAPIError, ConnectorAuthError
from sentry.tools import AccountGrant, list_issues, list_projects

API = "https://sentry.io/api/0"
ORG = "hoja"


class _Tokens:
    def __init__(self, org: str = ORG, token: str = "access-1") -> None:
        self.org = org
        self.token = token
        self.invalidated = 0
        self.handed_out: list[str] = []

    async def access_token(self) -> str:
        self.handed_out.append(self.token)
        return self.token

    async def invalidate(self) -> None:
        self.invalidated += 1

    def organization(self) -> str:
        return self.org


class _Refreshing(_Tokens):
    async def invalidate(self) -> None:
        self.invalidated += 1
        self.token = "access-2"


def _client(handler, tokens=None) -> tuple[SentryClient, _Tokens]:
    tokens = tokens or _Tokens()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SentryClient(http, tokens), tokens


def _issue(issue_id: str = "42", **overrides) -> dict:
    body = {
        "id": issue_id,
        "shortId": "HOJA-1",
        "title": "TypeError: cannot read property 'id' of undefined",
        "culprit": "app/handlers/issue.py in get_issue",
        "level": "error",
        "status": "unresolved",
        "count": "150",
        "userCount": 12,
        "firstSeen": "2026-11-01T10:00:00Z",
        "lastSeen": "2026-11-09T10:00:00Z",
        "permalink": "https://hoja.sentry.io/issues/42/",
        "project": {"id": "2", "slug": "hoja-web", "name": "Hoja Web"},
        "metadata": {"title": "TypeError: cannot read property 'id' of undefined"},
    }
    body.update(overrides)
    return body


def _link(next_url: str | None, results: bool) -> str:
    parts = ['<https://sentry.io/api/0/organizations/hoja/issues/?cursor=0:0:1>; rel="previous"; results="false"']
    if next_url:
        parts.append(
            f'<{next_url}>; rel="next"; results="{"true" if results else "false"}"; cursor="0:100:0"'
        )
    return ", ".join(parts)


async def test_get_organization_reads_the_single_scoped_org() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{API}/organizations/"
        return httpx.Response(200, json=[{"id": "1", "slug": "hoja", "name": "Hoja"}])

    client, _ = _client(handler)
    organization = await client.get_organization()
    assert organization.slug == "hoja"
    assert organization.name == "Hoja"


async def test_list_projects_is_org_scoped_and_maps_the_array_body() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "2",
                    "slug": "hoja-web",
                    "name": "Hoja Web",
                    "platform": "python",
                    "dateCreated": "2026-11-09T19:44:00Z",
                }
            ],
        )

    client, _ = _client(handler)
    projects = await client.list_projects()
    assert seen["url"].startswith(f"{API}/organizations/{ORG}/projects/")
    assert projects[0].slug == "hoja-web"
    assert projects[0].platform == "python"


async def test_list_issues_reads_the_link_header_and_stops_on_results_false() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if len(urls) == 1:
            return httpx.Response(
                200,
                json=[_issue("1"), _issue("2")],
                headers={
                    "link": _link(f"{API}/organizations/{ORG}/issues/?cursor=0:100:0", True)
                },
            )
        return httpx.Response(
            200,
            json=[_issue("3")],
            headers={"link": _link(None, False)},
        )

    client, _ = _client(handler)
    issues = await client.list_issues(max_results=10)
    assert [issue.id for issue in issues] == ["1", "2", "3"]
    assert len(urls) == 2
    assert "cursor=0:100:0" in urls[1]
    # The first page asked for at most the provider maximum, never more.
    assert "limit=10" in urls[0]


async def test_a_next_link_with_results_false_is_not_followed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json=[_issue("1")],
            headers={"link": _link(f"{API}/organizations/{ORG}/issues/?cursor=0:100:0", False)},
        )

    client, _ = _client(handler)
    issues = await client.list_issues()
    assert len(issues) == 1
    assert len(calls) == 1


async def test_the_page_cap_stops_a_provider_that_always_advertises_more() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json=[_issue("1")],
            headers={
                "link": _link(f"{API}/organizations/{ORG}/issues/?cursor=0:100:0", True)
            },
        )

    client, _ = _client(handler)
    issues = await client.list_issues(max_results=500)
    assert len(calls) == 10  # _MAX_PAGES
    assert len(issues) == 10


async def test_max_results_caps_the_collection_even_across_one_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_issue(str(i)) for i in range(1, 30)])

    client, _ = _client(handler)
    issues = await client.list_issues(max_results=5)
    assert len(issues) == 5


async def test_an_omitted_query_leaves_sentrys_default_and_an_empty_one_clears_it() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    client, _ = _client(handler)
    await client.list_issues()
    await client.list_issues(query="")
    await client.list_issues(query="is:unresolved level:error")
    assert "query" not in seen[0]
    assert "query=" in seen[1]
    assert "level%3Aerror" in seen[2] or "level:error" in seen[2]


async def test_a_project_filter_is_a_slug_not_a_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    client, _ = _client(handler)
    await client.list_issues(project="hoja-web")
    assert "project=hoja-web" in seen[0]
    with pytest.raises(ValueError):
        await client.list_issues(project="../../organizations/other/")


async def test_an_unknown_sort_is_refused_before_the_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    client, _ = _client(handler)
    with pytest.raises(ValueError):
        await client.list_issues(sort="alphabetical")


async def test_get_issue_and_latest_event_stay_inside_the_pinned_org() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/events/latest/"):
            return httpx.Response(
                200,
                json={
                    "eventID": "deadbeef",
                    "dateCreated": "2026-11-09T10:00:00Z",
                    "title": "TypeError",
                    "message": "cannot read property 'id' of undefined",
                    "platform": "javascript",
                    "tags": [{"key": "environment", "value": "production"}],
                    "entries": [
                        {
                            "type": "exception",
                            "data": {"values": [{"type": "TypeError"}]},
                        }
                    ],
                },
            )
        return httpx.Response(200, json=_issue("42"))

    client, _ = _client(handler)
    await client.get_issue("42")
    event = await client.get_latest_event("42")
    assert seen[0] == f"{API}/organizations/{ORG}/issues/42/"
    assert seen[1] == f"{API}/organizations/{ORG}/issues/42/events/latest/"
    assert event.event_id == "deadbeef"
    assert event.tags[0].key == "environment"
    assert event.exception_types == ["TypeError"]


@pytest.mark.parametrize("issue_id", ["", "abc", "42/../../organizations/x", "-1", "1e3"])
async def test_an_issue_id_that_is_not_numeric_never_reaches_a_url(issue_id: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    client, _ = _client(handler)
    with pytest.raises(ValueError):
        await client.get_issue(issue_id)


async def test_a_401_is_retried_exactly_once_with_a_fresh_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        if len(calls) == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json=[_issue("1")])

    tokens = _Refreshing()
    client, _ = _client(handler, tokens)
    issues = await client.list_issues()
    assert len(issues) == 1
    assert tokens.invalidated == 1
    assert calls == ["Bearer access-1", "Bearer access-2"]


async def test_a_second_401_is_a_credential_problem_not_a_retry_loop() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        return httpx.Response(401, json={"detail": "expired"})

    client, _ = _client(handler, _Refreshing())
    with pytest.raises(ConnectorAuthError):
        await client.list_issues()
    assert len(calls) == 2


async def test_a_403_means_the_grant_lacks_a_scope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "You do not have permission"})

    client, _ = _client(handler)
    with pytest.raises(ConnectorAPIError) as excinfo:
        await client.list_issues()
    assert "scope" in str(excinfo.value).lower()


async def test_a_429_names_the_wait() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "30"})

    client, _ = _client(handler)
    with pytest.raises(ConnectorAPIError) as excinfo:
        await client.list_issues()
    assert "30" in (excinfo.value.hint or "")


async def test_event_text_is_truncated_so_one_issue_cannot_flood_a_turn() -> None:
    huge = "x" * 10_000

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"eventID": "e1", "message": huge, "tags": [{"key": "k", "value": huge}]},
        )

    client, _ = _client(handler)
    event = await client.get_latest_event("1")
    assert len(event.message or "") <= 2001
    assert len(event.tags[0].value) <= 2001


async def test_a_grant_without_an_organization_is_refused_not_guessed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    client, _ = _client(handler, _Tokens(org=""))
    with pytest.raises(ConnectorAuthError):
        await client.list_projects()


async def test_the_tools_pass_the_grant_through_untouched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(f"{API}/organizations/{ORG}/projects/")
        return httpx.Response(200, json=[{"id": "2", "slug": "hoja-web", "name": "Hoja Web"}])

    tokens = _Tokens()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    grant = AccountGrant(account_id="a1", external_account=ORG, tokens=tokens)
    projects = await list_projects(http, grant)
    assert projects[0].slug == "hoja-web"


async def test_list_issues_tool_forwards_the_query() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[_issue("7")])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    grant = AccountGrant(account_id="a1", external_account=ORG, tokens=_Tokens())
    issues = await list_issues(http, grant, query="assigned:me")
    assert issues[0].id == "7"
    assert "assigned" in seen[0]
