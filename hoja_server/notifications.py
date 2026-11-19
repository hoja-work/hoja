"""Inbox fan-out: who hears about a change, and what the row says.

A notification is about one subject: a task, a project, an initiative or a cycle.
Each subject has its own subscriber table; the rules below are for tasks, and the
project, initiative and cycle rules follow the same shape further down.

Recipients are the issue's subscribers plus its assignee, minus whoever caused the
change. People become subscribers by creating an issue, being assigned to it,
commenting on it or being @mentioned in a comment, and they leave by
unsubscribing. Direct kinds (an assignment, a mention, a decision addressed to
them) reach their recipient whether or not they still subscribe.

Every helper runs on the caller's connection, so a notification commits or rolls
back with the change that caused it.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import and_, or_, select

from hoja_server.db import (
    comment,
    cycle_subscriber,
    inbox_notification,
    initiative_subscriber,
    issue_subscriber,
    new_id,
    project_subscriber,
    user,
    workflow_state,
)

# Kinds a person can turn off in Settings -> Notifications. ``issueCreated`` only
# exists on rows written before assignment had its own kind.
KINDS = ("issueAssigned", "issueMention", "issueComment", "issueStatusChanged",
         "issueUpdated", "issueDecision", "issueCreated",
         "projectUpdatePosted", "projectUpdateReply", "projectMention", "projectLeadAssigned",
         "projectUpdated", "initiativeUpdatePosted", "initiativeOwnerAssigned",
         "initiativeUpdated", "cycleStarted", "cycleCompleted")

# subject type -> (subscriber table, its subject column, the inbox column)
SUBJECTS = {
    "issue": (issue_subscriber, "issue_id", "issue_id"),
    "project": (project_subscriber, "project_id", "project_id"),
    "initiative": (initiative_subscriber, "initiative_id", "initiative_id"),
    "cycle": (cycle_subscriber, "cycle_id", "cycle_id"),
}
SUBJECT_COLUMNS = ("issue_id", "project_id", "initiative_id", "cycle_id")

_MENTION = re.compile(r"(?<![A-Za-z0-9._%+-])@([A-Za-z0-9._-]+)")
SNIPPET_LENGTH = 200


def _eligible(org_id: str):
    """Active human profiles in this workspace: the only people an inbox serves."""
    return and_(user.c.organization_id == org_id,
                or_(user.c.active.is_(None), user.c.active.is_(True)),
                user.c.archived_at.is_(None),
                or_(user.c.kind.is_(None), user.c.kind == "human"))


def subject_subscribers(conn, subject_type: str, subject_id: str) -> set[str]:
    table, column, _ = SUBJECTS[subject_type]
    return set(conn.execute(select(table.c.user_id).where(
        table.c[column] == subject_id)).scalars())


def subscribe_subject(conn, org_id: str, subject_type: str, subject_id: str, user_ids) -> None:
    """Add each eligible user to the subject's subscribers (idempotent)."""
    wanted = {u for u in user_ids if u}
    if not wanted:
        return
    table, column, _ = SUBJECTS[subject_type]
    eligible = set(conn.execute(select(user.c.id).where(and_(
        user.c.id.in_(wanted), _eligible(org_id)))).scalars())
    for uid in eligible - subject_subscribers(conn, subject_type, subject_id):
        conn.execute(table.insert().values(**{column: subject_id, "user_id": uid}))


def unsubscribe_subject(conn, subject_type: str, subject_id: str, user_id: str) -> None:
    table, column, _ = SUBJECTS[subject_type]
    conn.execute(table.delete().where(and_(table.c[column] == subject_id,
                                           table.c.user_id == user_id)))


def subscribers(conn, issue_id: str) -> set[str]:
    return subject_subscribers(conn, "issue", issue_id)


def subscribe(conn, org_id: str, issue_id: str, user_ids) -> None:
    """Add each eligible user to the issue's subscribers (idempotent)."""
    subscribe_subject(conn, org_id, "issue", issue_id, user_ids)


def notify_subject(conn, org_id: str, subject_type: str, subject_id: str,
                   actor_id: str | None, recipients, kind: str, ts: str,
                   data: dict | None = None, **refs) -> set[str]:
    """Write one row per eligible recipient other than the actor; returns who got one.

    ``refs`` carries the row's pointers: comment_id, project_update_id,
    initiative_update_id.
    """
    wanted = {r for r in recipients if r} - {actor_id}
    if not wanted:
        return set()
    eligible = set(conn.execute(select(user.c.id).where(and_(
        user.c.id.in_(wanted), _eligible(org_id)))).scalars())
    payload = json.dumps(data, separators=(",", ":")) if data is not None else None
    subject_column = SUBJECTS[subject_type][2]
    for recipient_id in sorted(eligible):
        conn.execute(inbox_notification.insert().values(
            id=new_id(), organization_id=org_id, recipient_id=recipient_id,
            actor_id=actor_id, kind=kind, created_at=ts, data=payload,
            **{subject_column: subject_id}, **{k: v for k, v in refs.items() if v},
        ))
    return eligible


def notify(conn, org_id: str, issue_id: str, actor_id: str | None, recipients, kind: str,
           ts: str, data: dict | None = None, comment_id: str | None = None) -> set[str]:
    """Task notifications: :func:`notify_subject` for an issue."""
    return notify_subject(conn, org_id, "issue", issue_id, actor_id, recipients, kind, ts,
                          data=data, comment_id=comment_id)


def audience(conn, issue_row: dict) -> set[str]:
    """Everyone following the issue: its subscribers and its assignee."""
    return subscribers(conn, issue_row["id"]) | {issue_row.get("assignee_id")} - {None}


def mentioned_user_ids(conn, org_id: str, body: str | None) -> set[str]:
    """Users named by ``@token`` in a body: display name, name without spaces, or email local part."""
    tokens = {t.lower().rstrip(".") for t in _MENTION.findall(body or "")}
    tokens.discard("")
    if not tokens:
        return set()
    rows = conn.execute(select(user.c.id, user.c.display_name, user.c.name, user.c.email)
                        .where(_eligible(org_id))).all()
    found = set()
    for uid, display_name, name, email in rows:
        keys = {(display_name or "").lower(),
                re.sub(r"\s+", "", name or "").lower(),
                (email or "").split("@", 1)[0].lower()}
        if tokens & (keys - {""}):
            found.add(uid)
    return found


def snippet(body: str | None, limit: int = SNIPPET_LENGTH) -> str:
    """A one-line preview: markdown images, links, emphasis and code fences flattened."""
    text = body or ""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def state_ref(conn, org_id: str, state_id: str | None) -> dict | None:
    if not state_id:
        return None
    row = conn.execute(select(workflow_state.c.id, workflow_state.c.name, workflow_state.c.type)
                       .where(and_(workflow_state.c.organization_id == org_id,
                                   workflow_state.c.id == state_id))).first()
    if not row:
        return {"id": state_id, "name": None, "type": None}
    return {"id": row.id, "name": row.name, "type": row.type}


def on_issue_created(conn, org_id: str, issue_id: str, actor_id: str | None,
                     assignee_id: str | None, ts: str) -> None:
    subscribe(conn, org_id, issue_id, [actor_id, assignee_id])
    if assignee_id:
        notify(conn, org_id, issue_id, actor_id, [assignee_id], "issueAssigned", ts, data={})


def on_issue_updated(conn, org_id: str, before: dict, after: dict, actor_id: str | None,
                     ts: str) -> None:
    """Fan out one update. ``before``/``after`` are issue rows (column names).

    The new assignee hears "assigned you" and nothing else. Everyone else following
    the issue hears about a state move, or failing that a priority or due-date
    change; any other edit is silent.
    """
    issue_id = before["id"]
    old_assignee, new_assignee = before.get("assignee_id"), after.get("assignee_id")
    assigned = new_assignee if new_assignee and new_assignee != old_assignee else None
    if assigned:
        subscribe(conn, org_id, issue_id, [assigned])
    followers = audience(conn, {"id": issue_id, "assignee_id": new_assignee})
    if old_assignee and old_assignee != new_assignee:
        # The person taken off the task still learns it moved.
        followers.add(old_assignee)
    if assigned:
        notify(conn, org_id, issue_id, actor_id, [assigned], "issueAssigned", ts, data={})
        followers.discard(assigned)
    if before.get("state_id") != after.get("state_id") and after.get("state_id"):
        notify(conn, org_id, issue_id, actor_id, followers, "issueStatusChanged", ts, data={
            "from": state_ref(conn, org_id, before.get("state_id")),
            "to": state_ref(conn, org_id, after.get("state_id"))})
        return
    changes = {}
    for field, column in (("priority", "priority"), ("dueDate", "due_date")):
        if column in after and before.get(column) != after.get(column):
            changes[field] = {"from": before.get(column), "to": after.get(column)}
    if changes:
        notify(conn, org_id, issue_id, actor_id, followers, "issueUpdated", ts,
               data={"changes": changes})


def on_comment(conn, org_id: str, issue_row: dict, actor_id: str | None, comment_id: str,
               body: str | None, ts: str) -> None:
    issue_id = issue_row["id"]
    mentioned = mentioned_user_ids(conn, org_id, body) - {actor_id}
    subscribe(conn, org_id, issue_id, [actor_id, *mentioned])
    data = {"commentId": comment_id, "snippet": snippet(body)}
    notify(conn, org_id, issue_id, actor_id, mentioned, "issueMention", ts,
           data=data, comment_id=comment_id)
    notify(conn, org_id, issue_id, actor_id, audience(conn, issue_row) - mentioned,
           "issueComment", ts, data=data, comment_id=comment_id)


def on_decision(conn, org_id: str, issue_row: dict, actor_id: str | None, decision_id: str,
                question: str, ts: str) -> None:
    recipients = audience(conn, issue_row) | {issue_row.get("creator_id")} - {None}
    notify(conn, org_id, issue_row["id"], actor_id, recipients, "issueDecision", ts,
           data={"decisionId": decision_id, "question": snippet(question)})


# ------------------------------------------------------------------ projects

def _user_ref(conn, org_id: str, user_id: str | None) -> dict | None:
    if not user_id:
        return None
    row = conn.execute(select(user.c.id, user.c.name, user.c.display_name).where(and_(
        user.c.organization_id == org_id, user.c.id == user_id))).first()
    return {"id": user_id, "name": (row.name or row.display_name) if row else None}


def _date_changes(before: dict, after: dict, fields) -> dict:
    changes = {}
    for field, column in fields:
        if column in after and before.get(column) != after.get(column):
            changes[field] = {"from": before.get(column), "to": after.get(column)}
    return changes


def on_project_created(conn, org_id: str, project_id: str, actor_id: str | None,
                       lead_id: str | None, member_ids, ts: str) -> None:
    subscribe_subject(conn, org_id, "project", project_id, [actor_id, lead_id, *member_ids])
    if lead_id:
        notify_subject(conn, org_id, "project", project_id, actor_id, [lead_id],
                       "projectLeadAssigned", ts, data={})


def on_project_updated(conn, org_id: str, before: dict, after: dict, actor_id: str | None,
                       ts: str) -> None:
    """A state, target date or lead change reaches the project's subscribers; the new
    lead hears "made you lead" instead. Every other edit is silent."""
    pid = before["id"]
    old_lead, new_lead = before.get("lead_id"), after.get("lead_id", before.get("lead_id"))
    lead = new_lead if new_lead and new_lead != old_lead else None
    if lead:
        subscribe_subject(conn, org_id, "project", pid, [lead])
        notify_subject(conn, org_id, "project", pid, actor_id, [lead], "projectLeadAssigned",
                       ts, data={})
    changes = _date_changes(before, after, (("state", "state"), ("targetDate", "target_date")))
    if "lead_id" in after and old_lead != new_lead:
        changes["lead"] = {"from": _user_ref(conn, org_id, old_lead),
                           "to": _user_ref(conn, org_id, new_lead)}
    if changes:
        followers = subject_subscribers(conn, "project", pid) - {lead}
        notify_subject(conn, org_id, "project", pid, actor_id, followers, "projectUpdated", ts,
                       data={"changes": changes})


def on_project_update_posted(conn, org_id: str, project_id: str, update_id: str,
                             actor_id: str | None, body: str | None, health: str | None,
                             ts: str) -> None:
    mentioned = mentioned_user_ids(conn, org_id, body) - {actor_id}
    subscribe_subject(conn, org_id, "project", project_id, [actor_id, *mentioned])
    text = snippet(body)
    notify_subject(conn, org_id, "project", project_id, actor_id, mentioned, "projectMention",
                   ts, data={"updateId": update_id, "snippet": text},
                   project_update_id=update_id)
    notify_subject(conn, org_id, "project", project_id, actor_id,
                   subject_subscribers(conn, "project", project_id) - mentioned,
                   "projectUpdatePosted", ts,
                   data={"updateId": update_id, "snippet": text, "health": health},
                   project_update_id=update_id)


def on_project_update_reply(conn, org_id: str, update_row: dict, comment_id: str,
                            actor_id: str | None, body: str | None, ts: str) -> None:
    """A reply reaches the update's author and everyone who replied before; anyone
    @mentioned hears "mentioned you" instead."""
    project_id, update_id = update_row["project_id"], update_row["id"]
    mentioned = mentioned_user_ids(conn, org_id, body) - {actor_id}
    subscribe_subject(conn, org_id, "project", project_id, [actor_id, *mentioned])
    earlier = set(conn.execute(select(comment.c.user_id).where(and_(
        comment.c.organization_id == org_id, comment.c.project_update_id == update_id,
        comment.c.id != comment_id, comment.c.archived_at.is_(None)))).scalars())
    participants = ({update_row.get("actor_id")} | earlier) - {None}
    data = {"updateId": update_id, "commentId": comment_id, "snippet": snippet(body)}
    refs = {"project_update_id": update_id, "comment_id": comment_id}
    notify_subject(conn, org_id, "project", project_id, actor_id, mentioned, "projectMention",
                   ts, data=data, **refs)
    notify_subject(conn, org_id, "project", project_id, actor_id, participants - mentioned,
                   "projectUpdateReply", ts, data=data, **refs)


# --------------------------------------------------------------- initiatives

def on_initiative_created(conn, org_id: str, initiative_id: str, actor_id: str | None,
                          owner_id: str | None, ts: str) -> None:
    subscribe_subject(conn, org_id, "initiative", initiative_id, [actor_id, owner_id])
    if owner_id:
        notify_subject(conn, org_id, "initiative", initiative_id, actor_id, [owner_id],
                       "initiativeOwnerAssigned", ts, data={})


def on_initiative_updated(conn, org_id: str, before: dict, after: dict,
                          actor_id: str | None, ts: str) -> None:
    iid = before["id"]
    old_owner, new_owner = before.get("owner_id"), after.get("owner_id", before.get("owner_id"))
    owner = new_owner if new_owner and new_owner != old_owner else None
    if owner:
        subscribe_subject(conn, org_id, "initiative", iid, [owner])
        notify_subject(conn, org_id, "initiative", iid, actor_id, [owner],
                       "initiativeOwnerAssigned", ts, data={})
    changes = _date_changes(before, after, (("status", "status"), ("targetDate", "target_date")))
    if "owner_id" in after and old_owner != new_owner:
        changes["owner"] = {"from": _user_ref(conn, org_id, old_owner),
                            "to": _user_ref(conn, org_id, new_owner)}
    if changes:
        followers = subject_subscribers(conn, "initiative", iid) - {owner}
        notify_subject(conn, org_id, "initiative", iid, actor_id, followers,
                       "initiativeUpdated", ts, data={"changes": changes})


def on_initiative_update_posted(conn, org_id: str, initiative_id: str, update_id: str,
                                actor_id: str | None, body: str | None, health: str | None,
                                ts: str) -> None:
    """Subscribers hear about the post. Anyone @mentioned in it is subscribed and hears
    about this post too (initiatives have no separate mention kind)."""
    mentioned = mentioned_user_ids(conn, org_id, body) - {actor_id}
    subscribe_subject(conn, org_id, "initiative", initiative_id, [actor_id, *mentioned])
    notify_subject(conn, org_id, "initiative", initiative_id, actor_id,
                   subject_subscribers(conn, "initiative", initiative_id),
                   "initiativeUpdatePosted", ts,
                   data={"updateId": update_id, "snippet": snippet(body), "health": health},
                   initiative_update_id=update_id)


# -------------------------------------------------------------------- cycles

def on_cycle_started(conn, org_id: str, cycle_row: dict, team_key: str | None, ts: str) -> None:
    cid = cycle_row["id"]
    notify_subject(conn, org_id, "cycle", cid, None, subject_subscribers(conn, "cycle", cid),
                   "cycleStarted", ts, data={
                       "name": cycle_row.get("name"), "number": cycle_row.get("number"),
                       "teamKey": team_key, "startsAt": cycle_row.get("starts_at"),
                       "endsAt": cycle_row.get("ends_at")})


def on_cycle_completed(conn, org_id: str, cycle_row: dict, team_key: str | None,
                       completed: int, total: int, carried_over: int, ts: str) -> None:
    cid = cycle_row["id"]
    notify_subject(conn, org_id, "cycle", cid, None, subject_subscribers(conn, "cycle", cid),
                   "cycleCompleted", ts, data={
                       "name": cycle_row.get("name"), "number": cycle_row.get("number"),
                       "teamKey": team_key, "completed": completed, "total": total,
                       "carriedOver": carried_over})


def subject_of(row: dict) -> tuple[str, str] | tuple[None, None]:
    """(subjectType, subjectId) of an inbox row: the first subject column that is set."""
    for column in SUBJECT_COLUMNS:
        if row.get(column):
            return column.removesuffix("_id"), row[column]
    return None, None


def unread_conditions(now: str):
    """Rows that are in the inbox right now: unarchived and not snoozed into the future."""
    return and_(inbox_notification.c.archived_at.is_(None),
                or_(inbox_notification.c.snoozed_until.is_(None),
                    inbox_notification.c.snoozed_until <= now))


__all__ = ["KINDS", "SUBJECTS", "SUBJECT_COLUMNS", "audience", "mentioned_user_ids", "notify",
           "notify_subject", "on_comment", "on_cycle_completed", "on_cycle_started",
           "on_decision", "on_initiative_created", "on_initiative_update_posted",
           "on_initiative_updated", "on_issue_created", "on_issue_updated",
           "on_project_created", "on_project_update_posted", "on_project_update_reply",
           "on_project_updated", "snippet", "state_ref", "subject_of", "subject_subscribers",
           "subscribe", "subscribe_subject", "subscribers", "unread_conditions",
           "unsubscribe_subject"]
