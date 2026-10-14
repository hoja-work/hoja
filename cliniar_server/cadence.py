"""Pure team-local cycle boundary calculations for fixed weekly cadence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BOUNDARY_TIME = time(0, 1)
POLICY_VERSION = 1
DURATION_WEEKS = 1
COOLDOWN_WEEKS = 0
UPCOMING_COUNT = 2


class CadenceInputError(ValueError):
    """Invalid timezone, weekday, or local boundary in cadence settings."""


@dataclass(frozen=True)
class CycleInterval:
    starts_at: datetime
    ends_at: datetime


def _local_boundary(day: date, zone: ZoneInfo) -> datetime:
    """Resolve 00:01 strictly; reject DST gaps and folds instead of guessing."""
    naive = datetime.combine(day, BOUNDARY_TIME)
    candidates = [naive.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [candidate for candidate in candidates
             if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive]
    unique_instants = {candidate.astimezone(timezone.utc) for candidate in valid}
    if len(unique_instants) != 1:
        reason = "nonexistent" if not unique_instants else "ambiguous"
        raise CadenceInputError(f"Local cycle boundary {naive.isoformat()} is {reason} in {zone.key}.")
    return valid[0]


def calculate_boundaries(
    *, timezone_name: str, weekday: int, now: datetime | None = None,
    upcoming_count: int = UPCOMING_COUNT,
) -> tuple[CycleInterval, ...]:
    """Return Current (when due) and upcoming weekly intervals in UTC.

    weekday follows ``date.weekday()`` (Monday=0). Activation starts at today's
    local 00:01; subsequent boundaries align to the chosen weekday. Intervals
    are half-open and each endpoint is independently localized across DST.
    """
    if not isinstance(weekday, int) or isinstance(weekday, bool) or not 0 <= weekday <= 6:
        raise CadenceInputError("weekday must be an integer from 0 (Monday) through 6 (Sunday).")
    if upcoming_count < 0:
        raise CadenceInputError("upcoming_count cannot be negative.")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise CadenceInputError(f"Unknown IANA timezone: {timezone_name!r}.") from exc
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise CadenceInputError("now must be timezone-aware.")
    local_now = instant.astimezone(zone)
    today = local_now.date()
    today_start = _local_boundary(today, zone)
    if today_start <= local_now:
        current_day = today
        next_day = today + timedelta(days=(weekday - today.weekday()) % 7 or 7)
        # At a selected weekday's 00:01 boundary, the current interval is full week.
        if current_day.weekday() == weekday:
            next_day = current_day + timedelta(days=7)
    else:
        # Before today's 00:01, do not backdate a Current cycle. If today
        # is the selected weekday, its first full cycle starts at 00:01.
        current_day = today if today.weekday() == weekday else today + timedelta(days=(weekday - today.weekday()) % 7)
        next_day = current_day + timedelta(days=7)
    start = _local_boundary(current_day, zone)
    boundaries = [start, _local_boundary(next_day, zone)]
    while len(boundaries) < upcoming_count + 2:
        next_local_date = boundaries[-1].astimezone(zone).date() + timedelta(days=7)
        boundaries.append(_local_boundary(next_local_date, zone))
    utc_boundaries = [boundary.astimezone(timezone.utc) for boundary in boundaries]
    return tuple(CycleInterval(a, b) for a, b in zip(utc_boundaries, utc_boundaries[1:]))


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise CadenceInputError(f"Unknown IANA timezone: {name!r}.") from exc


def _rollover_cycle_issues(conn, org_id: str, team_id: str,
                           from_cycle_ids: list, to_cycle_id: str, timestamp: str) -> int:
    """Move unfinished issues from just-closed cycles into the new active cycle.

    Mirrors Linear's "Issues rollover": open issues (unstarted/started states)
    carry forward automatically; backlog, triage, completed, and canceled
    issues stay behind. There is no way to keep an unfinished issue in a
    closed cycle. Returns the number of issues moved.
    """
    from sqlalchemy import and_, select, update
    from cliniar_server.db import issue, workflow_state
    movable = (
        select(issue.c.id)
        .select_from(issue.join(workflow_state, workflow_state.c.id == issue.c.state_id))
        .where(and_(
            issue.c.organization_id == org_id,
            issue.c.team_id == team_id,
            issue.c.cycle_id.in_(from_cycle_ids),
            issue.c.archived_at.is_(None),
            workflow_state.c.type.in_(("unstarted", "started")),
        ))
    )
    result = conn.execute(
        update(issue)
        .where(issue.c.id.in_(movable))
        .values(cycle_id=to_cycle_id, updated_at=timestamp)
    )
    return result.rowcount or 0


def reconcile_team_cadence(conn, org_id: str, row, now: datetime) -> dict:
    """Within a caller-held team write transaction, ensure Current and two Upcoming.

    A bounded reconciliation fails atomically on excessive downtime rather than
    returning an apparently complete but stale schedule.
    """
    from sqlalchemy import and_, select
    from cliniar_server.db import cycle, new_id, now_iso, team

    zone_name = row.get("timezone") or "UTC"
    validate_timezone(zone_name)
    team_id = row["id"]
    enabled = bool(row.get("cadence_enabled"))
    if not enabled:
        return {"enabled": False, "weekday": None, "timezone": zone_name,
                "policyVersion": 1, "activeCycle": None, "upcomingCycles": []}
    weekday = row.get("cadence_weekday")
    if type(weekday) is not int or weekday not in range(7):
        raise CadenceInputError("Team cadence weekday is invalid.")
    if (row.get("cadence_policy_version") != POLICY_VERSION or
        row.get("cadence_duration_weeks") != DURATION_WEEKS or
        row.get("cadence_cooldown_weeks") != COOLDOWN_WEEKS or
        row.get("cadence_upcoming_count") != UPCOMING_COUNT):
        raise CadenceInputError("Unsupported team cycle policy; review settings before retrying.")
    anchor_text = row.get("cadence_anchor_at")
    if not anchor_text:
        raise CadenceInputError("Team cycle anchor is missing.")
    anchor = datetime.fromisoformat(anchor_text)
    if anchor.tzinfo is None or now.tzinfo is None:
        raise CadenceInputError("Cadence timestamps must be timezone-aware.")
    current = now.astimezone(timezone.utc)
    initial = calculate_boundaries(timezone_name=zone_name, weekday=weekday, now=anchor)
    initial_start = initial[0].starts_at
    before_start = current < initial_start
    # Subsequent windows are calculated from local calendar dates, never 168 UTC hours.
    local_zone = validate_timezone(zone_name)
    start = initial[0].starts_at
    first_end = initial[0].ends_at
    boundaries = [start, first_end]
    max_intervals = 256
    while boundaries[-1] <= current:
        if len(boundaries) >= max_intervals:
            raise CadenceInputError("Cycle catch-up exceeds 255 intervals; manual review is required.")
        previous_day = boundaries[-1].astimezone(local_zone).date()
        next_day = previous_day + timedelta(days=7)
        boundaries.append(_local_boundary(next_day, local_zone).astimezone(timezone.utc))
    # Before the initial boundary, first two intervals are Upcoming. Otherwise
    # retain one active and two future slots; elapsed periods stay bounded.
    for _ in range(1 if before_start else 2):
        previous_day = boundaries[-1].astimezone(local_zone).date()
        boundaries.append(_local_boundary(previous_day + timedelta(days=7), local_zone).astimezone(timezone.utc))
    # Boundaries contain the current window (if due) and two upcoming slots.
    intervals = list(zip(boundaries, boundaries[1:]))
    persisted = conn.execute(select(cycle).where(and_(cycle.c.organization_id == org_id,
        cycle.c.team_id == team_id)).order_by(cycle.c.number)).mappings().all()
    if any(r["cadence_boundary_at"] is None for r in persisted):
        raise CadenceInputError("Existing unmanaged cycles require manual reconciliation.")
    # A stale clock or delayed request must never demote the persisted Current
    # pointer or rewrite completed history. Fail before any schedule insert.
    pointer = row.get("active_cycle_id")
    if pointer:
        pointed = next((r for r in persisted if r["id"] == pointer), None)
        if pointed is None:
            raise CadenceInputError("Active cycle pointer is missing from this team.")
        if current < datetime.fromisoformat(pointed["starts_at"]):
            raise CadenceInputError("Clock precedes the active cycle; retry after correcting the clock.")
    if len(persisted) > len(intervals):
        # Retain already generated future slots; their starts must continue
        # the same weekly local calendar cadence without changing past data.
        for r in persisted[len(intervals):]:
            prior_end = intervals[-1][1]
            next_end_day = prior_end.astimezone(local_zone).date() + timedelta(days=7)
            expected_end = _local_boundary(next_end_day, local_zone).astimezone(timezone.utc)
            if r["starts_at"] != prior_end.isoformat() or r["ends_at"] != expected_end.isoformat():
                raise CadenceInputError("Persisted future cycle conflicts with the cadence.")
            intervals.append((prior_end, expected_end))
    timestamp = now_iso()
    for idx, (begin, end) in enumerate(intervals, start=1):
        key = begin.isoformat()
        if idx <= len(persisted):
            existing = persisted[idx - 1]
            if existing["number"] != idx or existing["cadence_boundary_at"] != key or existing["ends_at"] != end.isoformat():
                raise CadenceInputError("Cycle schedule conflicts with persisted intervals.")
        else:
            conn.execute(cycle.insert().values(id=new_id(), organization_id=org_id,
                team_id=team_id, number=idx, name=f"Cycle {idx}",
                starts_at=key, ends_at=end.isoformat(), cadence_boundary_at=key,
                progress=0, created_at=timestamp, updated_at=timestamp))
    active_number = None if before_start else next(i for i, (begin, end) in enumerate(intervals, 1) if begin <= current < end)
    active = (conn.execute(select(cycle).where(and_(cycle.c.team_id == team_id,
        cycle.c.number == active_number))).mappings().one() if active_number else None)
    if active:
        newly_completed = []
        for past in persisted:
            if past["number"] < active_number and not past["completed_at"]:
                conn.execute(cycle.update().where(cycle.c.id == past["id"]).values(completed_at=past["ends_at"], updated_at=timestamp))
                newly_completed.append(past["id"])
        if newly_completed:
            # Linear-style rollover: unfinished issues follow the active pointer.
            _rollover_cycle_issues(conn, org_id, team_id, newly_completed, active["id"], timestamp)
    if row.get("active_cycle_id") != (active["id"] if active else None):
        conn.execute(team.update().where(and_(team.c.organization_id == org_id,
            team.c.id == team_id)).values(active_cycle_id=active["id"] if active else None, updated_at=timestamp))
    upcoming = conn.execute(select(cycle).where(and_(cycle.c.organization_id == org_id,
        cycle.c.team_id == team_id, cycle.c.number > (active_number or 0))).order_by(cycle.c.number).limit(2)).mappings().all()
    def serialize(r):
        return {"id": r["id"], "name": r["name"], "number": r["number"],
                "startsAt": r["starts_at"], "endsAt": r["ends_at"],
                "completedAt": r["completed_at"], "progress": r["progress"],
                "_team_id": team_id}
    return {"enabled": True, "weekday": weekday, "timezone": zone_name,
            "policyVersion": POLICY_VERSION, "activeCycle": serialize(active) if active else None,
            "upcomingCycles": [serialize(r) for r in upcoming]}
