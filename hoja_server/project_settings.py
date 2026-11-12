"""Workspace-managed project statuses and project templates (HOJA-489).

Replaces the free-string ``project.state`` with an ordered, editable status
set, and adds project templates that ``projectCreate`` expands server-side.

Admin-gated like the other settings surfaces (model_providers is the pattern):
reads and writes both require a workspace admin, because a workspace's status
set and templates are configuration, not content. The six default statuses are
the six state names the frontend has always offered as free strings, lazily
seeded per workspace on first read, so no pre-existing project state becomes
invalid and no organization-creation path needs to learn about statuses.
"""

from __future__ import annotations

import json

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from hoja_server.db import (
    new_id,
    now_iso,
    project_status,
    project_template,
    project_template_team,
    team,
    user,
)

query = QueryType()
mutation = MutationType()
project_status_type = ObjectType("ProjectStatus")
project_template_type = ObjectType("ProjectTemplate")

# (name, category). Names are the exact strings PROJECT_STATES in the frontend
# has always written into project.state, so every value already stored stays
# valid the moment validation switches on. Categories reuse the four workflow
# state kinds so project status and issue state stay comparable.
DEFAULT_PROJECT_STATUSES = [
    ("backlog", "backlog"),
    ("planned", "started"),
    ("started", "started"),
    ("paused", "started"),
    ("completed", "completed"),
    ("canceled", "canceled"),
]
CATEGORIES = {"backlog", "started", "completed", "canceled"}

SDL = gql(
    """
    type ProjectStatus {
      id: ID!
      name: String!
      "backlog | started | completed | canceled"
      category: String!
      color: String
      position: Int!
    }

    type ProjectTemplate {
      id: ID!
      name: String!
      description: String
      lead: User
      defaultState: String
      color: String
      icon: String
      "Task titles created in the project when the template is applied."
      taskSet: [String!]!
      teams: [Team!]!
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    type ProjectStatusPayload { success: Boolean! projectStatus: ProjectStatus }
    type ProjectTemplatePayload { success: Boolean! projectTemplate: ProjectTemplate }

    input ProjectStatusInput {
      name: String!
      category: String!
      color: String
    }

    input ProjectTemplateInput {
      name: String!
      description: String
      leadId: String
      state: String
      color: String
      icon: String
      teamIds: [String!]
      taskSet: [String!]
    }

    extend type Query {
      projectStatuses: [ProjectStatus!]!
      projectTemplates: [ProjectTemplate!]!
    }

    extend type Mutation {
      projectStatusCreate(input: ProjectStatusInput!): ProjectStatusPayload!
      projectStatusUpdate(id: String!, input: ProjectStatusInput!): ProjectStatusPayload!
      projectStatusDelete(id: String!): Boolean!
      projectStatusReorder(ids: [String!]!): Boolean!
      projectTemplateCreate(input: ProjectTemplateInput!): ProjectTemplatePayload!
      projectTemplateUpdate(id: String!, input: ProjectTemplateInput!): ProjectTemplatePayload!
      projectTemplateArchive(id: String!): Boolean!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can manage project settings.",
            extensions={"code": "FORBIDDEN"},
        )


def ensure_default_statuses(conn, org_id: str) -> None:
    """Seed the six default statuses for a workspace that has none yet.

    Idempotent and race-tolerant: the (organization_id, name) unique constraint
    turns a concurrent double-seed into a caught IntegrityError instead of a
    duplicate set. Runs inside the caller's transaction.
    """
    existing = conn.execute(
        select(project_status.c.id).where(
            project_status.c.organization_id == org_id).limit(1)
    ).first()
    if existing:
        return
    ts = now_iso()
    for position, (name, category) in enumerate(DEFAULT_PROJECT_STATUSES):
        try:
            conn.execute(project_status.insert().values(
                id=new_id(), organization_id=org_id, name=name, category=category,
                color=None, position=position, created_at=ts, updated_at=ts))
        except IntegrityError:
            conn.rollback()


def project_statuses(engine, org_id: str) -> list[dict]:
    """Ordered status rows for a workspace, seeding defaults on first read."""
    with engine.begin() as conn:
        ensure_default_statuses(conn, org_id)
        rows = conn.execute(
            select(project_status)
            .where(project_status.c.organization_id == org_id)
            .order_by(project_status.c.position, project_status.c.created_at)
        ).all()
    return [dict(r._mapping) for r in rows]


def configured_state_names(engine, org_id: str) -> list[str]:
    """Lowercased configured status names, for writer-side validation."""
    return [row["name"].strip().lower() for row in project_statuses(engine, org_id)]


def validate_project_state(engine, org_id: str, state: str) -> str:
    """Refuse a project transition to a status the workspace has not configured.

    Case-insensitive by name; the stored value keeps the configured status's
    own spelling so the UI never renders a status the settings pane does not
    list.
    """
    for row in project_statuses(engine, org_id):
        if row["name"].strip().lower() == state.strip().lower():
            return row["name"]
    configured = ", ".join(row["name"] for row in project_statuses(engine, org_id))
    raise GraphQLError(
        f"'{state}' is not a configured project status. Configured: {configured}.",
        extensions={"code": "BAD_USER_INPUT", "field": "state"},
    )


def default_project_state(engine, org_id: str) -> str:
    """The first status by position — the workspace's default for new projects."""
    rows = project_statuses(engine, org_id)
    return rows[0]["name"] if rows else "backlog"


def _serialize_status(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "category": row["category"],
        "color": row["color"], "position": row["position"],
    }


def _template_row(store, org_id: str, template_id: str):
    with store.engine.connect() as conn:
        row = conn.execute(select(project_template).where(and_(
            project_template.c.organization_id == org_id,
            project_template.c.id == template_id,
            project_template.c.archived_at.is_(None),
        ))).first()
    return dict(row._mapping) if row else None


def _serialize_template(store, org_id: str, row) -> dict:
    with store.engine.connect() as conn:
        lead = None
        if row["lead_id"]:
            # Scoped by organization: a template never names a user from
            # another workspace, and a stale id must not leak one.
            u = conn.execute(select(user.c.id, user.c.name, user.c.email).where(and_(
                user.c.id == row["lead_id"],
                user.c.organization_id == org_id))).first()
            lead = dict(u._mapping) if u else None
        team_rows = conn.execute(
            select(team).join(project_template_team,
                              project_template_team.c.team_id == team.c.id).where(
                and_(project_template_team.c.template_id == row["id"],
                     team.c.organization_id == org_id))).all()
    task_set = json.loads(row["task_set"]) if row["task_set"] else []
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "lead": lead, "defaultState": row["state"], "color": row["color"],
        "icon": row["icon"], "taskSet": task_set,
        "teams": [dict(t._mapping) for t in team_rows],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _require_non_empty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise GraphQLError(f"{field} must not be empty.",
                           extensions={"code": "BAD_USER_INPUT", "field": field})
    return value.strip()


def _check_category(category):
    if category not in CATEGORIES:
        raise GraphQLError(
            f"category must be one of {sorted(CATEGORIES)}.",
            extensions={"code": "BAD_USER_INPUT", "field": "category"})


def _require_org_references(store, org_id: str, lead_id, team_ids) -> None:
    """Refuse a template that names a user or team from another workspace.

    Cross-tenant isolation is enforced here at write time as well as on the
    read path, so a bad id is rejected instead of stored and echoed later.
    """
    with store.engine.connect() as conn:
        if lead_id:
            found = conn.execute(select(user.c.id).where(and_(
                user.c.id == lead_id,
                user.c.organization_id == org_id,
                user.c.active.is_(True)))).first()
            if not found:
                raise GraphQLError(
                    "leadId is not an active member of this workspace.",
                    extensions={"code": "BAD_USER_INPUT", "field": "leadId"})
        if team_ids:
            found = set(conn.execute(select(team.c.id).where(and_(
                team.c.id.in_(list(team_ids)),
                team.c.organization_id == org_id,
                team.c.archived_at.is_(None)))).scalars())
            if found != set(team_ids):
                raise GraphQLError(
                    "teamIds must be teams in this workspace.",
                    extensions={"code": "BAD_USER_INPUT", "field": "teamIds"})


@query.field("projectStatuses")
def r_project_statuses(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    return [_serialize_status(r) for r in project_statuses(store.engine, org)]


@mutation.field("projectStatusCreate")
def m_status_create(_, info, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    name = _require_non_empty(input["name"], "name")
    _check_category(input["category"])
    with store.engine.begin() as conn:
        ensure_default_statuses(conn, org)
        position = (conn.execute(
            select(func.max(project_status.c.position)).where(
                project_status.c.organization_id == org)
        ).scalar() or -1) + 1
        row_id = new_id()
        ts = now_iso()
        try:
            conn.execute(project_status.insert().values(
                id=row_id, organization_id=org, name=name, category=input["category"],
                color=input.get("color"), position=position,
                created_at=ts, updated_at=ts))
        except IntegrityError:
            raise GraphQLError(
                f"A status named '{name}' already exists.",
                extensions={"code": "BAD_USER_INPUT", "field": "name"}) from None
        row = dict(conn.execute(
            select(project_status).where(project_status.c.id == row_id)).first()._mapping)
    return {"success": True, "projectStatus": _serialize_status(row)}


@mutation.field("projectStatusUpdate")
def m_status_update(_, info, id, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    name = _require_non_empty(input["name"], "name")
    _check_category(input["category"])
    with store.engine.begin() as conn:
        row = conn.execute(select(project_status).where(and_(
            project_status.c.organization_id == org, project_status.c.id == id))).first()
        if not row:
            raise GraphQLError("Status not found.", extensions={"code": "NOT_FOUND"})
        try:
            conn.execute(project_status.update().where(project_status.c.id == id).values(
                name=name, category=input["category"], color=input.get("color"),
                updated_at=now_iso()))
        except IntegrityError:
            raise GraphQLError(
                f"A status named '{name}' already exists.",
                extensions={"code": "BAD_USER_INPUT", "field": "name"}) from None
        updated = dict(conn.execute(
            select(project_status).where(project_status.c.id == id)).first()._mapping)
    return {"success": True, "projectStatus": _serialize_status(updated)}


@mutation.field("projectStatusDelete")
def m_status_delete(_, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    # A workspace must keep at least one status: a project always has a state.
    remaining = [r for r in project_statuses(store.engine, org) if r["id"] != id]
    if not remaining:
        raise GraphQLError(
            "The last status cannot be deleted; every project has a state.",
            extensions={"code": "BAD_USER_INPUT"})
    with store.engine.begin() as conn:
        result = conn.execute(project_status.delete().where(and_(
            project_status.c.organization_id == org, project_status.c.id == id)))
    return result.rowcount > 0


@mutation.field("projectStatusReorder")
def m_status_reorder(_, info, ids):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    current = {r["id"] for r in project_statuses(store.engine, org)}
    if len(ids) != len(current) or set(ids) != current:
        raise GraphQLError(
            "Reorder must list exactly the workspace's statuses, in the new order.",
            extensions={"code": "BAD_USER_INPUT", "field": "ids"})
    ts = now_iso()
    with store.engine.begin() as conn:
        for position, status_id in enumerate(ids):
            conn.execute(project_status.update().where(and_(
                project_status.c.organization_id == org,
                project_status.c.id == status_id)).values(position=position, updated_at=ts))
    return True


@query.field("projectTemplates")
def r_project_templates(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        rows = conn.execute(select(project_template).where(and_(
            project_template.c.organization_id == org,
            project_template.c.archived_at.is_(None))).order_by(
            project_template.c.created_at)).all()
        rows = [dict(r._mapping) for r in rows]
    return [_serialize_template(store, org, r) for r in rows]


def _template_values(engine, org, inp):
    """Validate template input into column values. Status, if given, must be
    one the workspace has configured — a template with an unconfigured default
    status would silently create projects the API refuses to transition."""
    values = {"name": _require_non_empty(inp["name"], "name"),
              "description": inp.get("description"), "color": inp.get("color"),
              "icon": inp.get("icon")}
    if inp.get("state"):
        values["state"] = validate_project_state(engine, org, inp["state"])
    else:
        values["state"] = None
    task_set = inp.get("taskSet")
    if task_set is not None:
        cleaned = [t.strip() for t in task_set if isinstance(t, str) and t.strip()]
        if cleaned and not (inp.get("teamIds") or []):
            raise GraphQLError(
                "A template with a task set needs at least one team: its tasks "
                "are created in that team.",
                extensions={"code": "BAD_USER_INPUT", "field": "teamIds"})
        values["task_set"] = json.dumps(cleaned) if cleaned else None
    return values


@mutation.field("projectTemplateCreate")
def m_template_create(_, info, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    _require_org_references(store, org, input.get("leadId"), input.get("teamIds"))
    values = _template_values(store.engine, org, input)
    template_id = new_id()
    ts = now_iso()
    with store.engine.begin() as conn:
        conn.execute(project_template.insert().values(
            id=template_id, organization_id=org, lead_id=input.get("leadId"),
            created_at=ts, updated_at=ts, **values))
        for team_id in input.get("teamIds") or []:
            conn.execute(project_template_team.insert().values(
                template_id=template_id, team_id=team_id))
    row = _template_row(store, org, template_id)
    return {"success": True, "projectTemplate": _serialize_template(store, org, row)}


@mutation.field("projectTemplateUpdate")
def m_template_update(_, info, id, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    _require_org_references(store, org, input.get("leadId"), input.get("teamIds"))
    row = _template_row(store, org, id)
    if not row:
        raise GraphQLError("Template not found.", extensions={"code": "NOT_FOUND"})
    values = _template_values(store.engine, org, input)
    values["lead_id"] = input.get("leadId")
    values["updated_at"] = now_iso()
    with store.engine.begin() as conn:
        conn.execute(project_template.update().where(project_template.c.id == id).values(**values))
        conn.execute(project_template_team.delete().where(
            project_template_team.c.template_id == id))
        for team_id in input.get("teamIds") or []:
            conn.execute(project_template_team.insert().values(template_id=id, team_id=team_id))
    row = _template_row(store, org, id)
    return {"success": True, "projectTemplate": _serialize_template(store, org, row)}


@mutation.field("projectTemplateArchive")
def m_template_archive(_, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        result = conn.execute(project_template.update().where(and_(
            project_template.c.organization_id == org, project_template.c.id == id,
            project_template.c.archived_at.is_(None))).values(
            archived_at=now_iso(), updated_at=now_iso()))
    return result.rowcount > 0
