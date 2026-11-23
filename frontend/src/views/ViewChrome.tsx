/* The chrome around a view's rows, after Linear: the "…" menu of a saved view, the unsaved
 * changes actions ("Clear", "Save ▾"), the "Save to" picker of the editor, the details panel
 * (visibility, owner and the breakdown that filters on click) and the table of the views list.
 * Presentation only: every action is a callback the page wires to the views API.
 */
import React, { useState } from "react";
import { DropdownMenu } from "radix-ui";
import {
  ArrowDown,
  ArrowUp,
  Bell,
  Building2,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  Ellipsis,
  Link,
  Lock,
  Pencil,
  Pin,
  Plus,
  Star,
  Trash,
  Users,
} from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import type { ServerView } from "../views";
import { ViewIcon, ViewIconPicker } from "./ViewIcon";
import type { SaveTarget, ViewOrdering, ViewSection } from "./viewModel";

/* A named alias, so the lint rule that accepts a Radix `*Trigger asChild` child recognises it. */
const MenuTrigger = DropdownMenu.Trigger;

const menuClass =
  "z-50 min-w-56 max-w-[calc(100vw-24px)] rounded-lg border border-line bg-panel p-1 text-sm text-foreground shadow-lg";
const itemClass =
  "flex min-h-8 cursor-default select-none items-center gap-2 rounded-md px-2 py-1.5 outline-none data-[highlighted]:bg-subtle data-[disabled]:text-muted data-[disabled]:opacity-60 shell-phone:min-h-11";
const separatorClass = "my-1 h-px bg-line";

export type TeamOption = { id: string; name: string };

/** Where a view can be saved or moved: Personal, Workspace, and each team the person is in. */
export function targetLabel(target: SaveTarget, teams: TeamOption[]): string {
  if (target.kind === "personal") return "Personal";
  if (target.kind === "workspace") return "Workspace";
  if (target.kind === "team" && target.personal) {
    const team = teams.find((t) => t.id === target.teamId)?.name;
    return team ? `Personal · ${team}` : "Personal";
  }
  if (target.kind === "team")
    return teams.find((t) => t.id === target.teamId)?.name || "Team";
  return target.shared ? "Project · shared" : "Project · personal";
}

function TargetIcon({ target }: { target: SaveTarget }) {
  if (
    target.kind === "personal" ||
    (target.kind === "team" && target.personal) ||
    (target.kind === "project" && !target.shared)
  )
    return <Lock size={14} aria-hidden="true" />;
  if (target.kind === "team") return <Users size={14} aria-hidden="true" />;
  return <Building2 size={14} aria-hidden="true" />;
}

function sameTarget(a: SaveTarget, b: SaveTarget): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/* --------------------------------------------------------------- Save to */

/** The editor's "Save to" picker (Personal | Workspace | a team, or a project's two choices). */
export function SaveToPicker({
  value,
  options,
  teams,
  onChange,
}: {
  value: SaveTarget;
  options: SaveTarget[];
  teams: TeamOption[];
  onChange: (target: SaveTarget) => void;
}) {
  return (
    <DropdownMenu.Root>
      <span className="flex items-center gap-2 text-sm text-muted">
        Save to
        <MenuTrigger asChild>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 text-sm text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
            aria-label={`Save to ${targetLabel(value, teams)}`}
            data-view-save-to=""
          >
            <TargetIcon target={value} />
            {targetLabel(value, teams)}
          </button>
        </MenuTrigger>
      </span>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={4} className={menuClass}>
          <DropdownMenu.RadioGroup
            value={JSON.stringify(value)}
            onValueChange={(v) => onChange(JSON.parse(v) as SaveTarget)}
          >
            {options.map((option) => (
              <DropdownMenu.RadioItem
                key={JSON.stringify(option)}
                value={JSON.stringify(option)}
                className={`${itemClass} data-[state=checked]:font-medium`}
              >
                <TargetIcon target={option} />
                <span className="flex-1">{targetLabel(option, teams)}</span>
                {sameTarget(option, value) && (
                  <span aria-hidden="true">✓</span>
                )}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/* ------------------------------------------------------------- "…" menu */

export function ViewMenu({
  view,
  canEdit,
  moveOptions,
  teams,
  onEdit,
  onDuplicate,
  onMove,
  onSubscribe,
  onCopyLink,
  onExport,
  onDelete,
  onPin,
}: {
  /** Pin or unpin a team view as a tab of its team's task page. */
  onPin?: () => void;
  view: ServerView;
  canEdit: boolean;
  moveOptions: SaveTarget[];
  teams: TeamOption[];
  onEdit: () => void;
  onDuplicate: () => void;
  onMove: (target: SaveTarget) => void;
  onSubscribe: (onAdded: boolean, onCompleted: boolean) => void;
  onCopyLink: () => void;
  onExport?: () => void;
  onDelete: () => void;
}) {
  const sub = view.subscription;
  return (
    <DropdownMenu.Root>
      <MenuTrigger asChild>
        <button
          type="button"
          className="grid size-7 place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11"
          aria-label="View actions"
          data-view-menu=""
        >
          <Ellipsis size={15} aria-hidden="true" />
        </button>
      </MenuTrigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          className={menuClass}
          data-view-menu-content=""
        >
          <DropdownMenu.Item
            className={itemClass}
            disabled={!canEdit}
            onSelect={onEdit}
          >
            <Pencil size={14} aria-hidden="true" /> Edit…
          </DropdownMenu.Item>
          <DropdownMenu.Item className={itemClass} onSelect={onDuplicate}>
            <Copy size={14} aria-hidden="true" /> Duplicate…
          </DropdownMenu.Item>
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger
              className={itemClass}
              disabled={!canEdit || moveOptions.length < 2}
            >
              <Building2 size={14} aria-hidden="true" />
              <span className="flex-1">Move to</span>
              <ChevronRight size={14} aria-hidden="true" />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent className={menuClass} sideOffset={4}>
                {moveOptions.map((target) => (
                  <DropdownMenu.Item
                    key={JSON.stringify(target)}
                    className={itemClass}
                    onSelect={() => onMove(target)}
                  >
                    <TargetIcon target={target} />
                    {targetLabel(target, teams)}
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>
          <DropdownMenu.Separator className={separatorClass} />
          {view.kind === "issue" && (
            <DropdownMenu.Sub>
              <DropdownMenu.SubTrigger className={itemClass}>
                <Bell size={14} aria-hidden="true" />
                <span className="flex-1">Subscribe</span>
                <ChevronRight size={14} aria-hidden="true" />
              </DropdownMenu.SubTrigger>
              <DropdownMenu.Portal>
                <DropdownMenu.SubContent className={menuClass} sideOffset={4}>
                  <DropdownMenu.CheckboxItem
                    className={itemClass}
                    checked={Boolean(sub?.onAdded)}
                    onSelect={(e) => e.preventDefault()}
                    onCheckedChange={(on) =>
                      onSubscribe(Boolean(on), Boolean(sub?.onCompleted))
                    }
                  >
                    <span className="w-4" aria-hidden="true">
                      {sub?.onAdded ? "✓" : ""}
                    </span>
                    A task is added to the view
                  </DropdownMenu.CheckboxItem>
                  <DropdownMenu.CheckboxItem
                    className={itemClass}
                    checked={Boolean(sub?.onCompleted)}
                    onSelect={(e) => e.preventDefault()}
                    onCheckedChange={(on) =>
                      onSubscribe(Boolean(sub?.onAdded), Boolean(on))
                    }
                  >
                    <span className="w-4" aria-hidden="true">
                      {sub?.onCompleted ? "✓" : ""}
                    </span>
                    A task is marked completed or canceled
                  </DropdownMenu.CheckboxItem>
                </DropdownMenu.SubContent>
              </DropdownMenu.Portal>
            </DropdownMenu.Sub>
          )}
          {onPin && (
            <DropdownMenu.Item className={itemClass} onSelect={onPin}>
              <Pin size={14} aria-hidden="true" />
              {view.pinned ? "Unpin from team tabs" : "Pin to team tabs"}
            </DropdownMenu.Item>
          )}
          <DropdownMenu.Item className={itemClass} onSelect={onCopyLink}>
            <Link size={14} aria-hidden="true" /> Copy link
          </DropdownMenu.Item>
          {onExport && (
            <DropdownMenu.Item className={itemClass} onSelect={onExport}>
              <Download size={14} aria-hidden="true" /> Export tasks as CSV
            </DropdownMenu.Item>
          )}
          <DropdownMenu.Separator className={separatorClass} />
          <DropdownMenu.Item
            className={`${itemClass} data-[highlighted]:text-danger`}
            disabled={!canEdit}
            onSelect={onDelete}
          >
            <Trash size={14} aria-hidden="true" /> Delete
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/* ---------------------------------------------------- unsaved changes bar */

/** "Clear" and the "Save ▾" split button of a saved view with unsaved filter edits. */
export function UnsavedActions({
  canSave,
  onClear,
  onSave,
  onSaveAsNew,
  busy = false,
}: {
  canSave: boolean;
  onClear: () => void;
  onSave: () => void;
  onSaveAsNew: () => void;
  busy?: boolean;
}) {
  return (
    <div className="flex flex-none items-center gap-1.5" data-view-unsaved="">
      <button
        type="button"
        className="h-7 rounded-md px-2 text-sm text-muted hover:bg-panel hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
        onClick={onClear}
      >
        Clear
      </button>
      <DropdownMenu.Root>
        <MenuTrigger asChild>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1 rounded-md border border-line bg-panel px-2.5 text-sm text-foreground hover:bg-subtle disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
            disabled={busy}
            data-view-save=""
          >
            {busy ? "Saving…" : "Save"} <ChevronDown size={14} aria-hidden="true" />
          </button>
        </MenuTrigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={4} className={menuClass}>
            <DropdownMenu.Item
              className={itemClass}
              disabled={!canSave}
              onSelect={onSave}
            >
              <span className="flex-1">Save to this view</span>
              <kbd className="text-xs text-muted">Alt S</kbd>
            </DropdownMenu.Item>
            <DropdownMenu.Item className={itemClass} onSelect={onSaveAsNew}>
              <span className="flex-1">Create new view…</span>
              <kbd className="text-xs text-muted">Alt V</kbd>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}

/* ---------------------------------------------------------- details panel */

export type BreakdownRow = {
  key: string;
  value: string | null;
  label: string;
  count: number;
  icon?: React.ReactNode;
};

export function ViewDetailsPanel({
  view,
  visibility,
  owners,
  canChangeOwner,
  onOwner,
  breakdown,
  partial,
  onPick,
  onFavorite,
  menu,
}: {
  view: ServerView;
  visibility: string;
  owners: { id: string; name: string }[];
  canChangeOwner: boolean;
  onOwner: (id: string) => void;
  breakdown: Record<"assignee" | "labels" | "project", BreakdownRow[]>;
  /** The counts cover only the rows loaded so far. */
  partial: boolean;
  onPick: (field: "assignee" | "labels" | "project", value: string | null) => void;
  onFavorite: () => void;
  menu?: React.ReactNode;
}) {
  const [tab, setTab] = useState<"assignee" | "labels" | "project">(
    "assignee",
  );
  const rows = breakdown[tab];
  return (
    <aside
      className="grid min-h-0 w-full content-start gap-3 overflow-y-auto md:w-80 md:flex-none"
      aria-label="View details"
      data-view-details=""
    >
      <div className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2.5">
        <ViewIcon icon={view.icon} color={view.color} size={16} />
        <b className="min-w-0 flex-1 truncate font-medium">{view.name}</b>
        <button
          type="button"
          className="grid size-7 place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11 shell-phone:min-w-11"
          aria-label={view.favorite ? "Remove from favorites" : "Add to favorites"}
          aria-pressed={view.favorite}
          onClick={onFavorite}
        >
          <Star
            size={15}
            fill={view.favorite ? "currentColor" : "none"}
            aria-hidden="true"
          />
        </button>
        {menu}
      </div>
      {view.description ? (
        <p className="px-1 text-sm text-muted">{view.description}</p>
      ) : null}
      <dl className="grid grid-cols-[88px_minmax(0,1fr)] items-center gap-x-3 gap-y-2 rounded-lg border border-line bg-panel px-3 py-2.5 text-sm">
        <dt className="text-muted">Visibility</dt>
        <dd className="m-0 flex items-center gap-1.5">
          {view.visibility === "private" ? (
            <Lock size={14} aria-hidden="true" />
          ) : (
            <Building2 size={14} aria-hidden="true" />
          )}
          {visibility}
        </dd>
        <dt className="text-muted">Owner</dt>
        <dd className="m-0 min-w-0">
          {canChangeOwner ? (
            <select
              aria-label="Owner"
              className="w-full rounded-md border border-line bg-panel px-1.5 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-accent max-md:text-base shell-phone:min-h-11"
              value={view.owner?.id || ""}
              onChange={(e) => onOwner(e.target.value)}
            >
              {!view.owner && <option value="">Unknown</option>}
              {owners.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="truncate">{view.owner?.name || "Unknown"}</span>
          )}
        </dd>
      </dl>
      {view.kind === "issue" && (
        <div className="grid gap-2 rounded-lg border border-line bg-panel p-2">
          <div className="grid grid-cols-3 gap-1" role="tablist" aria-label="Breakdown">
            {(
              [
                ["assignee", "Assignees"],
                ["labels", "Labels"],
                ["project", "Projects"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                className="h-7 rounded-full border border-line text-sm text-muted aria-selected:bg-subtle aria-selected:font-medium aria-selected:text-foreground shell-phone:min-h-11"
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <ul className="m-0 grid list-none gap-0.5 p-0" data-view-breakdown="">
            {rows.length ? (
              rows.map((row) => (
                <li key={row.key}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-subtle shell-phone:min-h-11"
                    title={`Filter by ${row.label}`}
                    onClick={() => onPick(tab, row.value)}
                  >
                    {row.icon}
                    <span className="min-w-0 flex-1 truncate">{row.label}</span>
                    <span className="text-muted tabular-nums">{row.count}</span>
                  </button>
                </li>
              ))
            ) : (
              <li className="px-2 py-1.5 text-sm text-muted">Nothing to count</li>
            )}
          </ul>
          {partial && (
            <p className="px-2 text-xs text-muted">
              Counts cover the tasks loaded so far.
            </p>
          )}
        </div>
      )}
    </aside>
  );
}

/* ------------------------------------------------------------ views list */

export type ListColumn = "owner" | "created" | "updated";

export function ViewsTable({
  sections,
  ordering,
  descending,
  columns,
  onOrder,
  onOpen,
  onFavorite,
  onNew,
}: {
  sections: ViewSection[];
  ordering: ViewOrdering;
  descending: boolean;
  columns: ListColumn[];
  onOrder: (ordering: ViewOrdering) => void;
  onOpen: (view: ServerView) => void;
  onFavorite: (view: ServerView) => void;
  onNew?: (section: ViewSection) => void;
}) {
  const date = (iso?: string) =>
    iso
      ? new Date(iso).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        })
      : "";
  const grid = "grid items-center gap-3";
  // Phones keep the name and the star; the other columns are hidden there (max-md:hidden).
  const template = (wide: boolean) =>
    [
      "minmax(0,1fr)",
      ...(wide && columns.includes("owner") ? ["minmax(0,160px)"] : []),
      ...(wide && columns.includes("created") ? ["72px"] : []),
      ...(wide && columns.includes("updated") ? ["72px"] : []),
      "32px",
    ].join(" ");
  const [wide, setWide] = useState(
    () => typeof window === "undefined" || window.innerWidth >= 768,
  );
  React.useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const on = () => setWide(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const style = { gridTemplateColumns: template(wide) };
  const header = (label: string, key: ViewOrdering) => (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded px-1 text-left text-xs text-muted hover:text-foreground shell-phone:min-h-11"
      aria-label={`Sort by ${label}`}
      onClick={() => onOrder(key)}
    >
      {label}
      {ordering === key &&
        (descending ? (
          <ArrowUp size={12} aria-hidden="true" />
        ) : (
          <ArrowDown size={12} aria-hidden="true" />
        ))}
    </button>
  );
  return (
    <div className="grid min-w-0 gap-1" role="table" aria-label="Views" data-views-table="">
      <div className={`${grid} px-3 py-1`} style={style} role="row">
        <span role="columnheader">{header("Name", "name")}</span>
        {columns.includes("owner") && (
          <span role="columnheader" className="max-md:hidden">
            {header("Owner", "owner")}
          </span>
        )}
        {columns.includes("created") && (
          <span role="columnheader" className="max-md:hidden">
            {header("Created", "created")}
          </span>
        )}
        {columns.includes("updated") && (
          <span role="columnheader" className="max-md:hidden">
            {header("Updated", "updated")}
          </span>
        )}
        <span role="columnheader" className="sr-only">
          Favorite
        </span>
      </div>
      {sections.map((section) => (
        <section
          key={section.key}
          className="grid gap-0.5"
          aria-label={section.label}
          data-views-section={section.key}
        >
          <div className="flex h-9 items-center gap-2 rounded-md bg-subtle px-3 text-sm">
            <b className="font-medium">{section.label}</b>
            {section.hint && (
              <span className="text-muted">· {section.hint}</span>
            )}
            {onNew && (
              <button
                type="button"
                className="ml-auto grid size-6 place-items-center rounded text-muted hover:bg-panel hover:text-foreground shell-phone:min-h-11 shell-phone:min-w-11"
                aria-label={`New view in ${section.label}`}
                onClick={() => onNew(section)}
              >
                <Plus size={14} aria-hidden="true" />
              </button>
            )}
          </div>
          {section.views.map((v) => (
            <div
              key={v.id}
              role="row"
              style={style}
              className={`${grid} rounded-md px-3 hover:bg-subtle`}
              data-views-row=""
            >
              <button
                type="button"
                role="cell"
                className="flex min-h-10 min-w-0 items-center gap-2.5 text-left text-sm"
                onClick={() => onOpen(v)}
              >
                <ViewIcon icon={v.icon} color={v.color} />
                <span className="min-w-0 truncate font-medium">{v.name}</span>
                {v.description && (
                  <span className="min-w-0 truncate text-muted max-md:hidden">
                    {v.description}
                  </span>
                )}
              </button>
              {columns.includes("owner") && (
                <span role="cell" className="truncate text-sm text-muted max-md:hidden">
                  {v.owner?.name || ""}
                </span>
              )}
              {columns.includes("created") && (
                <span role="cell" className="text-sm text-muted max-md:hidden">
                  {date(v.createdAt)}
                </span>
              )}
              {columns.includes("updated") && (
                <span role="cell" className="text-sm text-muted max-md:hidden">
                  {date(v.updatedAt)}
                </span>
              )}
              <button
                type="button"
                role="cell"
                className="grid size-7 place-items-center rounded text-muted hover:text-foreground shell-phone:min-h-11 shell-phone:min-w-11"
                aria-label={`${v.favorite ? "Remove favorite" : "Favorite"} ${v.name}`}
                aria-pressed={v.favorite}
                onClick={() => onFavorite(v)}
              >
                <Star
                  size={14}
                  fill={v.favorite ? "currentColor" : "none"}
                  aria-hidden="true"
                />
              </button>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

/** The list page's own small display menu: ordering and the visible columns. */
export function ViewsListDisplay({
  ordering,
  descending,
  columns,
  onOrdering,
  onDirection,
  onColumns,
}: {
  ordering: ViewOrdering;
  descending: boolean;
  columns: ListColumn[];
  onOrdering: (ordering: ViewOrdering) => void;
  onDirection: (descending: boolean) => void;
  onColumns: (columns: ListColumn[]) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 text-sm text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
          aria-label="List display options"
          data-views-display=""
        >
          Display
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={4}
        className="z-50 grid w-64 max-w-[calc(100vw-24px)] gap-3 rounded-lg border border-line bg-panel p-3 text-sm text-foreground shadow-lg"
        aria-label="List display options"
      >
        <label className="flex items-center justify-between gap-2 text-muted">
          Ordering
          <select
            className="w-32 rounded-md border border-line bg-panel px-2 py-1 text-sm text-foreground max-md:text-base shell-phone:min-h-11"
            value={ordering}
            onChange={(e) => onOrdering(e.target.value as ViewOrdering)}
          >
            <option value="name">Name</option>
            <option value="owner">Owner</option>
            <option value="created">Created</option>
            <option value="updated">Updated</option>
          </select>
        </label>
        <label className="flex items-center justify-between gap-2 text-muted">
          Descending
          <input
            type="checkbox"
            checked={descending}
            onChange={(e) => onDirection(e.target.checked)}
          />
        </label>
        <fieldset className="m-0 grid gap-1.5 border-0 p-0">
          <legend className="mb-1 text-xs text-muted">Properties</legend>
          <div className="flex flex-wrap gap-1.5">
            {(
              [
                ["owner", "Owner"],
                ["created", "Created"],
                ["updated", "Updated"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={columns.includes(key)}
                className="rounded-full border border-line px-2.5 py-0.5 text-sm text-muted aria-pressed:bg-subtle aria-pressed:text-foreground shell-phone:min-h-11"
                onClick={() =>
                  onColumns(
                    columns.includes(key)
                      ? columns.filter((c) => c !== key)
                      : [...columns, key],
                  )
                }
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>
      </PopoverContent>
    </Popover>
  );
}

/* ------------------------------------------------------------ identity card */

/** The head of the view editor (and of "Edit…" on a saved view): icon and colour, name and
 * description edited in place, with the page's own actions on the right. */
export function ViewIdentityCard({
  icon,
  color,
  name,
  description,
  namePlaceholder,
  onChange,
  actions,
  autoFocusName = false,
  onSubmit,
  onCancel,
}: {
  icon?: string | null;
  color?: string | null;
  name: string;
  description: string;
  namePlaceholder: string;
  onChange: (patch: {
    icon?: string | null;
    color?: string | null;
    name?: string;
    description?: string;
  }) => void;
  actions: React.ReactNode;
  autoFocusName?: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const nameRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (autoFocusName) nameRef.current?.focus();
  }, [autoFocusName]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };
  return (
    <div
      className="grid gap-1 rounded-lg border border-line bg-panel px-3 py-2.5"
      data-view-identity=""
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">
        <ViewIconPicker icon={icon} color={color} onChange={onChange} />
        <input
          ref={nameRef}
          className="min-w-40 flex-1 border-0 bg-transparent text-lg font-medium text-foreground outline-none placeholder:text-muted focus-visible:outline-none max-md:text-base"
          value={name}
          maxLength={80}
          placeholder={namePlaceholder}
          aria-label="View name"
          onChange={(e) => onChange({ name: e.target.value })}
          onKeyDown={onKey}
          data-view-name-input=""
        />
        <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>
      </div>
      <input
        className="ml-10.5 border-0 bg-transparent text-sm text-foreground outline-none placeholder:text-muted max-md:ml-0 max-md:text-base"
        value={description}
        maxLength={500}
        placeholder="Description (optional)"
        aria-label="View description"
        onChange={(e) => onChange({ description: e.target.value })}
        onKeyDown={onKey}
        data-view-description-input=""
      />
    </div>
  );
}
