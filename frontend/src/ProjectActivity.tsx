import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {gql, relTime, StatusIcon} from './main';
import type {Issue, IssueState, Project, PulsePost, Team} from './main';
import {usePagedCache, useProjectEntityVersion, type PageOf} from './projectStore';
import {Markdown} from './markdown';

/* Project Activity: one timeline of project updates and the events on the project's issues.
 * Features: merged timeline, day groups, type filters, readable event sentences, issue links,
 * health trend + staleness, a keyboard-friendly composer, "new since your last visit",
 * merged paging across both sources, and relative times with exact timestamps. */

type ActivityEvent = {id: string; eventType: string; createdAt: string; changes?: Record<string, {from: unknown; to: unknown}> | null;
  actor?: {id: string; name?: string} | null; issue: Pick<Issue, 'id' | 'identifier' | 'title'>};
type Paged<T> = {rows: T[]; hasData: boolean; more: boolean; loadingMore: boolean; error: string; loadMore: () => void; retry: () => void};
type Entry = {kind: 'update'; at: string; id: string; update: PulsePost} | {kind: 'event'; at: string; id: string; event: ActivityEvent};
export type ActivityFilter = 'All' | 'Updates' | 'Status changes' | 'Comments' | 'New tasks';
const FILTERS: ActivityFilter[] = ['All', 'Updates', 'Status changes', 'Comments', 'New tasks'];
const EVENT_TYPES: Record<ActivityFilter, string[] | null> = {All: null, Updates: null, 'Status changes': ['issueUpdated'], Comments: ['commentCreated', 'commentUpdated'], 'New tasks': ['issueCreated']};
const HEALTH = ['on track', 'at risk', 'off track'] as const;
const PRIORITY = ['No priority', 'Urgent', 'High', 'Medium', 'Low'];
const STALE_DAYS = 7;
const EVENT_FIELDS = 'id eventType changes createdAt actor{id name} issue{id identifier title}';

const dayKey = (iso: string) => iso.slice(0, 10);
function dayLabel(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const strip = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((strip(now) - strip(d)) / 864e5);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, {weekday: diff < 7 ? 'long' : undefined, month: 'short', day: 'numeric', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric'});
}
const healthSlug = (h?: string) => (h || '').toLowerCase().replace(/\s+/g, '-') || 'none';

function Time({iso}: {iso: string}) {
  return <time dateTime={iso} title={new Date(iso).toLocaleString()}>{relTime(iso)}</time>;
}

export function ProjectActivity({project, teams, users, updates, onPublish, onOpenIssue}: {
  project: Project; teams: Team[]; users: Map<string, string>;
  updates: Paged<PulsePost>; onPublish: (body: string, health: string) => Promise<void>;
  onOpenIssue: (issue: Pick<Issue, 'id' | 'identifier' | 'title'>) => void;
}) {
  const [filter, setFilter] = useState<ActivityFilter>('All');
  const types = EVENT_TYPES[filter];
  const eventsVersion = useProjectEntityVersion(['issue', 'comment']);
  const fetchEvents = useCallback((first: number, after: string | null) =>
    gql(`query ProjectActivity($id:String!,$first:Int,$after:String,$types:[String!]){project(id:$id){activity(first:$first,after:$after,eventTypes:$types){nodes{${EVENT_FIELDS}} pageInfo{hasNextPage endCursor}}}}`,
      {id: project.id, first, after, types}).then(d => d.project.activity as PageOf<ActivityEvent>), [project.id, types]);
  const eventsKey = filter === 'Updates' ? null : `projectActivity:${project.id}:${filter}`;
  const events = usePagedCache<ActivityEvent>(eventsKey, fetchEvents, eventsVersion, 50, 'Project activity');

  // "New since your last visit": read the previous visit once, record this one.
  const seenKey = `hoja_project_activity_seen_${project.id}`;
  const [lastSeen] = useState(() => localStorage.getItem(seenKey) || '');
  // The visit counts as "seen" up to when it ends, so your own posts during it are not "new" next time.
  useEffect(() => {
    const mark = () => { try { localStorage.setItem(seenKey, new Date().toISOString()); } catch { /* storage unavailable */ } };
    mark();
    window.addEventListener('pagehide', mark);
    return () => { window.removeEventListener('pagehide', mark); mark(); };
  }, [seenKey]);

  const states = useMemo(() => {
    const m = new Map<string, IssueState>();
    for (const t of teams) for (const s of t.states.nodes) m.set(s.id, s);
    return m;
  }, [teams]);

  // Merge both sources; only show entries newer than the oldest row of any source that still has
  // older pages, so a later page can never insert something above what is already on screen.
  const sources = useMemo(() => {
    const out: {rows: {at: string}[]; paged: Paged<unknown>}[] = [];
    if (filter === 'All' || filter === 'Updates') out.push({rows: updates.rows.map(u => ({at: u.createdAt})), paged: updates as Paged<unknown>});
    if (filter !== 'Updates') out.push({rows: events.rows.map(e => ({at: e.createdAt})), paged: events as unknown as Paged<unknown>});
    return out;
  }, [filter, updates, events]);
  const cutoff = sources.reduce((c, s) => s.paged.more && s.rows.length ? (s.rows[s.rows.length - 1].at > c ? s.rows[s.rows.length - 1].at : c) : c, '');
  const entries = useMemo(() => {
    const all: Entry[] = [];
    if (filter === 'All' || filter === 'Updates') for (const u of updates.rows) all.push({kind: 'update', at: u.createdAt, id: `u:${u.id}`, update: u});
    if (filter !== 'Updates') for (const e of events.rows) {
      if (filter === 'Status changes' && !e.changes?.stateId) continue;
      all.push({kind: 'event', at: e.createdAt, id: `e:${e.id}`, event: e});
    }
    return all.filter(x => x.at >= cutoff).sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  }, [filter, updates.rows, events.rows, cutoff]);
  const more = sources.some(s => s.paged.more);
  const loadingMore = sources.some(s => s.paged.loadingMore);
  const loadOlder = () => { for (const s of sources) if (s.paged.more && (!s.rows.length || s.rows[s.rows.length - 1].at === cutoff)) s.paged.loadMore(); };
  const ready = sources.every(s => s.paged.hasData || s.paged.error);
  const errors = sources.filter(s => s.paged.error);
  const newCount = lastSeen ? entries.filter(x => x.at > lastSeen).length : 0;

  const groups = useMemo(() => {
    const g: {day: string; label: string; items: Entry[]}[] = [];
    for (const x of entries) {
      const k = dayKey(x.at);
      if (g[g.length - 1]?.day !== k) g.push({day: k, label: dayLabel(x.at), items: []});
      g[g.length - 1].items.push(x);
    }
    return g;
  }, [entries]);

  // Health: newest update first; trend shows the last six, oldest to newest.
  const latest = updates.rows.find(u => !u.id.startsWith('optimistic:')) || updates.rows[0];
  const trend = updates.rows.slice(0, 6).reverse();
  const staleDays = latest ? Math.floor((Date.now() - Date.parse(latest.createdAt)) / 864e5) : null;

  // Composer
  const [body, setBody] = useState(''), [health, setHealth] = useState<string>(latest?.status && HEALTH.includes(latest.status as typeof HEALTH[number]) ? latest.status : 'on track');
  const [busy, setBusy] = useState(false), [note, setNote] = useState<{error: boolean; text: string} | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  // Default the health choice to the latest update's, until the user picks one.
  const pickedHealth = useRef(false);
  useEffect(() => { if (!pickedHealth.current && latest?.status && HEALTH.includes(latest.status as typeof HEALTH[number])) setHealth(latest.status); }, [latest?.status]);
  const submit = async () => {
    const text = body.trim();
    if (!text || busy) return;
    setBusy(true); setNote(null); setBody('');
    try { await onPublish(text, health); setNote({error: false, text: 'Update posted.'}); }
    catch (err) { setBody(text); setNote({error: true, text: `${(err as Error).message} Your text was kept; check Pulse before retrying to avoid a duplicate.`}); }
    finally { setBusy(false); }
  };

  const issueRef = (i: ActivityEvent['issue']) =>
    <button type="button" className="activity-issue" onClick={() => onOpenIssue(i)} title={i.title}><code>{i.identifier}</code> <span>{i.title}</span></button>;
  const stateName = (id: unknown) => { const s = typeof id === 'string' ? states.get(id) : undefined; return s ? <span className="activity-state"><StatusIcon state={s}/>{s.name}</span> : <em>unknown status</em>; };
  const person = (id: unknown) => typeof id === 'string' ? <b>{users.get(id) || 'a former member'}</b> : <b>nobody</b>;
  type Line = {key: string; node: React.ReactNode; plural: (n: number) => React.ReactNode};
  const lines = (e: ActivityEvent): Line[] => {
    const c = e.changes || {}, ref = issueRef(e.issue), many = (n: number) => <b>{n} issues</b>;
    switch (e.eventType) {
      case 'issueCreated': return [{key: 'created', node: <>created {ref}</>, plural: n => <>created {many(n)}</>}];
      case 'commentCreated': return [{key: 'comment', node: <>commented on {ref}</>, plural: n => <>commented on {many(n)}</>}];
      case 'commentUpdated': return [{key: 'commentEdit', node: <>edited a comment on {ref}</>, plural: n => <>edited comments on {many(n)}</>}];
      case 'issueArchived': return [{key: 'archived', node: <>archived {ref}</>, plural: n => <>archived {many(n)}</>}];
    }
    const out: Line[] = [];
    if (c.stateId) out.push({key: `state:${c.stateId.from}>${c.stateId.to}`, node: <>moved {ref} from {stateName(c.stateId.from)} to {stateName(c.stateId.to)}</>, plural: n => <>moved {many(n)} from {stateName(c.stateId.from)} to {stateName(c.stateId.to)}</>});
    if (c.assigneeId) out.push(c.assigneeId.to
      ? {key: `assign:${c.assigneeId.to}`, node: <>assigned {ref} to {person(c.assigneeId.to)}</>, plural: n => <>assigned {many(n)} to {person(c.assigneeId.to)}</>}
      : {key: 'unassign', node: <>unassigned {ref}</>, plural: n => <>unassigned {many(n)}</>});
    if (c.priority) out.push({key: `priority:${c.priority.to}`, node: <>set priority of {ref} to <b>{PRIORITY[Number(c.priority.to) || 0]}</b></>, plural: n => <>set priority of {many(n)} to <b>{PRIORITY[Number(c.priority.to) || 0]}</b></>});
    if (c.projectId) out.push(c.projectId.to === project.id
      ? {key: 'projectIn', node: <>added {ref} to this project</>, plural: n => <>added {many(n)} to this project</>}
      : {key: 'projectOut', node: <>moved {ref} out of this project</>, plural: n => <>moved {many(n)} out of this project</>});
    if (c.title) out.push({key: 'title', node: <>renamed {ref}</>, plural: n => <>renamed {many(n)}</>});
    if (c.description) out.push({key: 'description', node: <>updated the description of {ref}</>, plural: n => <>updated descriptions of {many(n)}</>});
    return out.length ? out : [{key: 'updated', node: <>updated {ref}</>, plural: n => <>updated {many(n)}</>}];
  };
  // Bursts (3+ same-kind changes by one person within 10 minutes, e.g. a bulk move) collapse into one expandable line.
  type Row = {kind: 'update'; x: Entry & {kind: 'update'}} | {kind: 'lines'; at: string; actor: string; name: string; key: string; items: {id: string; at: string; type: string; line: Line}[]};
  const BURST_MS = 10 * 60e3, BURST_MIN = 3;
  const rowsOf = (items: Entry[]): Row[] => {
    const out: Row[] = [];
    for (const x of items) {
      if (x.kind === 'update') { out.push({kind: 'update', x}); continue; }
      const actor = x.event.actor?.id || '';
      for (const [i, line] of lines(x.event).entries()) {
        const prev = out[out.length - 1];
        const item = {id: `${x.id}:${i}`, at: x.at, type: x.event.eventType, line};
        if (prev?.kind === 'lines' && prev.actor === actor && prev.key === line.key && Date.parse(prev.items[prev.items.length - 1].at) - Date.parse(x.at) <= BURST_MS) prev.items.push(item);
        else out.push({kind: 'lines', at: x.at, actor, name: x.event.actor?.name || users.get(actor) || 'Someone', key: line.key, items: [item]});
      }
    }
    // Short runs stay as individual lines.
    return out.flatMap(r => r.kind === 'lines' && r.items.length > 1 && r.items.length < BURST_MIN ? r.items.map(it => ({...r, at: it.at, items: [it]})) : [r]);
  };
  const isNew = (at: string) => Boolean(lastSeen && at > lastSeen) || undefined;

  let dividerShown = false;
  return <section className="project-reference-activity project-activity" aria-labelledby="project-activity-title">
    <header className="activity-head">
      <h2 id="project-activity-title">Activity{newCount > 0 && <span className="activity-new-count" aria-label={`${newCount} new since your last visit`}>{newCount} new</span>}</h2>
      <div className="activity-health" aria-label="Project health">
        {latest ? <>
          <span className={`activity-health-pill health-${healthSlug(latest.status)}`}>{latest.status || 'No health'}</span>
          <span className="activity-trend" aria-label={`Health of the last ${trend.length} updates: ${trend.map(u => u.status || 'none').join(', ')}`}>
            {trend.map(u => <i key={u.id} className={`health-${healthSlug(u.status)}`} title={`${u.status || 'No health'} · ${new Date(u.createdAt).toLocaleDateString()}`}/>)}
          </span>
          <span className={staleDays !== null && staleDays >= STALE_DAYS ? 'activity-stale' : 'muted'} data-stale={staleDays !== null && staleDays >= STALE_DAYS || undefined}>
            Last update <Time iso={latest.createdAt}/>{staleDays !== null && staleDays >= STALE_DAYS ? ' — time for a new one' : ''}
          </span>
        </> : updates.hasData ? <span className="activity-stale" data-stale>No updates yet</span> : null}
      </div>
    </header>

    <form className="activity-composer" onSubmit={e => { e.preventDefault(); void submit(); }} aria-label="Post a project update">
      <label className="sr-only" htmlFor="activity-body">Update</label>
      <textarea id="activity-body" ref={bodyRef} value={body} rows={3} placeholder="What changed, what's next, any risks?"
        onChange={e => { setBody(e.target.value); setNote(null); }}
        onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit(); } }}/>
      <div className="activity-composer-row">
        <fieldset className="activity-health-pick"><legend className="sr-only">Health</legend>
          {HEALTH.map(h => <label key={h} className={`health-${healthSlug(h)}`}><input type="radio" name="activity-health" value={h} checked={health === h} onChange={() => { pickedHealth.current = true; setHealth(h); }}/>{h[0].toUpperCase() + h.slice(1)}</label>)}
        </fieldset>
        <small className="muted">Ctrl/⌘ + Enter</small>
        <button className="primary" disabled={busy || !body.trim()}>{busy ? 'Posting…' : 'Post update'}</button>
      </div>
      {note && <p role={note.error ? 'alert' : 'status'} className={note.error ? 'error' : 'muted'}>{note.text}</p>}
    </form>

    <div className="activity-filters" role="group" aria-label="Filter activity">
      {FILTERS.map(f => <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{f}</button>)}
    </div>

    {errors.map((s, i) => <p key={i} role="alert" className="error">{s.paged.error} <button type="button" onClick={s.paged.retry}>Retry</button></p>)}
    {!ready && <p role="status" className="muted">Loading activity…</p>}
    {ready && !entries.length && !errors.length && <div className="activity-empty"><h3>{filter === 'All' ? 'No activity yet' : `No ${filter.toLowerCase()} yet`}</h3>
      <p>{filter === 'All' || filter === 'Updates' ? 'Post an update above to tell the team how the project is going.' : 'Changes to this project’s issues will appear here.'}</p>
      {filter !== 'All' && <button type="button" onClick={() => setFilter('All')}>Show all activity</button>}</div>}

    <ol className="activity-feed" aria-label="Project activity">
      {groups.map(g => <li key={g.day} className="activity-day"><h3>{g.label}</h3><ol>
        {rowsOf(g.items).map(r => {
          const at = r.kind === 'update' ? r.x.at : r.items[0].at;
          const divider = !dividerShown && lastSeen && newCount > 0 && at <= lastSeen ? (dividerShown = true) : false;
          const id = r.kind === 'update' ? r.x.id : r.items[0].id;
          return <React.Fragment key={id}>
            {divider && <li className="activity-divider" role="separator">Earlier — before your last visit</li>}
            {r.kind === 'update'
              ? <li className="activity-entry activity-update" data-new={isNew(at)} aria-busy={r.x.update.id.startsWith('optimistic:') || undefined}>
                  <div className="activity-meta"><span className={`activity-health-pill health-${healthSlug(r.x.update.status)}`}>{r.x.update.status || 'Update'}</span><b>{r.x.update.actor?.name || 'Member'}</b> posted an update{r.x.update.progress != null && <span className="muted"> · {Math.round(r.x.update.progress)}% done</span>} · <Time iso={at}/></div>
                  <Markdown text={r.x.update.body} density="block" />
                </li>
              : r.items.length === 1
                ? <li className="activity-entry activity-event" data-type={r.items[0].type} data-new={isNew(at)}>
                    <span><b>{r.name}</b> {r.items[0].line.node}</span> <Time iso={at}/>
                  </li>
                : <li className="activity-entry activity-event activity-burst" data-type={r.items[0].type} data-count={r.items.length} data-new={r.items.some(it => isNew(it.at)) || undefined}>
                    <details><summary><span><b>{r.name}</b> {r.items[0].line.plural(r.items.length)}</span> <Time iso={at}/></summary>
                      <ol>{r.items.map(it => <li key={it.id} className="activity-burst-item"><span>{it.line.node}</span> <Time iso={it.at}/></li>)}</ol></details>
                  </li>}
          </React.Fragment>;
        })}
      </ol></li>)}
    </ol>

    {ready && entries.length > 0 && (more
      ? <button type="button" className="activity-more" disabled={loadingMore} onClick={loadOlder}>{loadingMore ? 'Loading…' : 'Load older activity'}</button>
      : <p className="activity-end muted">You’ve reached the beginning of this project’s activity.</p>)}
  </section>;
}
