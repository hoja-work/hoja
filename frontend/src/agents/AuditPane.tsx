/* Screen 6 — Audit log (HOJA-602-A-16).
 *
 * `audit_event` rows in the issue list's layout, filtered by actor or action. Grouping is the
 * shared `DisplayPanel`, which is passed the two groupings this data has (actor, action) and the
 * three fields a row can show. The rows reuse `.lin-row` so an audit row sits on the same
 * 42 px rhythm as a task row.
 */

import {useEffect, useMemo, useState} from 'react';
import {Circle, Play, Settings2, ShieldOff, SlidersHorizontal, Square} from 'lucide-react';
import {Avatar, IconBtn, cx} from '../graph/shared';
import {DisplayPanel, defaultDisplay, type LinDisplay} from '../graph/tasks';
import {getAgentsAdapter} from './adapter';
import {AgentsListShell, SearchField} from './chrome';
import type {AuditEvent} from './types';

export const AUDIT_DISPLAY: LinDisplay = {...defaultDisplay, grouping: 'action', ordering: 'updated', direction: 'desc'};

/* One 14 px glyph per action family. It occupies the issue row's status column: an audit row has
 * no status, and a column of identical blanks would read as a rendering bug. */
function actionGlyph(action: string) {
  if (action.startsWith('policy.')) return <Settings2 size={14} aria-hidden="true" />;
  if (action.startsWith('session.start')) return <Play size={14} aria-hidden="true" />;
  if (action.startsWith('session.stop')) return <Square size={14} aria-hidden="true" />;
  if (action.startsWith('runner.')) return <ShieldOff size={14} aria-hidden="true" />;
  return <Circle size={14} aria-hidden="true" />;
}

function stamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().replace('T', ' ').slice(0, 16) + 'Z';
}

function AuditRow({event, show, selected}: {event: AuditEvent; show: {actor: boolean; target: boolean; time: boolean}; selected: boolean}) {
  return (
    <div className={cx('lin-row', selected && 'selected')} data-action={event.action}>
      {actionGlyph(event.action)}
      {show.actor ? <Avatar name={event.actor} size={14} title={event.actor} /> : <span />}
      <span className="ident">{event.action}</span>
      {/* An audit row is a record, not a target: nothing opens from it, so it is not a button. */}
      <span className="title" title={event.detail}>{event.detail || 'No detail recorded'}</span>
      <span className="meta">
        {show.target && <span className="hidden md:inline-flex"><span className="lin-chip" title="Target">{event.target}</span></span>}
        {show.actor && <span className="date hidden sm:inline">{event.actor}</span>}
        {show.time && <span className="date" title={event.at}>{stamp(event.at)}</span>}
      </span>
    </div>
  );
}

export function AuditPane({workspaceName, tab, onTab}: {workspaceName: string; tab: string; onTab: (id: string) => void}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [displayOpen, setDisplayOpen] = useState(false);
  const [display, setDisplay] = useState<LinDisplay>({...AUDIT_DISPLAY});
  const [show, setShow] = useState({actor: true, target: true, time: true});

  useEffect(() => {
    let live = true;
    getAgentsAdapter().listAuditEvents()
      .then(rows => { if (live) { setEvents(rows); setError(''); } })
      .catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  const actors = useMemo(() => [...new Set(events.map(e => e.actor))].sort(), [events]);
  const actions = useMemo(() => [...new Set(events.map(e => e.action))].sort(), [events]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rows = events.filter(e =>
      (!actor || e.actor === actor) &&
      (!action || e.action === action) &&
      (!needle || `${e.actor} ${e.action} ${e.target} ${e.detail}`.toLowerCase().includes(needle)));
    const dir = display.direction === 'desc' ? -1 : 1;
    return rows.sort((a, b) => a.at < b.at ? dir : -dir);
  }, [events, actor, action, q, display.direction]);

  const groups = useMemo(() => {
    if (display.grouping === 'none') return [{key: 'all', name: '', items: visible}];
    const key = display.grouping === 'action' ? 'action' : 'actor';
    const map = new Map<string, {key: string; name: string; items: AuditEvent[]}>();
    for (const e of visible) {
      const k = e[key];
      if (!map.has(k)) map.set(k, {key: k, name: k, items: []});
      map.get(k)!.items.push(e);
    }
    return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [visible, display.grouping]);

  const chips = [
    actor ? {label: `Actor: ${actor}`, clear: () => setActor('')} : null,
    action ? {label: `Action: ${action}`, clear: () => setAction('')} : null,
  ].filter(Boolean) as {label: string; clear: () => void}[];

  return (
    <AgentsListShell
      ariaLabel="Audit log"
      surface="audit-log"
      workspaceName={workspaceName}
      trail="Audit log"
      tabs={[{id: 'agents', name: 'Agents'}, {id: 'queue', name: 'Run queue'}, {id: 'audit', name: 'Audit log'}]}
      activeTab={tab}
      onTab={onTab}
      toolbar={<>
        <SearchField value={q} onChange={setQ} placeholder="Search the audit log…" label="Search the audit log" />
        <label className="lin-search" style={{minWidth: 150, flex: '0 1 180px'}}>
          <span className="lin-note">Actor</span>
          <select value={actor} onChange={e => setActor(e.target.value)} aria-label="Filter by actor"
            style={{flex: 1, border: 0, background: 'transparent', color: 'var(--text)', fontSize: 13, minWidth: 0}}>
            <option value="">Anyone</option>
            {actors.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <label className="lin-search" style={{minWidth: 150, flex: '0 1 180px'}}>
          <span className="lin-note">Action</span>
          <select value={action} onChange={e => setAction(e.target.value)} aria-label="Filter by action"
            style={{flex: 1, border: 0, background: 'transparent', color: 'var(--text)', fontSize: 13, minWidth: 0}}>
            <option value="">Any action</option>
            {actions.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <span className="spacer" />
        <span style={{position: 'relative'}}>
          <IconBtn label="Display options" title="Display options" active={displayOpen} onClick={() => setDisplayOpen(v => !v)}>
            <SlidersHorizontal size={15} />
          </IconBtn>
          {displayOpen && <DisplayPanel display={display} setDisplay={d => setDisplay({...display, ...d})} onClose={() => setDisplayOpen(false)}
            groupingOptions={[['none', 'No grouping'], ['actor', 'Actor'], ['action', 'Action']]}
            groupingLabel="Group audit events by"
            entityToggles={false}
            propertyRows={[
              {key: 'actor', label: 'Actor', checked: show.actor, onToggle: () => setShow(s => ({...s, actor: !s.actor}))},
              {key: 'target', label: 'Target', checked: show.target, onToggle: () => setShow(s => ({...s, target: !s.target}))},
              {key: 'time', label: 'Time', checked: show.time, onToggle: () => setShow(s => ({...s, time: !s.time}))},
            ]} />}
        </span>
      </>}
      chips={chips.length ? <>
        {chips.map(c => (
          <span key={c.label} className="lin-fchip">{c.label}
            <button type="button" aria-label={`Remove filter ${c.label}`} onClick={c.clear}>×</button>
          </span>
        ))}
        <button type="button" className="lin-addrow" onClick={() => { setActor(''); setAction(''); }}>Clear all</button>
      </> : null}
    >
      {loading && <p className="lin-loading" role="status">Loading the audit log…</p>}
      {error && <p className="lin-error" role="alert">{error}</p>}
      {!loading && !error && (
        <div className="lin-list">
          {groups.map(g => (
            <section key={g.key} className="lin-group">
              {g.name && <header className="lin-group-head"><b>{g.name}</b><span className="count">{g.items.length}</span><span className="grow" /></header>}
              {g.items.map(e => <AuditRow key={e.id} event={e} show={show} selected={false} />)}
            </section>
          ))}
          {!visible.length && (
            <div className="lin-empty">
              <h2>No audit events match</h2>
              <p>Every policy change, session start and session stop is recorded here by the server.</p>
            </div>
          )}
        </div>
      )}
    </AgentsListShell>
  );
}
