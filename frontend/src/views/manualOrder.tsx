/**
 * Manual order in a task list: drag (dnd-kit) and the keyboard path.
 *
 * What Linear does (artifacts/runs/20261123-views-depth/SPEC.md §1): a drag writes the task's
 * rank between its new neighbours; dropped into another group it also sets the grouped field,
 * in the same write. Under "priority" ordering the drag moves the priority rank instead.
 * Linear's keyboard path stops at a group boundary (⌥↑ cannot leave the group) and leaves
 * focus behind when a status change moves the task (JOBS.md, J1 keyboard). Here ⌥↑/⌥↓ cross
 * group boundaries — the move sets the group field — and focus stays on the moved task;
 * ⌥⇧↑/⌥⇧↓ send it to the top or bottom of its group.
 *
 * The planning functions are pure (tested in scripts/test-view-filters.mjs); the React parts
 * only translate pointer and key events into them.
 */
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import React from "react";
import type { MovePlan, OrderGroups } from "./manualOrderPlan";
import { planDrop } from "./manualOrderPlan";

export * from "./manualOrderPlan";

/* ------------------------------------------------------------------- React */

export function ManualOrderList({
  enabled,
  groups,
  onDrop,
  children,
}: {
  enabled: boolean;
  groups: OrderGroups;
  onDrop: (plan: MovePlan) => void;
  children: React.ReactNode;
}) {
  const sensors = useSensors(
    // A small distance keeps a click a click.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 8 } }),
    // Space picks a task up (Enter keeps opening it); Space or Enter drops it.
    useSensor(KeyboardSensor, {
      keyboardCodes: { start: ["Space"], cancel: ["Escape"], end: ["Space", "Enter"] },
    }),
  );
  if (!enabled) return <>{children}</>;
  const groupOf = (id: string) => groups.find((g) => g.ids.includes(id));
  const onDragEnd = (event: DragEndEvent) => {
    const id = String(event.active.id);
    const overId = event.over ? String(event.over.id) : null;
    if (!overId || overId === id) return;
    let target = groupOf(overId);
    let index = 0;
    if (overId.startsWith("group:")) {
      target = groups.find((g) => `group:${g.key}` === overId);
      index = target ? target.ids.length : 0;
    } else if (target) {
      const others = target.ids.filter((x) => x !== id);
      const overAt = others.indexOf(overId);
      const from = groupOf(id);
      const movingDown =
        from === target && target.ids.indexOf(id) < target.ids.indexOf(overId);
      index = overAt + (movingDown ? 1 : 0);
    }
    if (!target) return;
    const plan = planDrop(groups, id, target.key, index);
    if (plan) onDrop(plan);
  };
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={onDragEnd}
      accessibility={{
        screenReaderInstructions: {
          draggable:
            "To move a task, press space, move it with the arrow keys and press space again. Or press Alt and an arrow key at any time.",
        },
      }}
    >
      {children}
    </DndContext>
  );
}

export function ManualOrderGroup({
  ids,
  children,
}: {
  ids: string[];
  children: React.ReactNode;
}) {
  return (
    <SortableContext items={ids} strategy={verticalListSortingStrategy}>
      {children}
    </SortableContext>
  );
}

export type SortableRowProps = {
  ref: (el: HTMLElement | null) => void;
  style: React.CSSProperties;
  "data-dragging"?: "";
} & Record<string, unknown>;

/** A row that can be dragged; renders `children(props)` so the row keeps its own element. */
export function SortableRow({
  id,
  enabled,
  children,
}: {
  id: string;
  enabled: boolean;
  children: (props: SortableRowProps) => React.ReactNode;
}) {
  const s = useSortable({ id, disabled: !enabled });
  if (!enabled) return <>{children({ ref: () => {}, style: {} })}</>;
  const { role: _role, tabIndex: _tabIndex, ...attributes } = s.attributes;
  return (
    <>
      {children({
        ref: s.setNodeRef,
        style: {
          transform: CSS.Transform.toString(s.transform),
          transition: s.transition,
          position: "relative",
          zIndex: s.isDragging ? 2 : undefined,
        },
        ...attributes,
        ...s.listeners,
        ...(s.isDragging ? { "data-dragging": "" } : {}),
      })}
    </>
  );
}
