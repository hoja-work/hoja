/* Screens 4 and 5 — Runners list and Runner detail (HOJA-602-A-16, WP13 T059).
 *
 * Both screens are the issue list's and the issue page's own pieces:
 *
 *   - the list is `IssueRow` (graph/tasks.tsx), the way `RunQueue.tsx` uses it, inside the shared
 *     `.lin-*` shell — so the identifier column, the 42 px rhythm and the meta chips are the
 *     issue list's, not a lookalike;
 *   - the detail is the `.lin-detail` + `.lin-props` split with the exported `PropDropdown` for
 *     property rows, the shape `AgentDetail.tsx` already proves.
 *
 * What this screen must never do, and how that is guaranteed rather than remembered:
 *
 *   - the tailcat address is a bearer secret (runner/hoja_runnerd/connect.py:8-12). The adapter
 *     drops it in `publicExposure` before anything reaches here, and the geometry script asserts
 *     the canary seeded in the fixture is absent from the rendered HTML;
 *   - the lease token hash is a credential column (FR-018). `publicRunner` maps it to `enrolled`,
 *     so this file has no field to leak;
 *   - the org kill switch (`ConnectSupervisor.set_org_enabled`) is a state, not an empty list: a
 *     disabled workspace says so and offers no revoke.
 *
 * Fields come from the two written authorities and nothing else — `Exposure.public_row()` for an
 * exposure, the spec glossary ("Runner — org, name, labels, lease token hash, last seen") for a
 * runner. `app/scripts/check-runner-contract.py` compares the two mappings in `types.ts` with
 * those sources, so a field invented here fails there.
 */

import {useCallback, useEffect, useMemo, useState} from 'react';
import {Copy, Lock, LockOpen, SlidersHorizontal} from 'lucide-react';
import {confirmDialog} from '../dialogs';
import {GraphStatusIcon, IconBtn, LabelChip, StatusAgeIcon, Tip} from '../graph/shared';
import {DisplayPanel, IssueRow, PropDropdown, defaultDisplay, type IssueRowModel, type LinDisplay} from '../graph/tasks';
import {getAgentsAdapter} from './adapter';
import {AGENTS_TABS, AgentsListShell, SampleDataNotice, SearchField} from './chrome';
import {RUNNER_STATUSES, runnerStatus, ttlLabel, ttlSecondsLeft} from './types';
import type {ConnectExposure, Runner, RunnerStatus} from './types';

/* A runner row is an `IssueRowModel`: the runner's id is the identifier column, its name is the
 * title, online/offline is the status glyph and `lastSeenAt` is the age the glyph explains. The
 * labels ride the model's own `labels`, so they render through the issue row's `LabelChip`. */
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

/* The row's second 14 px column is the one an issue spends on priority. A runner has no
 * priority; it has an enrollment, which is what FR-018 makes scarce. Filling the column keeps the
 * identifier where the issue list puts it instead of shifting it 23 px left. */
function enrollmentGlyph(r: Runner) {
  const label = r.enrolled ? 'Enrolled: the one-time lease token was accepted' : 'Not enrolled: no lease token has been presented';
  return (
    <span className="lin-tip" data-tip={label} role="img" aria-label={label}>
      {r.enrolled ? <Lock size={14} aria-hidden="true" /> : <LockOpen size={14} aria-hidden="true" />}
    </span>
  );
}

const RUNNER_DISPLAY: LinDisplay = {
  ...defaultDisplay,
  grouping: 'status',
  ordering: 'updated',
  props: {...defaultDisplay.props,
    // An issue row's priority column is spent on enrollment here, and assignee/project/cycle/
    // dueDate/created have no runner counterpart.
    priority: false, assignee: false, project: false, cycle: false, dueDate: false, created: false},
};

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
  const [q, setQ] = useState('');
  const [grouping, setGrouping] = useState<RunnerGrouping>('status');
  const [labels, setLabels] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [connectOn, setConnectOn] = useState(true);

  useEffect(() => {
    let live = true;
    Promise.all([getAgentsAdapter().listRunners(), getAgentsAdapter().connectEnabled()])
      .then(([rows, enabled]) => { if (live) { setRunners(rows); setConnectOn(enabled); setError(''); } })
      .catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  const allLabels = useMemo(() => [...new Set(runners.flatMap(r => r.labels))].sort(), [runners]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return runners.filter(r => {
      if (needle && !`${r.id} ${r.name} ${r.labels.join(' ')}`.toLowerCase().includes(needle)) return false;
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
      {loading && <p className="lin-loading" role="status">Loading runners…</p>}
      {error && <p className="lin-error" role="alert">{error}</p>}
      {!loading && !error && (
        <div className="lin-list">
          <SampleDataNotice surface="runners" />
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
                    leading={enrollmentGlyph(r)} />
                ))}
              </section>
            );
          })}
          {!visible.length && (
            <div className="lin-empty">
              <h2>No runners match</h2>
              <p>A runner appears here once <code>hoja-runnerd</code> has presented its one-time lease token.</p>
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
 * from AgentDetail (which owns a different vocabulary). */
function lastSeen(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
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
  const [exposures, setExposures] = useState<ConnectExposure[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [copied, setCopied] = useState(false);
  const [connectOn, setConnectOn] = useState(true);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    getAgentsAdapter().getRunner(runnerId)
      .then(async d => {
        const enabled = await getAgentsAdapter().connectEnabled();
        setConnectOn(enabled);
        setRunner(d ? d.runner : null);
        setExposures(d ? d.exposures : []);
        setError(d ? '' : `Runner ${runnerId} was not found.`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runnerId]);

  useEffect(() => { load(); }, [load]);

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
      const {audit} = await getAgentsAdapter().revokeExposure(exposure.id, viewerName);
      setExposures(cur => cur.filter(e => e.id !== exposure.id));
      setNotice(`Revoked and audited: ${audit.action} on ${audit.target}.`);
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
        <p className="lin-error" role="alert">{error || `Runner ${runnerId} was not found.`}{' '}
          <button type="button" onClick={() => { setError(''); load(); }}>Retry</button></p>
      </section>
    );
  }

  const tone = runnerStatus(runner.status);

  return (
    <section className="lin-page" data-surface="runner-detail" data-connect={connectOn ? 'on' : 'off'}
      aria-label={`${runner.id}: ${runner.name}`}>
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
            {tone.name} · last seen {lastSeen(runner.lastSeenAt)} · {runner.enrolled ? 'enrolled' : 'not enrolled'}
            {' · '}{exposures.length} live {exposures.length === 1 ? 'exposure' : 'exposures'}
            {runner.labels.length ? ` · ${runner.labels.join(', ')}` : ''}
          </p>

          <SampleDataNotice surface="runners" />
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
            {/* `data-surface` on the wrapper is what the geometry probe measures: these rows are
                `.lin-row`s, the same 42 px rhythm as an issue. */}
            <div data-exposures>
              {exposures.map(e => (
                <ExposureRow key={e.id} exposure={e} connectOn={connectOn} busy={busy === e.id} onRevoke={revoke} now={now} />
              ))}
            </div>
            {!exposures.length && connectOn && (
              <p className="lin-note">This runner holds no live exposure. One opens per leased job that asks for a port.</p>
            )}
          </section>

          {notice && <p className="lin-note" role="status" style={{marginTop: 12}}>{notice}</p>}
          {error && <p className="lin-error" role="alert">{error}</p>}
        </main>

        <aside className="lin-props" aria-label="Runner details">
          <h3>Runner</h3>
          <PropDropdown label="Enrollment" icon={<Lock size={13} aria-hidden="true" />}
            value={runner.enrolled ? 'Enrolled' : 'Not enrolled'}
            disabled
            onPick={() => undefined}
            options={[{id: 'enrolled', selected: runner.enrolled, label: 'Enrolled'},
              {id: 'none', selected: !runner.enrolled, label: 'Not enrolled'}]} />
          <div className="lin-prop">
            <span className="k">◷ Last seen</span>
            <span className="v">{lastSeen(runner.lastSeenAt)}</span>
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
