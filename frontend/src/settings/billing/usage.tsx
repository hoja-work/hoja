/* Settings → Billing (HOJA-602-A-12, WP14).
 *
 * The usage ledger, in the issue list's layout: `usageSummary` rows are grouped by agent,
 * model or month and drawn as `.lin-group` / `.lin-row`, the same 36 px group header and
 * 42 px row rhythm a task list uses (WP13's AuditPane composes the same pieces). Nothing
 * about the geometry is restated here — the classes are `graph/styles.css`, and the
 * grouping control is the shared `DisplayPanel`, so a change to the list proportions
 * moves this screen with it instead of leaving it behind.
 *
 * Two things this screen states rather than hides:
 *
 *   - every money figure is a decimal STRING from the server and is printed as the server
 *     gave it; nothing here does arithmetic on a price, because the ledger's rule is that
 *     a float never touches money;
 *   - the ledger is admin-only. A refusal is the state of the screen, not an empty list:
 *     "you may not read this" and "nothing has been spent" are different facts and the
 *     screen must not confuse them.
 *
 * Credits, run-minutes and budget used are the three numbers the acceptance names, and
 * each is read from the ledger rather than inferred: credits are 1 per 1,000 processed
 * tokens, run-minutes are the run's minutes with managed compute floored at 15, and the
 * budget is WP12's policy ceiling against this month's credits for that agent.
 */
import {useEffect, useMemo, useState} from 'react';
import {SlidersHorizontal} from 'lucide-react';
import {Avatar, IconBtn, Ring} from '../../graph/shared';
import {DisplayPanel, defaultDisplay, type LinDisplay} from '../../graph/tasks';

type UsageRow = {
  key: string;
  label: string;
  agentUserId?: string | null;
  model?: string | null;
  month: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  costUsd: string;
  computeCostUsd: string;
  limitCredits?: number | null;
  budgetAction?: string | null;
};

type UsageSummary = {
  month: string;
  groupBy: string;
  credits: number;
  runMinutes: number;
  costUsd: string;
  computeCostUsd: string;
  totalUsd: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  events: number;
  priceCard: string;
  rows: UsageRow[];
};

const USAGE_QUERY = `query UsageSummary($month: String, $groupBy: String) {
  usageSummary(month: $month, groupBy: $groupBy) {
    month groupBy credits runMinutes costUsd computeCostUsd totalUsd
    inputTokens outputTokens cacheReadTokens events priceCard
    rows { key label agentUserId model month inputTokens outputTokens cacheReadTokens
           credits runMinutes runs costUsd computeCostUsd limitCredits budgetAction }
  }
}`;

async function gql<T>(query: string, variables: Record<string, unknown>): Promise<T> {
  const token = localStorage.getItem('hoja_token');
  const response = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json', ...(token && token !== 'session' ? {authorization: `Bearer ${token}`} : {})},
    body: JSON.stringify({query, variables}),
  });
  const body = await response.json();
  if (body.errors?.length) {
    const error = new Error(body.errors[0].message) as Error & {code?: string};
    error.code = body.errors[0].extensions?.code;
    throw error;
  }
  return body.data as T;
}

/** The month selector's value is the browser's own YYYY-MM, so there is no date parsing. */
function monthValue(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function monthLabel(month: string): string {
  const [year, number] = month.split('-').map(Number);
  if (!year || !number) return month;
  return new Date(Date.UTC(year, number - 1, 1))
    .toLocaleDateString(undefined, {month: 'long', year: 'numeric', timeZone: 'UTC'});
}

/** Dollars, as the server printed them: grouped for reading, never rounded by us. */
function usd(value: string): string {
  const [whole, fraction] = String(value ?? '0').split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return `$${grouped}${fraction ? `.${fraction}` : ''}`;
}

function tokens(value: number): string {
  return value.toLocaleString();
}

const USAGE_DISPLAY: LinDisplay = {...defaultDisplay, grouping: 'agent'};

type Grouping = 'agent' | 'model' | 'month';

function UsageRowLine({row, groupBy, show}: {row: UsageRow; groupBy: Grouping; show: {tokens: boolean; runs: boolean; cost: boolean}}) {
  const label = row.label || row.key;
  const spent = row.limitCredits != null ? Math.min(1, row.credits / Math.max(1, row.limitCredits)) : 0;
  return (
    <div className="lin-row" data-usage-row={row.key} data-grouping={groupBy}>
      {groupBy === 'agent'
        ? <Avatar name={label} size={16} title={label} />
        : <span className="lin-chip" aria-hidden="true">{groupBy === 'model' ? 'model' : 'month'}</span>}
      <span className="title" title={label}>{label}</span>
      <span className="meta">
        {row.limitCredits != null && (
          <span className="lin-chip" title={`${row.credits} of ${row.limitCredits} credits used this month`}>
            <Ring frac={spent} size={12} />
            {row.credits} / {row.limitCredits} credits
          </span>
        )}
        {row.limitCredits == null && <span className="lin-chip" title="Credits this month">{row.credits} credits</span>}
        <span className="lin-chip" title="Wall minutes this run held compute">{row.runMinutes} min</span>
        {show.cost && <span className="date" title={`tokens ${usd(row.costUsd)} · compute ${usd(row.computeCostUsd)}`}>{usd(row.costUsd)}</span>}
        {show.runs && <span className="date hidden sm:inline">{row.runs} {row.runs === 1 ? 'run' : 'runs'}</span>}
        {show.tokens && (
          <span className="date hidden md:inline" title="fresh input / output / cache read">
            {tokens(row.inputTokens)} in · {tokens(row.outputTokens)} out · {tokens(row.cacheReadTokens)} cached
          </span>
        )}
      </span>
    </div>
  );
}

export function BillingUsageSettings() {
  const [month, setMonth] = useState(monthValue());
  const [grouping, setGrouping] = useState<Grouping>('agent');
  const [display, setDisplay] = useState<LinDisplay>({...USAGE_DISPLAY});
  const [displayOpen, setDisplayOpen] = useState(false);
  const [show, setShow] = useState({tokens: true, runs: true, cost: true});
  const [query, setQuery] = useState('');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refused, setRefused] = useState(false);

  /* A new month or grouping is a new request: the grouping is the server's argument, not
   * a pass over whatever happens to be loaded, so a month with more grouped rows than one
   * page cannot silently disagree with its own total. */
  useEffect(() => {
    let live = true;
    setLoading(true);
    setRefused(false);
    gql<{usageSummary: UsageSummary}>(USAGE_QUERY, {month, groupBy: grouping})
      .then(data => {
        if (!live) return;
        setSummary(data.usageSummary);
        setError('');
      })
      .catch((e: Error & {code?: string}) => {
        if (!live) return;
        setSummary(null);
        setRefused(e.code === 'FORBIDDEN');
        setError(e.code === 'FORBIDDEN' ? '' : e.message);
      })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [month, grouping]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = summary?.rows ?? [];
    if (!needle) return rows;
    return rows.filter(row => `${row.label} ${row.key} ${row.model ?? ''}`.toLowerCase().includes(needle));
  }, [summary, query]);

  const showing = visible.reduce((total, row) => total + row.credits, 0);
  const filtered = (summary?.rows.length ?? 0) !== visible.length;

  return (
    <section className="settings-page" data-settings-pane="billing">
      <header className="settings-page-heading">
        <span className="eyebrow">Administration</span>
        <h1>Billing</h1>
        <p>
          What agents spent. One credit is 1,000 processed tokens — fresh input, cache reads and
          output — and money is computed from those tokens at a versioned price card, never from
          a cost the harness reported. Managed compute is charged per run-minute with a
          fifteen-minute floor; an agent on your own model key records its usage and costs nothing.
        </p>
      </header>

      <div className="settings-group">
        <span className="settings-section-kicker">This month</span>
        <h2>{monthLabel(month)}</h2>
        {loading && <p role="status">Loading usage…</p>}
        {!loading && refused && (
          <div role="alert" data-refusal="forbidden">
            <b>Only workspace admins can read the usage ledger</b>
            <p>Spend for the whole workspace is billing data. An empty list would say
              something false, so this screen says why it is empty instead.</p>
          </div>
        )}
        {!loading && !refused && error && <p role="alert" className="error">{error}</p>}
        {!loading && !refused && !error && summary && (
          <>
            <div className="settings-form-grid" data-usage-totals>
              <div>
                <b>{summary.credits}</b>
                <p>credits</p>
              </div>
              <div>
                <b>{summary.runMinutes}</b>
                <p>run-minutes</p>
              </div>
              <div>
                <b>{usd(summary.costUsd)}</b>
                <p>tokens</p>
              </div>
              <div>
                <b>{usd(summary.computeCostUsd)}</b>
                <p>compute</p>
              </div>
              <div>
                <b>{usd(summary.totalUsd)}</b>
                <p>total</p>
              </div>
            </div>
            <p className="settings-section-intro">
              {summary.events === 0
                ? 'No runner has reported usage this month yet.'
                : `${summary.events} ${summary.events === 1 ? 'report' : 'reports'} from ${tokens(summary.inputTokens)} input, ${tokens(summary.outputTokens)} output and ${tokens(summary.cacheReadTokens)} cache-read tokens. Priced at card ${summary.priceCard}.`}
            </p>
          </>
        )}
      </div>

      {!loading && !refused && !error && summary && (
        <div className="settings-group">
          <div className="settings-section-head">
            <div>
              <span className="settings-section-kicker">Ledger</span>
              <h2>Usage by {grouping}</h2>
            </div>
            <span className="settings-badge">{summary.rows.length} {summary.rows.length === 1 ? 'row' : 'rows'}</span>
          </div>
          <div className="api-key-toolbar">
            <input type="search" aria-label="Search usage" placeholder="Search agent or model"
              value={query} onChange={event => setQuery(event.target.value)} />
            <label className="settings-field">
              <span className="settings-field-label">Month</span>
              <input type="month" aria-label="Usage month" value={month}
                onChange={event => setMonth(event.target.value || monthValue())} />
            </label>
            <span style={{position: 'relative', marginLeft: 'auto'}}>
              <IconBtn label="Display options" title="Display options" active={displayOpen}
                onClick={() => setDisplayOpen(value => !value)}>
                <SlidersHorizontal size={15} />
              </IconBtn>
              {displayOpen && (
                <DisplayPanel display={display}
                  setDisplay={next => {
                    /* The panel's grouping is a property of the request, so it selects the
                     * server-side grouping rather than regrouping a loaded page. A grouping
                     * this screen cannot produce (the panel's own "reset to default") is
                     * refused and the control is held on the grouping the ledger answered,
                     * so the select never claims a grouping the list is not showing. */
                    if (next.grouping === 'agent' || next.grouping === 'model' || next.grouping === 'month') {
                      setGrouping(next.grouping);
                      setDisplay(next);
                    } else {
                      setDisplay({...next, grouping});
                    }
                  }}
                  onClose={() => setDisplayOpen(false)}
                  groupingOptions={[['agent', 'Agent'], ['model', 'Model'], ['month', 'Month']]}
                  groupingLabel="Group usage by"
                  entityToggles={false}
                  propertyRows={[
                    {key: 'tokens', label: 'Tokens', checked: show.tokens, onToggle: () => setShow(value => ({...value, tokens: !value.tokens}))},
                    {key: 'runs', label: 'Runs', checked: show.runs, onToggle: () => setShow(value => ({...value, runs: !value.runs}))},
                    {key: 'cost', label: 'Token cost', checked: show.cost, onToggle: () => setShow(value => ({...value, cost: !value.cost}))},
                  ]} />
              )}
            </span>
          </div>

          <div className="lin-list" aria-label={`Usage by ${grouping}`}>
            {visible.length === 0 && (
              <div className="lin-empty" data-state="empty">
                <h2>{summary.rows.length ? 'No usage matches this search' : 'Nothing spent this month'}</h2>
                <p>
                  {summary.rows.length
                    ? 'The search box narrows the rows this month returned; clear it to see them all.'
                    : 'No runner has reported a run this month. A run that reports no tokens records no row.'}
                </p>
              </div>
            )}
            {visible.length > 0 && (
              <section className="lin-group">
                <header className="lin-group-head">
                  <b>{summary.rows.length} {grouping === 'month' ? 'months' : `${grouping}s`}</b>
                  <span className="count">{filtered ? `${showing} of ${summary.credits} credits shown` : `${summary.credits} credits`}</span>
                  <span className="grow" />
                  <span className="count">{summary.runMinutes} min</span>
                </header>
                {visible.map(row => (
                  <UsageRowLine key={row.key} row={row} groupBy={grouping} show={show} />
                ))}
              </section>
            )}
          </div>
          <p className="settings-section-intro">
            Rows are appended, never edited: a correction is a new row, so a figure that changed
            stays visible next to the number that replaced it.
          </p>
        </div>
      )}
    </section>
  );
}

export default BillingUsageSettings;
