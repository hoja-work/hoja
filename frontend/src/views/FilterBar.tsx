/* The filter bar every task list shares, after Linear's: "Add filter" (F) opens a type-ahead
 * menu of categories, each with its values and how many loaded rows each would select; picked
 * values become chips ("Priority | is either of | Urgent, High | ×") whose operator and values
 * stay clickable; "Advanced filter" edits nested AND/OR groups. The chips are the
 * `filterModel` tree, so what this bar shows is exactly what a saved view or URL stores.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  Box,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  CircleDot,
  FileText,
  ListFilter,
  Network,
  Plus,
  RefreshCcw,
  SignalHigh,
  Tag,
  Target,
  Triangle,
  UserPen,
  UserRound,
  Users,
  X,
  HeartPulse,
  Boxes,
} from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import {
  appendTo,
  chipCount,
  datePresets,
  describeDate,
  emptyGroup,
  chipOps,
  effectiveOp,
  fieldSpec,
  isSimple,
  opLabel,
  parseDateInput,
  RELATION_VALUES,
  removeNode,
  setChipValues,
  toggleValue,
  updateNode,
  type ChipOp,
  type FieldSpec,
  type FilterChip,
  type FilterGroup,
  type FilterNode,
  type FilterValue,
} from "./filterModel";
import {
  valuesLabel,
  type FilterCatalog,
  type MenuEntry,
} from "./filterLabels";

export {
  suggestViewName,
  uniqueName,
  valueLabel,
  type FilterCatalog,
  type MenuEntry,
  type ValueOption,
} from "./filterLabels";

const ICON = 14;
const UNTRACKED_RELATIONS =
  "Hoja does not track blocking, related or duplicate tasks yet.";

export const ISSUE_MENU: MenuEntry[] = [
  {
    key: "status",
    label: "Status",
    icon: <CircleDot size={ICON} />,
    field: "status",
  },
  {
    key: "statusType",
    label: "Status type",
    icon: <CircleDashed size={ICON} />,
    field: "statusType",
  },
  {
    key: "assignee",
    label: "Assignee",
    icon: <UserRound size={ICON} />,
    field: "assignee",
  },
  {
    key: "creator",
    label: "Creator",
    icon: <UserPen size={ICON} />,
    field: "creator",
  },
  {
    key: "priority",
    label: "Priority",
    icon: <SignalHigh size={ICON} />,
    field: "priority",
  },
  {
    key: "labels",
    label: "Labels",
    icon: <Tag size={ICON} />,
    field: "labels",
  },
  {
    key: "relation",
    label: "Relations",
    icon: <Network size={ICON} />,
    field: "relation",
  },
  {
    key: "dates",
    label: "Dates",
    icon: <CalendarDays size={ICON} />,
    fields: [
      "dueDate",
      "createdAt",
      "updatedAt",
      "startedAt",
      "completedAt",
      "canceledAt",
    ],
  },
  {
    key: "project",
    label: "Project",
    icon: <Box size={ICON} />,
    field: "project",
  },
  {
    key: "projectProperties",
    label: "Project properties",
    icon: <Boxes size={ICON} />,
    disabled:
      "Filtering tasks by project status, lead or labels is not a view filter yet.",
  },
  {
    key: "cycle",
    label: "Cycle",
    icon: <RefreshCcw size={ICON} />,
    field: "cycle",
  },
  {
    key: "estimate",
    label: "Estimate",
    icon: <Triangle size={ICON} />,
    field: "estimate",
  },
  {
    key: "subscriber",
    label: "Subscribers",
    icon: <Bell size={ICON} />,
    field: "subscriber",
  },
  { key: "team", label: "Team", icon: <Users size={ICON} />, field: "team" },
  {
    key: "content",
    label: "Content",
    icon: <FileText size={ICON} />,
    fields: ["content", "title", "description"],
  },
];

export const PROJECT_MENU: MenuEntry[] = [
  {
    key: "status",
    label: "Status",
    icon: <CircleDot size={ICON} />,
    field: "status",
  },
  {
    key: "lead",
    label: "Lead",
    icon: <UserRound size={ICON} />,
    field: "lead",
  },
  {
    key: "members",
    label: "Members",
    icon: <Users size={ICON} />,
    field: "members",
  },
  {
    key: "creator",
    label: "Creator",
    icon: <UserPen size={ICON} />,
    field: "creator",
  },
  {
    key: "health",
    label: "Health",
    icon: <HeartPulse size={ICON} />,
    field: "health",
  },
  {
    key: "team",
    label: "Lead team",
    icon: <Users size={ICON} />,
    field: "team",
  },
  {
    key: "dates",
    label: "Dates",
    icon: <CalendarDays size={ICON} />,
    fields: ["startDate", "targetDate"],
  },
  {
    key: "initiative",
    label: "Initiative",
    icon: <Target size={ICON} />,
    field: "initiative",
  },
  {
    key: "content",
    label: "Title & summary",
    icon: <FileText size={ICON} />,
    fields: ["name", "summary"],
  },
];

function rawLabel(chip: FilterChip): [string, string, string] {
  const value = chip.raw?.value;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const [op, operand] = Object.entries(
      value as Record<string, unknown>,
    )[0] || ["", ""];
    return [
      chip.raw?.key || "",
      op,
      Array.isArray(operand) ? operand.join(", ") : String(operand),
    ];
  }
  return [chip.raw?.key || "", "is", String(value)];
}

/* ------------------------------------------------------------ menu */

type Item = {
  id: string;
  label: string;
  icon?: React.ReactNode;
  hint?: string;
  count?: number;
  checked?: boolean;
  disabled?: string;
  submenu?: boolean;
  run: () => void;
};

const itemClass =
  "flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 data-active:bg-subtle shell-phone:min-h-11";

/** The value list of one field or the category list, with a search box on top. `chip` set
 * means the menu edits that chip's values instead of adding to `groupId`. */
function FilterMenu({
  root,
  onChange,
  catalog,
  groupId,
  chip,
  onAdvanced,
  onDone,
}: {
  root: FilterGroup;
  onChange: (root: FilterGroup) => void;
  catalog: FilterCatalog;
  groupId: string;
  chip?: FilterChip;
  onAdvanced?: () => void;
  onDone: () => void;
}) {
  const [query, setQuery] = useState("");
  const [path, setPath] = useState<string[]>(() => (chip ? [chip.field] : []));
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => inputRef.current?.focus(), [path]);
  const fields = catalog.fields;

  const group = useMemo((): FilterGroup => {
    if (root.id === groupId) return root;
    let found: FilterGroup | null = null;
    const walk = (n: FilterNode) => {
      if (n.type === "group") {
        if (n.id === groupId) found = n;
        n.items.forEach(walk);
      }
    };
    walk(root);
    return found || root;
  }, [root, groupId]);

  const liveChip = chip ? findChip(root, chip.id) : undefined;
  const chipFor = (field: string, op?: ChipOp) =>
    liveChip ||
    group.items.find(
      (n): n is FilterChip =>
        n.type === "chip" && n.field === field && (!op || n.op === op),
    );

  const apply = (field: string, value: FilterValue, op?: ChipOp) => {
    const spec = fieldSpec(field, fields);
    if (liveChip) {
      const has = liveChip.values.includes(value);
      const values = spec.single
        ? [value]
        : has
          ? liveChip.values.filter((v) => v !== value)
          : [...liveChip.values, value];
      const next = setChipValues(root, liveChip.id, values, fields);
      onChange(
        op && op !== liveChip.op
          ? updateNode(next, liveChip.id, (n) => ({ ...n, op }) as FilterChip)
          : next,
      );
    } else if (group.id === root.id)
      onChange(toggleValue(root, field, value, fields, op));
    else
      onChange(
        updateNode(root, group.id, (g) =>
          g.type === "group" ? toggleValue(g, field, value, fields, op) : g,
        ),
      );
    if (spec.single) onDone();
  };

  const currentField = path[path.length - 1];
  const entry = catalog.menu.find((m) => m.key === path[0]);
  const spec =
    currentField &&
    (entry?.field || entry?.fields?.includes(currentField) || chip)
      ? fieldSpec(currentField, fields)
      : null;
  const q = query.trim().toLowerCase();

  const valueItems = (field: string, prefix = ""): Item[] => {
    const s = fieldSpec(field, fields);
    const existing = chipFor(field);
    if (s.type === "relation")
      return [
        ...RELATION_VALUES.map((r) => ({
          id: `${field}:${r.value}`,
          label: prefix + r.label,
          hint: r.hint,
          checked: Boolean(existing?.values.includes(r.value)),
          icon: <Network size={ICON} />,
          run: () => apply(field, r.value),
        })),
        ...[
          "Blocked tasks",
          "Blocking tasks",
          "Tasks with relations",
          "Duplicates",
        ].map((label) => ({
          id: `${field}:${label}`,
          label: prefix + label,
          disabled: UNTRACKED_RELATIONS,
          run: () => undefined,
        })),
      ];
    return catalog.options(field).map((o) => ({
      id: `${field}:${String(o.value)}`,
      label: prefix + o.label,
      icon: o.icon,
      count: catalog.count?.(field, o.value),
      checked: Boolean(existing?.values.includes(o.value)),
      disabled: o.disabled,
      run: () => apply(field, o.value),
    }));
  };

  let items: Item[] = [];
  let note = "";
  if (!path.length) {
    const categories: Item[] = catalog.menu.map((m) => ({
      id: m.key,
      label: m.label,
      icon: m.icon,
      disabled: m.disabled,
      submenu: !m.disabled,
      run: () => {
        setPath(m.field ? [m.key, m.field] : [m.key]);
        setQuery("");
        setActive(0);
      },
    }));
    if (!q) {
      items = [
        ...(onAdvanced
          ? [
              {
                id: "advanced",
                label: "Advanced filter",
                icon: <ListFilter size={ICON} />,
                run: onAdvanced,
              },
            ]
          : []),
        ...categories,
      ];
    } else {
      // Typing searches categories and every value at once ("urg" finds Priority: Urgent).
      const hits = categories.filter((c) => c.label.toLowerCase().includes(q));
      const values: Item[] = [];
      for (const m of catalog.menu) {
        if (!m.field || m.disabled) continue;
        const s = fieldSpec(m.field, fields);
        if (s.type === "text" || s.type === "content" || s.type === "date")
          continue;
        for (const it of valueItems(m.field, `${m.label}: `))
          if (!it.disabled && it.label.toLowerCase().includes(q))
            values.push(it);
      }
      items = [...hits, ...values.slice(0, 40)];
    }
  } else if (entry?.fields && path.length === 1) {
    items = entry.fields
      .map((f) => fieldSpec(f, fields))
      .filter((s) => !q || s.label.toLowerCase().includes(q))
      .map((s) => ({
        id: s.key,
        label: s.label,
        icon: entry.icon,
        submenu: true,
        run: () => {
          setPath([entry.key, s.key]);
          setQuery("");
          setActive(0);
        },
      }));
  } else if (spec && (spec.type === "text" || spec.type === "content")) {
    note = `Type text to match ${spec.type === "content" ? "in the title or description" : `in the ${spec.label.toLowerCase()}`}, then press Enter.`;
    if (q)
      items = [
        {
          id: "text",
          label: `${spec.label} contains “${query.trim()}”`,
          icon: <FileText size={ICON} />,
          run: () => apply(spec.key, query.trim(), "contains"),
        },
      ];
  } else if (spec && spec.type === "date") {
    const existing = chipFor(spec.key);
    note =
      spec.direction === "future"
        ? "Try: 1 week from now, 2 weeks ago, Dec 31"
        : "Try: 10 days ago, 2 weeks, last month";
    const parsed = q ? parseDateInput(query, spec.direction) : null;
    const presets = datePresets(spec).filter(
      (p) => !q || p.label.toLowerCase().includes(q),
    );
    items = [
      ...(parsed
        ? [
            {
              id: "parsed",
              label: `${spec.label} ${opLabel(spec, parsed === "overdue" || parsed === "today" ? "is" : defaultDateOp(spec), 1)} ${describeDate(parsed)}`,
              icon: <CalendarDays size={ICON} />,
              run: () =>
                apply(
                  spec.key,
                  parsed,
                  parsed === "overdue" || parsed === "today"
                    ? "is"
                    : defaultDateOp(spec),
                ),
            },
          ]
        : []),
      ...presets.map((p) => ({
        id: `preset:${String(p.value)}`,
        label: p.label,
        checked: Boolean(
          existing?.values.includes(p.value) && existing.op === p.op,
        ),
        run: () => apply(spec.key, p.value, p.op),
      })),
      ...(spec.nullable && (!q || `no ${spec.label}`.toLowerCase().includes(q))
        ? [
            {
              id: "null",
              label: `No ${spec.label.toLowerCase()}`,
              count: catalog.count?.(spec.key, null),
              checked: Boolean(existing?.values.includes(null)),
              run: () => apply(spec.key, null, "is"),
            },
          ]
        : []),
    ];
    if (q && !parsed && !presets.length)
      note = `“${query.trim()}” is not a date Hoja understands. ${note}.`;
  } else if (spec) {
    items = valueItems(spec.key).filter(
      (it) => !q || it.label.toLowerCase().includes(q),
    );
  }

  const enabled = items.filter((it) => !it.disabled);
  const activeIndex = Math.min(active, Math.max(0, enabled.length - 1));
  const activeId = enabled[activeIndex]?.id;
  useEffect(() => {
    listRef.current
      ?.querySelector("[data-active]")
      ?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  const back = () => {
    if (chip) return;
    setPath((p) => (entry?.field ? [] : p.slice(0, -1)));
    setQuery("");
    setActive(0);
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, enabled.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      enabled[activeIndex]?.run();
    } else if (e.key === "ArrowRight" && enabled[activeIndex]?.submenu) {
      e.preventDefault();
      enabled[activeIndex].run();
    } else if (
      (e.key === "ArrowLeft" || e.key === "Backspace") &&
      !query &&
      path.length
    ) {
      e.preventDefault();
      back();
    }
  };

  const title = spec ? spec.label : entry?.label;
  return (
    <div className="flex max-h-96 min-w-0 flex-col" data-filter-menu="">
      <div className="flex items-center gap-1.5 border-b border-line px-2 py-1.5">
        {path.length > 0 && !chip && (
          <button
            type="button"
            className="grid size-6 flex-none place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground"
            aria-label="Back to filter categories"
            onClick={back}
          >
            <ChevronLeft size={ICON} />
          </button>
        )}
        <input
          ref={inputRef}
          className="min-w-0 flex-1 border-0 bg-transparent py-1 text-sm text-foreground outline-none placeholder:text-muted max-md:text-base"
          placeholder={title ? `${title}…` : "Add filter…"}
          aria-label={title ? `Search ${title}` : "Search filters"}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
        />
        {!path.length && (
          <kbd className="rounded border border-line px-1.5 text-xs text-muted">
            F
          </kbd>
        )}
      </div>
      <div
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1"
        role="menu"
        aria-label={title || "Filters"}
      >
        {items.map((it) => (
          <button
            key={it.id}
            type="button"
            role={
              it.submenu
                ? "menuitem"
                : it.checked === undefined
                  ? "menuitem"
                  : "menuitemcheckbox"
            }
            aria-checked={
              it.submenu || it.checked === undefined
                ? undefined
                : Boolean(it.checked)
            }
            aria-haspopup={it.submenu ? "menu" : undefined}
            className={itemClass}
            data-active={it.id === activeId ? "" : undefined}
            disabled={Boolean(it.disabled)}
            title={it.disabled || it.hint}
            onMouseEnter={() => {
              const i = enabled.findIndex((x) => x.id === it.id);
              if (i >= 0) setActive(i);
            }}
            onClick={it.run}
          >
            {it.checked !== undefined && !it.submenu && (
              <span
                className="grid size-3.5 flex-none place-items-center rounded border border-line"
                aria-hidden="true"
              >
                {it.checked && <Check size={11} />}
              </span>
            )}
            {it.icon && (
              <span className="flex-none text-muted" aria-hidden="true">
                {it.icon}
              </span>
            )}
            <span className="min-w-0 flex-1 truncate">{it.label}</span>
            {it.count !== undefined && (
              <span className="flex-none text-xs text-muted">{it.count}</span>
            )}
            {it.submenu && (
              <ChevronRight
                size={ICON}
                className="flex-none text-muted"
                aria-hidden="true"
              />
            )}
          </button>
        ))}
        {spec?.type === "date" && (
          <label className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted">
            Custom date
            <input
              type="date"
              className="min-w-0 flex-1 rounded-md border border-line bg-panel px-1.5 py-0.5 text-sm text-foreground max-md:text-base"
              onChange={(e) =>
                e.target.value &&
                apply(spec.key, e.target.value, defaultDateOp(spec))
              }
            />
          </label>
        )}
        {!items.length && !note && (
          <p className="px-2 py-1.5 text-sm text-muted">No matches</p>
        )}
        {note && <p className="px-2 py-1.5 text-xs text-muted">{note}</p>}
      </div>
    </div>
  );
}

function defaultDateOp(spec: FieldSpec): ChipOp {
  return spec.direction === "future" ? "before" : "after";
}

function findChip(root: FilterGroup, id: string): FilterChip | undefined {
  for (const n of root.items) {
    if (n.type === "chip" && n.id === id) return n;
    if (n.type === "group") {
      const found = findChip(n, id);
      if (found) return found;
    }
  }
  return undefined;
}

const panelClass =
  "z-50 w-72 max-w-full overflow-hidden rounded-lg border border-line bg-panel p-0 text-sm text-foreground shadow-lg";

/** The "Add filter" trigger: the toolbar icon, the "+" after the chips, or "+ Filter" in a group. */
export function AddFilterButton({
  root,
  onChange,
  catalog,
  groupId,
  open,
  onOpenChange,
  onAdvanced,
  variant = "icon",
}: {
  root: FilterGroup;
  onChange: (root: FilterGroup) => void;
  catalog: FilterCatalog;
  groupId?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onAdvanced?: () => void;
  variant?: "icon" | "plus" | "text";
}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const isOpen = open ?? ownOpen;
  const setOpen = (v: boolean) =>
    onOpenChange ? onOpenChange(v) : setOwnOpen(v);
  return (
    <Popover open={isOpen} onOpenChange={setOpen}>
      {variant === "text" ? (
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1 rounded-full border border-line bg-panel px-2.5 text-sm text-foreground hover:bg-subtle shell-phone:min-h-11"
            aria-label="Filter in this group"
          >
            <Plus size={ICON} /> Filter
          </button>
        </PopoverTrigger>
      ) : variant === "plus" ? (
        <PopoverTrigger asChild>
          <button
            type="button"
            className="grid size-7 flex-none place-items-center rounded-full text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11 shell-phone:min-w-11"
            aria-label="Add another filter"
            title="Add filter (F)"
          >
            <Plus size={ICON} />
          </button>
        </PopoverTrigger>
      ) : (
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex size-7 flex-none items-center justify-center rounded-full border border-line bg-panel text-sm text-foreground hover:bg-subtle aria-expanded:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11"
            aria-label="Add filter"
            title="Filter (F)"
            data-filter-button=""
          >
            <ListFilter size={16} />
          </button>
        </PopoverTrigger>
      )}
      <PopoverContent
        align={variant === "icon" ? "end" : "start"}
        side="bottom"
        sideOffset={4}
        className={panelClass}
        data-my-issue-popover=""
        aria-label="Task filters"
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        {isOpen && (
          <FilterMenu
            root={root}
            onChange={onChange}
            catalog={catalog}
            groupId={groupId || root.id}
            onAdvanced={
              onAdvanced
                ? () => {
                    setOpen(false);
                    onAdvanced();
                  }
                : undefined
            }
            onDone={() => setOpen(false)}
          />
        )}
      </PopoverContent>
    </Popover>
  );
}

const segmentClass =
  "inline-flex h-7 min-w-0 max-w-48 items-center gap-1 border-r border-line px-2 text-sm text-foreground hover:bg-subtle shell-phone:min-h-11";

function Chip({
  chip,
  root,
  onChange,
  catalog,
}: {
  chip: FilterChip;
  root: FilterGroup;
  onChange: (root: FilterGroup) => void;
  catalog: FilterCatalog;
}) {
  const [opOpen, setOpOpen] = useState(false);
  const [valuesOpen, setValuesOpen] = useState(false);
  const spec = fieldSpec(chip.field, catalog.fields);
  const icon = catalog.menu.find(
    (m) => m.field === chip.field || m.fields?.includes(chip.field),
  )?.icon;
  const remove = (
    <button
      type="button"
      className="grid h-7 w-7 flex-none place-items-center text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11"
      aria-label={`Remove ${spec.label} filter`}
      onClick={() => onChange(removeNode(root, chip.id))}
    >
      <X size={12} />
    </button>
  );
  if (chip.field === "raw") {
    const [key, op, value] = rawLabel(chip);
    return (
      <span
        className="inline-flex max-w-full items-stretch overflow-hidden rounded-md border border-line bg-panel"
        data-filter-chip=""
        title="This filter was saved by another client; it applies as written."
      >
        <span className={segmentClass}>{key}</span>
        <span className={`${segmentClass} text-muted`}>{op}</span>
        <span className={`${segmentClass} truncate`}>{value}</span>
        {remove}
      </span>
    );
  }
  const ops = chipOps(spec, chip.values);
  const currentOp = effectiveOp(spec, chip);
  return (
    <span
      className="inline-flex max-w-full items-stretch overflow-hidden rounded-md border border-line bg-panel"
      data-filter-chip=""
      data-field={chip.field}
    >
      <span className={`${segmentClass} hover:bg-transparent`}>
        {icon && (
          <span className="text-muted" aria-hidden="true">
            {icon}
          </span>
        )}
        {spec.label}
      </span>
      <Popover open={opOpen} onOpenChange={setOpOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={`${segmentClass} text-muted`}
            aria-label={`${spec.label} operator: ${opLabel(spec, currentOp, chip.values.length)}`}
            disabled={ops.length < 2}
          >
            {opLabel(spec, currentOp, chip.values.length)}
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="z-50 w-48 rounded-lg border border-line bg-panel p-1 text-sm shadow-lg"
        >
          <div role="menu" aria-label={`${spec.label} operators`}>
            {ops.map((op) => (
              <button
                key={op}
                type="button"
                role="menuitemradio"
                aria-checked={op === currentOp}
                className={itemClass}
                onClick={() => {
                  onChange(
                    updateNode(
                      root,
                      chip.id,
                      (n) => ({ ...n, op }) as FilterChip,
                    ),
                  );
                  setOpOpen(false);
                }}
              >
                <span
                  className="grid size-3.5 place-items-center"
                  aria-hidden="true"
                >
                  {op === currentOp && <Check size={12} />}
                </span>
                {opLabel(spec, op, Math.max(chip.values.length, 1))}
              </button>
            ))}
          </div>
        </PopoverContent>
      </Popover>
      <Popover open={valuesOpen} onOpenChange={setValuesOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={segmentClass}
            aria-label={`${spec.label} values: ${valuesLabel(catalog, chip)}`}
          >
            <span className="truncate">{valuesLabel(catalog, chip)}</span>
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className={panelClass}
          onCloseAutoFocus={(e) => e.preventDefault()}
        >
          {valuesOpen && (
            <FilterMenu
              root={root}
              onChange={onChange}
              catalog={catalog}
              groupId={root.id}
              chip={chip}
              onDone={() => setValuesOpen(false)}
            />
          )}
        </PopoverContent>
      </Popover>
      {remove}
    </span>
  );
}

const MAX_DEPTH = 4;

/** One AND/OR group of the advanced filter: its items joined by a clickable and/or pill. */
function GroupEditor({
  group,
  root,
  onChange,
  catalog,
  depth,
}: {
  group: FilterGroup;
  root: FilterGroup;
  onChange: (root: FilterGroup) => void;
  catalog: FilterCatalog;
  depth: number;
}) {
  const toggleConj = () => {
    const conj = group.conj === "and" ? "or" : "and";
    onChange(
      group.id === root.id
        ? { ...root, conj }
        : updateNode(root, group.id, (g) => ({ ...g, conj }) as FilterGroup),
    );
  };
  return (
    <div
      className={
        depth
          ? "grid gap-1.5 rounded-lg border border-line bg-bg p-2"
          : "grid gap-1.5"
      }
      data-filter-group=""
      data-conj={group.conj}
    >
      {group.items.map((item, i) => (
        <div key={item.id} className="flex min-w-0 items-start gap-1.5">
          {i === 0 ? (
            <span className="inline-flex h-7 w-10 flex-none items-center text-sm text-muted">
              Where
            </span>
          ) : (
            <button
              type="button"
              className="inline-flex h-7 w-10 flex-none items-center justify-center rounded-md border border-line bg-panel text-sm text-foreground hover:bg-subtle shell-phone:min-h-11"
              aria-label={`Match ${group.conj === "and" ? "all" : "any"} filters in this group; switch to ${group.conj === "and" ? "or" : "and"}`}
              onClick={toggleConj}
              data-conj-toggle=""
            >
              {group.conj}
            </button>
          )}
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
            {item.type === "chip" ? (
              <Chip
                chip={item}
                root={root}
                onChange={onChange}
                catalog={catalog}
              />
            ) : (
              <div className="min-w-0 flex-1">
                <GroupEditor
                  group={item}
                  root={root}
                  onChange={onChange}
                  catalog={catalog}
                  depth={depth + 1}
                />
              </div>
            )}
          </div>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-1.5">
        <AddFilterButton
          root={root}
          onChange={onChange}
          catalog={catalog}
          groupId={group.id}
          variant="text"
        />
        {depth < MAX_DEPTH && (
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-sm text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11"
            onClick={() =>
              onChange(
                appendTo(root, group.id, {
                  ...emptyGroup(group.conj === "and" ? "or" : "and"),
                  items: [],
                }),
              )
            }
          >
            <Plus size={ICON} /> Add filter group
          </button>
        )}
        {depth > 0 && (
          <button
            type="button"
            className="ml-auto inline-flex h-7 items-center rounded-full px-2 text-sm text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11"
            onClick={() => onChange(removeNode(root, group.id))}
          >
            Remove group
          </button>
        )}
      </div>
    </div>
  );
}

/** The chip row under the tabs. Renders nothing while no filter is set and the advanced editor is closed. */
export function FilterChips({
  root,
  onChange,
  catalog,
  advanced,
  onAdvancedChange,
  hideClear = false,
}: {
  root: FilterGroup;
  onChange: (root: FilterGroup) => void;
  catalog: FilterCatalog;
  advanced: boolean;
  onAdvancedChange: (advanced: boolean) => void;
  /** The row has its own "Clear" (a saved view's unsaved-changes bar). */
  hideClear?: boolean;
}) {
  const count = chipCount(root);
  const showAdvanced = advanced || !isSimple(root);
  if (!count && !advanced) return null;
  const clear = () => {
    onChange(emptyGroup());
    onAdvancedChange(false);
  };
  return (
    <div className="grid min-w-0 gap-2" data-filter-bar="">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {showAdvanced ? (
          <span
            className="inline-flex items-stretch overflow-hidden rounded-md border border-line bg-panel"
            data-filter-chip="advanced"
          >
            <button
              type="button"
              className={segmentClass}
              aria-expanded={advanced}
              onClick={() => onAdvancedChange(!advanced)}
            >
              <ListFilter size={ICON} className="text-muted" /> Advanced filter
              {count > 0 && <span className="text-xs text-muted">{count}</span>}
            </button>
            <button
              type="button"
              className="grid h-7 w-7 place-items-center text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11"
              aria-label="Clear advanced filter"
              onClick={clear}
            >
              <X size={12} />
            </button>
          </span>
        ) : (
          root.items.map(
            (item) =>
              item.type === "chip" && (
                <Chip
                  key={item.id}
                  chip={item}
                  root={root}
                  onChange={onChange}
                  catalog={catalog}
                />
              ),
          )
        )}
        {!showAdvanced && (
          <AddFilterButton
            root={root}
            onChange={onChange}
            catalog={catalog}
            variant="plus"
            onAdvanced={() => onAdvancedChange(true)}
          />
        )}
        {count > 0 && !hideClear && (
          <button
            type="button"
            className="h-7 rounded-full px-2 text-sm text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11"
            onClick={clear}
          >
            Clear
          </button>
        )}
      </div>
      {showAdvanced && advanced && (
        <div
          className="max-w-2xl rounded-lg border border-line bg-panel p-3 shadow-sm"
          data-advanced-filter=""
        >
          <GroupEditor
            group={root}
            root={root}
            onChange={onChange}
            catalog={catalog}
            depth={0}
          />
        </div>
      )}
    </div>
  );
}
