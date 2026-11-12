"""The write half of the GitHub connector: reviews, comments, and one guarded merge.

`client.py` is read-only on purpose — it is the surface every connector tool uses, and a read-only
grant cannot do damage through it. The Reviews actions need the other half, so this module adds
exactly the four calls the owner approved (approve, request changes, comment, squash-merge) and
nothing else.

Three rules hold here:

* **One token, no second credential.** Writes ask the same `TokenSource` the reader asks, so an
  eight-hour grant is refreshed in one place and the connector package still never sees a refresh
  token it could persist.
* **No synthetic success.** Every non-2xx response raises. The caller records a merge only when
  GitHub answered 200 with `merged: true`; there is no branch of this code that turns an ambiguous
  answer into "merged".
* **A 403 on a write names the missing permission.** It is either a spent rate limit (carries
  `x-ratelimit-remaining: 0`) or a grant without `pull_requests: write` / `issues: write` /
  `contents: write`. Those two read differently because the fix is different: wait, or change the
  app registration. The provisioned app currently holds read-only repository permissions, so this
  is the error a real write hits first — and the message says so rather than "forbidden".
"""

from __future__ import annotations

from typing import Any

import httpx

from github.client import GitHubClient, TokenSource, _checked_number, _checked_repo, _json
from github.errors import ConnectorAPIError, ConnectorAuthError

APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
COMMENT = "COMMENT"
REVIEW_EVENTS = (APPROVE, REQUEST_CHANGES, COMMENT)

MERGE_METHODS = ("squash", "merge", "rebase")

# Conclusions that mean the commit cannot be shipped as it stands.
_FAILING_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "stale"}
_PENDING_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}


class GitHubWriteClient:
    """Write actions for one connected account, over the reader's own token source.

    Reads go through a composed `GitHubClient` so 404/403/5xx map identically on both halves;
    only the three mutating verbs are implemented here.
    """

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens
        self._read = GitHubClient(http, tokens)

    def _base(self) -> str:
        return self._read._base()

    async def _send(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        """One mutating request, with the reader's single 401 retry and error mapping."""
        token = await self._tokens.access_token()
        url = f"{self._base()}{path}"
        request = {"json": payload, "headers": _headers(token)}
        response = await self._http.request(method, url, **request)
        if response.status_code == 401:
            # Exactly one retry: the token source refreshes on the next access_token() call.
            await self._tokens.invalidate()
            token = await self._tokens.access_token()
            request["headers"] = _headers(token)
            response = await self._http.request(method, url, **request)
        if response.status_code == 401:
            raise ConnectorAuthError(
                "GitHub rejected the stored credential.",
                hint="Reconnect GitHub in Settings > Connections.",
            )
        if response.status_code == 403:
            if (response.headers.get("x-ratelimit-remaining") or "").strip() == "0":
                raise ConnectorAPIError(
                    "GitHub rate-limited this account; the write was not retried.",
                    hint=(
                        "Nothing was sent twice. User tokens allow 5,000 requests an hour; "
                        "try again after the window resets."
                    ),
                )
            raise ConnectorAPIError(
                "This GitHub grant cannot write.",
                hint=(
                    "The app holds read-only repository permissions. Approving, requesting "
                    "changes, commenting and merging need pull_requests: write, issues: write "
                    "and contents: write on the app registration."
                ),
            )
        if response.status_code == 404:
            raise ConnectorAPIError(
                "GitHub has no such pull request for this token, or the token cannot see it.",
                hint="Install the app on that repository, then connect again.",
            )
        if response.status_code == 422:
            body = _json(response, allow_error=False)
            message = body.get("message") if isinstance(body, dict) else None
            errors = body.get("errors") if isinstance(body, dict) else None
            detail = f": {message}" if message else ""
            if errors:
                detail = f"{detail} ({errors})" if detail else f": {errors}"
            raise ConnectorAPIError(f"GitHub rejected the write{detail}")
        if response.status_code == 405:
            raise ConnectorAPIError("GitHub does not allow that action on this pull request.")
        if response.status_code == 409:
            # The merge endpoint's answer when the head moved or the branch is not mergeable.
            raise ConnectorAPIError(
                "GitHub refused the merge: the pull request changed or is not mergeable.",
                hint="Refresh the review; nothing was merged.",
            )
        if response.status_code == 429:
            raise ConnectorAPIError(
                "GitHub rate-limited the request; the write was not retried.",
            )
        if response.status_code >= 400:
            raise ConnectorAPIError(f"GitHub API returned HTTP {response.status_code}.")
        return _json(response)

    async def create_review(
        self, repository: str, number: int, *, event: str, body: str | None = None
    ) -> dict[str, Any]:
        """Submit one review. `event` is APPROVE, REQUEST_CHANGES or COMMENT."""
        repo = _checked_repo(repository)
        pr = _checked_number(number)
        if event not in REVIEW_EVENTS:
            raise ValueError(f"unsupported review event {event!r}")
        payload: dict[str, Any] = {"event": event}
        if body:
            payload["body"] = body
        return await self._send("POST", f"/repos/{repo}/pulls/{pr}/reviews", payload)

    async def create_comment(self, repository: str, number: int, body: str) -> dict[str, Any]:
        """Comment on a pull request (the issues endpoint is the one GitHub accepts)."""
        repo = _checked_repo(repository)
        pr = _checked_number(number)
        if not (body or "").strip():
            raise ValueError("a comment needs a body")
        return await self._send("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})

    async def merge_pull(
        self, repository: str, number: int, *, sha: str, method: str = "squash"
    ) -> dict[str, Any]:
        """Squash-merge exactly `sha`.

        `sha` is sent as GitHub's own optimistic concurrency check: if the head moved since the
        gate was read, GitHub answers 409 and nothing merges. That is the whole reason the
        parameter exists here rather than being optional.
        """
        repo = _checked_repo(repository)
        pr = _checked_number(number)
        if method not in MERGE_METHODS:
            raise ValueError(f"unsupported merge method {method!r}")
        if not (sha or "").strip():
            raise ValueError("a merge must name the commit it merges")
        return await self._send(
            "PUT", f"/repos/{repo}/pulls/{pr}/merge", {"sha": sha, "merge_method": method}
        )

    async def fetch_pull(self, repository: str, number: int) -> dict[str, Any]:
        """The pull request itself, raw.

        Deliberately not `get_pull_request`: that one pages up to 100 changed files, and a merge
        gate needs `head.sha`, `mergeable` and `mergeable_state` from this one object, nothing else.
        """
        repo = _checked_repo(repository)
        pr = _checked_number(number)
        payload = await self._read._get(f"/repos/{repo}/pulls/{pr}")
        if not isinstance(payload, dict):
            raise ConnectorAPIError("GitHub returned an unexpected pull request payload.")
        return payload

    async def fetch_pull_files(
        self, repository: str, number: int, *, max_pages: int = 30
    ) -> list[dict[str, Any]]:
        """Every file entry of a pull request, raw.

        Raw on purpose, like `fetch_pull`: the caller needs `patch`, `previous_filename`
        and the blob `sha`, all of which the connector's `PullRequestFile` model drops
        because tool callers never want them. GitHub caps a pull request's file list at
        3000 entries; thirty pages of 100 reaches that ceiling, and a shorter list ends
        the walk early.
        """
        repo = _checked_repo(repository)
        pr = _checked_number(number)
        entries: list[dict[str, Any]] = []
        for page in range(1, max(1, max_pages) + 1):
            payload = await self._read._get(
                f"/repos/{repo}/pulls/{pr}/files", {"per_page": 100, "page": page}
            )
            raw = payload if isinstance(payload, list) else None
            if not raw:
                break
            entries.extend(item for item in raw if isinstance(item, dict))
            if len(raw) < 100:
                break
        return entries

    async def fetch_check_runs(self, repository: str, sha: str) -> list[dict[str, Any]]:
        """Every check run GitHub recorded for one commit."""
        repo = _checked_repo(repository)
        payload = await self._read._get(f"/repos/{repo}/commits/{sha}/check-runs", {"per_page": 100})
        runs = payload.get("check_runs") if isinstance(payload, dict) else None
        return [r for r in (runs or []) if isinstance(r, dict)]

    async def fetch_combined_status(self, repository: str, sha: str) -> dict[str, Any]:
        """The legacy commit-status state, which older repositories still gate on."""
        repo = _checked_repo(repository)
        payload = await self._read._get(f"/repos/{repo}/commits/{sha}/status")
        return payload if isinstance(payload, dict) else {}


def summarize_checks(
    check_runs: list[dict[str, Any]], combined: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Reduce check runs and legacy statuses to one gate-shaped summary.

    `none` is distinct from `success`: a commit with no checks at all is not a passing commit,
    and it is not a failing one either. The merge gate reports it as "no checks" and lets the
    owner decide, because this project runs no platform CI (GitHub Actions is abandoned) and the
    release gate is local and manual. A `success` claim is only ever made from real passing runs.
    """
    total = passing = failing = pending = 0
    for run in check_runs:
        total += 1
        status = str(run.get("status") or "").lower()
        conclusion = str(run.get("conclusion") or "").lower()
        if status and status != "completed":
            pending += 1
        elif conclusion in _FAILING_CONCLUSIONS:
            failing += 1
        elif conclusion in {"success", "neutral", "skipped"}:
            passing += 1
        else:
            pending += 1

    combined_state = str((combined or {}).get("state") or "").lower()
    combined_total = len((combined or {}).get("statuses") or [])
    if combined_state == "failure" or combined_state == "error":
        failing += 1
        total += 1
    elif combined_state == "pending":
        pending += 1
        total += 1
    elif combined_state == "success":
        passing += 1
        total += 1

    if failing:
        state = "failure"
    elif pending:
        state = "pending"
    elif total and passing:
        state = "success"
    else:
        state = "none"
    return {
        "state": state,
        "total": total,
        "passing": passing,
        "failing": failing,
        "pending": pending,
        "combinedState": combined_state or None,
        "combinedContexts": combined_total,
    }


def _headers(token: str) -> dict[str, str]:
    # Same headers as the reader, imported lazily to keep the two halves in step.
    from github.client import _headers as client_headers

    return client_headers(token)


__all__ = [
    "APPROVE",
    "COMMENT",
    "MERGE_METHODS",
    "REQUEST_CHANGES",
    "REVIEW_EVENTS",
    "GitHubWriteClient",
    "summarize_checks",
]
