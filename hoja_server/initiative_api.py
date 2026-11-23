"""Initiative structure, insight and update conversation (revisions 88 and 90).

The SDL lives here, like views_api.py: sub-initiatives, labels, resources, the lead
team, readable ids, health roll-up, the progress history graph, reactions and
replies on updates, and the "write with agent" draft. ``bind`` attaches the new
fields to the Initiative, InitiativeUpdate and Project types defined in
resolvers.py. Every read is organization-scoped through the request context.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ariadne import MutationType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, func, select

from hoja_server.db import (
    comment,
    initiative,
    initiative_label,
    initiative_label_link,
    initiative_parent,
    initiative_project,
    initiative_reminder_policy,
    initiative_resource,
    initiative_update,
    initiative_update_reaction,
    issue,
    issue_label,
    issue_label_link,
    new_id,
    now_iso,
    project,
    project_update,
    team,
    user,
    workflow_state,
)

log = logging.getLogger(__name__)

SDL = gql(
    """
    "A label, or a label group when isGroup is set; an exclusive group allows one of its labels."
    type InitiativeLabel { id: ID! name: String! color: String parent: InitiativeLabel isGroup: Boolean! exclusive: Boolean! }
    type SlackChannel { id: ID! name: String! isPrivate: Boolean! }
    "The workspace's update schedule: cadence, weekday (0 = Monday) and hour in the owner's timezone."
    type InitiativeReminderPolicy { cadence: String! weekday: Int! hour: Int! followUps: Boolean! }
    input InitiativeReminderPolicyInput { cadence: String weekday: Int hour: Int followUps: Boolean }
    type InitiativeInsightRow { key: String! label: String! color: String total: Int! completed: Int! }
    "Tasks across the initiative's projects (sub-initiatives included), broken down."
    type InitiativeInsights {
      scope: Int! completed: Int! started: Int!
      byStatus: [InitiativeInsightRow!]! byAssignee: [InitiativeInsightRow!]!
      byLabel: [InitiativeInsightRow!]! byProject: [InitiativeInsightRow!]!
    }
    type InitiativeResource { id: ID! title: String! url: String! createdAt: DateTime! creator: User }
    type InitiativeReaction { emoji: String! count: Int! viewerReacted: Boolean! users: [User!]! }
    "Active projects of an initiative and its sub-initiatives, counted by latest health."
    type InitiativeProjectHealth { onTrack: Int! atRisk: Int! offTrack: Int! noUpdate: Int! }
    type InitiativeProgressPoint { weekStart: String! completed: Int! }
    "Cumulative completed tasks per week for one project, oldest week first."
    type InitiativeProgressSeries { project: Project! scope: Int! points: [InitiativeProgressPoint!]! }
    type InitiativeUpdateDraft { body: String! health: String! "model, or changes when no model is configured" source: String! }
    type InitiativeResourcePayload { success: Boolean! resource: InitiativeResource }

    extend type Initiative {
      "Readable id, I-<number>, stable when the name changes."
      identifier: String!
      number: Int!
      icon: String
      color: String
      "The first parent; an initiative may have several (see parents)."
      parent: Initiative
      parents: [Initiative!]!
      children: [Initiative!]!
      slackChannel: SlackChannel
      insights: InitiativeInsights!
      leadTeam: Team
      labels: [InitiativeLabel!]!
      resources: [InitiativeResource!]!
      sortOrder: Float
      "null follows the workspace schedule; never, weekly, biweekly or monthly override it."
      updateReminder: String
      "When the next update is due under the reminder cadence; null without one."
      updateDueAt: DateTime
      "Health of the latest update."
      health: String
      projectHealth: InitiativeProjectHealth!
      "Projects of this initiative and of every sub-initiative, once each."
      allProjects: [Project!]!
      progressHistory(weeks: Int = 12): [InitiativeProgressSeries!]!
    }
    extend type InitiativeUpdate {
      comments(first: Int = 50): CommentConnection!
      reactions: [InitiativeReaction!]!
    }
    extend type Team {
      "Whether the team's sidebar lists Initiatives."
      showInitiatives: Boolean!
    }
    extend type Query {
      initiativeLabels: [InitiativeLabel!]!
      initiativeReminderPolicy: InitiativeReminderPolicy!
      "Channels the viewer's connected Slack account can read; empty when none is connected."
      slackChannels: [SlackChannel!]!
    }
    extend type Mutation {
      initiativeLabelCreate(name: String!, color: String, parentId: String, isGroup: Boolean, exclusive: Boolean): InitiativeLabel!
      initiativeLabelUpdate(id: String!, name: String, color: String, parentId: String, exclusive: Boolean): InitiativeLabel!
      initiativeLabelArchive(id: String!): DeletePayload!
      "Admins set the workspace's update schedule."
      initiativeReminderPolicyUpdate(input: InitiativeReminderPolicyInput!): InitiativeReminderPolicy!
      teamShowInitiatives(teamId: String!, show: Boolean!): Team
      initiativeResourceCreate(initiativeId: String!, url: String!, title: String): InitiativeResourcePayload!
      initiativeResourceDelete(id: String!): DeletePayload!
      "Add the viewer's reaction, or remove it when it is already there."
      initiativeUpdateReact(updateId: String!, emoji: String!): InitiativeUpdate
      "Draft an update from what changed since the last one."
      initiativeUpdateDraft(initiativeId: String!): InitiativeUpdateDraft!
    }
    """
)

query = QueryType()
mutation = MutationType()

HEALTH = ("on track", "at risk", "off track")
REMINDER_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
EMOJI_MAX = 16


def _ctx(info):
    c = info.context
    return c["store"], c["writer"], c["org_id"], c.get("user_id")


def _health(value: str | None) -> str | None:
    return value if value in HEALTH else None


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ reads

def descendant_ids(conn, org_id: str, root_id: str) -> list[str]:
    """The initiative and every live sub-initiative below it, breadth first."""
    out, frontier, seen = [root_id], [root_id], {root_id}
    while frontier:
        rows = conn.execute(select(initiative.c.id).select_from(initiative_parent.join(
            initiative, initiative.c.id == initiative_parent.c.initiative_id)).where(and_(
            initiative_parent.c.organization_id == org_id, initiative_parent.c.parent_id.in_(frontier),
            initiative.c.archived_at.is_(None)))).scalars().all()
        frontier = [r for r in rows if r not in seen]
        seen.update(frontier)
        out.extend(frontier)
    return out


def project_ids_for(conn, org_id: str, initiative_ids: list[str]) -> list[str]:
    rows = conn.execute(select(initiative_project.c.project_id).select_from(
        initiative_project.join(project, project.c.id == initiative_project.c.project_id)).where(and_(
            initiative_project.c.organization_id == org_id,
            initiative_project.c.initiative_id.in_(initiative_ids),
            project.c.archived_at.is_(None)))).scalars().all()
    return list(dict.fromkeys(rows))


def latest_project_health(conn, org_id: str, project_ids: list[str]) -> dict[str, str | None]:
    """Each project's latest update health; old posts carried it in ``status``."""
    out: dict[str, str | None] = {pid: None for pid in project_ids}
    if not project_ids:
        return out
    rows = conn.execute(select(project_update.c.project_id, project_update.c.health,
                               project_update.c.status, project_update.c.created_at).where(and_(
        project_update.c.organization_id == org_id,
        project_update.c.project_id.in_(project_ids))).order_by(project_update.c.created_at)).all()
    for pid, health, status, _at in rows:
        out[pid] = _health(health) or _health(status)
    return out


def latest_initiative_update(conn, org_id: str, initiative_id: str) -> dict | None:
    row = conn.execute(select(initiative_update).where(and_(
        initiative_update.c.organization_id == org_id,
        initiative_update.c.initiative_id == initiative_id)).order_by(
        initiative_update.c.created_at.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


POLICY_DEFAULT = {"cadence": "never", "weekday": 4, "hour": 9, "followUps": True}


def reminder_policy(conn, org_id: str) -> dict:
    row = conn.execute(select(initiative_reminder_policy).where(
        initiative_reminder_policy.c.organization_id == org_id)).mappings().first()
    if not row:
        return dict(POLICY_DEFAULT)
    return {"cadence": row["cadence"], "weekday": row["weekday"], "hour": row["hour"],
            "followUps": bool(row["follow_ups"])}


def _zone(name: str | None):
    try:
        return ZoneInfo(name) if name else timezone.utc
    except Exception:  # an unknown zone name falls back to UTC
        return timezone.utc


def _slot_on(local: datetime, weekday: int, hour: int) -> datetime:
    back = (local.weekday() - weekday) % 7
    return (local - timedelta(days=back)).replace(hour=hour, minute=0, second=0, microsecond=0)


def schedule_slot(now: datetime, cadence: str, weekday: int, hour: int, tz, *,
                  upcoming: bool = False) -> datetime | None:
    """The latest reminder slot at or before ``now`` (or the first after it with
    ``upcoming``). Weekly: every ``weekday`` at ``hour``; biweekly: the same on even ISO
    weeks; monthly: the first such weekday of the month. Times are the owner's local."""
    if cadence not in REMINDER_DAYS:
        return None
    local = now.astimezone(tz)
    if cadence == "monthly":
        def first_of(year: int, month: int) -> datetime:
            day = datetime(year, month, 1, hour, tzinfo=tz)
            return day + timedelta(days=(weekday - day.weekday()) % 7)
        cand = first_of(local.year, local.month)
        if upcoming:
            if cand <= local:
                y, m = (local.year + (local.month == 12), local.month % 12 + 1)
                cand = first_of(y, m)
        elif cand > local:
            y, m = (local.year - (local.month == 1), (local.month - 2) % 12 + 1)
            cand = first_of(y, m)
        return cand.astimezone(timezone.utc)
    cand = _slot_on(local, weekday, hour)
    if cand > local:
        cand -= timedelta(days=7)
    if cadence == "biweekly":
        while cand.isocalendar()[1] % 2:
            cand -= timedelta(days=7)
    if upcoming:
        step = timedelta(days=14 if cadence == "biweekly" else 7)
        while cand <= local:
            cand += step
    return cand.astimezone(timezone.utc)


def add_working_days(moment: datetime, days: int, tz) -> datetime:
    local = moment.astimezone(tz)
    while days:
        local += timedelta(days=1)
        if local.weekday() < 5:
            days -= 1
    return local.astimezone(timezone.utc)


def effective_cadence(row: dict, policy: dict) -> str:
    """The initiative's own cadence, or the workspace's when it follows the default."""
    own = row.get("update_reminder")
    return own if own else policy["cadence"]


def owner_zone(conn, org_id: str, owner_id: str | None):
    if not owner_id:
        return timezone.utc
    return _zone(conn.execute(select(user.c.timezone).where(and_(
        user.c.organization_id == org_id, user.c.id == owner_id))).scalar())


def due_at(conn, org_id: str, row: dict, now: datetime | None = None) -> datetime | None:
    """When the owner owes an update: the missed slot if nothing was posted since it,
    otherwise the next slot. None when no reminder applies."""
    now = now or datetime.now(timezone.utc)
    policy = reminder_policy(conn, org_id)
    cadence = effective_cadence(row, policy)
    if cadence not in REMINDER_DAYS:
        return None
    tz = owner_zone(conn, org_id, row.get("owner_id"))
    slot = schedule_slot(now, cadence, policy["weekday"], policy["hour"], tz)
    latest = latest_initiative_update(conn, org_id, row["id"])
    created = _instant(row.get("created_at"))
    posted = _instant((latest or {}).get("created_at"))
    if slot and (not created or created < slot) and not (posted and posted >= slot):
        return slot
    return schedule_slot(now, cadence, policy["weekday"], policy["hour"], tz, upcoming=True)


def bind(initiative_type, update_type, project_type, team_type=None) -> None:
    def _parents(info, obj) -> list[dict]:
        store, _w, org, uid = _ctx(info)
        with store.engine.connect() as conn:
            ids = list(conn.execute(select(initiative_parent.c.parent_id).where(and_(
                initiative_parent.c.organization_id == org,
                initiative_parent.c.initiative_id == obj["id"])).order_by(
                initiative_parent.c.created_at)).scalars())
        found = [store.initiative_by_id(org, pid, viewer_id=uid) for pid in ids]
        return [f for f in found if f]

    @initiative_type.field("parent")
    def i_parent(obj, info):
        parents = _parents(info, obj)
        return parents[0] if parents else None

    @initiative_type.field("parents")
    def i_parents(obj, info):
        return _parents(info, obj)

    @initiative_type.field("children")
    def i_children(obj, info):
        store, _w, org, uid = _ctx(info)
        with store.engine.connect() as conn:
            ids = list(conn.execute(select(initiative.c.id).select_from(initiative_parent.join(
                initiative, initiative.c.id == initiative_parent.c.initiative_id)).where(and_(
                initiative_parent.c.organization_id == org, initiative_parent.c.parent_id == obj["id"],
                initiative.c.archived_at.is_(None))).order_by(
                func.coalesce(initiative.c.sort_order, 1e18), initiative.c.created_at)).scalars())
        found = [store.initiative_by_id(org, cid, viewer_id=uid) for cid in ids]
        return [f for f in found if f]

    @initiative_type.field("slackChannel")
    def i_slack(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            row = conn.execute(select(initiative.c.slack_channel_id, initiative.c.slack_channel_name).where(
                and_(initiative.c.organization_id == org, initiative.c.id == obj["id"]))).first()
        if not row or not row[0]:
            return None
        return {"id": row[0], "name": row[1] or row[0], "isPrivate": row[0].startswith("G")}

    @initiative_type.field("insights")
    def i_insights(obj, info):
        return insights_for(info, obj, _all_project_rows(info, obj))

    @initiative_type.field("leadTeam")
    def i_lead_team(obj, info):
        store, _w, org, uid = _ctx(info)
        tid = obj.get("_lead_team_id")
        return store.team_by_id_or_key(org, tid, viewer_id=uid) if tid else None

    @initiative_type.field("labels")
    def i_labels(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            rows = conn.execute(select(initiative_label).select_from(initiative_label_link.join(
                initiative_label, initiative_label.c.id == initiative_label_link.c.label_id)).where(and_(
                initiative_label_link.c.organization_id == org,
                initiative_label_link.c.initiative_id == obj["id"],
                initiative_label.c.archived_at.is_(None))).order_by(initiative_label.c.name)).mappings().all()
            return _labels_with_groups(conn, org, rows)

    @initiative_type.field("resources")
    def i_resources(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            rows = conn.execute(select(initiative_resource).where(and_(
                initiative_resource.c.organization_id == org,
                initiative_resource.c.initiative_id == obj["id"])).order_by(
                initiative_resource.c.created_at)).mappings().all()
        return [_ser_resource(r) for r in rows]

    @initiative_type.field("health")
    def i_health(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            latest = latest_initiative_update(conn, org, obj["id"])
        return _health((latest or {}).get("health"))

    @initiative_type.field("updateDueAt")
    def i_update_due(obj, info):
        store, _w, org, _uid = _ctx(info)
        if obj.get("status") != "active":
            return None
        row = {"id": obj["id"], "update_reminder": obj.get("updateReminder"),
               "owner_id": obj.get("_owner_id"), "created_at": obj.get("createdAt")}
        with store.engine.connect() as conn:
            due = due_at(conn, org, row)
        return due.isoformat() if due else None

    def _all_project_rows(info, obj):
        store, _w, org, uid = _ctx(info)
        with store.engine.connect() as conn:
            ids = project_ids_for(conn, org, descendant_ids(conn, org, obj["id"]))
        found = store.projects_by_ids(org, ids, viewer_id=uid) if ids else {}
        return [found[i] for i in ids if i in found]

    @initiative_type.field("allProjects")
    def i_all_projects(obj, info):
        return _all_project_rows(info, obj)

    @initiative_type.field("projectHealth")
    def i_project_health(obj, info):
        store, _w, org, _uid = _ctx(info)
        rows = [p for p in _all_project_rows(info, obj)
                if (p.get("state") or "").lower() not in ("completed", "canceled")]
        with store.engine.connect() as conn:
            health = latest_project_health(conn, org, [p["id"] for p in rows])
        counts = {"onTrack": 0, "atRisk": 0, "offTrack": 0, "noUpdate": 0}
        for p in rows:
            key = {"on track": "onTrack", "at risk": "atRisk", "off track": "offTrack"}.get(
                health.get(p["id"]) or "", "noUpdate")
            counts[key] += 1
        return counts

    @initiative_type.field("progressHistory")
    def i_progress_history(obj, info, weeks=12):
        store, _w, org, _uid = _ctx(info)
        weeks = max(2, min(int(weeks or 12), 52))
        projects = _all_project_rows(info, obj)
        if not projects:
            return []
        today = datetime.now(timezone.utc).date()
        first_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
        starts = [first_monday + timedelta(weeks=i) for i in range(weeks)]
        ids = [p["id"] for p in projects]
        with store.engine.connect() as conn:
            rows = conn.execute(select(issue.c.project_id, issue.c.completed_at, workflow_state.c.type).select_from(
                issue.join(workflow_state, workflow_state.c.id == issue.c.state_id)).where(and_(
                issue.c.organization_id == org, issue.c.project_id.in_(ids),
                issue.c.archived_at.is_(None)))).all()
        scope: dict[str, int] = {pid: 0 for pid in ids}
        done_at: dict[str, list[str]] = {pid: [] for pid in ids}
        for pid, completed_at, kind in rows:
            if kind == "canceled":
                continue
            scope[pid] += 1
            if kind == "completed":
                done_at[pid].append((completed_at or "")[:10] or today.isoformat())
        series = []
        for p in projects:
            stamps = sorted(done_at[p["id"]])
            points = []
            for start in starts:
                end = (start + timedelta(days=6)).isoformat()
                points.append({"weekStart": start.isoformat(),
                               "completed": sum(1 for s in stamps if s <= end)})
            series.append({"project": p, "scope": scope[p["id"]], "points": points})
        return series

    @update_type.field("comments")
    def u_comments(obj, info, first=50):
        store, _w, org, _uid = _ctx(info)
        first = max(1, min(int(first or 50), 200))
        with store.engine.connect() as conn:
            rows = conn.execute(select(comment).where(and_(
                comment.c.organization_id == org, comment.c.initiative_update_id == obj["id"],
                comment.c.archived_at.is_(None))).order_by(comment.c.created_at).limit(first)).mappings().all()
        nodes = [store.ser_comment(dict(r)) for r in rows]
        return {"nodes": nodes, "pageInfo": {"hasNextPage": False, "hasPreviousPage": False,
                                              "startCursor": None, "endCursor": None}}

    @update_type.field("reactions")
    def u_reactions(obj, info):
        store, _w, org, uid = _ctx(info)
        return reactions_for(store, org, uid, obj["id"])

    if team_type is not None:
        @team_type.field("showInitiatives")
        def t_show(obj, info):
            store, _w, org, _uid = _ctx(info)
            with store.engine.connect() as conn:
                return bool(conn.execute(select(team.c.show_initiatives).where(and_(
                    team.c.organization_id == org, team.c.id == obj["id"]))).scalar())

    @project_type.field("health")
    def p_health(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            return latest_project_health(conn, org, [obj["id"]]).get(obj["id"])


def insights_for(info, obj, projects: list[dict]) -> dict:
    """Tasks across the given projects by status, assignee, label and project."""
    store, _w, org, _uid = _ctx(info)
    out = {"scope": 0, "completed": 0, "started": 0, "byStatus": [], "byAssignee": [],
           "byLabel": [], "byProject": []}
    ids = [p["id"] for p in projects]
    if not ids:
        return out
    names = {p["id"]: p.get("name") or "Project" for p in projects}
    with store.engine.connect() as conn:
        rows = conn.execute(select(issue.c.id, issue.c.project_id, issue.c.assignee_id,
                                   workflow_state.c.name, workflow_state.c.type,
                                   workflow_state.c.color).select_from(
            issue.join(workflow_state, workflow_state.c.id == issue.c.state_id)).where(and_(
            issue.c.organization_id == org, issue.c.project_id.in_(ids),
            issue.c.archived_at.is_(None)))).all()
        rows = [r for r in rows if r[4] != "canceled"]
        issue_ids = [r[0] for r in rows]
        people = dict(conn.execute(select(user.c.id, func.coalesce(user.c.display_name, user.c.name)).where(
            and_(user.c.organization_id == org, user.c.id.in_({r[2] for r in rows if r[2]})))).all()) if rows else {}
        labels: dict[str, list[tuple]] = {}
        if issue_ids:
            for iid, lid, lname, lcolor in conn.execute(select(
                    issue_label_link.c.issue_id, issue_label.c.id, issue_label.c.name, issue_label.c.color)
                    .select_from(issue_label_link.join(issue_label, issue_label.c.id == issue_label_link.c.label_id))
                    .where(issue_label_link.c.issue_id.in_(issue_ids))):
                labels.setdefault(iid, []).append((lid, lname, lcolor))

    def bucket(table: dict, key: str, label: str, color, done: bool):
        row = table.setdefault(key, {"key": key, "label": label, "color": color, "total": 0, "completed": 0})
        row["total"] += 1
        row["completed"] += int(done)

    status, people_rows, label_rows, project_rows = {}, {}, {}, {}
    for iid, pid, assignee, sname, stype, scolor in rows:
        done = stype == "completed"
        out["scope"] += 1
        out["completed"] += int(done)
        out["started"] += int(stype == "started")
        bucket(status, sname or stype, sname or stype, scolor, done)
        bucket(people_rows, assignee or "none", people.get(assignee, "No assignee") if assignee else "No assignee", None, done)
        for lid, lname, lcolor in labels.get(iid, []) or [("none", "No label", None)]:
            bucket(label_rows, lid, lname, lcolor, done)
        bucket(project_rows, pid, names.get(pid, "Project"), None, done)
    order = lambda table: sorted(table.values(), key=lambda r: (-r["total"], r["label"]))  # noqa: E731
    out.update(byStatus=order(status), byAssignee=order(people_rows), byLabel=order(label_rows),
               byProject=order(project_rows))
    return out


def reactions_for(store, org: str, uid: str | None, update_id: str) -> list[dict]:
    with store.engine.connect() as conn:
        rows = conn.execute(select(initiative_update_reaction.c.emoji, initiative_update_reaction.c.user_id)
                            .where(and_(initiative_update_reaction.c.organization_id == org,
                                        initiative_update_reaction.c.update_id == update_id))
                            .order_by(initiative_update_reaction.c.created_at)).all()
        users = {}
        ids = list({r[1] for r in rows})
        if ids:
            for row in conn.execute(select(user).where(and_(user.c.organization_id == org,
                                                             user.c.id.in_(ids)))).mappings():
                users[row["id"]] = store.ser_user(dict(row))
    grouped: dict[str, list[str]] = {}
    for emoji, user_id in rows:
        grouped.setdefault(emoji, []).append(user_id)
    return [{"emoji": e, "count": len(u), "viewerReacted": uid in u,
             "users": [users[x] for x in u if users.get(x)]} for e, u in grouped.items()]


def with_filter_fields(store, org: str, rows: list[dict]) -> list[dict]:
    """Attach what view filters read: latest health, owner, lead team and label ids."""
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    with store.engine.connect() as conn:
        labels: dict[str, list[str]] = {}
        for iid, lid in conn.execute(select(initiative_label_link.c.initiative_id,
                                            initiative_label_link.c.label_id).where(and_(
                initiative_label_link.c.organization_id == org,
                initiative_label_link.c.initiative_id.in_(ids)))):
            labels.setdefault(iid, []).append(lid)
        health: dict[str, str | None] = {}
        for iid, value, _at in conn.execute(select(initiative_update.c.initiative_id, initiative_update.c.health,
                                                   initiative_update.c.created_at).where(and_(
                initiative_update.c.organization_id == org,
                initiative_update.c.initiative_id.in_(ids))).order_by(initiative_update.c.created_at)):
            health[iid] = _health(value)
    return [{**r, "health": health.get(r["id"]), "ownerId": r.get("_owner_id"),
             "leadTeamId": r.get("_lead_team_id"), "labelIds": labels.get(r["id"], [])} for r in rows]


def _ser_label(row, parent: dict | None = None) -> dict:
    return {"id": row["id"], "name": row["name"], "color": row.get("color"),
            "isGroup": bool(row.get("is_group")), "exclusive": bool(row.get("exclusive", True)),
            "parent": parent}


def _labels_with_groups(conn, org: str, rows) -> list[dict]:
    groups = {}
    ids = {r.get("parent_id") for r in rows if r.get("parent_id")}
    if ids:
        for g in conn.execute(select(initiative_label).where(initiative_label.c.id.in_(ids))).mappings():
            groups[g["id"]] = _ser_label(g)
    return [_ser_label(r, groups.get(r.get("parent_id"))) for r in rows]


def _ser_resource(row) -> dict:
    return {"id": row["id"], "title": row["title"], "url": row["url"],
            "createdAt": row["created_at"], "_creator_id": row.get("creator_id")}


def _require_initiative(conn, writer, org: str, ref: str) -> dict:
    ident = writer.resolve_initiative_id(conn, org, ref)
    row = conn.execute(select(initiative).where(and_(
        initiative.c.organization_id == org, initiative.c.id == ident,
        initiative.c.archived_at.is_(None)))).mappings().first()
    if not row:
        raise GraphQLError("Initiative not found.", extensions={"code": "NOT_FOUND"})
    return dict(row)


# ------------------------------------------------------------------ labels

@query.field("initiativeLabels")
def r_labels(_, info):
    store, _w, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        rows = conn.execute(select(initiative_label).where(and_(
            initiative_label.c.organization_id == org,
            initiative_label.c.archived_at.is_(None))).order_by(initiative_label.c.name)).mappings().all()
        return _labels_with_groups(conn, org, rows)


def _check_label_fields(name, color):
    if name is not None:
        name = name.strip()
        if not name or len(name) > 60:
            raise GraphQLError("A label needs a name of at most 60 characters.",
                               extensions={"code": "BAD_USER_INPUT", "field": "name"})
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise GraphQLError("Label colour must be a hex colour like #5e6ad2.",
                           extensions={"code": "BAD_USER_INPUT", "field": "color"})
    return name


def _group_row(conn, org: str, group_id: str | None):
    if not group_id:
        return None
    row = conn.execute(select(initiative_label).where(and_(
        initiative_label.c.organization_id == org, initiative_label.c.id == group_id,
        initiative_label.c.archived_at.is_(None)))).mappings().first()
    if not row or not row["is_group"]:
        raise GraphQLError("parentId must name a label group.",
                           extensions={"code": "BAD_USER_INPUT", "field": "parentId"})
    return row


@mutation.field("initiativeLabelCreate")
def m_label_create(_, info, name, color=None, parentId=None, isGroup=False, exclusive=True):  # noqa: N803 - GraphQL argument names
    store, _w, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to create labels.", extensions={"code": "UNAUTHENTICATED"})
    name = _check_label_fields(name, color)
    if isGroup and parentId:
        raise GraphQLError("Label groups do not nest.", extensions={"code": "BAD_USER_INPUT", "field": "parentId"})
    with store.engine.begin() as conn:
        group = _group_row(conn, org, parentId)
        existing = conn.execute(select(initiative_label).where(and_(
            initiative_label.c.organization_id == org, initiative_label.c.archived_at.is_(None),
            func.lower(initiative_label.c.name) == name.lower()))).mappings().first()
        if existing:
            return _labels_with_groups(conn, org, [existing])[0]
        row = {"id": new_id(), "organization_id": org, "name": name, "color": color,
               "created_at": now_iso(), "parent_id": group["id"] if group else None,
               "is_group": bool(isGroup), "exclusive": True if exclusive is None else bool(exclusive)}
        conn.execute(initiative_label.insert().values(**row))
    return _ser_label(row, _ser_label(group) if group else None)


@mutation.field("initiativeLabelUpdate")
def m_label_update(_, info, id, name=None, color=None, parentId=None, exclusive=None):  # noqa: N803 - GraphQL argument names
    store, _w, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to edit labels.", extensions={"code": "UNAUTHENTICATED"})
    name = _check_label_fields(name, color)
    with store.engine.begin() as conn:
        row = conn.execute(select(initiative_label).where(and_(
            initiative_label.c.organization_id == org, initiative_label.c.id == id,
            initiative_label.c.archived_at.is_(None)))).mappings().first()
        if not row:
            raise GraphQLError("Label not found.", extensions={"code": "NOT_FOUND"})
        values = {}
        if name is not None:
            values["name"] = name
        if color is not None:
            values["color"] = color or None
        if exclusive is not None and row["is_group"]:
            values["exclusive"] = bool(exclusive)
        if parentId is not None and not row["is_group"]:
            values["parent_id"] = _group_row(conn, org, parentId)["id"] if parentId else None
        if values:
            conn.execute(initiative_label.update().where(initiative_label.c.id == id).values(**values))
        fresh = conn.execute(select(initiative_label).where(initiative_label.c.id == id)).mappings().first()
        return _labels_with_groups(conn, org, [fresh])[0]


@mutation.field("initiativeLabelArchive")
def m_label_archive(_, info, id):
    store, _w, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to edit labels.", extensions={"code": "UNAUTHENTICATED"})
    with store.engine.begin() as conn:
        ts = now_iso()
        done = conn.execute(initiative_label.update().where(and_(
            initiative_label.c.organization_id == org, initiative_label.c.id == id,
            initiative_label.c.archived_at.is_(None))).values(archived_at=ts)).rowcount
        # Archiving a group releases its labels instead of hiding them.
        conn.execute(initiative_label.update().where(and_(
            initiative_label.c.organization_id == org, initiative_label.c.parent_id == id)).values(parent_id=None))
    return {"success": bool(done)}


# ---------------------------------------------------------------- schedule

@query.field("initiativeReminderPolicy")
def r_policy(_, info):
    store, _w, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        return reminder_policy(conn, org)


@mutation.field("initiativeReminderPolicyUpdate")
def m_policy_update(_, info, input):
    store, _w, org, uid = _ctx(info)
    with store.engine.begin() as conn:
        admin = conn.execute(select(user.c.admin).where(and_(
            user.c.organization_id == org, user.c.id == uid))).scalar()
        if not admin:
            raise GraphQLError("Only workspace admins change the update schedule.",
                               extensions={"code": "FORBIDDEN"})
        policy = reminder_policy(conn, org)
        if "cadence" in input and input["cadence"] is not None:
            if input["cadence"] not in ("never", *REMINDER_DAYS):
                raise GraphQLError("cadence is never, weekly, biweekly or monthly.",
                                   extensions={"code": "BAD_USER_INPUT", "field": "cadence"})
            policy["cadence"] = input["cadence"]
        for key, low, high in (("weekday", 0, 6), ("hour", 0, 23)):
            if input.get(key) is not None:
                value = input[key]
                if not isinstance(value, int) or not low <= value <= high:
                    raise GraphQLError(f"{key} must be {low} to {high}.",
                                       extensions={"code": "BAD_USER_INPUT", "field": key})
                policy[key] = value
        if input.get("followUps") is not None:
            policy["followUps"] = bool(input["followUps"])
        values = {"cadence": policy["cadence"], "weekday": policy["weekday"], "hour": policy["hour"],
                  "follow_ups": policy["followUps"], "updated_at": now_iso()}
        exists = conn.execute(select(initiative_reminder_policy.c.organization_id).where(
            initiative_reminder_policy.c.organization_id == org)).first()
        if exists:
            conn.execute(initiative_reminder_policy.update().where(
                initiative_reminder_policy.c.organization_id == org).values(**values))
        else:
            conn.execute(initiative_reminder_policy.insert().values(organization_id=org, **values))
        # A new schedule starts a new reminder cycle for everyone following it.
        conn.execute(initiative.update().where(and_(
            initiative.c.organization_id == org, initiative.c.update_reminder.is_(None))).values(
            reminder_sent_at=None, reminder_followups=None))
    return policy


# ------------------------------------------------------------------- teams

@mutation.field("teamShowInitiatives")
def m_team_show(_, info, teamId, show):  # noqa: N803 - GraphQL argument name
    store, writer, org, uid = _ctx(info)
    with store.engine.begin() as conn:
        writer._require_team_visible(conn, org, uid, teamId)
        done = conn.execute(team.update().where(and_(
            team.c.organization_id == org, team.c.id == teamId)).values(show_initiatives=bool(show))).rowcount
    return store.team_by_id_or_key(org, teamId, viewer_id=uid) if done else None


# ------------------------------------------------------------------- slack

def _slack_token(conn, org: str, uid: str | None) -> str | None:
    """The viewer's own connected Slack token, else the workspace-shared one."""
    from hoja_server.db import connector_account
    from hoja_server.secrets_box import decrypt_secret

    rows = conn.execute(select(connector_account).where(and_(
        connector_account.c.organization_id == org, connector_account.c.provider == "slack",
        connector_account.c.status == "active",
        (connector_account.c.owner_user_id == uid) | connector_account.c.owner_user_id.is_(None)))
        .order_by(connector_account.c.owner_user_id.is_(None))).mappings().all()
    for row in rows:
        try:
            token = decrypt_secret(row["encrypted_refresh_token"], org)
        except Exception:  # an unreadable token is skipped, as the connector service does
            continue
        if token:
            return token
    return None


def _slack_call(token: str, fn):
    import asyncio

    import httpx
    from slack.client import SlackClient

    async def go():
        async with httpx.AsyncClient(timeout=15) as http:
            return await fn(SlackClient(http, token))
    return asyncio.run(go())


@query.field("slackChannels")
def r_slack_channels(_, info):
    store, _w, org, uid = _ctx(info)
    with store.engine.connect() as conn:
        token = _slack_token(conn, org, uid)
    if not token:
        return []
    try:
        channels = _slack_call(token, lambda c: c.list_channels(max_results=500))
    except Exception as exc:
        raise GraphQLError("Slack did not list channels; reconnect Slack in Settings.",
                           extensions={"code": "UPSTREAM"}) from exc
    return [{"id": c.id, "name": c.name, "isPrivate": bool(getattr(c, "is_private", False))}
            for c in channels]


def slack_messages_since(conn, org: str, uid: str | None, channel_id: str,
                         since: str | None) -> list[str]:
    """Recent messages from the initiative's channel, oldest first, for the draft."""
    token = _slack_token(conn, org, uid)
    if not token:
        return []
    oldest = None
    moment = _instant(since)
    if moment:
        oldest = f"{moment.timestamp():.6f}"
    try:
        messages = _slack_call(token, lambda c: c.read_channel_history(
            channel_id, max_results=50, oldest=oldest))
    except Exception:
        log.warning("initiative draft: Slack history failed", exc_info=True)
        return []
    lines = []
    for m in reversed(messages):
        text = (getattr(m, "text", "") or "").strip().replace("\n", " ")
        if text:
            lines.append(text[:300])
    return lines


# --------------------------------------------------------------- resources

@mutation.field("initiativeResourceCreate")
def m_resource_create(_, info, initiativeId, url, title=None):  # noqa: N803 - GraphQL argument name
    store, writer, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to add resources.", extensions={"code": "UNAUTHENTICATED"})
    url = (url or "").strip()
    if not re.match(r"^https?://[^\s]+$", url) or len(url) > 2000:
        raise GraphQLError("A resource needs an http(s) link.",
                           extensions={"code": "BAD_USER_INPUT", "field": "url"})
    title = (title or "").strip()[:200] or re.sub(r"^https?://(www\.)?", "", url)[:200]
    with store.engine.begin() as conn:
        row = _require_initiative(conn, writer, org, initiativeId)
        res = {"id": new_id(), "organization_id": org, "initiative_id": row["id"], "title": title,
               "url": url, "creator_id": uid, "created_at": now_iso()}
        conn.execute(initiative_resource.insert().values(**res))
    return {"success": True, "resource": _ser_resource(res)}


@mutation.field("initiativeResourceDelete")
def m_resource_delete(_, info, id):
    store, _w, org, uid = _ctx(info)
    with store.engine.begin() as conn:
        row = conn.execute(select(initiative_resource).where(and_(
            initiative_resource.c.organization_id == org,
            initiative_resource.c.id == id))).mappings().first()
        if not row:
            return {"success": False}
        admin = conn.execute(select(user.c.admin).where(and_(
            user.c.organization_id == org, user.c.id == uid))).scalar()
        owner = conn.execute(select(initiative.c.creator_id, initiative.c.owner_id).where(
            initiative.c.id == row["initiative_id"])).first()
        if not (admin or uid in (row["creator_id"], *(owner or ()))):
            raise GraphQLError("Only whoever added the resource, the initiative's owner or "
                               "creator, or an admin can remove it.", extensions={"code": "FORBIDDEN"})
        conn.execute(initiative_resource.delete().where(initiative_resource.c.id == id))
    return {"success": True}


# --------------------------------------------------------------- reactions

@mutation.field("initiativeUpdateReact")
def m_react(_, info, updateId, emoji):  # noqa: N803 - GraphQL argument name
    store, _w, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to react.", extensions={"code": "UNAUTHENTICATED"})
    emoji = (emoji or "").strip()
    if not emoji or len(emoji) > EMOJI_MAX or re.search(r"[\s<>]", emoji):
        raise GraphQLError("A reaction is a single emoji.",
                           extensions={"code": "BAD_USER_INPUT", "field": "emoji"})
    with store.engine.begin() as conn:
        exists = conn.execute(select(initiative_update.c.id).where(and_(
            initiative_update.c.organization_id == org, initiative_update.c.id == updateId))).first()
        if not exists:
            return None
        key = and_(initiative_update_reaction.c.organization_id == org,
                   initiative_update_reaction.c.update_id == updateId,
                   initiative_update_reaction.c.user_id == uid,
                   initiative_update_reaction.c.emoji == emoji)
        if conn.execute(select(initiative_update_reaction.c.emoji).where(key)).first():
            conn.execute(initiative_update_reaction.delete().where(key))
        else:
            conn.execute(initiative_update_reaction.insert().values(
                organization_id=org, update_id=updateId, user_id=uid, emoji=emoji, created_at=now_iso()))
    return store.initiative_update_by_id(org, updateId)


# ------------------------------------------------------------------ drafts

def _facts(conn, store, org: str, row: dict, uid: str | None = None) -> tuple[list[str], str]:
    """What changed since the last update, as Markdown lines, and a suggested health."""
    latest = latest_initiative_update(conn, org, row["id"])
    since = (latest or {}).get("created_at") or (
        (datetime.now(timezone.utc) - timedelta(days=14)).isoformat())
    pids = project_ids_for(conn, org, descendant_ids(conn, org, row["id"]))
    names = dict(conn.execute(select(project.c.id, project.c.name).where(project.c.id.in_(pids))).all()) if pids else {}
    progress = store.project_progress_many(org, pids) if pids else {}
    health = latest_project_health(conn, org, pids)
    lines: list[str] = []
    for pid in pids:
        done = conn.execute(select(func.count()).select_from(issue.join(
            workflow_state, workflow_state.c.id == issue.c.state_id)).where(and_(
            issue.c.organization_id == org, issue.c.project_id == pid,
            workflow_state.c.type == "completed", issue.c.completed_at >= since))).scalar() or 0
        bits = [f"{round(progress.get(pid, 0))}% complete"]
        if done:
            bits.append(f"{done} task{'s' if done != 1 else ''} completed since the last update")
        if health.get(pid):
            bits.append(health[pid])
        lines.append(f"- **{names.get(pid, 'Project')}**: " + ", ".join(bits))
    worst = "on track"
    for value in health.values():
        if value == "off track" or (value == "at risk" and worst == "on track"):
            worst = value
    if not pids and latest and _health(latest.get("health")):
        worst = latest["health"]
    if row.get("slack_channel_id"):
        said = slack_messages_since(conn, org, uid, row["slack_channel_id"], since)
        if said:
            name = row.get("slack_channel_name") or row["slack_channel_id"]
            lines.append(f"- **#{name}** on Slack: {len(said)} message{'s' if len(said) != 1 else ''} since the last update")
            lines.extend(f"  > {text}" for text in said[-5:])
    return lines, worst


def _model_draft(store, org: str, row: dict, facts: list[str], health: str) -> str | None:
    """Ask the workspace's configured model to write the update; None when unavailable."""
    from hoja_server.agent_chat import _enabled_provider
    from hoja_server.model_providers import validate_model_base_url
    from hoja_server.secrets_box import decrypt_secret

    provider = _enabled_provider(store, org)
    if not provider:
        return None
    try:
        import openai

        base_url = validate_model_base_url(provider["base_url"]) or None
        client = openai.OpenAI(base_url=base_url, timeout=30, max_retries=0,
                               api_key=decrypt_secret(provider["encrypted_api_key"], org),
                               http_client=openai.DefaultHttpxClient(follow_redirects=False))
        prompt = (
            "Write a short status update for the initiative below, in Markdown, for the "
            "people following it. Lead with how it is going, then the notable progress per "
            "project, then risks and next steps. Quoted lines are recent Slack messages from "
            "the initiative's channel; use them for context. Use only the facts given; do not "
            "invent dates or numbers. No heading.\n\n"
            f"Initiative: {row['name']}\nSummary: {row.get('summary') or '-'}\n"
            f"Status: {row['status']}  Target date: {row.get('target_date') or '-'}\n"
            f"Suggested health: {health}\nProjects:\n" + ("\n".join(facts) or "- none linked"))
        reply = client.chat.completions.create(
            model=provider["model_id"], messages=[{"role": "user", "content": prompt}],
            max_tokens=600)
        text = (reply.choices[0].message.content or "").strip()
        return text or None
    except Exception:  # a failed draft falls back to the facts below
        log.warning("initiative update draft: model call failed", exc_info=True)
        return None


@mutation.field("initiativeUpdateDraft")
def m_draft(_, info, initiativeId):  # noqa: N803 - GraphQL argument name
    store, writer, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to draft an update.", extensions={"code": "UNAUTHENTICATED"})
    with store.engine.connect() as conn:
        row = _require_initiative(conn, writer, org, initiativeId)
        facts, health = _facts(conn, store, org, row, uid)
    text = _model_draft(store, org, row, facts, health)
    if text:
        return {"body": text, "health": health, "source": "model"}
    body = "\n".join([
        "How it is going since the last update:", "",
        *(facts or ["- No projects are linked yet."]), "",
        "**Risks:** ", "", "**Next:** "])
    return {"body": body, "health": health, "source": "changes"}


# --------------------------------------------------------------- reminders

def sweep_reminders(engine, now: str | None = None) -> int:
    """Remind owners of active initiatives on the schedule, as on Linear: at the slot in the
    owner's timezone when nothing was posted since the previous one, then follow-ups one
    working day later and two working days after that. Each send is claimed with a
    conditional update, so concurrent sweeps notify once."""
    from hoja_server import notifications

    ts = now or now_iso()
    current = _instant(ts) or datetime.now(timezone.utc)
    sent = 0
    with engine.begin() as conn:
        policies: dict[str, dict] = {}
        rows = conn.execute(select(initiative).where(and_(
            initiative.c.archived_at.is_(None), initiative.c.status == "active",
            initiative.c.owner_id.is_not(None),
            (initiative.c.update_reminder.is_(None)) | (initiative.c.update_reminder != "never")))).mappings().all()
        for row in rows:
            row = dict(row)
            org = row["organization_id"]
            policy = policies.setdefault(org, reminder_policy(conn, org))
            cadence = effective_cadence(row, policy)
            if cadence not in REMINDER_DAYS:
                continue
            tz = owner_zone(conn, org, row["owner_id"])
            slot = schedule_slot(current, cadence, policy["weekday"], policy["hour"], tz)
            created = _instant(row.get("created_at"))
            if not slot or (created and created >= slot):
                continue
            latest = latest_initiative_update(conn, org, row["id"])
            posted = _instant((latest or {}).get("created_at"))
            if posted and posted >= slot:
                continue
            stamp = slot.isoformat()
            last = _instant(row.get("reminder_sent_at"))
            followups = row.get("reminder_followups") or 0
            if not last or last < slot:
                claimed = conn.execute(initiative.update().where(and_(
                    initiative.c.id == row["id"],
                    (initiative.c.reminder_sent_at.is_(None)) | (initiative.c.reminder_sent_at < stamp)))
                    .values(reminder_sent_at=stamp, reminder_followups=0)).rowcount
                if claimed and notifications.on_initiative_update_due(conn, org, row, ts):
                    sent += 1
                continue
            if not policy["followUps"] or followups >= 2:
                continue
            due = add_working_days(slot, 1 if followups == 0 else 3, tz)
            if current < due:
                continue
            claimed = conn.execute(initiative.update().where(and_(
                initiative.c.id == row["id"], initiative.c.reminder_sent_at == row["reminder_sent_at"],
                func.coalesce(initiative.c.reminder_followups, 0) == followups)).values(
                reminder_followups=followups + 1)).rowcount
            if claimed and notifications.on_initiative_update_due(
                    conn, org, row, ts, follow_up=followups + 1):
                sent += 1
    return sent


async def run_reminders_forever(engine, interval_s: int = 900) -> None:
    import asyncio

    while True:
        try:
            sent = await asyncio.to_thread(sweep_reminders, engine)
            if sent:
                log.info("initiative update reminders sent: %s", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("initiative reminder sweep failed", exc_info=True)
        await asyncio.sleep(interval_s)


__all__ = ["SDL", "bind", "mutation", "query", "run_reminders_forever", "sweep_reminders"]
