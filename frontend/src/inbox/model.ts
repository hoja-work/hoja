// Inbox model: notification types, grouping by task, human copy, and the shared unread count.
//
// The server stores one row per event. The inbox shows one row per task (Linear's model): the
// latest event leads, and read / archive / snooze act on every notification of that task at once.
import { useEffect, useSyncExternalStore } from "react";
import { gql } from "../main";
import type { Issue } from "../main";
import { useEntityVersion } from "../sync";

export type InboxKind =
  | "issueAssigned"
  | "issueMention"
  | "issueComment"
  | "issueStatusChanged"
  | "issueUpdated"
  | "issueDecision"
  | "issueCreated";

type StateRef = { id?: string; name?: string; type?: string };
export type NotificationData = {
  from?: StateRef;
  to?: StateRef;
  changes?: Record<string, { from?: unknown; to?: unknown }>;
  commentId?: string;
  snippet?: string;
  question?: string;
} | null;

export type InboxNotification = {
  id: string;
  kind: InboxKind | string;
  createdAt: string;
  readAt?: string | null;
  archivedAt?: string | null;
  snoozedUntil?: string | null;
  data?: NotificationData | string;
  comment?: { id: string; body: string } | null;
  issue?: Issue | null;
  actor?: { id?: string; name?: string } | null;
};

export type InboxGroup = {
  /** The task id; groups are keyed by task. */
  key: string;
  issue: Issue | null;
  latest: InboxNotification;
  items: InboxNotification[];
  unreadCount: number;
  /** Something asked this person directly: an assignment, a mention or a decision. */
  direct: boolean;
};

/** Kinds that ask the reader to act. The Priority tab shows a task when any of its events is one. */
export const DIRECT_KINDS = new Set<string>([
  "issueAssigned",
  "issueMention",
  "issueDecision",
]);

export const NOTIFICATION_FIELDS = `id kind createdAt readAt archivedAt snoozedUntil data comment{id body} issue{id identifier title priority priorityLabel state{id name type color} team{id key name} assignee{id name}} actor{id name}`;

export function dataOf(n: InboxNotification): NotificationData {
  if (!n.data) return null;
  if (typeof n.data === "string") {
    try {
      return JSON.parse(n.data) as NotificationData;
    } catch {
      return null;
    }
  }
  return n.data;
}

export function groupNotifications(list: InboxNotification[]): InboxGroup[] {
  const groups = new Map<string, InboxGroup>();
  for (const n of list) {
    const key = n.issue?.id || `missing:${n.id}`;
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        issue: n.issue || null,
        latest: n,
        items: [],
        unreadCount: 0,
        direct: false,
      };
      groups.set(key, g);
    }
    g.items.push(n);
    if (!n.readAt) g.unreadCount++;
    if (DIRECT_KINDS.has(n.kind)) g.direct = true;
    if (n.createdAt > g.latest.createdAt) g.latest = n;
  }
  return [...groups.values()].sort((a, b) =>
    a.latest.createdAt < b.latest.createdAt ? 1 : -1,
  );
}

const PRIORITY = ["No priority", "Urgent", "High", "Medium", "Low"];

function plain(text: string, max = 140): string {
  const t = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`>#~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

function formatChange(field: string, to: unknown): string {
  if (field === "priority")
    return typeof to === "number" && PRIORITY[to]
      ? to === 0
        ? "removed the priority"
        : `set priority to ${PRIORITY[to]}`
      : "changed the priority";
  if (field === "dueDate" || field === "due_date") {
    if (!to) return "removed the due date";
    const t = Date.parse(String(to));
    return Number.isFinite(t)
      ? `set the due date to ${new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })}`
      : "changed the due date";
  }
  return `changed ${field}`;
}

/** What happened, as a phrase that follows the actor's name ("Ana" + "moved it to Done"). */
export function describe(n: InboxNotification): string {
  const data = dataOf(n);
  switch (n.kind) {
    case "issueAssigned":
      return "assigned you";
    case "issueMention":
      return "mentioned you";
    case "issueComment":
      return "commented";
    case "issueDecision":
      return "asked you for a decision";
    case "issueCreated":
      return "created the task";
    case "issueStatusChanged":
      return data?.to?.name
        ? `moved it to ${data.to.name}`
        : "changed the status";
    case "issueUpdated": {
      const changes = Object.entries(data?.changes || {});
      if (!changes.length) return "updated the task";
      return changes.map(([k, v]) => formatChange(k, v?.to)).join(", ");
    }
    default:
      return "updated the task";
  }
}

/** The quoted text under a comment or mention, when there is one. */
export function excerpt(n: InboxNotification): string {
  const data = dataOf(n);
  const body = n.comment?.body || data?.snippet || data?.question || "";
  return body ? plain(body) : "";
}

/** Compact relative time, the way a triage list reads: now, 5m, 3h, 2d, Nov 3. */
export function shortTime(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const m = Math.floor((now - t) / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  const date = new Date(t);
  return date.toLocaleDateString(
    undefined,
    date.getFullYear() === new Date(now).getFullYear()
      ? { month: "short", day: "numeric" }
      : { month: "short", day: "numeric", year: "numeric" },
  );
}

export function fullTime(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t)
    ? new Date(t).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "";
}

/* -------- snooze presets -------- */

export type SnoozeOption = { id: string; label: string; until: () => Date };
function at(days: number, hour: number): Date {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hour, 0, 0, 0);
  return d;
}
export const SNOOZE_OPTIONS: SnoozeOption[] = [
  {
    id: "hours",
    label: "In 3 hours",
    until: () => new Date(Date.now() + 3 * 3600_000),
  },
  { id: "tomorrow", label: "Tomorrow morning", until: () => at(1, 9) },
  {
    id: "monday",
    label: "Next Monday",
    until: () => at((8 - new Date().getDay()) % 7 || 7, 9),
  },
  { id: "week", label: "In a week", until: () => at(7, 9) },
];

/* -------- shared unread count (sidebar badge, tab title) -------- */

let unread = 0;
const listeners = new Set<() => void>();
function emit() {
  listeners.forEach((l) => l());
}
export function setInboxUnread(n: number) {
  const next = Math.max(0, n | 0);
  if (next === unread) return;
  unread = next;
  emit();
}
export function adjustInboxUnread(delta: number) {
  setInboxUnread(unread + delta);
}
export function useInboxUnread(): number {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => unread,
  );
}
let inflight: Promise<void> | null = null;
export function refreshInboxUnread(): Promise<void> {
  if (inflight) return inflight;
  inflight = gql(`query{inboxUnreadCount}`)
    .then((d) => setInboxUnread(Number(d.inboxUnreadCount) || 0))
    .catch(() => undefined)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Keeps the shared unread count fresh: on mount, on notification changes, and on tab focus. */
export function useInboxUnreadSync(enabled: boolean) {
  const version = useEntityVersion(["notification"]);
  useEffect(() => {
    if (enabled) void refreshInboxUnread();
  }, [enabled, version]);
  useEffect(() => {
    if (!enabled) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshInboxUnread();
    };
    document.addEventListener("visibilitychange", onVisible);
    // Snoozed notifications come back on their own schedule; nothing writes a change event then.
    const timer = window.setInterval(
      () => void refreshInboxUnread(),
      5 * 60_000,
    );
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(timer);
    };
  }, [enabled]);
}
