/* Client-side evaluation of the view filter grammar over the tasks already in the local store.
 *
 * Team Tasks, My issues and project tasks render from the local-first store, so the chip bar
 * filters in the browser with the same grammar the server executes (docs/views.md): "me" is
 * the viewer, relative dates resolve against now, and a negative predicate on an empty
 * property keeps the empty rows. Pure and import-free (type imports are erased) so the node
 * test script can run it.
 */
import type { FilterTree } from "./filterModel";
import { resolveDate } from "./filterModel";

/** The task fields the evaluator reads; the app's Issue type satisfies it. */
export type TaskLike = {
  id: string;
  identifier: string;
  title: string;
  description?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  stateChangedAt?: string | null;
  dueDate?: string | null;
  priority?: number | null;
  estimate?: number | null;
  parent?: { id: string } | null;
  state?: { id: string; name: string; type: string } | null;
  team?: { id: string; key?: string; name?: string } | null;
  project?: { id: string; name?: string } | null;
  assignee?: { id: string; name?: string } | null;
  creator?: { id: string; name?: string } | null;
  cycle?: { id: string; name?: string; number?: number } | null;
  subscribers?: { nodes: { id: string }[] } | null;
  labels?: { nodes: { id: string; name: string }[] } | null;
};

export type EvalContext = {
  viewerId?: string;
  myTeamIds?: ReadonlySet<string>;
  now?: number;
  /** Ids of tasks that have at least one sub-task in the loaded set. */
  parentIds?: ReadonlySet<string>;
};

type Aliases = string[] | null;

const norm = (v: unknown) => String(v).trim().toLowerCase();

function stateDate(t: TaskLike, types: string[]): string | null {
  return t.state && types.includes(t.state.type)
    ? t.stateChangedAt || t.updatedAt || null
    : null;
}

function single(t: TaskLike, key: string, ctx: EvalContext): Aliases {
  switch (key) {
    case "team":
      return t.team
        ? [
            t.team.id,
            t.team.key || "",
            ...(ctx.myTeamIds?.has(t.team.id) ? ["myteams"] : []),
          ]
        : null;
    case "state":
      return t.state
        ? [t.state.id, t.state.name, `${t.state.name}|${t.state.type}`]
        : null;
    case "stateType":
      return t.state ? [t.state.type] : null;
    case "assignee":
    case "creator": {
      const u = t[key];
      return u
        ? [u.id, u.name || "", ...(u.id === ctx.viewerId ? ["me"] : [])]
        : null;
    }
    case "project":
      return t.project ? [t.project.id, t.project.name || ""] : null;
    case "cycle":
      return t.cycle
        ? [
            t.cycle.id,
            t.cycle.name || "",
            t.cycle.number == null ? "" : String(t.cycle.number),
          ]
        : null;
    case "parent":
      return t.parent?.id ? [t.parent.id] : null;
    case "priority":
      return [String(t.priority ?? 0)];
    case "estimate":
      return t.estimate == null ? null : [String(t.estimate)];
    case "number": {
      const n = /(\d+)$/.exec(t.identifier)?.[1];
      return n ? [n] : null;
    }
    default:
      return null;
  }
}

function collection(t: TaskLike, key: string, ctx: EvalContext): string[][] {
  if (key === "labels")
    return (t.labels?.nodes || []).map((l) => [l.id, l.name]);
  if (key === "subscriber")
    return (t.subscribers?.nodes || []).map((u) => [
      u.id,
      ...(u.id === ctx.viewerId ? ["me"] : []),
    ]);
  return [];
}

function numberOf(t: TaskLike, key: string): number | null {
  if (key === "priority") return t.priority ?? 0;
  if (key === "estimate") return t.estimate ?? null;
  if (key === "number") {
    const n = /(\d+)$/.exec(t.identifier)?.[1];
    return n ? Number(n) : null;
  }
  return null;
}

function dateOf(t: TaskLike, key: string): string | null {
  switch (key) {
    case "createdAt":
      return t.createdAt || null;
    case "updatedAt":
      return t.updatedAt || null;
    case "dueDate":
      return t.dueDate || null;
    case "startedAt":
      return stateDate(t, ["started"]);
    case "completedAt":
      return stateDate(t, ["completed"]);
    case "canceledAt":
      return stateDate(t, ["canceled"]);
    default:
      return null;
  }
}

const TEXT = new Set(["identifier", "title", "description"]);
const DATES = new Set([
  "createdAt",
  "updatedAt",
  "dueDate",
  "startedAt",
  "completedAt",
  "canceledAt",
]);
const NUMBERS = new Set(["priority", "estimate", "number"]);

function matchesAlias(aliases: Aliases, value: unknown): boolean {
  if (value === null) return aliases === null;
  return Boolean(aliases && aliases.some((a) => a && norm(a) === norm(value)));
}

function dateMatches(
  raw: string | null,
  op: string,
  value: unknown,
  now: number,
  key: string,
): boolean {
  if (value === null) return op === "is" ? raw === null : raw !== null;
  if (!raw) return false;
  const at = Date.parse(
    /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00Z` : raw,
  );
  if (Number.isNaN(at)) return false;
  if (value === "overdue")
    return op === "is" && at < Math.floor(now / 864e5) * 864e5;
  const range = resolveDate(value as string, now);
  if (!range) return false;
  // A calendar-date column compares by its day, like the server's dueDate.
  const day = key === "dueDate" ? Math.floor(at / 864e5) * 864e5 : at;
  switch (op) {
    case "is":
      return (
        day >= range.start &&
        (range.end > range.start ? day < range.end : day === range.start)
      );
    case "before":
      return day < range.start;
    case "onOrBefore":
      return range.end > range.start ? day < range.end : day <= range.end;
    case "after":
      return range.end > range.start ? day >= range.end : day > range.end;
    case "onOrAfter":
      return day >= range.start;
    default:
      return false;
  }
}

function predicate(
  t: TaskLike,
  key: string,
  ops: Record<string, unknown>,
  ctx: EvalContext,
): boolean {
  const now = ctx.now ?? Date.now();
  for (const [op, value] of Object.entries(ops)) {
    const list = Array.isArray(value) ? value : [value];
    let ok: boolean;
    if (key === "hasSubIssues")
      ok = Boolean(ctx.parentIds?.has(t.id)) === Boolean(value);
    else if (key === "isSubIssue")
      ok = Boolean(t.parent?.id) === Boolean(value);
    else if (key === "parent" && op === "isNull")
      ok = !t.parent?.id === Boolean(value);
    else if (TEXT.has(key)) {
      const text =
        key === "identifier"
          ? t.identifier
          : key === "title"
            ? t.title
            : t.description || "";
      const v = String(value ?? "");
      ok =
        op === "contains" || op === "containsIgnoreCase"
          ? norm(text).includes(norm(v))
          : op === "equalsIgnoreCase"
            ? norm(text) === norm(v)
            : op === "is"
              ? text === v
              : op === "isNot"
                ? text !== v
                : false;
    } else if (DATES.has(key))
      ok = dateMatches(dateOf(t, key), op, value, now, key);
    else if (key === "labels" || key === "subscriber") {
      const items = collection(t, key, ctx);
      const has = (v: unknown) =>
        v === null ? items.length === 0 : items.some((a) => matchesAlias(a, v));
      if (op === "isMe") ok = has("me") === Boolean(value);
      else if (op === "includesAll") ok = list.every(has);
      else if (
        op === "includesNone" ||
        op === "includesNeither" ||
        op === "isNot" ||
        op === "isNeitherOf"
      )
        ok = !list.some(has);
      else ok = list.some(has);
    } else if (NUMBERS.has(key) && /^is(Greater|Less)Than/.test(op)) {
      const n = numberOf(t, key);
      const v = Number(value);
      ok =
        n !== null &&
        (op === "isGreaterThan"
          ? n > v
          : op === "isGreaterThanOrEqual"
            ? n >= v
            : op === "isLessThan"
              ? n < v
              : n <= v);
    } else {
      const aliases = single(t, key, ctx);
      if (op === "isMe") ok = matchesAlias(aliases, "me") === Boolean(value);
      else if (op === "is" || op === "isEitherOf")
        ok = list.some((v) => matchesAlias(aliases, v));
      else if (op === "isNot" || op === "isNeitherOf")
        ok = !list.some((v) => matchesAlias(aliases, v));
      else ok = true; // an operator the client cannot evaluate never hides rows the server would show
    }
    if (!ok) return false;
  }
  return true;
}

/** Whether a task matches a filter document. `{}` matches everything. */
export function matchesFilter(
  tree: FilterTree | null | undefined,
  t: TaskLike,
  ctx: EvalContext = {},
): boolean {
  if (!tree) return true;
  for (const [key, value] of Object.entries(tree)) {
    if (key === "and") {
      if (!(value as FilterTree[]).every((sub) => matchesFilter(sub, t, ctx)))
        return false;
    } else if (key === "or") {
      const list = value as FilterTree[];
      if (list.length && !list.some((sub) => matchesFilter(sub, t, ctx)))
        return false;
    } else if (
      value &&
      typeof value === "object" &&
      !predicate(t, key, value as Record<string, unknown>, ctx)
    )
      return false;
  }
  return true;
}

/** Ids of tasks that are a parent of another task in the set (`hasSubIssues`). */
export function parentIdsOf(tasks: readonly TaskLike[]): Set<string> {
  const out = new Set<string>();
  for (const t of tasks) if (t.parent?.id) out.add(t.parent.id);
  return out;
}

/** How many tasks each value of a property would select: the counts beside menu values. */
export function countBy(
  tasks: readonly TaskLike[],
  key: string,
  ctx: EvalContext = {},
): Map<string, number> {
  const out = new Map<string, number>();
  const bump = (k: string) => out.set(k, (out.get(k) || 0) + 1);
  for (const t of tasks) {
    if (key === "labels" || key === "subscriber") {
      const items = collection(t, key, ctx);
      if (!items.length) bump("null");
      for (const a of items) bump(a[0]);
      if (key === "subscriber" && items.some((a) => a.includes("me")))
        bump("me");
    } else if (key === "state") {
      if (t.state) bump(t.state.name);
    } else {
      const a = single(t, key, ctx);
      if (!a) bump("null");
      else {
        bump(a[0]);
        if (a.includes("me")) bump("me");
      }
    }
  }
  return out;
}
