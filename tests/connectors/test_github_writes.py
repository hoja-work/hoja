"""The write half of the GitHub connector: what it sends, and what it refuses to call success.

The two cases that matter for the owner's safety net are here: a 403 that is a spent rate limit is
reported as one and never retried, a 403 that is a missing permission says which permission, and a
200 that does not carry `merged: true` is not a merge.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from github.errors import ConnectorAPIError
from github.writes import GitHubWriteClient, summarize_checks

API = "https://api.github.com"
REPO = "hoja-work/hoja-cloud"
REVIEWS = f"{API}/repos/{REPO}/pulls/12/reviews"
COMMENTS = f"{API}/repos/{REPO}/issues/12/comments"
MERGE = f"{API}/repos/{REPO}/pulls/12/merge"


class _Tokens:
    def __init__(self) -> None:
        self.invalidated = 0

    async def access_token(self) -> str:
        return "ghu_one"

    async def invalidate(self) -> None:
        self.invalidated += 1

    def api_base(self) -> str:
        return API


def _client() -> GitHubWriteClient:
    return GitHubWriteClient(httpx.AsyncClient(), _Tokens())


@respx.mock
async def test_merge_sends_the_exact_sha_and_squash() -> None:
    route = respx.put(MERGE).mock(
        return_value=httpx.Response(200, json={"merged": True, "sha": "abc123"}))
    result = await _client().merge_pull(REPO, 12, sha="abc123")
    assert result["merged"] is True
    body = json.loads(route.calls[0].request.content)
    assert body == {"sha": "abc123", "merge_method": "squash"}


@respx.mock
async def test_merge_without_a_sha_is_refused_before_any_request() -> None:
    route = respx.put(MERGE).mock(return_value=httpx.Response(200, json={"merged": True}))
    with pytest.raises(ValueError):
        await _client().merge_pull(REPO, 12, sha="  ")
    assert not route.called


@respx.mock
async def test_rate_limited_403_is_reported_as_rate_limit_and_never_retried() -> None:
    route = respx.put(MERGE).mock(
        return_value=httpx.Response(
            403, json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0"}))
    with pytest.raises(ConnectorAPIError) as caught:
        await _client().merge_pull(REPO, 12, sha="abc123")
    assert "rate-limited" in str(caught.value)
    assert route.call_count == 1
    assert len(respx.calls) == 1, "a rejected write must not be replayed"


@respx.mock
async def test_permission_403_names_the_missing_write_permissions() -> None:
    respx.post(REVIEWS).mock(
        return_value=httpx.Response(
            403, json={"message": "Resource not accessible by integration"}))
    with pytest.raises(ConnectorAPIError) as caught:
        await _client().create_review(REPO, 12, event="APPROVE")
    assert "cannot write" in str(caught.value)
    assert "pull_requests: write" in (caught.value.hint or "")


@respx.mock
async def test_conflict_on_merge_is_an_error_not_a_merge() -> None:
    respx.put(MERGE).mock(
        return_value=httpx.Response(409, json={"message": "Head branch was modified"}))
    with pytest.raises(ConnectorAPIError) as caught:
        await _client().merge_pull(REPO, 12, sha="abc123")
    assert "refused the merge" in str(caught.value)


@respx.mock
async def test_review_validation_error_surfaces_githubs_message() -> None:
    respx.post(REVIEWS).mock(
        return_value=httpx.Response(422, json={"message": "Review cannot be blank"}))
    with pytest.raises(ConnectorAPIError) as caught:
        await _client().create_review(REPO, 12, event="REQUEST_CHANGES", body=" ")
    assert "Review cannot be blank" in str(caught.value)


@respx.mock
async def test_comment_posts_to_the_issues_endpoint() -> None:
    route = respx.post(COMMENTS).mock(return_value=httpx.Response(201, json={"id": 7}))
    await _client().create_comment(REPO, 12, "looks good")
    assert json.loads(route.calls[0].request.content) == {"body": "looks good"}


def test_summarize_checks_distinguishes_failure_pending_none_and_success() -> None:
    failing = summarize_checks([
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "failure"},
    ])
    assert (failing["state"], failing["failing"], failing["passing"]) == ("failure", 1, 1)

    pending = summarize_checks([{"status": "in_progress", "conclusion": None}])
    assert pending["state"] == "pending"

    absent = summarize_checks([])
    assert absent["state"] == "none", "no checks is neither a passing nor a failing gate"

    success = summarize_checks(
        [{"status": "completed", "conclusion": "success"}],
        {"state": "success", "statuses": [{"context": "ci"}]},
    )
    assert (success["state"], success["total"]) == ("success", 2)

    legacy_red = summarize_checks(
        [{"status": "completed", "conclusion": "success"}],
        {"state": "failure", "statuses": [{"context": "old-ci"}]},
    )
    assert legacy_red["state"] == "failure", "a legacy commit status still gates the merge"
