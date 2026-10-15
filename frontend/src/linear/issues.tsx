import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Bell, Check, ChevronDown, ChevronRight, Copy, ExternalLink, Link2, ListFilter, Plus, Search, Settings2, SlidersHorizontal, Trash2, X} from 'lucide-react';
import {gql, relTime, stateKey} from '../main';
import type {Cycle, Issue, IssueActivityEvent, IssueLabel, IssueState, Project, Team, Viewer} from '../main';
import {Avatar, CycleChip, CycleGlyph, DueChip, FavStar, IconBtn, LabelChip, LinMenu, LinearStatusIcon, PriorityIcon, ProjectChip, Ring, SubIssuePill, Tip, cx, fmtDate, fmtFullDate, useDismiss, useFavorite} from './shared';

/* Linear 1:1 Issues list + board + detail. */

export type LinAttachment = {id: string; title?: string; subtitle?: string; url: string; createdAt?: string};
export type LinIssue = Omit<Issue, 'subscribers'> & {
  estimate?: number | null;
  branchName?: string | null;
  url?: string | null;
  parent?: {id: string; identifier: string; title: string} | null;
  subscribers?: {nodes: {id: string; name?: string; avatarUrl?: string}[]};
  attachments?: {nodes: LinAttachment[]};
  activity?: {nodes: IssueActivityEvent[]};
};

export const linIssueFields = `{ id identifier title description createdAt updatedAt stateChangedAt dueDate priority priorityLabel estimate branchName url state { id name color type } team { id key name } project { id name } assignee { id name } creator { id name } cycle { id name number } parent { id identifier title } labels { nodes { id name color } } }`;

/* ---------------- filter + display models ---------------- */

export type LinFilters = {
  q: string;
  status: string[];
  assignee: string[];
  priority: number[];
  labels: string[];
  projects: string[];
  cycles: string[];
  creators: string[];
  subscribers: string[];
  dueFrom: string; dueTo: string;
  createdFrom: string; createdTo: string;
};
export const emptyFilters: LinFilters = {
  q: '', status: [], assignee: [], priority: [], labels: [], projects: [],
  cycles: [], creators: [], subscribers: [], dueFrom: '', dueTo: '', createdFrom: '', createdTo: '',
};

export type LinDisplay = {
  grouping: 'none' | 'status' | 'priority' | 'assignee' | 'labels' | 'project';
  ordering: 'created' | 'updated' | 'priority';
  direction: 'desc' | 'asc';
  hideCompleted: boolean;
  showSubIssues: boolean;
  showEmptyColumns: boolean;
  hiddenColumns: string[];
  props: Record<'id' | 'status' | 'assignee' | 'priority' | 'project' | 'dueDate' | 'cycle' | 'labels' | 'created' | 'updated', boolean>;
};
export const defaultDisplay: LinDisplay = {
  grouping: 'status', ordering: 'updated', direction: 'desc',
  hideCompleted: false, showSubIssues: true, showEmptyColumns: true, hiddenColumns: [],
  props: {id: true, status: true, assignee: true, priority: true, project: true, dueDate: true, cycle: true, labels: true, created: true, updated: true},
};

function inRange(iso: string | undefined | null, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!iso) return false;
  const d = iso.slice(0, 10);
  return (!from || d >= from) && (!to || d <= to);
}

export function applyLinFilters(issues: LinIssue[], f: LinFilters): LinIssue[] {
  const q = f.q.trim().toLowerCase();
  return issues.filter(i => {
    if (q && !`${i.identifier} ${i.title}`.toLowerCase().includes(q)) return false;
    if (f.status.length && !(i.state && f.status.includes(stateKey(i.state)))) return false;
    if (f.assignee.length) {
      const aid = i.assignee?.id || 'unassigned';
      if (!f.assignee.includes(aid)) return false;
    }
    if (f.priority.length && !f.priority.includes(i.priority || 0)) return false;
    if (f.labels.length && !i.labels?.nodes.some(l => f.labels.includes(l.id))) return false;
    if (f.projects.length && !(i.project && f.projects.includes(i.project.id))) return false;
    if (f.cycles.length) {
      const cid = i.cycle?.id || 'none';
      if (!f.cycles.includes(cid)) return false;
    }
    if (f.creators.length && !(i.creator && f.creators.includes(i.creator.id))) return false;
    if (f.subscribers.length && !i.subscribers?.nodes.some(s => f.subscribers.includes(s.id))) return false;
    if (!inRange(i.dueDate, f.dueFrom, f.dueTo)) return false;
    if (!inRange(i.createdAt, f.createdFrom, f.createdTo)) return false;
    return true;
  });
}

export function orderLinIssues(issues: LinIssue[], d: LinDisplay): LinIssue[] {
  const arr = [...issues];
  const dir = d.direction === 'desc' ? -1 : 1;
  arr.sort((a, b) => {
    if (d.ordering === 'priority') return ((a.priority || 5) - (b.priority || 5)) * dir;
    const ak = d.ordering === 'created' ? a.createdAt : a.updatedAt;
    const bk = d.ordering === 'created' ? b.createdAt : b.updatedAt;
    return ((ak || '').localeCompare(bk || '')) * dir;
  });
  return arr;
}

export function groupLinIssues(issues: LinIssue[], d: LinDisplay, states: IssueState[]): {key: string; name: string; state?: IssueState; items: LinIssue[]}[] {
  if (d.grouping === 'none') return [{key: 'all', name: '', items: issues}];
  const groups = new Map<string, {key: string; name: string; state?: IssueState; items: LinIssue[]}>();
  const keyOf = (i: LinIssue): {key: string; name: string; state?: IssueState} => {
    if (d.grouping === 'status') {
      const s = i.state;
      return s ? {key: stateKey(s), name: s.name, state: s} : {key: 'none', name: 'No status'};
    }
    if (d.grouping === 'priority') {
      const names = ['No priority', 'Urgent', 'High', 'Medium', 'Low'];
      return {key: String(i.priority || 0), name: names[i.priority || 0] || 'No priority'};
    }
    if (d.grouping === 'assignee') return {key: i.assignee?.id || 'unassigned', name: i.assignee?.name || 'Unassigned'};
    if (d.grouping === 'labels') {
      const l = i.labels?.nodes[0];
      return {key: l?.id || 'none', name: l?.name || 'No labels'};
    }
    if (d.grouping === 'project') return {key: i.project?.id || 'none', name: i.project?.name || 'No project'};
    return {key: 'all', name: ''};
  };
  for (const i of issues) {
    const k = keyOf(i);
    if (!groups.has(k.key)) groups.set(k.key, {...k, items: []});
    groups.get(k.key)!.items.push(i);
  }
  let out = [...groups.values()];
  if (d.grouping === 'status') {
    const order = new Map(states.map((s, idx) => [stateKey(s), idx]));
    out.sort((a, b) => (order.get(a.key) ?? 999) - (order.get(b.key) ?? 999));
  } else {
    out.sort((a, b) => a.name.localeCompare(b.name));
  }
  return out;
}

/* ---------------- filter menu ---------------- */

const FILTER_DEFS = [
  {key: 'status', label: 'Status', icon: '◔'},
  {key: 'assignee', label: 'Assignee', icon: '◉'},
  {key: 'creator', label: 'Creator', icon: '✎'},
  {key: 'priority', label: 'Priority', icon: '▮'},
  {key: 'labels', label: 'Labels', icon: '◈'},
  {key: 'dates', label: 'Dates', icon: '◷'},
  {key: 'project', label: 'Project', icon: '▣'},
  {key: 'cycle', label: 'Cycle', icon: '↻'},
  {key: 'subscribers', label: 'Subscribers', icon: '◎'},
  {key: 'content', label: 'Content', icon: '≡'},
] as const;
const FILTER_UNAVAILABLE = ['AI filter', 'Advanced filter', 'Agent', 'Agent Session', 'Relations', 'Suggested label', 'Project properties', 'Initiative', 'Added to cycle', 'Customers', 'Auto-closed', 'Links', 'Template'];

export function FilterMenu({filters, setFilters, team, teams, projects, labels, cycles, onClose, anchorClass}: {
  filters: LinFilters; setFilters: (f: LinFilters) => void;
  team?: Team; teams: Team[]; projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  onClose: () => void; anchorClass?: string;
}) {
  const ref = useDismiss(onClose);
  const [sub, setSub] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const members = team?.members?.nodes || [];
  const toggle = (key: keyof LinFilters, id: string | number) => {
    const arr = [...(filters[key] as (string | number)[])];
    const ix = arr.indexOf(id as never);
    if (ix >= 0) arr.splice(ix, 1); else arr.push(id as never);
    setFilters({...filters, [key]: arr});
  };
  const summary = (key: keyof LinFilters, total: number) => {
    const n = (filters[key] as unknown[]).length;
    return n ? `${n} selected` : total ? `${total} options` : '';
  };
  const opt = (key: 'status' | 'assignee' | 'priority' | 'labels' | 'projects' | 'cycles' | 'creators' | 'subscribers', id: string | number, label: React.ReactNode) => (
    <button type="button" key={String(id)} className="fm-opt" aria-checked={(filters[key] as unknown[]).includes(id)}
      onClick={() => toggle(key, id)}>
      <span className="fm-ic">{(filters[key] as unknown[]).includes(id) ? <Check size={14} /> : <span style={{width: 14}} />}</span>
      <span style={{flex: 1}}>{label}</span>
    </button>
  );
  const states = team?.states.nodes || [];
  const dateRow = (label: string, from: keyof LinFilters, to: keyof LinFilters) => (
    <div className="fm-opt" style={{cursor: 'default'}}>
      <span style={{width: 90, color: 'var(--muted)', fontSize: 12}}>{label}</span>
      <input type="date" value={filters[from] as string} onChange={e => setFilters({...filters, [from]: e.target.value})} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '4px 6px', fontSize: 12}} />
      <input type="date" value={filters[to] as string} onChange={e => setFilters({...filters, [to]: e.target.value})} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '4px 6px', fontSize: 12}} />
    </div>
  );
  return (
    <div ref={ref} className={cx('lin-popover lin-filtermenu', anchorClass)} role="dialog" aria-label="Add filter">
      {!sub && <>
        <div className="fm-search"><input autoFocus placeholder="Add filter…" value={search} onChange={e => setSearch(e.target.value)} /></div>
        <div style={{padding: '4px 0'}}>
          {FILTER_DEFS.filter(d => d.label.toLowerCase().includes(search.toLowerCase())).map(d => (
            <button type="button" key={d.key} className="fm-item" onClick={() => setSub(d.key)}>
              <span className="fm-ic">{d.icon}</span>
              <span style={{flex: 1}}>{d.label}</span>
              <span className="fm-val">{d.key === 'status' ? summary('status', states.length) : d.key === 'assignee' ? summary('assignee', members.length) : d.key === 'labels' ? summary('labels', labels.length) : d.key === 'project' ? summary('projects', projects.length) : d.key === 'cycle' ? summary('cycles', cycles.length) : ''}</span>
              <ChevronRight size={14} color="var(--muted)" />
            </button>
          ))}
          {FILTER_UNAVAILABLE.filter(l => l.toLowerCase().includes(search.toLowerCase())).map(l => (
            <Tip key={l} label="Not available in Hoja yet" className="fm-item" >
              <span className="fm-item" style={{opacity: .45, cursor: 'not-allowed', width: '100%'}}>
                <span className="fm-ic">○</span><span style={{flex: 1}}>{l}</span>
              </span>
            </Tip>
          ))}
        </div>
        <div style={{padding: 10, borderTop: '1px solid var(--line)'}}>
          <button type="button" className="lin-btn" style={{width: '100%', justifyContent: 'center'}}
            onClick={() => setFilters({...emptyFilters})}>Clear all filters</button>
        </div>
      </>}
      {sub && <>
        <button type="button" className="fm-back" onClick={() => setSub(null)}>‹ {FILTER_DEFS.find(d => d.key === sub)?.label}</button>
        <div className="fm-sub">
          {sub === 'status' && states.map(s => opt('status', stateKey(s), <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><LinearStatusIcon state={s} />{s.name}</span>))}
          {sub === 'assignee' && <>{opt('assignee', 'unassigned', 'Unassigned')}{members.map(m => opt('assignee', m.id, <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><Avatar name={m.name} size={18} />{m.name || m.email}</span>))}</>}
          {sub === 'creator' && members.map(m => opt('creators', m.id, <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><Avatar name={m.name} size={18} />{m.name || m.email}</span>))}
          {sub === 'priority' && [['1', 'Urgent'], ['2', 'High'], ['3', 'Medium'], ['4', 'Low'], ['0', 'No priority']].map(([v, l]) => opt('priority', Number(v), <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><PriorityIcon priority={Number(v)} />{l}</span>))}
          {sub === 'labels' && labels.map(l => opt('labels', l.id, <LabelChip label={l} />))}
          {sub === 'dates' && <>{dateRow('Due date', 'dueFrom', 'dueTo')}{dateRow('Created', 'createdFrom', 'createdTo')}</>}
          {sub === 'project' && projects.map(p => opt('projects', p.id, p.name))}
          {sub === 'cycle' && <>{opt('cycles', 'none', 'No cycle')}{cycles.filter(c => c.team?.id === team?.id).map(c => opt('cycles', c.id, c.name || `Cycle ${c.number}`))}</>}
          {sub === 'subscribers' && members.map(m => opt('subscribers', m.id, <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><Avatar name={m.name} size={18} />{m.name || m.email}</span>))}
          {sub === 'content' && (
            <div style={{padding: 8}}>
              <input placeholder="Search title or identifier…" value={filters.q} onChange={e => setFilters({...filters, q: e.target.value})}
                style={{width: '100%', border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} />
            </div>
          )}
        </div>
      </>}
    </div>
  );
}

/* ---------------- display options panel ---------------- */

export function DisplayPanel({display, setDisplay, onClose, anchorClass}: {
  display: LinDisplay; setDisplay: (d: LinDisplay) => void; onClose: () => void; anchorClass?: string;
}) {
  const ref = useDismiss(onClose);
  const set = (patch: Partial<LinDisplay>) => setDisplay({...display, ...patch});
  const toggleProp = (k: keyof LinDisplay['props']) => set({props: {...display.props, [k]: !display.props[k]}});
  const PROP_LABELS: [keyof LinDisplay['props'], string][] = [
    ['id', 'ID'], ['status', 'Status'], ['assignee', 'Assignee'], ['priority', 'Priority'],
    ['project', 'Project'], ['dueDate', 'Due date'], ['cycle', 'Cycle'], ['labels', 'Labels'],
    ['created', 'Created'], ['updated', 'Updated'],
  ];
  const UNAVAILABLE_PROPS = ['Milestone', 'Links', 'Customers', 'Customer revenue', 'Time in status'];
  return (
    <div ref={ref} className={cx('lin-popover lin-display', anchorClass)} role="dialog" aria-label="Display options">
      <div className="dp-sec">
        <div className="dp-title">View</div>
        <div className="dp-seg" role="group" aria-label="List or board">
          {(['List', 'Board'] as const).map(v => (
            <button key={v} type="button" aria-pressed={(display as unknown as {layout: string}).layout !== 'Board' ? v === 'List' : v === 'Board'}
              onClick={() => set({...(display as object), layout: v} as Partial<LinDisplay>)}>{v}</button>
          ))}
        </div>
      </div>
      <div className="dp-sec">
        <div className="dp-title">Grouping</div>
        <select aria-label="Group issues by" value={display.grouping}
          onChange={e => set({grouping: e.target.value as LinDisplay['grouping']})}>
          <option value="none">No grouping</option>
          <option value="status">Status</option>
          <option value="priority">Priority</option>
          <option value="assignee">Assignee</option>
          <option value="labels">Labels</option>
          <option value="project">Project</option>
        </select>
      </div>
      <div className="dp-sec">
        <div className="dp-title">Ordering</div>
        <div className="dp-row">
          <select aria-label="Order issues by" value={display.ordering}
            onChange={e => set({ordering: e.target.value as LinDisplay['ordering']})}>
            <option value="updated">Updated</option>
            <option value="created">Created</option>
            <option value="priority">Priority</option>
          </select>
          <div className="dp-seg">
            <button type="button" aria-pressed={display.direction === 'desc'} onClick={() => set({direction: 'desc'})}>Desc</button>
            <button type="button" aria-pressed={display.direction === 'asc'} onClick={() => set({direction: 'asc'})}>Asc</button>
          </div>
        </div>
        <div className="dp-row"><label><input type="checkbox" className="lin-check" checked={display.hideCompleted}
          onChange={e => set({hideCompleted: e.target.checked})} /> Hide completed issues</label></div>
        <div className="dp-row"><label><input type="checkbox" className="lin-check" checked={display.showSubIssues}
          onChange={e => set({showSubIssues: e.target.checked})} /> Show sub-issues</label></div>
        <div className="dp-row"><label><input type="checkbox" className="lin-check" checked={display.showEmptyColumns}
          onChange={e => set({showEmptyColumns: e.target.checked})} /> Show empty columns</label></div>
      </div>
      <div className="dp-sec">
        <div className="dp-title">Display properties</div>
        {PROP_LABELS.map(([k, label]) => (
          <div className="dp-row" key={k}><label><input type="checkbox" className="lin-check"
            checked={display.props[k]} onChange={() => toggleProp(k)} /> {label}</label></div>
        ))}
        {UNAVAILABLE_PROPS.map(label => (
          <div className="dp-row" key={label} style={{opacity: .45}}>
            <Tip label="Not available in Hoja yet"><label style={{cursor: 'not-allowed'}}><input type="checkbox" className="lin-check" disabled /> {label}</label></Tip>
          </div>
        ))}
      </div>
      <div className="dp-foot">
        <button type="button" onClick={() => set({...defaultDisplay, layout: (display as unknown as {layout: 'List' | 'Board'}).layout} as LinDisplay)}>Reset to team default</button>
        <button type="button" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

/* ---------------- issue row ---------------- */

export function IssueRow({issue, display, selected, onSelect, onOpen, childCount, showCheckbox}: {
  issue: LinIssue; display: LinDisplay; selected: boolean;
  onSelect: (id: string, on: boolean) => void; onOpen: (issue: LinIssue) => void;
  childCount: number; showCheckbox: boolean;
}) {
  const p = display.props;
  return (
    <div className={cx('lin-row', selected && 'selected')}>
      {showCheckbox && (
        <span className="rowcheck">
          <input type="checkbox" aria-label={`Select ${issue.identifier}`} checked={selected}
            onChange={e => onSelect(issue.id, e.target.checked)} onClick={e => e.stopPropagation()} />
        </span>
      )}
      {p.priority && <PriorityIcon priority={issue.priority} />}
      {p.id && <span className="ident">{issue.identifier}</span>}
      {p.status && <LinearStatusIcon state={issue.state} />}
      <button type="button" className="title" onClick={() => onOpen(issue)} title={issue.title}>{issue.title}</button>
      {childCount > 0 && <SubIssuePill done={0} total={childCount} />}
      <span className="meta">
        {p.labels && issue.labels?.nodes.map(l => <LabelChip key={l.id} label={l} />)}
        {p.project && issue.project && <ProjectChip name={issue.project.name} />}
        {p.cycle && issue.cycle && <CycleChip number={issue.cycle.number} name={issue.cycle.name} />}
        {p.dueDate && issue.dueDate && <DueChip iso={issue.dueDate} />}
        {p.assignee && (issue.assignee ? <Avatar name={issue.assignee.name} /> : <span className="date">—</span>)}
        {p.created && <span className="date" title={`Created ${fmtFullDate(issue.createdAt)}`} aria-label={`Created ${fmtFullDate(issue.createdAt)}`}>{fmtDate(issue.createdAt)}</span>}
        {p.updated && <span className="date" title={`Updated ${fmtFullDate(issue.updatedAt)}`} aria-label={`Updated ${fmtFullDate(issue.updatedAt)}`}>{fmtDate(issue.updatedAt)}</span>}
      </span>
    </div>
  );
}

/* ---------------- board ---------------- */

function BoardCard({issue, onOpen, onDragStart}: {issue: LinIssue; onOpen: (i: LinIssue) => void; onDragStart: (e: React.DragEvent, i: LinIssue) => void}) {
  return (
    <button type="button" className="lin-bcard" draggable
      onDragStart={e => onDragStart(e, issue)}
      onClick={() => onOpen(issue)} aria-label={`${issue.identifier}: ${issue.title}`}>
      <span className="top">
        <span className="ident">{issue.identifier}</span>
        <LinearStatusIcon state={issue.state} size={13} />
        <span style={{flex: 1}} />
        <PriorityIcon priority={issue.priority} size={13} />
      </span>
      <span className="ttl">{issue.title}</span>
      {(issue.labels?.nodes.length || issue.project || issue.dueDate) && (
        <span className="chips">
          {issue.labels?.nodes.map(l => <LabelChip key={l.id} label={l} />)}
          {issue.project && <ProjectChip name={issue.project.name} />}
          {issue.dueDate && <DueChip iso={issue.dueDate} />}
        </span>
      )}
      <span className="row">
        {issue.assignee ? <Avatar name={issue.assignee.name} size={18} /> : null}
        <span className="assignee-name">{issue.assignee?.name || 'Unassigned'}</span>
        <span>{fmtDate(issue.createdAt)}</span>
      </span>
    </button>
  );
}

function LinearBoard({issues, states, onOpen, onMoved, onNew, display, setDisplay}: {
  issues: LinIssue[]; states: IssueState[]; onOpen: (i: LinIssue) => void;
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onNew: () => void; display: LinDisplay; setDisplay: (d: LinDisplay) => void;
}) {
  const [colMenu, setColMenu] = useState<string | null>(null);
  const [dropCol, setDropCol] = useState<string | null>(null);
  const [error, setError] = useState('');
  const dragIssue = useRef<LinIssue | null>(null);
  const cols = states.map(s => ({state: s, items: issues.filter(i => i.state && stateKey(i.state) === stateKey(s))}));
  const visible = cols.filter(c => !display.hiddenColumns.includes(stateKey(c.state)) && (display.showEmptyColumns || c.items.length));
  const hidden = cols.filter(c => display.hiddenColumns.includes(stateKey(c.state)));
  const onDropCol = async (state: IssueState) => {
    const issue = dragIssue.current;
    dragIssue.current = null;
    setDropCol(null);
    if (!issue || (issue.state && stateKey(issue.state) === stateKey(state))) return;
    try { await onMoved(issue.id, state); } catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="lin-board" role="list" aria-label="Board">
      {visible.map(({state, items}) => (
        <section key={stateKey(state)} className="lin-bcol" aria-label={state.name}
          onDragOver={e => { e.preventDefault(); setDropCol(stateKey(state)); }}
          onDragLeave={() => setDropCol(cur => cur === stateKey(state) ? null : cur)}
          onDrop={e => { e.preventDefault(); void onDropCol(state); }}
          style={dropCol === stateKey(state) ? {outline: '2px dashed var(--accent)', outlineOffset: -2} : undefined}>
          <header>
            <LinearStatusIcon state={state} size={13} />
            <span>{state.name}</span><span className="count">{items.length}</span>
            <span className="sp" />
            <button type="button" aria-label={`Add issue to ${state.name}`} onClick={onNew}><Plus size={14} /></button>
            <span style={{position: 'relative'}}>
              <button type="button" aria-label={`${state.name} column options`} onClick={() => setColMenu(m => m === stateKey(state) ? null : stateKey(state))}>···</button>
              {colMenu === stateKey(state) && (
                <LinMenu onClose={() => setColMenu(null)} className="lin-dd-panel" items={[
                  {key: 'hide', label: 'Hide column', onSelect: () => setDisplay({...display, hiddenColumns: [...display.hiddenColumns, stateKey(state)]})},
                ]} />
              )}
            </span>
          </header>
          <div className="lin-bcards">
            {items.map(i => <BoardCard key={i.id} issue={i} onOpen={onOpen}
              onDragStart={(e, it) => { dragIssue.current = it; e.dataTransfer.effectAllowed = 'move'; }} />)}
            {!items.length && <div className="lin-bempty">No issues</div>}
          </div>
        </section>
      ))}
      {hidden.length > 0 && (
        <div className="lin-bcol lin-bcol-hidden">
          <header><span className="count">{hidden.length} hidden</span><span className="sp" />
            <button type="button" onClick={() => setDisplay({...display, hiddenColumns: []})}>Show all</button>
          </header>
          <div className="lin-bcards">
            {hidden.map(({state}) => (
              <button key={stateKey(state)} type="button" className="lin-hidden-col"
                title={`Show ${state.name} column`}
                onClick={() => setDisplay({...display, hiddenColumns: display.hiddenColumns.filter(k => k !== stateKey(state))})}>
                <LinearStatusIcon state={state} size={13} />
                <span className="grow">{state.name}</span>
                <span className="show">Show</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {error && <p className="lin-error" role="alert">{error} <button type="button" onClick={() => setError('')}>Dismiss</button></p>}
    </div>
  );
}

/* ---------------- bulk actions ---------------- */

function BulkBar({selected, issues, team, teams, cycles, labels, onClear, onDone}: {
  selected: string[]; issues: LinIssue[]; team?: Team; teams: Team[]; cycles: Cycle[];
  labels: IssueLabel[]; onClear: () => void; onDone: () => void;
}) {
  const [action, setAction] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ref = useDismiss(() => { setAction(null); });
  const run = async (input: Record<string, unknown>, labelIds?: string[]) => {
    setBusy(true); setError('');
    try {
      for (const id of selected) {
        const d = await gql(`mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}`,
          {id, input: labelIds ? {labelIds} : input});
        if (!d.issueUpdate?.success) throw new Error('Update was rejected');
      }
      onDone();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const states = team?.states.nodes || [];
  const members = team?.members?.nodes || [];
  const teamCycles = cycles.filter(c => c.team?.id === team?.id);
  return (
    <div className="lin-multibar" role="toolbar" aria-label="Bulk actions">
      <span className="n">{selected.length} selected</span>
      <span className="div" />
      <span style={{position: 'relative'}} ref={ref}>
        <button type="button" onClick={() => setAction(a => a ? null : 'menu')} disabled={busy}>Actions <ChevronDown size={13} /></button>
        {action && (
          <div className="lin-popover" style={{bottom: 'calc(100% + 8px)', left: 0, minWidth: 240, padding: 5}}>
            {!action || action === 'menu' ? <>
              {(['status', 'priority', 'assignee', 'cycle', 'label'] as const).map(a => (
                <button key={a} type="button" className="lin-menu-item" onClick={() => setAction(a)}>
                  <span className="lin-menu-label">{a === 'status' ? 'Set status…' : a === 'priority' ? 'Set priority…' : a === 'assignee' ? 'Set assignee…' : a === 'cycle' ? 'Add to cycle…' : 'Add label…'}</span>
                  <ChevronRight size={13} />
                </button>
              ))}
              <div className="lin-menu-divider" />
              <button type="button" className="lin-menu-item" onClick={() => { setAction(null); void (async () => { setBusy(true); try { for (const id of selected) await gql(`mutation($id:String!){issueArchive(id:$id){success}}`, {id}); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } })(); }}>Archive</button>
              <button type="button" className="lin-menu-item danger" onClick={() => { if (window.confirm(`Delete ${selected.length} issues? This cannot be undone.`)) { setAction(null); void (async () => { setBusy(true); try { for (const id of selected) await gql(`mutation($id:String!){issueDelete(id:$id){success}}`, {id}); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } })(); } }}>Delete</button>
            </> : <>
              <button type="button" className="lin-menu-item" onClick={() => setAction('menu')}>‹ Back</button>
              <div className="lin-menu-divider" />
              <div style={{maxHeight: 260, overflowY: 'auto'}}>
                {action === 'status' && states.map(s => (
                  <button key={s.id} type="button" className="lin-menu-item" disabled={busy}
                    onClick={() => void run({stateId: s.id})}><LinearStatusIcon state={s} /><span className="lin-menu-label">{s.name}</span></button>))}
                {action === 'priority' && [[1, 'Urgent'], [2, 'High'], [3, 'Medium'], [4, 'Low'], [0, 'No priority']].map(([v, l]) => (
                  <button key={v} type="button" className="lin-menu-item" disabled={busy}
                    onClick={() => void run({priority: v})}><PriorityIcon priority={v as number} /><span className="lin-menu-label">{l}</span></button>))}
                {action === 'assignee' && <><button type="button" className="lin-menu-item" disabled={busy} onClick={() => void run({assigneeId: null})}>Unassigned</button>
                  {members.map(m => <button key={m.id} type="button" className="lin-menu-item" disabled={busy} title={m.name || m.email} onClick={() => void run({assigneeId: m.id})}><Avatar name={m.name} size={18} /><span className="lin-menu-label">{m.name || m.email}</span></button>)}</>}
                {action === 'cycle' && <><button type="button" className="lin-menu-item" disabled={busy} onClick={() => void run({cycleId: null})}>Remove from cycle</button>
                  {teamCycles.map(c => <button key={c.id} type="button" className="lin-menu-item" disabled={busy} onClick={() => void run({cycleId: c.id})}><CycleGlyph /><span className="lin-menu-label">{c.name || `Cycle ${c.number}`}</span></button>)}</>}
                {action === 'label' && labels.map(l => (
                  <button key={l.id} type="button" className="lin-menu-item" disabled={busy}
                    onClick={() => { const first = issues.find(i => selected.includes(i.id)); const ids = [...new Set([...(first?.labels?.nodes || []).map(x => x.id), l.id])]; void run({}, ids); }}>
                    <LabelChip label={l} /></button>))}
              </div>
            </>}
            {error && <p className="lin-error" style={{margin: 6}}>{error}</p>}
          </div>
        )}
      </span>
      <button type="button" onClick={onClear} aria-label="Clear selection"><X size={14} /></button>
    </div>
  );
}

/* ---------------- the team issues page ---------------- */

type SavedView = {id: string; name: string; query: string; state: string};

export function LinearTeamIssues({team, teams, viewer, orgId, workspaceName, projects, labels, cycles, onIssue, onMoved, onNew, revision, layout, onLayoutChange, onNavigate}: {
  team?: Team; teams: Team[]; viewer: Viewer; orgId: string; workspaceName: string;
  projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  onIssue: (issue: LinIssue) => void;
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onNew: () => void; revision: number;
  layout: 'List' | 'Board'; onLayoutChange: (l: 'List' | 'Board') => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  const [rows, setRows] = useState<LinIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState('');
  const [tab, setTab] = useState<'active' | 'backlog' | 'all'>('all');
  const [filters, setFilters] = useState<LinFilters>({...emptyFilters});
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [views, setViews] = useState<SavedView[]>([]);
  const [viewsOpen, setViewsOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const displayKey = `hoja_lin_display_${orgId}_${team?.id || 'all'}`;
  const [display, setDisplayState] = useState<LinDisplay>(() => {
    try {
      const raw = localStorage.getItem(displayKey);
      if (raw) return {...defaultDisplay, ...JSON.parse(raw)};
    } catch { /* ignore */ }
    return {...defaultDisplay};
  });
  const setDisplay = (d: LinDisplay) => {
    setDisplayState(d);
    try { localStorage.setItem(displayKey, JSON.stringify(d)); } catch { /* ignore */ }
  };
  const effDisplay: LinDisplay = {...display, layout} as LinDisplay;

  useEffect(() => {
    try {
      const raw = localStorage.getItem(`hoja_saved_views_${orgId}`);
      if (raw) setViews(JSON.parse(raw).filter((v: {kind?: string}) => (v.kind || 'issues') === 'issues'));
    } catch { /* ignore */ }
  }, [orgId]);

  useEffect(() => {
    if (!team) return;
    let live = true;
    setLoading(true); setError(''); setMoreError('');
    setCursor(null); setHasMore(false);
    gql(`query TeamIssues($filter:IssueFilter){issues(first:100,filter:$filter,orderBy:updatedAt){nodes ${linIssueFields} pageInfo{hasNextPage endCursor}}}`,
      {filter: {team: {id: {eq: team.id}}}})
      .then(d => {
        if (!live) return;
        setRows(d.issues.nodes);
        setCursor(d.issues.pageInfo?.endCursor || null);
        setHasMore(Boolean(d.issues.pageInfo?.hasNextPage));
      })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [team?.id, revision]);

  const loadMore = async () => {
    if (loadingMore || !hasMore || !cursor || !team) return;
    setLoadingMore(true); setMoreError('');
    try {
      const d = await gql(`query TeamIssuesMore($filter:IssueFilter,$after:String){issues(first:100,after:$after,filter:$filter,orderBy:updatedAt){nodes ${linIssueFields} pageInfo{hasNextPage endCursor}}}`,
        {filter: {team: {id: {eq: team.id}}}, after: cursor});
      const page = d.issues;
      if (page.pageInfo?.hasNextPage && (!page.pageInfo.endCursor || page.pageInfo.endCursor === cursor || !page.nodes.length))
        throw new Error('Issue pagination did not advance.');
      setRows(old => {
        const ids = new Set(old.map(i => i.id));
        return [...old, ...page.nodes.filter((i: LinIssue) => !ids.has(i.id))];
      });
      setCursor(page.pageInfo?.endCursor || null);
      setHasMore(Boolean(page.pageInfo?.hasNextPage));
    } catch (e) { setMoreError((e as Error).message); } finally { setLoadingMore(false); }
  };

  const childCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of rows) {
      const pid = i.parent?.id;
      if (pid) m.set(pid, (m.get(pid) || 0) + 1);
    }
    return m;
  }, [rows]);

  const scoped = useMemo(() => {
    let base = rows;
    if (!effDisplay.showSubIssues) base = base.filter(i => !i.parent);
    if (effDisplay.hideCompleted) base = base.filter(i => i.state?.type !== 'completed' && i.state?.type !== 'canceled');
    if (tab === 'active') base = base.filter(i => i.state?.type === 'started' || i.state?.type === 'unstarted');
    if (tab === 'backlog') base = base.filter(i => i.state?.type === 'backlog');
    return orderLinIssues(applyLinFilters(base, filters), effDisplay);
  }, [rows, filters, effDisplay, tab]);

  const groups = useMemo(() => groupLinIssues(scoped, effDisplay, team?.states.nodes || []), [scoped, effDisplay, team]);
  const activeFilterCount = [filters.status, filters.assignee, filters.priority, filters.labels, filters.projects, filters.cycles, filters.creators, filters.subscribers].reduce((n, a) => n + a.length, 0)
    + (filters.dueFrom || filters.dueTo || filters.createdFrom || filters.createdTo ? 1 : 0);

  const applyView = (v: SavedView) => {
    setFilters({...emptyFilters, q: v.query || '', status: v.state ? [v.state] : []});
    setViewsOpen(false);
  };
  const saveCurrentView = () => {
    const name = window.prompt('Name this view:');
    if (!name?.trim()) return;
    const v = {id: `v${Date.now()}`, name: name.trim(), query: filters.q, state: filters.status[0] || '', kind: 'issues'};
    const next = [...views, v];
    setViews(next);
    try { localStorage.setItem(`hoja_saved_views_${orgId}`, JSON.stringify(next)); } catch { /* ignore */ }
  };

  if (!team) return <section className="lin-page"><p className="lin-error" role="alert">Team not found in this workspace.</p></section>;

  const filterChips: {label: string; clear: () => void}[] = [];
  if (filters.q) filterChips.push({label: `“${filters.q}”`, clear: () => setFilters({...filters, q: ''})});
  filters.status.forEach(s => filterChips.push({label: team.states.nodes.find(x => stateKey(x) === s)?.name || s, clear: () => setFilters({...filters, status: filters.status.filter(x => x !== s)})}));
  filters.priority.forEach(p => filterChips.push({label: ['No priority', 'Urgent', 'High', 'Medium', 'Low'][p], clear: () => setFilters({...filters, priority: filters.priority.filter(x => x !== p)})}));
  if (filters.dueFrom || filters.dueTo) filterChips.push({label: `Due ${filters.dueFrom || '…'} → ${filters.dueTo || '…'}`, clear: () => setFilters({...filters, dueFrom: '', dueTo: ''})});

  return (
    <section className="lin-page" aria-label={`${team.name} issues`}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={() => onNavigate('Team Home', `/workspace/team/${team.key.toLowerCase()}/home`)}>{workspaceName}</button>
          <span className="sep">›</span>
          <span className="current">Issues</span>
        </nav>
        <div className="lin-head-actions">
          <FavStar kind="issues" id={`team-issues-${team.id}`} label={`${team.name} issues`} />
          <span style={{position: 'relative'}}>
            <IconBtn label="Team notifications" title="Notification settings" onClick={() => setNotifOpen(v => !v)}><Bell size={15} /></IconBtn>
            {notifOpen && (
              <div className="lin-popover" style={{right: 0, top: 'calc(100% + 6px)', padding: 14, width: 260}}>
                <b style={{fontSize: 13}}>Team notifications</b>
                <p className="lin-note" style={{margin: '8px 0 0'}}>Per-team notification preferences live in workspace Settings → Notifications.</p>
                <button type="button" className="lin-btn" style={{marginTop: 10}} onClick={() => { setNotifOpen(false); onNavigate('Settings', '/workspace/settings/notifications'); }}>Open settings</button>
              </div>
            )}
          </span>
        </div>
      </header>

      <div className="lin-viewbar">
        <div className="lin-tabs" role="tablist" aria-label="Issue scope">
          {(['active', 'backlog', 'all'] as const).map(t => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} className="lin-tab"
              onClick={() => setTab(t)}>{t === 'all' ? 'All issues' : t[0].toUpperCase() + t.slice(1)}</button>
          ))}
        </div>
        <div className="lin-viewchips">
          {views.map(v => (
            <button key={v.id} type="button" className="lin-chipbtn" onClick={() => applyView(v)} title={v.query || 'Saved view'}>{v.name}</button>
          ))}
          <Tip label="Save current filters as a view">
            <button type="button" className="lin-chipbtn" aria-label="Add new view" onClick={saveCurrentView}><Plus size={13} /></button>
          </Tip>
        </div>
      </div>

      <div className="lin-toolbar">
        <label className="lin-search"><Search size={14} /><input placeholder="Search issues…" value={filters.q}
          onChange={e => setFilters({...filters, q: e.target.value})} aria-label="Search issues" /></label>
        <span className="spacer" />
        <span style={{position: 'relative'}}>
          <IconBtn label="Add filter" title="Add filter" active={filterOpen} onClick={() => { setFilterOpen(v => !v); setDisplayOpen(false); }}>
            <ListFilter size={15} />
            {activeFilterCount > 0 && <span style={{position: 'absolute', top: -4, right: -4, background: '#5e6ad2', color: '#fff', fontSize: 9, borderRadius: 99, minWidth: 15, height: 15, display: 'grid', placeItems: 'center', fontWeight: 700}}>{activeFilterCount}</span>}
          </IconBtn>
          {filterOpen && <FilterMenu filters={filters} setFilters={setFilters} team={team} teams={teams}
            projects={projects} labels={labels} cycles={cycles} onClose={() => setFilterOpen(false)}
            anchorClass="" />}
        </span>
        <span style={{position: 'relative'}}>
          <IconBtn label="Display options" title="Display options" active={displayOpen} onClick={() => { setDisplayOpen(v => !v); setFilterOpen(false); }}><SlidersHorizontal size={15} /></IconBtn>
          {displayOpen && <DisplayPanel display={effDisplay} setDisplay={d => setDisplay({...d, layout} as LinDisplay)} onClose={() => setDisplayOpen(false)} />}
        </span>
        <IconBtn label={layout === 'List' ? 'Switch to board view' : 'Switch to list view'}
          title={layout === 'List' ? 'Board view' : 'List view'}
          onClick={() => onLayoutChange(layout === 'List' ? 'Board' : 'List')}>
          {layout === 'List'
            ? <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="1.5" y="2.5" width="5.5" height="11" rx="1.5" /><rect x="9" y="2.5" width="5.5" height="7" rx="1.5" /></svg>
            : <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2 4h12M2 8h12M2 12h12" strokeLinecap="round" /></svg>}
        </IconBtn>
      </div>

      {filterChips.length > 0 && (
        <div className="lin-filterbar" aria-label="Active filters">
          {filterChips.map((c, ix) => (
            <span key={ix} className="lin-fchip">{c.label}<button type="button" aria-label={`Remove filter ${c.label}`} onClick={c.clear}>×</button></span>
          ))}
          <button type="button" className="lin-addrow" onClick={() => setFilters({...emptyFilters})}>Clear all</button>
        </div>
      )}

      <div className="lin-scroll">
        {loading && <p className="lin-loading" role="status">Loading issues…</p>}
        {error && <p className="lin-error" role="alert">{error}</p>}
        {!loading && !error && (layout === 'Board'
          ? <LinearBoard issues={scoped} states={team.states.nodes} onOpen={onIssue}
              onMoved={async (id, s) => { await onMoved(id, s); setRows(old => old.map(i => i.id === id ? {...i, state: s} : i)); }}
              onNew={onNew} display={effDisplay} setDisplay={d => setDisplay({...d, layout} as LinDisplay)} />
          : <div className="lin-list">
              {groups.map(g => (
                <section key={g.key} className="lin-group">
                  {g.name && (
                    <header className="lin-group-head">
                      <button type="button" className="chev" aria-label={`${collapsed.includes(g.key) ? 'Expand' : 'Collapse'} ${g.name}`}
                        aria-expanded={!collapsed.includes(g.key)}
                        onClick={() => setCollapsed(c => c.includes(g.key) ? c.filter(x => x !== g.key) : [...c, g.key])}>
                        <ChevronDown size={14} style={{transform: collapsed.includes(g.key) ? 'rotate(-90deg)' : undefined}} />
                      </button>
                      {g.state && <LinearStatusIcon state={g.state} size={13} />}
                      <b>{g.name}</b><span className="count">{g.items.length}</span>
                      <span className="grow" />
                      <Tip label={`Create issue in ${g.name}`}>
                        <button type="button" className="add" aria-label={`Create issue in ${g.name}`} onClick={onNew}><Plus size={14} /></button>
                      </Tip>
                    </header>
                  )}
                  {!collapsed.includes(g.key) && g.items.map(i => (
                    <IssueRow key={i.id} issue={i} display={effDisplay}
                      selected={selected.includes(i.id)}
                      showCheckbox
                      onSelect={(id, on) => setSelected(s => on ? [...s, id] : s.filter(x => x !== id))}
                      onOpen={onIssue} childCount={childCounts.get(i.id) || 0} />
                  ))}
                </section>
              ))}
              {!scoped.length && (
                <div className="lin-empty">
                  <ListFilter size={30} strokeWidth={1.4} />
                  <h2>No issues match</h2>
                  <p>Try clearing filters or create a new issue.</p>
                  <button type="button" className="lin-btn primary" onClick={onNew}><Plus size={14} /> New issue</button>
                </div>
              )}
              {(hasMore || moreError) && !loading && (
                <div className="lin-loadmore">
                  {hasMore && (
                    <button type="button" className="lin-btn" onClick={() => void loadMore()} disabled={loadingMore}>
                      {loadingMore ? 'Loading…' : `Load more issues (${rows.length} shown)`}
                    </button>
                  )}
                  {moreError && <span className="lin-error" role="alert">{moreError} <button type="button" className="lin-btn" onClick={() => void loadMore()}>Retry</button></span>}
                </div>
              )}
            </div>)}
      </div>

      {selected.length > 0 && (
        <BulkBar selected={selected} issues={rows} team={team} teams={teams} cycles={cycles} labels={labels}
          onClear={() => setSelected([])}
          onDone={() => { setSelected([]); /* parent refreshes via revision bump */ }} />
      )}
    </section>
  );
}

/* ---------------- property dropdown ---------------- */

function PropDropdown({label, icon, value, onPick, options, disabled}: {
  label: string; icon: React.ReactNode; value: React.ReactNode;
  onPick: (id: string | null) => void;
  options: {id: string | null; label: React.ReactNode; selected?: boolean; hint?: string}[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(() => setOpen(false));
  return (
    <div className="lin-prop lin-dd" ref={ref}>
      <span className="k">{icon}{label}</span>
      <span className="v">
        <button type="button" className="plain" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
          onClick={() => setOpen(v => !v)}><span className="trunc">{value}</span></button>
      </span>
      {open && (
        <div className="lin-menu lin-dd-panel" role="listbox" aria-label={label}>
          {options.map(o => (
            <button key={String(o.id)} type="button" role="option" aria-selected={o.selected} className="lin-dd-opt"
              title={o.hint} onClick={() => { setOpen(false); onPick(o.id); }}>
              <span className="grow">{o.label}</span>
              {o.selected && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- issue detail ---------------- */

const detailFields = `{ id identifier title description createdAt updatedAt dueDate priority priorityLabel estimate branchName url
  state{id name color type} team{id key name} project{id name} assignee{id name} creator{id name}
  cycle{id name number} parent{id identifier title}
  labels{nodes{id name color}} subscribers{nodes{id name}}
  attachments{nodes{id url title subtitle createdAt}}
  activity(first:50){nodes{id eventType createdAt actor{name} changes}}
  comments{nodes{id body createdAt user{id name}}} }`;

function describeEvent(e: {eventType: string; actor?: {name?: string}; changes?: Record<string, unknown>}, states: IssueState[]): string {
  const actor = e.actor?.name || 'Someone';
  const ch = (e.changes || {}) as Record<string, {from?: string | null; to?: string | null}>;
  if (e.eventType === 'issueCreated' || e.eventType === 'created') return `${actor} created the issue`;
  const sname = (id?: string | null) => (id && states.find(s => s.id === id)?.name) || 'Unknown';
  if (ch.stateId) return `${actor} moved from ${sname(ch.stateId.from)} to ${sname(ch.stateId.to)}`;
  if (ch.assigneeId) return `${actor} assigned the issue`;
  if (ch.cycleId) return `${actor} changed the cycle`;
  if (ch.priority) return `${actor} changed the priority`;
  return `${actor} updated the issue`;
}

export function LinearIssueDetail({workspaceName, viewer, issue, issueList, onOpenIssue, loading, error, onArchive, onDelete, onDeleteComment, teams, projects, labels, cycles, onUpdate, onBack, backLabel, onRetry, onComment}: {
  workspaceName?: string; viewer: Viewer; issue: LinIssue | null; issueList: LinIssue[];
  onOpenIssue: (issue: LinIssue) => void; loading: boolean; error: string;
  onArchive: () => Promise<void>; onDelete: () => Promise<void>; onDeleteComment: (id: string) => Promise<void>;
  teams: Team[]; projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  onUpdate: (input: Record<string, unknown>) => Promise<void>;
  onBack: () => void; backLabel: string; onRetry?: () => void; onComment: (body: string) => Promise<void>;
}) {
  const [detail, setDetail] = useState<LinIssue | null>(null);
  const [loadError, setLoadError] = useState('');
  const [subs, setSubs] = useState<LinIssue[]>([]);
  const [subsDone, setSubsDone] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState<string | null>(null);
  const [descEdit, setDescEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [updateError, setUpdateError] = useState('');
  const [comment, setComment] = useState('');
  const [commentBusy, setCommentBusy] = useState(false);
  const [commentError, setCommentError] = useState('');
  const [subscribed, setSubscribed] = useState(false);
  const [labelOpen, setLabelOpen] = useState(false);
  const [parentSearch, setParentSearch] = useState(false);
  const [parentQuery, setParentQuery] = useState('');
  const [parentResults, setParentResults] = useState<LinIssue[]>([]);
  const [attachMenu, setAttachMenu] = useState(false);
  const [attachUrl, setAttachUrl] = useState('');
  const [attachTitle, setAttachTitle] = useState('');
  const [uploading, setUploading] = useState(false);
  const [newSubTitle, setNewSubTitle] = useState('');
  const [addingSub, setAddingSub] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const moreRef = useDismiss(() => setMoreOpen(false));
  const labelRef = useDismiss(() => setLabelOpen(false));
  const attachRef = useDismiss(() => setAttachMenu(false));
  const parentRef = useDismiss(() => setParentSearch(false));

  const issueId = issue?.id;
  useEffect(() => {
    if (!issueId) return;
    let live = true;
    setLoadError('');
    const detailTimeout = setTimeout(() => {
      if (live) setLoadError('Loading the issue timed out. Check your connection and retry.');
    }, 20000);
    gql(`query($id:String!){issue(id:$id)${detailFields}}`, {id: issue?.identifier || issueId})
      .then(d => {
        if (!live) return;
        if (!d.issue) throw new Error('Issue not found');
        setDetail(d.issue);
        setTitle(d.issue.title);
        setSubscribed(!!(d.issue.subscribers?.nodes || []).some((s: {id: string}) => s.id === viewer.id));
        setLoadError('');
        return gql(`query($filter:IssueFilter){issues(first:100,filter:$filter){nodes ${linIssueFields}}}`, {filter: {parent: {id: {eq: d.issue.id}}}});
      })
      .then(d => { if (live && d) { setSubs(d.issues.nodes); setSubsDone(d.issues.nodes.filter((s: LinIssue) => s.state?.type === 'completed').length); } })
      .catch(e => { if (live) setLoadError((e as Error).message); })
      .finally(() => { if (live) clearTimeout(detailTimeout); });
    return () => { live = false; clearTimeout(detailTimeout); };
  }, [issueId]);

  useEffect(() => {
    if (!parentSearch || parentQuery.trim().length < 2) { setParentResults([]); return; }
    const t = setTimeout(() => {
      gql(`query($term:String!){searchIssues(term:$term,first:8){nodes{id identifier title state{id name color type}}}}`, {term: parentQuery.trim()})
        .then(d => setParentResults((d.searchIssues.nodes as LinIssue[]).filter(i => i.id !== detail?.id)))
        .catch(() => setParentResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [parentQuery, parentSearch]);

  if (loading) return <section className="lin-page"><p className="lin-loading" role="status">Loading issue…</p></section>;
  if (error || loadError || !detail) {
    return <section className="lin-page"><div className="lin-scroll"><p className="lin-error" role="alert">{error || loadError || 'Issue not found'}</p>
      <div style={{display: 'flex', gap: 8, margin: '0 24px'}}>
        {onRetry && <button type="button" className="lin-btn primary" onClick={onRetry}>Retry</button>}
        <button type="button" className="lin-btn" onClick={onBack}>← {backLabel}</button>
      </div></div></section>;
  }

  const team = teams.find(t => t.id === detail.team?.id);
  const states = team?.states.nodes || [];
  const members = team?.members?.nodes || [];
  const teamCycles = cycles.filter(c => c.team?.id === detail.team?.id);
  const labelIds = detail.labels?.nodes.map(l => l.id) || [];
  const change = async (input: Record<string, unknown>) => {
    setSaving(true); setUpdateError('');
    try {
      await onUpdate(input);
      const d = await gql(`query($id:String!){issue(id:$id)${detailFields}}`, {id: detail.identifier});
      setDetail(d.issue);
      setTitle(d.issue.title);
    } catch (e) { setUpdateError((e as Error).message); } finally { setSaving(false); }
  };
  const copyText = async (text: string) => { try { await navigator.clipboard.writeText(text); } catch { /* ignore */ } };
  const branchName = detail.branchName || `${(detail.team?.key || 'hoja').toLowerCase()}-${detail.identifier.split('-')[1] || ''}`;
  const asPrompt = `${detail.identifier}: ${detail.title}\n\n${detail.description || ''}`.trim();

  const idx = issueList.findIndex(i => i.id === detail.id);
  const prev = idx > 0 ? issueList[idx - 1] : null;
  const next = idx >= 0 && idx < issueList.length - 1 ? issueList[idx + 1] : null;

  const submitComment = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = comment.trim();
    if (!body || commentBusy) return;
    setCommentBusy(true); setCommentError('');
    try {
      await onComment(body);
      setComment('');
      const d = await gql(`query($id:String!){issue(id:$id){comments{nodes{id body createdAt user{id name}}}}}`, {id: detail.identifier});
      setDetail(cur => cur ? {...cur, comments: d.issue.comments} : cur);
    } catch (e) { setCommentError((e as Error).message); } finally { setCommentBusy(false); }
  };

  const doUpload = async (file: File) => {
    setUploading(true); setUpdateError('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const token = localStorage.getItem('hoja_token');
      const r = await fetch('/api/uploads', {
        method: 'POST', credentials: 'include',
        headers: token && token !== 'session' ? {authorization: `Bearer ${token}`} : {},
        body: fd,
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.error || 'Upload failed');
      const d = await gql(`mutation($input:AttachmentCreateInput!){attachmentCreate(input:$input){success attachment{id url title subtitle createdAt}}}`,
        {input: {issueId: detail.id, url: b.url, title: b.title || file.name}});
      if (!d.attachmentCreate?.success) throw new Error('Attachment was rejected');
      setDetail(cur => cur ? {...cur, attachments: {nodes: [...(cur.attachments?.nodes || []), d.attachmentCreate.attachment]}} : cur);
    } catch (e) { setUpdateError((e as Error).message); } finally { setUploading(false); }
  };

  const attachLink = async () => {
    if (!attachUrl.trim()) return;
    setUploading(true); setUpdateError('');
    try {
      const d = await gql(`mutation($input:AttachmentCreateInput!){attachmentCreate(input:$input){success attachment{id url title subtitle createdAt}}}`,
        {input: {issueId: detail.id, url: attachUrl.trim(), title: attachTitle.trim() || attachUrl.trim()}});
      if (!d.attachmentCreate?.success) throw new Error('Attachment was rejected');
      setDetail(cur => cur ? {...cur, attachments: {nodes: [...(cur.attachments?.nodes || []), d.attachmentCreate.attachment]}} : cur);
      setAttachUrl(''); setAttachTitle(''); setAttachMenu(false);
    } catch (e) { setUpdateError((e as Error).message); } finally { setUploading(false); }
  };

  const createSub = async () => {
    const t = newSubTitle.trim();
    if (!t || addingSub || !detail.team?.id) return;
    setAddingSub(true); setUpdateError('');
    try {
      const d = await gql(`mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id}}}`,
        {input: {title: t, teamId: detail.team.id, parentId: detail.id}});
      if (!d.issueCreate?.success) throw new Error('Sub-issue was rejected');
      setNewSubTitle('');
      const s = await gql(`query($filter:IssueFilter){issues(first:100,filter:$filter){nodes ${linIssueFields}}}`, {filter: {parent: {id: {eq: detail.id}}}});
      setSubs(s.issues.nodes);
      setSubsDone(s.issues.nodes.filter((x: LinIssue) => x.state?.type === 'completed').length);
    } catch (e) { setUpdateError((e as Error).message); } finally { setAddingSub(false); }
  };

  const toggleSubscribe = async () => {
    try {
      const d = await gql(`mutation($id:String!){${subscribed ? 'issueUnsubscribe' : 'issueSubscribe'}(id:$id)}`, {id: detail.id});
      if (d[subscribed ? 'issueUnsubscribe' : 'issueSubscribe']) setSubscribed(!subscribed);
    } catch (e) { setUpdateError((e as Error).message); }
  };

  return (
    <section className="lin-page" aria-label={`${detail.identifier}: ${detail.title}`}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={onBack}>← {backLabel}</button>
          <span className="sep">›</span>
          <span>{workspaceName}</span>
          <span className="sep">›</span>
          <span className="crumb-id">{detail.identifier}</span>
          <span className="current">{detail.title}</span>
        </nav>
        <div className="lin-head-actions">
          <FavStar kind="issues" id={detail.id} label={detail.identifier} />
          <span style={{position: 'relative'}} ref={moreRef}>
            <IconBtn label="Issue options" title="Issue options" onClick={() => setMoreOpen(v => !v)}><span style={{fontWeight: 700}}>···</span></IconBtn>
            {moreOpen && <LinMenu onClose={() => setMoreOpen(false)} className="lin-dd-panel" items={[
              {key: 'copy', label: 'Copy as prompt', icon: <Copy size={14} />, onSelect: () => void copyText(asPrompt)},
              {key: 'branch', label: 'Copy branch name', icon: <Copy size={14} />, onSelect: () => void copyText(branchName)},
              {key: 'div', divider: true},
              {key: 'archive', label: 'Archive issue', onSelect: () => { if (window.confirm(`Archive ${detail.identifier}?`)) void onArchive(); }},
              {key: 'delete', label: 'Delete issue', danger: true, onSelect: () => { if (window.confirm(`Permanently delete ${detail.identifier}? This cannot be undone.`)) void onDelete(); }},
            ]} />}
          </span>
          {idx >= 0 && <>
            <span className="lin-nav-count">{idx + 1} / {issueList.length}</span>
            <IconBtn label="Previous issue" title="Previous issue" disabled={!prev} onClick={() => prev && onOpenIssue(prev)}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M8 13V3M4 7l4-4 4 4" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </IconBtn>
            <IconBtn label="Next issue" title="Next issue" disabled={!next} onClick={() => next && onOpenIssue(next)}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M8 3v10M4 9l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </IconBtn>
          </>}
        </div>
      </header>
      <div className="lin-toolbar" style={{justifyContent: 'flex-end'}}>
        <IconBtn label="Copy link" title="Copy link" onClick={() => void copyText(window.location.href)}><Link2 size={15} /></IconBtn>
        <IconBtn label="Create copy of issue" title="Create copy of issue"
          onClick={() => { void (async () => {
            try {
              const d = await gql(`mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier}}}`,
                {input: {title: `Copy of ${detail.title}`, teamId: detail.team?.id, description: detail.description, priority: detail.priority, projectId: detail.project?.id, labelIds}});
              if (d.issueCreate?.success) onOpenIssue({...detail, id: d.issueCreate.issue.id, identifier: d.issueCreate.issue.identifier, title: `Copy of ${detail.title}`});
            } catch (e) { setUpdateError((e as Error).message); }
          })(); }}>
          <Copy size={15} />
        </IconBtn>
        <Tip label="Hoja has no public share links — copies the private URL">
          <span><IconBtn label="Share issue externally" title="Share issue externally" onClick={() => void copyText(window.location.href)}><ExternalLink size={15} /></IconBtn></span>
        </Tip>
        <IconBtn label="Archive issue" title="Archive issue"
          onClick={() => { if (window.confirm(`Archive ${detail.identifier}?`)) void onArchive().catch(e => setUpdateError((e as Error).message)); }}>
          <Trash2 size={15} />
        </IconBtn>
      </div>

      <div className="lin-detail">
        <main className="lin-detail-main">
          <textarea className="lin-detail-title" aria-label="Title" rows={1} value={title}
            onChange={e => { setTitle(e.target.value); e.currentTarget.style.height = 'auto'; e.currentTarget.style.height = e.currentTarget.scrollHeight + 'px'; }}
            onBlur={() => { const t = title.trim(); if (t && t !== detail.title) void change({title: t}); else setTitle(detail.title); }}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); } }} />
          {updateError && <p className="lin-error" role="alert">{updateError}</p>}
          {descEdit ? (
            <div>
              <textarea aria-label="Description" value={desc ?? ''} rows={6} autoFocus
                onChange={e => setDesc(e.target.value)}
                style={{width: '100%', border: '1px solid var(--line)', borderRadius: 8, padding: 10, font: 'inherit', fontSize: 14}} />
              <div style={{display: 'flex', gap: 8, marginTop: 8}}>
                <button type="button" className="lin-btn primary" disabled={saving} onClick={() => { void change({description: (desc ?? '').trim() || null}).then(() => setDescEdit(false)); }}>Save</button>
                <button type="button" className="lin-btn" onClick={() => { setDescEdit(false); setDesc(detail.description || ''); }}>Cancel</button>
              </div>
            </div>
          ) : (
            <p className={cx('lin-detail-desc', !detail.description && 'empty')}
              onClick={() => { setDesc(detail.description || ''); setDescEdit(true); }}
              title="Click to edit description" style={{cursor: 'text'}}>
              {detail.description || 'Add description…'}
            </p>
          )}

          <section className="lin-subsec" aria-label="Sub-issues">
            <header>
              <ChevronDown size={14} color="var(--muted)" />
              <span>Sub-Issues</span>
              <SubIssuePill done={subsDone} total={subs.length} />
              <span className="grow" />
              <IconBtn label="Sub-issue display settings" title="Display settings"><SlidersHorizontal size={14} /></IconBtn>
              <IconBtn label="Add sub-issue" title="Add sub-issue" onClick={() => setAddingSub(v => !v)}><Plus size={14} /></IconBtn>
            </header>
            {addingSub && (
              <div style={{display: 'flex', gap: 8, margin: '4px 0 8px'}}>
                <input placeholder="Sub-issue title…" value={newSubTitle} autoFocus
                  onChange={e => setNewSubTitle(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') void createSub(); }}
                  style={{flex: 1, border: '1px solid var(--line)', borderRadius: 7, padding: '8px 10px', fontSize: 13}} />
                <button type="button" className="lin-btn primary" disabled={!newSubTitle.trim()} onClick={() => void createSub()}>Add</button>
              </div>
            )}
            {subs.map(s => (
              <div key={s.id} className={cx('lin-subrow', s.state?.type === 'completed' && 'done')}>
                <LinearStatusIcon state={s.state} size={14} />
                <button type="button" className="t" onClick={() => onOpenIssue(s)} title={s.title}>{s.title}</button>
                {s.labels?.nodes.map(l => <LabelChip key={l.id} label={l} />)}
                {s.assignee && <Avatar name={s.assignee.name} size={18} />}
                <IconBtn label={`Open sub-issue ${s.identifier}`} title="Open sub-issue" onClick={() => onOpenIssue(s)}>
                  <ExternalLink size={13} />
                </IconBtn>
              </div>
            ))}
            {!subs.length && !addingSub && <p className="lin-note">No sub-issues yet.</p>}
          </section>

          <section className="lin-subsec" aria-label="Attachments">
            <header>
              <ChevronDown size={14} color="var(--muted)" />
              <span>Attachments</span>
              <span className="count" style={{color: 'var(--muted)', fontWeight: 500, fontSize: 12}}>{(detail.attachments?.nodes || []).length}</span>
              <span className="grow" />
              <span style={{position: 'relative'}} ref={attachRef}>
                <IconBtn label="Attach file or link" title="Attach" onClick={() => setAttachMenu(v => !v)}><Plus size={14} /></IconBtn>
                {attachMenu && (
                  <div className="lin-popover" style={{right: 0, top: 'calc(100% + 6px)', width: 300, padding: 12}}>
                    <b style={{fontSize: 13}}>Attach</b>
                    <div style={{display: 'flex', gap: 8, marginTop: 10}}>
                      <button type="button" className="lin-btn" disabled={uploading} onClick={() => fileRef.current?.click()}>
                        {uploading ? 'Uploading…' : 'Upload file'}
                      </button>
                      <input ref={fileRef} type="file" hidden accept="image/*,video/*,.pdf,.txt,.md"
                        onChange={e => { const f = e.target.files?.[0]; if (f) void doUpload(f); e.target.value = ''; }} />
                    </div>
                    <div className="lin-menu-divider" />
                    <input placeholder="Paste link URL…" value={attachUrl} onChange={e => setAttachUrl(e.target.value)}
                      style={{width: '100%', border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13, marginBottom: 8}} />
                    <input placeholder="Link title (optional)" value={attachTitle} onChange={e => setAttachTitle(e.target.value)}
                      style={{width: '100%', border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13, marginBottom: 8}} />
                    <button type="button" className="lin-btn primary" style={{width: '100%', justifyContent: 'center'}}
                      disabled={!attachUrl.trim() || uploading} onClick={() => void attachLink()}>Attach link</button>
                  </div>
                )}
              </span>
            </header>
            <div className="lin-attach-list">
              {(detail.attachments?.nodes || []).map(a => {
                const isImg = /\.(png|jpe?g|gif|webp|svg)$/i.test(a.url);
                return (
                  <div key={a.id} className="lin-attach">
                    {isImg ? <img className="thumb" src={a.url} alt="" /> : <Link2 size={16} color="var(--muted)" />}
                    <a href={a.url} target="_blank" rel="noopener noreferrer">{a.title}</a>
                    {a.subtitle && <small>{a.subtitle}</small>}
                    <small style={{marginLeft: 'auto'}}>{relTime(a.createdAt)}</small>
                  </div>
                );
              })}
            </div>
            {!(detail.attachments?.nodes || []).length && <p className="lin-note">No attachments yet.</p>}
          </section>

          <section className="lin-activity" aria-label="Activity">
            <header>
              <b>Activity</b>
              <span className="grow" />
              <button type="button" className="lin-subscribe-btn" onClick={() => void toggleSubscribe()} aria-pressed={subscribed}>
                {subscribed ? 'Subscribed' : 'Subscribe'}
              </button>
              <span className="lin-sublist">
                {(detail.subscribers?.nodes || []).slice(0, 5).map(s => <Avatar key={s.id} name={s.name} size={20} />)}
              </span>
            </header>
            {(detail.activity?.nodes || []).map(e => (
              <div key={e.id} className="lin-event">
                <Avatar name={e.actor?.name} size={18} />
                <span><b>{e.actor?.name || 'Someone'}</b> {describeEvent(e, states).replace(/^.*? (created|moved|assigned|changed|updated)/, '$1')}</span>
                <time>{relTime(e.createdAt)}</time>
              </div>
            ))}
            {(detail.comments?.nodes || []).map(c => (
              <article key={c.id} className="lin-comment">
                <header><Avatar name={c.user?.name} size={18} /><b>{c.user?.name || 'User'}</b><time>{relTime(c.createdAt)}</time></header>
                <p>{c.body}</p>
                {(viewer.admin || (viewer.id && c.user?.id === viewer.id)) && (
                  <button type="button" className="lin-addrow del" onClick={() => { if (window.confirm('Delete this comment?')) void onDeleteComment(c.id).catch(e => setCommentError((e as Error).message)); }}>Delete comment</button>
                )}
              </article>
            ))}
            <form className="lin-commentbox" onSubmit={submitComment}>
              <textarea rows={2} value={comment} aria-label="Leave a comment" placeholder="Leave a comment…"
                disabled={commentBusy}
                onChange={e => { setComment(e.target.value); if (commentError) setCommentError(''); }}
                onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
              <footer>
                <Tip label="Attach a file">
                  <button type="button" className="lin-icon-btn" aria-label="Attach a file" onClick={() => { setAttachMenu(true); }}>
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M11.5 8.5l-4.7 4.7a2.4 2.4 0 0 1-3.4-3.4l5.4-5.4a1.6 1.6 0 0 1 2.3 2.3L5.7 12" strokeLinecap="round" /></svg>
                  </button>
                </Tip>
                <button type="submit" className="lin-icon-btn" aria-label="Submit comment" title="Submit comment (⌘↵)"
                  disabled={commentBusy || !comment.trim()}>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M8 13V3M4 7l4-4 4 4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </button>
              </footer>
            </form>
            {commentError && <p className="lin-error" role="alert">{commentError}</p>}
          </section>
        </main>

        <aside className="lin-props" aria-label="Properties">
          <h3>Properties</h3>
          <PropDropdown label="Status" icon={<LinearStatusIcon state={detail.state} size={14} />}
            value={<span style={{display: 'inline-flex', alignItems: 'center', gap: 7}}>{detail.state?.name || 'No status'}</span>}
            onPick={id => { if (id) void change({stateId: id}); }}
            options={states.map(s => ({id: s.id, selected: s.id === detail.state?.id, label: <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><LinearStatusIcon state={s} />{s.name}</span>}))} />
          <PropDropdown label="Priority" icon={<PriorityIcon priority={detail.priority} />}
            value={['No priority', 'Urgent', 'High', 'Medium', 'Low'][detail.priority || 0]}
            onPick={id => void change({priority: Number(id)})}
            options={[[0, 'No priority'], [1, 'Urgent'], [2, 'High'], [3, 'Medium'], [4, 'Low']].map(([v, l]) => ({
              id: String(v), selected: (detail.priority || 0) === v,
              label: <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><PriorityIcon priority={v as number} />{l}</span>,
            }))} />
          <PropDropdown label="Assignee" icon={<Avatar name={detail.assignee?.name} size={16} />}
            value={detail.assignee?.name || 'Unassigned'}
            onPick={id => void change({assigneeId: id})}
            options={[{id: null, selected: !detail.assignee, label: 'Unassigned', hint: 'Unassigned'},
              ...members.map(m => ({id: m.id as string | null, selected: m.id === detail.assignee?.id,
                hint: m.name || m.email || undefined,
                label: <span style={{display: 'inline-flex', alignItems: 'center', gap: 8}}><Avatar name={m.name} size={18} />{m.name || m.email}</span>}))]} />
          <PropDropdown label="Cycle" icon={<CycleGlyph />}
            value={detail.cycle ? (detail.cycle.name || `Cycle ${detail.cycle.number}`) : 'No cycle'}
            onPick={id => void change({cycleId: id})}
            options={[{id: null, selected: !detail.cycle, label: 'No cycle'},
              ...teamCycles.map(c => ({id: c.id as string | null, selected: c.id === detail.cycle?.id, label: c.name || `Cycle ${c.number}`}))]} />
          <PropDropdown label="Project" icon={<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M8 1.8l5.6 3.2v6.4L8 14.6l-5.6-3.2V5L8 1.8Z" strokeLinejoin="round" /></svg>}
            value={detail.project?.name || 'No project'}
            onPick={id => void change({projectId: id})}
            options={[{id: null, selected: !detail.project, label: 'No project'},
              ...projects.map(p => ({id: p.id as string | null, selected: p.id === detail.project?.id, label: p.name}))]} />

          <div className="sec">Labels</div>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6}}>
            {(detail.labels?.nodes || []).map(l => <LabelChip key={l.id} label={l} onRemove={() => void change({labelIds: labelIds.filter(id => id !== l.id)})} />)}
          </div>
          <span style={{position: 'relative'}} ref={labelRef}>
            <button type="button" className="lin-addrow" onClick={() => setLabelOpen(v => !v)}>
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2.5 2.5h5.2l5.8 5.8-5.2 5.2-5.8-5.8V2.5Z" strokeLinejoin="round" /><circle cx="6" cy="6" r="1.2" fill="currentColor" /></svg>
              Add label
            </button>
            {labelOpen && (
              <div className="lin-menu lin-dd-panel" role="listbox" aria-label="Add label">
                {labels.filter(l => !labelIds.includes(l.id)).map(l => (
                  <button key={l.id} type="button" className="lin-dd-opt" onClick={() => { setLabelOpen(false); void change({labelIds: [...labelIds, l.id]}); }}>
                    <LabelChip label={l} />
                  </button>
                ))}
                {!labels.filter(l => !labelIds.includes(l.id)).length && <p className="lin-note" style={{padding: '8px 10px'}}>All labels added.</p>}
              </div>
            )}
          </span>

          <div className="sec">Estimate</div>
          <div className="lin-prop">
            <span className="k">⧗ Estimate</span>
            <span className="v"><input type="number" min={0} step={1} aria-label="Estimate (points)" value={detail.estimate ?? ''}
              onChange={e => { const v = e.target.value === '' ? null : Number(e.target.value); void change({estimate: v}); }}
              style={{width: 90, border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', fontSize: 13}} /></span>
          </div>
          <div className="lin-prop" style={{marginTop: 4}}>
            <span className="k">◷ Due date</span>
            <span className="v"><input type="date" aria-label="Due date" value={(detail.dueDate || '').slice(0, 10)}
              onChange={e => void change({dueDate: e.target.value || null})}
              style={{border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', fontSize: 13}} /></span>
          </div>

          <div className="sec">Parent</div>
          {detail.parent ? (
            <div className="lin-prop">
              <span className="v">
                <button type="button" className="plain" onClick={() => onOpenIssue({...(detail.parent as LinIssue), id: detail.parent!.id})}>
                  <span className="trunc"><b>{detail.parent.identifier}</b> {detail.parent.title}</span>
                </button>
                <IconBtn label="Remove parent" title="Remove parent" onClick={() => void change({parentId: null})}><X size={13} /></IconBtn>
              </span>
            </div>
          ) : (
            <span style={{position: 'relative'}} ref={parentRef}>
              <button type="button" className="lin-addrow" onClick={() => setParentSearch(v => !v)}>Set parent…</button>
              {parentSearch && (
                <div className="lin-popover" style={{left: 0, top: 'calc(100% + 4px)', width: 300, padding: 8}}>
                  <input autoFocus placeholder="Search issues…" value={parentQuery} onChange={e => setParentQuery(e.target.value)}
                    style={{width: '100%', border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13, marginBottom: 6}} />
                  {parentResults.map(r => (
                    <button key={r.id} type="button" className="lin-dd-opt"
                      onClick={() => { setParentSearch(false); setParentQuery(''); void change({parentId: r.id}); }}>
                      <span className="grow"><b>{r.identifier}</b> {r.title}</span>
                    </button>
                  ))}
                </div>
              )}
            </span>
          )}

          <div className="sec">Subscribers</div>
          <div className="lin-sublist">
            {(detail.subscribers?.nodes || []).map(s => <Avatar key={s.id} name={s.name} size={22} />)}
            {!detail.subscribers?.nodes.length && <span className="lin-note">No subscribers</span>}
          </div>

          <div className="sec">Timestamps</div>
          <div className="lin-ts">Created {detail.createdAt ? new Date(detail.createdAt).toLocaleString() : '—'}</div>
          <div className="lin-ts">Updated {detail.updatedAt ? new Date(detail.updatedAt).toLocaleString() : '—'}</div>

          <div className="sec">Related</div>
          <Tip label="Issue relations are not available in Hoja yet">
            <button type="button" className="lin-addrow" disabled style={{opacity: .5, cursor: 'not-allowed'}}>Add relationship…</button>
          </Tip>
        </aside>
      </div>
    </section>
  );
}
