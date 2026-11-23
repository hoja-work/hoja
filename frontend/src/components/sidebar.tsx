import * as React from "react";
import { cn } from "cn";
import { ChevronDown } from "lucide-react";
import { Tooltip as TooltipPrimitive } from "radix-ui";

/* The one sidebar row. The workspace navigation and the settings navigation both render
 * inside the app's single <aside id="workspace-sidebar"> (main.tsx) and both use this row,
 * so hover, current-page, focus, touch-target and collapsed-rail behaviour cannot drift. */
export const sidebarItemClass =
  "group relative flex w-full min-w-0 items-center gap-2 overflow-hidden rounded-lg bg-transparent px-2.5 py-1 text-left text-[13px] font-normal whitespace-nowrap text-muted hover:bg-sidebar-hover hover:text-foreground aria-[current=page]:bg-sidebar-active aria-[current=page]:font-medium aria-[current=page]:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11 shell-desktop:rail-collapsed:mx-auto shell-desktop:rail-collapsed:size-7 shell-desktop:rail-collapsed:min-h-7 shell-desktop:rail-collapsed:justify-center shell-desktop:rail-collapsed:gap-0 shell-desktop:rail-collapsed:p-0";

export function SidebarTooltipProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <TooltipPrimitive.Provider delayDuration={200} skipDelayDuration={80}>
      {children}
    </TooltipPrimitive.Provider>
  );
}

/** Rail-only tooltip: renders nothing in expanded mode so full labels never duplicate. */
export function SidebarTooltip({
  label,
  detail,
  shortcut,
  enabled,
  children,
}: {
  label: string;
  detail?: string;
  shortcut?: string;
  enabled: boolean;
  children: React.ReactElement;
}) {
  if (!enabled) return children;
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side="right"
          sideOffset={10}
          collisionPadding={8}
          className="z-50 flex items-center gap-2 rounded-md border border-line bg-panel px-2.5 py-1 text-[12px] font-medium text-foreground shadow-md shell-phone:hidden state-closed:animate-fade-out data-[state=delayed-open]:animate-fade-in"
        >
          <span>{label}</span>
          {detail && <span className="font-normal text-muted">{detail}</span>}
          {shortcut && (
            <kbd className="rounded border border-line bg-subtle px-1 py-px font-mono text-[10px] font-normal text-muted">
              {shortcut}
            </kbd>
          )}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

/** The same row as a real link: middle-click, ⌘/Ctrl-click and "open in new tab" work, and a
 *  plain click navigates in place through `onNavigate` (no full page load). */
export function SidebarLink({
  href,
  onNavigate,
  icon: Icon,
  label,
  current,
  trailing,
  className,
  ...data
}: {
  href: string;
  onNavigate: () => void;
  icon?: React.ComponentType<{
    size?: number;
    className?: string;
    "aria-hidden"?: boolean;
  }>;
  label: React.ReactNode;
  current?: boolean;
  trailing?: React.ReactNode;
  className?: string;
} & { [k: `data-${string}`]: string | undefined }) {
  return (
    <a
      href={href}
      data-nav-item=""
      aria-current={current ? "page" : undefined}
      className={cn(sidebarItemClass, "no-underline", className)}
      onClick={(e) => {
        if (
          e.defaultPrevented ||
          e.button !== 0 ||
          e.metaKey ||
          e.ctrlKey ||
          e.shiftKey ||
          e.altKey
        )
          return;
        e.preventDefault();
        onNavigate();
      }}
      {...data}
    >
      {Icon && (
        <Icon
          size={15}
          aria-hidden={true}
          className="flex-none text-sidebar-icon group-hover:text-foreground group-aria-[current=page]:text-foreground"
        />
      )}
      <span className="min-w-0 flex-1 truncate shell-desktop:rail-collapsed:hidden">
        {label}
      </span>
      {trailing}
    </a>
  );
}

export const SidebarItem = React.forwardRef<
  HTMLButtonElement,
  Omit<React.ComponentProps<"button">, "children"> & {
    icon?: React.ComponentType<{
      size?: number;
      className?: string;
      "aria-hidden"?: boolean;
    }>;
    label: React.ReactNode;
    current?: boolean;
    trailing?: React.ReactNode;
  }
>(function SidebarItem(
  { icon: Icon, label, current, trailing, className, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      data-nav-item=""
      aria-current={current ? "page" : undefined}
      className={cn(sidebarItemClass, className)}
      {...props}
    >
      {Icon && (
        <Icon
          size={15}
          aria-hidden={true}
          className="flex-none text-sidebar-icon group-hover:text-foreground group-aria-[current=page]:text-foreground"
        />
      )}
      <span className="min-w-0 flex-1 truncate shell-desktop:rail-collapsed:hidden">
        {label}
      </span>
      {trailing}
    </button>
  );
});

/** Group heading; collapsible when `onToggle` is given (workspace nav), static otherwise. */
export function SidebarGroup({
  label,
  collapsed,
  onToggle,
  children,
  ...data
}: {
  label: string;
  collapsed?: boolean;
  onToggle?: () => void;
  children: React.ReactNode;
} & { [k: `data-${string}`]: string | undefined }) {
  const id = React.useId();
  return (
    <div className="mb-4 last:mb-0" data-nav-group="" {...data}>
      {onToggle ? (
        <button
          type="button"
          data-nav-group-toggle=""
          aria-expanded={!collapsed}
          onClick={onToggle}
          className="group mx-1 mb-0.5 flex h-7 w-full items-center justify-between rounded-md bg-transparent px-1.5 text-left text-[13px] font-normal text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-desktop:rail-collapsed:hidden"
        >
          <span>{label}</span>
          <ChevronDown
            size={12}
            aria-hidden={true}
            className="transition-transform duration-150 motion-reduce:transition-none group-aria-[expanded=false]:-rotate-90"
          />
        </button>
      ) : (
        <p
          id={id}
          className="mx-1 mb-0.5 flex h-7 items-center px-1.5 text-[13px] text-muted shell-desktop:rail-collapsed:hidden"
        >
          {label}
        </p>
      )}
      {!collapsed && (
        <div
          className="grid gap-px"
          role="group"
          aria-labelledby={onToggle ? undefined : id}
        >
          {children}
        </div>
      )}
    </div>
  );
}
