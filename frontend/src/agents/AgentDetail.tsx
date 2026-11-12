/* Screen 2 — Agent detail (HOJA-602-A-16).
 *
 * The issue page's layout with the issue page's own pieces: `.lin-head` breadcrumb, the
 * right-aligned `.lin-toolbar`, then `.lin-detail` split into `.lin-detail-main` and the
 * `.lin-props` sidebar. The policy controls are the exported `PropDropdown` from the issue
 * properties sidebar, so a policy row is the same height and has the same keyboard behaviour as
 * an issue property.
 *
 * Every policy write goes through the adapter, which is the only thing that writes an audit
 * row. The screen never edits its own copy of the policy: it renders whatever the adapter
 * returned, so what you see is what was recorded.
 */

import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Copy} from 'lucide-react';
import {confirmDialog} from '../dialogs';
import {Avatar, IconBtn, Tip} from '../graph/shared';
import {PropDropdown} from '../graph/tasks';
import {getAgentsAdapter} from './adapter';
import {money, statusGlyph} from './AgentsPane';
import {sessionStatusName, agentStatus} from './types';
import type {AgentDetail, AgentEvent, AgentPolicy, AgentSession} from './types';

const EVENT_LABELS: Record<AgentEvent['kind'], string> = {
  thought: 'Thought', action: 'Action', elicitation: 'Needs input', response: 'Response', error: 'Error',
};

function relTime(iso?: string): string {
  if (!iso) return '';
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return '';
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

/** A policy list (tools, repos, secrets, network, approvers) with the sidebar's chip styling. */
function ChipList({label, items, onChange, hint, secret}: {
  label: string; items: string[]; onChange: (next: string[]) => void; hint?: string; secret?: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  /* Focus after the field exists rather than with `autoFocus`, which would move a screen
   * reader's cursor the moment the sidebar renders. */
  useEffect(() => { if (adding) inputRef.current?.focus(); }, [adding]);
  const commit = () => {
    const value = draft.trim();
    setAdding(false);
    setDraft('');
    if (!value || items.includes(value)) return;
    onChange([...items, value]);
  };
  return (
    <>
      <div className="sec">{label}</div>
      {hint && <p className="lin-note" style={{margin: '0 0 6px'}}>{hint}</p>}
      <div style={{display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6}}>
        {items.map(item => (
          <span key={item} className="lin-chip" title={secret ? `${item} (value never leaves the server)` : item}>
            {item}
            <button type="button" className="lin-chip-x" aria-label={`Remove ${item} from ${label}`}
              onClick={() => onChange(items.filter(x => x !== item))}>×</button>
          </span>
        ))}
        {!items.length && <span className="lin-note">None</span>}
      </div>
      {adding ? (
        <span style={{display: 'flex', gap: 6, alignItems: 'center'}}>
          <input ref={inputRef} value={draft} aria-label={`Add to ${label}`}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } if (e.key === 'Escape') { setAdding(false); setDraft(''); } }}
            style={{flex: 1, minWidth: 0, border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', fontSize: 13}} />
          <button type="button" className="lin-btn" onClick={commit}>Add</button>
        </span>
      ) : (
        <button type="button" className="lin-addrow" onClick={() => setAdding(true)}>Add {label.toLowerCase()}</button>
      )}
    </>
  );
}

export function AgentDetailPage({agentKey, workspaceName, viewerName, onBack, onOpenQueue}: {
  agentKey: string;
  workspaceName: string;
  viewerName: string;
  onBack: () => void;
  onOpenQueue: (agentKey: string) => void;
}) {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    getAgentsAdapter().getAgent(agentKey)
      .then(d => { setDetail(d); setError(d ? '' : `Agent ${agentKey} was not found.`); })
      .catch((e: Error) => setError(e.message));
  }, [agentKey]);

  useEffect(() => { load(); }, [load]);

  const active = useMemo(() => detail?.sessions.find(s => s.status === 'active' || s.status === 'awaiting-input') || null, [detail]);
  const events = useMemo(() => [...(detail?.events || [])].sort((a, b) => a.at < b.at ? -1 : 1), [detail]);

  const savePolicy = async (patch: Partial<AgentPolicy>) => {
    setBusy('policy');
    setNotice('');
    try {
      const {policy, audit} = await getAgentsAdapter().updatePolicy(agentKey, patch, viewerName);
      setDetail(d => d ? {...d, policy, agent: {...d.agent, budgetLimitUsd: policy.budgetUsd}} : d);
      setNotice(`Saved and audited: ${audit.action} by ${audit.actor}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const stop = async (session: AgentSession) => {
    if (!(await confirmDialog(`Stop the session on ${session.issueIdentifier}?`))) return;
    setBusy('stop');
    try {
      const {session: next, audit} = await getAgentsAdapter().stopSession(session.id, viewerName);
      setDetail(d => d ? {
        ...d,
        sessions: d.sessions.map(s => s.id === next.id ? next : s),
        agent: {...d.agent, status: d.agent.running > 1 ? 'working' : 'idle', running: Math.max(0, d.agent.running - 1)},
      } : d);
      setNotice(`Session stopped and audited: ${audit.action}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  if (!detail) {
    return (
      <section className="lin-page" data-surface="agent-detail" aria-label={`${agentKey}: agent`}>
        <header className="lin-head">
          <nav className="lin-crumb" aria-label="Breadcrumb">
            <button type="button" className="crumb-link" onClick={onBack}>← Agents</button>
            <span className="sep">›</span>
            <span className="current">{agentKey}</span>
          </nav>
        </header>
        {error
          ? <p className="lin-error" role="alert">{error} <button type="button" onClick={() => { setError(''); load(); }}>Retry</button></p>
          : <p className="lin-loading" role="status">Loading agent…</p>}
      </section>
    );
  }

  const {agent, policy, sessions} = detail;
  const status = agentStatus(agent.status);
  const running = sessions.filter(s => s.status === 'active' || s.status === 'awaiting-input');

  return (
    <section className="lin-page" data-surface="agent-detail" aria-label={`${agent.key}: ${agent.name}`}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={onBack}>← Agents</button>
          <span className="sep">›</span>
          <span>{workspaceName}</span>
          <span className="sep">›</span>
          <span className="crumb-id">{agent.key}</span>
          <span className="current">{agent.name}</span>
        </nav>
        <div className="lin-head-actions">
          <IconBtn label="Open run queue" title="Open run queue" onClick={() => onOpenQueue(agent.key)}>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <path d="M2 4h12M2 8h12M2 12h8" strokeLinecap="round" />
            </svg>
          </IconBtn>
        </div>
      </header>
      <div className="lin-toolbar" style={{justifyContent: 'flex-end'}}>
        <Tip label={copied ? 'Copied' : `Copy ${agent.key}`}>
          <span>
            <IconBtn label="Copy agent key" title="Copy agent key" onClick={() => {
              void navigator.clipboard.writeText(agent.key).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }).catch(() => setCopied(false));
            }}><Copy size={15} /></IconBtn>
          </span>
        </Tip>
      </div>

      <div className="lin-detail">
        <main className="lin-detail-main">
          <h1 className="lin-detail-title" style={{display: 'flex', alignItems: 'center', gap: 10}}>
            {statusGlyph(agent.status)} {agent.name}
          </h1>
          <p className="lin-detail-desc">
            {status.name} · {agent.harness} · {agent.model} · {running.length}/{agent.capacity} running · {money(agent.budgetUsedUsd)} of {money(agent.budgetLimitUsd)} · {agent.runnerPool} · owner {agent.owner.name}
          </p>

          <section className="lin-activity" aria-label="Session activity">
            <header>
              <b>Session activity</b>
              <span className="grow" />
              {active
                ? <button type="button" className="lin-subscribe-btn" disabled={busy === 'stop'}
                    onClick={() => void stop(active)}>{busy === 'stop' ? 'Stopping…' : `Stop ${active.issueIdentifier}`}</button>
                : <span className="lin-note">No session is running.</span>}
            </header>
            {events.map(e => (
              <div key={e.id} className="lin-event" data-kind={e.kind}>
                <Avatar name={agent.name} size={18} />
                <span>
                  <b className={e.kind === 'error' ? 'text-red-600' : undefined}>{EVENT_LABELS[e.kind]}</b>
                  {e.tool ? <> <code>{e.tool}</code></> : null} — {e.text}
                </span>
                <time>{relTime(e.at)}</time>
              </div>
            ))}
            {!events.length && <p className="lin-note">This agent has not recorded an event yet.</p>}
          </section>

          <section className="lin-activity" aria-label="Sessions">
            <header><b>Sessions</b></header>
            {sessions.map(s => (
              <div key={s.id} className="lin-event" data-session-status={s.status}>
                <span className="lin-chip">{sessionStatusName(s.status)}</span>
                <span><b>{s.issueIdentifier}</b> {s.issueTitle}{s.reason ? <span className="lin-note"> — {s.reason}</span> : null}</span>
                <time>{relTime(s.endedAt || s.startedAt)}</time>
              </div>
            ))}
            {!sessions.length && <p className="lin-note">Nothing has been delegated to this agent yet.</p>}
          </section>

          {notice && <p className="lin-note" role="status" style={{marginTop: 12}}>{notice}</p>}
          {error && <p className="lin-error" role="alert">{error}</p>}
        </main>

        <aside className="lin-props" aria-label="Agent policy">
          <h3>Policy</h3>
          <PropDropdown label="Scope" icon={<span aria-hidden="true">◎</span>}
            value={policy.scope === 'repo' ? 'Repositories only' : 'Whole workspace'}
            onPick={id => { if (id) void savePolicy({scope: id as AgentPolicy['scope']}); }}
            disabled={busy === 'policy'}
            options={[{id: 'repo', selected: policy.scope === 'repo', label: 'Repositories only'},
              {id: 'workspace', selected: policy.scope === 'workspace', label: 'Whole workspace'}]} />
          <div className="lin-prop">
            <span className="k">▮ Budget</span>
            <span className="v">
              <input type="number" min={0} step={1} aria-label="Budget limit (USD)" defaultValue={policy.budgetUsd}
                disabled={busy === 'policy'}
                onBlur={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v !== policy.budgetUsd) void savePolicy({budgetUsd: v}); }}
                style={{width: 90, border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', fontSize: 13}} />
            </span>
          </div>
          <div className="lin-prop">
            <span className="k">◷ Hours</span>
            <span className="v">
              <input type="text" aria-label="Working hours" defaultValue={policy.workingHours}
                disabled={busy === 'policy'}
                onBlur={e => { const v = e.target.value.trim(); if (v && v !== policy.workingHours) void savePolicy({workingHours: v}); }}
                style={{width: '100%', minWidth: 0, border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', fontSize: 13}} />
            </span>
          </div>

          <ChipList label="Tools" items={policy.tools} onChange={tools => void savePolicy({tools})} />
          <ChipList label="Repos" items={policy.repos} onChange={repos => void savePolicy({repos})} />
          <ChipList label="Secrets" items={policy.secrets} hint="Names only — a value never reaches this page." secret
            onChange={secrets => void savePolicy({secrets})} />
          <ChipList label="Network" items={policy.network} onChange={network => void savePolicy({network})} />
          <ChipList label="Approvers" items={policy.approvers} onChange={approvers => void savePolicy({approvers})} />
          <p className="lin-note" style={{marginTop: 16}}>
            Every change on this page is written to the audit log with your name.
          </p>
        </aside>
      </div>
    </section>
  );
}
