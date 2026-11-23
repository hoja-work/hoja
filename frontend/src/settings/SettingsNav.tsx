import * as React from "react";
import { ArrowLeft, LogOut, Search, X } from "lucide-react";
import { SidebarGroup, SidebarItem, SidebarLink } from "../components/sidebar";
import type { Capability } from "./context";
import {
  SETTINGS_GROUPS,
  findSection,
  matchesQuery,
  visibleSections,
} from "./registry";

/* Settings navigation. It is not a second sidebar: main.tsx renders it inside the same
 * <aside id="workspace-sidebar"> as the workspace navigation (same frame, drawer, focus
 * trap and phone toggle), and every row is the shared SidebarItem. */
export function SettingsNav({
  section,
  isAdmin,
  can,
  account,
  onNavigate,
  onBack,
  onLogout,
}: {
  section: string;
  isAdmin: boolean;
  can?: (c: Capability) => boolean;
  account: { name?: string; email?: string; avatar?: React.ReactNode };
  onNavigate: (id: string) => void;
  onBack: () => void;
  onLogout: () => void;
}) {
  const [query, setQuery] = React.useState("");
  const current = findSection(section)?.id;
  const sections = visibleSections(isAdmin, can).filter((s) =>
    matchesQuery(s, query),
  );
  const searchRef = React.useRef<HTMLInputElement>(null);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-settings-sidebar="">
      <div
        className="mb-2 flex flex-none items-center gap-1"
        data-settings-sidebar-top=""
      >
        <SidebarItem
          icon={ArrowLeft}
          label="Back to app"
          data-settings-back=""
          onClick={onBack}
        />
      </div>
      <label
        data-settings-search=""
        className="mx-0.5 mb-4 flex h-8 flex-none items-center gap-2 rounded-lg border border-line bg-panel px-2.5 text-muted focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20 shell-phone:min-h-11"
      >
        <Search
          size={14}
          className="flex-none text-sidebar-icon"
          aria-hidden="true"
        />
        <input
          ref={searchRef}
          type="search"
          aria-label="Search settings"
          placeholder="Search settings"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && sections[0]) onNavigate(sections[0].id);
            if (e.key === "Escape" && query) {
              e.stopPropagation();
              setQuery("");
            }
          }}
          className="h-full w-full min-w-0 border-0 bg-transparent p-0 text-[13px] text-foreground outline-none placeholder:text-muted shell-phone:text-base [&::-webkit-search-cancel-button]:hidden"
        />
        {query && (
          <button
            type="button"
            aria-label="Clear settings search"
            onClick={() => {
              setQuery("");
              searchRef.current?.focus();
            }}
            className="grid size-5 flex-none place-items-center rounded bg-transparent text-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11"
          >
            <X size={13} aria-hidden="true" />
          </button>
        )}
      </label>
      <nav
        id="settings-navigation"
        aria-label="Settings sections"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-2"
      >
        {SETTINGS_GROUPS.map((group) => {
          const items = sections.filter((s) => s.group === group);
          if (!items.length) return null;
          return (
            <SidebarGroup key={group} label={group} data-settings-group={group}>
              {items.map((s) => (
                <SidebarLink
                  key={s.id}
                  href={`/workspace/settings/${s.id}`}
                  icon={s.icon}
                  label={s.label}
                  current={current === s.id}
                  data-nav-id={s.id}
                  onNavigate={() => onNavigate(s.id)}
                />
              ))}
            </SidebarGroup>
          );
        })}
        {!sections.length && (
          <div
            className="mx-2 my-8 grid justify-items-center gap-1.5 text-center"
            data-settings-search-empty=""
          >
            <Search size={18} className="text-muted" aria-hidden="true" />
            <p className="m-0 font-medium text-foreground">
              No settings match “{query}”
            </p>
            <button
              type="button"
              onClick={() => setQuery("")}
              className="bg-transparent text-sm text-foreground underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
            >
              Clear search
            </button>
          </div>
        )}
      </nav>
      <div
        data-sidebar-bottom=""
        className="-mx-3 mt-auto flex flex-none items-center gap-2 border-t border-line px-4 py-2.5"
      >
        <span
          className="grid size-7 flex-none place-items-center"
          aria-hidden="true"
        >
          {account.avatar}
        </span>
        <span className="grid min-w-0 flex-1">
          <span className="truncate text-[13px] font-medium text-foreground">
            {account.name || "Account"}
          </span>
          {account.email && (
            <span className="truncate text-xs text-muted">{account.email}</span>
          )}
        </span>
        <button
          type="button"
          data-logout-button=""
          aria-label="Log out"
          title="Log out"
          onClick={onLogout}
          className="grid size-7 flex-none place-items-center rounded-lg bg-transparent text-sidebar-icon hover:bg-sidebar-hover hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11"
        >
          <LogOut size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
