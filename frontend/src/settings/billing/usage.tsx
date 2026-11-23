/* Settings → Billing (HOJA-602-A-12, WP14).
 *
 * The usage ledger, in the issue list's layout: `usageSummary` rows are grouped by agent,
 * model or month. Every money figure is a decimal STRING from the server and is printed
 * verbatim (no floats ever touch money); the ledger is admin-only so a permission refusal
 * renders as an explicit refusal state rather than an empty list.
 */
import { useEffect, useMemo, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { Avatar, IconBtn, Ring } from "../../graph/shared";
import {
  DisplayPanel,
  defaultDisplay,
  type LinDisplay,
} from "../../graph/tasks";

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

async function gql<T>(
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  const token = localStorage.getItem("hoja_token");
  const response = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(token && token !== "session"
        ? { authorization: `Bearer ${token}` }
        : {}),
    },
    body: JSON.stringify({ query, variables }),
  });
  const body = await response.json();
  if (body.errors?.length) {
    const error = new Error(body.errors[0].message) as Error & {
      code?: string;
    };
    error.code = body.errors[0].extensions?.code;
    throw error;
  }
  return body.data as T;
}

/** The month selector's value is the browser's own YYYY-MM, so there is no date parsing. */
function monthValue(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [year, number] = month.split("-").map(Number);
  if (!year || !number) return month;
  return new Date(Date.UTC(year, number - 1, 1)).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Dollars, as the server printed them: grouped for reading, never rounded by us. */
function usd(value: string): string {
  const [whole, fraction] = String(value ?? "0").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return `$${grouped}${fraction ? `.${fraction}` : ""}`;
}

function tokens(value: number): string {
  return value.toLocaleString();
}

const USAGE_DISPLAY: LinDisplay = { ...defaultDisplay, grouping: "agent" };

type Grouping = "agent" | "model" | "month";

function UsageRowLine({
  row,
  groupBy,
  show,
}: {
  row: UsageRow;
  groupBy: Grouping;
  show: { tokens: boolean; runs: boolean; cost: boolean };
}) {
  const label = row.label || row.key;
  const spent =
    row.limitCredits != null
      ? Math.min(1, row.credits / Math.max(1, row.limitCredits))
      : 0;
  return (
    <div
      className="flex min-h-11 min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line px-4 py-2.5 text-sm text-foreground transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none"
      data-usage-row={row.key}
      data-grouping={groupBy}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        {groupBy === "agent" ? (
          <Avatar name={label} size={18} title={label} />
        ) : (
          <span
            className="inline-flex shrink-0 items-center rounded-md border border-line bg-subtle px-2 py-0.5 text-xs text-muted"
            aria-hidden="true"
          >
            {groupBy === "model" ? "model" : "month"}
          </span>
        )}
        <span
          className="min-w-0 flex-1 truncate font-medium text-foreground"
          title={label}
        >
          {label}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        {row.limitCredits != null ? (
          <span
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg px-2.5 py-0.5 text-foreground"
            title={`${row.credits} of ${row.limitCredits} credits used this month`}
          >
            <Ring frac={spent} size={12} />
            <span>
              {row.credits} / {row.limitCredits} credits
            </span>
          </span>
        ) : (
          <span
            className="inline-flex items-center rounded-full border border-line bg-bg px-2.5 py-0.5 text-foreground"
            title="Credits this month"
          >
            {row.credits} credits
          </span>
        )}
        <span
          className="inline-flex items-center rounded-full border border-line bg-bg px-2.5 py-0.5"
          title="Wall minutes this run held compute"
        >
          {row.runMinutes} min
        </span>
        {show.cost && (
          <span
            className="font-mono text-foreground"
            title={`tokens ${usd(row.costUsd)} · compute ${usd(row.computeCostUsd)}`}
          >
            {usd(row.costUsd)}
          </span>
        )}
        {show.runs && (
          <span className="hidden sm:inline">
            {row.runs} {row.runs === 1 ? "run" : "runs"}
          </span>
        )}
        {show.tokens && (
          <span
            className="hidden md:inline"
            title="fresh input / output / cache read"
          >
            {tokens(row.inputTokens)} in · {tokens(row.outputTokens)} out ·{" "}
            {tokens(row.cacheReadTokens)} cached
          </span>
        )}
      </div>
    </div>
  );
}

export function BillingUsageSettings() {
  const [month, setMonth] = useState(monthValue());
  const [grouping, setGrouping] = useState<Grouping>("agent");
  const [display, setDisplay] = useState<LinDisplay>({ ...USAGE_DISPLAY });
  const [displayOpen, setDisplayOpen] = useState(false);
  const [show, setShow] = useState({ tokens: true, runs: true, cost: true });
  const [query, setQuery] = useState("");
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refused, setRefused] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  /* A new month or grouping is a new request: the grouping is the server's argument, not
   * a pass over whatever happens to be loaded, so a month with more grouped rows than one
   * page cannot silently disagree with its own total. */
  useEffect(() => {
    let live = true;
    setLoading(true);
    setRefused(false);
    gql<{ usageSummary: UsageSummary }>(USAGE_QUERY, {
      month,
      groupBy: grouping,
    })
      .then((data) => {
        if (!live) return;
        setSummary(data.usageSummary);
        setError("");
      })
      .catch((e: Error & { code?: string }) => {
        if (!live) return;
        setSummary(null);
        setRefused(e.code === "FORBIDDEN");
        setError(e.code === "FORBIDDEN" ? "" : e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [month, grouping, reloadKey]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = summary?.rows ?? [];
    if (!needle) return rows;
    return rows.filter((row) =>
      `${row.label} ${row.key} ${row.model ?? ""}`
        .toLowerCase()
        .includes(needle),
    );
  }, [summary, query]);

  const showing = visible.reduce((total, row) => total + row.credits, 0);
  const filtered = (summary?.rows.length ?? 0) !== visible.length;

  return (
    <section
      className="mx-auto flex w-full max-w-4xl min-w-0 flex-col gap-6 p-4 text-foreground"
      data-settings-page=""
      data-settings-pane="billing"
    >
      <header className="flex flex-col gap-2">
        <h1 className="m-0 text-xl font-semibold text-foreground">Billing</h1>
        <p className="m-0 max-w-2xl text-sm leading-relaxed text-muted">
          What agents spent. One credit is 1,000 processed tokens — fresh input,
          cache reads and output — and money is computed from those tokens at a
          versioned price card, never from a cost the harness reported. Managed
          compute is charged per run-minute with a fifteen-minute floor; an
          agent on your own model key records its usage and costs nothing.
        </p>
      </header>

      <div className="flex min-w-0 flex-col gap-4 rounded-xl border border-line bg-panel p-4 sm:p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="m-0 text-base font-semibold text-foreground">
            {monthLabel(month)}
          </h2>
          <span className="text-xs text-muted">Monthly summary</span>
        </div>

        {loading && (
          <p role="status" className="m-0 text-sm text-muted">
            Loading usage…
          </p>
        )}

        {!loading && refused && (
          <div
            role="alert"
            data-refusal="forbidden"
            className="rounded-lg border border-line bg-bg p-4"
          >
            <b className="block text-sm font-semibold text-foreground">
              Only workspace admins can read the usage ledger
            </b>
            <p className="mt-1 mb-0 text-sm text-muted">
              Spend for the whole workspace is billing data. An empty list would
              say something false, so this screen says why it is empty instead.
            </p>
          </div>
        )}

        {!loading && !refused && error && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-bg p-4 text-sm text-danger"
          >
            <span className="min-w-0 flex-1">{error}</span>
            <button
              type="button"
              className="inline-flex min-h-9 items-center rounded-md border border-line bg-panel px-3 py-1.5 text-sm font-medium text-foreground hover:bg-subtle"
              onClick={() => setReloadKey((k) => k + 1)}
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !refused && !error && summary && (
          <>
            <div
              className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
              data-usage-totals
            >
              {[
                { value: String(summary.credits), label: "Credits" },
                { value: String(summary.runMinutes), label: "Run-minutes" },
                { value: usd(summary.costUsd), label: "Tokens" },
                { value: usd(summary.computeCostUsd), label: "Compute" },
                { value: usd(summary.totalUsd), label: "Total" },
              ].map((metric) => (
                <div
                  key={metric.label}
                  className="flex min-w-0 flex-col gap-1 rounded-lg border border-line bg-bg p-3"
                >
                  <b className="truncate font-mono text-base font-semibold text-foreground">
                    {metric.value}
                  </b>
                  <p className="m-0 text-xs text-muted">{metric.label}</p>
                </div>
              ))}
            </div>
            <p className="m-0 text-xs leading-relaxed text-muted">
              {summary.events === 0
                ? "No runner has reported usage this month yet."
                : `${summary.events} ${summary.events === 1 ? "report" : "reports"} from ${tokens(summary.inputTokens)} input, ${tokens(summary.outputTokens)} output and ${tokens(summary.cacheReadTokens)} cache-read tokens. Priced at card ${summary.priceCard}.`}
            </p>
          </>
        )}
      </div>

      {!loading && !refused && !error && summary && (
        <div className="flex min-w-0 flex-col gap-4 rounded-xl border border-line bg-panel p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="m-0 text-base font-semibold text-foreground">
              Usage by {grouping}
            </h2>
            <span className="rounded-full border border-line bg-bg px-2.5 py-0.5 text-xs text-muted">
              {summary.rows.length} {summary.rows.length === 1 ? "row" : "rows"}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <input
              type="search"
              aria-label="Search usage"
              placeholder="Search agent or model"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-h-9 min-w-0 flex-1 rounded-md border border-line bg-bg px-3 py-1.5 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11 max-sm:basis-full"
            />
            <label className="flex min-w-0 items-center gap-2 text-xs text-muted max-sm:flex-1">
              <span>Month</span>
              <input
                type="month"
                aria-label="Usage month"
                value={month}
                onChange={(event) =>
                  setMonth(event.target.value || monthValue())
                }
                className="min-h-9 min-w-0 rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11 max-sm:flex-1"
              />
            </label>
            <div className="relative ml-auto pointer-coarse:[&>span>button]:size-11 shell-phone:[&>span>button]:size-11">
              <IconBtn
                label="Display options"
                title="Display options"
                active={displayOpen}
                onClick={() => setDisplayOpen((value) => !value)}
              >
                <SlidersHorizontal size={15} />
              </IconBtn>
              {displayOpen && (
                <DisplayPanel
                  /* Radix flips the panel above the trigger when the space below is short, but
                   * the panel kept its fixed 560px cap and ran off the top of the viewport. Cap it
                   * at the space Radix measured on the chosen side (less the phone app bar) so it
                   * scrolls instead, and give its controls finger-sized targets on touch screens. */
                  anchorClass="max-h-[min(560px,75dvh,var(--radix-popover-content-available-height))] shell-phone:data-[side=top]:max-h-[min(560px,75dvh,calc(var(--radix-popover-content-available-height)-64px))] pointer-coarse:[&_button]:min-h-11 pointer-coarse:[&_select]:min-h-11 pointer-coarse:[&_select]:text-base pointer-coarse:[&_label]:min-h-11 pointer-coarse:[&_input[type=checkbox]]:size-5"
                  display={display}
                  setDisplay={(next) => {
                    /* The panel's grouping is a property of the request, so it selects the
                     * server-side grouping rather than regrouping a loaded page. A grouping
                     * this screen cannot produce (the panel's own "reset to default") is
                     * refused and the control is held on the grouping the ledger answered,
                     * so the select never claims a grouping the list is not showing. */
                    if (
                      next.grouping === "agent" ||
                      next.grouping === "model" ||
                      next.grouping === "month"
                    ) {
                      setGrouping(next.grouping);
                      setDisplay(next);
                    } else {
                      setDisplay({ ...next, grouping });
                    }
                  }}
                  onClose={() => setDisplayOpen(false)}
                  groupingOptions={[
                    ["agent", "Agent"],
                    ["model", "Model"],
                    ["month", "Month"],
                  ]}
                  groupingLabel="Group usage by"
                  entityToggles={false}
                  propertyRows={[
                    {
                      key: "tokens",
                      label: "Tokens",
                      checked: show.tokens,
                      onToggle: () =>
                        setShow((value) => ({
                          ...value,
                          tokens: !value.tokens,
                        })),
                    },
                    {
                      key: "runs",
                      label: "Runs",
                      checked: show.runs,
                      onToggle: () =>
                        setShow((value) => ({ ...value, runs: !value.runs })),
                    },
                    {
                      key: "cost",
                      label: "Token cost",
                      checked: show.cost,
                      onToggle: () =>
                        setShow((value) => ({ ...value, cost: !value.cost })),
                    },
                  ]}
                />
              )}
            </div>
          </div>

          <div
            className="overflow-hidden rounded-lg border border-line bg-bg"
            aria-label={`Usage by ${grouping}`}
          >
            {visible.length === 0 ? (
              <div
                className="flex flex-col items-center justify-center gap-1 p-8 text-center"
                data-state="empty"
              >
                <h3 className="m-0 text-sm font-semibold text-foreground">
                  {summary.rows.length
                    ? "No usage matches this search"
                    : "Nothing spent this month"}
                </h3>
                <p className="m-0 max-w-md text-xs text-muted">
                  {summary.rows.length
                    ? "The search box narrows the rows this month returned; clear it to see them all."
                    : "No runner has reported a run this month. A run that reports no tokens records no row."}
                </p>
              </div>
            ) : (
              <section className="flex flex-col">
                <header className="flex flex-wrap items-center gap-3 bg-subtle px-4 py-2 text-xs text-muted">
                  <b className="font-semibold text-foreground">
                    {summary.rows.length}{" "}
                    {grouping === "month" ? "months" : `${grouping}s`}
                  </b>
                  <span>
                    {filtered
                      ? `${showing} of ${summary.credits} credits shown`
                      : `${summary.credits} credits`}
                  </span>
                  <span className="ml-auto">{summary.runMinutes} min</span>
                </header>
                {visible.map((row) => (
                  <UsageRowLine
                    key={row.key}
                    row={row}
                    groupBy={grouping}
                    show={show}
                  />
                ))}
              </section>
            )}
          </div>

          <p className="m-0 text-xs leading-relaxed text-muted">
            Rows are appended, never edited: a correction is a new row, so a
            figure that changed stays visible next to the number that replaced
            it.
          </p>
        </div>
      )}
    </section>
  );
}

export default BillingUsageSettings;
