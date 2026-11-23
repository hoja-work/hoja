// Inbox model: notification types, grouping by subject, human copy, and the shared unread count.
//
// The server stores one row per event. Every event is about one subject: a task, a project, an
// initiative or a cycle. The inbox shows one row per subject (Linear's model): the latest event
// leads, and read / archive / snooze act on every notification of that subject at once.
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
  | "issueCreated"
  | "projectUpdatePosted"
  | "projectUpdateReply"
  | "projectMention"
  | "projectLeadAssigned"
  | "projectUpdated"
  | "projectMemberAdded"
  | "initiativeUpdatePosted"
  | "initiativeUpdateReply"
  | "initiativeUpdateDue"
  | "initiativeOwnerAssigned"
  | "initiativeUpdated"
  | "cycleStarted"
  | "cycleCompleted"
  | "viewIssueAdded"
  | "viewIssueCompleted";

export type SubjectType = "issue" | "project" | "initiative" | "cycle";
export type ProjectRef = {
  id: string;
  name: string;
  icon?: string | null;
  color?: string | null;
  state?: string | null;
  health?: string | null;
  progress?: number | null;
  targetDate?: string | null;
  lead?: { id?: string; name?: string } | null;
};
export type InitiativeRef = {
  id: string;
  name: string;
  status?: string | null;
  summary?: string | null;
  targetDate?: string | null;
  owner?: { id?: string; name?: string } | null;
};
export type CycleRef = {
  id: string;
  name?: string | null;
  number?: number | null;
  startsAt?: string | null;
  endsAt?: string | null;
  completedAt?: string | null;
  progress?: number | null;
  team?: { id?: string; key?: string; name?: string } | null;
};
export type UpdateRef = {
  id: string;
  body: string;
  health?: string | null;
  createdAt?: string;
};

type StateRef = { id?: string; name?: string; type?: string };
export type NotificationData = {
  /** The followed view a viewIssueAdded / viewIssueCompleted row is about. */
  view?: { id: string; name: string; slugId?: string };
  stateType?: string;
  from?: StateRef;
  to?: StateRef;
  changes?: Record<string, { from?: unknown; to?: unknown }>;
  commentId?: string;
  snippet?: string;
  question?: string;
  updateId?: string;
  health?: string;
  name?: string;
  number?: number;
  teamKey?: string;
  completed?: number;
  total?: number;
  carriedOver?: number;
  unfinished?: number;
  followUp?: number;
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
  subjectType?: SubjectType;
  subjectId?: string;
  project?: ProjectRef | null;
  initiative?: InitiativeRef | null;
  cycle?: CycleRef | null;
  projectUpdate?: UpdateRef | null;
  initiativeUpdate?: UpdateRef | null;
  actor?: { id?: string; name?: string; avatarSeed?: string | null } | null;
};

export type InboxGroup = {
  /** The subject id; groups are keyed by subject. */
  key: string;
  type: SubjectType;
  issue: Issue | null;
  project: ProjectRef | null;
  initiative: InitiativeRef | null;
  cycle: CycleRef | null;
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
  "projectMention",
  "projectLeadAssigned",
  "projectMemberAdded",
  "initiativeOwnerAssigned",
]);

export const NOTIFICATION_FIELDS = `id kind createdAt readAt archivedAt snoozedUntil data subjectType subjectId comment{id body} issue{id identifier title priority priorityLabel state{id name type color} team{id key name} assignee{id name avatarSeed}} project{id name icon color state health progress targetDate lead{id name}} initiative{id name status summary targetDate owner{id name avatarSeed}} cycle{id name number startsAt endsAt completedAt progress team{id key name}} projectUpdate{id body health createdAt} initiativeUpdate{id body health createdAt} actor{id name avatarSeed}`;

export function subjectOf(n: InboxNotification): {
  type: SubjectType;
  id: string;
} {
  const type: SubjectType =
    n.subjectType ||
    (n.project
      ? "project"
      : n.initiative
        ? "initiative"
        : n.cycle
          ? "cycle"
          : "issue");
  const id =
    n.subjectId ||
    n.issue?.id ||
    n.project?.id ||
    n.initiative?.id ||
    n.cycle?.id ||
    `missing:${n.id}`;
  return { type, id };
}

/** The subject's name as the row shows it. */
export function subjectTitle(g: InboxGroup): string {
  if (g.type === "project") return g.project?.name || "Project unavailable";
  if (g.type === "initiative")
    return g.initiative?.name || "Initiative unavailable";
  if (g.type === "cycle") {
    const c = g.cycle;
    const team = c?.team?.key || dataOf(g.latest)?.teamKey;
    const name = c?.name || (c?.number ? `Cycle ${c.number}` : "Cycle");
    return team ? `${team} · ${name}` : name;
  }
  return g.issue?.title || "Task unavailable";
}
export function subjectLabel(type: SubjectType): string {
  return {
    issue: "Task",
    project: "Project",
    initiative: "Initiative",
    cycle: "Cycle",
  }[type];
}

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
    const subject = subjectOf(n);
    const key = subject.id;
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        type: subject.type,
        issue: n.issue || null,
        project: n.project || null,
        initiative: n.initiative || null,
        cycle: n.cycle || null,
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
  if (field === "targetDate" || field === "target_date") {
    if (!to) return "removed the target date";
    const t = Date.parse(String(to));
    return Number.isFinite(t)
      ? `set the target date to ${new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })}`
      : "changed the target date";
  }
  if (field === "state" || field === "status")
    return to ? `set the status to ${String(to)}` : "changed the status";
  if (field === "lead" || field === "owner") {
    const name = (to as { name?: string } | null)?.name;
    return name ? `made ${name} the ${field}` : `removed the ${field}`;
  }
  return `changed ${field}`;
}

function changesText(data: NotificationData, fallback: string): string {
  const changes = Object.entries(data?.changes || {});
  if (!changes.length) return fallback;
  return changes.map(([k, v]) => formatChange(k, v?.to)).join(", ");
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
    case "issueUpdated":
      return changesText(data, "updated the task");
    case "projectUpdatePosted":
    case "initiativeUpdatePosted":
      return data?.health
        ? `posted an update · ${healthLabel(data.health)}`
        : "posted an update";
    case "projectUpdateReply":
      return "replied to an update";
    case "projectMention":
      return "mentioned you";
    case "projectLeadAssigned":
      return "made you the project lead";
    case "projectMemberAdded":
      return "added you to the project";
    case "initiativeOwnerAssigned":
      return "made you the initiative owner";
    case "initiativeUpdateReply":
      return "replied to an update";
    case "initiativeUpdateDue":
      return data?.followUp
        ? "Reminder: an update on this initiative is still due"
        : "An update on this initiative is due";
    case "projectUpdated":
      return changesText(data, "updated the project");
    case "initiativeUpdated":
      return changesText(data, "updated the initiative");
    case "cycleStarted":
      return "started";
    case "cycleCompleted":
      return typeof data?.completed === "number" &&
        typeof data?.total === "number"
        ? `ended · ${data.completed} of ${data.total} done${cycleLeftover(data)}`
        : "ended";
    case "viewIssueAdded":
      return data?.view?.name
        ? `added it to ${data.view.name}`
        : "added it to a view you follow";
    case "viewIssueCompleted":
      return data?.view?.name
        ? `${data.stateType === "canceled" ? "canceled" : "completed"} it in ${data.view.name}`
        : "completed it in a view you follow";
    default:
      return "updated the task";
  }
}

/** ", 3 carried over" when the rollover moved them, ", 3 unfinished" when the cycle just ended. */
export function cycleLeftover(data: NotificationData): string {
  if (data?.carriedOver) return `, ${data.carriedOver} carried over`;
  if (data?.unfinished) return `, ${data.unfinished} unfinished`;
  return "";
}

export function healthLabel(health?: string | null): string {
  if (!health) return "";
  const h = health.toLowerCase().replace(/[^a-z]/g, "");
  return h === "ontrack"
    ? "On track"
    : h === "atrisk"
      ? "At risk"
      : h === "offtrack"
        ? "Off track"
        : health;
}

/** Cycle events have no person behind them; everything else does. */
export function isSystemEvent(n: InboxNotification): boolean {
  return n.kind === "cycleStarted" || n.kind === "cycleCompleted";
}

/** The quoted text under a comment, mention or update, when there is one. */
export function excerpt(n: InboxNotification): string {
  const data = dataOf(n);
  const body =
    n.comment?.body ||
    data?.snippet ||
    data?.question ||
    n.projectUpdate?.body ||
    n.initiativeUpdate?.body ||
    "";
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
