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

from ariadne import MutationType, ObjectType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server import audit
from hoja_server.db import issue, now_iso, project

mutation = MutationType()
project_type = ObjectType("Project")

#: The branch prefix a project repository's tasks work under: agent/<task identifier>.
BRANCH_PREFIX = "agent/"

SDL = gql(
    """
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
