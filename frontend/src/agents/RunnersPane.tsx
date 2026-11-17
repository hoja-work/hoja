/* Screens 4 and 5 — Runners list and Runner detail (HOJA-602-A-16, WP13).
 *
 * Both screens are the issue list's and the issue page's own pieces:
 *
 *   - the list is `IssueRow` (graph/tasks.tsx), the way `RunQueue.tsx` uses it, inside the shared
 *     `.lin-*` shell — so the identifier column, the 42 px rhythm and the meta chips are the
 *     issue list's, not a lookalike;
 *   - the detail is the `.lin-detail` + `.lin-props` split with the exported `PropDropdown` for
 *     property rows, the shape `AgentDetail.tsx` already proves.
 *
 * Since slice 5 the runners and their leases are server rows: `runners`, `runner(id)` and
 * `agentLeases` in app/hoja_server/agent_runners.py, all admin-gated and org-scoped. The current
 * lease rides the row (the issue row's `trailing` slot, exactly as the run queue's session chip
 * does) because "what is this machine holding?" is the question the list answers.
 *
 * Since HOJA-635 the list also *writes*: the toolbar's "Enroll runner" action mints a single-use
 * enrollment token (`runnerEnrollmentCreate`) and shows it once, together with the exact
 * `hoja-runnerd --enroll` command, because that response is the only place the plaintext exists —
 * the server keeps a sha256 and cannot hand it back. Withdrawing an unspent token
 * (`runnerEnrollmentRevoke`) and disabling an enrolled machine (`runnerRevoke`, on the detail
 * page) are the two ways back. None of those controls is a `.lin-row`: the list's row count must
 * stay equal to the server's runner count, which is what the geometry check asserts.
 *
 * Since slice 6 the Connect block is a server read too: `connect_endpoint` (revision 71) records
 * every published port the runner reports on `/runner/connect/open`, `connectEndpoints` serves the
 * live rows for this machine, and `connectEndpointClose` withdraws one (audited, and delivered to
 * the runner on its next heartbeat). Two secrets matter and both are structurally absent: the
 * tailcat address is a bearer secret (runner/hoja_runnerd/connect.py:8-12) that never reaches the
 * database, so it is not in the SDL and cannot be selected; and the lease token hash is a
 * credential column (FR-018) the server's SDL does not carry at all.
 */

import {useCallback, useEffect, useMemo, useState} from 'react';
import {Copy, Lock, LockOpen, Plus, SlidersHorizontal} from 'lucide-react';
import {confirmDialog} from '../dialogs';
import {GraphStatusIcon, IconBtn, LabelChip, Tip} from '../graph/shared';
import {DisplayPanel, IssueRow, PropDropdown, defaultDisplay, type IssueRowModel, type LinDisplay} from '../graph/tasks';
import {getAgentsAdapter} from './adapter';
import {AGENTS_TABS, AgentsListShell, SearchField} from './chrome';
import {RUNNER_STATUSES, runnerStatus, ttlLabel, ttlSecondsLeft} from './types';
import type {
  ConnectExposure, Runner, RunnerEnrollment, RunnerEnrollmentGrant, RunnerLease, RunnerStatus,
} from './types';

/* A runner row is an `IssueRowModel`: the runner's id is the identifier column, its name is the
 * title, online/offline is the status glyph and `lastSeenAt` is the age the glyph explains. A
 * runner that has not heartbeated yet has no age to explain, so the glyph shows no time rather
 * than a fabricated one. The labels ride the model's own `labels`, so they render through the
 * issue row's `LabelChip`. */
export function runnerRow(r: Runner): IssueRowModel {
  const tone = runnerStatus(r.status);
  return {
    id: r.id,
    identifier: r.id,
    title: r.name,
    state: {name: tone.name, type: tone.type, color: tone.color},
    stateChangedAt: r.lastSeenAt,
    updatedAt: r.lastSeenAt,
    labels: {nodes: r.labels.map(name => ({id: name, name}))},
  };
}

/* The row's second 14 px column is the one an issue spends on priority. A runner has no priority;
 * it has a credential, and whether that credential still authenticates is what FR-018 makes
 * scarce. Filling the column keeps the identifier where the issue list puts it instead of
 * shifting it 23 px left. */
function credentialGlyph(r: Runner) {
  const label = r.revoked
    ? `Revoked${r.revokedAt ? ` ${r.revokedAt.slice(0, 10)}` : ''}: this runner's token no longer authenticates`
    : r.enabled
      ? 'Enrolled: a one-time lease token was presented and accepted'
      : 'Disabled: this runner is refused at the lease route';
  return (
    <span className="lin-tip" data-tip={label} role="img" aria-label={label} data-credential={r.revoked ? 'revoked' : 'enrolled'}>
      {r.revoked || !r.enabled ? <LockOpen size={14} aria-hidden="true" /> : <Lock size={14} aria-hidden="true" />}
    </span>
  );
}

/* The lease a runner holds, as the row's trailing chip: the task it took and the harness it runs.
 * A runner holding nothing says nothing — an empty chip would be a second status column. */
function leaseChip(r: Runner) {
  if (!r.lease) return null;
  const task = r.lease.taskIdentifier || r.lease.taskId.slice(0, 8);
  return (
    <span className="lin-chip" data-lease={r.lease.id} data-lease-state={r.lease.state}
      title={`Holding ${task} · ${r.lease.harness} · fence ${r.lease.fence}${r.lease.leaseUntil ? ` · until ${r.lease.leaseUntil}` : ''}`}>
      <i style={{background: '#5e6ad2'}} />{task} · {r.lease.harness}
    </span>
  );
}

/* A labelled action inside `.lin-toolbar`. The toolbar's height is its content — a 28 px
 * `.lin-icon-btn` inside 8 px of padding — and both the issue list and the issue page are measured
 * against it, so a `.lin-btn` at its default ~34 px would break that parity on this screen alone.
 * Pinning the height keeps the shared `.lin-btn` styling and the shared toolbar geometry. */
const TOOLBAR_ACTION = {height: 28, padding: '0 12px'} as const;

const RUNNER_DISPLAY: LinDisplay = {
  ...defaultDisplay,
  grouping: 'status',
  ordering: 'updated',
  props: {...defaultDisplay.props,
    // An issue row's priority column is spent on the credential here, and assignee/project/cycle/
    // dueDate/created have no runner counterpart.
    priority: false, assignee: false, project: false, cycle: false, dueDate: false, created: false},
};

/* Enroll (HOJA-635) — the one control on this screen that hands out a credential.
 *
 * Three states, and the middle one is the whole point:
 *
 *   form   → name + lifetime, then `runnerEnrollmentCreate`
 *   token  → the plaintext, shown once, with the exact command that spends it
 *   list   → every token in the workspace and whether it can still be used
 *
 * The plaintext lives in this component's state and nowhere else: it is not in a URL, not in
 * localStorage and not refetched — the server stores only its sha256, so there is nothing to
 * refetch. Closing the panel drops it, and the block says so instead of letting someone assume
 * they can come back for it.
 */
function EnrollPanel({onClose}: {onClose: () => void}) {
  const [name, setName] = useState('');
  const [ttl, setTtl] = useState('1440');
  const [grant, setGrant] = useState<RunnerEnrollmentGrant | null>(null);
  const [enrollments, setEnrollments] = useState<RunnerEnrollment[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');

  const refresh = useCallback(() => {
    getAgentsAdapter().listRunnerEnrollments()
      .then(setEnrollments)
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const copy = (what: string, value: string) => {
    void navigator.clipboard.writeText(value)
      .then(() => { setCopied(what); window.setTimeout(() => setCopied(''), 1500); })
      .catch(() => setCopied(''));
  };

  const create = async () => {
    const label = name.trim();
    if (!label || busy) return;
    setBusy('create');
    setError('');
    try {
      const minutes = ttl.trim() === '' ? undefined : Number(ttl);
      const created = await getAgentsAdapter().createRunnerEnrollment(
        label, Number.isFinite(minutes) ? minutes : undefined);
      setGrant(created);
      setName('');
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const revoke = async (enrollment: RunnerEnrollment) => {
    if (!(await confirmDialog(
      `Withdraw the enrollment token "${enrollment.label || enrollment.id}"? A machine that has not `
      + 'used it yet will be refused at /runner/enroll, and the token cannot be restored.',
      {danger: true}))) return;
    setBusy(enrollment.id);
    setError('');
    try {
      await getAgentsAdapter().revokeRunnerEnrollment(enrollment.id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  return (
    <section className="lin-note" data-enroll-panel aria-label="Enroll a runner"
      style={{display: 'block', padding: '14px 20px', borderBottom: '1px solid var(--line)'}}>
      {!grant && (
        <div style={{display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center'}}>
          <b>Enroll a runner</b>
          <input aria-label="Runner name" placeholder="Machine name, e.g. mac-mini-01"
            value={name} onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void create(); }}
            style={{minWidth: 240, padding: '6px 10px', borderRadius: 7,
              border: '1px solid var(--line)', background: 'var(--panel)', color: 'var(--text)'}} />
          <label style={{display: 'inline-flex', alignItems: 'center', gap: 6}}>
            Expires in
            <input aria-label="Token lifetime in minutes" value={ttl} inputMode="numeric"
              onChange={e => setTtl(e.target.value)} style={{width: 80, padding: '6px 8px',
                borderRadius: 7, border: '1px solid var(--line)', background: 'var(--panel)',
                color: 'var(--text)'}} />
            min
          </label>
          <button type="button" className="lin-btn primary" disabled={!name.trim() || busy === 'create'}
            onClick={() => void create()}>
            {busy === 'create' ? 'Minting…' : 'Create token'}
          </button>
          <button type="button" className="lin-btn" onClick={onClose}>Close</button>
        </div>
      )}

      {grant && (
        <div data-enroll-token style={{display: 'block'}}>
          <p style={{margin: '0 0 8px'}}>
            <b>Copy this now.</b> Hoja keeps only a hash of the token, so this is the one and only
            time it can be shown — closing this panel discards it for good.
          </p>
          <div style={{display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8}}>
            <code data-enroll-secret style={{fontFamily: 'var(--mono, monospace)', fontSize: 12,
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--line)',
              background: 'var(--subtle)', wordBreak: 'break-all', maxWidth: '100%'}}>
              {grant.token}
            </code>
            <button type="button" className="lin-btn" onClick={() => copy('token', grant.token)}>
              {copied === 'token' ? 'Copied' : 'Copy token'}
            </button>
          </div>
          <pre data-enroll-command style={{margin: '0 0 8px', padding: '10px 12px', borderRadius: 8,
            border: '1px solid var(--line)', background: 'var(--subtle)', overflowX: 'auto',
            fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all'}}>{grant.command}</pre>
          <div style={{display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap'}}>
            <button type="button" className="lin-btn" onClick={() => copy('command', grant.command)}>
              {copied === 'command' ? 'Copied' : 'Copy command'}
            </button>
            <span className="lin-note">
              The daemon heartbeats every {grant.heartbeatIntervalS} s and the token works once.
            </span>
            <span className="grow" />
            <button type="button" className="lin-btn"
              onClick={() => { setGrant(null); onClose(); }}>Done</button>
          </div>
        </div>
      )}

      {error && <p className="lin-error" role="alert" style={{margin: '8px 0 0'}}>{error}</p>}

      {enrollments.length > 0 && (
        <div data-enroll-list style={{marginTop: 12}}>
          {enrollments.map(enrollment => (
            <div key={enrollment.id} className="lin-event" data-enrollment={enrollment.id}
              data-enrollment-status={enrollment.status}>
              <span className="lin-chip" title={`status: ${enrollment.status}`}>
                {enrollment.status}
              </span>
              <span>
                <b>{enrollment.label || 'unnamed'}</b>
                {' · '}{enrollmentExpiry(enrollment)}
                {enrollment.usedByRunner ? ` · used by ${enrollment.usedByRunner.slice(0, 8)}` : ''}
              </span>
              <span style={{marginLeft: 'auto', display: 'inline-flex', gap: 8, alignItems: 'center'}}>
                {enrollment.usable
                  ? <button type="button" className="lin-btn" disabled={busy === enrollment.id}
                      onClick={() => void revoke(enrollment)}>
                      {busy === enrollment.id ? 'Withdrawing…' : 'Withdraw'}
                    </button>
                  : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* When a token stops working, said in the list's own vocabulary: an age for one that already
 * expired, a deadline for one that still works, and "used" for one that did its job. */
function enrollmentExpiry(enrollment: RunnerEnrollment): string {
  if (enrollment.status === 'spent') return enrollment.usedAt ? `used ${lastSeen(enrollment.usedAt)}` : 'used';
  if (!enrollment.expiresAt) return 'never expires';
  if (enrollment.status === 'expired') return `expired ${lastSeen(enrollment.expiresAt)}`;
  const ms = Date.parse(enrollment.expiresAt) - Date.now();
  if (Number.isNaN(ms)) return enrollment.expiresAt;
  const m = Math.round(ms / 60000);
  if (m <= 0) return 'expires now';
  if (m < 60) return `expires in ${m}m`;
  return `expires in ${Math.round(m / 60)}h`;
}

type RunnerGrouping = 'status' | 'labels' | 'none';

export function RunnersPane({workspaceName, onOpen, tab, onTab}: {
  workspaceName: string;
  onOpen: (runner: Runner) => void;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [runners, setRunners] = useState<Runner[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [q, setQ] = useState('');
  const [grouping, setGrouping] = useState<RunnerGrouping>('status');
  const [labels, setLabels] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [connectOn, setConnectOn] = useState(true);
  /* Closed by default: the panel is the only thing on this screen that can mint a credential, and
     a screen that opens with a secret-producing form is a screen that invites a stray click. */
  const [enrollOpen, setEnrollOpen] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([getAgentsAdapter().listRunners(), getAgentsAdapter().connectEnabled()])
      .then(([page, enabled]) => {
        if (!live) return;
        setRunners(page.runners);
        setCursor(page.endCursor);
        setHasMore(page.hasNextPage);
        setConnectOn(enabled);
        setError('');
      })
      .catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor || paging) return;
    setPaging(true);
    try {
      const page = await getAgentsAdapter().listRunners({after: cursor});
      setRunners(current => [...current, ...page.runners]);
      setCursor(page.endCursor);
      setHasMore(page.hasNextPage);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPaging(false);
    }
  }, [cursor, paging]);

  const allLabels = useMemo(() => [...new Set(runners.flatMap(r => r.labels))].sort(), [runners]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return runners.filter(r => {
      if (needle && !`${r.id} ${r.name} ${r.labels.join(' ')} ${r.harnesses.join(' ')}`.toLowerCase().includes(needle)) return false;
      if (labels.length && !labels.some(l => r.labels.includes(l))) return false;
      return true;
    });
  }, [runners, q, labels]);

  const groups = useMemo(() => {
    if (grouping === 'none') return [{key: 'all', name: '', status: undefined as RunnerStatus | undefined, items: visible}];
    const map = new Map<string, {key: string; name: string; status?: RunnerStatus; items: Runner[]}>();
    for (const r of visible) {
      const key = grouping === 'status' ? r.status : (r.labels.join(' · ') || 'unlabelled');
      if (!map.has(key)) map.set(key, {
        key,
        name: grouping === 'status' ? runnerStatus(r.status).name : key,
        status: grouping === 'status' ? r.status : undefined,
        items: [],
      });
      map.get(key)!.items.push(r);
    }
    const out = [...map.values()];
    if (grouping === 'status') {
      const order = new Map<string, number>(RUNNER_STATUSES.map((s, i) => [s.id as string, i]));
      out.sort((a, b) => (order.get(a.key) ?? 999) - (order.get(b.key) ?? 999));
    } else out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }, [visible, grouping]);

  const collapsedSet = new Set(collapsed);

  return (
    <AgentsListShell
      ariaLabel="Runners"
      surface="runners-list"
      workspaceName={workspaceName}
      trail="Runners"
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbar={<>
        <SearchField value={q} onChange={setQ} placeholder="Search runners…" label="Search runners" />
        <span className="spacer" />
        {allLabels.map(l => {
          const on = labels.includes(l);
          return (
            <button key={l} type="button" className="lin-chipbtn" aria-pressed={on}
              onClick={() => setLabels(cur => on ? cur.filter(x => x !== l) : [...cur, l])}>{l}</button>
          );
        })}
        {/* HOJA-635: the toolbar, not a row — a runner row is one server runner, and this
            action is not a machine. */}
        <button type="button" className="lin-btn" data-action="enroll-runner"
          aria-expanded={enrollOpen} onClick={() => setEnrollOpen(v => !v)}
          style={TOOLBAR_ACTION}>
          <Plus size={14} aria-hidden="true" /> Enroll runner
        </button>
        <span style={{position: 'relative'}}>
          <IconBtn label="Display options" title="Display options" active={displayOpen}
            onClick={() => setDisplayOpen(v => !v)}>
            <SlidersHorizontal size={15} />
          </IconBtn>
          {displayOpen && <DisplayPanel
            display={{...RUNNER_DISPLAY, grouping}}
            setDisplay={d => { if (d.grouping) setGrouping(d.grouping as RunnerGrouping); }}
            onClose={() => setDisplayOpen(false)}
            groupingOptions={[['none', 'No grouping'], ['status', 'Online or offline'], ['labels', 'Label']]}
            groupingLabel="Group runners by"
            entityToggles={false}
            propertyRows={[]} />}
        </span>
      </>}
      chips={labels.length ? <>
        {labels.map(l => (
          <span key={l} className="lin-fchip">{l}
            <button type="button" aria-label={`Remove filter ${l}`} onClick={() => setLabels(cur => cur.filter(x => x !== l))}>×</button>
          </span>
        ))}
        <button type="button" className="lin-addrow" onClick={() => setLabels([])}>Clear all</button>
      </> : null}
    >
      {/* Minting a token enrolls no machine, so nothing about the list below changes and no
          refetch is needed: the panel owns its own read of `runnerEnrollments`. */}
      {enrollOpen && <EnrollPanel onClose={() => setEnrollOpen(false)} />}
      {loading && <p className="lin-loading" role="status">Loading runners…</p>}
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
                    {g.status && <GraphStatusIcon state={{name: runnerStatus(g.status).name, type: runnerStatus(g.status).type, color: runnerStatus(g.status).color}} size={13} />}
                    <b>{g.name}</b><span className="count">{g.items.length}</span>
                    <span className="grow" />
                  </header>
                )}
                {!isCollapsed && g.items.map(r => (
                  <IssueRow key={r.id} issue={runnerRow(r)} display={RUNNER_DISPLAY} selected={false}
                    showCheckbox={false} onSelect={() => undefined} onOpen={() => onOpen(r)} childCount={0}
                    leading={credentialGlyph(r)} trailing={leaseChip(r)} />
                ))}
              </section>
            );
          })}
          {!visible.length && (
            <div className="lin-empty" data-state="empty">
              <h2>{runners.length ? 'No runner matches these filters' : 'No machine is enrolled'}</h2>
              <p>
                {runners.length
                  ? 'The search box and the label chips narrow the runners loaded so far; clear them to see them all.'
                  : 'A runner appears here once hoja-runnerd has presented its one-time lease token.'}
              </p>
            </div>
          )}
          {(hasMore || runners.length > 0) && (
            <div className="lin-note" style={{display: 'flex', gap: 8, alignItems: 'center', padding: '8px 0'}}>
              <span>{runners.length} machine{runners.length === 1 ? '' : 's'} loaded</span>
              {hasMore && (
                <button type="button" className="lin-btn" data-action="load-more" disabled={paging} onClick={() => void loadMore()}>
                  {paging ? 'Loading…' : 'Load more runners'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {!connectOn && (
        <p className="lin-note" role="status" style={{padding: '0 20px 24px'}}>
          Connect is turned off for this workspace. Runners still lease work; no tunnel can be opened until an admin turns it back on.
        </p>
      )}
    </AgentsListShell>
  );
}

/* Last seen, in the shape the row's age glyph uses. Kept local so the detail page does not import
 * from AgentDetail (which owns a different vocabulary). A machine that has never heartbeated says
 * so: `null` is "no heartbeat recorded", which is not the same sentence as "years ago". */
function lastSeen(iso: string | null): string {
  if (!iso) return 'no heartbeat yet';
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function leaseLine(lease: RunnerLease): string {
  const task = lease.taskIdentifier || lease.taskId.slice(0, 8);
  return `${task} · ${lease.harness} · fence ${lease.fence} · ${lease.state}`;
}

function ExposureRow({exposure, connectOn, busy, onRevoke, now}: {
  exposure: ConnectExposure; connectOn: boolean; busy: boolean; onRevoke: (e: ConnectExposure) => void; now: number;
}) {
  const left = ttlSecondsLeft(exposure.expiresAt, now);
  const tone = left > 300 ? {name: 'Live', type: 'started', color: '#2f9e6f'}
    : left > 0 ? {name: 'Expiring', type: 'unstarted', color: '#c98a1b'}
      : {name: 'Expired', type: 'completed', color: '#95a2b3'};
  return (
    <div className="lin-row" data-exposure={exposure.id}>
      <GraphStatusIcon state={tone} size={14} />
      {/* The second 14 px slot: an exposure has no priority, and the TTL column below carries the
          number that matters, so the slot stays a fixed spacer rather than a blank glyph. */}
      <span aria-hidden="true" style={{width: 14, height: 14, display: 'inline-block'}} />
      <span className="ident">{exposure.id}</span>
      <span className="title" title={`Ports ${exposure.ports.join(', ')}`}>
        {exposure.ports.length === 1 ? `port ${exposure.ports[0]}` : `ports ${exposure.ports.join(', ')}`}
      </span>
      <span className="meta">
        {/* The pinned peer is the whole ACL (`--allow=nodekey:…`), so it is shown shortened and
            in full on hover: it is an identifier, not a secret. */}
        <span className="hidden md:inline-flex">
          <span className="lin-chip" title={`Pinned peer ${exposure.pinnedPeer}`}>{exposure.pinnedPeer.slice(0, 15)}…</span>
        </span>
        <span className="date" title={`Expires in ${left}s`} data-ttl={left}>{ttlLabel(exposure.expiresAt, now)}</span>
        <button type="button" className="lin-btn" disabled={!connectOn || busy}
          title={connectOn ? 'Revoke this exposure' : 'Connect is disabled for this workspace'}
          onClick={() => onRevoke(exposure)}>{busy ? 'Revoking…' : 'Revoke'}</button>
      </span>
    </div>
  );
}

export function RunnerDetailPage({runnerId, workspaceName, viewerName, onBack}: {
  runnerId: string;
  workspaceName: string;
  viewerName: string;
  onBack: () => void;
}) {
  const [runner, setRunner] = useState<Runner | null>(null);
  const [leases, setLeases] = useState<RunnerLease[]>([]);
  const [exposures, setExposures] = useState<ConnectExposure[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [copied, setCopied] = useState(false);
  const [connectOn, setConnectOn] = useState(true);
  const [revoking, setRevoking] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    getAgentsAdapter().getRunner(runnerId)
      .then(async d => {
        const enabled = await getAgentsAdapter().connectEnabled();
        setConnectOn(enabled);
        setRunner(d ? d.runner : null);
        setExposures(d ? d.exposures : []);
        setError(d ? '' : `Runner ${runnerId} was not found.`);
        /* The machine's leases are a second read (`agentLeases`) because they are not part of the
         * runner row: they are the history of what it took, which is what its page answers. */
        setLeases(d ? await getAgentsAdapter().listLeases(runnerId) : []);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runnerId]);

  useEffect(() => { load(); }, [load]);

  /* Revoke the machine itself, which is a different act from revoking one of its tunnels: this
   * one turns the runner's bearer token off, so /runner/claim refuses it on its next poll. */
  /* Takes the row as an argument rather than closing over `runner`: this hook list runs before the
   * "not found" early return, so the component's `runner` is still nullable here, and the narrowed
   * value at the button is the one that exists. */
  const revokeRunner = async (target: Runner) => {
    if (!(await confirmDialog(
      `Revoke ${target.name}? Its lease token stops authenticating immediately, so it is refused at `
      + '/runner/claim and any lease it holds is lost on the next heartbeat. Work is re-queued; the '
      + 'machine is not deleted.', {danger: true}))) return;
    setRevoking(true);
    setNotice('');
    setError('');
    try {
      await getAgentsAdapter().revokeRunner(target.id);
      load();
      setNotice(`${target.name} revoked. Its token no longer authenticates at /runner/claim.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRevoking(false);
    }
  };

  /* A TTL that does not count down is a lie about a tunnel that will die on its own. */
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  /* Two steps: the shared `confirmDialog` (never `window.confirm`), then a state transition that
   * also writes the audit row. The row only disappears when the adapter says it is gone. */
  const revoke = async (exposure: ConnectExposure) => {
    if (!(await confirmDialog(`Revoke the Connect exposure ${exposure.id} on ${exposure.ports.join(', ')}? The tunnel is killed, not hidden.`, {danger: true}))) return;
    setBusy(exposure.id);
    setNotice('');
    try {
      await getAgentsAdapter().revokeExposure(exposure.id, viewerName);
      setExposures(cur => cur.filter(e => e.id !== exposure.id));
      /* The audit row is the server's, read back through `auditEvents`; this sentence names the
       * action instead of echoing a row the client cannot see. */
      setNotice(`Revoked and audited: connect.exposure.close on ${exposure.id}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  if (loading) {
    return (
      <section className="lin-page" data-surface="runner-detail" aria-label={`${runnerId}: runner`}>
        <header className="lin-head">
          <nav className="lin-crumb" aria-label="Breadcrumb">
            <button type="button" className="crumb-link" onClick={onBack}>← Runners</button>
            <span className="sep">›</span>
            <span className="current">{runnerId}</span>
          </nav>
        </header>
        <p className="lin-loading" role="status">Loading runner…</p>
      </section>
    );
  }

  if (!runner) {
    return (
      <section className="lin-page" data-surface="runner-detail" aria-label={`${runnerId}: runner`}>
        <header className="lin-head">
          <nav className="lin-crumb" aria-label="Breadcrumb">
            <button type="button" className="crumb-link" onClick={onBack}>← Runners</button>
            <span className="sep">›</span>
            <span className="current">{runnerId}</span>
          </nav>
        </header>
        <div className="lin-empty" role="alert" data-state="error">
          <h2>This machine could not be opened</h2>
          <p>{error || `Runner ${runnerId} was not found.`}</p>
          <button type="button" className="lin-btn" onClick={() => { setError(''); load(); }}>Retry</button>
        </div>
      </section>
    );
  }

  const tone = runnerStatus(runner.status);

  return (
    <section className="lin-page" data-surface="runner-detail" data-connect={connectOn ? 'on' : 'off'}
      data-runner-status={runner.status} aria-label={`${runner.id}: ${runner.name}`}>
      <header className="lin-head">
        <nav className="lin-crumb" aria-label="Breadcrumb">
          <button type="button" className="crumb-link" onClick={onBack}>← Runners</button>
          <span className="sep">›</span>
          <span>{workspaceName}</span>
          <span className="sep">›</span>
          <span className="crumb-id">{runner.id}</span>
          <span className="current">{runner.name}</span>
        </nav>
      </header>
      <div className="lin-toolbar" style={{justifyContent: 'flex-end'}}>
        {/* HOJA-635: the way back for a machine. Two steps, then a state transition the server
            owns (`runnerRevoke`) — the row only reads "Revoked" once the server says so. */}
        {!runner.revoked && runner.enabled && (
          <button type="button" className="lin-btn" data-action="revoke-runner"
            disabled={revoking} onClick={() => void revokeRunner(runner)} style={TOOLBAR_ACTION}>
            {revoking ? 'Revoking…' : 'Revoke runner'}
          </button>
        )}
        <Tip label={copied ? 'Copied' : `Copy ${runner.id}`}>
          <span>
            <IconBtn label="Copy runner id" title="Copy runner id" onClick={() => {
              void navigator.clipboard.writeText(runner.id)
                .then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); })
                .catch(() => setCopied(false));
            }}><Copy size={15} /></IconBtn>
          </span>
        </Tip>
      </div>

      <div className="lin-detail">
        <main className="lin-detail-main">
          <h1 className="lin-detail-title" style={{display: 'flex', alignItems: 'center', gap: 10}}>
            <GraphStatusIcon state={{name: tone.name, type: tone.type, color: tone.color}} size={16} /> {runner.name}
          </h1>
          <p className="lin-detail-desc">
            {tone.name} · last seen {lastSeen(runner.lastSeenAt)} ·{' '}
            {runner.revoked ? 'revoked' : runner.enabled ? 'enrolled' : 'disabled'}
            {runner.harnesses.length ? ` · ${runner.harnesses.join(', ')}` : ''}
            {runner.labels.length ? ` · ${runner.labels.join(', ')}` : ''}
          </p>

          <section className="lin-activity" aria-label="Leases">
            <header>
              <b>Leases</b>
              <span className="grow" />
              <span className="lin-note">{runner.lease ? `Holding ${leaseLine(runner.lease)}` : 'Holding no lease'}</span>
            </header>
            <div data-leases>
              {leases.map(lease => (
                <div key={lease.id} className="lin-event" data-lease-row={lease.id} data-lease-state={lease.state}>
                  <span className="lin-chip" title={`state: ${lease.state}`}>{lease.state}</span>
                  <span><b>{lease.taskIdentifier || lease.taskId.slice(0, 8)}</b> · {lease.harness} · fence {lease.fence}</span>
                  <time>{relTime(lease.grantedAt || lease.finishedAt)}</time>
                </div>
              ))}
            </div>
            {!leases.length && (
              <p className="lin-note">
                This machine has never been granted work. A lease appears here when it long-polls for
                one, and the task it took is on the row.
              </p>
            )}
          </section>

          <section className="lin-activity" aria-label="Connect exposures">
            <header>
              <b>Connect exposures</b>
              <span className="grow" />
              <span className="lin-note">{connectOn ? 'Each tunnel is one process, one ephemeral key.' : 'Connect is disabled for this workspace.'}</span>
            </header>
            {!connectOn && (
              <p className="lin-note" data-connect-notice="off">
                An admin turned Connect off for this workspace, so no exposure can be opened and none can be revoked. This is a switch, not an empty list.
              </p>
            )}
            {/* `data-exposures` on the wrapper is what the geometry probe looks for: these rows are
                `.lin-row`s, the same 42 px rhythm as an issue. */}
            <div data-exposures>
              {exposures.map(e => (
                <ExposureRow key={e.id} exposure={e} connectOn={connectOn} busy={busy === e.id} onRevoke={revoke} now={now} />
              ))}
            </div>
            {!exposures.length && connectOn && (
              <p className="lin-note">Nothing is exposed. A tunnel opens per leased job that asks for a port.</p>
            )}
          </section>

          {notice && <p className="lin-note" role="status" style={{marginTop: 12}}>{notice}</p>}
          {error && <p className="lin-error" role="alert">{error}</p>}
        </main>

        <aside className="lin-props" aria-label="Runner details">
          <h3>Runner</h3>
          <PropDropdown label="Credential" icon={<Lock size={13} aria-hidden="true" />}
            value={runner.revoked ? 'Revoked' : runner.enabled ? 'Enrolled' : 'Disabled'}
            disabled
            onPick={() => undefined}
            options={[{id: 'enrolled', selected: !runner.revoked && runner.enabled, label: 'Enrolled'},
              {id: 'revoked', selected: runner.revoked, label: 'Revoked'}]} />
          <div className="lin-prop">
            <span className="k">◷ Last seen</span>
            <span className="v">{lastSeen(runner.lastSeenAt)}</span>
          </div>
          <div className="lin-prop">
            <span className="k">▮ Current lease</span>
            <span className="v" data-current-lease={runner.lease ? runner.lease.id : ''}>
              {runner.lease ? leaseLine(runner.lease) : 'None'}
            </span>
          </div>
          <div className="sec">Harnesses</div>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6}}>
            {runner.harnesses.map(name => <LabelChip key={name} label={{id: name, name}} />)}
            {!runner.harnesses.length && <span className="lin-note">None declared</span>}
          </div>
          <div className="sec">Labels</div>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6}}>
            {runner.labels.map(name => <LabelChip key={name} label={{id: name, name}} />)}
            {!runner.labels.length && <span className="lin-note">None</span>}
          </div>
          <div className="sec">Connect</div>
          <div className="lin-prop">
            <span className="k">▮ Workspace</span>
            <span className="v" data-connect-state={connectOn ? 'on' : 'off'}>{connectOn ? 'Enabled' : 'Disabled by an admin'}</span>
          </div>
          <p className="lin-note" style={{marginTop: 16}}>
            A runner leases work outbound-only. Revoking an exposure kills the tunnel process, so its key
            and its address die with it; the revoke is written to the audit log with your name.
          </p>
        </aside>
      </div>
    </section>
  );
}

/* The detail page's relative time, for a lease: the same arithmetic the row's age glyph uses. */
function relTime(iso: string | null): string {
  if (!iso) return '';
  return lastSeen(iso);
}
