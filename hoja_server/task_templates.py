"""Task templates: a stored set of defaults that ``issueCreate`` expands (HOJA-492).

The reference has Issue → Templates; Hoja had no template store, so the pane
was a visible gap rather than a screen. This module is the store plus the
expansion helper ``writer.issue_create`` calls — the copy of the shape
``project_settings`` proved for projects (HOJA-489), mapped onto tasks.

Two levels, as in the reference: a template with ``team_id`` NULL is
workspace-level and may create a task in any team the caller can see; a
template with a team is that team's own list.

**The one design decision — ``title`` and ``teamId`` becoming optional.**
``TaskCreateInput`` declared both as ``String!``, so a template could not
supply them. They are now ``String``, with this resolution order, and it is
deliberate rather than incidental:

1. An explicit input field always wins over the template's default. The pane
   prefills the form from the template precisely so a human can edit it; if
   the template then overrode the edit, the value a person saw on screen
   would not be the value stored. That is the failure this rule prevents,
   and it is why the rule is "input wins", not "template wins".
2. An absent field falls back to the template's default.
3. If neither supplies it, the mutation fails with ``BAD_USER_INPUT`` naming
   the field. It does not invent a title from the template's name, and it
   does not fall back to a placeholder: a task titled after its template is
   indistinguishable from one a person wrote.

For ``teamId`` the same order applies with one refusal: a caller may not use a
team-scoped template to create a task in a different team. The defaults were
authored for that team (its labels, its members), so moving them elsewhere
would silently produce a task the template author never described. A
workspace-level template has no team to disagree with.
"""

from __future__ import annotations

import json

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from hoja_server.db import (
    issue_label,
    new_id,
    now_iso,
    project,
    task_template,
    team,
    user,
)
from hoja_server.store import team_ancestor_ids

query = QueryType()
mutation = MutationType()
task_template_type = ObjectType("TaskTemplate")

SDL = gql(
    """
    type TaskTemplate {
      id: ID!
      name: String!
      description: String
      "Null for a workspace-level template; set for a team's own list."
      team: Team
      "The title a task created from this template gets when the caller omits one."
      defaultTitle: String!
      defaultDescription: String
      defaultPriority: Int
      defaultAssignee: User
      defaultProject: Project
      defaultLabels: [IssueLabel!]!
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    type TaskTemplatePayload { success: Boolean! taskTemplate: TaskTemplate }

    input TaskTemplateInput {
      name: String!
      description: String
      teamId: String
      defaultTitle: String!
      defaultDescription: String
      defaultPriority: Int
      defaultAssigneeId: String
      defaultProjectId: String
      defaultLabelIds: [String!]
    }

    extend type Query {
      "Every template in the workspace; with teamId, only that team's plus the workspace level it inherits."
      taskTemplates(teamId: String): [TaskTemplate!]!
    }

    extend type Mutation {
      taskTemplateCreate(input: TaskTemplateInput!): TaskTemplatePayload!
      taskTemplateUpdate(id: String!, input: TaskTemplateInput!): TaskTemplatePayload!
      taskTemplateArchive(id: String!): Boolean!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can manage task templates.",
            extensions={"code": "FORBIDDEN"},
        )


def _bad(field, message):
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _non_empty(value, field):
    if not isinstance(value, str) or not value.strip():
        _bad(field, f"{field} must not be empty.")
    return value.strip()


def _title_as_authored(value):
    """Non-empty, but kept exactly as written — a prefix's trailing space counts."""
    if not isinstance(value, str) or not value.strip():
        _bad("defaultTitle", "defaultTitle must not be empty.")
    return value


def _team_row(conn, org_id, team_id):
    row = conn.execute(select(team.c.id, team.c.key).where(and_(
        team.c.id == team_id,
        team.c.organization_id == org_id,
        team.c.archived_at.is_(None)))).first()
    if not row:
        _bad("teamId", "teamId must be a team in this workspace.")
    return dict(row._mapping)


def _checked_defaults(conn, org_id, team_id, values):
    """Validate the defaults a template names, at write time.

    A template that only fails when it is applied is a trap: the pane would
    save it, and the first person to create a task from it would get an error
    they cannot attribute. Every reference is therefore checked here, with the
    same rules ``issue_create`` applies to a hand-written task.
    """
    if assignee := values.get("default_assignee_id"):
        found = conn.execute(select(user.c.id).where(and_(
            user.c.id == assignee,
            user.c.organization_id == org_id,
            user.c.active.is_(True)))).first()
        if not found:
            _bad("defaultAssigneeId", "defaultAssigneeId is not an active member of this workspace.")
    if project_id := values.get("default_project_id"):
        found = conn.execute(select(project.c.id).where(and_(
            project.c.id == project_id,
            project.c.organization_id == org_id,
            project.c.archived_at.is_(None)))).first()
        if not found:
            _bad("defaultProjectId", "defaultProjectId must be a project in this workspace.")
    labels = values.get("_label_ids") or []
    if labels:
        applicable = team_ancestor_ids(conn, org_id, team_id) if team_id else None
        stmt = select(issue_label.c.id).where(and_(
            issue_label.c.id.in_(labels),
            issue_label.c.organization_id == org_id,
            issue_label.c.archived_at.is_(None)))
        if applicable:
            stmt = stmt.where(or_(issue_label.c.team_id.is_(None),
                                  issue_label.c.team_id.in_(applicable)))
        elif team_id is None:
            # A workspace-level template may only name workspace labels: it can
            # be applied to any team, and a team's label is not portable.
            stmt = stmt.where(issue_label.c.team_id.is_(None))
        found = set(conn.execute(stmt).scalars())
        if found != set(labels):
            _bad("defaultLabelIds",
                 "defaultLabelIds must be labels this template's team may use.")


def _name_taken(conn, org_id, team_id, name, exclude_id=None):
    stmt = select(task_template.c.id).where(and_(
        task_template.c.organization_id == org_id,
        task_template.c.archived_at.is_(None),
        task_template.c.name == name))
    stmt = stmt.where(task_template.c.team_id.is_(None)) if team_id is None \
        else stmt.where(task_template.c.team_id == team_id)
    if exclude_id:
        stmt = stmt.where(task_template.c.id != exclude_id)
    return conn.execute(stmt).first() is not None


def _row(store, org_id, template_id):
    with store.engine.connect() as conn:
        row = conn.execute(select(task_template).where(and_(
            task_template.c.organization_id == org_id,
            task_template.c.id == template_id,
            task_template.c.archived_at.is_(None),
        ))).first()
    return dict(row._mapping) if row else None


def _serialize(store, org_id, row) -> dict:
    with store.engine.connect() as conn:
        team_row = project_row = assignee_row = None
        if row["team_id"]:
            t = conn.execute(select(team).where(and_(
                team.c.id == row["team_id"],
                team.c.organization_id == org_id))).first()
            team_row = dict(t._mapping) if t else None
        if row["default_project_id"]:
            p = conn.execute(select(project).where(and_(
                project.c.id == row["default_project_id"],
                project.c.organization_id == org_id))).first()
            project_row = dict(p._mapping) if p else None
        if row["default_assignee_id"]:
            u = conn.execute(select(user.c.id, user.c.name, user.c.email).where(and_(
                user.c.id == row["default_assignee_id"],
                user.c.organization_id == org_id))).first()
            assignee_row = dict(u._mapping) if u else None
        label_ids = json.loads(row["default_label_ids"]) if row["default_label_ids"] else []
        labels = []
        if label_ids:
            labels = [dict(r._mapping) for r in conn.execute(
                select(issue_label).where(and_(
                    issue_label.c.id.in_(label_ids),
                    issue_label.c.organization_id == org_id))).all()]
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "team": team_row,
        "defaultTitle": row["default_title"],
        "defaultDescription": row["default_description"],
        "defaultPriority": row["default_priority"],
        "defaultAssignee": assignee_row,
        "defaultProject": project_row,
        "defaultLabels": labels,
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _values(conn, org_id, inp) -> dict:
    """Turn validated input into column values. Raises before any write."""
    team_id = inp.get("teamId") or None
    if team_id:
        _team_row(conn, org_id, team_id)
    values = {
        "team_id": team_id,
        "name": _non_empty(inp["name"], "name"),
        "description": inp.get("description"),
        # Validated for content, stored verbatim: a default title is often a
        # prefix ("Bug: "), and stripping it would make the template's task
        # differ from the same task typed into the form — the exact parity the
        # acceptance sentence asks for.
        "default_title": _title_as_authored(inp["defaultTitle"]),
        "default_description": inp.get("defaultDescription"),
        "default_priority": inp.get("defaultPriority"),
        "default_assignee_id": inp.get("defaultAssigneeId") or None,
        "default_project_id": inp.get("defaultProjectId") or None,
    }
    labels = inp.get("defaultLabelIds")
    values["_label_ids"] = labels or []
    values["default_label_ids"] = json.dumps(labels) if labels else None
    _checked_defaults(conn, org_id, team_id, values)
    return values


@query.field("taskTemplates")
def r_task_templates(_, info, teamId=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        stmt = select(task_template).where(and_(
            task_template.c.organization_id == org,
            task_template.c.archived_at.is_(None)))
        if teamId:
            # The team's own list, plus the workspace level it inherits from.
            stmt = stmt.where((task_template.c.team_id == teamId)
                              | (task_template.c.team_id.is_(None)))
        # No teamId means the whole workspace: the pane has to be able to show
        # a template it just created, and a workspace-only default silently
        # hid every team-scoped row after a reload (caught by the pane probe).
        rows = [dict(r._mapping) for r in conn.execute(
            stmt.order_by(task_template.c.name)).all()]
    return [_serialize(store, org, r) for r in rows]


@mutation.field("taskTemplateCreate")
def m_create(_, info, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        values = _values(conn, org, input)
        if _name_taken(conn, org, values["team_id"], values["name"]):
            _bad("name", f"A template named '{values['name']}' already exists here.")
        row_id = new_id()
        ts = now_iso()
        clean = {k: v for k, v in values.items() if not k.startswith("_")}
        try:
            conn.execute(task_template.insert().values(
                id=row_id, organization_id=org, created_at=ts, updated_at=ts, **clean))
        except IntegrityError:
            _bad("name", f"A template named '{values['name']}' already exists here.")
        row = dict(conn.execute(
            select(task_template).where(task_template.c.id == row_id)).first()._mapping)
    return {"success": True, "taskTemplate": _serialize(store, org, row)}


@mutation.field("taskTemplateUpdate")
def m_update(_, info, id, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    if not _row(store, org, id):
        raise GraphQLError("Task template not found.", extensions={"code": "NOT_FOUND"})
    with store.engine.begin() as conn:
        values = _values(conn, org, input)
        if _name_taken(conn, org, values["team_id"], values["name"], exclude_id=id):
            _bad("name", f"A template named '{values['name']}' already exists here.")
        clean = {k: v for k, v in values.items() if not k.startswith("_")}
        try:
            conn.execute(task_template.update().where(and_(
                task_template.c.organization_id == org,
                task_template.c.id == id)).values(updated_at=now_iso(), **clean))
        except IntegrityError:
            _bad("name", f"A template named '{values['name']}' already exists here.")
        row = dict(conn.execute(
            select(task_template).where(task_template.c.id == id)).first()._mapping)
    return {"success": True, "taskTemplate": _serialize(store, org, row)}


@mutation.field("taskTemplateArchive")
def m_archive(_, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        result = conn.execute(task_template.update().where(and_(
            task_template.c.organization_id == org,
            task_template.c.id == id,
            task_template.c.archived_at.is_(None),
        )).values(archived_at=now_iso(), updated_at=now_iso()))
    return result.rowcount > 0


# ------------------------------------------------------------ create expansion

def resolve_for_create(engine, org_id: str, inp: dict) -> dict | None:
    """The template a create request names, validated against the request's team.

    Returns None when the caller sent no ``templateId``, so the un-templated
    path stays byte-for-byte what it was. When a team-scoped template supplies
    the team, ``inp["teamId"]`` is filled from it — the caller's dict is the
    one the rest of the create path reads. Raises ``BAD_USER_INPUT`` when the
    template does not exist (archived counts as absent) or when a team-scoped
    template is asked to create in another team.
    """
    template_id = inp.get("templateId")
    if not template_id:
        return None
    with engine.connect() as conn:
        row = conn.execute(select(task_template).where(and_(
            task_template.c.organization_id == org_id,
            task_template.c.id == template_id,
            task_template.c.archived_at.is_(None),
        ))).first()
    if not row:
        _bad("templateId", "Task template not found.")
    row = dict(row._mapping)
    requested_team = inp.get("teamId") or None
    if row["team_id"]:
        if requested_team and requested_team not in (
                row["team_id"], _team_key(engine, org_id, row["team_id"])):
            _bad("teamId",
                 "This template belongs to a team; it cannot create a task in another team.")
        inp["teamId"] = row["team_id"]
    return row


def _team_key(engine, org_id, team_id):
    with engine.connect() as conn:
        return conn.execute(select(team.c.key).where(and_(
            team.c.id == team_id,
            team.c.organization_id == org_id))).scalar()


def team_id_for(engine, org_id: str, template_id: str) -> str | None:
    """The team a template will create in, or None for a workspace-level one.

    The create resolver needs this before it hands the input to the extension
    hook, because "is this caller allowed to attempt the write at all" is
    answered per team — and a templated create may not carry a team until the
    template supplies one. Read-only; a missing template answers None and the
    later expansion raises the real BAD_USER_INPUT error.
    """
    with engine.connect() as conn:
        return conn.execute(select(task_template.c.team_id).where(and_(
            task_template.c.organization_id == org_id,
            task_template.c.id == template_id,
            task_template.c.archived_at.is_(None)))).scalar()


def expand(inp: dict, template: dict) -> dict:
    """Fill only the fields the caller left absent. Explicit input always wins.

    Returned as a new dict: the caller's input is not mutated, so a rejection
    further down the create path cannot leave a half-expanded payload behind.
    """
    out = dict(inp)
    label_ids = out.get("labelIds")
    if label_ids is None and template["default_label_ids"]:
        out["labelIds"] = json.loads(template["default_label_ids"])
    if not (out.get("title") or "").strip():
        out["title"] = template["default_title"]
    if not (out.get("description") or "").strip() and template["default_description"]:
        out["description"] = template["default_description"]
    # None, not falsy: priority 0 is a real value a person can choose, so only
    # an omitted field (None) may be replaced by the template's default.
    if out.get("priority") is None and template["default_priority"] is not None:
        out["priority"] = template["default_priority"]
    if not out.get("assigneeId") and template["default_assignee_id"]:
        out["assigneeId"] = template["default_assignee_id"]
    if not out.get("projectId") and template["default_project_id"]:
        out["projectId"] = template["default_project_id"]
    return out
