/**
 * Manual order: the pure half (planning a drop or an Alt+arrow move). See manualOrder.tsx for
 * the behaviour and the React half; tested in scripts/test-view-filters.mjs.
 */
import { keyBetween } from "./ranking";

export type RankField = "sortOrder" | "prioritySortOrder";

/** The visible rows, by group, in display order. */
export type OrderGroups = { key: string; ids: string[] }[];

export type MovePlan = {
  id: string;
  /** The group the task lands in (may differ from where it started). */
  groupKey: string;
  fromGroupKey: string;
  afterId: string | null;
  beforeId: string | null;
};

const locate = (groups: OrderGroups, id: string) => {
  for (let g = 0; g < groups.length; g++) {
    const i = groups[g].ids.indexOf(id);
    if (i >= 0) return { g, i };
  }
  return null;
};

/** Drop `id` at `index` of group `toKey` (index counted without the task itself). */
export function planDrop(
  groups: OrderGroups,
  id: string,
  toKey: string,
  index: number,
): MovePlan | null {
  const from = locate(groups, id);
  const to = groups.find((g) => g.key === toKey);
  if (!from || !to) return null;
  const others = to.ids.filter((x) => x !== id);
  const at = Math.max(0, Math.min(index, others.length));
  const plan = {
    id,
    groupKey: toKey,
    fromGroupKey: groups[from.g].key,
    afterId: at > 0 ? others[at - 1] : null,
    beforeId: at < others.length ? others[at] : null,
  };
  // Dropped where it already was: nothing to write.
  const same = groups[from.g].key === toKey && from.i === at;
  return same ? null : plan;
}

/** ⌥↑/⌥↓ (one step, crossing into the neighbouring group at its edge) and ⌥⇧↑/⌥⇧↓ (top or
 * bottom of the task's own group). Returns null when the task cannot move that way. */
export function planKey(
  groups: OrderGroups,
  id: string,
  dir: -1 | 1,
  edge = false,
): MovePlan | null {
  const at = locate(groups, id);
  if (!at) return null;
  const group = groups[at.g];
  if (edge) return planDrop(groups, id, group.key, dir < 0 ? 0 : group.ids.length);
  const index = at.i + dir;
  if (index >= 0 && index < group.ids.length)
    return planDrop(groups, id, group.key, index);
  // Past the group's edge: the next group in that direction, at its near end.
  const next = groups[at.g + dir];
  if (!next) return null;
  return planDrop(groups, id, next.key, dir < 0 ? next.ids.length : 0);
}

/** The rank to show at once for a plan (the server's answer replaces it). */
export function optimisticRank(
  plan: MovePlan,
  rankOf: (id: string) => string | null | undefined,
): string | undefined {
  try {
    const a = plan.afterId ? rankOf(plan.afterId) || null : null;
    const b = plan.beforeId ? rankOf(plan.beforeId) || null : null;
    if (plan.afterId && !a) return undefined;
    if (plan.beforeId && !b) return undefined;
    return keyBetween(a, b && a && a >= b ? null : b);
  } catch {
    return undefined;
  }
}

/** Fields that put a task into group `key` under `grouping`; null when the grouping cannot be
 * changed by a drop (labels, team, creator…). */
export function groupPatch(
  grouping: string,
  key: string,
  task: { labels?: { nodes: { id: string; parent?: { id: string } | null }[] } | null },
  ctx: {
    stateIdForName?: (name: string) => string | undefined;
    labelGroupId?: string | null;
  },
): Record<string, unknown> | null {
  const none = key === "none";
  switch (grouping) {
    case "none":
      return {};
    case "status": {
      const stateId = ctx.stateIdForName?.(key);
      return stateId ? { stateId } : null;
    }
    case "assignee":
      return { assigneeId: none ? null : key };
    case "priority":
      return { priority: Number(key) || 0 };
    case "project":
      return { projectId: none ? null : key };
    case "cycle":
      return { cycleId: none ? null : key };
    case "labelGroup": {
      // Swap the group's label, keep every other label (linear-capture/s6b-*).
      const others = (task.labels?.nodes || [])
        .filter((l) => !ctx.labelGroupId || l.parent?.id !== ctx.labelGroupId)
        .map((l) => l.id);
      return { labelIds: none ? others : [...others, key] };
    }
    default:
      return null;
  }
}

