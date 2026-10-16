import {useEffect, useMemo, useState, useSyncExternalStore} from 'react';
import type {Cycle, Issue, IssueLabel, Project, Team, Viewer} from './main';
import type {ChangeEvent} from './realtime';

/* Local-first issue store.
 *
 * One /sync/bootstrap fills an IndexedDB snapshot (open issues + recently
 * closed ones, columnar rows). Later loads render that snapshot immediately and
 * catch up with /sync/delta from its seq. Realtime events only trigger a delta
 * pull. Optimistic edits are overlays that win over server rows until the
 * server has acknowledged them, so a concurrent delta never flickers them back.
 */

export type SyncMeta = {
  viewer: Viewer;
  workspace: {id: string; name: string; urlKey?: string};
  teams: {nodes: Team[]};
  projects: {nodes: Project[]; pageInfo?: {hasNextPage: boolean; endCursor?: string | null}};
  cycles: {nodes: Cycle[]};
  issueLabels: {nodes: IssueLabel[]};
  users: [string, string | null][];
};
export type SyncView = {meta: SyncMeta; issues: Issue[]; closedCutoff: string; reset: boolean};
export type SyncHandle = {
  pull: () => Promise<void>;
  reset: () => Promise<void>;
  forget: () => Promise<void>;
  stop: () => void;
  /** Overlay a local edit; call the returned function with whether the server accepted it. */
  patch: (issueId: string, patch: Partial<Issue>) => (accepted: boolean) => void;
  /** Patch derived from an IssueUpdateInput (ids resolved against workspace metadata). */
  patchFromInput: (input: Record<string, unknown>) => Partial<Issue>;
  /** Add issues fetched outside the snapshot (older closed pages) so deltas keep them fresh. */
  adopt: (issues: Issue[]) => void;
  /** Remove issues locally (archive/delete). settle(true, ids?) keeps them hidden until a pull
   *  has removed them; settle(false, ids?) brings them back. ids defaults to all hidden here. */
  hide: (ids: string[]) => (accepted: boolean, ids?: string[]) => void;
};

type Row = unknown[];
type Snapshot = {
  v: number; scope: string; seq: number; etag: string | null; closedCutoff: string;
  meta: SyncMeta; columns: string[]; rows: Record<string, Row>;
};

const PRIORITY_LABELS = ['No priority', 'Urgent', 'High', 'Medium', 'Low'];
const DB_NAME = 'hoja-sync';
const STORE = 'snapshots';
const KEY = 'current';

/* ---------------- shared store + hooks (the API pages build on) ----------------
 *
 * Pages never fetch the open-issue set themselves. They read it here:
 *   useIssues()                 every synced issue (open + recently closed + adopted), newest first
 *   useIssue(idOrIdentifier)    one synced issue, or undefined if outside the snapshot
 *   useSyncMeta()               viewer/workspace/teams(states, members)/projects/cycles/labels/users
 *   useSyncStatus()             {ready, closedCutoff} — closedCutoff: older closed issues are NOT synced
 *   getSync()                   the live SyncHandle: patch/patchFromInput (optimistic), adopt, pull
 *   useEntityVersion(entities, ids?)  counter bumped when a realtime event for those entities
 *                               (optionally only those entity ids) arrives — use as an effect dep
 *                               instead of the global `revision` prop.
 *   onEntityChange(entities, cb)      imperative form of the above; returns unsubscribe.
 *   useTeamIssues(teamId)       issues of one team, from an index shared by every caller.
 * Store updates are coalesced per microtask, so N patches/settles in a loop re-render once.
 * Entity names come from change_event: issue, comment, attachment, project, projectUpdate,
 * initiative, cycle, label, workflowState, team, teamMember, notification, user, member.
 */

type StoreState = {issues: Issue[]; meta: SyncMeta | null; ready: boolean; closedCutoff: string};
let storeState: StoreState = {issues: [], meta: null, ready: false, closedCutoff: ''};
let currentHandle: SyncHandle | null = null;
const storeListeners = new Set<() => void>();
const subscribeStore = (cb: () => void) => { storeListeners.add(cb); return () => { storeListeners.delete(cb); }; };
function setStore(next: Partial<StoreState>) {
  storeState = {...storeState, ...next};
  for (const cb of storeListeners) cb();
}

export function getSync(): SyncHandle | null { return currentHandle; }
export function useIssues(): Issue[] { return useSyncExternalStore(subscribeStore, () => storeState.issues); }
export function useSyncMeta(): SyncMeta | null { return useSyncExternalStore(subscribeStore, () => storeState.meta); }
export function useSyncStatus(): {ready: boolean; closedCutoff: string} {
  const ready = useSyncExternalStore(subscribeStore, () => storeState.ready);
  const closedCutoff = useSyncExternalStore(subscribeStore, () => storeState.closedCutoff);
  return useMemo(() => ({ready, closedCutoff}), [ready, closedCutoff]);
}
// One team index per issues array, shared by every useTeamIssues caller.
const teamIndexCache = new WeakMap<Issue[], Map<string, Issue[]>>();
function teamIndex(issues: Issue[]): Map<string, Issue[]> {
  let index = teamIndexCache.get(issues);
  if (!index) {
    index = new Map();
    for (const issue of issues) {
      const key = issue.team?.id || '';
      const list = index.get(key);
      if (list) list.push(issue); else index.set(key, [issue]);
    }
    teamIndexCache.set(issues, index);
  }
  return index;
}
const NO_ISSUES: Issue[] = [];
export function useTeamIssues(teamId: string | null | undefined): Issue[] {
  const issues = useIssues();
  return useMemo(() => (teamId ? teamIndex(issues).get(teamId) || NO_ISSUES : NO_ISSUES), [issues, teamId]);
}

export function useIssue(idOrIdentifier: string | null | undefined): Issue | undefined {
  const issues = useIssues();
  return useMemo(() => (idOrIdentifier ? issues.find(i => i.id === idOrIdentifier || i.identifier === idOrIdentifier) : undefined),
    [issues, idOrIdentifier]);
}

type EntityListener = {entities: Set<string>; ids?: Set<string>; cb: (events: ChangeEvent[]) => void};
const entityListeners = new Set<EntityListener>();
/** Called by the app shell with every realtime frame. */
export function publishChangeEvents(events: ChangeEvent[]) {
  for (const l of entityListeners) {
    const hits = events.filter(e => l.entities.has(e.entity) && (!l.ids || l.ids.has(e.entityId)));
    if (hits.length) l.cb(hits);
  }
}
export function onEntityChange(entities: string[], cb: (events: ChangeEvent[]) => void, ids?: string[]): () => void {
  const l: EntityListener = {entities: new Set(entities), ids: ids ? new Set(ids) : undefined, cb};
  entityListeners.add(l);
  return () => { entityListeners.delete(l); };
}
export function useEntityVersion(entities: string[], ids?: (string | null | undefined)[], debounceMs = 80): number {
  const [version, setVersion] = useState(0);
  const entityKey = entities.join(',');
  const idKey = ids ? ids.filter(Boolean).join(',') : '';
  useEffect(() => {
    let timer: number | undefined;
    const off = onEntityChange(entityKey.split(','), () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setVersion(v => v + 1), debounceMs);
    }, ids ? idKey.split(',').filter(Boolean) : undefined);
    return () => { window.clearTimeout(timer); off(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityKey, idKey, debounceMs]);
  return version;
}

/* ---------------- IndexedDB (best effort: private windows may refuse) ---------------- */

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function idb<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest): Promise<T | undefined> {
  try {
    const db = await openDb();
    return await new Promise<T>((resolve, reject) => {
      const req = run(db.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result as T);
      req.onerror = () => reject(req.error);
    });
  } catch {
    return undefined;
  }
}
const loadSnapshot = () => idb<Snapshot>('readonly', s => s.get(KEY));
const saveSnapshot = (snap: Snapshot) => idb('readwrite', s => s.put(snap, KEY));
const clearSnapshot = () => idb('readwrite', s => s.delete(KEY));

/* ---------------- hydration ---------------- */

type Lookups = {
  states: Map<string, NonNullable<Issue['state']>>;
  teams: Map<string, NonNullable<Issue['team']>>;
  projects: Map<string, {id: string; name: string}>;
  cycles: Map<string, {id: string; name?: string; number?: number}>;
  labels: Map<string, IssueLabel>;
  users: Map<string, {id: string; name: string}>;
};

function lookups(meta: SyncMeta): Lookups {
  const states = new Map<string, NonNullable<Issue['state']>>();
  const teams = new Map<string, NonNullable<Issue['team']>>();
  for (const t of meta.teams.nodes) {
    teams.set(t.id, {id: t.id, key: t.key, name: t.name});
    for (const s of t.states.nodes) states.set(s.id, s);
  }
  return {
    states, teams,
    projects: new Map(meta.projects.nodes.map(p => [p.id, {id: p.id, name: p.name}])),
    cycles: new Map(meta.cycles.nodes.map(c => [c.id, {id: c.id, name: c.name, number: c.number}])),
    labels: new Map(meta.issueLabels.nodes.map(l => [l.id, l])),
    users: new Map(meta.users.map(([id, name]) => [id, {id, name: name || ''}])),
  };
}

type Col = (row: Row) => unknown;
function columnReader(columns: string[]): (name: string) => Col {
  const at = new Map(columns.map((c, i) => [c, i]));
  return name => { const i = at.get(name); return i === undefined ? () => undefined : row => row[i]; };
}

function hydrate(row: Row, col: (name: string) => Col, lk: Lookups, parentRow: Row | undefined): Issue {
  const id = (k: string) => col(k)(row) as string | null;
  const priority = (col('priority')(row) as number | null) ?? 0;
  const sid = id('stateId'), tid = id('teamId'), pid = id('projectId'), aid = id('assigneeId');
  const cid = id('creatorId'), cyc = id('cycleId');
  return {
    id: id('id')!, identifier: id('identifier')!, title: id('title')!,
    createdAt: id('createdAt') || undefined, updatedAt: id('updatedAt') || undefined,
    stateChangedAt: id('stateChangedAt') || undefined, dueDate: id('dueDate') || undefined,
    priority, priorityLabel: PRIORITY_LABELS[priority] || 'No priority',
    state: sid ? lk.states.get(sid) : undefined,
    team: tid ? lk.teams.get(tid) : undefined,
    project: pid ? lk.projects.get(pid) : undefined,
    assignee: aid ? lk.users.get(aid) : undefined,
    creator: cid ? lk.users.get(cid) : undefined,
    cycle: cyc ? lk.cycles.get(cyc) : undefined,
    labels: {nodes: ((col('labelIds')(row) as string[]) || []).map(l => lk.labels.get(l)).filter(Boolean) as IssueLabel[]},
    subscribers: {nodes: ((col('subscriberIds')(row) as string[]) || []).map(u => ({id: u}))},
    estimate: (col('estimate')(row) as number | null) ?? null,
    parent: parentRow
      ? {id: parentRow[0] as string, identifier: col('identifier')(parentRow) as string, title: col('title')(parentRow) as string}
      : (id('parentId') ? {id: id('parentId')!, identifier: '', title: ''} : null),
  } as Issue;
}

/* Issue → row, for adopting issues loaded through GraphQL pages. */
function dehydrate(issue: Issue, columns: string[]): Row {
  const values: Record<string, unknown> = {
    id: issue.id, identifier: issue.identifier, title: issue.title, priority: issue.priority,
    createdAt: issue.createdAt, updatedAt: issue.updatedAt, stateChangedAt: issue.stateChangedAt,
    dueDate: issue.dueDate, estimate: (issue as {estimate?: number | null}).estimate,
    parentId: (issue as {parent?: {id: string} | null}).parent?.id, stateId: issue.state?.id, teamId: issue.team?.id, projectId: issue.project?.id,
    assigneeId: issue.assignee?.id, creatorId: issue.creator?.id, cycleId: issue.cycle?.id,
    labelIds: issue.labels?.nodes.map(l => l.id) || [], subscriberIds: issue.subscribers?.nodes.map(s => s.id) || [],
  };
  return columns.map(c => values[c] ?? null);
}

/* ---------------- client ---------------- */

export function startSync(opts: {
  headers: () => Record<string, string>;
  onView: (view: SyncView) => void;
  onError: (error: Error) => void;
}): SyncHandle {
  let snap: Snapshot | null = null;
  let stopped = false;
  let pulling: Promise<void> | null = null;
  let pullAgain = false;
  let saveTimer: number | undefined;
  let lk: Lookups | null = null;
  let lkMeta: SyncMeta | null = null;
  // Reuse hydrated objects while their row is unchanged so React can skip re-rendering them.
  const hydrated = new Map<string, {row: Row; parentRow: Row | undefined; issue: Issue}>();
  const overlays = new Map<string, Partial<Issue>[]>();
  const hidden = new Map<string, number>(); // id → number of outstanding hide() calls
  let emitQueued = false;

  const fetchJson = async (url: string, extra: Record<string, string> = {}) => {
    const res = await fetch(url, {credentials: 'include', headers: {...opts.headers(), ...extra}});
    if (res.status === 304) return {status: 304, body: null, etag: res.headers.get('etag')};
    if (!res.ok) throw new Error(res.status === 401 ? 'Authentication required' : `Sync failed (${res.status})`);
    return {status: res.status, body: await res.json(), etag: res.headers.get('etag')};
  };

  const persist = () => {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => { if (snap && !stopped) void saveSnapshot(snap); }, 250);
  };

  // Coalesce bursts (bulk patches, many settles, delta + overlay) into one rebuild per microtask.
  const requestEmit = () => {
    if (emitQueued) return;
    emitQueued = true;
    queueMicrotask(() => { emitQueued = false; emit(false); });
  };

  const emit = (reset: boolean) => {
    if (!snap || stopped) return;
    if (lkMeta !== snap.meta) { lk = lookups(snap.meta); lkMeta = snap.meta; hydrated.clear(); }
    const col = columnReader(snap.columns);
    const issues: Issue[] = [];
    const parentAt = snap.columns.indexOf('parentId');
    for (const [id, row] of Object.entries(snap.rows)) {
      if (hidden.has(id)) continue;
      const pid = parentAt >= 0 ? row[parentAt] as string | null : null;
      const parentRow = pid ? snap.rows[pid] : undefined;
      let entry = hydrated.get(id);
      if (!entry || entry.row !== row || entry.parentRow !== parentRow) {
        entry = {row, parentRow, issue: hydrate(row, col, lk!, parentRow)};
        hydrated.set(id, entry);
      }
      const layers = overlays.get(id);
      issues.push(layers ? Object.assign({}, entry.issue, ...layers) : entry.issue);
    }
    for (const id of hydrated.keys()) if (!(id in snap.rows)) hydrated.delete(id);
    issues.sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''));
    setStore({issues, meta: snap.meta, ready: true, closedCutoff: snap.closedCutoff});
    opts.onView({meta: snap.meta, issues, closedCutoff: snap.closedCutoff, reset});
  };

  const bootstrap = async () => {
    const res = await fetchJson('/sync/bootstrap', snap?.etag ? {'if-none-match': snap.etag} : {});
    if (stopped) return;
    if (res.status === 304 && snap) return; // the cached snapshot is exactly current
    const b = res.body;
    const rows: Record<string, Row> = {};
    for (const r of b.issues as Row[]) rows[r[0] as string] = r;
    snap = {v: b.v, scope: b.scope, seq: b.seq, etag: res.etag, closedCutoff: b.closedCutoff,
            meta: b.meta, columns: b.columns, rows};
    persist();
    emit(true);
  };

  const applyDelta = async () => {
    const s = snap!;
    const res = await fetchJson(`/sync/delta?after=${s.seq}&scope=${encodeURIComponent(s.scope)}`);
    if (stopped || snap !== s) return;
    const d = res.body;
    if (d.reload) { await bootstrap(); return; }
    if (!d.issues.length && !d.removed.length && !d.meta && d.seq === s.seq) return;
    const at = new Map((d.columns as string[]).map((c, i) => [c, i]));
    const rows = {...s.rows};
    for (const r of d.issues as Row[]) {
      // Columns are server-ordered; remap if the server's column set ever differs from the cached one.
      rows[r[0] as string] = d.columns.join() === s.columns.join() ? r : s.columns.map(c => (at.has(c) ? r[at.get(c)!] : null));
    }
    for (const id of d.removed as string[]) delete rows[id];
    snap = {...s, seq: d.seq, etag: null, rows, meta: d.meta ? d.meta : s.meta};
    persist();
    requestEmit();
  };

  const pull = (): Promise<void> => {
    if (stopped) return Promise.resolve();
    if (pulling) { pullAgain = true; return pulling; }
    pulling = (async () => {
      try {
        do {
          pullAgain = false;
          if (snap) await applyDelta(); else await bootstrap();
        } while (pullAgain && !stopped);
      } catch (e) {
        if (!stopped) opts.onError(e as Error);
      } finally {
        pulling = null;
      }
    })();
    return pulling;
  };

  void (async () => {
    const cached = await loadSnapshot();
    if (stopped) return;
    if (cached && cached.v === 1 && cached.rows && cached.meta) { snap = cached; emit(true); }
    await pull();
  })();

  const handle: SyncHandle = {
    pull,
    reset: async () => { snap = null; hydrated.clear(); setStore({issues: [], meta: null, ready: false}); await clearSnapshot(); await pull(); },
    forget: async () => { snap = null; hydrated.clear(); window.clearTimeout(saveTimer); setStore({issues: [], meta: null, ready: false, closedCutoff: ''}); await clearSnapshot(); },
    stop: () => { stopped = true; window.clearTimeout(saveTimer); if (currentHandle === handle) currentHandle = null; },
    patch: (issueId, patch) => {
      const layers = overlays.get(issueId) || [];
      layers.push(patch);
      overlays.set(issueId, layers);
      requestEmit();
      return accepted => {
        const drop = () => {
          const current = overlays.get(issueId)?.filter(p => p !== patch) || [];
          if (current.length) overlays.set(issueId, current); else overlays.delete(issueId);
          requestEmit();
        };
        // Keep an accepted edit visible until a pull has brought the server's version.
        if (accepted) void pull().finally(drop); else drop();
      };
    },
    patchFromInput: input => {
      if (!snap) return {};
      if (lkMeta !== snap.meta) { lk = lookups(snap.meta); lkMeta = snap.meta; }
      const out: Partial<Issue> = {};
      const has = (k: string) => Object.prototype.hasOwnProperty.call(input, k);
      const ref = <T,>(map: Map<string, T>, k: string) => (input[k] ? map.get(input[k] as string) : undefined);
      if (has('title')) out.title = input.title as string;
      if (has('description')) out.description = (input.description as string) || undefined;
      if (has('dueDate')) out.dueDate = (input.dueDate as string) || undefined;
      if (has('priority')) { out.priority = Number(input.priority); out.priorityLabel = PRIORITY_LABELS[out.priority]; }
      if (has('stateId')) out.state = ref(lk!.states, 'stateId');
      if (has('assigneeId')) out.assignee = ref(lk!.users, 'assigneeId');
      if (has('projectId')) out.project = ref(lk!.projects, 'projectId');
      if (has('cycleId')) out.cycle = ref(lk!.cycles, 'cycleId');
      if (has('labelIds')) out.labels = {nodes: ((input.labelIds as string[]) || []).map(l => lk!.labels.get(l)).filter(Boolean) as IssueLabel[]};
      return out;
    },
    adopt: issues => {
      if (!snap || !issues.length) return;
      const rows = {...snap.rows};
      for (const issue of issues) if (!(issue.id in rows)) rows[issue.id] = dehydrate(issue, snap.columns);
      snap = {...snap, rows};
      persist();
      requestEmit();
    },
    hide: ids => {
      for (const id of ids) hidden.set(id, (hidden.get(id) || 0) + 1);
      requestEmit();
      let open = new Set(ids);
      return (accepted, which) => {
        const done = (which || [...open]).filter(id => open.has(id));
        open = new Set([...open].filter(id => !done.includes(id)));
        const unhide = () => {
          for (const id of done) {
            const n = (hidden.get(id) || 1) - 1;
            if (n > 0) hidden.set(id, n); else hidden.delete(id);
          }
          requestEmit();
        };
        // Accepted removals stay hidden until a pull has dropped their rows.
        if (accepted) void pull().finally(unhide); else unhide();
      };
    },
  };
  currentHandle = handle;
  return handle;
}
