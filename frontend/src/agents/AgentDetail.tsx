/* Screen 2 — Agent detail (HOJA-602-A-16).
 *
 * The issue page's layout with the issue page's own pieces: `.lin-head` breadcrumb, the right-aligned
 * `.lin-toolbar`, then `.lin-detail` split into `.lin-detail-main` and the `.lin-props` sidebar. The
 * editable rows are the exported `PropDropdown` and the sidebar's own input shape, so a property row
 * here is the same height and has the same keyboard behaviour as an issue property.
 *
 * Everything on this page is a server fact (WP04 `agentMember`) except the two things the server
 * does not store yet: session rows and their events (WP06). Those sections say so in place instead of
 * showing the run queue's fixture rows under a real agent's name.
 *
 * The screen never edits its own copy of a value: it renders the policy the adapter returned, so what
 * you see is what the server stored. `updatePolicy` reports the audit row the server produced, or
 * `null` when it keeps none, and the notice under the form follows that instead of promising a trail.
 */

import {useCallback, useEffect, useMemo, useState} from 'react';
import {Copy} from 'lucide-react';
import {confirmDialog} from '../dialogs';
import {Avatar, IconBtn, Tip} from '../graph/shared';
import {PropDropdown} from '../graph/tasks';
import {getAgentsAdapter} from './adapter';
import {statusGlyph} from './AgentsPane';
import {sessionStatusName, agentStatus} from './types';
import type {AgentDetail, AgentEvent, AgentPolicy, AgentSession} from './types';

const EVENT_LABELS: Record<AgentEvent['kind'], string> = {
  thought: 'Thought', action: 'Action', elicitation: 'Needs input', response: 'Response', error: 'Error',
};

function relTime(iso?: string | null): string {
  if (!iso) return '';
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return '';
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function stamp(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

/* One editable sidebar row: the issue page's `.lin-prop` shape, saved on blur or Enter. Uncontrolled
 * inputs are re-keyed by the stored value, so a save redraws the field with what came back rather
 * than keeping the draft. */
function PolicyField({label, icon, value, placeholder, type = 'text', min, disabled, onSave}: {
  label: string; icon: React.ReactNode; value: string; placeholder?: string;
  type?: 'text' | 'number'; min?: number; disabled?: boolean; onSave: (next: string) => void;
}) {
  return (
    <div className="lin-prop">
      <span className="k">{icon}{label}</span>
      <span className="v">
        <input key={value} type={type} min={min} step={type === 'number' ? 1 : undefined}
          defaultValue={value} placeholder={placeholder} aria-label={label} disabled={disabled}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur(); } }}
          onBlur={e => { const next = e.target.value.trim(); if (next !== value.trim()) onSave(next); }}
          style={{width: '100%', minWidth: 0, border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', fontSize: 13}} />
      </span>
    </div>
  );
}

export function AgentDetailPage({agentKey, workspaceName, viewerName, onBack, onOpenQueue}: {
  /** `AgentMember.id`. */
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

  /* Returns the promise so a failed write can re-read the row and *then* state the refusal: the
   * other order lets the successful reload clear the error that explains what happened. */
  const load = useCallback(() => (
    getAgentsAdapter().getAgent(agentKey)
      .then(d => { setDetail(d); setError(d ? '' : `No agent member with the id ${agentKey} exists in this workspace.`); })
      .catch((e: Error) => { setDetail(null); setError(e.message); })
  ), [agentKey]);

  useEffect(() => { load(); }, [load]);

  const active = useMemo(() => detail?.sessions.find(s => s.status === 'active' || s.status === 'awaiting-input') || null, [detail]);
  const events = useMemo(() => [...(detail?.events || [])].sort((a, b) => a.at < b.at ? -1 : 1), [detail]);

  const applyPolicy = (policy: AgentPolicy, auditNotice: string) => {
    setDetail(d => d ? {...d, policy} : d);
    setNotice(auditNotice);
  };

  const savePolicy = async (patch: Partial<AgentPolicy>) => {
    setBusy('policy');
    setNotice('');
    setError('');
    try {
      const {policy, audit} = await getAgentsAdapter().updatePolicy(agentKey, patch, viewerName);
      applyPolicy(policy, audit
        ? `Saved. The server recorded ${audit.action} by ${audit.actor}.`
        : 'Saved. This server does not record an audit row for the change yet (WP05).');
      load();
    } catch (e) {
      const message = (e as Error).message;
      await load();
      setError(message);
    } finally {
      setBusy('');
    }
  };

  const stop = async (session: AgentSession) => {
    if (!(await confirmDialog(`Stop the session on ${session.issueIdentifier}?`))) return;
    setBusy('stop');
    try {
      const {session: next, audit} = await getAgentsAdapter().stopSession(session.id, viewerName);
      setDetail(d => d ? {...d, sessions: d.sessions.map(s => s.id === next.id ? next : s)} : d);
      setNotice(`Session stopped: ${audit.action}.`);
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
          ? <div className="lin-empty" role="alert" data-state="error">
              <h2>This agent could not be opened</h2>
              <p>{error}</p>
              <div style={{display: 'flex', gap: 8, justifyContent: 'center'}}>
                <button type="button" className="lin-btn" onClick={() => { setError(''); load(); }}>Retry</button>
                <button type="button" className="lin-btn" onClick={onBack}>← Agents</button>
              </div>
            </div>
          : <p className="lin-loading" role="status">Loading agent…</p>}
      </section>
    );
  }

  const {agent, policy, sessions} = detail;
  const status = agentStatus(agent.status);
  /* No run count: the server stores no session row yet (WP06), so "0 running" would be a claim
   * about the workspace, not a reading of it. Capacity is a stored column and is shown as one. */
  const caps = `${agent.capacity} ${agent.capacity === 1 ? 'slot' : 'slots'}`;

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
          <IconBtn label="Open run queue" title="Open run queue" onClick={() => onOpenQueue(agent.id)}>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <path d="M2 4h12M2 8h12M2 12h8" strokeLinecap="round" />
            </svg>
          </IconBtn>
        </div>
      </header>
      <div className="lin-toolbar" style={{justifyContent: 'flex-end'}}>
        <Tip label={copied ? 'Copied' : `Copy ${agent.key}`}>
          <span>
            <IconBtn label="Copy agent id" title="Copy agent id" onClick={() => {
              void navigator.clipboard.writeText(agent.id).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }).catch(() => setCopied(false));
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
            {status.name} · {agent.model || 'no model set'} · {caps} · run count not reported ·{' '}
            {agent.monthlyBudgetCredits === null ? 'workspace budget default' : `${agent.monthlyBudgetCredits} credits/month`} ·{' '}
            {agent.runnerPool || 'any runner pool'} · created {stamp(agent.createdAt)}
          </p>

          <section className="lin-activity" aria-label="Session activity">
            <header>
              <b>Session activity</b>
              <span className="grow" />
              {active
                ? <button type="button" className="lin-subscribe-btn" disabled={busy === 'stop'}
                    onClick={() => void stop(active)}>{busy === 'stop' ? 'Stopping…' : `Stop ${active.issueIdentifier}`}</button>
                /* Not "no session is running": the server reports no run state at all yet, and
                 * the difference matters to a reader deciding whether to delegate to this agent. */
                : <span className="lin-note">No run state is reported.</span>}
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
            {!events.length && (
              <p className="lin-note">
                This server stores no agent events yet: session traces arrive with WP06, so there is
                nothing here rather than a placeholder timeline.
              </p>
            )}
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
            {!sessions.length && (
              <p className="lin-note">
                No delegated run is stored for this agent. Delegation itself is server-side — the
                Delegate field on a task writes the delegate column — while run sessions are WP06.
                Open the <button type="button" className="lin-addrow" onClick={() => onOpenQueue(agent.id)}>run queue</button> to
                see the queue as it stands.
              </p>
            )}
          </section>

          {notice && <p className="lin-note" role="status" style={{marginTop: 12}}>{notice}</p>}
          {error && <p className="lin-error" role="alert">{error}</p>}
        </main>

        <aside className="lin-props" aria-label="Agent settings">
          <h3>Agent</h3>
          <PolicyField label="Name" icon={<span aria-hidden="true">◈</span>} value={policy.name}
            disabled={busy === 'policy'} onSave={name => void savePolicy({name})} />
          <PolicyField label="Display name" icon={<span aria-hidden="true">◍</span>} value={policy.displayName || ''}
            placeholder="Same as the name" disabled={busy === 'policy'}
            onSave={displayName => void savePolicy({displayName: displayName || null})} />
          <PolicyField label="Capacity" icon={<span aria-hidden="true">▦</span>} type="number" min={1}
            value={String(policy.capacity)} disabled={busy === 'policy'}
            onSave={next => { const n = Number(next); if (Number.isFinite(n) && n >= 1) void savePolicy({capacity: n}); }} />
          <PolicyField label="Budget" icon={<span aria-hidden="true">▮</span>} type="number" min={0}
            value={policy.monthlyBudgetCredits === null ? '' : String(policy.monthlyBudgetCredits)}
            placeholder="Workspace default" disabled={busy === 'policy'}
            onSave={next => void savePolicy({monthlyBudgetCredits: next === '' ? null : Number(next)})} />
          <PolicyField label="Model" icon={<span aria-hidden="true">◇</span>} value={policy.model || ''}
            placeholder="Not set" disabled={busy === 'policy'}
            onSave={next => void savePolicy({model: next || null})} />
          <PolicyField label="Runner pool" icon={<span aria-hidden="true">▤</span>} value={policy.runnerPool || ''}
            placeholder="Any pool" disabled={busy === 'policy'}
            onSave={next => void savePolicy({runnerPool: next || null})} />
          <PolicyField label="Timezone" icon={<span aria-hidden="true">◷</span>} value={policy.timezone || ''}
            placeholder="UTC" disabled={busy === 'policy'}
            onSave={next => void savePolicy({timezone: next || null})} />
          <PolicyField label="Hours" icon={<span aria-hidden="true">◔</span>} value={policy.workingHours || ''}
            placeholder='{"days":["mon","tue","wed","thu","fri"],"from":"08:00","to":"19:00"}'
            disabled={busy === 'policy'}
            onSave={next => void savePolicy({workingHours: next || null})} />
          <PropDropdown label="State" icon={<span aria-hidden="true">◎</span>}
            value={policy.archived ? 'Archived' : 'Active'}
            onPick={id => { if (id) void savePolicy({archived: id === 'archived'}); }}
            disabled={busy === 'policy'}
            options={[{id: 'active', selected: !policy.archived, label: 'Active'},
              {id: 'archived', selected: policy.archived, label: 'Archived'}]} />

          <div className="sec">Policy (WP12)</div>
          <p className="lin-note">
            Tools, scope, repos, secrets, network and approvers have no column on this server yet, so
            this page shows none of them rather than a placeholder policy. Their writes arrive with
            WP12; until then the only limits the server enforces are capacity, the monthly credit
            budget, working hours, the model and the runner pool above.
          </p>
        </aside>
      </div>
    </section>
  );
}
