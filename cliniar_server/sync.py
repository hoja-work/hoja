"""Compact browser sync: one bootstrap snapshot plus seq-based deltas.

The browser used to page issues 100 at a time through GraphQL and refetch the
whole dashboard on every change event. This module serves:

- ``bootstrap``: every open issue plus issues closed within ``closed_days``,
  in a columnar (header + row arrays) shape, alongside the small workspace
  metadata the shell needs. The response carries the org change ``seq`` and a
  validator so an unchanged snapshot revalidates as a bodyless 304.
- ``delta``: the issue rows touched after a seq, ids that disappeared, and
  fresh metadata only when a metadata entity changed.

Issue rows are normalized (ids, not nested objects); the client hydrates them
against the metadata it already holds.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from cliniar_server.db import (
    issue,
    issue_label_link,
    issue_subscriber,
    user,
    workflow_state,
)
from cliniar_server.store import Store, visible_team_clause

SYNC_VERSION = 1
DEFAULT_CLOSED_DAYS = 14
MAX_CLOSED_DAYS = 365
# A client further behind than this re-bootstraps instead of replaying.
MAX_DELTA_EVENTS = 5000

ISSUE_COLUMNS = (
    "id", "identifier", "title", "priority", "estimate", "createdAt", "updatedAt",
    "stateChangedAt", "dueDate", "stateId", "teamId", "projectId", "assigneeId",
    "creatorId", "cycleId", "parentId", "labelIds", "subscriberIds",
)
TEAM_COLUMN = ISSUE_COLUMNS.index("teamId")
_CLOSED_TYPES = ("completed", "canceled")
# Entities whose change invalidates the metadata block rather than issue rows.
META_ENTITIES = {"team", "teamMember", "workflowState", "label", "cycle", "project", "user", "member"}
# Entities that change who can see which issues; a delta cannot express that.
RELOAD_ENTITIES = {"team", "teamMember"}

META_QUERY = """query SyncMeta {
  viewer { id name email admin }
  workspace { id name urlKey }
  teams { nodes { id name key description states { nodes { id name color type } }
    members { nodes { id name email } } activeCycle { id name number startsAt endsAt progress } } }
  projects(first: 250) { nodes { id name description state progress startDate targetDate health
    lead { name } teams { nodes { id name key } } } pageInfo { hasNextPage endCursor } }
  cycles(first: 250) { nodes { id name number startsAt endsAt completedAt progress team { id name key } } }
  issueLabels(first: 250) { nodes { id name color } }
}"""


class SingleFlightCache:
    """Small LRU whose misses are built once even when many requests miss together.

    Python's sqlite3 driver re-acquires the GIL per fetched row, so N threads
    reading the same snapshot run far slower than one; sharing the build keeps a
    burst of page loads at the cost of a single query.
    """

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict = OrderedDict()
        self._inflight: dict = {}

    async def get(self, key, build: Callable[[], Awaitable]):
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key]
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await build()
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved when nobody else was waiting
            raise
        finally:
            self._inflight.pop(key, None)
        future.set_result(value)
        self._entries[key] = value
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return value


def closed_cutoff(closed_days: int, now: datetime | None = None) -> str:
    """Day-granular ISO date; issue timestamps compare lexicographically against it."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=closed_days)).date().isoformat()


def sync_scope(org_id: str, viewer_id: str) -> str:
    """Opaque identity of a snapshot's owner; a delta for another scope must re-bootstrap."""
    return hashlib.sha256(f"{org_id}|{viewer_id}".encode()).hexdigest()[:16]


def org_users(store: Store, org_id: str) -> list[list]:
    """[id, name] for every profile in the org, so creators and assignees always resolve."""
    with store.engine.connect() as conn:
        return [list(r) for r in conn.execute(select(user.c.id, user.c.name)
                                              .where(user.c.organization_id == org_id))]


def _visibility_key(visible: frozenset | None) -> str:
    if visible is None:
        return "*"
    return hashlib.sha256(",".join(sorted(visible)).encode()).hexdigest()[:16]


def issues_key(org_id: str, seq: int, cutoff: str, visible: frozenset | None) -> tuple:
    """Issue rows depend only on these, so viewers with equal visibility share them."""
    return (org_id, seq, cutoff, _visibility_key(visible))


def snapshot_etag(viewer_id: str, key: tuple) -> str:
    """Validator for a bootstrap: changes whenever any captured row, visibility or window changes."""
    raw = f"v{SYNC_VERSION}|{viewer_id}|" + "|".join(map(str, key))
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:32] + '"'


def _issue_select(conn, org_id: str, viewer_id: str | None):
    stmt = (
        select(
            issue.c.id, issue.c.identifier, issue.c.title, issue.c.priority, issue.c.estimate,
            issue.c.created_at, issue.c.updated_at, issue.c.state_changed_at, issue.c.due_date,
            issue.c.state_id, issue.c.team_id, issue.c.project_id, issue.c.assignee_id,
            issue.c.creator_id, issue.c.cycle_id, issue.c.parent_id,
        )
        .select_from(issue.outerjoin(workflow_state, workflow_state.c.id == issue.c.state_id))
        .where(and_(issue.c.organization_id == org_id, issue.c.archived_at.is_(None)))
    )
    if viewer_id is None:
        return stmt  # unscoped: callers filter rows by teamId per viewer themselves
    clause = visible_team_clause(conn, org_id, viewer_id, issue.c.team_id)
    return stmt if clause is None else stmt.where(clause)


def _rows_with_links(conn, stmt) -> list[list]:
    rows = conn.execute(stmt.order_by(issue.c.updated_at.desc(), issue.c.id.desc())).all()
    if not rows:
        return []
    ids = stmt.with_only_columns(issue.c.id).order_by(None).subquery()
    labels: dict[str, list[str]] = {}
    for iid, lid in conn.execute(select(issue_label_link.c.issue_id, issue_label_link.c.label_id)
                                 .where(issue_label_link.c.issue_id.in_(select(ids.c.id)))):
        labels.setdefault(iid, []).append(lid)
    subs: dict[str, list[str]] = {}
    for iid, uid in conn.execute(select(issue_subscriber.c.issue_id, issue_subscriber.c.user_id)
                                 .where(issue_subscriber.c.issue_id.in_(select(ids.c.id)))):
        subs.setdefault(iid, []).append(uid)
    return [[*row, labels.get(row[0], []), subs.get(row[0], [])] for row in rows]


def bootstrap_issues(store: Store, org_id: str, viewer_id: str, cutoff: str) -> list[list]:
    with store.engine.connect() as conn:
        stmt = _issue_select(conn, org_id, viewer_id).where(or_(
            workflow_state.c.type.is_(None),
            workflow_state.c.type.not_in(_CLOSED_TYPES),
            issue.c.updated_at >= cutoff,
        ))
        return _rows_with_links(conn, stmt)


def issues_by_id(store: Store, org_id: str, viewer_id: str | None, ids: list[str]) -> list[list]:
    """Rows for ids; viewer_id=None returns them unscoped (the caller must filter by teamId)."""
    out: list[list] = []
    with store.engine.connect() as conn:
        # Chunked to stay under SQLite's bound-parameter limit.
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            out.extend(_rows_with_links(conn, _issue_select(conn, org_id, viewer_id)
                                        .where(issue.c.id.in_(chunk))))
    return out


def row_visible(row: list, visible: frozenset | None) -> bool:
    """Issue visibility is team visibility; None means unrestricted (admin)."""
    return visible is None or row[TEAM_COLUMN] in visible


def build_delta(store: Store, org_id: str, viewer_id: str, after: int, scope: str | None = None) -> dict:
    """Changes after ``after``; ``reload`` tells the client to re-bootstrap instead."""
    latest = store.latest_change_seq(org_id)
    if after > latest or (scope is not None and scope != sync_scope(org_id, viewer_id)):
        return {"v": SYNC_VERSION, "reload": True, "seq": latest}
    events = store.change_events_after(org_id, after, limit=MAX_DELTA_EVENTS + 1, viewer_id=viewer_id)
    if len(events) > MAX_DELTA_EVENTS:
        return {"v": SYNC_VERSION, "reload": True, "seq": latest}
    entities = {e["entity"] for e in events}
    if entities & RELOAD_ENTITIES:
        return {"v": SYNC_VERSION, "reload": True, "seq": latest}
    touched = list(dict.fromkeys(e["entityId"] for e in events if e["entity"] == "issue"))
    rows = issues_by_id(store, org_id, viewer_id, touched) if touched else []
    present = {r[0] for r in rows}
    return {
        "v": SYNC_VERSION,
        "reload": False,
        # Invisible events still advance the cursor: everything up to latest was considered.
        "seq": max([latest] + [e["seq"] for e in events]),
        "columns": ISSUE_COLUMNS,
        "issues": rows,
        "removed": [i for i in touched if i not in present],
        "meta": bool(entities & META_ENTITIES),
        "other": sorted(entities - META_ENTITIES - {"issue"}),
    }


STATE_COLUMN = ISSUE_COLUMNS.index("stateId")
UPDATED_COLUMN = ISSUE_COLUMNS.index("updatedAt")


def closed_state_ids(store: Store, org_id: str) -> frozenset:
    with store.engine.connect() as conn:
        return frozenset(r[0] for r in conn.execute(select(workflow_state.c.id).where(and_(
            workflow_state.c.organization_id == org_id, workflow_state.c.type.in_(_CLOSED_TYPES)))))


def derive_issues(prev_rows: list[list], changes: dict, visible: frozenset | None, cutoff: str,
                  closed_states: frozenset) -> list[list]:
    """A newer bootstrap issue list from an older one plus the changed rows (None = gone).

    Applies the same window and visibility rules as bootstrap_issues, so a page
    load during a burst of writes costs a merge instead of re-reading every row.
    """
    out = [r for r in prev_rows if r[0] not in changes]
    for row in changes.values():
        if row is None or not row_visible(row, visible):
            continue
        if row[STATE_COLUMN] in closed_states and (row[UPDATED_COLUMN] or "") < cutoff:
            continue
        out.append(row)
    out.sort(key=lambda r: (r[UPDATED_COLUMN] or "", r[0]), reverse=True)
    return out
