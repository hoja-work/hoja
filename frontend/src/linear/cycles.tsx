import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Bell, CalendarDays, Check, ChevronDown, ChevronRight, Copy, Download, Link2, ListFilter, Plus, Settings2, SlidersHorizontal, X} from 'lucide-react';
import {gql, stateKey} from '../main';
import type {Cycle, IssueLabel, IssueState, Project, Team, Viewer} from '../main';
import {applyLinFilters, defaultDisplay, emptyFilters, groupLinIssues, orderLinIssues, DisplayPanel as _DP, FilterMenu, IssueRow, LinDisplay, LinFilters, LinIssue, linIssueFields} from './issues';
import {Avatar, CycleGlyph, FavStar, IconBtn, LabelChip, LinMenu, LinearStatusIcon, PriorityIcon, Ring, Tip, cx, fmtDate, fmtRange, useDismiss, useFavorite} from './shared';

/* Linear 1:1 Cycles views. */

export type CycleState = 'planned' | 'upcoming' | 'current' | 'completed';

export function cycleState(c: Cycle, now = Date.now()): CycleState {
  if (c.completedAt) return 'completed';
  const s = c.startsAt ? Date.parse(c.startsAt) : NaN;
  const e = c.endsAt ? Date.parse(c.endsAt) : NaN;
  if (!Number.isNaN(e) && e < now) return 'completed';
  if (!Number.isNaN(s) && !Number.isNaN(e) && s <= now && now <= e) return 'current';
  if (!Number.isNaN(s) && s > now) {
    // the nearest future cycle is "upcoming"; the rest are planned — resolved by caller ordering
    return 'upcoming';
  }
  return 'planned';
}

export function CycleStateIcon({state, size = 15}: {state: CycleState; size?: number}) {
  if (state === 'completed')
    return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#8a94a6" strokeWidth="1.5" aria-label="Completed"><path d="M2.7 8a5.3 5.3 0 1 1 1.6 3.8" strokeLinecap="round" /><path d="M2.7 12.4v-3h3" strokeLinecap="round" strokeLinejoin="round" /></svg>);
  if (state === 'current')
    return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#5e6ad2" strokeWidth="1.5" aria-label="Current"><circle cx="8" cy="8" r="6.2" /><path d="M6.4 5.4v5.2l4.5-2.6z" fill="#5e6ad2" stroke="none" /></svg>);
  if (state === 'upcoming')
    return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#8a94a6" strokeWidth="1.5" aria-label="Upcoming"><circle cx="8" cy="8" r="6.2" /><path d="M6.6 5.6v4.8l4.2-2.4z" fill="#8a94a6" stroke="none" opacity=".7" /></svg>);
  return (<svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#8a94a6" strokeWidth="1.5" aria-label="Planned"><circle cx="8" cy="8" r="5.6" strokeDasharray="2.6 2.2" /><path d="M8 5v3.2l2.2 1.4" strokeLinecap="round" /></svg>);
}

export type CycleStats = {scope: number; started: number; completed: number; points: number};

export function statsFor(issues: LinIssue[]): CycleStats {
  const scope = issues.length;
  const completed = issues.filter(i => i.state?.type === 'completed').length;
  const started = issues.filter(i => i.state?.type === 'started').length;
  const points = issues.reduce((n, i) => n + (i.estimate || 0), 0);
  return {scope, started, completed, points};
}

/* ---------------- burndown chart ---------------- */

export function BurndownChart({cycle, stats, height = 200}: {cycle: Cycle; stats: CycleStats; height?: number}) {
  const W = 560, H = height, padL = 8, padR = 8, padT = 10, padB = 22;
  const now = Date.now();
  const start = cycle.startsAt ? Date.parse(cycle.startsAt) : now - 6 * 864e5;
  const end = cycle.endsAt ? Date.parse(cycle.endsAt) : now + 864e5;
  const x0 = start - 864e5, x1 = end + 864e5;
  const maxY = Math.max(stats.scope, 4);
  const X = (t: number) => padL + ((t - x0) / (x1 - x0)) * (W - padL - padR);
  const Y = (v: number) => padT + (1 - v / maxY) * (H - padT - padB);
  const d = (t: number) => new Date(t).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  const hatchId = `hatch-${cycle.id}`;
  return (
    <svg className="lin-burndown" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Burndown: scope ${stats.scope}, started ${stats.started}, completed ${stats.completed}`}>
      <defs>
        <pattern id={hatchId} width="7" height="7" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
          <rect width="7" height="7" fill="transparent" />
          <line x1="0" y1="0" x2="0" y2="7" stroke="var(--line)" strokeWidth="2.4" />
        </pattern>
      </defs>
      {now > x0 && now < x1 && (
        <rect x={X(Math.max(now, start))} y={padT} width={Math.max(0, X(end) - X(Math.max(now, start)))} height={H - padT - padB} fill={`url(#${hatchId})`} />
      )}
      <line className="grid" x1={padL} y1={Y(stats.scope)} x2={W - padR} y2={Y(stats.scope)} />
      <line x1={X(start)} y1={Y(stats.scope)} x2={X(end)} y2={Y(0)} stroke="#8a94a6" strokeWidth="1.4" strokeDasharray="5 4" />
      <line x1={X(start)} y1={Y(stats.scope)} x2={X(Math.min(now, end))} y2={Y(stats.scope)} stroke="#8a94a6" strokeWidth="1.8" />
      <circle cx={X(start)} cy={Y(stats.scope)} r="3.4" fill="#e5b800" />
      <circle cx={X(Math.min(now, end))} cy={Y(stats.scope)} r="3.4" fill="#5e6ad2" />
      <text className="axis" x={X(start)} y={H - 6} textAnchor="middle">{d(start)}</text>
      <text className="axis" x={X(end)} y={H - 6} textAnchor="middle">{d(end)}</text>
    </svg>
  );
}

function Legend({stats}: {stats: CycleStats}) {
  const row = (color: string, label: string, n: number, pct?: number) => (
    <div className="lr"><span className="sw" style={{background: color}} />{label}
      <span className="n"><b>{n}</b>{pct !== undefined && <> · {pct}%</>}</span></div>
  );
  const pct = (n: number) => stats.scope ? Math.round((n / stats.scope) * 100) : 0;
  return (
    <div className="legend">
      {row('#8a94a6', 'Scope', stats.scope)}
      {row('#e5b800', 'Started', stats.started, pct(stats.started))}
      {row('#5e6ad2', 'Completed', stats.completed, pct(stats.completed))}
    </div>
  );
}

/* ---------------- overview rows ---------------- */

function CycleOverviewRow({cycle, state, stats, onCycle, showChart}: {
  cycle: Cycle; state: CycleState; stats: CycleStats; onCycle: (c: Cycle) => void; showChart: boolean;
}) {
  const name = cycle.name || `Cycle ${cycle.number ?? ''}`;
  const pct = stats.scope ? Math.round((stats.completed / stats.scope) * 100) : 0;
  const badge = state === 'current' ? 'Current' : state === 'upcoming' ? 'Upcoming' : state === 'completed' ? 'Completed' : 'Planned';
  return (
    <div className="lin-cycle-row">
      <div className={cx('lin-cycle-tmark', state === 'current' && 'now')}>
        <span>{fmtDate(cycle.startsAt).replace(' ', '\n')}</span><i />
      </div>
      <div className="lin-cycle-body">
        <div className="top">
          <button type="button" className="cname" onClick={() => onCycle(cycle)}>
            <CycleStateIcon state={state} />{name}
          </button>
          <span className="stats">
            <span className={cx('lin-badge', state === 'current' && 'current')}>{badge}</span>
            {state === 'completed'
              ? <><span className="lin-cap"><Ring frac={pct / 100} /> <b>{pct}%</b> success</span>
                  <span><b>{stats.completed}</b> completed</span></>
              : <span className="lin-cap"><Ring frac={stats.scope ? stats.started / stats.scope : 0} /> <b>{pct}%</b> of capacity</span>}
            <span><b>{stats.scope}</b> scope</span>
            <span className="range">{fmtRange(cycle.startsAt, cycle.endsAt)}</span>
            <button type="button" className="lin-cycle-detail-btn" onClick={() => onCycle(cycle)}>Cycle detail</button>
          </span>
        </div>
        {showChart && (
          <div className="lin-cycle-chart">
            <div className="chart"><BurndownChart cycle={cycle} stats={stats} height={180} /></div>
            <Legend stats={stats} />
          </div>
        )}
      </div>
    </div>
  );
}

function orderCycles(cycles: Cycle[]): {cycle: Cycle; state: CycleState}[] {
  const now = Date.now();
  const withState = cycles.map(c => ({cycle: c, state: cycleState(c, now)}));
  withState.sort((a, b) => (b.cycle.startsAt || '').localeCompare(a.cycle.startsAt || ''));
  // mark only the nearest future cycle as upcoming; the rest are planned
  let seenUpcoming = false;
  for (const w of [...withState].reverse()) {
    if (w.state === 'upcoming') {
      if (seenUpcoming) w.state = 'planned';
      seenUpcoming = true;
    }
  }
  return withState;
}

export function CycleOverviewRows({cycles, issuesByCycle, onCycle, emptyHint}: {
  cycles: Cycle[]; issuesByCycle: Map<string, LinIssue[]>; onCycle: (c: Cycle) => void; emptyHint?: string;
}) {
  const ordered = orderCycles(cycles);
  const current = ordered.find(o => o.state === 'current');
  if (!ordered.length) {
    return (
      <div className="lin-empty">
        <CalendarDays size={30} strokeWidth={1.4} />
        <h2>No cycles yet</h2>
        <p>{emptyHint || 'Enable automatic cycles in team cycle settings to start planning work in iterations.'}</p>
      </div>
    );
  }
  return (
    <div className="lin-cycles-tl">
      <div className="lin-cycles-spine" />
      {ordered.map(({cycle, state}) => (
        <CycleOverviewRow key={cycle.id} cycle={cycle} state={state}
          stats={statsFor(issuesByCycle.get(cycle.id) || [])}
          onCycle={onCycle} showChart={current?.cycle.id === cycle.id} />
      ))}
    </div>
  );
}

/* ---------------- team cycles overview page ---------------- */

export function LinearTeamCycles({team, teams, onCycle, onSettings, revision, onNavigate, workspaceName, viewer}: {
  team?: Team; teams: Team[]; onCycle: (c: Cycle) => void; onSettings: () => void;
  revision: number; onNavigate: (view: string, path?: string) => void; workspaceName: string; viewer: Viewer;
}) {
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [issues, setIssues] = useState<LinIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useDismiss(() => setMenuOpen(false));

  useEffect(() => {
    if (!team) return;
    let live = true;
    setLoading(true); setError('');
    Promise.all([
      gql(`query($filter:CycleFilter){cycles(first:100,filter:$filter){nodes{id name description number startsAt endsAt completedAt progress team{id key name}}}}`,
        {filter: {team: {id: {eq: team.id}}}}),
      gql(`query($filter:IssueFilter){issues(first:200,filter:$filter,orderBy:updatedAt){nodes ${linIssueFields}}}`,
        {filter: {team: {id: {eq: team.id}}}}),
    ]).then(([c, i]) => {
      if (!live) return;
      setCycles(c.cycles.nodes); setIssues(i.issues.nodes);
    }).catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [team?.id, revision]);

  const issuesByCycle = useMemo(() => {
    const m = new Map<string, LinIssue[]>();
    for (const i of issues) {
      const cid = i.cycle?.id;
      if (!cid) continue;
      if (!m.has(cid)) m.set(cid, []);
      m.get(cid)!.push(i);
    }
    return m;
  }, [issues]);

  if (!team) return <section className="lin-page"><p className="lin-error" role="alert">Team not found in this workspace.</p></section>;

  return (
    <section className="lin-page" aria-label={`${team.name} cycles`}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={() => onNavigate('Team Home', `/workspace/team/${team.key.toLowerCase()}/home`)}>{workspaceName}</button>
          <span className="sep">›</span>
          <span className="current">Cycles</span>
        </nav>
        <div className="lin-head-actions">
          <FavStar kind="cycles" id={`team-cycles-${team.id}`} label={`${team.name} cycles`} />
          <span style={{position: 'relative'}} ref={menuRef}>
            <IconBtn label="Cycle options" title="More actions" onClick={() => setMenuOpen(v => !v)}><span style={{fontWeight: 700}}>···</span></IconBtn>
            {menuOpen && <LinMenu onClose={() => setMenuOpen(false)} className="lin-dd-panel" items={[
              {key: 'settings', label: 'Cycle settings', icon: <Settings2 size={14} />, onSelect: onSettings},
            ]} />}
          </span>
        </div>
      </header>
      <div className="lin-cycles-head"><h1>Cycles</h1></div>
      <div className="lin-cycles-wrap">
        <div className="lin-scroll" style={{flex: 1}}>
          {loading && <p className="lin-loading" role="status">Loading cycles…</p>}
          {error && <p className="lin-error" role="alert">{error}</p>}
          {!loading && !error && (
            <CycleOverviewRows cycles={cycles} issuesByCycle={issuesByCycle} onCycle={onCycle}
              emptyHint={`${team.name} has no cycles yet. Enable automatic cycles in cycle settings.`} />
          )}
          {!loading && !error && !!cycles.length && (
            <div style={{padding: '8px 28px'}}>
              <button type="button" className="lin-btn" onClick={onSettings}>Cycle settings</button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ---------------- workspace cycles (route fix) ---------------- */

export function LinearWorkspaceCycles({teams, onCycle, onSettings, revision, onNavigate, workspaceName, viewer}: {
  teams: Team[]; onCycle: (c: Cycle) => void; onSettings: (team: Team) => void;
  revision: number; onNavigate: (view: string, path?: string) => void; workspaceName: string; viewer: Viewer;
}) {
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [issues, setIssues] = useState<LinIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setLoading(true); setError('');
    Promise.all([
      gql(`query{cycles(first:100){nodes{id name description number startsAt endsAt completedAt progress team{id key name}}}}`),
      gql(`query{issues(first:200,orderBy:updatedAt){nodes ${linIssueFields}}}`),
    ]).then(([c, i]) => { if (!live) return; setCycles(c.cycles.nodes); setIssues(i.issues.nodes); })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [revision]);
  const issuesByCycle = useMemo(() => {
    const m = new Map<string, LinIssue[]>();
    for (const i of issues) {
      const cid = i.cycle?.id;
      if (!cid) continue;
      if (!m.has(cid)) m.set(cid, []);
      m.get(cid)!.push(i);
    }
    return m;
  }, [issues]);
  const byTeam = useMemo(() => {
    const m = new Map<string, {team: Team; cycles: Cycle[]}>();
    for (const t of teams) m.set(t.id, {team: t, cycles: []});
    for (const c of cycles) {
      const tid = c.team?.id;
      if (tid && m.has(tid)) m.get(tid)!.cycles.push(c);
    }
    return [...m.values()].filter(g => g.cycles.length);
  }, [teams, cycles]);
  return (
    <section className="lin-page" aria-label="Workspace cycles">
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <span className="current">{workspaceName}</span><span className="sep">›</span><span className="current">Cycles</span>
        </nav>
      </header>
      <div className="lin-cycles-head"><h1>Cycles</h1></div>
      <div className="lin-scroll">
        {loading && <p className="lin-loading" role="status">Loading cycles…</p>}
        {error && <p className="lin-error" role="alert">{error}</p>}
        {!loading && !error && !byTeam.length && (
          <div className="lin-empty"><CalendarDays size={30} strokeWidth={1.4} /><h2>No cycles yet</h2>
            <p>Enable automatic cycles in a team's cycle settings to start planning work in iterations.</p></div>
        )}
        {byTeam.map(({team, cycles: tc}) => (
          <section key={team.id} style={{marginBottom: 8}}>
            <div style={{padding: '14px 28px 0', display: 'flex', alignItems: 'center', gap: 10}}>
              <b style={{fontSize: 13}}>{team.name}</b>
              <button type="button" className="lin-addrow" onClick={() => onSettings(team)}>Cycle settings</button>
            </div>
            <CycleOverviewRows cycles={tc} issuesByCycle={issuesByCycle} onCycle={onCycle} />
          </section>
        ))}
      </div>
    </section>
  );
}

/* ---------------- cycle detail ---------------- */

function download(filename: string, content: string, mime: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], {type: mime}));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

function cycleICS(cycle: Cycle): string {
  const f = (iso?: string | null) => (iso || '').replace(/[-:]/g, '').slice(0, 15) + 'Z';
  const name = cycle.name || `Cycle ${cycle.number ?? ''}`;
  return ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Hoja//Cycle//EN', 'BEGIN:VEVENT',
    `UID:${cycle.id}@hoja`, `DTSTART:${f(cycle.startsAt)}`, `DTEND:${f(cycle.endsAt)}`,
    `SUMMARY:${name}`, 'END:VEVENT', 'END:VCALENDAR'].join('\r\n');
}

function issuesCSV(issues: LinIssue[]): string {
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const head = ['Identifier', 'Title', 'Status', 'Priority', 'Assignee', 'Labels', 'Project', 'Cycle', 'Created', 'Updated'];
  const rows = issues.map(i => [i.identifier, i.title, i.state?.name, i.priorityLabel || i.priority,
    i.assignee?.name, (i.labels?.nodes || []).map(l => l.name).join('; '), i.project?.name,
    i.cycle?.name || i.cycle?.number, i.createdAt, i.updatedAt].map(esc).join(','));
  return [head.join(','), ...rows].join('\n');
}

function CycleOptionsMenu({cycle, issues, onChanged, onClose}: {
  cycle: Cycle; issues: LinIssue[]; onChanged: (c: Cycle) => void; onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [dates, setDates] = useState(false);
  const [name, setName] = useState(cycle.name || '');
  const [description, setDescription] = useState(cycle.description || '');
  const [startsAt, setStartsAt] = useState((cycle.startsAt || '').slice(0, 10));
  const [endsAt, setEndsAt] = useState((cycle.endsAt || '').slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fav, toggleFav] = useFavorite('cycles', cycle.id);
  const save = async (input: Record<string, unknown>) => {
    setBusy(true); setError('');
    try {
      const d = await gql(`mutation($id:String!,$input:CycleUpdateInput!){cycleUpdate(id:$id,input:$input){success cycle{id name description number startsAt endsAt completedAt progress team{id key name}}}}`,
        {id: cycle.id, input});
      if (!d.cycleUpdate?.success) throw new Error('Cycle update was rejected');
      onChanged(d.cycleUpdate.cycle);
      setEditing(false); setDates(false);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  if (editing || dates) {
    return (
      <div className="lin-popover" style={{padding: 14, width: 300}}>
        <b style={{fontSize: 13}}>{editing ? 'Edit cycle' : 'Change cycle dates'}</b>
        {editing && <>
          <label style={{display: 'grid', gap: 4, marginTop: 10, fontSize: 12}}>Name
            <input value={name} onChange={e => setName(e.target.value)} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
          <label style={{display: 'grid', gap: 4, marginTop: 8, fontSize: 12}}>Description
            <input value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional" style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
        </>}
        {dates && <>
          <label style={{display: 'grid', gap: 4, marginTop: 10, fontSize: 12}}>Start date
            <input type="date" value={startsAt} onChange={e => setStartsAt(e.target.value)} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
          <label style={{display: 'grid', gap: 4, marginTop: 8, fontSize: 12}}>End date
            <input type="date" value={endsAt} onChange={e => setEndsAt(e.target.value)} style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
        </>}
        {error && <p className="lin-error" style={{margin: '8px 0'}}>{error}</p>}
        <div style={{display: 'flex', gap: 8, marginTop: 12}}>
          <button type="button" className="lin-btn primary" disabled={busy}
            onClick={() => void save(editing ? {name: name.trim() || null, description: description.trim() || null} : {startsAt: startsAt || null, endsAt: endsAt || null})}>
            {busy ? 'Saving…' : 'Save'}</button>
          <button type="button" className="lin-btn" onClick={() => { setEditing(false); setDates(false); }}>Cancel</button>
        </div>
      </div>
    );
  }
  const cname = cycle.name || `Cycle ${cycle.number ?? ''}`;
  return (
    <LinMenu onClose={onClose} className="lin-dd-panel" items={[
      {key: 'edit', label: 'Edit cycle name and description…', onSelect: () => setEditing(true)},
      {key: 'dates', label: 'Change cycle dates', onSelect: () => setDates(true)},
      {key: 'sub', label: 'Subscribe to cycle notifications', disabled: true, onSelect: () => {}},
      {key: 'div1', divider: true},
      {key: 'fav', label: fav ? 'Remove from favorites' : 'Favorite', shortcut: 'ALT F', icon: fav ? <Check size={14} /> : undefined, onSelect: toggleFav},
      {key: 'copy', label: 'Copy link', icon: <Link2 size={14} />, onSelect: () => { void navigator.clipboard?.writeText(window.location.href); }},
      {key: 'cal', label: 'Subscribe to cycle calendar', icon: <CalendarDays size={14} />, onSelect: () => download(`${cname.replace(/\s+/g, '-').toLowerCase()}.ics`, cycleICS(cycle), 'text/calendar')},
      {key: 'csv', label: 'Export issues as CSV…', icon: <Download size={14} />, onSelect: () => download(`${cname.replace(/\s+/g, '-').toLowerCase()}-issues.csv`, issuesCSV(issues), 'text/csv')},
    ]} />
  );
}

export function LinearCycleDetail({cycle: initial, team, teams, viewer, orgId, workspaceName, projects, labels, cycles, revision, onBack, onSelect, onNavigate}: {
  cycle: Cycle; team?: Team; teams: Team[]; viewer: Viewer; orgId: string; workspaceName: string;
  projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  revision: number; onBack: () => void; onSelect: (issue: LinIssue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  const [cycle, setCycle] = useState<Cycle>(initial);
  const [issues, setIssues] = useState<LinIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filters, setFilters] = useState<LinFilters>({...emptyFilters});
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [paneOpen, setPaneOpen] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [progressOpen, setProgressOpen] = useState(true);
  const [dimTab, setDimTab] = useState<'assignees' | 'labels' | 'priority' | 'projects'>('assignees');
  const [dimFilter, setDimFilter] = useState<{kind: string; id: string} | null>(null);
  const [dateOpen, setDateOpen] = useState(false);
  const [endDate, setEndDate] = useState((initial.endsAt || '').slice(0, 10));
  const [savingDate, setSavingDate] = useState(false);
  const menuRef = useDismiss(() => setMenuOpen(false));
  const dateRef = useDismiss(() => setDateOpen(false));
  const display: LinDisplay = {...defaultDisplay, grouping: 'status'};

  useEffect(() => { setCycle(initial); setEndDate((initial.endsAt || '').slice(0, 10)); }, [initial.id]);

  useEffect(() => {
    let live = true;
    setLoading(true); setError('');
    gql(`query($filter:IssueFilter){issues(first:100,filter:$filter,orderBy:updatedAt){nodes ${linIssueFields}}}`,
      {filter: {cycle: {id: {eq: cycle.id}}}})
      .then(d => { if (live) setIssues(d.issues.nodes); })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [cycle.id, revision]);

  const state = cycleState(cycle);
  const cname = cycle.name || `Cycle ${cycle.number ?? ''}`;
  const stats = useMemo(() => statsFor(issues), [issues]);
  const childCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of issues) { const pid = i.parent?.id; if (pid) m.set(pid, (m.get(pid) || 0) + 1); }
    return m;
  }, [issues]);

  const scoped = useMemo(() => {
    let base = applyLinFilters(issues, filters);
    if (dimFilter) {
      if (dimFilter.kind === 'assignees') base = base.filter(i => (i.assignee?.id || 'none') === dimFilter.id);
      if (dimFilter.kind === 'labels') base = base.filter(i => i.labels?.nodes.some(l => l.id === dimFilter.id));
      if (dimFilter.kind === 'priority') base = base.filter(i => String(i.priority || 0) === dimFilter.id);
      if (dimFilter.kind === 'projects') base = base.filter(i => (i.project?.id || 'none') === dimFilter.id);
    }
    return orderLinIssues(base, display);
  }, [issues, filters, dimFilter]);
  const groups = useMemo(() => groupLinIssues(scoped, display, team?.states.nodes || []), [scoped, team]);

  const dimRows = useMemo(() => {
    const pct = (done: number, total: number) => total ? Math.round((done / total) * 100) : 0;
    if (dimTab === 'assignees') {
      const m = new Map<string, {id: string; name: string; total: number; done: number}>();
      for (const i of issues) {
        const id = i.assignee?.id || 'none', name = i.assignee?.name || 'No assignee';
        if (!m.has(id)) m.set(id, {id, name, total: 0, done: 0});
        const r = m.get(id)!; r.total++; if (i.state?.type === 'completed') r.done++;
      }
      return [...m.values()].map(r => ({key: r.id, icon: r.id === 'none' ? <span className="lin-avatar" style={{width: 20, height: 20}}>?</span> : <Avatar name={r.name} size={20} />, name: r.name, pct: pct(r.done, r.total), of: r.total}));
    }
    if (dimTab === 'labels') {
      const m = new Map<string, {id: string; label: IssueLabel; total: number; done: number}>();
      for (const i of issues) for (const l of i.labels?.nodes || []) {
        if (!m.has(l.id)) m.set(l.id, {id: l.id, label: l, total: 0, done: 0});
        const r = m.get(l.id)!; r.total++; if (i.state?.type === 'completed') r.done++;
      }
      return [...m.values()].map(r => ({key: r.id, icon: <i style={{width: 8, height: 8, borderRadius: 99, background: r.label.color || '#8a94a6'}} />, name: r.label.name, pct: pct(r.done, r.total), of: r.total}));
    }
    if (dimTab === 'priority') {
      const names = ['No priority', 'Urgent', 'High', 'Medium', 'Low'];
      const m = new Map<number, {total: number; done: number}>();
      for (const i of issues) {
        const p = i.priority || 0;
        if (!m.has(p)) m.set(p, {total: 0, done: 0});
        const r = m.get(p)!; r.total++; if (i.state?.type === 'completed') r.done++;
      }
      return [...m.entries()].map(([p, r]) => ({key: String(p), icon: <PriorityIcon priority={p} />, name: names[p], pct: pct(r.done, r.total), of: r.total}));
    }
    const m = new Map<string, {id: string; name: string; total: number; done: number}>();
    for (const i of issues) {
      const id = i.project?.id || 'none', name = i.project?.name || 'No project';
      if (!m.has(id)) m.set(id, {id, name, total: 0, done: 0});
      const r = m.get(id)!; r.total++; if (i.state?.type === 'completed') r.done++;
    }
    return [...m.values()].map(r => ({key: r.id, icon: <span>▣</span>, name: r.name, pct: pct(r.done, r.total), of: r.total}));
  }, [issues, dimTab]);

  const saveEndDate = async () => {
    setSavingDate(true);
    try {
      const d = await gql(`mutation($id:String!,$input:CycleUpdateInput!){cycleUpdate(id:$id,input:$input){success cycle{id name description number startsAt endsAt completedAt progress team{id key name}}}}`,
        {id: cycle.id, input: {endsAt: endDate || null}});
      if (!d.cycleUpdate?.success) throw new Error('Could not change the end date');
      setCycle(d.cycleUpdate.cycle);
      setDateOpen(false);
    } catch (e) { setError((e as Error).message); } finally { setSavingDate(false); }
  };

  return (
    <section className="lin-page" aria-label={cname}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={onBack}>{workspaceName}</button>
          <span className="sep">›</span>
          <button type="button" className="crumb-link" onClick={onBack}>Cycles</button>
          <span className="sep">›</span>
          <CycleStateIcon state={state} size={13} />
          <span className="current">{cname}</span>
          <ChevronDown size={13} color="var(--muted)" />
        </nav>
        <div className="lin-head-actions">
          <FavStar kind="cycles" id={cycle.id} label={cname} />
          <span style={{position: 'relative'}} ref={menuRef}>
            <IconBtn label="Cycle options" title="Cycle options" onClick={() => setMenuOpen(v => !v)}><span style={{fontWeight: 700}}>···</span></IconBtn>
            {menuOpen && <CycleOptionsMenu cycle={cycle} issues={issues} onChanged={setCycle} onClose={() => setMenuOpen(false)} />}
          </span>
        </div>
      </header>

      <div className="lin-cdetail">
        <div className="lin-cdetail-main">
          <div className="lin-cdetail-count">{issues.length} issues{dimFilter && <> · filtered <button type="button" className="lin-addrow" onClick={() => setDimFilter(null)}>Clear</button></>}</div>
          <div className="lin-toolbar">
            <span className="spacer" />
            <span style={{position: 'relative'}}>
              <IconBtn label="Add filter" title="Add filter" active={filterOpen} onClick={() => { setFilterOpen(v => !v); setDisplayOpen(false); }}><ListFilter size={15} /></IconBtn>
              {filterOpen && <FilterMenu filters={filters} setFilters={setFilters} team={team} teams={teams}
                projects={projects} labels={labels} cycles={cycles} onClose={() => setFilterOpen(false)} />}
            </span>
            <span style={{position: 'relative'}}>
              <IconBtn label="Display options" title="Display options" active={displayOpen} onClick={() => { setDisplayOpen(v => !v); setFilterOpen(false); }}><SlidersHorizontal size={15} /></IconBtn>
              {displayOpen && <_DP display={{...display, layout: 'List'} as LinDisplay} setDisplay={() => {}} onClose={() => setDisplayOpen(false)} />}
            </span>
            <IconBtn label={paneOpen ? 'Close cycle details' : 'Open cycle details'} title="Toggle details pane"
              active={paneOpen} onClick={() => setPaneOpen(v => !v)}>
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M10 2.5v11" /></svg>
            </IconBtn>
          </div>
          <div className="lin-scroll">
            {loading && <p className="lin-loading" role="status">Loading cycle issues…</p>}
            {error && <p className="lin-error" role="alert">{error}</p>}
            {!loading && !error && (
              <div className="lin-list">
                {groups.map(g => (
                  <section key={g.key} className="lin-group">
                    <header className="lin-group-head">
                      <button type="button" className="chev" aria-label={`${collapsed.includes(g.key) ? 'Expand' : 'Collapse'} ${g.name}`}
                        onClick={() => setCollapsed(c => c.includes(g.key) ? c.filter(x => x !== g.key) : [...c, g.key])}>
                        <ChevronDown size={14} style={{transform: collapsed.includes(g.key) ? 'rotate(-90deg)' : undefined}} />
                      </button>
                      {g.state && <LinearStatusIcon state={g.state} size={13} />}
                      <b>{g.name}</b><span className="count">{g.items.length}</span>
                      <span className="grow" />
                      <Tip label={`Create issue in ${g.name}`}>
                        <button type="button" className="add" aria-label={`Create issue in ${g.name}`}><Plus size={14} /></button>
                      </Tip>
                    </header>
                    {!collapsed.includes(g.key) && g.items.map(i => (
                      <IssueRow key={i.id} issue={i} display={display} selected={false}
                        showCheckbox={false} onSelect={() => {}} onOpen={onSelect} childCount={childCounts.get(i.id) || 0} />
                    ))}
                  </section>
                ))}
                {!scoped.length && (
                  <div className="lin-empty"><h2>No issues in this cycle</h2><p>Issues added to this cycle will appear here.</p></div>
                )}
              </div>
            )}
          </div>
        </div>

        {paneOpen && (
          <aside className="lin-cpane" aria-label="Cycle details">
            <div className="cur">
              <span className="lin-badge current">{state === 'current' ? 'Current' : state === 'upcoming' ? 'Upcoming' : state === 'completed' ? 'Completed' : 'Planned'}</span>
              <span className="lin-badge">{fmtRange(cycle.startsAt, cycle.endsAt)}</span>
            </div>
            <h2><CycleStateIcon state={state} size={16} /> {cname}
              <span style={{flex: 1}} />
              <FavStar kind="cycles" id={cycle.id} label={cname} />
            </h2>
            <Tip label="Cycle documents are not available in Hoja yet">
              <button type="button" className="adddoc" disabled style={{opacity: .55, cursor: 'not-allowed'}}>
                <Plus size={14} /> Add document or link…
              </button>
            </Tip>
            <p className="datenote">Can only move start date for upcoming cycle</p>
            <span style={{position: 'relative'}} ref={dateRef}>
              <button type="button" className="datebtn" onClick={() => setDateOpen(v => !v)}>
                Change cycle end date: {fmtDate(cycle.endsAt)} <ChevronDown size={13} />
              </button>
              {dateOpen && (
                <div className="lin-popover" style={{left: 0, top: 'calc(100% + 6px)', padding: 12, width: 250}}>
                  <label style={{display: 'grid', gap: 6, fontSize: 12}}>End date
                    <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                      style={{border: '1px solid var(--line)', borderRadius: 6, padding: '7px 10px', fontSize: 13}} /></label>
                  <button type="button" className="lin-btn primary" style={{marginTop: 10, width: '100%', justifyContent: 'center'}}
                    disabled={savingDate} onClick={() => void saveEndDate()}>{savingDate ? 'Saving…' : 'Save'}</button>
                </div>
              )}
            </span>

            <button type="button" className="lin-progress-head" aria-expanded={progressOpen} onClick={() => setProgressOpen(v => !v)}>
              <ChevronDown size={14} style={{transform: progressOpen ? undefined : 'rotate(-90deg)'}} color="var(--muted)" /> Progress
            </button>
            {progressOpen && <>
              <Legend stats={stats} />
              <div style={{marginTop: 8}}><BurndownChart cycle={cycle} stats={stats} /></div>
            </>}

            <div className="lin-ctabs" role="tablist" aria-label="Cycle breakdown">
              {(['assignees', 'labels', 'priority', 'projects'] as const).map(t => (
                <button key={t} type="button" role="tab" aria-selected={dimTab === t} className="lin-ctab"
                  onClick={() => { setDimTab(t); setDimFilter(null); }}>
                  {t[0].toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>
            <div>
              {dimRows.map(r => (
                <button key={r.key} type="button" className="lin-statrow"
                  onClick={() => setDimFilter(f => f?.id === r.key && f.kind === dimTab ? null : {kind: dimTab, id: r.key})}
                  style={dimFilter?.id === r.key ? {background: 'var(--subtle)'} : undefined}>
                  {r.icon}<span className="nm">{r.name}</span>
                  <span className="pc"><Ring frac={r.pct / 100} /> {r.pct}% of {r.of}</span>
                </button>
              ))}
              {!dimRows.length && <p className="lin-note">Nothing to break down yet.</p>}
            </div>
          </aside>
        )}
      </div>
    </section>
  );
}

/* ---------------- current / upcoming wrapper ---------------- */

export function LinearTeamCycle({team, teams, viewer, orgId, workspaceName, projects, labels, cycles, revision, upcoming, onBack, onSelect, onNavigate}: {
  team?: Team; teams: Team[]; viewer: Viewer; orgId: string; workspaceName: string;
  projects: Project[]; labels: IssueLabel[]; cycles: Cycle[];
  revision: number; upcoming: boolean; onBack: () => void; onSelect: (issue: LinIssue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!team) return;
    let live = true;
    setLoading(true); setError('');
    gql(`query($filter:CycleFilter){cycles(first:10,filter:$filter){nodes{id name description number startsAt endsAt completedAt progress team{id key name}}}}`,
      {filter: {team: {id: {eq: team.id}}, [upcoming ? 'isNext' : 'isActive']: {eq: true}}})
      .then(d => { if (live) setCycle(d.cycles.nodes[0] || null); })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [team?.id, upcoming, revision]);
  if (!team) return <section className="lin-page"><p className="lin-error" role="alert">Team not found in this workspace.</p></section>;
  if (loading) return <section className="lin-page"><p className="lin-loading" role="status">Loading cycle…</p></section>;
  if (error) return <section className="lin-page"><p className="lin-error" role="alert">{error}</p></section>;
  if (!cycle) {
    return (
      <section className="lin-page">
        <header className="lin-head">
          <nav className="lin-crumb"><button type="button" className="crumb-link" onClick={onBack}>← Cycles</button></nav>
        </header>
        <div className="lin-empty"><CalendarDays size={30} strokeWidth={1.4} />
          <h2>No {upcoming ? 'upcoming' : 'current'} cycle</h2>
          <p>{team.name} has no {upcoming ? 'next' : 'active'} cycle in this workspace.</p></div>
      </section>
    );
  }
  return <LinearCycleDetail cycle={cycle} team={team} teams={teams} viewer={viewer} orgId={orgId}
    workspaceName={workspaceName} projects={projects} labels={labels} cycles={cycles}
    revision={revision} onBack={onBack} onSelect={onSelect} onNavigate={onNavigate} />;
}

/* ---------------- cycle settings ---------------- */

export function LinearTeamCycleSettings({team, onBack, admin}: {
  team?: Team; onBack: () => void; admin?: boolean;
}) {
  const [saved, setSaved] = useState<{enabled: boolean; weekday?: number | null; timezone: string; policyVersion: number} | null>(null);
  const [weekday, setWeekday] = useState('');
  const [autoStarted, setAutoStarted] = useState<boolean | null>(null);
  const [autoCompleted, setAutoCompleted] = useState<boolean | null>(null);
  const [autoBacklog, setAutoBacklog] = useState<boolean | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!team) return;
    let live = true;
    setLoading(true); setError('');
    gql(`query($id:String!){teamCycleCadence(teamId:$id){enabled weekday timezone policyVersion}}`, {id: team.id})
      .then(d => { if (live) { setSaved(d.teamCycleCadence); setWeekday(d.teamCycleCadence.weekday == null ? '' : String(d.teamCycleCadence.weekday)); } })
      .catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    gql(`query($id:String!){team(id:$id){autoAddStarted autoAddCompleted autoAddBacklog}}`, {id: team.id})
      .then(d => { if (live && d.team) { setAutoStarted(d.team.autoAddStarted !== false); setAutoCompleted(d.team.autoAddCompleted !== false); setAutoBacklog(d.team.autoAddBacklog !== false); } })
      .catch(() => { /* flags stay null; card shows loading state */ });
    return () => { live = false; };
  }, [team?.id]);
  const enable = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!team || weekday === '' || busy) return;
    setBusy(true); setError('');
    try {
      const d = await gql(`mutation($id:String!,$weekday:Int!){teamCycleCadenceEnable(teamId:$id,weekday:$weekday){enabled weekday timezone policyVersion}}`,
        {id: team.id, weekday: Number(weekday)});
      setSaved(d.teamCycleCadenceEnable);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const saveAuto = async (started: boolean, completed: boolean, backlog: boolean) => {
    if (!team || !admin || autoBusy) return;
    setAutoBusy(true); setError('');
    try {
      const d = await gql(
        `mutation($id:String!,$input:TeamUpdateInput!){teamUpdate(id:$id,input:$input){success team{id autoAddStarted autoAddCompleted autoAddBacklog}}}`,
        {id: team.id, input: {autoAddStarted: started, autoAddCompleted: completed, autoAddBacklog: backlog}});
      if (!d.teamUpdate?.success) throw new Error('Could not save cycle automations.');
      setAutoStarted(d.teamUpdate.team.autoAddStarted !== false);
      setAutoCompleted(d.teamUpdate.team.autoAddCompleted !== false);
      setAutoBacklog(d.teamUpdate.team.autoAddBacklog !== false);
    } catch (e) { setError((e as Error).message); } finally { setAutoBusy(false); }
  };
  return (
    <section className="lin-page"><div className="lin-scroll"><div className="lin-csettings">
      <button type="button" className="lin-addrow" onClick={onBack}>← Cycles</button>
      <h1 style={{marginTop: 8}}>{team?.name || 'Team'} cycle settings</h1>
      <p className="desc">Configure how {team?.name || 'this team'} plans work in iterations.</p>
      {!team ? <p className="lin-error" role="alert">Team not found in this workspace.</p>
      : loading ? <p className="lin-loading" role="status">Loading cycle settings…</p>
      : <>
        {error && <p className="lin-error" role="alert">{error}</p>}
        <div className="card">
          <h2>Automatic cycles</h2>
          {saved?.enabled
            ? <p>Enabled · {saved.timezone} · cycles start each {days[saved.weekday ?? 0]}. The current and next two cycles are generated automatically; completed cycles close on their end date.</p>
            : <p>When enabled, Hoja generates one-week cycles for this team: a current cycle and two upcoming cycles, rolling forward automatically.</p>}
          {!saved?.enabled && (
            <form onSubmit={enable}>
              <label className="f">Start day
                <select value={weekday} onChange={e => setWeekday(e.target.value)} required>
                  <option value="">Choose weekday</option>
                  {days.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
              </label>
              {!admin && <p className="lin-note" role="status">Only workspace admins can enable team cycles.</p>}
              <button type="submit" className="lin-btn primary" disabled={!admin || busy || weekday === ''}>
                {busy ? 'Enabling…' : 'Enable automatic cycles'}</button>
            </form>
          )}
        </div>
        <div className="card">
          <h2>Cycle length</h2>
          <p>One-week cycles, no cooldown, two upcoming.</p>
          <div className="row2">
            <label className="f">Length
              <select value="1" disabled><option value="1">1 week</option></select></label>
            <label className="f">Cooldown
              <select value="0" disabled><option value="0">None</option></select></label>
          </div>
          <p className="lin-note">Other lengths and cooldowns are not supported by this server yet.</p>
        </div>
        <div className="card">
          <h2>Auto-complete</h2>
          <p>Cycles complete automatically on their end date. Incomplete issues roll into the next cycle.</p>
          <p className="lin-note">Changing or disabling the schedule is not available in this release.</p>
        </div>
        <div className="card">
          <h2>Cycle automations</h2>
          <p>Automatically add issues without a cycle into the current cycle. Issues already assigned to a cycle are never moved.</p>
          {autoStarted === null || autoCompleted === null || autoBacklog === null
            ? <p className="lin-loading" role="status">Loading automations…</p>
            : <>
              <label className="lin-auto-row">
                <input type="checkbox" className="lin-check" checked={autoStarted}
                  disabled={!admin || autoBusy}
                  onChange={e => { const v = e.target.checked; setAutoStarted(v); void saveAuto(v, autoCompleted, autoBacklog); }} />
                <span>Auto-add <b>started</b> issues to the current cycle</span>
              </label>
              <label className="lin-auto-row">
                <input type="checkbox" className="lin-check" checked={autoCompleted}
                  disabled={!admin || autoBusy}
                  onChange={e => { const v = e.target.checked; setAutoCompleted(v); void saveAuto(autoStarted, v, autoBacklog); }} />
                <span>Auto-add <b>completed</b> issues to the current cycle</span>
              </label>
              <label className="lin-auto-row">
                <input type="checkbox" className="lin-check" checked={autoBacklog}
                  disabled={!admin || autoBusy}
                  onChange={e => { const v = e.target.checked; setAutoBacklog(v); void saveAuto(autoStarted, autoCompleted, v); }} />
                <span>Auto-add <b>backlog</b> issues to the current cycle</span>
              </label>
              {!admin && <p className="lin-note" role="status">Only workspace admins can change cycle automations.</p>}
            </>}
        </div>
      </>}
    </div></div></section>
  );
}
