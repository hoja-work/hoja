import * as React from "react";
import { cn } from "cn";

/* A ledger is a table on a desktop and a stack of cards on a phone: the name always gets
 * its own full line (HOJA-667-A-27 squeezed it to two characters next to five pills), and
 * the figures sit in a fixed three-column grid that never wraps into a second row. */

export type LedgerColumn = { key: string; label: string };

export function Ledger({
  label,
  columns,
  children,
  busy,
}: {
  label: string;
  columns: LedgerColumn[];
  children: React.ReactNode;
  busy?: boolean;
}) {
  return (
    <div
      role="list"
      aria-label={label}
      aria-busy={busy || undefined}
      data-billing-ledger=""
      className={cn(
        "min-w-0 divide-y divide-line overflow-hidden rounded-(--ds-settings-row-radius) border border-line bg-card shadow-xs transition-opacity",
        busy && "opacity-60",
      )}
    >
      <div
        aria-hidden="true"
        className="hidden items-center gap-6 bg-subtle px-4 py-2 text-sm text-muted-foreground sm:flex"
      >
        <span className="min-w-0 flex-1">Name</span>
        <span className="grid w-96 flex-none grid-cols-3 gap-4 text-right">
          {columns.map((c) => (
            <span key={c.key}>{c.label}</span>
          ))}
        </span>
      </div>
      {children}
    </div>
  );
}

export function LedgerRow({
  leading,
  title,
  meta,
  figures,
  rowKey,
}: {
  leading?: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
  figures: { key: string; label: string; value: React.ReactNode }[];
  rowKey?: string;
}) {
  return (
    <div
      role="listitem"
      data-usage-row={rowKey}
      className="flex min-w-0 flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:gap-6"
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        {leading && <span className="flex flex-none">{leading}</span>}
        <div className="grid min-w-0 flex-1 gap-0.5">
          <span className="truncate text-base font-medium text-foreground">
            {title}
          </span>
          {meta && (
            <span className="truncate text-sm text-muted-foreground">
              {meta}
            </span>
          )}
        </div>
      </div>
      <dl className="m-0 grid min-w-0 grid-cols-3 gap-4 sm:w-96 sm:flex-none">
        {figures.map((f) => (
          <div key={f.key} className="grid min-w-0 gap-0.5 sm:text-right">
            <dt className="text-sm text-muted-foreground sm:sr-only">
              {f.label}
            </dt>
            <dd className="m-0 min-w-0 text-base text-foreground tabular-nums">
              {f.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** A thin bar for spend against a budget. Turns destructive at 90%. */
export function BudgetBar({ used, limit }: { used: number; limit: number }) {
  const frac = limit > 0 ? Math.min(1, used / limit) : 1;
  return (
    <span
      role="meter"
      aria-label="Budget used"
      aria-valuemin={0}
      aria-valuemax={limit}
      aria-valuenow={Math.min(used, limit)}
      className="mt-1 block h-1 w-full overflow-hidden rounded-full bg-subtle"
    >
      <span
        className={cn(
          "block h-full rounded-full",
          frac >= 0.9 ? "bg-destructive" : "bg-primary",
        )}
        style={{ width: `${Math.round(frac * 100)}%` }}
      />
    </span>
  );
}

export function Stat({
  label,
  value,
  title,
  detail,
  className,
  large,
}: {
  label: string;
  value: string;
  title?: string;
  detail?: React.ReactNode;
  className?: string;
  large?: boolean;
}) {
  return (
    <div
      data-billing-stat={label}
      className={cn(
        "grid min-w-0 content-start gap-1 rounded-(--ds-settings-row-radius) border border-line bg-card p-4 shadow-xs",
        className,
      )}
    >
      <span className="text-sm text-muted-foreground">{label}</span>
      <span
        title={title}
        className={cn(
          "truncate font-semibold tracking-tight text-foreground tabular-nums",
          large ? "text-3xl" : "text-xl",
        )}
      >
        {value}
      </span>
      {detail && (
        <span className="text-sm text-muted-foreground">{detail}</span>
      )}
    </div>
  );
}
