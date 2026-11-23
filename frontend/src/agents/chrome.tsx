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
import { MobileNavTrigger } from "../mobileNav";

export type AgentsTab = { id: string; name: string };

/* Touch affordances for the agent lists (HOJA-731), set on each pane's `.lin-list`:
 *   - on a touch screen the whole row is the title button's hit area (an `::after` over the
 *     `relative` row), not the 18 px line of text, so a row is a 42 px tap target;
 *   - a group header's chevron keeps its 14 px glyph and layout but gets a 44 px hit area;
 *   - on a phone a row may wrap, so `PhoneRowBreak` can move its chips to a second line.
 * The row primitive itself is shared with the issue list, so the rules hang off the list. */
export const TOUCH_LIST =
  "pointer-coarse:[&_[data-style-hooks~=lin-row]]:min-h-11 pointer-coarse:[&_[data-style-hooks~=title]]:after:absolute pointer-coarse:[&_[data-style-hooks~=title]]:after:inset-0 pointer-coarse:[&_[data-style-hooks~=chev]]:-m-3 pointer-coarse:[&_[data-style-hooks~=chev]]:p-4 shell-phone:[&_[data-style-hooks~=lin-row]]:flex-wrap shell-phone:[&_[data-style-hooks~=lin-row]]:gap-y-1";

/* Set on the `relative` span that anchors the shared `DisplayPanel` (HOJA-731). The panel caps
 * itself at 75vh whatever room is left under its button, so on a phone its last rows hung below
 * the screen where its own scroll could never bring them; Radix reports the room it has, so the
 * cap also takes that. On touch, its segment buttons, selects and checkbox rows get 44 px rows.
 * The proper home for this is `DisplayPanel` itself; until then the agent screens carry it. */
export const DISPLAY_ANCHOR =
  "relative [&_[data-style-hooks~=lin-display]]:max-h-[min(560px,75vh,var(--radix-popover-content-available-height,75vh))] pointer-coarse:[&_[data-style-hooks~=lin-display]_button]:min-h-11 pointer-coarse:[&_[data-style-hooks~=lin-display]_button]:min-w-11 pointer-coarse:[&_[data-style-hooks~=lin-display]_select]:min-h-11 pointer-coarse:[&_[data-style-hooks~=lin-display]_label]:min-h-11 pointer-coarse:[&_[data-style-hooks~=lin-display]_input[type=checkbox]]:size-5 pointer-coarse:[&_[data-style-hooks~=lin-display]_[data-style-hooks~=dp-row]]:py-0";

/* A zero-height flex item that ends the row's first line on a phone, so the chips that follow
 * it in a list row move under the title instead of squeezing it to a few letters. */
export function PhoneRowBreak() {
  return (
    <span
      className="hidden shell-phone:block basis-full h-0"
      aria-hidden="true"
    />
  );
}

/* The four list screens share one strip. It lives here, not in each pane, because a screen that
 * forgot a tab would silently drop a route from the UI while the router still served it. */
export const AGENTS_TABS: AgentsTab[] = [
  { id: "agents", name: "Agents" },
  { id: "queue", name: "Run queue" },
  { id: "runners", name: "Machines" },
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
  toolbarWraps = false,
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
  /** On a portrait phone the toolbar wraps onto a second line instead of scrolling sideways. */
  toolbarWraps?: boolean;
  chips?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      className="flex flex-col min-w-0 h-full bg-panel"
      data-surface={surface}
      data-style-hooks="lin-page"
      aria-label={ariaLabel}
    >
      <header
        className="flex flex-none items-center border-b border-line gap-2 min-h-12.25 pt-0 pr-5 pb-0 pl-5 border-b-line shell-phone:flex-wrap shell-phone:gap-y-1"
        data-style-hooks="lin-head"
      >
        <MobileNavTrigger />
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
      <div
        className="flex flex-none flex-wrap items-center gap-2.5 pt-2.5 pr-5 pb-0 pl-5"
        data-style-hooks="lin-viewbar"
      >
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
      {/* A list toolbar is one strip of controls, and the agent screens carry more of them than the
          issue list does (status chips, label chips, an action). At phone width the strip scrolls
          instead of painting the last control past the shell's clipped edge; the hidden scrollbar
          keeps the strip the same height as the issue list's, so the screens still line up row for
          row. The utilities sit on the element rather than in a stylesheet: an unlayered rule would
          outrank `overflow-visible` from a caller and could not be undone. */}
      <div
        className={cx(
          "flex flex-none items-center border-b border-line gap-1.5 pt-2 pr-5 pb-2 pl-5 border-b-line pointer-coarse:[&_[data-style-hooks~=lin-icon-btn]]:size-11",
          toolbarWraps
            ? "max-sm:flex-wrap"
            : "shell-phone:overflow-x-auto shell-phone:overflow-y-hidden shell-phone:[scrollbar-width:none] shell-phone:[&::-webkit-scrollbar]:hidden",
        )}
        data-style-hooks="lin-toolbar"
      >
        {toolbar}
      </div>
      {chips ? (
        <div
          className="flex flex-none flex-wrap items-center border-b border-line gap-1.5 pt-2 pr-5 pb-2 pl-5 border-b-line"
          aria-label="Active filters"
          data-style-hooks="lin-filterbar"
        >
          {chips}
        </div>
      ) : null}
      <div
        className={
          cx("flex-1 overflow-y-auto") + " flex-1 min-h-0 overflow-y-auto"
        }
        data-style-hooks="lin-scroll"
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
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  label: string;
  className?: string;
}) {
  return (
    <label
      className={cx(
        "flex flex-1 items-center border border-line gap-2 border-solid rounded-[7px] pt-0 pr-2.5 pb-0 pl-2.5 h-7.5 text-muted bg-panel min-w-50 flex-[0_1_280px] shell-phone:min-w-36 pointer-coarse:h-11",
        className,
      )}
    >
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
