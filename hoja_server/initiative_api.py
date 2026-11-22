"""Initiative structure, insight and update conversation (revision 88).

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

from ariadne import MutationType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, func, select

from hoja_server.db import (
    comment,
    initiative,
    initiative_label,
    initiative_label_link,
    initiative_project,
    initiative_resource,
    initiative_update,
    initiative_update_reaction,
    issue,
    new_id,
    now_iso,
    project,
    project_update,
    user,
    workflow_state,
)

log = logging.getLogger(__name__)

SDL = gql(
    """
    type InitiativeLabel { id: ID! name: String! color: String }
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
      parent: Initiative
      children: [Initiative!]!
      leadTeam: Team
      labels: [InitiativeLabel!]!
      resources: [InitiativeResource!]!
      sortOrder: Float
      "never, weekly, biweekly or monthly"
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
    extend type Query {
      initiativeLabels: [InitiativeLabel!]!
    }
    extend type Mutation {
      initiativeLabelCreate(name: String!, color: String): InitiativeLabel!
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
        rows = conn.execute(select(initiative.c.id).where(and_(
            initiative.c.organization_id == org_id, initiative.c.parent_id.in_(frontier),
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


def update_due_at(row: dict, latest: dict | None) -> datetime | None:
    days = REMINDER_DAYS.get(row.get("update_reminder") or "")
    if not days:
        return None
    since = _instant((latest or {}).get("created_at")) or _instant(row.get("created_at"))
    return since + timedelta(days=days) if since else None


def bind(initiative_type, update_type, project_type) -> None:
    @initiative_type.field("parent")
    def i_parent(obj, info):
        store, _w, org, _uid = _ctx(info)
        pid = obj.get("_parent_id")
        return store.initiative_by_id(org, pid) if pid else None

    @initiative_type.field("children")
    def i_children(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            rows = conn.execute(select(initiative).where(and_(
                initiative.c.organization_id == org, initiative.c.parent_id == obj["id"],
                initiative.c.archived_at.is_(None))).order_by(
                func.coalesce(initiative.c.sort_order, 1e18), initiative.c.created_at)).mappings().all()
            return [store.ser_initiative(conn, dict(r)) for r in rows]

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
        return [_ser_label(r) for r in rows]

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
        if not obj.get("updateReminder") or obj.get("status") != "active":
            return None
        with store.engine.connect() as conn:
            latest = latest_initiative_update(conn, org, obj["id"])
        due = update_due_at({"update_reminder": obj.get("updateReminder"),
                             "created_at": obj.get("createdAt")}, latest)
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

    @project_type.field("health")
    def p_health(obj, info):
        store, _w, org, _uid = _ctx(info)
        with store.engine.connect() as conn:
            return latest_project_health(conn, org, [obj["id"]]).get(obj["id"])


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


def _ser_label(row) -> dict:
    return {"id": row["id"], "name": row["name"], "color": row.get("color")}


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
    return [_ser_label(r) for r in rows]


@mutation.field("initiativeLabelCreate")
def m_label_create(_, info, name, color=None):
    store, _w, org, uid = _ctx(info)
    if not uid:
        raise GraphQLError("Sign in to create labels.", extensions={"code": "UNAUTHENTICATED"})
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise GraphQLError("A label needs a name of at most 60 characters.",
                           extensions={"code": "BAD_USER_INPUT", "field": "name"})
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise GraphQLError("Label colour must be a hex colour like #5e6ad2.",
                           extensions={"code": "BAD_USER_INPUT", "field": "color"})
    with store.engine.begin() as conn:
        existing = conn.execute(select(initiative_label).where(and_(
            initiative_label.c.organization_id == org, initiative_label.c.archived_at.is_(None),
            func.lower(initiative_label.c.name) == name.lower()))).mappings().first()
        if existing:
            return _ser_label(existing)
        row = {"id": new_id(), "organization_id": org, "name": name, "color": color,
               "created_at": now_iso()}
        conn.execute(initiative_label.insert().values(**row))
    return _ser_label(row)


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

def _facts(conn, store, org: str, row: dict) -> tuple[list[str], str]:
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
            "project, then risks and next steps. Use only the facts given; do not invent "
            "dates or numbers. No heading.\n\n"
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
        facts, health = _facts(conn, store, org, row)
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
    """Remind owners of active initiatives whose update is overdue. Each overdue period
    is claimed with a conditional stamp, so concurrent sweeps notify once."""
    from hoja_server import notifications

    ts = now or now_iso()
    current = _instant(ts) or datetime.now(timezone.utc)
    sent = 0
    with engine.begin() as conn:
        rows = conn.execute(select(initiative).where(and_(
            initiative.c.archived_at.is_(None), initiative.c.status == "active",
            initiative.c.owner_id.is_not(None),
            initiative.c.update_reminder.in_(tuple(REMINDER_DAYS))))).mappings().all()
        for row in rows:
            row = dict(row)
            latest = latest_initiative_update(conn, row["organization_id"], row["id"])
            due = update_due_at(row, latest)
            if not due or due > current:
                continue
            last = _instant(row.get("reminder_sent_at"))
            if last and last >= due:
                continue
            claimed = conn.execute(initiative.update().where(and_(
                initiative.c.id == row["id"],
                (initiative.c.reminder_sent_at.is_(None)) | (initiative.c.reminder_sent_at < due.isoformat())))
                .values(reminder_sent_at=ts)).rowcount
            if claimed and notifications.on_initiative_update_due(conn, row["organization_id"], row, ts):
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
