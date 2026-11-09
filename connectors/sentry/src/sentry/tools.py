"""Read-only Sentry operations. Each takes a grant resolved from the caller's principal."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from sentry.client import (
    Issue,
    LatestEvent,
    Organization,
    Project,
    SentryClient,
    TokenSource,
)


@dataclass(frozen=True)
class AccountGrant:
    """One connected Sentry organization the principal may use.

    `tokens` is the server-owned token source: it holds the sealed grant, refreshes the eight-hour
    token under a per-account lock, and is the only thing that can see the credential. The
    connector package never receives a refresh token it could persist itself, and the organization
    slug comes from the grant rather than from the model.
    """

    account_id: str
    external_account: str
    tokens: TokenSource


def _client(http: httpx.AsyncClient, grant: AccountGrant) -> SentryClient:
    return SentryClient(http, grant.tokens)


async def get_organization(http: httpx.AsyncClient, grant: AccountGrant) -> Organization:
    return await _client(http, grant).get_organization()


async def list_projects(
    http: httpx.AsyncClient, grant: AccountGrant, *, max_results: int = 100
) -> list[Project]:
    return await _client(http, grant).list_projects(max_results=max_results)


async def list_issues(
    http: httpx.AsyncClient,
    grant: AccountGrant,
    *,
    query: str | None = None,
    project: str | None = None,
    sort: str | None = None,
    stats_period: str | None = None,
    max_results: int = 50,
) -> list[Issue]:
    return await _client(http, grant).list_issues(
        query=query,
        project=project,
        sort=sort,
        stats_period=stats_period,
        max_results=max_results,
    )


async def get_issue(http: httpx.AsyncClient, grant: AccountGrant, issue_id: str) -> Issue:
    return await _client(http, grant).get_issue(issue_id)


async def get_latest_event(
    http: httpx.AsyncClient, grant: AccountGrant, issue_id: str
) -> LatestEvent:
    return await _client(http, grant).get_latest_event(issue_id)
