import * as React from "react";
import {
  BarChart3,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Cpu,
  RotateCw,
  Search,
} from "lucide-react";
import { Avatar, AvatarFallback } from "../../components/ui/avatar";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Segmented } from "../../components/ui/switch";
import {
  EmptyState,
  Loadable,
  Notice,
  SettingsPage,
  SettingsSection,
  useResource,
} from "../ui";
import {
  loadEvents,
  loadSummary,
  RECENT_LIMIT,
  type Grouping,
  type UsageEvent,
  type UsageRow,
  type UsageSummary,
} from "./billing/api";
import {
  currentMonth,
  exactUsd,
  formatCount,
  formatMinutes,
  formatTokens,
  formatUsd,
  formatWhen,
  monthLabel,
  shiftMonth,
  sumUsd,
} from "./billing/format";
import { BudgetBar, Ledger, LedgerRow, Stat } from "./billing/Ledger";

/* Billing & usage (HOJA-734). Read-only: what agents spent, per month, from the append-only
 * usage ledger. Budgets are edited on each agent (Agents), so this page links there instead
 * of pretending to own them. */

const AGENTS_PATH = "/workspace/agents";

const COLUMNS = [
  { key: "credits", label: "Credits" },
  { key: "time", label: "Run time" },
  { key: "cost", label: "Cost" },
];

function initials(name: string) {
  const parts = name
    .trim()
    .split(/[\s._-]+/)
    .filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

function AgentMark({ name }: { name: string }) {
  return (
    <Avatar size="sm" aria-hidden="true">
      <AvatarFallback className="bg-subtle text-xs text-muted-foreground">
        {initials(name)}
      </AvatarFallback>
    </Avatar>
  );
}

function IconMark({ icon: Icon }: { icon: typeof Cpu }) {
  return (
    <span
      aria-hidden="true"
      className="grid size-6 place-items-center rounded-md border border-line bg-subtle text-muted-foreground"
    >
      <Icon className="size-3.5" />
    </span>
  );
}

function Money({ total, parts }: { total: number; parts?: string }) {
  return <span title={parts ?? exactUsd(total)}>{formatUsd(total)}</span>;
}

function tokenMeta(r: {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
}) {
  return `${formatTokens(r.inputTokens)} in · ${formatTokens(r.outputTokens)} out · ${formatTokens(r.cacheReadTokens)} cached`;
}

function UsageLine({
  row,
  grouping,
  onOpenMonth,
}: {
  row: UsageRow;
  grouping: Grouping;
  onOpenMonth: (month: string) => void;
}) {
  const name =
    grouping === "month" ? monthLabel(row.label) : row.label || row.key;
  const total = sumUsd(row.costUsd, row.computeCostUsd);
  const budget = row.limitCredits;
  const budgetNote =
    budget != null
      ? row.budgetAction === "stop"
        ? "Stops at budget"
        : "Queues at budget"
      : null;
  return (
    <LedgerRow
      rowKey={row.key}
      leading={
        grouping === "agent" ? (
          <AgentMark name={name} />
        ) : (
          <IconMark icon={grouping === "model" ? Cpu : CalendarDays} />
        )
      }
      title={
        grouping === "month" ? (
          <Button
            variant="link"
            className="max-w-full truncate text-base font-medium"
            onClick={() => onOpenMonth(row.label)}
            aria-label={`Show ${name} by agent`}
          >
            {name}
          </Button>
        ) : (
          <span title={name}>{name}</span>
        )
      }
      meta={[
        `${formatCount(row.runs)} ${row.runs === 1 ? "run" : "runs"}`,
        tokenMeta(row),
        budgetNote,
      ]
        .filter(Boolean)
        .join(" · ")}
      figures={[
        {
          key: "credits",
          label: "Credits",
          value:
            budget != null ? (
              <span
                className="grid"
                title={`${formatCount(row.credits)} of ${formatCount(budget)} credits this month`}
              >
                <span>
                  {formatCount(row.credits)}
                  <span className="text-muted-foreground">
                    {" "}
                    / {formatCount(budget)}
                  </span>
                </span>
                <BudgetBar used={row.credits} limit={budget} />
              </span>
            ) : (
              formatCount(row.credits)
            ),
        },
        {
          key: "time",
          label: "Run time",
          value: formatMinutes(row.runMinutes),
        },
        {
          key: "cost",
          label: "Cost",
          value: (
            <Money
              total={total}
              parts={`Tokens ${exactUsd(row.costUsd)} · Compute ${exactUsd(row.computeCostUsd)}`}
            />
          ),
        },
      ]}
    />
  );
}

function EventLine({ event }: { event: UsageEvent }) {
  const total = sumUsd(event.costUsd, event.computeCostUsd);
  const key =
    event.authMode === "byo-key" ? "your model key" : "Hoja's model key";
  return (
    <LedgerRow
      rowKey={event.id}
      leading={<AgentMark name={event.agentName} />}
      title={<span title={event.agentName}>{event.agentName}</span>}
      meta={[
        formatWhen(event.recordedAt),
        event.model,
        key,
        event.source === "correction" ? "Correction" : null,
      ]
        .filter(Boolean)
        .join(" · ")}
      figures={[
        { key: "credits", label: "Credits", value: formatCount(event.credits) },
        {
          key: "time",
          label: "Run time",
          value: formatMinutes(event.runMinutes),
        },
        {
          key: "cost",
          label: "Cost",
          value: (
            <Money
              total={total}
              parts={`Tokens ${exactUsd(event.costUsd)} · Compute ${exactUsd(event.computeCostUsd)}`}
            />
          ),
        },
      ]}
    />
  );
}

function StatStrip({ summary }: { summary: UsageSummary }) {
  return (
    <div data-usage-totals="" className="grid grid-cols-2 gap-3 lg:grid-cols-6">
      <Stat
        large
        className="col-span-2"
        label={`Total for ${monthLabel(summary.month)}`}
        value={formatUsd(summary.totalUsd)}
        title={exactUsd(summary.totalUsd)}
        detail={
          summary.events === 0
            ? "No usage reported yet"
            : `${formatCount(summary.events)} usage ${summary.events === 1 ? "report" : "reports"}`
        }
      />
      <Stat
        label="Model tokens"
        value={formatUsd(summary.costUsd)}
        title={exactUsd(summary.costUsd)}
        detail={`${formatTokens(summary.inputTokens + summary.outputTokens + summary.cacheReadTokens)} tokens`}
      />
      <Stat
        label="Compute"
        value={formatUsd(summary.computeCostUsd)}
        title={exactUsd(summary.computeCostUsd)}
        detail="Per run minute"
      />
      <Stat
        label="Credits"
        value={formatCount(summary.credits)}
        detail="1 per 1,000 tokens"
      />
      <Stat
        label="Run time"
        value={formatMinutes(summary.runMinutes)}
        detail="Managed runs"
      />
    </div>
  );
}

export default function BillingSettings() {
  const thisMonth = currentMonth();
  const [month, setMonth] = React.useState(thisMonth);
  const [grouping, setGrouping] = React.useState<Grouping>("agent");
  const [filter, setFilter] = React.useState("");
  const summary = useResource(
    () => loadSummary(month, grouping),
    [month, grouping],
  );
  const events = useResource(() => loadEvents(month), [month]);

  const rows = React.useMemo(() => {
    const all = summary.data?.rows ?? [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((r) =>
      `${r.label} ${r.key} ${r.model ?? ""}`.toLowerCase().includes(needle),
    );
  }, [summary.data, filter]);

  const label = monthLabel(month);
  const groupedBy =
    grouping === "agent"
      ? "agent"
      : grouping === "model"
        ? "model"
        : "month, all time";

  return (
    <SettingsPage
      wide
      title="Billing & usage"
      description="What your agents spent. Credits count processed tokens, and money is priced from those tokens plus managed compute time. An agent on your own model key records usage but costs nothing here."
      actions={
        <div
          role="group"
          aria-label="Usage month"
          className="flex items-center gap-1"
        >
          <Button
            size="icon"
            variant="ghost"
            aria-label="Previous month"
            onClick={() => setMonth((m) => shiftMonth(m, -1))}
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </Button>
          <span
            aria-live="polite"
            data-usage-month={month}
            className="min-w-36 text-center text-base font-medium tabular-nums"
          >
            {label}
          </span>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Next month"
            disabled={month >= thisMonth}
            onClick={() => setMonth((m) => shiftMonth(m, 1))}
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </Button>
        </div>
      }
    >
      <Loadable resource={summary} rows={2}>
        {(data) => (
          <div className="grid gap-3">
            {summary.error && (
              <Notice
                tone="error"
                title={`Couldn't load ${label}`}
                action={
                  <Button size="sm" onClick={() => void summary.reload()}>
                    <RotateCw className="size-3.5" aria-hidden="true" />
                    Try again
                  </Button>
                }
              >
                {summary.error} The figures below are from{" "}
                {monthLabel(data.month)}.
              </Notice>
            )}
            <StatStrip summary={data} />
            {data.events > 0 && (
              <p className="m-0 text-sm text-muted-foreground">
                Priced at price card {data.priceCard}. Ledger rows are never
                edited: a correction is a new row.
              </p>
            )}
          </div>
        )}
      </Loadable>

      {(summary.data || !summary.error) && (
        <SettingsSection
          title={`Usage by ${groupedBy}`}
          description={
            <>
              Budgets are set per agent.{" "}
              <a
                href={AGENTS_PATH}
                className="font-medium text-foreground underline underline-offset-2 hover:no-underline"
              >
                Change budgets in Agents
              </a>
            </>
          }
          action={
            <Segmented
              label="Group usage by"
              value={grouping}
              onValueChange={(v) => {
                if (v === "agent" || v === "model" || v === "month") {
                  setGrouping(v);
                  setFilter("");
                }
              }}
              options={[
                { value: "agent", label: "Agent" },
                { value: "model", label: "Model" },
                { value: "month", label: "Month" },
              ]}
            />
          }
          bare
        >
          {summary.data && summary.data.rows.length > 5 && (
            <div className="relative">
              <Search
                className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                type="search"
                aria-label={`Filter usage by ${grouping}`}
                placeholder={
                  grouping === "model"
                    ? "Filter models"
                    : grouping === "agent"
                      ? "Filter agents"
                      : "Filter months"
                }
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="pl-8"
              />
            </div>
          )}
          <Loadable
            resource={summary}
            rows={3}
            isEmpty={(d) => d.rows.length === 0}
            empty={
              <EmptyState
                icon={<BarChart3 />}
                title={
                  grouping === "month"
                    ? "No usage recorded yet"
                    : `No usage in ${label}`
                }
                description="Usage appears here after an agent finishes its first run. Assign a task to an agent to get started."
                action={
                  month !== thisMonth || grouping === "month" ? undefined : (
                    <Button
                      size="sm"
                      onClick={() => setMonth(shiftMonth(month, -1))}
                    >
                      <ChevronLeft className="size-3.5" aria-hidden="true" />
                      View {monthLabel(shiftMonth(month, -1))}
                    </Button>
                  )
                }
              />
            }
          >
            {() =>
              rows.length === 0 ? (
                <EmptyState
                  icon={<Search />}
                  title="Nothing matches this filter"
                  action={
                    <Button size="sm" onClick={() => setFilter("")}>
                      Clear filter
                    </Button>
                  }
                />
              ) : (
                <Ledger
                  label={`Usage by ${groupedBy}`}
                  columns={COLUMNS}
                  busy={summary.loading}
                >
                  {rows.map((row) => (
                    <UsageLine
                      key={row.key}
                      row={row}
                      grouping={grouping}
                      onOpenMonth={(m) => {
                        setMonth(m);
                        setGrouping("agent");
                      }}
                    />
                  ))}
                </Ledger>
              )
            }
          </Loadable>
        </SettingsSection>
      )}

      <SettingsSection
        title="Recent usage reports"
        description={`The latest ${RECENT_LIMIT} ledger rows for ${label}, newest first.`}
        bare
      >
        <Loadable
          resource={events}
          rows={3}
          isEmpty={(d) => d.length === 0}
          empty={
            <EmptyState
              title="No reports this month"
              description="Each agent run adds a row when it reports tokens or compute time."
            />
          }
        >
          {(list) => (
            <Ledger
              label="Recent usage reports"
              columns={COLUMNS}
              busy={events.loading}
            >
              {list.map((e) => (
                <EventLine key={e.id} event={e} />
              ))}
            </Ledger>
          )}
        </Loadable>
      </SettingsSection>
    </SettingsPage>
  );
}
