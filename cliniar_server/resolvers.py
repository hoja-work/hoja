"""Ariadne resolver bindings — maps the SDL to Store/Writer.

Auth context (user_id, organization_id) is attached to `info.context` by the
FastAPI middleware in app.py. Every resolver reads ctx and stays org-scoped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import json

from graphql import GraphQLError
from ariadne import (
    MutationType,
    ObjectType,
    QueryType,
    ScalarType,
    make_executable_schema,
)
query = QueryType()
mutation = MutationType()
Initiative = ObjectType("Initiative")

# Object types needing custom field resolvers
Team = ObjectType("Team")
Initiative.set_field("owner", lambda obj, info: info.context["store"].get_user(info.context["org_id"], obj.get("_owner_id")) if obj.get("_owner_id") else None)
Initiative.set_field("creator", lambda obj, info: info.context["store"].get_user(info.context["org_id"], obj.get("_creator_id")) if obj.get("_creator_id") else None)
Issue = ObjectType("Issue")
IssueActivity = ObjectType("IssueActivity")
MyIssueActivity = ObjectType("MyIssueActivity")
Project = ObjectType("Project")
ProjectUpdate = ObjectType("ProjectUpdate")
Comment = ObjectType("Comment")
WorkflowState = ObjectType("WorkflowState")
Cycle = ObjectType("Cycle")
Attachment = ObjectType("Attachment")

datetime_scalar = ScalarType("DateTime")
timeless_scalar = ScalarType("TimelessDate")
json_scalar = ScalarType("JSON")


@datetime_scalar.serializer
def _ser_dt(value):
    return value  # already ISO strings in the DB


@timeless_scalar.serializer
def _ser_td(value):
    return value


@json_scalar.serializer
def _ser_json(value):
    return value


def _ctx(info):
    return info.context["store"], info.context["writer"], info.context["org_id"], info.context["user_id"]


def _conn(nodes: list) -> dict:
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": False, "hasPreviousPage": False,
            "startCursor": None,
            "endCursor": (nodes[-1].get("id") if nodes else None),
        },
    }


def _issue_cursor(node: dict, order_by: str) -> str:
    value = node.get("updatedAt" if order_by == "updatedAt" else "createdAt")
    payload = json.dumps([value, node["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_issue_cursor(cursor: str | None):
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(part, str) and part for part in value):
            raise ValueError
        return value[0], value[1]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid issue cursor.") from exc


def _activity_cursor(row):
    raw = json.dumps([row["created_at"], row["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_activity_cursor(cursor):
    if not cursor:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) for x in value):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid activity cursor.") from exc


def _project_update_cursor(row):
    raw = json.dumps([row["created_at"], row["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_project_update_cursor(cursor):
    if cursor is None:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) and x for x in value):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid project update cursor.") from exc


# ----------------------------------------------------------------- Query
@query.field("inboxNotifications")
def r_inbox_notifications(_, info, first=50, after=None, unreadOnly=False, archived=False):
    store, _w, org, uid = _ctx(info)
    if first < 0:
        raise ValueError("first must be non-negative.")
    cursor = _decode_activity_cursor(after)
    rows = store.inbox_notifications(org, uid, cursor, first + 1,
                                     unread_only=unreadOnly, archived=archived)
    nodes = [dict(row, createdAt=row["created_at"], readAt=row["read_at"],
                  archivedAt=row["archived_at"], _actor_id=row["actor_id"],
                  _issue_id=row["issue_id"]) for row in rows[:first]]
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(nodes[0]) if nodes else None,
        "endCursor": _activity_cursor(nodes[-1]) if nodes else None,
    }}


@query.field("inboxUnreadCount")
def r_inbox_unread_count(_, info):
    store, _w, org, uid = _ctx(info)
    return store.inbox_unread_count(org, uid)


@mutation.field("notificationMarkRead")
def m_notification_mark_read(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.inbox_mark(org, uid, id, "read_at")


@mutation.field("notificationArchive")
def m_notification_archive(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.inbox_mark(org, uid, id, "archived_at")


@mutation.field("notificationMarkAllRead")
def m_notification_mark_all_read(_, info):
    store, _w, org, uid = _ctx(info)
    return store.inbox_mark_all_read(org, uid)


InboxNotification = ObjectType("InboxNotification")


@InboxNotification.field("issue")
def inbox_issue(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["_issue_id"])


@InboxNotification.field("actor")
def inbox_actor(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_actor_id"]), uid) if obj.get("_actor_id") else None


@query.field("viewer")
def r_viewer(_, info):
    store, _w, org, uid = _ctx(info)
    return store.viewer(org, uid)


@query.field("myApiKeys")
def r_my_api_keys(_, info):
    store, _w, org, uid = _ctx(info)
    return store.list_api_keys(org, uid)


def _preference_payload(store, uid, *, success=True):
    values = store.user_preferences(uid)
    if values is None:
        return {"success": False, "theme": None, "fontSize": None}
    return {"success": success, "theme": values.get("theme", "light"),
            "fontSize": values.get("fontSize", "100")}


@query.field("myPreferences")
def r_my_preferences(_, info):
    store, _w, _org, uid = _ctx(info)
    return _preference_payload(store, uid)


@query.field("myNotificationPreferences")
def r_my_notification_preferences(_, info):
    store, _w, _org, uid = _ctx(info)
    prefs = store.user_preferences(uid) or {}
    return {"success": True,
            "issueCreated": prefs.get("notification:issueCreated", "true") == "true",
            "issueUpdated": prefs.get("notification:issueUpdated", "true") == "true"}


@query.field("issueLabelSettings")
def r_issue_label_settings(_, info):
    store, _w, org, _uid = _ctx(info)
    return store.labels(org)


@query.field("myWorkspaces")
def r_my_workspaces(_, info):
    store, _w, org, uid = _ctx(info)
    identity_id = info.context.get("identity_id")
    if not identity_id:
        membership = store.membership_for_profile(org, uid)
        identity_id = membership.get("identity_id") if membership else None
    if not identity_id:
        return []
    return store.identity_workspaces(identity_id)


@query.field("organizationMembers")
def r_organization_members(_, info, first=100):
    store, _w, org, uid = _ctx(info)
    return _conn([store.ser_user(member, uid) for member in store.organization_members(org)[:first]])


@query.field("workspace")
def r_workspace(_, info):
    store, _w, org, _uid = _ctx(info)
    return store.workspace(org)


@query.field("teams")
def r_teams(_, info, first=50, after=None, filter=None):
    store, _w, org, _uid = _ctx(info)
    key = None
    if filter and "key" in filter and "eq" in filter["key"]:
        key = filter["key"]["eq"]
    return _conn(store.teams(org, key=key)[:first])


@query.field("team")
def r_team(_, info, id):
    store, _w, org, _uid = _ctx(info)
    row = store.team_by_id_or_key(org, id)
    if not row:
        return None
    t = store.ser_team(row)
    t["_active_cycle_id"] = row.get("active_cycle_id")
    return t


@query.field("issues")
def r_issues(_, info, filter=None, first=50, after=None, orderBy="updatedAt"):  # noqa: N803
    store, _w, org, uid = _ctx(info)
    order_by = orderBy or "updatedAt"
    if order_by not in ("updatedAt", "createdAt"):
        raise ValueError("Unsupported issue ordering.")
    if first < 0:
        raise ValueError("first must be non-negative.")
    cursor = _decode_issue_cursor(after)
    rows = store.issues(org, uid, filter, order_by=order_by, after=cursor, limit=first + 1)
    nodes = rows[:first]
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first,
        "hasPreviousPage": after is not None,
        "startCursor": _issue_cursor(nodes[0], order_by) if nodes else None,
        "endCursor": _issue_cursor(nodes[-1], order_by) if nodes else None,
    })
    return result


@query.field("issue")
def r_issue(_, info, id):
    store, _w, org, _uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, id)


@query.field("projects")
def r_projects(_, info, filter=None, first=50, after=None):
    store, _w, org, _uid = _ctx(info)
    if first < 0:
        raise ValueError("first must be non-negative.")
    cursor = _decode_project_update_cursor(after)
    rows = store.projects(org, filter, after=cursor, limit=first + 1)
    nodes = rows[:first]
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first,
        "hasPreviousPage": after is not None,
        "startCursor": _project_update_cursor({"created_at": nodes[0]["createdAt"], "id": nodes[0]["id"]}) if nodes else None,
        "endCursor": _project_update_cursor({"created_at": nodes[-1]["createdAt"], "id": nodes[-1]["id"]}) if nodes else None,
    })
    return result

@query.field("initiatives")
def r_initiatives(_, info, first=50, after=None, status=None):
    store, _w, org, _uid = _ctx(info)
    if first < 0: raise ValueError("first must be non-negative.")
    cursor = _decode_project_update_cursor(after)
    rows = store.initiatives(org, after=cursor, status=status, limit=first + 1)
    nodes = rows[:first]
    result = _conn(nodes)
    result["pageInfo"].update({"hasNextPage": len(rows) > first, "hasPreviousPage": after is not None, "startCursor": _project_update_cursor({"created_at": nodes[0]["createdAt"], "id": nodes[0]["id"]}) if nodes else None, "endCursor": _project_update_cursor({"created_at": nodes[-1]["createdAt"], "id": nodes[-1]["id"]}) if nodes else None})
    return result

@query.field("initiative")
def r_initiative(_, info, id):
    store, _w, org, _uid = _ctx(info)
    return store.initiative_by_id(org, id)


@query.field("projectUpdates")
def r_project_updates(_, info, first=50, after=None, filter=None):
    store, _w, org, _uid = _ctx(info)
    if first < 0:
        raise ValueError("first must be non-negative.")
    project_filter = (filter or {}).get("project") or {}
    project_id = (project_filter.get("id") or {}).get("eq")
    cursor = _decode_project_update_cursor(after)
    rows = store.project_updates(org, after=cursor, project_id=project_id, limit=first + 1)
    nodes = rows[:first]
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _project_update_cursor(nodes[0]) if nodes else None,
        "endCursor": _project_update_cursor(nodes[-1]) if nodes else None,
    })
    return result


@query.field("project")
def r_project(_, info, id):
    store, _w, org, _uid = _ctx(info)
    return store.project_by_id(org, id)


@query.field("workflowStates")
def r_workflow_states(_, info, filter=None, first=100, after=None):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.workflow_states(org, filter)[:first])


@query.field("cycles")
def r_cycles(_, info, filter=None, first=100, after=None):
    store, writer, org, _uid = _ctx(info)
    if filter and "team" in filter and "id" in filter["team"] and "eq" in filter["team"]["id"]:
        writer.reconcile_team_cadence(org, filter["team"]["id"]["eq"])
    return _conn(store.cycles(org, filter)[:first])


@query.field("teamCycleCadence")
def r_team_cycle_cadence(_, info, teamId):
    store, writer, org, _uid = _ctx(info)
    return writer.reconcile_team_cadence(org, teamId)


@mutation.field("teamCycleCadenceEnable")
def m_team_cycle_cadence_enable(_, info, teamId, weekday):
    store, writer, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise ValueError("Only workspace admins can enable team cycles.")
    return writer.enable_team_cadence(org, teamId, weekday)


@query.field("cycle")
def r_cycle(_, info, id):
    store, _w, org, _uid = _ctx(info)
    return store.cycle_by_id(org, id)


@query.field("issueLabels")
def r_labels(_, info, filter=None, first=100, after=None):
    store, _w, org, _uid = _ctx(info)
    team_id = None
    name = None
    if filter:
        if "team" in filter and "id" in filter["team"] and "eq" in filter["team"]["id"]:
            team_id = filter["team"]["id"]["eq"]
        if "name" in filter and "eq" in filter["name"]:
            name = filter["name"]["eq"]
    return _conn(store.labels(org, team_id=team_id, name=name)[:first])


@query.field("searchIssues")
def r_search(_, info, term, first=20):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.search_issues(org, term)[:first])


@query.field("rateLimitStatus")
def r_rate_limit(_, info):
    resets = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return {
        "identifier": "local", "kind": "unlimited",
        "limit": 1000000, "remaining": 1000000, "requestsPerHour": 1000000,
        "resetsAt": resets,
    }


# ----------------------------------------------------------------- Team fields
@Team.field("states")
def t_states(obj, info, first=100):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.team_states(org, obj["id"])[:first])


@Team.field("members")
def t_members(obj, info, first=100):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.team_members(org, obj["id"])[:first])


@Team.field("cycles")
def t_cycles(obj, info, first=50):
    store, writer, org, _uid = _ctx(info)
    writer.reconcile_team_cadence(org, obj["id"])
    return _conn(store.team_cycles(org, obj["id"])[:first])


@Team.field("activeCycle")
def t_active_cycle(obj, info):
    store, _w, org, _uid = _ctx(info)
    cid = obj.get("_active_cycle_id")
    if not cid:
        return None
    status = info.context["writer"].reconcile_team_cadence(org, obj["id"])
    return status["activeCycle"] if status["enabled"] else next((c for c in store.team_cycles(org, obj["id"]) if c["id"] == cid), None)


# ----------------------------------------------------------------- State/cycle fields
@WorkflowState.field("team")
@Cycle.field("team")
def reference_team(obj, info):
    store, _w, org, _uid = _ctx(info)
    tid = obj.get("_team_id")
    if not tid:
        return None
    return store.ser_team(store.raw_team(org, tid))


# ----------------------------------------------------------------- Issue fields
@Issue.field("state")
def i_state(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.state_by_id(org, obj["_state_id"]) if obj.get("_state_id") else None


@Issue.field("assignee")
def i_assignee(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_assignee_id"]), uid) if obj.get("_assignee_id") else None


@Issue.field("creator")
def i_creator(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_creator_id"]), uid) if obj.get("_creator_id") else None


@Issue.field("team")
def i_team(obj, info):
    store, _w, org, _uid = _ctx(info)
    row = store.raw_team(org, obj["_team_id"]) if obj.get("_team_id") else None
    if not row:
        return None
    t = store.ser_team(row)
    t["_active_cycle_id"] = row.get("active_cycle_id")
    return t


@Issue.field("project")
def i_project(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.project_by_id(org, obj["_project_id"]) if obj.get("_project_id") else None


@Issue.field("cycle")
def i_cycle(obj, info):
    store, _w, org, _uid = _ctx(info)
    if not obj.get("_cycle_id"):
        return None
    with store.engine.connect() as conn:
        from sqlalchemy import and_, select

        from cliniar_server.db import cycle as cyc
        r = conn.execute(select(cyc).where(and_(cyc.c.organization_id == org, cyc.c.id == obj["_cycle_id"]))).first()
        return store.ser_cycle(dict(r._mapping)) if r else None


@Issue.field("parent")
def i_parent(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["_parent_id"]) if obj.get("_parent_id") else None


@Issue.field("labels")
def i_labels(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.issue_labels_for(org, obj["id"])[:first])


@Issue.field("subscribers")
def i_subscribers(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.issue_subscribers_for(org, obj["id"])[:first])


@Issue.field("comments")
def i_comments(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.comments_for(org, obj["id"])[:first])


@query.field("myIssueActivity")
def r_my_issue_activity(_, info, first=50, after=None):
    store, _w, org, uid = _ctx(info)
    if first < 0:
        raise ValueError("first must be non-negative.")
    cursor = _decode_activity_cursor(after)
    rows = store.my_issue_activity(org, uid, cursor, first + 1)
    nodes = [dict(row, eventType=row["event_type"], createdAt=row["created_at"]) for row in rows[:first]]
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(nodes[0]) if nodes else None,
        "endCursor": _activity_cursor(nodes[-1]) if nodes else None,
    }}


@MyIssueActivity.field("issue")
def mia_issue(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["issue_id"])


@MyIssueActivity.field("actor")
def mia_actor(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["actor_id"]), uid) if obj.get("actor_id") else None


@Issue.field("activity")
def i_activity(obj, info, first=50, after=None):
    store, _w, org, _uid = _ctx(info)
    if first < 0:
        raise ValueError("first must be non-negative.")
    cursor = _decode_activity_cursor(after)
    rows = store.issue_activity_for(org, obj["id"], cursor, first + 1)
    nodes = [dict(row, eventType=row["event_type"], createdAt=row["created_at"]) for row in rows[:first]]
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(nodes[0]) if nodes else None,
        "endCursor": _activity_cursor(nodes[-1]) if nodes else None,
    }}


@IssueActivity.field("actor")
def ia_actor(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["actor_id"]), obj["actor_id"]) if obj.get("actor_id") else None


# ----------------------------------------------------------------- Project fields
@Project.field("lead")
def p_lead(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_lead_id"]), uid) if obj.get("_lead_id") else None


@ProjectUpdate.field("project")
def pu_project(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.project_update_project(org, obj["_project_id"])


@ProjectUpdate.field("actor")
def pu_actor(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_actor_id"]), uid) if obj.get("_actor_id") else None


@Project.field("creator")
def p_creator(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_creator_id"]), uid) if obj.get("_creator_id") else None


@Project.field("members")
def p_members(obj, info, first=100):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.project_members(org, obj["id"])[:first])


@Project.field("teams")
def p_teams(obj, info, first=100):
    store, _w, org, _uid = _ctx(info)
    return _conn(store.project_teams(org, obj["id"])[:first])


# ----------------------------------------------------------------- Comment fields
@Comment.field("user")
def c_user(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.ser_user(store.get_user(org, obj["_user_id"]), uid) if obj.get("_user_id") else None


# ----------------------------------------------------------------- Mutations
@mutation.field("workspaceCreate")
def m_workspace_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    identity_id = info.context.get("identity_id")
    if not identity_id:
        membership = store.membership_for_profile(org, uid)
        identity_id = membership.get("identity_id") if membership else None
    if not identity_id:
        raise ValueError("Workspace identity is unavailable")
    created = writer.workspace_create(org, uid, input)
    created.pop("_identity_id", None)
    info.context["switch_target"] = {
        "user_id": created.pop("_profile_user_id"),
        "identity_id": identity_id,
        "organization_id": created["id"],
    }
    return {"success": True, "organization": created}

@mutation.field("teamCreate")
def m_team_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    tid = writer.team_create(org, uid, input)
    row = store.team_by_id_or_key(org, tid)
    return {"success": True, "team": store.ser_team(row)}


@mutation.field("teamUpdate")
def m_team_update(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    tid = writer.team_update(org, uid, id, input)
    return {"success": True, "team": store.ser_team(store.team_by_id_or_key(org, tid))}


@mutation.field("teamArchive")
def m_team_archive(_, info, id):
    _s, writer, org, uid = _ctx(info)
    return {"success": writer.team_archive(org, uid, id)}


@mutation.field("teamDelete")
def m_team_delete(_, info, id, deleteIssues=False):
    _s, writer, org, uid = _ctx(info)
    return {"success": writer.team_delete(org, uid, id, delete_issues=bool(deleteIssues))}


@mutation.field("workspaceUpdate")
def m_workspace_update(_, info, input):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "organization": None}
    updated = store.update_workspace(org, input)
    return {"success": bool(updated), "organization": updated}


@mutation.field("apiKeyCreate")
def m_api_key_create(_, info, label, access="read_write", expiresAt=None):
    store, _w, org, uid = _ctx(info)
    created = store.create_api_key(org, uid, label, access, expiresAt)
    if not created:
        return {"success": False, "apiKey": None, "secret": None}
    metadata, secret = created
    return {"success": True, "apiKey": metadata, "secret": secret}


@mutation.field("apiKeyRevoke")
def m_api_key_revoke(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.revoke_api_key(org, uid, id)


@mutation.field("userPreferenceSet")
def m_user_preference_set(_, info, key, value):
    store, _w, _org, uid = _ctx(info)
    success = store.set_user_preference(uid, key, value)
    return _preference_payload(store, uid, success=success)


@mutation.field("profileNameUpdate")
def m_profile_name_update(_, info, name):
    store, _w, org, uid = _ctx(info)
    updated = store.update_profile_name(org, uid, name)
    return {"success": bool(updated), "user": updated, "apiKey": None}


@mutation.field("notificationPreferenceSet")
def m_notification_preference_set(_, info, kind, enabled):
    store, _w, _org, uid = _ctx(info)
    success = store.set_notification_preference(uid, kind, enabled)
    prefs = store.user_preferences(uid) or {}
    return {"success": success,
            "issueCreated": prefs.get("notification:issueCreated", "true") == "true",
            "issueUpdated": prefs.get("notification:issueUpdated", "true") == "true"}


@mutation.field("workspaceSwitch")
def m_workspace_switch(_, info, organizationId):
    store, _w, org, uid = _ctx(info)
    if not info.context.get("browser_session"):
        return {"success": False, "organization": None}
    member = store.membership_for_profile(org, uid)
    target = store.switch_membership(member.get("identity_id") if member else None, organizationId)
    if not target:
        return {"success": False, "organization": None}
    info.context["switch_target"] = target
    return {"success": True, "organization": target["organization"]}


@mutation.field("membershipInvitationCreate")
def m_membership_invitation_create(_, info, userId):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    target = store.get_user(org, userId)
    if not actor or not actor.get("admin") or not target:
        return {"success": False, "user": None, "inviteCode": None}
    code = store.create_membership_invitation(org, userId, uid)
    return {"success": bool(code), "user": store.ser_user(store.get_user(org, userId), uid),
            "inviteCode": code}


@query.field("pendingMembershipInvitations")
def q_pending_membership_invitations(_, info):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return []
    return store.pending_membership_invitations(org)


@mutation.field("membershipInvitationRevoke")
def m_membership_invitation_revoke(_, info, userId):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False}
    return {"success": store.revoke_membership_invitation(org, userId)}


@mutation.field("memberDeactivate")
def m_member_deactivate(_, info, userId):
    return _set_member_active(info, userId, active=False)


@mutation.field("memberReactivate")
def m_member_reactivate(_, info, userId):
    return _set_member_active(info, userId, active=True)


def _set_member_active(info, user_id, *, active: bool):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "user": None}
    target = store.set_member_active(org, uid, user_id, active)
    return {"success": bool(target), "user": store.ser_user(target, uid)}


@mutation.field("membershipInvitationAccept")
def m_membership_invitation_accept(_, info, code, inviteePassword):
    store, _w, _org, uid = _ctx(info)
    identity_id = info.context.get("identity_id")
    organization = store.accept_membership_invitation(identity_id, code, inviteePassword) if identity_id else None
    return {"success": bool(organization), "organization": organization}


@mutation.field("memberRoleUpdate")
def m_member_role_update(_, info, userId, admin):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "user": None}
    target = store.set_member_admin_role(org, userId, admin)
    return {"success": bool(target), "user": store.ser_user(target, uid)}


@mutation.field("memberInvite")
def m_member_invite(_, info, input):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "user": None, "apiKey": None}
    team_ids = input.get("teamIds") or []
    for team_id in team_ids:
        if not store.team_by_id_or_key(org, team_id):
            raise GraphQLError("Team not found", extensions={"code": "BAD_USER_INPUT", "field": "teamIds"})
    created = store.invite_member(org, input.get("name", ""), input.get("email", ""),
                                  bool(input.get("admin", False)), team_ids)
    if not created:
        return {"success": False, "user": None, "apiKey": None}
    user_row, api_key = created
    return {"success": True, "user": store.ser_user(user_row, uid), "apiKey": api_key}


def _set_team_membership(info, team_id, user_id, *, add: bool):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "user": None, "apiKey": None}
    if not store.team_by_id_or_key(org, team_id):
        raise GraphQLError("Team not found", extensions={"code": "BAD_USER_INPUT", "field": "teamId"})
    target = store.get_user(org, user_id)
    if not target:
        raise GraphQLError("User not found", extensions={"code": "BAD_USER_INPUT", "field": "userId"})
    store.set_team_membership(org, team_id, user_id, add=add)
    return {"success": True, "user": store.ser_user(store.get_user(org, user_id), uid), "apiKey": None}


@mutation.field("teamMemberAdd")
def m_team_member_add(_, info, teamId, userId):
    return _set_team_membership(info, teamId, userId, add=True)


@mutation.field("teamMemberRemove")
def m_team_member_remove(_, info, teamId, userId):
    return _set_team_membership(info, teamId, userId, add=False)


@mutation.field("issueCreate")
def m_issue_create(_, info, input):
    store, w, org, uid = _ctx(info)
    iid = w.issue_create(org, uid, input)
    if not iid:
        return {"success": False, "issue": None}
    return {"success": True, "issue": store.issue_by_id_or_identifier(org, iid)}


@mutation.field("issueUpdate")
def m_issue_update(_, info, id, input):
    store, w, org, _uid = _ctx(info)
    iid = w.issue_update(org, id, input, actor_id=_uid)
    if not iid:
        return {"success": False, "issue": None}
    return {"success": True, "issue": store.issue_by_id_or_identifier(org, iid)}


@mutation.field("issueDelete")
def m_issue_delete(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.issue_delete(org, uid, id)}


@mutation.field("issueArchive")
def m_issue_archive(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.issue_archive(org, uid, id)}


@mutation.field("commentCreate")
def m_comment_create(_, info, input):
    store, w, org, uid = _ctx(info)
    cid = w.comment_create(org, uid, input)
    if not cid:
        return {"success": False, "comment": None}
    return {"success": True, "comment": _get_comment(store, org, cid)}


@mutation.field("commentUpdate")
def m_comment_update(_, info, id, input):
    store, w, org, _uid = _ctx(info)
    cid = w.comment_update(org, id, input)
    if not cid:
        return {"success": False, "comment": None}
    return {"success": True, "comment": _get_comment(store, org, cid)}


@mutation.field("commentDelete")
def m_comment_delete(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.comment_delete(org, uid, id)}


@mutation.field("issueLabelCreate")
def m_label_create(_, info, input):
    store, w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "issueLabel": None}
    name = str(input.get("name", "")).strip()
    if not name or len(name) > 64:
        return {"success": False, "issueLabel": None}
    input = {**input, "name": name}
    lid = w.label_create(org, input)
    labels = store.labels(org)
    node = next((label for label in labels if label["id"] == lid), None)
    return {"success": bool(lid), "issueLabel": node}


@mutation.field("issueLabelUpdate")
def m_label_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    if not w.label_update(org, uid, id, input):
        return {"success": False, "issueLabel": None}
    node = next((label for label in store.labels(org) if label["id"] == id), None)
    return {"success": True, "issueLabel": node}


@mutation.field("issueLabelDelete")
def m_label_delete(_, info, id):
    store, w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False}
    return {"success": w.label_delete(org, id)}


@mutation.field("projectCreate")
def m_project_create(_, info, input):
    store, w, org, uid = _ctx(info)
    pid = w.project_create(org, uid, input)
    return {"success": bool(pid), "project": store.project_by_id(org, pid) if pid else None}


@mutation.field("projectUpdate")
def m_project_update(_, info, id, input):
    store, w, org, _uid = _ctx(info)
    pid = w.project_update(org, id, input)
    return {"success": bool(pid), "project": store.project_by_id(org, pid) if pid else None}


@mutation.field("projectUpdateCreate")
def m_project_update_create(_, info, input):
    store, w, org, uid = _ctx(info)
    update_id = w.project_update_create(org, input["projectId"], uid, input)
    rows = store.project_updates(org, project_id=input["projectId"], limit=1) if update_id else []
    created = next((row for row in rows if row["id"] == update_id), None)
    return {"success": bool(created), "projectUpdate": created}


@mutation.field("projectArchive")
def m_project_archive(_, info, id):
    _s, w, org, _uid = _ctx(info)
    return {"success": w.project_archive(org, id)}

@mutation.field("initiativeCreate")
def m_initiative_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    ident = writer.initiative_create(org, uid, input)
    return {"success": True, "initiative": store.initiative_by_id(org, ident)}

@mutation.field("initiativeUpdate")
def m_initiative_update(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    success = writer.initiative_update(org, uid, id, input)
    return {"success": success, "initiative": store.initiative_by_id(org, id) if success else None}

@mutation.field("initiativeArchive")
def m_initiative_archive(_, info, id):
    _store, writer, org, uid = _ctx(info)
    return {"success": writer.initiative_archive(org, uid, id)}


@mutation.field("attachmentCreate")
def m_attachment_create(_, info, input):
    _store, w, org, uid = _ctx(info)
    created = w.attachment_create(org, uid, input)
    return {"success": bool(created), "attachment": created}


@Attachment.field("issue")
def a_issue(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["_issue_id"])


def _get_comment(store, org, cid):
    from sqlalchemy import and_, select

    from cliniar_server.db import comment as cmt
    with store.engine.connect() as conn:
        r = conn.execute(select(cmt).where(and_(cmt.c.organization_id == org, cmt.c.id == cid))).first()
        return store.ser_comment(dict(r._mapping)) if r else None


def build_schema():
    from pathlib import Path
    sdl = (Path(__file__).parent / "schema.graphql").read_text()
    from cliniar_server.model_providers import SDL as PROVIDER_SDL
    from cliniar_server import model_providers
    return make_executable_schema(
        [sdl, PROVIDER_SDL], query, mutation, model_providers.query, model_providers.mutation,
        model_providers.model_provider_type, Team, Initiative, Issue, IssueActivity, MyIssueActivity, Project, ProjectUpdate, Comment,
        WorkflowState, Cycle, Attachment,
        datetime_scalar, timeless_scalar, json_scalar, InboxNotification,
    )
