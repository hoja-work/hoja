/* The initiative page's extra tabs: Insights (tasks across every project, broken
 * down) and saved views of the initiative's projects. */
import React, { useEffect, useState } from "react";
import { Box, Trash2 } from "lucide-react";
import { gql } from "../main";
import { fmtDate, IconBtn, Ring } from "../graph/shared";
import { DetailButton } from "../graph/detail";
import { savePageView, type InitiativeProject, type PageView } from "./api";
import { HealthBadge } from "./fields";

type Row = {
  key: string;
  label: string;
  color?: string | null;
  total: number;
  completed: number;
};
type Insights = {
  scope: number;
  completed: number;
  started: number;
  byStatus: Row[];
  byAssignee: Row[];
  byLabel: Row[];
  byProject: Row[];
};

/** A number that leads: label above, value below. */
function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid gap-1 rounded-lg border border-line bg-panel p-4">
      <span className="text-xs text-muted">{label}</span>
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
    </div>
  );
}

/* One breakdown: each row is a bar of its share of the scope, with the completed part
 * filled. The numbers are written out, so the bar is never the only carrier. */
function Breakdown({
  title,
  rows,
  scope,
}: {
  title: string;
  rows: Row[];
  scope: number;
}) {
  if (!rows.length) return null;
  return (
    <section className="grid content-start gap-2" aria-label={title}>
      <h3 className="m-0 text-sm font-semibold">{title}</h3>
      <ul className="m-0 grid list-none gap-2 p-0">
        {rows.slice(0, 12).map((r) => (
          <li key={r.key} className="grid gap-1 text-sm">
            <div className="flex items-center gap-2">
              {r.color && (
                <span
                  aria-hidden
                  className="size-2 flex-none rounded-full"
                  style={{ background: r.color }}
                />
              )}
              <span className="min-w-0 flex-1 truncate">{r.label}</span>
              <span className="tabular-nums text-muted">
                {r.completed}/{r.total} done
              </span>
            </div>
            <div
              aria-hidden
              className="flex h-1.5 overflow-hidden rounded-full bg-subtle"
            >
              <span
                className="rounded-full bg-(--series-1)"
                style={{ width: `${scope ? (r.completed / scope) * 100 : 0}%` }}
              />
              <span
                className="bg-(--series-1) opacity-30"
                style={{
                  width: `${scope ? ((r.total - r.completed) / scope) * 100 : 0}%`,
                }}
              />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function InsightsTab({ id }: { id: string }) {
  const [data, setData] = useState<Insights | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const row = "key label color total completed";
    gql(
      `query($id:String!){initiative(id:$id){insights{scope completed started byStatus{${row}} byAssignee{${row}} byLabel{${row}} byProject{${row}}}}}`,
      { id },
    )
      .then((d) => setData(d.initiative?.insights || null))
      .catch((e: Error) => setError(e.message));
  }, [id]);
  if (error)
    return (
      <p className="text-sm text-danger" role="alert">
        {error}
      </p>
    );
  if (!data)
    return (
      <p className="text-sm text-muted" role="status">
        Loading insights…
      </p>
    );
  if (!data.scope)
    return (
      <p className="text-sm text-muted" data-initiative-insights="">
        Insights appear once the initiative's projects have tasks.
      </p>
    );
  const pct = Math.round((data.completed / data.scope) * 100);
  return (
    <div className="grid gap-6" data-initiative-insights="">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Scope" value={data.scope} />
        <Stat label="Completed" value={data.completed} />
        <Stat label="In progress" value={data.started} />
        <Stat label="Done" value={`${pct}%`} />
      </div>
      <p className="m-0 text-xs text-muted">
        Tasks across every project of this initiative and its sub-initiatives;
        canceled tasks are left out. Each bar is that group's share of the
        scope, filled where done.
      </p>
      <div className="grid gap-6 md:grid-cols-2">
        <Breakdown title="By status" rows={data.byStatus} scope={data.scope} />
        <Breakdown
          title="By project"
          rows={data.byProject}
          scope={data.scope}
        />
        <Breakdown
          title="By assignee"
          rows={data.byAssignee}
          scope={data.scope}
        />
        <Breakdown title="By label" rows={data.byLabel} scope={data.scope} />
      </div>
    </div>
  );
}

const PROJECT_STATES = [
  "backlog",
  "planned",
  "started",
  "paused",
  "completed",
  "canceled",
];

/** Projects of the initiative that a saved view selects (state and team). */
export function PageViewTab({
  view,
  projects,
  onOpenProject,
  onDelete,
}: {
  view: PageView;
  projects: InitiativeProject[];
  onOpenProject: (id: string) => void;
  onDelete: () => void;
}) {
  const wanted = (key: string) => {
    const f = view.filters?.[key];
    return f ? f.isEitherOf || (f.is ? [f.is] : []) : null;
  };
  const states = wanted("state");
  const teams = wanted("team");
  const rows = projects.filter(
    (p) =>
      (!states || states.includes((p.state || "").toLowerCase())) &&
      (!teams || (p.teams?.nodes || []).some((t) => teams.includes(t.id))),
  );
  return (
    <section className="grid gap-3" data-initiative-page-view="">
      <div className="flex items-center gap-2">
        <h2 className="m-0 flex-1 text-base font-semibold">{view.name}</h2>
        <IconBtn
          label={`Delete view ${view.name}`}
          title="Delete view"
          onClick={onDelete}
        >
          <Trash2 size={14} />
        </IconBtn>
      </div>
      <p className="m-0 text-xs text-muted">
        {states ? `Status: ${states.join(", ")}. ` : ""}
        {rows.length} of {projects.length} projects.
      </p>
      <ul className="m-0 grid list-none p-0 text-sm">
        {rows.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none"
              onClick={() => onOpenProject(p.id)}
            >
              <Box size={14} aria-hidden className="flex-none text-muted" />
              <span className="min-w-0 flex-1 truncate font-medium">
                {p.name}
              </span>
              <span className="hidden text-muted md:inline">{p.state}</span>
              <HealthBadge health={p.health} />
              <span className="hidden text-muted md:inline">
                {p.targetDate ? fmtDate(p.targetDate) : "—"}
              </span>
              <Ring frac={(p.progress || 0) / 100} size={14} />
            </button>
          </li>
        ))}
        {!rows.length && (
          <li className="py-2 text-sm text-muted">
            No projects match this view.
          </li>
        )}
      </ul>
    </section>
  );
}

/** The "+" tab: name a view of the initiative's projects by state and team. */
export function AddPageView({
  initiativeId,
  projects,
  onSaved,
  onCancel,
}: {
  initiativeId: string;
  projects: InitiativeProject[];
  onSaved: (view: PageView) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [states, setStates] = useState<string[]>([]);
  const [teams, setTeams] = useState<string[]>([]);
  const [error, setError] = useState("");
  const teamOptions = [
    ...new Map(
      projects
        .flatMap((p) => p.teams?.nodes || [])
        .map((t) => [t.id, t] as const),
    ).values(),
  ];
  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
  return (
    <form
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
      data-initiative-add-view=""
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        const filters: PageView["filters"] = {};
        if (states.length) filters.state = { isEitherOf: states };
        if (teams.length) filters.team = { isEitherOf: teams };
        savePageView(initiativeId, name.trim(), filters)
          .then(onSaved)
          .catch((err: Error) => setError(err.message));
      }}
    >
      <h2 className="m-0 text-base font-semibold">
        New view of this initiative
      </h2>
      <input
        aria-label="View name"
        placeholder="View name"
        className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <fieldset className="m-0 flex flex-wrap gap-3 border-0 p-0 text-sm">
        <legend className="mb-1 text-xs text-muted">Project status</legend>
        {PROJECT_STATES.map((s) => (
          <label key={s} className="inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={states.includes(s)}
              onChange={() => setStates((v) => toggle(v, s))}
            />
            {s}
          </label>
        ))}
      </fieldset>
      {teamOptions.length > 0 && (
        <fieldset className="m-0 flex flex-wrap gap-3 border-0 p-0 text-sm">
          <legend className="mb-1 text-xs text-muted">Team</legend>
          {teamOptions.map((t) => (
            <label key={t.id} className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={teams.includes(t.id)}
                onChange={() => setTeams((v) => toggle(v, t.id))}
              />
              {t.name || t.key}
            </label>
          ))}
        </fieldset>
      )}
      {error && (
        <p className="m-0 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <DetailButton onClick={onCancel}>Cancel</DetailButton>
        <DetailButton primary type="submit" disabled={!name.trim()}>
          Save view
        </DetailButton>
      </div>
    </form>
  );
}
