/* Screen 6 — Audit log (HOJA-602-A-16).
 *
 * `auditEvents` rows (WP05) in the issue list's layout, filtered by actor or action and paged with
 * the connection's own cursor. Grouping is the shared `DisplayPanel`, which is passed the two
 * groupings this data has (actor, action) and the three fields a row can show. The rows reuse
 * `.lin-row` so an audit row sits on the same 42 px rhythm as a task row.
 *
 * Two things this screen has to get right rather than smooth over:
 *
 *   - the ledger is admin-only, and a refusal is stated as a refusal. An empty connection would
 *     read as "nothing has happened", which is the opposite of what a 403 means;
 *   - `read_events` pages *oldest first*, so this list grows forward in time: `Load newer events`
 *     asks for the next page after the cursor, and the display order defaults to ascending to
 *     match. Sorting the loaded page newest-first instead would make every appended row jump to
 *     the top, which looks like a different row set rather than one more page.
 */

import {useEffect, useMemo, useState} from 'react';
import {Circle, Play, Settings2, ShieldOff, SlidersHorizontal, Square} from 'lucide-react';
import {Avatar, IconBtn, cx} from '../graph/shared';
import {DisplayPanel, defaultDisplay, type LinDisplay} from '../graph/tasks';
import {AgentApiError, getAgentsAdapter} from './adapter';
import {AGENTS_TABS, AgentsListShell, SearchField} from './chrome';
import type {AuditEvent} from './types';

export const AUDIT_DISPLAY: LinDisplay = {...defaultDisplay, grouping: 'action', ordering: 'created', direction: 'asc'};

/* One 14 px glyph per action family. It occupies the issue row's status column: an audit row has
 * no status, and a column of identical blanks would read as a rendering bug. */
function actionGlyph(action: string) {
  if (action.startsWith('policy.')) return <Settings2 size={14} aria-hidden="true" />;
  if (action.endsWith('.start')) return <Play size={14} aria-hidden="true" />;
  if (action.endsWith('.stop') || action.endsWith('.close') || action.endsWith('.revoke')) {
    return <Square size={14} aria-hidden="true" />;
  }
  if (action.startsWith('runner.') || action.startsWith('connect.')) return <ShieldOff size={14} aria-hidden="true" />;
  return <Circle size={14} aria-hidden="true" />;
}

function stamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().replace('T', ' ').slice(0, 16) + 'Z';
}

function AuditRow({event, show, selected}: {event: AuditEvent; show: {actor: boolean; target: boolean; time: boolean}; selected: boolean}) {
  return (
    <div className={cx('lin-row', selected && 'selected')} data-action={event.action} data-target={event.target}>
      {actionGlyph(event.action)}
      {show.actor ? <Avatar name={event.actor} size={14} title={event.actor} /> : <span />}
      <span className="ident">{event.action}</span>
      {/* An audit row is a record, not a target: nothing opens from it, so it is not a button. */}
      <span className="title" title={event.detail}>{event.detail || 'No detail recorded'}</span>
      <span className="meta">
        {show.target && <span className="hidden md:inline-flex"><span className="lin-chip" title="Target">{event.target}</span></span>}
        {show.actor && <span className="date hidden sm:inline" data-actor-kind={event.actorKind || undefined}
          title={event.actorKind ? `${event.actorKind} · ${event.actor}` : event.actor}>{event.actorKind === 'agent' ? 'agent ' : ''}{event.actor.slice(0, 8)}</span>}
        {show.time && <span className="date" title={event.at}>{stamp(event.at)}</span>}
      </span>
    </div>
  );
}

export function AuditPane({workspaceName, tab, onTab}: {workspaceName: string; tab: string; onTab: (id: string) => void}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  /* A refusal is not an error message over an empty list; it is the state of the screen. */
  const [refused, setRefused] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [q, setQ] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [displayOpen, setDisplayOpen] = useState(false);
  const [display, setDisplay] = useState<LinDisplay>({...AUDIT_DISPLAY});
  const [show, setShow] = useState({actor: true, target: true, time: true});

  /* One page, from the top, whenever the server-side filter changes: `actorId` and `action` are
   * the resolver's arguments, so the filter has to be part of the request rather than a pass over
   * whatever happens to be loaded. */
  useEffect(() => {
    let live = true;
    setLoading(true);
    setRefused(false);
    getAgentsAdapter().listAuditEvents({actor, action})
      .then(page => {
        if (!live) return;
        setEvents(page.events);
        setCursor(page.endCursor);
        setHasMore(page.hasNextPage);
        setError('');
      })
      .catch((e: Error) => {
        if (!live) return;
        const forbidden = e instanceof AgentApiError && e.code === 'FORBIDDEN';
        setEvents([]);
        setHasMore(false);
        setRefused(forbidden);
        setError(forbidden ? '' : e.message);
      })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [actor, action]);

  const loadMore = async () => {
    if (!cursor || paging) return;
    setPaging(true);
    try {
      const page = await getAgentsAdapter().listAuditEvents({actor, action, after: cursor});
      setEvents(current => [...current, ...page.events]);
      setCursor(page.endCursor);
      setHasMore(page.hasNextPage);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPaging(false);
    }
  };

  const actors = useMemo(() => [...new Set(events.map(e => e.actor))].sort(), [events]);
  const actions = useMemo(() => [...new Set(events.map(e => e.action))].sort(), [events]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rows = events.filter(e =>
      (!actor || e.actor === actor) &&
      (!action || e.action === action) &&
      (!needle || `${e.actor} ${e.action} ${e.target} ${e.detail}`.toLowerCase().includes(needle)));
    /* `asc` is oldest first, which is the order the ledger pages in (audit.read_events walks
     * forward by `seq`), so the default view and the `Load newer events` control agree. The
     * comparator used to be inverted — a row sorted by the direction's sign rather than by the
     * date — so `asc` drew the newest event at the top and the footer's promise was false. */
    const ascending = display.direction === 'asc';
    return rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0) * (ascending ? 1 : -1));
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
      tabs={AGENTS_TABS}
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
      {!loading && refused && (
        <div className="lin-empty" data-state="error" data-refusal="forbidden" role="alert">
          <h2>Only workspace admins can read the audit log</h2>
          <p>
            The ledger records who asked for every audited action, so this server refuses the read
            rather than showing you an empty log. Ask a workspace admin for access.
          </p>
        </div>
      )}
      {!loading && !refused && error && <p className="lin-error" role="alert">{error}</p>}
      {!loading && !refused && !error && (
        <div className="lin-list">
          {groups.map(g => (
            <section key={g.key} className="lin-group">
              {g.name && <header className="lin-group-head"><b>{g.name}</b><span className="count">{g.items.length}</span><span className="grow" /></header>}
              {g.items.map(e => <AuditRow key={e.id} event={e} show={show} selected={false} />)}
            </section>
          ))}
          {!visible.length && (
            <div className="lin-empty" data-state="empty">
              <h2>{events.length ? 'No audit events match' : 'Nothing recorded yet'}</h2>
              <p>
                {events.length
                  ? 'The filters and the search box narrow the events loaded so far; clear them to see the whole page.'
                  : 'No audited action has been recorded in this workspace yet.'}
              </p>
            </div>
          )}
          <div className="lin-note" style={{display: 'flex', gap: 8, alignItems: 'center', padding: '8px 0'}}>
            <span>{events.length} event{events.length === 1 ? '' : 's'} loaded, oldest first</span>
            {hasMore && (
              <button type="button" className="lin-btn" data-action="load-more" disabled={paging} onClick={() => void loadMore()}>
                {paging ? 'Loading…' : 'Load newer events'}
              </button>
            )}
          </div>
        </div>
      )}
    </AgentsListShell>
  );
}
