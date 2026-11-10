"""Typed, read-only GitHub REST client for one connected account.

The client never holds a token: it asks a `TokenSource` per request, which is what lets the server
refresh an expiring grant without the connector package ever seeing a refresh token it could
persist itself.

Two GitHub behaviours shape the error handling and both were measured against the provisioned app:

* **404 is used for "you cannot see it", not only "it does not exist".** GitHub deliberately
  answers 404 rather than 403 for a private repository the token cannot reach, so the message says
  so instead of telling the user the repository is missing.
* **403 covers two unrelated faults.** A spent rate limit carries `x-ratelimit-remaining: 0` and
  `x-ratelimit-reset`; anything else is a permission the app registration does not hold. They are
  reported separately because the fixes are different (wait vs. change the app).
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from github.config import DEFAULT_API_BASE
from github.errors import ConnectorAPIError, ConnectorAuthError

_MAX_PAGE = 100
_MAX_PAGES = 10
_HARD_LIMIT = 500
# A file body is capped: a generated lockfile can be megabytes, and the model only needs the head.
_MAX_FILE_CHARS = 200_000
_MAX_COMMENTS = 100
_MAX_PR_FILES = 100

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._\-/@ ]{1,400}$")


class TokenSource(Protocol):
    """The narrow view of the server's token store the client is allowed to use."""

    async def access_token(self) -> str: ...

    async def invalidate(self) -> None: ...

    def api_base(self) -> str: ...


class GitHubUser(BaseModel):
    login: str
    id: int = 0
    name: str | None = None
    email: str | None = None
    html_url: str | None = None
    type: str | None = None
    company: str | None = None


class Installation(BaseModel):
    id: int
    account: str | None = None
    repository_selection: str | None = None
    html_url: str | None = None


class Repository(BaseModel):
    id: int = 0
    full_name: str
    private: bool = False
    archived: bool = False
    description: str | None = None
    default_branch: str | None = None
    html_url: str | None = None
    updated_at: str | None = None


class FileEntry(BaseModel):
    path: str
    type: str = "file"
    size: int | None = None
    sha: str | None = None
    html_url: str | None = None
    # Present only for a file that could be decoded as text.
    text: str | None = None
    truncated: bool = False
    binary: bool = False


class Issue(BaseModel):
    number: int
    title: str
    state: str | None = None
    author: str | None = None
    labels: list[str] = Field(default_factory=list)
    comments: int = 0
    is_pull_request: bool = False
    html_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class IssueComment(BaseModel):
    id: int = 0
    author: str | None = None
    body: str | None = None
    created_at: str | None = None
    html_url: str | None = None


class PullRequest(BaseModel):
    number: int
    title: str
    state: str | None = None
    draft: bool = False
    author: str | None = None
    head: str | None = None
    base: str | None = None
    merged_at: str | None = None
    html_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PullRequestFile(BaseModel):
    filename: str
    status: str | None = None
    additions: int = 0
    deletions: int = 0
    changes: int = 0


class Page(BaseModel):
    items: list[Any] = Field(default_factory=list)
    next_page_token: str | None = None
    truncated: bool = False


class GitHubClient:
    """Holds one connected account's token source and talks to the API base it reports."""

    def __init__(self, http: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens

    def _base(self) -> str:
        try:
            base = self._tokens.api_base()
        except AttributeError:  # a test double that predates api_base()
            base = DEFAULT_API_BASE
        return (base or DEFAULT_API_BASE).rstrip("/")

    async def get_current_user(self) -> GitHubUser:
        return _user(await self._get("/user"))

    async def list_installations(self) -> list[Installation]:
        page = await self._pages(
            "/user/installations", {}, "installations", _installation, max_results=_HARD_LIMIT
        )
        return list(page.items)

    async def list_repositories(self, *, max_results: int = 100) -> Page:
        """Every repository this token can read, gathered from the user's installations.

        A user token reaches public repositories without an installation, but listing "accessible
        repositories" through `/user/installations` is the set GitHub itself reports for the app,
        and it is the set the connector can actually read. Repositories are de-duplicated by full
        name because two installations can overlap (an org and one of its members).
        """
        wanted = _clamp(max_results)
        seen: dict[str, Repository] = {}
        installations = await self.list_installations()
        truncated = False
        for installation in installations:
            if len(seen) >= wanted:
                # Another installation was never read, so the list is deliberately incomplete.
                truncated = True
                break
            page = await self._pages(
                f"/user/installations/{installation.id}/repositories",
                {},
                "repositories",
                _repo,
                max_results=max(1, wanted - len(seen)),
            )
            for repo in page.items:
                seen.setdefault(repo.full_name.lower(), repo)
            truncated = truncated or page.truncated or page.next_page_token is not None
        items = list(seen.values())
        return Page(
            items=items[:wanted],
            next_page_token=None,
            truncated=truncated or len(items) > wanted,
        )

    async def get_file_contents(
        self, repo: str, path: str, *, ref: str | None = None
    ) -> FileEntry | list[FileEntry]:
        """One file (decoded when it is text) or a directory listing."""
        payload = await self._get(
            f"/repos/{_checked_repo(repo)}/contents{_checked_path(path)}",
            {"ref": ref} if ref else {},
        )
        if isinstance(payload, list):
            entries = [_file_entry(item) for item in payload if isinstance(item, dict)]
            return entries[:_HARD_LIMIT]
        return _file_entry(payload)

    async def search_issues_and_prs(
        self, query: str, *, kind: str = "any", max_results: int = 30
    ) -> Page:
        """GitHub's issue search. It covers pull requests too; `kind` narrows it."""
        text = (query or "").strip()
        if not text:
            raise ValueError("query must not be empty.")
        if kind not in {"any", "issue", "pr"}:
            raise ValueError("kind must be any, issue or pr.")
        qualifier = {"any": "", "issue": " is:issue", "pr": " is:pr"}[kind]
        return await self._pages(
            "/search/issues",
            {"q": f"{text}{qualifier}", "sort": "updated", "order": "desc"},
            "items",
            _search_hit,
            max_results=max_results,
        )

    async def list_issues(
        self,
        repo: str,
        *,
        state: str = "open",
        labels: str | None = None,
        max_results: int = 30,
    ) -> Page:
        if state not in {"open", "closed", "all"}:
            raise ValueError("state must be open, closed or all.")
        params: dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = labels
        page = await self._pages(
            f"/repos/{_checked_repo(repo)}/issues",
            params,
            "items",
            _issue,
            max_results=max_results,
        )
        # `/issues` returns pull requests mixed in; they are dropped so a caller that asked for
        # issues does not get issues-and-some-PRs. Fewer than `max_results` can come back.
        return Page(
            items=[item for item in page.items if not item.is_pull_request],
            next_page_token=page.next_page_token,
            truncated=page.truncated,
        )

    async def get_issue(
        self, repo: str, number: int, *, comment_limit: int = 30
    ) -> tuple[Issue, str | None, list[IssueComment], bool]:
        checked = _checked_repo(repo)
        payload = await self._get(f"/repos/{checked}/issues/{_checked_number(number)}")
        issue = _issue(payload)
        if issue.is_pull_request:
            raise ConnectorAPIError(
                f"#{number} in {repo} is a pull request, not an issue.",
                hint="Use github_get_pull_request for pull requests.",
            )
        limit = max(1, min(int(comment_limit), _MAX_COMMENTS))
        page = await self._pages(
            f"/repos/{checked}/issues/{number}/comments",
            {},
            "items",
            _comment,
            max_results=limit,
        )
        return issue, _text(payload.get("body")), list(page.items), page.truncated or page.next_page_token is not None

    async def list_pull_requests(
        self, repo: str, *, state: str = "open", max_results: int = 30
    ) -> Page:
        if state not in {"open", "closed", "all"}:
            raise ValueError("state must be open, closed or all.")
        return await self._pages(
            f"/repos/{_checked_repo(repo)}/pulls",
            {"state": state},
            "items",
            _pull,
            max_results=max_results,
        )

    async def get_pull_request(
        self, repo: str, number: int, *, file_limit: int = 100
    ) -> tuple[PullRequest, str | None, list[PullRequestFile], bool]:
        checked = _checked_repo(repo)
        checked_number = _checked_number(number)
        payload = await self._get(f"/repos/{checked}/pulls/{checked_number}")
        limit = max(1, min(int(file_limit), _MAX_PR_FILES))
        page = await self._pages(
            f"/repos/{checked}/pulls/{checked_number}/files",
            {},
            "items",
            _pull_file,
            max_results=limit,
        )
        truncated = page.truncated or page.next_page_token is not None
        return _pull(payload), _text(payload.get("body")), list(page.items), truncated

    async def _pages(
        self,
        path: str,
        params: dict[str, Any],
        key: str,
        extract: Callable[[dict[str, Any]], Any],
        *,
        max_results: int,
    ) -> Page:
        wanted = _clamp(max_results)
        collected: list[Any] = []
        page_number = 1
        truncated = False
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            page_params["per_page"] = min(_MAX_PAGE, max(1, wanted - len(collected)))
            page_params["page"] = page_number
            payload = await self._get(path, page_params)
            # Some list endpoints answer a bare array (`/repos/{owner}/{repo}/issues`,
            # `/pulls`, `/pulls/{n}/files`, `/issues/{n}/comments`), others wrap it in a keyed
            # object (`/user/installations`, `/search/issues`). Both shapes are read here so a
            # caller never has to know which is which.
            if isinstance(payload, list):
                raw = payload
            elif isinstance(payload, dict):
                raw = payload.get(key)
            else:
                raw = None
            raw = raw if isinstance(raw, list) else []
            collected.extend(extract(item) for item in raw if isinstance(item, dict))
            if len(collected) >= wanted:
                truncated = len(raw) >= page_params["per_page"] and len(collected) > 0
                break
            if len(raw) < page_params["per_page"]:
                break
            page_number += 1
        else:
            truncated = True
        items = collected[:wanted]
        return Page(
            items=items,
            next_page_token=str(page_number + 1) if truncated else None,
            truncated=truncated,
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        token = await self._tokens.access_token()
        url = f"{self._base()}{path}"
        response = await self._http.get(url, params=params or {}, headers=_headers(token))
        if response.status_code == 401:
            # Exactly one retry: the source refreshes on the next access_token() call.
            await self._tokens.invalidate()
            token = await self._tokens.access_token()
            response = await self._http.get(url, params=params or {}, headers=_headers(token))
        if response.status_code == 401:
            raise ConnectorAuthError(
                "GitHub rejected the stored credential.",
                hint="Reconnect GitHub in Settings > Connections.",
            )
        if response.status_code == 403:
            if (response.headers.get("x-ratelimit-remaining") or "").strip() == "0":
                raise ConnectorAPIError(
                    "GitHub rate-limited this account.",
                    hint=(
                        f"The limit resets at {_reset_hint(response)}; user tokens allow 5,000 "
                        "requests an hour."
                    ),
                )
            raise ConnectorAPIError(
                "This GitHub grant cannot read that resource.",
                hint=(
                    "The app's repository permissions (metadata, contents, issues, pull_requests) "
                    "are read-only; check the app registration and re-authorize if they changed."
                ),
            )
        if response.status_code == 404:
            raise ConnectorAPIError(
                "GitHub has no such resource for this token, or the token cannot see it.",
                hint=(
                    "GitHub answers 404 for a private repository the app is not installed on. "
                    "Install the app on that account, then connect again."
                ),
            )
        if response.status_code == 422:
            payload = _json(response, allow_error=False)
            message = payload.get("message") if isinstance(payload, dict) else None
            raise ConnectorAPIError(
                f"GitHub rejected the request{f': {message}' if message else '.'}"
            )
        if response.status_code == 429:
            raise ConnectorAPIError(
                "GitHub rate-limited the request.",
                hint=f"Wait {_retry_after(response)} seconds and try again.",
            )
        if response.status_code >= 500:
            raise ConnectorAPIError(f"GitHub API returned HTTP {response.status_code}.")
        if response.status_code >= 400:
            raise ConnectorAPIError(f"GitHub API returned HTTP {response.status_code}.")
        return _json(response)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _json(response: httpx.Response, *, allow_error: bool = True) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorAPIError("GitHub returned a body that is not JSON.") from exc
    if allow_error and isinstance(payload, dict) and payload.get("message") and response.is_error:
        raise ConnectorAPIError(f"GitHub API error: {payload['message']}")
    return payload


def _user(payload: dict[str, Any]) -> GitHubUser:
    return GitHubUser(
        login=str(payload.get("login") or ""),
        id=_int(payload.get("id")),
        name=_text(payload.get("name")),
        email=_text(payload.get("email")),
        html_url=_text(payload.get("html_url")),
        type=_text(payload.get("type")),
        company=_text(payload.get("company")),
    )


def _installation(payload: dict[str, Any]) -> Installation:
    account = payload.get("account")
    login = account.get("login") if isinstance(account, dict) else None
    return Installation(
        id=_int(payload.get("id")),
        account=_text(login),
        repository_selection=_text(payload.get("repository_selection")),
        html_url=_text(payload.get("html_url")),
    )


def _repo(payload: dict[str, Any]) -> Repository:
    return Repository(
        id=_int(payload.get("id")),
        full_name=str(payload.get("full_name") or ""),
        private=bool(payload.get("private")),
        archived=bool(payload.get("archived")),
        description=_text(payload.get("description")),
        default_branch=_text(payload.get("default_branch")),
        html_url=_text(payload.get("html_url")),
        updated_at=_text(payload.get("updated_at")),
    )


def _file_entry(payload: dict[str, Any]) -> FileEntry:
    kind = str(payload.get("type") or "file")
    size = payload.get("size")
    entry = FileEntry(
        path=str(payload.get("path") or payload.get("name") or ""),
        type=kind,
        size=_int(size) if size is not None else None,
        sha=_text(payload.get("sha")),
        html_url=_text(payload.get("html_url")),
    )
    if kind != "file":
        return entry
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        # The contents API omits `content` above 1 MB; it is not an error, the file is just big.
        entry.truncated = True
        return entry
    raw = payload["content"]
    try:
        data = base64.b64decode(raw.replace("\n", ""), validate=True)
    except (binascii.Error, ValueError):
        entry.binary = True
        return entry
    head = data[:_MAX_FILE_CHARS]
    entry.truncated = len(data) > _MAX_FILE_CHARS
    try:
        entry.text = head.decode("utf-8")
    except UnicodeDecodeError:
        # A NUL in the first block is the reliable signal; a partial UTF-8 tail is not worth
        # guessing at.
        entry.binary = True
        entry.text = None
    return entry


def _issue(payload: dict[str, Any]) -> Issue:
    user = payload.get("user")
    labels = payload.get("labels")
    issue = Issue(
        number=_int(payload.get("number")),
        title=str(payload.get("title") or ""),
        state=_text(payload.get("state")),
        author=_text(user.get("login")) if isinstance(user, dict) else None,
        labels=[
            str(label.get("name"))
            for label in (labels if isinstance(labels, list) else [])
            if isinstance(label, dict) and label.get("name")
        ],
        comments=_int(payload.get("comments")),
        is_pull_request=bool(payload.get("pull_request")),
        html_url=_text(payload.get("html_url")),
        created_at=_text(payload.get("created_at")),
        updated_at=_text(payload.get("updated_at")),
    )
    return issue


def _search_hit(payload: dict[str, Any]) -> dict[str, Any]:
    """Search results carry the repository in a separate key, which a repo API item does not."""
    issue = _issue(payload)
    repository_url = str(payload.get("repository_url") or "")
    item = issue.model_dump()
    item["repository"] = repository_url.removeprefix("https://api.github.com/repos/") or None
    return item


def _comment(payload: dict[str, Any]) -> IssueComment:
    user = payload.get("user")
    return IssueComment(
        id=_int(payload.get("id")),
        author=_text(user.get("login")) if isinstance(user, dict) else None,
        body=_text(payload.get("body")),
        created_at=_text(payload.get("created_at")),
        html_url=_text(payload.get("html_url")),
    )


def _pull(payload: dict[str, Any]) -> PullRequest:
    user = payload.get("user")
    head = payload.get("head")
    base = payload.get("base")
    return PullRequest(
        number=_int(payload.get("number")),
        title=str(payload.get("title") or ""),
        state=_text(payload.get("state")),
        draft=bool(payload.get("draft")),
        author=_text(user.get("login")) if isinstance(user, dict) else None,
        head=_text(head.get("ref")) if isinstance(head, dict) else None,
        base=_text(base.get("ref")) if isinstance(base, dict) else None,
        merged_at=_text(payload.get("merged_at")),
        html_url=_text(payload.get("html_url")),
        created_at=_text(payload.get("created_at")),
        updated_at=_text(payload.get("updated_at")),
    )


def _pull_file(payload: dict[str, Any]) -> PullRequestFile:
    return PullRequestFile(
        filename=str(payload.get("filename") or ""),
        status=_text(payload.get("status")),
        additions=_int(payload.get("additions")),
        deletions=_int(payload.get("deletions")),
        changes=_int(payload.get("changes")),
    )


def _checked_repo(value: str) -> str:
    text = (value or "").strip()
    if not _REPO_PATTERN.fullmatch(text):
        raise ValueError("repo must be owner/name, for example hoja-work/hoja-cloud.")
    return text


def _checked_path(value: str) -> str:
    text = (value or "").strip().lstrip("/")
    if not text:
        # The repository root: `/contents` with no trailing slash lists the top level.
        return ""
    if not _PATH_PATTERN.fullmatch(text) or ".." in text.split("/"):
        raise ValueError("path must be a plain repository-relative path.")
    return f"/{text}"


def _checked_number(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("number must be a positive integer.") from exc
    if number <= 0:
        raise ValueError("number must be a positive integer.")
    return number


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 50
    return max(1, min(number, _HARD_LIMIT))


def _reset_hint(response: httpx.Response) -> str:
    raw = (response.headers.get("x-ratelimit-reset") or "").strip()
    if not raw.isdigit():
        return "the time GitHub reports"
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(raw), tz=timezone.utc).strftime("%H:%M UTC")


def _retry_after(response: httpx.Response) -> str:
    value = (response.headers.get("retry-after") or "").strip()
    return value if value.isdigit() else "a few"
