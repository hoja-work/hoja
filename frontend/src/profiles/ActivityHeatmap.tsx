import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { formatDuration, formatTokens, type ActivityDay } from "./api";

export type HeatmapMetric = "all" | "tokens" | "completed";

const LEVEL_CLASSES: Record<number, string> = {
  0: "bg-subtle border-line/60",
  1: "bg-emerald-200 border-emerald-300 dark:bg-emerald-950 dark:border-emerald-900",
  2: "bg-emerald-400 border-emerald-500 dark:bg-emerald-800 dark:border-emerald-700",
  3: "bg-emerald-500 border-emerald-600 dark:bg-emerald-600 dark:border-emerald-500",
  4: "bg-emerald-700 border-emerald-800 dark:bg-emerald-400 dark:border-emerald-300",
};

const METRIC_LABELS: { id: HeatmapMetric; label: string }[] = [
  { id: "all", label: "All activity" },
  { id: "tokens", label: "Tokens" },
  { id: "completed", label: "Shipped" },
];

const WEEKDAYS = ["", "Mon", "", "Wed", "", "Fri", ""];

function metricValue(day: ActivityDay, metric: HeatmapMetric): number {
  if (metric === "tokens") return day.tokens;
  if (metric === "completed") return day.completedTasks;
  return day.intensity;
}

function levelFor(
  day: ActivityDay,
  metric: HeatmapMetric,
  peak: number,
): number {
  if (metric === "all") return Math.max(0, Math.min(4, day.intensity));
  const v = metricValue(day, metric);
  if (v <= 0 || peak <= 0) return 0;
  const ratio = v / peak;
  if (ratio <= 0.25) return 1;
  if (ratio <= 0.5) return 2;
  if (ratio <= 0.75) return 3;
  return 4;
}

function formatDayDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

function monthShort(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  if (!y || !m) return "";
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, {
    month: "short",
    timeZone: "UTC",
  });
}

function weekdayUtc(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return 0;
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

export function ActivityHeatmap({
  daily,
  title = "Activity graph",
  subtitle,
}: {
  daily: ActivityDay[];
  title?: string;
  subtitle?: string;
}) {
  const [metric, setMetric] = useState<HeatmapMetric>("all");
  const [focused, setFocused] = useState<ActivityDay | null>(null);

  const { weeks, monthLabels, peak, activeCount } = useMemo(() => {
    if (!daily.length) {
      return {
        weeks: [] as (ActivityDay | null)[][],
        monthLabels: [] as string[],
        peak: 0,
        activeCount: 0,
      };
    }
    const leadPad = weekdayUtc(daily[0].date);
    const cells: (ActivityDay | null)[] = [
      ...Array.from({ length: leadPad }, () => null),
      ...daily,
    ];
    const cols: (ActivityDay | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) {
      const slice = cells.slice(i, i + 7);
      while (slice.length < 7) slice.push(null);
      cols.push(slice);
    }

    let lastMonth = "";
    const labels = cols.map((col) => {
      const first = col.find((c): c is ActivityDay => c !== null);
      if (!first) return "";
      const m = first.date.slice(0, 7);
      if (m !== lastMonth) {
        lastMonth = m;
        return monthShort(first.date);
      }
      return "";
    });

    let maxVal = 0;
    let active = 0;
    for (const d of daily) {
      const v = metricValue(d, metric);
      if (v > maxVal) maxVal = v;
      if (v > 0) active += 1;
    }
    return {
      weeks: cols,
      monthLabels: labels,
      peak: maxVal,
      activeCount: active,
    };
  }, [daily, metric]);

  const activeDay = focused ?? (daily.length ? daily[daily.length - 1] : null);

  /* The newest weeks sit at the right end. When the graph is wider than its card (phones),
   * start scrolled to the end so today is visible instead of the oldest months. */
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [weeks.length]);

  return (
    <section
      className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4"
      aria-label={title}
      data-activity-heatmap=""
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h2 className="m-0 text-sm font-semibold text-foreground">{title}</h2>
          <p className="m-0 text-xs text-muted">
            {subtitle ||
              `${activeCount} active ${activeCount === 1 ? "day" : "days"} in the last ${daily.length} days`}
          </p>
        </div>
        <div
          role="group"
          aria-label="Activity graph metric"
          className="inline-flex items-center gap-1 rounded-lg border border-line bg-bg p-0.5 max-sm:w-full"
        >
          {METRIC_LABELS.map((item) => {
            const active = metric === item.id;
            return (
              <button
                key={item.id}
                type="button"
                aria-pressed={active}
                onClick={() => setMetric(item.id)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11 max-sm:flex-1 ${
                  active
                    ? "bg-panel text-foreground shadow-xs transition-colors duration-150 ease-out"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      </div>

      <div
        ref={scroller}
        className="overflow-x-auto overscroll-x-contain pb-1"
        data-activity-heatmap-scroll=""
      >
        <div className="inline-flex flex-col gap-1">
          <div className="flex items-center gap-1 pl-8">
            {monthLabels.map((label, idx) => (
              <span
                key={idx}
                className="w-3 shrink-0 text-xs leading-none text-muted"
              >
                {label}
              </span>
            ))}
          </div>
          <div className="flex items-start gap-1">
            <div
              className="sticky left-0 z-10 flex w-7 shrink-0 flex-col gap-1 bg-panel pr-1"
              aria-hidden="true"
            >
              {WEEKDAYS.map((day, idx) => (
                <span
                  key={idx}
                  className="flex h-3 items-center text-xs leading-none text-muted"
                >
                  {day}
                </span>
              ))}
            </div>
            <div className="flex gap-1" role="grid" aria-label="Daily activity">
              {weeks.map((col, colIdx) => (
                <div key={colIdx} className="flex flex-col gap-1" role="row">
                  {col.map((cell, rowIdx) => {
                    if (!cell) {
                      return (
                        <span
                          key={rowIdx}
                          className="size-3 shrink-0 rounded-xs bg-transparent"
                          aria-hidden="true"
                        />
                      );
                    }
                    const lvl = levelFor(cell, metric, peak);
                    const isFocused = focused?.date === cell.date;
                    const summary = `${formatDayDate(cell.date)}: ${formatTokens(cell.tokens)} tokens, ${cell.completedTasks} shipped, ${cell.actions} actions`;
                    return (
                      <button
                        key={cell.date}
                        type="button"
                        role="gridcell"
                        title={summary}
                        aria-label={summary}
                        onMouseEnter={() => setFocused(cell)}
                        onFocus={() => setFocused(cell)}
                        onClick={() => setFocused(cell)}
                        className={`size-3 shrink-0 rounded-xs border transition-transform duration-100 focus-visible:outline-2 focus-visible:outline-accent ${LEVEL_CLASSES[lvl]} ${
                          isFocused ? "ring-1 ring-foreground" : ""
                        }`}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2.5 text-xs text-muted">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {activeDay ? (
            <>
              <span className="font-medium text-foreground">
                {formatDayDate(activeDay.date)}
              </span>
              <span className="tabular-nums">
                {formatTokens(activeDay.tokens)} tokens
              </span>
              <span className="tabular-nums">
                {activeDay.completedTasks} shipped
              </span>
              <span className="tabular-nums">
                {activeDay.runs} {activeDay.runs === 1 ? "run" : "runs"}
                {activeDay.runMinutes > 0
                  ? ` (${formatDuration(activeDay.runMinutes)})`
                  : ""}
              </span>
              <span className="tabular-nums">
                {activeDay.actions}{" "}
                {activeDay.actions === 1 ? "action" : "actions"}
              </span>
            </>
          ) : (
            <span>No activity recorded yet.</span>
          )}
        </div>
        <div className="flex items-center gap-1" aria-hidden="true">
          <span>Less</span>
          {[0, 1, 2, 3, 4].map((lvl) => (
            <span
              key={lvl}
              className={`size-3 rounded-xs border ${LEVEL_CLASSES[lvl]}`}
            />
          ))}
          <span>More</span>
        </div>
      </div>
    </section>
  );
}
