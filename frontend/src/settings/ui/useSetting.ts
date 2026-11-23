import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "../../components/ui/toast";

/* The settings save contract, in one place (DESIGN.md → "Settings").
 *
 *  - Instant controls (switch, segmented, select): the UI moves first, the request follows.
 *    Success shows a quiet inline "Saved"; failure snaps the control back, shows the reason
 *    inline and offers Retry. Nothing is ever left looking saved when it is not.
 *  - Text fields commit on blur or Enter, revert on Escape, validate before sending.
 *  - Only the latest change wins: a slow earlier response can never overwrite a later choice.
 *  - Workspace-wide changes may opt into an Undo toast (`undoLabel`).
 */
export type SaveState = "idle" | "saving" | "saved" | "error";
export type SaveStatus = {
  state: SaveState;
  error: string | null;
  retry: (() => void) | null;
};

export function errorMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e || "");
  if (!raw || /failed to fetch|networkerror|load failed/i.test(raw))
    return "Couldn't reach the server. Check your connection and try again.";
  if (/^unexpected token|is not valid json|json\.parse/i.test(raw))
    return "The server returned an unexpected response. Try again in a moment.";
  // Only bare transport-level refusals are replaced; a server sentence that explains itself
  // ("Only workspace admins can …", "Key must be …") is shown as written.
  if (
    /^(forbidden|not authori[sz]ed|unauthori[sz]ed|permission denied)\.?$/i.test(
      raw.trim(),
    )
  )
    return "You don't have permission to change this. Ask a workspace admin.";
  return raw.replace(/^error:\s*/i, "");
}

const SAVED_MS = 1800;

export function useSetting<T>({
  value,
  save,
  undoLabel,
}: {
  value: T;
  save: (next: T, previous: T) => Promise<unknown>;
  /** e.g. "Workspace name changed" — shows a toast with Undo after success. */
  undoLabel?: string;
}) {
  const [local, setLocal] = useState<T>(value);
  const [state, setState] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const committed = useRef<T>(value);
  const lastAttempt = useRef<T | null>(null);
  const timer = useRef<number | undefined>(undefined);

  // Follow the server value when nothing is in flight.
  // While a save is in flight, an incoming value is the caller's optimistic echo, not
  // confirmation; committing it would make a failed save "roll back" to the failed value.
  const inFlight = useRef(false);
  useEffect(() => {
    if (inFlight.current) return;
    committed.current = value;
    setLocal(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only an external value change resyncs
  }, [value]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const set = useCallback(
    async (next: T, opts: { silent?: boolean } = {}) => {
      const previous = committed.current;
      if (Object.is(next, previous) && state !== "error") {
        setLocal(next);
        return true;
      }
      const id = ++seq.current;
      lastAttempt.current = next;
      setLocal(next);
      setState("saving");
      inFlight.current = true;
      setError(null);
      window.clearTimeout(timer.current);
      try {
        await save(next, previous);
        if (id !== seq.current) return true;
        inFlight.current = false;
        committed.current = next;
        setState("saved");
        timer.current = window.setTimeout(() => setState("idle"), SAVED_MS);
        if (undoLabel && !opts.silent)
          toast({
            title: undoLabel,
            action: {
              label: "Undo",
              onClick: () => void set(previous, { silent: true }),
            },
          });
        return true;
      } catch (e) {
        if (id !== seq.current) return false;
        inFlight.current = false;
        setLocal(committed.current);
        setState("error");
        setError(errorMessage(e));
        return false;
      }
    },
    [save, state, undoLabel],
  );

  const retry =
    state === "error" && lastAttempt.current !== null
      ? () => void set(lastAttempt.current as T)
      : null;

  return {
    value: local,
    set,
    saving: state === "saving",
    status: { state, error, retry } satisfies SaveStatus,
  };
}

/** Text that commits on blur/Enter, reverts on Escape, and validates before saving. */
export function useSettingText({
  value,
  save,
  validate,
  undoLabel,
}: {
  value: string;
  save: (next: string, previous: string) => Promise<unknown>;
  validate?: (next: string) => string | null;
  undoLabel?: string;
}) {
  const setting = useSetting({ value, save, undoLabel });
  const [draft, setDraft] = useState(value);
  const [invalid, setInvalid] = useState<string | null>(null);
  // Escape reverts and blurs; the blur that follows must not commit the stale draft.
  const skipBlur = useRef(false);
  useEffect(() => setDraft(setting.value), [setting.value]);

  const commit = async () => {
    const next = draft.trim();
    if (next === setting.value.trim()) {
      setDraft(setting.value);
      setInvalid(null);
      return;
    }
    const problem = validate?.(next) ?? null;
    setInvalid(problem);
    if (problem) return;
    await setting.set(next);
  };
  const status: SaveStatus = invalid
    ? { state: "error", error: invalid, retry: null }
    : setting.status;
  return {
    status,
    saving: setting.saving,
    inputProps: {
      value: draft,
      "aria-invalid": status.state === "error" || undefined,
      onChange: (
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
      ) => {
        setDraft(e.target.value);
        if (invalid) setInvalid(null);
      },
      onBlur: () => {
        if (skipBlur.current) {
          skipBlur.current = false;
          return;
        }
        void commit();
      },
      onKeyDown: (
        e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
      ) => {
        if (e.key === "Enter" && e.currentTarget.tagName === "INPUT") {
          e.preventDefault();
          void commit();
        } else if (e.key === "Escape") {
          skipBlur.current = true;
          setDraft(setting.value);
          setInvalid(null);
          e.currentTarget.blur();
        }
      },
    },
  };
}

/** Load a settings resource with explicit loading / error / retry, and local mutation. */
export function useResource<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const reload = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const next = await loadRef.current();
      if (id === seq.current) setData(next);
    } catch (e) {
      if (id === seq.current) setError(errorMessage(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller-provided deps
  }, deps);
  return { data, setData, error, loading, reload };
}

/** Run a one-off mutation (create, delete, rotate) with busy + error state. */
export function useAction<A extends unknown[], R>(
  run: (...args: A) => Promise<R>,
) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const exec = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setBusy(true);
      setError(null);
      try {
        return await run(...args);
      } catch (e) {
        setError(errorMessage(e));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [run],
  );
  return { run: exec, busy, error, clearError: () => setError(null) };
}
