"""ABAC policy for issue comments (see ``abac`` for the evaluation model).

Words carry their author's name, so only the author may change them. Admins moderate by
deleting, never by rewriting: an admin edit would put words in someone else's mouth.

subject
    user_id        the caller's user id
    admin          caller is an active workspace admin
resource
    author_id      the comment's author (None for system/imported comments)
    archived       the comment was deleted
    team_visible   the caller can see the issue's team (private teams)

Matrix (after the visibility/archived denials):

    caller             edit    delete
    author             yes     yes
    workspace admin    no      yes
    other member       no      no
"""
from __future__ import annotations

from sqlalchemy import and_, select

from hoja_server.abac import Decision, Policy, deny, permit
from hoja_server.db import user
from hoja_server.store import team_visible

EDIT = "comment.edit"
DELETE = "comment.delete"

# Denials that must look exactly like "no such comment" so existence never leaks.
NOT_FOUND_RULES = frozenset({"d-hidden", "d-archived"})


def _is_author(c) -> bool:
    return bool(c.r.author_id) and c.r.author_id == c.s.user_id


RULES = [
    deny("d-hidden", {EDIT, DELETE}, lambda c: not c.r.team_visible, "Comment not found."),
    deny("d-archived", {EDIT, DELETE}, lambda c: bool(c.r.archived), "Comment not found."),
    permit("p-author", {EDIT, DELETE}, _is_author, "Authors manage their own comments."),
    permit("p-admin-moderate", DELETE, lambda c: bool(c.s.admin), "Workspace admins can remove any comment."),
]

POLICY = Policy(RULES)

DENIED_MESSAGE = {
    EDIT: "Only the comment's author can edit it.",
    DELETE: "Only the comment's author or a workspace admin can delete it.",
}


def decide(action: str, subject: dict, resource: dict) -> Decision:
    return POLICY.decide(action, subject, resource)


def subject(conn, org_id: str, viewer_id: str | None) -> dict:
    admin = conn.execute(select(user.c.admin).where(and_(
        user.c.id == viewer_id, user.c.organization_id == org_id,
        user.c.active.is_(True), user.c.archived_at.is_(None),
    ))).scalar() if viewer_id else None
    return {"user_id": viewer_id, "admin": bool(admin)}


def resource(conn, org_id: str, viewer_id: str | None, *, author_id: str | None,
             archived: bool, team_id: str | None) -> dict:
    visible = bool(viewer_id) and (not team_id or team_visible(conn, org_id, viewer_id, team_id))
    return {"author_id": author_id, "archived": archived, "team_visible": visible}
