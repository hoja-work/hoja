import { styleClasses, styleHooks } from "./shared";
import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  ArrowUpRight,
  Bell,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  Link2,
  ListFilter,
  Plus,
  Settings2,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { gql, stateKey } from "../main";
import type {
  Cycle,
  Issue,
  IssueLabel,
  IssueState,
  Project,
  Team,
  Viewer,
} from "../main";
import {
  getSync,
  useEntityVersion,
  useIssues,
  useSyncMeta,
  useSyncStatus,
} from "../sync";
import {
  applyLinFilters,
  defaultDisplay,
  emptyFilters,
  groupLinIssues,
  orderLinIssues,
  DisplayPanel as _DP,
  FilterMenu,
  IssueRow,
  LinDisplay,
  LinFilters,
  LinIssue,
  linIssueFields,
  SlaChip,
} from "./tasks";
import {
  Avatar,
  CycleGlyph,
  FavStar,
  IconBtn,
  LabelChip,
  LinMenu,
  LinPopover,
  GraphStatusIcon,
  PriorityIcon,
  Ring,
  Tip,
  cx,
  fmtDate,
  fmtRange,
  useFavorite,
} from "./shared";
import {
  asPct,
  cycleStats,
  deliveryRate,
  dropoutRate,
  remainingSteps,
  scopeCompletion,
} from "./cycleMetrics";
import type { CycleStats } from "./cycleMetrics";
import { setFollowing, useFollowing } from "../inbox/follow";
import { cycleKeyTarget } from "./cycleSelection";

/* the upstream API 1:1 Cycles views. */

export type CycleState = "planned" | "upcoming" | "current" | "completed";

export function cycleState(c: Cycle, now = Date.now()): CycleState {
  if (c.completedAt) return "completed";
  const s = c.startsAt ? Date.parse(c.startsAt) : NaN;
  const e = c.endsAt ? Date.parse(c.endsAt) : NaN;
  if (!Number.isNaN(e) && e < now) return "completed";
  if (!Number.isNaN(s) && !Number.isNaN(e) && s <= now && now <= e)
    return "current";
  if (!Number.isNaN(s) && s > now) {
    // the nearest future cycle is "upcoming"; the rest are planned — resolved by caller ordering
    return "upcoming";
  }
  return "planned";
}

export function CycleStateIcon({
  state,
  size = 15,
}: {
  state: CycleState;
  size?: number;
}) {
  if (state === "completed")
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        fill="none"
        stroke="#8a94a6"
        strokeWidth="1.5"
        aria-label="Completed"
      >
        <path d="M2.7 8a5.3 5.3 0 1 1 1.6 3.8" strokeLinecap="round" />
        <path d="M2.7 12.4v-3h3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (state === "current")
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        fill="none"
        stroke="#5e6ad2"
        strokeWidth="1.5"
        aria-label="Current"
      >
        <circle cx="8" cy="8" r="6.2" />
        <path d="M6.4 5.4v5.2l4.5-2.6z" fill="#5e6ad2" stroke="none" />
      </svg>
    );
  if (state === "upcoming")
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        fill="none"
        stroke="#8a94a6"
        strokeWidth="1.5"
        aria-label="Upcoming"
      >
        <circle cx="8" cy="8" r="6.2" />
        <path
          d="M6.6 5.6v4.8l4.2-2.4z"
          fill="#8a94a6"
          stroke="none"
          opacity=".7"
        />
      </svg>
    );
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="#8a94a6"
      strokeWidth="1.5"
      aria-label="Planned"
    >
      <circle cx="8" cy="8" r="5.6" strokeDasharray="2.6 2.2" />
      <path d="M8 5v3.2l2.2 1.4" strokeLinecap="round" />
    </svg>
  );
}

export type { CycleStats };
export const statsFor = cycleStats;

/* ---------------- local-first data layer ----------------
 *
 * Cycles come from the synced workspace metadata (useSyncMeta) and issues per
 * cycle from the synced issue store (useIssues), so every cycle page renders
 * from memory and follows other clients through the store's delta pulls.
 * Only what the store lacks is fetched: closed issues of cycles that predate
 * the store's closed window (adopted into the store once per session), cycles
 * beyond the metadata's first 250, and the team cadence/automation settings.
 * Local cycle edits are overlays over the metadata until the server confirms
 * them (or are dropped on failure), mirroring SyncHandle.patch for issues.
 */

const EMPTY: LinIssue[] = [];
const cycleOverlays = new Map<string, Partial<Cycle>[]>();
const extraCycles = new Map<string, Cycle>();
let cycleLocalVersion = 0;
const cycleListeners = new Set<() => void>();
const subscribeCycles = (cb: () => void) => {
  cycleListeners.add(cb);
  return () => {
    cycleListeners.delete(cb);
  };
};
function bumpCycles() {
  cycleLocalVersion++;
  for (const cb of cycleListeners) cb();
}

/** Overlay a local cycle edit; call the returned function with whether the server accepted it. */
export function patchCycle(
  cycleId: string,
  patch: Partial<Cycle>,
): (accepted: boolean, fresh?: Cycle | null) => void {
  const layers = cycleOverlays.get(cycleId) || [];
  layers.push(patch);
  cycleOverlays.set(cycleId, layers);
  bumpCycles();
  return (accepted, fresh) => {
    if (accepted && fresh)
      extraCycles.set(fresh.id, { ...extraCycles.get(fresh.id), ...fresh });
    const drop = () => {
      const rest = cycleOverlays.get(cycleId)?.filter((p) => p !== patch) || [];
      if (rest.length) cycleOverlays.set(cycleId, rest);
      else cycleOverlays.delete(cycleId);
      bumpCycles();
    };
    // Keep an accepted edit until a pull has brought the refreshed metadata.
    const sync = getSync();
    if (accepted && sync) void sync.pull().finally(drop);
    else drop();
  };
}

function rememberCycles(cycles: Cycle[]) {
  if (!cycles.length) return;
  for (const c of cycles)
    extraCycles.set(c.id, { ...extraCycles.get(c.id), ...c });
  bumpCycles();
}

/** Every known cycle (metadata ∪ fetched extras, with pending local edits applied); null until the store has metadata. */
export function useCycles(): Cycle[] | null {
  const meta = useSyncMeta();
  const version = useSyncExternalStore(
    subscribeCycles,
    () => cycleLocalVersion,
  );
  return useMemo(() => {
    if (!meta) return null;
    const byId = new Map<string, Cycle>();
    for (const c of extraCycles.values()) byId.set(c.id, c);
    // Metadata is fresher than anything fetched earlier; extras only add fields it lacks (description).
    for (const c of meta.cycles.nodes) {
      const prior = byId.get(c.id);
      byId.set(c.id, prior ? { ...prior, ...c } : c);
    }
    for (const [id, layers] of cycleOverlays) {
      const base = byId.get(id);
      if (base) byId.set(id, Object.assign({}, base, ...layers));
    }
    return [...byId.values()];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, version]);
}

/* Issues grouped by cycle id, shared by every mounted cycle view. A cycle's
 * array keeps its identity while its members are unchanged, so per-cycle
 * stats (cached by array) and memoized rows skip unrelated store changes. */
let groupMemo: { issues: Issue[] | null; groups: Map<string, LinIssue[]> } = {
  issues: null,
  groups: new Map(),
};
function groupByCycle(issues: Issue[]): Map<string, LinIssue[]> {
  if (groupMemo.issues === issues) return groupMemo.groups;
  const next = new Map<string, LinIssue[]>();
  for (const i of issues) {
    const cid = i.cycle?.id;
    if (!cid) continue;
    const arr = next.get(cid);
    if (arr) arr.push(i as LinIssue);
    else next.set(cid, [i as LinIssue]);
  }
  const prev = groupMemo.groups;
  for (const [cid, arr] of next) {
    const old = prev.get(cid);
    if (old && old.length === arr.length && old.every((x, k) => x === arr[k]))
      next.set(cid, old);
  }
  groupMemo = { issues, groups: next };
  return next;
}
export function useIssuesByCycle(): Map<string, LinIssue[]> {
  return groupByCycle(useIssues());
}

const statsCache = new WeakMap<LinIssue[], CycleStats>();
export function cachedStats(issues: LinIssue[]): CycleStats {
  let s = statsCache.get(issues);
  if (!s) {
    s = statsFor(issues);
    statsCache.set(issues, s);
  }
  return s;
}

/* Closed issues older than the store's window are not synced. A cycle that
 * started before that window may own some; fetch them once and adopt them so
 * the store (and its deltas) keeps them fresh from then on. */
const historyFields = `{ id identifier title createdAt updatedAt stateChangedAt dueDate priority priorityLabel state { id name color type } team { id key name } project { id name } assignee { id name avatarSeed } creator { id name avatarSeed } cycle { id name number } labels { nodes { id name color } } }`;
const historyRequested = new Set<string>();
// Session markers survive reloads (module state does not); storage may be unavailable.
const sessionGet = (k: string) => {
  try {
    return sessionStorage.getItem(k);
  } catch {
    return null;
  }
};
const sessionSet = (k: string, v: string) => {
  try {
    sessionStorage.setItem(k, v);
  } catch {
    /* best effort */
  }
};
const emptyHistoryKey = (cycleId: string) =>
  `hoja-cycle-history-empty:${cycleId}`;
export function cycleNeedsHistory(c: Cycle, closedCutoff: string): boolean {
  if (!closedCutoff) return false;
  return !c.startsAt || c.startsAt.slice(0, 10) < closedCutoff;
}
async function loadCycleHistory(
  cycleIds: string[],
  closedCutoff: string,
): Promise<void> {
  const ids = cycleIds.filter((id) => !historyRequested.has(id));
  if (!ids.length) return;
  for (const id of ids) historyRequested.add(id);
  const found = new Set<string>();
  try {
    let after: string | null = null;
    for (let page = 0; page < 20; page++) {
      const d = await gql(
        `query CycleHistory($after:String,$filter:IssueFilter){issues(first:250,after:$after,filter:$filter){nodes ${historyFields} pageInfo{hasNextPage endCursor}}}`,
        {
          after,
          filter: {
            cycle: { id: { in: ids } },
            state: { type: { in: ["completed", "canceled"] } },
          },
        },
      );
      getSync()?.adopt(d.issues.nodes);
      for (const n of d.issues.nodes as Issue[])
        if (n.cycle?.id) found.add(n.cycle.id);
      const info = d.issues.pageInfo;
      if (!info?.hasNextPage || !info.endCursor || info.endCursor === after)
        break;
      after = info.endCursor;
    }
    // Cycles with nothing out of window leave no adopted trace; remember that for this window.
    for (const id of ids)
      if (!found.has(id)) sessionSet(emptyHistoryKey(id), closedCutoff);
  } catch {
    for (const id of ids) historyRequested.delete(id); // retry on the next view
  }
}
/* A closed issue older than the window only enters the store through adopt
 * (bootstrap leaves it out; any edit makes it recent), so finding one means
 * this cycle's history was adopted into the persisted snapshot already. */
function hasAdoptedHistory(
  issues: LinIssue[] | undefined,
  closedCutoff: string,
): boolean {
  return !!issues?.some(
    (i) =>
      (i.state?.type === "completed" || i.state?.type === "canceled") &&
      (i.updatedAt || "") < closedCutoff,
  );
}
/** Background-load the out-of-window closed issues of these cycles (newest first, bounded). */
function useCycleHistory(cycles: Cycle[], limit: number) {
  const { ready, closedCutoff } = useSyncStatus();
  const byCycle = useIssuesByCycle();
  const key = useMemo(
    () =>
      cycles
        .filter((c) => cycleNeedsHistory(c, closedCutoff))
        .sort((a, b) => (b.startsAt || "").localeCompare(a.startsAt || ""))
        .slice(0, limit)
        .map((c) => c.id)
        .join(","),
    [cycles, closedCutoff, limit],
  );
  useEffect(() => {
    if (!ready || !key) return;
    const missing = key
      .split(",")
      .filter(
        (id) =>
          !hasAdoptedHistory(byCycle.get(id), closedCutoff) &&
          sessionGet(emptyHistoryKey(id)) !== closedCutoff,
      );
    if (missing.length) void loadCycleHistory(missing, closedCutoff);
    // byCycle is read once per key; adopting changes it and must not re-trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, key, closedCutoff]);
}

/* Reading cycles through GraphQL used to roll team cadences forward
 * (reconcile). Keep that side effect, at most every few minutes per team, off
 * the render path. If the metadata is truncated, the same request also
 * supplies the team's cycles the metadata could not carry. */
const reconcileKey = (teamId: string) => `hoja-cycle-reconciled:${teamId}`;
function useTeamCycleUpkeep(teamId: string | undefined) {
  const meta = useSyncMeta();
  const hasMeta = Boolean(meta);
  const truncated = (meta?.cycles.nodes.length || 0) >= 250;
  const cycleVersion = useEntityVersion(["cycle"], undefined, 250);
  useEffect(() => {
    if (!teamId || !hasMeta) return;
    const last = Number(sessionGet(reconcileKey(teamId)) || 0);
    if (!truncated && Date.now() - last < 5 * 60_000) return;
    sessionSet(reconcileKey(teamId), String(Date.now()));
    gql(
      `query TeamCycles($filter:CycleFilter){cycles(first:100,filter:$filter){nodes{${cycleFields}}}}`,
      { filter: { team: { id: { eq: teamId } } } },
    )
      .then((d) => {
        if (truncated) rememberCycles(d.cycles.nodes);
      })
      .catch(() => {
        sessionSet(reconcileKey(teamId), "0");
      });
    // cycleVersion: refetch the truncated set when another client changes cycles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId, hasMeta, truncated, truncated ? cycleVersion : 0]);
}

/* Optimistic issue update from a cycle view: overlay → mutation → settle/rollback. */
export async function updateIssueOptimistic(
  issueId: string,
  input: Record<string, unknown>,
): Promise<void> {
  const sync = getSync();
  const settle = sync ? sync.patch(issueId, sync.patchFromInput(input)) : null;
  try {
    const d = await gql(
      `mutation CycleIssueUpdate($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}`,
      { id: issueId, input },
    );
    if (!d.issueUpdate?.success) throw new Error("Update was rejected");
    settle?.(true);
  } catch (e) {
    settle?.(false);
    throw e;
  }
}

const cycleFields =
  "id name description number startsAt endsAt completedAt progress team{id key name}";
/** Optimistic cycleUpdate. Null inputs mean "unchanged" on this server, so they are not overlaid. */
async function updateCycleOptimistic(
  cycleId: string,
  input: Record<string, unknown>,
): Promise<void> {
  const patch: Partial<Cycle> = {};
  for (const [k, v] of Object.entries(input))
    if (v != null) (patch as Record<string, unknown>)[k] = v;
  const settle = patchCycle(cycleId, patch);
  try {
    const d = await gql(
      `mutation CycleUpdate($id:String!,$input:CycleUpdateInput!){cycleUpdate(id:$id,input:$input){success cycle{${cycleFields}}}}`,
      { id: cycleId, input },
    );
    if (!d.cycleUpdate?.success) throw new Error("Cycle update was rejected");
    settle(true, d.cycleUpdate.cycle);
  } catch (e) {
    settle(false);
    throw e;
  }
}

/* ---------------- burndown chart ---------------- */

/* The daily steps themselves live in ./cycleMetrics (remainingSteps), which
 * drops an issue from "remaining" when it is delivered *or* canceled. Without
 * history this is still the best client-side burndown. */
export function BurndownChart({
  cycle,
  stats,
  issues,
  height = 200,
}: {
  cycle: Cycle;
  stats: CycleStats;
  issues?: LinIssue[];
  height?: number;
}) {
  const W = 560,
    H = height,
    padL = 8,
    padR = 8,
    padT = 10,
    padB = 22;
  const now = Date.now();
  const start = cycle.startsAt ? Date.parse(cycle.startsAt) : now - 6 * 864e5;
  const end = cycle.endsAt ? Date.parse(cycle.endsAt) : now + 864e5;
  const x0 = start - 864e5,
    x1 = end + 864e5;
  const maxY = Math.max(stats.scope, 4);
  const X = (t: number) => padL + ((t - x0) / (x1 - x0)) * (W - padL - padR);
  const Y = (v: number) => padT + (1 - v / maxY) * (H - padT - padB);
  const steps = useMemo(
    () => remainingSteps(issues, stats.scope, start, Math.min(now, end)),
    // Recomputed when the cycle's issues (stable per membership) or window change, not per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [issues, stats, start, end],
  );
  const last = steps.length ? steps[steps.length - 1][1] : stats.scope;
  const until = Math.max(start, Math.min(now, end));
  const d = (t: number) =>
    new Date(t).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  const hatchId = `hatch-${cycle.id}`;
  return (
    <svg
      className={styleClasses(
        "in-data-[style-hooks~='lin-cycle-preview-chart']:max-h-42 w-full h-auto block",
      )}
      data-style-hooks={styleHooks(
        "lin-burndown in-data-[style-hooks~='lin-cycle-preview-chart']:max-h-[168px] w-full h-auto block",
      )}
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Burndown: scope ${stats.scope}, open ${stats.open}, delivered ${stats.completed}, canceled ${stats.canceled}`}
    >
      <defs>
        <pattern
          id={hatchId}
          width="7"
          height="7"
          patternTransform="rotate(45)"
          patternUnits="userSpaceOnUse"
        >
          <rect width="7" height="7" fill="transparent" />
          <line
            x1="0"
            y1="0"
            x2="0"
            y2="7"
            stroke="var(--line)"
            strokeWidth="2.4"
          />
        </pattern>
      </defs>
      {now > x0 && now < x1 && (
        <rect
          x={X(Math.max(now, start))}
          y={padT}
          width={Math.max(0, X(end) - X(Math.max(now, start)))}
          height={H - padT - padB}
          fill={`url(#${hatchId})`}
        />
      )}
      <line
        className={styleClasses(
          "in-data-[style-hooks~='lin-burndown']:stroke-line in-data-[style-hooks~='lin-burndown']:stroke-1",
        )}
        data-style-hooks={styleHooks(
          "grid in-data-[style-hooks~='lin-burndown']:[stroke:var(--line)] in-data-[style-hooks~='lin-burndown']:stroke-1",
        )}
        x1={padL}
        y1={Y(stats.scope)}
        x2={W - padR}
        y2={Y(stats.scope)}
      />
      <line
        x1={X(start)}
        y1={Y(stats.scope)}
        x2={X(end)}
        y2={Y(0)}
        stroke="#8a94a6"
        strokeWidth="1.4"
        strokeDasharray="5 4"
      />
      {steps.length > 1 ? (
        <polyline
          fill="none"
          stroke="#8a94a6"
          strokeWidth="1.8"
          strokeLinejoin="round"
          points={steps
            .map(([t, v]) => `${X(t).toFixed(1)},${Y(v).toFixed(1)}`)
            .join(" ")}
        />
      ) : (
        <line
          x1={X(start)}
          y1={Y(stats.scope)}
          x2={X(until)}
          y2={Y(stats.scope)}
          stroke="#8a94a6"
          strokeWidth="1.8"
        />
      )}
      <circle cx={X(start)} cy={Y(stats.scope)} r="3.4" fill="#e5b800" />
      <circle cx={X(until)} cy={Y(last)} r="3.4" fill="#5e6ad2" />
      <text
        className={styleClasses(
          "in-data-[style-hooks~='lin-burndown']:text-[10px] in-data-[style-hooks~='lin-burndown']:fill-muted",
        )}
        data-style-hooks={styleHooks(
          "axis in-data-[style-hooks~='lin-burndown']:text-[10px] in-data-[style-hooks~='lin-burndown']:[fill:var(--muted)]",
        )}
        x={X(start)}
        y={H - 6}
        textAnchor="middle"
      >
        {d(start)}
      </text>
      <text
        className={styleClasses(
          "in-data-[style-hooks~='lin-burndown']:text-[10px] in-data-[style-hooks~='lin-burndown']:fill-muted",
        )}
        data-style-hooks={styleHooks(
          "axis in-data-[style-hooks~='lin-burndown']:text-[10px] in-data-[style-hooks~='lin-burndown']:[fill:var(--muted)]",
        )}
        x={X(end)}
        y={H - 6}
        textAnchor="middle"
      >
        {d(end)}
      </text>
    </svg>
  );
}

function Legend({ stats }: { stats: CycleStats }) {
  const row = (color: string, label: string, n: number, pct?: number) => (
    <div
      className={styleClasses(
        "[[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:flex [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:items-center [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:gap-2 [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:py-1.75 [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:px-0 [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:border-b [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:border-b-line [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&:last-child]:border-b-0",
      )}
      data-style-hooks={styleHooks(
        "lr [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:flex [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:items-center [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:gap-[8px] [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:py-[7px] [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:px-0 [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:border-b [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:border-b-line [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&:last-child]:border-b-0",
      )}
    >
      <span
        className={styleClasses(
          "[[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:w-2 [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:h-2 [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:rounded-xs [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:flex-none",
        )}
        data-style-hooks={styleHooks(
          "sw [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:w-[8px] [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:h-[8px] [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:rounded-[2px] [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:flex-none",
        )}
        style={{ background: color }}
      />
      {label}
      <span
        className={styleClasses(
          "in-data-[style-hooks~='lin-multibar']:font-semibold in-data-[style-hooks~='lin-multibar']:py-0 in-data-[style-hooks~='lin-multibar']:px-2 in-data-[style-hooks~='lin-multibar']:whitespace-nowrap [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:ml-auto [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:text-muted [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:whitespace-nowrap [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&_b]:text-foreground",
        )}
        data-style-hooks={styleHooks(
          "n in-data-[style-hooks~='lin-multibar']:font-semibold in-data-[style-hooks~='lin-multibar']:py-0 in-data-[style-hooks~='lin-multibar']:px-[8px] in-data-[style-hooks~='lin-multibar']:whitespace-nowrap [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:ml-auto [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:text-muted [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&]:whitespace-nowrap [[data-style-hooks~='lin-cycle-chart']_[data-style-hooks~='legend']_&_b]:text-foreground",
        )}
      >
        <b>{n}</b>
        {pct !== undefined && <> · {pct}%</>}
      </span>
    </div>
  );
  /* Every row shares one denominator (scope) so the percentages add up and no
   * two rows can be read as being out of different wholes. Canceled is its own
   * row: dropping work must never read as delivering it. */
  const pct = (n: number) =>
    stats.scope ? Math.round((n / stats.scope) * 100) : 0;
  return (
    <div
      className={styleClasses(
        "in-data-[style-hooks~='lin-cycle-chart']:w-47.5 in-data-[style-hooks~='lin-cycle-chart']:flex-none in-data-[style-hooks~='lin-cycle-chart']:text-[12px]",
      )}
      data-style-hooks={styleHooks(
        "legend in-data-[style-hooks~='lin-cycle-chart']:w-[190px] in-data-[style-hooks~='lin-cycle-chart']:flex-none in-data-[style-hooks~='lin-cycle-chart']:text-[12px]",
      )}
    >
      {row("#8a94a6", "Scope", stats.scope)}
      {row("#5e6ad2", "Delivered", stats.completed, pct(stats.completed))}
      {row("#e5b800", "Open", stats.open, pct(stats.open))}
      {row("#95a2b3", "Canceled", stats.canceled, pct(stats.canceled))}
    </div>
  );
}

/* ---------------- overview rows ---------------- */

const CycleOverviewRow = React.memo(function CycleOverviewRow({
  cycle,
  state,
  stats,
  selected,
  controls,
  onSelect,
  onKeyDown,
  buttons,
}: {
  cycle: Cycle;
  state: CycleState;
  stats: CycleStats;
  selected: boolean;
  controls: string;
  onSelect: (c: Cycle) => void;
  onKeyDown: (e: React.KeyboardEvent, c: Cycle) => void;
  buttons: { current: Map<string, HTMLButtonElement> };
}) {
  const name = cycle.name || `Cycle ${cycle.number ?? ""}`;
  const ofScope = asPct(scopeCompletion(stats)) ?? 0;
  const dropped = asPct(dropoutRate(stats)) ?? 0;
  const badge =
    state === "current"
      ? "Current"
      : state === "upcoming"
        ? "Upcoming"
        : state === "completed"
          ? "Completed"
          : "Planned";
  return (
    <div
      className={styleClasses(
        cx(
          "relative grid grid-cols-[88px_minmax(0,1fr)] mb-1.75 border border-solid border-transparent rounded-xl [transition:background-color_.16s_ease,border-color_.16s_ease,box-shadow_.16s_ease] [&:hover]:bg-[color-mix(in_srgb,var(--panel)_65%,transparent)] [&:hover]:border-line shell-phone:block shell-phone:flex-[0_0_min(76vw,270px)] shell-phone:m-0 shell-phone:snap-start shell-phone:bg-panel shell-phone:border-line motion-reduce:[transition:none]",
          selected &&
            "data-[style-hooks~='lin-row']:bg-[color-mix(in_srgb,var(--accent-soft)_55%,transparent)] data-[style-hooks~='lin-cycle-row']:bg-panel data-[style-hooks~='lin-cycle-row']:border-[color-mix(in_srgb,#5e6ad2_32%,var(--line))] data-[style-hooks~='lin-cycle-row']:shadow-[0_8px_26px_#1b1b1b0a]",
        ),
      )}
      data-style-hooks={styleHooks(
        cx(
          "lin-cycle-row relative grid grid-cols-[88px_minmax(0,1fr)] mb-[7px] border border-solid border-transparent rounded-[12px] [transition:background-color_.16s_ease,border-color_.16s_ease,box-shadow_.16s_ease] [&:hover]:bg-[color-mix(in_srgb,var(--panel)_65%,transparent)] [&:hover]:border-line shell-phone:block shell-phone:flex-[0_0_min(76vw,270px)] shell-phone:m-0 shell-phone:snap-start shell-phone:bg-panel shell-phone:border-line motion-reduce:[transition:none]",
          selected &&
            "selected data-[style-hooks~='lin-row']:bg-[color-mix(in_srgb,var(--accent-soft)_55%,transparent)] data-[style-hooks~='lin-cycle-row']:bg-panel data-[style-hooks~='lin-cycle-row']:border-[color-mix(in_srgb,#5e6ad2_32%,var(--line))] data-[style-hooks~='lin-cycle-row']:shadow-[0_8px_26px_#1b1b1b0a]",
        ),
      )}
      role="listitem"
    >
      <div
        className={styleClasses(
          cx(
            "relative text-right pt-4 pr-10 pb-0 pl-0 text-[11px] text-muted leading-[1.35] whitespace-pre-line [&_i]:absolute [&_i]:right-4 [&_i]:top-4.5 [&_i]:w-2.5 [&_i]:h-2.5 [&_i]:rounded-[50%] [&_i]:bg-panel [&_i]:border-2 [&_i]:border-solid [&_i]:border-muted [&_i]:not-italic [&_i]:[transition:transform_.16s_ease,border-color_.16s_ease,background-color_.16s_ease] [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&_i]:border-[#5e6ad2] [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&_i]:transform-[scale(1.25)] shell-phone:hidden motion-reduce:[&_i]:[transition:none]",
            state === "current" &&
              "[[data-style-hooks~='lin-cycle-tmark']&_i]:bg-[#5e6ad2] [[data-style-hooks~='lin-cycle-tmark']&_i]:border-[#5e6ad2]",
          ),
        )}
        data-style-hooks={styleHooks(
          cx(
            "lin-cycle-tmark relative text-right pt-[16px] pr-[30px] pb-0 pl-0 text-[11px] text-muted leading-[1.35] whitespace-pre-line [&_i]:absolute [&_i]:right-[22px] [&_i]:top-[18px] [&_i]:w-[10px] [&_i]:h-[10px] [&_i]:rounded-[50%] [&_i]:bg-panel [&_i]:border-2 [&_i]:border-solid [&_i]:border-muted [&_i]:not-italic [&_i]:[transition:transform_.16s_ease,border-color_.16s_ease,background-color_.16s_ease] [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&_i]:border-[#5e6ad2] [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&_i]:transform-[scale(1.25)] shell-phone:hidden motion-reduce:[&_i]:[transition:none]",
            state === "current" &&
              "now [[data-style-hooks~='lin-cycle-tmark']&_i]:bg-[#5e6ad2] [[data-style-hooks~='lin-cycle-tmark']&_i]:border-[#5e6ad2]",
          ),
        )}
      >
        <span>{fmtDate(cycle.startsAt).replace(" ", "\n")}</span>
        <i />
      </div>
      <div
        className={styleClasses(
          "pt-3.25 pr-3 pb-3.25 pl-1 min-w-0 shell-phone:p-3",
        )}
        data-style-hooks={styleHooks(
          "lin-cycle-body pt-[13px] pr-[12px] pb-[13px] pl-[4px] min-w-0 shell-phone:p-[12px]",
        )}
      >
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-bcard']:flex in-data-[style-hooks~='lin-bcard']:items-center in-data-[style-hooks~='lin-bcard']:gap-1.5 in-data-[style-hooks~='lin-bcard']:h-3.75 in-data-[style-hooks~='lin-bcard']:min-h-3.75 in-data-[style-hooks~='lin-bcard']:text-[12px] in-data-[style-hooks~='lin-bcard']:font-[450] in-data-[style-hooks~='lin-bcard']:leading-3.75 in-data-[style-hooks~='lin-bcard']:text-(--card-meta) in-data-[style-hooks~='lin-bcard']:pr-0 in-data-[style-hooks~='lin-cycle-row']:flex in-data-[style-hooks~='lin-cycle-row']:items-center in-data-[style-hooks~='lin-cycle-row']:gap-2.5 in-data-[style-hooks~='lin-cycle-row']:min-w-0 shell-phone:in-data-[style-hooks~='lin-cycle-row']:items-start shell-phone:in-data-[style-hooks~='lin-cycle-row']:flex-col shell-phone:in-data-[style-hooks~='lin-cycle-row']:gap-2",
          )}
          data-style-hooks={styleHooks(
            "top in-data-[style-hooks~='lin-bcard']:flex in-data-[style-hooks~='lin-bcard']:items-center in-data-[style-hooks~='lin-bcard']:gap-[6px] in-data-[style-hooks~='lin-bcard']:h-[15px] in-data-[style-hooks~='lin-bcard']:min-h-[15px] in-data-[style-hooks~='lin-bcard']:text-[12px] in-data-[style-hooks~='lin-bcard']:font-[450] in-data-[style-hooks~='lin-bcard']:leading-[15px] in-data-[style-hooks~='lin-bcard']:text-(--card-meta) in-data-[style-hooks~='lin-bcard']:pr-0 in-data-[style-hooks~='lin-cycle-row']:flex in-data-[style-hooks~='lin-cycle-row']:items-center in-data-[style-hooks~='lin-cycle-row']:gap-[10px] in-data-[style-hooks~='lin-cycle-row']:min-w-0 shell-phone:in-data-[style-hooks~='lin-cycle-row']:items-start shell-phone:in-data-[style-hooks~='lin-cycle-row']:flex-col shell-phone:in-data-[style-hooks~='lin-cycle-row']:gap-[8px]",
          )}
        >
          <button
            type="button"
            className={styleClasses(
              "in-data-[style-hooks~='lin-cycle-row']:[background:none] in-data-[style-hooks~='lin-cycle-row']:border-0 in-data-[style-hooks~='lin-cycle-row']:font-[inherit] in-data-[style-hooks~='lin-cycle-row']:text-[14px] in-data-[style-hooks~='lin-cycle-row']:font-[650] in-data-[style-hooks~='lin-cycle-row']:text-foreground in-data-[style-hooks~='lin-cycle-row']:flex in-data-[style-hooks~='lin-cycle-row']:items-center in-data-[style-hooks~='lin-cycle-row']:gap-2.25 in-data-[style-hooks~='lin-cycle-row']:min-w-0 in-data-[style-hooks~='lin-cycle-row']:flex-1 in-data-[style-hooks~='lin-cycle-row']:py-0.75 in-data-[style-hooks~='lin-cycle-row']:px-1.25 in-data-[style-hooks~='lin-cycle-row']:rounded-md in-data-[style-hooks~='lin-cycle-row']:cursor-pointer in-data-[style-hooks~='lin-cycle-row']:text-left [[data-style-hooks~='lin-cycle-row']_&:hover]:bg-subtle [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-solid [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-2 [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-accent [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-offset-3 [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&]:text-[#5e6ad2] shell-phone:in-data-[style-hooks~='lin-cycle-row']:flex-none shell-phone:in-data-[style-hooks~='lin-cycle-row']:w-full shell-phone:in-data-[style-hooks~='lin-cycle-row']:text-[13px]",
            )}
            data-style-hooks={styleHooks(
              "cname in-data-[style-hooks~='lin-cycle-row']:[background:none] in-data-[style-hooks~='lin-cycle-row']:border-0 in-data-[style-hooks~='lin-cycle-row']:font-[inherit] in-data-[style-hooks~='lin-cycle-row']:text-[14px] in-data-[style-hooks~='lin-cycle-row']:font-[650] in-data-[style-hooks~='lin-cycle-row']:text-foreground in-data-[style-hooks~='lin-cycle-row']:flex in-data-[style-hooks~='lin-cycle-row']:items-center in-data-[style-hooks~='lin-cycle-row']:gap-[9px] in-data-[style-hooks~='lin-cycle-row']:min-w-0 in-data-[style-hooks~='lin-cycle-row']:flex-1 in-data-[style-hooks~='lin-cycle-row']:py-[3px] in-data-[style-hooks~='lin-cycle-row']:px-[5px] in-data-[style-hooks~='lin-cycle-row']:rounded-[6px] in-data-[style-hooks~='lin-cycle-row']:cursor-pointer in-data-[style-hooks~='lin-cycle-row']:text-left [[data-style-hooks~='lin-cycle-row']_&:hover]:bg-subtle [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-solid [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-2 [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-accent [[data-style-hooks~='lin-cycle-row']_&:focus-visible]:outline-offset-3 [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&]:text-[#5e6ad2] shell-phone:in-data-[style-hooks~='lin-cycle-row']:flex-none shell-phone:in-data-[style-hooks~='lin-cycle-row']:w-full shell-phone:in-data-[style-hooks~='lin-cycle-row']:text-[13px]",
            )}
            title={name}
            ref={(node) => {
              if (node) buttons.current.set(cycle.id, node);
              else buttons.current.delete(cycle.id);
            }}
            aria-pressed={selected}
            aria-controls={controls}
            onClick={() => onSelect(cycle)}
            onKeyDown={(e) => onKeyDown(e, cycle)}
          >
            <CycleStateIcon state={state} />
            <span
              className={styleClasses(
                "min-w-0 overflow-hidden text-ellipsis whitespace-nowrap",
              )}
              data-style-hooks={styleHooks(
                "lin-cycle-name-text min-w-0 overflow-hidden text-ellipsis whitespace-nowrap",
              )}
            >
              {name}
            </span>
            <ChevronRight
              className={styleClasses(
                "flex-none text-muted opacity-0 transform-[translateX(-3px)] [transition:opacity_.16s_ease,transform_.16s_ease] [[data-style-hooks~='lin-cycle-row']:hover_&]:opacity-100 [[data-style-hooks~='lin-cycle-row']:hover_&]:transform-[translateX(0)] [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&]:opacity-100 [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&]:transform-[translateX(0)] [[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='cname']:focus-visible_&]:opacity-100 [[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='cname']:focus-visible_&]:transform-[translateX(0)] shell-phone:opacity-100 shell-phone:transform-none motion-reduce:[transition:none]",
              )}
              data-style-hooks={styleHooks(
                "lin-cycle-select-chevron flex-none text-muted opacity-0 transform-[translateX(-3px)] [transition:opacity_.16s_ease,transform_.16s_ease] [[data-style-hooks~='lin-cycle-row']:hover_&]:opacity-100 [[data-style-hooks~='lin-cycle-row']:hover_&]:transform-[translateX(0)] [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&]:opacity-100 [[data-style-hooks~='lin-cycle-row'][data-style-hooks~='selected']_&]:transform-[translateX(0)] [[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='cname']:focus-visible_&]:opacity-100 [[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='cname']:focus-visible_&]:transform-[translateX(0)] shell-phone:opacity-100 shell-phone:transform-none motion-reduce:[transition:none]",
              )}
              size={14}
              aria-hidden="true"
            />
          </button>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-cycle-row']:ml-auto in-data-[style-hooks~='lin-cycle-row']:flex in-data-[style-hooks~='lin-cycle-row']:items-center in-data-[style-hooks~='lin-cycle-row']:justify-end in-data-[style-hooks~='lin-cycle-row']:gap-3 in-data-[style-hooks~='lin-cycle-row']:text-[12px] in-data-[style-hooks~='lin-cycle-row']:text-muted in-data-[style-hooks~='lin-cycle-row']:flex-none [[data-style-hooks~='lin-cycle-row']_&_b]:text-foreground [[data-style-hooks~='lin-cycle-row']_&_b]:font-semibold shell-phone:in-data-[style-hooks~='lin-cycle-row']:ml-0 shell-phone:in-data-[style-hooks~='lin-cycle-row']:justify-start shell-phone:in-data-[style-hooks~='lin-cycle-row']:gap-2.25 shell-phone:in-data-[style-hooks~='lin-cycle-row']:flex-wrap",
            )}
            data-style-hooks={styleHooks(
              "stats in-data-[style-hooks~='lin-cycle-row']:ml-auto in-data-[style-hooks~='lin-cycle-row']:flex in-data-[style-hooks~='lin-cycle-row']:items-center in-data-[style-hooks~='lin-cycle-row']:justify-end in-data-[style-hooks~='lin-cycle-row']:gap-[12px] in-data-[style-hooks~='lin-cycle-row']:text-[12px] in-data-[style-hooks~='lin-cycle-row']:text-muted in-data-[style-hooks~='lin-cycle-row']:flex-none [[data-style-hooks~='lin-cycle-row']_&_b]:text-foreground [[data-style-hooks~='lin-cycle-row']_&_b]:font-semibold shell-phone:in-data-[style-hooks~='lin-cycle-row']:ml-0 shell-phone:in-data-[style-hooks~='lin-cycle-row']:justify-start shell-phone:in-data-[style-hooks~='lin-cycle-row']:gap-[9px] shell-phone:in-data-[style-hooks~='lin-cycle-row']:flex-wrap",
            )}
          >
            <span
              className={styleClasses(
                cx(
                  "inline-flex items-center text-[12px] font-medium bg-subtle rounded-md py-0.75 px-2.25 text-muted whitespace-nowrap",
                  state === "current" &&
                    "in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
                ),
              )}
              data-style-hooks={styleHooks(
                cx(
                  "lin-badge inline-flex items-center text-[12px] font-medium bg-subtle rounded-[6px] py-[3px] px-[9px] text-muted whitespace-nowrap",
                  state === "current" &&
                    "current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
                ),
              )}
            >
              {badge}
            </span>
            <span
              className={styleClasses(
                "inline-flex items-center gap-1.5 whitespace-nowrap",
              )}
              data-style-hooks={styleHooks(
                "lin-cap inline-flex items-center gap-[6px] whitespace-nowrap",
              )}
            >
              <Ring frac={ofScope / 100} />{" "}
              <b>{stats.scope ? `${ofScope}%` : "—"}</b>
              <span
                className={styleClasses(
                  "[@media_(max-width:1100px)]:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:hidden [@media_(max-width:900px)]:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:inline shell-phone:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:inline",
                )}
                data-style-hooks={styleHooks(
                  "lin-cycle-stat-label [@media_(max-width:1100px)]:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:hidden [@media_(max-width:900px)]:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:inline shell-phone:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:inline",
                )}
              >
                {" "}
                of scope
              </span>
            </span>
            {stats.canceled > 0 && (
              <span>
                <b>{stats.canceled}</b> canceled · {dropped}%
              </span>
            )}
            <span>
              <b>{stats.scope}</b> scope
            </span>
            <span
              className={styleClasses(
                "in-data-[style-hooks~='lin-cycle-row']:whitespace-nowrap shell-phone:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:basis-full shell-phone:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:whitespace-normal",
              )}
              data-style-hooks={styleHooks(
                "range in-data-[style-hooks~='lin-cycle-row']:whitespace-nowrap shell-phone:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:basis-full shell-phone:[[data-style-hooks~='lin-cycle-row']_[data-style-hooks~='stats']_&]:whitespace-normal",
              )}
            >
              {fmtRange(cycle.startsAt, cycle.endsAt)}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
});

function orderCycles(cycles: Cycle[]): { cycle: Cycle; state: CycleState }[] {
  const now = Date.now();
  const withState = cycles.map((c) => ({
    cycle: c,
    state: cycleState(c, now),
  }));
  withState.sort((a, b) =>
    (b.cycle.startsAt || "").localeCompare(a.cycle.startsAt || ""),
  );
  // mark only the nearest future cycle as upcoming; the rest are planned
  let seenUpcoming = false;
  for (const w of [...withState].reverse()) {
    if (w.state === "upcoming") {
      if (seenUpcoming) w.state = "planned";
      seenUpcoming = true;
    }
  }
  return withState;
}

export function CycleOverviewRows({
  cycles,
  issuesByCycle,
  onCycle,
  emptyHint,
}: {
  cycles: Cycle[];
  issuesByCycle: Map<string, LinIssue[]>;
  onCycle: (c: Cycle) => void;
  emptyHint?: string;
}) {
  const ordered = useMemo(() => orderCycles(cycles), [cycles]);
  const orderedIds = useMemo(() => ordered.map((o) => o.cycle.id), [ordered]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected =
    ordered.find((o) => o.cycle.id === selectedId) ||
    ordered.find((o) => o.state === "current") ||
    ordered[0];
  const selectedCycleId = selected?.cycle.id;
  const previewId = `${useId()}-cycle-preview`;
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const select = useCallback((cycle: Cycle) => setSelectedId(cycle.id), []);
  useEffect(() => {
    if (!selectedCycleId) return;
    const button = buttons.current.get(selectedCycleId);
    const scroller = button?.closest("[data-cycle-timeline]");
    if (!button || !(scroller instanceof HTMLElement)) return;
    const reveal = () => {
      // Modify only horizontal overflow. scrollIntoView would also move the
      // workspace's vertical scroller when multiple teams have cycle sections.
      if (scroller.scrollWidth <= scroller.clientWidth) return;
      const card = button.getBoundingClientRect();
      const lane = scroller.getBoundingClientRect();
      if (card.left < lane.left) scroller.scrollLeft -= lane.left - card.left;
      else if (card.right > lane.right)
        scroller.scrollLeft += card.right - lane.right;
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [selectedCycleId]);
  const navigateSelection = useCallback(
    (e: React.KeyboardEvent, cycle: Cycle) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const target = cycleKeyTarget(orderedIds, cycle.id, e.key);
      if (!target) return;
      e.preventDefault();
      setSelectedId(target);
      buttons.current.get(target)?.focus();
    },
    [orderedIds],
  );
  if (!ordered.length) {
    return (
      <div
        className={styleClasses(
          "flex flex-col items-center py-15 px-5 text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-3 [&_h2]:mx-0 [&_h2]:mb-1.5",
        )}
        data-style-hooks={styleHooks(
          "lin-empty py-[60px] px-[20px] text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-[12px] [&_h2]:mx-0 [&_h2]:mb-[6px]",
        )}
      >
        <CalendarDays size={30} strokeWidth={1.4} />
        <h2>No cycles yet</h2>
        <p>
          {emptyHint ||
            "Enable automatic cycles in team cycle settings to start planning work in iterations."}
        </p>
      </div>
    );
  }
  return (
    <div
      className="grid grid-cols-4 items-start gap-4 min-w-0 p-4 shell-phone:grid-cols-1"
      data-style-hooks={styleHooks(
        "lin-cycle-workbench grid grid-cols-[minmax(400px,1fr)_minmax(330px,390px)] gap-[20px] [align-items:start] min-w-0 pt-[14px] px-[28px] pb-[60px] [@media_(max-width:1100px)]:grid-cols-[minmax(0,1fr)_minmax(310px,350px)] [@media_(max-width:1100px)]:gap-[14px] [@media_(max-width:1100px)]:pl-[20px] [@media_(max-width:1100px)]:pr-[20px] [@media_(max-width:900px)]:grid-cols-[minmax(0,1fr)] shell-phone:gap-[12px] shell-phone:pt-[10px] shell-phone:px-[12px] shell-phone:pb-[40px]",
      )}
    >
      <div
        className="col-span-3 min-w-0 shell-phone:col-span-1"
        data-style-hooks={styleHooks("lin-cycle-browser min-w-0")}
      >
        <div
          className={styleClasses(
            "flex items-center justify-between gap-3 pt-0 pr-0 pb-3 pl-22 text-muted text-xs font-medium [&_span:last-child]:text-xs [&_span:last-child]:font-normal shell-phone:pt-0 shell-phone:px-1 shell-phone:pb-2.25 shell-phone:[&_span:last-child]:hidden",
          )}
          data-style-hooks={styleHooks(
            "lin-cycle-nav-head flex items-center justify-between gap-[12px] pt-0 pr-0 pb-[12px] pl-[88px] text-muted text-xs font-medium shell-phone:pt-0 shell-phone:px-[4px] shell-phone:pb-[9px] shell-phone:[&_span:last-child]:hidden",
          )}
        >
          <span>Iteration timeline</span>
          <span>Use arrow keys to explore</span>
        </div>
        <div
          data-cycle-timeline
          className={styleClasses(
            "relative min-w-0 shell-phone:flex shell-phone:gap-2.25 shell-phone:overflow-x-auto shell-phone:pt-0.75 shell-phone:px-0.75 shell-phone:pb-3 shell-phone:[scroll-snap-type:x_proximity] shell-phone:scrollbar-thin motion-reduce:scroll-auto",
          )}
          data-style-hooks={styleHooks(
            "lin-cycles-tl relative min-w-0 shell-phone:flex shell-phone:gap-[9px] shell-phone:overflow-x-auto shell-phone:pt-[3px] shell-phone:px-[3px] shell-phone:pb-[12px] shell-phone:[scroll-snap-type:x_proximity] shell-phone:scrollbar-thin motion-reduce:scroll-auto",
          )}
          role="list"
          aria-label="Cycle selector"
        >
          <div
            className={styleClasses(
              "absolute top-0 bottom-0 left-16.5 w-0.5 bg-line shell-phone:hidden",
            )}
            data-style-hooks={styleHooks(
              "lin-cycles-spine absolute top-0 bottom-0 left-[61px] w-[2px] bg-line shell-phone:hidden",
            )}
            aria-hidden="true"
          />
          {ordered.map(({ cycle, state }) => {
            const issues = issuesByCycle.get(cycle.id) || EMPTY;
            return (
              <CycleOverviewRow
                key={cycle.id}
                cycle={cycle}
                state={state}
                stats={cachedStats(issues)}
                selected={selected?.cycle.id === cycle.id}
                controls={previewId}
                onSelect={select}
                onKeyDown={navigateSelection}
                buttons={buttons}
              />
            );
          })}
        </div>
      </div>
      {selected && (
        <CyclePreview
          id={previewId}
          cycle={selected.cycle}
          state={selected.state}
          issues={issuesByCycle.get(selected.cycle.id) || EMPTY}
          onOpen={onCycle}
        />
      )}
    </div>
  );
}

function CyclePreview({
  id,
  cycle,
  state,
  issues,
  onOpen,
}: {
  id: string;
  cycle: Cycle;
  state: CycleState;
  issues: LinIssue[];
  onOpen: (cycle: Cycle) => void;
}) {
  const name = cycle.name || `Cycle ${cycle.number ?? ""}`;
  const stats = cachedStats(issues);
  const ofScope = asPct(scopeCompletion(stats));
  const badge =
    state === "current"
      ? "Current"
      : state === "upcoming"
        ? "Upcoming"
        : state === "completed"
          ? "Completed"
          : "Planned";
  const shown = issues.slice(0, 4);
  const headingId = `${id}-heading`;
  return (
    <aside
      className="sticky top-4 col-start-4 min-w-0 w-full overflow-hidden border border-solid border-line rounded-xl bg-panel shell-phone:static shell-phone:col-start-1"
      data-style-hooks={styleHooks(
        "lin-cycle-preview sticky top-[14px] min-w-0 overflow-hidden border border-solid border-line rounded-[15px] bg-panel shadow-[0_16px_45px_#1b1b1b0b] [@media_(max-width:900px)]:static shell-phone:rounded-[13px]",
      )}
      id={id}
      aria-labelledby={headingId}
    >
      <div
        className={styleClasses(
          "min-w-0 animate-[lin-cycle-preview-in_.22s_cubic-bezier(.2,.8,.2,1)] motion-reduce:animate-none",
        )}
        data-style-hooks={styleHooks(
          "lin-cycle-preview-content min-w-0 animate-[lin-cycle-preview-in_.22s_cubic-bezier(.2,.8,.2,1)] motion-reduce:animate-none",
        )}
        key={cycle.id}
      >
        <header
          className={styleClasses(
            "relative pt-5 px-5 pb-4.5 border-b border-b-line [background:radial-gradient(circle_at_95%_0,color-mix(in_srgb,#5e6ad2_9%,transparent),transparent_58%)] [&_h2]:m-0 [&_h2]:text-foreground [&_h2]:text-[21px] [&_h2]:font-[680] [&_h2]:tracking-[-.035em] [&_h2]:leading-[1.2] [&_h2]:wrap-anywhere [&_p]:mt-1.75 [&_p]:mx-0 [&_p]:mb-3.75 [&_p]:text-muted [&_p]:text-[12px] shell-phone:pt-4.5 shell-phone:px-4 shell-phone:pb-4 shell-phone:[&_h2]:text-[20px]",
          )}
          data-style-hooks={styleHooks(
            "lin-cycle-preview-head relative pt-[20px] px-[20px] pb-[18px] border-b border-b-line [background:radial-gradient(circle_at_95%_0,color-mix(in_srgb,#5e6ad2_9%,transparent),transparent_58%)] [&_h2]:m-0 [&_h2]:text-foreground [&_h2]:text-[21px] [&_h2]:font-[680] [&_h2]:tracking-[-.035em] [&_h2]:leading-[1.2] [&_h2]:wrap-anywhere [&_p]:mt-[7px] [&_p]:mx-0 [&_p]:mb-[15px] [&_p]:text-muted [&_p]:text-[12px] shell-phone:pt-[18px] shell-phone:px-[16px] shell-phone:pb-[16px] shell-phone:[&_h2]:text-[20px]",
          )}
        >
          <div
            className={styleClasses(
              "flex items-center gap-1.75 mb-2.25 text-muted text-xs font-medium",
            )}
            data-style-hooks={styleHooks(
              "lin-cycle-preview-kicker flex items-center gap-[7px] mb-[9px] text-muted text-xs font-medium",
            )}
          >
            <span
              className={styleClasses(
                cx(
                  "w-1.5 h-1.5 flex-none rounded-[50%] bg-muted",
                  state === "current" &&
                    "in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
                ),
              )}
              data-style-hooks={styleHooks(
                cx(
                  "lin-cycle-live-dot w-[6px] h-[6px] flex-none rounded-[50%] bg-muted",
                  state === "current" &&
                    "current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
                ),
              )}
              aria-hidden="true"
            />
            {cycle.number != null ? `Cycle ${cycle.number}` : "Cycle"} · {badge}
          </div>
          <h2 id={headingId}>{name}</h2>
          <p>{fmtRange(cycle.startsAt, cycle.endsAt)}</p>
          <button
            type="button"
            className={styleClasses(
              "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333] justify-center w-full",
            )}
            data-style-hooks={styleHooks(
              "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333] lin-cycle-open justify-center w-full",
            )}
            onClick={() => onOpen(cycle)}
          >
            Open cycle <ArrowUpRight size={14} aria-hidden="true" />
          </button>
        </header>

        <div
          className={styleClasses(
            "flex items-center gap-3.25 pt-4 px-5 pb-3.5 [&_strong]:block [&_strong]:text-[13px] [&_strong]:font-[650] [&_span]:block [&_span]:mt-0.75 [&_span]:text-muted [&_span]:text-[11px] [&_span]:leading-[1.4] shell-phone:pt-3.5 shell-phone:px-4 shell-phone:pb-3",
          )}
          data-style-hooks={styleHooks(
            "lin-cycle-preview-progress flex items-center gap-[13px] pt-[16px] px-[20px] pb-[14px] [&_strong]:block [&_strong]:text-[13px] [&_strong]:font-[650] [&_span]:block [&_span]:mt-[3px] [&_span]:text-muted [&_span]:text-[11px] [&_span]:leading-[1.4] shell-phone:pt-[14px] shell-phone:px-[16px] shell-phone:pb-[12px]",
          )}
        >
          <div
            className={styleClasses(
              "grid place-items-center w-12 h-12 flex-none rounded-[50%] bg-[color-mix(in_srgb,#5e6ad2_7%,var(--panel))]",
            )}
            data-style-hooks={styleHooks(
              "lin-cycle-progress-ring grid place-items-center w-[48px] h-[48px] flex-none rounded-[50%] bg-[color-mix(in_srgb,#5e6ad2_7%,var(--panel))]",
            )}
          >
            <Ring frac={(ofScope ?? 0) / 100} size={44} />
          </div>
          <div>
            <strong>
              {ofScope === null ? "No scope yet" : `${ofScope}% delivered`}
            </strong>
            <span>
              {ofScope === null
                ? "Tasks added to this cycle will establish its scope."
                : `${stats.completed} of ${stats.scope} tasks completed`}
            </span>
          </div>
        </div>
        <dl
          className={styleClasses(
            "grid grid-cols-4 my-0 mx-5 border border-solid border-line rounded-[10px] overflow-hidden [&_div]:min-w-0 [&_div]:py-2.5 [&_div]:px-2.75 [&_div]:border-r [&_div]:border-r-line [&_div:last-child]:border-r-0 [&_dt]:text-muted [&_dt]:text-[10px] [&_dd]:mt-0.75 [&_dd]:mx-0 [&_dd]:mb-0 [&_dd]:text-[18px] [&_dd]:font-[650] [&_dd]:tracking-tight shell-phone:my-0 shell-phone:mx-4 shell-phone:[&_div]:py-2.25 shell-phone:[&_div]:px-2 [@media_(max-width:360px)]:grid-cols-2 [@media_(max-width:360px)]:[&_div:nth-child(2)]:border-r-0 [@media_(max-width:360px)]:[&_div:nth-child(-n+2)]:border-b [@media_(max-width:360px)]:[&_div:nth-child(-n+2)]:border-b-line",
          )}
          data-style-hooks={styleHooks(
            "lin-cycle-preview-stats grid grid-cols-4 my-0 mx-[20px] border border-solid border-line rounded-[10px] overflow-hidden [&_div]:min-w-0 [&_div]:py-[10px] [&_div]:px-[11px] [&_div]:border-r [&_div]:border-r-line [&_div:last-child]:border-r-0 [&_dt]:text-muted [&_dt]:text-[10px] [&_dd]:mt-[3px] [&_dd]:mx-0 [&_dd]:mb-0 [&_dd]:text-[18px] [&_dd]:font-[650] [&_dd]:tracking-tight shell-phone:my-0 shell-phone:mx-[16px] shell-phone:[&_div]:py-[9px] shell-phone:[&_div]:px-[8px] [@media_(max-width:360px)]:grid-cols-2 [@media_(max-width:360px)]:[&_div:nth-child(2)]:border-r-0 [@media_(max-width:360px)]:[&_div:nth-child(-n+2)]:border-b [@media_(max-width:360px)]:[&_div:nth-child(-n+2)]:border-b-line",
          )}
        >
          <div>
            <dt>Scope</dt>
            <dd>{stats.scope}</dd>
          </div>
          <div>
            <dt>Open</dt>
            <dd>{stats.open}</dd>
          </div>
          <div>
            <dt>Delivered</dt>
            <dd>{stats.completed}</dd>
          </div>
          <div>
            <dt>Canceled</dt>
            <dd>{stats.canceled}</dd>
          </div>
        </dl>

        <section
          className={styleClasses(
            "pt-4.25 px-5 pb-3 [&_header]:flex [&_header]:items-center [&_header]:gap-2.5 [&_header]:mb-2 [&_h3]:m-0 [&_h3]:text-[12px] [&_h3]:font-[650] [&_header_span]:ml-auto [&_header_span]:text-muted [&_header_span]:text-[10px] [&_header_span]:whitespace-nowrap shell-phone:pl-4 shell-phone:pr-4",
          )}
          data-style-hooks={styleHooks(
            "lin-cycle-preview-chart pt-[17px] px-[20px] pb-[12px] [&_header]:flex [&_header]:items-center [&_header]:gap-[10px] [&_header]:mb-[8px] [&_h3]:m-0 [&_h3]:text-[12px] [&_h3]:font-[650] [&_header_span]:ml-auto [&_header_span]:text-muted [&_header_span]:text-[10px] [&_header_span]:whitespace-nowrap shell-phone:pl-[16px] shell-phone:pr-[16px]",
          )}
          aria-label="Cycle burndown"
        >
          <header>
            <h3>Burndown</h3>
            <span>{stats.open} remaining</span>
          </header>
          {issues.length > 0 && state !== "upcoming" && state !== "planned" ? (
            <BurndownChart
              cycle={cycle}
              stats={stats}
              issues={issues}
              height={168}
            />
          ) : (
            <div
              className={styleClasses(
                "p-3.5 border border-dashed border-line rounded-[9px] text-muted text-[11px] leading-normal text-center",
              )}
              data-style-hooks={styleHooks(
                "lin-cycle-preview-empty p-[14px] border border-dashed border-line rounded-[9px] text-muted text-[11px] leading-normal text-center",
              )}
            >
              {issues.length
                ? `Burndown starts when the cycle begins on ${fmtDate(cycle.startsAt)}.`
                : "No task activity to chart yet."}
            </div>
          )}
        </section>

        <section
          className={styleClasses(
            "pt-4.25 px-5 pb-3 border-t border-t-line pb-4 [&_header]:flex [&_header]:items-center [&_header]:gap-2.5 [&_header]:mb-2 [&_h3]:m-0 [&_h3]:text-[12px] [&_h3]:font-[650] [&_header_span]:ml-auto [&_header_span]:text-muted [&_header_span]:text-[10px] [&_header_span]:whitespace-nowrap [&_ul]:grid [&_ul]:gap-0.5 [&_ul]:m-0 [&_ul]:p-0 [&_ul]:list-none [&_li]:flex [&_li]:items-center [&_li]:gap-1.75 [&_li]:min-w-0 [&_li]:py-1.5 [&_li]:px-0 [&_li]:text-[11px] shell-phone:pl-4 shell-phone:pr-4",
          )}
          data-style-hooks={styleHooks(
            "lin-cycle-preview-work pt-[17px] px-[20px] pb-[12px] border-t border-t-line pb-[16px] [&_header]:flex [&_header]:items-center [&_header]:gap-[10px] [&_header]:mb-[8px] [&_h3]:m-0 [&_h3]:text-[12px] [&_h3]:font-[650] [&_header_span]:ml-auto [&_header_span]:text-muted [&_header_span]:text-[10px] [&_header_span]:whitespace-nowrap [&_ul]:grid [&_ul]:gap-[2px] [&_ul]:m-0 [&_ul]:p-0 [&_ul]:list-none [&_li]:flex [&_li]:items-center [&_li]:gap-[7px] [&_li]:min-w-0 [&_li]:py-[6px] [&_li]:px-0 [&_li]:text-[11px] shell-phone:pl-[16px] shell-phone:pr-[16px]",
          )}
          aria-label="Cycle work snapshot"
        >
          <header>
            <h3>Work snapshot</h3>
            <span>
              {issues.length} {issues.length === 1 ? "task" : "tasks"}
            </span>
          </header>
          {shown.length ? (
            <ul>
              {shown.map((issue) => (
                <li key={issue.id}>
                  <GraphStatusIcon state={issue.state} size={14} />
                  <span
                    className={styleClasses(
                      "flex-none text-muted text-[10px] shell-phone:hidden",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-cycle-issue-id flex-none text-muted text-[10px] shell-phone:hidden",
                    )}
                  >
                    {issue.identifier}
                  </span>
                  <span
                    className={styleClasses(
                      "min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-cycle-issue-title min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap",
                    )}
                    title={issue.title}
                  >
                    {issue.title}
                  </span>
                  <Avatar
                    name={issue.assignee?.name}
                    seed={issue.assignee?.avatarSeed}
                    size={20}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <div
              className={styleClasses(
                "p-3.5 border border-dashed border-line rounded-[9px] text-muted text-[11px] leading-normal text-center",
              )}
              data-style-hooks={styleHooks(
                "lin-cycle-preview-empty p-[14px] border border-dashed border-line rounded-[9px] text-muted text-[11px] leading-normal text-center",
              )}
            >
              This cycle is ready for its first task.
            </div>
          )}
          {issues.length > shown.length && (
            <p
              className={styleClasses("mt-2 mx-0 mb-0 text-muted text-[10px]")}
              data-style-hooks={styleHooks(
                "lin-cycle-preview-more mt-[8px] mx-0 mb-0 text-muted text-[10px]",
              )}
            >
              + {issues.length - shown.length} more in the full cycle
            </p>
          )}
        </section>
      </div>
    </aside>
  );
}

/* ---------------- team cycles overview page ---------------- */

const EMPTY_CYCLES: Cycle[] = [];

export function GraphTeamCycles({
  team,
  teams,
  onCycle,
  onSettings,
  onNavigate,
  workspaceName,
  viewer,
}: {
  team?: Team;
  teams: Team[];
  onCycle: (c: Cycle) => void;
  onSettings: () => void;
  revision?: number;
  onNavigate: (view: string, path?: string) => void;
  workspaceName: string;
  viewer: Viewer;
}) {
  // Rendered from the local store: no per-view fetch, live through deltas.
  const all = useCycles();
  const issuesByCycle = useIssuesByCycle();
  const teamId = team?.id;
  const cycles = useMemo(
    () =>
      all && teamId ? all.filter((c) => c.team?.id === teamId) : EMPTY_CYCLES,
    [all, teamId],
  );
  const loading = all === null;
  const error = "";
  const [menuOpen, setMenuOpen] = useState(false);

  useTeamCycleUpkeep(teamId);
  useCycleHistory(cycles, 6);

  if (!team)
    return (
      <section
        className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
        data-style-hooks={styleHooks(
          "lin-page min-w-0 flex flex-col h-full bg-panel",
        )}
      >
        <p
          className={styleClasses(
            "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          data-style-hooks={styleHooks(
            "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          role="alert"
        >
          Team not found in this workspace.
        </p>
      </section>
    );

  return (
    <section
      className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
      data-style-hooks={styleHooks(
        "lin-page min-w-0 flex flex-col h-full bg-panel",
      )}
      aria-label={`${team.name} cycles`}
    >
      <header
        className={styleClasses(
          "flex items-center gap-2 min-h-12.25 py-0 px-5 border-b border-b-line flex-none",
        )}
        data-style-hooks={styleHooks(
          "lin-head flex items-center gap-[8px] min-h-[49px] py-0 px-[20px] border-b border-b-line flex-none",
        )}
      >
        <nav
          className={styleClasses(
            "flex items-center gap-1.75 text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
          )}
          data-style-hooks={styleHooks(
            "lin-crumb flex items-center gap-[7px] text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-[6px] [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
          )}
          aria-label="Breadcrumb"
        >
          <button
            type="button"
            className={styleClasses(
              "[[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-1.5 [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
            )}
            data-style-hooks={styleHooks(
              "crumb-link [[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-[6px] [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
            )}
            onClick={() =>
              onNavigate(
                "Team Home",
                `/workspace/team/${team.key.toLowerCase()}/home`,
              )
            }
          >
            {workspaceName}
          </button>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
            data-style-hooks={styleHooks(
              "sep in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
          >
            ›
          </span>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
            data-style-hooks={styleHooks(
              "current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
          >
            Cycles
          </span>
        </nav>
        <div
          className={styleClasses(
            "flex items-center gap-0.5 ml-auto flex-none",
          )}
          data-style-hooks={styleHooks(
            "lin-head-actions flex items-center gap-[2px] ml-auto flex-none",
          )}
        >
          <FavStar
            kind="cycles"
            id={`team-cycles-${team.id}`}
            label={`${team.name} cycles`}
          />
          <span style={{ position: "relative" }}>
            <IconBtn
              label="Cycle options"
              title="More actions"
              onClick={() => setMenuOpen((v) => !v)}
            >
              <span style={{ fontWeight: 700 }}>···</span>
            </IconBtn>
            {menuOpen && (
              <LinMenu
                onClose={() => setMenuOpen(false)}
                className={styleClasses(
                  "absolute top-[calc(100%+4px)] left-0 z-130 min-w-57.5 max-h-80 overflow-y-auto",
                )}
                data-style-hooks={styleHooks(
                  "lin-dd-panel absolute top-[calc(100%+4px)] left-0 z-130 min-w-[230px] max-h-[320px] overflow-y-auto",
                )}
                items={[
                  {
                    key: "settings",
                    label: "Cycle settings",
                    icon: <Settings2 size={14} />,
                    onSelect: onSettings,
                  },
                ]}
              />
            )}
          </span>
        </div>
      </header>
      <div
        className={styleClasses(
          "flex items-center gap-2 pt-3.5 px-7 pb-1 [&_h1]:text-[15px] [&_h1]:font-[650] [&_h1]:m-0 [&_h1]:tracking-[-.01em] [&_h1]:flex [&_h1]:items-center [&_h1]:gap-1.75",
        )}
        data-style-hooks={styleHooks(
          "lin-cycles-head flex items-center gap-[8px] pt-[14px] px-[28px] pb-[4px] [&_h1]:text-[15px] [&_h1]:font-[650] [&_h1]:m-0 [&_h1]:tracking-[-.01em] [&_h1]:flex [&_h1]:items-center [&_h1]:gap-[7px]",
        )}
      >
        <h1>Cycles</h1>
      </div>
      <div
        className={styleClasses("flex flex-1 min-h-0")}
        data-style-hooks={styleHooks("lin-cycles-wrap flex flex-1 min-h-0")}
      >
        <div
          className={styleClasses("flex-1 min-h-0 overflow-y-auto")}
          data-style-hooks={styleHooks(
            "lin-scroll flex-1 min-h-0 overflow-y-auto",
          )}
          style={{ flex: 1 }}
        >
          {loading && (
            <p
              className={styleClasses("p-10 text-center text-muted")}
              data-style-hooks={styleHooks(
                "lin-loading p-[40px] text-center text-muted",
              )}
              role="status"
            >
              Loading cycles…
            </p>
          )}
          {error && (
            <p
              className={styleClasses(
                "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
              )}
              data-style-hooks={styleHooks(
                "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
              )}
              role="alert"
            >
              {error}
            </p>
          )}
          {!loading && !error && (
            <CycleOverviewRows
              cycles={cycles}
              issuesByCycle={issuesByCycle}
              onCycle={onCycle}
              emptyHint={`${team.name} has no cycles yet. Enable automatic cycles in cycle settings.`}
            />
          )}
          {/* Shown with zero cycles too: settings are where cycles get enabled. */}
          {!loading && !error && (
            <div style={{ padding: "8px 28px" }}>
              <button
                type="button"
                className={styleClasses(
                  "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                )}
                data-style-hooks={styleHooks(
                  "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                )}
                onClick={onSettings}
              >
                Cycle settings
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ---------------- workspace cycles (route fix) ---------------- */

export function GraphWorkspaceCycles({
  teams,
  onCycle,
  onSettings,
  onNavigate,
  workspaceName,
  viewer,
}: {
  teams: Team[];
  onCycle: (c: Cycle) => void;
  onSettings: (team: Team) => void;
  revision?: number;
  onNavigate: (view: string, path?: string) => void;
  workspaceName: string;
  viewer: Viewer;
}) {
  const all = useCycles();
  const cycles = all || EMPTY_CYCLES;
  const issuesByCycle = useIssuesByCycle();
  const loading = all === null;
  const error = "";
  useCycleHistory(cycles, 12);
  const byTeam = useMemo(() => {
    const m = new Map<string, { team: Team; cycles: Cycle[] }>();
    for (const t of teams) m.set(t.id, { team: t, cycles: [] });
    for (const c of cycles) {
      const tid = c.team?.id;
      if (tid && m.has(tid)) m.get(tid)!.cycles.push(c);
    }
    return [...m.values()].filter((g) => g.cycles.length);
  }, [teams, cycles]);
  return (
    <section
      className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
      data-style-hooks={styleHooks(
        "lin-page min-w-0 flex flex-col h-full bg-panel",
      )}
      aria-label="Workspace cycles"
    >
      <header
        className={styleClasses(
          "flex items-center gap-2 min-h-12.25 py-0 px-5 border-b border-b-line flex-none",
        )}
        data-style-hooks={styleHooks(
          "lin-head flex items-center gap-[8px] min-h-[49px] py-0 px-[20px] border-b border-b-line flex-none",
        )}
      >
        <nav
          className={styleClasses(
            "flex items-center gap-1.75 text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
          )}
          data-style-hooks={styleHooks(
            "lin-crumb flex items-center gap-[7px] text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-[6px] [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
          )}
          aria-label="Breadcrumb"
        >
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
            data-style-hooks={styleHooks(
              "current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
          >
            {workspaceName}
          </span>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
            data-style-hooks={styleHooks(
              "sep in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
          >
            ›
          </span>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
            data-style-hooks={styleHooks(
              "current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
          >
            Cycles
          </span>
        </nav>
      </header>
      <div
        className={styleClasses(
          "flex items-center gap-2 pt-3.5 px-7 pb-1 [&_h1]:text-[15px] [&_h1]:font-[650] [&_h1]:m-0 [&_h1]:tracking-[-.01em] [&_h1]:flex [&_h1]:items-center [&_h1]:gap-1.75",
        )}
        data-style-hooks={styleHooks(
          "lin-cycles-head flex items-center gap-[8px] pt-[14px] px-[28px] pb-[4px] [&_h1]:text-[15px] [&_h1]:font-[650] [&_h1]:m-0 [&_h1]:tracking-[-.01em] [&_h1]:flex [&_h1]:items-center [&_h1]:gap-[7px]",
        )}
      >
        <h1>Cycles</h1>
      </div>
      <div
        className={styleClasses("flex-1 min-h-0 overflow-y-auto")}
        data-style-hooks={styleHooks(
          "lin-scroll flex-1 min-h-0 overflow-y-auto",
        )}
      >
        {loading && (
          <p
            className={styleClasses("p-10 text-center text-muted")}
            data-style-hooks={styleHooks(
              "lin-loading p-[40px] text-center text-muted",
            )}
            role="status"
          >
            Loading cycles…
          </p>
        )}
        {error && (
          <p
            className={styleClasses(
              "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
            )}
            data-style-hooks={styleHooks(
              "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
            )}
            role="alert"
          >
            {error}
          </p>
        )}
        {!loading && !error && !byTeam.length && (
          <div
            className={styleClasses(
              "py-15 px-5 text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-3 [&_h2]:mx-0 [&_h2]:mb-1.5",
            )}
            data-style-hooks={styleHooks(
              "lin-empty py-[60px] px-[20px] text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-[12px] [&_h2]:mx-0 [&_h2]:mb-[6px]",
            )}
          >
            <CalendarDays size={30} strokeWidth={1.4} />
            <h2>No cycles yet</h2>
            <p>
              Enable automatic cycles in a team's cycle settings to start
              planning work in iterations.
            </p>
          </div>
        )}
        {byTeam.map(({ team, cycles: tc }) => (
          <section key={team.id} style={{ marginBottom: 8 }}>
            <div
              style={{
                padding: "14px 28px 0",
                display: "flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              <b style={{ fontSize: 13 }}>{team.name}</b>
              <button
                type="button"
                className={styleClasses(
                  "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
                )}
                data-style-hooks={styleHooks(
                  "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
                )}
                onClick={() => onSettings(team)}
              >
                Cycle settings
              </button>
            </div>
            <CycleOverviewRows
              cycles={tc}
              issuesByCycle={issuesByCycle}
              onCycle={onCycle}
            />
          </section>
        ))}
      </div>
    </section>
  );
}

/* ---------------- cycle detail ---------------- */

function download(filename: string, content: string, mime: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type: mime }));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

function cycleICS(cycle: Cycle): string {
  const f = (iso?: string | null) =>
    (iso || "").replace(/[-:]/g, "").slice(0, 15) + "Z";
  const name = cycle.name || `Cycle ${cycle.number ?? ""}`;
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Hoja//Cycle//EN",
    "BEGIN:VEVENT",
    `UID:${cycle.id}@hoja`,
    `DTSTART:${f(cycle.startsAt)}`,
    `DTEND:${f(cycle.endsAt)}`,
    `SUMMARY:${name}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n");
}

function issuesCSV(issues: LinIssue[]): string {
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const head = [
    "Identifier",
    "Title",
    "Status",
    "Priority",
    "Assignee",
    "Labels",
    "Project",
    "Cycle",
    "Created",
    "Updated",
  ];
  const rows = issues.map((i) =>
    [
      i.identifier,
      i.title,
      i.state?.name,
      i.priorityLabel || i.priority,
      i.assignee?.name,
      (i.labels?.nodes || []).map((l) => l.name).join("; "),
      i.project?.name,
      i.cycle?.name || i.cycle?.number,
      i.createdAt,
      i.updatedAt,
    ]
      .map(esc)
      .join(","),
  );
  return [head.join(","), ...rows].join("\n");
}

function CycleOptionsMenu({
  cycle,
  issues,
  onError,
  onClose,
}: {
  cycle: Cycle;
  issues: LinIssue[];
  onError: (message: string) => void;
  onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [dates, setDates] = useState(false);
  const [name, setName] = useState(cycle.name || "");
  const [description, setDescription] = useState(cycle.description || "");
  const [startsAt, setStartsAt] = useState((cycle.startsAt || "").slice(0, 10));
  const [endsAt, setEndsAt] = useState((cycle.endsAt || "").slice(0, 10));
  const busy = false;
  const error = "";
  const [fav, toggleFav] = useFavorite("cycles", cycle.id);
  const following = useFollowing("cycle", cycle.id);
  // Optimistic: the edit shows at once; a rejected save rolls back and reports on the page.
  const save = (input: Record<string, unknown>) => {
    setEditing(false);
    setDates(false);
    onClose();
    updateCycleOptimistic(cycle.id, input).catch((e) =>
      onError((e as Error).message || "Cycle update failed"),
    );
  };
  if (editing || dates) {
    return (
      <LinPopover
        onClose={onClose}
        className={styleClasses(
          "absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
        )}
        data-style-hooks={styleHooks(
          "lin-popover absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
        )}
        style={{ padding: 14, width: 300 }}
      >
        <b style={{ fontSize: 13 }}>
          {editing ? "Edit cycle" : "Change cycle dates"}
        </b>
        {editing && (
          <>
            <label
              style={{ display: "grid", gap: 4, marginTop: 10, fontSize: 12 }}
            >
              Name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                style={{
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  padding: "7px 10px",
                  fontSize: 13,
                }}
              />
            </label>
            <label
              style={{ display: "grid", gap: 4, marginTop: 8, fontSize: 12 }}
            >
              Description
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Optional"
                style={{
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  padding: "7px 10px",
                  fontSize: 13,
                }}
              />
            </label>
          </>
        )}
        {dates && (
          <>
            <label
              style={{ display: "grid", gap: 4, marginTop: 10, fontSize: 12 }}
            >
              Start date
              <input
                type="date"
                value={startsAt}
                onChange={(e) => setStartsAt(e.target.value)}
                style={{
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  padding: "7px 10px",
                  fontSize: 13,
                }}
              />
            </label>
            <label
              style={{ display: "grid", gap: 4, marginTop: 8, fontSize: 12 }}
            >
              End date
              <input
                type="date"
                value={endsAt}
                onChange={(e) => setEndsAt(e.target.value)}
                style={{
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  padding: "7px 10px",
                  fontSize: 13,
                }}
              />
            </label>
          </>
        )}
        {error && (
          <p
            className={styleClasses(
              "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
            )}
            data-style-hooks={styleHooks(
              "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
            )}
            style={{ margin: "8px 0" }}
          >
            {error}
          </p>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button
            type="button"
            className={styleClasses(
              "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
            )}
            data-style-hooks={styleHooks(
              "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
            )}
            disabled={busy}
            onClick={() =>
              save(
                editing
                  ? {
                      name: name.trim() || null,
                      description: description.trim() || null,
                    }
                  : { startsAt: startsAt || null, endsAt: endsAt || null },
              )
            }
          >
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            className={styleClasses(
              "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
            )}
            data-style-hooks={styleHooks(
              "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
            )}
            onClick={() => {
              setEditing(false);
              setDates(false);
            }}
          >
            Cancel
          </button>
        </div>
      </LinPopover>
    );
  }
  const cname = cycle.name || `Cycle ${cycle.number ?? ""}`;
  return (
    <LinMenu
      onClose={onClose}
      className={styleClasses(
        "absolute top-[calc(100%+4px)] left-0 z-130 min-w-57.5 max-h-80 overflow-y-auto",
      )}
      data-style-hooks={styleHooks(
        "lin-dd-panel absolute top-[calc(100%+4px)] left-0 z-130 min-w-[230px] max-h-[320px] overflow-y-auto",
      )}
      items={[
        {
          key: "edit",
          label: "Edit cycle name and description…",
          onSelect: () => setEditing(true),
        },
        {
          key: "dates",
          label: "Change cycle dates",
          onSelect: () => setDates(true),
        },
        {
          key: "sub",
          label: following
            ? "Unsubscribe from cycle notifications"
            : "Subscribe to cycle notifications",
          icon: following ? <Check size={14} /> : undefined,
          disabled: following === null,
          onSelect: () => {
            onClose();
            setFollowing("cycle", cycle.id, !following).catch((e) =>
              onError(
                `Couldn’t ${following ? "unsubscribe" : "subscribe"}: ${(e as Error).message}`,
              ),
            );
          },
        },
        { key: "div1", divider: true },
        {
          key: "fav",
          label: fav ? "Remove from favorites" : "Favorite",
          shortcut: "ALT F",
          icon: fav ? <Check size={14} /> : undefined,
          onSelect: toggleFav,
        },
        {
          key: "copy",
          label: "Copy link",
          icon: <Link2 size={14} />,
          onSelect: () => {
            void navigator.clipboard?.writeText(window.location.href);
          },
        },
        {
          key: "cal",
          label: "Subscribe to cycle calendar",
          icon: <CalendarDays size={14} />,
          onSelect: () =>
            download(
              `${cname.replace(/\s+/g, "-").toLowerCase()}.ics`,
              cycleICS(cycle),
              "text/calendar",
            ),
        },
        {
          key: "csv",
          label: "Export tasks as CSV…",
          icon: <Download size={14} />,
          onSelect: () =>
            download(
              `${cname.replace(/\s+/g, "-").toLowerCase()}-issues.csv`,
              issuesCSV(issues),
              "text/csv",
            ),
        },
      ]}
    />
  );
}

/* Issue groups of a cycle. Memoized so unrelated store changes (which
 * re-render the page) do not re-render hundreds of rows; callbacks from the
 * parent are read through refs to keep the props stable. */
type IssueGroup = ReturnType<typeof groupLinIssues>[number];
const ISSUE_MIME = "application/x-hoja-issue";
const CycleIssueGroups = React.memo(function CycleIssueGroups({
  groups,
  collapsed,
  setCollapsed,
  display,
  childCounts,
  onOpen,
  onMove,
}: {
  groups: IssueGroup[];
  collapsed: string[];
  setCollapsed: React.Dispatch<React.SetStateAction<string[]>>;
  display: LinDisplay;
  childCounts: Map<string, number>;
  onOpen: (issue: LinIssue) => void;
  onMove: (issueId: string, stateId: string) => void;
}) {
  const handlers = useRef({ onOpen, onMove });
  handlers.current = { onOpen, onMove };
  const open = useMemo(() => (i: LinIssue) => handlers.current.onOpen(i), []);
  const noop = useMemo(() => () => {}, []);
  const [dropKey, setDropKey] = useState<string | null>(null);
  return (
    <>
      {groups.map((g) => (
        <section
          role="presentation"
          key={g.key}
          className={styleClasses("mt-2 mx-2 mb-1")}
          data-style-hooks={styleHooks("lin-group mt-[8px] mx-[8px] mb-[4px]")}
          style={
            dropKey === g.key
              ? {
                  outline: "1px dashed var(--accent)",
                  outlineOffset: 2,
                  borderRadius: 7,
                }
              : undefined
          }
          onDragOver={
            g.state
              ? (e) => {
                  if (e.dataTransfer.types.includes(ISSUE_MIME)) {
                    e.preventDefault();
                    if (dropKey !== g.key) setDropKey(g.key);
                  }
                }
              : undefined
          }
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null))
              setDropKey((k) => (k === g.key ? null : k));
          }}
          onDrop={
            g.state
              ? (e) => {
                  e.preventDefault();
                  setDropKey(null);
                  const id = e.dataTransfer.getData(ISSUE_MIME);
                  if (id && g.state) handlers.current.onMove(id, g.state.id);
                }
              : undefined
          }
        >
          <header
            className={styleClasses(
              "flex items-center gap-2 bg-subtle rounded-[7px] min-h-9 py-0 px-2.5 text-[13px] [&_b]:font-semibold",
            )}
            data-style-hooks={styleHooks(
              "lin-group-head flex items-center gap-[8px] bg-subtle rounded-[7px] min-h-[36px] py-0 px-[10px] text-[13px] [&_b]:font-semibold",
            )}
          >
            <button
              type="button"
              className={styleClasses(
                "in-data-[style-hooks~='lin-group-head']:[background:none] in-data-[style-hooks~='lin-group-head']:border-0 in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:grid in-data-[style-hooks~='lin-group-head']:place-items-center in-data-[style-hooks~='lin-group-head']:p-1 in-data-[style-hooks~='lin-group-head']:rounded-sm [[data-style-hooks~='lin-group-head']_&:hover]:text-foreground",
              )}
              data-style-hooks={styleHooks(
                "chev in-data-[style-hooks~='lin-group-head']:[background:none] in-data-[style-hooks~='lin-group-head']:border-0 in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:grid in-data-[style-hooks~='lin-group-head']:place-items-center in-data-[style-hooks~='lin-group-head']:p-[4px] in-data-[style-hooks~='lin-group-head']:rounded-[4px] [[data-style-hooks~='lin-group-head']_&:hover]:text-foreground",
              )}
              aria-label={`${collapsed.includes(g.key) ? "Expand" : "Collapse"} ${g.name}`}
              onClick={() =>
                setCollapsed((c) =>
                  c.includes(g.key)
                    ? c.filter((x) => x !== g.key)
                    : [...c, g.key],
                )
              }
            >
              <ChevronDown
                size={14}
                style={{
                  transform: collapsed.includes(g.key)
                    ? "rotate(-90deg)"
                    : undefined,
                }}
              />
            </button>
            {g.state && <GraphStatusIcon state={g.state} size={13} />}
            <b>{g.name}</b>
            <span
              className={styleClasses(
                "in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:font-medium in-data-[style-hooks~='lin-group-head']:text-[12px] [[data-style-hooks~='lin-bcol']_>_header_&]:text-muted [[data-style-hooks~='lin-bcol']_>_header_&]:font-normal",
              )}
              data-style-hooks={styleHooks(
                "count in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:font-medium in-data-[style-hooks~='lin-group-head']:text-[12px] [[data-style-hooks~='lin-bcol']_>_header_&]:text-muted [[data-style-hooks~='lin-bcol']_>_header_&]:font-normal",
              )}
            >
              {g.items.length}
            </span>
            <span
              className={styleClasses(
                "in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere",
              )}
              data-style-hooks={styleHooks(
                "grow in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere",
              )}
            />
            <Tip label="Creating tasks from a cycle group is not available yet. Use Create new task.">
              <button
                type="button"
                className={styleClasses(
                  "in-data-[style-hooks~='lin-group-head']:[background:none] in-data-[style-hooks~='lin-group-head']:border-0 in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:grid in-data-[style-hooks~='lin-group-head']:place-items-center in-data-[style-hooks~='lin-group-head']:p-1.25 in-data-[style-hooks~='lin-group-head']:rounded-[5px] [[data-style-hooks~='lin-group-head']_&:hover]:bg-line [[data-style-hooks~='lin-group-head']_&:hover]:text-foreground",
                )}
                data-style-hooks={styleHooks(
                  "add in-data-[style-hooks~='lin-group-head']:[background:none] in-data-[style-hooks~='lin-group-head']:border-0 in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:grid in-data-[style-hooks~='lin-group-head']:place-items-center in-data-[style-hooks~='lin-group-head']:p-[5px] in-data-[style-hooks~='lin-group-head']:rounded-[5px] [[data-style-hooks~='lin-group-head']_&:hover]:bg-line [[data-style-hooks~='lin-group-head']_&:hover]:text-foreground",
                )}
                aria-label={`Create task in ${g.name}`}
                disabled
                style={{ opacity: 0.5, cursor: "not-allowed" }}
              >
                <Plus size={14} />
              </button>
            </Tip>
          </header>
          {!collapsed.includes(g.key) &&
            g.items.map((i) => (
              <div
                role="presentation"
                key={i.id}
                draggable
                data-issue-id={i.id}
                onDragStart={(e) => {
                  e.dataTransfer.setData(ISSUE_MIME, i.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onDragEnd={() => setDropKey(null)}
              >
                <IssueRow
                  issue={i}
                  display={display}
                  selected={false}
                  showCheckbox={false}
                  onSelect={noop}
                  onOpen={open}
                  childCount={childCounts.get(i.id) || 0}
                  trailing={<SlaChip sla={i.sla} />}
                />
              </div>
            ))}
        </section>
      ))}
    </>
  );
});

export function GraphCycleDetail({
  cycle: initial,
  team,
  teams,
  viewer,
  orgId,
  workspaceName,
  projects,
  labels,
  cycles,
  onBack,
  onSelect,
  onNavigate,
}: {
  cycle: Cycle;
  team?: Team;
  teams: Team[];
  viewer: Viewer;
  orgId: string;
  workspaceName: string;
  projects: Project[];
  labels: IssueLabel[];
  cycles: Cycle[];
  revision?: number;
  onBack: () => void;
  onSelect: (issue: LinIssue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  // The cycle and its issues come from the local store (live); `initial` only seeds the first paint.
  const allCycles = useCycles();
  const cycle = useMemo(() => {
    const live = allCycles?.find((c) => c.id === initial.id);
    return live ? { ...initial, ...live } : initial;
  }, [allCycles, initial]);
  const issuesByCycle = useIssuesByCycle();
  const issues = issuesByCycle.get(cycle.id) || EMPTY;
  const { ready } = useSyncStatus();
  const loading = !ready;
  const [error, setError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [filters, setFilters] = useState<LinFilters>({ ...emptyFilters });
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [paneOpen, setPaneOpen] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [progressOpen, setProgressOpen] = useState(true);
  const [dimTab, setDimTab] = useState<
    "assignees" | "labels" | "priority" | "projects"
  >("assignees");
  const [dimFilter, setDimFilter] = useState<{
    kind: string;
    id: string;
  } | null>(null);
  const [dateOpen, setDateOpen] = useState(false);
  const [endDate, setEndDate] = useState((initial.endsAt || "").slice(0, 10));
  const savingDate = false;

  const display = useMemo<LinDisplay>(
    () => ({ ...defaultDisplay, grouping: "status" }),
    [],
  );

  useEffect(() => {
    setEndDate((initial.endsAt || "").slice(0, 10));
    setError("");
    setMutationError("");
  }, [initial.id, initial.endsAt]);
  useTeamCycleUpkeep(cycle.team?.id);
  // Closed issues older than the store window are fetched once and adopted.
  const historyScope = useMemo(() => [cycle], [cycle]);
  useCycleHistory(historyScope, 1);

  const state = cycleState(cycle);
  const cname = cycle.name || `Cycle ${cycle.number ?? ""}`;
  const stats = cachedStats(issues);
  const childCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of issues) {
      const pid = i.parent?.id;
      if (pid) m.set(pid, (m.get(pid) || 0) + 1);
    }
    return m;
  }, [issues]);

  const scoped = useMemo(() => {
    let base = applyLinFilters(issues, filters);
    if (dimFilter) {
      if (dimFilter.kind === "assignees")
        base = base.filter((i) => (i.assignee?.id || "none") === dimFilter.id);
      if (dimFilter.kind === "labels")
        base = base.filter((i) =>
          i.labels?.nodes.some((l) => l.id === dimFilter.id),
        );
      if (dimFilter.kind === "priority")
        base = base.filter((i) => String(i.priority || 0) === dimFilter.id);
      if (dimFilter.kind === "projects")
        base = base.filter((i) => (i.project?.id || "none") === dimFilter.id);
    }
    return orderLinIssues(base, display);
  }, [issues, filters, dimFilter, display]);
  const groups = useMemo(
    () => groupLinIssues(scoped, display, team?.states.nodes || []),
    [scoped, team, display],
  );

  const dimRows = useMemo(() => {
    /* Per-bucket rows use the same rule as the cycle itself: canceled work is
     * counted and shown, never folded into the denominator of delivered work. */
    type Bucket = { total: number; done: number; canceled: number };
    const bump = (m: Map<string, Bucket>, key: string, i: LinIssue) => {
      let r = m.get(key);
      if (!r) m.set(key, (r = { total: 0, done: 0, canceled: 0 }));
      if (i.state?.type === "canceled") r.canceled++;
      else {
        r.total++;
        if (i.state?.type === "completed") r.done++;
      }
    };
    const out = (
      key: string,
      icon: React.ReactNode,
      name: string,
      r: Bucket,
    ) => ({
      key,
      icon,
      name,
      pct: r.total ? Math.round((r.done / r.total) * 100) : 0,
      of: r.total,
      canceled: r.canceled,
    });
    if (dimTab === "assignees") {
      const m = new Map<string, Bucket>();
      const names = new Map<string, string>();
      for (const i of issues) {
        const id = i.assignee?.id || "none";
        names.set(id, i.assignee?.name || "No assignee");
        bump(m, id, i);
      }
      return [...m.entries()].map(([id, r]) =>
        out(
          id,
          id === "none" ? (
            <span
              className={styleClasses(
                "rounded-[50%] grid place-items-center font-bold flex-none text-[#5b4a52] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:absolute [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:top-2 [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:right-2.5 in-data-[style-hooks~='lin-sublist']:-ml-1 in-data-[style-hooks~='lin-sublist']:border-2 in-data-[style-hooks~='lin-sublist']:border-solid in-data-[style-hooks~='lin-sublist']:border-panel [[data-style-hooks~='lin-sublist']_&:first-child]:ml-0",
              )}
              data-style-hooks={styleHooks(
                "lin-avatar rounded-[50%] grid place-items-center font-bold flex-none text-[#5b4a52] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:absolute [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:top-[8px] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:right-[10px] in-data-[style-hooks~='lin-sublist']:ml-[-4px] in-data-[style-hooks~='lin-sublist']:border-2 in-data-[style-hooks~='lin-sublist']:border-solid in-data-[style-hooks~='lin-sublist']:border-panel [[data-style-hooks~='lin-sublist']_&:first-child]:ml-0",
              )}
              style={{ width: 20, height: 20 }}
            >
              ?
            </span>
          ) : (
            <Avatar name={names.get(id)!} size={20} />
          ),
          names.get(id)!,
          r,
        ),
      );
    }
    if (dimTab === "labels") {
      const m = new Map<string, Bucket>();
      const labels = new Map<string, IssueLabel>();
      for (const i of issues)
        for (const l of i.labels?.nodes || []) {
          labels.set(l.id, l);
          bump(m, l.id, i);
        }
      return [...m.entries()].map(([id, r]) =>
        out(
          id,
          <i
            style={{
              width: 8,
              height: 8,
              borderRadius: 99,
              background: labels.get(id)!.color || "#8a94a6",
            }}
          />,
          labels.get(id)!.name,
          r,
        ),
      );
    }
    if (dimTab === "priority") {
      const names = ["No priority", "Urgent", "High", "Medium", "Low"];
      const m = new Map<string, Bucket>();
      for (const i of issues) bump(m, String(i.priority || 0), i);
      return [...m.entries()].map(([p, r]) =>
        out(p, <PriorityIcon priority={Number(p)} />, names[Number(p)], r),
      );
    }
    const m = new Map<string, Bucket>();
    const names = new Map<string, string>();
    for (const i of issues) {
      const id = i.project?.id || "none";
      names.set(id, i.project?.name || "No project");
      bump(m, id, i);
    }
    return [...m.entries()].map(([id, r]) =>
      out(id, <span>▣</span>, names.get(id)!, r),
    );
  }, [issues, dimTab]);

  const saveEndDate = () => {
    setDateOpen(false);
    setMutationError("");
    updateCycleOptimistic(cycle.id, { endsAt: endDate || null }).catch((e) =>
      setMutationError((e as Error).message || "Could not change the end date"),
    );
  };
  // Dropping a row on another status group moves it there at once; a rejected update rolls back.
  const moveToState = (issueId: string, stateId: string) => {
    const issue = issues.find((i) => i.id === issueId);
    if (!issue || issue.state?.id === stateId) return;
    setMutationError("");
    updateIssueOptimistic(issueId, { stateId }).catch((e) =>
      setMutationError(
        `${issue.identifier}: ${(e as Error).message || "Status change failed"}`,
      ),
    );
  };

  return (
    <section
      className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
      data-style-hooks={styleHooks(
        "lin-page min-w-0 flex flex-col h-full bg-panel",
      )}
      aria-label={cname}
    >
      <header
        className={styleClasses(
          "flex items-center gap-2 min-h-12.25 py-0 px-5 border-b border-b-line flex-none",
        )}
        data-style-hooks={styleHooks(
          "lin-head flex items-center gap-[8px] min-h-[49px] py-0 px-[20px] border-b border-b-line flex-none",
        )}
      >
        <nav
          className={styleClasses(
            "flex items-center gap-1.75 text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
          )}
          data-style-hooks={styleHooks(
            "lin-crumb flex items-center gap-[7px] text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-[6px] [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
          )}
          aria-label="Breadcrumb"
        >
          <button
            type="button"
            className={styleClasses(
              "[[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-1.5 [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
            )}
            data-style-hooks={styleHooks(
              "crumb-link [[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-[6px] [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
            )}
            onClick={onBack}
          >
            {workspaceName}
          </button>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
            data-style-hooks={styleHooks(
              "sep in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
          >
            ›
          </span>
          <button
            type="button"
            className={styleClasses(
              "[[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-1.5 [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
            )}
            data-style-hooks={styleHooks(
              "crumb-link [[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-[6px] [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
            )}
            onClick={onBack}
          >
            Cycles
          </button>
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
            data-style-hooks={styleHooks(
              "sep in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
            )}
          >
            ›
          </span>
          <CycleStateIcon state={state} size={13} />
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
            data-style-hooks={styleHooks(
              "current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
            )}
          >
            {cname}
          </span>
          <ChevronDown size={13} color="var(--muted)" />
        </nav>
        <div
          className={styleClasses(
            "flex items-center gap-0.5 ml-auto flex-none",
          )}
          data-style-hooks={styleHooks(
            "lin-head-actions flex items-center gap-[2px] ml-auto flex-none",
          )}
        >
          <FavStar kind="cycles" id={cycle.id} label={cname} />
          <span style={{ position: "relative" }}>
            <IconBtn
              label="Cycle options"
              title="Cycle options"
              onClick={() => setMenuOpen((v) => !v)}
            >
              <span style={{ fontWeight: 700 }}>···</span>
            </IconBtn>
            {menuOpen && (
              <CycleOptionsMenu
                cycle={cycle}
                issues={issues}
                onError={setMutationError}
                onClose={() => setMenuOpen(false)}
              />
            )}
          </span>
        </div>
      </header>

      <div
        className={styleClasses(
          "flex flex-1 min-h-0 [@media_(max-width:900px)]:flex-col",
        )}
        data-style-hooks={styleHooks(
          "lin-cdetail flex flex-1 min-h-0 [@media_(max-width:900px)]:flex-col",
        )}
      >
        <div
          className={styleClasses("flex-1 min-w-0 flex flex-col")}
          data-style-hooks={styleHooks(
            "lin-cdetail-main flex-1 min-w-0 flex flex-col",
          )}
        >
          <div
            className={styleClasses(
              "pt-3 px-6 pb-0 text-[13px] text-muted font-medium",
            )}
            data-style-hooks={styleHooks(
              "lin-cdetail-count pt-[12px] px-[24px] pb-0 text-[13px] text-muted font-medium",
            )}
          >
            {issues.length} tasks
            {dimFilter && (
              <>
                {" "}
                · filtered{" "}
                <button
                  type="button"
                  className={styleClasses(
                    "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
                  )}
                  onClick={() => setDimFilter(null)}
                >
                  Clear
                </button>
              </>
            )}
          </div>
          <div
            className={styleClasses(
              "flex items-center gap-1.5 py-2 px-5 border-b border-b-line flex-none",
            )}
            data-style-hooks={styleHooks(
              "lin-toolbar flex items-center gap-[6px] py-[8px] px-[20px] border-b border-b-line flex-none",
            )}
          >
            <span
              className={styleClasses(
                "in-data-[style-hooks~='lin-toolbar']:flex-1",
              )}
              data-style-hooks={styleHooks(
                "spacer in-data-[style-hooks~='lin-toolbar']:flex-1",
              )}
            />
            <span style={{ position: "relative" }}>
              <IconBtn
                label="Add filter"
                title="Add filter"
                active={filterOpen}
                onClick={() => {
                  setFilterOpen((v) => !v);
                  setDisplayOpen(false);
                }}
              >
                <ListFilter size={15} />
              </IconBtn>
              {filterOpen && (
                <FilterMenu
                  filters={filters}
                  setFilters={setFilters}
                  team={team}
                  teams={teams}
                  projects={projects}
                  labels={labels}
                  cycles={cycles}
                  onClose={() => setFilterOpen(false)}
                />
              )}
            </span>
            <span style={{ position: "relative" }}>
              <IconBtn
                label="Display options"
                title="Display options"
                active={displayOpen}
                onClick={() => {
                  setDisplayOpen((v) => !v);
                  setFilterOpen(false);
                }}
              >
                <SlidersHorizontal size={15} />
              </IconBtn>
              {displayOpen && (
                <_DP
                  display={{ ...display, layout: "List" } as LinDisplay}
                  setDisplay={() => {}}
                  onClose={() => setDisplayOpen(false)}
                />
              )}
            </span>
            <IconBtn
              label={paneOpen ? "Close cycle details" : "Open cycle details"}
              title="Toggle details pane"
              active={paneOpen}
              onClick={() => setPaneOpen((v) => !v)}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <rect x="2" y="2.5" width="12" height="11" rx="1.5" />
                <path d="M10 2.5v11" />
              </svg>
            </IconBtn>
          </div>
          <div
            className={styleClasses("flex-1 min-h-0 overflow-y-auto")}
            data-style-hooks={styleHooks(
              "lin-scroll flex-1 min-h-0 overflow-y-auto",
            )}
          >
            {loading && (
              <p
                className={styleClasses("p-10 text-center text-muted")}
                data-style-hooks={styleHooks(
                  "lin-loading p-[40px] text-center text-muted",
                )}
                role="status"
              >
                Loading cycle tasks…
              </p>
            )}
            {error && (
              <p
                className={styleClasses(
                  "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                )}
                data-style-hooks={styleHooks(
                  "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                )}
                role="alert"
              >
                {error}
              </p>
            )}
            {mutationError && (
              <p
                className={styleClasses(
                  "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                )}
                data-style-hooks={styleHooks(
                  "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                )}
                role="alert"
              >
                {mutationError}
              </p>
            )}
            {!loading && !error && (
              <div
                className={styleClasses("pt-1.5 px-3 pb-10")}
                data-style-hooks={styleHooks(
                  "lin-list pt-[6px] px-[12px] pb-[40px]",
                )}
              >
                <CycleIssueGroups
                  groups={groups}
                  collapsed={collapsed}
                  setCollapsed={setCollapsed}
                  display={display}
                  childCounts={childCounts}
                  onOpen={onSelect}
                  onMove={moveToState}
                />
                {!scoped.length && (
                  <div
                    className={styleClasses(
                      "py-15 px-5 text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-3 [&_h2]:mx-0 [&_h2]:mb-1.5",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-empty py-[60px] px-[20px] text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-[12px] [&_h2]:mx-0 [&_h2]:mb-[6px]",
                    )}
                  >
                    <h2>No tasks in this cycle</h2>
                    <p>Tasks added to this cycle will appear here.</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {paneOpen && (
          <aside
            className={styleClasses(
              "w-82.5 flex-none border-l border-l-line overflow-y-auto pt-4.5 px-5 pb-15 [&_h2]:text-[15px] [&_h2]:font-[650] [&_h2]:m-0 [&_h2]:flex [&_h2]:items-center [&_h2]:gap-2 [@media_(max-width:1100px)]:w-72.5 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:border-l-0 [@media_(max-width:900px)]:border-t [@media_(max-width:900px)]:border-t-line",
            )}
            data-style-hooks={styleHooks(
              "lin-cpane w-[330px] flex-none border-l border-l-line overflow-y-auto pt-[18px] px-[20px] pb-[60px] [&_h2]:text-[15px] [&_h2]:font-[650] [&_h2]:m-0 [&_h2]:flex [&_h2]:items-center [&_h2]:gap-[8px] [@media_(max-width:1100px)]:w-[290px] [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:border-l-0 [@media_(max-width:900px)]:border-t [@media_(max-width:900px)]:border-t-line",
            )}
            aria-label="Cycle details"
          >
            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-cpane']:flex in-data-[style-hooks~='lin-cpane']:items-center in-data-[style-hooks~='lin-cpane']:gap-2 in-data-[style-hooks~='lin-cpane']:mb-3",
              )}
              data-style-hooks={styleHooks(
                "cur in-data-[style-hooks~='lin-cpane']:flex in-data-[style-hooks~='lin-cpane']:items-center in-data-[style-hooks~='lin-cpane']:gap-[8px] in-data-[style-hooks~='lin-cpane']:mb-[12px]",
              )}
            >
              <span
                className={styleClasses(
                  "inline-flex items-center text-[12px] font-medium bg-subtle rounded-md py-0.75 px-2.25 text-muted whitespace-nowrap in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
                )}
                data-style-hooks={styleHooks(
                  "lin-badge inline-flex items-center text-[12px] font-medium bg-subtle rounded-[6px] py-[3px] px-[9px] text-muted whitespace-nowrap current in-data-[style-hooks~='lin-crumb']:text-foreground in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:overflow-hidden in-data-[style-hooks~='lin-crumb']:text-ellipsis in-data-[style-hooks~='lin-crumb']:whitespace-nowrap data-[style-hooks~='lin-badge']:text-foreground data-[style-hooks~='lin-cycle-live-dot']:bg-[#5e6ad2] data-[style-hooks~='lin-cycle-live-dot']:shadow-[0_0_0_4px_color-mix(in_srgb,#5e6ad2_12%,transparent)]",
                )}
              >
                {state === "current"
                  ? "Current"
                  : state === "upcoming"
                    ? "Upcoming"
                    : state === "completed"
                      ? "Completed"
                      : "Planned"}
              </span>
              <span
                className={styleClasses(
                  "inline-flex items-center text-[12px] font-medium bg-subtle rounded-md py-0.75 px-2.25 text-muted whitespace-nowrap",
                )}
                data-style-hooks={styleHooks(
                  "lin-badge inline-flex items-center text-[12px] font-medium bg-subtle rounded-[6px] py-[3px] px-[9px] text-muted whitespace-nowrap",
                )}
              >
                {fmtRange(cycle.startsAt, cycle.endsAt)}
              </span>
            </div>
            <h2>
              <CycleStateIcon state={state} size={16} /> {cname}
              <span style={{ flex: 1 }} />
              <FavStar kind="cycles" id={cycle.id} label={cname} />
            </h2>
            <Tip label="Cycle documents are not available in Hoja yet">
              <button
                type="button"
                className={styleClasses(
                  "in-data-[style-hooks~='lin-cpane']:flex in-data-[style-hooks~='lin-cpane']:items-center in-data-[style-hooks~='lin-cpane']:gap-2 in-data-[style-hooks~='lin-cpane']:w-full in-data-[style-hooks~='lin-cpane']:[background:none] in-data-[style-hooks~='lin-cpane']:border-0 in-data-[style-hooks~='lin-cpane']:text-muted in-data-[style-hooks~='lin-cpane']:text-[13px] in-data-[style-hooks~='lin-cpane']:py-2 in-data-[style-hooks~='lin-cpane']:px-1.5 in-data-[style-hooks~='lin-cpane']:rounded-md in-data-[style-hooks~='lin-cpane']:text-left [[data-style-hooks~='lin-cpane']_&:hover]:bg-subtle [[data-style-hooks~='lin-cpane']_&:hover]:text-foreground",
                )}
                data-style-hooks={styleHooks(
                  "adddoc in-data-[style-hooks~='lin-cpane']:flex in-data-[style-hooks~='lin-cpane']:items-center in-data-[style-hooks~='lin-cpane']:gap-[8px] in-data-[style-hooks~='lin-cpane']:w-full in-data-[style-hooks~='lin-cpane']:[background:none] in-data-[style-hooks~='lin-cpane']:border-0 in-data-[style-hooks~='lin-cpane']:text-muted in-data-[style-hooks~='lin-cpane']:text-[13px] in-data-[style-hooks~='lin-cpane']:py-[8px] in-data-[style-hooks~='lin-cpane']:px-[6px] in-data-[style-hooks~='lin-cpane']:rounded-[6px] in-data-[style-hooks~='lin-cpane']:text-left [[data-style-hooks~='lin-cpane']_&:hover]:bg-subtle [[data-style-hooks~='lin-cpane']_&:hover]:text-foreground",
                )}
                disabled
                style={{ opacity: 0.55, cursor: "not-allowed" }}
              >
                <Plus size={14} /> Add document or link…
              </button>
            </Tip>
            <p
              className={styleClasses(
                "in-data-[style-hooks~='lin-cpane']:text-[12px] in-data-[style-hooks~='lin-cpane']:text-muted in-data-[style-hooks~='lin-cpane']:mt-2.5 in-data-[style-hooks~='lin-cpane']:mx-0 in-data-[style-hooks~='lin-cpane']:mb-1",
              )}
              data-style-hooks={styleHooks(
                "datenote in-data-[style-hooks~='lin-cpane']:text-[12px] in-data-[style-hooks~='lin-cpane']:text-muted in-data-[style-hooks~='lin-cpane']:mt-[10px] in-data-[style-hooks~='lin-cpane']:mx-0 in-data-[style-hooks~='lin-cpane']:mb-[4px]",
              )}
            >
              Can only move start date for upcoming cycle
            </p>
            <span style={{ position: "relative" }}>
              <button
                type="button"
                className={styleClasses(
                  "in-data-[style-hooks~='lin-cpane']:inline-flex in-data-[style-hooks~='lin-cpane']:items-center in-data-[style-hooks~='lin-cpane']:gap-2 in-data-[style-hooks~='lin-cpane']:border in-data-[style-hooks~='lin-cpane']:border-solid in-data-[style-hooks~='lin-cpane']:border-line in-data-[style-hooks~='lin-cpane']:rounded-[7px] in-data-[style-hooks~='lin-cpane']:bg-panel in-data-[style-hooks~='lin-cpane']:text-[12px] in-data-[style-hooks~='lin-cpane']:py-1.5 in-data-[style-hooks~='lin-cpane']:px-2.5 in-data-[style-hooks~='lin-cpane']:text-foreground [[data-style-hooks~='lin-cpane']_&:hover]:bg-subtle",
                )}
                data-style-hooks={styleHooks(
                  "datebtn in-data-[style-hooks~='lin-cpane']:inline-flex in-data-[style-hooks~='lin-cpane']:items-center in-data-[style-hooks~='lin-cpane']:gap-[8px] in-data-[style-hooks~='lin-cpane']:border in-data-[style-hooks~='lin-cpane']:border-solid in-data-[style-hooks~='lin-cpane']:border-line in-data-[style-hooks~='lin-cpane']:rounded-[7px] in-data-[style-hooks~='lin-cpane']:bg-panel in-data-[style-hooks~='lin-cpane']:text-[12px] in-data-[style-hooks~='lin-cpane']:py-[6px] in-data-[style-hooks~='lin-cpane']:px-[10px] in-data-[style-hooks~='lin-cpane']:text-foreground [[data-style-hooks~='lin-cpane']_&:hover]:bg-subtle",
                )}
                onClick={() => setDateOpen((v) => !v)}
              >
                Change cycle end date: {fmtDate(cycle.endsAt)}{" "}
                <ChevronDown size={13} />
              </button>
              {dateOpen && (
                <LinPopover
                  onClose={() => setDateOpen(false)}
                  className={styleClasses(
                    "absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-popover absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
                  )}
                  style={{
                    left: 0,
                    top: "calc(100% + 6px)",
                    padding: 12,
                    width: 250,
                  }}
                >
                  <label style={{ display: "grid", gap: 6, fontSize: 12 }}>
                    End date
                    <input
                      type="date"
                      value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                      style={{
                        border: "1px solid var(--line)",
                        borderRadius: 6,
                        padding: "7px 10px",
                        fontSize: 13,
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    className={styleClasses(
                      "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                    )}
                    style={{
                      marginTop: 10,
                      width: "100%",
                      justifyContent: "center",
                    }}
                    disabled={savingDate}
                    onClick={() => void saveEndDate()}
                  >
                    {savingDate ? "Saving…" : "Save"}
                  </button>
                </LinPopover>
              )}
            </span>

            <button
              type="button"
              className={styleClasses(
                "flex items-center gap-1.5 text-[13px] font-semibold mt-4 mx-0 mb-1 [background:none] border-0 text-foreground py-1 px-0 w-full text-left",
              )}
              data-style-hooks={styleHooks(
                "lin-progress-head flex items-center gap-[6px] text-[13px] font-semibold mt-[16px] mx-0 mb-[4px] [background:none] border-0 text-foreground py-[4px] px-0 w-full text-left",
              )}
              aria-expanded={progressOpen}
              onClick={() => setProgressOpen((v) => !v)}
            >
              <ChevronDown
                size={14}
                style={{
                  transform: progressOpen ? undefined : "rotate(-90deg)",
                }}
                color="var(--muted)"
              />{" "}
              Progress
            </button>
            {progressOpen && (
              <>
                <Legend stats={stats} />
                <div style={{ marginTop: 8 }}>
                  <BurndownChart cycle={cycle} stats={stats} issues={issues} />
                </div>
              </>
            )}

            <div
              className={styleClasses(
                "flex gap-1.5 mt-3.5 mx-0 mb-1 flex-wrap",
              )}
              data-style-hooks={styleHooks(
                "lin-ctabs flex gap-[6px] mt-[14px] mx-0 mb-[4px] flex-wrap",
              )}
              role="tablist"
              aria-label="Cycle breakdown"
            >
              {(["assignees", "labels", "priority", "projects"] as const).map(
                (t) => (
                  <button
                    key={t}
                    type="button"
                    role="tab"
                    aria-selected={dimTab === t}
                    className={styleClasses(
                      "py-1.25 px-3 rounded-[99px] text-[12px] font-medium border border-solid border-line bg-panel text-muted aria-selected:bg-subtle aria-selected:text-foreground aria-selected:font-semibold aria-selected:border-line",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-ctab py-[5px] px-[12px] rounded-[99px] text-[12px] font-medium border border-solid border-line bg-panel text-muted aria-selected:bg-subtle aria-selected:text-foreground aria-selected:font-semibold aria-selected:border-line",
                    )}
                    onClick={() => {
                      setDimTab(t);
                      setDimFilter(null);
                    }}
                  >
                    {t[0].toUpperCase() + t.slice(1)}
                  </button>
                ),
              )}
            </div>
            <div>
              {dimRows.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  className={styleClasses(
                    "flex items-center gap-2.5 w-full [background:none] border-0 [font:inherit] text-[13px] text-foreground py-2 px-1.5 rounded-[7px] text-left [&:hover]:bg-subtle",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-statrow flex items-center gap-[10px] w-full [background:none] border-0 [font:inherit] text-[13px] text-foreground py-[8px] px-[6px] rounded-[7px] text-left [&:hover]:bg-subtle",
                  )}
                  onClick={() =>
                    setDimFilter((f) =>
                      f?.id === r.key && f.kind === dimTab
                        ? null
                        : { kind: dimTab, id: r.key },
                    )
                  }
                  style={
                    dimFilter?.id === r.key
                      ? { background: "var(--subtle)" }
                      : undefined
                  }
                >
                  {r.icon}
                  <span
                    className={styleClasses(
                      "in-data-[style-hooks~='lin-statrow']:flex-1 in-data-[style-hooks~='lin-statrow']:min-w-0 in-data-[style-hooks~='lin-statrow']:overflow-hidden in-data-[style-hooks~='lin-statrow']:text-ellipsis in-data-[style-hooks~='lin-statrow']:whitespace-nowrap",
                    )}
                    data-style-hooks={styleHooks(
                      "nm in-data-[style-hooks~='lin-statrow']:flex-1 in-data-[style-hooks~='lin-statrow']:min-w-0 in-data-[style-hooks~='lin-statrow']:overflow-hidden in-data-[style-hooks~='lin-statrow']:text-ellipsis in-data-[style-hooks~='lin-statrow']:whitespace-nowrap",
                    )}
                  >
                    {r.name}
                  </span>
                  <span
                    className={styleClasses(
                      "in-data-[style-hooks~='lin-statrow']:text-muted in-data-[style-hooks~='lin-statrow']:text-[12px] in-data-[style-hooks~='lin-statrow']:inline-flex in-data-[style-hooks~='lin-statrow']:items-center in-data-[style-hooks~='lin-statrow']:gap-1.75 in-data-[style-hooks~='lin-statrow']:whitespace-nowrap",
                    )}
                    data-style-hooks={styleHooks(
                      "pc in-data-[style-hooks~='lin-statrow']:text-muted in-data-[style-hooks~='lin-statrow']:text-[12px] in-data-[style-hooks~='lin-statrow']:inline-flex in-data-[style-hooks~='lin-statrow']:items-center in-data-[style-hooks~='lin-statrow']:gap-[7px] in-data-[style-hooks~='lin-statrow']:whitespace-nowrap",
                    )}
                  >
                    <Ring frac={r.pct / 100} /> {r.pct}% of {r.of}
                    {r.canceled > 0 && <> · {r.canceled} canceled</>}
                  </span>
                </button>
              ))}
              {!dimRows.length && (
                <p
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                >
                  Nothing to break down yet.
                </p>
              )}
            </div>
          </aside>
        )}
      </div>
    </section>
  );
}

/* ---------------- current / upcoming wrapper ---------------- */

/* Mirrors the server's isActive/isNext: the team's active cycle, and the
 * cycle after it by number (or the first one when none is active). */
function pickTeamCycle(
  cycles: Cycle[],
  teamId: string,
  activeId: string | undefined,
  upcoming: boolean,
): Cycle | null {
  const mine = cycles
    .filter((c) => c.team?.id === teamId)
    .sort((a, b) => (a.number ?? 0) - (b.number ?? 0));
  const at = activeId ? mine.findIndex((c) => c.id === activeId) : -1;
  if (!upcoming) return at >= 0 ? mine[at] : null;
  if (activeId && at < 0) return null;
  return mine[at + 1] || null;
}

export function GraphTeamCycle({
  team,
  teams,
  viewer,
  orgId,
  workspaceName,
  projects,
  labels,
  cycles,
  upcoming,
  onBack,
  onSelect,
  onNavigate,
}: {
  team?: Team;
  teams: Team[];
  viewer: Viewer;
  orgId: string;
  workspaceName: string;
  projects: Project[];
  labels: IssueLabel[];
  cycles: Cycle[];
  revision?: number;
  upcoming: boolean;
  onBack: () => void;
  onSelect: (issue: LinIssue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  const meta = useSyncMeta();
  const all = useCycles();
  const teamId = team?.id;
  // The active cycle id lives on the synced team; fall back to the team prop before metadata arrives.
  const activeId = (meta?.teams.nodes.find((t) => t.id === teamId) || team)
    ?.activeCycle?.id;
  const cycle = useMemo(
    () =>
      all && teamId ? pickTeamCycle(all, teamId, activeId, upcoming) : null,
    [all, teamId, activeId, upcoming],
  );
  const loading = all === null;
  const error = "";
  useTeamCycleUpkeep(teamId);
  if (!team)
    return (
      <section
        className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
        data-style-hooks={styleHooks(
          "lin-page min-w-0 flex flex-col h-full bg-panel",
        )}
      >
        <p
          className={styleClasses(
            "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          data-style-hooks={styleHooks(
            "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          role="alert"
        >
          Team not found in this workspace.
        </p>
      </section>
    );
  if (loading)
    return (
      <section
        className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
        data-style-hooks={styleHooks(
          "lin-page min-w-0 flex flex-col h-full bg-panel",
        )}
      >
        <p
          className={styleClasses("p-10 text-center text-muted")}
          data-style-hooks={styleHooks(
            "lin-loading p-[40px] text-center text-muted",
          )}
          role="status"
        >
          Loading cycle…
        </p>
      </section>
    );
  if (error)
    return (
      <section
        className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
        data-style-hooks={styleHooks(
          "lin-page min-w-0 flex flex-col h-full bg-panel",
        )}
      >
        <p
          className={styleClasses(
            "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          data-style-hooks={styleHooks(
            "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          role="alert"
        >
          {error}
        </p>
      </section>
    );
  if (!cycle) {
    return (
      <section
        className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
        data-style-hooks={styleHooks(
          "lin-page min-w-0 flex flex-col h-full bg-panel",
        )}
      >
        <header
          className={styleClasses(
            "flex items-center gap-2 min-h-12.25 py-0 px-5 border-b border-b-line flex-none",
          )}
          data-style-hooks={styleHooks(
            "lin-head flex items-center gap-[8px] min-h-[49px] py-0 px-[20px] border-b border-b-line flex-none",
          )}
        >
          <nav
            className={styleClasses(
              "flex items-center gap-1.75 text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
            )}
            data-style-hooks={styleHooks(
              "lin-crumb flex items-center gap-[7px] text-[13px] text-muted min-w-0 flex-1 [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-[6px] [&_a]:whitespace-nowrap [&_a:hover]:text-foreground",
            )}
          >
            <button
              type="button"
              className={styleClasses(
                "[[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-1.5 [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
              )}
              data-style-hooks={styleHooks(
                "crumb-link [[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-[6px] [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
              )}
              onClick={onBack}
            >
              ← Cycles
            </button>
          </nav>
        </header>
        <div
          className={styleClasses(
            "py-15 px-5 text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-3 [&_h2]:mx-0 [&_h2]:mb-1.5",
          )}
          data-style-hooks={styleHooks(
            "lin-empty py-[60px] px-[20px] text-center text-muted [&_h2]:text-foreground [&_h2]:text-[15px] [&_h2]:mt-[12px] [&_h2]:mx-0 [&_h2]:mb-[6px]",
          )}
        >
          <CalendarDays size={30} strokeWidth={1.4} />
          <h2>No {upcoming ? "upcoming" : "current"} cycle</h2>
          <p>
            {team.name} has no {upcoming ? "next" : "active"} cycle in this
            workspace.
          </p>
        </div>
      </section>
    );
  }
  return (
    <GraphCycleDetail
      cycle={cycle}
      team={team}
      teams={teams}
      viewer={viewer}
      orgId={orgId}
      workspaceName={workspaceName}
      projects={projects}
      labels={labels}
      cycles={cycles}
      onBack={onBack}
      onSelect={onSelect}
      onNavigate={onNavigate}
    />
  );
}

/* ---------------- cycle settings ---------------- */

type Cadence = {
  enabled: boolean;
  weekday?: number | null;
  timezone: string;
  policyVersion: number;
};
type AutoFlags = { started: boolean; completed: boolean; backlog: boolean };
/* Last known settings per team: a revisit renders at once and revalidates in the background. */
const settingsCache = new Map<
  string,
  { cadence?: Cadence; auto?: AutoFlags }
>();

export function GraphTeamCycleSettings({
  team,
  onBack,
  admin,
}: {
  team?: Team;
  onBack: () => void;
  admin?: boolean;
}) {
  const cached = team ? settingsCache.get(team.id) : undefined;
  const [saved, setSaved] = useState<Cadence | null>(cached?.cadence || null);
  const [weekday, setWeekday] = useState(
    cached?.cadence?.weekday == null ? "" : String(cached.cadence.weekday),
  );
  const [autoStarted, setAutoStarted] = useState<boolean | null>(
    cached?.auto?.started ?? null,
  );
  const [autoCompleted, setAutoCompleted] = useState<boolean | null>(
    cached?.auto?.completed ?? null,
  );
  const [autoBacklog, setAutoBacklog] = useState<boolean | null>(
    cached?.auto?.backlog ?? null,
  );
  const autoBusy = false;
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(!cached?.cadence);
  const [error, setError] = useState("");
  // Server-confirmed automation flags (rollback target) and the newest save's sequence number.
  const confirmedAuto = useRef<AutoFlags | null>(cached?.auto || null);
  const autoSeq = useRef(0);
  const teamVersion = useEntityVersion(["team"], [team?.id]);
  const remember = (
    teamId: string,
    next: { cadence?: Cadence; auto?: AutoFlags },
  ) => settingsCache.set(teamId, { ...settingsCache.get(teamId), ...next });
  const showAuto = (f: AutoFlags) => {
    setAutoStarted(f.started);
    setAutoCompleted(f.completed);
    setAutoBacklog(f.backlog);
  };
  useEffect(() => {
    if (!team) return;
    let live = true;
    const teamId = team.id;
    const prior = settingsCache.get(teamId);
    setLoading(!prior?.cadence);
    setError("");
    gql(
      `query($id:String!){teamCycleCadence(teamId:$id){enabled weekday timezone policyVersion}}`,
      { id: teamId },
    )
      .then((d) => {
        remember(teamId, { cadence: d.teamCycleCadence });
        if (live) {
          setSaved(d.teamCycleCadence);
          setWeekday(
            d.teamCycleCadence.weekday == null
              ? ""
              : String(d.teamCycleCadence.weekday),
          );
        }
      })
      .catch((e) => {
        if (live) setError((e as Error).message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    gql(
      `query($id:String!){team(id:$id){autoAddStarted autoAddCompleted autoAddBacklog}}`,
      { id: teamId },
    )
      .then((d) => {
        if (!d.team) return;
        const flags = {
          started: d.team.autoAddStarted !== false,
          completed: d.team.autoAddCompleted !== false,
          backlog: d.team.autoAddBacklog !== false,
        };
        remember(teamId, { auto: flags });
        // Do not clobber a toggle the user made while this was loading.
        if (live && autoSeq.current === 0) {
          confirmedAuto.current = flags;
          showAuto(flags);
        }
      })
      .catch(() => {
        /* flags stay null; card shows loading state */
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team?.id, teamVersion]);
  const enable = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!team || weekday === "" || busy) return;
    const previous = saved;
    // Optimistic: show the schedule as enabled at once; roll back if the server refuses.
    setSaved({
      enabled: true,
      weekday: Number(weekday),
      timezone: previous?.timezone || "UTC",
      policyVersion: previous?.policyVersion ?? 1,
    });
    setBusy(true);
    setError("");
    try {
      const d = await gql(
        `mutation($id:String!,$weekday:Int!){teamCycleCadenceEnable(teamId:$id,weekday:$weekday){enabled weekday timezone policyVersion}}`,
        { id: team.id, weekday: Number(weekday) },
      );
      setSaved(d.teamCycleCadenceEnable);
      remember(team.id, { cadence: d.teamCycleCadenceEnable });
      void getSync()?.pull(); // generated cycles arrive with the metadata
    } catch (e) {
      setSaved(previous);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const days = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
  ];
  // Toggles apply instantly; each save carries all three flags, so the newest one wins.
  const saveAuto = async (
    started: boolean,
    completed: boolean,
    backlog: boolean,
  ) => {
    if (!team || !admin) return;
    const seq = ++autoSeq.current;
    const teamId = team.id;
    showAuto({ started, completed, backlog });
    setError("");
    try {
      const d = await gql(
        `mutation($id:String!,$input:TeamUpdateInput!){teamUpdate(id:$id,input:$input){success team{id autoAddStarted autoAddCompleted autoAddBacklog}}}`,
        {
          id: teamId,
          input: {
            autoAddStarted: started,
            autoAddCompleted: completed,
            autoAddBacklog: backlog,
          },
        },
      );
      if (!d.teamUpdate?.success)
        throw new Error("Could not save cycle automations.");
      const flags = {
        started: d.teamUpdate.team.autoAddStarted !== false,
        completed: d.teamUpdate.team.autoAddCompleted !== false,
        backlog: d.teamUpdate.team.autoAddBacklog !== false,
      };
      confirmedAuto.current = flags;
      remember(teamId, { auto: flags });
      if (seq === autoSeq.current) showAuto(flags);
    } catch (e) {
      if (seq === autoSeq.current) {
        if (confirmedAuto.current) showAuto(confirmedAuto.current);
        setError((e as Error).message || "Could not save cycle automations.");
      }
    }
  };
  return (
    <section
      className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
      data-style-hooks={styleHooks(
        "lin-page min-w-0 flex flex-col h-full bg-panel",
      )}
    >
      <div
        className={styleClasses("flex-1 min-h-0 overflow-y-auto")}
        data-style-hooks={styleHooks(
          "lin-scroll flex-1 min-h-0 overflow-y-auto",
        )}
      >
        <div
          className={styleClasses(
            'max-w-160 py-7 px-8 [&_h1]:text-[18px] [&_h1]:mt-0 [&_h1]:mx-0 [&_h1]:mb-1.5 [&_h1]:tracking-[-.02em] [&_select]:border [&_select]:border-solid [&_select]:border-line [&_select]:rounded-[7px] [&_select]:py-2 [&_select]:px-2.5 [&_select]:[font:inherit] [&_select]:bg-panel [&_select]:text-foreground [&_select]:max-w-70 [&_input[type="date"]]:border [&_input[type="date"]]:border-solid [&_input[type="date"]]:border-line [&_input[type="date"]]:rounded-[7px] [&_input[type="date"]]:py-2 [&_input[type="date"]]:px-2.5 [&_input[type="date"]]:[font:inherit] [&_input[type="date"]]:bg-panel [&_input[type="date"]]:text-foreground [&_input[type="date"]]:max-w-70',
          )}
          data-style-hooks={styleHooks(
            'lin-csettings max-w-[640px] py-[28px] px-[32px] [&_h1]:text-[18px] [&_h1]:mt-0 [&_h1]:mx-0 [&_h1]:mb-[6px] [&_h1]:tracking-[-.02em] [&_select]:border [&_select]:border-solid [&_select]:border-line [&_select]:rounded-[7px] [&_select]:py-[8px] [&_select]:px-[10px] [&_select]:[font:inherit] [&_select]:bg-panel [&_select]:text-foreground [&_select]:max-w-[280px] [&_input[type="date"]]:border [&_input[type="date"]]:border-solid [&_input[type="date"]]:border-line [&_input[type="date"]]:rounded-[7px] [&_input[type="date"]]:py-[8px] [&_input[type="date"]]:px-[10px] [&_input[type="date"]]:[font:inherit] [&_input[type="date"]]:bg-panel [&_input[type="date"]]:text-foreground [&_input[type="date"]]:max-w-[280px]',
          )}
        >
          <button
            type="button"
            className={styleClasses(
              "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
            )}
            data-style-hooks={styleHooks(
              "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
            )}
            onClick={onBack}
          >
            ← Cycles
          </button>
          <h1 style={{ marginTop: 8 }}>
            {team?.name || "Team"} cycle settings
          </h1>
          <p
            className={styleClasses(
              "[[data-style-hooks~='lin-csettings']_p&]:text-muted [[data-style-hooks~='lin-csettings']_p&]:text-[13px] [[data-style-hooks~='lin-csettings']_p&]:mt-0 [[data-style-hooks~='lin-csettings']_p&]:mx-0 [[data-style-hooks~='lin-csettings']_p&]:mb-5",
            )}
            data-style-hooks={styleHooks(
              "desc [[data-style-hooks~='lin-csettings']_p&]:text-muted [[data-style-hooks~='lin-csettings']_p&]:text-[13px] [[data-style-hooks~='lin-csettings']_p&]:mt-0 [[data-style-hooks~='lin-csettings']_p&]:mx-0 [[data-style-hooks~='lin-csettings']_p&]:mb-[20px]",
            )}
          >
            Configure how {team?.name || "this team"} plans work in iterations.
          </p>
          {!team ? (
            <p
              className={styleClasses(
                "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
              )}
              data-style-hooks={styleHooks(
                "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
              )}
              role="alert"
            >
              Team not found in this workspace.
            </p>
          ) : loading ? (
            <p
              className={styleClasses("p-10 text-center text-muted")}
              data-style-hooks={styleHooks(
                "lin-loading p-[40px] text-center text-muted",
              )}
              role="status"
            >
              Loading cycle settings…
            </p>
          ) : (
            <>
              {error && (
                <p
                  className={styleClasses(
                    "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                  )}
                  role="alert"
                >
                  {error}
                </p>
              )}
              <div
                className={styleClasses(
                  "in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-4.5 in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-3.5 [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-1 [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-3",
                )}
                data-style-hooks={styleHooks(
                  "card in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-[18px] in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-[4px] [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-[12px]",
                )}
              >
                <h2>Automatic cycles</h2>
                {saved?.enabled ? (
                  <p>
                    Enabled · {saved.timezone} · cycles start each{" "}
                    {days[saved.weekday ?? 0]}. The current and next two cycles
                    are generated automatically; completed cycles close on their
                    end date.
                  </p>
                ) : (
                  <p>
                    When enabled, Hoja generates one-week cycles for this team:
                    a current cycle and two upcoming cycles, rolling forward
                    automatically.
                  </p>
                )}
                {!saved?.enabled && (
                  <form onSubmit={enable}>
                    <label
                      className={styleClasses(
                        "[[data-style-hooks~='lin-csettings']_label&]:grid [[data-style-hooks~='lin-csettings']_label&]:gap-1.5 [[data-style-hooks~='lin-csettings']_label&]:text-[12px] [[data-style-hooks~='lin-csettings']_label&]:font-semibold [[data-style-hooks~='lin-csettings']_label&]:mb-3",
                      )}
                      data-style-hooks={styleHooks(
                        "f [[data-style-hooks~='lin-csettings']_label&]:grid [[data-style-hooks~='lin-csettings']_label&]:gap-[6px] [[data-style-hooks~='lin-csettings']_label&]:text-[12px] [[data-style-hooks~='lin-csettings']_label&]:font-semibold [[data-style-hooks~='lin-csettings']_label&]:mb-[12px]",
                      )}
                    >
                      Start day
                      <select
                        value={weekday}
                        onChange={(e) => setWeekday(e.target.value)}
                        required
                      >
                        <option value="">Choose weekday</option>
                        {days.map((d, i) => (
                          <option key={d} value={i}>
                            {d}
                          </option>
                        ))}
                      </select>
                    </label>
                    {!admin && (
                      <p
                        className={styleClasses("text-[12px] text-muted")}
                        data-style-hooks={styleHooks(
                          "lin-note text-[12px] text-muted",
                        )}
                        role="status"
                      >
                        Only workspace admins can enable team cycles.
                      </p>
                    )}
                    <button
                      type="submit"
                      className={styleClasses(
                        "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                      )}
                      disabled={!admin || busy || weekday === ""}
                    >
                      {busy ? "Enabling…" : "Enable automatic cycles"}
                    </button>
                  </form>
                )}
              </div>
              <div
                className={styleClasses(
                  "in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-4.5 in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-3.5 [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-1 [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-3",
                )}
                data-style-hooks={styleHooks(
                  "card in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-[18px] in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-[4px] [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-[12px]",
                )}
              >
                <h2>Cycle length</h2>
                <p>One-week cycles, no cooldown, two upcoming.</p>
                <div
                  className={styleClasses(
                    "in-data-[style-hooks~='lin-csettings']:grid in-data-[style-hooks~='lin-csettings']:grid-cols-[1fr_1fr] in-data-[style-hooks~='lin-csettings']:gap-3",
                  )}
                  data-style-hooks={styleHooks(
                    "row2 in-data-[style-hooks~='lin-csettings']:grid in-data-[style-hooks~='lin-csettings']:grid-cols-[1fr_1fr] in-data-[style-hooks~='lin-csettings']:gap-[12px]",
                  )}
                >
                  <label
                    className={styleClasses(
                      "[[data-style-hooks~='lin-csettings']_label&]:grid [[data-style-hooks~='lin-csettings']_label&]:gap-1.5 [[data-style-hooks~='lin-csettings']_label&]:text-[12px] [[data-style-hooks~='lin-csettings']_label&]:font-semibold [[data-style-hooks~='lin-csettings']_label&]:mb-3",
                    )}
                    data-style-hooks={styleHooks(
                      "f [[data-style-hooks~='lin-csettings']_label&]:grid [[data-style-hooks~='lin-csettings']_label&]:gap-[6px] [[data-style-hooks~='lin-csettings']_label&]:text-[12px] [[data-style-hooks~='lin-csettings']_label&]:font-semibold [[data-style-hooks~='lin-csettings']_label&]:mb-[12px]",
                    )}
                  >
                    Length
                    <select value="1" disabled>
                      <option value="1">1 week</option>
                    </select>
                  </label>
                  <label
                    className={styleClasses(
                      "[[data-style-hooks~='lin-csettings']_label&]:grid [[data-style-hooks~='lin-csettings']_label&]:gap-1.5 [[data-style-hooks~='lin-csettings']_label&]:text-[12px] [[data-style-hooks~='lin-csettings']_label&]:font-semibold [[data-style-hooks~='lin-csettings']_label&]:mb-3",
                    )}
                    data-style-hooks={styleHooks(
                      "f [[data-style-hooks~='lin-csettings']_label&]:grid [[data-style-hooks~='lin-csettings']_label&]:gap-[6px] [[data-style-hooks~='lin-csettings']_label&]:text-[12px] [[data-style-hooks~='lin-csettings']_label&]:font-semibold [[data-style-hooks~='lin-csettings']_label&]:mb-[12px]",
                    )}
                  >
                    Cooldown
                    <select value="0" disabled>
                      <option value="0">None</option>
                    </select>
                  </label>
                </div>
                <p
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                >
                  Other lengths and cooldowns are not supported by this server
                  yet.
                </p>
              </div>
              <div
                className={styleClasses(
                  "in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-4.5 in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-3.5 [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-1 [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-3",
                )}
                data-style-hooks={styleHooks(
                  "card in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-[18px] in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-[4px] [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-[12px]",
                )}
              >
                <h2>Auto-complete</h2>
                <p>
                  Cycles complete automatically on their end date. Incomplete
                  tasks roll into the next cycle.
                </p>
                <p
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                >
                  Changing or disabling the schedule is not available in this
                  release.
                </p>
              </div>
              <div
                className={styleClasses(
                  "in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-4.5 in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-3.5 [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-1 [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-3",
                )}
                data-style-hooks={styleHooks(
                  "card in-data-[style-hooks~='lin-csettings']:border in-data-[style-hooks~='lin-csettings']:border-solid in-data-[style-hooks~='lin-csettings']:border-line in-data-[style-hooks~='lin-csettings']:rounded-[10px] in-data-[style-hooks~='lin-csettings']:p-[18px] in-data-[style-hooks~='lin-csettings']:bg-panel in-data-[style-hooks~='lin-csettings']:mb-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:text-[14px] [[data-style-hooks~='lin-csettings']_&_h2]:mt-0 [[data-style-hooks~='lin-csettings']_&_h2]:mx-0 [[data-style-hooks~='lin-csettings']_&_h2]:mb-[4px] [[data-style-hooks~='lin-csettings']_&_p]:text-[13px] [[data-style-hooks~='lin-csettings']_&_p]:text-muted [[data-style-hooks~='lin-csettings']_&_p]:mt-0 [[data-style-hooks~='lin-csettings']_&_p]:mx-0 [[data-style-hooks~='lin-csettings']_&_p]:mb-[12px]",
                )}
              >
                <h2>Cycle automations</h2>
                <p>
                  Automatically add tasks without a cycle into the current
                  cycle. Tasks already assigned to a cycle are never moved.
                </p>
                {autoStarted === null ||
                autoCompleted === null ||
                autoBacklog === null ? (
                  <p
                    className={styleClasses("p-10 text-center text-muted")}
                    data-style-hooks={styleHooks(
                      "lin-loading p-[40px] text-center text-muted",
                    )}
                    role="status"
                  >
                    Loading automations…
                  </p>
                ) : (
                  <>
                    <label
                      className={styleClasses(
                        "in-data-[style-hooks~='lin-csettings']:flex in-data-[style-hooks~='lin-csettings']:items-center in-data-[style-hooks~='lin-csettings']:gap-2.5 in-data-[style-hooks~='lin-csettings']:text-[13px] in-data-[style-hooks~='lin-csettings']:py-1.75 in-data-[style-hooks~='lin-csettings']:px-0.5 in-data-[style-hooks~='lin-csettings']:cursor-pointer [[data-style-hooks~='lin-csettings']_&_input:disabled]:cursor-not-allowed",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-auto-row in-data-[style-hooks~='lin-csettings']:flex in-data-[style-hooks~='lin-csettings']:items-center in-data-[style-hooks~='lin-csettings']:gap-[10px] in-data-[style-hooks~='lin-csettings']:text-[13px] in-data-[style-hooks~='lin-csettings']:py-[7px] in-data-[style-hooks~='lin-csettings']:px-[2px] in-data-[style-hooks~='lin-csettings']:cursor-pointer [[data-style-hooks~='lin-csettings']_&_input:disabled]:cursor-not-allowed",
                      )}
                    >
                      <input
                        type="checkbox"
                        className={styleClasses(
                          "w-3.75 h-3.75 accent-accent flex-none",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-check w-[15px] h-[15px] accent-accent flex-none",
                        )}
                        checked={autoStarted}
                        disabled={!admin || autoBusy}
                        onChange={(e) => {
                          void saveAuto(
                            e.target.checked,
                            autoCompleted,
                            autoBacklog,
                          );
                        }}
                      />
                      <span>
                        Auto-add <b>started</b> tasks to the current cycle
                      </span>
                    </label>
                    <label
                      className={styleClasses(
                        "in-data-[style-hooks~='lin-csettings']:flex in-data-[style-hooks~='lin-csettings']:items-center in-data-[style-hooks~='lin-csettings']:gap-2.5 in-data-[style-hooks~='lin-csettings']:text-[13px] in-data-[style-hooks~='lin-csettings']:py-1.75 in-data-[style-hooks~='lin-csettings']:px-0.5 in-data-[style-hooks~='lin-csettings']:cursor-pointer [[data-style-hooks~='lin-csettings']_&_input:disabled]:cursor-not-allowed",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-auto-row in-data-[style-hooks~='lin-csettings']:flex in-data-[style-hooks~='lin-csettings']:items-center in-data-[style-hooks~='lin-csettings']:gap-[10px] in-data-[style-hooks~='lin-csettings']:text-[13px] in-data-[style-hooks~='lin-csettings']:py-[7px] in-data-[style-hooks~='lin-csettings']:px-[2px] in-data-[style-hooks~='lin-csettings']:cursor-pointer [[data-style-hooks~='lin-csettings']_&_input:disabled]:cursor-not-allowed",
                      )}
                    >
                      <input
                        type="checkbox"
                        className={styleClasses(
                          "w-3.75 h-3.75 accent-accent flex-none",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-check w-[15px] h-[15px] accent-accent flex-none",
                        )}
                        checked={autoCompleted}
                        disabled={!admin || autoBusy}
                        onChange={(e) => {
                          void saveAuto(
                            autoStarted,
                            e.target.checked,
                            autoBacklog,
                          );
                        }}
                      />
                      <span>
                        Auto-add <b>completed</b> tasks to the current cycle
                      </span>
                    </label>
                    <label
                      className={styleClasses(
                        "in-data-[style-hooks~='lin-csettings']:flex in-data-[style-hooks~='lin-csettings']:items-center in-data-[style-hooks~='lin-csettings']:gap-2.5 in-data-[style-hooks~='lin-csettings']:text-[13px] in-data-[style-hooks~='lin-csettings']:py-1.75 in-data-[style-hooks~='lin-csettings']:px-0.5 in-data-[style-hooks~='lin-csettings']:cursor-pointer [[data-style-hooks~='lin-csettings']_&_input:disabled]:cursor-not-allowed",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-auto-row in-data-[style-hooks~='lin-csettings']:flex in-data-[style-hooks~='lin-csettings']:items-center in-data-[style-hooks~='lin-csettings']:gap-[10px] in-data-[style-hooks~='lin-csettings']:text-[13px] in-data-[style-hooks~='lin-csettings']:py-[7px] in-data-[style-hooks~='lin-csettings']:px-[2px] in-data-[style-hooks~='lin-csettings']:cursor-pointer [[data-style-hooks~='lin-csettings']_&_input:disabled]:cursor-not-allowed",
                      )}
                    >
                      <input
                        type="checkbox"
                        className={styleClasses(
                          "w-3.75 h-3.75 accent-accent flex-none",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-check w-[15px] h-[15px] accent-accent flex-none",
                        )}
                        checked={autoBacklog}
                        disabled={!admin || autoBusy}
                        onChange={(e) => {
                          void saveAuto(
                            autoStarted,
                            autoCompleted,
                            e.target.checked,
                          );
                        }}
                      />
                      <span>
                        Auto-add <b>backlog</b> tasks to the current cycle
                      </span>
                    </label>
                    {!admin && (
                      <p
                        className={styleClasses("text-[12px] text-muted")}
                        data-style-hooks={styleHooks(
                          "lin-note text-[12px] text-muted",
                        )}
                        role="status"
                      >
                        Only workspace admins can change cycle automations.
                      </p>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
