import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Clock,
  Flame,
  ListChecks,
  Trophy,
  Users,
} from "lucide-react";
import { GraphStatusIcon, UserBlobatar } from "../graph/shared";
import { ActivityHeatmap } from "./ActivityHeatmap";
import {
  fetchLeaderboard,
  formatDuration,
  formatTokens,
  type ActivityLeaderboard,
} from "./api";

type BoardTab = "people" | "tasks" | "agents";
type PeopleSort = "tokens" | "completed" | "actions";

const RANGES = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
  { days: 365, label: "1y" },
] as const;

function RankBadge({ rank }: { rank: number }) {
  const tone =
    rank === 1
      ? "bg-amber-500/15 text-amber-700 border-amber-500/40 dark:text-amber-300"
      : rank === 2
        ? "bg-slate-400/15 text-slate-700 border-slate-400/40 dark:text-slate-300"
        : rank === 3
          ? "bg-orange-500/15 text-orange-700 border-orange-500/40 dark:text-orange-300"
          : "bg-subtle text-muted border-line";
  return (
    <span
      className={`inline-flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold tabular-nums ${tone}`}
    >
      {rank}
    </span>
  );
}

function ShareBar({ value, max }: { value: number; max: number }) {
  const pct =
    max > 0 ? Math.max(value > 0 ? 4 : 0, Math.round((value / max) * 100)) : 0;
  return (
    <div
      className="h-1.5 w-24 overflow-hidden rounded-full bg-subtle"
      aria-hidden="true"
    >
      <div
        className="h-full rounded-full bg-accent transition-transform duration-200 ease-out"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function LeaderboardPage({
  viewerId,
  onOpenMember,
  onOpenTask,
}: {
  viewerId?: string;
  onOpenMember: (userId: string) => void;
  onOpenTask: (identifier: string, title: string) => void;
}) {
  const [days, setDays] = useState<number>(30);
  const [tab, setTab] = useState<BoardTab>("people");
  const [peopleSort, setPeopleSort] = useState<PeopleSort>("tokens");
  const [data, setData] = useState<ActivityLeaderboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    fetchLeaderboard(days, days >= 365 ? 365 : 182, 50)
      .then((res) => {
        if (live) setData(res);
      })
      .catch((e: Error) => {
        if (live) setError(e.message || "Could not load leaderboard.");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [days]);

  const sortedPeople = useMemo(() => {
    const rows = [...(data?.people ?? [])];
    if (peopleSort === "completed") {
      rows.sort(
        (a, b) =>
          b.completedTasks - a.completedTasks ||
          b.tokens - a.tokens ||
          a.name.localeCompare(b.name),
      );
    } else if (peopleSort === "actions") {
      rows.sort(
        (a, b) =>
          b.actions - a.actions ||
          b.completedTasks - a.completedTasks ||
          a.name.localeCompare(b.name),
      );
    } else {
      rows.sort(
        (a, b) =>
          b.tokens - a.tokens ||
          b.completedTasks - a.completedTasks ||
          a.name.localeCompare(b.name),
      );
    }
    return rows;
  }, [data?.people, peopleSort]);

  const maxPersonVal = useMemo(() => {
    if (!sortedPeople.length) return 0;
    return Math.max(
      ...sortedPeople.map((p) =>
        peopleSort === "completed"
          ? p.completedTasks
          : peopleSort === "actions"
            ? p.actions
            : p.tokens,
      ),
    );
  }, [sortedPeople, peopleSort]);

  const maxTaskTokens = useMemo(
    () => Math.max(0, ...(data?.tasks ?? []).map((t) => t.tokens)),
    [data?.tasks],
  );

  const maxAgentTokens = useMemo(
    () => Math.max(0, ...(data?.agents ?? []).map((a) => a.tokens)),
    [data?.agents],
  );

  return (
    <section
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg text-foreground shell-phone:overflow-y-auto shell-phone:overscroll-contain"
      aria-label="Leaderboard"
      data-leaderboard-page=""
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-panel px-5 py-3 max-sm:px-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <Trophy size={16} className="text-muted" aria-hidden="true" />
          <h1 className="m-0 text-sm font-semibold text-foreground">
            Leaderboard
          </h1>
          <span className="text-xs text-muted">
            Tokens, runtime, and shipped work
          </span>
        </div>
        <div className="flex items-center gap-2 max-sm:w-full">
          {viewerId && (
            <button
              type="button"
              onClick={() => onOpenMember(viewerId)}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-panel px-3 py-1.5 text-xs font-medium text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
            >
              My profile
            </button>
          )}
          <div
            role="group"
            aria-label="Time range"
            className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-bg p-0.5 max-sm:flex-1"
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

          {loading && !data ? (
            <div
              role="status"
              className="rounded-xl border border-line bg-panel p-8 text-center text-sm text-muted"
            >
              Loading workspace activity…
            </div>
          ) : data ? (
            <>
              <div
                className="grid grid-cols-2 gap-3 lg:grid-cols-4"
                data-leaderboard-kpis=""
              >
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <Flame size={14} aria-hidden="true" />
                    Tokens burned ({days}d)
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {formatTokens(data.totalTokens)}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {formatTokens(data.totalInputTokens)} in ·{" "}
                    {formatTokens(data.totalOutputTokens)} out ·{" "}
                    {formatTokens(data.totalCacheReadTokens)} cached
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <Clock size={14} aria-hidden="true" />
                    Agent runtime
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {formatDuration(data.totalRunMinutes)}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {data.totalRuns} {data.totalRuns === 1 ? "run" : "runs"}{" "}
                    recorded
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <CheckCircle2 size={14} aria-hidden="true" />
                    Tasks shipped
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {data.totalCompletedTasks}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {data.totalActions} workspace actions
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-line bg-panel p-4">
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <Users size={14} aria-hidden="true" />
                    Active roster
                  </span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {data.people.length}
                  </span>
                  <span className="text-xs tabular-nums text-muted">
                    {data.agents.length}{" "}
                    {data.agents.length === 1 ? "agent" : "agents"} ·{" "}
                    {data.tasks.length} metered tasks
                  </span>
                </div>
              </div>

              <ActivityHeatmap daily={data.daily} title="Workspace activity" />

              <section className="flex flex-col overflow-hidden rounded-xl border border-line bg-panel">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
                  <div
                    role="tablist"
                    aria-label="Leaderboard categories"
                    className="flex flex-wrap items-center gap-1"
                  >
                    {(
                      [
                        {
                          id: "people",
                          label: `People (${data.people.length})`,
                          icon: Users,
                        },
                        {
                          id: "tasks",
                          label: `Top tasks (${data.tasks.length})`,
                          icon: ListChecks,
                        },
                        {
                          id: "agents",
                          label: `Agents (${data.agents.length})`,
                          icon: Bot,
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

                  {tab === "people" && (
                    <div
                      role="group"
                      aria-label="Sort people by"
                      className="inline-flex items-center gap-1 text-xs text-muted"
                    >
                      <span>Rank by</span>
                      {(
                        [
                          ["tokens", "Tokens"],
                          ["completed", "Shipped"],
                          ["actions", "Activity"],
                        ] as const
                      ).map(([id, label]) => (
                        <button
                          key={id}
                          type="button"
                          aria-pressed={peopleSort === id}
                          onClick={() => setPeopleSort(id)}
                          className={`rounded-md px-2 py-1 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11 pointer-coarse:px-3 ${
                            peopleSort === id
                              ? "bg-subtle text-foreground"
                              : "text-muted hover:text-foreground"
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {tab === "people" && (
                  <div
                    className="divide-y divide-line"
                    data-leaderboard-people=""
                  >
                    {sortedPeople.length === 0 ? (
                      <p className="m-0 p-6 text-center text-sm text-muted">
                        No workspace members found.
                      </p>
                    ) : (
                      sortedPeople.map((person, index) => {
                        const metricVal =
                          peopleSort === "completed"
                            ? person.completedTasks
                            : peopleSort === "actions"
                              ? person.actions
                              : person.tokens;
                        return (
                          <button
                            key={person.userId}
                            type="button"
                            onClick={() => onOpenMember(person.userId)}
                            className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent pointer-coarse:min-h-12"
                          >
                            <RankBadge rank={index + 1} />
                            <span
                              className="size-8 shrink-0 overflow-hidden rounded-full"
                              aria-hidden="true"
                            >
                              <UserBlobatar
                                user={{
                                  id: person.userId,
                                  name: person.name,
                                  email: person.email,
                                  avatarSeed: person.avatarSeed,
                                }}
                              />
                            </span>
                            <span className="flex min-w-0 flex-1 flex-col">
                              <span className="flex items-center gap-2">
                                <span className="truncate text-sm font-medium text-foreground">
                                  {person.name}
                                </span>
                                {viewerId === person.userId && (
                                  <span className="rounded-full border border-line bg-subtle px-2 py-0.5 text-xs text-muted">
                                    You
                                  </span>
                                )}
                              </span>
                              <span className="truncate text-xs text-muted">
                                {person.completedTasks} shipped ·{" "}
                                {person.openTasks} open · {person.activeDays}{" "}
                                active{" "}
                                {person.activeDays === 1 ? "day" : "days"}
                              </span>
                            </span>
                            <div className="hidden items-center gap-3 sm:flex">
                              <ShareBar value={metricVal} max={maxPersonVal} />
                              <span className="w-16 text-right text-xs tabular-nums text-muted">
                                {formatDuration(person.runMinutes)}
                              </span>
                            </div>
                            <span className="flex shrink-0 flex-col items-end">
                              <span className="text-sm font-semibold tabular-nums text-foreground">
                                {formatTokens(person.tokens)}
                              </span>
                              <span className="text-xs text-muted">tokens</span>
                            </span>
                          </button>
                        );
                      })
                    )}
                  </div>
                )}

                {tab === "tasks" && (
                  <div
                    className="divide-y divide-line"
                    data-leaderboard-tasks=""
                  >
                    {data.tasks.length === 0 ? (
                      <p className="m-0 p-6 text-center text-sm text-muted">
                        No tasks have recorded token usage in the last {days}{" "}
                        days yet. Delegate a task to an agent to start tracking
                        burn per task.
                      </p>
                    ) : (
                      data.tasks.map((task, index) => (
                        <button
                          key={task.issueId}
                          type="button"
                          onClick={() =>
                            onOpenTask(task.identifier, task.title)
                          }
                          className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent pointer-coarse:min-h-12"
                        >
                          <RankBadge rank={index + 1} />
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
                          <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate text-sm font-medium text-foreground">
                              {task.title}
                            </span>
                            <span className="truncate text-xs text-muted">
                              {task.assigneeName
                                ? `Owner: ${task.assigneeName}`
                                : "Unassigned"}
                              {task.delegateName
                                ? ` · Agent: ${task.delegateName}`
                                : ""}
                              {` · ${task.runs} ${task.runs === 1 ? "run" : "runs"}`}
                            </span>
                          </span>
                          <div className="hidden items-center gap-3 sm:flex">
                            <ShareBar value={task.tokens} max={maxTaskTokens} />
                            <span className="w-16 text-right text-xs tabular-nums text-muted">
                              {formatDuration(task.runMinutes)}
                            </span>
                          </div>
                          <span className="flex shrink-0 flex-col items-end">
                            <span className="text-sm font-semibold tabular-nums text-foreground">
                              {formatTokens(task.tokens)}
                            </span>
                            <span className="text-xs text-muted">tokens</span>
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}

                {tab === "agents" && (
                  <div
                    className="divide-y divide-line"
                    data-leaderboard-agents=""
                  >
                    {data.agents.length === 0 ? (
                      <p className="m-0 p-6 text-center text-sm text-muted">
                        No agent usage recorded in the last {days} days.
                      </p>
                    ) : (
                      data.agents.map((ag, index) => (
                        <button
                          key={ag.agentUserId}
                          type="button"
                          onClick={() => onOpenMember(ag.agentUserId)}
                          className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent pointer-coarse:min-h-12"
                        >
                          <RankBadge rank={index + 1} />
                          <span
                            className="size-8 shrink-0 overflow-hidden rounded-full"
                            aria-hidden="true"
                          >
                            <UserBlobatar
                              user={{
                                id: ag.agentUserId,
                                name: ag.name,
                                avatarSeed: ag.avatarSeed,
                              }}
                            />
                          </span>
                          <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate text-sm font-medium text-foreground">
                              {ag.name}
                            </span>
                            <span className="truncate text-xs text-muted">
                              {ag.runs} {ag.runs === 1 ? "run" : "runs"} ·{" "}
                              {ag.completedRuns} completed ·{" "}
                              {formatTokens(ag.inputTokens)} in /{" "}
                              {formatTokens(ag.outputTokens)} out
                            </span>
                          </span>
                          <div className="hidden items-center gap-3 sm:flex">
                            <ShareBar value={ag.tokens} max={maxAgentTokens} />
                            <span className="w-16 text-right text-xs tabular-nums text-muted">
                              {formatDuration(ag.runMinutes)}
                            </span>
                          </div>
                          <span className="flex shrink-0 flex-col items-end">
                            <span className="text-sm font-semibold tabular-nums text-foreground">
                              {formatTokens(ag.tokens)}
                            </span>
                            <span className="text-xs text-muted">tokens</span>
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
