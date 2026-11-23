/* Hierarchy helpers shared by the list and the kanban (HOJA-33).
 *
 * Both surfaces receive a flat array of tasks whose `parent` relation is loaded. Rendering
 * them in arrival order is what made a parent and its sub-tasks read as two unrelated lists.
 * These helpers keep one source of truth for the ordering, the depth and the progress rollup
 * so the list, the board and the detail cannot disagree about the same tree. */

export type SubtreeNode = {id: string; parent?: {id: string; identifier?: string} | null};
export type SubtreeRow<T> = {issue: T; depth: number; parentIdent?: string};
export type ChildRollup = {done: number; total: number};

/** Parent-first order inside one rendered group (a status column, a grouped section).
 *  A sub-task directly follows its parent and carries its depth. A sub-task whose parent is
 *  outside the group stays a root and keeps a parent chip, so it is never mistaken for an
 *  orphan. Cycles in parent data can never render a row twice. */
export function nestIssues<T extends SubtreeNode>(items: T[]): SubtreeRow<T>[] {
  const inGroup = new Set(items.map(i => i.id));
  const kids = new Map<string, T[]>();
  for (const i of items) {
    const pid = i.parent?.id;
    if (!pid || pid === i.id || !inGroup.has(pid)) continue;
    const list = kids.get(pid);
    if (list) list.push(i); else kids.set(pid, [i]);
  }
  const out: SubtreeRow<T>[] = [];
  const seen = new Set<string>();
  const walk = (issue: T, depth: number) => {
    if (seen.has(issue.id)) return;
    seen.add(issue.id);
    const pid = issue.parent?.id;
    out.push({issue, depth, parentIdent: pid && !inGroup.has(pid) ? issue.parent?.identifier : undefined});
    for (const kid of kids.get(issue.id) || []) walk(kid, depth + 1);
  };
  for (const i of items) if (!i.parent?.id || i.parent.id === i.id || !inGroup.has(i.parent.id)) walk(i, 0);
  for (const i of items) walk(i, 0);
  return out;
}

/** Completed/total sub-tasks per parent, from the rows the caller actually holds. It is a
 *  count of loaded children, not a server aggregate: a parent whose children fall outside
 *  the page shows the ratio for what is on screen. */
export function childRollups<T extends SubtreeNode & {state?: {type?: string}}>(items: T[]): Map<string, ChildRollup> {
  const m = new Map<string, ChildRollup>();
  for (const i of items) {
    const pid = i.parent?.id;
    if (!pid) continue;
    const r = m.get(pid) || {done: 0, total: 0};
    r.total += 1;
    if (i.state?.type === 'completed') r.done += 1;
    m.set(pid, r);
  }
  return m;
}
