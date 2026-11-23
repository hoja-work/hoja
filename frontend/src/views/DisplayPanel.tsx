/* The Display panel (Shift V) every list shares, after Linear's: layout, grouping, sub-grouping
 * (rows on a board), ordering and its direction, the completed window, sub-issues, empty
 * groups and the property chips, with "Reset" and "Set default for everyone" in the footer.
 * It edits a display document (`displayModel`); the caller stores it through
 * `useViewPreferences`, which also decides the dot on the trigger.
 */
import React from "react";
import {
  ArrowDownWideNarrow,
  ArrowUpNarrowWide,
  ChartGantt,
  LayoutList,
  SlidersHorizontal,
  SquareKanban,
} from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import {
  COMPLETED_WINDOWS,
  ISSUE_GROUPINGS,
  INITIATIVE_GROUPINGS,
  INITIATIVE_ORDERINGS,
  ISSUE_ORDERINGS,
  ISSUE_PROPERTIES,
  NESTED_OPTIONS,
  PROJECT_GROUPINGS,
  PROJECT_ORDERINGS,
  PROJECT_PROPERTIES,
  PROJECT_WINDOWS,
  type DisplayDoc,
  type EffectiveDisplay,
  type Layout,
  toggleProperty,
  type Option,
} from "./displayModel";

export type DisplayVariant = "issue" | "project" | "initiative";

const rowClass =
  "flex min-h-8 items-center justify-between gap-3 text-sm text-muted";
const selectClass =
  "w-36 max-w-full rounded-md border border-line bg-panel px-2 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-accent max-md:text-base shell-phone:min-h-11";

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Option[];
  onChange: (v: string) => void;
}) {
  return (
    <label className={rowClass}>
      <span>{label}</span>
      <select
        aria-label={label}
        className={selectClass}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={Boolean(o.disabled)}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Switch({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className={rowClass}>
      <span>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        className="relative h-4 w-7 flex-none rounded-full bg-line transition-colors duration-150 ease-out aria-checked:bg-accent shell-phone:min-h-6"
        onClick={() => onChange(!checked)}
      >
        <span
          className={`absolute top-0.5 left-0.5 size-3 rounded-full bg-panel shadow-sm transition-transform duration-150 ease-out ${checked ? "translate-x-3" : ""}`}
        />
      </button>
    </div>
  );
}

const LAYOUT_ICON: Record<Layout, React.ReactNode> = {
  list: <LayoutList size={15} />,
  board: <SquareKanban size={15} />,
  timeline: <ChartGantt size={15} />,
};

export function DisplayPanel({
  display,
  onChange,
  variant = "issue",
  layouts = ["list", "board"],
  showCompleted = true,
  hasOverrides,
  onReset,
  onSetDefault,
  canSetDefault = true,
  labelGroups = [],
}: {
  display: EffectiveDisplay;
  onChange: (patch: DisplayDoc) => void;
  variant?: DisplayVariant;
  layouts?: Layout[];
  showCompleted?: boolean;
  hasOverrides: boolean;
  onReset: () => void;
  onSetDefault?: () => void;
  canSetDefault?: boolean;
  /** The workspace's label groups, for grouping by one (Linear's "Label group"). */
  labelGroups?: { id: string; name: string }[];
}) {
  const issue = variant === "issue";
  const initiative = variant === "initiative";
  const groupings = issue
    ? ISSUE_GROUPINGS
    : initiative
      ? INITIATIVE_GROUPINGS
      : PROJECT_GROUPINGS;
  const orderings = issue
    ? ISSUE_ORDERINGS
    : initiative
      ? INITIATIVE_ORDERINGS
      : PROJECT_ORDERINGS;
  const properties = issue ? ISSUE_PROPERTIES : PROJECT_PROPERTIES;
  const board = display.layout === "board";
  const visible = new Set(display.visibleProperties);
  return (
    <div className="grid gap-3 text-sm" data-display-panel="">
      {layouts.length > 1 && (
        <div
          className="grid gap-1.5"
          style={{
            gridTemplateColumns: `repeat(${layouts.length}, minmax(0, 1fr))`,
          }}
          role="radiogroup"
          aria-label="Layout"
        >
          {layouts.map((layout) => (
            <button
              key={layout}
              type="button"
              role="radio"
              aria-checked={display.layout === layout}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-line bg-panel text-sm text-foreground capitalize hover:bg-subtle aria-checked:bg-subtle aria-checked:font-medium aria-checked:inset-shadow-sm shell-phone:min-h-11"
              onClick={() => onChange({ layout })}
            >
              {LAYOUT_ICON[layout]}
              {layout}
            </button>
          ))}
        </div>
      )}
      <div className="grid gap-1">
        <Select
          label={board ? "Columns" : "Grouping"}
          value={display.grouping}
          options={groupings}
          onChange={(grouping) => onChange({ grouping })}
        />
        {issue && display.grouping === "labelGroup" && (
          <Select
            label="Label group"
            value={
              display.groupingLabelGroupId ||
              [...labelGroups].sort((a, b) => a.name.localeCompare(b.name))[0]
                ?.id ||
              ""
            }
            options={
              labelGroups.length
                ? labelGroups.map((g) => ({ value: g.id, label: g.name }))
                : [
                    {
                      value: "",
                      label: "No label groups",
                      disabled: "Create a label group in Settings › Labels.",
                    },
                  ]
            }
            onChange={(groupingLabelGroupId) =>
              onChange({ groupingLabelGroupId })
            }
          />
        )}
        {!initiative && (
        <Select
          label={board ? "Rows" : "Sub-grouping"}
          value={display.subGrouping}
          options={groupings.map((o) =>
            o.value !== "none" && o.value === display.grouping
              ? { ...o, disabled: "Already the grouping" }
              : o,
          )}
          onChange={(subGrouping) => onChange({ subGrouping })}
        />
        )}
        {initiative && (
          <>
            <Switch
              label="Nest sub-initiatives"
              checked={display.nested !== false}
              onChange={(v) => onChange({ nested: v })}
            />
            <Switch
              label="Show summaries"
              checked={display.summary !== false}
              onChange={(v) => onChange({ summary: v })}
            />
          </>
        )}
        <div className={rowClass}>
          <span>Ordering</span>
          <span className="flex items-center gap-1">
            <button
              type="button"
              className="grid size-7 place-items-center rounded-md border border-line bg-panel text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11 shell-phone:min-w-11"
              aria-label={
                display.reverse
                  ? "Ordering reversed; switch to normal order"
                  : "Reverse ordering"
              }
              aria-pressed={display.reverse}
              title="Reverse ordering"
              onClick={() => onChange({ reverse: !display.reverse })}
            >
              {display.reverse ? (
                <ArrowUpNarrowWide size={14} />
              ) : (
                <ArrowDownWideNarrow size={14} />
              )}
            </button>
            <select
              className={selectClass}
              aria-label="Ordering"
              value={display.ordering}
              onChange={(e) => onChange({ ordering: e.target.value })}
            >
              {orderings.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </span>
        </div>
        {issue && (
          <Switch
            label="Order completed by recency"
            checked={display.orderCompletedByRecency}
            onChange={(v) => onChange({ orderCompletedByRecency: v })}
          />
        )}
      </div>
      <div className="grid gap-1 border-t border-line pt-3">
        {showCompleted && (
          <Select
            label={issue ? "Completed tasks" : "Closed projects"}
            value={display.completedWindow}
            options={issue ? COMPLETED_WINDOWS : PROJECT_WINDOWS}
            onChange={(v) =>
              onChange({ completedWindow: v as DisplayDoc["completedWindow"] })
            }
          />
        )}
        {issue && (
          <Switch
            label="Show sub-tasks"
            checked={display.showSubIssues}
            onChange={(v) => onChange({ showSubIssues: v })}
          />
        )}
      </div>
      {!initiative && (
      <div className="grid gap-1 border-t border-line pt-3">
        <span className="text-sm font-medium text-foreground">
          {board ? "Board options" : "List options"}
        </span>
        {issue && !board && (
          <Select
            label="Nested sub-tasks"
            value={display.nestedSubIssues}
            options={NESTED_OPTIONS}
            onChange={(v) =>
              onChange({ nestedSubIssues: v as DisplayDoc["nestedSubIssues"] })
            }
          />
        )}
        <Switch
          label={board ? "Show empty columns" : "Show empty groups"}
          checked={display.showEmptyGroups}
          onChange={(v) => onChange({ showEmptyGroups: v })}
        />
      </div>
      )}
      {!initiative && (
      <div className="grid gap-2">
        <span className="text-sm text-muted">Display properties</span>
        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label="Display properties"
        >
          {properties.map((p) => (
            <button
              key={p.value}
              type="button"
              aria-pressed={visible.has(p.value)}
              disabled={Boolean(p.disabled)}
              title={p.disabled}
              className="rounded-full border border-line px-2.5 py-0.5 text-sm text-muted hover:bg-subtle hover:transition-none aria-pressed:bg-subtle aria-pressed:text-foreground aria-pressed:transition-colors aria-pressed:duration-150 aria-pressed:ease-out disabled:cursor-not-allowed disabled:opacity-50 shell-phone:min-h-11"
              onClick={() => {
                onChange({
                  visibleProperties: toggleProperty(
                    display.visibleProperties,
                    properties,
                    p.value,
                  ),
                });
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      )}
      <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
        <button
          type="button"
          className="rounded-md px-2 py-1 text-sm text-muted hover:bg-subtle hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 shell-phone:min-h-11"
          disabled={!hasOverrides}
          onClick={onReset}
        >
          Reset
        </button>
        {onSetDefault && (
          <button
            type="button"
            className="rounded-md px-2 py-1 text-sm text-accent hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 shell-phone:min-h-11"
            disabled={!hasOverrides || !canSetDefault}
            title={
              canSetDefault
                ? "Everyone without their own options sees this display"
                : "Only the owner or an admin can change the default"
            }
            onClick={onSetDefault}
          >
            Set default for everyone
          </button>
        )}
      </div>
    </div>
  );
}

/** The toolbar button with the dot that marks options differing from the default. */
export function DisplayButton({
  open,
  onOpenChange,
  hasOverrides,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  hasOverrides: boolean;
  children: React.ReactNode;
}) {
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="relative inline-flex size-7 flex-none items-center justify-center rounded-full border border-line bg-panel text-sm text-foreground hover:bg-subtle aria-expanded:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11"
          aria-label="Display options"
          title={
            hasOverrides
              ? "Display (Shift V) · changed from the default"
              : "Display (Shift V)"
          }
          data-display-button=""
        >
          <SlidersHorizontal size={16} />
          {hasOverrides && (
            <span
              className="absolute top-0 right-0 size-2 rounded-full bg-accent ring-2 ring-panel"
              data-display-dot=""
              aria-hidden="true"
            />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={4}
        className="z-50 grid max-h-(--radix-popover-content-available-height) w-80 max-w-full content-start overflow-y-auto overscroll-contain rounded-lg border border-line bg-panel p-3 text-foreground shadow-lg"
        data-my-issue-popover=""
        aria-label="Display options"
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}
