"""Read-only GitHub operations. Each takes a grant resolved from the caller's principal."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from github.client import (
    FileEntry,
    GitHubClient,
    GitHubUser,
    Issue,
    IssueComment,
    Page,
    PullRequest,
    PullRequestFile,
    TokenSource,
)


@dataclass(frozen=True)
class AccountGrant:
    """A connected GitHub account the principal may use.

    `tokens` is the server-owned token source: it refreshes the expiring grant on demand and is the
    only thing that can see the stored credential. The connector package never receives a refresh
    token it could persist itself.
    """

    account_id: str
    external_account: str
    tokens: TokenSource


def _client(http: httpx.AsyncClient, grant: AccountGrant) -> GitHubClient:
    return GitHubClient(http, grant.tokens)


async def get_current_user(http: httpx.AsyncClient, grant: AccountGrant) -> GitHubUser:
    return await _client(http, grant).get_current_user()


async def list_repositories(
    http: httpx.AsyncClient, grant: AccountGrant, *, max_results: int = 100
) -> Page:
    return await _client(http, grant).list_repositories(max_results=max_results)


async def get_file_contents(
    http: httpx.AsyncClient, grant: AccountGrant, repo: str, path: str, *, ref: str | None = None
) -> FileEntry | list[FileEntry]:
    return await _client(http, grant).get_file_contents(repo, path, ref=ref)


async def search_issues_and_prs(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    query: str,
    *,
    kind: str = "any",
    max_results: int = 30,
) -> Page:
    return await _client(http, grant).search_issues_and_prs(
        query, kind=kind, max_results=max_results
    )


async def list_issues(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    repo: str,
    *,
    state: str = "open",
    labels: str | None = None,
    max_results: int = 30,
) -> Page:
    return await _client(http, grant).list_issues(
        repo, state=state, labels=labels, max_results=max_results
    )


async def get_issue(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    repo: str,
    number: int,
    *,
    comment_limit: int = 30,
) -> tuple[Issue, str | None, list[IssueComment], bool]:
    return await _client(http, grant).get_issue(repo, number, comment_limit=comment_limit)


async def list_pull_requests(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    repo: str,
    *,
    state: str = "open",
    max_results: int = 30,
) -> Page:
    return await _client(http, grant).list_pull_requests(
        repo, state=state, max_results=max_results
    )


async def get_pull_request(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    repo: str,
    number: int,
    *,
    file_limit: int = 100,
) -> tuple[PullRequest, str | None, list[PullRequestFile], bool]:
    return await _client(http, grant).get_pull_request(repo, number, file_limit=file_limit)


__all__ = [
    "AccountGrant",
    "get_current_user",
    "get_file_contents",
    "get_issue",
    "get_pull_request",
    "list_issues",
    "list_pull_requests",
    "list_repositories",
    "search_issues_and_prs",
]
