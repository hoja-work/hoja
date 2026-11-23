/* The trace viewer: one agent session (hoja.trace/v1) as a waterfall, a tool breakdown and the task trail.
 * Opened from a trace attachment on an issue; the attachment stays a plain JSON download as well. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Activity,
  Bot,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Download,
  MessageSquare,
  Octagon,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import {
  defaultCollapsed,
  descendantMarks,
  fetchTrace,
  formatDuration,
  formatTokens,
  offsetLabel,
  totalTokens,
  traceRows,
  type HojaTrace,
  type SpanStatus,
  type TraceRow,
  type TraceSpan,
} from "./trace";
import { storedApiToken } from "../main";

type Tab = "timeline" | "tools" | "tasks";

/* The agents whose plugins upload traces (hoja-claude-plugin, hoja-codex-plugin). */
const HOST_NAMES: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
};

const BAR: Record<TraceSpan["kind"], string> = {
  session: "bg-muted",
  turn: "bg-foreground/15",
  llm: "bg-violet-500/70",
  tool: "bg-accent",
  subagent: "bg-teal-500/70",
};
const STATUS_BAR: Partial<Record<SpanStatus, string>> = {
  error: "bg-danger",
  interrupted: "bg-danger/60",
  blocked: "bg-amber-500",
};
const STATUS_TEXT: Record<SpanStatus, string> = {
  ok: "Succeeded",
  error: "Failed",
  blocked: "Blocked by the task gate",
  interrupted: "Interrupted",
  running: "Still running when the trace was saved",
};

function KindIcon({ span }: { span: TraceSpan }) {
  const cls = "size-4 shrink-0 text-muted";
  if (span.status === "blocked")
    return <Octagon className="size-4 shrink-0 text-amber-500" aria-hidden />;
  if (span.status === "error" || span.status === "interrupted")
    return <CircleAlert className="size-4 shrink-0 text-danger" aria-hidden />;
  if (span.kind === "turn")
    return <MessageSquare className={cls} aria-hidden />;
  if (span.kind === "llm") return <Sparkles className={cls} aria-hidden />;
  if (span.kind === "subagent") return <Bot className={cls} aria-hidden />;
  return <Wrench className={cls} aria-hidden />;
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-line bg-bg p-2 sm:p-4">
      <span className="truncate text-sm text-muted">{label}</span>
      <span className="truncate text-lg font-semibold tabular-nums">
        {value}
      </span>
      {note && (
        <span className="hidden truncate text-xs text-muted sm:block">
          {note}
        </span>
      )}
    </div>
  );
}

function Pre({ label, text }: { label: string; text: unknown }) {
  if (typeof text !== "string" || !text) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted">{label}</span>
      <pre className="max-h-64 overflow-auto rounded-md border border-line bg-bg p-2 text-xs whitespace-pre-wrap break-words">
        {text}
      </pre>
    </div>
  );
}

function SpanDetail({ row, onClose }: { row: TraceRow; onClose: () => void }) {
  const { span } = row;
  const a = span.attrs || {};
  const usage = a.usage as Record<string, number> | undefined;
  return (
    <aside
      aria-label="Span details"
      className="flex max-h-96 flex-col gap-3 overflow-auto border-t border-line p-4 sm:max-h-none sm:w-96 sm:shrink-0 sm:border-t-0 sm:border-l"
    >
      <div className="flex items-start gap-2">
        <KindIcon span={span} />
        <span className="min-w-0 flex-1 font-medium break-words">
          {span.name}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close span details"
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-subtle hover:transition-none"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">Status</dt>
        <dd>{STATUS_TEXT[span.status] || span.status}</dd>
        <dt className="text-muted">Started</dt>
        <dd className="tabular-nums">{offsetLabel(row.startMs)}</dd>
        <dt className="text-muted">Duration</dt>
        <dd className="tabular-nums">
          {formatDuration(row.durationMs)}
          {a.measured === "hook" ? " (measured)" : ""}
        </dd>
        {typeof a.model === "string" && (
          <>
            <dt className="text-muted">Model</dt>
            <dd className="break-all">{a.model}</dd>
          </>
        )}
        {typeof a.stopReason === "string" && (
          <>
            <dt className="text-muted">Stop reason</dt>
            <dd>{a.stopReason}</dd>
          </>
        )}
        {typeof a.thinkingMs === "number" && (
          <>
            <dt className="text-muted">Thinking</dt>
            <dd className="tabular-nums">{formatDuration(a.thinkingMs)}</dd>
          </>
        )}
        {usage && (
          <>
            <dt className="text-muted">Tokens in / out</dt>
            <dd className="tabular-nums">
              {formatTokens(usage.input_tokens || 0)} /{" "}
              {formatTokens(usage.output_tokens || 0)}
            </dd>
            <dt className="text-muted">Cache read / write</dt>
            <dd className="tabular-nums">
              {formatTokens(usage.cache_read_tokens || 0)} /{" "}
              {formatTokens(usage.cache_creation_tokens || 0)}
            </dd>
          </>
        )}
        {typeof a.file === "string" && (
          <>
            <dt className="text-muted">File</dt>
            <dd className="break-all">{a.file}</dd>
          </>
        )}
        {span.agentId && (
          <>
            <dt className="text-muted">Agent</dt>
            <dd className="break-all">{span.agentId}</dd>
          </>
        )}
      </dl>
      <Pre label="Blocked because" text={a.blockedReason} />
      <Pre label="Prompt" text={a.prompt} />
      <Pre label="Input" text={a.input} />
      <Pre label="Output" text={a.output} />
      <Pre label="Error" text={a.error} />
      <Pre label="Response" text={a.text} />
      <Pre label="Context the task gate added" text={a.hookContext} />
    </aside>
  );
}

function Timeline({ trace }: { trace: HojaTrace }) {
  const [collapsed, setCollapsed] = useState(() => defaultCollapsed(trace));
  const [selected, setSelected] = useState<string | null>(null);
  const rows = useMemo(() => traceRows(trace, collapsed), [trace, collapsed]);
  const total = Math.max(1, trace.session.durationMs);
  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectedRow = rows.find((r) => r.span.id === selected);
  // Folded rows draw their contents; computed once per trace for every row that can fold.
  const marks = useMemo(
    () =>
      new Map(
        trace.spans
          .filter((s) => s.kind === "turn" || s.kind === "subagent")
          .map((s) => [s.id, descendantMarks(trace, s.id)] as const),
      ),
    [trace],
  );
  const marksFor = (id: string) =>
    marks.get(id) || { marks: [], failed: 0, blocked: 0 };
  const expandable = trace.spans.filter(
    (s) => s.kind === "turn" || s.kind === "subagent",
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
      <div className="flex min-h-64 min-w-0 flex-1 flex-col sm:min-h-0">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2 text-xs text-muted">
          <span className="flex-1">
            {rows.length} of {trace.spans.length - 1} spans
          </span>
          <button
            type="button"
            className="rounded-md px-2 py-1 transition-colors hover:bg-subtle hover:transition-none"
            onClick={() => setCollapsed(new Set())}
          >
            Expand all
          </button>
          <button
            type="button"
            className="rounded-md px-2 py-1 transition-colors hover:bg-subtle hover:transition-none"
            onClick={() => setCollapsed(new Set(expandable.map((s) => s.id)))}
          >
            Collapse all
          </button>
        </div>
        <div className="hidden border-b border-line px-4 py-1 text-xs text-muted tabular-nums sm:flex">
          <span className="w-2/5 shrink-0" />
          <div className="flex flex-1 justify-between">
            {[0, 0.25, 0.5, 0.75, 1].map((f) => (
              <span key={f}>{offsetLabel(total * f)}</span>
            ))}
          </div>
        </div>
        <ul
          className="min-h-0 flex-1 overflow-auto"
          aria-label="Session timeline"
        >
          {rows.map((row) => {
            const { span } = row;
            const left = (row.startMs / total) * 100;
            const width = Math.max(0.4, (row.durationMs / total) * 100);
            const open = !collapsed.has(span.id);
            const isSelected = selected === span.id;
            return (
              <li
                key={span.id}
                className="flex flex-col border-b border-line/60 sm:flex-row sm:items-center"
              >
                <div
                  className="flex min-w-0 items-center gap-1 py-1 pr-2 sm:w-2/5 sm:shrink-0"
                  style={{
                    paddingLeft: `${0.75 + Math.min(row.depth, 8) * 1}rem`,
                  }}
                >
                  {row.children > 0 ? (
                    <button
                      type="button"
                      onClick={() => toggle(span.id)}
                      aria-expanded={open}
                      aria-label={`${open ? "Collapse" : "Expand"} ${span.name}`}
                      className="flex size-6 shrink-0 items-center justify-center rounded text-muted transition-colors hover:bg-subtle hover:transition-none"
                    >
                      {open ? (
                        <ChevronDown className="size-4" aria-hidden />
                      ) : (
                        <ChevronRight className="size-4" aria-hidden />
                      )}
                    </button>
                  ) : (
                    <span className="size-6 shrink-0" />
                  )}
                  <button
                    type="button"
                    onClick={() => setSelected(isSelected ? null : span.id)}
                    aria-pressed={isSelected}
                    className={`flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left text-sm transition-colors hover:bg-subtle hover:transition-none ${isSelected ? "bg-accent-soft" : ""}`}
                  >
                    <KindIcon span={span} />
                    <span
                      className={`min-w-0 flex-1 truncate ${span.kind === "turn" ? "font-medium" : ""} ${span.kind === "llm" ? "text-muted" : ""}`}
                      title={span.name}
                    >
                      {span.name}
                    </span>
                    {!open &&
                      row.children > 0 &&
                      marksFor(span.id).failed > 0 && (
                        <span className="shrink-0 text-xs text-danger tabular-nums">
                          {marksFor(span.id).failed} failed
                        </span>
                      )}
                    {!open &&
                      row.children > 0 &&
                      marksFor(span.id).blocked > 0 && (
                        <span className="shrink-0 text-xs text-amber-600 tabular-nums">
                          {marksFor(span.id).blocked} blocked
                        </span>
                      )}
                    <span className="shrink-0 text-xs text-muted tabular-nums">
                      {formatDuration(row.durationMs)}
                    </span>
                  </button>
                </div>
                <div
                  className="relative mx-4 mb-2 h-2 shrink-0 rounded-sm bg-subtle sm:mx-0 sm:mr-4 sm:mb-0 sm:flex-1"
                  aria-hidden
                >
                  <div
                    className={`absolute top-0 bottom-0 rounded-sm ${STATUS_BAR[span.status] || BAR[span.kind]}`}
                    style={{
                      left: `${Math.min(left, 99.6)}%`,
                      width: `${Math.min(width, 100 - Math.min(left, 99.6))}%`,
                    }}
                  />
                  {!open &&
                    row.children > 0 &&
                    marksFor(span.id).marks.map((m, k) => (
                      <div
                        key={k}
                        className={`absolute top-0 bottom-0 ${STATUS_BAR[m.status] || BAR[m.kind]}`}
                        style={{
                          left: `${Math.min((m.startMs / total) * 100, 99.8)}%`,
                          width: `${Math.max(0.15, (m.durationMs / total) * 100)}%`,
                        }}
                      />
                    ))}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
      {selectedRow && (
        <SpanDetail row={selectedRow} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function Tools({ trace }: { trace: HojaTrace }) {
  const longest = Math.max(1, ...trace.tools.map((t) => t.totalMs));
  if (!trace.tools.length)
    return <p className="p-4 text-sm text-muted">No tool calls.</p>;
  return (
    <div className="min-h-0 flex-1 overflow-auto p-4">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-2 pr-2 font-normal">Tool</th>
            <th className="py-2 pr-2 text-right font-normal">Calls</th>
            <th className="py-2 pr-2 text-right font-normal">Failed</th>
            <th className="py-2 pr-2 text-right font-normal">Blocked</th>
            <th className="py-2 pr-2 text-right font-normal">Time</th>
            <th className="hidden w-1/3 py-2 font-normal sm:table-cell">
              <span className="sr-only">Share of tool time</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {trace.tools.map((t) => (
            <tr key={t.name} className="border-t border-line">
              <td className="max-w-48 truncate py-2 pr-2" title={t.name}>
                {t.name}
              </td>
              <td className="py-2 pr-2 text-right tabular-nums">{t.calls}</td>
              <td
                className={`py-2 pr-2 text-right tabular-nums ${t.errors ? "text-danger" : "text-muted"}`}
              >
                {t.errors || "–"}
              </td>
              <td
                className={`py-2 pr-2 text-right tabular-nums ${t.blocked ? "text-amber-600" : "text-muted"}`}
              >
                {t.blocked || "–"}
              </td>
              <td className="py-2 pr-2 text-right tabular-nums">
                {formatDuration(t.totalMs)}
              </td>
              <td
                className="hidden py-2 sm:table-cell"
                aria-label={`${Math.round((t.totalMs / longest) * 100)}% of the slowest tool's time`}
              >
                <div className="h-2 rounded-sm bg-subtle">
                  <div
                    className="h-2 rounded-sm bg-accent"
                    style={{ width: `${(t.totalMs / longest) * 100}%` }}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {trace.totals.filesTouched.length > 0 && (
        <div className="mt-4 flex flex-col gap-1">
          <span className="text-sm text-muted">
            Files written ({trace.totals.filesTouched.length})
          </span>
          <ul className="flex flex-col gap-1 text-sm">
            {trace.totals.filesTouched.map((f) => (
              <li key={f} className="truncate font-mono text-xs" title={f}>
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Tasks({ trace }: { trace: HojaTrace }) {
  const t0 = Date.parse(trace.session.start);
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4">
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Tasks</h3>
        {!trace.tasks.length && (
          <p className="text-sm text-muted">The session tracked no tasks.</p>
        )}
        <ul className="flex flex-col gap-2">
          {trace.tasks.map((task) => {
            const answered = (task.questions || []).filter(
              (q) => q.answered,
            ).length;
            return (
              <li
                key={task.id}
                className="flex flex-col gap-1 rounded-lg border border-line p-2"
              >
                <div className="flex items-center gap-2 text-sm">
                  {task.status === "completed" ? (
                    <CircleCheck
                      className="size-4 shrink-0 text-accent"
                      aria-hidden
                    />
                  ) : (
                    <Activity
                      className="size-4 shrink-0 text-muted"
                      aria-hidden
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    {task.subject}
                  </span>
                  {task.issue && (
                    <span className="shrink-0 rounded bg-subtle px-2 text-xs">
                      {task.issue}
                    </span>
                  )}
                  <span className="shrink-0 text-xs text-muted">
                    {task.status.replace("_", " ")}
                  </span>
                </div>
                {task.questions && task.questions.length > 0 && (
                  <ul className="flex flex-col gap-1 pl-6 text-xs text-muted">
                    <li>
                      {answered} of {task.questions.length} acceptance questions
                      answered with evidence
                    </li>
                    {task.questions.map((q) => (
                      <li key={q.id} className="flex gap-1">
                        <span aria-hidden>{q.answered ? "✓" : "○"}</span>
                        <span className="sr-only">
                          {q.answered ? "Answered:" : "Open:"}
                        </span>
                        <span>{q.text}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Events</h3>
        {!trace.events.length && (
          <p className="text-sm text-muted">No events.</p>
        )}
        <ol className="flex flex-col">
          {trace.events.map((e, i) => (
            <li
              key={`${e.at}-${i}`}
              className="flex gap-3 border-t border-line py-2 text-sm"
            >
              <span className="w-16 shrink-0 text-xs text-muted tabular-nums">
                {offsetLabel(Date.parse(e.at) - t0)}
              </span>
              <span
                className={`min-w-0 flex-1 break-words ${e.kind === "gate.block" ? "text-amber-600" : e.kind === "error" ? "text-danger" : ""}`}
              >
                {e.message}
              </span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function Body({
  trace,
  tab,
  setTab,
}: {
  trace: HojaTrace;
  tab: Tab;
  setTab: (t: Tab) => void;
}) {
  const t = trace.totals;
  const tokens = totalTokens(t.tokens);
  const tabs: [Tab, string][] = [
    ["timeline", "Timeline"],
    ["tools", `Tools (${trace.tools.length})`],
    ["tasks", `Tasks and events (${trace.tasks.length + trace.events.length})`],
  ];
  return (
    <>
      <div className="grid grid-cols-3 gap-2 border-b border-line p-4 sm:grid-cols-5">
        <Stat
          label="Duration"
          value={formatDuration(trace.session.durationMs)}
          note={`${t.turns} turn${t.turns === 1 ? "" : "s"}`}
        />
        <Stat
          label="Model calls"
          value={String(t.llmCalls)}
          note={`${formatDuration(t.llmMs)} model time${t.llmMs > trace.session.durationMs ? ", all agents" : ""}`}
        />
        <Stat
          label="Tool calls"
          value={String(t.toolCalls)}
          note={
            [
              t.toolErrors && `${t.toolErrors} failed`,
              t.blocked && `${t.blocked} blocked`,
            ]
              .filter(Boolean)
              .join(" · ") || "none failed"
          }
        />
        <Stat
          label="Tokens"
          value={tokens ? formatTokens(tokens) : "–"}
          note={
            tokens
              ? `${formatTokens(t.tokens.output_tokens)} out · ${formatTokens(t.tokens.cache_read_tokens)} cached`
              : "not recorded"
          }
        />
        <Stat
          label="Subagents"
          value={String(t.subagents)}
          note={
            t.compactions ? `${t.compactions} compactions` : "no compactions"
          }
        />
      </div>
      <div
        role="tablist"
        aria-label="Trace views"
        className="flex gap-1 overflow-x-auto border-b border-line px-4"
      >
        {tabs.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`trace-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`trace-panel-${id}`}
            onClick={() => setTab(id)}
            className={`shrink-0 border-b-2 px-2 py-2 text-sm whitespace-nowrap transition-colors hover:text-foreground hover:transition-none ${tab === id ? "border-accent text-foreground" : "border-transparent text-muted"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`trace-panel-${tab}`}
        aria-labelledby={`trace-tab-${tab}`}
        className="flex min-h-0 flex-1 flex-col"
      >
        {tab === "timeline" && <Timeline trace={trace} />}
        {tab === "tools" && <Tools trace={trace} />}
        {tab === "tasks" && <Tasks trace={trace} />}
      </div>
      {(trace.truncated?.length || trace.session.basis === "hooks") && (
        <p className="border-t border-line px-4 py-2 text-xs text-muted">
          {trace.session.basis === "hooks" &&
            "Built from hook events only: no model calls or token counts. "}
          {trace.truncated?.join(" · ")}
        </p>
      )}
    </>
  );
}

export function TraceViewer({
  url,
  title,
  onClose,
}: {
  url: string;
  title: string;
  onClose: () => void;
}) {
  const [trace, setTrace] = useState<HojaTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("timeline");
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let live = true;
    fetchTrace(url, storedApiToken())
      .then((t) => live && setTrace(t))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [url]);
  useEffect(() => {
    closeRef.current?.focus();
    // Capture phase: the issue page under the viewer also handles Escape (it leaves the issue), and the viewer is on
    // top, so it takes the key first and keeps it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const s = trace?.session;
  const meta = s
    ? [
        [
          HOST_NAMES[trace.source.host] || trace.source.host,
          trace.source.hostVersion,
        ]
          .filter(Boolean)
          .join(" "),
        s.models.join(", "),
        s.gitBranch,
        new Date(s.start).toLocaleString(),
      ].filter(Boolean)
    : [];
  return createPortal(
    <div
      className="fixed inset-0 z-100 flex items-stretch justify-center bg-black/40 p-2 sm:p-8"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Session trace: ${s?.title || title}`}
        data-trace-viewer=""
        className="flex min-h-0 w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-line bg-panel text-foreground shadow-xl"
      >
        <header className="flex items-start gap-3 border-b border-line p-4">
          <Activity className="mt-1 size-5 shrink-0 text-accent" aria-hidden />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h2 className="truncate font-semibold">
              {s?.title || "Session trace"}
            </h2>
            <p className="truncate text-sm text-muted">
              {meta.length ? meta.join(" · ") : title}
            </p>
          </div>
          <a
            href={url}
            download={title}
            className="flex h-8 shrink-0 items-center gap-1 rounded-md border border-line px-2 text-sm transition-colors hover:bg-subtle hover:transition-none"
          >
            <Download className="size-4" aria-hidden />
            <span className="hidden sm:inline">JSON</span>
            <span className="sr-only sm:hidden">Download JSON</span>
          </a>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close trace"
            className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-subtle hover:transition-none"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>
        {trace ? (
          <Body trace={trace} tab={tab} setTab={setTab} />
        ) : (
          <div
            className="flex flex-1 items-center justify-center p-4 text-sm text-muted"
            role="status"
          >
            {error || "Loading trace…"}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
