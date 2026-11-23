"""View subscriptions: telling a view's followers when a task joins it or finishes in it.

A member follows a task view (``view_subscription``) for one or both events:

* ``on_added`` — a task starts matching the view, because it was created matching or an
  edit made it match.
* ``on_completed`` — a task that matched the view moves to a completed or canceled state.

Matching is decided by the same engine that runs ``View.issues``: the saved filter, compiled
for the subscriber (so "me" is the subscriber, not whoever made the change) AND the view's
scope AND the subscriber's team visibility. The completed-issues window is a display choice
and is ignored here.

Every helper runs on the writer's connection, inside the transaction that changes the task,
so a notification commits or rolls back with its cause. The cost is bounded by the
organization's subscriptions: a workspace where nobody follows a view pays one indexed query
per write, and the "before" snapshot is taken only when there is something to compare.
"""
from __future__ import annotations

from sqlalchemy import and_, select

from hoja_server import notifications
from hoja_server import views as viewdocs
from hoja_server.db import issue, saved_view, view_subscription, workflow_state

ADDED = "viewIssueAdded"
COMPLETED = "viewIssueCompleted"
_CLOSED = ("completed", "canceled")


def _subscriptions(conn, org_id: str) -> list[dict]:
    """Live subscriptions on unarchived task views in the organization."""
    stmt = select(view_subscription.c.view_id, view_subscription.c.user_id,
                  view_subscription.c.on_added, view_subscription.c.on_completed,
                  saved_view.c.name, saved_view.c.slug_id, saved_view.c.filters,
                  saved_view.c.display, saved_view.c.scope, saved_view.c.team_id,
                  saved_view.c.project_id, saved_view.c.initiative_id,
                  saved_view.c.visibility, saved_view.c.owner_id).select_from(
        view_subscription.join(saved_view, saved_view.c.id == view_subscription.c.view_id)
    ).where(and_(view_subscription.c.organization_id == org_id,
                 saved_view.c.organization_id == org_id,
                 saved_view.c.kind == "issue",
                 saved_view.c.archived_at.is_(None)))
    return [dict(r._mapping) for r in conn.execute(stmt)]


class _Matcher:
    """Answers "does the task match this follower's view?" with as few queries as possible.

    A view's filter is compiled and run once per view, not once per follower, unless it is
    personal ("me", "my teams"), in which case once per (view, follower). Team visibility of
    the task and of a team-shared view is looked up once per follower. So a write costs
    about (views followed + people following) small queries, however many subscriptions
    there are.
    """

    def __init__(self, conn, org_id: str, issue_id: str) -> None:
        self.conn, self.org_id, self.issue_id = conn, org_id, issue_id
        row = conn.execute(select(issue.c.team_id, issue.c.archived_at).where(and_(
            issue.c.organization_id == org_id, issue.c.id == issue_id))).first()
        self.exists = row is not None and row.archived_at is None
        self.team_id = row.team_id if row is not None else None
        self._compiled: dict[str, tuple[dict, bool] | None] = {}
        self._results: dict[tuple[str, str | None], bool] = {}
        self._team_visible: dict[tuple[str, str | None], bool] = {}

    def team_visible(self, user_id: str, team_id: str | None) -> bool:
        from hoja_server.store import team_visible

        if not team_id:
            return True
        key = (user_id, team_id)
        if key not in self._team_visible:
            self._team_visible[key] = team_visible(self.conn, self.org_id, user_id, team_id)
        return self._team_visible[key]

    def view_visible(self, sub: dict) -> bool:
        """Mirror of store._scope_views for one follower: private is the owner's, team is
        that team's members', workspace is everyone's."""
        if sub["visibility"] == "private":
            return sub["owner_id"] == sub["user_id"]
        if sub["visibility"] == "team":
            return self.team_visible(sub["user_id"], sub["team_id"])
        return True

    def _compile(self, sub: dict) -> tuple[dict, bool] | None:
        """(impersonal filter, whether the filter depends on who runs it); None when the
        stored document no longer parses."""
        from hoja_server.store import Store

        view_id = sub["view_id"]
        if view_id not in self._compiled:
            try:
                saved = viewdocs.load(sub["filters"])
                scope = Store.view_scope_filter_on(
                    self.conn, self.org_id, "issue", sub["scope"], sub["team_id"],
                    sub["project_id"], sub["initiative_id"])
                anyone = viewdocs.compile_filter("issue", saved, viewer_id=None)
                # A filter is personal when compiling it for someone changes it.
                personal = viewdocs.compile_filter("issue", saved,
                                                   viewer_id="__probe__") != anyone
                self._compiled[view_id] = (viewdocs.conjoin(anyone, scope), personal)
            except viewdocs.ViewError:
                self._compiled[view_id] = None  # a document an older grammar accepted
        return self._compiled[view_id]

    def matches(self, sub: dict) -> bool:
        from hoja_server.store import Store, _apply_issue_filter

        if not self.exists or not self.team_visible(sub["user_id"], self.team_id):
            return False
        compiled = self._compile(sub)
        if compiled is None:
            return False
        flt, personal = compiled
        key = (sub["view_id"], sub["user_id"] if personal else None)
        if key not in self._results:
            if personal:
                saved = viewdocs.load(sub["filters"])
                flt = viewdocs.conjoin(
                    viewdocs.compile_filter("issue", saved, viewer_id=sub["user_id"]),
                    Store.view_scope_filter_on(
                        self.conn, self.org_id, "issue", sub["scope"], sub["team_id"],
                        sub["project_id"], sub["initiative_id"]))
            stmt = select(issue.c.id).where(and_(
                issue.c.organization_id == self.org_id, issue.c.id == self.issue_id))
            if viewdocs.load(sub["display"]).get("showSubIssues") is False:
                stmt = stmt.where(issue.c.parent_id.is_(None))
            stmt = _apply_issue_filter(self.conn, stmt, self.org_id,
                                       sub["user_id"] if personal else None, flt)
            self._results[key] = self.conn.execute(stmt).first() is not None
        return self._results[key]


def _state_type(conn, org_id: str, issue_id: str) -> str | None:
    return conn.execute(select(workflow_state.c.type).select_from(
        issue.join(workflow_state, workflow_state.c.id == issue.c.state_id)).where(and_(
            issue.c.organization_id == org_id, issue.c.id == issue_id))).scalar()


class Snapshot:
    """Which subscribed views a task matched before a change, and its state type then."""

    def __init__(self, subs: list[dict], matched: set[tuple[str, str]],
                 state_type: str | None) -> None:
        self.subs = subs
        self.matched = matched
        self.state_type = state_type


def snapshot(conn, org_id: str, issue_id: str | None) -> Snapshot | None:
    """Take before a change; None when nobody in the organization follows a view."""
    subs = _subscriptions(conn, org_id)
    if not subs:
        return None
    if issue_id is None:  # a task being created matched nothing before
        return Snapshot(subs, set(), None)
    matcher = _Matcher(conn, org_id, issue_id)
    matched = {(s["view_id"], s["user_id"]) for s in subs if matcher.matches(s)}
    return Snapshot(subs, matched, _state_type(conn, org_id, issue_id))


def after_change(conn, org_id: str, issue_id: str, actor_id: str | None, ts: str,
                 before: Snapshot | None) -> set[str]:
    """Notify the followers of every view the task joined or finished in; returns who heard.

    The actor never hears about their own change, and one task produces at most one row
    per (view, follower, event).
    """
    if before is None:
        return set()
    state_type = _state_type(conn, org_id, issue_id)
    finished = state_type in _CLOSED and before.state_type not in _CLOSED
    matcher = _Matcher(conn, org_id, issue_id)
    notified: set[str] = set()
    for sub in before.subs:
        if sub["user_id"] == actor_id or not matcher.view_visible(sub):
            continue
        key = (sub["view_id"], sub["user_id"])
        was = key in before.matched
        if sub["on_completed"] and finished and was:
            kind = COMPLETED
        elif sub["on_added"] and not was and matcher.matches(sub):
            kind = ADDED
        else:
            continue
        notified |= notifications.notify(
            conn, org_id, issue_id, actor_id, [sub["user_id"]], kind, ts,
            data={"view": {"id": sub["view_id"], "name": sub["name"], "slugId": sub["slug_id"]},
                  **({"stateType": state_type} if kind == COMPLETED else {})})
    return notified


def has_subscribers(conn, org_id: str, view_id: str) -> bool:
    return conn.execute(select(view_subscription.c.id).where(and_(
        view_subscription.c.organization_id == org_id,
        view_subscription.c.view_id == view_id)).limit(1)).first() is not None
