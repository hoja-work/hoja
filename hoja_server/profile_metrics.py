"""Member profiles, daily activity heatmaps, and token-burner leaderboards.

Read-only analytics over ``usage_event``, ``agent_session``, ``issue``, and
``issue_activity``. Every query is scoped strictly to the caller's workspace
(``organization_id``) and exposes operational usage (tokens, credits, run
minutes, completed tasks, actions) rather than billing dollar amounts, so
every active workspace member can view profiles and leaderboards without
weakening the admin-only billing ledger gate in ``usage_ledger.py``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ariadne import QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, desc, select

from hoja_server.db import (
    agent_session,
    issue,
    issue_activity,
    team,
    team_member,
    usage_event,
    user,
    workflow_state,
)

DEFAULT_DAYS = 30
MAX_DAYS = 365
DEFAULT_HEATMAP_DAYS = 182
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

query = QueryType()

SDL = gql(
    """
    type ActivityDay {
      "ISO calendar date in UTC (YYYY-MM-DD)."
      date: String!
      tokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
      completedTasks: Int!
      actions: Int!
      "Normalized activity bucket 0..4 for heatmap cells."
      intensity: Int!
    }

    type PersonLeaderboardRow {
      userId: ID!
      name: String!
      displayName: String
      email: String
      avatarSeed: String
      kind: String!
      tokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
      completedTasks: Int!
      openTasks: Int!
      actions: Int!
      activeDays: Int!
    }

    type TaskLeaderboardRow {
      issueId: ID!
      identifier: String!
      title: String!
      stateName: String
      stateColor: String
      stateType: String
      assigneeId: ID
      assigneeName: String
      assigneeAvatarSeed: String
      delegateId: ID
      delegateName: String
      delegateAvatarSeed: String
      tokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
    }

    type AgentLeaderboardRow {
      agentUserId: ID!
      name: String!
      avatarSeed: String
      tokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
      completedRuns: Int!
    }

    type ActivityLeaderboard {
      days: Int!
      heatmapDays: Int!
      totalTokens: Int!
      totalInputTokens: Int!
      totalOutputTokens: Int!
      totalCacheReadTokens: Int!
      totalCredits: Int!
      totalRunMinutes: Int!
      totalRuns: Int!
      totalCompletedTasks: Int!
      totalActions: Int!
      daily: [ActivityDay!]!
      people: [PersonLeaderboardRow!]!
      tasks: [TaskLeaderboardRow!]!
      agents: [AgentLeaderboardRow!]!
    }

    type ProfileUser {
      id: ID!
      name: String!
      displayName: String
      email: String
      avatarSeed: String
      admin: Boolean!
      active: Boolean!
      kind: String!
      timezone: String
      statusEmoji: String
      statusLabel: String
      createdAt: String
    }

    type ProfileTeam {
      id: ID!
      key: String!
      name: String!
    }

    type ProfileTaskSummary {
      id: ID!
      identifier: String!
      title: String!
      priority: Int!
      stateName: String
      stateColor: String
      stateType: String
      updatedAt: String
      tokens: Int!
      runMinutes: Int!
    }

    type ProfileActivityItem {
      id: ID!
      eventType: String!
      createdAt: String!
      issueId: ID!
      issueIdentifier: String!
      issueTitle: String!
    }

    type MemberProfile {
      user: ProfileUser!
      teams: [ProfileTeam!]!
      days: Int!
      heatmapDays: Int!
      totalTokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
      completedTasks: Int!
      openTasks: Int!
      createdTasks: Int!
      actions: Int!
      activeDays: Int!
      currentStreak: Int!
      longestStreak: Int!
      rankByTokens: Int
      rankByCompleted: Int
      daily: [ActivityDay!]!
      topTasks: [TaskLeaderboardRow!]!
      assignedTasks: [ProfileTaskSummary!]!
      recentActivity: [ProfileActivityItem!]!
    }

    type TaskModelUsage {
      agentUserId: ID!
      agentName: String!
      agentAvatarSeed: String
      model: String!
      tokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
    }

    type TaskUsageSummary {
      issueId: ID!
      tokens: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
      breakdown: [TaskModelUsage!]!
    }

    extend type Query {
      "Workspace activity heatmap and token/shipping leaderboards."
      activityLeaderboard(days: Int, heatmapDays: Int, limit: Int): ActivityLeaderboard!
      "One workspace member's profile, activity heatmap, open work and token stats."
      memberProfile(userId: ID!, days: Int, heatmapDays: Int): MemberProfile
      "Recorded token and runtime usage for a single task (by id or identifier)."
      taskUsage(issueId: String!): TaskUsageSummary
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _clamp_days(raw: int | None, default: int = DEFAULT_DAYS) -> int:
    if raw is None:
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise GraphQLError("days must be an integer.", extensions={"code": "BAD_USER_INPUT"}) from None
    return max(1, min(n, MAX_DAYS))


def _clamp_limit(raw: int | None) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise GraphQLError("limit must be an integer.", extensions={"code": "BAD_USER_INPUT"}) from None
    return max(1, min(n, MAX_LIMIT))


def _day_str(stamp: str | None) -> str | None:
    if not stamp or len(stamp) < 10:
        return None
    candidate = str(stamp)[:10]
    if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
        return candidate
    return None


def _tokens_of(inp: Any, out: Any, cache: Any) -> int:
    return max(0, int(inp or 0)) + max(0, int(out or 0)) + max(0, int(cache or 0))


def _score_day(day: dict[str, Any]) -> int:
    # Combine actions, completed tasks, and token activity into a single positive signal
    # so the default heatmap lights up whether a workspace uses agents heavily or ships tasks manually.
    token_points = 0
    if day["tokens"] > 0:
        token_points = max(1, day["tokens"] // 25_000)
    return day["actions"] + day["completedTasks"] * 3 + day["runs"] * 2 + token_points


def _build_daily_series(
    raw_days: dict[str, dict[str, Any]],
    *,
    end_day: date,
    num_days: int,
) -> list[dict[str, Any]]:
    start_day = end_day - timedelta(days=num_days - 1)
    series: list[dict[str, Any]] = []
    scores: list[int] = []
    for offset in range(num_days):
        d = (start_day + timedelta(days=offset)).isoformat()
        bucket = raw_days.get(d)
        entry = {
            "date": d,
            "tokens": int(bucket["tokens"]) if bucket else 0,
            "inputTokens": int(bucket["inputTokens"]) if bucket else 0,
            "outputTokens": int(bucket["outputTokens"]) if bucket else 0,
            "cacheReadTokens": int(bucket["cacheReadTokens"]) if bucket else 0,
            "credits": int(bucket["credits"]) if bucket else 0,
            "runMinutes": int(bucket["runMinutes"]) if bucket else 0,
            "runs": len(bucket["runIds"]) if bucket else 0,
            "completedTasks": int(bucket["completedTasks"]) if bucket else 0,
            "actions": int(bucket["actions"]) if bucket else 0,
            "intensity": 0,
        }
        sc = _score_day(entry)
        if sc > 0:
            scores.append(sc)
        series.append(entry)

    if scores:
        peak = max(scores)
        for entry in series:
            sc = _score_day(entry)
            if sc <= 0:
                entry["intensity"] = 0
            else:
                ratio = sc / peak
                if ratio <= 0.25:
                    entry["intensity"] = 1
                elif ratio <= 0.5:
                    entry["intensity"] = 2
                elif ratio <= 0.75:
                    entry["intensity"] = 3
                else:
                    entry["intensity"] = 4
    return series


def _streaks(daily: list[dict[str, Any]]) -> tuple[int, int]:
    longest = 0
    run = 0
    for entry in daily:
        if entry["intensity"] > 0:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0

    current = 0
    # Allow today to still be in progress without zeroing out yesterday's streak.
    idx = len(daily) - 1
    if idx >= 0 and daily[idx]["intensity"] == 0:
        idx -= 1
    while idx >= 0 and daily[idx]["intensity"] > 0:
        current += 1
        idx -= 1
    return current, longest


def _empty_day_bucket() -> dict[str, Any]:
    return {
        "tokens": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "credits": 0,
        "runMinutes": 0,
        "runIds": set(),
        "completedTasks": 0,
        "actions": 0,
    }


def _load_workspace_context(conn, org_id: str) -> dict[str, Any]:
    users = {
        str(r["id"]): dict(r)
        for r in conn.execute(
            select(user).where(user.c.organization_id == org_id)
        ).mappings().all()
    }
    states = {
        str(r["id"]): dict(r)
        for r in conn.execute(
            select(workflow_state).where(workflow_state.c.organization_id == org_id)
        ).mappings().all()
    }
    issues = {
        str(r["id"]): dict(r)
        for r in conn.execute(
            select(issue).where(
                and_(issue.c.organization_id == org_id, issue.c.archived_at.is_(None))
            )
        ).mappings().all()
    }
    sessions = {
        str(r["id"]): dict(r)
        for r in conn.execute(
            select(agent_session).where(agent_session.c.organization_id == org_id)
        ).mappings().all()
    }
    return {"users": users, "states": states, "issues": issues, "sessions": sessions}


def _display_name(row: dict[str, Any] | None, fallback: str) -> str:
    if not row:
        return fallback
    return str(row.get("name") or row.get("display_name") or row.get("email") or fallback)


def _usage_human_owner(
    ev: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    issues: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return (issue_id, human_user_id) for one usage_event row."""
    sess = sessions.get(str(ev["session_id"])) if ev.get("session_id") else None
    issue_id = str(sess["issue_id"]) if sess and sess.get("issue_id") else None
    iss = issues.get(issue_id) if issue_id else None
    human_id = None
    if sess and sess.get("creator_id"):
        human_id = str(sess["creator_id"])
    elif iss and iss.get("assignee_id"):
        human_id = str(iss["assignee_id"])
    elif iss and iss.get("creator_id"):
        human_id = str(iss["creator_id"])
    return issue_id, human_id


def compute_leaderboard(
    conn,
    org_id: str,
    *,
    days: int = DEFAULT_DAYS,
    heatmap_days: int = DEFAULT_HEATMAP_DAYS,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    today = now_dt.date()
    window_start = (today - timedelta(days=days - 1)).isoformat()
    heatmap_start = (today - timedelta(days=heatmap_days - 1)).isoformat()
    earliest_iso = min(window_start, heatmap_start)

    ctx = _load_workspace_context(conn, org_id)
    users = ctx["users"]
    states = ctx["states"]
    issues = ctx["issues"]
    sessions = ctx["sessions"]

    usage_rows = conn.execute(
        select(usage_event).where(
            and_(
                usage_event.c.organization_id == org_id,
                usage_event.c.recorded_at >= earliest_iso,
            )
        )
    ).mappings().all()

    activity_rows = conn.execute(
        select(
            issue_activity.c.id,
            issue_activity.c.actor_id,
            issue_activity.c.issue_id,
            issue_activity.c.event_type,
            issue_activity.c.created_at,
        ).where(
            and_(
                issue_activity.c.organization_id == org_id,
                issue_activity.c.created_at >= earliest_iso,
            )
        )
    ).mappings().all()

    daily_map: dict[str, dict[str, Any]] = defaultdict(_empty_day_bucket)

    # Seed every active human workspace member so new workspaces still show a roster.
    people_map: dict[str, dict[str, Any]] = {}
    for uid, u in users.items():
        if u.get("archived_at") is not None:
            continue
        if (u.get("kind") or "human") != "human":
            continue
        people_map[uid] = {
            "userId": uid,
            "name": _display_name(u, uid),
            "displayName": u.get("display_name"),
            "email": u.get("email"),
            "avatarSeed": u.get("avatar_seed"),
            "kind": u.get("kind") or "human",
            "tokens": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadTokens": 0,
            "credits": 0,
            "runMinutes": 0,
            "runIds": set(),
            "completedTasks": 0,
            "openTasks": 0,
            "actions": 0,
            "activeDaySet": set(),
        }

    def ensure_person(uid: str) -> dict[str, Any] | None:
        if uid in people_map:
            return people_map[uid]
        u = users.get(uid)
        if not u or (u.get("kind") or "human") != "human":
            return None
        entry = {
            "userId": uid,
            "name": _display_name(u, uid),
            "displayName": u.get("display_name"),
            "email": u.get("email"),
            "avatarSeed": u.get("avatar_seed"),
            "kind": u.get("kind") or "human",
            "tokens": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadTokens": 0,
            "credits": 0,
            "runMinutes": 0,
            "runIds": set(),
            "completedTasks": 0,
            "openTasks": 0,
            "actions": 0,
            "activeDaySet": set(),
        }
        people_map[uid] = entry
        return entry

    agents_map: dict[str, dict[str, Any]] = {}
    for uid, u in users.items():
        if u.get("archived_at") is not None:
            continue
        if (u.get("kind") or "human") == "agent":
            agents_map[uid] = {
                "agentUserId": uid,
                "name": _display_name(u, uid),
                "avatarSeed": u.get("avatar_seed"),
                "tokens": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheReadTokens": 0,
                "credits": 0,
                "runMinutes": 0,
                "runIds": set(),
                "completedRuns": 0,
            }

    def ensure_agent(uid: str) -> dict[str, Any]:
        if uid not in agents_map:
            u = users.get(uid)
            agents_map[uid] = {
                "agentUserId": uid,
                "name": _display_name(u, uid),
                "avatarSeed": u.get("avatar_seed") if u else None,
                "tokens": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheReadTokens": 0,
                "credits": 0,
                "runMinutes": 0,
                "runIds": set(),
                "completedRuns": 0,
            }
        return agents_map[uid]

    tasks_map: dict[str, dict[str, Any]] = {}

    total_input = 0
    total_output = 0
    total_cache = 0
    total_credits = 0
    total_minutes = 0
    total_run_ids: set[str] = set()

    for ev in usage_rows:
        day = _day_str(ev["recorded_at"])
        inp = max(0, int(ev["input_tokens"] or 0))
        out = max(0, int(ev["output_tokens"] or 0))
        cache = max(0, int(ev["cache_read_tokens"] or 0))
        toks = inp + out + cache
        cred = max(0, int(ev["credits"] or 0))
        mins = max(0, int(ev["run_minutes"] or 0))
        run_key = str(ev["run_id"] or ev["session_id"] or ev["id"])
        issue_id, human_id = _usage_human_owner(dict(ev), sessions, issues)

        if day and day >= heatmap_start:
            db = daily_map[day]
            db["tokens"] += toks
            db["inputTokens"] += inp
            db["outputTokens"] += out
            db["cacheReadTokens"] += cache
            db["credits"] += cred
            db["runMinutes"] += mins
            db["runIds"].add(run_key)

        if not day or day < window_start:
            continue

        total_input += inp
        total_output += out
        total_cache += cache
        total_credits += cred
        total_minutes += mins
        total_run_ids.add(run_key)

        ag = ensure_agent(str(ev["agent_user_id"]))
        ag["tokens"] += toks
        ag["inputTokens"] += inp
        ag["outputTokens"] += out
        ag["cacheReadTokens"] += cache
        ag["credits"] += cred
        ag["runMinutes"] += mins
        ag["runIds"].add(run_key)

        if human_id:
            person = ensure_person(human_id)
            if person is not None:
                person["tokens"] += toks
                person["inputTokens"] += inp
                person["outputTokens"] += out
                person["cacheReadTokens"] += cache
                person["credits"] += cred
                person["runMinutes"] += mins
                person["runIds"].add(run_key)
                person["activeDaySet"].add(day)

        if issue_id and issue_id in issues:
            iss = issues[issue_id]
            t = tasks_map.get(issue_id)
            if t is None:
                st = states.get(str(iss.get("state_id") or ""))
                assignee = users.get(str(iss.get("assignee_id") or ""))
                delegate = users.get(str(iss.get("delegate_id") or ""))
                t = {
                    "issueId": issue_id,
                    "identifier": str(iss.get("identifier") or issue_id),
                    "title": str(iss.get("title") or ""),
                    "stateName": st.get("name") if st else None,
                    "stateColor": st.get("color") if st else None,
                    "stateType": st.get("type") if st else None,
                    "assigneeId": str(iss["assignee_id"]) if iss.get("assignee_id") else None,
                    "assigneeName": _display_name(assignee, "") if assignee else None,
                    "assigneeAvatarSeed": assignee.get("avatar_seed") if assignee else None,
                    "delegateId": str(iss["delegate_id"]) if iss.get("delegate_id") else None,
                    "delegateName": _display_name(delegate, "") if delegate else None,
                    "delegateAvatarSeed": delegate.get("avatar_seed") if delegate else None,
                    "tokens": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheReadTokens": 0,
                    "credits": 0,
                    "runMinutes": 0,
                    "runIds": set(),
                }
                tasks_map[issue_id] = t
            t["tokens"] += toks
            t["inputTokens"] += inp
            t["outputTokens"] += out
            t["cacheReadTokens"] += cache
            t["credits"] += cred
            t["runMinutes"] += mins
            t["runIds"].add(run_key)

    # Count completed agent sessions inside the window.
    for sess in sessions.values():
        day = _day_str(sess.get("ended_at") or sess.get("updated_at") or sess.get("created_at"))
        if day and day >= window_start and sess.get("state") in ("done", "in_review"):
            ag = ensure_agent(str(sess["agent_user_id"]))
            ag["completedRuns"] += 1

    # Issues: open count + completed in window/heatmap.
    total_completed = 0
    for iss in issues.values():
        assignee_id = str(iss["assignee_id"]) if iss.get("assignee_id") else None
        st = states.get(str(iss.get("state_id") or ""))
        st_type = (st.get("type") if st else "") or ""
        is_open = (
            iss.get("completed_at") is None
            and iss.get("canceled_at") is None
            and st_type not in ("completed", "canceled")
        )
        if is_open and assignee_id:
            p = ensure_person(assignee_id)
            if p is not None:
                p["openTasks"] += 1

        comp_day = _day_str(iss.get("completed_at"))
        if comp_day:
            if comp_day >= heatmap_start:
                daily_map[comp_day]["completedTasks"] += 1
            if comp_day >= window_start:
                total_completed += 1
                if assignee_id:
                    p = ensure_person(assignee_id)
                    if p is not None:
                        p["completedTasks"] += 1
                        p["activeDaySet"].add(comp_day)

    total_actions = 0
    for act in activity_rows:
        day = _day_str(act["created_at"])
        if not day:
            continue
        if day >= heatmap_start:
            daily_map[day]["actions"] += 1
        if day >= window_start:
            total_actions += 1
            actor_id = str(act["actor_id"]) if act.get("actor_id") else None
            if actor_id:
                p = ensure_person(actor_id)
                if p is not None:
                    p["actions"] += 1
                    p["activeDaySet"].add(day)

    people_rows = []
    for p in people_map.values():
        people_rows.append({
            **{k: v for k, v in p.items() if k not in ("runIds", "activeDaySet")},
            "runs": len(p["runIds"]),
            "activeDays": len(p["activeDaySet"]),
        })
    people_rows.sort(
        key=lambda r: (
            -r["tokens"],
            -r["completedTasks"],
            -r["actions"],
            r["name"].lower(),
        )
    )

    task_rows = []
    for t in tasks_map.values():
        task_rows.append({
            **{k: v for k, v in t.items() if k != "runIds"},
            "runs": len(t["runIds"]),
        })
    task_rows.sort(key=lambda r: (-r["tokens"], -r["runMinutes"], r["identifier"]))

    agent_rows = []
    for a in agents_map.values():
        agent_rows.append({
            **{k: v for k, v in a.items() if k != "runIds"},
            "runs": len(a["runIds"]),
        })
    agent_rows.sort(key=lambda r: (-r["tokens"], -r["runs"], r["name"].lower()))

    return {
        "days": days,
        "heatmapDays": heatmap_days,
        "totalTokens": total_input + total_output + total_cache,
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCacheReadTokens": total_cache,
        "totalCredits": total_credits,
        "totalRunMinutes": total_minutes,
        "totalRuns": len(total_run_ids),
        "totalCompletedTasks": total_completed,
        "totalActions": total_actions,
        "daily": _build_daily_series(daily_map, end_day=today, num_days=heatmap_days),
        "people": people_rows[:limit],
        "tasks": task_rows[:limit],
        "agents": agent_rows[:limit],
    }


def compute_member_profile(
    conn,
    org_id: str,
    user_id: str,
    *,
    days: int = DEFAULT_DAYS,
    heatmap_days: int = DEFAULT_HEATMAP_DAYS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now_dt = now or datetime.now(timezone.utc)
    today = now_dt.date()
    window_start = (today - timedelta(days=days - 1)).isoformat()
    heatmap_start = (today - timedelta(days=heatmap_days - 1)).isoformat()
    earliest_iso = min(window_start, heatmap_start)

    ctx = _load_workspace_context(conn, org_id)
    users = ctx["users"]
    target = users.get(user_id)
    if target is None:
        return None

    states = ctx["states"]
    issues = ctx["issues"]
    sessions = ctx["sessions"]

    team_rows = conn.execute(
        select(team.c.id, team.c.key, team.c.name)
        .join(team_member, team_member.c.team_id == team.c.id)
        .where(
            and_(
                team.c.organization_id == org_id,
                team.c.archived_at.is_(None),
                team_member.c.user_id == user_id,
            )
        )
        .order_by(team.c.name)
    ).mappings().all()

    usage_rows = conn.execute(
        select(usage_event).where(
            and_(
                usage_event.c.organization_id == org_id,
                usage_event.c.recorded_at >= earliest_iso,
            )
        )
    ).mappings().all()

    activity_rows = conn.execute(
        select(
            issue_activity.c.id,
            issue_activity.c.issue_id,
            issue_activity.c.event_type,
            issue_activity.c.created_at,
        )
        .where(
            and_(
                issue_activity.c.organization_id == org_id,
                issue_activity.c.actor_id == user_id,
                issue_activity.c.created_at >= earliest_iso,
            )
        )
        .order_by(desc(issue_activity.c.created_at))
    ).mappings().all()

    daily_map: dict[str, dict[str, Any]] = defaultdict(_empty_day_bucket)
    is_agent = (target.get("kind") or "human") == "agent"

    total_input = 0
    total_output = 0
    total_cache = 0
    total_credits = 0
    total_minutes = 0
    run_ids: set[str] = set()
    active_days_window: set[str] = set()
    task_usage_map: dict[str, dict[str, Any]] = {}
    all_task_usage: dict[str, dict[str, int]] = defaultdict(lambda: {"tokens": 0, "runMinutes": 0})

    for ev in usage_rows:
        day = _day_str(ev["recorded_at"])
        inp = max(0, int(ev["input_tokens"] or 0))
        out = max(0, int(ev["output_tokens"] or 0))
        cache = max(0, int(ev["cache_read_tokens"] or 0))
        toks = inp + out + cache
        cred = max(0, int(ev["credits"] or 0))
        mins = max(0, int(ev["run_minutes"] or 0))
        run_key = str(ev["run_id"] or ev["session_id"] or ev["id"])
        issue_id, human_id = _usage_human_owner(dict(ev), sessions, issues)

        if issue_id:
            all_task_usage[issue_id]["tokens"] += toks
            all_task_usage[issue_id]["runMinutes"] += mins

        belongs = (str(ev["agent_user_id"]) == user_id) if is_agent else (human_id == user_id)
        if not belongs:
            continue

        if day and day >= heatmap_start:
            db = daily_map[day]
            db["tokens"] += toks
            db["inputTokens"] += inp
            db["outputTokens"] += out
            db["cacheReadTokens"] += cache
            db["credits"] += cred
            db["runMinutes"] += mins
            db["runIds"].add(run_key)

        if not day or day < window_start:
            continue

        total_input += inp
        total_output += out
        total_cache += cache
        total_credits += cred
        total_minutes += mins
        run_ids.add(run_key)
        active_days_window.add(day)

        if issue_id and issue_id in issues:
            iss = issues[issue_id]
            t = task_usage_map.get(issue_id)
            if t is None:
                st = states.get(str(iss.get("state_id") or ""))
                assignee = users.get(str(iss.get("assignee_id") or ""))
                delegate = users.get(str(iss.get("delegate_id") or ""))
                t = {
                    "issueId": issue_id,
                    "identifier": str(iss.get("identifier") or issue_id),
                    "title": str(iss.get("title") or ""),
                    "stateName": st.get("name") if st else None,
                    "stateColor": st.get("color") if st else None,
                    "stateType": st.get("type") if st else None,
                    "assigneeId": str(iss["assignee_id"]) if iss.get("assignee_id") else None,
                    "assigneeName": _display_name(assignee, "") if assignee else None,
                    "assigneeAvatarSeed": assignee.get("avatar_seed") if assignee else None,
                    "delegateId": str(iss["delegate_id"]) if iss.get("delegate_id") else None,
                    "delegateName": _display_name(delegate, "") if delegate else None,
                    "delegateAvatarSeed": delegate.get("avatar_seed") if delegate else None,
                    "tokens": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheReadTokens": 0,
                    "credits": 0,
                    "runMinutes": 0,
                    "runIds": set(),
                }
                task_usage_map[issue_id] = t
            t["tokens"] += toks
            t["inputTokens"] += inp
            t["outputTokens"] += out
            t["cacheReadTokens"] += cache
            t["credits"] += cred
            t["runMinutes"] += mins
            t["runIds"].add(run_key)

    completed_tasks = 0
    open_tasks = 0
    created_tasks = 0
    assigned_tasks: list[dict[str, Any]] = []

    for iid, iss in issues.items():
        assignee_id = str(iss["assignee_id"]) if iss.get("assignee_id") else None
        delegate_id = str(iss["delegate_id"]) if iss.get("delegate_id") else None
        creator_id = str(iss["creator_id"]) if iss.get("creator_id") else None
        owner_match = (delegate_id == user_id) if is_agent else (assignee_id == user_id)

        st = states.get(str(iss.get("state_id") or ""))
        st_type = (st.get("type") if st else "") or ""
        is_open = (
            iss.get("completed_at") is None
            and iss.get("canceled_at") is None
            and st_type not in ("completed", "canceled")
        )

        if owner_match:
            if is_open:
                open_tasks += 1
                tu = all_task_usage.get(iid)
                assigned_tasks.append({
                    "id": iid,
                    "identifier": str(iss.get("identifier") or iid),
                    "title": str(iss.get("title") or ""),
                    "priority": int(iss.get("priority") or 0),
                    "stateName": st.get("name") if st else None,
                    "stateColor": st.get("color") if st else None,
                    "stateType": st.get("type") if st else None,
                    "updatedAt": iss.get("updated_at") or iss.get("created_at"),
                    "tokens": tu["tokens"] if tu else 0,
                    "runMinutes": tu["runMinutes"] if tu else 0,
                })
            comp_day = _day_str(iss.get("completed_at"))
            if comp_day:
                if comp_day >= heatmap_start:
                    daily_map[comp_day]["completedTasks"] += 1
                if comp_day >= window_start:
                    completed_tasks += 1
                    active_days_window.add(comp_day)

        if creator_id == user_id:
            created_day = _day_str(iss.get("created_at"))
            if created_day and created_day >= window_start:
                created_tasks += 1

    actions_count = 0
    recent_activity: list[dict[str, Any]] = []
    for act in activity_rows:
        day = _day_str(act["created_at"])
        if day and day >= heatmap_start:
            daily_map[day]["actions"] += 1
        if day and day >= window_start:
            actions_count += 1
            active_days_window.add(day)
        if len(recent_activity) < 25:
            iss = issues.get(str(act["issue_id"]))
            if iss:
                recent_activity.append({
                    "id": str(act["id"]),
                    "eventType": str(act["event_type"]),
                    "createdAt": str(act["created_at"]),
                    "issueId": str(act["issue_id"]),
                    "issueIdentifier": str(iss.get("identifier") or act["issue_id"]),
                    "issueTitle": str(iss.get("title") or ""),
                })

    assigned_tasks.sort(
        key=lambda t: (
            0 if t["priority"] == 1 else (t["priority"] or 99),
            -(int(datetime.fromisoformat(str(t["updatedAt"]).replace("Z", "+00:00")).timestamp())
              if t.get("updatedAt") else 0),
        )
    )

    top_tasks = [
        {**{k: v for k, v in t.items() if k != "runIds"}, "runs": len(t["runIds"])}
        for t in task_usage_map.values()
    ]
    top_tasks.sort(key=lambda r: (-r["tokens"], -r["runMinutes"], r["identifier"]))

    daily = _build_daily_series(daily_map, end_day=today, num_days=heatmap_days)
    current_streak, longest_streak = _streaks(daily)

    # Compute workspace ranks for humans using the same leaderboard calculation.
    rank_by_tokens = None
    rank_by_completed = None
    if not is_agent:
        lb = compute_leaderboard(conn, org_id, days=days, heatmap_days=7, limit=MAX_LIMIT, now=now_dt)
        by_tokens = lb["people"]
        for idx, row in enumerate(by_tokens, start=1):
            if row["userId"] == user_id and row["tokens"] > 0:
                rank_by_tokens = idx
                break
        by_completed = sorted(
            by_tokens,
            key=lambda r: (-r["completedTasks"], -r["actions"], r["name"].lower()),
        )
        for idx, row in enumerate(by_completed, start=1):
            if row["userId"] == user_id and row["completedTasks"] > 0:
                rank_by_completed = idx
                break

    return {
        "user": {
            "id": str(target["id"]),
            "name": _display_name(target, user_id),
            "displayName": target.get("display_name"),
            "email": target.get("email"),
            "avatarSeed": target.get("avatar_seed"),
            "admin": bool(target.get("admin")),
            "active": bool(target.get("active", True)),
            "kind": str(target.get("kind") or "human"),
            "timezone": target.get("timezone"),
            "statusEmoji": target.get("status_emoji"),
            "statusLabel": target.get("status_label"),
            "createdAt": target.get("created_at"),
        },
        "teams": [
            {"id": str(r["id"]), "key": str(r["key"]), "name": str(r["name"])}
            for r in team_rows
        ],
        "days": days,
        "heatmapDays": heatmap_days,
        "totalTokens": total_input + total_output + total_cache,
        "inputTokens": total_input,
        "outputTokens": total_output,
        "cacheReadTokens": total_cache,
        "credits": total_credits,
        "runMinutes": total_minutes,
        "runs": len(run_ids),
        "completedTasks": completed_tasks,
        "openTasks": open_tasks,
        "createdTasks": created_tasks,
        "actions": actions_count,
        "activeDays": len(active_days_window),
        "currentStreak": current_streak,
        "longestStreak": longest_streak,
        "rankByTokens": rank_by_tokens,
        "rankByCompleted": rank_by_completed,
        "daily": daily,
        "topTasks": top_tasks[:15],
        "assignedTasks": assigned_tasks[:25],
        "recentActivity": recent_activity,
    }


def compute_task_usage(conn, org_id: str, issue_ref: str) -> dict[str, Any] | None:
    ref = (issue_ref or "").strip()
    if not ref:
        return None
    iss = conn.execute(
        select(issue.c.id).where(
            and_(
                issue.c.organization_id == org_id,
                (issue.c.id == ref) | (issue.c.identifier == ref),
            )
        )
    ).first()
    if not iss:
        return None
    issue_id = str(iss[0])

    session_ids = [
        str(r[0])
        for r in conn.execute(
            select(agent_session.c.id).where(
                and_(
                    agent_session.c.organization_id == org_id,
                    agent_session.c.issue_id == issue_id,
                )
            )
        ).all()
    ]
    if not session_ids:
        return {
            "issueId": issue_id,
            "tokens": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadTokens": 0,
            "credits": 0,
            "runMinutes": 0,
            "runs": 0,
            "breakdown": [],
        }

    events = conn.execute(
        select(usage_event).where(
            and_(
                usage_event.c.organization_id == org_id,
                usage_event.c.session_id.in_(session_ids),
            )
        )
    ).mappings().all()

    agent_ids = list({str(e["agent_user_id"]) for e in events})
    users = {}
    if agent_ids:
        for row in conn.execute(
            select(user.c.id, user.c.name, user.c.display_name, user.c.email, user.c.avatar_seed)
            .where(and_(user.c.organization_id == org_id, user.c.id.in_(agent_ids)))
        ).mappings().all():
            users[str(row["id"])] = dict(row)

    total_input = 0
    total_output = 0
    total_cache = 0
    total_credits = 0
    total_minutes = 0
    run_ids: set[str] = set()
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}

    for ev in events:
        inp = max(0, int(ev["input_tokens"] or 0))
        out = max(0, int(ev["output_tokens"] or 0))
        cache = max(0, int(ev["cache_read_tokens"] or 0))
        toks = inp + out + cache
        cred = max(0, int(ev["credits"] or 0))
        mins = max(0, int(ev["run_minutes"] or 0))
        run_key = str(ev["run_id"] or ev["session_id"] or ev["id"])
        ag_id = str(ev["agent_user_id"])
        model = str(ev["model"] or "unknown")

        total_input += inp
        total_output += out
        total_cache += cache
        total_credits += cred
        total_minutes += mins
        run_ids.add(run_key)

        pair = (ag_id, model)
        b = by_pair.get(pair)
        if b is None:
            u = users.get(ag_id)
            b = {
                "agentUserId": ag_id,
                "agentName": _display_name(u, ag_id),
                "agentAvatarSeed": u.get("avatar_seed") if u else None,
                "model": model,
                "tokens": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheReadTokens": 0,
                "credits": 0,
                "runMinutes": 0,
                "runIds": set(),
            }
            by_pair[pair] = b
        b["tokens"] += toks
        b["inputTokens"] += inp
        b["outputTokens"] += out
        b["cacheReadTokens"] += cache
        b["credits"] += cred
        b["runMinutes"] += mins
        b["runIds"].add(run_key)

    breakdown = [
        {**{k: v for k, v in b.items() if k != "runIds"}, "runs": len(b["runIds"])}
        for b in by_pair.values()
    ]
    breakdown.sort(key=lambda r: (-r["tokens"], r["agentName"].lower()))

    return {
        "issueId": issue_id,
        "tokens": total_input + total_output + total_cache,
        "inputTokens": total_input,
        "outputTokens": total_output,
        "cacheReadTokens": total_cache,
        "credits": total_credits,
        "runMinutes": total_minutes,
        "runs": len(run_ids),
        "breakdown": breakdown,
    }


@query.field("activityLeaderboard")
def r_activity_leaderboard(_, info, days=None, heatmapDays=None, limit=None):  # noqa: N803
    store, org, _uid = _ctx(info)
    d = _clamp_days(days, DEFAULT_DAYS)
    hd = _clamp_days(heatmapDays, DEFAULT_HEATMAP_DAYS)
    lim = _clamp_limit(limit)
    with store.engine.connect() as conn:
        return compute_leaderboard(conn, org, days=d, heatmap_days=hd, limit=lim)


@query.field("memberProfile")
def r_member_profile(_, info, userId, days=None, heatmapDays=None):  # noqa: N803
    store, org, _uid = _ctx(info)
    d = _clamp_days(days, DEFAULT_DAYS)
    hd = _clamp_days(heatmapDays, DEFAULT_HEATMAP_DAYS)
    with store.engine.connect() as conn:
        return compute_member_profile(conn, org, str(userId), days=d, heatmap_days=hd)


@query.field("taskUsage")
def r_task_usage(_, info, issueId):  # noqa: N803
    store, org, _uid = _ctx(info)
    with store.engine.connect() as conn:
        return compute_task_usage(conn, org, str(issueId))
