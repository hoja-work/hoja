/* The initiatives list, after Linear's: Active / Planned / Completed / All plus saved
 * views; filters on status, health, owner, lead team and label; grouping and
 * ordering; sub-initiatives nested under their parent (T collapses or expands every
 * nest); drag a row to reorder it, or hold Alt (Option) while dropping it on another
 * row to nest it there. Dropping a sub-initiative on "Move to top level" un-nests it. */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, ListFilter, Plus, Settings2, X } from "lucide-react";
import { confirmDialog, promptDialog } from "../dialogs";
import { useMenuDismiss } from "../menu";
import { FollowButton } from "../inbox/follow";
import type { Viewer } from "../main";
import { useProjectEntityVersion } from "../projectStore";
import { Avatar, fmtDate, Ring } from "../graph/shared";
import { HEALTH, healthLabel } from "../graph/health";
import {
  deleteInitiativeView,
  fetchAllInitiatives,
  fetchInitiativeViews,
  progressFraction,
  saveInitiativeView,
  updateInitiative,
  type InitiativeRow,
  type InitiativeView,
} from "./api";
import {
  HealthBadge,
  InitiativeIcon,
  InitiativeStatusIcon,
  initiativeStatusLabel,
  INITIATIVE_STATUSES,
  LabelDot,
  useInitiativeLabels,
  useMembers,
  useTeams,
} from "./fields";

type Tab = "active" | "planned" | "completed" | "all" | `view:${string}`;
type Filters = {
  status: string[];
  health: string[];
  owner: string[];
  leadTeam: string[];
  label: string[];
};
type Grouping = "none" | "status" | "health" | "owner" | "leadTeam";
type Ordering =
  "manual" | "name" | "targetDate" | "created" | "health" | "priority";
type Display = {
  grouping: Grouping;
  ordering: Ordering;
  nested: boolean;
  summary: boolean;
};

const EMPTY: Filters = {
  status: [],
  health: [],
  owner: [],
  leadTeam: [],
  label: [],
};
const DEFAULT_DISPLAY: Display = {
  grouping: "none",
  ordering: "manual",
  nested: true,
  summary: true,
};
const HEALTH_RANK: Record<string, number> = {
  "off track": 0,
  "at risk": 1,
  "on track": 2,
};

const tabFromPath = (path: string): Tab =>
  path.endsWith("/active")
    ? "active"
    : path.endsWith("/planned")
      ? "planned"
      : path.endsWith("/completed")
        ? "completed"
        : "all";

/* Saved-view documents use the server's filter language. */
function toViewFilters(f: Filters): InitiativeView["filters"] {
  const out: InitiativeView["filters"] = {};
  for (const key of Object.keys(f) as (keyof Filters)[])
    if (f[key].length) out[key] = { isEitherOf: f[key] };
  return out;
}
function fromViewFilters(v: InitiativeView["filters"]): Filters {
  const out: Filters = { ...EMPTY };
  for (const key of Object.keys(EMPTY) as (keyof Filters)[]) {
    const p = v?.[key];
    if (p) out[key] = p.isEitherOf || (p.is ? [p.is] : []);
  }
  return out;
}

function sorter(ordering: Ordering) {
  return (a: InitiativeRow, b: InitiativeRow) => {
    switch (ordering) {
      case "name":
        return a.name.localeCompare(b.name);
      case "targetDate":
        return (a.targetDate || "9999").localeCompare(b.targetDate || "9999");
      case "created":
        return (b.createdAt || "").localeCompare(a.createdAt || "");
      case "health":
        return (
          (HEALTH_RANK[a.health || ""] ?? 3) -
          (HEALTH_RANK[b.health || ""] ?? 3)
        );
      case "priority":
        return (a.priority || 5) - (b.priority || 5);
      default:
        return (
          (a.sortOrder ?? Number.MAX_SAFE_INTEGER) -
            (b.sortOrder ?? Number.MAX_SAFE_INTEGER) ||
          (b.createdAt || "").localeCompare(a.createdAt || "")
        );
    }
  };
}

export function InitiativesList({
  path,
  viewer,
  onOpenInitiative,
  onNew,
}: {
  path: string;
  viewer: Viewer;
  onOpenInitiative: (id: string) => void;
  onNew: (defaults?: Record<string, unknown>) => void;
}) {
  const [tab, setTab] = useState<Tab>(() => tabFromPath(path));
  const [rows, setRows] = useState<InitiativeRow[] | null>(null);
  const [error, setError] = useState("");
  const [views, setViews] = useState<InitiativeView[]>([]);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [display, setDisplay] = useState<Display>(DEFAULT_DISPLAY);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropHint, setDropHint] = useState<{
    id: string;
    nest: boolean;
  } | null>(null);
  const version = useProjectEntityVersion(["initiative"]);
  const members = useMembers();
  const teams = useTeams();
  const { labels } = useInitiativeLabels();

  const reload = useCallback(
    () =>
      fetchAllInitiatives()
        .then((r) => {
          setRows(r);
          setError("");
        })
        .catch((e: Error) => setError(e.message)),
    [],
  );
  useEffect(() => {
    void reload();
  }, [reload, version]);
  useEffect(() => {
    fetchInitiativeViews()
      .then(setViews)
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    const onPop = () => setTab(tabFromPath(location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  /* The inbox used to open an initiative as ?initiative=<id>; send it to the page. */
  useEffect(() => {
    const legacy = new URLSearchParams(location.search).get("initiative");
    if (legacy) onOpenInitiative(legacy);
  }, [onOpenInitiative]);

  const canEdit = (i: InitiativeRow) =>
    Boolean(viewer.admin || (viewer.id && i.creator?.id === viewer.id));

  const view = tab.startsWith("view:")
    ? views.find((v) => `view:${v.id}` === tab)
    : undefined;
  const selectTab = (next: Tab) => {
    setTab(next);
    const v = next.startsWith("view:")
      ? views.find((x) => `view:${x.id}` === next)
      : undefined;
    setFilters(v ? fromViewFilters(v.filters) : EMPTY);
    setDisplay(
      v ? { ...DEFAULT_DISPLAY, ...(v.display as Partial<Display>) } : display,
    );
    if (!v)
      history.pushState(
        {},
        "",
        next === "all"
          ? "/workspace/initiatives"
          : `/workspace/initiatives/${next}`,
      );
  };

  const byId = useMemo(
    () => new Map((rows || []).map((r) => [r.id, r])),
    [rows],
  );
  const matches = useCallback(
    (i: InitiativeRow) => {
      if (!view && tab !== "all" && i.status !== tab) return false;
      if (filters.status.length && !filters.status.includes(i.status))
        return false;
      if (filters.health.length && !filters.health.includes(i.health || "none"))
        return false;
      if (
        filters.owner.length &&
        !filters.owner.includes(i.owner?.id || "none")
      )
        return false;
      if (
        filters.leadTeam.length &&
        !filters.leadTeam.includes(i.leadTeam?.id || "none")
      )
        return false;
      if (
        filters.label.length &&
        !(i.labels || []).some((l) => filters.label.includes(l.id))
      )
        return false;
      return true;
    },
    [filters, tab, view],
  );
  const visible = useMemo(() => (rows || []).filter(matches), [rows, matches]);

  const groupOf = (i: InitiativeRow): [string, string] => {
    switch (display.grouping) {
      case "status":
        return [i.status, initiativeStatusLabel(i.status)];
      case "health":
        return [i.health || "none", healthLabel(i.health)];
      case "owner":
        return [i.owner?.id || "none", i.owner?.name || "No owner"];
      case "leadTeam":
        return [i.leadTeam?.id || "none", i.leadTeam?.name || "No lead team"];
      default:
        return ["all", ""];
    }
  };
  const groups = useMemo(() => {
    const map = new Map<string, { label: string; rows: InitiativeRow[] }>();
    for (const i of visible) {
      const [key, label] = groupOf(i);
      if (!map.has(key)) map.set(key, { label, rows: [] });
      map.get(key)!.rows.push(i);
    }
    return [...map.entries()];
    // groupOf reads display.grouping only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, display.grouping]);

  /* Flatten one group into rows with their depth: children follow their parent when
   * nesting is on and the parent is in the same group. */
  const flatten = (list: InitiativeRow[]) => {
    const sort = sorter(display.ordering);
    const present = new Set(list.map((r) => r.id));
    const kids = new Map<string, InitiativeRow[]>();
    const roots: InitiativeRow[] = [];
    for (const r of list) {
      const p = r.parent?.id;
      if (display.nested && p && present.has(p)) {
        if (!kids.has(p)) kids.set(p, []);
        kids.get(p)!.push(r);
      } else roots.push(r);
    }
    const out: { row: InitiativeRow; depth: number; childCount: number }[] = [];
    const walk = (r: InitiativeRow, depth: number) => {
      const children = (kids.get(r.id) || []).sort(sort);
      out.push({ row: r, depth, childCount: children.length });
      if (!collapsed.has(r.id)) children.forEach((c) => walk(c, depth + 1));
    };
    roots.sort(sort).forEach((r) => walk(r, 0));
    return out;
  };

  /* T collapses every nest, or expands them all when any is collapsed. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        e.key.toLowerCase() !== "t" ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        t?.closest("input,textarea,select,[contenteditable=true]")
      )
        return;
      const parents = (rows || [])
        .filter((r) => (rows || []).some((c) => c.parent?.id === r.id))
        .map((r) => r.id);
      setCollapsed((cur) => (cur.size ? new Set() : new Set(parents)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows]);

  const move = async (
    dragged: string,
    target: string | null,
    nest: boolean,
  ) => {
    const d = byId.get(dragged);
    if (!d || dragged === target) return;
    if (!canEdit(d)) {
      setError("Only the initiative's creator or an admin can move it.");
      return;
    }
    let input: Record<string, unknown>;
    if (target === null) input = { parentId: null };
    else if (nest) input = { parentId: target };
    else {
      const t = byId.get(target);
      if (!t) return;
      const siblings = (rows || [])
        .filter(
          (r) =>
            (r.parent?.id || null) === (t.parent?.id || null) &&
            r.id !== dragged,
        )
        .sort(sorter("manual"));
      const at = siblings.findIndex((r) => r.id === target);
      const before = siblings[at - 1]?.sortOrder;
      const here = t.sortOrder ?? at * 1000;
      const sortOrder =
        before !== undefined && before !== null
          ? (before + here) / 2
          : here - 1000;
      input = { parentId: t.parent?.id || null, sortOrder };
      if (display.ordering !== "manual")
        setDisplay((x) => ({ ...x, ordering: "manual" }));
    }
    setRows((cur) =>
      (cur || []).map((r) =>
        r.id === dragged
          ? {
              ...r,
              parent:
                "parentId" in input
                  ? input.parentId
                    ? { id: input.parentId as string }
                    : null
                  : r.parent,
              sortOrder:
                "sortOrder" in input
                  ? (input.sortOrder as number)
                  : r.sortOrder,
            }
          : r,
      ),
    );
    try {
      await updateInitiative(d.id, input);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      void reload();
    }
  };

  const filterCount = Object.values(filters).reduce((n, v) => n + v.length, 0);
  const saveView = async () => {
    const name = await promptDialog("Name this view");
    if (!name?.trim()) return;
    try {
      const v = await saveInitiativeView(
        name.trim(),
        toViewFilters(filters),
        display,
      );
      setViews((cur) => [...cur, v]);
      setTab(`view:${v.id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const removeView = async (v: InitiativeView) => {
    if (!(await confirmDialog(`Delete the view ${v.name}?`, { danger: true })))
      return;
    try {
      await deleteInitiativeView(v.id);
      setViews((cur) => cur.filter((x) => x.id !== v.id));
      if (tab === `view:${v.id}`) selectTab("all");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "active", label: "Active" },
    { id: "planned", label: "Planned" },
    { id: "completed", label: "Completed" },
    { id: "all", label: "All initiatives" },
  ];
  const pill =
    "inline-flex h-7 flex-none items-center gap-1 rounded-full border border-line bg-panel px-3 text-xs font-medium text-muted no-underline transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none aria-[current=page]:bg-subtle aria-[current=page]:text-foreground focus-visible:outline-2 focus-visible:outline-accent";

  return (
    <section
      className="relative flex min-h-0 min-w-0 flex-1 flex-col"
      data-initiatives-page=""
    >
      <header className="flex h-11 flex-none items-center justify-between border-b border-line px-4">
        <h1 className="m-0 text-sm font-medium">Initiatives</h1>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent"
          onClick={() => onNew()}
          title="New initiative (N then I)"
        >
          <Plus size={14} aria-hidden /> New initiative
        </button>
      </header>
      <div
        className="flex flex-none items-center gap-2 px-2 py-2"
        data-initiatives-toolbar=""
      >
        <nav
          className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto scrollbar-none"
          aria-label="Initiative views"
        >
          {tabs.map((t) => (
            <a
              key={t.id}
              className={pill}
              href={
                t.id === "all"
                  ? "/workspace/initiatives"
                  : `/workspace/initiatives/${t.id}`
              }
              aria-current={tab === t.id ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                selectTab(t.id);
              }}
            >
              {t.label}
            </a>
          ))}
          {views.map((v) => (
            <span
              key={v.id}
              className="group inline-flex flex-none items-center"
            >
              <a
                className={pill}
                href={`/workspace/initiatives?view=${v.id}`}
                aria-current={tab === `view:${v.id}` ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  selectTab(`view:${v.id}`);
                }}
              >
                {v.name}
              </a>
              <button
                type="button"
                aria-label={`Delete view ${v.name}`}
                className="ml-0.5 grid size-5 place-items-center rounded-full text-muted opacity-0 hover:bg-subtle group-focus-within:opacity-100 group-hover:opacity-100 focus-visible:opacity-100"
                onClick={() => void removeView(v)}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </nav>
        {(filterCount > 0 ||
          display.grouping !== DEFAULT_DISPLAY.grouping ||
          display.ordering !== DEFAULT_DISPLAY.ordering) &&
          !view && (
            <button
              type="button"
              className="flex-none rounded-md px-2 py-1 text-xs text-muted hover:bg-subtle hover:text-foreground"
              onClick={() => void saveView()}
            >
              Save view
            </button>
          )}
        <FilterButton
          filters={filters}
          count={filterCount}
          onChange={setFilters}
          members={members}
          teams={teams}
          labels={labels}
        />
        <DisplayButton display={display} onChange={setDisplay} />
      </div>
      {filterCount > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-4 pb-2 text-xs">
          {(Object.keys(filters) as (keyof Filters)[]).flatMap((k) =>
            filters[k].map((v) => (
              <button
                key={`${k}:${v}`}
                type="button"
                className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 hover:bg-subtle"
                aria-label={`Remove filter ${k} ${v}`}
                onClick={() =>
                  setFilters((f) => ({
                    ...f,
                    [k]: f[k].filter((x) => x !== v),
                  }))
                }
              >
                <span className="text-muted">
                  {k === "leadTeam" ? "Lead team" : k}:
                </span>
                {describe(k, v, members, teams, labels)}
                <X size={11} aria-hidden />
              </button>
            )),
          )}
          <button
            type="button"
            className="rounded-md px-2 py-0.5 text-muted hover:bg-subtle"
            onClick={() => setFilters(EMPTY)}
          >
            Clear
          </button>
        </div>
      )}
      <div
        className="min-h-0 flex-1 overflow-y-auto pb-16"
        data-initiatives-content=""
      >
        {error && (
          <p
            className="mx-4 my-2 rounded-md border border-line p-2 text-sm text-danger"
            role="alert"
          >
            {error}{" "}
            <button
              type="button"
              className="underline"
              onClick={() => void reload()}
            >
              Retry
            </button>
          </p>
        )}
        {rows === null && !error && (
          <p className="p-8 text-center text-sm text-muted" role="status">
            Loading initiatives…
          </p>
        )}
        {rows !== null && !visible.length && (
          <div
            className="mx-auto grid max-w-sm gap-3 p-12 text-sm text-muted"
            data-initiatives-empty=""
          >
            <h2 className="m-0 text-base font-semibold text-foreground">
              {filterCount ? "No initiatives match" : "Initiatives"}
            </h2>
            <p className="m-0">
              {filterCount
                ? "Change or clear the filters to see more."
                : "Initiatives are larger, strategic product efforts that set the direction of your company. They are comprised of projects that align with the goals of the initiative and allow you to monitor progress at scale."}
            </p>
            {!filterCount && (
              <button
                type="button"
                className="justify-self-start rounded-full bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:brightness-110"
                onClick={() => onNew()}
              >
                Create new initiative
              </button>
            )}
          </div>
        )}
        {visible.length > 0 && (
          <div role="table" aria-label="Initiatives" className="text-sm">
            <div
              role="row"
              className="hidden grid-cols-12 items-center gap-3 border-b border-line px-4 py-2 text-xs font-medium text-muted md:grid"
              data-initiatives-list-head=""
            >
              <span role="columnheader" className="col-span-4">
                Name
              </span>
              <span role="columnheader" className="col-span-2">
                Health
              </span>
              <span role="columnheader" className="col-span-2">
                Owner
              </span>
              <span role="columnheader" className="col-span-1">
                Target
              </span>
              <span role="columnheader" className="col-span-1">
                Progress
              </span>
              <span role="columnheader" className="col-span-2">
                Status
              </span>
            </div>
            {groups.map(([key, g]) => (
              <div role="rowgroup" key={key} aria-label={g.label || undefined}>
                {display.grouping !== "none" && (
                  <div
                    role="row"
                    className="flex items-center gap-2 border-b border-line bg-subtle px-4 py-1.5 text-xs font-medium"
                  >
                    <span role="cell">{g.label}</span>
                    <span className="text-muted">{g.rows.length}</span>
                  </div>
                )}
                {flatten(g.rows).map(({ row: i, depth, childCount }) => (
                  <InitiativeListRow
                    key={i.id}
                    row={i}
                    depth={depth}
                    childCount={childCount}
                    collapsed={collapsed.has(i.id)}
                    showSummary={display.summary}
                    dragging={dragId === i.id}
                    dropHint={dropHint?.id === i.id ? dropHint : null}
                    onToggle={() =>
                      setCollapsed((cur) => {
                        const next = new Set(cur);
                        if (next.has(i.id)) next.delete(i.id);
                        else next.add(i.id);
                        return next;
                      })
                    }
                    onOpen={() => onOpenInitiative(i.identifier || i.id)}
                    onDragStart={() => setDragId(i.id)}
                    onDragEnd={() => {
                      setDragId(null);
                      setDropHint(null);
                    }}
                    onDragOver={(nest) =>
                      dragId &&
                      dragId !== i.id &&
                      setDropHint({ id: i.id, nest })
                    }
                    onDrop={(nest) => {
                      if (dragId) void move(dragId, i.id, nest);
                      setDragId(null);
                      setDropHint(null);
                    }}
                  />
                ))}
              </div>
            ))}
            {dragId && byId.get(dragId)?.parent && (
              /* A pointer-only drop target shown while dragging; the keyboard path to the
               * same change is the Parent property on the initiative page. */
              // eslint-disable-next-line jsx-a11y/no-static-element-interactions
              <div
                className="mx-4 my-2 rounded-md border border-dashed border-line p-3 text-center text-xs text-muted"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  void move(dragId, null, false);
                  setDragId(null);
                }}
              >
                Drop here to move to the top level
              </div>
            )}
            <p className="hidden px-4 pt-3 text-xs text-muted md:block">
              Drag a row to reorder it. Hold Alt (Option) while dropping it on
              another initiative to nest it. Press T to collapse or expand
              sub-initiatives.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function describe(
  key: keyof Filters,
  value: string,
  members: { id: string; name: string }[],
  teams: { id: string; name: string }[],
  labels: { id: string; name: string }[],
) {
  if (value === "none") return "None";
  if (key === "status") return initiativeStatusLabel(value);
  if (key === "health") return healthLabel(value);
  if (key === "owner")
    return members.find((m) => m.id === value)?.name || value;
  if (key === "leadTeam")
    return teams.find((t) => t.id === value)?.name || value;
  return labels.find((l) => l.id === value)?.name || value;
}

function InitiativeListRow({
  row: i,
  depth,
  childCount,
  collapsed,
  showSummary,
  dragging,
  dropHint,
  onToggle,
  onOpen,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: {
  row: InitiativeRow;
  depth: number;
  childCount: number;
  collapsed: boolean;
  showSummary: boolean;
  dragging: boolean;
  dropHint: { nest: boolean } | null;
  onToggle: () => void;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (nest: boolean) => void;
  onDrop: (nest: boolean) => void;
}) {
  const projects = i.allProjects || i.projects?.nodes || [];
  const progress = progressFraction(projects);
  return (
    <div
      role="row"
      tabIndex={-1}
      draggable
      aria-level={depth + 1}
      data-initiative-row=""
      data-dragging={dragging || undefined}
      data-drop={dropHint ? (dropHint.nest ? "nest" : "before") : undefined}
      className="group relative flex items-center gap-3 border-b border-line px-4 py-3 transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none data-dragging:opacity-50 data-[drop=before]:border-t-2 data-[drop=before]:border-t-accent data-[drop=nest]:bg-accent-soft md:grid md:grid-cols-12"
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", i.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={(e) => {
        e.preventDefault();
        onDragOver(e.altKey);
      }}
      onDrop={(e) => {
        e.preventDefault();
        onDrop(e.altKey);
      }}
    >
      <div
        role="cell"
        className="flex min-w-0 flex-1 items-center gap-2 md:col-span-4"
        style={{ paddingLeft: depth * 20 }}
      >
        {childCount > 0 ? (
          <button
            type="button"
            aria-expanded={!collapsed}
            aria-label={`${collapsed ? "Expand" : "Collapse"} ${i.name}`}
            className="relative z-10 grid size-5 flex-none place-items-center rounded text-muted hover:bg-subtle"
            onClick={onToggle}
          >
            <ChevronRight
              size={13}
              className="transition-transform aria-expanded:rotate-90 motion-reduce:transition-none"
              style={{ transform: collapsed ? undefined : "rotate(90deg)" }}
            />
          </button>
        ) : (
          <span className="size-5 flex-none" aria-hidden />
        )}
        <InitiativeIcon icon={i.icon} color={i.color} />
        <div className="min-w-0">
          <a
            className="block truncate font-medium text-foreground no-underline after:absolute after:inset-0 focus-visible:outline-2 focus-visible:outline-accent"
            href={`/workspace/initiative/${encodeURIComponent(i.identifier || i.id)}`}
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey) return;
              e.preventDefault();
              onOpen();
            }}
          >
            {i.name}
            {childCount > 0 && collapsed && (
              <span className="ml-1.5 text-xs text-muted">+{childCount}</span>
            )}
          </a>
          {showSummary && i.summary && (
            <p className="m-0 truncate text-xs text-muted">{i.summary}</p>
          )}
        </div>
        {(i.labels || []).slice(0, 2).map((l) => (
          <span
            key={l.id}
            className="hidden flex-none items-center gap-1 rounded-full border border-line px-1.5 text-xs text-muted lg:inline-flex"
          >
            <LabelDot color={l.color} />
            {l.name}
          </span>
        ))}
        <span className="relative z-10 ml-auto hidden md:inline-flex">
          <FollowButton
            type="initiative"
            id={i.id}
            known={i.viewerSubscribed}
            compact
          />
        </span>
      </div>
      <span role="cell" className="flex-none md:col-span-2">
        <HealthBadge health={i.health} />
      </span>
      <span
        role="cell"
        className="hidden min-w-0 items-center gap-1.5 md:col-span-2 md:flex"
      >
        {i.owner?.name ? (
          <>
            <Avatar name={i.owner.name} seed={i.owner.avatarSeed} size={16} />
            <span className="truncate">{i.owner.name}</span>
          </>
        ) : (
          <span className="text-muted">No owner</span>
        )}
      </span>
      <time role="cell" className="hidden text-muted md:col-span-1 md:block">
        {i.targetDate ? fmtDate(i.targetDate) : "—"}
      </time>
      <span
        role="cell"
        className="hidden items-center gap-1.5 md:col-span-1 md:flex"
        title={`${projects.length} ${projects.length === 1 ? "project" : "projects"}`}
      >
        <Ring frac={progress} size={14} />
        {Math.round(progress * 100)}%
      </span>
      <span
        role="cell"
        className="relative flex min-w-0 flex-none items-center gap-1.5 md:col-span-2"
        title={initiativeStatusLabel(i.status)}
      >
        <InitiativeStatusIcon status={i.status} />
        <span className="hidden truncate md:inline">
          {initiativeStatusLabel(i.status)}
        </span>
        <span className="ml-auto hidden text-xs text-muted xl:inline">
          {i.identifier}
        </span>
      </span>
    </div>
  );
}

/* ---------------- filter & display popovers ---------------- */

function Popover({
  label,
  icon,
  count,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  count?: number;
  children: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef<HTMLDivElement | null>(null);
  const panelId = React.useId();
  useMenuDismiss({ open, within: [ref], onDismiss: () => setOpen(false) });
  return (
    <div className="relative flex-none" ref={ref}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={panelId}
        className="inline-flex size-7 items-center justify-center gap-1 rounded-full border border-line bg-panel text-muted hover:bg-subtle hover:text-foreground aria-expanded:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
        onClick={() => setOpen((v) => !v)}
      >
        {icon}
        {count ? (
          <span className="absolute -top-1 -right-1 grid size-4 place-items-center rounded-full bg-accent text-xs text-white">
            {count}
          </span>
        ) : null}
      </button>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label={label}
          className="absolute top-full right-0 z-50 mt-1 grid max-h-96 w-72 max-w-xs gap-3 overflow-y-auto rounded-lg border border-line bg-panel p-3 text-sm shadow-lg"
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

function CheckGroup({
  title,
  options,
  value,
  onChange,
}: {
  title: string;
  options: { id: string; label: React.ReactNode }[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  if (!options.length) return null;
  return (
    <fieldset className="m-0 grid gap-1 border-0 p-0">
      <legend className="mb-1 text-xs font-medium text-muted">{title}</legend>
      {options.map((o) => (
        <label key={o.id} className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value.includes(o.id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...value, o.id]
                  : value.filter((x) => x !== o.id),
              )
            }
          />
          <span className="inline-flex min-w-0 items-center gap-1.5 truncate">
            {o.label}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function FilterButton({
  filters,
  count,
  onChange,
  members,
  teams,
  labels,
}: {
  filters: Filters;
  count: number;
  onChange: (f: Filters) => void;
  members: { id: string; name: string }[];
  teams: { id: string; name: string }[];
  labels: { id: string; name: string; color?: string | null }[];
}) {
  const set = (k: keyof Filters) => (v: string[]) =>
    onChange({ ...filters, [k]: v });
  return (
    <Popover
      label="Filter initiatives"
      icon={<ListFilter size={15} />}
      count={count}
    >
      {() => (
        <>
          <CheckGroup
            title="Status"
            value={filters.status}
            onChange={set("status")}
            options={INITIATIVE_STATUSES.map((s) => ({
              id: s.id,
              label: (
                <>
                  <InitiativeStatusIcon status={s.id} />
                  {s.label}
                </>
              ),
            }))}
          />
          <CheckGroup
            title="Health"
            value={filters.health}
            onChange={set("health")}
            options={[...HEALTH, "none"].map((h) => ({
              id: h,
              label: <HealthBadge health={h === "none" ? null : h} />,
            }))}
          />
          <CheckGroup
            title="Owner"
            value={filters.owner}
            onChange={set("owner")}
            options={[{ id: "none", name: "No owner" }, ...members].map(
              (m) => ({
                id: m.id,
                label: m.name,
              }),
            )}
          />
          <CheckGroup
            title="Lead team"
            value={filters.leadTeam}
            onChange={set("leadTeam")}
            options={[{ id: "none", name: "No lead team" }, ...teams].map(
              (t) => ({
                id: t.id,
                label: t.name,
              }),
            )}
          />
          <CheckGroup
            title="Labels"
            value={filters.label}
            onChange={set("label")}
            options={labels.map((l) => ({
              id: l.id,
              label: (
                <>
                  <LabelDot color={l.color} />
                  {l.name}
                </>
              ),
            }))}
          />
          {count > 0 && (
            <button
              type="button"
              className="justify-self-start rounded-md px-2 py-1 text-xs text-muted hover:bg-subtle"
              onClick={() => onChange(EMPTY)}
            >
              Clear filters
            </button>
          )}
        </>
      )}
    </Popover>
  );
}

function DisplayButton({
  display,
  onChange,
}: {
  display: Display;
  onChange: (d: Display) => void;
}) {
  const select =
    "rounded-md border border-line bg-bg px-2 py-1 text-sm text-foreground";
  return (
    <Popover label="Display options" icon={<Settings2 size={15} />}>
      {() => (
        <>
          <label className="flex items-center justify-between gap-3">
            Grouping
            <select
              className={select}
              value={display.grouping}
              onChange={(e) =>
                onChange({ ...display, grouping: e.target.value as Grouping })
              }
            >
              <option value="none">No grouping</option>
              <option value="status">Status</option>
              <option value="health">Health</option>
              <option value="owner">Owner</option>
              <option value="leadTeam">Lead team</option>
            </select>
          </label>
          <label className="flex items-center justify-between gap-3">
            Ordering
            <select
              className={select}
              value={display.ordering}
              onChange={(e) =>
                onChange({ ...display, ordering: e.target.value as Ordering })
              }
            >
              <option value="manual">Manual</option>
              <option value="name">Name</option>
              <option value="targetDate">Target date</option>
              <option value="created">Created</option>
              <option value="health">Health</option>
              <option value="priority">Priority</option>
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={display.nested}
              onChange={(e) =>
                onChange({ ...display, nested: e.target.checked })
              }
            />
            Nest sub-initiatives
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={display.summary}
              onChange={(e) =>
                onChange({ ...display, summary: e.target.checked })
              }
            />
            Show summaries
          </label>
        </>
      )}
    </Popover>
  );
}
