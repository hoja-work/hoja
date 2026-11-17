/* The list chrome shared by the four agent list screens (HOJA-602-A-16).
 *
 * It is the issue list's chrome, element for element: `.lin-head` breadcrumb, `.lin-viewbar`
 * tabs, `.lin-toolbar` with a `.lin-search`, optional `.lin-filterbar` chips, then a
 * `.lin-scroll` body. Nothing here is restyled or re-spaced, which is why the geometry test can
 * assert a 2 px tolerance instead of comparing two hand-tuned layouts. The only agent-specific
 * decisions are the tab labels and the tablist name.
 */

import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { cx } from "../graph/shared";

export type AgentsTab = { id: string; name: string };

/* The four list screens share one strip. It lives here, not in each pane, because a screen that
 * forgot a tab would silently drop a route from the UI while the router still served it. */
export const AGENTS_TABS: AgentsTab[] = [
  { id: "agents", name: "Agents" },
  { id: "queue", name: "Run queue" },
  { id: "runners", name: "Runners" },
  { id: "audit", name: "Audit log" },
];

export function AgentsListShell({
  ariaLabel,
  surface,
  workspaceName,
  trail,
  tabs,
  activeTab,
  onTab,
  toolbar,
  chips,
  children,
}: {
  ariaLabel: string;
  /** `data-surface` marker: the geometry test finds these screens by it, not by CSS class. */
  surface: string;
  workspaceName: string;
  trail: string;
  tabs: AgentsTab[];
  activeTab: string;
  onTab: (id: string) => void;
  toolbar: ReactNode;
  chips?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      className="flex flex-col min-w-0 h-full bg-panel"
      data-surface={surface}
      aria-label={ariaLabel}
    >
      <header className="flex flex-none items-center border-b border-line gap-2 min-h-12.25 pt-0 pr-5 pb-0 pl-5 border-b-line shell-phone:flex-wrap shell-phone:gap-y-1">
        <nav
          className="flex flex-1 items-center gap-1.75 text-[13px] text-muted min-w-0 shell-phone:min-w-0 shell-phone:flex-wrap shell-phone:gap-y-0.5"
          aria-label="Breadcrumb"
        >
          <span>{workspaceName}</span>
          <span className="text-muted opacity-70">›</span>
          <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
            {trail}
          </span>
        </nav>
      </header>
      <div className="flex flex-none flex-wrap items-center gap-2.5 pt-2.5 pr-5 pb-0 pl-5">
        <div
          className="flex items-center gap-1"
          role="tablist"
          aria-label="Agent view"
        >
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={activeTab === t.id}
              className="border border-transparent hover:bg-subtle hover:text-foreground aria-selected:bg-subtle aria-selected:text-foreground aria-selected:font-semibold shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 pt-1.25 pr-2.75 pb-1.25 pl-2.75 rounded-[99px] bg-transparent text-muted text-[12px] font-medium border-solid"
              onClick={() => onTab(t.id)}
            >
              {t.name}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-none items-center border-b border-line gap-1.5 pt-2 pr-5 pb-2 pl-5 border-b-line">
        {toolbar}
      </div>
      {chips ? (
        <div
          className="flex flex-none flex-wrap items-center border-b border-line gap-1.5 pt-2 pr-5 pb-2 pl-5 border-b-line"
          aria-label="Active filters"
        >
          {chips}
        </div>
      ) : null}
      <div
        className={
          cx("flex-1 overflow-y-auto") + " flex-1 min-h-0 overflow-y-auto"
        }
      >
        {children}
      </div>
    </section>
  );
}

export function SearchField({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <label className="flex flex-1 items-center border border-line gap-2 border-solid rounded-[7px] pt-0 pr-2.5 pb-0 pl-2.5 h-7.5 text-muted bg-panel min-w-50 flex-[0_1_280px]">
      <Search size={14} aria-hidden="true" />
      <input
        className="flex-1 border-0 outline-none placeholder:text-muted shell-phone:min-h-11 pointer-coarse:min-h-11 [outline:0] bg-transparent text-foreground text-[13px] min-w-0"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
      />
    </label>
  );
}
