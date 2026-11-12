/* Screen 1 — Agents (HOJA-602-A-16).
 *
 * One row per agent, in the issue list's layout: the same `.lin-row`, the same leading slot
 * order (selection, status glyph, identifier, title, chips, meta), the same `.lin-group`
 * headers. Filtering and grouping are the issue list's own components — `FilterMenu` is handed
 * the agent statuses and owners in its `FilterTeam` shape, and `DisplayPanel` is handed the two
 * groupings this surface can actually produce — so no control here is a copy.
 */

import {useEffect, useMemo, useState} from 'react';
import {ListFilter, SlidersHorizontal} from 'lucide-react';
import {Avatar, GraphStatusIcon, IconBtn, Ring, StatusAgeIcon, cx} from '../graph/shared';
import {DisplayPanel, FilterMenu, defaultDisplay, emptyFilters, type FilterTeam, type LinDisplay, type LinFilters} from '../graph/tasks';
import {stateKey} from '../main';
import {getAgentsAdapter} from './adapter';
import {AgentsListShell, SearchField} from './chrome';
import {AGENT_STATUSES, agentStatus} from './types';
import type {Agent, AgentStatus} from './types';

export type AgentPropKey = 'id' | 'status' | 'harness' | 'capacity' | 'budget' | 'pool' | 'owner' | 'updated';

const AGENT_PROPS: {key: AgentPropKey; label: string}[] = [
  {key: 'id', label: 'ID'},
  {key: 'status', label: 'Status'},
  {key: 'harness', label: 'Harness and model'},
  {key: 'capacity', label: 'Running / capacity'},
  {key: 'budget', label: 'Budget used'},
  {key: 'pool', label: 'Runner pool'},
  {key: 'owner', label: 'Owner'},
  {key: 'updated', label: 'Updated'},
];

function defaultAgentProps(): Record<AgentPropKey, boolean> {
  return {id: true, status: true, harness: true, capacity: true, budget: true, pool: true, owner: true, updated: false};
}

export const AGENT_DISPLAY: LinDisplay & {agentProps: Record<AgentPropKey, boolean>} = {
  ...defaultDisplay,
  grouping: 'status',
  agentProps: defaultAgentProps(),
};

export function statusGlyph(status: AgentStatus) {
  const s = agentStatus(status);
  return <GraphStatusIcon state={{name: s.name, type: s.type, color: s.color}} size={14} />;
}

export function money(usd: number): string {
  return `$${usd.toFixed(2).replace(/\.00$/, '')}`;
}

function budgetTone(a: Agent): string {
  const frac = a.budgetLimitUsd > 0 ? a.budgetUsedUsd / a.budgetLimitUsd : 0;
  if (frac >= 0.9) return 'text-red-600';
  if (frac >= 0.7) return 'text-amber-600';
  return 'text-neutral-600';
}

/* One agent, drawn with the issue row's grid and the issue row's own leading glyphs. The two
 * 14 px columns before the identifier are the same two the issue row has: the status symbol
 * (which explains how long the agent has held that status on hover, like an issue's) and a
 * second symbol. On an issue the second is priority; an agent has no priority, so it carries the
 * capacity ring, which is the agent's own scarce resource. Everything after the identifier is
 * chips in the right-hand meta column, as on an issue. */
function AgentRow({agent, props, onOpen}: {
  agent: Agent; props: Record<AgentPropKey, boolean>; onOpen: (a: Agent) => void;
}) {
  const tone = agentStatus(agent.status);
  const frac = agent.capacity > 0 ? agent.running / agent.capacity : 0;
  return (
    <div className="lin-row" data-agent={agent.key}>
      {props.status && <StatusAgeIcon issue={{state: {name: tone.name, type: tone.type, color: tone.color},
        stateChangedAt: agent.statusChangedAt, updatedAt: agent.updatedAt}} />}
      {props.capacity && (
        <span className="lin-tip" data-tip={`${agent.running} of ${agent.capacity} slots in use`} role="img"
          aria-label={`Capacity: ${agent.running} of ${agent.capacity} slots in use`}>
          <Ring frac={agent.running / Math.max(1, agent.capacity)} size={14} />
        </span>
      )}
      {props.id && <span className="ident">{agent.key}</span>}
      <button type="button" className="title" onClick={() => onOpen(agent)} title={agent.name}>{agent.name}</button>
      <span className="meta">
        {/* Narrow screens keep the row readable instead of clipping the identifier: the extra
            columns return as space allows, widest last. */}
        {props.harness && <span className="hidden sm:inline-flex"><span className="lin-chip" title="Harness and model">{agent.harness} · {agent.model}</span></span>}
        {props.budget && (
          <span className="hidden md:inline-flex">
            <span className={`lin-chip ${budgetTone(agent)}`} title={`${Math.round(frac * 100)}% of the slots in use; budget used of ${money(agent.budgetLimitUsd)}`}>
              {money(agent.budgetUsedUsd)} / {money(agent.budgetLimitUsd)}
            </span>
          </span>
        )}
        {props.pool && <span className="hidden lg:inline-flex"><span className="lin-chip" title="Runner pool">{agent.runnerPool}</span></span>}
        {props.owner && (agent.owner?.name ? <Avatar name={agent.owner.name} title={agent.owner.name} /> : <span className="date">—</span>)}
        {props.updated && <span className="date">{agent.updatedAt.slice(0, 10)}</span>}
      </span>
    </div>
  );
}

export function AgentsPane({workspaceName, onOpen, tab, onTab}: {
  workspaceName: string;
  onOpen: (agent: Agent) => void;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<LinFilters>({...emptyFilters});
  const [display, setDisplay] = useState<LinDisplay & {agentProps: Record<AgentPropKey, boolean>}>(() => ({...AGENT_DISPLAY}));
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    getAgentsAdapter().listAgents()
      .then(rows => { if (live) { setAgents(rows); setError(''); } })
      .catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  /* The filter menu speaks in LinFilters, and so does this list: `status` holds
   * `stateKey(status)`, `assignee` holds owner ids. Nothing is translated on the way in, so a
   * chip's label and the predicate that produced it cannot drift apart. */
  const filterTeam = useMemo<FilterTeam>(() => ({
    id: 'agents',
    states: {nodes: AGENT_STATUSES.map(s => ({id: s.id, name: s.name, color: s.color, type: s.type}))},
    members: {nodes: [...new Map(agents.map(a => [a.owner.id, a.owner])).values()].map(o => ({id: o.id, name: o.name}))},
  }), [agents]);

  const visible = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    const rows = agents.filter(a => {
      if (q && !`${a.key} ${a.name} ${a.harness} ${a.model} ${a.runnerPool}`.toLowerCase().includes(q)) return false;
      if (filters.status.length && !filters.status.includes(stateKey(agentStatus(a.status)))) return false;
      if (filters.assignee.length && !filters.assignee.includes(a.owner.id)) return false;
      return true;
    });
    const dir = display.direction === 'desc' ? -1 : 1;
    rows.sort((a, b) => display.ordering === 'priority' ? a.name.localeCompare(b.name) * dir : (a.updatedAt < b.updatedAt ? dir : -dir));
    return rows;
  }, [agents, filters, display.direction, display.ordering]);

  const groups = useMemo(() => {
    if (display.grouping === 'none') return [{key: 'all', name: '', status: undefined as AgentStatus | undefined, items: visible}];
    const map = new Map<string, {key: string; name: string; status?: AgentStatus; items: Agent[]}>();
    for (const a of visible) {
      const key = display.grouping === 'runner' ? a.runnerPool : a.status;
      const name = display.grouping === 'runner' ? a.runnerPool : agentStatus(a.status).name;
      if (!map.has(key)) map.set(key, {key, name, status: display.grouping === 'status' ? a.status : undefined, items: []});
      map.get(key)!.items.push(a);
    }
    const out = [...map.values()];
    if (display.grouping === 'status') {
      const order = new Map<string, number>(AGENT_STATUSES.map((s, i) => [s.id as string, i]));
      out.sort((a, b) => (order.get(a.key) || 999) - (order.get(b.key) || 999));
    } else out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }, [visible, display.grouping]);

  const collapsedSet = new Set(collapsed);

  const filterChips: {label: string; clear: () => void}[] = [];
  if (filters.q) filterChips.push({label: `“${filters.q}”`, clear: () => setFilters({...filters, q: ''})});
  filters.status.forEach(s => filterChips.push({
    label: AGENT_STATUSES.find(x => stateKey(x) === s)?.name || s,
    clear: () => setFilters({...filters, status: filters.status.filter(x => x !== s)}),
  }));
  filters.assignee.forEach(id => filterChips.push({
    label: filterTeam.members?.nodes.find(m => m.id === id)?.name || id,
    clear: () => setFilters({...filters, assignee: filters.assignee.filter(x => x !== id)}),
  }));

  return (
    <AgentsListShell
      ariaLabel="Agents"
      surface="agents-list"
      workspaceName={workspaceName}
      trail="Agents"
      tabs={[{id: 'agents', name: 'Agents'}, {id: 'queue', name: 'Run queue'}, {id: 'audit', name: 'Audit log'}]}
      activeTab={tab}
      onTab={onTab}
      toolbar={<>
        <SearchField value={filters.q} onChange={q => setFilters({...filters, q})} placeholder="Search agents…" label="Search agents" />
        <span className="spacer" />
        <span style={{position: 'relative'}}>
          <IconBtn label="Add filter" title="Add filter" active={filterOpen}
            onClick={() => { setFilterOpen(v => !v); setDisplayOpen(false); }}>
            <ListFilter size={15} />
          </IconBtn>
          {filterOpen && <FilterMenu filters={filters} setFilters={setFilters} team={filterTeam} teams={[]}
            projects={[]} labels={[]} cycles={[]} onClose={() => setFilterOpen(false)} anchorClass=""
            only={['status', 'assignee', 'content']} />}
        </span>
        <span style={{position: 'relative'}}>
          <IconBtn label="Display options" title="Display options" active={displayOpen}
            onClick={() => { setDisplayOpen(v => !v); setFilterOpen(false); }}>
            <SlidersHorizontal size={15} />
          </IconBtn>
          {displayOpen && <DisplayPanel
            display={display}
            setDisplay={d => setDisplay(prev => ({...prev, ...d}))}
            onClose={() => setDisplayOpen(false)}
            groupingOptions={[['none', 'No grouping'], ['status', 'Status'], ['runner', 'Runner pool']]}
            groupingLabel="Group agents by"
            entityToggles={false}
            propertyRows={AGENT_PROPS.map(p => ({
              key: p.key, label: p.label, checked: display.agentProps[p.key],
              onToggle: () => setDisplay(prev => ({...prev, agentProps: {...prev.agentProps, [p.key]: !prev.agentProps[p.key]}})),
            }))} />}
        </span>
      </>}
      chips={filterChips.length ? <>
        {filterChips.map((c, ix) => (
          <span key={ix} className="lin-fchip">{c.label}
            <button type="button" aria-label={`Remove filter ${c.label}`} onClick={c.clear}>×</button>
          </span>
        ))}
        <button type="button" className="lin-addrow" onClick={() => setFilters({...emptyFilters})}>Clear all</button>
      </> : null}
    >
      {loading && <p className="lin-loading" role="status">Loading agents…</p>}
      {error && <p className="lin-error" role="alert">{error}</p>}
      {!loading && !error && (
        <div className="lin-list">
          {groups.map(g => {
            const isCollapsed = collapsedSet.has(g.key);
            return (
              <section key={g.key} className="lin-group">
                {g.name && (
                  <header className="lin-group-head">
                    <button type="button" className="chev" aria-expanded={!isCollapsed}
                      aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${g.name}`}
                      onClick={() => setCollapsed(c => c.includes(g.key) ? c.filter(x => x !== g.key) : [...c, g.key])}>
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"
                        style={{transform: isCollapsed ? 'rotate(-90deg)' : undefined}} aria-hidden="true">
                        <path d="M4 6.5 8 10.5 12 6.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                    {g.status && statusGlyph(g.status)}
                    <b>{g.name}</b><span className="count">{g.items.length}</span>
                    <span className="grow" />
                  </header>
                )}
                {!isCollapsed && g.items.map(a => (
                  <AgentRow key={a.id} agent={a} props={display.agentProps} onOpen={onOpen} />
                ))}
              </section>
            );
          })}
          {!visible.length && (
            <div className="lin-empty">
              <ListFilter size={30} strokeWidth={1.4} aria-hidden="true" />
              <h2>No agents match</h2>
              <p>Clear the filters to see every agent in this workspace.</p>
            </div>
          )}
        </div>
      )}
    </AgentsListShell>
  );
}
