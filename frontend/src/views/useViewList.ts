/* The views the signed-in person can read, for the sidebar favorites, the command palette and
 * the team tabs. One request is shared by every mounted reader and repeated after any view write
 * (`VIEWS_CHANGED`), so a star or a rename shows everywhere at once. The app shell names the
 * person and workspace (`setViewListIdentity`); a change of either drops the shared list.
 */
import { useEffect, useState } from "react";
import { listViews, VIEWS_CHANGED, type ServerView } from "../views";
import { createViewListCache } from "./viewListCache";

const cache = createViewListCache(() => listViews());
const IDENTITY_CHANGED = "hoja:views-identity";

/* Registered once, before any reader's listener, so every reader of one change event shares
   one fresh request instead of each starting its own. */
if (typeof window !== "undefined")
  window.addEventListener(VIEWS_CHANGED, () => cache.invalidate());

/** The shell calls this while rendering with `<workspace id>:<viewer id>`. A change drops the
 * cache at once (so a reader mounting in the same render starts empty), and mounted readers
 * clear what they show and read again just after the render. */
export function setViewListIdentity(identity: string): void {
  if (!cache.setIdentity(identity)) return;
  queueMicrotask(() => {
    try {
      window.dispatchEvent(new Event(IDENTITY_CHANGED));
    } catch {
      /* no window (tests) */
    }
  });
}

export function useViewList(enabled = true): {
  views: ServerView[];
  error: string;
} {
  const [views, setViews] = useState<ServerView[]>(() => cache.last());
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    // What the cache holds now: [] when the identity changed while this reader was off.
    setViews(cache.last());
    const read = () => {
      const started = cache.generation();
      return cache
        .load()
        .then((rows) => {
          if (!live || started !== cache.generation()) return;
          setViews(rows);
          setError("");
        })
        .catch((e) => {
          if (live && started === cache.generation())
            setError((e as Error).message);
        });
    };
    void read();
    const onChange = () => void read();
    const onIdentity = () => {
      setViews([]);
      setError("");
      void read();
    };
    window.addEventListener(VIEWS_CHANGED, onChange);
    window.addEventListener(IDENTITY_CHANGED, onIdentity);
    return () => {
      live = false;
      window.removeEventListener(VIEWS_CHANGED, onChange);
      window.removeEventListener(IDENTITY_CHANGED, onIdentity);
    };
  }, [enabled]);
  return { views, error };
}
