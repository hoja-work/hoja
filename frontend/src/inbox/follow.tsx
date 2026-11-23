/* Following a subject: a task, a project, an initiative or a cycle.
 *
 * Following is what puts a subject's events in your inbox: comments and status on a task, posted
 * updates and status/target/lead changes on a project or initiative, the start and end of a cycle.
 * Creators, leads, owners and people who take part are subscribed automatically; this is the
 * manual switch. State is shared per subject, so every Follow control for it agrees. */
import { useEffect, useState, useSyncExternalStore } from "react";
import { Bell, BellRing } from "lucide-react";
import { gql } from "../main";
import { errorText } from "../localFirst";

export type FollowType = "issue" | "project" | "initiative" | "cycle";

const MUTATIONS: Record<FollowType, [string, string]> = {
  issue: ["issueSubscribe", "issueUnsubscribe"],
  project: ["projectSubscribe", "projectUnsubscribe"],
  initiative: ["initiativeSubscribe", "initiativeUnsubscribe"],
  cycle: ["cycleSubscribe", "cycleUnsubscribe"],
};

const state = new Map<string, boolean>();
const inflight = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();
let revision = 0;
const emit = () => {
  revision++;
  listeners.forEach((l) => l());
};
const keyOf = (type: FollowType, id: string) => `${type}:${id}`;

/** Seed from data already in hand (a row that fetched `viewerSubscribed`), so no extra request runs. */
export function primeFollowing(type: FollowType, id: string, on: boolean) {
  const key = keyOf(type, id);
  if (state.get(key) === on) return;
  state.set(key, on);
  emit();
}

function load(type: FollowType, id: string): Promise<void> {
  const key = keyOf(type, id);
  const running = inflight.get(key);
  if (running) return running;
  const p = gql(`query($id:String!){${type}(id:$id){id viewerSubscribed}}`, {
    id,
  })
    .then((d) => {
      const v = d?.[type]?.viewerSubscribed;
      if (typeof v === "boolean") {
        state.set(key, v);
        emit();
      }
    })
    .catch(() => undefined)
    .finally(() => inflight.delete(key));
  inflight.set(key, p);
  return p;
}

export async function setFollowing(
  type: FollowType,
  id: string,
  on: boolean,
): Promise<void> {
  const key = keyOf(type, id);
  const before = state.get(key);
  state.set(key, on);
  emit();
  try {
    const field = MUTATIONS[type][on ? 0 : 1];
    const d = await gql(`mutation($id:String!){${field}(id:$id)}`, { id });
    if (d?.[field] === false) throw Error("The server refused the change.");
  } catch (e) {
    if (before === undefined) state.delete(key);
    else state.set(key, before);
    emit();
    throw e;
  }
}

/** `null` while unknown. */
export function useFollowing(
  type: FollowType,
  id: string | null | undefined,
  known?: boolean | null,
): boolean | null {
  useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => revision,
  );
  useEffect(() => {
    if (!id) return;
    if (typeof known === "boolean") primeFollowing(type, id, known);
    else if (!state.has(keyOf(type, id))) void load(type, id);
  }, [type, id, known]);
  if (!id) return null;
  const v = state.get(keyOf(type, id));
  return v === undefined ? null : v;
}

const WHAT: Record<FollowType, string> = {
  issue: "comments and status changes on this task",
  project:
    "updates, replies, and status, target-date and lead changes on this project",
  initiative:
    "updates, and status, target-date and owner changes on this initiative",
  cycle: "when this cycle starts and ends",
};

export function followHint(type: FollowType, on: boolean): string {
  return on
    ? `You get ${WHAT[type]} in your inbox. Unfollow to stop.`
    : `Follow to get ${WHAT[type]} in your inbox.`;
}

/** A compact Follow / Following switch. `compact` drops the label for dense rows. */
export function FollowButton({
  type,
  id,
  known,
  compact = false,
  className = "",
  onError,
}: {
  type: FollowType;
  id: string;
  known?: boolean | null;
  compact?: boolean;
  className?: string;
  onError?: (message: string) => void;
}) {
  const following = useFollowing(type, id, known);
  const [busy, setBusy] = useState(false);
  const on = following === true;
  const label = on ? "Following" : "Follow";
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={`${label}: ${followHint(type, on)}`}
      title={followHint(type, on)}
      disabled={busy || following === null}
      data-follow-button={type}
      className={`inline-flex flex-none items-center gap-1.5 rounded-md border border-line bg-transparent px-2 py-1 text-xs text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none aria-pressed:bg-subtle aria-pressed:text-foreground disabled:cursor-default disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 ${compact ? "px-1.5" : ""} ${className}`}
      onClick={async (e) => {
        e.stopPropagation();
        setBusy(true);
        try {
          await setFollowing(type, id, !on);
        } catch (err) {
          onError?.(
            `Couldn’t ${on ? "unfollow" : "follow"}: ${errorText(err)}`,
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      {on ? <BellRing size={13} /> : <Bell size={13} />}
      {!compact && <span>{label}</span>}
    </button>
  );
}
