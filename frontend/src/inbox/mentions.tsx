/* @mentions in comment boxes. The server parses `@handle` in a comment body and notifies the person
 * it names (an "issueMention" in their inbox), matching the handle against a member's display name,
 * their name with spaces removed, or their email's local part. This offers the handle as you type. */
import { useState, type KeyboardEvent, type RefObject } from "react";
import { Avatar } from "../graph/shared";

export type Mentionable = { id: string; name: string; email?: string };

const HANDLE = /^[A-Za-z0-9._-]+$/;
export function mentionHandle(m: Mentionable): string {
  const squashed = (m.name || "").replace(/\s+/g, "");
  if (squashed && HANDLE.test(squashed)) return squashed;
  const local = (m.email || "").split("@")[0];
  return local && HANDLE.test(local) ? local : squashed;
}

const TRIGGER = /(^|\s)@([A-Za-z0-9._-]*)$/;

export function useMentions(
  ref: RefObject<HTMLTextAreaElement | null>,
  members: Mentionable[],
  value: string,
  setValue: (next: string) => void,
) {
  const [query, setQuery] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const q = (query || "").toLowerCase();
  const matches =
    query === null
      ? []
      : members
          .filter(
            (m) =>
              m.name?.toLowerCase().includes(q) ||
              mentionHandle(m).toLowerCase().startsWith(q) ||
              (m.email || "").toLowerCase().startsWith(q),
          )
          .slice(0, 6);
  const track = () => {
    const el = ref.current;
    if (!el) return;
    const m = TRIGGER.exec(
      el.value.slice(0, el.selectionStart ?? el.value.length),
    );
    setQuery(m ? m[2] : null);
    setIndex(0);
  };
  const pick = (m: Mentionable) => {
    const el = ref.current;
    const caret = el?.selectionStart ?? value.length;
    const before = value
      .slice(0, caret)
      .replace(/@([A-Za-z0-9._-]*)$/, `@${mentionHandle(m)} `);
    setValue(before + value.slice(caret));
    setQuery(null);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(before.length, before.length);
    });
  };
  /** Returns true when the key drove the menu and the textarea must not see it. */
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!matches.length) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const step = e.key === "ArrowDown" ? 1 : -1;
      setIndex((i) => (i + step + matches.length) % matches.length);
    } else if (
      (e.key === "Enter" && !e.metaKey && !e.ctrlKey) ||
      e.key === "Tab"
    ) {
      pick(matches[Math.min(index, matches.length - 1)]);
    } else if (e.key === "Escape") {
      setQuery(null);
    } else return false;
    e.preventDefault();
    e.stopPropagation();
    return true;
  };
  return {
    matches,
    index,
    pick,
    track,
    onKeyDown,
    close: () => setQuery(null),
  };
}

export function MentionMenu({
  matches,
  index,
  pick,
}: {
  matches: Mentionable[];
  index: number;
  pick: (m: Mentionable) => void;
}) {
  if (!matches.length) return null;
  return (
    <ul
      role="listbox"
      aria-label="Mention someone"
      className="absolute bottom-full left-2 z-50 mb-1 flex w-64 list-none flex-col gap-0.5 rounded-lg border border-line bg-panel p-1 shadow-(--shadow)"
      data-mention-menu=""
    >
      {matches.map((m, i) => (
        <li key={m.id} role="option" aria-selected={i === index}>
          <button
            type="button"
            // Keep focus (and the caret) in the textarea while choosing.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => pick(m)}
            className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-subtle ${i === index ? "bg-subtle" : "bg-transparent"}`}
          >
            <Avatar name={m.name} size={18} />
            <span className="truncate">{m.name}</span>
            <span className="ml-auto truncate text-xs text-muted">
              @{mentionHandle(m)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
