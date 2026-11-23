import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Flame,
  ListChecks,
  Trophy,
  Zap,
} from "lucide-react";
import { GraphStatusIcon, PriorityIcon, UserBlobatar } from "../graph/shared";
import { ActivityHeatmap } from "./ActivityHeatmap";
import {
  fetchMemberProfile,
  formatActivityVerb,
  formatDuration,
  formatRelativeTime,
  formatTokens,
  type MemberProfile,
} from "./api";

type ProfileTab = "open" | "tokens" | "activity";

const RANGES = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
  { days: 365, label: "1y" },
] as const;

export function MemberProfilePage({
  userId,
  viewerId,
  onBack,
  onOpenLeaderboard,
  onOpenTask,
  onOpenSettingsProfile,
}: {
  userId: string;
  viewerId?: string;
  onBack: () => void;
  onOpenLeaderboard: () => void;
  onOpenTask: (identifier: string, title: string) => void;
  onOpenSettingsProfile?: () => void;
}) {
  const [days, setDays] = useState<number>(30);
  const [tab, setTab] = useState<ProfileTab>("open");
  const [profile, setProfile] = useState<MemberProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    fetchMemberProfile(userId, days, days >= 365 ? 365 : 182)
      .then((res) => {
        if (!live) return;
        setProfile(res);
        if (!res) setError("Member not found in this workspace.");
      })
      .catch((e: Error) => {
        if (live) setError(e.message || "Could not load member profile.");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [userId, days]);

  const maxTaskTokens = useMemo(
    () => Math.max(0, ...(profile?.topTasks ?? []).map((t) => t.tokens)),
    [profile?.topTasks],
  );

  const isMe = Boolean(viewerId && viewerId === userId);

  return (
    <section
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg text-foreground shell-phone:overflow-y-auto shell-phone:overscroll-contain"
      aria-label="Member profile"
      data-member-profile-page=""
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-panel px-5 py-3 max-sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
          >
            <ArrowLeft size={14} aria-hidden="true" />
            Members
          </button>
          <span className="text-xs text-muted" aria-hidden="true">
            ›
          </span>
          <h1 className="m-0 truncate text-sm font-semibold text-foreground">
            {profile?.user.name || "Profile"}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 max-sm:w-full">
          {isMe && onOpenSettingsProfile && (
            <button
              type="button"
              onClick={onOpenSettingsProfile}
              className="shrink-0 whitespace-nowrap rounded-lg border border-line bg-panel px-3 py-1.5 text-xs font-medium text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
            >
              Edit profile
            </button>
          )}
          <button
            type="button"
            onClick={onOpenLeaderboard}
            className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-panel px-3 py-1.5 text-xs font-medium text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
          >
            <Trophy size={13} aria-hidden="true" />
            Leaderboard
          </button>
          <div
            role="group"
            aria-label="Time range"
            className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-bg p-0.5 max-sm:w-full"
          >
            {RANGES.map((r) => {
              const active = days === r.days;
              return (
                <button
                  key={r.days}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setDays(r.days)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11 max-sm:flex-1 ${
                    active
                      ? "bg-panel text-foreground shadow-xs transition-colors duration-150 ease-out"
                      : "text-muted hover:text-foreground"
                  }`}
                >
                  {r.label}
                </button>
              );
            })}
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6 shell-phone:flex-none shell-phone:overflow-visible">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
          {error && (
            <div
              role="alert"
              className="rounded-xl border border-line bg-panel p-4 text-sm text-danger"
            >
              {error}
            </div>
          )}

          {loading && !profile ? (
            <div
              role="status"
              className="rounded-xl border border-line bg-panel p-8 text-center text-sm text-muted"
            >
              Loading profile…
            </div>
          ) : profile ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-line bg-panel p-5">
                <div className="flex min-w-0 items-center gap-4">
                  <span
                    className="size-16 shrink-0 overflow-hidden rounded-full border border-line bg-subtle"
                    aria-hidden="true"
                  >
                    <UserBlobatar user={profile.user} />
                  </span>
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="m-0 text-lg font-semibold text-foreground">
                        {profile.user.name}
                      </h2>
                      {profile.user.kind === "agent" ? (
                        <span className="rounded-full border border-line bg-subtle px-2 py-0.5 text-xs font-medium text-muted">
                          Agent
                        </span>
                      ) : profile.user.admin ? (
                        <span className="rounded-full border border-line bg-subtle px-2 py-0.5 text-xs font-medium text-muted">
                          Admin
                        </span>
                      ) : null}
                      {profile.user.statusLabel && (
                        <span className="rounded-full border border-line bg-bg px-2 py-0.5 text-xs text-muted">
                          {profile.user.statusEmoji
                            ? `${profile.user.statusEmoji} `
                            : ""}
                          {profile.user.statusLabel}
                        </span>
                      )}
                    </div>
                    <p className="m-0 text-xs text-muted wrap-anywhere">
                      {[
                        profile.user.email,
                        profile.user.timezone,
                        profile.teams.length
                          ? profile.teams.map((t) => t.name).join(" · ")
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {profile.rankByTokens != null && (
                    <button
                      type="button"
                      onClick={onOpenLeaderboard}
                      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg px-3 py-1 text-xs font-medium text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-10"
                    >
                      <Flame size={13} aria-hidden="true" />#
                      {profile.rankByTokens} token burner
                    </button>
                  )}
                  {profile.rankByCompleted != null && (
                    <button
                      type="button"
                      onClick={onOpenLeaderboard}
                      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg px-3 py-1 text-xs font-medium text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-10"
                    >
                      <Trophy size={13} aria-hidden="true" />#
                      {profile.rankByCompleted} shipper
                    </button>
                  )}
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg px-3 py-1 text-xs font-medium tabular-nums text-muted">
                    <Zap size={13} aria-hidden="true" />
                    {profile.currentStreak}d streak · best{" "}
                    {profile.longestStreak}d
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <Flame size={14} aria-hidden="true" />
                    Tokens burned ({days}d)
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {formatTokens(profile.totalTokens)}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {formatTokens(profile.inputTokens)} in ·{" "}
                    {formatTokens(profile.outputTokens)} out ·{" "}
                    {formatTokens(profile.cacheReadTokens)} cached
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <Clock size={14} aria-hidden="true" />
                    Agent runtime
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {formatDuration(profile.runMinutes)}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {profile.runs} {profile.runs === 1 ? "run" : "runs"} ·{" "}
                    {profile.credits} credits
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <CheckCircle2 size={14} aria-hidden="true" />
                    Tasks shipped
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {profile.completedTasks}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {profile.createdTasks} created · {profile.activeDays} active{" "}
                    {profile.activeDays === 1 ? "day" : "days"}
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <ListChecks size={14} aria-hidden="true" />
                    Open tasks
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {profile.openTasks}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {profile.actions} actions in {days}d
                  </span>
                </div>
              </div>

              <ActivityHeatmap
                daily={profile.daily}
                title={`${profile.user.name}'s activity`}
              />

              <section className="flex flex-col overflow-hidden rounded-xl border border-line bg-panel">
                <div
                  role="tablist"
                  aria-label="Profile sections"
                  className="flex flex-wrap items-center gap-1 border-b border-line px-4 py-3"
                >
                  {(
                    [
                      {
                        id: "open",
                        label: `Open tasks (${profile.assignedTasks.length})`,
                        icon: ListChecks,
                      },
                      {
                        id: "tokens",
                        label: `Top token tasks (${profile.topTasks.length})`,
                        icon: Flame,
                      },
                      {
                        id: "activity",
                        label: `Recent activity (${profile.recentActivity.length})`,
                        icon: Activity,
                      },
                    ] as const
                  ).map(({ id, label, icon: Icon }) => {
                    const active = tab === id;
                    return (
                      <button
                        key={id}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => setTab(id)}
                        className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11 ${
                          active
                            ? "bg-subtle text-foreground transition-colors duration-150 ease-out"
                            : "text-muted hover:bg-subtle hover:text-foreground"
                        }`}
                      >
                        <Icon size={14} aria-hidden="true" />
                        {label}
                      </button>
                    );
                  })}
                </div>

                {tab === "open" && (
                  <div className="divide-y divide-line">
                    {profile.assignedTasks.length === 0 ? (
                      <p className="m-0 p-6 text-center text-sm text-muted">
                        No open tasks assigned right now.
                      </p>
                    ) : (
                      profile.assignedTasks.map((task) => (
                        <button
                          key={task.id}
                          type="button"
                          onClick={() =>
                            onOpenTask(task.identifier, task.title)
                          }
                          className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-12"
                        >
                          <PriorityIcon priority={task.priority} size={14} />
                          <GraphStatusIcon
                            state={{
                              name: task.stateName || "",
                              type: task.stateType || "",
                              color: task.stateColor || undefined,
                            }}
                            size={14}
                          />
                          <span className="shrink-0 font-mono text-xs font-semibold text-muted">
                            {task.identifier}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                            {task.title}
                          </span>
                          {task.tokens > 0 && (
                            <span className="shrink-0 rounded-full border border-line bg-bg px-2 py-0.5 text-xs tabular-nums text-muted">
                              {formatTokens(task.tokens)} tok
                            </span>
                          )}
                          <span className="shrink-0 text-xs tabular-nums text-muted">
                            {formatRelativeTime(task.updatedAt)}
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}

                {tab === "tokens" && (
                  <div className="divide-y divide-line">
                    {profile.topTasks.length === 0 ? (
                      <p className="m-0 p-6 text-center text-sm text-muted">
                        No token usage recorded for tasks in the last {days}{" "}
                        days.
                      </p>
                    ) : (
                      profile.topTasks.map((task) => {
                        const pct =
                          maxTaskTokens > 0
                            ? Math.max(
                                4,
                                Math.round((task.tokens / maxTaskTokens) * 100),
                              )
                            : 0;
                        return (
                          <button
                            key={task.issueId}
                            type="button"
                            onClick={() =>
                              onOpenTask(task.identifier, task.title)
                            }
                            className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-12"
                          >
                            <GraphStatusIcon
                              state={{
                                name: task.stateName || "",
                                type: task.stateType || "",
                                color: task.stateColor || undefined,
                              }}
                              size={14}
                            />
                            <span className="shrink-0 font-mono text-xs font-semibold text-muted">
                              {task.identifier}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                              {task.title}
                            </span>
                            <div
                              className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-subtle sm:block"
                              aria-hidden="true"
                            >
                              <div
                                className="h-full rounded-full bg-accent"
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            <span className="shrink-0 text-xs tabular-nums text-muted">
                              {formatDuration(task.runMinutes)}
                            </span>
                            <span className="w-16 shrink-0 text-right text-sm font-semibold tabular-nums text-foreground">
                              {formatTokens(task.tokens)}
                            </span>
                          </button>
                        );
                      })
                    )}
                  </div>
                )}

                {tab === "activity" && (
                  <div className="divide-y divide-line">
                    {profile.recentActivity.length === 0 ? (
                      <p className="m-0 p-6 text-center text-sm text-muted">
                        No recent activity recorded yet.
                      </p>
                    ) : (
                      profile.recentActivity.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() =>
                            onOpenTask(item.issueIdentifier, item.issueTitle)
                          }
                          className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-12"
                        >
                          <span className="shrink-0 text-xs text-muted">
                            {formatActivityVerb(item.eventType)}
                          </span>
                          <span className="shrink-0 font-mono text-xs font-semibold text-foreground">
                            {item.issueIdentifier}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                            {item.issueTitle}
                          </span>
                          <span className="shrink-0 text-xs tabular-nums text-muted">
                            {formatRelativeTime(item.createdAt)}
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}
              </section>
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
