import type { ToolCallRecord } from "./types";

export type ToolIconKind =
  | "database"
  | "schema"
  | "connector"
  | "wrench";

export interface ToolDisplayMeta {
  icon: ToolIconKind;
  runningLabel: string;
  doneLabel: string;
  preview: string | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function humanizeToolName(name: string): string {
  return name
    .replace(/^[a-z0-9-]+__/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseGraphqlSummary(query: string): {
  isMutation: boolean;
  field: string | null;
} {
  const cleaned = query.replace(/#[^\n]*/g, "").trim();
  const isMutation = /^mutation\b/i.test(cleaned);
  // Find the first selection inside the root `{ ... }` block.
  const braceIdx = cleaned.indexOf("{");
  if (braceIdx === -1) return { isMutation, field: null };
  const body = cleaned.slice(braceIdx + 1);
  const match = body.match(/^\s*(?:[a-zA-Z_][a-zA-Z0-9_]*\s*:\s*)?([a-zA-Z_][a-zA-Z0-9_]*)/);
  return { isMutation, field: match ? match[1] : null };
}

export function describeToolCall(tc: ToolCallRecord): ToolDisplayMeta {
  const args = asRecord(tc.args);

  if (tc.name === "schema") {
    return {
      icon: "schema",
      runningLabel: "Reading GraphQL schema",
      doneLabel: "Read GraphQL schema",
      preview: null,
    };
  }

  if (tc.name === "graphql") {
    const query = typeof args?.query === "string" ? args.query : "";
    const { isMutation, field } = parseGraphqlSummary(query);
    return {
      icon: "database",
      runningLabel: isMutation ? "Updating workspace" : "Querying workspace",
      doneLabel: isMutation ? "Updated workspace" : "Queried workspace",
      preview: field,
    };
  }

  const label = humanizeToolName(tc.name) || tc.name;
  let preview: string | null = null;
  if (args) {
    for (const v of Object.values(args)) {
      if (typeof v === "string" && v.trim()) {
        const oneLine = v.trim().replace(/\s+/g, " ");
        preview = oneLine.length > 42 ? `${oneLine.slice(0, 42)}…` : oneLine;
        break;
      }
    }
  }
  const isConnector = tc.name.includes("__") || tc.name.includes(":");
  return {
    icon: isConnector ? "connector" : "wrench",
    runningLabel: `Running ${label}`,
    doneLabel: label.charAt(0).toUpperCase() + label.slice(1),
    preview,
  };
}

export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  return rs ? `${m}m ${rs}s` : `${m}m`;
}

export function summarizeToolGroup(calls: ToolCallRecord[]): {
  state: "running" | "completed" | "error";
  headline: string;
  secondary: string | null;
  totalMs: number | null;
  errorCount: number;
  runningCount: number;
} {
  const runningCalls = calls.filter((c) => c.status === "running");
  const errorCalls = calls.filter((c) => c.status === "error");
  const runningCount = runningCalls.length;
  const errorCount = errorCalls.length;
  const state =
    runningCount > 0 ? "running" : errorCount > 0 ? "error" : "completed";

  const durations = calls
    .map((c) => c.durationMs)
    .filter((d): d is number => typeof d === "number" && Number.isFinite(d));
  const totalMs =
    durations.length > 0 ? durations.reduce((sum, d) => sum + d, 0) : null;

  if (calls.length === 1) {
    const meta = describeToolCall(calls[0]);
    return {
      state,
      headline: state === "running" ? `${meta.runningLabel}…` : meta.doneLabel,
      secondary: meta.preview,
      totalMs,
      errorCount,
      runningCount,
    };
  }

  if (runningCount > 0) {
    const active = describeToolCall(runningCalls[runningCalls.length - 1]);
    return {
      state,
      headline: `${active.runningLabel}…`,
      secondary: active.preview,
      totalMs,
      errorCount,
      runningCount,
    };
  }

  const names = calls.map((c) => describeToolCall(c).preview || c.name);
  const unique = [...new Set(names)].slice(0, 3);
  return {
    state,
    headline: `Used ${calls.length} tools`,
    secondary: unique.length ? unique.join(", ") : null,
    totalMs,
    errorCount,
    runningCount,
  };
}

export function formatToolInput(tc: ToolCallRecord): {
  query?: string;
  variables?: string;
  raw?: string;
} | null {
  if (tc.args == null) return null;
  const args = asRecord(tc.args);
  if (tc.name === "graphql" && args && typeof args.query === "string") {
    const vars = args.variables;
    const hasVars =
      vars &&
      typeof vars === "object" &&
      Object.keys(vars as Record<string, unknown>).length > 0;
    return {
      query: args.query.trim(),
      ...(hasVars ? { variables: JSON.stringify(vars, null, 2) } : {}),
    };
  }
  if (typeof tc.args === "string") {
    return tc.args.trim() ? { raw: tc.args } : null;
  }
  if (args && Object.keys(args).length === 0) return null;
  try {
    return { raw: JSON.stringify(tc.args, null, 2) };
  } catch {
    return { raw: String(tc.args) };
  }
}

export function formatToolOutput(output: string | null | undefined): string | null {
  if (!output) return null;
  const trimmed = output.trim();
  if (!trimmed) return null;
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return trimmed;
    }
  }
  return trimmed;
}
