/**
 * The menu surface library: one implementation of "a menu closes when you press
 * outside it or press Escape".
 *
 * Every menu-like surface in the app — the sidebar and workspace switcher
 * (`Popover`), the My issues filter/display panels, the settings dropdown
 * (`SettingsSelect`), the Linear-style dropdowns (`LinMenu`) and the agent
 * history panel — dismisses through `useMenuDismiss`, so they cannot drift apart.
 *
 * A caller passes the elements that belong to the menu. Anything inside them is
 * "inside": the trigger, the panel, and any portal the panel renders into. A
 * press anywhere else, or Escape, dismisses.
 */
import { useLayoutEffect, useRef, type ReactNode } from "react";
import {
  Popover as UIPopover,
  PopoverTrigger,
  PopoverContent,
} from "./components/ui/popover";

/** A ref whose `current` is the element; `readonly` so a `RefObject<HTMLDivElement>` fits. */
export type MenuElementRef = { readonly current: Element | null };

export type MenuDismissOptions = {
  /** Listeners are attached only while this is true. */
  open: boolean;
  /** Called for an outside press, or for Escape when no `onEscape` is given. */
  onDismiss: () => void;
  /** Elements that belong to the menu. A press inside any of them does not dismiss. */
  within?: readonly MenuElementRef[];
  /**
   * Extra membership test for content the menu does not own as a DOM descendant,
   * e.g. a portal rendered by a third-party component.
   */
  alsoWithin?: (node: Node) => boolean;
  /**
   * Take over Escape entirely — the caller dismisses, keeps focus and stops
   * propagation itself. Omitted means Escape dismisses.
   */
  onEscape?: (event: KeyboardEvent) => void;
};

function belongsToMenu(
  node: Node,
  within: readonly MenuElementRef[] | undefined,
  alsoWithin: ((node: Node) => boolean) | undefined,
): boolean {
  if (within)
    for (const ref of within) if (ref.current?.contains(node)) return true;
  return Boolean(alsoWithin?.(node));
}

/**
 * Dismiss an open menu on an outside `pointerdown` or on Escape.
 *
 * The listeners are attached once per open/close, not per render: the options are
 * read through a ref, so callers can pass inline callbacks without resubscribing
 * on every keystroke of a parent component.
 */
export function useMenuDismiss(options: MenuDismissOptions): void {
  const latest = useRef(options);

  useLayoutEffect(() => {
    latest.current = options;
  });

  useLayoutEffect(() => {
    if (!options.open) return;

    const onPointerDown = (event: PointerEvent) => {
      const { onDismiss, within, alsoWithin } = latest.current;
      const target = event.target;
      if (!(target instanceof Node)) {
        onDismiss();
        return;
      }
      if (belongsToMenu(target, within, alsoWithin)) return;
      onDismiss();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const { onDismiss, onEscape } = latest.current;
      if (onEscape) onEscape(event);
      else onDismiss();
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [options.open]);
}

type PopoverProps = {
  className?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  panelClassName: string;
  panelId: string;
  panelRole?: "menu" | "dialog" | "listbox";
  trigger: (open: boolean, toggle: () => void) => ReactNode;
  children: ReactNode;
};

/**
 * A compatibility adapter for the shared UI popover primitive.
 *
 * Use this for any menu, dropdown or inline panel: the trigger and panel share one
 * container, so a press on the trigger counts as inside and the panel dismisses on
 * an outside press or Escape like every other menu.
 */
export function Popover({
  className,
  open,
  onOpenChange,
  panelClassName,
  panelId,
  panelRole,
  trigger,
  children,
}: PopoverProps) {
  return (
    <UIPopover open={open} onOpenChange={onOpenChange}>
      <div className={className}>
        <PopoverTrigger asChild>{trigger(open, () => {})}</PopoverTrigger>
        <PopoverContent
          align="start"
          sideOffset={6}
          data-popover-panel
          className={`z-55 w-auto border border-solid border-line rounded-[10px] bg-panel p-1.5 shadow-(--shadow) ${panelClassName}`}
          id={panelId}
          role={panelRole}
        >
          {children}
        </PopoverContent>
      </div>
    </UIPopover>
  );
}
