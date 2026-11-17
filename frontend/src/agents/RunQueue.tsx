/* Screen 3 — Run queue (HOJA-602-A-16).
 *
 * An issue list of delegated tasks grouped by session status. The rows are the issue list's own
 * `IssueRow`: the identifier, title and priority are the issue's, and the only addition is the
 * session chip this surface passes through the row's `trailing` slot.
 *
 * Sessions are server-backed and paginated; claim and verdict chips represent verification.
 *
 * The row's leading glyph is the *session* status, not the issue status. On this screen the
 * question a reader asks of a row is "where is this run right now?", and the group header above
 * it answers the same question; the issue's own status is still one click away on the task.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { GraphStatusIcon } from "../graph/shared";
import {
  IssueRow,
  defaultDisplay,
  type IssueRowModel,
  type LinDisplay,
} from "../graph/tasks";
import { getAgentsAdapter } from "./adapter";
import { AGENTS_TABS, AgentsListShell, SearchField } from "./chrome";
import {
  SESSION_STATUSES,
  sessionStatusName,
  verdictColor,
  verdictLabel,
} from "./types";
import type { AgentSession, SessionStatus } from "./types";

const SESSION_TYPE: Record<SessionStatus, { type: string; color: string }> = {
  queued: { type: "backlog", color: "#e2a03f" },
  active: { type: "started", color: "#5e6ad2" },
  "awaiting-input": { type: "unstarted", color: "#c98a1b" },
  "awaiting-verification": { type: "unstarted", color: "#2f9e6f" },
  done: { type: "completed", color: "#95a2b3" },
  failed: { type: "canceled", color: "#c0392b" },
};

export function sessionRow(s: AgentSession): IssueRowModel {
  const tone = SESSION_TYPE[s.status];
  return {
    id: s.issueId,
    identifier: s.issueIdentifier,
    title: s.issueTitle,
    priority: s.priority,
    state: {
      name: sessionStatusName(s.status),
      type: tone.type,
      color: tone.color,
    },
    stateChangedAt: s.startedAt || null,
    createdAt: s.startedAt || null,
    updatedAt: s.endedAt || s.startedAt || null,
  };
}

export function RunQueue({
  workspaceName,
  agentKey,
  onOpenRun,
  onOpenAgent,
  tab,
  onTab,
}: {
  workspaceName: string;
  /** Omitted on the workspace-wide queue. */
  agentKey?: string;
  onOpenRun: (session: AgentSession) => void;
  onOpenAgent: (agentKey: string) => void;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<SessionStatus[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const display: LinDisplay = useMemo(
    () => ({
      ...defaultDisplay,
      grouping: "none",
      props: { ...defaultDisplay.props, updated: false },
    }),
    [],
  );

  useEffect(() => {
    let live = true;
    setLoading(true);
    getAgentsAdapter()
      .listSessions(agentKey ? { agentId: agentKey } : undefined)
      .then((page) => {
        if (!live) return;
        setSessions(page.sessions);
        setCursor(page.endCursor);
        setHasMore(page.hasNextPage);
        setError("");
      })
      .catch((e: Error) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [agentKey]);

  const loadMore = useCallback(async () => {
    if (!cursor || paging) return;
    setPaging(true);
    try {
      const page = await getAgentsAdapter().listSessions({
        agentId: agentKey,
        after: cursor,
      });
      setSessions((current) => [...current, ...page.sessions]);
      setCursor(page.endCursor);
      setHasMore(page.hasNextPage);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPaging(false);
    }
  }, [agentKey, cursor, paging]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sessions.filter((s) => {
      if (
        needle &&
        !`${s.issueIdentifier} ${s.issueTitle} ${s.agentName} ${s.reason || ""}`
          .toLowerCase()
          .includes(needle)
      )
        return false;
      if (status.length && !status.includes(s.status)) return false;
      return true;
    });
  }, [sessions, q, status]);

  const groups = useMemo(
    () =>
      SESSION_STATUSES.map((s) => ({
        key: s.id,
        name: s.name,
        items: visible.filter((v) => v.status === s.id),
      })).filter((g) => g.items.length),
    [visible],
  );

  const collapsedSet = new Set(collapsed);
  const agentLabel =
    sessions[0]?.agentName || (agentKey ? agentKey.slice(0, 8) : "");

  return (
    <AgentsListShell
      ariaLabel={agentKey ? `${agentKey} run queue` : "Run queue"}
      surface="run-queue"
      workspaceName={workspaceName}
      trail={agentKey ? `${agentLabel} · Run queue` : "Run queue"}
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbar={
        <>
          <SearchField
            value={q}
            onChange={setQ}
            placeholder="Search the queue…"
            label="Search the run queue"
          />
          <span className="flex-1" />
          {SESSION_STATUSES.map((s) => {
            const on = status.includes(s.id);
            return (
              <button
                key={s.id}
                type="button"
                className="inline-flex items-center border border-line hover:text-foreground hover:border-accent aria-pressed:bg-subtle aria-pressed:text-foreground aria-pressed:font-semibold gap-1.5 pt-1.25 pr-2.75 pb-1.25 pl-2.75 border-solid rounded-[99px] bg-panel text-[12px] text-muted font-medium aria-pressed:border-line shell-phone:min-w-11! shell-phone:min-h-11!"
                aria-pressed={on}
                onClick={() =>
                  setStatus((cur) =>
                    on ? cur.filter((x) => x !== s.id) : [...cur, s.id],
                  )
                }
              >
                {s.name}
              </button>
            );
          })}
        </>
      }
      chips={
        status.length ? (
          <>
            {status.map((s) => (
              <span
                key={s}
                className="inline-flex items-center border border-line gap-1.5 text-[12px] border-solid bg-subtle rounded-[99px] pt-0.75 pr-1.5 pb-0.75 pl-2.5 text-foreground"
              >
                {sessionStatusName(s)}
                <button
                  className="border-0 hover:text-foreground [background:none] text-muted text-[13px] leading-none pt-0.5 pr-1 pb-0.5 pl-1 rounded-sm"
                  type="button"
                  aria-label={`Remove filter ${sessionStatusName(s)}`}
                  onClick={() => setStatus((cur) => cur.filter((x) => x !== s))}
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              className="inline-flex items-center border-0 hover:bg-subtle hover:text-foreground gap-2 [background:none] text-muted text-[13px] pt-1.25 pr-1.5 pb-1.25 pl-1.5 rounded-md"
              onClick={() => setStatus([])}
            >
              Clear all
            </button>
          </>
        ) : null
      }
    >
      {loading && (
        <p
          className="text-center pt-10 pr-10 pb-10 pl-10 text-muted"
          role="status"
        >
          Loading the queue…
        </p>
      )}
      {error && (
        <p
          className="border border-danger/40 mt-4 mr-6 mb-4 ml-6 pt-3 pr-4 pb-3 pl-4 border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]"
          role="alert"
        >
          {error}
        </p>
      )}
      {!loading && !error && (
        <div className="pt-1.5 pr-3 pb-10 pl-3">
          {groups.map((g) => {
            const isCollapsed = collapsedSet.has(g.key);
            const tone = SESSION_TYPE[g.key as SessionStatus];
            return (
              <section key={g.key} className="mt-2 mr-2 mb-1 ml-2">
                <header className="flex items-center gap-2 bg-subtle rounded-[7px] min-h-9 pt-0 pr-2.5 pb-0 pl-2.5 text-[13px]">
                  <button
                    type="button"
                    className="grid place-items-center border-0 hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [background:none] text-muted pt-1 pr-1 pb-1 pl-1 rounded-sm"
                    aria-expanded={!isCollapsed}
                    aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${g.name}`}
                    onClick={() =>
                      setCollapsed((c) =>
                        c.includes(g.key)
                          ? c.filter((x) => x !== g.key)
                          : [...c, g.key],
                      )
                    }
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      style={{
                        transform: isCollapsed ? "rotate(-90deg)" : undefined,
                      }}
                      aria-hidden="true"
                    >
                      <path
                        d="M4 6.5 8 10.5 12 6.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                  <GraphStatusIcon
                    state={{ name: g.name, type: tone.type, color: tone.color }}
                    size={13}
                  />
                  <b className="font-semibold">{g.name}</b>
                  <span className="text-muted font-medium text-[12px]">
                    {g.items.length}
                  </span>
                  <span className="flex-1" />
                </header>
                {!isCollapsed &&
                  g.items.map((s) => (
                    <IssueRow
                      key={s.id}
                      issue={sessionRow(s)}
                      display={display}
                      selected={false}
                      showCheckbox={false}
                      onSelect={() => undefined}
                      onOpen={() => onOpenRun(s)}
                      childCount={0}
                      trailing={
                        <>
                          <span
                            className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                            data-session-chip={s.status}
                            title={`${s.agentName} · ${sessionStatusName(s.status)} · ${s.state}${s.reason ? ` · ${s.reason}` : ""}${s.harness ? ` · harness ${s.harness}` : ""}`}
                          >
                            <i
                              className="flex-none not-italic w-2.25 h-2.25 rounded-[50%]"
                              style={{
                                background: SESSION_TYPE[s.status].color,
                              }}
                            />
                            {s.agentName}
                          </span>
                          {/* A queued lease naming a harness waits for a runner that
                              declares it, rather than being held by the schedule. */}
                          {s.status === "queued" && s.harness && (
                            <span
                              className="inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-full border border-line bg-panel px-2 text-xs font-medium text-(--card-meta)"
                              data-harness-waiting={s.harness}
                              title={`Queued for harness ${s.harness}: the lease goes to a runner that declared it, and no runner has taken it yet`}
                            >
                              <i
                                className="size-2 shrink-0 rounded-full not-italic"
                                style={{
                                  background: SESSION_TYPE[s.status].color,
                                }}
                              />
                              {s.harness}
                            </span>
                          )}
                          {s.verdict ? (
                            <span
                              className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                              data-verdict={s.verdict}
                              title={`${verdictLabel(s.verdict)} — recorded by a separate principal; claim submitted ${s.claimAt ? new Date(s.claimAt).toISOString().slice(0, 10) : "earlier"}`}
                            >
                              <i
                                className="flex-none not-italic w-2.25 h-2.25 rounded-[50%]"
                                style={{ background: verdictColor(s.verdict) }}
                              />
                              {verdictLabel(s.verdict)}
                            </span>
                          ) : s.status === "awaiting-verification" &&
                            s.claim ? (
                            <span
                              className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                              data-claim="awaiting"
                              title={`A claim submitted ${s.claimAt ? new Date(s.claimAt).toISOString() : "unknown"}; no principal has signed it yet`}
                            >
                              <i
                                className="flex-none not-italic w-2.25 h-2.25 rounded-[50%]"
                                style={{ background: verdictColor(null) }}
                              />
                              Awaiting verifier
                            </span>
                          ) : null}
                          {s.reason && (
                            <span className="hidden md:inline-block max-w-56 overflow-hidden text-ellipsis whitespace-nowrap align-middle text-[12px] text-muted">
                              {s.reason}
                            </span>
                          )}
                        </>
                      }
                    />
                  ))}
              </section>
            );
          })}
          {!visible.length && (
            <div className="text-center pt-15 pr-5 pb-15 pl-5 text-muted">
              <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
                {sessions.length
                  ? "No run matches these filters"
                  : "Nothing is queued"}
              </h2>
              <p>
                {sessions.length
                  ? "The search and status chips narrow the runs loaded so far; clear them to see the whole queue."
                  : "Delegate a task to an agent and it appears here, grouped by where the run stands."}
              </p>
            </div>
          )}
          <div className="flex items-center gap-2 py-2 text-[12px] text-muted">
            <span>
              {sessions.length} run{sessions.length === 1 ? "" : "s"} loaded
            </span>
            {hasMore && (
              <button
                type="button"
                className="border border-line rounded-md px-3 py-1.5 hover:bg-subtle disabled:opacity-50"
                data-action="load-more"
                disabled={paging}
                onClick={() => void loadMore()}
              >
                {paging ? "Loading…" : "Load more runs"}
              </button>
            )}
          </div>
        </div>
      )}
      {agentKey && (
        <p className="text-[12px] text-muted pt-0 pr-5 pb-6 pl-5">
          <button
            type="button"
            className="inline-flex cursor-pointer items-center gap-1.5 whitespace-nowrap border-0 bg-transparent p-0 text-muted hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:min-w-11! shell-phone:min-h-11!"
            onClick={() => onOpenAgent(agentKey)}
          >
            ‹ Back to {agentLabel}
          </button>
        </p>
      )}
      <p className="text-[12px] text-muted pt-0 pr-5 pb-6 pl-5">
        A queue row opens that agent's queue. The run's transcript is on the
        agent page.
      </p>
    </AgentsListShell>
  );
}
