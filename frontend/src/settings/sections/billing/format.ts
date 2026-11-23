/* Billing figures. The server sends money as six-decimal strings ("0.003412"); people read
 * dollars and cents. Display rounds to cents, a non-zero amount under half a cent reads
 * "<$0.01" (never a misleading "$0.00"), and the exact string stays available as a title. */

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});
const COUNT = new Intl.NumberFormat("en-US");
const COMPACT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function amount(value: string | number | null | undefined): number {
  const n = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export function formatUsd(value: string | number | null | undefined): string {
  const n = amount(value);
  if (n !== 0 && Math.abs(n) < 0.005) return n < 0 ? "-<$0.01" : "<$0.01";
  return USD.format(n);
}

/** The server's exact figure, for a title attribute: "$0.003412". */
export function exactUsd(value: string | number | null | undefined): string {
  const raw =
    typeof value === "number" ? value.toFixed(6) : String(value ?? "0");
  return raw.startsWith("-") ? `-$${raw.slice(1)}` : `$${raw}`;
}

export function sumUsd(...values: (string | null | undefined)[]): number {
  // Six-decimal strings summed in integer micro-dollars, so 0.1 + 0.2 stays 0.3.
  const micros = values.reduce(
    (total, v) => total + Math.round(amount(v) * 1_000_000),
    0,
  );
  return micros / 1_000_000;
}

export function formatCount(value: number | null | undefined): string {
  return COUNT.format(value ?? 0);
}

export function formatTokens(value: number | null | undefined): string {
  const n = value ?? 0;
  return n < 10_000 ? COUNT.format(n) : COMPACT.format(n);
}

export function formatMinutes(value: number | null | undefined): string {
  const n = value ?? 0;
  if (n < 60) return `${n} min`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return m ? `${COUNT.format(h)} h ${m} min` : `${COUNT.format(h)} h`;
}

/** YYYY-MM for the current UTC month (the ledger's months are UTC). */
export function currentMonth(now = new Date()): string {
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return currentMonth(d);
}

export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
