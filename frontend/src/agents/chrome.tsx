/* The list chrome shared by the four agent list screens (HOJA-602-A-16).
 *
 * It is the issue list's chrome, element for element: `.lin-head` breadcrumb, `.lin-viewbar`
 * tabs, `.lin-toolbar` with a `.lin-search`, optional `.lin-filterbar` chips, then a
 * `.lin-scroll` body. Nothing here is restyled or re-spaced, which is why the geometry test can
 * assert a 2 px tolerance instead of comparing two hand-tuned layouts. The only agent-specific
 * decisions are the tab labels and the tablist name.
 */

import type {ReactNode} from 'react';
import {Search} from 'lucide-react';
import {cx} from '../graph/shared';

export type AgentsTab = {id: string; name: string};

/* The four list screens share one strip. It lives here, not in each pane, because a screen that
 * forgot a tab would silently drop a route from the UI while the router still served it. */
export const AGENTS_TABS: AgentsTab[] = [
  {id: 'agents', name: 'Agents'},
  {id: 'queue', name: 'Run queue'},
  {id: 'runners', name: 'Runners'},
  {id: 'audit', name: 'Audit log'},
];

export function AgentsListShell({ariaLabel, surface, workspaceName, trail, tabs, activeTab, onTab, toolbar, chips, children}: {
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
    <section className="lin-page" data-surface={surface} aria-label={ariaLabel}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <span>{workspaceName}</span>
          <span className="sep">›</span>
          <span className="current">{trail}</span>
        </nav>
      </header>
      <div className="lin-viewbar">
        <div className="lin-tabs" role="tablist" aria-label="Agent view">
          {tabs.map(t => (
            <button key={t.id} type="button" role="tab" aria-selected={activeTab === t.id} className="lin-tab"
              onClick={() => onTab(t.id)}>{t.name}</button>
          ))}
        </div>
      </div>
      <div className="lin-toolbar">{toolbar}</div>
      {chips ? <div className="lin-filterbar" aria-label="Active filters">{chips}</div> : null}
      <div className={cx('lin-scroll')}>{children}</div>
    </section>
  );
}

export function SearchField({value, onChange, placeholder, label}: {value: string; onChange: (v: string) => void; placeholder: string; label: string}) {
  return (
    <label className="lin-search">
      <Search size={14} aria-hidden="true" />
      <input placeholder={placeholder} value={value} onChange={e => onChange(e.target.value)} aria-label={label} />
    </label>
  );
}
