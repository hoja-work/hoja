"""Inbox fan-out: who hears about a change to an issue, and what the row says.

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
    inbox_notification,
    issue_subscriber,
    new_id,
    user,
    workflow_state,
)

# Kinds a person can turn off in Settings -> Notifications. ``issueCreated`` only
# exists on rows written before assignment had its own kind.
KINDS = ("issueAssigned", "issueMention", "issueComment", "issueStatusChanged",
         "issueUpdated", "issueDecision", "issueCreated")

_MENTION = re.compile(r"(?<![A-Za-z0-9._%+-])@([A-Za-z0-9._-]+)")
SNIPPET_LENGTH = 200


def _eligible(org_id: str):
    """Active human profiles in this workspace: the only people an inbox serves."""
    return and_(user.c.organization_id == org_id,
                or_(user.c.active.is_(None), user.c.active.is_(True)),
                user.c.archived_at.is_(None),
                or_(user.c.kind.is_(None), user.c.kind == "human"))


def subscribers(conn, issue_id: str) -> set[str]:
    return set(conn.execute(select(issue_subscriber.c.user_id).where(
        issue_subscriber.c.issue_id == issue_id)).scalars())


def subscribe(conn, org_id: str, issue_id: str, user_ids) -> None:
    """Add each eligible user to the issue's subscribers (idempotent)."""
    wanted = {u for u in user_ids if u}
    if not wanted:
        return
    eligible = set(conn.execute(select(user.c.id).where(and_(
        user.c.id.in_(wanted), _eligible(org_id)))).scalars())
    for uid in eligible - subscribers(conn, issue_id):
        conn.execute(issue_subscriber.insert().values(issue_id=issue_id, user_id=uid))


def notify(conn, org_id: str, issue_id: str, actor_id: str | None, recipients, kind: str,
           ts: str, data: dict | None = None, comment_id: str | None = None) -> set[str]:
    """Write one row per eligible recipient other than the actor; returns who got one."""
    wanted = {r for r in recipients if r} - {actor_id}
    if not wanted:
        return set()
    eligible = set(conn.execute(select(user.c.id).where(and_(
        user.c.id.in_(wanted), _eligible(org_id)))).scalars())
    payload = json.dumps(data, separators=(",", ":")) if data is not None else None
    for recipient_id in sorted(eligible):
        conn.execute(inbox_notification.insert().values(
            id=new_id(), organization_id=org_id, recipient_id=recipient_id,
            issue_id=issue_id, actor_id=actor_id, kind=kind, created_at=ts,
            data=payload, comment_id=comment_id,
        ))
    return eligible


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


def unread_conditions(now: str):
    """Rows that are in the inbox right now: unarchived and not snoozed into the future."""
    return and_(inbox_notification.c.archived_at.is_(None),
                or_(inbox_notification.c.snoozed_until.is_(None),
                    inbox_notification.c.snoozed_until <= now))


__all__ = ["KINDS", "audience", "mentioned_user_ids", "notify", "on_comment", "on_decision",
           "on_issue_created", "on_issue_updated", "snippet", "state_ref", "subscribe",
           "subscribers", "unread_conditions"]
