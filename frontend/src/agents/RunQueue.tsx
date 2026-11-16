/* Screen 3 — Run queue (HOJA-602-A-16).
 *
 * An issue list of delegated tasks grouped by session status. The rows are the issue list's own
 * `IssueRow`: the identifier, title and priority are the issue's, and the only addition is the
 * session chip this surface passes through the row's `trailing` slot.
 *
 * The rows are real: they come from `workspaceAgentSessions` (slice 4, admin-gated, paged), and
 * `AgentSession.issue` is what supplies the identifier, title and priority the issue row draws. A
 * queued run shows the schedule check's `queueReason` inline, because "queued" alone does not tell
 * a reader whether to wait or to fix something.
 *
 * The row's leading glyph is the *session* status, not the issue status. On this screen the
 * question a reader asks of a row is "where is this run right now?", and the group header above
 * it answers the same question; the issue's own status is still one click away on the task.
 */

import {useCallback, useEffect, useMemo, useState} from 'react';
import {GraphStatusIcon} from '../graph/shared';
import {IssueRow, defaultDisplay, type IssueRowModel, type LinDisplay} from '../graph/tasks';
import {getAgentsAdapter} from './adapter';
import {AGENTS_TABS, AgentsListShell, SearchField} from './chrome';
import {SESSION_STATUSES, sessionStatusName} from './types';
import type {AgentSession, SessionStatus} from './types';

const SESSION_TYPE: Record<SessionStatus, {type: string; color: string}> = {
  queued: {type: 'backlog', color: '#e2a03f'},
  active: {type: 'started', color: '#5e6ad2'},
  'awaiting-input': {type: 'unstarted', color: '#c98a1b'},
  'awaiting-verification': {type: 'unstarted', color: '#2f9e6f'},
  done: {type: 'completed', color: '#95a2b3'},
  failed: {type: 'canceled', color: '#c0392b'},
};

export function sessionRow(s: AgentSession): IssueRowModel {
  const tone = SESSION_TYPE[s.status];
  return {
    id: s.issueId,
    identifier: s.issueIdentifier,
    title: s.issueTitle,
    priority: s.priority,
    state: {name: sessionStatusName(s.status), type: tone.type, color: tone.color},
    stateChangedAt: s.startedAt || null,
    createdAt: s.startedAt || null,
    updatedAt: s.endedAt || s.startedAt || null,
  };
}

export function RunQueue({workspaceName, agentKey, onOpenRun, onOpenAgent, tab, onTab}: {
  workspaceName: string;
  /** An `AgentMember.id`; omitted on the workspace-wide queue. */
  agentKey?: string;
  onOpenRun: (session: AgentSession) => void;
  onOpenAgent: (agentKey: string) => void;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<SessionStatus[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const display: LinDisplay = useMemo(() => ({...defaultDisplay, grouping: 'none', props: {...defaultDisplay.props, updated: false}}), []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    getAgentsAdapter().listSessions(agentKey ? {agentId: agentKey} : undefined)
      .then(page => {
        if (!live) return;
        setSessions(page.sessions);
        setCursor(page.endCursor);
        setHasMore(page.hasNextPage);
        setError('');
      })
      .catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [agentKey]);

  const loadMore = useCallback(async () => {
    if (!cursor || paging) return;
    setPaging(true);
    try {
      const page = await getAgentsAdapter().listSessions({agentId: agentKey, after: cursor});
      setSessions(current => [...current, ...page.sessions]);
      setCursor(page.endCursor);
      setHasMore(page.hasNextPage);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPaging(false);
    }
  }, [agentKey, cursor, paging]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sessions.filter(s => {
      if (needle && !`${s.issueIdentifier} ${s.issueTitle} ${s.agentName} ${s.reason || ''}`.toLowerCase().includes(needle)) return false;
      if (status.length && !status.includes(s.status)) return false;
      return true;
    });
  }, [sessions, q, status]);

  const groups = useMemo(() => SESSION_STATUSES
    .map(s => ({key: s.id, name: s.name, items: visible.filter(v => v.status === s.id)}))
    .filter(g => g.items.length), [visible]);

  const collapsedSet = new Set(collapsed);
  /* A filtered queue only holds one agent's runs, so its own rows name the agent: a route segment
   * is a UUID, and a breadcrumb full of UUIDs is not a breadcrumb. */
  const agentLabel = sessions[0]?.agentName || (agentKey ? agentKey.slice(0, 8) : '');

  return (
    <AgentsListShell
      ariaLabel={agentKey ? `${agentKey} run queue` : 'Run queue'}
      surface="run-queue"
      workspaceName={workspaceName}
      trail={agentKey ? `${agentLabel} · Run queue` : 'Run queue'}
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbar={<>
        <SearchField value={q} onChange={setQ} placeholder="Search the queue…" label="Search the run queue" />
        <span className="spacer" />
        {SESSION_STATUSES.map(s => {
          const on = status.includes(s.id);
          return (
            <button key={s.id} type="button" className="lin-chipbtn" aria-pressed={on}
              onClick={() => setStatus(cur => on ? cur.filter(x => x !== s.id) : [...cur, s.id])}>{s.name}</button>
          );
        })}
      </>}
      chips={status.length ? <>
        {status.map(s => (
          <span key={s} className="lin-fchip">{sessionStatusName(s)}
            <button type="button" aria-label={`Remove filter ${sessionStatusName(s)}`}
              onClick={() => setStatus(cur => cur.filter(x => x !== s))}>×</button>
          </span>
        ))}
        <button type="button" className="lin-addrow" onClick={() => setStatus([])}>Clear all</button>
      </> : null}
    >
      {loading && <p className="lin-loading" role="status">Loading the queue…</p>}
      {error && <p className="lin-error" role="alert">{error}</p>}
      {!loading && !error && (
        <div className="lin-list">
          {groups.map(g => {
            const isCollapsed = collapsedSet.has(g.key);
            const tone = SESSION_TYPE[g.key as SessionStatus];
            return (
              <section key={g.key} className="lin-group">
                <header className="lin-group-head">
                  <button type="button" className="chev" aria-expanded={!isCollapsed}
                    aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${g.name}`}
                    onClick={() => setCollapsed(c => c.includes(g.key) ? c.filter(x => x !== g.key) : [...c, g.key])}>
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"
                      style={{transform: isCollapsed ? 'rotate(-90deg)' : undefined}} aria-hidden="true">
                      <path d="M4 6.5 8 10.5 12 6.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                  <GraphStatusIcon state={{name: g.name, type: tone.type, color: tone.color}} size={13} />
                  <b>{g.name}</b><span className="count">{g.items.length}</span>
                  <span className="grow" />
                </header>
                {!isCollapsed && g.items.map(s => (
                  <IssueRow key={s.id} issue={sessionRow(s)} display={display} selected={false}
                    showCheckbox={false} onSelect={() => undefined} onOpen={() => onOpenRun(s)} childCount={0}
                    trailing={<>
                      <span className="lin-chip" data-session-chip={s.status}
                        title={`${s.agentName} · ${sessionStatusName(s.status)} · ${s.state}${s.reason ? ` · ${s.reason}` : ''}`}>
                        <i style={{background: SESSION_TYPE[s.status].color}} />{s.agentName}
                      </span>
                      {s.reason && <span className="hidden md:inline-block max-w-56 overflow-hidden text-ellipsis whitespace-nowrap align-middle lin-note">{s.reason}</span>}
                    </>} />
                ))}
              </section>
            );
          })}
          {!visible.length && (
            <div className="lin-empty" data-state="empty">
              <h2>{sessions.length ? 'No run matches these filters' : 'Nothing is queued'}</h2>
              <p>
                {sessions.length
                  ? 'The search box and the status chips narrow the runs loaded so far; clear them to see the whole queue.'
                  : 'Delegate a task to an agent and it appears here, grouped by where the run stands.'}
              </p>
            </div>
          )}
          <div className="lin-note" style={{display: 'flex', gap: 8, alignItems: 'center', padding: '8px 0'}}>
            <span>{sessions.length} run{sessions.length === 1 ? '' : 's'} loaded</span>
            {hasMore && (
              <button type="button" className="lin-btn" data-action="load-more" disabled={paging} onClick={() => void loadMore()}>
                {paging ? 'Loading…' : 'Load more runs'}
              </button>
            )}
          </div>
        </div>
      )}
      {agentKey && (
        <p className="lin-note" style={{padding: '0 20px 24px'}}>
          <button type="button" className="crumb-link" onClick={() => onOpenAgent(agentKey)}>‹ Back to {agentLabel}</button>
        </p>
      )}
      <p className="lin-note" style={{padding: '0 20px 24px'}}>
        A queue row opens that agent's queue. The run's own transcript is on the agent page, and the
        task page's inline session view is a later slice.
      </p>
    </AgentsListShell>
  );
}
