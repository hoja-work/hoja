/* Words for filters: a chip's values as the bar shows them, and the name a view saved from a
 * filtered list starts with. Pure (type imports are erased) so the node test script runs it.
 */
import type React from "react";
import {
  describeDate,
  fieldSpec,
  isSimple,
  opLabel,
  RELATION_VALUES,
  type FieldSpec,
  type FilterChip,
  type FilterGroup,
  type FilterValue,
} from "./filterModel";

export type ValueOption = {
  value: FilterValue;
  label: string;
  icon?: React.ReactNode;
  disabled?: string;
};

export type MenuEntry = {
  key: string;
  label: string;
  icon: React.ReactNode;
  /** A category that is one field. */
  field?: string;
  /** A category that opens a list of fields (Dates, Content). */
  fields?: string[];
  /** Shown but not selectable, with the reason. */
  disabled?: string;
};

export type FilterCatalog = {
  fields: FieldSpec[];
  menu: MenuEntry[];
  /** The values a field offers, in menu order. */
  options: (field: string) => ValueOption[];
  /** How many rows a value would select; undefined hides the count. */
  count?: (field: string, value: FilterValue) => number | undefined;
  /** Plain-language filters (viewFilterSuggestion): text in, filters for the chips out. */
  suggest?: (
    prompt: string,
  ) => Promise<{ filters: Record<string, unknown> | null; message?: string | null }>;
};

/* ------------------------------------------------------------ labels */

export function valueLabel(
  catalog: FilterCatalog,
  field: string,
  value: FilterValue,
): string {
  const spec = fieldSpec(field, catalog.fields);
  if (spec.type === "date")
    return value === null
      ? `No ${spec.label.toLowerCase()}`
      : describeDate(value);
  if (spec.type === "relation")
    return (
      RELATION_VALUES.find((r) => r.value === value)?.label || String(value)
    );
  if (spec.type === "text" || spec.type === "content")
    return `“${String(value)}”`;
  const found = catalog.options(field).find((o) => o.value === value);
  if (found) return found.label;
  if (value === "me") return "Current user";
  if (value === null) return `No ${spec.label.toLowerCase()}`;
  return String(value);
}

export function valuesLabel(catalog: FilterCatalog, chip: FilterChip): string {
  const spec = fieldSpec(chip.field, catalog.fields);
  if (chip.values.length <= 2)
    return chip.values
      .map((v) => valueLabel(catalog, chip.field, v))
      .join(", ");
  return `${chip.values.length} ${spec.label.toLowerCase()}`;
}

/** A name for a view saved from a filtered list, after Linear's smart naming: the picked values
 * in a few words ("Urgent, High · Assigned to Current user"), or `fallback` for a filter that
 * does not read as a short phrase (advanced groups, more than three chips). */
export function suggestViewName(
  root: FilterGroup,
  catalog: FilterCatalog,
  fallback = "Filtered tasks",
): string {
  if (!isSimple(root) || !root.items.length || root.items.length > 3)
    return fallback;
  const parts: string[] = [];
  for (const item of root.items) {
    if (item.type !== "chip" || item.field === "raw") return fallback;
    const spec = fieldSpec(item.field, catalog.fields);
    const values = valuesLabel(catalog, item);
    const negative = item.op === "isNot" || item.op === "includesNone";
    if (spec.type === "ref" && item.field === "assignee")
      parts.push(`${negative ? "Not assigned" : "Assigned"} to ${values}`);
    else if (spec.type === "ref" && item.field === "creator")
      parts.push(`${negative ? "Not created" : "Created"} by ${values}`);
    else if (
      !negative &&
      [
        "status",
        "statusType",
        "priority",
        "labels",
        "project",
        "cycle",
        "team",
      ].includes(item.field)
    )
      parts.push(values);
    else
      parts.push(
        `${spec.label} ${opLabel(spec, item.op, item.values.length)} ${values}`,
      );
  }
  const name = parts.join(" · ");
  return name.length > 80 ? `${name.slice(0, 79)}…` : name;
}

/** `name`, or `name 2`, `name 3`… when `taken` (compared case-insensitively) already has it. */
export function uniqueName(name: string, taken: readonly string[]): string {
  const used = new Set(taken.map((t) => t.trim().toLowerCase()));
  if (!used.has(name.trim().toLowerCase())) return name;
  for (let n = 2; ; n += 1) {
    const next = `${name} ${n}`;
    if (!used.has(next.toLowerCase())) return next;
  }
}

export type ViewIdentity = { name: string; description: string; icon: string };

/** Linear's smart naming for an empty new-view draft: after the first chip, a name, a one-line
 * description and an icon ("Priority is Urgent" → "Urgent tasks", "Tasks with urgent priority",
 * the urgent glyph). Null when there is no chip to read, or the first chip does not read as a
 * phrase (a negation, a raw predicate, an advanced group). `noun` is the plural the page uses. */
export function suggestViewIdentity(
  root: FilterGroup,
  catalog: FilterCatalog,
  noun = "tasks",
): ViewIdentity | null {
  const first = root.items[0];
  if (!first || first.type !== "chip" || first.field === "raw") return null;
  if (!first.values.length) return null;
  const spec = fieldSpec(first.field, catalog.fields);
  const values = valuesLabel(catalog, first);
  const Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
  const negative = first.op === "isNot" || first.op === "includesNone";
  const lower = values.toLowerCase();
  const clip = (s: string) => (s.length > 80 ? `${s.slice(0, 79)}…` : s);
  const make = (name: string, description: string, icon: string) => ({
    name: clip(name),
    description: clip(description),
    icon,
  });
  if (negative)
    return make(
      `${Noun} without ${values}`,
      `${Noun} where ${spec.label.toLowerCase()} ${opLabel(spec, first.op, first.values.length)} ${values}`,
      "Funnel",
    );
  switch (first.field) {
    case "priority":
      return make(
        `${values} ${noun}`,
        `${Noun} with ${lower} priority`,
        first.values.length === 1 && first.values[0] === 1
          ? "Siren"
          : "SignalHigh",
      );
    case "status":
    case "statusType":
      return make(`${values} ${noun}`, `${Noun} in ${values}`, "CircleDot");
    case "labels":
      return make(`${values} ${noun}`, `${Noun} labeled ${values}`, "Tag");
    case "project":
      return make(`${values} ${noun}`, `${Noun} in the ${values} project`, "Box");
    case "team":
      return make(`${values} ${noun}`, `${Noun} of the ${values} team`, "Users");
    case "cycle":
      return make(`${values} ${noun}`, `${Noun} in ${values}`, "RefreshCw");
    case "assignee":
      return make(
        first.values.includes("me") && first.values.length === 1
          ? `Assigned to me`
          : `${Noun} assigned to ${values}`,
        `${Noun} assigned to ${values}`,
        "UserRound",
      );
    case "creator":
      return make(
        first.values.includes("me") && first.values.length === 1
          ? `Created by me`
          : `${Noun} created by ${values}`,
        `${Noun} created by ${values}`,
        "UserRound",
      );
    default:
      return make(
        `${spec.label} ${opLabel(spec, first.op, first.values.length)} ${values}`,
        `${Noun} where ${spec.label.toLowerCase()} ${opLabel(spec, first.op, first.values.length)} ${values}`,
        spec.type === "date" ? "Calendar" : "Funnel",
      );
  }
}
