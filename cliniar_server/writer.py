"""Write operations with transactional identifiers and derived fields."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from graphql import GraphQLError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Engine

from cliniar_server.store import team_visible
from cliniar_server.db import (
    PRIORITY_LABELS,
    attachment,
    comment,
    cycle,
    entity_url,
    issue,
    issue_activity,
    inbox_notification,
    issue_label,
    issue_label_link,
    issue_subscriber,
    new_id,
    now_iso,
    organization,
    project,
    project_member,
    project_update,
    initiative,
    initiative_project,
    project_team,
    seed_states_for_team,
    team,
    team_member,
    user,
    workflow_state,
)


def _slug(text: str, n: int = 24) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:n].strip("-")


def _org_url_key(conn, org_id: str) -> str:
    r = conn.execute(select(organization.c.url_key).where(organization.c.id == org_id)).first()
    return (r[0] if r and r[0] else "local")


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
) -> None:
    state_id = inp.get("stateId") if "stateId" in inp else default_state_id
    if state_id:
        _require_org_reference(
            conn, workflow_state, org_id, state_id, "stateId",
            workflow_state.c.team_id == team_id,
        )
    if assignee_id := inp.get("assigneeId"):
        _require_team_user(conn, org_id, team_id, assignee_id, "assigneeId")
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
    if parent_id := inp.get("parentId"):
        if parent_id == issue_id:
            raise InvalidReferenceError("parentId")
        _require_org_reference(
            conn, issue, org_id, parent_id, "parentId", issue.c.team_id == team_id
        )
    label_ids = inp.get("labelIds") if "labelIds" in inp else None
    if label_ids:
        expected = set(label_ids)
        found = set(
            conn.execute(
                select(issue_label.c.id).where(
                    and_(
                        issue_label.c.id.in_(expected),
                        issue_label.c.organization_id == org_id,
                        issue_label.c.archived_at.is_(None),
                        or_(
                            issue_label.c.team_id.is_(None),
                            issue_label.c.team_id == team_id,
                        ),
                    )
                )
            ).scalars()
        )
        if found != expected:
            raise InvalidReferenceError("labelIds")


class Writer:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def workspace_create(self, actor_org_id: str, actor_id: str, inp: dict) -> dict:
        """Create a workspace and enroll its creating identity as admin."""
        from cliniar_server.db import global_identity, organization_membership

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
        with self.engine.begin() as conn:
            admin = conn.execute(select(user.c.id).where(and_(
                user.c.id == viewer_id, user.c.organization_id == org_id,
                user.c.active.is_(True), user.c.archived_at.is_(None), user.c.admin.is_(True)
            ))).first()
            if not admin:
                raise GraphQLError("Admin access required", extensions={"code": "FORBIDDEN"})
            if conn.execute(select(team.c.id).where(and_(
                team.c.organization_id == org_id, team.c.key == key
            ))).first():
                raise GraphQLError("Team key already exists", extensions={"code": "BAD_USER_INPUT", "field": "key"})
            ts, tid = now_iso(), new_id()
            conn.execute(team.insert().values(
                id=tid, organization_id=org_id, name=name.strip(), key=key,
                description=inp.get("description"), color=inp.get("color"), icon=inp.get("icon"),
                private=bool(inp.get("private", False)), timezone=inp.get("timezone") or "UTC",
                issue_counter=0, created_at=ts, updated_at=ts,
            ))
            default_state_id = seed_states_for_team(conn, org_id, tid)
            conn.execute(team.update().where(team.c.id == tid).values(
                default_state_id=default_state_id
            ))
            conn.execute(team_member.insert().values(team_id=tid, user_id=viewer_id))
            for member_id in dict.fromkeys(inp.get("memberIds") or []):
                if member_id == viewer_id:
                    continue
                if not conn.execute(select(user.c.id).where(and_(
                    user.c.id == member_id, user.c.organization_id == org_id,
                    user.c.active.is_(True), user.c.archived_at.is_(None)))).first():
                    raise InvalidReferenceError("memberIds")
                conn.execute(team_member.insert().values(team_id=tid, user_id=member_id))
            return tid

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
        with self.engine.begin() as conn:
            self._require_team_admin(conn, org_id, viewer_id)
            self._team_row(conn, org_id, team_id)
            if "key" in values and conn.execute(select(team.c.id).where(and_(
                team.c.organization_id == org_id, team.c.key == values["key"], team.c.id != team_id
            ))).first():
                raise GraphQLError("Team key already exists", extensions={"code": "BAD_USER_INPUT", "field": "key"})
            values["updated_at"] = now_iso()
            conn.execute(team.update().where(team.c.id == team_id).values(**values))
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
            for label, table in (("projects", project_team), ("cycles", cycle)):
                if conn.execute(select(table.c.team_id).where(table.c.team_id == team_id).limit(1)).first():
                    blockers.append(label)
            if not delete_issues and conn.execute(select(issue.c.id).where(issue.c.team_id == team_id).limit(1)).first():
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
            conn.execute(team_member.delete().where(team_member.c.team_id == team_id))
            conn.execute(issue_label.delete().where(issue_label.c.team_id == team_id))
            conn.execute(workflow_state.delete().where(workflow_state.c.team_id == team_id))
            conn.execute(team.delete().where(team.c.id == team_id))
            return True

    def enable_team_cadence(self, org_id: str, team_id: str, weekday: int, *, now=None) -> dict:
        """Enable once and reconcile the fixed one-week, two-upcoming policy."""
        from datetime import datetime, timezone
        from cliniar_server.cadence import reconcile_team_cadence, validate_timezone
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
        from cliniar_server.cadence import reconcile_team_cadence
        instant = now or datetime.now(timezone.utc)
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
            conn.execute(initiative.insert().values(id=ident, organization_id=org_id, name=name, summary=inp.get("summary"), status=status, priority=priority, owner_id=owner_id, creator_id=actor_id, target_date=inp.get("targetDate"), created_at=ts, updated_at=ts))
            self._initiative_projects(conn, org_id, ident, inp.get("projectIds", []))
        return ident

    @staticmethod
    def _initiative_projects(conn, org_id: str, ident: str, project_ids: list[str]) -> None:
        for pid in dict.fromkeys(project_ids):
            if not conn.execute(select(project.c.id).where(and_(project.c.id == pid, project.c.organization_id == org_id, project.c.archived_at.is_(None)))).first():
                raise ValueError("Project must belong to the current organization.")
            conn.execute(initiative_project.insert().values(organization_id=org_id, initiative_id=ident, project_id=pid))

    def initiative_update(self, org_id: str, viewer_id: str, ident: str, inp: dict) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(initiative.c.creator_id).where(and_(
                initiative.c.id == ident, initiative.c.organization_id == org_id,
                initiative.c.archived_at.is_(None)))).first()
            if row:
                self._require_creator_or_admin(conn, org_id, viewer_id, row[0])
        fields = {"name", "summary", "status", "priority", "ownerId", "targetDate"}
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
            exists = conn.execute(select(initiative.c.id).where(and_(initiative.c.id == ident, initiative.c.organization_id == org_id, initiative.c.archived_at.is_(None)))).first()
            if not exists: return False
            if "projectIds" in inp:
                conn.execute(initiative_project.delete().where(and_(initiative_project.c.organization_id == org_id, initiative_project.c.initiative_id == ident)))
                self._initiative_projects(conn, org_id, ident, inp["projectIds"])
            values["updated_at"] = now_iso()
            conn.execute(initiative.update().where(and_(initiative.c.id == ident, initiative.c.organization_id == org_id)).values(**values))
        return True

    def initiative_archive(self, org_id: str, viewer_id: str, ident: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(initiative.c.creator_id).where(and_(
                initiative.c.id == ident, initiative.c.organization_id == org_id,
                initiative.c.archived_at.is_(None)))).first()
            if row:
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
    def _notify_issue_recipients(conn, org_id, issue_id, actor_id, recipients, kind, ts):
        for recipient_id in set(recipients) - {actor_id, None}:
            conn.execute(inbox_notification.insert().values(
                id=new_id(), organization_id=org_id, recipient_id=recipient_id,
                issue_id=issue_id, actor_id=actor_id, kind=kind, created_at=ts,
            ))

    # -------------------------------------------------------------- issues
    def issue_create(self, org_id: str, viewer_id: str, inp: dict) -> str | None:
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
            _validate_issue_references(
                conn,
                org_id,
                tid,
                inp,
                default_state_id=trow.get("default_state_id"),
            )
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
            prio = int(inp.get("priority", 0) or 0)
            state_id = inp.get("stateId") or trow.get("default_state_id")
            ts = now_iso()
            url_key = _org_url_key(conn, org_id)
            iid = new_id()
            conn.execute(issue.insert().values(
                id=iid, organization_id=org_id, team_id=tid, number=n, identifier=ident,
                title=inp["title"], description=inp.get("description"),
                priority=prio, priority_label=PRIORITY_LABELS.get(prio, "No priority"),
                estimate=inp.get("estimate"), state_id=state_id, state_changed_at=ts,
                assignee_id=inp.get("assigneeId"), creator_id=viewer_id,
                project_id=inp.get("projectId"), cycle_id=inp.get("cycleId"),
                parent_id=inp.get("parentId"),
                url=entity_url(url_key, "issue", ident),
                branch_name=f"{viewer_id[:8]}/{ident.lower()}-{_slug(inp['title'])}",
                due_date=inp.get("dueDate"), created_at=ts, updated_at=ts,
            ))
            self._apply_state_ts(conn, org_id, iid, state_id, ts)
            for lid in (inp.get("labelIds") or []):
                conn.execute(issue_label_link.insert().values(issue_id=iid, label_id=lid))
            self._record_activity(conn, org_id, iid, viewer_id, "issueCreated", {"title": inp["title"]}, ts)
            self._notify_issue_recipients(
                conn, org_id, iid, viewer_id, [inp.get("assigneeId"), viewer_id], "issueCreated", ts
            )
            return iid

    def issue_update(self, org_id: str, ident: str, inp: dict, actor_id: str | None = None) -> str | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(issue).where(and_(
                issue.c.organization_id == org_id,
                (issue.c.id == ident) | (issue.c.identifier == ident.upper())))).first()
            if not row:
                return None
            issue_row = dict(row._mapping)
            iid = issue_row["id"]
            if actor_id is not None:
                self._require_team_visible(conn, org_id, actor_id, issue_row["team_id"])
                if inp.get("teamId") is not None:
                    raise InvalidReferenceError("teamId")
            _validate_issue_references(
                conn, org_id, issue_row["team_id"], inp, issue_id=iid
            )
            ts = now_iso()
            values: dict = {"updated_at": ts}
            mapping = {
                "title": "title", "description": "description", "estimate": "estimate",
                "assigneeId": "assignee_id", "projectId": "project_id",
                "cycleId": "cycle_id", "parentId": "parent_id", "dueDate": "due_date",
            }
            for k, col in mapping.items():
                if k in inp:
                    values[col] = inp[k]
            if "priority" in inp:
                p = int(inp["priority"] or 0)
                values["priority"] = p
                values["priority_label"] = PRIORITY_LABELS.get(p, "No priority")
            if "stateId" in inp:
                values["state_id"] = inp["stateId"]
                if inp["stateId"] != issue_row.get("state_id"):
                    values["state_changed_at"] = ts
            changes = {}
            for field, column in {"title": "title", "description": "description", "priority": "priority", "stateId": "state_id", "assigneeId": "assignee_id"}.items():
                if field in inp and issue_row.get(column) != values.get(column):
                    changes[field] = {"from": issue_row.get(column), "to": values.get(column)}
            conn.execute(issue.update().where(issue.c.id == iid).values(**values))
            if "stateId" in inp:
                self._apply_state_ts(conn, org_id, iid, inp["stateId"], ts)
            if "labelIds" in inp:
                conn.execute(issue_label_link.delete().where(issue_label_link.c.issue_id == iid))
                for lid in (inp.get("labelIds") or []):
                    conn.execute(issue_label_link.insert().values(issue_id=iid, label_id=lid))
            if changes:
                self._record_activity(conn, org_id, iid, actor_id, "issueUpdated", changes, ts)
                self._notify_issue_recipients(
                    conn, org_id, iid, actor_id,
                    [issue_row.get("assignee_id"), values.get("assignee_id", issue_row.get("assignee_id")),
                     issue_row.get("creator_id")], "issueUpdated", ts,
                )
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

    def _authorize_issue_change(self, org_id: str, viewer_id: str, ident: str) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(select(issue.c.creator_id, issue.c.team_id).where(and_(
                issue.c.organization_id == org_id,
                (issue.c.id == ident) | (issue.c.identifier == ident.upper()),
            ))).first()
            if row:
                self._require_team_visible(conn, org_id, viewer_id, row[1])
                self._require_creator_or_admin(conn, org_id, viewer_id, row[0])

    def issue_archive(self, org_id: str, viewer_id: str, ident: str) -> bool:
        self._authorize_issue_change(org_id, viewer_id, ident)
        return self._soft_delete(issue, org_id, ident, by_identifier=True, event_type="issueArchived")

    def issue_delete(self, org_id: str, viewer_id: str, ident: str) -> bool:
        self._authorize_issue_change(org_id, viewer_id, ident)
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
        with self.engine.begin() as conn:
            irow = conn.execute(select(issue).where(and_(
                issue.c.organization_id == org_id,
                (issue.c.id == inp["issueId"]) | (issue.c.identifier == str(inp["issueId"]).upper())))).first()
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
            return cid

    def comment_update(self, org_id: str, viewer_id: str, cid: str, inp: dict) -> str | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(comment.c.id, comment.c.user_id, comment.c.issue_id).where(and_(
                comment.c.organization_id == org_id, comment.c.id == cid))).first()
            if not row:
                return None
            self._require_creator_or_admin(conn, org_id, viewer_id, row[1])
            issue_team = conn.execute(select(issue.c.team_id).where(issue.c.id == row[2])).scalar()
            self._require_team_visible(conn, org_id, viewer_id, issue_team)
            vals = {"updated_at": now_iso()}
            if "body" in inp:
                vals["body"] = inp["body"]
            conn.execute(comment.update().where(comment.c.id == cid).values(**vals))
            issue_id = conn.execute(select(comment.c.issue_id).where(comment.c.id == cid)).scalar_one()
            self._record_activity(conn, org_id, issue_id, None, "commentUpdated", {"commentId": cid}, vals["updated_at"])
            return cid

    def comment_delete(self, org_id: str, viewer_id: str, cid: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(comment.c.user_id).where(and_(
                comment.c.organization_id == org_id, comment.c.id == cid,
                comment.c.archived_at.is_(None)))).first()
            if row:
                self._require_creator_or_admin(conn, org_id, viewer_id, row[0])
        return self._soft_delete(comment, org_id, cid)

    # -------------------------------------------------------------- attachments
    def attachment_create(self, org_id: str, viewer_id: str, inp: dict) -> dict | None:
        with self.engine.begin() as conn:
            parsed_url = urlparse(inp["url"])
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username
                or parsed_url.password
            ):
                raise InvalidReferenceError("url")
            irow = conn.execute(select(issue.c.id).where(and_(
                issue.c.organization_id == org_id,
                (issue.c.id == inp["issueId"])
                | (issue.c.identifier == str(inp["issueId"]).upper()),
            ))).first()
            if not irow:
                return None
            aid = new_id()
            created_at = now_iso()
            conn.execute(attachment.insert().values(
                id=aid,
                organization_id=org_id,
                issue_id=irow[0],
                creator_id=viewer_id,
                url=inp["url"],
                title=inp["title"],
                subtitle=inp.get("subtitle"),
                created_at=created_at,
            ))
            return {
                "id": aid,
                "url": inp["url"],
                "title": inp["title"],
                "subtitle": inp.get("subtitle"),
                "createdAt": created_at,
                "_issue_id": irow[0],
            }

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
        with self.engine.begin() as conn:
            admin = conn.execute(select(user.c.id).where(and_(
                user.c.id == viewer_id, user.c.organization_id == org_id,
                user.c.active.is_(True), user.c.archived_at.is_(None), user.c.admin.is_(True),
            ))).first()
            if not admin:
                raise GraphQLError("Admin access required", extensions={"code": "FORBIDDEN"})
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
        with self.engine.begin() as conn:
            if lead_id := inp.get("leadId"):
                _require_org_reference(
                    conn, user, org_id, lead_id, "leadId",
                    user.c.active.is_(True),
                )
            team_ids = set(inp.get("teamIds") or [])
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
            slug = _slug(inp["name"])
            url_key = _org_url_key(conn, org_id)
            conn.execute(project.insert().values(
                id=pid, organization_id=org_id, name=inp["name"],
                description=inp.get("description"), slug_id=slug,
                icon=inp.get("icon"), color=inp.get("color"),
                state=inp.get("state", "backlog"), progress=0,
                lead_id=inp.get("leadId"), creator_id=viewer_id,
                start_date=inp.get("startDate"), target_date=inp.get("targetDate"),
                url=entity_url(url_key, "project", slug),
                created_at=ts, updated_at=ts))
            conn.execute(project_member.insert().values(project_id=pid, user_id=viewer_id))
            for team_id in team_ids:
                conn.execute(project_team.insert().values(project_id=pid, team_id=team_id))
            return pid

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
                actor_id=actor_id, body=body, status=inp.get("status"),
                progress=row.get("progress"), created_at=ts,
            ))
            return update_id

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
        with self.engine.begin() as conn:
            row = conn.execute(select(project.c.id).where(and_(
                project.c.organization_id == org_id, project.c.id == pid))).first()
            if not row:
                return None
            if not self._require_project_owner(conn, org_id, viewer_id, pid):
                return None
            if lead_id := inp.get("leadId"):
                _require_org_reference(
                    conn, user, org_id, lead_id, "leadId",
                    user.c.active.is_(True),
                )
            vals = {"updated_at": now_iso()}
            m = {"name": "name", "description": "description", "leadId": "lead_id",
                 "state": "state", "color": "color", "icon": "icon",
                 "startDate": "start_date", "targetDate": "target_date"}
            for k, col in m.items():
                if k in inp:
                    vals[col] = inp[k]
            conn.execute(project.update().where(project.c.id == pid).values(**vals))
            return pid

    def project_archive(self, org_id: str, viewer_id: str, pid: str) -> bool:
        with self.engine.begin() as conn:
            if not self._require_project_owner(conn, org_id, viewer_id, pid):
                return False
        return self._soft_delete(project, org_id, pid)
