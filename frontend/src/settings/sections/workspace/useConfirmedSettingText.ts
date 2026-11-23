import { useEffect, useRef, useState } from "react";
import { useSetting, type SaveStatus } from "../../ui";

/* useSettingText with a confirmation step between "the person committed a valid value" and
 * "the request goes out". Same contract (Enter or blur commits, Escape reverts, validation
 * runs first), but a valid change parks in `pending` until `confirm()` or `cancel()`, so the
 * caller can show a ConfirmAction that names the consequence (a changed URL breaks links).
 * Proposal: fold this into useSettingText as a `confirm` option. */
export function useConfirmedSettingText({
  value,
  save,
  validate,
  normalize = (v) => v.trim(),
}: {
  value: string;
  save: (next: string, previous: string) => Promise<unknown>;
  validate?: (next: string) => string | null;
  normalize?: (raw: string) => string;
}) {
  const setting = useSetting({ value, save });
  const [draft, setDraft] = useState(value);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  // Escape blurs the field; that blur must not commit the draft it just threw away (the
  // blur handler still sees the pre-Escape draft, because state updates are batched).
  const reverting = useRef(false);
  // The dialog reports "closed" after a confirmed save too; that close is not a cancel.
  const confirmed = useRef(false);
  useEffect(() => setDraft(setting.value), [setting.value]);

  const commit = () => {
    confirmed.current = false;
    if (pending !== null) return;
    const next = normalize(draft);
    if (next === setting.value) {
      setDraft(setting.value);
      setInvalid(null);
      return;
    }
    const problem = validate?.(next) ?? null;
    setInvalid(problem);
    if (!problem) setPending(next);
  };
  const revert = () => {
    setDraft(setting.value);
    setInvalid(null);
  };

  const status: SaveStatus = invalid
    ? { state: "error", error: invalid, retry: null }
    : setting.status;

  return {
    status,
    draft,
    committed: setting.value,
    pending,
    /** Send the parked value. Resolves once the request settled; failures show on the row. */
    confirm: async () => {
      const next = pending;
      if (next === null) return;
      await setting.set(next);
      confirmed.current = true;
      setPending(null);
    },
    cancel: () => {
      if (confirmed.current) {
        confirmed.current = false;
        return;
      }
      setPending(null);
      revert();
    },
    inputProps: {
      value: draft,
      "aria-invalid": status.state === "error" || undefined,
      onChange: (e: React.ChangeEvent<HTMLInputElement>) => {
        setDraft(e.target.value);
        if (invalid) setInvalid(null);
      },
      onBlur: () => {
        if (reverting.current) {
          reverting.current = false;
          return;
        }
        commit();
      },
      onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          revert();
          reverting.current = true;
          e.currentTarget.blur();
          reverting.current = false;
        }
      },
    },
  };
}
