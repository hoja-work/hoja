"""The GitHub REST client: mapping, bounds, pagination and the error taxonomy a user can act on."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from github.client import GitHubClient
from github.errors import ConnectorAPIError, ConnectorAuthError

API = "https://api.github.com"
USER = f"{API}/user"
INSTALLATIONS = f"{API}/user/installations"
REPO = "hoja-work/hoja-cloud"


class _Tokens:
    """A token source that can be told to expire once, so the 401 retry is observable."""

    def __init__(self, *, tokens: list[str] | None = None) -> None:
        self._tokens = tokens or ["ghu_one"]
        self.invalidated = 0
        self.calls = 0

    async def access_token(self) -> str:
        value = self._tokens[min(self.calls, len(self._tokens) - 1)]
        self.calls += 1
        return value

    async def invalidate(self) -> None:
        self.invalidated += 1

    def api_base(self) -> str:
        return API


def _client(tokens: _Tokens | None = None) -> tuple[GitHubClient, _Tokens]:
    source = tokens or _Tokens()
    return GitHubClient(httpx.AsyncClient(), source), source


@respx.mock
async def test_current_user_maps_the_profile_fields() -> None:
    respx.get(USER).mock(
        return_value=httpx.Response(
            200,
            json={
                "login": "rinadelph",
                "id": 68679193,
                "name": "Rinadelph",
                "email": "r@hoja.work",
                "html_url": "https://github.com/rinadelph",
                "type": "User",
                "company": "Hoja",
            },
        )
    )
    client, _ = _client()
    user = await client.get_current_user()
    assert user.login == "rinadelph"
    assert user.id == 68679193
    assert user.name == "Rinadelph"


@respx.mock
async def test_repositories_are_gathered_from_installations_and_deduplicated() -> None:
    respx.get(INSTALLATIONS).mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "installations": [
                    {"id": 1, "account": {"login": "hoja-work"}, "repository_selection": "selected"},
                    {"id": 2, "account": {"login": "rinadelph"}, "repository_selection": "all"},
                ],
            },
        )
    )
    respx.get(f"{INSTALLATIONS}/1/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "repositories": [
                    {"id": 10, "full_name": REPO, "private": True, "default_branch": "main"},
                    {"id": 11, "full_name": "hoja-work/hoja", "private": True},
                ],
            },
        )
    )
    respx.get(f"{INSTALLATIONS}/2/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                # The same repository from a second installation, with different casing.
                "repositories": [{"id": 10, "full_name": REPO.upper(), "private": True}],
            },
        )
    )
    client, _ = _client()
    page = await client.list_repositories(max_results=10)
    names = [repo.full_name for repo in page.items]
    assert len(names) == 2
    assert page.truncated is False


@respx.mock
async def test_repository_listing_is_bounded_and_reports_truncation() -> None:
    respx.get(INSTALLATIONS).mock(
        return_value=httpx.Response(200, json={"installations": [{"id": 1}]})
    )
    respx.get(f"{INSTALLATIONS}/1/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "repositories": [
                    {"id": index, "full_name": f"hoja-work/repo-{index}"} for index in range(2)
                ]
            },
        )
    )
    client, _ = _client()
    page = await client.list_repositories(max_results=2)
    assert len(page.items) == 2
    assert page.truncated is True


@respx.mock
async def test_a_text_file_is_decoded_and_a_binary_file_is_flagged() -> None:
    text = base64.b64encode(b"# Hoja\n\nread me\n").decode()
    respx.get(f"{API}/repos/{REPO}/contents/README.md").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "file",
                "path": "README.md",
                "sha": "abc",
                "size": 18,
                "encoding": "base64",
                "content": text,
                "html_url": f"https://github.com/{REPO}/blob/main/README.md",
            },
        )
    )
    client, _ = _client()
    entry = await client.get_file_contents(REPO, "README.md")
    assert entry.text == "# Hoja\n\nread me\n"
    assert entry.binary is False


@respx.mock
async def test_a_binary_file_is_reported_without_text() -> None:
    content = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00").decode()
    respx.get(f"{API}/repos/{REPO}/contents/logo.png").mock(
        return_value=httpx.Response(
            200,
            json={"type": "file", "path": "logo.png", "encoding": "base64", "content": content},
        )
    )
    client, _ = _client()
    entry = await client.get_file_contents(REPO, "logo.png")
    assert entry.binary is True
    assert entry.text is None


@respx.mock
async def test_a_file_larger_than_the_api_returns_is_reported_as_truncated() -> None:
    respx.get(f"{API}/repos/{REPO}/contents/package-lock.json").mock(
        return_value=httpx.Response(
            200, json={"type": "file", "path": "package-lock.json", "size": 4_000_000}
        )
    )
    client, _ = _client()
    entry = await client.get_file_contents(REPO, "package-lock.json")
    assert entry.truncated is True
    assert entry.text is None


@respx.mock
async def test_a_directory_path_returns_its_entries() -> None:
    respx.get(f"{API}/repos/{REPO}/contents/app").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"type": "file", "path": "app/pyproject.toml", "size": 10},
                {"type": "dir", "path": "app/connectors"},
            ],
        )
    )
    client, _ = _client()
    entries = await client.get_file_contents(REPO, "app")
    assert [entry.path for entry in entries] == ["app/pyproject.toml", "app/connectors"]


@respx.mock
async def test_the_repository_root_lists_the_top_level() -> None:
    route = respx.get(f"{API}/repos/{REPO}/contents").mock(
        return_value=httpx.Response(200, json=[{"type": "dir", "path": "app"}])
    )
    client, _ = _client()
    entries = await client.get_file_contents(REPO, "")
    assert route.called
    assert entries[0].path == "app"


async def test_a_path_that_cannot_be_one_is_refused_before_the_request() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        await client.get_file_contents(REPO, "../../etc/passwd")


async def test_a_repository_that_is_not_owner_name_is_refused() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        await client.list_issues("just-a-name")


@respx.mock
async def test_list_issues_drops_pull_requests_that_the_endpoint_mixes_in() -> None:
    respx.get(f"{API}/repos/{REPO}/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 1, "title": "an issue", "state": "open", "user": {"login": "a"}},
                {
                    "number": 2,
                    "title": "a pull request",
                    "state": "open",
                    "pull_request": {"url": "x"},
                },
            ],
        )
    )
    client, _ = _client()
    page = await client.list_issues(REPO)
    assert [issue.number for issue in page.items] == [1]


@respx.mock
async def test_get_issue_returns_the_body_and_comments() -> None:
    respx.get(f"{API}/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "Crash on save",
                "state": "open",
                "body": "Steps to reproduce ...",
                "user": {"login": "reporter"},
                "labels": [{"name": "bug"}, {"name": "p1"}],
                "comments": 1,
            },
        )
    )
    respx.get(f"{API}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 99,
                    "user": {"login": "maintainer"},
                    "body": "Fixed in #8",
                    "created_at": "2026-11-09T00:00:00Z",
                }
            ],
        )
    )
    client, _ = _client()
    issue, body, comments, truncated = await client.get_issue(REPO, 7)
    assert issue.labels == ["bug", "p1"]
    assert body.startswith("Steps")
    assert comments[0].body == "Fixed in #8"
    assert truncated is False


@respx.mock
async def test_asking_for_a_pull_request_as_an_issue_points_at_the_right_tool() -> None:
    respx.get(f"{API}/repos/{REPO}/issues/9").mock(
        return_value=httpx.Response(
            200, json={"number": 9, "title": "a PR", "pull_request": {"url": "x"}}
        )
    )
    client, _ = _client()
    with pytest.raises(ConnectorAPIError) as caught:
        await client.get_issue(REPO, 9)
    assert "pull request" in str(caught.value).lower()


@respx.mock
async def test_pull_requests_are_listed_with_their_heads_and_states() -> None:
    respx.get(f"{API}/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 80,
                    "title": "feat: github connector",
                    "state": "open",
                    "draft": True,
                    "user": {"login": "rinadelph"},
                    "head": {"ref": "feat/connector-github"},
                    "base": {"ref": "feat/connector-grafana"},
                }
            ],
        )
    )
    client, _ = _client()
    page = await client.list_pull_requests(REPO)
    pull = page.items[0]
    assert pull.draft is True
    assert pull.head == "feat/connector-github"
    assert pull.base == "feat/connector-grafana"


@respx.mock
async def test_one_pull_request_returns_a_files_summary() -> None:
    respx.get(f"{API}/repos/{REPO}/pulls/80").mock(
        return_value=httpx.Response(
            200,
            json={"number": 80, "title": "feat", "state": "open", "body": "what changed"},
        )
    )
    respx.get(f"{API}/repos/{REPO}/pulls/80/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"filename": "app/x.py", "status": "added", "additions": 5, "deletions": 0, "changes": 5},
                {"filename": "app/y.py", "status": "modified", "additions": 1, "deletions": 1, "changes": 2},
            ],
        )
    )
    client, _ = _client()
    _pull, body, files, truncated = await client.get_pull_request(REPO, 80)
    assert body == "what changed"
    assert [item.filename for item in files] == ["app/x.py", "app/y.py"]
    assert files[1].changes == 2
    assert truncated is False


@respx.mock
async def test_search_narrows_to_issues_or_pull_requests_and_reports_the_repository() -> None:
    route = respx.get(f"{API}/search/issues").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "number": 12,
                        "title": "flaky test",
                        "state": "open",
                        "repository_url": f"{API}/repos/{REPO}",
                    }
                ],
            },
        )
    )
    client, _ = _client()
    page = await client.search_issues_and_prs("flaky", kind="pr")
    assert route.calls[0].request.url.params["q"] == "flaky is:pr"
    assert page.items[0]["repository"] == REPO


@respx.mock
async def test_an_expired_token_is_retried_once_through_the_source() -> None:
    route = respx.get(USER).mock(
        side_effect=[
            httpx.Response(401, json={"message": "Bad credentials"}),
            httpx.Response(200, json={"login": "rinadelph", "id": 1}),
        ]
    )
    client, source = _client(_Tokens(tokens=["ghu_stale", "ghu_fresh"]))
    user = await client.get_current_user()
    assert user.login == "rinadelph"
    assert source.invalidated == 1
    assert route.call_count == 2


@respx.mock
async def test_a_token_that_still_fails_after_the_retry_means_reconnect() -> None:
    respx.get(USER).mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    client, _ = _client()
    with pytest.raises(ConnectorAuthError):
        await client.get_current_user()


@respx.mock
async def test_a_spent_rate_limit_is_reported_with_its_reset_time() -> None:
    respx.get(USER).mock(
        return_value=httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1762732800"},
            json={"message": "API rate limit exceeded"},
        )
    )
    client, _ = _client()
    with pytest.raises(ConnectorAPIError) as caught:
        await client.get_current_user()
    assert "rate-limited" in str(caught.value).lower()
    assert "5,000" in (caught.value.hint or "")


@respx.mock
async def test_a_permission_refusal_is_not_reported_as_a_rate_limit() -> None:
    respx.get(USER).mock(
        return_value=httpx.Response(
            403, headers={"x-ratelimit-remaining": "4999"}, json={"message": "Resource not accessible"}
        )
    )
    client, _ = _client()
    with pytest.raises(ConnectorAPIError) as caught:
        await client.get_current_user()
    assert "rate-limited" not in str(caught.value).lower()
    assert "permissions" in (caught.value.hint or "").lower()


@respx.mock
async def test_a_404_explains_that_private_repositories_need_an_installation() -> None:
    respx.get(f"{API}/repos/{REPO}/contents/secret.md").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    client, _ = _client()
    with pytest.raises(ConnectorAPIError) as caught:
        await client.get_file_contents(REPO, "secret.md")
    assert "install" in (caught.value.hint or "").lower()


@respx.mock
async def test_a_validation_error_carries_githubs_own_message() -> None:
    respx.get(f"{API}/search/issues").mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )
    client, _ = _client()
    with pytest.raises(ConnectorAPIError) as caught:
        await client.search_issues_and_prs("state:nonsense")
    assert "Validation Failed" in str(caught.value)


async def test_an_empty_search_query_is_refused() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        await client.search_issues_and_prs("  ")


async def test_an_unknown_state_is_refused_before_the_request() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        await client.list_issues(REPO, state="merged")
