/* Display options for task and project lists: what the Display panel edits and how a list
 * applies them (grouping, sub-grouping, ordering, the completed window, sub-issues, empty
 * groups). The document is the server's `display` (docs/views.md), so a page's options are
 * stored through `viewPreferences` and a saved view's through its `display`.
 *
 * Pure: `scripts/test-view-filters.mjs` runs it with node.
 */
import type { TaskLike } from "./filterEval";
import { compareRank } from "./ranking";

export type Layout = "list" | "board" | "timeline";
export type CompletedWindow =
  "none" | "day" | "week" | "month" | "quarter" | "halfYear" | "all";
export type NestedSubIssues = "all" | "matching" | "hide";

export type DisplayDoc = {
  layout?: Layout;
  grouping?: string;
  subGrouping?: string;
  ordering?: string;
  reverse?: boolean;
  visibleProperties?: string[];
  completedWindow?: CompletedWindow;
  showEmptyGroups?: boolean;
  showSubIssues?: boolean;
  nestedSubIssues?: NestedSubIssues;
  orderCompletedByRecency?: boolean;
  hiddenColumns?: string[];
  collapsedGroups?: string[];
  boardColumnOrder?: string[];
  timelineZoom?: number;
  /** Grouping by label group: which group's labels become the groups. */
  groupingLabelGroupId?: string | null;
  /** Initiative views: nest sub-initiatives under their parent; show summaries. */
  nested?: boolean;
  summary?: boolean;
};

export type Option = { value: string; label: string; disabled?: string };

export const ISSUE_GROUPINGS: Option[] = [
  { value: "none", label: "No grouping" },
  { value: "status", label: "Status" },
  { value: "statusType", label: "Status type" },
  { value: "assignee", label: "Assignee" },
  { value: "creator", label: "Creator" },
  { value: "project", label: "Project" },
  { value: "priority", label: "Priority" },
  { value: "cycle", label: "Cycle" },
  { value: "labels", label: "Label" },
  { value: "labelGroup", label: "Label group" },
  { value: "team", label: "Team" },
];

/** Initiative views (views.py INITIATIVE_GROUPINGS / INITIATIVE_ORDERINGS). */
export const INITIATIVE_GROUPINGS: Option[] = [
  { value: "none", label: "No grouping" },
  { value: "status", label: "Status" },
  { value: "health", label: "Health" },
  { value: "owner", label: "Owner" },
  { value: "leadTeam", label: "Lead team" },
];
export const INITIATIVE_ORDERINGS: Option[] = [
  { value: "manual", label: "Manual" },
  { value: "name", label: "Name" },
  { value: "targetDate", label: "Target date" },
  { value: "created", label: "Created" },
  { value: "health", label: "Health" },
  { value: "priority", label: "Priority" },
];

export const ISSUE_ORDERINGS: Option[] = [
  { value: "manual", label: "Manual" },
  { value: "title", label: "Title" },
  { value: "status", label: "Status" },
  { value: "priority", label: "Priority" },
  { value: "estimate", label: "Estimate" },
  { value: "updated", label: "Updated" },
  { value: "created", label: "Created" },
  { value: "dueDate", label: "Due date" },
];

export const COMPLETED_WINDOWS: Option[] = [
  { value: "all", label: "All" },
  { value: "day", label: "Past day" },
  { value: "week", label: "Past week" },
  { value: "month", label: "Past month" },
  { value: "none", label: "None" },
];

export const NESTED_OPTIONS: Option[] = [
  { value: "all", label: "Show all" },
  { value: "matching", label: "Show matching" },
  { value: "hide", label: "Hide" },
];

/** Display properties in Linear's order; `value` is the server's visibleProperties name. */
export const ISSUE_PROPERTIES: Option[] = [
  { value: "identifier", label: "ID" },
  { value: "status", label: "Status" },
  { value: "assignee", label: "Assignee" },
  { value: "creator", label: "Creator" },
  { value: "priority", label: "Priority" },
  { value: "project", label: "Project" },
  { value: "dueDate", label: "Due date" },
  {
    value: "milestone",
    label: "Milestone",
    disabled: "Hoja projects have no milestones yet.",
  },
  { value: "labels", label: "Labels" },
  { value: "links", label: "Links" },
  { value: "timeInStatus", label: "Time in status" },
  { value: "createdAt", label: "Created" },
  { value: "updatedAt", label: "Updated" },
  { value: "pullRequests", label: "Pull requests" },
  { value: "estimate", label: "Estimate" },
];

export const PROJECT_GROUPINGS: Option[] = [
  { value: "none", label: "No grouping" },
  { value: "lead", label: "Lead" },
  { value: "members", label: "Member" },
  { value: "status", label: "Status" },
  { value: "statusType", label: "Status type" },
  { value: "health", label: "Health" },
  { value: "team", label: "Team" },
  { value: "startDate", label: "Start date" },
  { value: "targetDate", label: "Target date" },
];

export const PROJECT_ORDERINGS: Option[] = [
  { value: "manual", label: "Manual" },
  { value: "name", label: "Name" },
  { value: "status", label: "Status" },
  { value: "updated", label: "Updated" },
  { value: "created", label: "Created" },
  { value: "startDate", label: "Start date" },
  { value: "targetDate", label: "Target date" },
];

export const PROJECT_WINDOWS: Option[] = [
  { value: "all", label: "All" },
  { value: "week", label: "Past week" },
  { value: "month", label: "Past month" },
  { value: "quarter", label: "Past 3 months" },
  { value: "halfYear", label: "Past 6 months" },
  { value: "none", label: "None" },
];

export const PROJECT_PROPERTIES: Option[] = [
  { value: "id", label: "ID" },
  { value: "summary", label: "Summary" },
  { value: "status", label: "Status" },
  { value: "health", label: "Health" },
  { value: "lead", label: "Lead" },
  { value: "members", label: "Members" },
  { value: "startDate", label: "Start date" },
  { value: "targetDate", label: "Target date" },
  { value: "issues", label: "Issues" },
  { value: "progress", label: "Progress" },
  { value: "createdAt", label: "Created" },
  { value: "updatedAt", label: "Updated" },
  { value: "completedAt", label: "Completed" },
  { value: "teams", label: "Teams" },
];

/** What a task list shows before anyone changes anything (Linear's system defaults). */
export const ISSUE_DEFAULTS: Required<
  Pick<
    DisplayDoc,
    | "layout"
    | "grouping"
    | "subGrouping"
    | "ordering"
    | "reverse"
    | "visibleProperties"
    | "completedWindow"
    | "showEmptyGroups"
    | "showSubIssues"
    | "nestedSubIssues"
    | "orderCompletedByRecency"
    | "hiddenColumns"
    | "collapsedGroups"
  >
> = {
  layout: "list",
  grouping: "status",
  subGrouping: "none",
  ordering: "priority",
  reverse: false,
  visibleProperties: [
    "identifier",
    "status",
    "assignee",
    "priority",
    "project",
    "labels",
    "createdAt",
  ],
  completedWindow: "week",
  showEmptyGroups: false,
  showSubIssues: true,
  // Linear's default since 2026-09: a filtered list nests only the sub-issues that match.
  nestedSubIssues: "matching",
  orderCompletedByRecency: false,
  hiddenColumns: [],
  collapsedGroups: [],
};

export const PROJECT_DEFAULTS: DisplayDoc = {
  layout: "list",
  grouping: "none",
  subGrouping: "none",
  ordering: "manual",
  reverse: false,
  visibleProperties: [
    "status",
    "health",
    "lead",
    "targetDate",
    "progress",
    "teams",
  ],
  completedWindow: "all",
};

export type EffectiveDisplay = typeof ISSUE_DEFAULTS & DisplayDoc;

/** Layers, lowest first: system defaults, the page's own defaults, the shared default, the user's. */
export function mergeDisplay(
  ...layers: (DisplayDoc | null | undefined)[]
): EffectiveDisplay {
  const out: DisplayDoc = {};
  for (const layer of layers) {
    if (!layer) continue;
    for (const [k, v] of Object.entries(layer))
      if (v !== undefined && v !== null)
        (out as Record<string, unknown>)[k] = v;
  }
  return { ...ISSUE_DEFAULTS, ...out } as EffectiveDisplay;
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Keys of `user` that change what `base` would show: drives the dot on the Display button. */
export function divergentKeys(
  user: DisplayDoc | null | undefined,
  base: DisplayDoc,
): string[] {
  if (!user) return [];
  // Collapsing a group is a per-session gesture, not a change of the view's meaning.
  return Object.keys(user).filter(
    (k) =>
      k !== "collapsedGroups" &&
      !same(
        (user as Record<string, unknown>)[k],
        (base as Record<string, unknown>)[k],
      ),
  );
}

/* ------------------------------------------------------------- applying */

export type GroupCtx = {
  /** Workflow states in display order, for status columns and empty groups. */
  states?: { id: string; name: string; type: string; color?: string }[];
  users?: { id: string; name: string }[];
  projects?: { id: string; name: string }[];
  cycles?: { id: string; name?: string; number?: number }[];
  labels?: {
    id: string;
    name: string;
    color?: string;
    parentId?: string | null;
    isGroup?: boolean;
  }[];
  teams?: { id: string; name: string }[];
  viewerId?: string;
  /** Grouping by label group (Linear's issueGroupingLabelGroupId): whose labels form the groups. */
  labelGroupId?: string | null;
};

/** The label group a `labelGroup` grouping uses: the chosen one, else the first group. */
export function activeLabelGroup(ctx: GroupCtx): { id: string; name: string } | null {
  const groups = (ctx.labels || []).filter((l) => l.isGroup);
  return (
    groups.find((g) => g.id === ctx.labelGroupId) ||
    groups.sort((a, b) => a.name.localeCompare(b.name))[0] ||
    null
  );
}

export type GroupKey = { key: string; label: string; rank: number };

const STATUS_TYPE_ORDER = [
  "triage",
  "started",
  "unstarted",
  "backlog",
  "completed",
  "canceled",
];
const STATUS_TYPE_LABEL: Record<string, string> = {
  triage: "Triage",
  backlog: "Backlog",
  unstarted: "Unstarted",
  started: "Started",
  completed: "Completed",
  canceled: "Canceled",
};
export const PRIORITY_LABELS = [
  "No priority",
  "Urgent",
  "High",
  "Medium",
  "Low",
];
/** Urgent first, No priority last. */
const PRIORITY_RANK = [4, 0, 1, 2, 3];

const closedType = (type?: string) =>
  type === "completed" || type === "canceled";

function stateRank(
  state: { name: string; type: string } | null | undefined,
  ctx: GroupCtx,
): number {
  if (!state) return 1e6;
  const i = (ctx.states || []).findIndex(
    (s) => s.name === state.name && s.type === state.type,
  );
  const pos = i < 0 ? 999 : i;
  // Open states keep workflow order; completed and canceled follow every open state.
  return (closedType(state.type) ? 1e5 : 0) + pos;
}

/** The groups one task belongs to under `grouping`. A task with several labels is in each label's group. */
export function groupKeysOf(
  t: TaskLike,
  grouping: string,
  ctx: GroupCtx = {},
): GroupKey[] {
  const none = (label: string): GroupKey[] => [
    { key: "none", label, rank: 1e7 },
  ];
  switch (grouping) {
    case "status":
      return t.state
        ? [
            {
              key: t.state.name,
              label: t.state.name,
              rank: stateRank(t.state, ctx),
            },
          ]
        : none("No status");
    case "statusType":
      return t.state
        ? [
            {
              key: t.state.type,
              label: STATUS_TYPE_LABEL[t.state.type] || t.state.type,
              rank: STATUS_TYPE_ORDER.indexOf(t.state.type),
            },
          ]
        : none("No status");
    case "assignee":
    case "creator": {
      const u = t[grouping];
      return u
        ? [
            {
              key: u.id,
              label: u.name || "Unknown",
              rank: u.id === ctx.viewerId ? -1 : 0,
            },
          ]
        : none(grouping === "assignee" ? "No assignee" : "No creator");
    }
    case "project":
      return t.project
        ? [{ key: t.project.id, label: t.project.name || "Project", rank: 0 }]
        : none("No project");
    case "priority": {
      const p = t.priority ?? 0;
      return [
        {
          key: String(p),
          label: PRIORITY_LABELS[p] || "No priority",
          rank: PRIORITY_RANK[p] ?? 4,
        },
      ];
    }
    case "cycle":
      return t.cycle
        ? [
            {
              key: t.cycle.id,
              label:
                t.cycle.name ||
                (t.cycle.number != null ? `Cycle ${t.cycle.number}` : "Cycle"),
              rank: -(t.cycle.number || 0),
            },
          ]
        : none("No cycle");
    case "labels": {
      const labels = t.labels?.nodes || [];
      return labels.length
        ? labels.map((l) => ({ key: l.id, label: l.name, rank: 0 }))
        : none("No labels");
    }
    case "team":
      return t.team
        ? [
            {
              key: t.team.id,
              label: t.team.name || t.team.key || "Team",
              rank: 0,
            },
          ]
        : none("No team");
    case "labelGroup": {
      // One child of the group per task (an exclusive group), else "No <group>".
      const group = activeLabelGroup(ctx);
      const child = (t.labels?.nodes || []).find(
        (l) => group && l.parent?.id === group.id,
      );
      return child
        ? [{ key: child.id, label: child.name, rank: 0 }]
        : none(group ? `No ${group.name}` : "No label group");
    }
    default:
      return [{ key: "all", label: "", rank: 0 }];
  }
}

/** Every group a grouping can have, for "Show empty groups". */
export function allGroupKeys(grouping: string, ctx: GroupCtx): GroupKey[] {
  switch (grouping) {
    case "status":
      return (ctx.states || []).map((s) => ({
        key: s.name,
        label: s.name,
        rank: stateRank(s, ctx),
      }));
    case "statusType":
      return STATUS_TYPE_ORDER.filter((t) => t !== "triage").map((t, i) => ({
        key: t,
        label: STATUS_TYPE_LABEL[t],
        rank: i + 1,
      }));
    case "priority":
      return PRIORITY_LABELS.map((label, p) => ({
        key: String(p),
        label,
        rank: PRIORITY_RANK[p],
      }));
    case "assignee":
    case "creator":
      return (ctx.users || []).map((u) => ({
        key: u.id,
        label: u.name,
        rank: u.id === ctx.viewerId ? -1 : 0,
      }));
    case "project":
      return (ctx.projects || []).map((p) => ({
        key: p.id,
        label: p.name,
        rank: 0,
      }));
    case "cycle":
      return (ctx.cycles || []).map((c) => ({
        key: c.id,
        label: c.name || `Cycle ${c.number ?? ""}`.trim(),
        rank: -(c.number || 0),
      }));
    case "labels":
      return (ctx.labels || []).map((l) => ({
        key: l.id,
        label: l.name,
        rank: 0,
      }));
    case "team":
      return (ctx.teams || []).map((t) => ({
        key: t.id,
        label: t.name,
        rank: 0,
      }));
    case "labelGroup": {
      const group = activeLabelGroup(ctx);
      return (ctx.labels || [])
        .filter((l) => group && l.parentId === group.id)
        .map((l) => ({ key: l.id, label: l.name, rank: 0 }));
    }
    default:
      return [];
  }
}

export type Group<T> = {
  key: string;
  label: string;
  items: T[];
  total: number;
};

/** Bucket tasks into groups in display order. `totals` (the unfiltered set) feeds "6 / 81". */
export function groupTasks<T extends TaskLike>(
  items: readonly T[],
  grouping: string,
  ctx: GroupCtx = {},
  opts: { showEmpty?: boolean; totals?: readonly T[] } = {},
): Group<T>[] {
  const meta = new Map<string, GroupKey>();
  const buckets = new Map<string, T[]>();
  const totals = new Map<string, number>();
  for (const t of items)
    for (const g of groupKeysOf(t, grouping, ctx)) {
      meta.set(g.key, g);
      const list = buckets.get(g.key);
      if (list) list.push(t);
      else buckets.set(g.key, [t]);
    }
  for (const t of opts.totals || items)
    for (const g of groupKeysOf(t, grouping, ctx)) {
      totals.set(g.key, (totals.get(g.key) || 0) + 1);
      if (opts.showEmpty && !meta.has(g.key)) meta.set(g.key, g);
    }
  if (opts.showEmpty)
    for (const g of allGroupKeys(grouping, ctx))
      if (!meta.has(g.key)) meta.set(g.key, g);
  return [...meta.values()]
    .sort(
      (a, b) =>
        a.rank - b.rank ||
        (a.key === "none"
          ? 1
          : b.key === "none"
            ? -1
            : a.label.localeCompare(b.label)),
    )
    .map((g) => ({
      key: g.key,
      label: g.label,
      items: buckets.get(g.key) || [],
      total: totals.get(g.key) || 0,
    }))
    .filter((g) => opts.showEmpty || g.items.length > 0);
}

/* ------------------------------------------------------------- ordering */

const ms = (v?: string | null) => (v ? Date.parse(v) || 0 : 0);

/** Compare under one ordering; empty values ("No priority", no due date) sort last either way. */
export function sortTasks<T extends TaskLike>(
  items: readonly T[],
  display: DisplayDoc,
  ctx: GroupCtx = {},
): T[] {
  const ordering = display.ordering || "priority";
  const dir = display.reverse ? -1 : 1;
  type Key = { empty: boolean; v: number | string };
  const key = (t: T): Key => {
    switch (ordering) {
      case "priority":
        return { empty: !t.priority, v: t.priority || 0 };
      case "title":
        return { empty: false, v: t.title.toLowerCase() };
      case "dueDate":
        return { empty: !t.dueDate, v: ms(t.dueDate) };
      case "estimate":
        return { empty: t.estimate == null, v: -(t.estimate || 0) };
      case "status":
        return {
          empty: !t.state,
          v: t.state
            ? STATUS_TYPE_ORDER.indexOf(t.state.type) * 1000 +
              stateRank(t.state, ctx)
            : 0,
        };
      case "created":
        return { empty: false, v: -ms(t.createdAt) };
      case "manual":
        // The task's rank; unranked tasks (imports) after ranked ones, newest first.
        return { empty: !t.rank, v: t.rank || "" };
      default: // updated
        return { empty: false, v: -ms(t.updatedAt || t.createdAt) };
    }
  };
  const tie = (a: T, b: T) =>
    (ordering === "priority" ? compareRank(a.priorityRank, b.priorityRank) : 0) ||
    (ordering === "manual"
      ? ms(b.createdAt) - ms(a.createdAt)
      : ms(b.updatedAt || b.createdAt) - ms(a.updatedAt || a.createdAt)) ||
    a.id.localeCompare(b.id);
  const keyed = items.map((t) => ({ t, k: key(t) }));
  keyed.sort((a, b) => {
    if (a.k.empty !== b.k.empty) return a.k.empty ? 1 : -1;
    const c = a.k.v < b.k.v ? -1 : a.k.v > b.k.v ? 1 : 0;
    return c * dir || tie(a.t, b.t);
  });
  const sorted = keyed.map((x) => x.t);
  if (!display.orderCompletedByRecency) return sorted;
  // Open work in the chosen order, then closed work by when it closed, newest first.
  const closedAt = (t: T) => ms(t.stateChangedAt || t.updatedAt);
  return [
    ...sorted.filter((t) => !closedType(t.state?.type)),
    ...sorted
      .filter((t) => closedType(t.state?.type))
      .sort((a, b) => closedAt(b) - closedAt(a)),
  ];
}

/* ------------------------------------------------------------- windows */

const WINDOW_DAYS: Record<string, number> = {
  day: 1,
  week: 7,
  month: 30,
  quarter: 91,
  halfYear: 182,
};

/** Drop completed and canceled tasks closed before the window; open tasks always stay. */
export function applyCompletedWindow<T extends TaskLike>(
  items: readonly T[],
  window: string | undefined,
  now = Date.now(),
): T[] {
  if (!window || window === "all") return [...items];
  return items.filter((t) => {
    if (!closedType(t.state?.type)) return true;
    if (window === "none") return false;
    const at = ms(t.stateChangedAt || t.updatedAt);
    return at >= now - (WINDOW_DAYS[window] || 7) * 864e5;
  });
}

/** The rows a list renders before nesting: sub-issues dropped when the view hides them, and
 * with "Show all" every sub-issue of a shown parent added back even when the filter missed it. */
export function withSubIssues<T extends TaskLike>(
  visible: readonly T[],
  base: readonly T[],
  display: DisplayDoc,
): T[] {
  if (display.showSubIssues === false)
    return visible.filter((t) => !t.parent?.id);
  if ((display.nestedSubIssues || "all") !== "all") return [...visible];
  const shown = new Set(visible.map((t) => t.id));
  const children = new Map<string, T[]>();
  for (const t of base) {
    const p = t.parent?.id;
    if (!p) continue;
    const list = children.get(p);
    if (list) list.push(t);
    else children.set(p, [t]);
  }
  const out = [...visible];
  const queue = [...visible];
  while (queue.length) {
    const t = queue.shift() as T;
    for (const c of children.get(t.id) || [])
      if (!shown.has(c.id)) {
        shown.add(c.id);
        out.push(c);
        queue.push(c);
      }
  }
  return out;
}

/** `visibleProperties` with `property` switched on or off. The panel's properties keep its
 * order; names it does not list (set by the API or another client, such as "cycle") stay. */
export function toggleProperty(
  current: readonly string[],
  options: readonly Option[],
  property: string,
): string[] {
  const on = current.includes(property);
  const next = on
    ? current.filter((v) => v !== property)
    : [...current, property];
  const listed = options.map((o) => o.value);
  return [
    ...listed.filter((v) => next.includes(v)),
    ...next.filter((v) => !listed.includes(v)),
  ];
}

/* ------------------------------------------------------------- the Projects page */

/** The Projects page's own controls, in the words its menus show. */
export type ProjectPageOptions = {
  layout: "List" | "Board" | "Timeline";
  group: string;
  ordering: string;
  showClosed: boolean;
  summary: boolean;
};

const PROJECT_LAYOUT_WORDS: [string, ProjectPageOptions["layout"]][] = [
  ["list", "List"],
  ["board", "Board"],
  ["timeline", "Timeline"],
];
export const PROJECT_PAGE_GROUPS: [string, string][] = [
  ["none", "No grouping"],
  ["status", "Status"],
  ["team", "Team"],
  ["lead", "Lead"],
  ["health", "Health"],
];
export const PROJECT_PAGE_ORDERINGS: [string, string][] = [
  ["name", "Name"],
  ["status", "Status"],
  ["targetDate", "Target date"],
  ["startDate", "Start date"],
];

const word = (
  pairs: [string, string][],
  key: string | undefined,
  fallback: string,
) => pairs.find(([k]) => k === key)?.[1] || fallback;
const key = (pairs: [string, string][], label: string, fallback: string) =>
  pairs.find(([, l]) => l === label)?.[0] || fallback;

/** A stored project display document as the Projects page's controls read it. */
export function projectPageOptions(display: DisplayDoc): ProjectPageOptions {
  return {
    layout: word(
      PROJECT_LAYOUT_WORDS,
      display.layout,
      "List",
    ) as ProjectPageOptions["layout"],
    group: word(PROJECT_PAGE_GROUPS, display.grouping, "No grouping"),
    ordering: word(PROJECT_PAGE_ORDERINGS, display.ordering, "Name"),
    showClosed: (display.completedWindow || "all") !== "none",
    summary: (display.visibleProperties || []).includes("summary"),
  };
}

/** The display patch one control change writes, so the page's options persist through
 * `viewPreferences` (Linear stores a project list's display per view, not per tab). */
export function projectPagePatch(
  change: Partial<ProjectPageOptions>,
  display: DisplayDoc,
): DisplayDoc {
  const patch: DisplayDoc = {};
  if (change.layout)
    patch.layout = key(PROJECT_LAYOUT_WORDS, change.layout, "list") as Layout;
  if (change.group)
    patch.grouping = key(PROJECT_PAGE_GROUPS, change.group, "none");
  if (change.ordering)
    patch.ordering = key(PROJECT_PAGE_ORDERINGS, change.ordering, "name");
  if (change.showClosed !== undefined)
    patch.completedWindow = change.showClosed ? "all" : "none";
  if (change.summary !== undefined) {
    const current = display.visibleProperties || [];
    const without = current.filter((p) => p !== "summary");
    patch.visibleProperties = change.summary
      ? [...without, "summary"]
      : without;
  }
  return patch;
}
