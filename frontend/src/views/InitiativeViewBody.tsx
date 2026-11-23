/**
 * A saved initiative view's page: the shared filter chips and Display panel over the server's
 * initiative view engine (View.initiatives / viewPreview), as a task view has. Linear's schema
 * gives initiative views their own groupings and orderings (initiativeGrouping,
 * initiativesViewOrdering — SPEC.md §6); views.py INITIATIVE_GROUPINGS/ORDERINGS mirror them.
 */
import React, { useEffect, useMemo, useState } from "react";
import { ListFilter } from "lucide-react";
import { gql } from "../main";
import {
  HealthBadge,
  InitiativeIcon,
  InitiativeStatusIcon,
  INITIATIVE_STATUSES,
  useInitiativeLabels,
  useMembers,
  useTeams,
} from "../initiatives/fields";
import { LIST_FIELDS, type InitiativeRow } from "../initiatives/api";
import { updateView, type ServerView } from "../views";
import { AddFilterButton, FilterChips } from "./FilterBar";
import { DisplayButton, DisplayPanel } from "./DisplayPanel";
import {
  chipsToFilterTree,
  emptyGroup,
  filterTreeToChips,
  INITIATIVE_FIELDS,
  type FilterGroup,
} from "./filterModel";
import type { FilterCatalog } from "./filterLabels";
import { mergeDisplay, type DisplayDoc } from "./displayModel";

const HEALTH = [
  { value: "on track", label: "On track" },
  { value: "at risk", label: "At risk" },
  { value: "off track", label: "Off track" },
  { value: "none", label: "No update" },
];
const STATUS_LABEL = Object.fromEntries(
  INITIATIVE_STATUSES.map((s) => [s.id, s.label]),
);

const same = (a: unknown, b: unknown) =>
  JSON.stringify(a ?? {}) === JSON.stringify(b ?? {});

export function InitiativeViewBody({
  view,
  onView,
  onOpenInitiative,
  header,
}: {
  view: ServerView;
  onView: (view: ServerView) => void;
  onOpenInitiative: (id: string) => void;
  header?: React.ReactNode;
}) {
  const members = useMembers();
  const teams = useTeams();
  const { labels } = useInitiativeLabels();
  const [root, setRoot] = useState<FilterGroup>(() =>
    filterTreeToChips(view.filters, INITIATIVE_FIELDS),
  );
  const [display, setDisplay] = useState<DisplayDoc>(() => ({
    grouping: "none",
    ordering: "manual",
    ...(view.display as DisplayDoc),
  }));
  const [advanced, setAdvanced] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [rows, setRows] = useState<InitiativeRow[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const tree = useMemo(() => chipsToFilterTree(root, INITIATIVE_FIELDS), [root]);
  const dirty = !same(tree, view.filters) || !same(display, { grouping: "none", ordering: "manual", ...(view.display as DisplayDoc) });

  useEffect(() => {
    let live = true;
    // The draft runs whole on the server (viewPreview), so what is shown is what Save stores.
    gql(
      `query($f:JSON,$d:JSON,$s:String,$t:String){viewPreview(kind:"initiative",filters:$f,display:$d,scope:$s,teamId:$t){initiatives(first:250){nodes{${LIST_FIELDS}}}}}`,
      {
        f: tree,
        d: { grouping: display.grouping, ordering: display.ordering, reverse: display.reverse || false },
        s: view.scope === "team" ? "team" : null,
        t: view.scope === "team" ? view.team?.id || null : null,
      },
    )
      .then((d) => {
        if (!live) return;
        setRows(d.viewPreview.initiatives.nodes);
        setError("");
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [tree, display.grouping, display.ordering, display.reverse, view.scope, view.team?.id]);

  const catalog = useMemo<FilterCatalog>(
    () => ({
      fields: INITIATIVE_FIELDS,
      menu: INITIATIVE_FIELDS.map((f) => ({
        key: f.key,
        label: f.label,
        field: f.key,
        icon: <ListFilter size={14} />,
      })),
      options: (field) => {
        switch (field) {
          case "status":
            return INITIATIVE_STATUSES.map((s) => ({ value: s.id, label: s.label }));
          case "health":
            return HEALTH;
          case "owner":
            return [{ value: "none", label: "No owner" }, ...members.map((m) => ({ value: m.id, label: m.name }))];
          case "leadTeam":
            return [{ value: "none", label: "No lead team" }, ...teams.map((t) => ({ value: t.id, label: t.name }))];
          case "label":
            return labels.filter((l) => !l.isGroup).map((l) => ({ value: l.id, label: l.name }));
          default:
            return [];
        }
      },
      count: (field, value) =>
        rows?.filter((r) => {
          if (field === "status") return r.status === value;
          if (field === "health") return (r.health || "none") === value;
          if (field === "owner") return (r.owner?.id || "none") === value;
          if (field === "leadTeam") return (r.leadTeam?.id || "none") === value;
          if (field === "label") return (r.labels || []).some((l) => l.id === value);
          return false;
        }).length,
    }),
    [members, teams, labels, rows],
  );

  const groups = useMemo(() => {
    const list = rows || [];
    const key = (r: InitiativeRow): [string, string] => {
      switch (display.grouping) {
        case "status":
          return [r.status, STATUS_LABEL[r.status] || r.status];
        case "health":
          return [r.health || "none", HEALTH.find((h) => h.value === (r.health || "none"))?.label || "No update"];
        case "owner":
          return [r.owner?.id || "none", r.owner?.name || "No owner"];
        case "leadTeam":
          return [r.leadTeam?.id || "none", r.leadTeam?.name || "No lead team"];
        default:
          return ["all", ""];
      }
    };
    const out = new Map<string, { label: string; items: InitiativeRow[] }>();
    for (const r of list) {
      const [k, label] = key(r);
      const g = out.get(k) || { label, items: [] };
      g.items.push(r);
      out.set(k, g);
    }
    return [...out.entries()].map(([k, g]) => ({ key: k, ...g }));
  }, [rows, display.grouping]);

  const save = async () => {
    setBusy(true);
    try {
      onView(await updateView(view.id, { filters: tree, display }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const reset = () => {
    setRoot(filterTreeToChips(view.filters, INITIATIVE_FIELDS));
    setDisplay({ grouping: "none", ordering: "manual", ...(view.display as DisplayDoc) });
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col" data-view-page="" data-initiative-view="" data-initiatives-page="">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2">
        {header}
        <span className="flex-1" />
        <AddFilterButton root={root} onChange={setRoot} catalog={catalog} />
        <DisplayButton open={displayOpen} onOpenChange={setDisplayOpen} hasOverrides={false}>
          <DisplayPanel
            variant="initiative"
            layouts={["list"]}
            showCompleted={false}
            display={{ ...mergeDisplay(), ...display } as never}
            onChange={(patch) => setDisplay((d) => ({ ...d, ...patch }))}
            hasOverrides={false}
            onReset={reset}
            canSetDefault={false}
          />
        </DisplayButton>
      </div>
      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <FilterChips
          root={root}
          onChange={setRoot}
          catalog={catalog}
          advanced={advanced}
          onAdvancedChange={setAdvanced}
          hideClear={dirty}
        />
        {dirty && (
          <span className="ml-auto flex items-center gap-2" data-view-unsaved="">
            <button type="button" className="rounded-md px-2 py-1 text-sm text-muted hover:bg-subtle" onClick={reset}>
              Clear
            </button>
            <button
              type="button"
              className="rounded-md border border-line px-2 py-1 text-sm hover:bg-subtle disabled:opacity-50"
              disabled={busy}
              onClick={() => void save()}
            >
              Save to view
            </button>
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="px-4 text-sm text-danger">
          {error}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto" data-initiative-view-rows="">
        {rows === null ? (
          <p role="status" className="p-10 text-center text-muted">
            Loading initiatives…
          </p>
        ) : !rows.length ? (
          <p className="p-10 text-center text-muted">No initiatives match this view.</p>
        ) : (
          groups.map((g) => (
            <section key={g.key} data-initiative-group={g.key}>
              {g.label && (
                <header className="mx-2.5 mb-0.5 flex h-8.5 items-center gap-2 rounded-[7px] bg-subtle px-2 text-[12px] text-muted">
                  <b className="font-medium text-foreground">{g.label}</b>
                  <span className="text-[11px]">{g.items.length}</span>
                </header>
              )}
              {g.items.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className="flex w-full min-w-0 items-center gap-2 px-6 py-2 text-left text-[13px] hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-none"
                  data-initiative-row={r.id}
                  onClick={() => onOpenInitiative(r.identifier || r.id)}
                >
                  <InitiativeIcon icon={r.icon} color={r.color} />
                  <span className="min-w-0 flex-1 truncate">
                    {r.name}
                    {display.summary !== false && r.summary && (
                      <span className="ml-2 text-muted">{r.summary}</span>
                    )}
                  </span>
                  <InitiativeStatusIcon status={r.status} />
                  {r.health && <HealthBadge health={r.health} />}
                  <span className="w-28 truncate text-right text-[12px] text-muted">{r.owner?.name || ""}</span>
                  <span className="w-20 text-right text-[12px] text-muted">{r.targetDate || ""}</span>
                </button>
              ))}
            </section>
          ))
        )}
      </div>
    </section>
  );
}
