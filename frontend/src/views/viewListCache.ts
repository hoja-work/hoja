/* The shared read behind `useViewList`, without React so the node test script runs it.
 *
 * One request is shared by every reader. The cache belongs to one person in one workspace:
 * `setIdentity` with a different `<workspace>:<viewer>` drops it, so a workspace switch, or
 * signing out and in as someone else without a reload, never shows the previous list (whose
 * private views are not the new person's to see). A read started under an older identity or
 * before a later change (`invalidate`) never lands.
 */
import type { ServerView } from "../views";

export type ViewListCache = {
  /** The list for the current identity, from the one shared request. */
  load: () => Promise<ServerView[]>;
  /** The last list this identity read ([] after an identity change). */
  last: () => ServerView[];
  /** Forget the cached request (after a view write); bumps the generation. */
  invalidate: () => void;
  /** Switch identity; true when it changed (and the cache was dropped). */
  setIdentity: (identity: string) => boolean;
  /** Bumped by every invalidation and identity change: a read that started under an older
   * generation is stale. */
  generation: () => number;
};

/** The kinds a views page can show; other kinds (initiative views) have no page here yet. */
const SHOWN_KINDS = new Set(["issue", "project"]);

export function createViewListCache(
  fetch: () => Promise<ServerView[]>,
): ViewListCache {
  let inflight: Promise<ServerView[]> | null = null;
  let last: ServerView[] = [];
  let generation = 0;
  let identity = "";
  return {
    load() {
      if (!inflight) {
        const started = generation;
        const request = fetch().then((rows) => {
          const shown = rows.filter((v) => SHOWN_KINDS.has(v.kind));
          if (started === generation) last = shown;
          return shown;
        });
        // A failed read is not cached: the next reader retries.
        request.catch(() => {
          if (inflight === request) inflight = null;
        });
        inflight = request;
      }
      return inflight;
    },
    last: () => last,
    invalidate() {
      inflight = null;
      generation += 1;
    },
    setIdentity(next) {
      if (next === identity) return false;
      identity = next;
      inflight = null;
      last = [];
      generation += 1;
      return true;
    },
    generation: () => generation,
  };
}
