"""Company Knowledge Brain: hierarchical workspace, team, and project context docs.

Knowledge documents store company standards, architecture notes, and team/project
conventions in Markdown. Enabled documents are automatically merged in hierarchy
order (``workspace`` -> ``team`` -> ``project``) and injected into Agent Chat,
delegated runner leases, and MCP tool responses.

Permissions:
- Read (``knowledgeDocs``, ``knowledgeContext``): any active member of the workspace, for
  workspace docs and for team/project docs whose team or project the member can see. A
  private team's docs are invisible to non-members, exactly like its tasks (HOJA-735).
- Write (``knowledgeDocCreate``, ``knowledgeDocUpdate``, ``knowledgeDocDelete``):
  workspace admins and active workspace members with write access (read-only API
  keys and read-only OAuth tokens are blocked at the GraphQL/MCP mutation boundary),
  under the same visibility rule: a doc on a team or project the caller cannot see is
  NOT_FOUND, and nobody can file a doc under a team or project they cannot see.
"""

from __future__ import annotations

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server.db import new_id, now_iso, project, team, user, workspace_knowledge

VALID_SCOPES = ("workspace", "team", "project")
_SCOPE_LABELS = {
    "workspace": "Workspace",
    "team": "Team",
    "project": "Project",
}
_SCOPE_ORDER = {"workspace": 0, "team": 1, "project": 2}
DEFAULT_MAX_CHARS = 12_000
_TRUNCATION_SUFFIX = "\n\n... (truncated)"

query = QueryType()
mutation = MutationType()
knowledge_doc_type = ObjectType("KnowledgeDoc")

SDL = gql(
    """
    type KnowledgeDoc {
      id: ID!
      scope: String!
      scopeId: String
      title: String!
      body: String!
      enabled: Boolean!
      createdBy: User
      updatedBy: User
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    type KnowledgeDocPayload {
      success: Boolean!
      knowledgeDoc: KnowledgeDoc
    }

    input KnowledgeDocInput {
      scope: String!
      scopeId: String
      title: String!
      body: String!
      enabled: Boolean
    }

    input KnowledgeDocUpdateInput {
      scope: String
      scopeId: String
      title: String
      body: String
      enabled: Boolean
    }

    extend type Query {
      knowledgeDocs(scope: String, scopeId: String): [KnowledgeDoc!]!
      knowledgeContext(teamId: String, projectId: String): String!
    }

    extend type Mutation {
      knowledgeDocCreate(input: KnowledgeDocInput!): KnowledgeDocPayload!
      knowledgeDocUpdate(id: String!, input: KnowledgeDocUpdateInput!): KnowledgeDocPayload!
      knowledgeDocDelete(id: String!): Boolean!
    }
    """
)


def resolve_knowledge_context(
    conn,
    org_id: str,
    *,
    team_id: str | None = None,
    project_id: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Merge enabled knowledge docs in hierarchy order: workspace -> team -> project.

    Returns cleanly formatted Markdown sections bounded to ``max_chars`` characters.
    Returns an empty string when no enabled documents apply (or ``max_chars <= 0``).
    """
    if not org_id or max_chars <= 0:
        return ""

    tiers: list[tuple[str, str | None]] = [("workspace", None)]
    if team_id:
        tiers.append(("team", team_id))
    if project_id:
        tiers.append(("project", project_id))

    sections: list[str] = []
    for scope, scope_id in tiers:
        conditions = [
            workspace_knowledge.c.organization_id == org_id,
            workspace_knowledge.c.scope == scope,
            workspace_knowledge.c.enabled.is_(True),
        ]
        if scope == "workspace":
            conditions.append(workspace_knowledge.c.scope_id.is_(None))
        else:
            conditions.append(workspace_knowledge.c.scope_id == scope_id)
        rows = conn.execute(
            select(workspace_knowledge.c.title, workspace_knowledge.c.body)
            .where(and_(*conditions))
            .order_by(workspace_knowledge.c.created_at, workspace_knowledge.c.title, workspace_knowledge.c.id)
        ).all()
        label = _SCOPE_LABELS[scope]
        for title, body in rows:
            clean_title = " ".join(str(title or "").split())
            clean_body = str(body or "").strip()
            if not clean_title and not clean_body:
                continue
            sections.append(f"### [{label}] {clean_title}\n{clean_body}")

    if not sections:
        return ""

    merged = "\n\n".join(sections)
    if len(merged) <= max_chars:
        return merged
    if max_chars <= len(_TRUNCATION_SUFFIX):
        return merged[:max_chars]
    return merged[: max_chars - len(_TRUNCATION_SUFFIX)].rstrip() + _TRUNCATION_SUFFIX


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _bad(field: str, message: str) -> None:
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _require_active_member(store, org: str, uid: str) -> dict:
    actor = store.get_user(org, uid)
    if not actor or not actor.get("active", True):
        raise GraphQLError(
            "Only active workspace members can access workspace knowledge.",
            extensions={"code": "FORBIDDEN"},
        )
    return actor


def _scope_visible(conn, org_id: str, viewer_id: str, scope: str, scope_id: str | None) -> bool:
    """Whether the viewer may see a doc filed under (scope, scope_id): the same visibility
    rule as the team's tasks and the project's page."""
    from hoja_server import store as store_module

    if scope == "workspace":
        return True
    if not scope_id:
        return False
    if scope == "team":
        return store_module.team_visible(conn, org_id, viewer_id, scope_id)
    if scope == "project":
        stmt = select(project.c.id).where(and_(
            project.c.organization_id == org_id, project.c.id == scope_id))
        stmt = store_module._scope_projects(conn, stmt, org_id, viewer_id)
        return conn.execute(stmt).first() is not None
    return False


def _not_found() -> None:
    raise GraphQLError("Knowledge document not found.", extensions={"code": "NOT_FOUND"})


def _validate_scope(conn, org_id: str, scope: str, scope_id: str | None) -> tuple[str, str | None]:
    clean_scope = (scope or "").strip().lower()
    if clean_scope not in VALID_SCOPES:
        _bad("scope", "scope must be one of 'workspace', 'team', or 'project'.")
    clean_scope_id = (scope_id or "").strip() or None
    if clean_scope == "workspace":
        if clean_scope_id is not None:
            _bad("scopeId", "scopeId must be empty for workspace-scoped knowledge.")
        return clean_scope, None
    if clean_scope_id is None:
        _bad("scopeId", f"scopeId is required when scope is '{clean_scope}'.")
    if clean_scope == "team":
        found = conn.execute(
            select(team.c.id).where(
                and_(
                    team.c.id == clean_scope_id,
                    team.c.organization_id == org_id,
                    team.c.archived_at.is_(None),
                )
            )
        ).first()
        if not found:
            _bad("scopeId", "scopeId must reference an active team in this workspace.")
    else:
        found = conn.execute(
            select(project.c.id).where(
                and_(
                    project.c.id == clean_scope_id,
                    project.c.organization_id == org_id,
                    project.c.archived_at.is_(None),
                )
            )
        ).first()
        if not found:
            _bad("scopeId", "scopeId must reference an active project in this workspace.")
    return clean_scope, clean_scope_id


def _validate_title(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _bad("title", "title must not be empty.")
    return value.strip()


def _validate_body(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _bad("body", "body must not be empty.")
    return value.strip()


def _user_summary(conn, org_id: str, uid: str | None) -> dict | None:
    if not uid:
        return None
    row = conn.execute(
        select(user).where(and_(user.c.id == uid, user.c.organization_id == org_id))
    ).first()
    if not row:
        return None
    m = dict(row._mapping)
    return {
        "id": m["id"],
        "name": m.get("name") or "",
        "displayName": m.get("display_name") or m.get("name") or "",
        "email": m.get("email") or "",
        "active": bool(m.get("active", True)),
        "admin": bool(m.get("admin", False)),
        "avatarUrl": m.get("avatar_url"),
        "avatarSeed": m.get("avatar_seed"),
        "url": m.get("url") or "",
        "timezone": m.get("timezone"),
        "statusEmoji": m.get("status_emoji"),
        "statusLabel": m.get("status_label"),
        "createdAt": m.get("created_at"),
        "updatedAt": m.get("updated_at"),
    }


def _serialize(conn, org_id: str, row: dict) -> dict:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "scopeId": row["scope_id"],
        "title": row["title"],
        "body": row["body"],
        "enabled": bool(row["enabled"]),
        "createdBy": _user_summary(conn, org_id, row.get("created_by")),
        "updatedBy": _user_summary(conn, org_id, row.get("updated_by")),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


@query.field("knowledgeDocs")
def r_knowledge_docs(_, info, scope=None, scopeId=None):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_active_member(store, org, uid)
    conditions = [workspace_knowledge.c.organization_id == org]
    if scope is not None and str(scope).strip():
        clean_scope = str(scope).strip().lower()
        if clean_scope not in VALID_SCOPES:
            _bad("scope", "scope must be one of 'workspace', 'team', or 'project'.")
        conditions.append(workspace_knowledge.c.scope == clean_scope)
    if scopeId is not None and str(scopeId).strip():
        conditions.append(workspace_knowledge.c.scope_id == str(scopeId).strip())
    with store.engine.connect() as conn:
        rows = [
            dict(r._mapping)
            for r in conn.execute(
                select(workspace_knowledge)
                .where(and_(*conditions))
                .order_by(workspace_knowledge.c.created_at, workspace_knowledge.c.title)
            ).all()
        ]
        rows = [r for r in rows if _scope_visible(conn, org, uid, r["scope"], r["scope_id"])]
        rows.sort(key=lambda r: (_SCOPE_ORDER.get(r["scope"], 99), r["title"].lower(), r["created_at"]))
        return [_serialize(conn, org, r) for r in rows]


@query.field("knowledgeContext")
def r_knowledge_context(_, info, teamId=None, projectId=None):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_active_member(store, org, uid)
    clean_team = (teamId or "").strip() or None
    clean_project = (projectId or "").strip() or None
    with store.engine.connect() as conn:
        if clean_team and not _scope_visible(conn, org, uid, "team", clean_team):
            raise GraphQLError("Team not found.", extensions={"code": "NOT_FOUND"})
        if clean_project and not _scope_visible(conn, org, uid, "project", clean_project):
            raise GraphQLError("Project not found.", extensions={"code": "NOT_FOUND"})
        return resolve_knowledge_context(conn, org, team_id=clean_team, project_id=clean_project)


@mutation.field("knowledgeDocCreate")
def m_knowledge_doc_create(_, info, input):
    store, org, uid = _ctx(info)
    _require_active_member(store, org, uid)
    title = _validate_title(input.get("title"))
    body = _validate_body(input.get("body"))
    enabled = True if input.get("enabled") is None else bool(input.get("enabled"))
    with store.engine.begin() as conn:
        scope, scope_id = _validate_scope(conn, org, input.get("scope"), input.get("scopeId"))
        if not _scope_visible(conn, org, uid, scope, scope_id):
            _bad("scopeId", f"scopeId must reference an active {scope} in this workspace.")
        row_id = new_id()
        ts = now_iso()
        conn.execute(
            workspace_knowledge.insert().values(
                id=row_id,
                organization_id=org,
                scope=scope,
                scope_id=scope_id,
                title=title,
                body=body,
                enabled=enabled,
                created_by=uid,
                updated_by=uid,
                created_at=ts,
                updated_at=ts,
            )
        )
        row = dict(
            conn.execute(
                select(workspace_knowledge).where(workspace_knowledge.c.id == row_id)
            )
            .first()
            ._mapping
        )
        return {"success": True, "knowledgeDoc": _serialize(conn, org, row)}


@mutation.field("knowledgeDocUpdate")
def m_knowledge_doc_update(_, info, id, input):
    store, org, uid = _ctx(info)
    _require_active_member(store, org, uid)
    with store.engine.begin() as conn:
        existing = conn.execute(
            select(workspace_knowledge).where(
                and_(
                    workspace_knowledge.c.organization_id == org,
                    workspace_knowledge.c.id == id,
                )
            )
        ).first()
        if not existing:
            _not_found()
        cur = dict(existing._mapping)
        if not _scope_visible(conn, org, uid, cur["scope"], cur["scope_id"]):
            _not_found()

        next_scope = input["scope"] if "scope" in input and input["scope"] is not None else cur["scope"]
        if "scopeId" in input:
            next_scope_id = input["scopeId"]
        elif str(next_scope).strip().lower() == "workspace":
            next_scope_id = None
        else:
            next_scope_id = cur["scope_id"]
        scope, scope_id = _validate_scope(conn, org, next_scope, next_scope_id)
        if not _scope_visible(conn, org, uid, scope, scope_id):
            _bad("scopeId", f"scopeId must reference an active {scope} in this workspace.")

        title = _validate_title(input["title"]) if "title" in input and input["title"] is not None else cur["title"]
        body = _validate_body(input["body"]) if "body" in input and input["body"] is not None else cur["body"]
        enabled = bool(input["enabled"]) if "enabled" in input and input["enabled"] is not None else bool(cur["enabled"])

        conn.execute(
            workspace_knowledge.update()
            .where(
                and_(
                    workspace_knowledge.c.organization_id == org,
                    workspace_knowledge.c.id == id,
                )
            )
            .values(
                scope=scope,
                scope_id=scope_id,
                title=title,
                body=body,
                enabled=enabled,
                updated_by=uid,
                updated_at=now_iso(),
            )
        )
        row = dict(
            conn.execute(
                select(workspace_knowledge).where(workspace_knowledge.c.id == id)
            )
            .first()
            ._mapping
        )
        return {"success": True, "knowledgeDoc": _serialize(conn, org, row)}


@mutation.field("knowledgeDocDelete")
def m_knowledge_doc_delete(_, info, id):
    store, org, uid = _ctx(info)
    _require_active_member(store, org, uid)
    with store.engine.begin() as conn:
        existing = conn.execute(select(workspace_knowledge.c.scope, workspace_knowledge.c.scope_id).where(
            and_(workspace_knowledge.c.organization_id == org, workspace_knowledge.c.id == id))).first()
        if not existing or not _scope_visible(conn, org, uid, existing[0], existing[1]):
            return False
        result = conn.execute(
            workspace_knowledge.delete().where(
                and_(
                    workspace_knowledge.c.organization_id == org,
                    workspace_knowledge.c.id == id,
                )
            )
        )
    return result.rowcount > 0
