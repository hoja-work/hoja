/* Initiative progress over time: one line per project, the share of its tasks
 * completed by the end of each week. Percent of scope puts every project on one
 * axis. Colours follow the project's slot in the initiative (fixed order, never
 * cycled); past eight projects the rest fold into "Other". A legend is always
 * shown, up to four lines are also labelled at their end, and the same numbers
 * are available as a table. */
import React, { useEffect, useMemo, useRef, useState } from "react";

export type ProgressSeries = {
  project: { id: string; name: string };
  scope: number;
  points: { weekStart: string; completed: number }[];
};

type Line = { id: string; name: string; color: string; values: number[] };

const SLOTS = 8;
const H = 200;
const PAD = { top: 12, right: 12, bottom: 24, left: 36 };

const weekLabel = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

export function ProgressGraph({ series }: { series: ProgressSeries[] }) {
  const [table, setTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) =>
      setWidth(Math.max(240, Math.round(e.contentRect.width))),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [table]);

  const weeks = useMemo(
    () => series[0]?.points.map((p) => p.weekStart) || [],
    [series],
  );
  const lines: Line[] = useMemo(() => {
    const pct = (s: ProgressSeries) =>
      s.points.map((p) => (s.scope ? (p.completed / s.scope) * 100 : 0));
    const head = series.slice(0, series.length > SLOTS ? SLOTS - 1 : SLOTS);
    const out = head.map((s, i) => ({
      id: s.project.id,
      name: s.project.name,
      color: `var(--series-${i + 1})`,
      values: pct(s),
    }));
    const rest = series.slice(head.length);
    if (rest.length) {
      const scope = rest.reduce((n, s) => n + s.scope, 0);
      out.push({
        id: "other",
        name: `Other (${rest.length})`,
        color: `var(--series-${SLOTS})`,
        values: weeks.map((_, w) =>
          scope
            ? (rest.reduce((n, s) => n + (s.points[w]?.completed || 0), 0) /
                scope) *
              100
            : 0,
        ),
      });
    }
    return out;
  }, [series, weeks]);

  if (!series.length || weeks.length < 2)
    return (
      <p className="m-0 py-2 text-sm text-muted">
        The graph appears once projects with tasks are linked.
      </p>
    );

  const innerW = width - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (i / (weeks.length - 1)) * innerW;
  const y = (v: number) => PAD.top + innerH - (Math.min(100, v) / 100) * innerH;
  const path = (values: number[]) =>
    values
      .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
      .join("");
  const labelled = lines.length <= 4;
  /* End labels sit just above each line's last point. Labels that would collide are
   * stacked upward, 16px apart, so none of them is drawn across a line. */
  const labelTop = new Map<string, number>();
  if (labelled) {
    let ceiling = Infinity;
    [...lines]
      .map((l) => ({ id: l.id, top: y(l.values[weeks.length - 1]) - 4 }))
      .sort((a, b) => b.top - a.top)
      .forEach((l) => {
        const top = Math.min(l.top, ceiling - 16);
        labelTop.set(l.id, top);
        ceiling = top;
      });
  }
  const at = hover ?? weeks.length - 1;

  return (
    <figure className="m-0 grid gap-2" data-initiative-progress-graph="">
      <figcaption className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="font-medium text-foreground">
          Completed tasks, by project
        </span>
        <span className="flex-1" />
        <button
          type="button"
          className="rounded-md border border-line px-2 py-0.5 text-xs text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent"
          aria-pressed={table}
          onClick={() => setTable((v) => !v)}
        >
          {table ? "Show graph" : "Show table"}
        </button>
      </figcaption>
      <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-xs text-muted">
        {lines.map((l) => (
          <li key={l.id}>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded px-1 hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent aria-pressed:text-foreground"
              aria-pressed={focus === l.id}
              onClick={() => setFocus((f) => (f === l.id ? null : l.id))}
            >
              <span
                className="inline-block h-0.5 w-3 rounded-full"
                style={{ background: l.color }}
                aria-hidden
              />
              {l.name}
            </button>
          </li>
        ))}
      </ul>
      {table ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="text-muted">
                <th className="py-1 pr-3 text-left font-medium">Project</th>
                {weeks.map((w) => (
                  <th key={w} className="py-1 pr-3 text-right font-medium">
                    {weekLabel(w)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.id} className="border-t border-line">
                  <th scope="row" className="py-1 pr-3 text-left font-medium">
                    {l.name}
                  </th>
                  {l.values.map((v, i) => (
                    <td key={i} className="py-1 pr-3 text-right tabular-nums">
                      {Math.round(v)}%
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={boxRef} className="relative">
          <svg
            width={width}
            height={H}
            viewBox={`0 0 ${width} ${H}`}
            role="img"
            aria-label={`Completed tasks per week for ${lines.length} projects; use the arrow keys to move between weeks.`}
            tabIndex={0}
            className="block max-w-full focus-visible:outline-2 focus-visible:outline-accent"
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              const i = Math.round(
                ((e.clientX - r.left - PAD.left) / innerW) * (weeks.length - 1),
              );
              setHover(Math.max(0, Math.min(weeks.length - 1, i)));
            }}
            onMouseLeave={() => setHover(null)}
            onBlur={() => setHover(null)}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                e.preventDefault();
                setHover((h) =>
                  Math.max(
                    0,
                    Math.min(
                      weeks.length - 1,
                      (h ?? weeks.length - 1) +
                        (e.key === "ArrowLeft" ? -1 : 1),
                    ),
                  ),
                );
              }
            }}
          >
            {[0, 50, 100].map((v) => (
              <g key={v}>
                <line
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={y(v)}
                  y2={y(v)}
                  stroke="var(--line)"
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 6}
                  y={y(v) + 4}
                  textAnchor="end"
                  fontSize={11}
                  fill="var(--muted)"
                >
                  {v}%
                </text>
              </g>
            ))}
            {[0, Math.floor((weeks.length - 1) / 2), weeks.length - 1].map(
              (i) => (
                <text
                  key={i}
                  x={x(i)}
                  y={H - 6}
                  textAnchor={
                    i === 0
                      ? "start"
                      : i === weeks.length - 1
                        ? "end"
                        : "middle"
                  }
                  fontSize={11}
                  fill="var(--muted)"
                >
                  {weekLabel(weeks[i])}
                </text>
              ),
            )}
            {hover !== null && (
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={PAD.top}
                y2={PAD.top + innerH}
                stroke="var(--muted)"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            )}
            {lines.map((l) => (
              <path
                key={l.id}
                d={path(l.values)}
                fill="none"
                stroke={l.color}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity={focus && focus !== l.id ? 0.2 : 1}
              />
            ))}
            {hover !== null &&
              lines.map((l) => (
                <circle
                  key={l.id}
                  cx={x(hover)}
                  cy={y(l.values[hover])}
                  r={4}
                  fill={l.color}
                  stroke="var(--panel)"
                  strokeWidth={2}
                  opacity={focus && focus !== l.id ? 0.2 : 1}
                />
              ))}
          </svg>
          {labelled && hover === null && (
            <ul className="pointer-events-none absolute inset-0 m-0 list-none p-0">
              {lines.map((l) => (
                <li
                  key={l.id}
                  className="absolute -translate-y-full truncate text-right text-xs text-muted"
                  style={{
                    right: width - x(weeks.length - 1),
                    top: labelTop.get(l.id),
                    maxWidth: innerW / 2,
                  }}
                >
                  {l.name} {Math.round(l.values[weeks.length - 1])}%
                </li>
              ))}
            </ul>
          )}
          {hover !== null && (
            <div
              role="status"
              className="pointer-events-none absolute top-0 z-10 grid min-w-40 gap-1 rounded-md border border-line bg-panel p-2 text-xs shadow-lg"
              style={
                x(at) > width / 2
                  ? { right: width - x(at) + 8 }
                  : { left: x(at) + 8 }
              }
            >
              <span className="font-medium">
                Week of {weekLabel(weeks[at])}
              </span>
              {lines.map((l) => (
                <span key={l.id} className="flex items-center gap-2">
                  <span
                    className="inline-block size-2 flex-none rounded-full"
                    style={{ background: l.color }}
                    aria-hidden
                  />
                  <span className="min-w-0 flex-1 truncate text-muted">
                    {l.name}
                  </span>
                  <span className="tabular-nums">
                    {Math.round(l.values[at])}%
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}
