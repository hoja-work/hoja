/* Screen 1 — Agents (HOJA-602-A-16).
 *
 * One row per agent, in the issue list's layout: the same `.lin-row`, the same leading slot order
 * (status glyph, identifier, title, chips, meta), the same `.lin-group` headers. Filtering and
 * grouping are the issue list's own components — `FilterMenu` is handed the agent statuses in its
 * `FilterTeam` shape, and `DisplayPanel` is handed the two groupings this surface can produce — so
 * no control here is a copy.
 *
 * The rows are real: `agentMembers` from WP04 (app/hoja_server/agent_members.py). Three columns the
 * server does not have — running sessions, harness, owner — render as "not reported" rather than as
 * a plausible number, which is why the row's capacity ring is empty and its tooltip says so. The
 * create form is the only write here, and it is `agentMemberCreate`.
 */

import {useEffect, useMemo, useRef, useState} from 'react';
import {ListFilter, Plus, SlidersHorizontal} from 'lucide-react';
import {GraphStatusIcon, IconBtn, Ring, StatusAgeIcon} from '../graph/shared';
import {DisplayPanel, FilterMenu, defaultDisplay, emptyFilters, type FilterTeam, type LinDisplay, type LinFilters} from '../graph/tasks';
import {stateKey} from '../main';
import {getAgentsAdapter} from './adapter';
import {AGENTS_TABS, AgentsListShell, SearchField} from './chrome';
import {AGENT_STATUSES, agentStatus} from './types';
import type {Agent, AgentStatus} from './types';

export type AgentPropKey = 'id' | 'status' | 'model' | 'capacity' | 'budget' | 'pool' | 'updated';

const AGENT_PROPS: {key: AgentPropKey; label: string}[] = [
  {key: 'id', label: 'ID'},
  {key: 'status', label: 'Status'},
  {key: 'model', label: 'Model'},
  {key: 'capacity', label: 'Running / capacity'},
  {key: 'budget', label: 'Monthly budget'},
  {key: 'pool', label: 'Runner pool'},
  {key: 'updated', label: 'Updated'},
];

function defaultAgentProps(): Record<AgentPropKey, boolean> {
  return {id: true, status: true, model: true, capacity: true, budget: true, pool: true, updated: false};
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

/* What the row shows for a column the server does not report. One spelling, so "not reported"
 * cannot drift into a zero somewhere. */
function NotReported({what}: {what: string}) {
  return <span className="date" title={`${what}: the server does not report this yet`}>—</span>;
}

/* One agent, drawn with the issue row's grid and the issue row's own leading glyphs. The two 14 px
 * columns before the identifier are the same two the issue row has: the status symbol (with its age
 * on hover, like an issue's) and a second symbol. On an issue the second is priority; an agent has
 * no priority, so it carries the capacity ring, which is the agent's own scarce resource. */
function AgentRow({agent, props, onOpen}: {
  agent: Agent; props: Record<AgentPropKey, boolean>; onOpen: (a: Agent) => void;
}) {
  const tone = agentStatus(agent.status);
  const slots = agent.running === null
    ? `${agent.capacity} ${agent.capacity === 1 ? 'slot' : 'slots'}; running sessions are not reported yet`
    : `${agent.running} of ${agent.capacity} slots in use`;
  return (
    <div className="lin-row" data-agent={agent.id} data-archived={agent.archivedAt ? 'true' : undefined}>
      {props.status && <StatusAgeIcon issue={{state: {name: tone.name, type: tone.type, color: tone.color},
        stateChangedAt: agent.statusChangedAt, updatedAt: agent.updatedAt}} />}
      {props.capacity && (
        <span className="lin-tip" data-tip={slots} role="img" aria-label={`Capacity: ${slots}`}>
          <Ring frac={agent.running === null ? 0 : agent.running / Math.max(1, agent.capacity)} size={14} />
        </span>
      )}
      {props.id && <span className="ident">{agent.key}</span>}
      <button type="button" className="title" onClick={() => onOpen(agent)} title={agent.name}>{agent.name}</button>
      <span className="meta">
        {/* Narrow screens keep the row readable instead of clipping the identifier: the extra
            columns return as space allows, widest last. */}
        {props.model && <span className="hidden sm:inline-flex">
          {agent.model ? <span className="lin-chip" title="Model">{agent.model}</span> : <NotReported what="Model" />}
        </span>}
        {props.budget && <span className="hidden md:inline-flex">
          {agent.monthlyBudgetCredits !== null
            ? <span className="lin-chip" title="Credits per calendar month">{agent.monthlyBudgetCredits} credits/mo</span>
            : <NotReported what="Monthly budget" />}
        </span>}
        {props.pool && <span className="hidden lg:inline-flex">
          {agent.runnerPool ? <span className="lin-chip" title="Runner pool">{agent.runnerPool}</span> : <NotReported what="Runner pool" />}
        </span>}
        {props.updated && <span className="date">{agent.updatedAt.slice(0, 10)}</span>}
      </span>
    </div>
  );
}

/* `agentMemberCreate`. The form asks for the columns WP04 stores and nothing else — no harness, no
 * owner, no status, because a create that accepted them would be inventing a write the server
 * drops. Errors are the server's own text (name collisions are a real BAD_USER_INPUT). */
function NewAgentDialog({onClose, onCreated}: {onClose: () => void; onCreated: (agent: Agent) => void}) {
  const [name, setName] = useState('');
  const [capacity, setCapacity] = useState('1');
  const [credits, setCredits] = useState('');
  const [model, setModel] = useState('');
  const [pool, setPool] = useState('');
  const [timezone, setTimezone] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => { nameRef.current?.focus(); }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setError('');
    try {
      const agent = await getAgentsAdapter().createAgent({
        name: trimmed,
        capacity: Number(capacity) || 1,
        monthlyBudgetCredits: credits.trim() === '' ? null : Number(credits),
        model: model.trim() || null,
        runnerPool: pool.trim() || null,
        timezone: timezone.trim() || null,
      });
      onCreated(agent);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = {width: '100%', border: '1px solid var(--line)', borderRadius: 6, padding: '6px 8px', fontSize: 13} as const;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <form className="modal" role="dialog" aria-modal="true" aria-label="New agent" onSubmit={submit}>
        <h2 style={{margin: '0 0 4px', fontSize: 15}}>New agent</h2>
        <p className="lin-note" style={{margin: '0 0 12px'}}>
          An agent is a workspace principal, not a seat: it gets a name and the four limits below.
        </p>
        <label style={{display: 'block', marginBottom: 10}}>
          <span className="lin-note">Name</span>
          <input ref={nameRef} value={name} onChange={e => setName(e.target.value)} required aria-label="Agent name" style={field} />
        </label>
        <label style={{display: 'block', marginBottom: 10}}>
          <span className="lin-note">Capacity (concurrent turns)</span>
          <input type="number" min={1} step={1} value={capacity} onChange={e => setCapacity(e.target.value)} aria-label="Capacity" style={field} />
        </label>
        <label style={{display: 'block', marginBottom: 10}}>
          <span className="lin-note">Monthly budget (credits, blank for the workspace default)</span>
          <input type="number" min={0} step={1} value={credits} onChange={e => setCredits(e.target.value)} aria-label="Monthly budget credits" style={field} />
        </label>
        <label style={{display: 'block', marginBottom: 10}}>
          <span className="lin-note">Model</span>
          <input value={model} onChange={e => setModel(e.target.value)} aria-label="Model" placeholder="claude-sonnet-4" style={field} />
        </label>
        <label style={{display: 'block', marginBottom: 10}}>
          <span className="lin-note">Runner pool</span>
          <input value={pool} onChange={e => setPool(e.target.value)} aria-label="Runner pool" placeholder="linux-x64" style={field} />
        </label>
        <label style={{display: 'block', marginBottom: 12}}>
          <span className="lin-note">Timezone</span>
          <input value={timezone} onChange={e => setTimezone(e.target.value)} aria-label="Timezone" placeholder="UTC" style={field} />
        </label>
        {error && <p className="lin-error" role="alert">{error}</p>}
        <div style={{display: 'flex', gap: 8, justifyContent: 'flex-end'}}>
          <button type="button" className="lin-btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="lin-btn primary" disabled={busy || !name.trim()}>{busy ? 'Creating…' : 'Create agent'}</button>
        </div>
      </form>
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
  const [creating, setCreating] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    getAgentsAdapter().listAgents()
      .then(rows => { if (live) { setAgents(rows); setError(''); } })
      .catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [reloadKey]);

  /* The filter menu speaks in LinFilters, and so does this list: `status` holds
   * `stateKey(status)`. There is no assignee facet: `agent_member` has no owner column, so an owner
   * filter would offer a value that does not exist. */
  const filterTeam = useMemo<FilterTeam>(() => ({
    id: 'agents',
    states: {nodes: AGENT_STATUSES.map(s => ({id: s.id, name: s.name, color: s.color, type: s.type}))},
    members: {nodes: []},
  }), []);

  const visible = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    const rows = agents.filter(a => {
      if (q && !`${a.key} ${a.name} ${a.model || ''} ${a.runnerPool || ''} ${a.timezone || ''}`.toLowerCase().includes(q)) return false;
      if (filters.status.length && !filters.status.includes(stateKey(agentStatus(a.status)))) return false;
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
      const key = display.grouping === 'runner' ? (a.runnerPool || '—') : a.status;
      const name = display.grouping === 'runner' ? (a.runnerPool || 'No runner pool') : agentStatus(a.status).name;
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

  /* Three states that must not look alike: a refused read (401/403/other), a workspace with no
   * agents, and agents that the current filters exclude. Before WP04 all three drew "No agents
   * match", which is how a 403 passed for an empty workspace. */
  const refused = error !== '';
  const forbidden = error !== '' && /admin|forbidden|permission/i.test(error);
  const nothingYet = !loading && !refused && agents.length === 0;

  return (
    <AgentsListShell
      ariaLabel="Agents"
      surface="agents-list"
      workspaceName={workspaceName}
      trail="Agents"
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbar={<>
        <SearchField value={filters.q} onChange={q => setFilters({...filters, q})} placeholder="Search agents…" label="Search agents" />
        <span className="spacer" />
        {/* The issue list's toolbar vocabulary is icon buttons at 28 px, which is what keeps this
            strip the same height as the issue list's. The primary, labelled button lives in the
            empty state, where there is room for it. */}
        <IconBtn label="New agent" title="New agent" onClick={() => setCreating(true)}>
          <Plus size={15} />
        </IconBtn>
        <span style={{position: 'relative'}}>
          <IconBtn label="Add filter" title="Add filter" active={filterOpen}
            onClick={() => { setFilterOpen(v => !v); setDisplayOpen(false); }}>
            <ListFilter size={15} />
          </IconBtn>
          {filterOpen && <FilterMenu filters={filters} setFilters={setFilters} team={filterTeam} teams={[]}
            projects={[]} labels={[]} cycles={[]} onClose={() => setFilterOpen(false)} anchorClass=""
            only={['status', 'content']} />}
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
      {refused && (
        <div className="lin-empty" role="alert" data-state="error">
          <h2>{forbidden ? 'You cannot see this workspace’s agents' : 'Agents could not be loaded'}</h2>
          <p>{error}</p>
          <button type="button" className="lin-btn" onClick={() => setReloadKey(k => k + 1)}>Retry</button>
        </div>
      )}
      {nothingYet && (
        <div className="lin-empty" data-state="empty">
          <h2>No agents yet</h2>
          <p>A workspace with no agent members has nothing to delegate. Create one to give the workspace an identity that can act.</p>
          <button type="button" className="lin-btn primary" onClick={() => setCreating(true)}>
            <Plus size={14} aria-hidden="true" /> New agent
          </button>
        </div>
      )}
      {!loading && !refused && !nothingYet && (
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
            <div className="lin-empty" data-state="no-match">
              <ListFilter size={30} strokeWidth={1.4} aria-hidden="true" />
              <h2>No agents match</h2>
              <p>Clear the filters to see all {agents.length} agent{agents.length === 1 ? '' : 's'} in this workspace.</p>
            </div>
          )}
        </div>
      )}
      {creating && (
        <NewAgentDialog
          onClose={() => setCreating(false)}
          onCreated={agent => { setCreating(false); onOpen(agent); }} />
      )}
    </AgentsListShell>
  );
}
