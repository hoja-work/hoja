import { styleClasses, styleHooks } from "./shared";
import {
  Crumb,
  CrumbCurrent,
  CrumbSep,
  DetailColumns,
  DetailHeader,
  DetailNotice,
  DetailPage,
  DetailSection,
  DetailTitle,
  MarkdownField,
  PropertiesPanel,
} from "./detail";
import { StateGlyph } from "./stateGlyph";
import { actorLabel, eventPhrase } from "./activityEvents";
import { TaskUsageSection } from "../profiles/TaskUsageSection";
import React, {
  memo,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { confirmDialog } from "../dialogs";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Activity,
  FileText,
  Link2,
  Plus,
  SlidersHorizontal,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { attachGithubReferences, gql, relTime, stateKey } from "../main";
import { MentionMenu, useMentions } from "../inbox/mentions";
import type {
  Cycle,
  Issue,
  IssueActivityEvent,
  IssueLabel,
  IssueSla,
  IssueState,
  Project,
  Team,
  Viewer,
} from "../main";
import { Markdown } from "../markdown";
import { MobileNavTrigger } from "../mobileNav";
import { isTraceAttachment } from "../traces/trace";
import { TraceViewer } from "../traces/TraceViewer";
import {
  addLocalIssue,
  getSync,
  guessNextIdentifier,
  isLocalIssueId,
  localIssueId,
  onEntityChange,
  useEntityVersion,
  useIssueSelect,
  useSyncMeta,
} from "../sync";
import {
  Avatar,
  CycleChip,
  CycleGlyph,
  DueChip,
  FavStar,
  IconBtn,
  LabelChip,
  LinMenu,
  LinPopover,
  GraphStatusIcon,
  PriorityIcon,
  ProjectChip,
  StatusAgeIcon,
  SubIssuePill,
  Tip,
  cx,
  fmtDate,
  fmtFullDate,
  type MenuItem,
} from "./shared";
import {
  runExtensionAction,
  useIssueExtensionActions,
  type ExtensionAction,
} from "../extensions";
import {
  answerDecision,
  cancelDecision,
  decisionFields,
  type IssueDecision,
} from "../decisions";
import {
  ChosenDecisionMediaPreview,
  VisualDecisionOptions,
} from "../VisualDecisionOptions";
import { getAgentsAdapter, type AgentChoice } from "../agents/adapter";

/* Linear 1:1 Issues list + board + detail. */

export type LinAttachment = {
  id: string;
  title?: string;
  subtitle?: string;
  url: string;
  contentType?: string | null;
  createdAt?: string;
};
export type LinGithubLink = {
  id: string;
  kind: string;
  externalId: string;
  url: string;
  title: string;
  state?: string | null;
  author?: string | null;
  sha?: string | null;
  environment?: string | null;
  createdAt?: string;
};
export type LinIssue = Omit<Issue, "subscribers"> & {
  estimate?: number | null;
  branchName?: string | null;
  url?: string | null;
  parent?: { id: string; identifier: string; title: string } | null;
  subscribers?: {
    nodes: {
      id: string;
      name?: string;
      avatarUrl?: string;
      avatarSeed?: string | null;
    }[];
  };
  attachments?: { nodes: LinAttachment[] };
  githubLinks?: LinGithubLink[];
  activity?: { nodes: IssueActivityEvent[] };
  /** Open/deferred questions, newest first (docs/decisions.md); decisionNeeded flags them. */
  decisions?: IssueDecision[];
};

export const linIssueFields = `{ id identifier title description createdAt updatedAt stateChangedAt dueDate priority priorityLabel estimate branchName url decisionNeeded sla { state resolutionDueAt firstResponseDueAt firstRespondedAt breachedAt overdueMinutes pausedMinutes cycleEndsAt } state { id name color type } team { id key name } project { id name } assignee { id name avatarSeed } creator { id name avatarSeed } cycle { id name number } parent { id identifier title } labels { nodes { id name color } } }`;

/* ---------------- filter + display models ---------------- */

export type LinFilters = {
  q: string;
  status: string[];
  assignee: string[];
  priority: number[];
  labels: string[];
  projects: string[];
  cycles: string[];
  creators: string[];
  subscribers: string[];
  dueFrom: string;
  dueTo: string;
  createdFrom: string;
  createdTo: string;
};
export const emptyFilters: LinFilters = {
  q: "",
  status: [],
  assignee: [],
  priority: [],
  labels: [],
  projects: [],
  cycles: [],
  creators: [],
  subscribers: [],
  dueFrom: "",
  dueTo: "",
  createdFrom: "",
  createdTo: "",
};

export type LinDisplay = {
  /** The agent surface groups by `runner`, and the audit log by `actor` or `action`
   * (HOJA-602-A-16). The issue list never sets those; `groupLinIssues` treats any grouping it
   * does not implement as one ungrouped list, so a stored value cannot hide rows. */
  grouping:
    | "none"
    | "status"
    | "priority"
    | "assignee"
    | "labels"
    | "project"
    | "runner"
    | "actor"
    | "action"
    | "agent"
    | "model"
    | "month";
  ordering: "created" | "updated" | "priority";
  direction: "desc" | "asc";
  hideCompleted: boolean;
  showSubIssues: boolean;
  showEmptyColumns: boolean;
  hiddenColumns: string[];
  props: Record<
    | "id"
    | "status"
    | "assignee"
    | "priority"
    | "project"
    | "dueDate"
    | "cycle"
    | "labels"
    | "created"
    | "updated",
    boolean
  >;
};
export const defaultDisplay: LinDisplay = {
  grouping: "status",
  ordering: "updated",
  direction: "desc",
  hideCompleted: false,
  showSubIssues: true,
  showEmptyColumns: true,
  hiddenColumns: [],
  props: {
    id: true,
    status: true,
    assignee: true,
    priority: true,
    project: true,
    dueDate: true,
    cycle: true,
    labels: true,
    created: true,
    updated: true,
  },
};

function inRange(
  iso: string | undefined | null,
  from: string,
  to: string,
): boolean {
  if (!from && !to) return true;
  if (!iso) return false;
  const d = iso.slice(0, 10);
  return (!from || d >= from) && (!to || d <= to);
}

export function applyLinFilters(issues: LinIssue[], f: LinFilters): LinIssue[] {
  const q = f.q.trim().toLowerCase();
  return issues.filter((i) => {
    if (q && !`${i.identifier} ${i.title}`.toLowerCase().includes(q))
      return false;
    if (f.status.length && !(i.state && f.status.includes(stateKey(i.state))))
      return false;
    if (f.assignee.length) {
      const aid = i.assignee?.id || "unassigned";
      if (!f.assignee.includes(aid)) return false;
    }
    if (f.priority.length && !f.priority.includes(i.priority || 0))
      return false;
    if (
      f.labels.length &&
      !i.labels?.nodes.some((l) => f.labels.includes(l.id))
    )
      return false;
    if (f.projects.length && !(i.project && f.projects.includes(i.project.id)))
      return false;
    if (f.cycles.length) {
      const cid = i.cycle?.id || "none";
      if (!f.cycles.includes(cid)) return false;
    }
    if (f.creators.length && !(i.creator && f.creators.includes(i.creator.id)))
      return false;
    if (
      f.subscribers.length &&
      !i.subscribers?.nodes.some((s) => f.subscribers.includes(s.id))
    )
      return false;
    if (!inRange(i.dueDate, f.dueFrom, f.dueTo)) return false;
    if (!inRange(i.createdAt, f.createdFrom, f.createdTo)) return false;
    return true;
  });
}

export function orderLinIssues(issues: LinIssue[], d: LinDisplay): LinIssue[] {
  const arr = [...issues];
  const dir = d.direction === "desc" ? -1 : 1;
  if (d.ordering === "priority") {
    arr.sort((a, b) => ((a.priority || 5) - (b.priority || 5)) * dir);
    return arr;
  }
  // ISO timestamps order lexicographically; plain comparison is far cheaper than localeCompare on 50k rows.
  const key = d.ordering === "created" ? "createdAt" : "updatedAt";
  arr.sort((a, b) => {
    const ak = a[key] || "",
      bk = b[key] || "";
    return ak === bk ? 0 : ak < bk ? -dir : dir;
  });
  return arr;
}

export function groupLinIssues(
  issues: LinIssue[],
  d: LinDisplay,
  states: IssueState[],
): { key: string; name: string; state?: IssueState; items: LinIssue[] }[] {
  const IMPLEMENTED = ["status", "priority", "assignee", "labels", "project"];
  if (!IMPLEMENTED.includes(d.grouping))
    return [{ key: "all", name: "", items: issues }];
  const groups = new Map<
    string,
    { key: string; name: string; state?: IssueState; items: LinIssue[] }
  >();
  const keyOf = (
    i: LinIssue,
  ): { key: string; name: string; state?: IssueState } => {
    if (d.grouping === "status") {
      const s = i.state;
      return s
        ? { key: stateKey(s), name: s.name, state: s }
        : { key: "none", name: "No status" };
    }
    if (d.grouping === "priority") {
      const names = ["No priority", "Urgent", "High", "Medium", "Low"];
      return {
        key: String(i.priority || 0),
        name: names[i.priority || 0] || "No priority",
      };
    }
    if (d.grouping === "assignee")
      return {
        key: i.assignee?.id || "unassigned",
        name: i.assignee?.name || "Unassigned",
      };
    if (d.grouping === "labels") {
      const l = i.labels?.nodes[0];
      return { key: l?.id || "none", name: l?.name || "No labels" };
    }
    if (d.grouping === "project")
      return {
        key: i.project?.id || "none",
        name: i.project?.name || "No project",
      };
    return { key: "all", name: "" };
  };
  for (const i of issues) {
    const k = keyOf(i);
    if (!groups.has(k.key)) groups.set(k.key, { ...k, items: [] });
    groups.get(k.key)!.items.push(i);
  }
  let out = [...groups.values()];
  if (d.grouping === "status") {
    const order = new Map(states.map((s, idx) => [stateKey(s), idx]));
    out.sort((a, b) => (order.get(a.key) ?? 999) - (order.get(b.key) ?? 999));
  } else {
    out.sort((a, b) => a.name.localeCompare(b.name));
  }
  return out;
}

/* ---------------- filter menu ---------------- */

const FILTER_DEFS = [
  { key: "status", label: "Status", icon: "◔" },
  { key: "assignee", label: "Assignee", icon: "◉" },
  { key: "creator", label: "Creator", icon: "✎" },
  { key: "priority", label: "Priority", icon: "▮" },
  { key: "labels", label: "Labels", icon: "◈" },
  { key: "dates", label: "Dates", icon: "◷" },
  { key: "project", label: "Project", icon: "▣" },
  { key: "cycle", label: "Cycle", icon: "↻" },
  { key: "subscribers", label: "Subscribers", icon: "◎" },
  { key: "content", label: "Content", icon: "≡" },
] as const;
const FILTER_UNAVAILABLE = [
  "AI filter",
  "Advanced filter",
  "Agent",
  "Agent Session",
  "Relations",
  "Suggested label",
  "Project properties",
  "Initiative",
  "Added to cycle",
  "Customers",
  "Auto-closed",
  "Links",
  "Template",
];

/* The filter menu only needs the states it can filter by and the people it can filter on, so it
 * accepts that shape instead of a whole Team. The agent surface passes its own status list and
 * owners here (HOJA-602-A-16) without inventing a Team; a real Team satisfies it unchanged. */
export type FilterTeam = {
  id: string;
  states: { nodes: IssueState[] };
  members?: {
    nodes: {
      id: string;
      name: string;
      email?: string;
      avatarSeed?: string | null;
    }[];
  };
};

export function FilterMenu({
  filters,
  setFilters,
  team,
  teams,
  projects,
  labels,
  cycles,
  onClose,
  anchorClass,
  only,
}: {
  filters: LinFilters;
  setFilters: (f: LinFilters) => void;
  team?: FilterTeam;
  teams: Team[];
  projects: Project[];
  labels: IssueLabel[];
  cycles: Cycle[];
  onClose: () => void;
  anchorClass?: string;
  /** Restrict the menu to these filter definitions. The agent surface reads status, owner and
   * content from the same `LinFilters` shape, and listing priority/labels/cycles there would
   * offer a filter that cannot match anything. Omitted means every definition, as on issues. */
  only?: readonly string[];
}) {
  const [sub, setSub] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const members = team?.members?.nodes || [];
  const toggle = (key: keyof LinFilters, id: string | number) => {
    const arr = [...(filters[key] as (string | number)[])];
    const ix = arr.indexOf(id as never);
    if (ix >= 0) arr.splice(ix, 1);
    else arr.push(id as never);
    setFilters({ ...filters, [key]: arr });
  };
  const summary = (key: keyof LinFilters, total: number) => {
    const n = (filters[key] as unknown[]).length;
    return n ? `${n} selected` : total ? `${total} options` : "";
  };
  const opt = (
    key:
      | "status"
      | "assignee"
      | "priority"
      | "labels"
      | "projects"
      | "cycles"
      | "creators"
      | "subscribers",
    id: string | number,
    label: React.ReactNode,
  ) => (
    <button
      type="button"
      key={String(id)}
      className={styleClasses(
        "in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-2.25 in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-1.75 in-data-[style-hooks~='lin-filtermenu']:px-2.5 in-data-[style-hooks~='lin-filtermenu']:rounded-md in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-left in-data-[style-hooks~='lin-filtermenu']:text-foreground [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle [[data-style-hooks~='lin-filtermenu']_&[aria-checked=\"true\"]]:font-semibold",
      )}
      data-style-hooks={styleHooks(
        "fm-opt in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-[9px] in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-[7px] in-data-[style-hooks~='lin-filtermenu']:px-[10px] in-data-[style-hooks~='lin-filtermenu']:rounded-[6px] in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-left in-data-[style-hooks~='lin-filtermenu']:text-foreground [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle [[data-style-hooks~='lin-filtermenu']_&[aria-checked=\"true\"]]:font-semibold",
      )}
      aria-checked={(filters[key] as unknown[]).includes(id)}
      onClick={() => toggle(key, id)}
    >
      <span
        className={styleClasses(
          "[[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:grid [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:place-items-center [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:flex-none",
        )}
        data-style-hooks={styleHooks(
          "fm-ic [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:grid [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:place-items-center [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:flex-none",
        )}
      >
        {(filters[key] as unknown[]).includes(id) ? (
          <Check size={14} />
        ) : (
          <span style={{ width: 14 }} />
        )}
      </span>
      <span style={{ flex: 1 }}>{label}</span>
    </button>
  );
  const states = team?.states.nodes || [];
  const dateRow = (
    label: string,
    from: keyof LinFilters,
    to: keyof LinFilters,
  ) => (
    <div
      className={styleClasses(
        "in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-2.25 in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-1.75 in-data-[style-hooks~='lin-filtermenu']:px-2.5 in-data-[style-hooks~='lin-filtermenu']:rounded-md in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-left in-data-[style-hooks~='lin-filtermenu']:text-foreground [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle [[data-style-hooks~='lin-filtermenu']_&[aria-checked=\"true\"]]:font-semibold",
      )}
      data-style-hooks={styleHooks(
        "fm-opt in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-[9px] in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-[7px] in-data-[style-hooks~='lin-filtermenu']:px-[10px] in-data-[style-hooks~='lin-filtermenu']:rounded-[6px] in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-left in-data-[style-hooks~='lin-filtermenu']:text-foreground [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle [[data-style-hooks~='lin-filtermenu']_&[aria-checked=\"true\"]]:font-semibold",
      )}
      style={{ cursor: "default" }}
    >
      <span style={{ width: 90, color: "var(--muted)", fontSize: 12 }}>
        {label}
      </span>
      <input
        type="date"
        value={filters[from] as string}
        onChange={(e) => setFilters({ ...filters, [from]: e.target.value })}
        style={{
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: "4px 6px",
          fontSize: 12,
        }}
      />
      <input
        type="date"
        value={filters[to] as string}
        onChange={(e) => setFilters({ ...filters, [to]: e.target.value })}
        style={{
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: "4px 6px",
          fontSize: 12,
        }}
      />
    </div>
  );
  return (
    <LinPopover
      onClose={onClose}
      className={styleClasses(
        cx(
          "absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] w-70 max-h-[min(480px,70vh)] overflow-y-auto",
          anchorClass,
        ),
      )}
      data-style-hooks={styleHooks(
        cx(
          "lin-popover absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] lin-filtermenu w-[280px] max-h-[min(480px,70vh)] overflow-y-auto",
          anchorClass,
        ),
      )}
      role="dialog"
      aria-label="Add filter"
    >
      {!sub && (
        <>
          <div
            className={styleClasses(
              "in-data-[style-hooks~='lin-filtermenu']:p-2 in-data-[style-hooks~='lin-filtermenu']:border-b in-data-[style-hooks~='lin-filtermenu']:border-b-line in-data-[style-hooks~='lin-filtermenu']:sticky in-data-[style-hooks~='lin-filtermenu']:top-0 in-data-[style-hooks~='lin-filtermenu']:bg-panel [[data-style-hooks~='lin-filtermenu']_&_input]:w-full [[data-style-hooks~='lin-filtermenu']_&_input]:border [[data-style-hooks~='lin-filtermenu']_&_input]:border-solid [[data-style-hooks~='lin-filtermenu']_&_input]:border-line [[data-style-hooks~='lin-filtermenu']_&_input]:rounded-md [[data-style-hooks~='lin-filtermenu']_&_input]:py-1.75 [[data-style-hooks~='lin-filtermenu']_&_input]:px-2.5 [[data-style-hooks~='lin-filtermenu']_&_input]:text-[13px] [[data-style-hooks~='lin-filtermenu']_&_input]:bg-bg [[data-style-hooks~='lin-filtermenu']_&_input]:text-foreground [[data-style-hooks~='lin-filtermenu']_&_input]:[outline:0]",
            )}
            data-style-hooks={styleHooks(
              "fm-search in-data-[style-hooks~='lin-filtermenu']:p-[8px] in-data-[style-hooks~='lin-filtermenu']:border-b in-data-[style-hooks~='lin-filtermenu']:border-b-line in-data-[style-hooks~='lin-filtermenu']:sticky in-data-[style-hooks~='lin-filtermenu']:top-0 in-data-[style-hooks~='lin-filtermenu']:bg-panel [[data-style-hooks~='lin-filtermenu']_&_input]:w-full [[data-style-hooks~='lin-filtermenu']_&_input]:border [[data-style-hooks~='lin-filtermenu']_&_input]:border-solid [[data-style-hooks~='lin-filtermenu']_&_input]:border-line [[data-style-hooks~='lin-filtermenu']_&_input]:rounded-[6px] [[data-style-hooks~='lin-filtermenu']_&_input]:py-[7px] [[data-style-hooks~='lin-filtermenu']_&_input]:px-[10px] [[data-style-hooks~='lin-filtermenu']_&_input]:text-[13px] [[data-style-hooks~='lin-filtermenu']_&_input]:bg-bg [[data-style-hooks~='lin-filtermenu']_&_input]:text-foreground [[data-style-hooks~='lin-filtermenu']_&_input]:[outline:0]",
            )}
          >
            <input
              placeholder="Add filter…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div style={{ padding: "4px 0" }}>
            {FILTER_DEFS.filter(
              (d) =>
                (!only || only.includes(d.key)) &&
                d.label.toLowerCase().includes(search.toLowerCase()),
            ).map((d) => (
              <button
                type="button"
                key={d.key}
                className={styleClasses(
                  "in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-2.5 in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-2 in-data-[style-hooks~='lin-filtermenu']:px-3 in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-foreground in-data-[style-hooks~='lin-filtermenu']:text-left [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle",
                )}
                data-style-hooks={styleHooks(
                  "fm-item in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-[10px] in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-[8px] in-data-[style-hooks~='lin-filtermenu']:px-[12px] in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-foreground in-data-[style-hooks~='lin-filtermenu']:text-left [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle",
                )}
                onClick={() => setSub(d.key)}
              >
                <span
                  className={styleClasses(
                    "[[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:grid [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:place-items-center [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:flex-none",
                  )}
                  data-style-hooks={styleHooks(
                    "fm-ic [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:grid [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:place-items-center [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:flex-none",
                  )}
                >
                  {d.icon}
                </span>
                <span style={{ flex: 1 }}>{d.label}</span>
                <span
                  className={styleClasses(
                    "[[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:ml-auto [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-[12px] [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:max-w-30 [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:overflow-hidden [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-ellipsis [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:whitespace-nowrap",
                  )}
                  data-style-hooks={styleHooks(
                    "fm-val [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:ml-auto [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-[12px] [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:max-w-[120px] [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:overflow-hidden [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-ellipsis [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:whitespace-nowrap",
                  )}
                >
                  {d.key === "status"
                    ? summary("status", states.length)
                    : d.key === "assignee"
                      ? summary("assignee", members.length)
                      : d.key === "labels"
                        ? summary("labels", labels.length)
                        : d.key === "project"
                          ? summary("projects", projects.length)
                          : d.key === "cycle"
                            ? summary("cycles", cycles.length)
                            : ""}
                </span>
                <ChevronRight size={14} color="var(--muted)" />
              </button>
            ))}
            {!only &&
              FILTER_UNAVAILABLE.filter((l) =>
                l.toLowerCase().includes(search.toLowerCase()),
              ).map((l) => (
                <Tip
                  key={l}
                  label="Not available in Hoja yet"
                  className={styleClasses(
                    "in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-2.5 in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-2 in-data-[style-hooks~='lin-filtermenu']:px-3 in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-foreground in-data-[style-hooks~='lin-filtermenu']:text-left [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle",
                  )}
                  data-style-hooks={styleHooks(
                    "fm-item in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-[10px] in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-[8px] in-data-[style-hooks~='lin-filtermenu']:px-[12px] in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-foreground in-data-[style-hooks~='lin-filtermenu']:text-left [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle",
                  )}
                >
                  <span
                    className={styleClasses(
                      "in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-2.5 in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-2 in-data-[style-hooks~='lin-filtermenu']:px-3 in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-foreground in-data-[style-hooks~='lin-filtermenu']:text-left [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle",
                    )}
                    data-style-hooks={styleHooks(
                      "fm-item in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-[10px] in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-[8px] in-data-[style-hooks~='lin-filtermenu']:px-[12px] in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:text-foreground in-data-[style-hooks~='lin-filtermenu']:text-left [[data-style-hooks~='lin-filtermenu']_&:hover]:bg-subtle",
                    )}
                    style={{
                      opacity: 0.45,
                      cursor: "not-allowed",
                      width: "100%",
                    }}
                  >
                    <span
                      className={styleClasses(
                        "[[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:grid [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:place-items-center [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:flex-none",
                      )}
                      data-style-hooks={styleHooks(
                        "fm-ic [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:text-muted [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:grid [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:place-items-center [[data-style-hooks~='lin-filtermenu']_[data-style-hooks~='fm-item']_&]:flex-none",
                      )}
                    >
                      ○
                    </span>
                    <span style={{ flex: 1 }}>{l}</span>
                  </span>
                </Tip>
              ))}
          </div>
          <div style={{ padding: 10, borderTop: "1px solid var(--line)" }}>
            <button
              type="button"
              className={styleClasses(
                "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
              )}
              data-style-hooks={styleHooks(
                "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
              )}
              style={{ width: "100%", justifyContent: "center" }}
              onClick={() => setFilters({ ...emptyFilters })}
            >
              Clear all filters
            </button>
          </div>
        </>
      )}
      {sub && (
        <>
          <button
            type="button"
            className={styleClasses(
              "in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-2 in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-2 in-data-[style-hooks~='lin-filtermenu']:px-3 in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:border-b in-data-[style-hooks~='lin-filtermenu']:border-b-line in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:font-semibold in-data-[style-hooks~='lin-filtermenu']:text-foreground",
            )}
            data-style-hooks={styleHooks(
              "fm-back in-data-[style-hooks~='lin-filtermenu']:flex in-data-[style-hooks~='lin-filtermenu']:items-center in-data-[style-hooks~='lin-filtermenu']:gap-[8px] in-data-[style-hooks~='lin-filtermenu']:w-full in-data-[style-hooks~='lin-filtermenu']:py-[8px] in-data-[style-hooks~='lin-filtermenu']:px-[12px] in-data-[style-hooks~='lin-filtermenu']:bg-transparent in-data-[style-hooks~='lin-filtermenu']:border-b in-data-[style-hooks~='lin-filtermenu']:border-b-line in-data-[style-hooks~='lin-filtermenu']:text-[13px] in-data-[style-hooks~='lin-filtermenu']:font-semibold in-data-[style-hooks~='lin-filtermenu']:text-foreground",
            )}
            onClick={() => setSub(null)}
          >
            ‹ {FILTER_DEFS.find((d) => d.key === sub)?.label}
          </button>
          <div
            className={styleClasses(
              "in-data-[style-hooks~='lin-filtermenu']:border-t in-data-[style-hooks~='lin-filtermenu']:border-t-line in-data-[style-hooks~='lin-filtermenu']:p-1.5",
            )}
            data-style-hooks={styleHooks(
              "fm-sub in-data-[style-hooks~='lin-filtermenu']:border-t in-data-[style-hooks~='lin-filtermenu']:border-t-line in-data-[style-hooks~='lin-filtermenu']:p-[6px]",
            )}
          >
            {sub === "status" &&
              states.map((s) =>
                opt(
                  "status",
                  stateKey(s),
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <GraphStatusIcon state={s} />
                    {s.name}
                  </span>,
                ),
              )}
            {sub === "assignee" && (
              <>
                {opt("assignee", "unassigned", "Unassigned")}
                {members.map((m) =>
                  opt(
                    "assignee",
                    m.id,
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 8,
                      }}
                    >
                      <Avatar name={m.name} seed={m.avatarSeed} size={18} />
                      {m.name || m.email}
                    </span>,
                  ),
                )}
              </>
            )}
            {sub === "creator" &&
              members.map((m) =>
                opt(
                  "creators",
                  m.id,
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <Avatar name={m.name} seed={m.avatarSeed} size={18} />
                    {m.name || m.email}
                  </span>,
                ),
              )}
            {sub === "priority" &&
              [
                ["1", "Urgent"],
                ["2", "High"],
                ["3", "Medium"],
                ["4", "Low"],
                ["0", "No priority"],
              ].map(([v, l]) =>
                opt(
                  "priority",
                  Number(v),
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <PriorityIcon priority={Number(v)} />
                    {l}
                  </span>,
                ),
              )}
            {sub === "labels" &&
              labels.map((l) => opt("labels", l.id, <LabelChip label={l} />))}
            {sub === "dates" && (
              <>
                {dateRow("Due date", "dueFrom", "dueTo")}
                {dateRow("Created", "createdFrom", "createdTo")}
              </>
            )}
            {sub === "project" &&
              projects.map((p) => opt("projects", p.id, p.name))}
            {sub === "cycle" && (
              <>
                {opt("cycles", "none", "No cycle")}
                {cycles
                  .filter((c) => c.team?.id === team?.id)
                  .map((c) =>
                    opt("cycles", c.id, c.name || `Cycle ${c.number}`),
                  )}
              </>
            )}
            {sub === "subscribers" &&
              members.map((m) =>
                opt(
                  "subscribers",
                  m.id,
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <Avatar name={m.name} seed={m.avatarSeed} size={18} />
                    {m.name || m.email}
                  </span>,
                ),
              )}
            {sub === "content" && (
              <div style={{ padding: 8 }}>
                <input
                  placeholder="Search title or identifier…"
                  value={filters.q}
                  onChange={(e) =>
                    setFilters({ ...filters, q: e.target.value })
                  }
                  style={{
                    width: "100%",
                    border: "1px solid var(--line)",
                    borderRadius: 6,
                    padding: "7px 10px",
                    fontSize: 13,
                  }}
                />
              </div>
            )}
          </div>
        </>
      )}
    </LinPopover>
  );
}

/* ---------------- display options panel ---------------- */

export function DisplayPanel({
  display,
  setDisplay,
  onClose,
  anchorClass,
  groupingOptions,
  groupingLabel = "Group tasks by",
  propertyRows,
  entityToggles = true,
}: {
  display: LinDisplay;
  setDisplay: (d: LinDisplay) => void;
  onClose: () => void;
  anchorClass?: string;
  /** Which groupings this surface can actually produce. Defaults to the issue list's set. */
  groupingOptions?: [string, string][];
  groupingLabel?: string;
  /** Replace the issue property checkboxes with the caller's own fields (agent surface). */
  propertyRows?: {
    key: string;
    label: string;
    checked: boolean;
    onToggle: () => void;
  }[];
  /** The completed/sub-task/empty-column switches are issue concepts; a caller can drop them. */
  entityToggles?: boolean;
}) {
  const set = (patch: Partial<LinDisplay>) =>
    setDisplay({ ...display, ...patch });
  const toggleProp = (k: keyof LinDisplay["props"]) =>
    set({ props: { ...display.props, [k]: !display.props[k] } });
  const PROP_LABELS: [keyof LinDisplay["props"], string][] = [
    ["id", "ID"],
    ["status", "Status"],
    ["assignee", "Assignee"],
    ["priority", "Priority"],
    ["project", "Project"],
    ["dueDate", "Due date"],
    ["cycle", "Cycle"],
    ["labels", "Labels"],
    ["created", "Created"],
    ["updated", "Updated"],
  ];
  const UNAVAILABLE_PROPS = [
    "Milestone",
    "Links",
    "Customers",
    "Customer revenue",
    "Time in status",
  ];
  const GROUPS: [string, string][] = groupingOptions || [
    ["none", "No grouping"],
    ["status", "Status"],
    ["priority", "Priority"],
    ["assignee", "Assignee"],
    ["labels", "Labels"],
    ["project", "Project"],
  ];
  return (
    <LinPopover
      onClose={onClose}
      className={styleClasses(
        cx(
          "absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] w-75 max-h-[min(560px,75vh)] overflow-y-auto p-1.5 [&_select]:flex-1 [&_select]:border [&_select]:border-solid [&_select]:border-line [&_select]:rounded-md [&_select]:bg-panel [&_select]:text-foreground [&_select]:py-1.5 [&_select]:px-2 [&_select]:text-[12px]",
          anchorClass,
        ),
      )}
      data-style-hooks={styleHooks(
        cx(
          "lin-popover absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] lin-display w-[300px] max-h-[min(560px,75vh)] overflow-y-auto p-[6px] [&_select]:flex-1 [&_select]:border [&_select]:border-solid [&_select]:border-line [&_select]:rounded-[6px] [&_select]:bg-panel [&_select]:text-foreground [&_select]:py-[6px] [&_select]:px-[8px] [&_select]:text-[12px]",
          anchorClass,
        ),
      )}
      role="dialog"
      aria-label="Display options"
    >
      <div
        className={styleClasses(
          "in-data-[style-hooks~='lin-display']:pt-2 in-data-[style-hooks~='lin-display']:px-2 in-data-[style-hooks~='lin-display']:pb-2.5 in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
        data-style-hooks={styleHooks(
          "dp-sec in-data-[style-hooks~='lin-display']:pt-[8px] in-data-[style-hooks~='lin-display']:px-[8px] in-data-[style-hooks~='lin-display']:pb-[10px] in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
      >
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-0.5 in-data-[style-hooks~='lin-display']:mx-1 in-data-[style-hooks~='lin-display']:mb-2",
          )}
          data-style-hooks={styleHooks(
            "dp-title in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-[2px] in-data-[style-hooks~='lin-display']:mx-[4px] in-data-[style-hooks~='lin-display']:mb-[8px]",
          )}
        >
          View
        </div>
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:border in-data-[style-hooks~='lin-display']:border-solid in-data-[style-hooks~='lin-display']:border-line in-data-[style-hooks~='lin-display']:rounded-[7px] in-data-[style-hooks~='lin-display']:overflow-hidden [[data-style-hooks~='lin-display']_&_button]:py-1.5 [[data-style-hooks~='lin-display']_&_button]:px-3 [[data-style-hooks~='lin-display']_&_button]:bg-transparent [[data-style-hooks~='lin-display']_&_button]:text-[12px] [[data-style-hooks~='lin-display']_&_button]:text-muted [[data-style-hooks~='lin-display']_&_button]:font-medium [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:bg-subtle [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:text-foreground [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:font-semibold",
          )}
          data-style-hooks={styleHooks(
            "dp-seg in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:border in-data-[style-hooks~='lin-display']:border-solid in-data-[style-hooks~='lin-display']:border-line in-data-[style-hooks~='lin-display']:rounded-[7px] in-data-[style-hooks~='lin-display']:overflow-hidden [[data-style-hooks~='lin-display']_&_button]:py-[6px] [[data-style-hooks~='lin-display']_&_button]:px-[12px] [[data-style-hooks~='lin-display']_&_button]:bg-transparent [[data-style-hooks~='lin-display']_&_button]:text-[12px] [[data-style-hooks~='lin-display']_&_button]:text-muted [[data-style-hooks~='lin-display']_&_button]:font-medium [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:bg-subtle [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:text-foreground [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:font-semibold",
          )}
          role="group"
          aria-label="List or board"
        >
          {(["List", "Board"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={
                (display as unknown as { layout: string }).layout !== "Board"
                  ? v === "List"
                  : v === "Board"
              }
              onClick={() =>
                set({
                  ...(display as object),
                  layout: v,
                } as Partial<LinDisplay>)
              }
            >
              {v}
            </button>
          ))}
        </div>
      </div>
      <div
        className={styleClasses(
          "in-data-[style-hooks~='lin-display']:pt-2 in-data-[style-hooks~='lin-display']:px-2 in-data-[style-hooks~='lin-display']:pb-2.5 in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
        data-style-hooks={styleHooks(
          "dp-sec in-data-[style-hooks~='lin-display']:pt-[8px] in-data-[style-hooks~='lin-display']:px-[8px] in-data-[style-hooks~='lin-display']:pb-[10px] in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
      >
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-0.5 in-data-[style-hooks~='lin-display']:mx-1 in-data-[style-hooks~='lin-display']:mb-2",
          )}
          data-style-hooks={styleHooks(
            "dp-title in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-[2px] in-data-[style-hooks~='lin-display']:mx-[4px] in-data-[style-hooks~='lin-display']:mb-[8px]",
          )}
        >
          Grouping
        </div>
        <select
          aria-label={groupingLabel}
          value={display.grouping}
          onChange={(e) =>
            set({ grouping: e.target.value as LinDisplay["grouping"] })
          }
        >
          {GROUPS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div
        className={styleClasses(
          "in-data-[style-hooks~='lin-display']:pt-2 in-data-[style-hooks~='lin-display']:px-2 in-data-[style-hooks~='lin-display']:pb-2.5 in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
        data-style-hooks={styleHooks(
          "dp-sec in-data-[style-hooks~='lin-display']:pt-[8px] in-data-[style-hooks~='lin-display']:px-[8px] in-data-[style-hooks~='lin-display']:pb-[10px] in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
      >
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-0.5 in-data-[style-hooks~='lin-display']:mx-1 in-data-[style-hooks~='lin-display']:mb-2",
          )}
          data-style-hooks={styleHooks(
            "dp-title in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-[2px] in-data-[style-hooks~='lin-display']:mx-[4px] in-data-[style-hooks~='lin-display']:mb-[8px]",
          )}
        >
          Ordering
        </div>
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
          )}
          data-style-hooks={styleHooks(
            "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
          )}
        >
          <select
            aria-label="Order tasks by"
            value={display.ordering}
            onChange={(e) =>
              set({ ordering: e.target.value as LinDisplay["ordering"] })
            }
          >
            <option value="updated">Updated</option>
            <option value="created">Created</option>
            <option value="priority">Priority</option>
          </select>
          <div
            className={styleClasses(
              "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:border in-data-[style-hooks~='lin-display']:border-solid in-data-[style-hooks~='lin-display']:border-line in-data-[style-hooks~='lin-display']:rounded-[7px] in-data-[style-hooks~='lin-display']:overflow-hidden [[data-style-hooks~='lin-display']_&_button]:py-1.5 [[data-style-hooks~='lin-display']_&_button]:px-3 [[data-style-hooks~='lin-display']_&_button]:bg-transparent [[data-style-hooks~='lin-display']_&_button]:text-[12px] [[data-style-hooks~='lin-display']_&_button]:text-muted [[data-style-hooks~='lin-display']_&_button]:font-medium [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:bg-subtle [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:text-foreground [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:font-semibold",
            )}
            data-style-hooks={styleHooks(
              "dp-seg in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:border in-data-[style-hooks~='lin-display']:border-solid in-data-[style-hooks~='lin-display']:border-line in-data-[style-hooks~='lin-display']:rounded-[7px] in-data-[style-hooks~='lin-display']:overflow-hidden [[data-style-hooks~='lin-display']_&_button]:py-[6px] [[data-style-hooks~='lin-display']_&_button]:px-[12px] [[data-style-hooks~='lin-display']_&_button]:bg-transparent [[data-style-hooks~='lin-display']_&_button]:text-[12px] [[data-style-hooks~='lin-display']_&_button]:text-muted [[data-style-hooks~='lin-display']_&_button]:font-medium [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:bg-subtle [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:text-foreground [[data-style-hooks~='lin-display']_&_button[aria-pressed=\"true\"]]:font-semibold",
            )}
          >
            <button
              type="button"
              aria-pressed={display.direction === "desc"}
              onClick={() => set({ direction: "desc" })}
            >
              Desc
            </button>
            <button
              type="button"
              aria-pressed={display.direction === "asc"}
              onClick={() => set({ direction: "asc" })}
            >
              Asc
            </button>
          </div>
        </div>
        {entityToggles && (
          <>
            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
              )}
              data-style-hooks={styleHooks(
                "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
              )}
            >
              <label>
                <input
                  type="checkbox"
                  className={styleClasses(
                    "w-3.75 h-3.75 accent-accent flex-none",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-check w-[15px] h-[15px] accent-accent flex-none",
                  )}
                  checked={display.hideCompleted}
                  onChange={(e) => set({ hideCompleted: e.target.checked })}
                />{" "}
                Hide completed tasks
              </label>
            </div>
            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
              )}
              data-style-hooks={styleHooks(
                "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
              )}
            >
              <label>
                <input
                  type="checkbox"
                  className={styleClasses(
                    "w-3.75 h-3.75 accent-accent flex-none",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-check w-[15px] h-[15px] accent-accent flex-none",
                  )}
                  checked={display.showSubIssues}
                  onChange={(e) => set({ showSubIssues: e.target.checked })}
                />{" "}
                Show sub-tasks
              </label>
            </div>
            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
              )}
              data-style-hooks={styleHooks(
                "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
              )}
            >
              <label>
                <input
                  type="checkbox"
                  className={styleClasses(
                    "w-3.75 h-3.75 accent-accent flex-none",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-check w-[15px] h-[15px] accent-accent flex-none",
                  )}
                  checked={display.showEmptyColumns}
                  onChange={(e) => set({ showEmptyColumns: e.target.checked })}
                />{" "}
                Show empty columns
              </label>
            </div>
          </>
        )}
      </div>
      <div
        className={styleClasses(
          "in-data-[style-hooks~='lin-display']:pt-2 in-data-[style-hooks~='lin-display']:px-2 in-data-[style-hooks~='lin-display']:pb-2.5 in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
        data-style-hooks={styleHooks(
          "dp-sec in-data-[style-hooks~='lin-display']:pt-[8px] in-data-[style-hooks~='lin-display']:px-[8px] in-data-[style-hooks~='lin-display']:pb-[10px] in-data-[style-hooks~='lin-display']:border-b in-data-[style-hooks~='lin-display']:border-b-line [[data-style-hooks~='lin-display']_&:last-child]:border-b-0",
        )}
      >
        <div
          className={styleClasses(
            "in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-0.5 in-data-[style-hooks~='lin-display']:mx-1 in-data-[style-hooks~='lin-display']:mb-2",
          )}
          data-style-hooks={styleHooks(
            "dp-title in-data-[style-hooks~='lin-display']:text-[11px] in-data-[style-hooks~='lin-display']:font-semibold in-data-[style-hooks~='lin-display']:text-muted in-data-[style-hooks~='lin-display']:uppercase in-data-[style-hooks~='lin-display']:tracking-[.06em] in-data-[style-hooks~='lin-display']:mt-[2px] in-data-[style-hooks~='lin-display']:mx-[4px] in-data-[style-hooks~='lin-display']:mb-[8px]",
          )}
        >
          Display properties
        </div>
        {propertyRows
          ? propertyRows.map((row) => (
              <div
                className={styleClasses(
                  "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
                )}
                data-style-hooks={styleHooks(
                  "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
                )}
                key={row.key}
              >
                <label>
                  <input
                    type="checkbox"
                    className={styleClasses(
                      "w-3.75 h-3.75 accent-accent flex-none",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-check w-[15px] h-[15px] accent-accent flex-none",
                    )}
                    checked={row.checked}
                    onChange={row.onToggle}
                  />{" "}
                  {row.label}
                </label>
              </div>
            ))
          : PROP_LABELS.map(([k, label]) => (
              <div
                className={styleClasses(
                  "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
                )}
                data-style-hooks={styleHooks(
                  "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
                )}
                key={k}
              >
                <label>
                  <input
                    type="checkbox"
                    className={styleClasses(
                      "w-3.75 h-3.75 accent-accent flex-none",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-check w-[15px] h-[15px] accent-accent flex-none",
                    )}
                    checked={display.props[k]}
                    onChange={() => toggleProp(k)}
                  />{" "}
                  {label}
                </label>
              </div>
            ))}
        {UNAVAILABLE_PROPS.map((label) => (
          <div
            className={styleClasses(
              "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:py-1.25 in-data-[style-hooks~='lin-display']:px-1 in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-2.25 [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
            )}
            data-style-hooks={styleHooks(
              "dp-row in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:items-center in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:py-[5px] in-data-[style-hooks~='lin-display']:px-[4px] in-data-[style-hooks~='lin-display']:text-[13px] [[data-style-hooks~='lin-display']_&_label]:flex [[data-style-hooks~='lin-display']_&_label]:items-center [[data-style-hooks~='lin-display']_&_label]:gap-[9px] [[data-style-hooks~='lin-display']_&_label]:flex-1 [[data-style-hooks~='lin-display']_&_label]:cursor-pointer",
            )}
            key={label}
            style={{ opacity: 0.45 }}
          >
            <Tip label="Not available in Hoja yet">
              <label style={{ cursor: "not-allowed" }}>
                <input
                  type="checkbox"
                  className={styleClasses(
                    "w-3.75 h-3.75 accent-accent flex-none",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-check w-[15px] h-[15px] accent-accent flex-none",
                  )}
                  disabled
                />{" "}
                {label}
              </label>
            </Tip>
          </div>
        ))}
      </div>
      <div
        className={styleClasses(
          "in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:gap-2 in-data-[style-hooks~='lin-display']:pt-2.5 in-data-[style-hooks~='lin-display']:px-2 in-data-[style-hooks~='lin-display']:pb-1.5 [[data-style-hooks~='lin-display']_&_button]:flex-1 [[data-style-hooks~='lin-display']_&_button]:text-[12px] [[data-style-hooks~='lin-display']_&_button]:p-1.75 [[data-style-hooks~='lin-display']_&_button]:rounded-md [[data-style-hooks~='lin-display']_&_button]:border [[data-style-hooks~='lin-display']_&_button]:border-solid [[data-style-hooks~='lin-display']_&_button]:border-line [[data-style-hooks~='lin-display']_&_button]:bg-panel [[data-style-hooks~='lin-display']_&_button]:text-foreground [[data-style-hooks~='lin-display']_&_button:hover]:bg-subtle",
        )}
        data-style-hooks={styleHooks(
          "dp-foot in-data-[style-hooks~='lin-display']:flex in-data-[style-hooks~='lin-display']:gap-[8px] in-data-[style-hooks~='lin-display']:pt-[10px] in-data-[style-hooks~='lin-display']:px-[8px] in-data-[style-hooks~='lin-display']:pb-[6px] [[data-style-hooks~='lin-display']_&_button]:flex-1 [[data-style-hooks~='lin-display']_&_button]:text-[12px] [[data-style-hooks~='lin-display']_&_button]:p-[7px] [[data-style-hooks~='lin-display']_&_button]:rounded-[6px] [[data-style-hooks~='lin-display']_&_button]:border [[data-style-hooks~='lin-display']_&_button]:border-solid [[data-style-hooks~='lin-display']_&_button]:border-line [[data-style-hooks~='lin-display']_&_button]:bg-panel [[data-style-hooks~='lin-display']_&_button]:text-foreground [[data-style-hooks~='lin-display']_&_button:hover]:bg-subtle",
        )}
      >
        <button
          type="button"
          onClick={() =>
            set({
              ...defaultDisplay,
              layout: (display as unknown as { layout: "List" | "Board" })
                .layout,
            } as LinDisplay)
          }
        >
          Reset to team default
        </button>
        <button type="button" onClick={onClose}>
          Done
        </button>
      </div>
    </LinPopover>
  );
}

/* ---------------- decisions ---------------- */

/** "Waiting on me" marker: the issue has an open decision (docs/decisions.md). */
export function DecisionFlag({
  label = "Needs a decision",
}: {
  label?: string;
}) {
  return (
    <span
      className={styleClasses(
        "inline-grid place-items-center flex-none w-4 h-4 rounded-[50%] border border-solid border-[color-mix(in_srgb,var(--warning,#f2c94c)_70%,var(--line))] bg-[color-mix(in_srgb,var(--warning,#f2c94c)_18%,var(--panel))] text-foreground text-[10px] font-bold leading-none cursor-default",
      )}
      data-style-hooks={styleHooks(
        "lin-df inline-grid place-items-center flex-none w-[16px] h-[16px] rounded-[50%] border border-solid border-[color-mix(in_srgb,var(--warning,#f2c94c)_70%,var(--line))] bg-[color-mix(in_srgb,var(--warning,#f2c94c)_18%,var(--panel))] text-foreground text-[10px] font-bold leading-none cursor-default",
      )}
      role="img"
      aria-label={label}
      title={label}
    >
      ?
    </span>
  );
}

function replaceDecision(
  list: IssueDecision[] | undefined,
  next: IssueDecision,
): IssueDecision[] {
  const rows = list || [];
  const at = rows.findIndex((d) => d.id === next.id);
  return at < 0
    ? [next, ...rows]
    : rows.map((d) => (d.id === next.id ? next : d));
}

/* An open decision sits above the issue body: one click on an option answers it. Answered
 * and canceled decisions collapse to a line, so the issue keeps the record of what was
 * decided without the banner shouting forever. */
function DecisionPanel({
  decisions,
  onAnswer,
  onCancel,
}: {
  decisions: IssueDecision[];
  onAnswer: (
    d: IssueDecision,
    input: { option?: string; answer?: string },
  ) => Promise<void>;
  onCancel: (d: IssueDecision) => Promise<void>;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [showSettled, setShowSettled] = useState(false);
  if (!decisions.length) return null;
  const open = decisions.filter((d) => d.status === "open");
  const settled = decisions.filter((d) => d.status !== "open");
  const run = async (
    d: IssueDecision,
    input: { option?: string; answer?: string },
  ) => {
    setPending(d.id);
    setError("");
    try {
      await onAnswer(d, input);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(null);
    }
  };
  const cancel = async (d: IssueDecision) => {
    setPending(d.id);
    setError("");
    try {
      await onCancel(d);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(null);
    }
  };
  return (
    <section
      className={styleClasses("mt-3.5 mx-0 mb-0 flex flex-col gap-2.5")}
      data-style-hooks={styleHooks(
        "lin-decisions mt-[14px] mx-0 mb-0 flex flex-col gap-[10px]",
      )}
      aria-label="Decisions"
    >
      {open.map((d) => (
        <article
          className={styleClasses(
            "border border-solid border-[color-mix(in_srgb,var(--warning,#f2c94c)_55%,var(--line))] rounded-[10px] py-3 px-3.5 bg-[color-mix(in_srgb,var(--warning,#f2c94c)_8%,var(--panel))] [&_header]:flex [&_header]:items-center [&_header]:gap-2.5 open",
          )}
          data-style-hooks={styleHooks(
            "lin-decision border border-solid border-[color-mix(in_srgb,var(--warning,#f2c94c)_55%,var(--line))] rounded-[10px] py-[12px] px-[14px] bg-[color-mix(in_srgb,var(--warning,#f2c94c)_8%,var(--panel))] [&_header]:flex [&_header]:items-center [&_header]:gap-[10px] open",
          )}
          key={d.id}
        >
          <header>
            <span
              className={styleClasses(
                "text-[11px] font-bold tracking-[.04em] uppercase text-muted",
              )}
              data-style-hooks={styleHooks(
                "lin-decision-kicker text-[11px] font-bold tracking-[.04em] uppercase text-muted",
              )}
            >
              Decision needed
            </span>
            <button
              type="button"
              className={styleClasses(
                "ml-auto [background:none] border-0 py-0.5 px-1 text-muted text-[12px] cursor-pointer [&:hover]:text-foreground pointer-coarse:min-h-11 pointer-coarse:px-3 pointer-coarse:-my-2.5 pointer-coarse:-mr-3",
              )}
              data-style-hooks={styleHooks(
                "lin-decision-dismiss ml-auto [background:none] border-0 py-[2px] px-[4px] text-muted text-[12px] cursor-pointer [&:hover]:text-foreground",
              )}
              disabled={pending === d.id}
              onClick={() => void cancel(d)}
              title="Withdraw this question"
            >
              Withdraw
            </button>
          </header>
          <p
            className={styleClasses(
              "mt-1.5 mx-0 mb-0 text-[14px] font-semibold leading-[1.45]",
            )}
            data-style-hooks={styleHooks(
              "lin-decision-q mt-[6px] mx-0 mb-0 text-[14px] font-semibold leading-[1.45]",
            )}
          >
            {d.question}
          </p>
          {d.context && (
            <div
              className={styleClasses("mt-1.5 text-[13px] text-muted")}
              data-style-hooks={styleHooks(
                "lin-decision-context mt-[6px] text-[13px] text-muted",
              )}
            >
              <Markdown text={d.context} density="block" />
            </div>
          )}
          {d.options.some((o) => Boolean(o.mediaUrl)) ? (
            <VisualDecisionOptions
              options={d.options}
              disabled={pending === d.id}
              onPick={(key) => void run(d, { option: key })}
            />
          ) : (
            <div
              className={styleClasses(
                "flex flex-wrap gap-2 mt-2.5 shell-phone:flex-col shell-phone:flex-nowrap shell-phone:items-stretch",
              )}
              data-style-hooks={styleHooks(
                "lin-decision-actions flex flex-wrap gap-[8px] mt-[10px]",
              )}
            >
              {d.options.length ? (
                d.options.map((o) => (
                  <button
                    type="button"
                    key={o.key}
                    className={styleClasses(
                      "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333] in-data-[style-hooks~='lin-decisions']:flex in-data-[style-hooks~='lin-decisions']:flex-col in-data-[style-hooks~='lin-decisions']:items-start in-data-[style-hooks~='lin-decisions']:gap-0.5 in-data-[style-hooks~='lin-decisions']:text-left in-data-[style-hooks~='lin-decisions']:max-w-full in-data-[style-hooks~='lin-decisions']:bg-panel in-data-[style-hooks~='lin-decisions']:text-foreground in-data-[style-hooks~='lin-decisions']:border in-data-[style-hooks~='lin-decisions']:border-solid in-data-[style-hooks~='lin-decisions']:border-[color-mix(in_srgb,var(--warning,#f2c94c)_60%,var(--line))] in-data-[style-hooks~='lin-decisions']:shadow-none in-data-[style-hooks~='lin-decisions']:font-semibold [[data-style-hooks~='lin-decisions']_&:hover]:bg-[color-mix(in_srgb,var(--warning,#f2c94c)_18%,var(--panel))] [&_b]:font-[650] [&_small]:font-normal [&_small]:opacity-80",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333] lin-decision-option in-data-[style-hooks~='lin-decisions']:flex in-data-[style-hooks~='lin-decisions']:flex-col in-data-[style-hooks~='lin-decisions']:items-start in-data-[style-hooks~='lin-decisions']:gap-[2px] in-data-[style-hooks~='lin-decisions']:text-left in-data-[style-hooks~='lin-decisions']:max-w-full in-data-[style-hooks~='lin-decisions']:bg-panel in-data-[style-hooks~='lin-decisions']:text-foreground in-data-[style-hooks~='lin-decisions']:border in-data-[style-hooks~='lin-decisions']:border-solid in-data-[style-hooks~='lin-decisions']:border-[color-mix(in_srgb,var(--warning,#f2c94c)_60%,var(--line))] in-data-[style-hooks~='lin-decisions']:shadow-none in-data-[style-hooks~='lin-decisions']:font-semibold [[data-style-hooks~='lin-decisions']_&:hover]:bg-[color-mix(in_srgb,var(--warning,#f2c94c)_18%,var(--panel))] [&_b]:font-[650] [&_small]:font-normal [&_small]:opacity-80",
                    )}
                    disabled={pending === d.id}
                    title={o.description || o.label}
                    onClick={() => void run(d, { option: o.key })}
                  >
                    <b>{o.label}</b>
                    {o.description && <small>{o.description}</small>}
                  </button>
                ))
              ) : (
                <form
                  className={styleClasses(
                    "flex gap-2 flex-[1_1_320px] shell-phone:flex-auto min-w-0 pointer-coarse:[&_input]:min-h-11 pointer-coarse:[&_button]:min-h-11 [&_input]:flex-1 [&_input]:min-w-0 [&_input]:py-2 [&_input]:px-2.5 [&_input]:border [&_input]:border-solid [&_input]:border-line [&_input]:rounded-[7px] [&_input]:bg-bg [&_input]:text-foreground [&_input]:text-[13px]",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-decision-free flex gap-[8px] flex-[1_1_320px] min-w-0 [&_input]:flex-1 [&_input]:min-w-0 [&_input]:py-[8px] [&_input]:px-[10px] [&_input]:border [&_input]:border-solid [&_input]:border-line [&_input]:rounded-[7px] [&_input]:bg-bg [&_input]:text-foreground [&_input]:text-[13px]",
                  )}
                  onSubmit={(e) => {
                    e.preventDefault();
                    const text = draft.trim();
                    if (text) void run(d, { answer: text });
                  }}
                >
                  <input
                    aria-label={`Answer: ${d.question}`}
                    placeholder="Type the answer…"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={pending === d.id}
                  />
                  <button
                    type="submit"
                    className={styleClasses(
                      "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                    )}
                    disabled={!draft.trim() || pending === d.id}
                  >
                    Answer
                  </button>
                </form>
              )}
            </div>
          )}
          <small
            className={styleClasses("block mt-2 text-muted text-[12px]")}
            data-style-hooks={styleHooks(
              "lin-decision-meta block mt-[8px] text-muted text-[12px]",
            )}
          >
            {d.askedByAgent
              ? `Asked by ${d.askedByAgent}`
              : `Asked by ${d.askedBy?.name || "an agent"}`}
            {d.askedByAgent && d.askedBy?.name ? ` (${d.askedBy.name})` : ""} —
            agents are waiting on this
          </small>
        </article>
      ))}
      {error && (
        <p
          className={styleClasses(
            "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          data-style-hooks={styleHooks(
            "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
          )}
          role="alert"
        >
          {error}
        </p>
      )}
      {settled.length > 0 && (
        <div
          className={styleClasses("mt-0.5")}
          data-style-hooks={styleHooks("lin-decision-settled mt-[2px]")}
        >
          <button
            type="button"
            className={styleClasses(
              "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
            )}
            data-style-hooks={styleHooks(
              "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
            )}
            aria-expanded={showSettled}
            onClick={() => setShowSettled((v) => !v)}
          >
            {showSettled ? "Hide" : "Show"} {settled.length} answered decision
            {settled.length === 1 ? "" : "s"}
          </button>
          {showSettled &&
            settled.map((d) => {
              const chosenOpt = d.chosenOption
                ? d.options.find((o) => o.key === d.chosenOption)
                : undefined;
              return (
                <div
                  key={d.id}
                  className={styleClasses(
                    "mt-1.5 mx-0 mb-0 text-[13px] text-muted wrap-anywhere [&_small]:text-muted",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-decision-answer mt-[6px] mx-0 mb-0 text-[13px] text-muted wrap-anywhere [&_small]:text-muted",
                  )}
                >
                  <div>
                    <b>{d.question}</b> → {d.answer}{" "}
                    <small>
                      · answered by {d.answeredBy?.name || "a member"}
                    </small>
                  </div>
                  <ChosenDecisionMediaPreview option={chosenOpt} />
                </div>
              );
            })}
        </div>
      )}
    </section>
  );
}

/* ---------------- issue row ---------------- */

/* Memoized: with stable onSelect/onOpen only rows whose issue/selection changed re-render. */
/* The fields a list row actually reads. Narrower than LinIssue so a surface that is not the
 * issue store — the agent run queue, which renders a delegated task from its session record
 * (HOJA-602-A-16) — can build a row without inventing a whole issue. LinIssue satisfies it. */
export type IssueRowModel = {
  id: string;
  identifier: string;
  title: string;
  priority?: number | null;
  state?: { id?: string; name?: string; type?: string; color?: string } | null;
  stateChangedAt?: string | null;
  decisionNeeded?: boolean;
  labels?: { nodes: IssueLabel[] };
  project?: { id: string; name: string } | null;
  cycle?: { id: string; name?: string; number?: number } | null;
  dueDate?: string | null;
  assignee?: { id: string; name?: string; avatarSeed?: string | null } | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  /** The clock this row's task runs against, when a policy covers it. */
  sla?: IssueSla | null;
};

/* HOJA-704: the SLA chip. One glance says whether the task is on track, about to
 * be late, already late, or waiting on somebody. The server derives the state —
 * this only chooses a word and a tone for it, so the chip and the API can never
 * disagree about whether a deadline passed. */
export type SlaChipView = {
  state: string;
  label: string;
  title: string;
  className: string;
};

const SLA_SOON_MS = 24 * 60 * 60 * 1000;

export function slaChipView(sla: IssueSla, now = Date.now()): SlaChipView {
  const policy = sla.policy?.name ? ` · ${sla.policy.name}` : "";
  const due = sla.resolutionDueAt || sla.firstResponseDueAt;
  const dueText = due ? ` · due ${dueIn(due, now)}` : "";
  const title = `SLA${policy}${dueText}`;
  if (sla.state === "breached") {
    const late = sla.overdueMinutes
      ? ` by ${relTime2(sla.overdueMinutes)}`
      : "";
    return {
      state: "breached",
      label: "Breached",
      title: `${title}${late ? ` — late${late}` : ""}`,
      className: "border-danger/40 bg-danger/10 text-danger",
    };
  }
  if (sla.state === "paused") {
    return {
      state: "paused",
      label: "Paused",
      title: `${title} — the clock is paused in this state`,
      className: "border-line bg-subtle text-muted",
    };
  }
  if (sla.state === "met") {
    return {
      state: "met",
      label: "Met",
      title: `${title} — met`,
      className: "border-line bg-panel text-muted",
    };
  }
  if (sla.state === "detached") {
    return {
      state: "detached",
      label: "Detached",
      title: `${title} — no policy covers this task any more`,
      className: "border-line bg-panel text-muted",
    };
  }
  const next = [
    sla.firstRespondedAt ? null : sla.firstResponseDueAt,
    sla.resolutionDueAt,
  ]
    .filter((v): v is string => !!v)
    .map((v) => Date.parse(v))
    .filter((v) => !Number.isNaN(v));
  const soonest = next.length ? Math.min(...next) : null;
  if (soonest !== null && soonest - now <= SLA_SOON_MS) {
    return {
      state: "soon",
      label: "Due soon",
      title,
      className: "border-foreground/25 bg-subtle text-foreground",
    };
  }
  return {
    state: "ok",
    label: "On track",
    title,
    className: "border-line bg-subtle text-muted",
  };
}

/** A span of minutes as the shortest honest phrase. */
function relTime2(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

/** A deadline as "in 3h" or "3h ago". ``relTime`` reads backwards only. */
function dueIn(iso: string, now: number): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const minutes = Math.round((at - now) / 60000);
  return minutes >= 0 ? `in ${relTime2(minutes)}` : `${relTime2(-minutes)} ago`;
}

export function SlaChip({
  sla,
  className,
}: {
  sla?: IssueSla | null;
  className?: string;
}) {
  if (!sla || !sla.state) return null;
  const view = slaChipView(sla);
  return (
    <span
      data-sla-chip={view.state}
      title={view.title}
      className={cx(
        // The same pill geometry as `SubIssuePill`, so a row's chips line up.
        "inline-flex flex-none items-center gap-1 whitespace-nowrap rounded-full border py-0.5 px-2 text-[12px] font-medium leading-none",
        view.className,
        className,
      )}
    >
      {view.label}
    </span>
  );
}

export const IssueRow = memo(function IssueRow({
  issue,
  display,
  selected,
  onSelect,
  onOpen,
  childCount,
  childDone = 0,
  showCheckbox,
  trailing,
  leading,
}: {
  issue: IssueRowModel;
  display: LinDisplay;
  selected: boolean;
  onSelect: (id: string, on: boolean) => void;
  onOpen: (issue: LinIssue) => void;
  childCount: number;
  childDone?: number;
  showCheckbox: boolean;
  /** A caller-owned glyph for the row's second 14 px slot, where an issue shows priority. A
   * surface whose rows have no priority — the runner list (HOJA-602-A-16) — fills the column with
   * its own scarce resource instead of leaving a blank, and keeps the identifier column where the
   * issue list puts it. Absent by default, so every existing caller is unchanged. */
  leading?: React.ReactNode;
  /** A caller-owned chip that belongs to this row but not to the issue model, such as the agent
   * session badge on the run queue. Rendered beside the row's own flags so it inherits the row's
   * height and alignment instead of becoming a second, separately styled row. */
  trailing?: React.ReactNode;
}) {
  const p = display.props;
  return (
    <div
      data-issue-row
      className={styleClasses(
        cx(
          "flex items-center gap-2.25 min-h-10.5 py-1.25 px-2.5 border-b border-b-line text-[13px] relative [&:hover]:bg-subtle",
          selected &&
            "data-[style-hooks~='lin-row']:bg-[color-mix(in_srgb,var(--accent-soft)_55%,transparent)] data-[style-hooks~='lin-cycle-row']:bg-panel data-[style-hooks~='lin-cycle-row']:border-[color-mix(in_srgb,#5e6ad2_32%,var(--line))] data-[style-hooks~='lin-cycle-row']:shadow-[0_8px_26px_#1b1b1b0a]",
        ),
      )}
      data-style-hooks={styleHooks(
        cx(
          "lin-row flex items-center gap-[9px] min-h-[42px] py-[5px] px-[10px] border-b border-b-line text-[13px] relative [&:hover]:bg-subtle",
          selected &&
            "selected data-[style-hooks~='lin-row']:bg-[color-mix(in_srgb,var(--accent-soft)_55%,transparent)] data-[style-hooks~='lin-cycle-row']:bg-panel data-[style-hooks~='lin-cycle-row']:border-[color-mix(in_srgb,#5e6ad2_32%,var(--line))] data-[style-hooks~='lin-cycle-row']:shadow-[0_8px_26px_#1b1b1b0a]",
        ),
      )}
    >
      {showCheckbox && (
        <span
          className={styleClasses(
            "in-data-[style-hooks~='lin-row']:flex-none in-data-[style-hooks~='lin-row']:grid in-data-[style-hooks~='lin-row']:place-items-center in-data-[style-hooks~='lin-row']:w-5.5 in-data-[style-hooks~='lin-row']:h-5.5 [[data-style-hooks~='lin-row']_&_input]:w-3.75 [[data-style-hooks~='lin-row']_&_input]:h-3.75 [[data-style-hooks~='lin-row']_&_input]:accent-accent [[data-style-hooks~='lin-row']_&_input]:cursor-pointer [[data-style-hooks~='lin-row']_&_input]:m-0 [[data-style-hooks~='lin-row']:not(:hover):not([data-style-hooks~='selected'])_&]:invisible",
          )}
          data-style-hooks={styleHooks(
            "rowcheck in-data-[style-hooks~='lin-row']:flex-none in-data-[style-hooks~='lin-row']:grid in-data-[style-hooks~='lin-row']:place-items-center in-data-[style-hooks~='lin-row']:w-[22px] in-data-[style-hooks~='lin-row']:h-[22px] [[data-style-hooks~='lin-row']_&_input]:w-[15px] [[data-style-hooks~='lin-row']_&_input]:h-[15px] [[data-style-hooks~='lin-row']_&_input]:accent-accent [[data-style-hooks~='lin-row']_&_input]:cursor-pointer [[data-style-hooks~='lin-row']_&_input]:m-0 [[data-style-hooks~='lin-row']:not(:hover):not([data-style-hooks~='selected'])_&]:invisible",
          )}
        >
          <input
            type="checkbox"
            aria-label={`Select ${issue.identifier}`}
            checked={selected}
            onChange={(e) => onSelect(issue.id, e.target.checked)}
            onClick={(e) => e.stopPropagation()}
          />
        </span>
      )}
      {/* Linear leads every row with the status indicator, so the eye can scan state
          down the left edge; priority and identifier follow. */}
      {p.status && <StatusAgeIcon issue={issue} />}
      {leading ?? (p.priority && <PriorityIcon priority={issue.priority} />)}
      {/* A long identifier — a runner's column is a full UUID — may shrink and ellipsize on a phone
          rather than push the title and chips off the row. The cap is 14ch, wider than any issue
          identifier this app issues (`HOJA-667-A-34` is 14), so the issue list renders identically;
          the geometry suite asserts the identifier column is the same on both. */}
      {p.id && (
        <span
          className={styleClasses(
            "in-data-[style-hooks~='lin-row']:font-semibold in-data-[style-hooks~='lin-row']:whitespace-nowrap in-data-[style-hooks~='lin-row']:text-foreground [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:font-[450] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:text-muted shell-phone:flex-none shell-phone:max-w-[14ch] shell-phone:overflow-hidden shell-phone:text-ellipsis",
          )}
          data-style-hooks={styleHooks("ident")}
        >
          {issue.identifier}
        </span>
      )}
      {/* The row hands back the object it was handed. `IssueRowModel` is the render contract;
          a caller that passes a projection owns the consequence of its own missing fields. */}
      <button
        type="button"
        className={styleClasses(
          "in-data-[style-hooks~='lin-row']:flex-1 in-data-[style-hooks~='lin-row']:min-w-0 in-data-[style-hooks~='lin-row']:overflow-hidden in-data-[style-hooks~='lin-row']:text-ellipsis in-data-[style-hooks~='lin-row']:whitespace-nowrap in-data-[style-hooks~='lin-row']:text-foreground in-data-[style-hooks~='lin-row']:[background:none] in-data-[style-hooks~='lin-row']:border-0 in-data-[style-hooks~='lin-row']:text-left in-data-[style-hooks~='lin-row']:[font:inherit] in-data-[style-hooks~='lin-row']:p-0 in-data-[style-hooks~='lin-row']:cursor-pointer",
        )}
        data-style-hooks={styleHooks(
          "title in-data-[style-hooks~='lin-row']:flex-1 in-data-[style-hooks~='lin-row']:min-w-0 in-data-[style-hooks~='lin-row']:overflow-hidden in-data-[style-hooks~='lin-row']:text-ellipsis in-data-[style-hooks~='lin-row']:whitespace-nowrap in-data-[style-hooks~='lin-row']:text-foreground in-data-[style-hooks~='lin-row']:[background:none] in-data-[style-hooks~='lin-row']:border-0 in-data-[style-hooks~='lin-row']:text-left in-data-[style-hooks~='lin-row']:[font:inherit] in-data-[style-hooks~='lin-row']:p-0 in-data-[style-hooks~='lin-row']:cursor-pointer",
        )}
        onClick={() => onOpen(issue as LinIssue)}
        title={issue.title}
      >
        {issue.title}
      </button>
      {issue.decisionNeeded && <DecisionFlag />}
      {childCount > 0 && <SubIssuePill done={childDone} total={childCount} />}
      {trailing}
      <span
        className={styleClasses(
          "in-data-[style-hooks~='lin-row']:flex in-data-[style-hooks~='lin-row']:items-center in-data-[style-hooks~='lin-row']:gap-2 in-data-[style-hooks~='lin-row']:flex-none in-data-[style-hooks~='lin-row']:ml-auto",
        )}
        data-style-hooks={styleHooks(
          "meta in-data-[style-hooks~='lin-row']:flex in-data-[style-hooks~='lin-row']:items-center in-data-[style-hooks~='lin-row']:gap-[8px] in-data-[style-hooks~='lin-row']:flex-none in-data-[style-hooks~='lin-row']:ml-auto",
        )}
      >
        {p.labels &&
          issue.labels?.nodes.map((l) => <LabelChip key={l.id} label={l} />)}
        {p.project && issue.project && (
          <ProjectChip name={issue.project.name} />
        )}
        {p.cycle && issue.cycle && (
          <CycleChip number={issue.cycle.number} name={issue.cycle.name} />
        )}
        {p.dueDate && issue.dueDate && <DueChip iso={issue.dueDate} />}
        {p.assignee &&
          (issue.assignee ? (
            <Avatar
              name={issue.assignee.name}
              seed={issue.assignee.avatarSeed}
            />
          ) : (
            <span
              className={styleClasses(
                "in-data-[style-hooks~='lin-row']:text-muted in-data-[style-hooks~='lin-row']:text-[12px] in-data-[style-hooks~='lin-row']:whitespace-nowrap",
              )}
              data-style-hooks={styleHooks(
                "date in-data-[style-hooks~='lin-row']:text-muted in-data-[style-hooks~='lin-row']:text-[12px] in-data-[style-hooks~='lin-row']:whitespace-nowrap",
              )}
            >
              —
            </span>
          ))}
        {p.created && (
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-row']:text-muted in-data-[style-hooks~='lin-row']:text-[12px] in-data-[style-hooks~='lin-row']:whitespace-nowrap",
            )}
            data-style-hooks={styleHooks(
              "date in-data-[style-hooks~='lin-row']:text-muted in-data-[style-hooks~='lin-row']:text-[12px] in-data-[style-hooks~='lin-row']:whitespace-nowrap",
            )}
            title={`Created ${fmtFullDate(issue.createdAt)}`}
            aria-label={`Created ${fmtFullDate(issue.createdAt)}`}
          >
            {fmtDate(issue.createdAt)}
          </span>
        )}
        {p.updated && (
          <span
            className={styleClasses(
              "in-data-[style-hooks~='lin-row']:text-muted in-data-[style-hooks~='lin-row']:text-[12px] in-data-[style-hooks~='lin-row']:whitespace-nowrap",
            )}
            data-style-hooks={styleHooks(
              "date in-data-[style-hooks~='lin-row']:text-muted in-data-[style-hooks~='lin-row']:text-[12px] in-data-[style-hooks~='lin-row']:whitespace-nowrap",
            )}
            title={`Updated ${fmtFullDate(issue.updatedAt)}`}
            aria-label={`Updated ${fmtFullDate(issue.updatedAt)}`}
          >
            {fmtDate(issue.updatedAt)}
          </span>
        )}
      </span>
    </div>
  );
});

/* ---------------- property dropdown ---------------- */

/* The properties-sidebar control. Exported so the agent detail page (HOJA-602-A-16) edits its
 * policy with the same widget, keyboard behaviour and row height as an issue property. */
/** A property that shows a fact rather than offering a menu (HOJA-704's SLA row).
 * Same geometry as ``PropDropdown`` so the pane's rows stay on one grid. */
export function PropStatic({
  label,
  icon,
  value,
}: {
  label: string;
  icon: React.ReactNode;
  value: React.ReactNode;
}) {
  return (
    <div
      className={styleClasses(
        "flex items-center gap-2.5 min-h-8.5 text-[13px] relative",
      )}
      data-style-hooks={styleHooks(
        "lin-prop flex items-center gap-[10px] min-h-[34px] text-[13px] relative",
      )}
    >
      <span
        className={styleClasses("text-muted")}
        data-style-hooks={styleHooks("k text-muted")}
      >
        <span className="inline-flex items-center gap-2">
          {icon}
          {label}
        </span>
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-1.75">{value}</span>
    </div>
  );
}

/** One line under the SLA chip: the deadline, or why there is none. */
export function slaClockSummary(sla: IssueSla, now = Date.now()): string {
  const due = sla.resolutionDueAt || sla.firstResponseDueAt;
  if (!due) return sla.policy?.name ? sla.policy.name : "";
  const at = Date.parse(due);
  if (Number.isNaN(at)) return sla.policy?.name || "";
  const view = slaChipView(sla, now);
  const when = new Date(at).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return view.state === "breached" || view.state === "met"
    ? `${sla.policy?.name || "SLA"} · ${when}`
    : `${sla.policy?.name || "SLA"} · due ${when}`;
}

export function PropDropdown({
  label,
  icon,
  value,
  onPick,
  options,
  disabled,
  onOpen,
  footer,
}: {
  label: string;
  icon: React.ReactNode;
  value: React.ReactNode;
  onPick: (id: string | null) => void;
  options: {
    id: string | null;
    label: React.ReactNode;
    selected?: boolean;
    hint?: string;
  }[];
  disabled?: boolean;
  /** Called when the menu opens, for a picker whose options are fetched on demand (Delegate). */
  onOpen?: () => void;
  /** A sentence under the options — a refusal or an empty workspace, not a bare panel. */
  footer?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className={styleClasses(
        "flex items-center gap-2.5 min-h-8.5 text-[13px] relative",
      )}
      data-style-hooks={styleHooks(
        "lin-prop flex items-center gap-[10px] min-h-[34px] text-[13px] relative lin-dd relative",
      )}
    >
      <span
        className={styleClasses(
          "in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-2 in-data-[style-hooks~='lin-prop']:text-muted in-data-[style-hooks~='lin-prop']:w-24 in-data-[style-hooks~='lin-prop']:flex-none",
        )}
        data-style-hooks={styleHooks(
          "k in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[8px] in-data-[style-hooks~='lin-prop']:text-muted in-data-[style-hooks~='lin-prop']:w-[96px] in-data-[style-hooks~='lin-prop']:flex-none",
        )}
      >
        {icon}
        {label}
      </span>
      <span
        className={styleClasses(
          "in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-1.75",
        )}
        data-style-hooks={styleHooks(
          "v in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[7px]",
        )}
      >
        <button
          type="button"
          className={styleClasses(
            "[[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[background:none] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:border-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[font:inherit] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-foreground [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:py-1 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:px-1.5 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:rounded-md [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:inline-flex [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:items-center [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:gap-1.75 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:min-w-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-left [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&:hover]:bg-subtle",
          )}
          data-style-hooks={styleHooks(
            "plain [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[background:none] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:border-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[font:inherit] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-foreground [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:py-[4px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:px-[6px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:rounded-[6px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:inline-flex [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:items-center [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:gap-[7px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:min-w-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-left [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&:hover]:bg-subtle",
          )}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => {
            if (!open) onOpen?.();
            setOpen((v) => !v);
          }}
        >
          <span
            className={styleClasses(
              "[[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:overflow-hidden [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:text-ellipsis [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:whitespace-nowrap",
            )}
            data-style-hooks={styleHooks(
              "trunc [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:overflow-hidden [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:text-ellipsis [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:whitespace-nowrap",
            )}
          >
            {value}
          </span>
        </button>
      </span>
      {open && (
        <LinPopover
          onClose={() => setOpen(false)}
          className={styleClasses(
            "absolute z-130 min-w-55 max-w-[320px] p-1.25 border border-solid border-line rounded-[9px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] top-[calc(100%+4px)] left-0 min-w-57.5 max-h-[min(20rem,var(--radix-popover-content-available-height))] overflow-y-auto",
          )}
          data-style-hooks={styleHooks(
            "lin-menu absolute z-130 min-w-[220px] max-w-[320px] p-[5px] border border-solid border-line rounded-[9px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] lin-dd-panel absolute top-[calc(100%+4px)] left-0 z-130 min-w-[230px] max-h-[320px] overflow-y-auto",
          )}
          role="listbox"
          aria-label={label}
        >
          {options.map((o) => (
            <button
              key={String(o.id)}
              type="button"
              role="option"
              aria-selected={o.selected}
              className={styleClasses(
                "flex items-center gap-2.25 w-full py-1.75 px-2.5 rounded-md bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle aria-selected:font-semibold pointer-coarse:min-h-11",
              )}
              data-style-hooks={styleHooks(
                "lin-dd-opt flex items-center gap-[9px] w-full py-[7px] px-[10px] rounded-[6px] bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle aria-selected:font-semibold",
              )}
              title={o.hint}
              onClick={() => {
                setOpen(false);
                onPick(o.id);
              }}
            >
              <span
                className={styleClasses(
                  "in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere",
                )}
                data-style-hooks={styleHooks(
                  "grow in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere",
                )}
              >
                {o.label}
              </span>
              {o.selected && <Check size={14} />}
            </button>
          ))}
          {footer}
        </LinPopover>
      )}
    </div>
  );
}

/* ---------------- issue detail ---------------- */

/* Which renderer an attachment row uses. The recorded content type is the truth; the
 * URL path is only the fallback for external links and for rows created before the
 * type was stored. Query and fragment are stripped so `/shot.png?v=2` still reads as
 * an image, and an extensionless upload id is never guessed at from its characters. */
const IMAGE_PATH = /\.(png|jpe?g|gif|webp|avif|bmp|svg)$/i;
export function attachmentKind(a: {
  url?: string | null;
  contentType?: string | null;
}): "image" | "document" {
  const type = (a.contentType || "").split(";")[0].trim().toLowerCase();
  if (type) return type.startsWith("image/") ? "image" : "document";
  const path = (a.url || "").split(/[?#]/)[0];
  return IMAGE_PATH.test(path) ? "image" : "document";
}

// canEdit/canDelete come from the server's comment policy; the UI never re-derives them.
const commentFields = `{id body createdAt editedAt canEdit canDelete user{id name avatarSeed}}`;
const detailFields = `{ id identifier title description createdAt updatedAt dueDate priority priorityLabel estimate branchName url
  state{id name color type} team{id key name} project{id name} assignee{id name avatarSeed} delegate{id name avatarSeed} delegateId creator{id name avatarSeed}
  cycle{id name number} parent{id identifier title}
  sla{state resolutionDueAt firstResponseDueAt firstRespondedAt breachedAt overdueMinutes pausedMinutes cycleEndsAt retargetedAt startedAt policy{id name}}
  labels{nodes{id name color}} subscribers{nodes{id name avatarSeed}}
  attachments{nodes{id url title subtitle contentType createdAt}}
  githubLinks{id kind externalId url title state author sha environment createdAt}
  activity(first:50){nodes{id eventType createdAt actor{name avatarSeed} changes}}
  decisionNeeded
  decisions(first:20){${decisionFields}}
  comments{nodes${commentFields}} }`;

/* Detail cache. The sync store already holds every synced issue's properties; this keeps
 * what the snapshot does not carry (description, comments, activity, attachments,
 * sub-tasks) in memory, so a revisit or an open after a hover prefetch renders at once.
 * Entries are marked stale by realtime events and revalidated when next shown. */
export type IssueDetailEntry = { detail: LinIssue; subs: LinIssue[] };
const DETAIL_CACHE_MAX = 60;
const detailCache = new Map<string, IssueDetailEntry>();
const detailInflight = new Map<string, Promise<IssueDetailEntry>>();
const staleDetails = new Set<string>();

function rememberDetail(entry: IssueDetailEntry) {
  for (const k of [entry.detail.id, entry.detail.identifier]) {
    detailCache.delete(k);
    detailCache.set(k, entry);
  }
  while (detailCache.size > DETAIL_CACHE_MAX * 2)
    detailCache.delete(detailCache.keys().next().value as string);
}
export function cachedIssueDetail(
  idOrIdentifier?: string | null,
): IssueDetailEntry | undefined {
  return idOrIdentifier ? detailCache.get(idOrIdentifier) : undefined;
}
function updateCachedDetail(
  id: string,
  fn: (entry: IssueDetailEntry) => IssueDetailEntry,
) {
  const cur = detailCache.get(id);
  if (cur) rememberDetail(fn(cur));
}
const subsQuery = `issues(first:100,filter:$filter){nodes ${linIssueFields}}`;
/** Fetch (deduplicated) and cache an issue's full detail. Pass the task's id as knownId when you have it: one request instead of two. */
export function fetchIssueDetail(
  idOrIdentifier: string,
  knownId?: string,
): Promise<IssueDetailEntry> {
  const running =
    (knownId && detailInflight.get(knownId)) ||
    detailInflight.get(idOrIdentifier);
  if (running) return running;
  if (knownId) staleDetails.delete(knownId);
  const p = (async () => {
    let detail: LinIssue | null;
    let subs: LinIssue[];
    if (knownId) {
      const d = await gql(
        `query IssueDetail($id:String!,$filter:IssueFilter){issue(id:$id)${detailFields} ${subsQuery}}`,
        { id: knownId, filter: { parent: { id: { eq: knownId } } } },
      );
      detail = d.issue;
      subs = d.issues?.nodes || [];
    } else {
      const d = await gql(
        `query IssueDetail($id:String!){issue(id:$id)${detailFields}}`,
        { id: idOrIdentifier },
      );
      detail = d.issue;
      if (!detail) throw new Error("Task not found");
      const s = await gql(
        `query IssueSubs($filter:IssueFilter){${subsQuery}}`,
        { filter: { parent: { id: { eq: detail.id } } } },
      );
      subs = s.issues.nodes;
    }
    if (!detail) throw new Error("Task not found");
    const entry = { detail, subs };
    rememberDetail(entry);
    return entry;
  })();
  const keys = knownId ? [knownId, idOrIdentifier] : [idOrIdentifier];
  for (const k of keys) detailInflight.set(k, p);
  const clear = () => {
    for (const k of keys)
      if (detailInflight.get(k) === p) detailInflight.delete(k);
  };
  p.then(clear, clear);
  return p;
}
async function fetchIssueComments(issueId: string) {
  const d = await gql(
    `query IssueComments($id:String!){issue(id:$id){comments{nodes${commentFields}}}}`,
    { id: issueId },
  );
  if (d.issue)
    updateCachedDetail(issueId, (e) => ({
      ...e,
      detail: { ...e.detail, comments: d.issue.comments },
    }));
}
/** Warm the detail cache (no-op when cached, in flight, or a local placeholder). */
export function prefetchIssueDetail(
  idOrIdentifier?: string | null,
  knownId?: string,
) {
  if (!idOrIdentifier || isLocalIssueId(knownId || idOrIdentifier)) return;
  const key = knownId || idOrIdentifier;
  if (
    (detailCache.has(key) &&
      !staleDetails.has(detailCache.get(key)!.detail.id)) ||
    detailInflight.has(key)
  )
    return;
  void fetchIssueDetail(idOrIdentifier, knownId).catch(() => undefined);
}
const intentTimers = new Map<string, number>();
/** Spread onto an issue row/link: prefetches its detail after a short hover or on focus/touch (intent, not fly-over). */
export function issueIntentProps(
  issue: { id: string; identifier?: string },
  delay = 120,
) {
  const cancel = () => {
    window.clearTimeout(intentTimers.get(issue.id));
    intentTimers.delete(issue.id);
  };
  const start = () => {
    cancel();
    intentTimers.set(
      issue.id,
      window.setTimeout(() => {
        intentTimers.delete(issue.id);
        prefetchIssueDetail(issue.identifier || issue.id, issue.id);
      }, delay),
    );
  };
  return {
    onMouseEnter: start,
    onMouseLeave: cancel,
    onFocus: start,
    onTouchStart: () =>
      prefetchIssueDetail(issue.identifier || issue.id, issue.id),
  };
}
// Comment/attachment events carry their own id, not the issue's, so a create may touch any cached issue.
onEntityChange(
  ["issue", "comment", "attachment", "issueDecision"],
  (events) => {
    const entries = [...new Set(detailCache.values())];
    for (const e of events) {
      if (e.entity === "issue") {
        for (const entry of entries) {
          if (
            entry.detail.id === e.entityId ||
            entry.subs.some((s) => s.id === e.entityId)
          )
            staleDetails.add(entry.detail.id);
        }
        continue;
      }
      if (e.entity === "issueDecision") {
        // A decision event names the decision, not its issue, so every cached detail revalidates.
        for (const entry of entries) staleDetails.add(entry.detail.id);
        continue;
      }
      const owner =
        e.entity === "comment" && e.action !== "create"
          ? entries.find((entry) =>
              entry.detail.comments?.nodes.some((c) => c.id === e.entityId),
            )
          : undefined;
      if (owner) staleDetails.add(owner.detail.id);
      else if (e.action === "create" || e.entity === "attachment")
        for (const entry of entries) staleDetails.add(entry.detail.id);
    }
  },
);

/** The state a new issue most likely lands in when no status was picked (the server applies the team default). */
export function guessDefaultState(team?: Team): Issue["state"] {
  const states = team?.states.nodes || [];
  return (
    states.find((s) => s.type === "backlog") ||
    states.find((s) => s.type === "unstarted") ||
    states[0]
  );
}

const PRIORITY_NAMES = ["No priority", "Urgent", "High", "Medium", "Low"];

/* Store fields win over the fetched detail (they carry realtime deltas and optimistic overlays);
 * the detail supplies what the snapshot lacks. A reference the store could not resolve
 * (e.g. a project outside the synced page) falls back to the detail of the same version. */
function mergeIssueView(
  base: LinIssue,
  live: Issue | undefined,
  users: Map<string, string>,
  layers: Partial<LinIssue>[],
): LinIssue {
  let out: LinIssue = base;
  if (live) {
    const sameVersion =
      !live.updatedAt || !base.updatedAt || base.updatedAt >= live.updatedAt;
    const ref = <
      K extends
        "project" | "cycle" | "assignee" | "delegate" | "creator" | "team",
    >(
      k: K,
    ) => (live[k] || (sameVersion ? base[k] : undefined)) as LinIssue[K];
    out = {
      ...base,
      id: live.id,
      identifier: live.identifier,
      title: live.title,
      priority: live.priority,
      priorityLabel: live.priorityLabel,
      state: live.state || (sameVersion ? base.state : undefined),
      team: ref("team"),
      project: ref("project"),
      cycle: ref("cycle"),
      assignee: ref("assignee"),
      delegate: ref("delegate"),
      creator: ref("creator"),
      labels: live.labels,
      dueDate: live.dueDate,
      createdAt: base.createdAt || live.createdAt,
      decisionNeeded: live.decisionNeeded ?? base.decisionNeeded,
      updatedAt:
        live.updatedAt && (!base.updatedAt || live.updatedAt > base.updatedAt)
          ? live.updatedAt
          : base.updatedAt,
    };
    if (Object.prototype.hasOwnProperty.call(live, "description"))
      out.description = live.description;
    if (live.subscribers) {
      out.subscribers = {
        nodes: live.subscribers.nodes.map(
          (s) =>
            base.subscribers?.nodes.find((b) => b.id === s.id) || {
              id: s.id,
              name: users.get(s.id),
            },
        ),
      };
    }
  }
  for (const l of layers) out = { ...out, ...l };
  return out;
}

/** What happened, without the actor (the caller names who did it). */
function describeEvent(
  e: {
    eventType: string;
    actor?: { name?: string; avatarSeed?: string | null };
    changes?: Record<string, unknown>;
  },
  states: IssueState[],
): string {
  if (e.eventType === "issueCreated" || e.eventType === "created")
    return "created the task";
  const fixed = eventPhrase(e);
  if (fixed) return fixed;
  const ch = (e.changes || {}) as Record<
    string,
    { from?: string | null; to?: string | null }
  >;
  const sname = (id?: string | null) =>
    (id && states.find((s) => s.id === id)?.name) || "Unknown";
  if (ch.stateId)
    return `moved from ${sname(ch.stateId.from)} to ${sname(ch.stateId.to)}`;
  if (ch.assigneeId) return "assigned the task";
  if (ch.cycleId) return "changed the cycle";
  if (ch.priority) return "changed the priority";
  return "updated the task";
}

type PendingComment = {
  id: string;
  body: string;
  createdAt: string;
  known: Set<string>;
};

/** True below 900px, where DetailColumns stacks the properties under the main
 * column. Mirrors that media query so the task's core properties can move up. */
function useNarrowDetail() {
  const query = "(max-width: 900px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return narrow;
}

export function GraphIssueDetail({
  workspaceName,
  viewer,
  issue,
  issueList,
  onOpenIssue,
  loading,
  error,
  onArchive,
  onDelete,
  onDeleteComment,
  teams,
  projects,
  labels,
  cycles,
  onUpdate,
  onBack,
  backLabel,
  onRetry,
}: {
  workspaceName?: string;
  viewer: Viewer;
  issue: LinIssue | null;
  issueList: LinIssue[];
  onOpenIssue: (issue: LinIssue) => void;
  loading: boolean;
  error: string;
  onArchive: () => Promise<void>;
  onDelete: () => Promise<void>;
  onDeleteComment: (id: string) => Promise<void>;
  teams: Team[];
  projects: Project[];
  labels: IssueLabel[];
  cycles: Cycle[];
  onUpdate: (input: Record<string, unknown>) => Promise<void>;
  onBack: () => void;
  backLabel: string;
  onRetry?: () => void;
  /** Kept for callers; comments are created here so the pending comment can be swapped for the server's. */
  onComment?: (body: string) => Promise<void>;
}) {
  const [, force] = useReducer((x: number) => x + 1, 0);
  const [loadError, setLoadError] = useState("");
  const [retryTick, setRetryTick] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const narrow = useNarrowDetail();
  const [layers, setLayers] = useState<{ patch: Partial<LinIssue> }[]>([]);
  const [updateError, setUpdateError] = useState("");
  // Ids whose preview failed to load, so a dead URL degrades to an icon instead of
  // leaving the browser's torn-page glyph in the row.
  const [brokenThumbs, setBrokenThumbs] = useState<Record<string, boolean>>({});
  // An agent session trace attachment (hoja.trace/v1) opens in the trace viewer.
  const [openTrace, setOpenTrace] = useState<{
    url: string;
    title: string;
  } | null>(null);
  const [comment, setComment] = useState("");
  const [commentError, setCommentError] = useState("");
  const commentRef = useRef<HTMLTextAreaElement | null>(null);
  const mentions = useMentions(
    commentRef,
    teams.find((t) => t.id === issue?.team?.id)?.members?.nodes || [],
    comment,
    setComment,
  );
  const [pendingComments, setPendingComments] = useState<PendingComment[]>([]);
  const [hiddenComments, setHiddenComments] = useState<string[]>([]);
  const [editedComments, setEditedComments] = useState<Record<string, string>>(
    {},
  );
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(
    null,
  );
  const [pendingSubs, setPendingSubs] = useState<LinIssue[]>([]);
  const [labelOpen, setLabelOpen] = useState(false);
  const [parentSearch, setParentSearch] = useState(false);
  const [parentQuery, setParentQuery] = useState("");
  const [parentResults, setParentResults] = useState<LinIssue[]>([]);
  /* The Delegate picker's options are agent members (WP04, admin-only), fetched the first time the
   * menu opens rather than on every task page: a non-admin reading a task must not be shown an
   * error about a control they did not ask for, and a refused read is stated in the menu itself. */
  const [agentChoices, setAgentChoices] = useState<AgentChoice[] | null>(null);
  const [agentChoicesError, setAgentChoicesError] = useState("");
  const [agentChoicesLoading, setAgentChoicesLoading] = useState(false);
  const loadAgentChoices = () => {
    if (agentChoices || agentChoicesLoading) return;
    setAgentChoicesError("");
    setAgentChoicesLoading(true);
    getAgentsAdapter()
      .listAgentChoices()
      .then((rows) => setAgentChoices(rows))
      .catch((e: Error) => setAgentChoicesError(e.message))
      .finally(() => setAgentChoicesLoading(false));
  };
  const [attachMenu, setAttachMenu] = useState(false);
  const [attachUrl, setAttachUrl] = useState("");
  const [attachTitle, setAttachTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [newSubTitle, setNewSubTitle] = useState("");
  const [addingSub, setAddingSub] = useState(false);
  /* Extension actions come from one session-cached query (extensionActions(entity:"Issue"));
   * a failed query leaves the list empty, so the menu simply has no action entries. */
  const extensionActions = useIssueExtensionActions();
  const [runningActionId, setRunningActionId] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<{
    kind: "status" | "error";
    text: string;
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const composerFileRef = useRef<HTMLInputElement>(null);
  const titleFocused = useRef(false);

  const issueId = issue?.id;
  const issueIdent = issue?.identifier;
  const live = useIssueSelect(issueId);
  const meta = useSyncMeta();
  const users = useMemo(
    () => new Map((meta?.users || []).map(([id, name]) => [id, name || ""])),
    [meta],
  );
  const entry = cachedIssueDetail(issueId) || cachedIssueDetail(issueIdent);
  const base: LinIssue | null =
    entry?.detail || issue || (live as LinIssue | undefined) || null;
  const detail: LinIssue | null = base
    ? mergeIssueView(
        base,
        live,
        users,
        layers.map((l) => l.patch),
      )
    : null;
  const detailReady = Boolean(entry);
  const [title, setTitle] = useState(detail?.title || "");
  const issueVersion = useEntityVersion(["issue"], [issueId]);

  // Render from the store/cache immediately; fetch only what is missing or stale.
  useEffect(() => {
    if (!issueId || isLocalIssueId(issueId)) return;
    const cached = cachedIssueDetail(issueId);
    if (cached && !staleDetails.has(issueId)) return;
    let live = true;
    setLoadError("");
    const detailTimeout = cached
      ? undefined
      : setTimeout(() => {
          if (live)
            setLoadError(
              "Loading the task timed out. Check your connection and retry.",
            );
        }, 20000);
    fetchIssueDetail(issueIdent || issueId, issueId)
      .then(() => {
        if (live) {
          setLoadError("");
          force();
        }
      })
      .catch((e) => {
        if (live) setLoadError((e as Error).message);
      })
      .finally(() => clearTimeout(detailTimeout));
    return () => {
      live = false;
      clearTimeout(detailTimeout);
    };
  }, [issueId, issueIdent, issueVersion, retryTick]);

  // New/edited/deleted comments from anyone. Create events cannot be scoped to this issue, so
  // they refresh only this issue's comments (one small query), not the whole detail.
  useEffect(() => {
    if (!issueId || isLocalIssueId(issueId)) return;
    let timer: number | undefined;
    let live = true;
    const off = onEntityChange(["comment"], (events) => {
      const known = new Set(
        (cachedIssueDetail(issueId)?.detail.comments?.nodes || []).map(
          (c) => c.id,
        ),
      );
      if (
        !events.some(
          (e) =>
            (e.action === "create" && !known.has(e.entityId)) ||
            (e.action !== "create" && known.has(e.entityId)),
        )
      )
        return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        void fetchIssueComments(issueId)
          .then(() => {
            if (live) force();
          })
          .catch(() => undefined);
      }, 80);
    });
    return () => {
      live = false;
      window.clearTimeout(timer);
      off();
    };
  }, [issueId]);

  const liveTitle = detail?.title;
  useEffect(() => {
    if (!titleFocused.current && liveTitle !== undefined) setTitle(liveTitle);
  }, [liveTitle]);

  useEffect(() => {
    if (!parentSearch || parentQuery.trim().length < 2) {
      setParentResults([]);
      return;
    }
    const t = setTimeout(() => {
      gql(
        `query($term:String!){searchIssues(term:$term,first:8){nodes{id identifier title state{id name color type}}}}`,
        { term: parentQuery.trim() },
      )
        .then((d) =>
          setParentResults(
            (d.searchIssues.nodes as LinIssue[]).filter(
              (i) => i.id !== detail?.id,
            ),
          ),
        )
        .catch(() => setParentResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [parentQuery, parentSearch, detail?.id]);

  /* A decision answer is written once server-side; the cached detail then carries the row
   * that came back, so the banner and the badge agree without a refetch. */
  const rememberDecision = (next: IssueDecision) => {
    updateCachedDetail(detail!.id, (e) => ({
      ...e,
      detail: {
        ...e.detail,
        decisions: replaceDecision(e.detail.decisions, next),
      },
    }));
    force();
  };
  const answerOne = async (
    decision: IssueDecision,
    input: { option?: string; answer?: string },
  ) => {
    const result = await answerDecision(decision.id, input);
    rememberDecision(result.decision);
  };
  const cancelOne = async (decision: IssueDecision) => {
    rememberDecision(await cancelDecision(decision.id));
  };

  if (!detail) {
    if (loading || (!error && !loadError))
      return (
        <section
          className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
          data-style-hooks={styleHooks(
            "lin-page min-w-0 flex flex-col h-full bg-panel",
          )}
        >
          <p
            className={styleClasses("p-10 text-center text-muted")}
            data-style-hooks={styleHooks(
              "lin-loading p-[40px] text-center text-muted",
            )}
            role="status"
          >
            Loading task…
          </p>
        </section>
      );
    return (
      <section
        className={styleClasses("min-w-0 flex flex-col h-full bg-panel")}
        data-style-hooks={styleHooks(
          "lin-page min-w-0 flex flex-col h-full bg-panel",
        )}
      >
        <div
          className={styleClasses("flex-1 min-h-0 overflow-y-auto")}
          data-style-hooks={styleHooks(
            "lin-scroll flex-1 min-h-0 overflow-y-auto",
          )}
        >
          <p
            className={styleClasses(
              "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
            )}
            data-style-hooks={styleHooks(
              "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
            )}
            role="alert"
          >
            {error || loadError || "Task not found"}
          </p>
          <div style={{ display: "flex", gap: 8, margin: "0 24px" }}>
            {onRetry && (
              <button
                type="button"
                className={styleClasses(
                  "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                )}
                data-style-hooks={styleHooks(
                  "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                )}
                onClick={onRetry}
              >
                Retry
              </button>
            )}
            <button
              type="button"
              className={styleClasses(
                "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
              )}
              data-style-hooks={styleHooks(
                "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
              )}
              onClick={onBack}
            >
              ← {backLabel}
            </button>
          </div>
        </div>
      </section>
    );
  }

  const team = teams.find((t) => t.id === detail.team?.id);
  const states = team?.states.nodes || [];
  const members = team?.members?.nodes || [];
  const teamCycles = cycles.filter((c) => c.team?.id === detail.team?.id);
  const labelIds = detail.labels?.nodes.map((l) => l.id) || [];

  /* Local view of an IssueUpdateInput, applied until the server answers (covers fields the
   * store does not track, like estimate and parent, and issues outside the snapshot). */
  const localPatch = (input: Record<string, unknown>): Partial<LinIssue> => {
    const out: Partial<LinIssue> = {};
    const has = (k: string) => Object.prototype.hasOwnProperty.call(input, k);
    if (has("title")) out.title = input.title as string;
    if (has("description"))
      out.description = (input.description as string) || undefined;
    if (has("priority")) {
      out.priority = Number(input.priority);
      out.priorityLabel = PRIORITY_NAMES[out.priority];
    }
    if (has("estimate")) out.estimate = input.estimate as number | null;
    if (has("dueDate")) out.dueDate = (input.dueDate as string) || undefined;
    if (has("stateId")) out.state = states.find((s) => s.id === input.stateId);
    if (has("assigneeId")) {
      const m = members.find((x) => x.id === input.assigneeId);
      out.assignee = m
        ? { id: m.id, name: m.name || m.email || "" }
        : undefined;
    }
    /* The delegate is an agent member, so its label comes from the agent list rather than the
     * team's members: `delegateId` holds a `user.id`, and `agent_member` is where it is named. */
    if (has("delegateId")) {
      const choice = agentChoices?.find((x) => x.id === input.delegateId);
      out.delegate = choice
        ? { id: choice.id, name: choice.name }
        : input.delegateId
          ? detail.delegate
          : null;
      out.delegateId = (input.delegateId as string | null) ?? null;
    }
    if (has("cycleId")) {
      const c = teamCycles.find((x) => x.id === input.cycleId);
      out.cycle = c ? { id: c.id, name: c.name, number: c.number } : undefined;
    }
    if (has("projectId")) {
      const p = projects.find((x) => x.id === input.projectId);
      out.project = p ? { id: p.id, name: p.name } : undefined;
    }
    if (has("labelIds")) {
      const ids = (input.labelIds as string[]) || [];
      out.labels = {
        nodes: ids
          .map(
            (id) =>
              labels.find((l) => l.id === id) ||
              detail.labels?.nodes.find((l) => l.id === id),
          )
          .filter(Boolean) as IssueLabel[],
      };
    }
    if (has("parentId")) {
      const r = input.parentId
        ? parentResults.find((x) => x.id === input.parentId)
        : undefined;
      out.parent = r
        ? { id: r.id, identifier: r.identifier, title: r.title }
        : input.parentId
          ? detail.parent
          : null;
    }
    return out;
  };
  const withLayer = async (
    patch: Partial<LinIssue>,
    run: () => Promise<void>,
  ): Promise<boolean> => {
    const layer = { patch };
    setLayers((ls) => [...ls, layer]);
    try {
      await run();
      updateCachedDetail(detail.id, (e) => ({
        ...e,
        detail: { ...e.detail, ...patch },
      }));
      force();
      return true;
    } catch (e) {
      setUpdateError((e as Error).message);
      return false;
    } finally {
      setLayers((ls) => ls.filter((l) => l !== layer));
    }
  };
  const change = (input: Record<string, unknown>): Promise<boolean> => {
    setUpdateError("");
    return withLayer(localPatch(input), () => onUpdate(input));
  };
  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* ignore */
    }
  };
  const branchName =
    detail.branchName ||
    `${(detail.team?.key || "hoja").toLowerCase()}-${detail.identifier.split("-")[1] || ""}`;
  const asPrompt =
    `${detail.identifier}: ${detail.title}\n\n${detail.description || ""}`.trim();

  const idx = issueList.findIndex((i) => i.id === detail.id);
  const prev = idx > 0 ? issueList[idx - 1] : null;
  const next =
    idx >= 0 && idx < issueList.length - 1 ? issueList[idx + 1] : null;

  const serverComments = (detail.comments?.nodes || [])
    .filter((c) => !hiddenComments.includes(c.id))
    .map((c) =>
      editedComments[c.id] !== undefined
        ? { ...c, body: editedComments[c.id] }
        : c,
    );
  const shownPendingComments = pendingComments.filter(
    (p) =>
      !serverComments.some(
        (c) =>
          !p.known.has(c.id) && c.body === p.body && c.user?.id === viewer.id,
      ),
  );

  const submitComment = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = comment.trim();
    if (!body) return;
    const temp: PendingComment = {
      id: `local-comment-${Date.now()}-${pendingComments.length}`,
      body,
      createdAt: new Date().toISOString(),
      known: new Set((detail.comments?.nodes || []).map((c) => c.id)),
    };
    setPendingComments((p) => [...p, temp]);
    setComment("");
    setCommentError("");
    try {
      const d = await gql(
        `mutation($input:CommentCreateInput!){commentCreate(input:$input){success comment${commentFields}}}`,
        { input: { issueId: detail.id, body } },
      );
      const created = d.commentCreate?.comment;
      if (!d.commentCreate?.success || !created)
        throw new Error("Comment was rejected");
      updateCachedDetail(detail.id, (en) =>
        en.detail.comments?.nodes.some((c) => c.id === created.id)
          ? en
          : {
              ...en,
              detail: {
                ...en.detail,
                comments: {
                  nodes: [...(en.detail.comments?.nodes || []), created],
                },
              },
            },
      );
      force();
    } catch (err) {
      setComment((cur) => cur || body);
      setCommentError((err as Error).message);
    } finally {
      setPendingComments((p) => p.filter((x) => x !== temp));
    }
  };
  const deleteComment = async (id: string) => {
    setHiddenComments((h) => [...h, id]);
    setCommentError("");
    try {
      await onDeleteComment(id);
      updateCachedDetail(detail.id, (en) => ({
        ...en,
        detail: {
          ...en.detail,
          comments: {
            nodes: (en.detail.comments?.nodes || []).filter((c) => c.id !== id),
          },
        },
      }));
      force();
    } catch (err) {
      setCommentError((err as Error).message);
      // Rights may have changed (demotion, deletion elsewhere): let the server's answer redraw the controls.
      void fetchIssueComments(detail.id)
        .then(force)
        .catch(() => undefined);
    } finally {
      setHiddenComments((h) => h.filter((x) => x !== id));
    }
  };
  const saveComment = async (id: string, body: string) => {
    const text = body.trim();
    if (!text) return;
    setEditing(null);
    setCommentError("");
    setEditedComments((m) => ({ ...m, [id]: text }));
    try {
      const d = await gql(
        `mutation($id:String!,$input:CommentUpdateInput!){commentUpdate(id:$id,input:$input){success comment${commentFields}}}`,
        { id, input: { body: text } },
      );
      const saved = d.commentUpdate?.comment;
      if (!d.commentUpdate?.success || !saved)
        throw new Error("This comment no longer exists.");
      updateCachedDetail(detail.id, (en) => ({
        ...en,
        detail: {
          ...en.detail,
          comments: {
            nodes: (en.detail.comments?.nodes || []).map((c) =>
              c.id === id ? saved : c,
            ),
          },
        },
      }));
      force();
    } catch (err) {
      setCommentError((err as Error).message);
      setEditing({ id, body: text });
      void fetchIssueComments(detail.id)
        .then(force)
        .catch(() => undefined);
    } finally {
      setEditedComments((m) => {
        const n = { ...m };
        delete n[id];
        return n;
      });
    }
  };

  const doUpload = async (file: File) => {
    setUploading(true);
    setUpdateError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const token = localStorage.getItem("hoja_token");
      const r = await fetch("/api/uploads", {
        method: "POST",
        credentials: "include",
        headers:
          token && token !== "session"
            ? { authorization: `Bearer ${token}` }
            : {},
        body: fd,
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.error || "Upload failed");
      const d = await gql(
        `mutation($input:AttachmentCreateInput!){attachmentCreate(input:$input){success attachment{id url title subtitle contentType createdAt}}}`,
        {
          input: {
            issueId: detail.id,
            url: b.url,
            title: b.title || file.name,
          },
        },
      );
      if (!d.attachmentCreate?.success)
        throw new Error("Attachment was rejected");
      updateCachedDetail(detail.id, (en) => ({
        ...en,
        detail: {
          ...en.detail,
          attachments: {
            nodes: [
              ...(en.detail.attachments?.nodes || []),
              d.attachmentCreate.attachment,
            ],
          },
        },
      }));
      force();
    } catch (e) {
      setUpdateError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const attachLink = async () => {
    if (!attachUrl.trim()) return;
    setUploading(true);
    setUpdateError("");
    try {
      const d = await gql(
        `mutation($input:AttachmentCreateInput!){attachmentCreate(input:$input){success attachment{id url title subtitle contentType createdAt}}}`,
        {
          input: {
            issueId: detail.id,
            url: attachUrl.trim(),
            title: attachTitle.trim() || attachUrl.trim(),
          },
        },
      );
      if (!d.attachmentCreate?.success)
        throw new Error("Attachment was rejected");
      updateCachedDetail(detail.id, (en) => ({
        ...en,
        detail: {
          ...en.detail,
          attachments: {
            nodes: [
              ...(en.detail.attachments?.nodes || []),
              d.attachmentCreate.attachment,
            ],
          },
        },
      }));
      force();
      setAttachUrl("");
      setAttachTitle("");
      setAttachMenu(false);
    } catch (e) {
      setUpdateError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  /* Optimistic issue: shown in the store (lists/board) and here until the server's row replaces it. */
  const placeholderIssue = (
    fields: Partial<LinIssue> & { title: string },
  ): LinIssue => {
    const now = new Date().toISOString();
    return {
      id: localIssueId(),
      identifier: guessNextIdentifier(detail.team?.key || "ISS"),
      priority: 0,
      priorityLabel: PRIORITY_NAMES[0],
      createdAt: now,
      updatedAt: now,
      stateChangedAt: now,
      team: detail.team,
      state: guessDefaultState(team),
      creator: viewer.id ? { id: viewer.id, name: viewer.name } : undefined,
      labels: { nodes: [] },
      subscribers: {
        nodes: viewer.id ? [{ id: viewer.id, name: viewer.name }] : [],
      },
      ...fields,
    };
  };
  const createSub = async () => {
    const t = newSubTitle.trim();
    if (!t || !detail.team?.id) return;
    const temp = placeholderIssue({
      title: t,
      parent: {
        id: detail.id,
        identifier: detail.identifier,
        title: detail.title,
      },
    });
    const resolve = addLocalIssue(temp as Issue);
    setPendingSubs((p) => [...p, temp]);
    setNewSubTitle("");
    setUpdateError("");
    try {
      const d = await gql(
        `mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue ${linIssueFields}}}`,
        { input: { title: t, teamId: detail.team.id, parentId: detail.id } },
      );
      const created = d.issueCreate?.issue as LinIssue | undefined;
      if (!d.issueCreate?.success || !created)
        throw new Error("Sub-task was rejected");
      resolve(created as Issue);
      updateCachedDetail(detail.id, (en) =>
        en.subs.some((s) => s.id === created.id)
          ? en
          : { ...en, subs: [...en.subs, created] },
      );
      force();
    } catch (e) {
      resolve(null);
      setNewSubTitle((cur) => cur || t);
      setUpdateError((e as Error).message);
    } finally {
      setPendingSubs((p) => p.filter((x) => x !== temp));
    }
  };
  const copyIssue = async () => {
    const copyTitle = `Copy of ${detail.title}`;
    const temp = placeholderIssue({
      title: copyTitle,
      description: detail.description,
      priority: detail.priority,
      priorityLabel: detail.priorityLabel,
      project: detail.project,
      labels: detail.labels,
    });
    const resolve = addLocalIssue(temp as Issue);
    setUpdateError("");
    try {
      const d = await gql(
        `mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue ${linIssueFields}}}`,
        {
          input: {
            title: copyTitle,
            teamId: detail.team?.id,
            description: detail.description,
            priority: detail.priority,
            projectId: detail.project?.id,
            labelIds,
          },
        },
      );
      const created = d.issueCreate?.issue as LinIssue | undefined;
      if (!d.issueCreate?.success || !created)
        throw new Error("Copy was rejected");
      resolve(created as Issue);
      onOpenIssue(created);
    } catch (e) {
      resolve(null);
      setUpdateError((e as Error).message);
    }
  };

  const subscribed = Boolean(
    viewer.id &&
    (detail.subscribers?.nodes || []).some((s) => s.id === viewer.id),
  );
  const toggleSubscribe = () => {
    if (!viewer.id) return;
    const field = subscribed ? "issueUnsubscribe" : "issueSubscribe";
    const current = detail.subscribers?.nodes || [];
    const nodes = subscribed
      ? current.filter((s) => s.id !== viewer.id)
      : [...current, { id: viewer.id, name: viewer.name }];
    setUpdateError("");
    void withLayer({ subscribers: { nodes } }, async () => {
      const settle = live
        ? getSync()?.patch(detail.id, {
            subscribers: { nodes: nodes.map((s) => ({ id: s.id })) },
          })
        : undefined;
      try {
        const d = await gql(`mutation($id:String!){${field}(id:$id)}`, {
          id: detail.id,
        });
        if (!d[field]) throw new Error("Subscription was not changed");
      } catch (e) {
        settle?.(false);
        throw e;
      }
      settle?.(true);
    });
  };
  const archive = () => {
    void onArchive().catch((e) => setUpdateError((e as Error).message));
  };
  const remove = () => {
    void onDelete().catch((e) => setUpdateError((e as Error).message));
  };
  /* Run one extension action for this issue. The server answers with a status and the
   * extension's own message; a hook veto or transport failure arrives as an EXTENSION error. */
  const runAction = async (action: ExtensionAction) => {
    setRunningActionId(action.id);
    setActionNotice(null);
    try {
      const result = await runExtensionAction(action.id, detail.id);
      if (!result?.success)
        throw new Error(
          result?.message || "The extension did not accept this action.",
        );
      const outcome = [result.status, result.message]
        .filter(Boolean)
        .join(" — ");
      setActionNotice({
        kind: "status",
        text: `${action.title} · ${action.extensionName}${outcome ? ` — ${outcome}` : ""}`,
      });
    } catch (e) {
      setActionNotice({
        kind: "error",
        text: `${action.title} · ${action.extensionName} — ${(e as Error).message}`,
      });
    } finally {
      setRunningActionId(null);
    }
  };
  const actionMenuItems: MenuItem[] = extensionActions.actions.length
    ? [
        { key: "extensions-divider", divider: true },
        ...extensionActions.actions.map<MenuItem>((action) => ({
          key: `extension:${action.id}`,
          label: `${action.title} · ${action.extensionName}`,
          icon: <Zap size={14} />,
          disabled: runningActionId === action.id,
          onSelect: () => void runAction(action),
        })),
      ]
    : [];
  const subsView = [...(entry?.subs || []), ...pendingSubs];
  const subsDone = subsView.filter((s) => s.state?.type === "completed").length;

  // Status, priority, assignee, delegate, cycle, project (and SLA): the properties
  // a phone user changes most. On a narrow screen they sit right under the title
  // instead of at the very bottom of the stacked page.
  const coreProps = (
    <>
      <PropDropdown
        label="Status"
        icon={<GraphStatusIcon state={detail.state} size={14} />}
        value={
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 7,
            }}
          >
            {detail.state?.name || "No status"}
          </span>
        }
        onPick={(id) => {
          if (id) void change({ stateId: id });
        }}
        options={states.map((s) => ({
          id: s.id,
          selected: s.id === detail.state?.id,
          label: (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <GraphStatusIcon state={s} />
              {s.name}
            </span>
          ),
        }))}
      />
      <PropDropdown
        label="Priority"
        icon={<PriorityIcon priority={detail.priority} />}
        value={
          ["No priority", "Urgent", "High", "Medium", "Low"][
            detail.priority || 0
          ]
        }
        onPick={(id) => void change({ priority: Number(id) })}
        options={[
          [0, "No priority"],
          [1, "Urgent"],
          [2, "High"],
          [3, "Medium"],
          [4, "Low"],
        ].map(([v, l]) => ({
          id: String(v),
          selected: (detail.priority || 0) === v,
          label: (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <PriorityIcon priority={v as number} />
              {l}
            </span>
          ),
        }))}
      />
      <PropDropdown
        label="Assignee"
        icon={
          <Avatar
            name={detail.assignee?.name}
            seed={detail.assignee?.avatarSeed}
            size={16}
          />
        }
        value={detail.assignee?.name || "Unassigned"}
        onPick={(id) => void change({ assigneeId: id })}
        options={[
          {
            id: null,
            selected: !detail.assignee,
            label: "Unassigned",
            hint: "Unassigned",
          },
          ...members.map((m) => ({
            id: m.id as string | null,
            selected: m.id === detail.assignee?.id,
            hint: m.name || m.email || undefined,
            label: (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <Avatar name={m.name} seed={m.avatarSeed} size={18} />
                {m.name || m.email}
              </span>
            ),
          })),
        ]}
      />
      {/* The agent this task is delegated to — `issue.delegate_id` (WP04), separate from the
        assignee: the assignee is the person accountable, the delegate is the agent that runs
        it. Same control, same row height and same keyboard behaviour as the Assignee row. */}
      <PropDropdown
        label="Delegate"
        icon={<Bot size={14} />}
        value={detail.delegate?.name || "No delegate"}
        onOpen={loadAgentChoices}
        onPick={(id) => void change({ delegateId: id })}
        options={[
          {
            id: null,
            selected: !detail.delegate,
            label: "No delegate",
            hint: "Nothing is delegated",
          },
          ...(agentChoices || []).map((a) => ({
            id: a.id as string | null,
            selected: a.id === detail.delegate?.id,
            hint: a.name,
            label: (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <Bot size={14} aria-hidden="true" />
                {a.name}
              </span>
            ),
          })),
        ]}
        footer={
          agentChoicesError ? (
            <p
              className={styleClasses("text-[12px] text-muted")}
              data-style-hooks={styleHooks("lin-note text-[12px] text-muted")}
              role="alert"
              style={{ padding: "8px 10px" }}
            >
              {agentChoicesError}
            </p>
          ) : agentChoicesLoading || !agentChoices ? (
            <p
              className={styleClasses("text-[12px] text-muted")}
              data-style-hooks={styleHooks("lin-note text-[12px] text-muted")}
              style={{ padding: "8px 10px" }}
              role="status"
            >
              Loading agents…
            </p>
          ) : !agentChoices.length ? (
            <p
              className={styleClasses("text-[12px] text-muted")}
              data-style-hooks={styleHooks("lin-note text-[12px] text-muted")}
              style={{ padding: "8px 10px" }}
            >
              This workspace has no active agent member to delegate to.
            </p>
          ) : undefined
        }
      />
      <PropDropdown
        label="Cycle"
        icon={<CycleGlyph />}
        value={
          detail.cycle
            ? detail.cycle.name || `Cycle ${detail.cycle.number}`
            : "No cycle"
        }
        onPick={(id) => void change({ cycleId: id })}
        options={[
          { id: null, selected: !detail.cycle, label: "No cycle" },
          ...teamCycles.map((c) => ({
            id: c.id as string | null,
            selected: c.id === detail.cycle?.id,
            label: c.name || `Cycle ${c.number}`,
          })),
        ]}
      />
      <PropDropdown
        label="Project"
        icon={
          <svg
            width="13"
            height="13"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path
              d="M8 1.8l5.6 3.2v6.4L8 14.6l-5.6-3.2V5L8 1.8Z"
              strokeLinejoin="round"
            />
          </svg>
        }
        value={detail.project?.name || "No project"}
        onPick={(id) => void change({ projectId: id })}
        options={[
          { id: null, selected: !detail.project, label: "No project" },
          ...projects.map((p) => ({
            id: p.id as string | null,
            selected: p.id === detail.project?.id,
            label: p.name,
          })),
        ]}
      />
      {detail.sla && (
        <PropStatic
          label="SLA"
          icon={
            <svg
              width="13"
              height="13"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <circle cx="8" cy="8.6" r="5.4" />
              <path d="M8 5.8v3l2 1.3M5.6 2.2h4.8" strokeLinecap="round" />
            </svg>
          }
          value={
            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
              <SlaChip sla={detail.sla} />
              <span className="min-w-0 truncate text-muted">
                {slaClockSummary(detail.sla)}
              </span>
            </span>
          }
        />
      )}
    </>
  );

  return (
    <DetailPage
      label={`${detail.identifier}: ${detail.title}`}
      surface="issue-detail"
      // A landscape phone has ~270px under the app bar: the header scrolls away with
      // the page there instead of pinning another 51px.
      className="[@media_(max-width:1000px)_and_(max-height:500px)]:overflow-y-auto [@media_(max-width:1000px)_and_(max-height:500px)]:overscroll-contain [@media_(max-width:1000px)_and_(max-height:500px)]:[&_[data-style-hooks~='lin-detail']]:flex-none [@media_(max-width:1000px)_and_(max-height:500px)]:[&_[data-style-hooks~='lin-detail']]:overflow-visible"
    >
      <DetailHeader
        crumbs={
          <>
            <Crumb onClick={onBack}>
              ← <span className="shell-phone:sr-only">{backLabel}</span>
            </Crumb>
            <CrumbSep className="shell-phone:hidden" />
            <span className="shell-phone:hidden">{workspaceName}</span>
            <CrumbSep className="shell-phone:hidden" />
            {detail.parent && (
              <>
                <Crumb
                  className="lin-crumb-parent shell-phone:hidden!"
                  data-parent-path={detail.parent.identifier}
                  title={`Parent: ${detail.parent.identifier} ${detail.parent.title}`}
                  onClick={() =>
                    onOpenIssue({
                      ...(detail.parent as LinIssue),
                      id: detail.parent!.id,
                    })
                  }
                >
                  {detail.parent.identifier}
                </Crumb>
                <CrumbSep className="shell-phone:hidden" />
              </>
            )}
            <CrumbCurrent title={detail.identifier}>
              {detail.identifier}
            </CrumbCurrent>
          </>
        }
        actions={
          <>
            <FavStar kind="issues" id={detail.id} label={detail.identifier} />
            <span style={{ position: "relative" }}>
              <IconBtn
                label="Task options"
                title="Task options"
                onClick={() => setMoreOpen((v) => !v)}
              >
                <span style={{ fontWeight: 700 }}>···</span>
              </IconBtn>
              {moreOpen && (
                <LinMenu
                  onClose={() => setMoreOpen(false)}
                  className={styleClasses(
                    "absolute top-[calc(100%+4px)] left-0 z-130 min-w-57.5 max-h-80 overflow-y-auto data-[style-hooks~='lin-dd-panel']:right-0 data-[style-hooks~='lin-dd-panel']:left-auto pointer-coarse:max-h-[min(400px,calc(100dvh-140px))] pointer-coarse:[&_[role=menuitem]]:min-h-11",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-dd-panel absolute top-[calc(100%+4px)] left-0 z-130 min-w-[230px] max-h-[320px] overflow-y-auto lin-dd-right data-[style-hooks~='lin-dd-panel']:right-0 data-[style-hooks~='lin-dd-panel']:left-auto",
                  )}
                  items={[
                    {
                      key: "link",
                      label: "Copy link",
                      icon: <Link2 size={14} />,
                      onSelect: () => void copyText(window.location.href),
                    },
                    {
                      key: "duplicate",
                      label: "Create copy of task",
                      icon: <Copy size={14} />,
                      onSelect: () => void copyIssue(),
                    },
                    {
                      key: "copy",
                      label: "Copy as prompt",
                      icon: <Copy size={14} />,
                      onSelect: () => void copyText(asPrompt),
                    },
                    {
                      key: "branch",
                      label: "Copy branch name",
                      icon: <Copy size={14} />,
                      onSelect: () => void copyText(branchName),
                    },
                    ...actionMenuItems,
                    { key: "div", divider: true },
                    {
                      key: "archive",
                      label: "Archive task",
                      onSelect: async () => {
                        if (
                          await confirmDialog(`Archive ${detail.identifier}?`)
                        )
                          archive();
                      },
                    },
                    {
                      key: "delete",
                      label: "Delete task",
                      danger: true,
                      onSelect: async () => {
                        if (
                          await confirmDialog(
                            `Permanently delete ${detail.identifier}? This cannot be undone.`,
                          )
                        )
                          remove();
                      },
                    },
                  ]}
                />
              )}
            </span>
            {idx >= 0 && (
              <>
                <span
                  className={styleClasses(
                    "text-[12px] text-muted my-0 mx-1 whitespace-nowrap shell-phone:hidden",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-nav-count text-[12px] text-muted my-0 mx-[4px] whitespace-nowrap",
                  )}
                >
                  {idx + 1} / {issueList.length}
                </span>
                <span
                  style={{ display: "contents" }}
                  {...(prev ? issueIntentProps(prev) : {})}
                >
                  <IconBtn
                    label="Previous task"
                    title="Previous task"
                    disabled={!prev}
                    onClick={() => prev && onOpenIssue(prev)}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                    >
                      <path
                        d="M8 13V3M4 7l4-4 4 4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </IconBtn>
                </span>
                <span
                  style={{ display: "contents" }}
                  {...(next ? issueIntentProps(next) : {})}
                >
                  <IconBtn
                    label="Next task"
                    title="Next task"
                    disabled={!next}
                    onClick={() => next && onOpenIssue(next)}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                    >
                      <path
                        d="M8 3v10M4 9l4 4 4-4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </IconBtn>
                </span>
              </>
            )}
          </>
        }
      />

      <DetailColumns
        data-issue-content=""
        toolbarOnPhone={false}
        toolbar={
          <>
            <IconBtn
              label="Copy link"
              title="Copy link"
              onClick={() => void copyText(window.location.href)}
            >
              <Link2 size={15} />
            </IconBtn>
            <IconBtn
              label="Create copy of task"
              title="Create copy of task"
              onClick={() => void copyIssue()}
            >
              <Copy size={15} />
            </IconBtn>
            <Tip label="Hoja has no public share links — copies the private URL">
              <span>
                <IconBtn
                  label="Share task externally"
                  title="Share task externally"
                  onClick={() => void copyText(window.location.href)}
                >
                  <ExternalLink size={15} />
                </IconBtn>
              </span>
            </Tip>
            <IconBtn
              label="Archive task"
              title="Archive task"
              onClick={async () => {
                if (await confirmDialog(`Archive ${detail.identifier}?`))
                  archive();
              }}
            >
              <Trash2 size={15} />
            </IconBtn>
          </>
        }
        main={
          <>
            {detail.parent && (
              // A phone's header has no room for the parent crumb: it moves here.
              <button
                type="button"
                className="hidden shell-phone:flex items-center gap-1.5 max-w-full min-h-11 -mt-2 mb-1 p-0 border-0 bg-transparent text-[13px] text-muted text-left cursor-pointer hover:text-foreground"
                onClick={() =>
                  onOpenIssue({
                    ...(detail.parent as LinIssue),
                    id: detail.parent!.id,
                  })
                }
              >
                <span className="flex-none">Sub-task of</span>
                <span className="flex-none font-semibold">
                  {detail.parent.identifier}
                </span>
                <span className="min-w-0 truncate">{detail.parent.title}</span>
              </button>
            )}
            <DetailTitle
              value={title}
              onFocusChange={(focused) => {
                titleFocused.current = focused;
              }}
              onCommit={(t) => {
                setTitle(t);
                void change({ title: t });
              }}
            />
            {updateError && <DetailNotice>{updateError}</DetailNotice>}
            {actionNotice && (
              <p
                className={styleClasses(
                  actionNotice.kind === "error"
                    ? "flex items-center gap-2.5 flex-wrap mt-3 mx-0 mb-0 py-2.5 px-3.5 border border-solid border-line rounded-lg bg-subtle text-foreground text-[13px] [&_>_span]:flex-[1_1_220px] [&_>_span]:min-w-0 [&_>_span]:wrap-anywhere [&_>_span]:leading-normal data-[style-hooks~='lin-action-notice']:border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] data-[style-hooks~='lin-action-notice']:bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))] data-[style-hooks~='lin-action-notice']:text-danger"
                    : "flex items-center gap-2.5 flex-wrap mt-3 mx-0 mb-0 py-2.5 px-3.5 border border-solid border-line rounded-lg bg-subtle text-foreground text-[13px] [&_>_span]:flex-[1_1_220px] [&_>_span]:min-w-0 [&_>_span]:wrap-anywhere [&_>_span]:leading-normal",
                )}
                data-style-hooks={styleHooks(
                  actionNotice.kind === "error"
                    ? "lin-action-notice flex items-center gap-[10px] flex-wrap mt-[12px] mx-0 mb-0 py-[10px] px-[14px] border border-solid border-line rounded-[8px] bg-subtle text-foreground text-[13px] [&_>_span]:flex-[1_1_220px] [&_>_span]:min-w-0 [&_>_span]:wrap-anywhere [&_>_span]:leading-normal error data-[style-hooks~='lin-action-notice']:border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] data-[style-hooks~='lin-action-notice']:bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))] data-[style-hooks~='lin-action-notice']:text-danger"
                    : "lin-action-notice flex items-center gap-[10px] flex-wrap mt-[12px] mx-0 mb-0 py-[10px] px-[14px] border border-solid border-line rounded-[8px] bg-subtle text-foreground text-[13px] [&_>_span]:flex-[1_1_220px] [&_>_span]:min-w-0 [&_>_span]:wrap-anywhere [&_>_span]:leading-normal",
                )}
                role={actionNotice.kind === "error" ? "alert" : "status"}
              >
                <span>{actionNotice.text}</span>
                <button
                  type="button"
                  className={styleClasses(
                    "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                  )}
                  onClick={() => setActionNotice(null)}
                >
                  Dismiss
                </button>
              </p>
            )}
            {loadError && (
              <DetailNotice>
                {loadError}{" "}
                <button
                  type="button"
                  className={styleClasses(
                    "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                  )}
                  onClick={() => {
                    setRetryTick((n) => n + 1);
                    onRetry?.();
                  }}
                >
                  Retry
                </button>
              </DetailNotice>
            )}
            {narrow && (
              <PropertiesPanel inline title={null}>
                {coreProps}
              </PropertiesPanel>
            )}
            <DecisionPanel
              decisions={detail.decisions || []}
              onAnswer={answerOne}
              onCancel={cancelOne}
            />
            <MarkdownField
              data-issue-description=""
              value={detail.description}
              ready={detailReady}
              onSave={(text) =>
                change({ description: text }).then((ok) => {
                  if (ok) void attachGithubReferences(detail.id, text);
                  return ok;
                })
              }
            />

            <DetailSection
              title="Sub-tasks"
              badge={
                <>
                  <SubIssuePill done={subsDone} total={subsView.length} />
                </>
              }
              actions={
                <>
                  <span className="contents shell-phone:hidden">
                    <IconBtn
                      label="Sub-task display settings"
                      title="Sub-task display options are not available yet"
                      disabled
                    >
                      <SlidersHorizontal size={14} />
                    </IconBtn>
                  </span>
                  <IconBtn
                    label="Add sub-task"
                    title="Add sub-task"
                    onClick={() => setAddingSub((v) => !v)}
                  >
                    <Plus size={14} />
                  </IconBtn>
                </>
              }
            >
              {addingSub && (
                <div style={{ display: "flex", gap: 8, margin: "4px 0 8px" }}>
                  <input
                    className="min-w-0 pointer-coarse:min-h-11"
                    placeholder="Sub-task title…"
                    value={newSubTitle}
                    onChange={(e) => setNewSubTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void createSub();
                    }}
                    style={{
                      flex: 1,
                      border: "1px solid var(--line)",
                      borderRadius: 7,
                      padding: "8px 10px",
                      fontSize: 13,
                    }}
                  />
                  <button
                    type="button"
                    className={styleClasses(
                      "pointer-coarse:min-h-11 inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                    )}
                    disabled={!newSubTitle.trim()}
                    onClick={() => void createSub()}
                  >
                    Add
                  </button>
                </div>
              )}
              {subsView.map((s, subIndex) => (
                <div
                  key={s.id}
                  className={styleClasses(
                    cx(
                      "relative flex items-center gap-2.5 py-1.75 pr-2 pl-7 rounded-[7px] font-[inherit] text-[13px] w-full [background:none] border-0 text-left text-foreground cursor-pointer [&:hover]:bg-subtle pointer-coarse:min-h-11 shell-phone:[&_[data-style-hooks~='lin-icon-btn']]:hidden",
                      s.state?.type === "completed" && "",
                      isLocalIssueId(s.id) &&
                        "data-[style-hooks~='lin-subrow']:opacity-62 data-[style-hooks~='lin-comment']:opacity-62 [[data-style-hooks~='lin-comment']&_>_header_time]:italic",
                    ),
                  )}
                  data-style-hooks={styleHooks(
                    cx(
                      "lin-subrow flex items-center gap-[10px] py-[7px] px-[8px] rounded-[7px] font-[inherit] text-[13px] w-full [background:none] border-0 text-left text-foreground cursor-pointer [&:hover]:bg-subtle",
                      s.state?.type === "completed" && "done",
                      isLocalIssueId(s.id) &&
                        "pending data-[style-hooks~='lin-subrow']:opacity-62 data-[style-hooks~='lin-comment']:opacity-62 [[data-style-hooks~='lin-comment']&_>_header_time]:italic",
                    ),
                  )}
                  aria-busy={isLocalIssueId(s.id) || undefined}
                  {...(isLocalIssueId(s.id) ? {} : issueIntentProps(s))}
                >
                  {/* The rail runs the height of the list and closes on the last sub-task, so
                          the set reads as one branch of the task rather than as loose rows. */}
                  {subIndex === subsView.length - 1 ? (
                    <span
                      aria-hidden="true"
                      className="absolute left-3 top-0 h-1/2 w-2.5 border-l-2 border-b-2 border-line rounded-bl-[6px]"
                      data-tree-rail="last"
                    />
                  ) : (
                    <>
                      <span
                        aria-hidden="true"
                        className="absolute left-3 top-0 bottom-0 w-0.5 bg-line"
                        data-tree-rail="through"
                      />
                      <span
                        aria-hidden="true"
                        className="absolute left-3 top-1/2 h-0.5 w-2.5 bg-line"
                      />
                    </>
                  )}
                  <StateGlyph state={s.state} size={15} />
                  <span
                    className={styleClasses(
                      "in-data-[style-hooks~='lin-row']:font-semibold in-data-[style-hooks~='lin-row']:whitespace-nowrap in-data-[style-hooks~='lin-row']:text-foreground [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:font-[450] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:text-muted shell-phone:hidden",
                    )}
                    data-style-hooks={styleHooks(
                      "ident in-data-[style-hooks~='lin-row']:font-semibold in-data-[style-hooks~='lin-row']:whitespace-nowrap in-data-[style-hooks~='lin-row']:text-foreground [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:font-[450] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:text-muted",
                    )}
                  >
                    {s.identifier}
                  </span>
                  <button
                    type="button"
                    className={styleClasses(
                      "in-data-[style-hooks~='lin-subrow']:flex-1 in-data-[style-hooks~='lin-subrow']:min-w-0 in-data-[style-hooks~='lin-subrow']:overflow-hidden in-data-[style-hooks~='lin-subrow']:text-ellipsis in-data-[style-hooks~='lin-subrow']:whitespace-nowrap [[data-style-hooks~='lin-subrow'][data-style-hooks~='done']_&]:line-through [[data-style-hooks~='lin-subrow'][data-style-hooks~='done']_&]:text-muted pointer-coarse:min-h-11",
                    )}
                    data-style-hooks={styleHooks(
                      "t in-data-[style-hooks~='lin-subrow']:flex-1 in-data-[style-hooks~='lin-subrow']:min-w-0 in-data-[style-hooks~='lin-subrow']:overflow-hidden in-data-[style-hooks~='lin-subrow']:text-ellipsis in-data-[style-hooks~='lin-subrow']:whitespace-nowrap [[data-style-hooks~='lin-subrow'][data-style-hooks~='done']_&]:line-through [[data-style-hooks~='lin-subrow'][data-style-hooks~='done']_&]:text-muted",
                    )}
                    disabled={isLocalIssueId(s.id)}
                    onClick={() => onOpenIssue(s)}
                    title={s.title}
                  >
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate">{s.title}</span>
                      {s.description ? (
                        <span className="truncate text-[11px] font-normal text-muted">
                          <span className="hidden shell-phone:inline">
                            {s.identifier} ·{" "}
                          </span>
                          {s.description.split("\n")[0].slice(0, 96)}
                        </span>
                      ) : (
                        <span className="hidden truncate text-[11px] font-normal text-muted shell-phone:block">
                          {s.identifier}
                        </span>
                      )}
                    </span>
                  </button>
                  {s.labels?.nodes.map((l) => (
                    <LabelChip key={l.id} label={l} />
                  ))}
                  {s.assignee && (
                    <Avatar
                      name={s.assignee.name}
                      seed={s.assignee.avatarSeed}
                      size={18}
                    />
                  )}
                  <IconBtn
                    label={`Open sub-task ${s.identifier}`}
                    title="Open sub-task"
                    disabled={isLocalIssueId(s.id)}
                    onClick={() => onOpenIssue(s)}
                  >
                    <ExternalLink size={13} />
                  </IconBtn>
                </div>
              ))}
              {!subsView.length && !addingSub && (
                <p
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                >
                  {detailReady ? "No sub-tasks yet." : "Loading sub-tasks…"}
                </p>
              )}
            </DetailSection>

            <DetailSection
              title="Attachments"
              badge={
                <>
                  <span
                    style={{
                      color: "var(--muted)",
                      fontWeight: 500,
                      fontSize: 12,
                    }}
                    className={styleClasses(
                      "in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:font-medium in-data-[style-hooks~='lin-group-head']:text-[12px] [[data-style-hooks~='lin-bcol']_>_header_&]:text-muted [[data-style-hooks~='lin-bcol']_>_header_&]:font-normal",
                    )}
                    data-style-hooks={styleHooks(
                      "count in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:font-medium in-data-[style-hooks~='lin-group-head']:text-[12px] [[data-style-hooks~='lin-bcol']_>_header_&]:text-muted [[data-style-hooks~='lin-bcol']_>_header_&]:font-normal",
                    )}
                  >
                    {(detail.attachments?.nodes || []).length}
                  </span>
                </>
              }
              actions={
                <>
                  <span style={{ position: "relative" }}>
                    <IconBtn
                      label="Attach file or link"
                      title="Attach"
                      onClick={() => setAttachMenu((v) => !v)}
                    >
                      <Plus size={14} />
                    </IconBtn>
                    {attachMenu && (
                      <LinPopover
                        onClose={() => setAttachMenu(false)}
                        className={styleClasses(
                          "absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] max-w-[calc(100vw-2rem)] max-h-(--radix-popover-content-available-height) overflow-y-auto pointer-coarse:[&_input]:min-h-11 pointer-coarse:[&_button]:min-h-11 shell-phone:[&_button:first-child]:flex-1 shell-phone:[&_button:first-child]:justify-center",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-popover absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
                        )}
                        style={{
                          right: 0,
                          top: "calc(100% + 6px)",
                          width: 300,
                          padding: 12,
                        }}
                      >
                        <b style={{ fontSize: 13 }}>Attach</b>
                        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                          <button
                            type="button"
                            className={styleClasses(
                              "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                            )}
                            data-style-hooks={styleHooks(
                              "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                            )}
                            disabled={uploading}
                            onClick={() => fileRef.current?.click()}
                          >
                            {uploading ? "Uploading…" : "Upload file"}
                          </button>
                          <input
                            ref={fileRef}
                            type="file"
                            hidden
                            accept="image/*,video/*,.pdf,.txt,.md,.markdown,.csv,.tsv,.json,.rtf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp"
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              if (f) void doUpload(f);
                              e.target.value = "";
                            }}
                          />
                        </div>
                        <div
                          className={styleClasses("h-px bg-line my-1.25 mx-1")}
                          data-style-hooks={styleHooks(
                            "lin-menu-divider h-px bg-line my-[5px] mx-[4px]",
                          )}
                        />
                        <input
                          placeholder="Paste link URL…"
                          value={attachUrl}
                          onChange={(e) => setAttachUrl(e.target.value)}
                          style={{
                            width: "100%",
                            border: "1px solid var(--line)",
                            borderRadius: 6,
                            padding: "7px 10px",
                            fontSize: 13,
                            marginBottom: 8,
                          }}
                        />
                        <input
                          placeholder="Link title (optional)"
                          value={attachTitle}
                          onChange={(e) => setAttachTitle(e.target.value)}
                          style={{
                            width: "100%",
                            border: "1px solid var(--line)",
                            borderRadius: 6,
                            padding: "7px 10px",
                            fontSize: 13,
                            marginBottom: 8,
                          }}
                        />
                        <button
                          type="button"
                          className={styleClasses(
                            "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                          )}
                          data-style-hooks={styleHooks(
                            "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                          )}
                          style={{ width: "100%", justifyContent: "center" }}
                          disabled={!attachUrl.trim() || uploading}
                          onClick={() => void attachLink()}
                        >
                          Attach link
                        </button>
                      </LinPopover>
                    )}
                  </span>
                </>
              }
            >
              <div
                className={styleClasses("flex flex-col gap-1.5 my-1.5 mx-0")}
                data-style-hooks={styleHooks(
                  "lin-attach-list flex flex-col gap-[6px] my-[6px] mx-0",
                )}
              >
                {(detail.attachments?.nodes || []).map((a) => {
                  const preview =
                    attachmentKind(a) === "image" && !brokenThumbs[a.id];
                  return (
                    <div
                      key={a.id}
                      className={styleClasses(
                        "flex items-center gap-2.5 border border-solid border-line rounded-lg py-2 px-3 text-[13px] [&_a]:text-foreground [&_a]:font-[550] [&_a]:overflow-hidden [&_a]:text-ellipsis [&_a]:whitespace-nowrap [&_small]:text-muted pointer-coarse:py-0 pointer-coarse:min-h-11 pointer-coarse:[&_a]:py-3.5 pointer-coarse:[&>button]:py-3.5",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-attach flex items-center gap-[10px] border border-solid border-line rounded-[8px] py-[8px] px-[12px] text-[13px] [&_a]:text-foreground [&_a]:font-[550] [&_a]:overflow-hidden [&_a]:text-ellipsis [&_a]:whitespace-nowrap [&_small]:text-muted",
                      )}
                    >
                      {preview ? (
                        <img
                          className={styleClasses(
                            "[[data-style-hooks~='lin-attach']_img&]:w-11 [[data-style-hooks~='lin-attach']_img&]:h-11 [[data-style-hooks~='lin-attach']_img&]:object-cover [[data-style-hooks~='lin-attach']_img&]:rounded-md [[data-style-hooks~='lin-attach']_img&]:flex-none",
                          )}
                          data-style-hooks={styleHooks(
                            "thumb [[data-style-hooks~='lin-attach']_img&]:w-[44px] [[data-style-hooks~='lin-attach']_img&]:h-[44px] [[data-style-hooks~='lin-attach']_img&]:object-cover [[data-style-hooks~='lin-attach']_img&]:rounded-[6px] [[data-style-hooks~='lin-attach']_img&]:flex-none",
                          )}
                          src={a.url}
                          alt=""
                          loading="lazy"
                          decoding="async"
                          onError={() =>
                            setBrokenThumbs((m) => ({ ...m, [a.id]: true }))
                          }
                        />
                      ) : isTraceAttachment(a) ? (
                        <Activity
                          size={16}
                          color="var(--accent)"
                          aria-hidden="true"
                        />
                      ) : (
                        <FileText
                          size={16}
                          color="var(--muted)"
                          aria-hidden="true"
                        />
                      )}
                      {isTraceAttachment(a) ? (
                        <button
                          type="button"
                          className="min-w-0 truncate text-left font-semibold text-foreground hover:underline"
                          aria-haspopup="dialog"
                          onClick={() =>
                            setOpenTrace({
                              url: a.url || "",
                              title: a.title || "",
                            })
                          }
                        >
                          {a.title}
                        </button>
                      ) : (
                        <a
                          href={a.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {a.title}
                        </a>
                      )}
                      {a.subtitle && <small>{a.subtitle}</small>}
                      <small
                        className="flex-none whitespace-nowrap"
                        style={{ marginLeft: "auto" }}
                      >
                        {relTime(a.createdAt)}
                      </small>
                    </div>
                  );
                })}
              </div>
              {openTrace && (
                <TraceViewer
                  url={openTrace.url}
                  title={openTrace.title}
                  onClose={() => setOpenTrace(null)}
                />
              )}
              {!(detail.attachments?.nodes || []).length && (
                <p
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                >
                  {detailReady ? "No attachments yet." : "Loading attachments…"}
                </p>
              )}
            </DetailSection>

            {(() => {
              // Compact and absent by default: only rendered when something is actually linked,
              // so an issue with no GitHub work stays visually unchanged.
              const ghl = detail.githubLinks || [];
              if (!ghl.length) return null;
              return (
                <section
                  className={styleClasses(
                    "border-t border-t-line py-3.5 px-0 [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-2 [&_>_header]:text-[13px] [&_>_header]:font-semibold [&_>_header]:mb-1.5",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-subsec border-t border-t-line py-[14px] px-0 [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-[8px] [&_>_header]:text-[13px] [&_>_header]:font-semibold [&_>_header]:mb-[6px]",
                  )}
                  aria-label="GitHub"
                >
                  <header>
                    <ChevronDown size={14} color="var(--muted)" />
                    <span>GitHub</span>
                    <span
                      style={{
                        color: "var(--muted)",
                        fontWeight: 500,
                        fontSize: 12,
                      }}
                      className={styleClasses(
                        "in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:font-medium in-data-[style-hooks~='lin-group-head']:text-[12px] [[data-style-hooks~='lin-bcol']_>_header_&]:text-muted [[data-style-hooks~='lin-bcol']_>_header_&]:font-normal",
                      )}
                      data-style-hooks={styleHooks(
                        "count in-data-[style-hooks~='lin-group-head']:text-muted in-data-[style-hooks~='lin-group-head']:font-medium in-data-[style-hooks~='lin-group-head']:text-[12px] [[data-style-hooks~='lin-bcol']_>_header_&]:text-muted [[data-style-hooks~='lin-bcol']_>_header_&]:font-normal",
                      )}
                    >
                      {ghl.length}
                    </span>
                  </header>
                  <div
                    className={styleClasses(
                      "flex flex-col gap-1.5 my-1.5 mx-0",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-attach-list flex flex-col gap-[6px] my-[6px] mx-0",
                    )}
                  >
                    {ghl.map((l) => {
                      const kindLabel =
                        l.kind === "pr"
                          ? "PR"
                          : l.kind === "commit"
                            ? "Commit"
                            : l.kind === "deploy"
                              ? "Deploy"
                              : l.kind;
                      const detailText =
                        l.kind === "pr"
                          ? l.state
                          : l.kind === "commit"
                            ? l.sha
                              ? l.sha.slice(0, 7)
                              : ""
                            : l.kind === "deploy"
                              ? l.environment
                              : "";
                      return (
                        <div
                          key={l.id}
                          className={styleClasses(
                            "flex items-center gap-2.5 border border-solid border-line rounded-lg py-2 px-3 text-[13px] [&_a]:text-foreground [&_a]:font-[550] [&_a]:overflow-hidden [&_a]:text-ellipsis [&_a]:whitespace-nowrap [&_small]:text-muted",
                          )}
                          data-style-hooks={styleHooks(
                            "lin-attach flex items-center gap-[10px] border border-solid border-line rounded-[8px] py-[8px] px-[12px] text-[13px] [&_a]:text-foreground [&_a]:font-[550] [&_a]:overflow-hidden [&_a]:text-ellipsis [&_a]:whitespace-nowrap [&_small]:text-muted",
                          )}
                        >
                          <Link2 size={16} color="var(--muted)" />
                          <small
                            style={{
                              textTransform: "uppercase",
                              letterSpacing: ".04em",
                            }}
                          >
                            {kindLabel}
                          </small>
                          <a
                            href={l.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {l.title || l.externalId}
                          </a>
                          {detailText && <small>{detailText}</small>}
                          <small
                            className="flex-none whitespace-nowrap"
                            style={{ marginLeft: "auto" }}
                          >
                            {relTime(l.createdAt)}
                          </small>
                        </div>
                      );
                    })}
                  </div>
                </section>
              );
            })()}

            <DetailSection
              title="Activity"
              variant="activity"
              actions={
                <>
                  <button
                    type="button"
                    className={styleClasses(
                      "[background:none] border-0 text-muted text-[12px] font-medium inline-flex items-center gap-1.75 py-1.25 px-2 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-subscribe-btn [background:none] border-0 text-muted text-[12px] font-medium inline-flex items-center gap-[7px] py-[5px] px-[8px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
                    )}
                    onClick={() => void toggleSubscribe()}
                    aria-pressed={subscribed}
                  >
                    {subscribed ? "Subscribed" : "Subscribe"}
                  </button>
                  <span
                    className={styleClasses("flex gap-1.5 mt-2")}
                    data-style-hooks={styleHooks(
                      "lin-sublist flex gap-[6px] mt-[8px]",
                    )}
                  >
                    {(detail.subscribers?.nodes || []).slice(0, 5).map((s) => (
                      <Avatar
                        key={s.id}
                        name={s.name}
                        seed={s.avatarSeed}
                        size={20}
                      />
                    ))}
                  </span>
                </>
              }
            >
              {(detail.activity?.nodes || []).map((e) => (
                <div
                  key={e.id}
                  className={styleClasses(
                    "flex gap-2.5 items-start text-[13px] text-muted py-1.75 px-0 [&_b]:text-foreground [&_b]:font-semibold [&_time]:ml-auto [&_time]:flex-none [&_time]:text-[12px]",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-event flex gap-[10px] items-start text-[13px] text-muted py-[7px] px-0 [&_b]:text-foreground [&_b]:font-semibold [&_time]:ml-auto [&_time]:flex-none [&_time]:text-[12px]",
                  )}
                >
                  <Avatar
                    name={actorLabel(e)}
                    seed={e.actor?.avatarSeed}
                    size={18}
                  />
                  <span>
                    <b>{actorLabel(e)}</b> {describeEvent(e, states)}
                  </span>
                  <time>{relTime(e.createdAt)}</time>
                </div>
              ))}
              {!detailReady && (
                <p
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                  role="status"
                >
                  Loading activity…
                </p>
              )}
              {serverComments.map((c) => (
                <article
                  data-issue-comment
                  key={c.id}
                  className={styleClasses(
                    cx(
                      "border border-solid border-line rounded-lg py-2.75 px-3.25 my-2 mx-0 text-[13px] [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-2 [&_>_header]:mb-1.5 [&_>_header_b]:text-[13px] [&_>_header_time]:text-muted [&_>_header_time]:text-[12px]",
                      editedComments[c.id] !== undefined &&
                        "data-[style-hooks~='lin-subrow']:opacity-62 data-[style-hooks~='lin-comment']:opacity-62 [[data-style-hooks~='lin-comment']&_>_header_time]:italic",
                    ),
                  )}
                  data-style-hooks={styleHooks(
                    cx(
                      "lin-comment border border-solid border-line rounded-[8px] py-[11px] px-[13px] my-[8px] mx-0 text-[13px] [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-[8px] [&_>_header]:mb-[6px] [&_>_header_b]:text-[13px] [&_>_header_time]:text-muted [&_>_header_time]:text-[12px]",
                      editedComments[c.id] !== undefined &&
                        "pending data-[style-hooks~='lin-subrow']:opacity-62 data-[style-hooks~='lin-comment']:opacity-62 [[data-style-hooks~='lin-comment']&_>_header_time]:italic",
                    ),
                  )}
                  aria-busy={editedComments[c.id] !== undefined || undefined}
                >
                  <header>
                    <Avatar
                      name={c.user?.name}
                      seed={c.user?.avatarSeed}
                      size={18}
                    />
                    <b>{c.user?.name || "User"}</b>
                    <time>
                      {editedComments[c.id] !== undefined
                        ? "Saving…"
                        : relTime(c.createdAt)}
                    </time>
                    {c.editedAt && editedComments[c.id] === undefined && (
                      <span
                        className={styleClasses(
                          "[[data-style-hooks~='lin-comment']_>_header_&]:text-muted [[data-style-hooks~='lin-comment']_>_header_&]:text-[12px]",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-comment-edited [[data-style-hooks~='lin-comment']_>_header_&]:text-muted [[data-style-hooks~='lin-comment']_>_header_&]:text-[12px]",
                        )}
                        title={`Edited ${relTime(c.editedAt)}`}
                      >
                        (edited)
                      </span>
                    )}
                  </header>
                  {editing?.id === c.id && c.canEdit ? (
                    <form
                      className={styleClasses(
                        "[[data-style-hooks~='lin-comment']_>_&]:mt-1 border border-solid border-line rounded-[9px] mt-3.5 bg-panel [&_textarea]:w-full [&_textarea]:border-0 [&_textarea]:bg-transparent [&_textarea]:resize-y [&_textarea]:min-h-16 [&_textarea]:py-3 [&_textarea]:px-3.5 [&_textarea]:[font:inherit] [&_textarea]:text-[13px] [&_textarea]:text-foreground [&_textarea:focus]:[outline:none] [&_footer]:flex [&_footer]:items-center [&_footer]:justify-end [&_footer]:gap-1 [&_footer]:py-1.5 [&_footer]:px-2 [&_footer]:border-t [&_footer]:border-t-line",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-commentbox [[data-style-hooks~='lin-comment']_>_&]:mt-[4px] border border-solid border-line rounded-[9px] mt-[14px] bg-panel [&_textarea]:w-full [&_textarea]:border-0 [&_textarea]:bg-transparent [&_textarea]:resize-y [&_textarea]:min-h-[64px] [&_textarea]:py-[12px] [&_textarea]:px-[14px] [&_textarea]:[font:inherit] [&_textarea]:text-[13px] [&_textarea]:text-foreground [&_textarea:focus]:[outline:none] [&_footer]:flex [&_footer]:items-center [&_footer]:justify-end [&_footer]:gap-[4px] [&_footer]:py-[6px] [&_footer]:px-[8px] [&_footer]:border-t [&_footer]:border-t-line",
                      )}
                      onSubmit={(e) => {
                        e.preventDefault();
                        void saveComment(c.id, editing.body);
                      }}
                    >
                      <textarea
                        rows={2}
                        value={editing.body}
                        aria-label="Edit comment"
                        onChange={(e) =>
                          setEditing({ id: c.id, body: e.target.value })
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Escape") setEditing(null);
                          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                            e.preventDefault();
                            e.currentTarget.form?.requestSubmit();
                          }
                        }}
                      />
                      <footer>
                        <button
                          type="button"
                          className={styleClasses(
                            "pointer-coarse:min-h-11 inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                          )}
                          data-style-hooks={styleHooks(
                            "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default",
                          )}
                          onClick={() => setEditing(null)}
                        >
                          Cancel
                        </button>
                        <button
                          type="submit"
                          className={styleClasses(
                            "pointer-coarse:min-h-11 inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                          )}
                          data-style-hooks={styleHooks(
                            "lin-btn inline-flex items-center gap-[8px] border border-solid border-line bg-panel text-foreground rounded-[7px] py-[8px] px-[14px] text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default primary data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
                          )}
                          disabled={!editing.body.trim()}
                        >
                          Save
                        </button>
                      </footer>
                    </form>
                  ) : (
                    <Markdown text={c.body} />
                  )}
                  {editing?.id !== c.id && (
                    <>
                      {c.canEdit && (
                        <button
                          type="button"
                          className={styleClasses(
                            "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground in-data-[style-hooks~='lin-comment']:mt-2 pointer-coarse:min-h-11 pointer-coarse:in-data-[style-hooks~='lin-comment']:mt-0 pointer-coarse:-mb-2.5",
                          )}
                          data-style-hooks={styleHooks(
                            "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground del in-data-[style-hooks~='lin-comment']:mt-[8px]",
                          )}
                          onClick={() => setEditing({ id: c.id, body: c.body })}
                        >
                          Edit comment
                        </button>
                      )}
                      {c.canDelete && (
                        <button
                          type="button"
                          className={styleClasses(
                            "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground in-data-[style-hooks~='lin-comment']:mt-2 pointer-coarse:min-h-11 pointer-coarse:in-data-[style-hooks~='lin-comment']:mt-0 pointer-coarse:-mb-2.5",
                          )}
                          data-style-hooks={styleHooks(
                            "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground del in-data-[style-hooks~='lin-comment']:mt-[8px]",
                          )}
                          onClick={async () => {
                            if (
                              await confirmDialog(
                                c.user?.id && c.user.id !== viewer.id
                                  ? `Delete ${c.user?.name || "this member"}'s comment? This removes it for everyone.`
                                  : "Delete this comment?",
                                { danger: true },
                              )
                            )
                              void deleteComment(c.id);
                          }}
                        >
                          Delete comment
                        </button>
                      )}
                    </>
                  )}
                </article>
              ))}
              {shownPendingComments.map((p) => (
                <article
                  key={p.id}
                  className={styleClasses(
                    "border border-solid border-line rounded-lg py-2.75 px-3.25 my-2 mx-0 text-[13px] [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-2 [&_>_header]:mb-1.5 [&_>_header_b]:text-[13px] [&_>_header_time]:text-muted [&_>_header_time]:text-[12px] data-[style-hooks~='lin-subrow']:opacity-62 data-[style-hooks~='lin-comment']:opacity-62 [[data-style-hooks~='lin-comment']&_>_header_time]:italic",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-comment border border-solid border-line rounded-[8px] py-[11px] px-[13px] my-[8px] mx-0 text-[13px] [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-[8px] [&_>_header]:mb-[6px] [&_>_header_b]:text-[13px] [&_>_header_time]:text-muted [&_>_header_time]:text-[12px] pending data-[style-hooks~='lin-subrow']:opacity-62 data-[style-hooks~='lin-comment']:opacity-62 [[data-style-hooks~='lin-comment']&_>_header_time]:italic",
                  )}
                  aria-busy="true"
                >
                  <header>
                    <Avatar
                      name={viewer.name}
                      seed={viewer.avatarSeed}
                      size={18}
                    />
                    <b>{viewer.name || "You"}</b>
                    <time>Sending…</time>
                  </header>
                  <Markdown text={p.body} />
                </article>
              ))}
              <form
                className={styleClasses(
                  "[[data-style-hooks~='lin-comment']_>_&]:mt-1 border border-solid border-line rounded-[9px] mt-3.5 bg-panel [&_textarea]:w-full [&_textarea]:border-0 [&_textarea]:bg-transparent [&_textarea]:resize-y [&_textarea]:min-h-16 [&_textarea]:py-3 [&_textarea]:px-3.5 [&_textarea]:[font:inherit] [&_textarea]:text-[13px] [&_textarea]:text-foreground [&_textarea:focus]:[outline:none] [&_footer]:flex [&_footer]:items-center [&_footer]:justify-end [&_footer]:gap-1 [&_footer]:py-1.5 [&_footer]:px-2 [&_footer]:border-t [&_footer]:border-t-line",
                )}
                data-style-hooks={styleHooks(
                  "lin-commentbox [[data-style-hooks~='lin-comment']_>_&]:mt-[4px] border border-solid border-line rounded-[9px] mt-[14px] bg-panel [&_textarea]:w-full [&_textarea]:border-0 [&_textarea]:bg-transparent [&_textarea]:resize-y [&_textarea]:min-h-[64px] [&_textarea]:py-[12px] [&_textarea]:px-[14px] [&_textarea]:[font:inherit] [&_textarea]:text-[13px] [&_textarea]:text-foreground [&_textarea:focus]:[outline:none] [&_footer]:flex [&_footer]:items-center [&_footer]:justify-end [&_footer]:gap-[4px] [&_footer]:py-[6px] [&_footer]:px-[8px] [&_footer]:border-t [&_footer]:border-t-line",
                )}
                onSubmit={submitComment}
              >
                <div className="relative">
                  <MentionMenu
                    matches={mentions.matches}
                    index={mentions.index}
                    pick={mentions.pick}
                  />
                  <textarea
                    ref={commentRef}
                    rows={2}
                    value={comment}
                    aria-label="Leave a comment"
                    placeholder="Leave a comment… Type @ to mention someone"
                    onChange={(e) => {
                      setComment(e.target.value);
                      if (commentError) setCommentError("");
                      mentions.track();
                    }}
                    onClick={mentions.track}
                    onBlur={mentions.close}
                    onKeyDown={(e) => {
                      if (mentions.onKeyDown(e)) return;
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        e.currentTarget.form?.requestSubmit();
                      }
                    }}
                  />
                </div>
                <footer>
                  <Tip label="Attach a file">
                    <button
                      type="button"
                      className={styleClasses(
                        "grid place-items-center w-7 h-7 rounded-md bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
                      )}
                      data-style-hooks={styleHooks(
                        "lin-icon-btn grid place-items-center w-[28px] h-[28px] rounded-[6px] bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
                      )}
                      aria-label="Attach a file"
                      disabled={uploading}
                      onClick={() => {
                        /* Opens the file picker in place. The attach panel hangs off the
                         * Attachments header, which a long thread pushes far above the
                         * composer, so opening it from here jumped the page away from the
                         * comment being written. Links are attached from that header. */
                        composerFileRef.current?.click();
                      }}
                    >
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 16 16"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.5"
                      >
                        <path
                          d="M11.5 8.5l-4.7 4.7a2.4 2.4 0 0 1-3.4-3.4l5.4-5.4a1.6 1.6 0 0 1 2.3 2.3L5.7 12"
                          strokeLinecap="round"
                        />
                      </svg>
                    </button>
                  </Tip>
                  <input
                    ref={composerFileRef}
                    type="file"
                    hidden
                    accept="image/*,video/*,.pdf,.txt,.md,.markdown,.csv,.tsv,.json,.rtf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void doUpload(f);
                      e.target.value = "";
                    }}
                  />
                  <button
                    type="submit"
                    className={styleClasses(
                      "grid place-items-center w-7 h-7 rounded-md bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-icon-btn grid place-items-center w-[28px] h-[28px] rounded-[6px] bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
                    )}
                    aria-label="Submit comment"
                    title="Submit comment (⌘↵)"
                    disabled={!comment.trim()}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                    >
                      <path
                        d="M8 13V3M4 7l4-4 4 4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                </footer>
              </form>
              {commentError && (
                <p
                  className={styleClasses(
                    "in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-error in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-[10px] my-[16px] mx-[24px] py-[12px] px-[16px] border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-[8px] text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]",
                  )}
                  role="alert"
                >
                  {commentError}
                </p>
              )}
            </DetailSection>
          </>
        }
        aside={
          <PropertiesPanel title={narrow ? "More properties" : "Properties"}>
            {!narrow && coreProps}

            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
              data-style-hooks={styleHooks(
                "sec in-data-[style-hooks~='lin-props']:mt-[18px] in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-[4px] in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
            >
              Labels
            </div>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                marginBottom: 6,
              }}
            >
              {(detail.labels?.nodes || []).map((l) => (
                <LabelChip
                  key={l.id}
                  label={l}
                  onRemove={() =>
                    void change({
                      labelIds: labelIds.filter((id) => id !== l.id),
                    })
                  }
                />
              ))}
            </div>
            <span style={{ position: "relative" }}>
              <button
                type="button"
                className={styleClasses(
                  "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
                )}
                data-style-hooks={styleHooks(
                  "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
                )}
                onClick={() => setLabelOpen((v) => !v)}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <path
                    d="M2.5 2.5h5.2l5.8 5.8-5.2 5.2-5.8-5.8V2.5Z"
                    strokeLinejoin="round"
                  />
                  <circle cx="6" cy="6" r="1.2" fill="currentColor" />
                </svg>
                Add label
              </button>
              {labelOpen && (
                <LinPopover
                  onClose={() => setLabelOpen(false)}
                  className={styleClasses(
                    "absolute z-130 min-w-55 max-w-[320px] p-1.25 border border-solid border-line rounded-[9px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] top-[calc(100%+4px)] left-0 min-w-57.5 max-h-[min(20rem,var(--radix-popover-content-available-height))] overflow-y-auto",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-menu absolute z-130 min-w-[220px] max-w-[320px] p-[5px] border border-solid border-line rounded-[9px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] lin-dd-panel absolute top-[calc(100%+4px)] left-0 z-130 min-w-[230px] max-h-[320px] overflow-y-auto",
                  )}
                  role="listbox"
                  aria-label="Add label"
                >
                  {labels
                    .filter((l) => !labelIds.includes(l.id))
                    .map((l) => (
                      <button
                        key={l.id}
                        type="button"
                        className={styleClasses(
                          "flex items-center gap-2.25 w-full py-1.75 px-2.5 rounded-md bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle aria-selected:font-semibold pointer-coarse:min-h-11",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-dd-opt flex items-center gap-[9px] w-full py-[7px] px-[10px] rounded-[6px] bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle aria-selected:font-semibold",
                        )}
                        onClick={() => {
                          setLabelOpen(false);
                          void change({ labelIds: [...labelIds, l.id] });
                        }}
                      >
                        <LabelChip label={l} />
                      </button>
                    ))}
                  {!labels.filter((l) => !labelIds.includes(l.id)).length && (
                    <p
                      className={styleClasses("text-[12px] text-muted")}
                      data-style-hooks={styleHooks(
                        "lin-note text-[12px] text-muted",
                      )}
                      style={{ padding: "8px 10px" }}
                    >
                      All labels added.
                    </p>
                  )}
                </LinPopover>
              )}
            </span>

            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
              data-style-hooks={styleHooks(
                "sec in-data-[style-hooks~='lin-props']:mt-[18px] in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-[4px] in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
            >
              Estimate
            </div>
            <div
              className={styleClasses(
                "flex items-center gap-2.5 min-h-8.5 text-[13px] relative",
              )}
              data-style-hooks={styleHooks(
                "lin-prop flex items-center gap-[10px] min-h-[34px] text-[13px] relative",
              )}
            >
              <span
                className={styleClasses(
                  "in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-2 in-data-[style-hooks~='lin-prop']:text-muted in-data-[style-hooks~='lin-prop']:w-24 in-data-[style-hooks~='lin-prop']:flex-none",
                )}
                data-style-hooks={styleHooks(
                  "k in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[8px] in-data-[style-hooks~='lin-prop']:text-muted in-data-[style-hooks~='lin-prop']:w-[96px] in-data-[style-hooks~='lin-prop']:flex-none",
                )}
              >
                ⧗ Estimate
              </span>
              <span
                className={styleClasses(
                  "in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-1.75",
                )}
                data-style-hooks={styleHooks(
                  "v in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[7px]",
                )}
              >
                <input
                  type="number"
                  min={0}
                  step={1}
                  aria-label="Estimate (points)"
                  className="pointer-coarse:min-h-11"
                  value={detail.estimate ?? ""}
                  onChange={(e) => {
                    const v =
                      e.target.value === "" ? null : Number(e.target.value);
                    void change({ estimate: v });
                  }}
                  style={{
                    width: 90,
                    border: "1px solid var(--line)",
                    borderRadius: 6,
                    padding: "5px 8px",
                    fontSize: 13,
                  }}
                />
              </span>
            </div>
            <div
              className={styleClasses(
                "flex items-center gap-2.5 min-h-8.5 text-[13px] relative",
              )}
              data-style-hooks={styleHooks(
                "lin-prop flex items-center gap-[10px] min-h-[34px] text-[13px] relative",
              )}
              style={{ marginTop: 4 }}
            >
              <span
                className={styleClasses(
                  "in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-2 in-data-[style-hooks~='lin-prop']:text-muted in-data-[style-hooks~='lin-prop']:w-24 in-data-[style-hooks~='lin-prop']:flex-none",
                )}
                data-style-hooks={styleHooks(
                  "k in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[8px] in-data-[style-hooks~='lin-prop']:text-muted in-data-[style-hooks~='lin-prop']:w-[96px] in-data-[style-hooks~='lin-prop']:flex-none",
                )}
              >
                ◷ Due date
              </span>
              <span
                className={styleClasses(
                  "in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-1.75",
                )}
                data-style-hooks={styleHooks(
                  "v in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[7px]",
                )}
              >
                <input
                  type="date"
                  aria-label="Due date"
                  className="pointer-coarse:min-h-11"
                  value={(detail.dueDate || "").slice(0, 10)}
                  onChange={(e) =>
                    void change({ dueDate: e.target.value || null })
                  }
                  style={{
                    border: "1px solid var(--line)",
                    borderRadius: 6,
                    padding: "5px 8px",
                    fontSize: 13,
                  }}
                />
              </span>
            </div>

            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
              data-style-hooks={styleHooks(
                "sec in-data-[style-hooks~='lin-props']:mt-[18px] in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-[4px] in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
            >
              Parent
            </div>
            <div
              className={styleClasses(
                "flex items-center gap-2.5 min-h-8.5 text-[13px] relative",
              )}
              data-style-hooks={styleHooks(
                "lin-prop flex items-center gap-[10px] min-h-[34px] text-[13px] relative",
              )}
            >
              <span
                className={styleClasses(
                  "in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-1.75",
                )}
                data-style-hooks={styleHooks(
                  "v in-data-[style-hooks~='lin-prop']:flex-1 in-data-[style-hooks~='lin-prop']:min-w-0 in-data-[style-hooks~='lin-prop']:flex in-data-[style-hooks~='lin-prop']:items-center in-data-[style-hooks~='lin-prop']:gap-[7px]",
                )}
                style={{
                  position: "relative",
                  flexDirection: "column",
                  alignItems: "stretch",
                }}
              >
                <span className="flex w-full min-w-0 items-center gap-1.75">
                  {detail.parent ? (
                    <button
                      type="button"
                      className={styleClasses(
                        "[[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[background:none] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:border-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[font:inherit] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-foreground [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:py-1 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:px-1.5 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:rounded-md [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:inline-flex [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:items-center [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:gap-1.75 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:min-w-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-left [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&:hover]:bg-subtle",
                      )}
                      data-style-hooks={styleHooks(
                        "plain [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[background:none] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:border-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:[font:inherit] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-foreground [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:py-[4px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:px-[6px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:rounded-[6px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:inline-flex [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:items-center [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:gap-[7px] [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:min-w-0 [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&]:text-left [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_button&:hover]:bg-subtle",
                      )}
                      onClick={() =>
                        onOpenIssue({
                          ...(detail.parent as LinIssue),
                          id: detail.parent!.id,
                        })
                      }
                    >
                      <span
                        className={styleClasses(
                          "[[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:overflow-hidden [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:text-ellipsis [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:whitespace-nowrap",
                        )}
                        data-style-hooks={styleHooks(
                          "trunc [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:overflow-hidden [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:text-ellipsis [[data-style-hooks~='lin-prop']_[data-style-hooks~='v']_&]:whitespace-nowrap",
                        )}
                      >
                        <b>{detail.parent.identifier}</b> {detail.parent.title}
                      </span>
                    </button>
                  ) : (
                    <span
                      className={styleClasses("text-[12px] text-muted")}
                      data-style-hooks={styleHooks(
                        "lin-note text-[12px] text-muted",
                      )}
                    >
                      No parent
                    </span>
                  )}
                  {detail.parent && (
                    <IconBtn
                      label="Remove parent"
                      title="Remove parent"
                      onClick={() => void change({ parentId: null })}
                    >
                      <X size={13} />
                    </IconBtn>
                  )}
                </span>
                {/* Same picker both ways, so moving an issue between parents is one step. */}
                <button
                  type="button"
                  className={styleClasses(
                    "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
                  )}
                  data-style-hooks={styleHooks(
                    "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
                  )}
                  onClick={() => setParentSearch((v) => !v)}
                >
                  {detail.parent ? "Change parent…" : "Set parent…"}
                </button>
                {parentSearch && (
                  <LinPopover
                    onClose={() => setParentSearch(false)}
                    className={styleClasses(
                      "absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f] max-w-[calc(100vw-2rem)] max-h-(--radix-popover-content-available-height) overflow-y-auto pointer-coarse:[&_input]:min-h-11 pointer-coarse:[&_button]:min-h-11",
                    )}
                    data-style-hooks={styleHooks(
                      "lin-popover absolute z-130 border border-solid border-line rounded-[10px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
                    )}
                    style={{
                      left: 0,
                      top: "calc(100% + 4px)",
                      width: 300,
                      padding: 8,
                    }}
                  >
                    <input
                      placeholder="Search tasks…"
                      aria-label="Search for a parent task"
                      value={parentQuery}
                      onChange={(e) => setParentQuery(e.target.value)}
                      style={{
                        width: "100%",
                        border: "1px solid var(--line)",
                        borderRadius: 6,
                        padding: "7px 10px",
                        fontSize: 13,
                        marginBottom: 6,
                      }}
                    />
                    {parentResults.map((r) => (
                      <button
                        key={r.id}
                        type="button"
                        className={styleClasses(
                          "flex items-center gap-2.25 w-full py-1.75 px-2.5 rounded-md bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle aria-selected:font-semibold pointer-coarse:min-h-11",
                        )}
                        data-style-hooks={styleHooks(
                          "lin-dd-opt flex items-center gap-[9px] w-full py-[7px] px-[10px] rounded-[6px] bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle aria-selected:font-semibold",
                        )}
                        onClick={() => {
                          setParentSearch(false);
                          setParentQuery("");
                          void change({ parentId: r.id });
                        }}
                      >
                        <span
                          className={styleClasses(
                            "in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere",
                          )}
                          data-style-hooks={styleHooks(
                            "grow in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere",
                          )}
                        >
                          <b>{r.identifier}</b> {r.title}
                        </span>
                      </button>
                    ))}
                  </LinPopover>
                )}
              </span>
            </div>

            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
              data-style-hooks={styleHooks(
                "sec in-data-[style-hooks~='lin-props']:mt-[18px] in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-[4px] in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
            >
              Subscribers
            </div>
            <div
              className={styleClasses("flex gap-1.5 mt-2")}
              data-style-hooks={styleHooks(
                "lin-sublist flex gap-[6px] mt-[8px]",
              )}
            >
              {(detail.subscribers?.nodes || []).map((s) => (
                <Avatar
                  key={s.id}
                  name={s.name}
                  seed={s.avatarSeed}
                  size={22}
                />
              ))}
              {!detail.subscribers?.nodes.length && (
                <span
                  className={styleClasses("text-[12px] text-muted")}
                  data-style-hooks={styleHooks(
                    "lin-note text-[12px] text-muted",
                  )}
                >
                  No subscribers
                </span>
              )}
            </div>

            <TaskUsageSection issueId={detail.id} />

            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
              data-style-hooks={styleHooks(
                "sec in-data-[style-hooks~='lin-props']:mt-[18px] in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-[4px] in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
            >
              Timestamps
            </div>
            <div
              className={styleClasses("text-[12px] text-muted py-0.75 px-0")}
              data-style-hooks={styleHooks(
                "lin-ts text-[12px] text-muted py-[3px] px-0",
              )}
            >
              Created{" "}
              {detail.createdAt
                ? new Date(detail.createdAt).toLocaleString()
                : "—"}
            </div>
            <div
              className={styleClasses("text-[12px] text-muted py-0.75 px-0")}
              data-style-hooks={styleHooks(
                "lin-ts text-[12px] text-muted py-[3px] px-0",
              )}
            >
              Updated{" "}
              {detail.updatedAt
                ? new Date(detail.updatedAt).toLocaleString()
                : "—"}
            </div>

            <div
              className={styleClasses(
                "in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
              data-style-hooks={styleHooks(
                "sec in-data-[style-hooks~='lin-props']:mt-[18px] in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-[4px] in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted",
              )}
            >
              Related
            </div>
            <Tip label="Task relations are not available in Hoja yet">
              <button
                type="button"
                className={styleClasses(
                  "inline-flex items-center gap-2 [background:none] border-0 text-muted text-[13px] py-1.25 px-1.5 rounded-md [&:hover]:bg-subtle [&:hover]:text-foreground",
                )}
                data-style-hooks={styleHooks(
                  "lin-addrow inline-flex items-center gap-[8px] [background:none] border-0 text-muted text-[13px] py-[5px] px-[6px] rounded-[6px] [&:hover]:bg-subtle [&:hover]:text-foreground",
                )}
                disabled
                style={{ opacity: 0.5, cursor: "not-allowed" }}
              >
                Add relationship…
              </button>
            </Tip>
          </PropertiesPanel>
        }
      />
    </DetailPage>
  );
}
