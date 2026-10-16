import React, {useEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react';
import {Bell, CalendarDays, Check, ChevronDown, ChevronRight, Copy, Download, Link2, ListFilter, Plus, Settings2, SlidersHorizontal, X} from 'lucide-react';
import {gql, stateKey} from '../main';
import type {Cycle, Issue, IssueLabel, IssueState, Project, Team, Viewer} from '../main';
import {getSync, useEntityVersion, useIssues, useSyncMeta, useSyncStatus} from '../sync';
import {applyLinFilters, defaultDisplay, emptyFilters, groupLinIssues, orderLinIssues, DisplayPanel as _DP, FilterMenu, IssueRow, LinDisplay, LinFilters, LinIssue, linIssueFields} from './issues';
import {Avatar, CycleGlyph, FavStar, IconBtn, LabelChip, LinMenu, LinearStatusIcon, PriorityIcon, Ring, Tip, cx, fmtDate, fmtRange, useDismiss, useFavorite} from './shared';

/* Linear 1:1 Cycles views. */

export type CycleState = 'planned' | 'upcoming' | 'current' | 'completed';

export function cycleState(c: Cycle, now = Date.now()): CycleState {
  if (c.completedAt) return 'completed';
  const s = c.startsAt ? Date.parse(c.startsAt) : NaN;
  const e = c.endsAt ? Date.parse(c.endsAt) : NaN;
  if (!Number.isNaN(e) && e < now) return 'completed';
  if (!Number.isNaN(s) && !Number.isNaN(e) && s <= now && now <= e) return 'current';
  if (!Number.isNaN(s) && s > now) {
    // the nearest future cycle is "upcoming"; the rest are planned — resolved by caller ordering
    return 'upcoming';
  }
  return 'planned';
}

export function CycleStateIcon({state, size = 15}: {state: CycleState; size?: number}) {
  if (state === 'completed')
    return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#8a94a6" strokeWidth="1.5" aria-label="Completed"><path d="M2.7 8a5.3 5.3 0 1 1 1.6 3.8" strokeLinecap="round" /><path d="M2.7 12.4v-3h3" strokeLinecap="round" strokeLinejoin="round" /></svg>);
  if (state === 'current')
    return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#5e6ad2" strokeWidth="1.5" aria-label="Current"><circle cx="8" cy="8" r="6.2" /><path d="M6.4 5.4v5.2l4.5-2.6z" fill="#5e6ad2" stroke="none" /></svg>);
  if (state === 'upcoming')
    return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#8a94a6" strokeWidth="1.5" aria-label="Upcoming"><circle cx="8" cy="8" r="6.2" /><path d="M6.6 5.6v4.8l4.2-2.4z" fill="#8a94a6" stroke="none" opacity=".7" /></svg>);
  return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#8a94a6" strokeWidth="1.5" aria-label="Planned"><circle cx="8" cy="8" r="5.6" strokeDasharray="2.6 2.2" /><path d="M8 5v3.2l2.2 1.4" strokeLinecap="round" /></svg>);
}

export type CycleStats = {scope: number; started: number; completed: number; points: number};

export function statsFor(issues: LinIssue[]): CycleStats {
  let started = 0, completed = 0, points = 0;
  for (const i of issues) {
    const t = i.state?.type;
    if (t === 'completed') completed++; else if (t === 'started') started++;
    points += i.estimate || 0;
  }
  return {scope: issues.length, started, completed, points};
}

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
const subscribeCycles = (cb: () => void) => { cycleListeners.add(cb); return () => { cycleListeners.delete(cb); }; };
function bumpCycles() { cycleLocalVersion++; for (const cb of cycleListeners) cb(); }

/** Overlay a local cycle edit; call the returned function with whether the server accepted it. */
export function patchCycle(cycleId: string, patch: Partial<Cycle>): (accepted: boolean, fresh?: Cycle | null) => void {
  const layers = cycleOverlays.get(cycleId) || [];
  layers.push(patch);
  cycleOverlays.set(cycleId, layers);
  bumpCycles();
  return (accepted, fresh) => {
    if (accepted && fresh) extraCycles.set(fresh.id, {...extraCycles.get(fresh.id), ...fresh});
    const drop = () => {
      const rest = cycleOverlays.get(cycleId)?.filter(p => p !== patch) || [];
      if (rest.length) cycleOverlays.set(cycleId, rest); else cycleOverlays.delete(cycleId);
      bumpCycles();
    };
    // Keep an accepted edit until a pull has brought the refreshed metadata.
    const sync = getSync();
    if (accepted && sync) void sync.pull().finally(drop); else drop();
  };
}

function rememberCycles(cycles: Cycle[]) {
  if (!cycles.length) return;
  for (const c of cycles) extraCycles.set(c.id, {...extraCycles.get(c.id), ...c});
  bumpCycles();
}

/** Every known cycle (metadata ∪ fetched extras, with pending local edits applied); null until the store has metadata. */
export function useCycles(): Cycle[] | null {
  const meta = useSyncMeta();
  const version = useSyncExternalStore(subscribeCycles, () => cycleLocalVersion);
  return useMemo(() => {
    if (!meta) return null;
    const byId = new Map<string, Cycle>();
    for (const c of extraCycles.values()) byId.set(c.id, c);
    // Metadata is fresher than anything fetched earlier; extras only add fields it lacks (description).
    for (const c of meta.cycles.nodes) { const prior = byId.get(c.id); byId.set(c.id, prior ? {...prior, ...c} : c); }
    for (const [id, layers] of cycleOverlays) { const base = byId.get(id); if (base) byId.set(id, Object.assign({}, base, ...layers)); }
    return [...byId.values()];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, version]);
}

/* Issues grouped by cycle id, shared by every mounted cycle view. A cycle's
 * array keeps its identity while its members are unchanged, so per-cycle
 * stats (cached by array) and memoized rows skip unrelated store changes. */
let groupMemo: {issues: Issue[] | null; groups: Map<string, LinIssue[]>} = {issues: null, groups: new Map()};
function groupByCycle(issues: Issue[]): Map<string, LinIssue[]> {
  if (groupMemo.issues === issues) return groupMemo.groups;
  const next = new Map<string, LinIssue[]>();
  for (const i of issues) {
    const cid = i.cycle?.id;
    if (!cid) continue;
    const arr = next.get(cid);
    if (arr) arr.push(i as LinIssue); else next.set(cid, [i as LinIssue]);
  }
  const prev = groupMemo.groups;
  for (const [cid, arr] of next) {
    const old = prev.get(cid);
    if (old && old.length === arr.length && old.every((x, k) => x === arr[k])) next.set(cid, old);
  }
  groupMemo = {issues, groups: next};
  return next;
}
export function useIssuesByCycle(): Map<string, LinIssue[]> {
  return groupByCycle(useIssues());
}

const statsCache = new WeakMap<LinIssue[], CycleStats>();
export function cachedStats(issues: LinIssue[]): CycleStats {
  let s = statsCache.get(issues);
  if (!s) { s = statsFor(issues); statsCache.set(issues, s); }
  return s;
}

/* Closed issues older than the store's window are not synced. A cycle that
 * started before that window may own some; fetch them once and adopt them so
 * the store (and its deltas) keeps them fresh from then on. */
const historyFields = `{ id identifier title createdAt updatedAt stateChangedAt dueDate priority priorityLabel state { id name color type } team { id key name } project { id name } assignee { id name } creator { id name } cycle { id name number } labels { nodes { id name color } } }`;
const historyRequested = new Set<string>();
// Session markers survive reloads (module state does not); storage may be unavailable.
const sessionGet = (k: string) => { try { return sessionStorage.getItem(k); } catch { return null; } };
const sessionSet = (k: string, v: string) => { try { sessionStorage.setItem(k, v); } catch { /* best effort */ } };
const emptyHistoryKey = (cycleId: string) => `hoja-cycle-history-empty:${cycleId}`;
export function cycleNeedsHistory(c: Cycle, closedCutoff: string): boolean {
  if (!closedCutoff) return false;
  return !c.startsAt || c.startsAt.slice(0, 10) < closedCutoff;
}
async function loadCycleHistory(cycleIds: string[], closedCutoff: string): Promise<void> {
  const ids = cycleIds.filter(id => !historyRequested.has(id));
  if (!ids.length) return;
  for (const id of ids) historyRequested.add(id);
  const found = new Set<string>();
  try {
    let after: string | null = null;
    for (let page = 0; page < 20; page++) {
      const d = await gql(`query CycleHistory($after:String,$filter:IssueFilter){issues(first:250,after:$after,filter:$filter){nodes ${historyFields} pageInfo{hasNextPage endCursor}}}`,
        {after, filter: {cycle: {id: {in: ids}}, state: {type: {in: ['completed', 'canceled']}}}});
      getSync()?.adopt(d.issues.nodes);
      for (const n of d.issues.nodes as Issue[]) if (n.cycle?.id) found.add(n.cycle.id);
      const info = d.issues.pageInfo;
      if (!info?.hasNextPage || !info.endCursor || info.endCursor === after) break;
      after = info.endCursor;
    }
    // Cycles with nothing out of window leave no adopted trace; remember that for this window.
    for (const id of ids) if (!found.has(id)) sessionSet(emptyHistoryKey(id), closedCutoff);
  } catch {
    for (const id of ids) historyRequested.delete(id); // retry on the next view
  }
}
/* A closed issue older than the window only enters the store through adopt
 * (bootstrap leaves it out; any edit makes it recent), so finding one means
 * this cycle's history was adopted into the persisted snapshot already. */
function hasAdoptedHistory(issues: LinIssue[] | undefined, closedCutoff: string): boolean {
  return !!issues?.some(i => (i.state?.type === 'completed' || i.state?.type === 'canceled') && (i.updatedAt || '') < closedCutoff);
}
/** Background-load the out-of-window closed issues of these cycles (newest first, bounded). */
function useCycleHistory(cycles: Cycle[], limit: number) {
  const {ready, closedCutoff} = useSyncStatus();
  const byCycle = useIssuesByCycle();
  const key = useMemo(() => cycles
    .filter(c => cycleNeedsHistory(c, closedCutoff))
    .sort((a, b) => (b.startsAt || '').localeCompare(a.startsAt || ''))
    .slice(0, limit).map(c => c.id).join(','), [cycles, closedCutoff, limit]);
  useEffect(() => {
    if (!ready || !key) return;
    const missing = key.split(',').filter(id =>
      !hasAdoptedHistory(byCycle.get(id), closedCutoff) && sessionGet(emptyHistoryKey(id)) !== closedCutoff);
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
  const cycleVersion = useEntityVersion(['cycle'], undefined, 250);
  useEffect(() => {
    if (!teamId || !hasMeta) return;
    const last = Number(sessionGet(reconcileKey(teamId)) || 0);
    if (!truncated && Date.now() - last < 5 * 60_000) return;
    sessionSet(reconcileKey(teamId), String(Date.now()));
    gql(`query TeamCycles($filter:CycleFilter){cycles(first:100,filter:$filter){nodes{${cycleFields}}}}`,
      {filter: {team: {id: {eq: teamId}}}})
      .then(d => { if (truncated) rememberCycles(d.cycles.nodes); })
      .catch(() => { sessionSet(reconcileKey(teamId), '0'); });
    // cycleVersion: refetch the truncated set when another client changes cycles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId, hasMeta, truncated, truncated ? cycleVersion : 0]);
}

/* Optimistic issue update from a cycle view: overlay → mutation → settle/rollback. */
export async function updateIssueOptimistic(issueId: string, input: Record<string, unknown>): Promise<void> {
  const sync = getSync();
  const settle = sync ? sync.patch(issueId, sync.patchFromInput(input)) : null;
  try {
    const d = await gql(`mutation CycleIssueUpdate($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}`, {id: issueId, input});
    if (!d.issueUpdate?.success) throw new Error('Update was rejected');
    settle?.(true);
  } catch (e) {
    settle?.(false);
    throw e;
  }
}

const cycleFields = 'id name description number startsAt endsAt completedAt progress team{id key name}';
/** Optimistic cycleUpdate. Null inputs mean "unchanged" on this server, so they are not overlaid. */
async function updateCycleOptimistic(cycleId: string, input: Record<string, unknown>): Promise<void> {
  const patch: Partial<Cycle> = {};
  for (const [k, v] of Object.entries(input)) if (v != null) (patch as Record<string, unknown>)[k] = v;
  const settle = patchCycle(cycleId, patch);
  try {
    const d = await gql(`mutation CycleUpdate($id:String!,$input:CycleUpdateInput!){cycleUpdate(id:$id,input:$input){success cycle{${cycleFields}}}}`, {id: cycleId, input});
    if (!d.cycleUpdate?.success) throw new Error('Cycle update was rejected');
    settle(true, d.cycleUpdate.cycle);
  } catch (e) {
    settle(false);
    throw e;
  }
}

/* ---------------- burndown chart ---------------- */

/* Remaining scope at daily steps from the cycle start to now (or its end),
 * from completion times of the store's issues: scope minus issues completed
 * by then. Without history this is the best client-side burndown. */
function remainingSteps(issues: LinIssue[] | undefined, scope: number, start: number, until: number): [number, number][] {
  if (!issues || !(until > start)) return [];
  const done = issues.filter(i => i.state?.type === 'completed')
    .map(i => Date.parse(i.stateChangedAt || i.updatedAt || '')).filter(t => !Number.isNaN(t)).sort((a, b) => a - b);
  const steps: [number, number][] = [];
  const days = Math.min(400, Math.ceil((until - start) / 864e5));
  let k = 0;
  for (let d = 0; d <= days; d++) {
    const t = Math.min(until, start + d * 864e5);
    while (k < done.length && done[k] <= t) k++;
    steps.push([t, scope - k]);
  }
  return steps;
}

export function BurndownChart({cycle, stats, issues, height = 200}: {cycle: Cycle; stats: CycleStats; issues?: LinIssue[]; height?: number}) {
  const W = 560, H = height, padL = 8, padR = 8, padT = 10, padB = 22;
  const now = Date.now();
  const start = cycle.startsAt ? Date.parse(cycle.startsAt) : now - 6 * 864e5;
  const end = cycle.endsAt ? Date.parse(cycle.endsAt) : now + 864e5;
  const x0 = start - 864e5, x1 = end + 864e5;
  const maxY = Math.max(stats.scope, 4);
  const X = (t: number) => padL + ((t - x0) / (x1 - x0)) * (W - padL - padR);
  const Y = (v: number) => padT + (1 - v / maxY) * (H - padT - padB);
  const steps = useMemo(() => remainingSteps(issues, stats.scope, start, Math.min(now, end)),
    // Recomputed when the cycle's issues (stable per membership) or window change, not per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [issues, stats, start, end]);
  const last = steps.length ? steps[steps.length - 1][1] : stats.scope;
  const d = (t: number) => new Date(t).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  const hatchId = `hatch-${cycle.id}`;
  return (
    <svg className="lin-burndown" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Burndown: scope ${stats.scope}, started ${stats.started}, completed ${stats.completed}`}>
      <defs>
        <pattern id={hatchId} width="7" height="7" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
          <rect width="7" height="7" fill="transparent" />
          <line x1="0" y1="0" x2="0" y2="7" stroke="var(--line)" strokeWidth="2.4" />
        </pattern>
      </defs>
      {now > x0 && now < x1 && (
        <rect x={X(Math.max(now, start))} y={padT} width={Math.max(0, X(end) - X(Math.max(now, start)))} height={H - padT - padB} fill={`url(#${hatchId})`} />
      )}
      <line className="grid" x1={padL} y1={Y(stats.scope)} x2={W - padR} y2={Y(stats.scope)} />
      <line x1={X(start)} y1={Y(stats.scope)} x2={X(end)} y2={Y(0)} stroke="#8a94a6" strokeWidth="1.4" strokeDasharray="5 4" />
      {steps.length > 1
        ? <polyline fill="none" stroke="#8a94a6" strokeWidth="1.8" strokeLinejoin="round"
            points={steps.map(([t, v]) => `${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')} />
        : <line x1={X(start)} y1={Y(stats.scope)} x2={X(Math.min(now, end))} y2={Y(stats.scope)} stroke="#8a94a6" strokeWidth="1.8" />}
      <circle cx={X(start)} cy={Y(stats.scope)} r="3.4" fill="#e5b800" />
      <circle cx={X(Math.min(now, end))} cy={Y(last)} r="3.4" fill="#5e6ad2" />
      <text className="axis" x={X(start)} y={H - 6} textAnchor="middle">{d(start)}</text>
      <text className="axis" x={X(end)} y={H - 6} textAnchor="middle">{d(end)}</text>
    </svg>
  );
}

function Legend({stats}: {stats: CycleStats}) {
  const row = (color: string, label: string, n: number, pct?: number) => (
    <div className="lr"><span className="sw" style={{background: color}} />{label}
      <span className="n"><b>{n}</b>{pct !== undefined && <> · {pct}%</>}</span></div>
  );
  const pct = (n: number) => stats.scope ? Math.round((n / stats.scope) * 100) : 0;
  return (
    <div className="legend">
      {row('#8a94a6', 'Scope', stats.scope)}
      {row('#e5b800', 'Started', stats.started, pct(stats.started))}
      {row('#5e6ad2', 'Completed', stats.completed, pct(stats.completed))}
    </div>
  );
}

/* ---------------- overview rows ---------------- */

const CycleOverviewRow = React.memo(function CycleOverviewRow({cycle, state, stats, issues, onCycle, showChart}: {
  cycle: Cycle; state: CycleState; stats: CycleStats; issues: LinIssue[]; onCycle: (c: Cycle) => void; showChart: boolean;
}) {
  const name = cycle.name || `Cycle ${cycle.number ?? ''}`;
  const pct = stats.scope ? Math.round((stats.completed / stats.scope) * 100) : 0;
  const badge = state === 'current' ? 'Current' : state === 'upcoming' ? 'Upcoming' : state === 'completed' ? 'Completed' : 'Planned';
  return (
    <div className="lin-cycle-row">
      <div className={cx('lin-cycle-tmark', state === 'current' && 'now')}>
        <span>{fmtDate(cycle.startsAt).replace(' ', '\n')}</span><i />
      </div>
      <div className="lin-cycle-body">
        <div className="top">
          <button type="button" className="cname" onClick={() => onCycle(cycle)}>
            <CycleStateIcon state={state} />{name}
          </button>
          <span className="stats">
            <span className={cx('lin-badge', state === 'current' && 'current')}>{badge}</span>
            {state === 'completed'
              ? <><span className="lin-cap"><Ring frac={pct / 100} /> <b>{pct}%</b> success</span>
                  <span><b>{stats.completed}</b> completed</span></>
              : <span className="lin-cap"><Ring frac={stats.scope ? stats.started / stats.scope : 0} /> <b>{pct}%</b> of capacity</span>}
            <span><b>{stats.scope}</b> scope</span>
            <span className="range">{fmtRange(cycle.startsAt, cycle.endsAt)}</span>
            <button type="button" className="lin-cycle-detail-btn" onClick={() => onCycle(cycle)}>Cycle detail</button>
          </span>
        </div>
        {showChart && (
          <div className="lin-cycle-chart">
            <div className="chart"><BurndownChart cycle={cycle} stats={stats} issues={issues} height={180} /></div>
            <Legend stats={stats} />
          </div>
        )}
      </div>
    </div>
  );
});

function orderCycles(cycles: Cycle[]): {cycle: Cycle; state: CycleState}[] {
  const now = Date.now();
  const withState = cycles.map(c => ({cycle: c, state: cycleState(c, now)}));
  withState.sort((a, b) => (b.cycle.startsAt || '').localeCompare(a.cycle.startsAt || ''));
  // mark only the nearest future cycle as upcoming; the rest are planned
  let seenUpcoming = false;
  for (const w of [...withState].reverse()) {
    if (w.state === 'upcoming') {
      if (seenUpcoming) w.state = 'planned';
      seenUpcoming = true;
    }
  }
  return withState;
}

export function CycleOverviewRows({cycles, issuesByCycle, onCycle, emptyHint}: {
  cycles: Cycle[]; issuesByCycle: Map<string, LinIssue[]>; onCycle: (c: Cycle) => void; emptyHint?: string;
}) {
  const ordered = useMemo(() => orderCycles(cycles), [cycles]);
  const current = ordered.find(o => o.state === 'current');
  // Rows are memoized; keep their callback stable even when the parent passes a fresh closure.
  const onCycleRef = useRef(onCycle);
  onCycleRef.current = onCycle;
  const openCycle = useMemo(() => (c: Cycle) => onCycleRef.current(c), []);
  if (!ordered.length) {
    return (
      <div className="lin-empty">
        <CalendarDays size={30} strokeWidth={1.4} />
        <h2>No cycles yet</h2>
        <p>{emptyHint || 'Enable automatic cycles in team cycle settings to start planning work in iterations.'}</p>
      </div>
    );
  }
  return (
    <div className="lin-cycles-tl">
      <div className="lin-cycles-spine" />
      {ordered.map(({cycle, state}) => {
        const issues = issuesByCycle.get(cycle.id) || EMPTY;
        return (
          <CycleOverviewRow key={cycle.id} cycle={cycle} state={state} issues={issues}
            stats={cachedStats(issues)}
            onCycle={openCycle} showChart={current?.cycle.id === cycle.id} />
        );
      })}
    </div>
  );
}

/* ---------------- team cycles overview page ---------------- */

const EMPTY_CYCLES: Cycle[] = [];

export function LinearTeamCycles({team, teams, onCycle, onSettings, onNavigate, workspaceName, viewer}: {
  team?: Team; teams: Team[]; onCycle: (c: Cycle) => void; onSettings: () => void;
  revision?: number; onNavigate: (view: string, path?: string) => void; workspaceName: string; viewer: Viewer;
}) {
  // Rendered from the local store: no per-view fetch, live through deltas.
  const all = useCycles();
  const issuesByCycle = useIssuesByCycle();
  const teamId = team?.id;
  const cycles = useMemo(() => (all && teamId ? all.filter(c => c.team?.id === teamId) : EMPTY_CYCLES), [all, teamId]);
  const loading = all === null;
  const error = '';
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useDismiss(() => setMenuOpen(false));
  useTeamCycleUpkeep(teamId);
  useCycleHistory(cycles, 6);

  if (!team) return <section className="lin-page"><p className="lin-error" role="alert">Team not found in this workspace.</p></section>;

  return (
    <section className="lin-page" aria-label={`${team.name} cycles`}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={() => onNavigate('Team Home', `/workspace/team/${team.key.toLowerCase()}/home`)}>{workspaceName}</button>
          <span className="sep">›</span>
          <span className="current">Cycles</span>
        </nav>
        <div className="lin-head-actions">
          <FavStar kind="cycles" id={`team-cycles-${team.id}`} label={`${team.name} cycles`} />
          <span style={{position: 'relative'}} ref={menuRef}>
            <IconBtn label="Cycle options" title="More actions" onClick={() => setMenuOpen(v => !v)}><span style={{fontWeight: 700}}>···</span></IconBtn>
            {menuOpen && <LinMenu onClose={() => setMenuOpen(false)} className="lin-dd-panel" items={[
              {key: 'settings', label: 'Cycle settings', icon: <Settings2 size={14} />, onSelect: onSettings},
            ]} />}
          </span>
        </div>
      </header>
      <div className="lin-cycles-head"><h1>Cycles</h1></div>
      <div className="lin-cycles-wrap">
        <div className="lin-scroll" style={{flex: 1}}>
          {loading && <p className="lin-loading" role="status">Loading cycles…</p>}
          {error && <p className="lin-error" role="alert">{error}</p>}
          {!loading && !error && (
            <CycleOverviewRows cycles={cycles} issuesByCycle={issuesByCycle} onCycle={onCycle}
              emptyHint={`${team.name} has no cycles yet. Enable automatic cycles in cycle settings.`} />
          )}
          {!loading && !error && !!cycles.length && (
            <div style={{padding: '8px 28px'}}>
              <button type="button" className="lin-btn" onClick={onSettings}>Cycle settings</button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ---------------- workspace cycles (route fix) ---------------- */

export function LinearWorkspaceCycles({teams, onCycle, onSettings, onNavigate, workspaceName, viewer}: {
  teams: Team[]; onCycle: (c: Cycle) => void; onSettings: (team: Team) => void;
  revision?: number; onNavigate: (view: string, path?: string) => void; workspaceName: string; viewer: Viewer;
}) {
  const all = useCycles();
  const cycles = all || EMPTY_CYCLES;
  const issuesByCycle = useIssuesByCycle();
  const loading = all === null;
  const error = '';
  useCycleHistory(cycles, 12);
  const byTeam = useMemo(() => {
    const m = new Map<string, {team: Team; cycles: Cycle[]}>();
    for (const t of teams) m.set(t.id, {team: t, cycles: []});
    for (const c of cycles) {
      const tid = c.team?.id;
      if (tid && m.has(tid)) m.get(tid)!.cycles.push(c);
    }
    return [...m.values()].filter(g => g.cycles.length);
  }, [teams, cycles]);
  return (
    <section className="lin-page" aria-label="Workspace cycles">
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <span className="current">{workspaceName}</span><span className="sep">›</span><span className="current">Cycles</span>
        </nav>
      </header>
      <div className="lin-cycles-head"><h1>Cycles</h1></div>
      <div className="lin-scroll">
        {loading && <p className="lin-loading" role="status">Loading cycles…</p>}
        {error && <p className="lin-error" role="alert">{error}</p>}
        {!loading && !error && !byTeam.length && (
          <div className="lin-empty"><CalendarDays size={30} strokeWidth={1.4} /><h2>No cycles yet</h2>
            <p>Enable automatic cycles in a team's cycle settings to start planning work in iterations.</p></div>
        )}
        {byTeam.map(({team, cycles: tc}) => (
          <section key={team.id} style={{marginBottom: 8}}>
            <div style={{padding: '14px 28px 0', display: 'flex', alignItems: 'center', gap: 10}}>
              <b style={{fontSize: 13}}>{team.name}</b>
              <button type="button" className="lin-addrow" onClick={() => onSettings(team)}>Cycle settings</button>
            </div>
            <CycleOverviewRows cycles={tc} issuesByCycle={issuesByCycle} onCycle={onCycle} />
          </section>
        ))}
      </div>
    </section>
  );
}

/* ---------------- cycle detail ---------------- */

function download(filename: string, content: string, mime: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], {type: mime}));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

function cycleICS(cycle: Cycle): string {
  const f = (iso?: string | null) => (iso || '').replace(/[-:]/g, '').slice(0, 15) + 'Z';
  const name = cycle.name || `Cycle ${cycle.number ?? ''}`;
  return ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Hoja//Cycle//EN', 'BEGIN:VEVENT',
    `UID:${cycle.id}@hoja`, `DTSTART:${f(cycle.startsAt)}`, `DTEND:${f(cycle.endsAt)}`,
    `SUMMARY:${name}`, 'END:VEVENT', 'END:VCALENDAR'].join('\r\n');
}

function issuesCSV(issues: LinIssue[]): string {
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const head = ['Identifier', 'Title', 'Status', 'Priority', 'Assignee', 'Labels', 'Project', 'Cycle', 'Created', 'Updated'];
  const rows = issues.map(i => [i.identifier, i.title, i.state?.name, i.priorityLabel || i.priority,
    i.assignee?.name, (i.labels?.nodes || []).map(l => l.name).join('; '), i.project?.name,
    i.cycle?.name || i.cycle?.number, i.createdAt, i.updatedAt].map(esc).join(','));
  return [head.join(','), ...rows].join('\n');
}

function CycleOptionsMenu({cycle, issues, onError, onClose}: {
  cycle: Cycle; issues: LinIssue[]; onError: (message: string) => void; onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [dates, setDates] = useState(false);
  const [name, setName] = useState(cycle.name || '');
  const [description, setDescription] = useState(cycle.description || '');
  const [startsAt, setStartsAt] = useState((cycle.startsAt || '').slice(0, 10));
  const [endsAt, setEndsAt] = useState((cycle.endsAt || '').slice(0, 10));
  const busy = false;
  const error = '';
  const [fav, toggleFav] = useFavorite('cycles', cycle.id);
  // Optimistic: the edit shows at once; a rejected save rolls back and reports on the page.
  const save = (input: Record<string, unknown>) => {
    setEditing(false); setDates(false); onClose();
    updateCycleOptimistic(cycle.id, input).catch(e => onError((e as Error).message || 'Cycle update failed'));
  };
  if (editing || dates) {
    return (
      <div className="lin-popover" style={{padding: 14, width: 300}}>
        <b style={{fontSize: 13}}>{editing ? 'Edit cycle' : 'Change cycle dates'}</b>
        {editing && <>
          <label style={{display: 'grid', gap: 4, marginTop: 10, fontSize: 12}}>Name
            <input value={name} onChange={e => setName(e.target.value)} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
          <label style={{display: 'grid', gap: 4, marginTop: 8, fontSize: 12}}>Description
            <input value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional" style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
        </>}
        {dates && <>
          <label style={{display: 'grid', gap: 4, marginTop: 10, fontSize: 12}}>Start date
            <input type="date" value={startsAt} onChange={e => setStartsAt(e.target.value)} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
          <label style={{display: 'grid', gap: 4, marginTop: 8, fontSize: 12}}>End date
            <input type="date" value={endsAt} onChange={e => setEndsAt(e.target.value)} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
        </>}
        {error && <p className="lin-error" style={{margin: '8px 0'}}>{error}</p>}
        <div style={{display: 'flex', gap: 8, marginTop: 12}}>
          <button type="button" className="lin-btn primary" disabled={busy}
            onClick={() => save(editing ? {name: name.trim() || null, description: description.trim() || null} : {startsAt: startsAt || null, endsAt: endsAt || null})}>
            {busy ? 'Saving…' : 'Save'}</button>
          <button type="button" className="lin-btn" onClick={() => { setEditing(false); setDates(false); }}>Cancel</button>
        </div>
      </div>
    );
  }
  const cname = cycle.name || `Cycle ${cycle.number ?? ''}`;
  return (
    <LinMenu onClose={onClose} className="lin-dd-panel" items={[
      {key: 'edit', label: 'Edit cycle name and description…', onSelect: () => setEditing(true)},
      {key: 'dates', label: 'Change cycle dates', onSelect: () => setDates(true)},
      {key: 'sub', label: 'Subscribe to cycle notifications', disabled: true, onSelect: () => {}},
      {key: 'div1', divider: true},
      {key: 'fav', label: fav ? 'Remove from favorites' : 'Favorite', shortcut: 'ALT F', icon: fav ? <Check size={14} /> : undefined, onSelect: toggleFav},
      {key: 'copy', label: 'Copy link', icon: <Link2 size={14} />, onSelect: () => { void navigator.clipboard?.writeText(window.location.href); }},
      {key: 'cal', label: 'Subscribe to cycle calendar', icon: <CalendarDays size={14} />, onSelect: () => download(`${cname.replace(/\s+/g, '-').toLowerCase()}.ics`, cycleICS(cycle), 'text/calendar')},
      {key: 'csv', label: 'Export issues as CSV…', icon: <Download size={14} />, onSelect: () => download(`${cname.replace(/\s+/g, '-').toLowerCase()}-issues.csv`, issuesCSV(issues), 'text/csv')},
    ]} />
  );
}

/* Issue groups of a cycle. Memoized so unrelated store changes (which
 * re-render the page) do not re-render hundreds of rows; callbacks from the
 * parent are read through refs to keep the props stable. */
type IssueGroup = ReturnType<typeof groupLinIssues>[number];
const ISSUE_MIME = 'application/x-hoja-issue';
const CycleIssueGroups = React.memo(function CycleIssueGroups({groups, collapsed, setCollapsed, display, childCounts, onOpen, onMove}: {
  groups: IssueGroup[]; collapsed: string[]; setCollapsed: React.Dispatch<React.SetStateAction<string[]>>;
  display: LinDisplay; childCounts: Map<string, number>;
  onOpen: (issue: LinIssue) => void; onMove: (issueId: string, stateId: string) => void;
}) {
  const handlers = useRef({onOpen, onMove});
  handlers.current = {onOpen, onMove};
  const open = useMemo(() => (i: LinIssue) => handlers.current.onOpen(i), []);
  const noop = useMemo(() => () => {}, []);
  const [dropKey, setDropKey] = useState<string | null>(null);
  return (
    <>
      {groups.map(g => (
        <section key={g.key} className="lin-group"
          style={dropKey === g.key ? {outline: '1px dashed var(--accent)', outlineOffset: 2, borderRadius: 7} : undefined}
          onDragOver={g.state ? e => { if (e.dataTransfer.types.includes(ISSUE_MIME)) { e.preventDefault(); if (dropKey !== g.key) setDropKey(g.key); } } : undefined}
          onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropKey(k => (k === g.key ? null : k)); }}
          onDrop={g.state ? e => {
            e.preventDefault(); setDropKey(null);
            const id = e.dataTransfer.getData(ISSUE_MIME);
            if (id && g.state) handlers.current.onMove(id, g.state.id);
          } : undefined}>
          <header className="lin-group-head">
            <button type="button" className="chev" aria-label={`${collapsed.includes(g.key) ? 'Expand' : 'Collapse'} ${g.name}`}
              onClick={() => setCollapsed(c => c.includes(g.key) ? c.filter(x => x !== g.key) : [...c, g.key])}>
              <ChevronDown size={14} style={{transform: collapsed.includes(g.key) ? 'rotate(-90deg)' : undefined}} />
            </button>
            {g.state && <LinearStatusIcon state={g.state} size={13} />}
            <b>{g.name}</b><span className="count">{g.items.length}</span>
            <span className="grow" />
            <Tip label={`Create issue in ${g.name}`}>
              <button type="button" className="add" aria-label={`Create issue in ${g.name}`}><Plus size={14} /></button>
            </Tip>
          </header>
          {!collapsed.includes(g.key) && g.items.map(i => (
            <div key={i.id} draggable data-issue-id={i.id}
              onDragStart={e => { e.dataTransfer.setData(ISSUE_MIME, i.id); e.dataTransfer.effectAllowed = 'move'; }}
              onDragEnd={() => setDropKey(null)}>
              <IssueRow issue={i} display={display} selected={false}
                showCheckbox={false} onSelect={noop} onOpen={open} childCount={childCounts.get(i.id) || 0} />
            </div>
          ))}
        </section>
      ))}
    </>
  );
});

export function LinearCycleDetail({cycle: initial, team, teams, viewer, orgId, workspaceName, projects, labels, cycles, onBack, onSelect, onNavigate}: {
  cycle: Cycle; team?: Team; teams: Team[]; viewer: Viewer; orgId: string; workspaceName: string;
  projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  revision?: number; onBack: () => void; onSelect: (issue: LinIssue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  // The cycle and its issues come from the local store (live); `initial` only seeds the first paint.
  const allCycles = useCycles();
  const cycle = useMemo(() => {
    const live = allCycles?.find(c => c.id === initial.id);
    return live ? {...initial, ...live} : initial;
  }, [allCycles, initial]);
  const issuesByCycle = useIssuesByCycle();
  const issues = issuesByCycle.get(cycle.id) || EMPTY;
  const {ready} = useSyncStatus();
  const loading = !ready;
  const [error, setError] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [filters, setFilters] = useState<LinFilters>({...emptyFilters});
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [paneOpen, setPaneOpen] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [progressOpen, setProgressOpen] = useState(true);
  const [dimTab, setDimTab] = useState<'assignees' | 'labels' | 'priority' | 'projects'>('assignees');
  const [dimFilter, setDimFilter] = useState<{kind: string; id: string} | null>(null);
  const [dateOpen, setDateOpen] = useState(false);
  const [endDate, setEndDate] = useState((initial.endsAt || '').slice(0, 10));
  const savingDate = false;
  const menuRef = useDismiss(() => setMenuOpen(false));
  const dateRef = useDismiss(() => setDateOpen(false));
  const display = useMemo<LinDisplay>(() => ({...defaultDisplay, grouping: 'status'}), []);

  useEffect(() => { setEndDate((initial.endsAt || '').slice(0, 10)); setError(''); setMutationError(''); }, [initial.id]);
  useTeamCycleUpkeep(cycle.team?.id);
  // Closed issues older than the store window are fetched once and adopted.
  const historyScope = useMemo(() => [cycle], [cycle.id, cycle.startsAt]);
  useCycleHistory(historyScope, 1);

  const state = cycleState(cycle);
  const cname = cycle.name || `Cycle ${cycle.number ?? ''}`;
  const stats = cachedStats(issues);
  const childCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of issues) { const pid = i.parent?.id; if (pid) m.set(pid, (m.get(pid) || 0) + 1); }
    return m;
  }, [issues]);

  const scoped = useMemo(() => {
    let base = applyLinFilters(issues, filters);
    if (dimFilter) {
      if (dimFilter.kind === 'assignees') base = base.filter(i => (i.assignee?.id || 'none') === dimFilter.id);
      if (dimFilter.kind === 'labels') base = base.filter(i => i.labels?.nodes.some(l => l.id === dimFilter.id));
      if (dimFilter.kind === 'priority') base = base.filter(i => String(i.priority || 0) === dimFilter.id);
      if (dimFilter.kind === 'projects') base = base.filter(i => (i.project?.id || 'none') === dimFilter.id);
    }
    return orderLinIssues(base, display);
  }, [issues, filters, dimFilter]);
  const groups = useMemo(() => groupLinIssues(scoped, display, team?.states.nodes || []), [scoped, team]);

  const dimRows = useMemo(() => {
    const pct = (done: number, total: number) => total ? Math.round((done / total) * 100) : 0;
    if (dimTab === 'assignees') {
      const m = new Map<string, {id: string; name: string; total: number; done: number}>();
      for (const i of issues) {
        const id = i.assignee?.id || 'none', name = i.assignee?.name || 'No assignee';
        if (!m.has(id)) m.set(id, {id, name, total: 0, done: 0});
        const r = m.get(id)!; r.total++; if (i.state?.type === 'completed') r.done++;
      }
      return [...m.values()].map(r => ({key: r.id, icon: r.id === 'none' ? <span className="lin-avatar" style={{width: 20, height: 20}}>?</span> : <Avatar name={r.name} size={20} />, name: r.name, pct: pct(r.done, r.total), of: r.total}));
    }
    if (dimTab === 'labels') {
      const m = new Map<string, {id: string; label: IssueLabel; total: number; done: number}>();
      for (const i of issues) for (const l of i.labels?.nodes || []) {
        if (!m.has(l.id)) m.set(l.id, {id: l.id, label: l, total: 0, done: 0});
        const r = m.get(l.id)!; r.total++; if (i.state?.type === 'completed') r.done++;
      }
      return [...m.values()].map(r => ({key: r.id, icon: <i style={{width: 8, height: 8, borderRadius: 99, background: r.label.color || '#8a94a6'}} />, name: r.label.name, pct: pct(r.done, r.total), of: r.total}));
    }
    if (dimTab === 'priority') {
      const names = ['No priority', 'Urgent', 'High', 'Medium', 'Low'];
      const m = new Map<number, {total: number; done: number}>();
      for (const i of issues) {
        const p = i.priority || 0;
        if (!m.has(p)) m.set(p, {total: 0, done: 0});
        const r = m.get(p)!; r.total++; if (i.state?.type === 'completed') r.done++;
      }
      return [...m.entries()].map(([p, r]) => ({key: String(p), icon: <PriorityIcon priority={p} />, name: names[p], pct: pct(r.done, r.total), of: r.total}));
    }
    const m = new Map<string, {id: string; name: string; total: number; done: number}>();
    for (const i of issues) {
      const id = i.project?.id || 'none', name = i.project?.name || 'No project';
      if (!m.has(id)) m.set(id, {id, name, total: 0, done: 0});
      const r = m.get(id)!; r.total++; if (i.state?.type === 'completed') r.done++;
    }
    return [...m.values()].map(r => ({key: r.id, icon: <span>▣</span>, name: r.name, pct: pct(r.done, r.total), of: r.total}));
  }, [issues, dimTab]);

  const saveEndDate = () => {
    setDateOpen(false);
    setMutationError('');
    updateCycleOptimistic(cycle.id, {endsAt: endDate || null})
      .catch(e => setMutationError((e as Error).message || 'Could not change the end date'));
  };
  // Dropping a row on another status group moves it there at once; a rejected update rolls back.
  const moveToState = (issueId: string, stateId: string) => {
    const issue = issues.find(i => i.id === issueId);
    if (!issue || issue.state?.id === stateId) return;
    setMutationError('');
    updateIssueOptimistic(issueId, {stateId})
      .catch(e => setMutationError(`${issue.identifier}: ${(e as Error).message || 'Status change failed'}`));
  };

  return (
    <section className="lin-page" aria-label={cname}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={onBack}>{workspaceName}</button>
          <span className="sep">›</span>
          <button type="button" className="crumb-link" onClick={onBack}>Cycles</button>
          <span className="sep">›</span>
          <CycleStateIcon state={state} size={13} />
          <span className="current">{cname}</span>
          <ChevronDown size={13} color="var(--muted)" />
        </nav>
        <div className="lin-head-actions">
          <FavStar kind="cycles" id={cycle.id} label={cname} />
          <span style={{position: 'relative'}} ref={menuRef}>
            <IconBtn label="Cycle options" title="Cycle options" onClick={() => setMenuOpen(v => !v)}><span style={{fontWeight: 700}}>···</span></IconBtn>
            {menuOpen && <CycleOptionsMenu cycle={cycle} issues={issues} onError={setMutationError} onClose={() => setMenuOpen(false)} />}
          </span>
        </div>
      </header>

      <div className="lin-cdetail">
        <div className="lin-cdetail-main">
          <div className="lin-cdetail-count">{issues.length} issues{dimFilter && <> · filtered <button type="button" className="lin-addrow" onClick={() => setDimFilter(null)}>Clear</button></>}</div>
          <div className="lin-toolbar">
            <span className="spacer" />
            <span style={{position: 'relative'}}>
              <IconBtn label="Add filter" title="Add filter" active={filterOpen} onClick={() => { setFilterOpen(v => !v); setDisplayOpen(false); }}><ListFilter size={15} /></IconBtn>
              {filterOpen && <FilterMenu filters={filters} setFilters={setFilters} team={team} teams={teams}
                projects={projects} labels={labels} cycles={cycles} onClose={() => setFilterOpen(false)} />}
            </span>
            <span style={{position: 'relative'}}>
              <IconBtn label="Display options" title="Display options" active={displayOpen} onClick={() => { setDisplayOpen(v => !v); setFilterOpen(false); }}><SlidersHorizontal size={15} /></IconBtn>
              {displayOpen && <_DP display={{...display, layout: 'List'} as LinDisplay} setDisplay={() => {}} onClose={() => setDisplayOpen(false)} />}
            </span>
            <IconBtn label={paneOpen ? 'Close cycle details' : 'Open cycle details'} title="Toggle details pane"
              active={paneOpen} onClick={() => setPaneOpen(v => !v)}>
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M10 2.5v11" /></svg>
            </IconBtn>
          </div>
          <div className="lin-scroll">
            {loading && <p className="lin-loading" role="status">Loading cycle issues…</p>}
            {error && <p className="lin-error" role="alert">{error}</p>}
            {mutationError && <p className="lin-error" role="alert">{mutationError}</p>}
            {!loading && !error && (
              <div className="lin-list">
                <CycleIssueGroups groups={groups} collapsed={collapsed} setCollapsed={setCollapsed} display={display}
                  childCounts={childCounts} onOpen={onSelect} onMove={moveToState} />
                {!scoped.length && (
                  <div className="lin-empty"><h2>No issues in this cycle</h2><p>Issues added to this cycle will appear here.</p></div>
                )}
              </div>
            )}
          </div>
        </div>

        {paneOpen && (
          <aside className="lin-cpane" aria-label="Cycle details">
            <div className="cur">
              <span className="lin-badge current">{state === 'current' ? 'Current' : state === 'upcoming' ? 'Upcoming' : state === 'completed' ? 'Completed' : 'Planned'}</span>
              <span className="lin-badge">{fmtRange(cycle.startsAt, cycle.endsAt)}</span>
            </div>
            <h2><CycleStateIcon state={state} size={16} /> {cname}
              <span style={{flex: 1}} />
              <FavStar kind="cycles" id={cycle.id} label={cname} />
            </h2>
            <Tip label="Cycle documents are not available in Hoja yet">
              <button type="button" className="adddoc" disabled style={{opacity: .55, cursor: 'not-allowed'}}>
                <Plus size={14} /> Add document or link…
              </button>
            </Tip>
            <p className="datenote">Can only move start date for upcoming cycle</p>
            <span style={{position: 'relative'}} ref={dateRef}>
              <button type="button" className="datebtn" onClick={() => setDateOpen(v => !v)}>
                Change cycle end date: {fmtDate(cycle.endsAt)} <ChevronDown size={13} />
              </button>
              {dateOpen && (
                <div className="lin-popover" style={{left: 0, top: 'calc(100% + 6px)', padding: 12, width: 250}}>
                  <label style={{display: 'grid', gap: 6, fontSize: 12}}>End date
                    <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                      style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
                  <button type="button" className="lin-btn primary" style={{marginTop: 10, width: '100%', justifyContent: 'center'}}
                    disabled={savingDate} onClick={() => void saveEndDate()}>{savingDate ? 'Saving…' : 'Save'}</button>
                </div>
              )}
            </span>

            <button type="button" className="lin-progress-head" aria-expanded={progressOpen} onClick={() => setProgressOpen(v => !v)}>
              <ChevronDown size={14} style={{transform: progressOpen ? undefined : 'rotate(-90deg)'}} color="var(--muted)" /> Progress
            </button>
            {progressOpen && <>
              <Legend stats={stats} />
              <div style={{marginTop: 8}}><BurndownChart cycle={cycle} stats={stats} issues={issues} /></div>
            </>}

            <div className="lin-ctabs" role="tablist" aria-label="Cycle breakdown">
              {(['assignees', 'labels', 'priority', 'projects'] as const).map(t => (
                <button key={t} type="button" role="tab" aria-selected={dimTab === t} className="lin-ctab"
                  onClick={() => { setDimTab(t); setDimFilter(null); }}>
                  {t[0].toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>
            <div>
              {dimRows.map(r => (
                <button key={r.key} type="button" className="lin-statrow"
                  onClick={() => setDimFilter(f => f?.id === r.key && f.kind === dimTab ? null : {kind: dimTab, id: r.key})}
                  style={dimFilter?.id === r.key ? {background: 'var(--subtle)'} : undefined}>
                  {r.icon}<span className="nm">{r.name}</span>
                  <span className="pc"><Ring frac={r.pct / 100} /> {r.pct}% of {r.of}</span>
                </button>
              ))}
              {!dimRows.length && <p className="lin-note">Nothing to break down yet.</p>}
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
function pickTeamCycle(cycles: Cycle[], teamId: string, activeId: string | undefined, upcoming: boolean): Cycle | null {
  const mine = cycles.filter(c => c.team?.id === teamId).sort((a, b) => (a.number ?? 0) - (b.number ?? 0));
  const at = activeId ? mine.findIndex(c => c.id === activeId) : -1;
  if (!upcoming) return at >= 0 ? mine[at] : null;
  if (activeId && at < 0) return null;
  return mine[at + 1] || null;
}

export function LinearTeamCycle({team, teams, viewer, orgId, workspaceName, projects, labels, cycles, upcoming, onBack, onSelect, onNavigate}: {
  team?: Team; teams: Team[]; viewer: Viewer; orgId: string; workspaceName: string;
  projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  revision?: number; upcoming: boolean; onBack: () => void; onSelect: (issue: LinIssue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  const meta = useSyncMeta();
  const all = useCycles();
  const teamId = team?.id;
  // The active cycle id lives on the synced team; fall back to the team prop before metadata arrives.
  const activeId = (meta?.teams.nodes.find(t => t.id === teamId) || team)?.activeCycle?.id;
  const cycle = useMemo(() => (all && teamId ? pickTeamCycle(all, teamId, activeId, upcoming) : null),
    [all, teamId, activeId, upcoming]);
  const loading = all === null;
  const error = '';
  useTeamCycleUpkeep(teamId);
  if (!team) return <section className="lin-page"><p className="lin-error" role="alert">Team not found in this workspace.</p></section>;
  if (loading) return <section className="lin-page"><p className="lin-loading" role="status">Loading cycle…</p></section>;
  if (error) return <section className="lin-page"><p className="lin-error" role="alert">{error}</p></section>;
  if (!cycle) {
    return (
      <section className="lin-page">
        <header className="lin-head">
          <nav className="lin-crumb"><button type="button" className="crumb-link" onClick={onBack}>← Cycles</button></nav>
        </header>
        <div className="lin-empty"><CalendarDays size={30} strokeWidth={1.4} />
          <h2>No {upcoming ? 'upcoming' : 'current'} cycle</h2>
          <p>{team.name} has no {upcoming ? 'next' : 'active'} cycle in this workspace.</p></div>
      </section>
    );
  }
  return <LinearCycleDetail cycle={cycle} team={team} teams={teams} viewer={viewer} orgId={orgId}
    workspaceName={workspaceName} projects={projects} labels={labels} cycles={cycles}
    onBack={onBack} onSelect={onSelect} onNavigate={onNavigate} />;
}

/* ---------------- cycle settings ---------------- */

type Cadence = {enabled: boolean; weekday?: number | null; timezone: string; policyVersion: number};
type AutoFlags = {started: boolean; completed: boolean; backlog: boolean};
/* Last known settings per team: a revisit renders at once and revalidates in the background. */
const settingsCache = new Map<string, {cadence?: Cadence; auto?: AutoFlags}>();

export function LinearTeamCycleSettings({team, onBack, admin}: {
  team?: Team; onBack: () => void; admin?: boolean;
}) {
  const cached = team ? settingsCache.get(team.id) : undefined;
  const [saved, setSaved] = useState<Cadence | null>(cached?.cadence || null);
  const [weekday, setWeekday] = useState(cached?.cadence?.weekday == null ? '' : String(cached.cadence.weekday));
  const [autoStarted, setAutoStarted] = useState<boolean | null>(cached?.auto?.started ?? null);
  const [autoCompleted, setAutoCompleted] = useState<boolean | null>(cached?.auto?.completed ?? null);
  const [autoBacklog, setAutoBacklog] = useState<boolean | null>(cached?.auto?.backlog ?? null);
  const autoBusy = false;
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(!cached?.cadence);
  const [error, setError] = useState('');
  // Server-confirmed automation flags (rollback target) and the newest save's sequence number.
  const confirmedAuto = useRef<AutoFlags | null>(cached?.auto || null);
  const autoSeq = useRef(0);
  const teamVersion = useEntityVersion(['team'], [team?.id]);
  const remember = (teamId: string, next: {cadence?: Cadence; auto?: AutoFlags}) =>
    settingsCache.set(teamId, {...settingsCache.get(teamId), ...next});
  const showAuto = (f: AutoFlags) => { setAutoStarted(f.started); setAutoCompleted(f.completed); setAutoBacklog(f.backlog); };
  useEffect(() => {
    if (!team) return;
    let live = true;
    const teamId = team.id;
    const prior = settingsCache.get(teamId);
    setLoading(!prior?.cadence); setError('');
    gql(`query($id:String!){teamCycleCadence(teamId:$id){enabled weekday timezone policyVersion}}`, {id: teamId})
      .then(d => {
        remember(teamId, {cadence: d.teamCycleCadence});
        if (live) { setSaved(d.teamCycleCadence); setWeekday(d.teamCycleCadence.weekday == null ? '' : String(d.teamCycleCadence.weekday)); }
      })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    gql(`query($id:String!){team(id:$id){autoAddStarted autoAddCompleted autoAddBacklog}}`, {id: teamId})
      .then(d => {
        if (!d.team) return;
        const flags = {started: d.team.autoAddStarted !== false, completed: d.team.autoAddCompleted !== false, backlog: d.team.autoAddBacklog !== false};
        remember(teamId, {auto: flags});
        // Do not clobber a toggle the user made while this was loading.
        if (live && autoSeq.current === 0) { confirmedAuto.current = flags; showAuto(flags); }
      })
      .catch(() => { /* flags stay null; card shows loading state */ });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team?.id, teamVersion]);
  const enable = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!team || weekday === '' || busy) return;
    const previous = saved;
    // Optimistic: show the schedule as enabled at once; roll back if the server refuses.
    setSaved({enabled: true, weekday: Number(weekday), timezone: previous?.timezone || 'UTC', policyVersion: previous?.policyVersion ?? 1});
    setBusy(true); setError('');
    try {
      const d = await gql(`mutation($id:String!,$weekday:Int!){teamCycleCadenceEnable(teamId:$id,weekday:$weekday){enabled weekday timezone policyVersion}}`,
        {id: team.id, weekday: Number(weekday)});
      setSaved(d.teamCycleCadenceEnable);
      remember(team.id, {cadence: d.teamCycleCadenceEnable});
      void getSync()?.pull(); // generated cycles arrive with the metadata
    } catch (e) { setSaved(previous); setError((e as Error).message); } finally { setBusy(false); }
  };
  const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  // Toggles apply instantly; each save carries all three flags, so the newest one wins.
  const saveAuto = async (started: boolean, completed: boolean, backlog: boolean) => {
    if (!team || !admin) return;
    const seq = ++autoSeq.current;
    const teamId = team.id;
    showAuto({started, completed, backlog});
    setError('');
    try {
      const d = await gql(
        `mutation($id:String!,$input:TeamUpdateInput!){teamUpdate(id:$id,input:$input){success team{id autoAddStarted autoAddCompleted autoAddBacklog}}}`,
        {id: teamId, input: {autoAddStarted: started, autoAddCompleted: completed, autoAddBacklog: backlog}});
      if (!d.teamUpdate?.success) throw new Error('Could not save cycle automations.');
      const flags = {started: d.teamUpdate.team.autoAddStarted !== false, completed: d.teamUpdate.team.autoAddCompleted !== false, backlog: d.teamUpdate.team.autoAddBacklog !== false};
      confirmedAuto.current = flags;
      remember(teamId, {auto: flags});
      if (seq === autoSeq.current) showAuto(flags);
    } catch (e) {
      if (seq === autoSeq.current) {
        if (confirmedAuto.current) showAuto(confirmedAuto.current);
        setError((e as Error).message || 'Could not save cycle automations.');
      }
    }
  };
  return (
    <section className="lin-page"><div className="lin-scroll"><div className="lin-csettings">
      <button type="button" className="lin-addrow" onClick={onBack}>← Cycles</button>
      <h1 style={{marginTop: 8}}>{team?.name || 'Team'} cycle settings</h1>
      <p className="desc">Configure how {team?.name || 'this team'} plans work in iterations.</p>
      {!team ? <p className="lin-error" role="alert">Team not found in this workspace.</p>
      : loading ? <p className="lin-loading" role="status">Loading cycle settings…</p>
      : <>
        {error && <p className="lin-error" role="alert">{error}</p>}
        <div className="card">
          <h2>Automatic cycles</h2>
          {saved?.enabled
            ? <p>Enabled · {saved.timezone} · cycles start each {days[saved.weekday ?? 0]}. The current and next two cycles are generated automatically; completed cycles close on their end date.</p>
            : <p>When enabled, Hoja generates one-week cycles for this team: a current cycle and two upcoming cycles, rolling forward automatically.</p>}
          {!saved?.enabled && (
            <form onSubmit={enable}>
              <label className="f">Start day
                <select value={weekday} onChange={e => setWeekday(e.target.value)} required>
                  <option value="">Choose weekday</option>
                  {days.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
              </label>
              {!admin && <p className="lin-note" role="status">Only workspace admins can enable team cycles.</p>}
              <button type="submit" className="lin-btn primary" disabled={!admin || busy || weekday === ''}>
                {busy ? 'Enabling…' : 'Enable automatic cycles'}</button>
            </form>
          )}
        </div>
        <div className="card">
          <h2>Cycle length</h2>
          <p>One-week cycles, no cooldown, two upcoming.</p>
          <div className="row2">
            <label className="f">Length
              <select value="1" disabled><option value="1">1 week</option></select></label>
            <label className="f">Cooldown
              <select value="0" disabled><option value="0">None</option></select></label>
          </div>
          <p className="lin-note">Other lengths and cooldowns are not supported by this server yet.</p>
        </div>
        <div className="card">
          <h2>Auto-complete</h2>
          <p>Cycles complete automatically on their end date. Incomplete issues roll into the next cycle.</p>
          <p className="lin-note">Changing or disabling the schedule is not available in this release.</p>
        </div>
        <div className="card">
          <h2>Cycle automations</h2>
          <p>Automatically add issues without a cycle into the current cycle. Issues already assigned to a cycle are never moved.</p>
          {autoStarted === null || autoCompleted === null || autoBacklog === null
            ? <p className="lin-loading" role="status">Loading automations…</p>
            : <>
              <label className="lin-auto-row">
                <input type="checkbox" className="lin-check" checked={autoStarted}
                  disabled={!admin || autoBusy}
                  onChange={e => { void saveAuto(e.target.checked, autoCompleted, autoBacklog); }} />
                <span>Auto-add <b>started</b> issues to the current cycle</span>
              </label>
              <label className="lin-auto-row">
                <input type="checkbox" className="lin-check" checked={autoCompleted}
                  disabled={!admin || autoBusy}
                  onChange={e => { void saveAuto(autoStarted, e.target.checked, autoBacklog); }} />
                <span>Auto-add <b>completed</b> issues to the current cycle</span>
              </label>
              <label className="lin-auto-row">
                <input type="checkbox" className="lin-check" checked={autoBacklog}
                  disabled={!admin || autoBusy}
                  onChange={e => { void saveAuto(autoStarted, autoCompleted, e.target.checked); }} />
                <span>Auto-add <b>backlog</b> issues to the current cycle</span>
              </label>
              {!admin && <p className="lin-note" role="status">Only workspace admins can change cycle automations.</p>}
            </>}
        </div>
      </>}
    </div></div></section>
  );
}
