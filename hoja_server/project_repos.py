"""The repository a project's agent tasks are worked on in (HOJA-602).

Where a delegated task runs is decided when its lease is granted (``runner_api._run_spec``),
in this order:

1. the task's **project** names a repository → the run works in a worktree of it, on the task's
   own branch (``agent/<identifier>``);
2. else the **agent's policy** grants a repository → that one, the same way;
3. else → a scratch folder. A task never needs a repo to be worked on.

Who allows what, in this model:

* an **admin** names the project's repository (``projectRepositorySet``; audited, before and after);
* a person **delegating** a task in that project to an agent is the permission to work on that
  task there. That is the policy's existing rule (``agent_policy`` RULES: "a delegated task is the
  grant itself"); who may delegate to a given agent is its policy's ``delegators`` list;
* the run only ever writes the task's own branch, ``agent/<identifier>``, in a fresh worktree:
  never the default branch, and never another task's.

A project's repository is used for that project's tasks only: a task elsewhere never gets it.
"""
from __future__ import annotations

import asyncio
import logging
import re

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, or_, select

from hoja_server import audit, tenant_context
from hoja_server.db import connector_account, github_link, issue, now_iso, project

log = logging.getLogger("hoja_server.project_repos")

query = QueryType()
mutation = MutationType()
project_type = ObjectType("Project")

#: The branch prefix a project repository's tasks work under: agent/<task identifier>.
BRANCH_PREFIX = "agent/"

_OWNER_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_URL_REPO = re.compile(
    r"^(?:https?://(?:www\.)?github\.com/|git@github\.com:)"
    r"(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$"
)
_GITHUB_EXTERNAL_ID_REPO = re.compile(
    r"^(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:[#@:/]|$)"
)

SDL = gql(
    """
    type ProjectRepositoryChoice {
      fullName: String!
      url: String
      source: String!
    }

    extend type Query {
      "Repositories known in this workspace: configured on projects, linked from GitHub, or accessible through a connected GitHub account."
      projectRepositoryChoices: [ProjectRepositoryChoice!]!
    }

    extend type Project {
      "The repository this project's agent tasks are worked on in: owner/name, a git URL or a path on the machine. Null: tasks run in a scratch folder (or the agent's own repo grant)."
      repository: String
    }

    extend type Mutation {
      "Admin only. Name (or, with null, clear) the repository agent tasks in this project are worked on in. Each task gets its own branch, agent/<identifier>."
      projectRepositorySet(projectId: String!, repository: String): Project
    }
    """
)


def clean_repository(value: str | None) -> str | None:
    """A repository reference as a person typed it, or None to clear. Refused when it could not
    be one (a wildcard, spaces, shell metacharacters)."""
    text = (value or "").strip()
    if not text:
        return None
    if text == "*" or len(text) > 300 or any(ch in text for ch in " \n\r\t\"'`$;|&<>\\"):
        raise GraphQLError("A repository is owner/name, a git URL, or a path on the machine.",
                           extensions={"code": "BAD_USER_INPUT", "field": "repository"})
    return text.rstrip("/")


def repository_for_task(conn, org_id: str, task_id: str) -> tuple[str | None, str | None]:
    """(repository, project name) for a task whose project names one; (None, None) otherwise."""
    row = conn.execute(select(project.c.repository, project.c.name).join(
        issue, issue.c.project_id == project.c.id).where(and_(
            issue.c.organization_id == org_id, issue.c.id == task_id,
            project.c.archived_at.is_(None)))).first()
    if not row or not row[0]:
        return None, None
    return str(row[0]), str(row[1])


@project_type.field("repository")
def r_repository(obj, info):
    store = info.context["store"]
    with store.engine.connect() as conn:
        return conn.execute(select(project.c.repository).where(and_(
            project.c.organization_id == info.context["org_id"],
            project.c.id == obj["id"]))).scalar()


@mutation.field("projectRepositorySet")
def m_project_repository_set(_obj, info, projectId, repository=None):  # noqa: N803
    store, org, uid = info.context["store"], info.context["org_id"], info.context["user_id"]
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can set a project's repository: it decides "
                           "where agents may write.", extensions={"code": "FORBIDDEN"})
    value = clean_repository(repository)
    with store.engine.begin() as conn:
        row = conn.execute(select(project.c.id, project.c.repository).where(and_(
            project.c.organization_id == org,
            project.c.id == projectId))).first()
        if row is None:
            raise GraphQLError("No such project.", extensions={"code": "NOT_FOUND"})
        before = row[1]
        conn.execute(project.update().where(and_(
            project.c.organization_id == org, project.c.id == projectId,
        )).values(repository=value, updated_at=now_iso()))
    if before != value:
        audit.record_request(store, info, user_id=uid, action="project.repository.change",
                             target_kind="project", target_id=projectId,
                             detail={"before": before, "after": value})
    return store.project_by_id(org, projectId, viewer_id=uid)


def _normalize_repo_choice(raw: str | None) -> tuple[str, str | None] | None:
    """Turn a project repository string or GitHub URL/slug into (fullName, htmlUrl)."""
    text = (raw or "").strip()
    if not text:
        return None
    m_url = _GITHUB_URL_REPO.match(text)
    if m_url:
        slug = m_url.group("slug").lower()
        return slug, f"https://github.com/{slug}"
    if _OWNER_REPO.match(text):
        slug = text.lower()
        return slug, f"https://github.com/{slug}"
    return text.rstrip("/"), None


def _parse_github_link_repo(external_id: str | None, url: str | None) -> tuple[str, str | None] | None:
    for candidate in (url, external_id):
        text = (candidate or "").strip()
        if not text:
            continue
        m_url = _GITHUB_URL_REPO.match(text)
        if m_url:
            slug = m_url.group("slug").lower()
            return slug, f"https://github.com/{slug}"
        m_ext = _GITHUB_EXTERNAL_ID_REPO.match(text)
        if m_ext:
            slug = m_ext.group("slug").lower()
            return slug, f"https://github.com/{slug}"
    return None


async def _fetch_github_account_repos(store, org_id: str, user_id: str) -> list[tuple[str, str | None]]:
    """Best-effort fetch of accessible repositories from an active GitHub connector account."""
    try:
        import httpx
        from github import GitHubAppConfig

        from hoja_server.github_tokens import GitHubTokenStore
    except Exception:
        return []

    with tenant_context.scoped(org_id), store.engine.connect() as conn:
        account = conn.execute(
            select(connector_account).where(and_(
                connector_account.c.organization_id == org_id,
                connector_account.c.provider == "github",
                connector_account.c.status == "active",
                connector_account.c.revoked_at.is_(None),
                or_(
                    connector_account.c.owner_user_id == user_id,
                    connector_account.c.owner_user_id.is_(None),
                ),
            )).order_by(
                connector_account.c.owner_user_id.is_(None),
                connector_account.c.created_at,
            ).limit(1)
        ).mappings().first()
    if not account:
        return []

    try:
        config = GitHubAppConfig.from_env()
        async with httpx.AsyncClient(timeout=5.0) as http:
            tokens = GitHubTokenStore(store.engine, http, config)
            token = await tokens.access_token(str(account["id"]), org_id)
            api_base = tokens.api_base().rstrip("/")
            resp = await http.get(
                f"{api_base}/user/repos",
                params={"per_page": 100, "sort": "updated"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
    except Exception as exc:
        log.debug("Skipping GitHub connector repo list for org %s: %s", org_id, exc)
        return []

    out: list[tuple[str, str | None]] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("full_name") or "").strip().lower()
            if not full_name or not _OWNER_REPO.match(full_name):
                continue
            html_url = str(item.get("html_url") or f"https://github.com/{full_name}")
            out.append((full_name, html_url))
    return out


@query.field("projectRepositoryChoices")
def r_project_repository_choices(_obj, info):
    store, org, uid = info.context["store"], info.context["org_id"], info.context["user_id"]
    choices: dict[str, dict[str, str | None]] = {}

    def _add(full_name: str, url: str | None, source: str) -> None:
        key = full_name.lower()
        if key not in choices:
            choices[key] = {
                "fullName": full_name,
                "url": url,
                "source": source,
            }

    with tenant_context.scoped(org), store.engine.connect() as conn:
        proj_rows = conn.execute(
            select(project.c.repository).where(and_(
                project.c.organization_id == org,
                project.c.archived_at.is_(None),
                project.c.repository.is_not(None),
            )).order_by(project.c.updated_at.desc())
        ).fetchall()
        for (repo_val,) in proj_rows:
            norm = _normalize_repo_choice(repo_val)
            if norm:
                _add(norm[0], norm[1], "project")

        link_rows = conn.execute(
            select(github_link.c.external_id, github_link.c.url).where(
                github_link.c.organization_id == org
            ).order_by(github_link.c.updated_at.desc()).limit(500)
        ).fetchall()
        for ext_id, url in link_rows:
            parsed = _parse_github_link_repo(ext_id, url)
            if parsed:
                _add(parsed[0], parsed[1], "github_link")

    try:
        gh_repos = asyncio.run(_fetch_github_account_repos(store, org, uid))
    except Exception:
        gh_repos = []
    for full_name, html_url in gh_repos:
        _add(full_name, html_url, "github")

    return list(choices.values())
