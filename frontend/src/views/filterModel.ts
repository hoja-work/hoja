/* The filter model shared by every task list (team Tasks, My issues, project tasks, saved views).
 *
 * The server stores a view's filters in its grammar (docs/views.md): a JSON object whose keys
 * are properties (`{"priority": {"isEitherOf": [1, 2]}}`), plus `and` / `or` lists. The browser
 * edits the same selection as chips ("Priority | is either of | Urgent, High | ×") arranged in
 * nested AND/OR groups. `filterTreeToChips` and `chipsToFilterTree` are the only translation
 * between the two, so a view saved from a chip bar and reopened later reads back exactly.
 *
 * Pure and import-free on purpose: `scripts/test-view-filters.mjs` runs it under
 * `node --experimental-strip-types` without a bundler.
 */

export type FilterValue = string | number | boolean | null;
/** A filter document in the server grammar. */
export type FilterTree = Record<string, unknown>;

export type ChipOp =
  | "is"
  | "isNot"
  | "includesAny"
  | "includesAll"
  | "includesNone"
  | "before"
  | "after"
  | "onOrBefore"
  | "onOrAfter"
  | "contains"
  | "gt"
  | "gte"
  | "lt"
  | "lte";

export type FieldType =
  | "ref"
  | "enum"
  | "labels"
  | "number"
  | "date"
  | "text"
  | "relation"
  | "content"
  | "raw";

export type FilterChip = {
  type: "chip";
  id: string;
  field: string;
  op: ChipOp;
  values: FilterValue[];
  /** Only for `field: "raw"`: a predicate this model does not edit, kept verbatim. */
  raw?: { key: string; value: unknown };
};
export type FilterGroup = {
  type: "group";
  id: string;
  conj: "and" | "or";
  items: FilterNode[];
};
export type FilterNode = FilterChip | FilterGroup;

export type FieldSpec = {
  key: string;
  label: string;
  type: FieldType;
  /** The grammar property the chip writes. */
  server: string;
  ops: ChipOp[];
  /** Accepts `null` ("No assignee", "No project", "No due date"). */
  nullable?: boolean;
  /** Date fields: whether relative presets look back ("1 week ago") or ahead ("1 week from now"). */
  direction?: "past" | "future";
  /** Chips of this field hold a single value. */
  single?: boolean;
};

const IDENTITY: ChipOp[] = ["is", "isNot"];
const DATE_OPS: ChipOp[] = ["before", "after", "onOrBefore", "onOrAfter", "is"];

/** Every field a task chip can address, in the order the filter menu lists them. */
export const ISSUE_FIELDS: FieldSpec[] = [
  {
    key: "status",
    label: "Status",
    type: "ref",
    server: "state",
    ops: IDENTITY,
  },
  {
    key: "statusType",
    label: "Status type",
    type: "enum",
    server: "stateType",
    ops: IDENTITY,
  },
  {
    key: "assignee",
    label: "Assignee",
    type: "ref",
    server: "assignee",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "creator",
    label: "Creator",
    type: "ref",
    server: "creator",
    ops: IDENTITY,
  },
  {
    key: "priority",
    label: "Priority",
    type: "enum",
    server: "priority",
    ops: IDENTITY,
  },
  {
    key: "labels",
    label: "Labels",
    type: "labels",
    server: "labels",
    ops: ["includesAny", "includesAll", "includesNone"],
    nullable: true,
  },
  {
    key: "relation",
    label: "Relations",
    type: "relation",
    server: "",
    ops: IDENTITY,
    single: true,
  },
  {
    key: "dueDate",
    label: "Due date",
    type: "date",
    server: "dueDate",
    ops: DATE_OPS,
    nullable: true,
    direction: "future",
    single: true,
  },
  {
    key: "createdAt",
    label: "Created date",
    type: "date",
    server: "createdAt",
    ops: DATE_OPS,
    direction: "past",
    single: true,
  },
  {
    key: "updatedAt",
    label: "Updated date",
    type: "date",
    server: "updatedAt",
    ops: DATE_OPS,
    direction: "past",
    single: true,
  },
  {
    key: "startedAt",
    label: "Started date",
    type: "date",
    server: "startedAt",
    ops: DATE_OPS,
    nullable: true,
    direction: "past",
    single: true,
  },
  {
    key: "completedAt",
    label: "Completed date",
    type: "date",
    server: "completedAt",
    ops: DATE_OPS,
    nullable: true,
    direction: "past",
    single: true,
  },
  {
    key: "canceledAt",
    label: "Canceled date",
    type: "date",
    server: "canceledAt",
    ops: DATE_OPS,
    nullable: true,
    direction: "past",
    single: true,
  },
  {
    key: "project",
    label: "Project",
    type: "ref",
    server: "project",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "cycle",
    label: "Cycle",
    type: "ref",
    server: "cycle",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "estimate",
    label: "Estimate",
    type: "number",
    server: "estimate",
    ops: ["is", "isNot", "gt", "gte", "lt", "lte"],
    nullable: true,
  },
  {
    key: "subscriber",
    label: "Subscribers",
    type: "ref",
    server: "subscriber",
    ops: IDENTITY,
  },
  { key: "team", label: "Team", type: "ref", server: "team", ops: IDENTITY },
  {
    key: "content",
    label: "Title or description",
    type: "content",
    server: "",
    ops: ["contains"],
    single: true,
  },
  {
    key: "title",
    label: "Title",
    type: "text",
    server: "title",
    ops: ["contains", "is", "isNot"],
    single: true,
  },
  {
    key: "description",
    label: "Description",
    type: "text",
    server: "description",
    ops: ["contains", "is", "isNot"],
    single: true,
  },
];

/** Project-view fields, the subset of Linear's project filter Hoja executes. */
export const PROJECT_FIELDS: FieldSpec[] = [
  {
    key: "status",
    label: "Status",
    type: "ref",
    server: "status",
    ops: IDENTITY,
  },
  {
    key: "lead",
    label: "Lead",
    type: "ref",
    server: "lead",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "members",
    label: "Members",
    type: "ref",
    server: "members",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "creator",
    label: "Creator",
    type: "ref",
    server: "creator",
    ops: IDENTITY,
  },
  {
    key: "health",
    label: "Health",
    type: "enum",
    server: "health",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "team",
    label: "Lead team",
    type: "ref",
    server: "team",
    ops: IDENTITY,
  },
  {
    key: "startDate",
    label: "Start date",
    type: "date",
    server: "startDate",
    ops: DATE_OPS,
    nullable: true,
    direction: "future",
    single: true,
  },
  {
    key: "targetDate",
    label: "Target date",
    type: "date",
    server: "targetDate",
    ops: DATE_OPS,
    nullable: true,
    direction: "future",
    single: true,
  },
  {
    key: "initiative",
    label: "Initiative",
    type: "ref",
    server: "initiative",
    ops: IDENTITY,
    nullable: true,
  },
  {
    key: "name",
    label: "Name",
    type: "text",
    server: "name",
    ops: ["contains", "is", "isNot"],
    single: true,
  },
  {
    key: "summary",
    label: "Summary",
    type: "text",
    server: "summary",
    ops: ["contains", "is", "isNot"],
    single: true,
  },
];

export const RAW_FIELD: FieldSpec = {
  key: "raw",
  label: "Filter",
  type: "raw",
  server: "",
  ops: ["is"],
};

export function fieldSpec(
  key: string,
  fields: FieldSpec[] = ISSUE_FIELDS,
): FieldSpec {
  return fields.find((f) => f.key === key) || RAW_FIELD;
}

/** Relation chips: the value names one structural predicate. */
export const RELATION_VALUES = [
  { value: "parent", label: "Parent tasks", hint: "Has sub-tasks" },
  { value: "sub", label: "Sub-tasks", hint: "Has a parent" },
  { value: "top", label: "Top-level tasks", hint: "No parent" },
] as const;

/* ------------------------------------------------------------------ ids */

let seq = 0;
export function nextId(prefix = "f"): string {
  seq += 1;
  return `${prefix}${seq.toString(36)}`;
}

export function emptyGroup(conj: "and" | "or" = "and"): FilterGroup {
  return { type: "group", id: nextId("g"), conj, items: [] };
}

export function newChip(
  field: string,
  values: FilterValue[] = [],
  op?: ChipOp,
  fields: FieldSpec[] = ISSUE_FIELDS,
): FilterChip {
  const spec = fieldSpec(field, fields);
  return {
    type: "chip",
    id: nextId(),
    field,
    op: op || defaultOp(spec),
    values,
  };
}

export function defaultOp(spec: FieldSpec): ChipOp {
  if (spec.type === "date")
    return spec.direction === "future" ? "before" : "after";
  return spec.ops[0];
}

/* An empty value ("No estimate", "No labels", "No due date") only makes sense with an
 * identity operator: "estimate > none" or "has all of: no labels" is not a filter. */
const EMPTY_VALUE_OPS: ChipOp[] = ["is", "isNot", "includesAny", "includesNone"];

/** The operators a chip with these values may use: every operator of its field, only
 * the identity ones while one of the values is the empty value, and only "is" for
 * "overdue". */
export function chipOps(spec: FieldSpec, values: FilterValue[]): ChipOp[] {
  // "Overdue" is a moment, not a range: the server only accepts it with "is".
  if (spec.type === "date" && values.includes("overdue") && spec.ops.includes("is"))
    return ["is"];
  if (!values.some((v) => v === null)) return spec.ops;
  const ops = spec.ops.filter((op) => EMPTY_VALUE_OPS.includes(op));
  return ops.length ? ops : spec.ops;
}

/** The operator a chip is written with: its own, unless its values rule it out. */
export function effectiveOp(spec: FieldSpec, chip: FilterChip): ChipOp {
  const ops = chipOps(spec, chip.values);
  return ops.includes(chip.op) ? chip.op : ops[0];
}

/* ----------------------------------------------------------- chips → tree */

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

const NUMBER_OPS: Record<string, string> = {
  gt: "isGreaterThan",
  gte: "isGreaterThanOrEqual",
  lt: "isLessThan",
  lte: "isLessThanOrEqual",
};

/** One chip as a grammar predicate, or null while the chip has no value yet. */
export function chipToPredicate(
  chip: FilterChip,
  fields: FieldSpec[] = ISSUE_FIELDS,
): FilterTree | null {
  if (chip.field === "raw")
    return chip.raw ? { [chip.raw.key]: chip.raw.value } : null;
  const spec = fieldSpec(chip.field, fields);
  const values = chip.values.filter((v) => v !== undefined && v !== "");
  if (!values.length) return null;
  const first = values[0];
  const op = effectiveOp(spec, chip);
  switch (spec.type) {
    case "relation": {
      const positive = op !== "isNot";
      if (first === "parent") return { hasSubIssues: { is: positive } };
      if (first === "sub") return { isSubIssue: { is: positive } };
      if (first === "top") return { parent: { isNull: positive } };
      return null;
    }
    case "content": {
      const text = String(first);
      return {
        or: [
          { title: { contains: text } },
          { description: { contains: text } },
        ],
      };
    }
    case "text":
      return { [spec.server]: { [op]: String(first) } };
    case "date":
      return { [spec.server]: { [op]: first } };
    case "labels":
      return { [spec.server]: { [op]: values } };
    case "number":
      if (NUMBER_OPS[op])
        return { [spec.server]: { [NUMBER_OPS[op]]: first } };
    // falls through: is / isNot behave like an identity property
    default: {
      const negative = op === "isNot";
      if (values.length === 1)
        return { [spec.server]: { [negative ? "isNot" : "is"]: first } };
      return {
        [spec.server]: { [negative ? "isNeitherOf" : "isEitherOf"]: values },
      };
    }
  }
}

/** A chip tree in the server grammar. An empty tree is `{}` ("everything"). */
export function chipsToFilterTree(
  root: FilterNode | null | undefined,
  fields: FieldSpec[] = ISSUE_FIELDS,
): FilterTree {
  if (!root) return {};
  return nodeToTree(root, fields) || {};
}

function nodeToTree(node: FilterNode, fields: FieldSpec[]): FilterTree | null {
  if (node.type === "chip") return chipToPredicate(node, fields);
  const parts = node.items
    .map((item) => nodeToTree(item, fields))
    .filter((p): p is FilterTree => Boolean(p));
  if (!parts.length) return null;
  if (parts.length === 1) return parts[0];
  if (node.conj === "and") {
    // Distinct single-key predicates merge into one object: the compact form a URL carries.
    const keys = parts.map((p) => Object.keys(p));
    const flat = keys.every(
      (k) => k.length === 1 && k[0] !== "and" && k[0] !== "or",
    );
    const unique = new Set(keys.map((k) => k[0])).size === parts.length;
    if (flat && unique) return Object.assign({}, ...parts);
  }
  return { [node.conj]: parts };
}

/* ----------------------------------------------------------- tree → chips */

function specByServer(key: string, fields: FieldSpec[]): FieldSpec | undefined {
  return fields.find(
    (f) => f.server === key && f.type !== "relation" && f.type !== "content",
  );
}

const LABEL_READ: Record<string, ChipOp> = {
  includesAny: "includesAny",
  includesEither: "includesAny",
  is: "includesAny",
  isEitherOf: "includesAny",
  includesAll: "includesAll",
  includesNone: "includesNone",
  includesNeither: "includesNone",
  isNot: "includesNone",
  isNeitherOf: "includesNone",
};
const NUMBER_READ: Record<string, ChipOp> = {
  isGreaterThan: "gt",
  isGreaterThanOrEqual: "gte",
  isLessThan: "lt",
  isLessThanOrEqual: "lte",
};

function raw(key: string, value: unknown): FilterChip {
  return {
    type: "chip",
    id: nextId(),
    field: "raw",
    op: "is",
    values: [],
    raw: { key, value },
  };
}

/** The chips one grammar property reads as. Anything this model cannot edit stays a raw chip,
 * so reading and writing a view never drops part of what it selects. */
function propertyChips(
  key: string,
  ops: unknown,
  fields: FieldSpec[],
): FilterChip[] {
  if (!isObject(ops)) return [raw(key, ops)];
  const entries = Object.entries(ops);
  if (!entries.length) return [];
  const isIssue =
    fields === ISSUE_FIELDS || fields.some((f) => f.key === "relation");
  if (isIssue && entries.length === 1) {
    const [op, value] = entries[0];
    if (
      (key === "hasSubIssues" || key === "isSubIssue") &&
      op === "is" &&
      typeof value === "boolean"
    )
      return [
        {
          type: "chip",
          id: nextId(),
          field: "relation",
          op: value ? "is" : "isNot",
          values: [key === "hasSubIssues" ? "parent" : "sub"],
        },
      ];
    if (key === "parent" && op === "isNull" && typeof value === "boolean")
      return [
        {
          type: "chip",
          id: nextId(),
          field: "relation",
          op: value ? "is" : "isNot",
          values: ["top"],
        },
      ];
  }
  const spec = specByServer(key, fields);
  if (!spec) return [raw(key, ops)];
  const out: FilterChip[] = [];
  for (const [op, value] of entries) {
    const chip = (chipOp: ChipOp, values: FilterValue[]) =>
      out.push({
        type: "chip",
        id: nextId(),
        field: spec.key,
        op: chipOp,
        values,
      });
    const list = Array.isArray(value) ? (value as FilterValue[]) : null;
    if (spec.type === "labels" && LABEL_READ[op])
      chip(LABEL_READ[op], list || [value as FilterValue]);
    else if (
      spec.type === "date" &&
      (DATE_OPS as string[]).includes(op) &&
      !list
    )
      chip(op as ChipOp, [value as FilterValue]);
    else if (
      spec.type === "text" &&
      (op === "contains" || op === "containsIgnoreCase") &&
      typeof value === "string"
    )
      chip("contains", [value]);
    else if (
      spec.type === "text" &&
      (op === "is" || op === "isNot") &&
      typeof value === "string"
    )
      chip(op, [value]);
    else if (
      spec.type === "number" &&
      NUMBER_READ[op] &&
      typeof value === "number"
    )
      chip(NUMBER_READ[op], [value]);
    else if (
      (spec.type === "ref" || spec.type === "enum" || spec.type === "number") &&
      (op === "is" || op === "isNot") &&
      !list
    )
      chip(op, [value as FilterValue]);
    else if (
      (spec.type === "ref" || spec.type === "enum" || spec.type === "number") &&
      (op === "isEitherOf" || op === "isNeitherOf") &&
      list
    )
      chip(op === "isEitherOf" ? "is" : "isNot", list);
    else if (spec.type === "ref" && op === "isMe" && typeof value === "boolean")
      chip(value ? "is" : "isNot", ["me"]);
    else out.push(raw(key, { [op]: value }));
  }
  return out;
}

function contentChip(obj: FilterTree): FilterChip | null {
  const list = obj.or;
  if (
    Object.keys(obj).length !== 1 ||
    !Array.isArray(list) ||
    list.length !== 2
  )
    return null;
  const [a, b] = list as FilterTree[];
  const text = (p: FilterTree, key: string) => {
    const ops = isObject(p) && Object.keys(p).length === 1 ? p[key] : undefined;
    return isObject(ops) &&
      Object.keys(ops).length === 1 &&
      typeof ops.contains === "string"
      ? ops.contains
      : null;
  };
  const t = text(a, "title"),
    d = text(b, "description");
  return t !== null && t === d
    ? {
        type: "chip",
        id: nextId(),
        field: "content",
        op: "contains",
        values: [t],
      }
    : null;
}

function parseGroup(obj: unknown, fields: FieldSpec[]): FilterGroup {
  const group = emptyGroup("and");
  if (!isObject(obj)) return group;
  const keys = Object.keys(obj);
  if (keys.length === 1 && keys[0] === "or" && Array.isArray(obj.or)) {
    const content = fields.some((f) => f.type === "content")
      ? contentChip(obj)
      : null;
    if (content) {
      group.items.push(content);
      return group;
    }
    group.conj = "or";
    group.items = obj.or.map((item) => parseNode(item, fields, "or"));
    return group;
  }
  for (const key of keys) {
    const value = obj[key];
    if ((key === "and" || key === "or") && Array.isArray(value)) {
      if (key === "and")
        for (const item of value)
          group.items.push(
            ...flattenInto(parseNode(item, fields, "and"), "and"),
          );
      else {
        const nested = parseGroup({ or: value }, fields);
        group.items.push(nested.items.length === 1 ? nested.items[0] : nested);
      }
    } else group.items.push(...propertyChips(key, value, fields));
  }
  return group;
}

function flattenInto(node: FilterNode, conj: "and" | "or"): FilterNode[] {
  return node.type === "group" && node.conj === conj ? node.items : [node];
}

function parseNode(
  obj: unknown,
  fields: FieldSpec[],
  parent: "and" | "or",
): FilterNode {
  const group = parseGroup(obj, fields);
  // A single predicate needs no group around it; an `and` object inside an `or` list stays one.
  void parent;
  return group.items.length === 1 ? group.items[0] : group;
}

/** The chip tree a grammar document reads as. The root is always an `and` or `or` group. */
export function filterTreeToChips(
  tree: FilterTree | null | undefined,
  fields: FieldSpec[] = ISSUE_FIELDS,
): FilterGroup {
  return parseGroup(tree || {}, fields);
}

/** True when the tree reads as a flat row of chips ANDed together: the quick-filter bar. */
export function isSimple(root: FilterGroup): boolean {
  return (
    (root.conj === "and" || root.items.length <= 1) &&
    root.items.every((item) => item.type === "chip")
  );
}

export function chipCount(node: FilterNode): number {
  return node.type === "chip"
    ? 1
    : node.items.reduce((n, item) => n + chipCount(item), 0);
}

export function hasFilters(tree: FilterTree | null | undefined): boolean {
  return Boolean(tree && Object.keys(tree).length);
}

/* ------------------------------------------------------------- tree edits */

export function updateNode(
  root: FilterGroup,
  id: string,
  change: (node: FilterNode) => FilterNode | null,
): FilterGroup {
  const walk = (node: FilterNode): FilterNode | null => {
    if (node.id === id) return change(node);
    if (node.type === "chip") return node;
    const items = node.items
      .map(walk)
      .filter((n): n is FilterNode => Boolean(n));
    return { ...node, items };
  };
  const next = walk(root);
  return next && next.type === "group" ? next : emptyGroup(root.conj);
}

export function removeNode(root: FilterGroup, id: string): FilterGroup {
  const pruned = updateNode(root, id, () => null);
  // A group left empty by the removal goes too, except the root.
  const prune = (group: FilterGroup): FilterGroup => ({
    ...group,
    items: group.items
      .map((item) => (item.type === "group" ? prune(item) : item))
      .filter((item) => item.type === "chip" || item.items.length > 0),
  });
  return prune(pruned);
}

export function appendTo(
  root: FilterGroup,
  groupId: string,
  node: FilterNode,
): FilterGroup {
  if (root.id === groupId) return { ...root, items: [...root.items, node] };
  return updateNode(root, groupId, (group) =>
    group.type === "group"
      ? { ...group, items: [...group.items, node] }
      : group,
  );
}

/** Set a chip's values, keeping its operator meaningful: a single-valued field keeps one value,
 * and a chip that lost every value is removed. */
export function setChipValues(
  root: FilterGroup,
  id: string,
  values: FilterValue[],
  fields: FieldSpec[] = ISSUE_FIELDS,
): FilterGroup {
  return values.length
    ? updateNode(root, id, (node) => {
        if (node.type !== "chip") return node;
        const spec = fieldSpec(node.field, fields);
        return { ...node, values: spec.single ? values.slice(-1) : values };
      })
    : removeNode(root, id);
}

/** Toggle one value on the root's chip for `field`, creating the chip when there is none.
 * This is what picking a value in the quick filter menu does. */
export function toggleValue(
  root: FilterGroup,
  field: string,
  value: FilterValue,
  fields: FieldSpec[] = ISSUE_FIELDS,
  op?: ChipOp,
): FilterGroup {
  const spec = fieldSpec(field, fields);
  // A single-valued field (a date, a relation, a text match) has one chip per group: picking
  // another preset replaces its value and operator ("after 1 week ago" → "is today") instead
  // of stacking a second, contradictory chip beside it.
  const existing = root.items.find(
    (item): item is FilterChip =>
      item.type === "chip" &&
      item.field === field &&
      (spec.single || !op || item.op === op),
  );
  if (!existing || spec.single) {
    if (existing && spec.single) {
      const next = setChipValues(root, existing.id, [value], fields);
      return op && op !== existing.op
        ? updateNode(next, existing.id, (n) => ({ ...n, op }) as FilterChip)
        : next;
    }
    return {
      ...root,
      items: [...root.items, newChip(field, [value], op, fields)],
    };
  }
  const has = existing.values.some((v) => v === value);
  return setChipValues(
    root,
    existing.id,
    has
      ? existing.values.filter((v) => v !== value)
      : [...existing.values, value],
    fields,
  );
}

/** The operators a chip offers; `is` / `is not` read "is either of" / "is not" with several values. */
export function opLabel(spec: FieldSpec, op: ChipOp, count: number): string {
  if (spec.type === "date") {
    return (
      {
        before: "before",
        after: "after",
        onOrBefore: "on or before",
        onOrAfter: "on or after",
        is: "is",
      }[op as string] || op
    );
  }
  if (spec.type === "labels")
    return (
      {
        includesAny: count > 1 ? "include any of" : "include",
        includesAll: "include all of",
        includesNone: count > 1 ? "include none of" : "do not include",
      }[op as string] || op
    );
  if (spec.type === "number" && NUMBER_OPS[op])
    return (
      { gt: "more than", gte: "at least", lt: "less than", lte: "at most" }[
        op as string
      ] || op
    );
  if (spec.type === "text" || spec.type === "content")
    return op === "contains" ? "contains" : op === "isNot" ? "is not" : "is";
  if (op === "isNot") return count > 1 ? "is not" : "is not";
  return count > 1 ? "is either of" : "is";
}

/* ------------------------------------------------------------- dates */

const UNIT: Record<string, string> = {
  d: "D",
  day: "D",
  days: "D",
  w: "W",
  wk: "W",
  wks: "W",
  week: "W",
  weeks: "W",
  m: "M",
  mo: "M",
  mos: "M",
  month: "M",
  months: "M",
  y: "Y",
  yr: "Y",
  yrs: "Y",
  year: "Y",
  years: "Y",
  h: "H",
  hr: "H",
  hrs: "H",
  hour: "H",
  hours: "H",
};
const WORD_NUMBER: Record<string, number> = {
  a: 1,
  an: 1,
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
};
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function duration(sign: "-" | "", n: number, unit: string): string {
  return unit === "H" ? `${sign}PT${n}H` : `${sign}P${n}${unit}`;
}

/** Free text a person types in the date menu ("10 days ago", "2 weeks", "in 3 days", "last month",
 * "tomorrow", "2026-11-30", "Nov 30 2026") as a grammar value: an ISO duration, a keyword, or an
 * ISO date. A bare amount ("2 weeks") looks back for past dates and ahead for due dates. */
export function parseDateInput(
  text: string,
  direction: "past" | "future" = "past",
): string | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, " ");
  if (!t) return null;
  if (["today", "yesterday", "tomorrow", "now", "overdue"].includes(t))
    return t;
  if (/^\d{4}-\d{2}-\d{2}$/.test(t))
    return Number.isNaN(Date.parse(`${t}T00:00:00Z`)) ? null : t;
  if (/^[+-]?p(?=\d|t\d)/.test(t)) return t.toUpperCase().replace(/^\+/, "");
  const rel = /^(last|next|this) (day|week|month|year)$/.exec(t);
  if (rel)
    return rel[1] === "this"
      ? "today"
      : duration(rel[1] === "last" ? "-" : "", 1, UNIT[rel[2]]);
  const m =
    /^(in )?(\d{1,4}|an?|one|two|three|four|five|six|seven|eight|nine|ten) ?([a-z]+)( ago| from now| ahead| later| before| after)?$/.exec(
      t,
    );
  if (m && UNIT[m[3]]) {
    const n = /^\d/.test(m[2]) ? Number(m[2]) : WORD_NUMBER[m[2]];
    if (!n) return null;
    const ahead =
      Boolean(m[1]) || /from now|ahead|later|after/.test(m[4] || "");
    const back = /ago|before/.test(m[4] || "");
    const sign = back ? "-" : ahead ? "" : direction === "past" ? "-" : "";
    return duration(sign, n, UNIT[m[3]]);
  }
  // A written date ("Nov 30 2026", "30 November 2026"): read in UTC so the day never shifts.
  if (/[a-z]/.test(t) && /\d/.test(t)) {
    const parsed = Date.parse(`${t} UTC`);
    if (!Number.isNaN(parsed))
      return new Date(parsed).toISOString().slice(0, 10);
  }
  return null;
}

const UNIT_WORD: Record<string, string> = {
  Y: "year",
  M: "month",
  W: "week",
  D: "day",
  H: "hour",
};

/** A stored date value as the chip shows it: "1 week ago", "3 days from now", "Overdue", "Nov 30, 2026". */
export function describeDate(value: FilterValue): string {
  if (value === null) return "No date";
  const v = String(value);
  const keyword: Record<string, string> = {
    today: "Today",
    yesterday: "Yesterday",
    tomorrow: "Tomorrow",
    now: "Now",
    overdue: "Overdue",
  };
  if (keyword[v]) return keyword[v];
  const d = /^(-)?P(?:T)?(\d+)([YMWDH])$/.exec(v);
  if (d) {
    const n = Number(d[2]);
    const word = `${n} ${UNIT_WORD[d[3]]}${n === 1 ? "" : "s"}`;
    return d[1] ? `${word} ago` : `${word} from now`;
  }
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (iso) return `${MONTHS[Number(iso[2]) - 1]} ${Number(iso[3])}, ${iso[1]}`;
  return v;
}

/** Relative presets the date submenu offers, as Linear's do. */
export function datePresets(
  spec: FieldSpec,
): { label: string; op: ChipOp; value: FilterValue }[] {
  if (spec.direction === "future")
    return [
      ...(spec.server === "dueDate" || spec.server === "targetDate"
        ? [{ label: "Overdue", op: "is" as ChipOp, value: "overdue" }]
        : []),
      { label: "Today", op: "is", value: "today" },
      { label: "1 day from now", op: "before", value: "P1D" },
      { label: "3 days from now", op: "before", value: "P3D" },
      { label: "1 week from now", op: "before", value: "P1W" },
      { label: "1 month from now", op: "before", value: "P1M" },
      { label: "3 months from now", op: "before", value: "P3M" },
    ];
  return [
    { label: "Today", op: "is", value: "today" },
    { label: "1 day ago", op: "after", value: "-P1D" },
    { label: "3 days ago", op: "after", value: "-P3D" },
    { label: "1 week ago", op: "after", value: "-P1W" },
    { label: "1 month ago", op: "after", value: "-P1M" },
    { label: "3 months ago", op: "after", value: "-P3M" },
    { label: "6 months ago", op: "after", value: "-P6M" },
    { label: "1 year ago", op: "after", value: "-P1Y" },
  ];
}

const DAY = 864e5;

function startOfUtcDay(ms: number): number {
  return Math.floor(ms / DAY) * DAY;
}

/** A grammar date value as a time range [start, end) in ms, measured from `now`. Durations
 * without hours name a UTC calendar day, exactly as the server resolves them. */
export function resolveDate(
  value: FilterValue,
  now: number,
): { start: number; end: number } | null {
  if (value === null || value === undefined) return null;
  const v = String(value);
  const today = startOfUtcDay(now);
  if (v === "now") return { start: now, end: now };
  if (v === "today" || v === "overdue")
    return { start: today, end: today + DAY };
  if (v === "yesterday") return { start: today - DAY, end: today };
  if (v === "tomorrow") return { start: today + DAY, end: today + 2 * DAY };
  const d =
    /^([+-])?P(?=\d|T\d)(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(
      v,
    );
  if (d) {
    const sign = d[1] === "-" ? -1 : 1;
    const [y, mo, w, dd, h, mi, s] = d.slice(2).map((x) => Number(x || 0));
    const at = new Date(now);
    at.setUTCFullYear(
      at.getUTCFullYear() + sign * y,
      at.getUTCMonth() + sign * mo,
      at.getUTCDate() + sign * (w * 7 + dd),
    );
    const ms = at.getTime() + sign * ((h * 60 + mi) * 60 + s) * 1000;
    if (h || mi || s) return { start: ms, end: ms };
    const day = startOfUtcDay(ms);
    return { start: day, end: day + DAY };
  }
  const parsed = Date.parse(
    /^\d{4}-\d{2}-\d{2}$/.test(v) ? `${v}T00:00:00Z` : v,
  );
  if (Number.isNaN(parsed)) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(v))
    return { start: parsed, end: parsed + DAY };
  return { start: parsed, end: parsed };
}

/* ------------------------------------------------------------- URL */

function toBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text: string): string {
  const b64 =
    text.replace(/-/g, "+").replace(/_/g, "/") +
    "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(b64);
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

/** The `?filter=` value for a tree: URL-safe base64 of its compact JSON; "" for no filter. */
export function encodeFilterParam(tree: FilterTree | null | undefined): string {
  return hasFilters(tree) ? toBase64Url(JSON.stringify(tree)) : "";
}

/** Read a `?filter=` value back; anything that is not a filter object reads as null. */
export function decodeFilterParam(
  param: string | null | undefined,
): FilterTree | null {
  if (!param || param.length > 8000) return null;
  try {
    const parsed = JSON.parse(fromBase64Url(param));
    return isObject(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Replace (or drop) `filter` in a query string, keeping every other parameter. */
export function withFilterParam(
  search: string,
  tree: FilterTree | null | undefined,
): string {
  const params = new URLSearchParams(search);
  const encoded = encodeFilterParam(tree);
  if (encoded) params.set("filter", encoded);
  else params.delete("filter");
  const out = params.toString();
  return out ? `?${out}` : "";
}
