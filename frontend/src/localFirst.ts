import {useCallback, useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import type {Issue} from './main';

/* UI helpers for local-first pages (inbox, members, settings, command palette).
 *
 * Re-exports the sync store hooks so pages import one module, and adds:
 *   useOptimisticList(base)   overlay layers (add/patch/remove) over a server list, with rollback
 *   useIssueSearch(issues, q) instant in-memory issue search over the synced store
 *   navigateTo(path)          push a route and let the app shell's popstate handler resolve it
 */
export {getSync, useEntityVersion, useIssues, useSyncMeta, useSyncStatus} from './sync';

/* ---------------- optimistic list overlays ---------------- */

export type Layer<T> = {
  id: string;
  patch?: Partial<T>;
  remove?: boolean;
  add?: T;
  /** For `add`: hide the placeholder once the base holds the real item. */
  replacedBy?: (item: T) => boolean;
};
type KeyedLayer<T> = Layer<T> & {key: number};
export type LayerHandle = {
  /** Remove the overlay now (rollback, or the base already holds the change). */
  drop: () => void;
  /** Keep the overlay until `refresh` has brought the server's version, then remove it. */
  settleAfter: (refresh: Promise<unknown> | undefined) => void;
};

export function applyLayers<T extends {id: string}>(base: T[], layers: Layer<T>[]): T[] {
  if (!layers.length) return base;
  let out = base;
  for (const l of layers) {
    if (l.add) {
      const add = l.add;
      if (!out.some(x => x.id === l.id || (l.replacedBy ? l.replacedBy(x) : false))) out = [...out, add];
    } else if (l.remove) {
      out = out.filter(x => x.id !== l.id);
    } else if (l.patch) {
      const patch = l.patch;
      out = out.map(x => (x.id === l.id ? {...x, ...patch} : x));
    }
  }
  return out;
}

let layerSeq = 0;
export function useOptimisticList<T extends {id: string}>(base: T[]) {
  const [layers, setLayers] = useState<KeyedLayer<T>[]>([]);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const view = useMemo(() => applyLayers(base, layers), [base, layers]);
  const push = useCallback((layer: Layer<T>): LayerHandle => {
    const key = ++layerSeq;
    setLayers(l => [...l, {...layer, key}]);
    const drop = () => { if (live.current) setLayers(l => l.filter(x => x.key !== key)); };
    return {drop, settleAfter: refresh => { if (refresh) void refresh.catch(() => undefined).finally(drop); else drop(); }};
  }, []);
  const pending = layers.length > 0;
  return [view, push, pending] as const;
}

/** Message from a thrown value, for role="alert" lines. */
export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/* ---------------- instant issue search ---------------- */

// Hydrated issues are reused by the store while unchanged, so the lowercase haystack is computed
// once per issue version instead of once per keystroke.
const haystacks = new WeakMap<Issue, string>();
function haystack(issue: Issue): string {
  let h = haystacks.get(issue);
  if (h === undefined) { h = `${issue.identifier} ${issue.title}`.toLowerCase(); haystacks.set(issue, h); }
  return h;
}

/** Precompute haystacks in idle slices so the first keystroke does not pay for the whole index. */
export function warmSearchIndex(issues: Issue[]): () => void {
  let i = 0, timer = 0;
  const step = () => {
    const end = Math.min(issues.length, i + 5000);
    for (; i < end; i++) haystack(issues[i]);
    if (i < issues.length) timer = window.setTimeout(step, 0);
  };
  timer = window.setTimeout(step, 0);
  return () => window.clearTimeout(timer);
}

export function searchTerms(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/** Every issue whose identifier+title contains all terms (uncapped, store order). */
function matchAll(pool: Issue[], terms: string[]): Issue[] {
  const out: Issue[] = [];
  outer: for (const issue of pool) {
    const h = haystack(issue);
    for (const t of terms) if (!h.includes(t)) continue outer;
    out.push(issue);
  }
  return out;
}

/** Rank: exact identifier, identifier prefix, whole phrase, then any-order terms; store order (newest first) within each. */
function rank(matches: Issue[], terms: string[], cap: number): Issue[] {
  const q = terms.join(' ');
  const exact: Issue[] = [], prefix: Issue[] = [], phrase: Issue[] = [], rest: Issue[] = [];
  for (const issue of matches) {
    const id = issue.identifier.toLowerCase();
    if (id === q) exact.push(issue);
    else if (id.startsWith(q)) { if (prefix.length < cap) prefix.push(issue); }
    else if (terms.length > 1 && !haystack(issue).includes(q)) { if (rest.length < cap) rest.push(issue); }
    else if (phrase.length < cap) phrase.push(issue);
    if (exact.length && prefix.length + phrase.length >= cap) break;
  }
  return [...exact, ...prefix, ...phrase, ...rest].slice(0, cap);
}

/**
 * Search the synced issues on every keystroke without a network round trip. When the query only
 * extends the previous one (typing forward), only the previous matches are re-checked.
 */
export function useIssueSearch(issues: Issue[], query: string, cap = 8): {results: Issue[]; total: number} {
  const last = useRef<{issues: Issue[]; terms: string[]; q: string; matches: Issue[]} | null>(null);
  return useMemo(() => {
    const terms = searchTerms(query);
    if (!terms.length) { last.current = null; return {results: [], total: 0}; }
    const q = terms.join(' ');
    const prev = last.current;
    const narrowing = prev && prev.issues === issues && q.startsWith(prev.q) && prev.terms.length <= terms.length;
    const matches = matchAll(narrowing ? prev!.matches : issues, terms);
    last.current = {issues, terms, q, matches};
    return {results: rank(matches, terms, cap), total: matches.length};
  }, [issues, query, cap]);
}

/** Route through the app shell's popstate handler (it resolves views from the URL). */
export function navigateTo(path: string) {
  history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

/**
 * Render an overlay directly under <body>. Overlays inside the app shell are descendants of many
 * `:has()` selector subjects, so every DOM change in them re-styles the whole page behind them
 * (hundreds of ms with a long issue list); outside the shell the cost is local.
 */
export function bodyLayer(node: ReactNode) {
  return createPortal(node, document.body);
}
