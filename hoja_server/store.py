"""Store — org-scoped data access + serialization to the upstream API camelCase shapes.

Every method takes an org_id and NEVER returns rows from other orgs → tenant
isolation by construction. Serializers emit exactly the camelCase field names
hoja's Pydantic models expect.
"""
from __future__ import annotations

import hashlib
import json
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from graphql import GraphQLError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Engine

from hoja_server import addresses
from hoja_server import decisions as decisiondocs
from hoja_server import views as viewdocs


class InviteRefused(Exception):
    """An invitation the workspace will not issue. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


from hoja_server.db import (
    api_key,
    attachment,
    github_link,
    change_event,
    browser_session,
    comment,
    cycle,
    issue,
    issue_activity,
    issue_decision,
    inbox_notification,
    initiative,
    initiative_project,
    organization,
    global_identity,
    organization_membership,
    identity_credential,
    membership_invitation,
    new_id,
    issue_label,
    issue_label_link,
    issue_subscriber,
    project,
    project_update,
    project_member,
    project_team,
    saved_view,
    saved_view_favorite,
    team,
    team_member,
    token_hash,
    password_credential,
    user,
    user_preference,
    workflow_state,
)


def change_event_dict(r, *, with_org: bool = False) -> dict:
    """change_event row → ChangeEvent; recipientId only for per-user events (notifications)."""
    out = {"seq": r["seq"], "entity": r["entity"], "entityId": r["entity_id"],
           "action": r["action"], "teamId": r["team_id"]}
    if r.get("recipient_id") is not None:
        out["recipientId"] = r["recipient_id"]
    if with_org:
        out["organization_id"] = r["organization_id"]
    return out


def _conn_rows(conn, stmt) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(stmt)]


def _one(conn, stmt) -> dict | None:
    r = conn.execute(stmt).first()
    return dict(r._mapping) if r else None


def _normalize_expiry(value: str | None):
    """Return a UTC ISO timestamp, None for no expiry, or False when invalid or already past."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GraphQLError("expiresAt must be an ISO 8601 datetime string.",
                           extensions={"code": "BAD_USER_INPUT", "field": "expiresAt"})
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat() if parsed > datetime.now(timezone.utc) else False


_MAX_SEARCH_TERM = 256
_PBKDF2_ITERATIONS = 600_000
_LEGACY_PBKDF2_ITERATIONS = 310_000


def _check_search_term(value) -> str:
    if not isinstance(value, str):
        raise GraphQLError("Search term must be a string.", extensions={"code": "BAD_USER_INPUT"})
    if len(value) > _MAX_SEARCH_TERM:
        raise GraphQLError(f"Search term must be at most {_MAX_SEARCH_TERM} characters.",
                           extensions={"code": "BAD_USER_INPUT"})
    return value


def _like_contains(value: str) -> str:
    """Escape LIKE wildcards so user text matches literally (pair with escape="\\")."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


PUBLIC, RESTRICTED, PRIVATE = "public", "restricted", "private"
TEAM_ACCESS_LEVELS = (PUBLIC, RESTRICTED, PRIVATE)
TEAM_ROLES = ("owner", "member")


def _member_team_ids(viewer_id: str):
    """Subquery of the team ids this viewer holds a team_member row on."""
    return select(team_member.c.team_id).where(team_member.c.user_id == viewer_id)


def visibility_holds(team_cols, viewer_id: str):
    """SQL predicate over one team row: may ``viewer_id`` see it?

    The single definition of the visibility rule (decision 2 of the brief):
      public     — any member of the organization;
      restricted — explicit members, plus members of the parent team (they may also join);
      private    — explicit members only.

    Departments are not an access boundary, so a restricted team looks at its direct
    parent only; the membership cascade keeps an explicit row on every ancestor, which
    is what makes a grandparent member count here too. Everything else (reads, boards,
    search, attachments, realtime) is expressed through this predicate.
    """
    members = _member_team_ids(viewer_id)
    return or_(
        team_cols["visibility"] == PUBLIC,
        team_cols["id"].in_(members),
        and_(team_cols["visibility"] == RESTRICTED, team_cols["parent_team_id"].in_(members)),
    )


def visible_team_select(org_id: str, viewer_id: str, *, alias: str = "visible_team"):
    """Subquery of the team ids in ``org_id`` that ``viewer_id`` may see."""
    vt = team.alias(alias)
    cols = {"id": vt.c.id, "visibility": vt.c.visibility, "parent_team_id": vt.c.parent_team_id}
    return select(vt.c.id).where(and_(vt.c.organization_id == org_id, visibility_holds(cols, viewer_id)))


def _viewer_is_admin(conn, org_id: str, viewer_id: str) -> bool:
    return bool(conn.execute(select(user.c.admin).where(and_(
        user.c.id == viewer_id, user.c.organization_id == org_id))).scalar())


def visible_team_clause(conn, org_id: str, viewer_id: str, team_id_col):
    """Restrict team_id_col to teams the viewer may see. None means unrestricted (admin)."""
    if _viewer_is_admin(conn, org_id, viewer_id):
        return None
    return team_id_col.in_(visible_team_select(org_id, viewer_id))


def team_ancestor_ids(conn, org_id: str, team_id: str) -> list[str]:
    """The team followed by its parents, root last (nesting is capped at three levels)."""
    chain, current = [], team_id
    while current and current not in chain:
        parent = conn.execute(select(team.c.parent_team_id).where(and_(
            team.c.id == current, team.c.organization_id == org_id))).scalar()
        chain.append(current)
        current = parent
    return chain


def team_descendant_ids(conn, org_id: str, team_id: str) -> list[str]:
    """The team followed by every team beneath it, breadth first."""
    seen, queue = [team_id], [team_id]
    while queue:
        children = list(conn.execute(select(team.c.id).where(and_(
            team.c.organization_id == org_id, team.c.parent_team_id == queue.pop(0)))).scalars())
        for child in children:
            if child not in seen:
                seen.append(child)
                queue.append(child)
    return seen


def team_visible(conn, org_id: str, viewer_id: str, team_id: str) -> bool:
    stmt = select(team.c.id).where(and_(team.c.id == team_id, team.c.organization_id == org_id))
    clause = visible_team_clause(conn, org_id, viewer_id, team.c.id)
    if clause is not None:
        stmt = stmt.where(clause)
    return conn.execute(stmt).first() is not None


def _scope_teams(conn, stmt, org_id, viewer_id, team_id_col, *, nullable=False):
    """Apply team visibility when a viewer is given. viewer_id=None is an internal, unscoped call."""
    if viewer_id is None:
        return stmt
    clause = visible_team_clause(conn, org_id, viewer_id, team_id_col)
    if clause is None:
        return stmt
    return stmt.where(or_(team_id_col.is_(None), clause) if nullable else clause)

def _scope_projects(conn, stmt, org_id, viewer_id):
    """Hide projects whose every team link is invisible to the viewer.

    A project with no team links keeps the historical behaviour and stays org-wide:
    it has no team to be private about. A project linked only to teams the viewer
    cannot see disappears, mirroring the upstream contract's rule that a project becomes private
    once its last public team is removed.
    """
    if viewer_id is None:
        return stmt
    clause = visible_team_clause(conn, org_id, viewer_id, team.c.id)
    if clause is None:
        return stmt
    visible_ids = (
        select(project_team.c.project_id)
        .select_from(project_team.join(team, team.c.id == project_team.c.team_id))
        .where(clause)
    )
    any_linked = select(project_team.c.project_id)
    return stmt.where(or_(~project.c.id.in_(any_linked), project.c.id.in_(visible_ids)))


def _scope_views(conn, stmt, org_id, viewer_id):
    """Apply view visibility: private → owner only, team → that team's members, else workspace.

    A private row is filtered here rather than after the query, so a copied id cannot leak
    the view's name or filters to a non-owner (docs/views.md#sharing-and-visibility).
    """
    if viewer_id is None:
        return stmt
    team_clause = visible_team_clause(conn, org_id, viewer_id, saved_view.c.team_id)
    shared = saved_view.c.visibility == "workspace"
    private = and_(saved_view.c.visibility == "private",
                   saved_view.c.owner_id == viewer_id)
    team_shared = saved_view.c.visibility == "team"
    if team_clause is not None:
        team_shared = and_(team_shared, team_clause)
    return stmt.where(or_(shared, private, team_shared))


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ----------------------------------------------------------------- views
    def ser_view(self, row: dict, *, favorite: bool = False) -> dict:
        """saved_view row → View. Documents are decoded here so callers never parse JSON."""
        return {
            "id": row["id"], "name": row["name"], "description": row.get("description"),
            "icon": row.get("icon"), "color": row.get("color"), "slugId": row.get("slug_id"),
            "kind": row.get("kind"), "scope": row.get("scope"),
            "visibility": row.get("visibility"), "shared": row.get("visibility") != "private",
            "filters": viewdocs.load(row.get("filters")),
            "display": viewdocs.load(row.get("display")),
            "favorite": favorite,
            "archivedAt": row.get("archived_at"),
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
            "_owner_id": row.get("owner_id"), "_creator_id": row.get("creator_id"),
            "_updated_by_id": row.get("updated_by_id"), "_team_id": row.get("team_id"),
            "_project_id": row.get("project_id"), "_initiative_id": row.get("initiative_id"),
        }

    @staticmethod
    def _favorite_ids(conn, org_id: str, user_id: str | None) -> set[str]:
        if not user_id:
            return set()
        return set(conn.execute(select(saved_view_favorite.c.view_id).where(and_(
            saved_view_favorite.c.organization_id == org_id,
            saved_view_favorite.c.user_id == user_id))).scalars())

    def views(self, org_id: str, viewer_id: str, *, kind: str | None = None,
              scope: str | None = None, include_archived: bool = False,
              limit: int = 200) -> list[dict]:
        """Views the viewer may read; private rows are the owner's alone."""
        with self.engine.connect() as conn:
            stmt = select(saved_view).where(saved_view.c.organization_id == org_id)
            if not include_archived:
                stmt = stmt.where(saved_view.c.archived_at.is_(None))
            stmt = _scope_views(conn, stmt, org_id, viewer_id)
            if kind:
                stmt = stmt.where(saved_view.c.kind == kind)
            if scope:
                stmt = stmt.where(saved_view.c.scope == scope)
            stmt = stmt.order_by(saved_view.c.created_at, saved_view.c.id).limit(limit)
            favorites = self._favorite_ids(conn, org_id, viewer_id)
            return [self.ser_view(row, favorite=row["id"] in favorites)
                    for row in _conn_rows(conn, stmt)]

    def view_by_id(self, org_id: str, viewer_id: str, ident: str,
                   *, include_archived: bool = True) -> dict | None:
        """Resolve by id or slug; visibility applies, so a copied link grants nothing."""
        with self.engine.connect() as conn:
            stmt = select(saved_view).where(and_(
                saved_view.c.organization_id == org_id,
                or_(saved_view.c.id == ident, saved_view.c.slug_id == ident)))
            if not include_archived:
                stmt = stmt.where(saved_view.c.archived_at.is_(None))
            stmt = _scope_views(conn, stmt, org_id, viewer_id)
            row = _one(conn, stmt)
            if not row:
                return None
            favorites = self._favorite_ids(conn, org_id, viewer_id)
            return self.ser_view(row, favorite=row["id"] in favorites)

    # ------------------------------------------------------------- decisions
    def ser_decision(self, row: dict) -> dict:
        """issue_decision row → IssueDecision; options decode here, never in callers."""
        return {
            "id": row["id"], "question": row["question"],
            "options": decisiondocs.load_options(row.get("options")),
            "context": row.get("context"), "status": row.get("status"),
            "answered": row.get("status") == decisiondocs.ANSWERED,
            "answer": row.get("answer"), "chosenOption": row.get("chosen_option"),
            "askedByAgent": row.get("asked_by_agent"),
            "answeredAt": row.get("answered_at"), "canceledAt": row.get("canceled_at"),
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
            "_issue_id": row.get("issue_id"), "_asked_by_id": row.get("asked_by_id"),
            "_answered_by_id": row.get("answered_by_id"),
        }

    def decisions_for_issue(self, org_id: str, viewer_id: str, issue_ref: str,
                            *, status: str | None = None,
                            limit: int = 50) -> list[dict]:
        """Decisions on one issue, newest first. The issue must be readable by the viewer."""
        with self.engine.connect() as conn:
            issue_row = self._issue_ref_row(conn, org_id, viewer_id, issue_ref)
            if not issue_row:
                return []
            stmt = select(issue_decision).where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.issue_id == issue_row["id"]))
            if status:
                stmt = stmt.where(issue_decision.c.status == status)
            stmt = stmt.order_by(issue_decision.c.created_at.desc(),
                                 issue_decision.c.id.desc()).limit(limit)
            return [self.ser_decision(row) for row in _conn_rows(conn, stmt)]

    def decision_by_id(self, org_id: str, viewer_id: str, decision_id: str) -> dict | None:
        """One decision the viewer may read; another org's id is indistinguishable from missing."""
        with self.engine.connect() as conn:
            row = _one(conn, select(issue_decision).where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.id == decision_id)))
            if not row:
                return None
            issue_row = self._issue_ref_row(conn, org_id, viewer_id, row["issue_id"])
            if not issue_row:
                return None
            return self.ser_decision(row)

    def _issue_ref_row(self, conn, org_id: str, viewer_id: str, issue_ref: str) -> dict | None:
        """Resolve an issue by identifier or id, honouring team visibility."""
        stmt = select(issue.c.id, issue.c.identifier, issue.c.team_id).where(and_(
            issue.c.organization_id == org_id,
            or_(issue.c.id == issue_ref, issue.c.identifier == issue_ref)))
        clause = visible_team_clause(conn, org_id, viewer_id, issue.c.team_id)
        if clause is not None:
            stmt = stmt.where(clause)
        row = conn.execute(stmt).mappings().first()
        return dict(row) if row else None

    def project_visible(self, org_id: str, viewer_id: str | None, project_id: str) -> bool:
        """Whether a project is visible to the viewer, using the same rule as project lists."""
        with self.engine.connect() as conn:
            stmt = select(project.c.id).where(and_(
                project.c.organization_id == org_id, project.c.id == project_id,
                project.c.archived_at.is_(None)))
            stmt = _scope_projects(conn, stmt, org_id, viewer_id)
            return conn.execute(stmt).first() is not None

    def is_initialized(self) -> bool:
        """Whether this database already contains any workspace."""
        with self.engine.connect() as conn:
            return conn.execute(select(organization.c.id).limit(1)).first() is not None

    def user_preferences(self, user_id: str) -> dict[str, str] | None:
        """Read preference values for a verified user profile."""
        with self.engine.connect() as conn:
            if not conn.execute(select(user.c.id).where(
                    user.c.id == user_id, user.c.active.is_(True),
                    user.c.archived_at.is_(None))).first():
                return None
            rows = conn.execute(select(user_preference.c.preference_key,
                                       user_preference.c.value).where(
                user_preference.c.user_id == user_id)).all()
            return {row.preference_key: row.value for row in rows}

    def set_user_preference(self, user_id: str, preference_key: str, value: str) -> bool:
        allowed = {"theme": {"light", "dark"}, "fontSize": {"90", "100", "110"},
                   "notification:issueCreated": {"true", "false"},
                   "notification:issueUpdated": {"true", "false"}}
        if preference_key not in allowed or value not in allowed[preference_key]:
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            if not conn.execute(select(user.c.id).where(
                    user.c.id == user_id, user.c.active.is_(True),
                    user.c.archived_at.is_(None))).first():
                return False
            exists = conn.execute(select(user_preference.c.user_id).where(and_(
                user_preference.c.user_id == user_id,
                user_preference.c.preference_key == preference_key))).first()
            if exists:
                conn.execute(user_preference.update().where(and_(
                    user_preference.c.user_id == user_id,
                    user_preference.c.preference_key == preference_key)).values(
                        value=value, updated_at=now))
            else:
                conn.execute(user_preference.insert().values(
                    user_id=user_id, preference_key=preference_key,
                    value=value, updated_at=now))
        return True

    # ------------------------------------------------------------------ auth
    def change_events_after(self, org_id: str, after_seq: int, limit: int = 200,
                            viewer_id: str | None = None) -> list[dict]:
        """Events for teams the viewer can see. Without a viewer only team-less events are returned."""
        with self.engine.connect() as conn:
            stmt = select(change_event).where(and_(
                change_event.c.organization_id == org_id, change_event.c.seq > after_seq))
            if viewer_id is None:
                stmt = stmt.where(and_(change_event.c.team_id.is_(None),
                                       change_event.c.recipient_id.is_(None)))
            else:
                stmt = _scope_teams(conn, stmt, org_id, viewer_id, change_event.c.team_id, nullable=True)
                # Per-user events (notifications) reach only their recipient.
                stmt = stmt.where(or_(change_event.c.recipient_id.is_(None),
                                      change_event.c.recipient_id == viewer_id))
            rows = conn.execute(stmt.order_by(change_event.c.seq).limit(limit)).mappings().all()
        return [change_event_dict(r) for r in rows]

    def change_org_ids_after(self, after_seq: int, limit: int = 1000) -> list[dict]:
        """Cheap global change scan for the realtime broadcaster: seq + org only."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(change_event.c.seq, change_event.c.organization_id)
                .where(change_event.c.seq > after_seq)
                .order_by(change_event.c.seq)
                .limit(limit)
            ).all()
        return [{"seq": r[0], "organization_id": r[1]} for r in rows]

    def latest_change_seq(self, org_id: str) -> int:
        """Newest change_event seq for an org (0 when none)."""
        with self.engine.connect() as conn:
            return int(conn.execute(
                select(func.max(change_event.c.seq))
                .where(change_event.c.organization_id == org_id)
            ).scalar() or 0)

    def change_events_after_global(self, after_seq: int, limit: int = 5000) -> list[dict]:
        """All orgs' raw change events after seq (broadcaster feed)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(change_event)
                .where(change_event.c.seq > after_seq)
                .order_by(change_event.c.seq)
                .limit(limit)
            ).mappings().all()
        return [change_event_dict(r, with_org=True) for r in rows]

    def visible_team_ids(self, org_id: str, viewer_id: str) -> frozenset | None:
        """Viewer-visible team ids, or None when unrestricted (admin)."""
        with self.engine.connect() as conn:
            if _viewer_is_admin(conn, org_id, viewer_id):
                return None
            rows = conn.execute(visible_team_select(org_id, viewer_id)).all()
            return frozenset(r[0] for r in rows)

    def max_change_seq(self) -> int:
        """Newest change_event seq across every org (0 when none)."""
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.max(change_event.c.seq))).scalar() or 0)

    def change_events_after_orgs(self, heads: dict[str, int], limit: int = 5000) -> list[dict]:
        """Raw events newer than a per-org cursor (gap-free when seqs are commit-ordered per org)."""
        out: list[dict] = []
        items = list(heads.items())
        with self.engine.connect() as conn:
            for start in range(0, len(items), 200):
                chunk = items[start:start + 200]
                clause = or_(*[and_(change_event.c.organization_id == org,
                                    change_event.c.seq > seq) for org, seq in chunk])
                rows = conn.execute(select(change_event).where(clause)
                                    .order_by(change_event.c.seq).limit(limit)).mappings().all()
                out.extend(change_event_dict(r, with_org=True) for r in rows)
        out.sort(key=lambda e: e["seq"])
        return out

    def visibility_map(self, org_id: str, user_ids) -> dict[str, frozenset | None]:
        """visible_team_ids for many viewers with three queries instead of one per viewer.

        Mirrors ``visibility_holds`` exactly: admins see everything (None); everyone
        else sees public teams, the teams they hold a membership row on, and restricted
        teams whose parent they belong to.
        """
        uids = list(dict.fromkeys(user_ids))
        if not uids:
            return {}
        with self.engine.connect() as conn:
            admins: set[str] = set()
            known: set[str] = set()
            for start in range(0, len(uids), 500):
                chunk = uids[start:start + 500]
                for uid, admin in conn.execute(select(user.c.id, user.c.admin).where(and_(
                        user.c.organization_id == org_id, user.c.id.in_(chunk)))):
                    known.add(uid)
                    if admin:
                        admins.add(uid)
            public = frozenset(r[0] for r in conn.execute(select(team.c.id).where(and_(
                team.c.organization_id == org_id, team.c.visibility == PUBLIC))))
            restricted_parent: dict[str, str] = {
                tid: parent for tid, parent in conn.execute(select(team.c.id, team.c.parent_team_id).where(and_(
                    team.c.organization_id == org_id, team.c.visibility == RESTRICTED,
                    team.c.parent_team_id.is_not(None))))
            }
            member_of: dict[str, set[str]] = {}
            for start in range(0, len(uids), 500):
                chunk = uids[start:start + 500]
                for uid, tid in conn.execute(
                        select(team_member.c.user_id, team_member.c.team_id)
                        .join(team, team.c.id == team_member.c.team_id)
                        .where(and_(team.c.organization_id == org_id,
                                    team_member.c.user_id.in_(chunk)))):
                    member_of.setdefault(uid, set()).add(tid)
            visible_restricted: dict[str, set[str]] = {}
            for tid, parent in restricted_parent.items():
                for uid in uids:
                    if parent in member_of.get(uid, ()):
                        visible_restricted.setdefault(uid, set()).add(tid)
        out: dict[str, frozenset | None] = {}
        for uid in uids:
            if uid in admins:
                out[uid] = None
            elif uid not in known:
                out[uid] = frozenset()
            else:
                out[uid] = (public | frozenset(member_of.get(uid, ()))
                            | frozenset(visible_restricted.get(uid, ())))
        return out

    def validate_credentials(self, key_hashes, session_hashes) -> dict[tuple[str, str], tuple[str, str]]:
        """Still-valid credentials → (user_id, organization_id), in a couple of batched queries.

        Used to revalidate every open realtime socket at once instead of one auth
        lookup (and api-key touch write) per socket per interval.
        """
        now = datetime.now(timezone.utc).isoformat()
        out: dict[tuple[str, str], tuple[str, str]] = {}
        keys, sessions = list(set(key_hashes)), list(set(session_hashes))
        with self.engine.connect() as conn:
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                for th, uid, org in conn.execute(
                        select(api_key.c.token_hash, api_key.c.user_id, api_key.c.organization_id)
                        .join(user, and_(user.c.id == api_key.c.user_id,
                                         user.c.organization_id == api_key.c.organization_id))
                        .where(and_(api_key.c.token_hash.in_(chunk), api_key.c.revoked_at.is_(None),
                                    or_(api_key.c.expires_at.is_(None), api_key.c.expires_at > now),
                                    user.c.active.is_(True), user.c.archived_at.is_(None)))):
                    out[("key", th)] = (uid, org)
            for start in range(0, len(sessions), 500):
                chunk = sessions[start:start + 500]
                for th, uid, org in conn.execute(
                        select(browser_session.c.token_hash, browser_session.c.user_id,
                               browser_session.c.organization_id)
                        .join(user, and_(user.c.id == browser_session.c.user_id,
                                         user.c.organization_id == browser_session.c.organization_id))
                        .join(organization_membership, and_(
                            organization_membership.c.profile_user_id == browser_session.c.user_id,
                            organization_membership.c.organization_id == browser_session.c.organization_id,
                            organization_membership.c.active.is_(True)))
                        .where(and_(browser_session.c.token_hash.in_(chunk),
                                    browser_session.c.revoked_at.is_(None),
                                    browser_session.c.expires_at > now,
                                    user.c.active.is_(True), user.c.archived_at.is_(None)))):
                    out[("session", th)] = (uid, org)
        return out

    def resolve_token(self, token: str) -> dict | None:
        """token → {user_id, organization_id} or None."""
        th = token_hash(token)
        with self.engine.connect() as conn:
            row = _one(
                conn,
                select(api_key)
                .join(
                    user,
                    and_(
                        user.c.id == api_key.c.user_id,
                        user.c.organization_id == api_key.c.organization_id,
                    ),
                )
                .where(
                    and_(
                        api_key.c.token_hash == th,
                        api_key.c.revoked_at.is_(None),
                        or_(api_key.c.expires_at.is_(None),
                            api_key.c.expires_at > datetime.now(timezone.utc).isoformat()),
                        user.c.active.is_(True),
                        user.c.archived_at.is_(None),
                    )
                ),
            )
            return row

    def touch_api_key(self, key_id: str, last_used_at: str | None = None) -> None:
        """Record use, at most once per hour, to avoid a write on every request."""
        now = datetime.now(timezone.utc)
        threshold = (now - timedelta(hours=1)).isoformat()
        if last_used_at is not None and last_used_at >= threshold:
            return  # recent enough: skip the write transaction (a global lock on SQLite)
        with self.engine.begin() as conn:
            conn.execute(api_key.update().where(and_(
                api_key.c.id == key_id,
                or_(api_key.c.last_used_at.is_(None), api_key.c.last_used_at < threshold),
            )).values(last_used_at=now.isoformat()))

    def list_api_keys(self, org_id: str, user_id: str) -> list[dict]:
        """Return metadata for this user's keys in the active workspace only."""
        with self.engine.connect() as conn:
            return _conn_rows(conn, select(
                api_key.c.id, api_key.c.label, api_key.c.access, api_key.c.hint,
                api_key.c.expires_at.label("expiresAt"),
                api_key.c.last_used_at.label("lastUsedAt"),
                api_key.c.created_at.label("createdAt"),
                api_key.c.revoked_at.label("revokedAt"),
            ).where(and_(api_key.c.organization_id == org_id,
                         api_key.c.user_id == user_id)).order_by(api_key.c.created_at))

    def create_api_key(self, org_id: str, user_id: str, label: str, access: str,
                       expires_at: str | None = None) -> tuple[dict, str] | None:
        from hoja_server.db import gen_token
        clean_label = label.strip()
        if not clean_label or len(clean_label) > 80:
            return None
        if access not in ("read", "read_write"):
            return None
        expires_at = _normalize_expiry(expires_at)
        if expires_at is False:
            return None
        raw_token = gen_token()
        key_id = new_id()
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            if not conn.execute(select(user.c.id).where(and_(
                    user.c.id == user_id, user.c.organization_id == org_id,
                    user.c.active.is_(True), user.c.archived_at.is_(None)))).first():
                return None
            conn.execute(api_key.insert().values(
                id=key_id, token_hash=token_hash(raw_token), label=clean_label,
                hint=raw_token[-4:], access=access, expires_at=expires_at,
                user_id=user_id, organization_id=org_id, created_at=now))
        return {"id": key_id, "label": clean_label, "access": access, "hint": raw_token[-4:],
                "expiresAt": expires_at, "lastUsedAt": None,
                "createdAt": now, "revokedAt": None}, raw_token

    def revoke_api_key(self, org_id: str, user_id: str, key_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(api_key.update().where(and_(
                api_key.c.id == key_id, api_key.c.organization_id == org_id,
                api_key.c.user_id == user_id, api_key.c.revoked_at.is_(None),
            )).values(revoked_at=datetime.now(timezone.utc).isoformat()))
            return bool(result.rowcount)

    def create_browser_session_for_token(self, token: str, days: int = 14) -> tuple[str, dict] | None:
        """Create a browser session for the owner of a valid API key."""
        identity = self.resolve_token(token)
        if not identity:
            return None
        raw = secrets.token_urlsafe(48)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=days)
        with self.engine.begin() as conn:
            conn.execute(browser_session.insert().values(
                id=new_id(), token_hash=token_hash(raw), user_id=identity["user_id"],
                organization_id=identity["organization_id"], created_at=now.isoformat(),
                expires_at=expires.isoformat()))
        return raw, {"user_id": identity["user_id"],
                     "organization_id": identity["organization_id"],
                     "expires_at": expires.isoformat()}

    @staticmethod
    def _password_digest(password: str, salt: bytes) -> str:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
        return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, stored) -> bool:
        """Verify iteration-tagged hashes and legacy 3-part hashes (implicit 310k iterations)."""
        parts = stored.split("$") if isinstance(stored, str) else []
        try:
            if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
                iterations, salt_hex, digest_hex = int(parts[1]), parts[2], parts[3]
            elif len(parts) == 3 and parts[0] == "pbkdf2_sha256":
                iterations, salt_hex, digest_hex = _LEGACY_PBKDF2_ITERATIONS, parts[1], parts[2]
            else:
                return False
            if not 1 <= iterations <= 10_000_000:
                return False
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), iterations)
            return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _reject_unverified(password: str) -> None:
        """Burn one PBKDF2 round when no credential exists, so unknown accounts take as long as wrong passwords."""
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"\x00" * 16, _PBKDF2_ITERATIONS)

    def set_password(self, api_token: str, password: str) -> bool:
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters.")
        identity = self.resolve_token(api_token)
        if not identity:
            return False
        salt = secrets.token_bytes(16)
        encoded = self._password_digest(password, salt)
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            member = conn.execute(select(organization_membership.c.identity_id).where(
                organization_membership.c.profile_user_id == identity["user_id"])).first()
            if member:
                existing = conn.execute(select(identity_credential.c.identity_id).where(
                    identity_credential.c.identity_id == member[0])).first()
                values = {"identity_id": member[0], "password_hash": encoded,
                          "created_at": now.isoformat(), "updated_at": now.isoformat()}
                credential_table = identity_credential
                key_col = identity_credential.c.identity_id
                credential_key = member[0]
            else:
                existing = conn.execute(select(password_credential.c.user_id).where(
                    password_credential.c.user_id == identity["user_id"])).first()
                values = {"user_id": identity["user_id"], "password_hash": encoded,
                      "created_at": now.isoformat(), "updated_at": now.isoformat()}
                credential_table = password_credential
                key_col = password_credential.c.user_id
                credential_key = identity["user_id"]
            if existing:
                conn.execute(credential_table.update().where(
                    key_col == credential_key).values(
                        password_hash=encoded, updated_at=now.isoformat()))
            else:
                conn.execute(credential_table.insert().values(**values))
            # A password change ends every browser session for this identity's profiles.
            profile_ids = ([row[0] for row in conn.execute(select(organization_membership.c.profile_user_id).where(
                organization_membership.c.identity_id == member[0]))]
                if member else [identity["user_id"]])
            conn.execute(browser_session.update().where(and_(
                browser_session.c.user_id.in_(profile_ids),
                browser_session.c.revoked_at.is_(None))).values(revoked_at=now.isoformat()))
        return True

    def workspaces_for_email(self, email: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(
                organization_membership.c.identity_id,
                organization_membership.c.organization_id,
                organization_membership.c.profile_user_id,
                organization.c.name, organization.c.url_key,
                user.c.email, user.c.name.label("profile_name"), user.c.admin,
            ).join(global_identity, global_identity.c.id == organization_membership.c.identity_id)
             .join(organization, organization.c.id == organization_membership.c.organization_id)
             .join(user, and_(user.c.id == organization_membership.c.profile_user_id,
                              user.c.organization_id == organization_membership.c.organization_id))
             .where(and_(global_identity.c.email == email.strip().lower(),
                         organization_membership.c.active.is_(True),
                         user.c.active.is_(True), user.c.archived_at.is_(None)))).mappings().all()
        return [dict(row) for row in rows]

    def matching_login_workspaces(self, email: str) -> list[dict]:
        """Return selectable workspaces; credential verification happens after selection."""
        return [{"organizationId": item["organization_id"],
                 "name": item["name"], "urlKey": item["url_key"]}
                for item in self.workspaces_for_email(email)]

    def create_session_for_membership(self, identity_id: str, profile_user_id: str,
                                      organization_id: str, days: int = 14) -> tuple[str, dict] | tuple[None, None]:
        selected = self.switch_membership(identity_id, organization_id)
        if not selected or selected["user_id"] != profile_user_id:
            return None, None
        raw = secrets.token_urlsafe(48)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=days)
        with self.engine.begin() as conn:
            conn.execute(browser_session.insert().values(
                id=new_id(), token_hash=token_hash(raw), user_id=profile_user_id,
                organization_id=organization_id, created_at=now.isoformat(),
                expires_at=expires.isoformat()))
        return raw, {"expires_at": expires.isoformat(), "user_id": profile_user_id,
                     "organization_id": organization_id, "identity_id": identity_id}

    def create_browser_session(self, email: str, password: str, *, organization_ref: str | None = None, days: int = 14) -> tuple[str, dict] | None:
        candidates = self.workspaces_for_email(email)
        if organization_ref:
            selected_workspace = next((r for r in candidates if
                r["organization_id"] == organization_ref or r["url_key"] == organization_ref), None)
            if not selected_workspace:
                return None
        else:
            selected_workspace = candidates[0] if len(candidates) == 1 else None
        if len(candidates) != 1:
            # A shared identity credential authenticates once; an explicit
            # workspace selects the profile only after membership is verified.
            if not selected_workspace:
                if candidates or not organization_ref:
                    self._reject_unverified(password)
                    return None
            else:
                identity_id = selected_workspace["identity_id"]
                with self.engine.connect() as conn:
                    credential = conn.execute(select(identity_credential).where(
                        identity_credential.c.identity_id == identity_id
                    )).mappings().first()
                if not credential:
                    self._reject_unverified(password)
                    return None
                if not self._verify_password(password, credential["password_hash"]):
                    return None
                raw = secrets.token_urlsafe(48)
                now = datetime.now(timezone.utc)
                expires = now + timedelta(days=days)
                with self.engine.begin() as conn:
                    conn.execute(browser_session.insert().values(
                        id=new_id(), token_hash=token_hash(raw),
                        user_id=selected_workspace["profile_user_id"],
                        organization_id=selected_workspace["organization_id"],
                        created_at=now.isoformat(), expires_at=expires.isoformat()))
                return raw, {"user_id": selected_workspace["profile_user_id"],
                             "identity_id": identity_id,
                             "organization_id": selected_workspace["organization_id"],
                             "expires_at": expires.isoformat()}
            # Pre-revision-6 fallback only; duplicate identities remain isolated.
            with self.engine.connect() as conn:
                legacy = conn.execute(select(password_credential.c.password_hash,
                    user.c.id.label("profile_user_id"), user.c.organization_id,
                    user.c.email, organization.c.url_key)
                    .join(user, user.c.id == password_credential.c.user_id)
                    .join(organization, organization.c.id == user.c.organization_id)
                    .where(and_(func.lower(user.c.email) == email.strip().lower(), user.c.active.is_(True),
                                user.c.archived_at.is_(None),
                                or_(user.c.organization_id == organization_ref,
                                    organization.c.url_key == organization_ref)))).mappings().first()
            if not legacy:
                self._reject_unverified(password)
                return None
            if not self._verify_password(password, legacy["password_hash"]):
                return None
            raw = secrets.token_urlsafe(48)
            now = datetime.now(timezone.utc)
            expires = now + timedelta(days=days)
            with self.engine.begin() as conn:
                conn.execute(browser_session.insert().values(
                    id=new_id(), token_hash=token_hash(raw), user_id=legacy["profile_user_id"],
                    organization_id=legacy["organization_id"], created_at=now.isoformat(),
                    expires_at=expires.isoformat()))
            return raw, {"user_id": legacy["profile_user_id"],
                         "organization_id": legacy["organization_id"], "expires_at": expires.isoformat()}
        selected = selected_workspace or candidates[0]
        with self.engine.connect() as conn:
            credential = conn.execute(select(identity_credential).where(
                identity_credential.c.identity_id == selected["identity_id"])).mappings().first()
            if not credential:
                # Backward-compatible fallback before migration/identity setup.
                credential = conn.execute(select(password_credential).where(
                    password_credential.c.user_id == selected["profile_user_id"])).mappings().first()
        if not credential:
            self._reject_unverified(password)
            return None
        if not self._verify_password(password, credential["password_hash"]):
            return None
        raw = secrets.token_urlsafe(48)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=days)
        with self.engine.begin() as conn:
            conn.execute(browser_session.insert().values(
                id=new_id(), token_hash=token_hash(raw), user_id=selected["profile_user_id"],
                organization_id=selected["organization_id"], created_at=now.isoformat(),
                expires_at=expires.isoformat()))
        return raw, {"user_id": selected["profile_user_id"], "identity_id": selected["identity_id"],
                     "organization_id": selected["organization_id"], "expires_at": expires.isoformat()}

    def resolve_browser_session(self, raw: str) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.connect() as conn:
            session = _one(conn, select(browser_session).join(user, and_(
                user.c.id == browser_session.c.user_id,
                user.c.organization_id == browser_session.c.organization_id))
                .where(and_(browser_session.c.token_hash == token_hash(raw),
                    browser_session.c.revoked_at.is_(None), browser_session.c.expires_at > now,
                    user.c.active.is_(True), user.c.archived_at.is_(None))))
            if not session:
                return None
            membership = _one(conn, select(organization_membership).where(and_(
                organization_membership.c.profile_user_id == session["user_id"],
                organization_membership.c.organization_id == session["organization_id"],
                organization_membership.c.active.is_(True))))
            if not membership:
                return None
            session["identity_id"] = membership["identity_id"]
            return session

    def revoke_browser_session(self, raw: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(browser_session.update().where(and_(
                browser_session.c.token_hash == token_hash(raw),
                browser_session.c.revoked_at.is_(None))).values(
                    revoked_at=datetime.now(timezone.utc).isoformat()))

    # ------------------------------------------------------------ serializers
    def ser_user(self, row: dict | None, ctx_user_id: str | None = None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "name": row.get("name"), "displayName": row.get("display_name"),
            "email": row.get("email"), "active": bool(row.get("active", True)),
            "isMe": row["id"] == ctx_user_id, "admin": bool(row.get("admin", False)),
            "avatarUrl": row.get("avatar_url"), "url": row.get("url"),
            "timezone": row.get("timezone"), "statusEmoji": row.get("status_emoji"),
            "statusLabel": row.get("status_label"),
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
        }

    def ser_state(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "name": row["name"], "color": row.get("color"),
            "description": row.get("description"), "position": row.get("position"),
            "type": row["type"], "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"), "_team_id": row.get("team_id"),
        }

    def ser_team(self, row: dict | None) -> dict | None:
        if not row:
            return None
        auto_started = row.get("auto_add_started", True)
        auto_completed = row.get("auto_add_completed", True)
        auto_backlog = row.get("auto_add_backlog", True)
        return {
            "id": row["id"], "name": row["name"], "key": row["key"],
            "description": row.get("description"), "color": row.get("color"),
            "icon": row.get("icon"),
            "visibility": row.get("visibility") or PUBLIC,
            "parentTeamId": row.get("parent_team_id"),
            "timezone": row.get("timezone"), "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"), "_id": row["id"],
            "autoAddStarted": True if auto_started is None else bool(auto_started),
            "autoAddCompleted": True if auto_completed is None else bool(auto_completed),
            "autoAddBacklog": True if auto_backlog is None else bool(auto_backlog),
            "_active_cycle_id": row.get("active_cycle_id"),
        }

    def ser_label(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "name": row["name"], "color": row.get("color"),
            "description": row.get("description"), "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def ser_cycle(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "name": row.get("name"), "number": row.get("number"),
            "description": row.get("description"),
            "startsAt": row.get("starts_at"), "endsAt": row.get("ends_at"),
            "completedAt": row.get("completed_at"), "progress": row.get("progress"),
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
            "_team_id": row.get("team_id"),
        }

    def ser_project(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "name": row["name"], "description": row.get("description"),
            "slugId": row.get("slug_id"), "icon": row.get("icon"), "color": row.get("color"),
            "state": row.get("state"), "progress": row.get("progress"),
            "startDate": row.get("start_date"), "targetDate": row.get("target_date"),
            "url": row.get("url"), "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "_lead_id": row.get("lead_id"), "_creator_id": row.get("creator_id"),
        }

    def ser_comment(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "body": row["body"], "url": row.get("url"),
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
            "editedAt": row.get("updated_at") if (row.get("updated_at") or "") > (row.get("created_at") or "") else None,
            "_user_id": row.get("user_id"),
            "_archived": row.get("archived_at") is not None,
        }

    def ser_attachment(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "url": row.get("url"), "title": row.get("title"),
            "subtitle": row.get("subtitle"), "contentType": row.get("content_type"),
            "createdAt": row.get("created_at"),
            "_issue_id": row.get("issue_id"),
        }

    def ser_github_link(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "kind": row.get("kind"),
            "externalId": row.get("external_id"), "url": row.get("url"),
            "title": row.get("title"), "state": row.get("state"),
            "author": row.get("author"), "sha": row.get("sha"),
            "environment": row.get("environment"),
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
        }

    def ser_issue(self, row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"], "identifier": row["identifier"], "title": row["title"],
            "description": row.get("description"), "priority": row.get("priority", 0),
            "priorityLabel": row.get("priority_label"), "estimate": row.get("estimate"),
            "url": row.get("url"), "branchName": row.get("branch_name"),
            "number": row.get("number"), "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"), "dueDate": row.get("due_date"),
            "completedAt": row.get("completed_at"), "canceledAt": row.get("canceled_at"),
            "startedAt": row.get("started_at"), "snoozedUntilAt": row.get("snoozed_until_at"),
            "stateChangedAt": row.get("state_changed_at"),
            # ids for lazy field resolvers
            "_state_id": row.get("state_id"), "_assignee_id": row.get("assignee_id"),
            "_creator_id": row.get("creator_id"), "_team_id": row.get("team_id"),
            "_project_id": row.get("project_id"), "_cycle_id": row.get("cycle_id"),
            "_parent_id": row.get("parent_id"), "_org_id": row.get("organization_id"),
            # Maintained by decision writes, so a board flags a blocked issue in one read.
            "decisionNeeded": bool(row.get("decision_needed")),
        }

    # ------------------------------------------------------------------ reads
    def get_user(self, org_id: str, user_id: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(conn, select(user).where(
                and_(user.c.organization_id == org_id, user.c.id == user_id)))

    def viewer(self, org_id: str, user_id: str) -> dict | None:
        return self.ser_user(self.get_user(org_id, user_id), user_id)

    def membership_for_profile(self, org_id: str, user_id: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(conn, select(organization_membership).where(and_(
                organization_membership.c.organization_id == org_id,
                organization_membership.c.profile_user_id == user_id,
                organization_membership.c.active.is_(True))))

    def identity_workspaces(self, identity_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(
                organization_membership.c.id,
                organization_membership.c.organization_id.label("organizationId"),
                organization.c.name, organization.c.url_key.label("urlKey"),
                user.c.admin,
            ).join(organization, organization.c.id == organization_membership.c.organization_id)
             .join(user, and_(user.c.id == organization_membership.c.profile_user_id,
                              user.c.organization_id == organization_membership.c.organization_id))
             .where(and_(organization_membership.c.identity_id == identity_id,
                         organization_membership.c.active.is_(True),
                         user.c.active.is_(True), user.c.archived_at.is_(None)))
             .order_by(organization.c.name)).mappings().all()
        return [{"id": r["id"], "organizationId": r["organizationId"],
                 "name": r["name"], "urlKey": r["urlKey"],
                 "role": "admin" if r["admin"] else "member"} for r in rows]

    def switch_membership(self, identity_id: str | None, organization_ref: str) -> dict | None:
        if not identity_id:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(
                organization_membership.c.profile_user_id,
                organization_membership.c.organization_id,
                organization.c.id.label("id"), organization.c.name,
                organization.c.url_key.label("urlKey"),
            ).join(organization, organization.c.id == organization_membership.c.organization_id)
             .join(user, and_(user.c.id == organization_membership.c.profile_user_id,
                              user.c.organization_id == organization_membership.c.organization_id))
             .where(and_(organization_membership.c.identity_id == identity_id,
                         organization.c.id == organization_ref,
                         organization_membership.c.active.is_(True),
                         user.c.active.is_(True), user.c.archived_at.is_(None)))).mappings().first()
        if not row:
            return None
        return {"user_id": row["profile_user_id"], "organization_id": row["organization_id"],
                "organization": {"id": row["id"], "name": row["name"], "urlKey": row["urlKey"]}}

    def create_membership_invitation(self, org_id: str, profile_user_id: str,
                                     creator_profile_id: str, hours: int = 72) -> str | None:
        raw = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            valid = conn.execute(select(user.c.id).where(and_(
                user.c.id == profile_user_id, user.c.organization_id == org_id,
                user.c.active.is_(True), user.c.archived_at.is_(None),
            ))).first()
            if not valid:
                return None
            # One row per profile: the column is UNIQUE, so an accepted
            # invitation left behind made re-inviting a former member fail with
            # an IntegrityError. Replace the row either way; the acceptance is
            # recorded on the membership, not here.
            conn.execute(membership_invitation.delete().where(
                membership_invitation.c.profile_user_id == profile_user_id,
            ))
            conn.execute(membership_invitation.insert().values(
                id=new_id(), token_hash=token_hash(raw), organization_id=org_id,
                profile_user_id=profile_user_id, created_by_profile_id=creator_profile_id,
                created_at=now.isoformat(),
                expires_at=(now + timedelta(hours=hours)).isoformat()))
        return raw

    def pending_membership_invitations(self, org_id: str) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.connect() as conn:
            stmt = select(user.c.id, user.c.name, user.c.email, membership_invitation.c.expires_at).select_from(
                membership_invitation.join(user, user.c.id == membership_invitation.c.profile_user_id)
            ).where(and_(
                membership_invitation.c.organization_id == org_id,
                membership_invitation.c.accepted_at.is_(None),
                membership_invitation.c.expires_at > now,
            )).order_by(user.c.name)
            return [{"userId": r.id, "name": r.name, "email": r.email, "expiresAt": r.expires_at}
                    for r in conn.execute(stmt).mappings().all()]

    def revoke_membership_invitation(self, org_id: str, profile_user_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(membership_invitation.delete().where(and_(
                membership_invitation.c.organization_id == org_id,
                membership_invitation.c.profile_user_id == profile_user_id,
                membership_invitation.c.accepted_at.is_(None),
            )))
            return result.rowcount > 0

    def set_member_active(self, org_id: str, actor_id: str, user_id: str, active: bool) -> dict | None:
        """Deactivate or reactivate a member. Refuses to strand the workspace."""
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            target = conn.execute(select(user.c.id, user.c.admin, user.c.active).where(and_(
                user.c.organization_id == org_id, user.c.id == user_id,
                user.c.archived_at.is_(None), user.c.kind == "human",
            ))).first()
            if not target:
                return None
            if not active:
                if user_id == actor_id:
                    return None
                if target.admin and target.active:
                    other_admins = conn.execute(select(user.c.id).where(and_(
                        user.c.organization_id == org_id, user.c.admin.is_(True),
                        user.c.active.is_(True), user.c.archived_at.is_(None),
                        user.c.id != user_id,
                    )).with_for_update()).all()
                    if not other_admins:
                        return None
            conn.execute(user.update().where(user.c.id == user_id).values(
                active=bool(active), updated_at=now))
        return self.get_user(org_id, user_id)

    def archive_member(self, org_id: str, actor_id: str, user_id: str) -> dict | None:
        """Remove a member: archived, sessions and keys ended, re-invitable."""
        from hoja_server.db import api_key as api_key_table

        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            target = conn.execute(select(user.c.id, user.c.admin, user.c.active).where(and_(
                user.c.organization_id == org_id, user.c.id == user_id,
                user.c.archived_at.is_(None), user.c.kind == "human",
            ))).first()
            if not target:
                return None
            if user_id == actor_id:
                return None
            if target.admin and target.active:
                other_admins = conn.execute(select(user.c.id).where(and_(
                    user.c.organization_id == org_id, user.c.admin.is_(True),
                    user.c.active.is_(True), user.c.archived_at.is_(None),
                    user.c.id != user_id,
                )).with_for_update()).all()
                if not other_admins:
                    return None
            conn.execute(user.update().where(user.c.id == user_id).values(
                active=False, archived_at=now, updated_at=now))
            conn.execute(organization_membership.update().where(and_(
                organization_membership.c.organization_id == org_id,
                organization_membership.c.profile_user_id == user_id,
            )).values(active=False))
            conn.execute(api_key_table.update().where(and_(
                api_key_table.c.user_id == user_id,
                api_key_table.c.organization_id == org_id,
                api_key_table.c.revoked_at.is_(None),
            )).values(revoked_at=now))
            conn.execute(browser_session.delete().where(
                browser_session.c.user_id == user_id))
            identity_id = conn.execute(select(organization_membership.c.identity_id).where(and_(
                organization_membership.c.organization_id == org_id,
                organization_membership.c.profile_user_id == user_id,
            ))).scalar_one_or_none()
            if identity_id:
                conn.execute(identity_credential.delete().where(
                    identity_credential.c.identity_id == identity_id))
        return self.get_user(org_id, user_id)

    def archived_members(self, org_id: str) -> list[dict]:
        """Removed members, so an admin can audit and undo a removal."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(user).where(and_(
                user.c.organization_id == org_id,
                user.c.archived_at.is_not(None),
                user.c.kind == "human",
            )).order_by(user.c.archived_at.desc())).mappings().all()
        return [dict(r) for r in rows]

    def restore_member(self, org_id: str, actor_id: str, user_id: str) -> dict | None:
        """Undo an archive: the same profile returns with its history intact."""
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            target = conn.execute(select(user.c.id).where(and_(
                user.c.organization_id == org_id, user.c.id == user_id,
                user.c.archived_at.is_not(None), user.c.kind == "human",
            ))).first()
            if not target:
                return None
            conn.execute(user.update().where(user.c.id == user_id).values(
                active=True, archived_at=None, updated_at=now))
            conn.execute(organization_membership.update().where(and_(
                organization_membership.c.organization_id == org_id,
                organization_membership.c.profile_user_id == user_id,
            )).values(active=True))
        return self.get_user(org_id, user_id)

    def accept_membership_invitation(self, identity_id: str, raw: str,
                                     invitee_password: str) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            invite = conn.execute(select(membership_invitation).where(and_(
                membership_invitation.c.token_hash == token_hash(raw),
                membership_invitation.c.accepted_at.is_(None),
                membership_invitation.c.expires_at > now,
            ))).mappings().first()
            if not invite:
                return None
            already = conn.execute(select(organization_membership.c.id).where(and_(
                organization_membership.c.identity_id == identity_id,
                organization_membership.c.organization_id == invite["organization_id"],
            ))).first()
            if already:
                return None
            profile = conn.execute(select(user.c.id).where(and_(
                user.c.id == invite["profile_user_id"],
                user.c.organization_id == invite["organization_id"],
                user.c.active.is_(True), user.c.archived_at.is_(None),
            ))).first()
            if not profile:
                return None
            identity_email = conn.execute(select(global_identity.c.email).where(
                global_identity.c.id == identity_id)).scalar_one_or_none()
            target_identity = conn.execute(select(organization_membership.c.identity_id).where(
                organization_membership.c.profile_user_id == profile[0])).scalar_one_or_none()
            target_credential = conn.execute(select(identity_credential).where(
                identity_credential.c.identity_id == target_identity)).mappings().first() if target_identity else None
            profile_email = conn.execute(select(user.c.email).where(
                user.c.id == profile[0])).scalar_one_or_none()
            if (not identity_email or not profile_email or identity_id == target_identity or
                    not addresses.same(identity_email, profile_email) or
                    not target_credential):
                return None
            if not self._verify_password(invitee_password, target_credential["password_hash"]):
                return None
            conn.execute(organization_membership.update().where(
                organization_membership.c.profile_user_id == profile[0]).values(
                    identity_id=identity_id, active=True))
            conn.execute(membership_invitation.update().where(
                membership_invitation.c.id == invite["id"]).values(
                    accepted_at=now, accepted_identity_id=identity_id))
            org_row = conn.execute(select(organization.c.id, organization.c.name,
                                          organization.c.url_key).where(
                organization.c.id == invite["organization_id"])).mappings().first()
            return {"id": org_row["id"], "name": org_row["name"],
                    "urlKey": org_row["url_key"]} if org_row else None

    def peek_membership_invitation(self, raw: str) -> dict | None:
        """The workspace a still-valid code belongs to, without spending it.

        The invite screen needs this before it asks for anything: telling
        someone their code expired only after they have chosen a password is
        the same dead end, one screen later.
        """
        if not raw:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.connect() as conn:
            invite = conn.execute(
                select(membership_invitation.c.organization_id).select_from(
                    membership_invitation.join(
                        user, user.c.id == membership_invitation.c.profile_user_id)
                ).where(and_(
                    membership_invitation.c.token_hash == token_hash(raw),
                    membership_invitation.c.accepted_at.is_(None),
                    membership_invitation.c.expires_at > now,
                    user.c.active.is_(True),
                    user.c.archived_at.is_(None),
                ))).first()
            if invite is None:
                return None
            org_row = conn.execute(select(organization.c.name, organization.c.url_key).where(
                organization.c.id == invite[0])).mappings().first()
        return dict(org_row) if org_row else None

    def accept_membership_invitation_by_identity(self, identity_id: str | None,
                                                 raw: str) -> dict | None:
        """Accept an invitation for an already verified identity, with no password.

        A workspace that signs people in with Google has no credential to
        create: the invitation names an address and Google proved it. Every
        check that made the password path safe stays here — the code exists, is
        unspent and unexpired, the profile belongs to the inviting organization,
        the profile is not deactivated, and the verified address is the invited
        address. Nothing about the *identity* is inferred from the code.
        """
        if not identity_id or not raw:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            invite = conn.execute(select(membership_invitation).where(and_(
                membership_invitation.c.token_hash == token_hash(raw),
                membership_invitation.c.accepted_at.is_(None),
                membership_invitation.c.expires_at > now,
            ))).mappings().first()
            if not invite:
                return None
            identity_email = conn.execute(select(global_identity.c.email).where(
                global_identity.c.id == identity_id)).scalar_one_or_none()
            profile = conn.execute(select(user.c.id, user.c.email).where(and_(
                user.c.id == invite["profile_user_id"],
                user.c.organization_id == invite["organization_id"],
                user.c.active.is_(True), user.c.archived_at.is_(None),
            ))).first()
            if not profile or not identity_email:
                return None
            if not addresses.same(identity_email, profile[1]):
                return None
            membership = conn.execute(select(organization_membership.c.id).where(and_(
                organization_membership.c.profile_user_id == profile[0],
                organization_membership.c.organization_id == invite["organization_id"],
            ))).first()
            if membership is None:
                return None
            conn.execute(organization_membership.update().where(
                organization_membership.c.id == membership[0]).values(
                identity_id=identity_id, active=True))
            conn.execute(membership_invitation.update().where(
                membership_invitation.c.id == invite["id"]).values(
                accepted_at=now, accepted_identity_id=identity_id))
            org_row = conn.execute(select(organization.c.id, organization.c.name,
                                          organization.c.url_key).where(
                organization.c.id == invite["organization_id"])).mappings().first()
        return {"id": org_row["id"], "name": org_row["name"],
                "urlKey": org_row["url_key"]} if org_row else None

    def accept_new_member_invitation(self, raw: str, email: str,
                                     password: str) -> tuple[str, dict] | None:
        """Set credentials for a pre-created invited profile and consume its code."""
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters.")
        now = datetime.now(timezone.utc)
        salt = secrets.token_bytes(16)
        digest = self._password_digest(password, salt)
        identity_id = new_id()
        session_token = secrets.token_urlsafe(48)
        normalized_email = addresses.canonical(email)
        with self.engine.begin() as conn:
            invite = conn.execute(select(membership_invitation).where(and_(
                membership_invitation.c.token_hash == token_hash(raw),
                membership_invitation.c.accepted_at.is_(None),
                membership_invitation.c.expires_at > now.isoformat(),
            ))).mappings().first()
            if not invite:
                return None
            profile = conn.execute(select(user).where(and_(
                user.c.id == invite["profile_user_id"],
                user.c.organization_id == invite["organization_id"],
                user.c.active.is_(True), user.c.archived_at.is_(None),
                func.lower(user.c.email) == normalized_email,
            ))).mappings().first()
            if not profile:
                return None
            membership = conn.execute(select(organization_membership).where(and_(
                organization_membership.c.profile_user_id == profile["id"],
                organization_membership.c.organization_id == invite["organization_id"],
                organization_membership.c.active.is_(True),
            ))).mappings().first()
            if not membership:
                return None
            credential = conn.execute(select(identity_credential).where(
                identity_credential.c.identity_id == membership["identity_id"])).first()
            if credential:
                return None
            conn.execute(identity_credential.insert().values(
                identity_id=membership["identity_id"], password_hash=digest,
                created_at=now.isoformat(), updated_at=now.isoformat()))
            conn.execute(membership_invitation.update().where(
                membership_invitation.c.id == invite["id"]).values(
                accepted_at=now.isoformat(), accepted_identity_id=membership["identity_id"]))
            expires = now + timedelta(days=14)
            conn.execute(browser_session.insert().values(
                id=new_id(), token_hash=token_hash(session_token), user_id=profile["id"],
                organization_id=invite["organization_id"], created_at=now.isoformat(),
                expires_at=expires.isoformat()))
            organization_row = conn.execute(select(organization).where(
                organization.c.id == invite["organization_id"])).mappings().first()
        if not organization_row:
            return None
        return session_token, {"user_id": profile["id"], "organization_id": invite["organization_id"],
                               "identity_id": membership["identity_id"],
                               "organization": {"id": organization_row["id"],
                                                "name": organization_row["name"],
                                                "urlKey": organization_row["url_key"]}}

    def organization_members(self, org_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            return _conn_rows(conn, select(user).where(and_(
                user.c.organization_id == org_id,
                user.c.archived_at.is_(None),
                user.c.kind == "human",  # extension bots are not members
            )).order_by(user.c.name, user.c.email))

    def set_member_admin_role(self, org_id: str, user_id: str, is_admin: bool) -> dict | None:
        with self.engine.begin() as conn:
            target = conn.execute(select(user.c.admin).where(and_(
                user.c.organization_id == org_id, user.c.id == user_id,
                user.c.archived_at.is_(None), user.c.kind == "human",
            ))).first()
            if not target:
                return None
            if target[0] and not is_admin:
                admins = conn.execute(select(user.c.id).where(and_(
                    user.c.organization_id == org_id, user.c.admin.is_(True),
                    user.c.archived_at.is_(None),
                )).with_for_update()).all()
                if len(admins) <= 1:
                    return None
            result = conn.execute(user.update().where(and_(
                user.c.organization_id == org_id, user.c.id == user_id,
                user.c.archived_at.is_(None),
            )).values(admin=bool(is_admin), updated_at=datetime.now(timezone.utc).isoformat()))
            if not result.rowcount:
                return None
        return self.get_user(org_id, user_id)

    def invite_member(self, org_id: str, name: str, email: str, is_admin: bool = False,
                      team_ids: list[str] | None = None) -> tuple[dict, str] | None:
        """Add a profile for someone who is not in this workspace yet.

        A returning member is not an error: if the profile exists but was
        deactivated, the invitation reactivates *that* profile so its history
        and its team seats survive. Every other refusal names its cause, because
        "invite failed" made the admin guess between four different situations.
        """
        from hoja_server.db import api_key as api_key_table, gen_token, new_id, token_hash

        normalized_email = addresses.canonical(email)
        clean_name = name.strip()
        if not clean_name or "@" not in normalized_email:
            raise InviteRefused("invalid_input", "A name and an email address are required.")
        raw_token = gen_token()
        uid = new_id()
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            existing = conn.execute(select(
                user.c.id, user.c.active, user.c.email, user.c.name,
            ).where(and_(
                user.c.organization_id == org_id,
                func.lower(user.c.email) == normalized_email,
            ))).first()
            if existing is not None:
                if existing[1]:
                    raise InviteRefused(
                        "already_member",
                        f"{existing[3] or existing[2] or normalized_email} is already a member of this workspace.",
                    )
                # Removed earlier: restore the profile instead of minting a second
                # one, and hand back a fresh member invite key.
                # Reactivation is a *new* relationship, not a resumption: the
                # inviter decides the role, and the old password and browser
                # sessions belong to the access that was revoked.
                conn.execute(user.update().where(user.c.id == existing[0]).values(
                    active=True, admin=bool(is_admin), archived_at=None, updated_at=now))
                stale = conn.execute(select(organization_membership.c.identity_id).where(
                    organization_membership.c.profile_user_id == existing[0])).scalar_one_or_none()
                if stale:
                    conn.execute(identity_credential.delete().where(
                        identity_credential.c.identity_id == stale))
                conn.execute(browser_session.delete().where(
                    browser_session.c.user_id == existing[0]))
                conn.execute(api_key_table.insert().values(
                    id=new_id(), token_hash=token_hash(raw_token), label="member invite",
                    user_id=existing[0], organization_id=org_id, created_at=now,
                ))
                uid = existing[0]
                identity = conn.execute(select(global_identity.c.id).where(
                    global_identity.c.email == normalized_email)).first()
                if identity is None:
                    identity_id = new_id()
                    conn.execute(global_identity.insert().values(
                        id=identity_id, email=normalized_email, created_at=now))
                else:
                    identity_id = identity[0]
                membership = conn.execute(select(organization_membership.c.id).where(and_(
                    organization_membership.c.profile_user_id == uid,
                    organization_membership.c.organization_id == org_id,
                ))).first()
                if membership is None:
                    conn.execute(organization_membership.insert().values(
                        id=new_id(), identity_id=identity_id, organization_id=org_id,
                        profile_user_id=uid, active=True, created_at=now))
                else:
                    conn.execute(organization_membership.update().where(
                        organization_membership.c.id == membership[0]).values(
                        identity_id=identity_id, active=True))
                for team_id in dict.fromkeys(team_ids or []):
                    resolved = conn.execute(select(team.c.id).where(and_(
                        team.c.organization_id == org_id,
                        or_(team.c.id == team_id, team.c.key == team_id.upper()),
                    ))).first()
                    if resolved is None:
                        continue
                    for seat in self._team_and_ancestors(conn, org_id, resolved[0]):
                        already = conn.execute(select(team_member.c.team_id).where(and_(
                            team_member.c.team_id == seat,
                            team_member.c.user_id == uid))).first()
                        if already is None:
                            conn.execute(team_member.insert().values(
                                team_id=seat, user_id=uid, role="member"))
                restored = conn.execute(select(user).where(user.c.id == uid)).mappings().first()
                return dict(restored), raw_token
            if conn.execute(select(global_identity.c.id).where(
                global_identity.c.email == normalized_email)).first():
                raise InviteRefused(
                    "identity_exists",
                    "That address already has an account on this workspace. Ask an admin to restore it instead.",
                )
            conn.execute(user.insert().values(
                id=uid, organization_id=org_id, name=clean_name, display_name=clean_name,
                email=normalized_email, active=True, admin=bool(is_admin),
                created_at=now, updated_at=now,
            ))
            identity_id = new_id()
            conn.execute(global_identity.insert().values(
                id=identity_id, email=normalized_email, created_at=now))
            conn.execute(organization_membership.insert().values(
                id=new_id(), identity_id=identity_id, organization_id=org_id,
                profile_user_id=uid, active=True, created_at=now))
            conn.execute(api_key_table.insert().values(
                id=new_id(), token_hash=token_hash(raw_token), label="member invite",
                user_id=uid, organization_id=org_id, created_at=now,
            ))
            seats: dict[str, None] = {}
            for team_id in dict.fromkeys(team_ids or []):
                # Resolve a team key to its row id first: `team_member.team_id`
                # is a foreign key, and the change-event trigger derives the
                # organization from that team row, so an unresolved key would
                # insert a dangling seat and fail the whole invitation.
                canonical = conn.execute(select(team.c.id).where(and_(
                    team.c.organization_id == org_id,
                    or_(team.c.id == team_id, team.c.key == team_id.upper()),
                ))).first()
                if canonical is None:
                    continue
                team_id = canonical[0]
                # Same rule as set_team_membership: a sub-team seat implies its ancestors.
                for ancestor in self._team_and_ancestors(conn, org_id, team_id):
                    seats[ancestor] = None
            for team_id in seats:
                conn.execute(team_member.insert().values(
                    team_id=team_id, user_id=uid, role="member"))
        return self.get_user(org_id, uid), raw_token

    def workspace(self, org_id: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(conn, select(organization.c.id, organization.c.name,
                                     organization.c.url_key.label("urlKey")).where(
                organization.c.id == org_id))

    def update_workspace(self, org_id: str, values: dict) -> dict | None:
        changes = {}
        if values.get("name") is not None:
            name = values["name"].strip()
            if not name:
                return None
            changes["name"] = name
        if values.get("urlKey") is not None:
            url_key = values["urlKey"].strip().lower()
            if not url_key or not url_key.replace("-", "").isalnum():
                return None
            changes["url_key"] = url_key
        if not changes:
            return self.workspace(org_id)
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            result = conn.execute(organization.update().where(
                organization.c.id == org_id).values(**changes))
            if not result.rowcount:
                return None
        return self.workspace(org_id)

    def workspace_update(self, org_id: str, values: dict) -> dict | None:
        """Compatibility alias for the existing workspace settings update."""
        return self.update_workspace(org_id, values)

    def teams(self, org_id: str, key: str | None = None, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(team).where(and_(
                team.c.organization_id == org_id, team.c.archived_at.is_(None)))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, team.c.id)
            if key:
                stmt = stmt.where(team.c.key == key.upper())
            stmt = stmt.order_by(team.c.key)
            return [self.ser_team(r) for r in _conn_rows(conn, stmt)]

    def team_by_id_or_key(self, org_id: str, ident: str, viewer_id: str | None = None) -> dict | None:
        with self.engine.connect() as conn:
            stmt = select(team).where(and_(
                team.c.organization_id == org_id,
                or_(team.c.id == ident, team.c.key == ident.upper())))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, team.c.id)
            return _one(conn, stmt)  # raw row (field resolvers need _id / active_cycle_id)

    def team_visible_for(self, org_id: str, viewer_id: str | None, team_id: str | None) -> bool:
        if not team_id:
            return True
        with self.engine.connect() as conn:
            return team_visible(conn, org_id, viewer_id, team_id)

    def team_states(self, org_id: str, team_id: str, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            if viewer_id is not None and not team_visible(conn, org_id, viewer_id, team_id):
                return []
            stmt = select(workflow_state).where(and_(
                workflow_state.c.organization_id == org_id,
                workflow_state.c.team_id == team_id,
                workflow_state.c.archived_at.is_(None))).order_by(workflow_state.c.position)
            return [self.ser_state(r) for r in _conn_rows(conn, stmt)]

    def workflow_states(self, org_id: str, flt: dict | None = None, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(workflow_state).where(and_(
                workflow_state.c.organization_id == org_id,
                workflow_state.c.archived_at.is_(None),
            ))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, workflow_state.c.team_id)
            if flt:
                if "team" in flt:
                    team_ids = _resolve_team_ids(conn, org_id, flt["team"])
                    stmt = stmt.where(
                        workflow_state.c.team_id.in_(team_ids or ["__none__"])
                    )
                for field, column in (
                    ("type", workflow_state.c.type),
                    ("name", workflow_state.c.name),
                ):
                    if field in flt:
                        clause = _apply_str_cmp(column, flt[field])
                        if clause is not None:
                            stmt = stmt.where(clause)
            return [
                self.ser_state(r)
                for r in _conn_rows(conn, stmt.order_by(workflow_state.c.position))
            ]

    def set_team_membership(self, org_id: str, team_id: str, user_id: str, *, add: bool,
                            role: str = "member") -> None:
        """Add or remove a team member, honouring the parent/child membership rules.

        Adding to a sub-team also adds the person to every ancestor, because a
        department is a reporting structure and a sub-team member belongs to the
        department above it. Removal never cascades: dropping someone from a parent
        while they are still on a descendant is refused instead of silently taking
        away their access to the sub-team.
        """
        with self.engine.begin() as conn:
            if add:
                if role not in TEAM_ROLES:
                    raise GraphQLError("role must be one of owner, member",
                                       extensions={"code": "BAD_USER_INPUT", "field": "role"})
                for ancestor in self._team_and_ancestors(conn, org_id, team_id):
                    exists = conn.execute(select(team_member.c.user_id).where(and_(
                        team_member.c.team_id == ancestor, team_member.c.user_id == user_id))).first()
                    if not exists:
                        conn.execute(team_member.insert().values(
                            team_id=ancestor, user_id=user_id,
                            role=role if ancestor == team_id else "member"))
                return
            descendants = self._team_descendants(conn, org_id, team_id)
            still_on = [tid for tid in descendants if tid != team_id and conn.execute(
                select(team_member.c.user_id).where(and_(
                    team_member.c.team_id == tid, team_member.c.user_id == user_id))).first()]
            if still_on:
                names = ", ".join(sorted(
                    r[0] for r in conn.execute(select(team.c.key).where(team.c.id.in_(still_on)))))
                raise GraphQLError(
                    f"This person is still a member of the sub-team(s) {names}. "
                    "Remove them from those first.",
                    extensions={"code": "BAD_USER_INPUT", "field": "teamId",
                                "subTeams": names})
            conn.execute(team_member.delete().where(and_(
                team_member.c.team_id == team_id, team_member.c.user_id == user_id)))

    @staticmethod
    def _team_and_ancestors(conn, org_id: str, team_id: str) -> list[str]:
        return team_ancestor_ids(conn, org_id, team_id)

    @staticmethod
    def _team_descendants(conn, org_id: str, team_id: str) -> list[str]:
        return team_descendant_ids(conn, org_id, team_id)

    def team_members(self, org_id: str, team_id: str, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            if viewer_id is not None and not team_visible(conn, org_id, viewer_id, team_id):
                return []
            stmt = select(user).select_from(
                user.join(team_member, team_member.c.user_id == user.c.id)
            ).where(and_(team_member.c.team_id == team_id, user.c.organization_id == org_id,
                         user.c.kind == "human"))  # extension bots hold seats but are not members
            return [self.ser_user(r) for r in _conn_rows(conn, stmt)]

    def team_children(self, org_id: str, team_id: str, viewer_id: str | None = None) -> list[dict]:
        """Direct sub-teams, scoped like every other team read."""
        with self.engine.connect() as conn:
            if viewer_id is not None and not team_visible(conn, org_id, viewer_id, team_id):
                return []
            stmt = select(team).where(and_(
                team.c.organization_id == org_id,
                team.c.parent_team_id == team_id,
                team.c.archived_at.is_(None)))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, team.c.id)
            return [self.ser_team(r) for r in _conn_rows(conn, stmt.order_by(team.c.key))]

    def team_cycles(self, org_id: str, team_id: str, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            if viewer_id is not None and not team_visible(conn, org_id, viewer_id, team_id):
                return []
            stmt = select(cycle).where(and_(
                cycle.c.organization_id == org_id, cycle.c.team_id == team_id)).order_by(cycle.c.number)
            return [self.ser_cycle(r) for r in _conn_rows(conn, stmt)]

    def cycles(self, org_id: str, flt: dict | None = None, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(cycle).where(cycle.c.organization_id == org_id)
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, cycle.c.team_id)
            if flt:
                if "team" in flt:
                    team_ids = _resolve_team_ids(conn, org_id, flt["team"])
                    stmt = stmt.where(cycle.c.team_id.in_(team_ids or ["__none__"]))
                for field, column in (("id", cycle.c.id), ("name", cycle.c.name)):
                    if field in flt:
                        clause = _apply_str_cmp(column, flt[field])
                        if clause is not None:
                            stmt = stmt.where(clause)
                cycle_ids = _resolve_cycle_ids(conn, org_id, flt)
                if cycle_ids is not None:
                    stmt = stmt.where(cycle.c.id.in_(cycle_ids or ["__none__"]))
            return [
                self.ser_cycle(r)
                for r in _conn_rows(conn, stmt.order_by(cycle.c.number))
            ]

    def team_cadence_snapshot(self, org_id: str, team_id: str, viewer_id: str) -> dict:
        """Cadence view for read-only API keys: reads persisted state and never reconciles."""
        with self.engine.connect() as conn:
            if not team_visible(conn, org_id, viewer_id, team_id):
                raise ValueError("Team not found in this workspace.")
            row = _one(conn, select(team).where(and_(team.c.organization_id == org_id, team.c.id == team_id)))
            cycle_rows = _conn_rows(conn, select(cycle).where(and_(
                cycle.c.organization_id == org_id, cycle.c.team_id == team_id)).order_by(cycle.c.number))
        zone_name = row.get("timezone") or "UTC"
        if not row.get("cadence_enabled"):
            return {"enabled": False, "weekday": None, "timezone": zone_name, "policyVersion": 1,
                    "activeCycle": None, "upcomingCycles": []}
        active = next((c for c in cycle_rows if c["id"] == row.get("active_cycle_id")), None)
        after_number = active["number"] if active else 0
        upcoming = [c for c in cycle_rows if c["number"] > after_number][:2]
        return {"enabled": True, "weekday": row.get("cadence_weekday"), "timezone": zone_name,
                "policyVersion": row.get("cadence_policy_version"),
                "activeCycle": self.ser_cycle(active), "upcomingCycles": [self.ser_cycle(c) for c in upcoming]}

    def issue_by_id_or_identifier(self, org_id: str, ident: str, viewer_id: str | None = None) -> dict | None:
        with self.engine.connect() as conn:
            stmt = select(issue).where(and_(
                issue.c.organization_id == org_id,
                or_(issue.c.id == ident, issue.c.identifier == ident.upper())))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            return self.ser_issue(_one(conn, stmt))

    def issue_children(
        self, org_id: str, issue_id: str, viewer_id: str | None = None,
        after: tuple[str, str] | None = None, limit: int | None = None,
    ) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(issue).where(and_(
                issue.c.organization_id == org_id,
                issue.c.parent_id == issue_id,
                issue.c.archived_at.is_(None),
            ))
            if after:
                created_at, cursor_id = after
                stmt = stmt.where(or_(
                    issue.c.created_at > created_at,
                    and_(issue.c.created_at == created_at, issue.c.id > cursor_id),
                ))
            stmt = stmt.order_by(issue.c.created_at, issue.c.id)
            if limit is not None:
                stmt = stmt.limit(limit)
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            return [self.ser_issue(row) for row in _conn_rows(conn, stmt)]

    def inbox_notifications(self, org_id: str, user_id: str, after=None, limit=51,
                            unread_only=False, archived=False) -> list[dict]:
        with self.engine.connect() as conn:
            disabled_kinds = [key.removeprefix("notification:") for key, value
                              in self.user_preferences(user_id).items()
                              if key.startswith("notification:") and value == "false"]
            stmt = select(inbox_notification).where(and_(
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.recipient_id == user_id,
                inbox_notification.c.archived_at.is_not(None) if archived else inbox_notification.c.archived_at.is_(None),
            ))
            if disabled_kinds:
                stmt = stmt.where(inbox_notification.c.kind.not_in(disabled_kinds))
            if unread_only:
                stmt = stmt.where(inbox_notification.c.read_at.is_(None))
            if after:
                ts, notification_id = after
                stmt = stmt.where(inbox_notification.c.created_at <= ts).where(or_(inbox_notification.c.created_at < ts,
                    and_(inbox_notification.c.created_at == ts, inbox_notification.c.id < notification_id)))
            return _conn_rows(conn, stmt.order_by(
                inbox_notification.c.created_at.desc(), inbox_notification.c.id.desc()).limit(limit))

    def inbox_unread_count(self, org_id: str, user_id: str) -> int:
        from sqlalchemy import func
        with self.engine.connect() as conn:
            conditions = [
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.recipient_id == user_id,
                inbox_notification.c.read_at.is_(None),
                inbox_notification.c.archived_at.is_(None),
            ]
            disabled_kinds = [key.removeprefix("notification:") for key, value
                              in self.user_preferences(user_id).items()
                              if key.startswith("notification:") and value == "false"]
            if disabled_kinds:
                conditions.append(inbox_notification.c.kind.not_in(disabled_kinds))
            return conn.execute(select(func.count()).select_from(inbox_notification).where(
                and_(*conditions))).scalar_one()

    def update_profile_name(self, org_id: str, user_id: str, name: str) -> dict | None:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 80:
            return None
        with self.engine.begin() as conn:
            result = conn.execute(user.update().where(and_(
                user.c.id == user_id, user.c.organization_id == org_id,
                user.c.active.is_(True), user.c.archived_at.is_(None),
            )).values(name=clean_name, display_name=clean_name,
                      updated_at=datetime.now(timezone.utc).isoformat()))
            if not result.rowcount:
                return None
        return self.ser_user(self.get_user(org_id, user_id), user_id)

    def set_notification_preference(self, user_id: str, kind: str, enabled: bool) -> bool:
        if kind not in {"issueCreated", "issueUpdated"}:
            return False
        return self.set_user_preference(user_id, f"notification:{kind}",
                                        "true" if enabled else "false")

    def inbox_mark(self, org_id: str, user_id: str, notification_id: str, column: str) -> bool:
        if column not in ("read_at", "archived_at"):
            raise ValueError("Unsupported notification state.")
        from hoja_server.db import now_iso
        target = inbox_notification.c[column]
        with self.engine.begin() as conn:
            result = conn.execute(inbox_notification.update().where(and_(
                inbox_notification.c.id == notification_id,
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.recipient_id == user_id,
                target.is_(None),
            )).values({column: now_iso()}))
            return result.rowcount > 0 or conn.execute(select(inbox_notification.c.id).where(and_(
                inbox_notification.c.id == notification_id,
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.recipient_id == user_id,
            ))).first() is not None

    def inbox_mark_all_read(self, org_id: str, user_id: str) -> bool:
        from hoja_server.db import now_iso
        with self.engine.begin() as conn:
            conn.execute(inbox_notification.update().where(and_(
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.recipient_id == user_id,
                inbox_notification.c.read_at.is_(None),
            )).values(read_at=now_iso()))
        return True

    def my_issue_activity(self, org_id: str, viewer_id: str, after=None, limit=51) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(issue_activity).join(issue, and_(
                issue_activity.c.issue_id == issue.c.id,
                issue_activity.c.organization_id == issue.c.organization_id,
            )).where(and_(
                issue_activity.c.organization_id == org_id,
                issue.c.organization_id == org_id,
                issue.c.archived_at.is_(None),
                or_(issue.c.assignee_id == viewer_id, issue.c.creator_id == viewer_id,
                    issue_activity.c.actor_id == viewer_id),
            ))
            # Activity carries `changes`, which can repeat a title or body: scope it like the issue itself.
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            if after:
                ts, event_id = after
                stmt = stmt.where(issue_activity.c.created_at <= ts).where(or_(issue_activity.c.created_at < ts,
                    and_(issue_activity.c.created_at == ts, issue_activity.c.id < event_id)))
            rows = _conn_rows(conn, stmt.order_by(
                issue_activity.c.created_at.desc(), issue_activity.c.id.desc()).limit(limit))
            for row in rows:
                row["changes"] = json.loads(row["changes"]) if row.get("changes") else None
            return rows

    def project_issue_activity(self, org_id: str, project_id: str, viewer_id: str | None = None,
                               after=None, limit=51, event_types: list[str] | None = None) -> list[dict]:
        """Events on the issues currently in a project, newest first, limited to teams the viewer can see."""
        with self.engine.connect() as conn:
            stmt = select(issue_activity).join(issue, and_(
                issue_activity.c.issue_id == issue.c.id,
                issue_activity.c.organization_id == issue.c.organization_id,
            )).where(and_(
                issue_activity.c.organization_id == org_id,
                issue.c.organization_id == org_id,
                issue.c.project_id == project_id,
            ))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            if event_types:
                stmt = stmt.where(issue_activity.c.event_type.in_(event_types))
            if after:
                ts, event_id = after
                stmt = stmt.where(issue_activity.c.created_at <= ts).where(or_(issue_activity.c.created_at < ts,
                    and_(issue_activity.c.created_at == ts, issue_activity.c.id < event_id)))
            rows = _conn_rows(conn, stmt.order_by(
                issue_activity.c.created_at.desc(), issue_activity.c.id.desc()).limit(limit))
            for row in rows:
                row["changes"] = json.loads(row["changes"]) if row.get("changes") else None
            return rows

    def issue_activity_for(self, org_id: str, issue_id: str, after=None, limit=51) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(issue_activity).where(and_(
                issue_activity.c.organization_id == org_id,
                issue_activity.c.issue_id == issue_id,
            ))
            if after:
                ts, event_id = after
                stmt = stmt.where(issue_activity.c.created_at <= ts).where(or_(issue_activity.c.created_at < ts,
                    and_(issue_activity.c.created_at == ts, issue_activity.c.id < event_id)))
            rows = _conn_rows(conn, stmt.order_by(
                issue_activity.c.created_at.desc(), issue_activity.c.id.desc()).limit(limit))
            for row in rows:
                row["changes"] = json.loads(row["changes"]) if row.get("changes") else None
            return rows

    def labels(self, org_id: str, team_id: str | None = None, name: str | None = None,
               viewer_id: str | None = None) -> list[dict]:
        """Labels in the org. With a team_id, its own plus the ones it inherits from ancestors."""
        with self.engine.connect() as conn:
            stmt = select(issue_label).where(and_(
                issue_label.c.organization_id == org_id, issue_label.c.archived_at.is_(None)))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue_label.c.team_id, nullable=True)
            if team_id:
                stmt = stmt.where(issue_label.c.team_id.in_(team_ancestor_ids(conn, org_id, team_id)))
            if name:
                stmt = stmt.where(issue_label.c.name == name)
            return [self.ser_label(r) for r in _conn_rows(conn, stmt.order_by(issue_label.c.name))]

    def issue_labels_for(self, org_id: str, issue_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(issue_label).select_from(
                issue_label.join(issue_label_link, issue_label_link.c.label_id == issue_label.c.id)
            ).where(and_(issue_label_link.c.issue_id == issue_id,
                         issue_label.c.organization_id == org_id))
            return [self.ser_label(r) for r in _conn_rows(conn, stmt)]

    # ------------------------------------------------------------ batch loaders
    # One query per list page instead of one per row (GraphQL resolvers prime them).
    def issue_labels_for_many(self, org_id: str, issue_ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {i: [] for i in issue_ids}
        with self.engine.connect() as conn:
            for start in range(0, len(issue_ids), 500):
                chunk = issue_ids[start:start + 500]
                rows = conn.execute(select(issue_label_link.c.issue_id, issue_label).select_from(
                    issue_label.join(issue_label_link, issue_label_link.c.label_id == issue_label.c.id)
                ).where(and_(issue_label_link.c.issue_id.in_(chunk),
                             issue_label.c.organization_id == org_id))).mappings().all()
                for r in rows:
                    row = dict(r)
                    out[row.pop("issue_id")].append(self.ser_label(row))
        return out

    def issue_subscribers_for_many(self, org_id: str, issue_ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {i: [] for i in issue_ids}
        with self.engine.connect() as conn:
            for start in range(0, len(issue_ids), 500):
                chunk = issue_ids[start:start + 500]
                rows = conn.execute(select(issue_subscriber.c.issue_id.label("_issue_id"), user).select_from(
                    user.join(issue_subscriber, issue_subscriber.c.user_id == user.c.id)
                ).where(and_(issue_subscriber.c.issue_id.in_(chunk),
                             user.c.organization_id == org_id))).mappings().all()
                for r in rows:
                    row = dict(r)
                    out[row.pop("_issue_id")].append(self.ser_user(row))
        return out

    def users_by_ids(self, org_id: str, user_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self.engine.connect() as conn:
            for start in range(0, len(user_ids), 500):
                chunk = user_ids[start:start + 500]
                for row in _conn_rows(conn, select(user).where(and_(
                        user.c.organization_id == org_id, user.c.id.in_(chunk)))):
                    out[row["id"]] = row
        return out

    def issues_by_ids(self, org_id: str, issue_ids: list[str], viewer_id: str | None = None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self.engine.connect() as conn:
            for start in range(0, len(issue_ids), 500):
                chunk = issue_ids[start:start + 500]
                stmt = select(issue).where(and_(issue.c.organization_id == org_id, issue.c.id.in_(chunk)))
                stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
                for row in _conn_rows(conn, stmt):
                    out[row["id"]] = self.ser_issue(row)
        return out

    def projects_by_ids(self, org_id: str, project_ids: list[str],
                        viewer_id: str | None = None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self.engine.connect() as conn:
            for start in range(0, len(project_ids), 500):
                chunk = project_ids[start:start + 500]
                stmt = select(project).where(and_(
                    project.c.organization_id == org_id, project.c.id.in_(chunk),
                    project.c.archived_at.is_(None)))
                stmt = _scope_projects(conn, stmt, org_id, viewer_id)
                for row in _conn_rows(conn, stmt):
                    out[row["id"]] = self.ser_project(row)
        return out

    def project_progress_many(self, org_id: str, project_ids: list[str]) -> dict[str, float]:
        """Percent (0-100) of each project's non-canceled, non-archived issues that are completed.

        Derived from issues on every read (one grouped query per batch) because the stored
        project.progress column is never updated after creation."""
        out: dict[str, float] = {p: 0.0 for p in project_ids}
        with self.engine.connect() as conn:
            for start in range(0, len(project_ids), 500):
                chunk = project_ids[start:start + 500]
                stmt = select(issue.c.project_id, workflow_state.c.type, func.count()).select_from(
                    issue.join(workflow_state, workflow_state.c.id == issue.c.state_id)
                ).where(and_(issue.c.organization_id == org_id, issue.c.project_id.in_(chunk),
                             issue.c.archived_at.is_(None))).group_by(issue.c.project_id, workflow_state.c.type)
                done: dict[str, int] = {}
                scope: dict[str, int] = {}
                for pid, kind, n in conn.execute(stmt):
                    if kind == "canceled":
                        continue
                    scope[pid] = scope.get(pid, 0) + n
                    if kind == "completed":
                        done[pid] = done.get(pid, 0) + n
                for pid, total in scope.items():
                    out[pid] = round(done.get(pid, 0) * 100.0 / total, 2) if total else 0.0
        return out

    def project_teams_many(self, org_id: str, project_ids: list[str],
                           viewer_id: str | None = None) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {p: [] for p in project_ids}
        with self.engine.connect() as conn:
            for start in range(0, len(project_ids), 500):
                chunk = project_ids[start:start + 500]
                stmt = select(project_team.c.project_id.label("_project_id"), team).select_from(
                    team.join(project_team, project_team.c.team_id == team.c.id)
                ).where(and_(project_team.c.project_id.in_(chunk), team.c.organization_id == org_id,
                             team.c.archived_at.is_(None)))
                stmt = _scope_teams(conn, stmt, org_id, viewer_id, team.c.id)
                for r in conn.execute(stmt).mappings().all():
                    row = dict(r)
                    out[row.pop("_project_id")].append(self.ser_team(row))
        return out

    def issue_subscribers_for(self, org_id: str, issue_id: str, limit: int | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(user).select_from(
                user.join(issue_subscriber, issue_subscriber.c.user_id == user.c.id)
            ).where(and_(issue_subscriber.c.issue_id == issue_id, user.c.organization_id == org_id))
            if limit:
                stmt = stmt.limit(limit)
            return [self.ser_user(r) for r in _conn_rows(conn, stmt)]

    def comments_for(self, org_id: str, issue_id: str, limit: int | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(comment).where(and_(
                comment.c.organization_id == org_id, comment.c.issue_id == issue_id,
                comment.c.archived_at.is_(None))).order_by(comment.c.created_at)
            if limit:
                stmt = stmt.limit(limit)
            return [self.ser_comment(r) for r in _conn_rows(conn, stmt)]

    def issue_attachments_for(self, org_id: str, issue_id: str, limit: int | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(attachment).where(and_(
                attachment.c.organization_id == org_id,
                attachment.c.issue_id == issue_id)).order_by(attachment.c.created_at)
            if limit:
                stmt = stmt.limit(limit)
            return [self.ser_attachment(r) for r in _conn_rows(conn, stmt)]

    def issue_github_links_for(self, org_id: str, issue_id: str,
                               limit: int | None = None) -> list[dict]:
        """The GitHub objects linked to one issue, newest first.

        Ordered newest-first because the panel shows the most recent activity; the
        (organization_id, kind, external_id) unique key already prevents duplicates.
        """
        with self.engine.connect() as conn:
            stmt = select(github_link).where(and_(
                github_link.c.organization_id == org_id,
                github_link.c.issue_id == issue_id)).order_by(github_link.c.created_at.desc())
            if limit:
                stmt = stmt.limit(limit)
            return [self.ser_github_link(r) for r in _conn_rows(conn, stmt)]

    def github_link_add(self, org_id: str, issue_id: str, *, kind: str,
                        external_id: str, url: str, title: str) -> dict | None:
        """Record a GitHub reference a person wrote into a task.

        Shares the (organization_id, kind, external_id) natural key with the webhook
        path, so typing the same reference twice updates one row instead of
        duplicating it. Returns None when the issue is not in this organization,
        which is also what the RLS policy would enforce at the row level.
        """
        from hoja_server.db import now_iso

        with self.engine.begin() as conn:
            owns = conn.execute(
                select(issue.c.id).where(and_(
                    issue.c.organization_id == org_id,
                    issue.c.id == issue_id)).limit(1)
            ).first()
            if not owns:
                return None
            existing = conn.execute(
                select(github_link.c.id).where(and_(
                    github_link.c.organization_id == org_id,
                    github_link.c.kind == kind,
                    github_link.c.external_id == external_id)).limit(1)
            ).first()
            stamp = now_iso()
            if existing:
                link_id = existing[0]
                conn.execute(update(github_link).where(github_link.c.id == link_id).values(
                    issue_id=issue_id, url=url, title=title, updated_at=stamp))
            else:
                link_id = f"ghl_{secrets.token_hex(12)}"
                conn.execute(github_link.insert().values(
                    id=link_id, organization_id=org_id, issue_id=issue_id, kind=kind,
                    external_id=external_id, url=url, title=title, state=None,
                    author=None, sha=None, environment=None,
                    created_at=stamp, updated_at=stamp))
            rows = _conn_rows(conn, select(github_link).where(github_link.c.id == link_id))
            return self.ser_github_link(rows[0]) if rows else None

    def upload_visible(self, org_id: str, viewer_id: str, file_id: str) -> bool:
        """May this viewer fetch an uploaded file?

        Uploads are org-wide blobs, so the file itself carries no team. When any
        attachment row points at it, the file takes the visibility of the *most*
        permissive of those issues; a file no attachment references (an avatar, an
        in-flight upload) stays readable by any member of the organization.
        """
        with self.engine.connect() as conn:
            issue_ids = list(conn.execute(select(attachment.c.issue_id).where(and_(
                attachment.c.organization_id == org_id,
                attachment.c.url.contains(file_id),
                attachment.c.issue_id.is_not(None)))).scalars())
            if not issue_ids:
                return True
            stmt = select(issue.c.id).where(and_(
                issue.c.organization_id == org_id, issue.c.id.in_(issue_ids)))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            return conn.execute(stmt).first() is not None

    def projects(self, org_id: str, flt: dict | None = None,
                 after: tuple[str, str] | None = None, limit: int | None = None,
                 viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(project).where(and_(
                project.c.organization_id == org_id, project.c.archived_at.is_(None)))
            stmt = _apply_project_filter(stmt, flt, conn, org_id)
            stmt = _scope_projects(conn, stmt, org_id, viewer_id)
            if after:
                stmt = stmt.where(project.c.created_at >= after[0]).where((project.c.created_at > after[0]) |
                                  ((project.c.created_at == after[0]) & (project.c.id > after[1])))
            stmt = stmt.order_by(project.c.created_at, project.c.id)
            if limit is not None:
                stmt = stmt.limit(limit)
            return [self.ser_project(r) for r in _conn_rows(conn, stmt)]

    def cycle_by_id(self, org_id: str, cycle_id: str, viewer_id: str | None = None) -> dict | None:
        with self.engine.connect() as conn:
            stmt = select(cycle).where(and_(
                cycle.c.organization_id == org_id, cycle.c.id == cycle_id))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, cycle.c.team_id)
            return self.ser_cycle(_one(conn, stmt))

    def project_by_id(self, org_id: str, pid: str, viewer_id: str | None = None) -> dict | None:
        with self.engine.connect() as conn:
            stmt = select(project).where(and_(
                project.c.organization_id == org_id, project.c.id == pid,
                project.c.archived_at.is_(None)))
            stmt = _scope_projects(conn, stmt, org_id, viewer_id)
            return self.ser_project(_one(conn, stmt))

    def initiatives(self, org_id: str, after: tuple[str, str] | None = None, status: str | None = None,
                    statuses: list[str] | None = None, limit: int = 51) -> list[dict]:
        stmt = select(initiative).where(and_(initiative.c.organization_id == org_id, initiative.c.archived_at.is_(None)))
        if status:
            stmt = stmt.where(initiative.c.status == status)
        if statuses:
            stmt = stmt.where(initiative.c.status.in_(statuses))
        if after:
            stmt = stmt.where(initiative.c.created_at <= after[0]).where((initiative.c.created_at < after[0]) | ((initiative.c.created_at == after[0]) & (initiative.c.id < after[1])))
        with self.engine.connect() as conn:
            rows = _conn_rows(conn, stmt.order_by(initiative.c.created_at.desc(), initiative.c.id.desc()).limit(limit))
            return [self.ser_initiative(conn, row) for row in rows]

    def initiative_by_id(self, org_id: str, ident: str) -> dict | None:
        with self.engine.connect() as conn:
            row = _one(conn, select(initiative).where(and_(initiative.c.organization_id == org_id, initiative.c.id == ident, initiative.c.archived_at.is_(None))))
            return self.ser_initiative(conn, row) if row else None

    @staticmethod
    def ser_initiative(conn, row) -> dict:
        result = {"id": row["id"], "name": row["name"], "summary": row["summary"], "status": row["status"], "priority": row["priority"], "targetDate": row["target_date"], "createdAt": row["created_at"], "updatedAt": row["updated_at"], "_owner_id": row["owner_id"], "_creator_id": row["creator_id"], "_org_id": row["organization_id"], "owner": None, "creator": None}
        for field in ("owner", "creator"):
            uid = row[f"{field}_id"]
            if uid:
                user_row = _one(conn, select(user).where(and_(user.c.organization_id == row["organization_id"], user.c.id == uid)))
                result[field] = Store.__new__(Store).ser_user(user_row) if user_row else None
        ids = list(conn.execute(select(project.c.id).select_from(project.join(initiative_project, initiative_project.c.project_id == project.c.id)).where(and_(initiative_project.c.organization_id == row["organization_id"], initiative_project.c.initiative_id == row["id"], project.c.organization_id == row["organization_id"], project.c.archived_at.is_(None)))).scalars())
        result["projects"] = {"nodes": [Store.__new__(Store).ser_project(_one(conn, select(project).where(project.c.id == pid))) for pid in ids], "pageInfo": {"hasNextPage": False, "hasPreviousPage": False, "startCursor": None, "endCursor": None}}
        return result

    def project_updates(self, org_id: str, after: tuple[str, str] | None = None,
                       project_id: str | None = None, limit: int = 51) -> list[dict]:
        """Return organization-scoped update posts newest-first, with stable id tie-breaks."""
        stmt = select(project_update).where(project_update.c.organization_id == org_id)
        stmt = stmt.join(project, and_(project.c.id == project_update.c.project_id,
                                       project.c.organization_id == org_id,
                                       project.c.archived_at.is_(None)))
        if project_id:
            stmt = stmt.where(project_update.c.project_id == project_id)
        if after:
            created_at, update_id = after
            stmt = stmt.where(project_update.c.created_at <= created_at).where(or_(project_update.c.created_at < created_at,
                                  and_(project_update.c.created_at == created_at,
                                       project_update.c.id < update_id)))
        stmt = stmt.order_by(project_update.c.created_at.desc(), project_update.c.id.desc()).limit(limit)
        with self.engine.connect() as conn:
            rows = _conn_rows(conn, stmt)
            result = []
            for row in rows:
                row["_project_id"] = row["project_id"]
                row["_actor_id"] = row.get("actor_id")
                row["createdAt"] = row["created_at"]
                result.append(row)
            return result

    def project_update_project(self, org_id: str, pid: str) -> dict | None:
        return self.project_by_id(org_id, pid)

    def project_members(self, org_id: str, project_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(user).select_from(
                user.join(project_member, project_member.c.user_id == user.c.id)
            ).where(and_(project_member.c.project_id == project_id, user.c.organization_id == org_id))
            return [self.ser_user(r) for r in _conn_rows(conn, stmt)]

    def project_teams(self, org_id: str, project_id: str, viewer_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(team).select_from(
                team.join(project_team, project_team.c.team_id == team.c.id)
            ).where(and_(
                project_team.c.project_id == project_id,
                team.c.organization_id == org_id,
                team.c.archived_at.is_(None),
            ))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, team.c.id)
            return [self.ser_team(r) for r in _conn_rows(conn, stmt)]

    def state_by_id(self, org_id: str, sid: str) -> dict | None:
        with self.engine.connect() as conn:
            return self.ser_state(_one(conn, select(workflow_state).where(and_(
                workflow_state.c.organization_id == org_id, workflow_state.c.id == sid))))

    def states_by_ids(self, org_id: str, state_ids: list[str]) -> dict[str, dict]:
        """Load workflow states for an issue page in one organization-scoped query."""
        ids = list(dict.fromkeys(sid for sid in state_ids if sid))
        if not ids:
            return {}
        with self.engine.connect() as conn:
            rows = _conn_rows(conn, select(workflow_state).where(and_(
                workflow_state.c.organization_id == org_id, workflow_state.c.id.in_(ids))))
            return {row["id"]: self.ser_state(row) for row in rows}

    def raw_team(self, org_id: str, tid: str, viewer_id: str | None = None) -> dict | None:
        with self.engine.connect() as conn:
            stmt = select(team).where(and_(team.c.organization_id == org_id, team.c.id == tid))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, team.c.id)
            return _one(conn, stmt)

    # --------------------------------------------------------------- issue list
    def issues(self, org_id: str, viewer_id: str, flt: dict | None,
               order_by: str = "updatedAt", after: tuple[str, str] | None = None,
               limit: int | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            stmt = select(issue).where(and_(
                issue.c.organization_id == org_id, issue.c.archived_at.is_(None)))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            stmt = _apply_issue_filter(conn, stmt, org_id, viewer_id, flt)
            col = issue.c.updated_at if order_by == "updatedAt" else issue.c.created_at
            stmt = stmt.order_by(col.desc(), issue.c.id.desc())
            if after:
                from sqlalchemy import or_
                stmt = stmt.where(col <= after[0]).where(or_(col < after[0], and_(col == after[0], issue.c.id < after[1])))
            if limit is not None:
                stmt = stmt.limit(limit)
            return [self.ser_issue(r) for r in _conn_rows(conn, stmt)]

    def search_issues(self, org_id: str, term: str, viewer_id: str | None = None,
                      after: tuple[str, str] | None = None,
                      limit: int | None = None) -> list[dict]:
        needle = _like_contains(_check_search_term(term))
        with self.engine.connect() as conn:
            stmt = select(issue).where(and_(
                issue.c.organization_id == org_id, issue.c.archived_at.is_(None),
                or_(issue.c.title.ilike(needle, escape="\\"),
                    issue.c.description.ilike(needle, escape="\\"),
                    issue.c.identifier.ilike(needle, escape="\\"))))
            stmt = _scope_teams(conn, stmt, org_id, viewer_id, issue.c.team_id)
            # The id tiebreak makes the order total: a page boundary keys on
            # (updated_at, id), so rows sharing a timestamp must be ordered by
            # the same column the cursor filters on or a page can drop them.
            stmt = stmt.order_by(issue.c.updated_at.desc(), issue.c.id.desc())
            if after:
                stmt = stmt.where(issue.c.updated_at <= after[0]).where(or_(
                    issue.c.updated_at < after[0],
                    and_(issue.c.updated_at == after[0], issue.c.id < after[1])))
            if limit is not None:
                stmt = stmt.limit(limit)
            return [self.ser_issue(r) for r in _conn_rows(conn, stmt)]


# ============================================================ filter compilers
def _apply_str_cmp(col, cmp: dict):
    clauses = []
    if "eq" in cmp:
        clauses.append(col == cmp["eq"])
    if "neq" in cmp:
        clauses.append(col != cmp["neq"])
    if "in" in cmp:
        clauses.append(col.in_(cmp["in"]))
    if "nin" in cmp:
        clauses.append(col.notin_(cmp["nin"]))
    if "contains" in cmp:
        clauses.append(col.ilike(_like_contains(_check_search_term(cmp["contains"])), escape="\\"))
    if "containsIgnoreCase" in cmp:
        clauses.append(col.ilike(_like_contains(_check_search_term(cmp["containsIgnoreCase"])), escape="\\"))
    if "eqIgnoreCase" in cmp:
        clauses.append(func.lower(col) == _check_search_term(cmp["eqIgnoreCase"]).lower())
    return and_(*clauses) if clauses else None


def _apply_num_cmp(col, cmp: dict):
    clauses = []
    if "eq" in cmp:
        clauses.append(col == cmp["eq"])
    if "neq" in cmp:
        clauses.append(col != cmp["neq"])
    if "gt" in cmp:
        clauses.append(col > cmp["gt"])
    if "lt" in cmp:
        clauses.append(col < cmp["lt"])
    if "gte" in cmp:
        clauses.append(col >= cmp["gte"])
    if "lte" in cmp:
        clauses.append(col <= cmp["lte"])
    if "in" in cmp:
        clauses.append(col.in_(cmp["in"]))
    if "nin" in cmp:
        clauses.append(col.notin_(cmp["nin"]))
    return and_(*clauses) if clauses else None


def _apply_date_cmp(col, cmp: dict):
    # ISO strings sort lexicographically → string comparison is correct
    clauses = []
    if "eq" in cmp:
        clauses.append(col == cmp["eq"])
    if "gt" in cmp:
        clauses.append(col > cmp["gt"])
    if "lt" in cmp:
        clauses.append(col < cmp["lt"])
    if "gte" in cmp:
        clauses.append(col >= cmp["gte"])
    if "lte" in cmp:
        clauses.append(col <= cmp["lte"])
    return and_(*clauses) if clauses else None


def _apply_project_filter(stmt, flt: dict | None, conn, org_id: str):
    if not flt:
        return stmt
    if "slugId" in flt:
        c = _apply_str_cmp(project.c.slug_id, flt["slugId"])
        if c is not None:
            stmt = stmt.where(c)
    if "name" in flt:
        c = _apply_str_cmp(project.c.name, flt["name"])
        if c is not None:
            stmt = stmt.where(c)
    if "state" in flt:
        c = _apply_str_cmp(project.c.state, flt["state"])
        if c is not None:
            stmt = stmt.where(c)
    if "team" in flt:
        team_ids = _resolve_team_ids(conn, org_id, flt["team"])
        project_ids = select(project_team.c.project_id).where(
            project_team.c.team_id.in_(team_ids or ["__none__"]))
        stmt = stmt.where(project.c.id.in_(project_ids))
    return stmt


def _resolve_team_ids(conn, org_id: str, cmp: dict) -> list[str]:
    stmt = select(team.c.id).where(team.c.organization_id == org_id)
    if "key" in cmp:
        kc = _apply_str_cmp(team.c.key, cmp["key"])
        if kc is not None:
            stmt = stmt.where(kc)
    if "id" in cmp:
        ic = _apply_str_cmp(team.c.id, cmp["id"])
        if ic is not None:
            stmt = stmt.where(ic)
    if "name" in cmp:
        nc = _apply_str_cmp(team.c.name, cmp["name"])
        if nc is not None:
            stmt = stmt.where(nc)
    return [r[0] for r in conn.execute(stmt)]


def _resolve_state_ids(conn, org_id: str, cmp: dict) -> list[str]:
    stmt = select(workflow_state.c.id).where(workflow_state.c.organization_id == org_id)
    if "name" in cmp:
        c = _apply_str_cmp(workflow_state.c.name, cmp["name"])
        if c is not None:
            stmt = stmt.where(c)
    if "type" in cmp:
        c = _apply_str_cmp(workflow_state.c.type, cmp["type"])
        if c is not None:
            stmt = stmt.where(c)
    if "team" in cmp:
        team_ids = _resolve_team_ids(conn, org_id, cmp["team"])
        stmt = stmt.where(workflow_state.c.team_id.in_(team_ids or ["__none__"]))
    return [r[0] for r in conn.execute(stmt)]


def _resolve_user_ids(conn, org_id: str, cmp: dict) -> list[str]:
    stmt = select(user.c.id).where(user.c.organization_id == org_id)
    if "id" in cmp:
        c = _apply_str_cmp(user.c.id, cmp["id"])
        if c is not None:
            stmt = stmt.where(c)
    if "email" in cmp:
        c = _apply_str_cmp(user.c.email, cmp["email"])
        if c is not None:
            stmt = stmt.where(c)
    if "displayName" in cmp:
        c = _apply_str_cmp(user.c.display_name, cmp["displayName"])
        if c is not None:
            stmt = stmt.where(c)
    return [r[0] for r in conn.execute(stmt)]


def _resolve_cycle_ids(conn, org_id: str, cmp: dict) -> list[str] | None:
    """Resolve active/next cycle booleans; return None when neither is requested."""
    requested = {"isActive", "isNext"}.intersection(cmp)
    if not requested:
        return None
    all_ids = set(
        conn.execute(
            select(cycle.c.id).where(cycle.c.organization_id == org_id)
        ).scalars()
    )
    matches: set[str] | None = None
    team_rows = conn.execute(
        select(team.c.id, team.c.active_cycle_id).where(
            team.c.organization_id == org_id
        )
    ).all()
    for field in requested:
        positive: set[str] = set()
        if field == "isActive":
            positive = {row[1] for row in team_rows if row[1]}
        else:
            for team_id, active_cycle_id in team_rows:
                rows = conn.execute(
                    select(cycle.c.id)
                    .where(
                        and_(
                            cycle.c.organization_id == org_id,
                            cycle.c.team_id == team_id,
                        )
                    )
                    .order_by(cycle.c.number)
                ).scalars().all()
                if not rows:
                    continue
                if active_cycle_id in rows:
                    index = rows.index(active_cycle_id) + 1
                    if index < len(rows):
                        positive.add(rows[index])
                elif not active_cycle_id:
                    positive.add(rows[0])
        comparator = cmp[field] or {}
        if "eq" in comparator:
            expected = bool(comparator["eq"])
        elif "neq" in comparator:
            expected = not bool(comparator["neq"])
        else:
            continue
        field_matches = positive if expected else all_ids - positive
        matches = field_matches if matches is None else matches.intersection(field_matches)
    return sorted(matches or [])


def _apply_issue_filter(conn, stmt, org_id, viewer_id, flt: dict | None):
    if not flt:
        return stmt
    if "team" in flt:
        ids = _resolve_team_ids(conn, org_id, flt["team"])
        stmt = stmt.where(issue.c.team_id.in_(ids or ["__none__"]))
    if "state" in flt:
        ids = _resolve_state_ids(conn, org_id, flt["state"])
        stmt = stmt.where(issue.c.state_id.in_(ids or ["__none__"]))
    for who, col in (("assignee", issue.c.assignee_id), ("creator", issue.c.creator_id)):
        if who in flt:
            ids = _resolve_user_ids(conn, org_id, flt[who])
            stmt = stmt.where(col.in_(ids or ["__none__"]))
    if "project" in flt:
        cmp = flt["project"]
        if "id" in cmp:
            c = _apply_str_cmp(issue.c.project_id, cmp["id"])
            if c is not None:
                stmt = stmt.where(c)
        elif "name" in cmp:
            sub = select(project.c.id).where(project.c.organization_id == org_id)
            nc = _apply_str_cmp(project.c.name, cmp["name"])
            if nc is not None:
                sub = sub.where(nc)
            stmt = stmt.where(issue.c.project_id.in_([r[0] for r in conn.execute(sub)] or ["__none__"]))
    if "cycle" in flt:
        cycle_filter = flt["cycle"]
        sub = select(cycle.c.id).where(cycle.c.organization_id == org_id)
        for field, column in (("id", cycle.c.id), ("name", cycle.c.name)):
            if field in cycle_filter:
                clause = _apply_str_cmp(column, cycle_filter[field])
                if clause is not None:
                    sub = sub.where(clause)
        cycle_ids = {row[0] for row in conn.execute(sub)}
        boolean_ids = _resolve_cycle_ids(conn, org_id, cycle_filter)
        if boolean_ids is not None:
            cycle_ids.intersection_update(boolean_ids)
        stmt = stmt.where(issue.c.cycle_id.in_(cycle_ids or ["__none__"]))
    if "parent" in flt:
        cmp = flt["parent"] or {}
        # `isNull` is a Hoja extension: GraphQL cannot express `eq: null`, and
        # clients strip nulls from variables, so orphans need a boolean.
        if cmp.get("isNull") is True:
            stmt = stmt.where(issue.c.parent_id.is_(None))
        elif cmp.get("isNull") is False:
            stmt = stmt.where(issue.c.parent_id.is_not(None))
        if "id" in cmp:
            c = _apply_str_cmp(issue.c.parent_id, cmp["id"])
            if c is not None:
                stmt = stmt.where(c)
    if "priority" in flt:
        c = _apply_num_cmp(issue.c.priority, flt["priority"])
        if c is not None:
            stmt = stmt.where(c)
    if "number" in flt:
        c = _apply_num_cmp(issue.c.number, flt["number"])
        if c is not None:
            stmt = stmt.where(c)
    if "identifier" in flt:
        c = _apply_str_cmp(issue.c.identifier, flt["identifier"])
        if c is not None:
            stmt = stmt.where(c)
    if "title" in flt:
        c = _apply_str_cmp(issue.c.title, flt["title"])
        if c is not None:
            stmt = stmt.where(c)
    if "description" in flt:
        c = _apply_str_cmp(issue.c.description, flt["description"])
        if c is not None:
            stmt = stmt.where(c)
    for datef, col in (("updatedAt", issue.c.updated_at), ("createdAt", issue.c.created_at),
                       ("dueDate", issue.c.due_date)):
        if datef in flt:
            c = _apply_date_cmp(col, flt[datef])
            if c is not None:
                stmt = stmt.where(c)
    if "labels" in flt and "name" in flt["labels"]:
        sub = select(issue_label_link.c.issue_id).select_from(
            issue_label_link.join(issue_label, issue_label.c.id == issue_label_link.c.label_id)
        ).where(issue_label.c.organization_id == org_id)
        nc = _apply_str_cmp(issue_label.c.name, flt["labels"]["name"])
        if nc is not None:
            sub = sub.where(nc)
        stmt = stmt.where(issue.c.id.in_([r[0] for r in conn.execute(sub)] or ["__none__"]))
    for sub_filter in flt.get("and") or []:
        stmt = _apply_issue_filter(conn, stmt, org_id, viewer_id, sub_filter)
    if flt.get("or"):
        alternatives = []
        for sub_filter in flt["or"]:
            subquery = select(issue.c.id).where(and_(
                issue.c.organization_id == org_id,
                issue.c.archived_at.is_(None),
            ))
            subquery = _apply_issue_filter(
                conn, subquery, org_id, viewer_id, sub_filter
            )
            alternatives.append(issue.c.id.in_(subquery))
        stmt = stmt.where(or_(*alternatives))
    return stmt
