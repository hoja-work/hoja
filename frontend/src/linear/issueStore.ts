import {useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore} from 'react';
import type {RefObject} from 'react';
import {gql} from '../main';
import type {Issue} from '../main';
import {getSync} from '../sync';

/* Helpers the issue list/board/views build on top of the local-first store (../sync).
 *
 * - runBounded: concurrent mutations with a cap, collecting per-item failures.
 * - optimistic bulk update/archive/delete: every overlay is applied before the first
 *   request leaves, requests run 6 at a time, failures roll back only their own rows.
 * - hidden ids: the store has no "remove locally" overlay, so archive/delete hide rows
 *   here until the delta that removes them has been pulled.
 * - useScrollWindow: the visible slice of a long list inside a scroll container.
 */

export const BULK_CONCURRENCY = 6;

export async function runBounded<T>(items: T[], limit: number, fn: (item: T) => Promise<void>): Promise<{item: T; error: Error}[]> {
  const failures: {item: T; error: Error}[] = [];
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++];
      try { await fn(item); } catch (e) { failures.push({item, error: e instanceof Error ? e : new Error(String(e))}); }
    }
  };
  await Promise.all(Array.from({length: Math.min(limit, items.length)}, worker));
  return failures;
}

/* ---------------- locally hidden (archived/deleted, not yet confirmed by a delta) ---------------- */

let hidden: ReadonlySet<string> = new Set();
const hiddenListeners = new Set<() => void>();
function setHidden(ids: string[], on: boolean) {
  const next = new Set(hidden);
  for (const id of ids) { if (on) next.add(id); else next.delete(id); }
  hidden = next;
  for (const cb of hiddenListeners) cb();
}
const subscribeHidden = (cb: () => void) => { hiddenListeners.add(cb); return () => { hiddenListeners.delete(cb); }; };
/** Ids removed optimistically; filter them out of anything derived from useIssues(). */
export function useHiddenIssueIds(): ReadonlySet<string> {
  return useSyncExternalStore(subscribeHidden, () => hidden);
}

/* ---------------- optimistic bulk mutations ---------------- */

const UPDATE = `mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}`;

function summarize(failures: {error: Error}[], total: number, verb: string): string {
  if (!failures.length) return '';
  const first = failures[0].error.message || 'Request failed';
  return total === 1 ? first : `${failures.length} of ${total} issues could not be ${verb}: ${first}`;
}

/** Overlay every update at once, then send them BULK_CONCURRENCY at a time. Returns failed ids + message. */
export async function bulkUpdate(ids: string[], inputFor: (id: string) => Record<string, unknown> | null): Promise<{failed: string[]; error: string}> {
  const sync = getSync();
  const work: {id: string; input: Record<string, unknown>; settle?: (ok: boolean) => void}[] = [];
  for (const id of ids) {
    const input = inputFor(id);
    if (!input) continue;
    work.push({id, input, settle: sync?.patch(id, sync.patchFromInput(input))});
  }
  const failures = await runBounded(work, BULK_CONCURRENCY, async w => {
    try {
      const d = await gql(UPDATE, {id: w.id, input: w.input});
      if (!d.issueUpdate?.success) throw new Error('Update was rejected');
    } catch (e) { w.settle?.(false); throw e; }
    w.settle?.(true);
  });
  return {failed: failures.map(f => f.item.id), error: summarize(failures, work.length, 'updated')};
}

/** Archive or delete: hide rows immediately, unhide failures, drop the rest once the delta removed them. */
export async function bulkRemove(ids: string[], kind: 'archive' | 'delete'): Promise<{failed: string[]; error: string}> {
  const mutation = kind === 'archive'
    ? `mutation($id:String!){issueArchive(id:$id){success}}`
    : `mutation($id:String!){issueDelete(id:$id){success}}`;
  setHidden(ids, true);
  const failures = await runBounded(ids, BULK_CONCURRENCY, async id => {
    const d = await gql(mutation, {id});
    const payload = kind === 'archive' ? d.issueArchive : d.issueDelete;
    if (!payload?.success) throw new Error(kind === 'archive' ? 'Archive was rejected' : 'Delete was rejected');
  });
  const failed = failures.map(f => f.item);
  if (failed.length) setHidden(failed, false);
  const failedSet = new Set(failed);
  const done = ids.filter(id => !failedSet.has(id));
  if (done.length) {
    const sync = getSync();
    if (sync) void sync.pull().finally(() => setHidden(done, false));
    // Without a store nothing will bring these rows back; keep them hidden.
  }
  return {failed, error: summarize(failures, ids.length, kind === 'archive' ? 'archived' : 'deleted')};
}

/** Add one label to each issue, keeping each issue's own labels. */
export function addLabelInput(issue: Pick<Issue, 'labels'> | undefined, labelId: string): Record<string, unknown> | null {
  const own = (issue?.labels?.nodes || []).map(l => l.id);
  return own.includes(labelId) ? null : {labelIds: [...own, labelId]};
}

/* ---------------- stable callbacks ---------------- */

/** A callback identity that never changes but always calls the latest function (keeps memoized rows memoized). */
export function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const ref = useRef(fn);
  useLayoutEffect(() => { ref.current = fn; });
  return useCallback((...args: A) => ref.current(...args), []);
}

/* ---------------- windowing ---------------- */

export type ScrollWindow = {top: number; bottom: number};
const QUANTUM = 200;

/* Clipping ancestors of the anchor. Pages live in different shells, so the nominal scroll box
 * (e.g. .lin-scroll) may have grown to its content and left the scrolling to an outer element. */
function clippingAncestors(anchor: HTMLElement): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (let el = anchor.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    const oy = getComputedStyle(el).overflowY;
    if (oy !== 'visible') out.push(el);
  }
  return out;
}

/** The visible viewport expressed in the anchor's coordinates, quantized so small scrolls don't re-render. */
export function useScrollWindow(scrollRef: RefObject<HTMLElement | null>, anchorRef: RefObject<HTMLElement | null>, enabled: boolean): ScrollWindow {
  const [win, setWin] = useState<ScrollWindow>(() => ({top: 0, bottom: typeof window === 'undefined' ? 1000 : window.innerHeight}));
  useEffect(() => {
    if (!enabled) return;
    const anchor = anchorRef.current;
    if (!anchor) return;
    let frame = 0;
    let clips = clippingAncestors(anchor);
    const measure = () => {
      frame = 0;
      let vpTop = 0, vpBottom = window.innerHeight;
      for (const el of clips) {
        const r = el.getBoundingClientRect();
        vpTop = Math.max(vpTop, r.top); vpBottom = Math.min(vpBottom, r.bottom);
      }
      const anchorTop = anchor.getBoundingClientRect().top;
      const top = Math.floor((vpTop - anchorTop) / QUANTUM) * QUANTUM;
      const bottom = Math.ceil((Math.max(vpTop, vpBottom) - anchorTop) / QUANTUM) * QUANTUM;
      setWin(w => (w.top === top && w.bottom === bottom ? w : {top, bottom}));
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const onResize = () => { clips = clippingAncestors(anchor); schedule(); };
    measure();
    // Capture-phase listener sees scrolls of whichever ancestor (or the document) scrolls.
    document.addEventListener('scroll', schedule, {capture: true, passive: true});
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null;
    if (scrollRef.current) ro?.observe(scrollRef.current);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('scroll', schedule, {capture: true});
      window.removeEventListener('resize', onResize);
      ro?.disconnect();
    };
  }, [scrollRef, anchorRef, enabled]);
  return win;
}

/** [start, end) of fixed-height rows that intersect the window (plus overscan). */
export function visibleRange(win: ScrollWindow, rowsTop: number, rowH: number, n: number, overscan = 900): [number, number] {
  const start = Math.max(0, Math.min(n, Math.floor((win.top - overscan - rowsTop) / rowH)));
  const end = Math.max(start, Math.min(n, Math.ceil((win.bottom + overscan - rowsTop) / rowH)));
  return [start, end];
}
