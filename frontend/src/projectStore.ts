import {useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react';
import type {Issue, Project} from './main';
import {getSync, useEntityVersion, useIssues, useSyncMeta, useSyncStatus} from './sync';

// Re-exported under page-local names so main.tsx can import them without touching its shared sync import line.
export {useSyncMeta as useProjectSyncMeta, useEntityVersion as useProjectEntityVersion};

/* Local-first helpers for the projects / initiatives / pulse pages.
 *
 * Projects come from the sync metadata (first 250, re-sent by /sync/delta when a
 * project changes) plus any project fetched beyond it. Optimistic edits are
 * overlays that win until a pull has brought the server's version, mirroring
 * SyncHandle.patch for issues. Issues per project are grouped once per store
 * emit and shared by every page. Server-only lists (project updates, initiatives)
 * live in a stale-while-revalidate page cache keyed per view, so revisiting a
 * page renders the previous rows at once and refreshes them in the background.
 */

type Listener = () => void;
const listeners = new Set<Listener>();
let version = 0;
const bump = () => { version++; for (const l of listeners) l(); };
const subscribe = (cb: Listener) => { listeners.add(cb); return () => { listeners.delete(cb); }; };
const getVersion = () => version;

const overlays = new Map<string, Partial<Project>[]>();
const pendingCreates = new Map<string, Project>();
const known = new Map<string, Project>(); // fetched outside the metadata block (pagination, mutation results)
const hidden = new Set<string>();

export type ProjectNotice = {message: string; retry?: () => void};
let notice: ProjectNotice | null = null;
export function setProjectNotice(next: ProjectNotice | null) { notice = next; bump(); }
export function useProjectNotice(): ProjectNotice | null {
  useSyncExternalStore(subscribe, getVersion);
  return notice;
}

/** Keep server rows that are not part of the metadata block (beyond the first 250). */
export function rememberProjects(rows: Project[]) {
  if (!rows.length) return;
  for (const p of rows) known.set(p.id, p);
  bump();
}

const settleAfterPull = (drop: () => void) => {
  const sync = getSync();
  if (sync) void sync.pull().finally(drop); else drop();
};

/** Overlay a local project edit; call the result with whether the server accepted it. */
export function patchProject(id: string, patch: Partial<Project>): (accepted: boolean, server?: Project) => void {
  const layers = overlays.get(id) || [];
  layers.push(patch);
  overlays.set(id, layers);
  bump();
  return (accepted, server) => {
    const drop = () => {
      const rest = overlays.get(id)?.filter(p => p !== patch) || [];
      if (rest.length) overlays.set(id, rest); else overlays.delete(id);
      bump();
    };
    if (accepted && server) known.set(server.id, server);
    if (accepted) settleAfterPull(drop); else drop();
  };
}

/** Show a project before the server has created it. Settle with the created project, or null on failure. */
export function addPendingProject(draft: Omit<Project, 'id'>): (created: Project | null) => void {
  const tempId = `optimistic:${Math.random().toString(36).slice(2)}`;
  pendingCreates.set(tempId, {...draft, id: tempId});
  bump();
  return created => {
    pendingCreates.delete(tempId);
    if (created) known.set(created.id, created);
    bump();
    if (created) void getSync()?.pull();
  };
}
export const isPendingProject = (p: {id: string}) => p.id.startsWith('optimistic:');

/** Hide a project (archive) until the server answers; rejected archives reappear. */
export function hideProject(id: string): (accepted: boolean) => void {
  hidden.add(id);
  bump();
  return accepted => {
    if (accepted) { void getSync()?.pull(); return; }
    hidden.delete(id);
    bump();
  };
}

/** Every project the client knows: metadata ∪ extra pages ∪ pending creates, with overlays applied. */
export function useProjects(extra?: Project[]): Project[] {
  const meta = useSyncMeta();
  const v = useSyncExternalStore(subscribe, getVersion);
  return useMemo(() => {
    const byId = new Map<string, Project>();
    for (const p of known.values()) byId.set(p.id, p);
    for (const p of extra || []) byId.set(p.id, p);
    for (const p of meta?.projects.nodes || []) byId.set(p.id, p); // freshest: re-sent on every project change
    const out: Project[] = [];
    for (const p of pendingCreates.values()) out.push(p);
    for (const p of byId.values()) {
      if (hidden.has(p.id)) continue;
      const layers = overlays.get(p.id);
      out.push(layers ? Object.assign({}, p, ...layers) : p);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, v, extra]);
}

/** The freshest copy of one project (store, overlays) falling back to the given row. */
export function useProject(fallback: Project): Project {
  const meta = useSyncMeta();
  const v = useSyncExternalStore(subscribe, getVersion);
  return useMemo(() => {
    const base = meta?.projects.nodes.find(p => p.id === fallback.id) || known.get(fallback.id) || fallback;
    const layers = overlays.get(fallback.id);
    return layers ? Object.assign({}, base, ...layers) : base;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, v, fallback]);
}

/* ---------------- issues grouped by project (one O(n) pass per store emit) ---------------- */

export type ProjectIssueIndex = {byProject: Map<string, Issue[]>; open: Map<string, number>};
const indexCache = new WeakMap<Issue[], ProjectIssueIndex>();
const CLOSED = new Set(['completed', 'canceled']);
export function indexIssuesByProject(issues: Issue[]): ProjectIssueIndex {
  let index = indexCache.get(issues);
  if (!index) {
    const byProject = new Map<string, Issue[]>(), open = new Map<string, number>();
    for (const issue of issues) {
      const pid = issue.project?.id;
      if (!pid) continue;
      let list = byProject.get(pid);
      if (!list) { list = []; byProject.set(pid, list); }
      list.push(issue);
      if (!CLOSED.has(issue.state?.type || '')) open.set(pid, (open.get(pid) || 0) + 1);
    }
    index = {byProject, open};
    indexCache.set(issues, index);
  }
  return index;
}
export function useProjectIssueIndex(): ProjectIssueIndex {
  return indexIssuesByProject(useIssues());
}

const NO_ISSUES: Issue[] = [];
type Gql = (query: string, variables?: Record<string, unknown>) => Promise<any>;
type OlderPages = {cutoff: string; ids: string[]; cursor: string | null; more: boolean};
const olderPages = new Map<string, OlderPages>();

/** A project's synced issues plus its closed issues older than the sync window, paged on demand and adopted into the store. */
export function useProjectIssues(projectId: string, gql: Gql, fields: string, enabled = true) {
  const all = useIssues();
  const {ready, closedCutoff} = useSyncStatus();
  const issues = indexIssuesByProject(all).byProject.get(projectId) || NO_ISSUES;
  const [state, setState] = useState(() => olderPages.get(projectId) || null);
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  const busy = useRef(false);
  const allRef = useRef(all);
  allRef.current = all;
  const fetchOlder = useCallback(async (after: string | null) => {
    if (busy.current || !closedCutoff) return;
    busy.current = true; setLoading(true); setError('');
    try {
      const d = await gql(`query ProjectOlderIssues($after:String,$filter:IssueFilter){issues(first:50,after:$after,filter:$filter,orderBy:createdAt){nodes ${fields} pageInfo{hasNextPage endCursor}}}`,
        {after, filter: {project: {id: {eq: projectId}}, state: {type: {in: ['completed', 'canceled']}}, updatedAt: {lt: closedCutoff}}});
      const page = d.issues;
      if (page.pageInfo?.hasNextPage && (!page.pageInfo.endCursor || page.pageInfo.endCursor === after || !page.nodes.length)) throw Error('Project issues pagination did not advance.');
      getSync()?.adopt(page.nodes);
      const prior = after ? olderPages.get(projectId)?.ids || [] : [];
      const next: OlderPages = {cutoff: closedCutoff, ids: [...prior, ...page.nodes.map((i: Issue) => i.id)], cursor: page.pageInfo?.endCursor || null, more: Boolean(page.pageInfo?.hasNextPage)};
      olderPages.set(projectId, next);
      setState(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      busy.current = false; setLoading(false);
    }
  }, [gql, fields, projectId, closedCutoff]);
  useEffect(() => {
    if (!enabled || !ready || !closedCutoff) return;
    const cached = olderPages.get(projectId);
    // A re-bootstrap drops adopted rows; refetch when any adopted id is no longer in the store.
    const present = cached && cached.cutoff === closedCutoff && (cached.ids.length === 0 || (() => {
      const ids = new Set(allRef.current.map(i => i.id));
      return cached.ids.every(id => ids.has(id));
    })());
    if (present) { setState(cached!); return; }
    void fetchOlder(null);
  }, [enabled, ready, closedCutoff, projectId, fetchOlder]);
  return {
    issues, ready, loading, error,
    more: Boolean(state?.more),
    loadMore: () => void fetchOlder(state?.cursor || null),
    retry: () => void fetchOlder(state?.more ? state.cursor : null),
  };
}

/* ---------------- stale-while-revalidate page cache ---------------- */

export type PageOf<T> = {nodes: T[]; pageInfo?: {hasNextPage?: boolean; endCursor?: string | null}};
type Op = {apply: (rows: any[]) => any[]; committedAt: number | null};
type Paged = {base: any[]; cursor: string | null; more: boolean; ops: Op[]; started: number; landed: boolean; view: any[]};
const pages = new Map<string, Paged>();
const pageListeners = new Map<string, Set<Listener>>();
const REVALIDATE_MAX = 250;
// The first rows of each list are kept per browser tab so a reload paints them before revalidating.
const STORAGE_PREFIX = 'hoja_page_cache:';
const STORED_ROWS = 50;
const saveTimers = new Map<string, number>();
function readStored(key: string): Pick<Paged, 'base' | 'cursor' | 'more'> | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + key);
    const data = raw ? JSON.parse(raw) : null;
    return data && Array.isArray(data.base) ? {base: data.base, cursor: data.cursor ?? null, more: Boolean(data.more)} : null;
  } catch { return null; }
}
function store(key: string, e: Paged) {
  window.clearTimeout(saveTimers.get(key));
  saveTimers.set(key, window.setTimeout(() => {
    try {
      const base = e.base.slice(0, STORED_ROWS);
      const truncated = base.length < e.base.length;
      // A truncated list resumes from its last stored row on "load more".
      const cursor = truncated ? null : e.cursor;
      sessionStorage.setItem(STORAGE_PREFIX + key, JSON.stringify({base, cursor, more: truncated || e.more}));
    } catch { /* storage full or blocked: the in-memory cache still works */ }
  }, 300));
}
const entry = (key: string): Paged => {
  let e = pages.get(key);
  if (!e) {
    const stored = readStored(key);
    e = {base: stored?.base || [], cursor: stored?.cursor ?? null, more: stored?.more ?? false, ops: [], started: 0, landed: Boolean(stored), view: stored?.base || []};
    pages.set(key, e);
  }
  return e;
};
const publish = (key: string, e: Paged) => {
  e.view = e.ops.reduce((rows, op) => op.apply(rows), e.base);
  if (e.landed) store(key, e);
  for (const l of pageListeners.get(key) || []) l();
};

/**
 * Optimistic change to a cached list (insert, remove, edit). It stays applied over every
 * revalidation until settled. Settle with an idempotent `commit` (folded into the cached rows,
 * and replacing the optimistic change) when the
 * server accepted it — the overlay then lasts until a refetch that started afterwards lands,
 * so an older in-flight response cannot flicker it back — or with nothing to roll back.
 */
export function optimisticPage<T>(key: string, apply: (rows: T[]) => T[]): (commit?: (rows: T[]) => T[]) => void {
  const e = entry(key);
  const op: Op = {apply, committedAt: null};
  e.ops = [...e.ops, op];
  publish(key, e);
  return commit => {
    if (commit) { e.base = commit(e.base); op.apply = commit as Op['apply']; op.committedAt = e.started; } else e.ops = e.ops.filter(o => o !== op);
    publish(key, e);
  };
}
export const hasPage = (key: string) => Boolean(pages.get(key)?.landed);

/**
 * Rows for `key`, served from the cache at once and revalidated whenever `revision` changes.
 * A revalidation refetches the loaded window in one request (up to 250 rows) and swaps it in,
 * so rows never blank out while fresh data loads.
 */
export function usePagedCache<T extends {id: string}>(key: string | null, fetchPage: (first: number, after: string | null) => Promise<PageOf<T>>, revision: number, pageSize: number, label: string) {
  const sub = useCallback((cb: Listener) => {
    if (!key) return () => {};
    let set = pageListeners.get(key);
    if (!set) { set = new Set(); pageListeners.set(key, set); }
    set.add(cb);
    return () => { set!.delete(cb); };
  }, [key]);
  const view = useSyncExternalStore(sub, () => (key ? entry(key).view : undefined)) as T[] | undefined;
  const e = key ? entry(key) : undefined;
  const [loading, setLoading] = useState<'refresh' | 'more' | null>(null), [error, setError] = useState('');
  const generation = useRef(0), busy = useRef(false), lastMode = useRef<'refresh' | 'more'>('refresh');
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;

  const run = useCallback(async (mode: 'refresh' | 'more') => {
    if (!key) return;
    if (mode === 'more' && busy.current) return;
    const seq = ++generation.current;
    lastMode.current = mode;
    busy.current = true; setLoading(mode); setError('');
    const e = entry(key);
    const startedAt = ++e.started;
    try {
      if (mode === 'more') {
        const after = e.cursor;
        const page = await fetchRef.current(pageSize, after);
        if (seq !== generation.current) return;
        if (page.pageInfo?.hasNextPage && (!page.pageInfo.endCursor || page.pageInfo.endCursor === after || !page.nodes.length)) throw Error(`${label} pagination did not advance. Retry later.`);
        const seen = new Set(e.base.map(r => r.id));
        e.base = [...e.base, ...page.nodes.filter(r => !seen.has(r.id))];
        e.cursor = page.pageInfo?.endCursor || null; e.more = Boolean(page.pageInfo?.hasNextPage);
      } else {
        const loadedRows = e.base.length;
        const first = Math.min(REVALIDATE_MAX, Math.max(pageSize, loadedRows));
        const page = await fetchRef.current(first, null);
        if (seq !== generation.current) return;
        const windowIds = new Set(page.nodes.map(r => r.id));
        // Rows past the refetched window (only when more than 250 were loaded) are kept as they were.
        const tail = loadedRows > first ? e.base.slice(first).filter(r => !windowIds.has(r.id)) : [];
        if (!tail.length) { e.cursor = page.pageInfo?.endCursor || null; e.more = Boolean(page.pageInfo?.hasNextPage); }
        e.base = [...page.nodes, ...tail];
        // Accepted optimistic changes are now reflected by a response that started after them.
        e.ops = e.ops.filter(op => op.committedAt === null || op.committedAt >= startedAt);
      }
      e.landed = true;
      publish(key, e);
    } catch (err) {
      if (seq === generation.current) setError((err as Error).message);
    } finally {
      if (seq === generation.current) { busy.current = false; setLoading(null); }
    }
  }, [key, pageSize, label]);

  useEffect(() => { const gen = generation; void run('refresh'); return () => { gen.current++; busy.current = false; }; }, [run, revision]);

  return {
    rows: view || ([] as T[]),
    hasData: Boolean(e?.landed),
    more: Boolean(e?.more),
    loading: loading !== null, loadingMore: loading === 'more', error,
    loadMore: () => void run('more'),
    refresh: () => void run('refresh'),
    retry: () => void run(lastMode.current),
  };
}
