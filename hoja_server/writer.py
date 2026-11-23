"""Write operations with transactional identifiers and derived fields."""
from __future__ import annotations

import json
import re
import logging
from urllib.parse import urlparse

from graphql import GraphQLError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Engine

from hoja_server import comment_policy, identifiers, notifications, uploads
from hoja_server import decisions as decisiondocs
from hoja_server import views as viewdocs
from hoja_server.db import (
    cycle_subscriber,
    PRIORITY_LABELS,
    attachment,
    comment,
    cycle,
    entity_url,
    inbox_notification,
    initiative,
    initiative_label,
    initiative_label_link,
    initiative_parent,
    initiative_project,
    initiative_update,
    initiative_update_reaction,
    issue,
    issue_activity,
    issue_decision,
    issue_label,
    issue_label_link,
    issue_subscriber,
    org_links,
    new_id,
    now_iso,
    organization,
    project,
    project_member,
    project_team,
    project_template,
    project_template_team,
    project_update,
    saved_view,
    saved_view_favorite,
    seed_states_for_team,
    team,
    team_member,
    user,
    workflow_state,
)
from hoja_server.store import (
    PUBLIC,
    TEAM_ACCESS_LEVELS,
    _scope_projects,
    team_ancestor_ids,
    team_descendant_ids,
    team_visible,
)

log = logging.getLogger(__name__)


def _slug(text: str, n: int = 24) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:n].strip("-")


class InvalidReferenceError(GraphQLError):
    def __init__(self, field: str) -> None:
        super().__init__(
            "Invalid reference",
            extensions={"code": "BAD_USER_INPUT", "field": field},
        )


def _require_org_reference(conn, table, org_id: str, value: str, field: str, *extra) -> str:
    conditions = [
        table.c.id == value,
        table.c.organization_id == org_id,
        *extra,
    ]
    if "archived_at" in table.c:
        conditions.append(table.c.archived_at.is_(None))
    if not conn.execute(select(table.c.id).where(and_(*conditions))).first():
        raise InvalidReferenceError(field)
    return value


def _require_team_user(conn, org_id: str, team_id: str, value: str, field: str) -> str:
    row = conn.execute(
        select(user.c.id)
        .join(team_member, team_member.c.user_id == user.c.id)
        .where(
            and_(
                user.c.id == value,
                user.c.organization_id == org_id,
                user.c.active.is_(True),
                user.c.archived_at.is_(None),
                team_member.c.team_id == team_id,
            )
        )
    ).first()
    if not row:
        raise InvalidReferenceError(field)
    return value


def _validate_issue_references(
    conn,
    org_id: str,
    team_id: str,
    inp: dict,
    *,
    issue_id: str | None = None,
    default_state_id: str | None = None,
) -> str | None:
    """Validate issue references and return the normalized internal parent id."""
    state_id = inp.get("stateId") if "stateId" in inp else default_state_id
    if state_id:
        _require_org_reference(
            conn, workflow_state, org_id, state_id, "stateId",
            workflow_state.c.team_id == team_id,
        )
    if assignee_id := inp.get("assigneeId"):
        _require_team_user(conn, org_id, team_id, assignee_id, "assigneeId")
    # Delegation is cross-tenant-safe by construction: the predicate is scoped to
    # this organization, so an agent member of another workspace never matches —
    # even when the caller knows its id.
    if delegate_id := inp.get("delegateId"):
        from hoja_server import agent_members

        if not agent_members.is_delegate_target(conn, org_id, delegate_id):
            raise InvalidReferenceError("delegateId")
    if project_id := inp.get("projectId"):
        _require_org_reference(conn, project, org_id, project_id, "projectId")
        project_teams = list(
            conn.execute(
                select(project_team.c.team_id).where(
                    project_team.c.project_id == project_id
                )
            ).scalars()
        )
        if project_teams and team_id not in project_teams:
            raise InvalidReferenceError("projectId")
    if cycle_id := inp.get("cycleId"):
        _require_org_reference(
            conn, cycle, org_id, cycle_id, "cycleId", cycle.c.team_id == team_id
        )
    parent_id = None
    if supplied_parent_id := inp.get("parentId"):
        parent = conn.execute(select(issue.c.id).where(and_(
            issue.c.organization_id == org_id,
            issue.c.team_id == team_id,
            issue.c.archived_at.is_(None),
            # A retired flat identifier resolves through the alias ledger too,
            # so a caller can parent a child under HOJA-517 after it has been
            # renumbered to its recursive form.
            identifiers.issue_ref_clause(org_id, supplied_parent_id),
        ))).first()
        if not parent:
            raise InvalidReferenceError("parentId")
        parent_id = parent[0]
        if parent_id == issue_id:
            raise InvalidReferenceError("parentId")
        if issue_id is not None:
            # Reparenting below any descendant would make parent traversal cyclic.
            ancestor_id, seen = parent_id, set()
            while ancestor_id:
                if ancestor_id == issue_id or ancestor_id in seen:
                    raise InvalidReferenceError("parentId")
                seen.add(ancestor_id)
                ancestor_id = conn.execute(select(issue.c.parent_id).where(and_(
                    issue.c.organization_id == org_id, issue.c.id == ancestor_id,
                ))).scalar()
    label_ids = inp.get("labelIds") if "labelIds" in inp else None
    if label_ids:
        expected = set(label_ids)
        applicable_teams = team_ancestor_ids(conn, org_id, team_id)
        found = set(
            conn.execute(
                select(issue_label.c.id).where(
                    and_(
                        issue_label.c.id.in_(expected),
                        issue_label.c.organization_id == org_id,
                        issue_label.c.archived_at.is_(None),
                        or_(
                            issue_label.c.team_id.is_(None),
                            # A sub-team may use its own labels and every ancestor's.
                            issue_label.c.team_id.in_(applicable_teams),
                        ),
                    )
                )
            ).scalars()
        )
        if found != expected:
            raise InvalidReferenceError("labelIds")
    return parent_id


class Writer:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def workspace_create(self, actor_org_id: str, actor_id: str, inp: dict) -> dict:
        """Create a workspace and enroll its creating identity as admin."""
        from hoja_server.db import organization_membership

        name = inp.get("name")
        raw_key = inp.get("urlKey")
        if not isinstance(name, str) or not name.strip():
            raise GraphQLError("Workspace name is required", extensions={"code": "BAD_USER_INPUT", "field": "name"})
        key = raw_key.strip().lower() if isinstance(raw_key, str) else ""
        if not key or len(key) > 48 or not key.replace("-", "").isalnum() or key.startswith("-") or key.endswith("-") or "--" in key:
            raise GraphQLError("Workspace key must contain letters, numbers, and hyphens", extensions={"code": "BAD_USER_INPUT", "field": "urlKey"})
        ts, org_id, profile_id = now_iso(), new_id(), new_id()
        with self.engine.begin() as conn:
            actor = conn.execute(select(user).where(and_(
                user.c.id == actor_id, user.c.organization_id == actor_org_id,
                user.c.active.is_(True), user.c.archived_at.is_(None),
                user.c.admin.is_(True),
            ))).mappings().first()
            if not actor:
                raise GraphQLError("Admin access required", extensions={"code": "FORBIDDEN"})
            if conn.execute(select(organization.c.id).where(
                func.lower(organization.c.url_key) == key
            )).first():
                raise GraphQLError("Workspace key already exists", extensions={"code": "BAD_USER_INPUT", "field": "urlKey"})
            member = conn.execute(select(organization_membership.c.identity_id).where(and_(
                organization_membership.c.organization_id == actor_org_id,
                organization_membership.c.profile_user_id == actor_id,
                organization_membership.c.active.is_(True),
            ))).first()
            if not member:
                raise GraphQLError("Workspace identity is unavailable", extensions={"code": "FORBIDDEN"})
            conn.execute(organization.insert().values(
                id=org_id, name=name.strip(), url_key=key, created_at=ts, updated_at=ts,
            ))
            conn.execute(user.insert().values(
                id=profile_id, organization_id=org_id, name=actor.get("name"),
                display_name=actor.get("display_name") or actor.get("name"),
                email=actor.get("email"), active=True, admin=True,
                timezone=actor.get("timezone") or "UTC", created_at=ts, updated_at=ts,
            ))
            conn.execute(organization_membership.insert().values(
                id=new_id(), identity_id=member[0], organization_id=org_id,
                profile_user_id=profile_id, active=True, created_at=ts,
            ))
        return {"id": org_id, "name": name.strip(), "urlKey": key,
                "_profile_user_id": profile_id, "_identity_id": member[0]}

    # --------------------------------------------------------------- teams
    def team_create(self, org_id: str, viewer_id: str, inp: dict) -> str:
        name, key = inp.get("name"), inp.get("key")
        if not isinstance(name, str) or not name.strip():
            raise GraphQLError("Team name is required", extensions={"code": "BAD_USER_INPUT", "field": "name"})
        if not isinstance(key, str) or not key.strip() or not key.strip().isalnum():
            raise GraphQLError("Team key must be alphanumeric", extensions={"code": "BAD_USER_INPUT", "field": "key"})
        key = key.strip().upper()
        # HOJA-490: `team.create` is a capability, not a role check, so a
        # workspace can let a member create teams without making them an
        # admin. Checked before the write transaction opens, because the grant
        # lookup uses its own connection.
        from hoja_server import permissions as permission_rules
        if not permission_rules.can_for_engine(self.engine, org_id, viewer_id, "team.create"):
            raise GraphQLError(
                "Your workspace role does not allow this: Create teams.",
                extensions={"code": "FORBIDDEN", "capability": "team.create"},
            )
        with self.engine.begin() as conn:
            if conn.execute(select(team.c.id).where(and_(
                team.c.organization_id == org_id, team.c.key == key
            ))).first():
                raise GraphQLError("Team key already exists", extensions={"code": "BAD_USER_INPUT", "field": "key"})
            parent_team_id = inp.get("parentTeamId") or None
            if parent_team_id:
                parent_team_id = self._team_row(conn, org_id, parent_team_id)[0]
                self._validate_parent(conn, org_id, None, parent_team_id)
            visibility = self._validate_visibility(inp.get("visibility") or PUBLIC)
            ts, tid = now_iso(), new_id()
            conn.execute(team.insert().values(
                id=tid, organization_id=org_id, name=name.strip(), key=key,
                description=inp.get("description"), color=inp.get("color"), icon=inp.get("icon"),
                visibility=visibility, parent_team_id=parent_team_id,
                timezone=inp.get("timezone") or "UTC",
                issue_counter=0, created_at=ts, updated_at=ts,
            ))
            default_state_id = seed_states_for_team(conn, org_id, tid)
            conn.execute(team.update().where(team.c.id == tid).values(
                default_state_id=default_state_id
            ))
            # The creator owns the team and, by the cascade rule, every ancestor.
            self._seat_member(conn, org_id, tid, viewer_id, role="owner")
            for member_id in dict.fromkeys(inp.get("memberIds") or []):
                if member_id == viewer_id:
                    continue
                if not conn.execute(select(user.c.id).where(and_(
                    user.c.id == member_id, user.c.organization_id == org_id,
                    user.c.active.is_(True), user.c.archived_at.is_(None)))).first():
                    raise InvalidReferenceError("memberIds")
                self._seat_member(conn, org_id, tid, member_id)
            return tid

    def _seat_member(self, conn, org_id: str, team_id: str, user_id: str, *, role: str = "member") -> None:
        """Seat someone on a team and, by the cascade rule, on every ancestor (idempotent)."""
        for ancestor in team_ancestor_ids(conn, org_id, team_id):
            exists = conn.execute(select(team_member.c.user_id).where(and_(
                team_member.c.team_id == ancestor, team_member.c.user_id == user_id))).first()
            if not exists:
                conn.execute(team_member.insert().values(
                    team_id=ancestor, user_id=user_id,
                    role=role if ancestor == team_id else "member"))

    def _require_team_admin(self, conn, org_id: str, viewer_id: str) -> None:
        admin = conn.execute(select(user.c.id).where(and_(
            user.c.id == viewer_id, user.c.organization_id == org_id,
            user.c.active.is_(True), user.c.archived_at.is_(None), user.c.admin.is_(True)
        ))).first()
        if not admin:
            raise GraphQLError("Admin access required", extensions={"code": "FORBIDDEN"})

    def _require_creator_or_admin(self, conn, org_id: str, viewer_id: str, creator_id: str | None) -> None:
        if creator_id and creator_id == viewer_id:
            return
        self._require_team_admin(conn, org_id, viewer_id)

    def _require_team_visible(self, conn, org_id: str, viewer_id: str, team_id: str | None) -> None:
        """Private teams are writable only by their members (or admins); never leak existence."""
        if team_id and not team_visible(conn, org_id, viewer_id, team_id):
            raise InvalidReferenceError("teamId")

    def _team_row(self, conn, org_id: str, team_id: str, *, include_archived: bool = False):
        clauses = [team.c.id == team_id, team.c.organization_id == org_id]
        if not include_archived:
            clauses.append(team.c.archived_at.is_(None))
        row = conn.execute(select(team).where(and_(*clauses))).first()
        if not row:
            raise GraphQLError("Team not found", extensions={"code": "BAD_USER_INPUT", "field": "id"})
        return row

    # ----------------------------------------------------------- departments
    MAX_TEAM_DEPTH = 3

    @staticmethod
    def _team_depth(conn, org_id: str, team_id: str | None) -> int:
        """1 for a root team, 2 for its child, 3 for a grandchild."""
        depth, current, seen = 0, team_id, set()
        while current and current not in seen:
            seen.add(current)
            depth += 1
            current = conn.execute(select(team.c.parent_team_id).where(and_(
                team.c.id == current, team.c.organization_id == org_id))).scalar()
        return depth

    @classmethod
    def _subtree_height(cls, conn, org_id: str, team_id: str) -> int:
        """How many levels live at or below this team (1 when it has no children)."""
        children = list(conn.execute(select(team.c.id).where(and_(
            team.c.organization_id == org_id, team.c.parent_team_id == team_id))).scalars())
        if not children:
            return 1
        return 1 + max(cls._subtree_height(conn, org_id, child) for child in children)

    def _validate_parent(self, conn, org_id: str, team_id: str | None, parent_team_id: str | None) -> None:
        """Reject self-parenting, cycles and any shape deeper than MAX_TEAM_DEPTH."""
        if not parent_team_id:
            return
        parent = self._team_row(conn, org_id, parent_team_id)
        if team_id is None:
            # Creating a team under this parent: the new team is one level deeper.
            new_depth = self._team_depth(conn, org_id, parent_team_id) + 1
            if new_depth > self.MAX_TEAM_DEPTH:
                raise GraphQLError(
                    f"Departments can nest at most {self.MAX_TEAM_DEPTH} levels deep (max depth {self.MAX_TEAM_DEPTH})",
                    extensions={"code": "BAD_USER_INPUT", "field": "parentTeamId"})
            return
        if parent_team_id == team_id:
            raise GraphQLError("A team cannot be its own parent",
                               extensions={"code": "BAD_USER_INPUT", "field": "parentTeamId"})
        # Walk up from the proposed parent: reaching the team means a cycle.
        current, seen = parent_team_id, set()
        while current and current not in seen:
            if current == team_id:
                raise GraphQLError(
                    "A team cannot be nested under itself or one of its descendants (cycle)",
                    extensions={"code": "BAD_USER_INPUT", "field": "parentTeamId"})
            seen.add(current)
            current = conn.execute(select(team.c.parent_team_id).where(and_(
                team.c.id == current, team.c.organization_id == org_id))).scalar()
        new_depth = (self._team_depth(conn, org_id, parent_team_id)
                     + self._subtree_height(conn, org_id, team_id))
        if new_depth > self.MAX_TEAM_DEPTH:
            raise GraphQLError(
                f"Departments can nest at most {self.MAX_TEAM_DEPTH} levels deep (max depth {self.MAX_TEAM_DEPTH})",
                extensions={"code": "BAD_USER_INPUT", "field": "parentTeamId"})
        if not parent:
            raise InvalidReferenceError("parentTeamId")

    @staticmethod
    def _validate_visibility(value) -> str:
        if value not in TEAM_ACCESS_LEVELS:
            raise GraphQLError("visibility must be one of public, restricted, private",
                               extensions={"code": "BAD_USER_INPUT", "field": "visibility"})
        return value

    def _apply_reparent(self, conn, org_id: str, team_id: str, previous_parent_id: str | None) -> None:
        """Side effects of moving a team in or out of a department.

        Nesting: every member of the moved subtree also joins the new ancestors, the
        in-flight cycle is closed, and the not-yet-started cycles take the boundaries
        of the nearest parent cycle so the sub-team reports on the department rhythm.
        Un-nesting: labels the team was using through its old ancestors become its own
        copies when issues still use them, and the unused ones simply stop applying.
        """
        if previous_parent_id:
            self._release_inherited_labels(conn, org_id, team_id, previous_parent_id)
        new_parent_id = conn.execute(select(team.c.parent_team_id).where(
            team.c.id == team_id)).scalar()
        if not new_parent_id:
            return
        subtree = [team_id, *team_descendant_ids(conn, org_id, team_id)[1:]]
        ancestors = team_ancestor_ids(conn, org_id, new_parent_id)
        for member_id in list(conn.execute(select(team_member.c.user_id).where(
                team_member.c.team_id.in_(subtree))).scalars()):
            for ancestor in ancestors:
                if not conn.execute(select(team_member.c.user_id).where(and_(
                        team_member.c.team_id == ancestor,
                        team_member.c.user_id == member_id))).first():
                    conn.execute(team_member.insert().values(
                        team_id=ancestor, user_id=member_id, role="member"))
        for tid in subtree:
            self._align_cycles_with_parent(conn, org_id, tid)

    def _release_inherited_labels(self, conn, org_id: str, team_id: str, old_parent_id: str) -> None:
        """Turn labels used through the old ancestors into team-owned copies."""
        ancestors = team_ancestor_ids(conn, org_id, old_parent_id)
        inherited = conn.execute(select(
            issue_label.c.id, issue_label.c.name, issue_label.c.color, issue_label.c.description,
        ).where(and_(
            issue_label.c.organization_id == org_id,
            issue_label.c.team_id.in_(ancestors),
            issue_label.c.archived_at.is_(None),
        ))).all()
        for label_id, name, color, description in inherited:
            used = conn.execute(select(issue_label_link.c.issue_id).select_from(
                issue_label_link.join(issue, issue.c.id == issue_label_link.c.issue_id)
            ).where(and_(issue_label_link.c.label_id == label_id,
                         issue.c.team_id == team_id))).scalars().all()
            if not used:
                continue  # never used here: dropping out of the department is enough
            ts, copy_id = now_iso(), new_id()
            conn.execute(issue_label.insert().values(
                id=copy_id, organization_id=org_id, team_id=team_id, name=name,
                color=color, description=description, created_at=ts, updated_at=ts))
            conn.execute(issue_label_link.update().where(and_(
                issue_label_link.c.label_id == label_id,
                issue_label_link.c.issue_id.in_(used),
            )).values(label_id=copy_id))

    @staticmethod
    def _align_cycles_with_parent(conn, org_id: str, team_id: str) -> None:
        """Close the in-flight cycle and snap upcoming ones to the nearest ancestor cycle."""
        ts = now_iso()
        active_id = conn.execute(select(team.c.active_cycle_id).where(
            team.c.id == team_id)).scalar()
        if active_id:
            conn.execute(cycle.update().where(cycle.c.id == active_id).values(
                completed_at=ts, updated_at=ts))
            conn.execute(team.update().where(team.c.id == team_id).values(
                active_cycle_id=None, updated_at=ts))
        parent_id = conn.execute(select(team.c.parent_team_id).where(
            team.c.id == team_id)).scalar()
        if not parent_id:
            return
        parent_cycles = {
            number: (starts_at, ends_at, boundary_at)
            for number, starts_at, ends_at, boundary_at in conn.execute(
                select(cycle.c.number, cycle.c.starts_at, cycle.c.ends_at, cycle.c.cadence_boundary_at)
                .where(and_(cycle.c.organization_id == org_id, cycle.c.team_id == parent_id,
                            cycle.c.completed_at.is_(None))))
        }
        if not parent_cycles:
            return
        upcoming = conn.execute(select(cycle.c.id, cycle.c.number).where(and_(
            cycle.c.organization_id == org_id, cycle.c.team_id == team_id,
            cycle.c.completed_at.is_(None), cycle.c.id != (active_id or "")))).all()
        for cycle_id, number in upcoming:
            nearest = min(parent_cycles, key=lambda n: abs((n or 0) - (number or 0)))
            starts_at, ends_at, boundary_at = parent_cycles[nearest]
            conn.execute(cycle.update().where(cycle.c.id == cycle_id).values(
                starts_at=starts_at, ends_at=ends_at, cadence_boundary_at=boundary_at,
                updated_at=ts))

    def team_update(self, org_id: str, viewer_id: str, team_id: str, inp: dict) -> str:
        values: dict = {}
        if "name" in inp:
            if not isinstance(inp["name"], str) or not inp["name"].strip():
                raise GraphQLError("Team name is required", extensions={"code": "BAD_USER_INPUT", "field": "name"})
            values["name"] = inp["name"].strip()
        if "key" in inp:
            key = inp["key"]
            if not isinstance(key, str) or not key.strip() or not key.strip().isalnum():
                raise GraphQLError("Team key must be alphanumeric", extensions={"code": "BAD_USER_INPUT", "field": "key"})
            values["key"] = key.strip().upper()
        if "description" in inp:
            values["description"] = inp["description"]
        for key, column in (("autoAddStarted", "auto_add_started"),
                            ("autoAddCompleted", "auto_add_completed"),
                            ("autoAddBacklog", "auto_add_backlog")):
            if key in inp:
                if not isinstance(inp[key], bool):
                    raise GraphQLError(f"{key} must be a boolean", extensions={"code": "BAD_USER_INPUT", "field": key})
                values[column] = inp[key]
        if "visibility" in inp:
            values["visibility"] = self._validate_visibility(inp["visibility"])
        with self.engine.begin() as conn:
            self._require_team_admin(conn, org_id, viewer_id)
            self._team_row(conn, org_id, team_id)
            if "key" in values and conn.execute(select(team.c.id).where(and_(
                team.c.organization_id == org_id, team.c.key == values["key"], team.c.id != team_id
            ))).first():
                raise GraphQLError("Team key already exists", extensions={"code": "BAD_USER_INPUT", "field": "key"})
            reparented = False
            if "parentTeamId" in inp:
                parent_team_id = inp["parentTeamId"] or None
                previous = conn.execute(select(team.c.parent_team_id).where(
                    team.c.id == team_id)).scalar()
                self._validate_parent(conn, org_id, team_id, parent_team_id)
                values["parent_team_id"] = parent_team_id
                reparented = parent_team_id != previous
            values["updated_at"] = now_iso()
            conn.execute(team.update().where(team.c.id == team_id).values(**values))
            if reparented:
                self._apply_reparent(conn, org_id, team_id, previous)
            return team_id

    def team_archive(self, org_id: str, viewer_id: str, team_id: str) -> bool:
        with self.engine.begin() as conn:
            self._require_team_admin(conn, org_id, viewer_id)
            self._team_row(conn, org_id, team_id)
            conn.execute(team.update().where(team.c.id == team_id).values(archived_at=now_iso(), updated_at=now_iso()))
            return True

    def team_delete(self, org_id: str, viewer_id: str, team_id: str, *, delete_issues: bool = False) -> bool:
        with self.engine.begin() as conn:
            self._require_team_admin(conn, org_id, viewer_id)
            self._team_row(conn, org_id, team_id, include_archived=True)
            blockers = []
            # A plain delete is the conservative path and refuses while the team still owns
            # anything. deleteIssues=True is the explicit "delete this team and its work"
            # path, so it also takes the team-owned relations instead of dead-ending on them.
            if not delete_issues:
                for label, table in (("projects", project_team), ("cycles", cycle)):
                    if conn.execute(select(table.c.team_id).where(table.c.team_id == team_id).limit(1)).first():
                        blockers.append(label)
                if conn.execute(select(issue.c.id).where(issue.c.team_id == team_id).limit(1)).first():
                    blockers.append("issues")
            if blockers:
                raise GraphQLError(
                    f"Team still has {', '.join(blockers)}. Archive it instead, or move or delete them first.",
                    extensions={"code": "BAD_USER_INPUT", "field": "id"})
            if delete_issues:
                issue_ids = select(issue.c.id).where(issue.c.team_id == team_id)
                for child in (attachment, comment, inbox_notification, issue_activity,
                              issue_label_link, issue_subscriber):
                    conn.execute(child.delete().where(child.c.issue_id.in_(issue_ids)))
                conn.execute(issue.delete().where(issue.c.team_id == team_id))
            # Cycles belong to their team, so they go with it; any left-over reference is
            # cleared rather than left dangling.
            team_cycle_ids = select(cycle.c.id).where(cycle.c.team_id == team_id)
            conn.execute(issue.update().where(issue.c.cycle_id.in_(team_cycle_ids))
                         .values(cycle_id=None, updated_at=now_iso()))
            conn.execute(inbox_notification.delete().where(
                inbox_notification.c.cycle_id.in_(team_cycle_ids)))
            conn.execute(cycle_subscriber.delete().where(
                cycle_subscriber.c.cycle_id.in_(team_cycle_ids)))
            conn.execute(cycle.delete().where(cycle.c.team_id == team_id))
            # Projects are workspace-level: deleting a team only drops its link to them.
            conn.execute(project_team.delete().where(project_team.c.team_id == team_id))
            conn.execute(team_member.delete().where(team_member.c.team_id == team_id))
            conn.execute(issue_label.delete().where(issue_label.c.team_id == team_id))
            conn.execute(workflow_state.delete().where(workflow_state.c.team_id == team_id))
            conn.execute(team.delete().where(team.c.id == team_id))
            return True

    def enable_team_cadence(self, org_id: str, team_id: str, weekday: int, *, now=None) -> dict:
        """Enable once and reconcile the fixed one-week, two-upcoming policy."""
        from datetime import datetime, timezone

        from hoja_server.cadence import reconcile_team_cadence, validate_timezone
        if type(weekday) is not int or weekday not in range(7):
            raise ValueError("Choose a weekday from Monday (0) through Sunday (6).")
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("Cadence clock must be timezone-aware.")
        with self.engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            row = conn.execute(select(team).where(and_(team.c.organization_id == org_id,
                team.c.id == team_id, team.c.archived_at.is_(None))).with_for_update()).mappings().first()
            if not row:
                raise ValueError("Team not found in this workspace.")
            if row["cadence_enabled"]:
                if row["cadence_weekday"] != weekday:
                    raise ValueError("Changing a cycle schedule is not supported yet.")
                return reconcile_team_cadence(conn, org_id, row, instant)
            existing = conn.execute(select(cycle.c.id).where(and_(cycle.c.organization_id == org_id,
                cycle.c.team_id == team_id, cycle.c.archived_at.is_(None)))).first()
            if existing:
                raise ValueError("This team already has cycles; reconcile existing cycles before enabling cadence.")
            validate_timezone(row.get("timezone") or "UTC")
            updated = conn.execute(team.update().where(and_(team.c.id == team_id,
                team.c.organization_id == org_id, team.c.cadence_enabled.is_(False)))
                .values(cadence_enabled=True, cadence_weekday=weekday,
                        cadence_anchor_at=instant.astimezone(timezone.utc).isoformat(),
                        cadence_policy_version=1, updated_at=now_iso()))
            if updated.rowcount != 1:
                raise ValueError("Cycle settings changed; reload before retrying.")
            fresh = conn.execute(select(team).where(team.c.id == team_id)).mappings().one()
            return reconcile_team_cadence(conn, org_id, fresh, instant)

    def reconcile_team_cadence(self, org_id: str, team_id: str, *, now=None, viewer_id: str | None = None) -> dict:
        from datetime import datetime, timezone

        from hoja_server.cadence import reconcile_team_cadence, validate_timezone
        instant = now or datetime.now(timezone.utc)
        # Read paths (teams/activeCycle/cycles) call this on every request. Without a
        # cadence there is nothing to reconcile, so answer without the write lock
        # (BEGIN IMMEDIATE on SQLite serializes every page load with every write).
        with self.engine.connect() as conn:
            peek = conn.execute(select(team.c.cadence_enabled, team.c.timezone).where(and_(
                team.c.organization_id == org_id, team.c.id == team_id,
                team.c.archived_at.is_(None)))).first()
            if peek is not None and not peek.cadence_enabled:
                if viewer_id is not None:
                    self._require_team_visible(conn, org_id, viewer_id, team_id)
                validate_timezone(peek.timezone or "UTC")
                return {"enabled": False, "weekday": None, "timezone": peek.timezone or "UTC",
                        "policyVersion": 1, "activeCycle": None, "upcomingCycles": []}
        with self.engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            row = conn.execute(select(team).where(and_(team.c.organization_id == org_id,
                team.c.id == team_id, team.c.archived_at.is_(None))).with_for_update()).mappings().first()
            if not row:
                raise ValueError("Team not found in this workspace.")
            if viewer_id is not None:
                self._require_team_visible(conn, org_id, viewer_id, team_id)
            return reconcile_team_cadence(conn, org_id, row, instant)

    def initiative_create(self, org_id: str, actor_id: str, inp: dict) -> str:
        name, status, priority = str(inp.get("name", "")).strip(), inp.get("status", "planned"), inp.get("priority", 0)
        if not name or status not in {"active", "planned", "completed", "canceled"} or not isinstance(priority, int) or priority not in range(0, 5):
            raise ValueError("Invalid initiative name, status, or priority.")
        ident, ts, owner_id = new_id(), now_iso(), inp.get("ownerId")
        with self.engine.begin() as conn:
            if owner_id and not conn.execute(select(user.c.id).where(and_(user.c.id == owner_id, user.c.organization_id == org_id))).first():
                raise ValueError("Owner must belong to the current organization.")
            structure = self._initiative_structure(conn, org_id, None, inp, actor_id)
            parents = structure.pop("_parents", None)
            number = (conn.execute(select(func.max(initiative.c.number)).where(
                initiative.c.organization_id == org_id)).scalar() or 0) + 1
            conn.execute(initiative.insert().values(id=ident, organization_id=org_id, name=name, summary=inp.get("summary"), description=inp.get("description"), status=status, priority=priority, owner_id=owner_id, creator_id=actor_id, target_date=inp.get("targetDate"), created_at=ts, updated_at=ts, number=number, **structure))
            self._initiative_projects(conn, org_id, ident, inp.get("projectIds", []))
            if parents:
                self._set_initiative_parents(conn, org_id, ident, parents)
            if inp.get("labelIds"):
                self._initiative_labels(conn, org_id, ident, inp["labelIds"])
            notifications.on_initiative_created(conn, org_id, ident, actor_id, owner_id, ts)
        return ident

    INITIATIVE_REMINDERS = ("never", "weekly", "biweekly", "monthly")

    @staticmethod
    def resolve_initiative_id(conn, org_id: str, ref: str) -> str:
        """An initiative reference is its id or its readable ``I-<number>``."""
        match = re.fullmatch(r"[Ii]-(\d+)", ref or "")
        if not match:
            return ref
        found = conn.execute(select(initiative.c.id).where(and_(
            initiative.c.organization_id == org_id,
            initiative.c.number == int(match.group(1))))).scalar()
        return found or ref

    def _initiative_structure(self, conn, org_id: str, ident: str | None, inp: dict,
                              viewer_id: str | None = None) -> dict:
        """Validate the revision-88 fields an input carries and return their columns."""
        values: dict = {}
        # Parents are applied after the row exists (see _set_initiative_parents); the
        # desired list travels back under a private key.
        if "parentIds" in inp:
            values["_parents"] = list(dict.fromkeys(inp["parentIds"] or []))
        elif "parentId" in inp:
            values["_parents"] = [inp["parentId"]] if inp["parentId"] else []
        if "slackChannelId" in inp:
            channel = (inp["slackChannelId"] or "").strip() or None
            if channel and not re.fullmatch(r"[CG][A-Z0-9]{6,}", channel):
                raise ValueError("Slack channel must be a channel id like C0123ABCD.")
            values["slack_channel_id"] = channel
            values["slack_channel_name"] = ((inp.get("slackChannelName") or "").strip()[:80] or None) if channel else None
        if "icon" in inp:
            icon = (inp["icon"] or "").strip() or None
            if icon and len(icon) > 32:
                raise ValueError("Initiative icon must be a short name or emoji.")
            values["icon"] = icon
        if "color" in inp:
            color = (inp["color"] or "").strip() or None
            if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Initiative colour must be a hex colour like #5e6ad2.")
            values["color"] = color
        if "leadTeamId" in inp:
            team_id = inp["leadTeamId"] or None
            if team_id and not conn.execute(select(team.c.id).where(and_(
                    team.c.id == team_id, team.c.organization_id == org_id))).first():
                raise ValueError("Lead team must belong to the current organization.")
            if team_id and viewer_id:
                self._require_team_visible(conn, org_id, viewer_id, team_id)
            values["lead_team_id"] = team_id
        if "updateReminder" in inp:
            # None follows the workspace schedule; "never" opts out; a cadence overrides it.
            reminder = inp["updateReminder"] or None
            if reminder == "default":
                reminder = None
            if reminder and reminder not in self.INITIATIVE_REMINDERS:
                raise ValueError("Update reminders are default, never, weekly, biweekly or monthly.")
            values["update_reminder"] = reminder
            values["reminder_sent_at"] = None
            values["reminder_followups"] = None
        if "sortOrder" in inp:
            order = inp["sortOrder"]
            if order is not None and not isinstance(order, (int, float)):
                raise ValueError("sortOrder must be a number.")
            values["sort_order"] = float(order) if order is not None else None
        return values

    MAX_INITIATIVE_DEPTH = 5

    def _set_initiative_parents(self, conn, org_id: str, ident: str, refs: list[str]) -> None:
        """Replace an initiative's parents. Every parent must exist in this workspace, may
        not be the initiative or anything below it, and the deepest chain through it stays
        within five levels."""
        parents = []
        for ref in refs:
            pid = self.resolve_initiative_id(conn, org_id, ref)
            if pid == ident:
                raise ValueError("An initiative cannot be its own parent.")
            if not conn.execute(select(initiative.c.id).where(and_(
                    initiative.c.id == pid, initiative.c.organization_id == org_id,
                    initiative.c.archived_at.is_(None)))).first():
                raise ValueError("Parent initiative must belong to the current organization.")
            parents.append(pid)
        parents = list(dict.fromkeys(parents))

        def edges(column_from, column_to, start: str) -> dict[str, int]:
            """Distance from ``start`` to everything reachable along the parent table."""
            dist, frontier = {start: 0}, [start]
            while frontier:
                rows = conn.execute(select(column_from, column_to).where(and_(
                    initiative_parent.c.organization_id == org_id, column_from.in_(frontier)))).all()
                frontier = []
                for src, dst in rows:
                    if dst not in dist:
                        dist[dst] = dist[src] + 1
                        frontier.append(dst)
            return dist

        below = edges(initiative_parent.c.parent_id, initiative_parent.c.initiative_id, ident)
        depth_below = max(below.values())
        for pid in parents:
            if pid in below:
                raise ValueError("That parent would nest the initiative inside itself.")
            above = edges(initiative_parent.c.initiative_id, initiative_parent.c.parent_id, pid)
            if max(above.values()) + 1 + depth_below + 1 > self.MAX_INITIATIVE_DEPTH:
                raise ValueError("Initiatives nest at most five levels deep.")
        conn.execute(initiative_parent.delete().where(and_(
            initiative_parent.c.organization_id == org_id, initiative_parent.c.initiative_id == ident)))
        ts = now_iso()
        for pid in parents:
            conn.execute(initiative_parent.insert().values(
                organization_id=org_id, initiative_id=ident, parent_id=pid, created_at=ts))
        conn.execute(initiative.update().where(and_(
            initiative.c.id == ident, initiative.c.organization_id == org_id)).values(
            parent_id=parents[0] if parents else None))

    def _initiative_labels(self, conn, org_id: str, ident: str, label_ids: list[str]) -> None:
        """Replace an initiative's labels. Group headers cannot be applied, and an
        exclusive group allows one of its labels."""
        rows = {}
        for lid in dict.fromkeys(label_ids):
            row = conn.execute(select(initiative_label).where(and_(
                initiative_label.c.id == lid, initiative_label.c.organization_id == org_id,
                initiative_label.c.archived_at.is_(None)))).mappings().first()
            if not row:
                raise ValueError("Label must belong to the current organization.")
            if row.get("is_group"):
                raise ValueError("A label group is not a label; pick one of its labels.")
            rows[lid] = row
        per_group: dict[str, int] = {}
        for row in rows.values():
            group = row.get("parent_id")
            if not group:
                continue
            exclusive = conn.execute(select(initiative_label.c.exclusive).where(
                initiative_label.c.id == group)).scalar()
            per_group[group] = per_group.get(group, 0) + 1
            if exclusive and per_group[group] > 1:
                raise ValueError("Only one label from this group can be applied.")
        conn.execute(initiative_label_link.delete().where(and_(
            initiative_label_link.c.organization_id == org_id,
            initiative_label_link.c.initiative_id == ident)))
        for lid in rows:
            conn.execute(initiative_label_link.insert().values(
                organization_id=org_id, initiative_id=ident, label_id=lid))

    @staticmethod
    def _initiative_projects(conn, org_id: str, ident: str, project_ids: list[str]) -> None:
        for pid in dict.fromkeys(project_ids):
            if not conn.execute(select(project.c.id).where(and_(project.c.id == pid, project.c.organization_id == org_id, project.c.archived_at.is_(None)))).first():
                raise ValueError("Project must belong to the current organization.")
            conn.execute(initiative_project.insert().values(organization_id=org_id, initiative_id=ident, project_id=pid))

    def initiative_update(self, org_id: str, viewer_id: str, ident: str, inp: dict) -> bool:
        with self.engine.begin() as conn:
            # Any workspace member edits an initiative (as on Linear); archiving is narrower.
            ident = self.resolve_initiative_id(conn, org_id, ident)
            lead = conn.execute(select(initiative.c.lead_team_id).where(and_(
                initiative.c.id == ident, initiative.c.organization_id == org_id))).scalar()
            if lead and not team_visible(conn, org_id, viewer_id, lead):
                return False
        fields = {"name", "summary", "description", "status", "priority", "ownerId", "targetDate"}
        values = {k: v for k, v in inp.items() if k in fields}
        if "name" in values:
            values["name"] = str(values["name"]).strip()
            if not values["name"]: raise ValueError("Initiative name must not be empty.")
        if "status" in values and values["status"] not in {"active", "planned", "completed", "canceled"}: raise ValueError("Invalid initiative status.")
        if "priority" in values and (not isinstance(values["priority"], int) or values["priority"] not in range(0, 5)): raise ValueError("Invalid initiative priority.")
        if "targetDate" in values: values["target_date"] = values.pop("targetDate")
        if "ownerId" in values:
            owner = values.pop("ownerId")
            if owner:
                with self.engine.connect() as owner_conn:
                    if not owner_conn.execute(select(user.c.id).where(and_(user.c.id == owner, user.c.organization_id == org_id))).first():
                        raise ValueError("Owner must belong to the current organization.")
            values["owner_id"] = owner
        with self.engine.begin() as conn:
            exists = conn.execute(select(initiative).where(and_(initiative.c.id == ident, initiative.c.organization_id == org_id, initiative.c.archived_at.is_(None)))).mappings().first()
            if not exists: return False
            before = dict(exists)
            if "projectIds" in inp:
                conn.execute(initiative_project.delete().where(and_(initiative_project.c.organization_id == org_id, initiative_project.c.initiative_id == ident)))
                self._initiative_projects(conn, org_id, ident, inp["projectIds"])
            if "labelIds" in inp:
                self._initiative_labels(conn, org_id, ident, inp["labelIds"] or [])
            structure = self._initiative_structure(conn, org_id, ident, inp, viewer_id)
            parents = structure.pop("_parents", None)
            current = list(conn.execute(select(initiative_parent.c.parent_id).where(and_(
                initiative_parent.c.organization_id == org_id,
                initiative_parent.c.initiative_id == ident))).scalars())
            if inp.get("addParentId"):
                parents = (parents if parents is not None else current) + [inp["addParentId"]]
            if inp.get("removeParentId"):
                gone = self.resolve_initiative_id(conn, org_id, inp["removeParentId"])
                parents = [p for p in (parents if parents is not None else current) if
                           self.resolve_initiative_id(conn, org_id, p) != gone]
            if parents is not None:
                self._set_initiative_parents(conn, org_id, ident, parents)
            values.update(structure)
            values["updated_at"] = now_iso()
            conn.execute(initiative.update().where(and_(initiative.c.id == ident, initiative.c.organization_id == org_id)).values(**values))
            notifications.on_initiative_updated(conn, org_id, before, values, viewer_id, values["updated_at"])
        return True

    def initiative_archive(self, org_id: str, viewer_id: str, ident: str) -> bool:
        with self.engine.begin() as conn:
            ident = self.resolve_initiative_id(conn, org_id, ident)
            row = conn.execute(select(initiative.c.creator_id, initiative.c.owner_id).where(and_(
                initiative.c.id == ident, initiative.c.organization_id == org_id,
                initiative.c.archived_at.is_(None)))).first()
            if row and viewer_id != row[1]:
                self._require_creator_or_admin(conn, org_id, viewer_id, row[0])
            result = conn.execute(initiative.update().where(and_(initiative.c.id == ident, initiative.c.organization_id == org_id, initiative.c.archived_at.is_(None))).values(archived_at=now_iso(), updated_at=now_iso()))
            return result.rowcount > 0

    @staticmethod
    def _record_activity(conn, org_id, issue_id, actor_id, event_type, changes, ts):
        conn.execute(issue_activity.insert().values(
            id=new_id(), organization_id=org_id, issue_id=issue_id,
            actor_id=actor_id, event_type=event_type,
            changes=json.dumps(changes, separators=(",", ":")) if changes else None,
            created_at=ts,
        ))

    @staticmethod
    def _notify_issue_recipients(conn, org_id, issue_id, actor_id, recipients, kind, ts,
                                 data=None, comment_id=None):
        """Write rows for an explicit recipient list. Fan-out decisions live in
        ``hoja_server.notifications``; this stays for callers that already know who."""
        notifications.notify(conn, org_id, issue_id, actor_id, recipients, kind, ts,
                             data=data, comment_id=comment_id)

    # -------------------------------------------------------------- issues
    @staticmethod
    def _automation_flag(team_row: dict, key: str) -> bool:
        """Team auto-add toggles default to on (the upstream contract's defaults); NULL reads as on."""
        value = team_row.get(key, True)
        return True if value is None else bool(value)

    def _auto_add_cycle_id(self, conn, org_id: str, team_row: dict, state_id: str | None) -> str | None:
        """carry-over-style "auto-add active issues to current cycle".

        Returns the team's active cycle id when an issue with no cycle
        qualifies (started-type with auto_add_started, completed-type with
        auto_add_completed, backlog-type with auto_add_backlog), else None.
        Cooldown is always 0 in Hoja, so the previous-cycle attribution
        nuance does not apply yet.
        """
        active_id = team_row.get("active_cycle_id")
        if not active_id or not state_id:
            return None
        stype = conn.execute(select(workflow_state.c.type).where(and_(
            workflow_state.c.organization_id == org_id,
            workflow_state.c.id == state_id))).scalar()
        if stype == "started" and self._automation_flag(team_row, "auto_add_started"):
            return active_id
        if stype == "completed" and self._automation_flag(team_row, "auto_add_completed"):
            return active_id
        if stype == "backlog" and self._automation_flag(team_row, "auto_add_backlog"):
            return active_id
        return None

    def issue_create(self, org_id: str, viewer_id: str, inp: dict) -> str | None:
        # HOJA-492: a template supplies what the caller left absent, and (for a
        # team-scoped template) the team itself. This runs before reference
        # validation so the expanded title/teamId/labels are validated by the
        # same code a hand-written task goes through — never a second, parallel
        # set of checks that could drift from it.
        from hoja_server import task_templates

        inp = dict(inp)
        template = task_templates.resolve_for_create(self.engine, org_id, inp)
        if template is not None:
            inp = task_templates.expand(inp, template)
        if not (inp.get("title") or "").strip():
            raise GraphQLError(
                "title is required when no task template supplies one.",
                extensions={"code": "BAD_USER_INPUT", "field": "title"},
            )
        if not inp.get("teamId"):
            raise GraphQLError(
                "teamId is required when no task template supplies one.",
                extensions={"code": "BAD_USER_INPUT", "field": "teamId"},
            )
        with self.engine.begin() as conn:
            team_ref = inp["teamId"]
            trow = conn.execute(select(team).where(and_(
                team.c.organization_id == org_id,
                (team.c.id == team_ref) | (team.c.key == str(team_ref).upper())))).first()
            if not trow:
                return None
            trow = dict(trow._mapping)
            tid = trow["id"]
            self._require_team_visible(conn, org_id, viewer_id, tid)
            parent_id = _validate_issue_references(
                conn,
                org_id,
                tid,
                inp,
                default_state_id=trow.get("default_state_id"),
            )
            if parent_id:
                # Recursive identifiers (HOJA-472). The child takes its own
                # number from the disjoint child range, so a sub-issue never
                # consumes a top-level HOJA-nnn number. The team-row update
                # below serialises concurrent creates in Postgres, which is
                # what makes the sibling count stable.
                parent = conn.execute(select(
                    issue.c.identifier, issue.c.slot, issue.c.depth,
                ).where(and_(
                    issue.c.id == parent_id,
                    issue.c.organization_id == org_id,
                ))).mappings().one()
                depth = int(parent["depth"] or 0) + 1
                if depth > identifiers.MAX_DEPTH:
                    raise InvalidReferenceError("parentId")
                conn.execute(
                    team.update()
                    .where(and_(team.c.id == tid, team.c.organization_id == org_id))
                    .values(child_counter=func.coalesce(team.c.child_counter, 0) + 1)
                )
                child_counter = conn.execute(
                    select(team.c.child_counter).where(
                        and_(team.c.id == tid, team.c.organization_id == org_id)
                    )
                ).scalar_one()
                sibling_no = 1 + (conn.execute(
                    select(func.count()).select_from(issue).where(and_(
                        issue.c.organization_id == org_id,
                        issue.c.parent_id == parent_id,
                    ))
                ).scalar() or 0)
                n = identifiers.child_number(child_counter)
                ident = identifiers.child_identifier(
                    parent["identifier"], parent["slot"], sibling_no)
                slot, issue_depth = identifiers.slot_for(sibling_no, depth), depth
            else:
                result = conn.execute(
                    team.update()
                    .where(and_(team.c.id == tid, team.c.organization_id == org_id))
                    .values(issue_counter=func.coalesce(team.c.issue_counter, 0) + 1)
                )
                if result.rowcount != 1:
                    raise InvalidReferenceError("teamId")
                n = conn.execute(
                    select(team.c.issue_counter).where(
                        and_(team.c.id == tid, team.c.organization_id == org_id)
                    )
                ).scalar_one()
                ident = f"{trow['key']}-{n}"
                slot, issue_depth = identifiers.ROOT_SLOT, 0
            prio = int(inp.get("priority", 0) or 0)
            state_id = inp.get("stateId") or trow.get("default_state_id")
            cycle_id = inp.get("cycleId") or self._auto_add_cycle_id(conn, org_id, trow, state_id)
            ts = now_iso()
            url_key, base_url = org_links(conn, org_id)
            iid = new_id()
            conn.execute(issue.insert().values(
                id=iid, organization_id=org_id, team_id=tid, number=n, identifier=ident,
                slot=slot, depth=issue_depth,
                title=inp["title"], description=inp.get("description"),
                priority=prio, priority_label=PRIORITY_LABELS.get(prio, "No priority"),
                estimate=inp.get("estimate"), state_id=state_id, state_changed_at=ts,
                assignee_id=inp.get("assigneeId"), delegate_id=inp.get("delegateId"),
                creator_id=viewer_id,
                project_id=inp.get("projectId"), cycle_id=cycle_id,
                parent_id=parent_id,
                url=entity_url(url_key, "issue", ident, base_url),
                branch_name=f"{viewer_id[:8]}/{ident.lower()}-{_slug(inp['title'])}",
                due_date=inp.get("dueDate"), created_at=ts, updated_at=ts,
            ))
            self._apply_state_ts(conn, org_id, iid, state_id, ts)
            # HOJA-491: a task created under an SLA policy starts its clock here,
            # in the same transaction that creates the row — never retroactively.
            from hoja_server import sla as sla_module

            policy = sla_module.matching_policy(conn, org_id, tid, prio)
            if policy is not None:
                sla_module.start_clock(conn, org_id, iid, policy, ts)
            for lid in (inp.get("labelIds") or []):
                conn.execute(issue_label_link.insert().values(issue_id=iid, label_id=lid))
            self._record_activity(conn, org_id, iid, viewer_id, "issueCreated", {"title": inp["title"]}, ts)
            notifications.on_issue_created(conn, org_id, iid, viewer_id, inp.get("assigneeId"), ts)
            session = self._open_delegation_session(
                conn, org_id, iid, inp.get("delegateId"), viewer_id, ts)
        self._dispatch_admitted(session)
        return iid

    # ----------------------------------------------------------- delegation
    def _open_delegation_session(self, conn, org_id: str, issue_id: str,
                                 delegate_id: str | None, actor_id: str | None,
                                 ts: str) -> dict | None:
        """Delegating opens a session; it is not a label on the issue (HOJA-602-A-7).

        Runs in the delegating transaction, so the schedule verdict and the first
        activity exist by the time the caller can read the issue back. Withdrawing
        the delegate cancels the live session instead of leaving a runner with a
        claim on a task nobody delegated.
        """
        from hoja_server import agent_schedule, agent_sessions

        previous = agent_sessions.latest_for_issue(conn, org_id, issue_id)
        superseded = bool(
            previous
            and previous["state"] not in agent_sessions.TERMINAL
            and previous["agent_user_id"] != delegate_id
        )
        if superseded:
            # Removing the delegate, or moving it to another agent, ends the old run:
            # two live claims on one task is what invariant I1 forbids.
            agent_sessions.transition(
                conn, previous, "cancel", at=ts, actor_id=actor_id,
                reason="the delegate changed" if delegate_id else "the delegate was removed",
            )
        if delegate_id:
            row = conn.execute(select(issue).where(and_(
                issue.c.organization_id == org_id, issue.c.id == issue_id,
            ))).mappings().one()
            return agent_schedule.delegate(conn, org_id, dict(row), delegate_id,
                                           actor_id, at=ts)
        return None

    def _dispatch_admitted(self, session: dict | None) -> None:
        """Hand an admitted session to WP07's queue, after the commit.

        ``runner_api.enqueue_task`` opens its own connection, so it cannot run
        inside this writer's transaction (SQLite would wait on the lock the
        transaction already holds). A failure here leaves the session admitted with
        no lease: visible, and retryable by the next ``reevaluate``, which is a
        better outcome than rolling back an issue the caller already created.
        """
        if not session:
            return
        from hoja_server import agent_schedule

        try:
            agent_schedule.enqueue_admitted(self.engine, session)
        except Exception:  # the issue write has already committed
            log.exception("could not enqueue lease for agent session %s", session.get("id"))

    # ------------------------------------------------------------- decisions
    def _issue_row_for_decision(self, conn, org_id: str, viewer_id: str, ref: str) -> dict:
        row = conn.execute(select(issue).where(and_(
            issue.c.organization_id == org_id,
            identifiers.issue_ref_clause(org_id, ref),
            issue.c.archived_at.is_(None)))).mappings().first()
        if not row:
            raise GraphQLError("Issue not found.", extensions={"code": "NOT_FOUND"})
        # Both an invisible team and another organization read as "no such decision", so a
        # decision id never reveals that an issue exists.
        if row["team_id"] and not team_visible(conn, org_id, viewer_id, row["team_id"]):
            raise GraphQLError("Decision not found.", extensions={"code": "NOT_FOUND"})
        return dict(row)

    @staticmethod
    def _refresh_decision_flag(conn, org_id: str, issue_id: str) -> bool:
        """Recompute the issue's maintained flag from its open decisions; return it."""
        open_count = conn.execute(select(func.count()).select_from(issue_decision).where(and_(
            issue_decision.c.organization_id == org_id,
            issue_decision.c.issue_id == issue_id,
            issue_decision.c.status == decisiondocs.OPEN))).scalar() or 0
        needed = bool(open_count)
        conn.execute(issue.update().where(and_(
            issue.c.organization_id == org_id, issue.c.id == issue_id)).values(
                decision_needed=needed))
        return needed

    def decision_create(self, org_id: str, viewer_id: str, inp: dict) -> str:
        """Ask a question on an issue. The issue is flagged in the same transaction."""
        question = decisiondocs.parse_question(inp.get("question"))
        options = decisiondocs.parse_options(inp.get("options"))
        context = decisiondocs.parse_context(inp.get("context"))
        agent = decisiondocs.parse_agent(inp.get("agent"))
        ts = now_iso()
        with self.engine.begin() as conn:
            irow = self._issue_row_for_decision(conn, org_id, viewer_id, inp.get("issueId"))
            open_count = conn.execute(select(func.count()).select_from(issue_decision).where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.issue_id == irow["id"],
                issue_decision.c.status == decisiondocs.OPEN))).scalar() or 0
            if open_count >= decisiondocs.MAX_OPEN_PER_ISSUE:
                raise GraphQLError(
                    f"This issue already has {open_count} open decisions; answer or cancel "
                    "one before asking another.",
                    extensions={"code": "BAD_USER_INPUT", "field": "issueId"})
            decision_id = new_id()
            conn.execute(issue_decision.insert().values(
                id=decision_id, organization_id=org_id, issue_id=irow["id"],
                question=question, options=decisiondocs.dump_options(options),
                context=context, status=decisiondocs.OPEN,
                asked_by_id=viewer_id, asked_by_agent=agent,
                created_at=ts, updated_at=ts))
            self._refresh_decision_flag(conn, org_id, irow["id"])
            self._record_activity(conn, org_id, irow["id"], viewer_id, "decisionCreated",
                                  {"decisionId": decision_id, "question": question}, ts)
            notifications.on_decision(conn, org_id, irow, viewer_id, decision_id, question, ts)
        return decision_id

    def decision_answer(self, org_id: str, viewer_id: str, decision_id: str,
                        inp: dict) -> tuple[bool, bool]:
        """Answer once. Returns ``(changed, replay)``; a replay stores nothing new."""
        ts = now_iso()
        with self.engine.begin() as conn:
            row = conn.execute(select(issue_decision).where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.id == decision_id))).mappings().first()
            if not row:
                raise GraphQLError("Decision not found.", extensions={"code": "NOT_FOUND"})
            row = dict(row)
            irow = self._issue_row_for_decision(conn, org_id, viewer_id, row["issue_id"])
            if row["status"] == decisiondocs.CANCELED:
                raise GraphQLError("This decision was canceled and cannot be answered.",
                                   extensions={"code": "BAD_USER_INPUT", "field": "id"})
            chosen, answer = decisiondocs.answer_for(
                decisiondocs.load_options(row.get("options")),
                option=inp.get("option"), answer=inp.get("answer"))
            if row["status"] == decisiondocs.ANSWERED:
                if decisiondocs.is_replay(row, chosen_option=chosen, answer=answer):
                    return False, True
                raise GraphQLError(
                    "This decision is already answered; the first answer stands.",
                    extensions={"code": "ALREADY_ANSWERED", "field": "id"})
            conn.execute(issue_decision.update().where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.id == decision_id)).values(
                    status=decisiondocs.ANSWERED, answer=answer, chosen_option=chosen,
                    answered_by_id=viewer_id, answered_at=ts, updated_at=ts))
            self._refresh_decision_flag(conn, org_id, irow["id"])
            self._record_activity(conn, org_id, irow["id"], viewer_id, "decisionAnswered",
                                  {"decisionId": decision_id, "chosenOption": chosen,
                                   "answer": answer}, ts)
        return True, False

    def decision_cancel(self, org_id: str, viewer_id: str, decision_id: str) -> bool:
        """Withdraw an open decision question (the asker or an admin)."""
        ts = now_iso()
        with self.engine.begin() as conn:
            row = conn.execute(select(issue_decision).where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.id == decision_id))).mappings().first()
            if not row:
                raise GraphQLError("Decision not found.", extensions={"code": "NOT_FOUND"})
            row = dict(row)
            irow = self._issue_row_for_decision(conn, org_id, viewer_id, row["issue_id"])
            if row["status"] != decisiondocs.OPEN:
                return False
            self._require_creator_or_admin(conn, org_id, viewer_id, row.get("asked_by_id"))
            conn.execute(issue_decision.update().where(and_(
                issue_decision.c.organization_id == org_id,
                issue_decision.c.id == decision_id)).values(
                    status=decisiondocs.CANCELED, canceled_at=ts, updated_at=ts))
            self._refresh_decision_flag(conn, org_id, irow["id"])
            self._record_activity(conn, org_id, irow["id"], viewer_id, "decisionCanceled",
                                  {"decisionId": decision_id}, ts)
        return True

    def issue_update(self, org_id: str, ident: str, inp: dict, actor_id: str | None = None) -> str | None:
        session = None
        with self.engine.begin() as conn:
            row = conn.execute(select(issue).where(and_(
                issue.c.organization_id == org_id,
                identifiers.issue_ref_clause(org_id, ident)))).first()
            if not row:
                return None
            issue_row = dict(row._mapping)
            iid = issue_row["id"]
            if actor_id is not None:
                self._require_team_visible(conn, org_id, actor_id, issue_row["team_id"])
                if inp.get("teamId") is not None:
                    raise InvalidReferenceError("teamId")
            if inp.get("parentId"):
                # PostgreSQL: serialize reparenting per team so two transactions cannot
                # independently validate opposite edges and commit a cycle. SQLite ignores
                # FOR UPDATE and serializes writes at the database level instead.
                conn.execute(select(team.c.id).where(and_(
                    team.c.organization_id == org_id, team.c.id == issue_row["team_id"],
                )).with_for_update()).scalar_one()
            parent_id = _validate_issue_references(
                conn, org_id, issue_row["team_id"], inp, issue_id=iid
            )
            ts = now_iso()
            values: dict = {"updated_at": ts}
            mapping = {
                "title": "title", "description": "description", "estimate": "estimate",
                "assigneeId": "assignee_id", "delegateId": "delegate_id",
                "projectId": "project_id",
                "cycleId": "cycle_id", "parentId": "parent_id", "dueDate": "due_date",
            }
            for k, col in mapping.items():
                if k in inp:
                    values[col] = inp[k]
            if "parentId" in inp:
                values["parent_id"] = parent_id
            if "priority" in inp:
                p = int(inp["priority"] or 0)
                values["priority"] = p
                values["priority_label"] = PRIORITY_LABELS.get(p, "No priority")
            if "stateId" in inp:
                values["state_id"] = inp["stateId"]
                if inp["stateId"] != issue_row.get("state_id"):
                    values["state_changed_at"] = ts
                    # carry-over-style auto-add: a transition into Started/Completed
                    # pulls a cycle-less issue into the active cycle, unless the
                    # caller explicitly set (or cleared) the cycle.
                    if "cycleId" not in inp and not issue_row.get("cycle_id"):
                        team_row = conn.execute(select(team).where(and_(
                            team.c.organization_id == org_id,
                            team.c.id == issue_row["team_id"]))).mappings().first()
                        if team_row:
                            auto_id = self._auto_add_cycle_id(
                                conn, org_id, dict(team_row), inp["stateId"])
                            if auto_id:
                                values["cycle_id"] = auto_id
            changes = {}
            for field, column in {"title": "title", "description": "description", "priority": "priority", "stateId": "state_id", "assigneeId": "assignee_id", "delegateId": "delegate_id", "projectId": "project_id"}.items():
                if field in inp and issue_row.get(column) != values.get(column):
                    changes[field] = {"from": issue_row.get(column), "to": values.get(column)}
            conn.execute(issue.update().where(issue.c.id == iid).values(**values))
            # HOJA-602-A-7: moving the delegate opens or cancels the session in this
            # same commit, so the issue never points at an agent with no session.
            if "delegateId" in inp and issue_row.get("delegate_id") != inp.get("delegateId"):
                session = self._open_delegation_session(
                    conn, org_id, iid, inp.get("delegateId"), actor_id, ts)
            if "stateId" in inp:
                self._apply_state_ts(conn, org_id, iid, inp["stateId"], ts)
                # HOJA-491: pause / resume / stop the clock on the same commit as
                # the transition, so a reader never sees the two disagree.
                from hoja_server import sla as sla_module

                sla_module.on_state_change(conn, org_id, iid, inp["stateId"], ts)
            # HOJA-704: a priority, team or cycle change re-matches the policy on
            # the same commit. The helper reads the row back, so it sees the
            # values just written rather than the input that asked for them.
            if any(k in inp for k in ("priority", "cycleId", "teamId", "stateId")):
                from hoja_server import sla as sla_module

                sla_module.retarget_clock(conn, org_id, iid, ts, actor_id)
            if "labelIds" in inp:
                conn.execute(issue_label_link.delete().where(issue_label_link.c.issue_id == iid))
                for lid in (inp.get("labelIds") or []):
                    conn.execute(issue_label_link.insert().values(issue_id=iid, label_id=lid))
            if changes:
                self._record_activity(conn, org_id, iid, actor_id, "issueUpdated", changes, ts)
            # Outside the activity gate: a due-date move is not in ``changes`` but is
            # something the people following the task hear about.
            notifications.on_issue_updated(
                conn, org_id, issue_row, {**issue_row, **values}, actor_id, ts)
        self._dispatch_admitted(session)
        return iid

    def _apply_state_ts(self, conn, org_id, iid, state_id, ts):
        if not state_id:
            return
        srow = conn.execute(select(workflow_state.c.type).where(and_(
            workflow_state.c.organization_id == org_id,
            workflow_state.c.id == state_id))).first()
        if not srow:
            return
        stype = srow[0]
        vals: dict = {}
        if stype == "started":
            vals = {"started_at": ts, "completed_at": None, "canceled_at": None}
        elif stype == "completed":
            cur = conn.execute(select(issue.c.started_at).where(issue.c.id == iid)).first()
            vals = {"completed_at": ts, "canceled_at": None}
            if not (cur and cur[0]):
                vals["started_at"] = ts
        elif stype == "canceled":
            vals = {"canceled_at": ts}
        elif stype in ("backlog", "unstarted", "triage"):
            vals = {"started_at": None, "completed_at": None, "canceled_at": None}
        if vals:
            conn.execute(issue.update().where(issue.c.id == iid).values(**vals))

    def _authorize_issue_change(self, org_id: str, viewer_id: str, ident: str,
                                capability: str | None = None) -> None:
        """Creator, admin, or (when named) a holder of the delete capability.

        HOJA-490 adds ``capability`` so a workspace can grant deleting other
        people's tasks without granting admin. Visibility is still checked first:
        a capability never reveals a private team's task.
        """
        # The capability lookup uses its own connection, so it runs before the
        # transaction opens: taking a second connection inside a SQLite write
        # transaction would wait on the lock it already holds.
        holds_capability = False
        if capability:
            from hoja_server import permissions as permission_rules
            holds_capability = permission_rules.can_for_engine(
                self.engine, org_id, viewer_id, capability)
        with self.engine.begin() as conn:
            row = conn.execute(select(issue.c.creator_id, issue.c.team_id).where(and_(
                issue.c.organization_id == org_id,
                identifiers.issue_ref_clause(org_id, ident),
            ))).first()
            if row:
                self._require_team_visible(conn, org_id, viewer_id, row[1])
                if not holds_capability:
                    self._require_creator_or_admin(conn, org_id, viewer_id, row[0])

    def issue_archive(self, org_id: str, viewer_id: str, ident: str) -> bool:
        self._authorize_issue_change(org_id, viewer_id, ident, capability="task.delete")
        return self._soft_delete(issue, org_id, ident, by_identifier=True, event_type="issueArchived")

    def issue_delete(self, org_id: str, viewer_id: str, ident: str) -> bool:
        self._authorize_issue_change(org_id, viewer_id, ident, capability="task.delete")
        return self._soft_delete(issue, org_id, ident, by_identifier=True)

    def _soft_delete(self, table, org_id, ident, by_identifier=False, event_type=None) -> bool:
        with self.engine.begin() as conn:
            cond = (table.c.id == ident)
            if by_identifier and "identifier" in table.c:
                cond = cond | (table.c.identifier == ident.upper())
            row = conn.execute(select(table.c.id).where(and_(
                table.c.organization_id == org_id, cond))).first()
            if not row:
                return False
            ts = now_iso()
            conn.execute(table.update().where(table.c.id == row[0]).values(archived_at=ts))
            if event_type:
                self._record_activity(conn, org_id, row[0], None, event_type, None, ts)
            return True

    # -------------------------------------------------------------- comments
    def comment_create(self, org_id: str, viewer_id: str, inp: dict) -> str | None:
        """A comment on an issue or on a project update — exactly one parent.

        ``issueId`` keeps the old path untouched, including the activity row and the
        first-response hook. ``projectUpdateId`` is the Pulse path: an update has no
        team, no identifier and no activity stream of its own, so it records only
        the comment row.
        """
        issue_ref = inp.get("issueId")
        update_ref = inp.get("projectUpdateId")
        initiative_update_ref = inp.get("initiativeUpdateId")
        if sum(bool(x) for x in (issue_ref, update_ref, initiative_update_ref)) != 1:
            # Two parents would leave the reply with no single owner; none would
            # leave it unreachable once the mutation returns.
            raise GraphQLError(
                "A comment needs exactly one parent: pass issueId, projectUpdateId or initiativeUpdateId.",
                extensions={"code": "BAD_USER_INPUT", "field": "issueId"})
        with self.engine.begin() as conn:
            if initiative_update_ref:
                urow = self._pulse_update_row(conn, initiative_update, org_id, initiative_update_ref)
                if not urow:
                    return None
                ts = now_iso()
                cid = new_id()
                conn.execute(comment.insert().values(
                    id=cid, organization_id=org_id, initiative_update_id=urow["id"],
                    body=inp["body"], user_id=viewer_id, created_at=ts, updated_at=ts))
                notifications.on_initiative_update_reply(
                    conn, org_id, dict(urow), cid, viewer_id, inp["body"], ts)
                return cid
            if update_ref:
                urow = self._pulse_update_row(conn, project_update, org_id, update_ref)
                if not urow:
                    return None
                ts = now_iso()
                cid = new_id()
                conn.execute(comment.insert().values(
                    id=cid, organization_id=org_id, project_update_id=urow["id"],
                    body=inp["body"], user_id=viewer_id, created_at=ts, updated_at=ts))
                notifications.on_project_update_reply(
                    conn, org_id, dict(urow), cid, viewer_id, inp["body"], ts)
                return cid
            irow = conn.execute(select(issue).where(and_(
                issue.c.organization_id == org_id,
                identifiers.issue_ref_clause(org_id, inp["issueId"])))).first()
            if not irow:
                return None
            irow = dict(irow._mapping)
            self._require_team_visible(conn, org_id, viewer_id, irow.get("team_id"))
            ts = now_iso()
            cid = new_id()
            conn.execute(comment.insert().values(
                id=cid, organization_id=org_id, issue_id=irow["id"], body=inp["body"],
                user_id=viewer_id, url=f"{irow.get('url')}#comment-{cid[:8]}",
                created_at=ts, updated_at=ts))
            self._record_activity(conn, org_id, irow["id"], viewer_id, "commentCreated", {"commentId": cid}, ts)
            notifications.on_comment(conn, org_id, irow, viewer_id, cid, inp["body"], ts)
            # HOJA-491: the first comment by someone other than the creator is the
            # "first response" the target measures.
            from hoja_server import sla as sla_module

            sla_module.on_first_response(conn, org_id, irow["id"], viewer_id, ts)
            return cid

    def comment_update(self, org_id: str, viewer_id: str, cid: str, inp: dict) -> str | None:
        with self.engine.begin() as conn:
            row = self._authorize_comment(conn, org_id, viewer_id, cid, comment_policy.EDIT)
            if not row:
                return None
            vals = {"updated_at": now_iso()}
            if "body" in inp:
                vals["body"] = inp["body"]
            conn.execute(comment.update().where(comment.c.id == cid).values(**vals))
            # issue_activity.issue_id is NOT NULL, and a Pulse reply has no issue: the
            # activity row is issue-only, so it is written only when there is an issue
            # to hang it on. An update's own editedAt is the edit trail there.
            if row.issue_id:
                self._record_activity(conn, org_id, row.issue_id, viewer_id, "commentUpdated", {"commentId": cid}, vals["updated_at"])
            return cid

    def comment_delete(self, org_id: str, viewer_id: str, cid: str) -> bool:
        with self.engine.begin() as conn:
            if not self._authorize_comment(conn, org_id, viewer_id, cid, comment_policy.DELETE):
                return False
            conn.execute(comment.update().where(comment.c.id == cid).values(archived_at=now_iso()))
            return True

    def _authorize_comment(self, conn, org_id: str, viewer_id: str, cid: str, action: str):
        """The comment row if the policy permits ``action``; None when it is absent or must look absent."""
        # An outer join, because a Pulse comment has project_update_id set and
        # issue_id NULL: the inner join used to drop it, so its own author could
        # neither edit nor delete it. team_id stays None for those, which
        # comment_policy.resource reads as "visible to the whole workspace".
        row = conn.execute(
            select(comment.c.user_id, comment.c.issue_id, comment.c.archived_at, issue.c.team_id)
            .select_from(comment.outerjoin(issue, issue.c.id == comment.c.issue_id))
            .where(and_(comment.c.organization_id == org_id, comment.c.id == cid))
        ).first()
        if not row:
            return None
        decision = comment_policy.decide(
            action, comment_policy.subject(conn, org_id, viewer_id),
            comment_policy.resource(conn, org_id, viewer_id, author_id=row.user_id,
                                    archived=row.archived_at is not None, team_id=row.team_id))
        if decision:
            return row
        if decision.rule in comment_policy.NOT_FOUND_RULES:
            return None
        raise GraphQLError(comment_policy.DENIED_MESSAGE[action],
                           extensions={"code": "FORBIDDEN", "rule": decision.rule or "default-deny"})

    # -------------------------------------------------------------- attachments
    def attachment_create(self, org_id: str, viewer_id: str, inp: dict) -> dict | None:
        with self.engine.begin() as conn:
            parsed_url = urlparse(inp["url"])
            relative_upload = (
                not parsed_url.scheme
                and not parsed_url.netloc
                and (parsed_url.path or "").startswith("/api/uploads/")
            )
            if not relative_upload and (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username
                or parsed_url.password
            ):
                raise InvalidReferenceError("url")
            irow = conn.execute(select(issue.c.id).where(and_(
                issue.c.organization_id == org_id,
                identifiers.issue_ref_clause(org_id, inp["issueId"]),
            ))).first()
            if not irow:
                return None
            aid = new_id()
            created_at = now_iso()
            # An uploaded blob's type is knowable from its sidecar; an external link's
            # is not, so it stays NULL and the client falls back to the URL extension.
            content_type = uploads.content_type_for_url(inp["url"])
            conn.execute(attachment.insert().values(
                id=aid,
                organization_id=org_id,
                issue_id=irow[0],
                creator_id=viewer_id,
                url=inp["url"],
                title=inp["title"],
                subtitle=inp.get("subtitle"),
                content_type=content_type,
                created_at=created_at,
            ))
            return {
                "id": aid,
                "url": inp["url"],
                "title": inp["title"],
                "subtitle": inp.get("subtitle"),
                "contentType": content_type,
                "createdAt": created_at,
                "_issue_id": irow[0],
            }

    # ---------------------------------------------------------- subscribers
    def _issue_row_for(self, conn, org_id: str, issue_id: str):
        return conn.execute(select(issue.c.id).where(and_(
            issue.c.organization_id == org_id,
            identifiers.issue_ref_clause(org_id, issue_id),
        ))).first()

    def issue_subscribe(self, org_id: str, user_id: str, issue_id: str) -> bool:
        """Subscribe a user to an issue (idempotent)."""
        with self.engine.begin() as conn:
            irow = self._issue_row_for(conn, org_id, issue_id)
            if not irow:
                return False
            exists = conn.execute(select(issue_subscriber.c.user_id).where(and_(
                issue_subscriber.c.issue_id == irow[0],
                issue_subscriber.c.user_id == user_id))).first()
            if not exists:
                conn.execute(issue_subscriber.insert().values(
                    issue_id=irow[0], user_id=user_id))
            return True

    def issue_unsubscribe(self, org_id: str, user_id: str, issue_id: str) -> bool:
        """Remove a user's subscription to an issue (idempotent)."""
        with self.engine.begin() as conn:
            irow = self._issue_row_for(conn, org_id, issue_id)
            if not irow:
                return False
            conn.execute(issue_subscriber.delete().where(and_(
                issue_subscriber.c.issue_id == irow[0],
                issue_subscriber.c.user_id == user_id)))
            return True

    def subject_subscribe(self, org_id: str, user_id: str, subject_type: str,
                          subject_id: str, subscribed: bool) -> bool:
        """Follow or unfollow a project, initiative or cycle (idempotent).

        The caller has already resolved ``subject_id`` to a row the user can see;
        this only re-checks that it belongs to the workspace.
        """
        from hoja_server.db import cycle as cycle_t, initiative as initiative_t, project as project_t
        table = {"project": project_t, "initiative": initiative_t, "cycle": cycle_t}[subject_type]
        with self.engine.begin() as conn:
            if not conn.execute(select(table.c.id).where(and_(
                    table.c.organization_id == org_id, table.c.id == subject_id))).first():
                return False
            if subscribed:
                # Same eligibility as auto-subscribe: only active human members follow.
                notifications.subscribe_subject(conn, org_id, subject_type, subject_id, [user_id])
            else:
                notifications.unsubscribe_subject(conn, subject_type, subject_id, user_id)
            return True

    # ---------------------------------------------------------------- cycles
    def cycle_update(self, org_id: str, cycle_id: str, inp: dict) -> dict | None:
        """Update a cycle's name, description, or dates (manual override)."""
        from hoja_server.store import Store
        with self.engine.begin() as conn:
            row = conn.execute(select(cycle).where(and_(
                cycle.c.organization_id == org_id, cycle.c.id == cycle_id))).first()
            if not row:
                return None
            values: dict = {}
            if inp.get("name") is not None:
                values["name"] = inp["name"]
            if inp.get("description") is not None:
                values["description"] = inp["description"]
            if inp.get("startsAt") is not None:
                values["starts_at"] = inp["startsAt"]
            if inp.get("endsAt") is not None:
                values["ends_at"] = inp["endsAt"]
            if values:
                from datetime import datetime, timezone
                values["updated_at"] = datetime.now(timezone.utc).isoformat()
                conn.execute(cycle.update().where(cycle.c.id == cycle_id).values(**values))
            fresh = conn.execute(select(cycle).where(cycle.c.id == cycle_id)).first()
            store = Store(self.engine)
            return store.ser_cycle(dict(fresh._mapping)) if fresh else None

    # -------------------------------------------------------------- labels
    def label_create(self, org_id: str, inp: dict) -> str | None:
        with self.engine.begin() as conn:
            team_id = None
            if inp.get("teamId"):
                trow = conn.execute(select(team.c.id).where(and_(
                    team.c.organization_id == org_id,
                    (team.c.id == inp["teamId"]) | (team.c.key == str(inp["teamId"]).upper())))).first()
                if not trow:
                    raise InvalidReferenceError("teamId")
                team_id = trow[0]
            ts = now_iso()
            lid = new_id()
            conn.execute(issue_label.insert().values(
                id=lid, organization_id=org_id, team_id=team_id, name=inp["name"],
                color=inp.get("color"), description=inp.get("description"),
                created_at=ts, updated_at=ts))
            return lid

    def label_update(self, org_id: str, viewer_id: str, lid: str, inp: dict) -> bool:
        values: dict = {}
        if "name" in inp:
            name = inp["name"]
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
                raise GraphQLError("Label name must be 1–64 characters", extensions={"code": "BAD_USER_INPUT", "field": "name"})
            values["name"] = name.strip()
        for field in ("color", "description"):
            if field in inp:
                values[field] = inp[field]
        # HOJA-490: `label.manage` replaces the bare admin check, so the
        # capability that already gates create/delete gates update too.
        from hoja_server import permissions as permission_rules
        if not permission_rules.can_for_engine(self.engine, org_id, viewer_id, "label.manage"):
            raise GraphQLError(
                "Your workspace role does not allow this: Manage labels.",
                extensions={"code": "FORBIDDEN", "capability": "label.manage"},
            )
        with self.engine.begin() as conn:
            if not values:
                return conn.execute(select(issue_label.c.id).where(and_(
                    issue_label.c.id == lid, issue_label.c.organization_id == org_id,
                    issue_label.c.archived_at.is_(None)))).first() is not None
            values["updated_at"] = now_iso()
            result = conn.execute(issue_label.update().where(and_(
                issue_label.c.id == lid, issue_label.c.organization_id == org_id,
                issue_label.c.archived_at.is_(None))).values(**values))
            return result.rowcount > 0

    def label_delete(self, org_id: str, lid: str) -> bool:
        return self._soft_delete(issue_label, org_id, lid)

    # -------------------------------------------------------------- projects
    def project_create(self, org_id: str, viewer_id: str, inp: dict) -> str | None:
        from hoja_server import project_settings

        # HOJA-489: a project's state is a workspace-configured status, and a
        # template may supply every field the caller leaves absent. Validation
        # happens before the transaction opens; the stored value is the
        # configured status's own spelling.
        template = None
        if inp.get("templateId"):
            with self.engine.connect() as conn:
                template = conn.execute(select(project_template).where(and_(
                    project_template.c.organization_id == org_id,
                    project_template.c.id == inp["templateId"],
                    project_template.c.archived_at.is_(None),
                ))).first()
            if not template:
                raise GraphQLError(
                    "Project template not found.",
                    extensions={"code": "BAD_USER_INPUT", "field": "templateId"})
            template = dict(template._mapping)
        if inp.get("state"):
            state_name = project_settings.validate_project_state(self.engine, org_id, inp["state"])
        elif template and template["state"]:
            state_name = project_settings.validate_project_state(self.engine, org_id, template["state"])
        else:
            state_name = project_settings.default_project_state(self.engine, org_id)
        # Template expansion: absent fields fall back to the template's, so the
        # API and the web client produce the same project field-for-field.
        expanded = dict(inp)
        if template:
            if not (expanded.get("name") or "").strip():
                expanded["name"] = template["name"]
            if expanded.get("description") in (None, ""):
                expanded["description"] = template["description"]
            if not expanded.get("leadId"):
                expanded["leadId"] = template["lead_id"]
            if not expanded.get("icon"):
                expanded["icon"] = template["icon"]
            if not expanded.get("color"):
                expanded["color"] = template["color"]
            if not expanded.get("teamIds"):
                with self.engine.connect() as conn:
                    expanded["teamIds"] = list(conn.execute(
                        select(project_template_team.c.team_id).where(
                            project_template_team.c.template_id == template["id"])
                    ).scalars())
        with self.engine.begin() as conn:
            if lead_id := expanded.get("leadId"):
                _require_org_reference(
                    conn, user, org_id, lead_id, "leadId",
                    user.c.active.is_(True),
                )
            team_ids = set(expanded.get("teamIds") or [])
            if team_ids:
                found = set(
                    conn.execute(
                        select(team.c.id).where(
                            and_(
                                team.c.id.in_(team_ids),
                                team.c.organization_id == org_id,
                                team.c.archived_at.is_(None),
                            )
                        )
                    ).scalars()
                )
                if found != team_ids:
                    raise InvalidReferenceError("teamIds")
                for tid in team_ids:
                    if not team_visible(conn, org_id, viewer_id, tid):
                        raise InvalidReferenceError("teamIds")
            ts = now_iso()
            pid = new_id()
            slug = _slug(expanded["name"])
            url_key, base_url = org_links(conn, org_id)
            conn.execute(project.insert().values(
                id=pid, organization_id=org_id, name=expanded["name"],
                description=expanded.get("description"), slug_id=slug,
                icon=expanded.get("icon"), color=expanded.get("color"),
                state=state_name, progress=0,
                lead_id=expanded.get("leadId"), creator_id=viewer_id,
                start_date=expanded.get("startDate"), target_date=expanded.get("targetDate"),
                url=entity_url(url_key, "project", slug, base_url),
                created_at=ts, updated_at=ts))
            members = [viewer_id, *self._project_member_ids(conn, org_id, inp.get("memberIds"))]
            for uid in dict.fromkeys(members):
                conn.execute(project_member.insert().values(project_id=pid, user_id=uid))
            for team_id in team_ids:
                conn.execute(project_team.insert().values(project_id=pid, team_id=team_id))
            notifications.on_project_created(
                conn, org_id, pid, viewer_id, expanded.get("leadId"), members, ts)
            notifications.on_project_members_added(
                conn, org_id, pid, viewer_id, [uid for uid in members if uid != viewer_id], ts)
        # The optional task set becomes real tasks in the project, through the
        # same tested issue_create path the API uses — never a row written by
        # hand. issue_create opens its own transaction, so this runs after the
        # project transaction has committed.
        task_titles = json.loads(template["task_set"]) if template and template["task_set"] else []
        if task_titles and team_ids:
            for title in task_titles:
                self.issue_create(org_id, viewer_id, {
                    "teamId": sorted(team_ids)[0], "title": title, "projectId": pid})
        return pid

    @staticmethod
    def _project_member_ids(conn, org_id: str, ids) -> list[str]:
        """Validated member ids: active people in this organization, never agents or bots."""
        if ids is None:
            return []
        if not isinstance(ids, list):
            raise InvalidReferenceError("memberIds")
        for uid in ids:
            _require_org_reference(conn, user, org_id, uid, "memberIds",
                                   user.c.active.is_(True), user.c.kind == "human")
        return list(ids)

    def project_update_create(self, org_id: str, pid: str, actor_id: str, inp: dict) -> str | None:
        body = inp.get("body")
        if not isinstance(body, str) or not body.strip():
            raise GraphQLError("Project update body must not be empty.", extensions={"code": "BAD_USER_INPUT", "field": "body"})
        with self.engine.begin() as conn:
            row = conn.execute(select(project).where(and_(
                project.c.organization_id == org_id,
                project.c.id == pid,
                project.c.archived_at.is_(None),
            ))).mappings().first()
            if not row:
                return None
            update_id = new_id()
            ts = now_iso()
            conn.execute(project_update.insert().values(
                id=update_id, organization_id=org_id, project_id=pid,
                actor_id=actor_id, body=body, status=inp.get("status"), health=inp.get("health"),
                progress=self._project_progress(conn, org_id, pid), created_at=ts, updated_at=ts,
            ))
            notifications.on_project_update_posted(
                conn, org_id, pid, update_id, actor_id, body, inp.get("health"), ts)
            return update_id

    def _pulse_update_row(self, conn, table, org_id: str, update_id: str):
        """The update row in this org, or None — cross-org ids must look absent."""
        return conn.execute(select(table).where(and_(
            table.c.organization_id == org_id, table.c.id == update_id))).mappings().first()

    @staticmethod
    def _pulse_edit_body(inp: dict) -> str | None:
        if "body" in inp and (not isinstance(inp["body"], str) or not inp["body"].strip()):
            raise GraphQLError("Update body must not be empty.",
                               extensions={"code": "BAD_USER_INPUT", "field": "body"})
        return inp.get("body")

    def project_update_update(self, org_id: str, viewer_id: str, update_id: str, inp: dict) -> str | None:
        """Edit a project update. Author only: words carry their author's name, and an
        admin edit would put words in someone else's mouth (comment_policy's matrix)."""
        with self.engine.begin() as conn:
            row = self._pulse_update_row(conn, project_update, org_id, update_id)
            if not row or row["actor_id"] != viewer_id:
                return None
            values = {}
            body = self._pulse_edit_body(inp)
            if body is not None:
                values["body"] = body
            for field in ("status", "health"):
                if field in inp:
                    values[field] = inp[field]
            if not values:
                return update_id
            values["updated_at"] = now_iso()
            conn.execute(project_update.update().where(and_(
                project_update.c.organization_id == org_id,
                project_update.c.id == update_id)).values(**values))
            return update_id

    def project_update_delete(self, org_id: str, viewer_id: str, update_id: str) -> bool:
        """Delete a project update: the author, or a workspace admin moderating."""
        with self.engine.begin() as conn:
            row = self._pulse_update_row(conn, project_update, org_id, update_id)
            if not row:
                return False
            actor = conn.execute(select(user.c.admin).where(and_(
                user.c.organization_id == org_id, user.c.id == viewer_id))).scalar()
            if row["actor_id"] != viewer_id and not actor:
                return False
            # Replies hang off the update, so they go with it. Hard delete, not archive:
            # an archived reply would keep pointing at a row that no longer exists, and a
            # database created by create_all carries a real FK that refuses that.
            conn.execute(comment.delete().where(and_(
                comment.c.organization_id == org_id,
                comment.c.project_update_id == update_id)))
            # The posts and replies a reader was told about are gone; so are the rows.
            conn.execute(inbox_notification.delete().where(and_(
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.project_update_id == update_id)))
            conn.execute(project_update.delete().where(and_(
                project_update.c.organization_id == org_id, project_update.c.id == update_id)))
            return True

    def initiative_update_create(self, org_id: str, initiative_id: str, actor_id: str, inp: dict) -> str | None:
        body = inp.get("body")
        if not isinstance(body, str) or not body.strip():
            raise GraphQLError("Initiative update body must not be empty.",
                               extensions={"code": "BAD_USER_INPUT", "field": "body"})
        with self.engine.begin() as conn:
            exists = conn.execute(select(initiative.c.id).where(and_(
                initiative.c.organization_id == org_id, initiative.c.id == initiative_id,
                initiative.c.archived_at.is_(None)))).scalar()
            if not exists:
                return None
            update_id = new_id()
            ts = now_iso()
            conn.execute(initiative_update.insert().values(
                id=update_id, organization_id=org_id, initiative_id=initiative_id,
                actor_id=actor_id, body=body, status=inp.get("status"),
                health=inp.get("health"), created_at=ts, updated_at=ts,
            ))
            notifications.on_initiative_update_posted(
                conn, org_id, initiative_id, update_id, actor_id, body, inp.get("health"), ts)
            return update_id

    def initiative_update_update(self, org_id: str, viewer_id: str, update_id: str, inp: dict) -> str | None:
        """Edit an initiative update. Author only, for the same reason as project updates."""
        with self.engine.begin() as conn:
            row = self._pulse_update_row(conn, initiative_update, org_id, update_id)
            if not row or row["actor_id"] != viewer_id:
                return None
            values = {}
            body = self._pulse_edit_body(inp)
            if body is not None:
                values["body"] = body
            for field in ("status", "health"):
                if field in inp:
                    values[field] = inp[field]
            if not values:
                return update_id
            values["updated_at"] = now_iso()
            conn.execute(initiative_update.update().where(and_(
                initiative_update.c.organization_id == org_id,
                initiative_update.c.id == update_id)).values(**values))
            return update_id

    def initiative_update_delete(self, org_id: str, viewer_id: str, update_id: str) -> bool:
        """Delete an initiative update: the author, or a workspace admin moderating."""
        with self.engine.begin() as conn:
            row = self._pulse_update_row(conn, initiative_update, org_id, update_id)
            if not row:
                return False
            actor = conn.execute(select(user.c.admin).where(and_(
                user.c.organization_id == org_id, user.c.id == viewer_id))).scalar()
            if row["actor_id"] != viewer_id and not actor:
                return False
            conn.execute(inbox_notification.delete().where(and_(
                inbox_notification.c.organization_id == org_id,
                inbox_notification.c.initiative_update_id == update_id)))
            # Replies and reactions hang off the update and go with it.
            conn.execute(comment.delete().where(and_(
                comment.c.organization_id == org_id, comment.c.initiative_update_id == update_id)))
            conn.execute(initiative_update_reaction.delete().where(and_(
                initiative_update_reaction.c.organization_id == org_id,
                initiative_update_reaction.c.update_id == update_id)))
            conn.execute(initiative_update.delete().where(and_(
                initiative_update.c.organization_id == org_id, initiative_update.c.id == update_id)))
            return True

    def _project_progress(self, conn, org_id: str, pid: str) -> float:
        """Snapshot of the project's completion percent for a project update (see Store.project_progress_many)."""
        rows = conn.execute(select(workflow_state.c.type, func.count()).select_from(
            issue.join(workflow_state, workflow_state.c.id == issue.c.state_id)
        ).where(and_(issue.c.organization_id == org_id, issue.c.project_id == pid,
                     issue.c.archived_at.is_(None))).group_by(workflow_state.c.type)).all()
        scope = sum(n for kind, n in rows if kind != "canceled")
        done = sum(n for kind, n in rows if kind == "completed")
        return round(done * 100.0 / scope, 2) if scope else 0.0

    def _require_project_owner(self, conn, org_id: str, viewer_id: str, pid: str) -> bool:
        """Creator, lead, or admin may edit/archive a project. Returns False when the project is absent."""
        row = conn.execute(select(project.c.creator_id, project.c.lead_id).where(and_(
            project.c.organization_id == org_id, project.c.id == pid,
            project.c.archived_at.is_(None)))).first()
        if not row:
            return False
        if viewer_id in (row[0], row[1]):
            return True
        self._require_team_admin(conn, org_id, viewer_id)
        return True

    def project_update(self, org_id: str, viewer_id: str, pid: str, inp: dict) -> str | None:
        from hoja_server import project_settings

        # Validate before the transaction opens: project_settings seeds default
        # statuses on first read, and a seed on a second connection while the
        # outer transaction holds SQLite's read lock would deadlock.
        canonical_state = (
            project_settings.validate_project_state(self.engine, org_id, inp["state"])
            if inp.get("state") else None)
        with self.engine.begin() as conn:
            row = conn.execute(select(project).where(and_(
                project.c.organization_id == org_id, project.c.id == pid))).mappings().first()
            if not row:
                return None
            before = dict(row)
            if not self._require_project_owner(conn, org_id, viewer_id, pid):
                return None
            if lead_id := inp.get("leadId"):
                _require_org_reference(
                    conn, user, org_id, lead_id, "leadId",
                    user.c.active.is_(True),
                )
            if "teamIds" in inp:
                team_ids = set(inp.get("teamIds") or [])
                if team_ids:
                    found = set(conn.execute(select(team.c.id).where(and_(
                        team.c.id.in_(team_ids),
                        team.c.organization_id == org_id,
                        team.c.archived_at.is_(None)))).scalars())
                    if found != team_ids:
                        raise InvalidReferenceError("teamIds")
                    for tid in team_ids:
                        if not team_visible(conn, org_id, viewer_id, tid):
                            raise InvalidReferenceError("teamIds")
                conn.execute(project_team.delete().where(project_team.c.project_id == pid))
                for tid in sorted(team_ids):
                    conn.execute(project_team.insert().values(project_id=pid, team_id=tid))
            if "memberIds" in inp:
                wanted = list(dict.fromkeys(self._project_member_ids(conn, org_id, inp.get("memberIds"))))
                current = set(conn.execute(select(project_member.c.user_id).where(
                    project_member.c.project_id == pid)).scalars())
                conn.execute(project_member.delete().where(and_(
                    project_member.c.project_id == pid,
                    project_member.c.user_id.not_in(wanted or [""]))))
                added = [uid for uid in wanted if uid not in current]
                for uid in added:
                    conn.execute(project_member.insert().values(project_id=pid, user_id=uid))
                notifications.on_project_members_added(conn, org_id, pid, viewer_id, added, now_iso())
            vals = {"updated_at": now_iso()}
            m = {"name": "name", "description": "description", "leadId": "lead_id",
                 "state": "state", "color": "color", "icon": "icon",
                 "startDate": "start_date", "targetDate": "target_date"}
            for k, col in m.items():
                if k in inp:
                    vals[col] = inp[k]
            if canonical_state is not None:
                vals["state"] = canonical_state
            conn.execute(project.update().where(project.c.id == pid).values(**vals))
            notifications.on_project_updated(conn, org_id, before, vals, viewer_id, vals["updated_at"])
            return pid

    def project_archive(self, org_id: str, viewer_id: str, pid: str) -> bool:
        with self.engine.begin() as conn:
            if not self._require_project_owner(conn, org_id, viewer_id, pid):
                return False
        return self._soft_delete(project, org_id, pid)

    # --------------------------------------------------------------- views
    # A view's validation lives in hoja_server.views (the extension-manifest pattern); the
    # writer only resolves references, checks ownership, and stores the parsed documents.

    def _view_scope_target(self, conn, org_id: str, viewer_id: str, scope: str,
                           inp: dict) -> dict:
        """Resolve the one reference ``scope`` requires and reject the others."""
        wanted = {"workspace": None, "team": "teamId", "project": "projectId",
                  "initiative": "initiativeId"}[scope]
        supplied = {name: inp[name] for name in ("teamId", "projectId", "initiativeId")
                    if inp.get(name)}
        if wanted is None:
            if supplied:
                raise GraphQLError(
                    f"A {scope} view does not take {sorted(supplied)[0]}.",
                    extensions={"code": "BAD_USER_INPUT", "field": sorted(supplied)[0]})
            return {}
        if not supplied.get(wanted):
            raise GraphQLError(f"A {scope} view requires {wanted}.",
                               extensions={"code": "BAD_USER_INPUT", "field": wanted})
        for name, value in supplied.items():
            if name != wanted:
                raise GraphQLError(f"A {scope} view does not take {name}.",
                                   extensions={"code": "BAD_USER_INPUT", "field": name})
            if not isinstance(value, str) or not value.strip():
                raise GraphQLError(f"{name} must be a non-empty string.",
                                   extensions={"code": "BAD_USER_INPUT", "field": name})
        target = supplied[wanted]
        if scope == "team":
            self._team_row(conn, org_id, target)
            self._require_team_visible(conn, org_id, viewer_id, target)
            return {"team_id": target}
        if scope == "project":
            stmt = select(project.c.id).where(and_(
                project.c.id == target, project.c.organization_id == org_id,
                project.c.archived_at.is_(None)))
            if not conn.execute(_scope_projects(conn, stmt, org_id, viewer_id)).first():
                raise InvalidReferenceError("projectId")
            return {"project_id": target}
        stmt = select(initiative.c.id).where(and_(
            initiative.c.id == target, initiative.c.organization_id == org_id,
            initiative.c.archived_at.is_(None)))
        if not conn.execute(stmt).first():
            raise InvalidReferenceError("initiativeId")
        return {"initiative_id": target}

    @staticmethod
    def _view_slug(name: str, view_id: str) -> str:
        """A URL fragment that stays unique per organization even for duplicate names."""
        return f"{_slug(name, 32) or 'view'}-{view_id[:8]}"

    def view_create(self, org_id: str, viewer_id: str, inp: dict) -> str:
        kind = inp.get("kind") or "issue"
        name = inp.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise GraphQLError("View name must be a non-empty string of at most 80 characters.",
                               extensions={"code": "BAD_USER_INPUT", "field": "name"})
        scope = inp.get("scope") or "workspace"
        if scope not in viewdocs.SCOPES:
            raise GraphQLError(f"scope must be one of {list(viewdocs.SCOPES)}.",
                               extensions={"code": "BAD_USER_INPUT", "field": "scope"})
        visibility = inp.get("visibility") or "private"
        if visibility not in viewdocs.VISIBILITIES:
            raise GraphQLError(f"visibility must be one of {list(viewdocs.VISIBILITIES)}.",
                               extensions={"code": "BAD_USER_INPUT", "field": "visibility"})
        if visibility == "team" and scope != "team":
            raise GraphQLError("A team-shared view must be scoped to a team.",
                               extensions={"code": "BAD_USER_INPUT", "field": "visibility"})
        filters = viewdocs.parse_filters(kind, inp.get("filters"))
        display = viewdocs.parse_display(kind, inp.get("display"))
        view_id, ts = new_id(), now_iso()
        with self.engine.begin() as conn:
            target = self._view_scope_target(conn, org_id, viewer_id, scope, inp)
            conn.execute(saved_view.insert().values(
                id=view_id, organization_id=org_id, kind=kind, name=name.strip(),
                description=inp.get("description"), icon=inp.get("icon"),
                color=inp.get("color"), slug_id=self._view_slug(name.strip(), view_id),
                owner_id=viewer_id, creator_id=viewer_id, updated_by_id=viewer_id,
                scope=scope, visibility=visibility,
                filters=viewdocs.dump(filters), display=viewdocs.dump(display),
                created_at=ts, updated_at=ts, **target))
        return view_id

    def _view_row(self, conn, org_id: str, view_id: str) -> dict | None:
        row = conn.execute(select(saved_view).where(and_(
            saved_view.c.organization_id == org_id, saved_view.c.id == view_id))).mappings().first()
        return dict(row) if row else None

    def _require_view_owner(self, conn, org_id: str, viewer_id: str, owner_id: str) -> None:
        if owner_id == viewer_id:
            return
        admin = conn.execute(select(user.c.id).where(and_(
            user.c.id == viewer_id, user.c.organization_id == org_id,
            user.c.active.is_(True), user.c.archived_at.is_(None), user.c.admin.is_(True),
        ))).first()
        if not admin:
            raise GraphQLError(
                "Only the view's owner or a workspace admin can change it.",
                extensions={"code": "FORBIDDEN"})

    def view_update(self, org_id: str, viewer_id: str, view_id: str, inp: dict) -> bool:
        if "kind" in inp:
            raise GraphQLError("A view's kind cannot change.",
                               extensions={"code": "BAD_USER_INPUT", "field": "kind"})
        with self.engine.begin() as conn:
            row = self._view_row(conn, org_id, view_id)
            if not row:
                return False
            self._require_view_owner(conn, org_id, viewer_id, row["owner_id"])
            kind = row["kind"]
            values: dict = {"updated_at": now_iso(), "updated_by_id": viewer_id}
            if "name" in inp:
                name = inp["name"]
                if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
                    raise GraphQLError(
                        "View name must be a non-empty string of at most 80 characters.",
                        extensions={"code": "BAD_USER_INPUT", "field": "name"})
                values["name"] = name.strip()
            for field, column in (("description", "description"), ("icon", "icon"),
                                  ("color", "color")):
                if field in inp:
                    values[column] = inp[field]
            if "scope" in inp:
                scope = inp["scope"]
                if scope not in viewdocs.SCOPES:
                    raise GraphQLError(f"scope must be one of {list(viewdocs.SCOPES)}.",
                                       extensions={"code": "BAD_USER_INPUT", "field": "scope"})
                target = self._view_scope_target(conn, org_id, viewer_id, scope, inp)
                values.update({"scope": scope, "team_id": None, "project_id": None,
                               "initiative_id": None})
                values.update(target)
            if "visibility" in inp:
                visibility = inp["visibility"]
                if visibility not in viewdocs.VISIBILITIES:
                    raise GraphQLError(
                        f"visibility must be one of {list(viewdocs.VISIBILITIES)}.",
                        extensions={"code": "BAD_USER_INPUT", "field": "visibility"})
                scope = values.get("scope", row["scope"])
                if visibility == "team" and scope != "team":
                    raise GraphQLError("A team-shared view must be scoped to a team.",
                                       extensions={"code": "BAD_USER_INPUT",
                                                   "field": "visibility"})
                values["visibility"] = visibility
            if "filters" in inp:
                values["filters"] = viewdocs.dump(viewdocs.parse_filters(kind, inp["filters"]))
            if "display" in inp:
                values["display"] = viewdocs.dump(viewdocs.parse_display(kind, inp["display"]))
            if "archived" in inp:
                archived = inp["archived"]
                if not isinstance(archived, bool):
                    raise GraphQLError("archived must be true or false.",
                                       extensions={"code": "BAD_USER_INPUT", "field": "archived"})
                values["archived_at"] = now_iso() if archived else None
            conn.execute(saved_view.update().where(and_(
                saved_view.c.organization_id == org_id,
                saved_view.c.id == view_id)).values(**values))
        return True

    def view_delete(self, org_id: str, viewer_id: str, view_id: str) -> bool:
        """Archive, the recorded inverse of create: nothing is erased, so it can be restored."""
        with self.engine.begin() as conn:
            row = self._view_row(conn, org_id, view_id)
            if not row:
                return False
            self._require_view_owner(conn, org_id, viewer_id, row["owner_id"])
            conn.execute(saved_view.update().where(and_(
                saved_view.c.organization_id == org_id,
                saved_view.c.id == view_id)).values(archived_at=now_iso(), updated_at=now_iso()))
        return True

    def view_favorite_toggle(self, org_id: str, viewer_id: str, view_id: str) -> bool:
        """Toggle this user's favorite; returns the new state. Any viewer who can read may favorite."""
        from hoja_server.store import _scope_views

        with self.engine.begin() as conn:
            visible = select(saved_view.c.id).where(and_(
                saved_view.c.organization_id == org_id, saved_view.c.id == view_id))
            if not conn.execute(_scope_views(conn, visible, org_id, viewer_id)).first():
                raise GraphQLError("View not found.", extensions={"code": "NOT_FOUND"})
            existing = conn.execute(select(saved_view_favorite.c.view_id).where(and_(
                saved_view_favorite.c.view_id == view_id,
                saved_view_favorite.c.user_id == viewer_id))).first()
            if existing:
                conn.execute(saved_view_favorite.delete().where(and_(
                    saved_view_favorite.c.view_id == view_id,
                    saved_view_favorite.c.user_id == viewer_id)))
                return False
            conn.execute(saved_view_favorite.insert().values(
                organization_id=org_id, view_id=view_id, user_id=viewer_id,
                created_at=now_iso()))
        return True


def move_issue_to_state(conn, org_id: str, issue_id: str, state_id: str,
                        actor_id: str | None, ts: str) -> dict:
    """Move one issue to one state on a caller's connection (WP10's In Review gate).

    This lives in the writer, not in the verifier, because a state change carries
    four things that must not have two implementations: the ``state_changed_at``
    stamp, the cycle auto-add rule, the SLA clock, and the activity row plus
    notifications a reader sees. ``issue_update`` batches all of an update's
    fields into one activity; the verifier moves exactly one field, so it writes
    its own row and reuses the same helpers.

    Returns ``{"moved": bool, "stateId": ..., "from": ...}``. A no-op is not an
    error: two verifiers reaching the same conclusion must not both create rows.
    """
    row = conn.execute(select(issue).where(and_(
        issue.c.organization_id == org_id, issue.c.id == issue_id))).mappings().first()
    if not row:
        return {"moved": False, "stateId": None, "from": None}
    issue_row = dict(row)
    if issue_row.get("state_id") == state_id:
        return {"moved": False, "stateId": state_id, "from": issue_row.get("state_id")}
    writer = Writer(conn.engine)
    values: dict = {"state_id": state_id, "state_changed_at": ts, "updated_at": ts}
    team_row = conn.execute(select(team).where(and_(
        team.c.organization_id == org_id,
        team.c.id == issue_row["team_id"]))).mappings().first()
    if team_row and not issue_row.get("cycle_id"):
        auto_id = writer._auto_add_cycle_id(conn, org_id, dict(team_row), state_id)
        if auto_id:
            values["cycle_id"] = auto_id
    conn.execute(issue.update().where(issue.c.id == issue_id).values(**values))
    writer._apply_state_ts(conn, org_id, issue_id, state_id, ts)
    # HOJA-491: pause / resume / stop the clock on the same commit as the
    # transition, exactly as a hand-made move does.
    from hoja_server import sla as sla_module

    sla_module.on_state_change(conn, org_id, issue_id, state_id, ts)
    # The move may have auto-added a cycle (HOJA-704), which is exactly the
    # change a cycle-bound policy binds its deadline to.
    sla_module.retarget_clock(conn, org_id, issue_id, ts, actor_id)
    writer._record_activity(conn, org_id, issue_id, actor_id, "issueUpdated",
                            {"stateId": {"from": issue_row.get("state_id"), "to": state_id}}, ts)
    notifications.on_issue_updated(conn, org_id, issue_row, {**issue_row, **values}, actor_id, ts)
    return {"moved": True, "stateId": state_id, "from": issue_row.get("state_id")}
