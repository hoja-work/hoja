"""Announce cycle starts and ends on time, for every team.

The cadence rollover (``cadence.py``) announces a cycle when it moves a team's
active pointer, but it runs only when someone reads that team's cycles, and a team
with hand-made cycles has no rollover at all. This sweep runs from the app lifespan
in system context, like the webhook worker and the lease reaper, and announces any
cycle whose start or end has passed:

- **started**: ``starts_at`` has passed, the cycle is not archived, and it has not
  already ended (a cycle created entirely in the past is stamped, not announced).
- **ended**: ``completed_at`` is set or ``ends_at`` has passed. The counts are taken
  now: issues still open are ``unfinished`` (the rollover, if one runs later, carries
  them; its own announcement is suppressed by the stamp).

Each edge is claimed with ``notifications.claim_cycle_event`` before anything is
sent, so the sweep, the rollover and a second app process never announce twice.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine

from hoja_server.db import cycle, now_iso, team

log = logging.getLogger(__name__)

SWEEP_INTERVAL_S = 60
BATCH = 200


def _instant(value: str | None) -> datetime | None:
    """A stored cycle edge as an aware UTC instant.

    Cadence cycles store ``isoformat()`` with an offset; dates edited by hand are
    stored as the client sent them (``2026-11-20``, ``...Z``, another offset). The
    query narrows by text with a day of slack; this decides exactly.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sweep_once(engine: Engine, now: str | None = None) -> dict[str, int]:
    """Announce every due edge; returns how many starts and ends were claimed."""
    from hoja_server import cadence, notifications

    ts = now or now_iso()
    current = _instant(ts) or datetime.now(timezone.utc)
    # Text bound with a day of slack for offsets and bare dates; _instant decides.
    horizon = (current + timedelta(days=1)).isoformat()
    counts = {"started": 0, "ended": 0, "skipped": 0}
    with engine.begin() as conn:
        due_start = conn.execute(select(cycle, team.c.key.label("team_key")).select_from(
            cycle.join(team, team.c.id == cycle.c.team_id)).where(and_(
                cycle.c.archived_at.is_(None),
                cycle.c.start_notified_at.is_(None),
                cycle.c.starts_at.is_not(None),
                cycle.c.starts_at <= horizon)).order_by(cycle.c.starts_at).limit(BATCH)).mappings().all()
        for row in due_start:
            row = dict(row)
            starts = _instant(row.get("starts_at"))
            if starts is None or starts > current:
                continue
            ends = _instant(row.get("ends_at"))
            already_over = bool(row.get("completed_at")) or (ends is not None and ends <= current)
            if already_over:
                # Nobody was told it started while it ran; saying so now would be noise.
                if notifications.claim_cycle_event(conn, row["id"], "start", ts):
                    counts["skipped"] += 1
                continue
            if notifications.on_cycle_started(conn, row["organization_id"], row, row["team_key"], ts):
                counts["started"] += 1
        due_end = conn.execute(select(cycle, team.c.key.label("team_key")).select_from(
            cycle.join(team, team.c.id == cycle.c.team_id)).where(and_(
                cycle.c.archived_at.is_(None),
                cycle.c.end_notified_at.is_(None),
                or_(cycle.c.completed_at.is_not(None),
                    and_(cycle.c.ends_at.is_not(None), cycle.c.ends_at <= horizon)))).order_by(
                cycle.c.ends_at).limit(BATCH)).mappings().all()
        for row in due_end:
            row = dict(row)
            ends = _instant(row.get("ends_at"))
            if not row.get("completed_at") and (ends is None or ends > current):
                continue
            completed, total, _carried = cadence._cycle_tally(conn, row["organization_id"], row["id"])
            if notifications.on_cycle_completed(
                    conn, row["organization_id"], row, row["team_key"],
                    completed, total, 0, ts, unfinished=max(0, total - completed)):
                counts["ended"] += 1
    return counts


async def run_forever(engine: Engine, interval_s: int = SWEEP_INTERVAL_S) -> None:
    """Background loop started by the app lifespan, in system context."""
    while True:
        try:
            counts = await asyncio.to_thread(sweep_once, engine)
            if counts["started"] or counts["ended"]:
                log.info("cycle events: %s", counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("cycle event sweep failed", exc_info=True)
        await asyncio.sleep(interval_s)
