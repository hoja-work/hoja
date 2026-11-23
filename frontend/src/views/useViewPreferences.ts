/* Display options of one page or saved view, kept on the server in layers (docs/views.md,
 * "Display preferences"): the default everyone sees and the viewer's own overrides.
 *
 * The page renders immediately from a localStorage copy of the last answer, so an offline or
 * slow load still opens with the viewer's layout; the server answer replaces it when it
 * arrives. Edits apply locally at once and are sent in one debounced write.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getViewPreferences,
  resetViewPreferences,
  setViewPreferences,
  type PreferenceTarget,
  type ViewDocument,
  type ViewPreferenceState,
} from "../views";
import {
  divergentKeys,
  mergeDisplay,
  type DisplayDoc,
  type EffectiveDisplay,
} from "./displayModel";

type Layers = { def: DisplayDoc; user: DisplayDoc | null };

const CACHE_PREFIX = "hoja_view_prefs:";
const WRITE_DELAY = 400;

function cacheKey(target: PreferenceTarget | null): string | null {
  if (!target) return null;
  return (
    CACHE_PREFIX +
    (target.viewId ? `view:${target.viewId}` : target.viewType || "")
  );
}

function readCache(key: string | null): Layers {
  if (!key) return { def: {}, user: null };
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "null");
    if (parsed && typeof parsed === "object")
      return { def: parsed.def || {}, user: parsed.user || null };
  } catch {
    /* a corrupt cache reads as empty */
  }
  return { def: {}, user: null };
}

function writeCache(key: string | null, layers: Layers) {
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(layers));
  } catch {
    /* storage full or blocked: the server copy still holds the preferences */
  }
}

function fromState(state: ViewPreferenceState): Layers {
  return {
    def: (state.default || {}) as DisplayDoc,
    user: (state.user || null) as DisplayDoc | null,
  };
}

export type ViewPreferences = {
  display: EffectiveDisplay;
  /** The display everyone starts from (system defaults + page defaults + the shared default). */
  base: EffectiveDisplay;
  update: (patch: DisplayDoc) => void;
  reset: () => void;
  setDefault: () => Promise<void>;
  /** The viewer's options differ from the default: the dot on the Display button. */
  hasOverrides: boolean;
  error: string;
};

/** `target` null keeps the options in memory only (a page without a preference key). */
export function useViewPreferences(
  target: PreferenceTarget | null,
  pageDefaults: DisplayDoc = {},
): ViewPreferences {
  const key = cacheKey(target);
  const [layers, setLayers] = useState<Layers>(() => readCache(key));
  const [error, setError] = useState("");
  /* Edits not yet sent, with the page they belong to: a tab switch inside the debounce must
     write them to the tab they were made on, not to the one now showing. */
  const pending = useRef<{
    target: PreferenceTarget | null;
    values: DisplayDoc;
  }>({ target: null, values: {} });
  const timer = useRef<number | undefined>(undefined);
  const targetRef = useRef(target);
  targetRef.current = target;
  const viewId = target?.viewId,
    viewType = target?.viewType;

  useEffect(() => {
    setLayers(readCache(key));
    if (!key) return;
    let live = true;
    getViewPreferences({ viewId, viewType })
      .then((state) => {
        if (!live) return;
        // Edits made while the read was in flight stay on top of what it returned.
        const next = fromState(state);
        const queued =
          cacheKey(pending.current.target) === key
            ? pending.current.values
            : {};
        const merged = Object.keys(queued).length
          ? { ...next, user: { ...(next.user || {}), ...queued } }
          : next;
        setLayers(merged);
        writeCache(key, merged);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [key, viewId, viewType]);

  const flush = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    const { target: t, values } = pending.current;
    pending.current = { target: null, values: {} };
    if (!t || !Object.keys(values).length) return;
    setViewPreferences(t, values as ViewDocument)
      .then((state) => {
        setError("");
        // A newer edit is queued, or the page moved on: keep what is on screen.
        if (Object.keys(pending.current.values).length) return;
        writeCache(cacheKey(t), fromState(state));
        if (cacheKey(targetRef.current) === cacheKey(t))
          setLayers(fromState(state));
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  // Send what is queued when the page goes away, rather than dropping the last change.
  useEffect(() => () => flush(), [flush, key]);

  const update = useCallback(
    (patch: DisplayDoc) => {
      setLayers((old) => {
        const next = { ...old, user: { ...(old.user || {}), ...patch } };
        writeCache(key, next);
        return next;
      });
      if (!key) return;
      // An edit for another page than the queued one sends the queued one first.
      if (pending.current.target && cacheKey(pending.current.target) !== key)
        flush();
      pending.current = {
        target: targetRef.current,
        values: { ...pending.current.values, ...patch },
      };
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(flush, WRITE_DELAY);
    },
    [key, flush],
  );

  const reset = useCallback(() => {
    window.clearTimeout(timer.current);
    pending.current = { target: null, values: {} };
    setLayers((old) => {
      const next = { ...old, user: null };
      writeCache(key, next);
      return next;
    });
    const t = targetRef.current;
    if (t)
      resetViewPreferences(t)
        .then((state) => {
          const next = fromState(state);
          setLayers(next);
          writeCache(cacheKey(t), next);
        })
        .catch((e) => setError((e as Error).message));
  }, [key]);

  const base = useMemo(
    () => mergeDisplay(pageDefaults, layers.def),
    [pageDefaults, layers.def],
  );
  const display = useMemo(
    () => mergeDisplay(pageDefaults, layers.def, layers.user),
    [pageDefaults, layers],
  );

  const setDefault = useCallback(async () => {
    const t = targetRef.current;
    const { collapsedGroups: _collapsed, ...values } = layers.user || {};
    void _collapsed;
    if (!t || !Object.keys(values).length) return;
    window.clearTimeout(timer.current);
    pending.current = { target: null, values: {} };
    try {
      const next = fromState(
        await setViewPreferences(t, values as ViewDocument, "default"),
      );
      setLayers(next);
      writeCache(cacheKey(t), next);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [layers.user]);

  return {
    display,
    base,
    update,
    reset,
    setDefault,
    hasOverrides: divergentKeys(layers.user, base).length > 0,
    error,
  };
}
