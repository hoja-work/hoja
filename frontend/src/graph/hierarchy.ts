/* Sub-task hierarchy for the issue list and the task page.
 *
 * One derivation feeds both surfaces so they cannot drift: a parent is always followed by its
 * own children, and each row learns the depth it renders at plus which ancestor rails still
 * pass through it. Children are ordered oldest-first, which keeps a task above the sub-tasks
 * it spawned even though the list itself sorts newest-first.
 */

/** Horizontal step per level. Matches the reference layout's 24px indent. */
export const SIBLING_STEP = 24;
/** Left inset of a depth-0 row (Tailwind pl-10.5). */
export const ROW_INSET = 42;
/** The rail sits inside the 24px step, not on its edge. */
export const RAIL_OFFSET = 11;

export type Parented = {
  id: string;
  parent?: { id: string } | null;
  state?: { type?: string | null } | null;
  createdAt?: string | null;
};

export type TreeRow<T extends Parented> = {
  node: T;
  /** 0 for a task that is not a sub-task. */
  depth: number;
  /** True when this row is the last of its siblings, which closes the rail with a corner. */
  isLast: boolean;
  /** Depths of ancestors whose rail continues past this row. */
  openRails: number[];
  childCount: number;
  doneCount: number;
  /** Parent exists but fell outside the loaded set, so the row keeps its chip instead of nesting. */
  parentOutside: boolean;
};

export function railLeft(column: number) {
  return ROW_INSET + column * SIBLING_STEP + RAIL_OFFSET;
}

/**
 * Flatten issues into display order: every parent immediately followed by its children.
 * Only a parent that is present in `items` can nest a child; anything else stays a root and is
 * flagged `parentOutside` so the caller can show the parent chip instead of a silent orphan.
 */
export function orderTree<T extends Parented>(
  items: T[],
  collapsed: Record<string, boolean> = {},
): TreeRow<T>[] {
  const ids = new Set(items.map((i) => i.id));
  const children = new Map<string, T[]>();
  const roots: T[] = [];

  for (const item of items) {
    const parentId = item.parent?.id;
    if (parentId && parentId !== item.id && ids.has(parentId)) {
      const list = children.get(parentId);
      if (list) list.push(item);
      else children.set(parentId, [item]);
    } else {
      roots.push(item);
    }
  }

  // Oldest first: a sub-task is never rendered above the task that created it.
  for (const list of children.values()) {
    list.sort((a, b) => (a.createdAt || "").localeCompare(b.createdAt || ""));
  }

  const out: TreeRow<T>[] = [];
  const walk = (node: T, depth: number, isLast: boolean, openRails: number[]) => {
    const kids = children.get(node.id) || [];
    out.push({
      node,
      depth,
      isLast,
      openRails: [...openRails],
      childCount: kids.length,
      doneCount: kids.filter((k) => k.state?.type === "completed").length,
      parentOutside: Boolean(node.parent?.id && !ids.has(node.parent.id)),
    });
    if (kids.length === 0 || collapsed[node.id]) return;
    for (const [index, kid] of kids.entries()) {
      const kidIsLast = index === kids.length - 1;
      walk(kid, depth + 1, kidIsLast, kidIsLast ? openRails : [...openRails, depth]);
    }
  };

  for (const root of roots) walk(root, 0, true, []);
  return out;
}
