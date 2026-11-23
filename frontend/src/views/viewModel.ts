/* What the views pages decide without rendering: where a view lives (its URL and the section of
 * the views list it is listed under), how the list sorts, the look a view can take (icon set
 * and swatches), and the unsaved edits a person keeps on a saved view across reloads.
 * Pure (type imports are erased) so the node test script runs it.
 */
import type { ServerView, ViewDocument } from "../views";

/* ------------------------------------------------------------------ look */

/** Linear's nine swatches, in its order; the first is the neutral default. */
export const VIEW_COLORS = [
  "#bec2c8",
  "#95a2b3",
  "#5e6ad2",
  "#26b5ce",
  "#4cb782",
  "#f2c94c",
  "#f2994a",
  "#d89f94",
  "#eb5757",
] as const;

/** The icon names a view can carry, grouped like the picker shows them. `ViewIcon` renders
 * each name; any other string is treated as an emoji. */
export const VIEW_ICON_GROUPS: [string, string[]][] = [
  [
    "Work",
    [
      "Layers",
      "ListTodo",
      "CircleDot",
      "CircleCheck",
      "Inbox",
      "Box",
      "Tag",
      "Funnel",
      "Flag",
      "Bookmark",
      "Star",
      "Target",
    ],
  ],
  [
    "Signals",
    [
      "Siren",
      "SignalHigh",
      "Flame",
      "Zap",
      "Bug",
      "ShieldAlert",
      "Clock",
      "Calendar",
      "RefreshCw",
      "Rocket",
      "Sparkles",
      "Heart",
    ],
  ],
  [
    "People",
    [
      "UserRound",
      "Users",
      "Eye",
      "MessageCircle",
      "Megaphone",
      "Briefcase",
      "Code",
      "Palette",
      "Wrench",
      "Database",
      "Globe",
      "Lock",
    ],
  ],
];

export const VIEW_EMOJI: string[] = [
  "😀", "😎", "🤖", "👀", "🔥", "⚡", "🚀", "🎯", "🐛", "🛠️", "📌", "📦",
  "🧪", "💡", "📈", "🗓️", "✅", "❗", "⭐", "❤️", "🌱", "🌍", "🔒", "💬",
];

/** True for an icon value that is an emoji rather than an icon name. */
export function isEmojiIcon(icon: string | null | undefined): boolean {
  return Boolean(icon) && !/^[A-Za-z][A-Za-z0-9]*$/.test(icon as string);
}

/* ------------------------------------------------------------------ paths */

/** The page of a saved view: `/workspace/view/<slug>` (its id when it has no slug). */
export function viewPath(view: Pick<ServerView, "id"> & { slugId?: string | null }): string {
  return `/workspace/view/${encodeURIComponent(view.slugId || view.id)}`;
}

/** The id or slug in a saved-view path, or null for any other path. */
export function viewIdentOf(path: string): string | null {
  const m = /^\/workspace\/view\/([^/?#]+)/.exec(path);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Older view links keep working: `/workspace/views/issues/<id>`, the team form of it, and the
 * stable link a view's `url` carries (`/<workspace>/view/<slug>`) all open the view page. The
 * list and editor routes (`…/views/issues`, `…/views/issues/new`) are left alone. */
export function redirectLegacyViewPath(path: string): string {
  const [pathname, rest = ""] = splitPath(path);
  const legacy =
    /^\/workspace\/(?:team\/[^/]+\/)?views\/(?:issues|projects)\/([^/]+)\/?$/.exec(
      pathname,
    );
  if (legacy && legacy[1] !== "new") return `/workspace/view/${legacy[1]}${rest}`;
  const stable = /^\/([^/]+)\/view\/([^/]+)\/?$/.exec(pathname);
  if (stable && stable[1] !== "workspace" && stable[1] !== "project")
    return `/workspace/view/${stable[2]}${rest}`;
  return path;
}

function splitPath(path: string): [string, string] {
  const at = path.search(/[?#]/);
  return at < 0 ? [path, ""] : [path.slice(0, at), path.slice(at)];
}

/* ------------------------------------------------------------ list sections */

export type ViewSection = {
  key: string;
  label: string;
  hint?: string;
  views: ServerView[];
};

/** The views list, Linear's way: "Personal views · Only visible to you" first, then the
 * workspace's shared views, then one section per team and per project. */
export function viewSections(
  views: readonly ServerView[],
  names: {
    team: (id: string) => string | undefined;
    project?: (id: string) => string | undefined;
  },
): ViewSection[] {
  const personal: ServerView[] = [];
  const workspace: ServerView[] = [];
  const byTeam = new Map<string, ServerView[]>();
  const byProject = new Map<string, ServerView[]>();
  for (const v of views) {
    if (v.visibility === "private") personal.push(v);
    else if (v.scope === "team" && v.team?.id)
      byTeam.set(v.team.id, [...(byTeam.get(v.team.id) || []), v]);
    else if (v.scope === "project" && v.project?.id)
      byProject.set(v.project.id, [...(byProject.get(v.project.id) || []), v]);
    else workspace.push(v);
  }
  const sections: ViewSection[] = [];
  if (personal.length)
    sections.push({
      key: "personal",
      label: "Personal views",
      hint: "Only visible to you",
      views: personal,
    });
  if (workspace.length)
    sections.push({ key: "workspace", label: "Workspace", views: workspace });
  const teamSections = [...byTeam].map(([id, rows]) => ({
    key: `team:${id}`,
    label: names.team(id) || "Team",
    views: rows,
  }));
  const projectSections = [...byProject].map(([id, rows]) => ({
    key: `project:${id}`,
    label: names.project?.(id) || "Project",
    views: rows,
  }));
  const byLabel = (a: ViewSection, b: ViewSection) =>
    a.label.localeCompare(b.label);
  return [
    ...sections,
    ...teamSections.sort(byLabel),
    ...projectSections.sort(byLabel),
  ];
}

export type ViewOrdering = "name" | "created" | "updated" | "owner";

export function sortViews(
  views: readonly ServerView[],
  by: ViewOrdering,
  descending = false,
): ServerView[] {
  const key = (v: ServerView): string =>
    by === "created"
      ? v.createdAt || ""
      : by === "updated"
        ? v.updatedAt || ""
        : by === "owner"
          ? (v.owner?.name || "").toLowerCase()
          : v.name.toLowerCase();
  const sorted = [...views].sort(
    (a, b) => key(a).localeCompare(key(b)) || a.id.localeCompare(b.id),
  );
  return descending ? sorted.reverse() : sorted;
}

/** Pinned tabs of a team (or project) page, in their stored order. */
export function pinnedTabs(views: readonly ServerView[]): ServerView[] {
  return views
    .filter((v) => v.pinned)
    .sort(
      (a, b) =>
        (a.sortOrder ?? Number.MAX_SAFE_INTEGER) -
          (b.sortOrder ?? Number.MAX_SAFE_INTEGER) ||
        (a.createdAt || "").localeCompare(b.createdAt || ""),
    );
}

/** The `sortOrder` writes that move pinned tab `id` to `index` of `tabs` (the current order,
 * as `pinnedTabs` gives it). When every tab has an order only the moved one changes, halfway
 * between its new neighbours (one past either end); otherwise every tab is numbered 1..n so a
 * tab without an order cannot fall behind. Empty when nothing moves. */
export function reorderPinned(
  tabs: readonly ServerView[],
  id: string,
  index: number,
): { id: string; sortOrder: number }[] {
  const from = tabs.findIndex((v) => v.id === id);
  if (from < 0) return [];
  const to = Math.max(0, Math.min(index, tabs.length - 1));
  if (to === from) return [];
  const next = tabs.filter((v) => v.id !== id);
  next.splice(to, 0, tabs[from]);
  const ordered = (v: ServerView) =>
    typeof v.sortOrder === "number" && Number.isFinite(v.sortOrder);
  if (tabs.every(ordered)) {
    const before = next[to - 1]?.sortOrder as number | undefined;
    const after = next[to + 1]?.sortOrder as number | undefined;
    const sortOrder =
      before === undefined
        ? (after as number) - 1
        : after === undefined
          ? before + 1
          : (before + after) / 2;
    return [{ id, sortOrder }];
  }
  return next
    .map((v, n) => ({ id: v.id, sortOrder: n + 1, was: v.sortOrder }))
    .filter((w) => w.was !== w.sortOrder)
    .map(({ id: vid, sortOrder }) => ({ id: vid, sortOrder }));
}

/* --------------------------------------------------------- where a view goes */

export type SaveTarget =
  | { kind: "personal" }
  | { kind: "workspace" }
  /* `personal`: a team view only its owner sees (Linear's "Personal" when saving from a team);
     it stays scoped to the team, so it lists the team's tasks and lives in its views. */
  | { kind: "team"; teamId: string; personal?: true }
  | { kind: "project"; projectId: string; shared: boolean };

/** The create/update fields that put a view in `target` ("Save to" and "Move to"). */
export function targetInput(target: SaveTarget): {
  scope: "workspace" | "team" | "project";
  visibility: "private" | "workspace" | "team";
  teamId?: string;
  projectId?: string;
} {
  switch (target.kind) {
    case "personal":
      return { scope: "workspace", visibility: "private" };
    case "workspace":
      return { scope: "workspace", visibility: "workspace" };
    case "team":
      return {
        scope: "team",
        visibility: target.personal ? "private" : "team",
        teamId: target.teamId,
      };
    case "project":
      return {
        scope: "project",
        visibility: target.shared ? "workspace" : "private",
        projectId: target.projectId,
      };
  }
}

/** Where a saved view is now, as a `SaveTarget`. */
export function targetOf(view: ServerView): SaveTarget {
  if (view.scope === "project" && view.project?.id)
    return {
      kind: "project",
      projectId: view.project.id,
      shared: view.visibility !== "private",
    };
  if (view.scope === "team" && view.team?.id)
    return view.visibility === "private"
      ? { kind: "team", teamId: view.team.id, personal: true }
      : { kind: "team", teamId: view.team.id };
  return view.visibility === "private"
    ? { kind: "personal" }
    : { kind: "workspace" };
}

/** The words for a view's visibility in its details panel. */
export function visibilityLabel(
  view: ServerView,
  teamName?: string,
): string {
  if (view.visibility === "private")
    return view.scope === "team" && teamName ? `Personal · ${teamName}` : "Personal";
  if (view.scope === "team") return teamName ? `Team · ${teamName}` : "Team";
  if (view.scope === "project") return "Workspace · project";
  return "Workspace";
}

/* ------------------------------------------------------------ unsaved edits */

/** A person's unsaved filter/display edits on a saved view. Kept per browser and per view, so a
 * reload keeps the bar with "Clear" and "Save" (Linear keeps unsaved edits across reloads). */
export type ViewEdits = { filters?: ViewDocument; display?: ViewDocument };

const EDITS_PREFIX = "hoja_view_edits:";

/* Keyed by person and view: two people sharing a browser never see each other's drafts. */
function editsKey(viewId: string, userId?: string | null): string {
  return `${EDITS_PREFIX}${userId || "anon"}:${viewId}`;
}

export function loadViewEdits(
  viewId: string,
  userId?: string | null,
): ViewEdits | null {
  try {
    const raw = localStorage.getItem(editsKey(viewId, userId));
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as ViewEdits) : null;
  } catch {
    return null;
  }
}

export function saveViewEdits(
  viewId: string,
  edits: ViewEdits | null,
  userId?: string | null,
): void {
  try {
    const key = editsKey(viewId, userId);
    if (!edits || (!edits.filters && !edits.display)) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(edits));
  } catch {
    /* storage blocked: the edits stay for this visit only */
  }
}

/** Order-insensitive equality of two JSON documents (key order never makes a view dirty). */
export function sameDocument(a: unknown, b: unknown): boolean {
  return stable(a ?? {}) === stable(b ?? {});
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .filter((k) => (value as Record<string, unknown>)[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${stable((value as Record<string, unknown>)[k])}`)
      .join(",")}}`;
  return JSON.stringify(value);
}

/** The keys of `display` that differ from `base`; the dirty part of a display edit. */
export function displayChanges(
  display: ViewDocument,
  base: ViewDocument,
): ViewDocument {
  const out: ViewDocument = {};
  for (const [k, v] of Object.entries(display))
    if (k !== "collapsedGroups" && !sameDocument(v, base[k])) out[k] = v;
  return out;
}

/** Whether `viewer` may edit `view` (rename, filters, move, delete): its owner or an admin. */
export function canEditView(
  view: Pick<ServerView, "owner">,
  viewer: { id?: string; admin?: boolean } | undefined,
): boolean {
  if (!viewer?.id) return false;
  return Boolean(viewer.admin) || view.owner?.id === viewer.id;
}
