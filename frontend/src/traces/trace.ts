/* Agent session traces (`hoja.trace/v1`) attached to issues by the Hoja plugins for Claude Code and Codex.
 *
 * A trace is one session as a flat list of spans (session → turn → model call / tool call → subagent → …) plus
 * instant events (gate blocks, compactions, task updates). The plugin uploads it through /api/uploads and attaches
 * it with a `.trace.json` title; this module recognises such an attachment, validates the document, and shapes it
 * for TraceViewer. Nothing here trusts the file: every field is optional and unknown kinds are ignored. */

export const TRACE_SCHEMA = "hoja.trace/v1";

export type SpanKind = "session" | "turn" | "llm" | "tool" | "subagent";
export type SpanStatus = "ok" | "error" | "blocked" | "interrupted" | "running";
export type TokenUsage = {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
};
export type TraceSpan = {
  id: string;
  parentId?: string;
  kind: SpanKind;
  name: string;
  start: string;
  end?: string;
  durationMs?: number;
  status: SpanStatus;
  agentId?: string;
  attrs?: Record<string, unknown>;
};
export type TraceEvent = {
  at: string;
  kind: string;
  message: string;
  spanId?: string;
};
export type TraceTask = {
  id: string;
  subject: string;
  status: string;
  parentId?: string;
  issue?: string;
  questions?: { id: string; text: string; answered: boolean }[];
};
export type ToolStat = {
  name: string;
  calls: number;
  errors: number;
  blocked: number;
  totalMs: number;
};
export type HojaTrace = {
  schema: string;
  id: string;
  source: {
    host: string;
    hostVersion?: string;
    plugin?: string;
    detail?: string;
  };
  session: {
    id: string;
    title?: string;
    cwd?: string;
    gitBranch?: string;
    models: string[];
    start: string;
    end: string;
    durationMs: number;
    basis?: "transcript" | "hooks";
  };
  totals: {
    turns: number;
    llmCalls: number;
    toolCalls: number;
    toolErrors: number;
    blocked: number;
    subagents: number;
    compactions: number;
    tokens: TokenUsage;
    tokensByModel: Record<string, TokenUsage>;
    llmMs: number;
    toolMs: number;
    filesTouched: string[];
  };
  tools: ToolStat[];
  tasks: TraceTask[];
  issues: string[];
  spans: TraceSpan[];
  events: TraceEvent[];
  truncated?: string[];
};

/* A trace attachment is JSON whose title ends in `.trace.json` (what the plugins name it), or whose subtitle the
 * plugin wrote. The document itself is still validated before it is drawn. */
export function isTraceAttachment(a: {
  url?: string | null;
  title?: string | null;
  subtitle?: string | null;
  contentType?: string | null;
}): boolean {
  const type = (a.contentType || "").split(";")[0].trim().toLowerCase();
  const path = (a.url || "").split(/[?#]/)[0];
  const json = type ? type === "application/json" : /\.json$/i.test(path);
  return (
    json &&
    (/\.trace\.json$/i.test(a.title || "") ||
      /^(Claude Code|Codex|Antigravity) trace\b/.test(a.subtitle || ""))
  );
}

const num = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) ? v : 0;
const usage = (u: unknown): TokenUsage => {
  const o = (u && typeof u === "object" ? u : {}) as Record<string, unknown>;
  return {
    input_tokens: num(o.input_tokens),
    output_tokens: num(o.output_tokens),
    cache_read_tokens: num(o.cache_read_tokens),
    cache_creation_tokens: num(o.cache_creation_tokens),
  };
};

/* Parse and normalise; throws with a message a person can act on. */
export function parseTrace(value: unknown): HojaTrace {
  const t = value as Partial<HojaTrace> | null;
  if (!t || typeof t !== "object" || t.schema !== TRACE_SCHEMA)
    throw new Error("This file is not a hoja.trace/v1 session trace.");
  if (!Array.isArray(t.spans) || !t.session)
    throw new Error("The trace has no spans.");
  const spans = t.spans.filter(
    (s): s is TraceSpan =>
      !!s && typeof s.id === "string" && typeof s.kind === "string",
  );
  const totals = (t.totals || {}) as Partial<HojaTrace["totals"]>;
  return {
    ...(t as HojaTrace),
    spans,
    events: Array.isArray(t.events) ? t.events : [],
    tasks: Array.isArray(t.tasks) ? t.tasks : [],
    tools: Array.isArray(t.tools) ? t.tools : [],
    issues: Array.isArray(t.issues) ? t.issues : [],
    session: {
      ...t.session,
      models: Array.isArray(t.session.models) ? t.session.models : [],
    },
    totals: {
      turns: num(totals.turns),
      llmCalls: num(totals.llmCalls),
      toolCalls: num(totals.toolCalls),
      toolErrors: num(totals.toolErrors),
      blocked: num(totals.blocked),
      subagents: num(totals.subagents),
      compactions: num(totals.compactions),
      tokens: usage(totals.tokens),
      tokensByModel: Object.fromEntries(
        Object.entries(totals.tokensByModel || {}).map(([k, v]) => [
          k,
          usage(v),
        ]),
      ),
      llmMs: num(totals.llmMs),
      toolMs: num(totals.toolMs),
      filesTouched: Array.isArray(totals.filesTouched)
        ? totals.filesTouched
        : [],
    },
  };
}

/* Signed in with a cookie, the cookie authorises the read; signed in with an API key, the key does (as for gql). */
export async function fetchTrace(
  url: string,
  apiToken?: string | null,
): Promise<HojaTrace> {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: apiToken ? { authorization: `Bearer ${apiToken}` } : {},
  });
  if (!res.ok)
    throw new Error(`Could not load the trace (HTTP ${res.status}).`);
  return parseTrace(await res.json());
}

export const totalTokens = (u: TokenUsage) =>
  u.input_tokens +
  u.output_tokens +
  u.cache_read_tokens +
  u.cache_creation_tokens;

export function formatDuration(ms: number | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "–";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
}

export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 2 : 1)}M`;
}

/* Offsets are what a waterfall reads: "+3m 4s" from the start of the session. */
export const offsetLabel = (ms: number) =>
  `+${formatDuration(Math.max(0, ms))}`;

export type TraceRow = {
  span: TraceSpan;
  depth: number;
  children: number;
  startMs: number;
  durationMs: number;
};

/* Depth-first rows under the session span, children in start order, skipping collapsed subtrees. */
export function traceRows(
  trace: HojaTrace,
  collapsed: ReadonlySet<string>,
): TraceRow[] {
  const t0 = Date.parse(trace.session.start);
  const byParent = new Map<string, TraceSpan[]>();
  const ids = new Set(trace.spans.map((s) => s.id));
  for (const s of trace.spans) {
    if (s.kind === "session") continue;
    const parent = s.parentId && ids.has(s.parentId) ? s.parentId : "session";
    byParent.set(parent, [...(byParent.get(parent) || []), s]);
  }
  for (const list of byParent.values())
    list.sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const rows: TraceRow[] = [];
  const visit = (parent: string, depth: number) => {
    for (const span of byParent.get(parent) || []) {
      const startMs = Math.max(0, (Date.parse(span.start) || t0) - t0);
      const end = span.end ? Date.parse(span.end) - t0 : undefined;
      const durationMs =
        span.durationMs ??
        (end !== undefined && end >= startMs ? end - startMs : 0);
      rows.push({
        span,
        depth,
        children: (byParent.get(span.id) || []).length,
        startMs,
        durationMs,
      });
      if (!collapsed.has(span.id)) visit(span.id, depth + 1);
    }
  };
  visit("session", 0);
  return rows;
}

/* Turns and subagents start collapsed in a long session, so the first view is an outline. */
export function defaultCollapsed(trace: HojaTrace): Set<string> {
  if (trace.spans.length <= 120) return new Set();
  return new Set(
    trace.spans
      .filter((s) => s.kind === "turn" || s.kind === "subagent")
      .map((s) => s.id),
  );
}

export type Mark = {
  startMs: number;
  durationMs: number;
  kind: SpanKind;
  status: SpanStatus;
};

/* Everything under one span, flattened: drawn inside a collapsed row's bar so a folded turn or subagent still shows
 * where the model and tools spent time and where something failed. Failures are kept first when over `limit`. */
export function descendantMarks(
  trace: HojaTrace,
  id: string,
  limit = 400,
): { marks: Mark[]; failed: number; blocked: number } {
  const t0 = Date.parse(trace.session.start);
  const children = new Map<string, TraceSpan[]>();
  for (const s of trace.spans)
    if (s.parentId)
      children.set(s.parentId, [...(children.get(s.parentId) || []), s]);
  const all: Mark[] = [];
  let failed = 0;
  let blocked = 0;
  const stack = [...(children.get(id) || [])];
  while (stack.length) {
    const s = stack.pop()!;
    stack.push(...(children.get(s.id) || []));
    if (s.kind !== "llm" && s.kind !== "tool") continue;
    if (s.status === "error" || s.status === "interrupted") failed++;
    if (s.status === "blocked") blocked++;
    const startMs = Math.max(0, (Date.parse(s.start) || t0) - t0);
    all.push({
      startMs,
      durationMs: s.durationMs || 0,
      kind: s.kind,
      status: s.status,
    });
  }
  const bad = (m: Mark) => m.status !== "ok" && m.status !== "running";
  const marks =
    all.length <= limit
      ? all
      : [...all.filter(bad), ...all.filter((m) => !bad(m))].slice(0, limit);
  return { marks, failed, blocked };
}
