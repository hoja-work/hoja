import { useState } from "react";
import { Collapsible } from "radix-ui";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  AlertCircle,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  Copy,
  Database,
  FileCode2,
  Loader2,
  Plug,
  Wrench,
} from "lucide-react";
import type { ToolCallRecord } from "./types";
import {
  describeToolCall,
  formatDuration,
  formatToolInput,
  formatToolOutput,
  summarizeToolGroup,
  type ToolIconKind,
} from "./toolMeta";

function ToolGlyph({ kind }: { kind: ToolIconKind }) {
  const cls = "size-3.5 shrink-0 text-muted";
  switch (kind) {
    case "database":
      return <Database className={cls} aria-hidden="true" />;
    case "schema":
      return <FileCode2 className={cls} aria-hidden="true" />;
    case "delegate":
      return <Bot className={cls} aria-hidden="true" />;
    case "connector":
      return <Plug className={cls} aria-hidden="true" />;
    default:
      return <Wrench className={cls} aria-hidden="true" />;
  }
}

function StatusGlyph({
  status,
  reduced,
}: {
  status: "running" | "completed" | "error";
  reduced: boolean | null;
}) {
  if (status === "running") {
    return (
      <Loader2
        className={`size-3.5 shrink-0 text-amber-600 dark:text-amber-400 ${reduced ? "" : "animate-spin"}`}
        aria-hidden="true"
      />
    );
  }
  if (status === "error") {
    return (
      <AlertCircle
        className="size-3.5 shrink-0 text-danger"
        aria-hidden="true"
      />
    );
  }
  return (
    <CheckCircle2
      className="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400"
      aria-hidden="true"
    />
  );
}

function CopySnippetButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? `Copied ${label}` : `Copy ${label}`}
      onClick={() => {
        void navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1400);
          })
          .catch(() => {});
      }}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11 pointer-coarse:px-2.5"
    >
      {copied ? (
        <>
          <Check className="size-3 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
          <span>Copied</span>
        </>
      ) : (
        <>
          <Copy className="size-3" aria-hidden="true" />
          <span>Copy</span>
        </>
      )}
    </button>
  );
}

function ToolCallDetails({ call }: { call: ToolCallRecord }) {
  const input = formatToolInput(call);
  const output = formatToolOutput(call.output);

  if (!input && !output && !call.error && call.status === "running") {
    return (
      <p className="m-0 text-xs text-muted">Waiting for tool response…</p>
    );
  }

  if (!input && !output && !call.error) {
    return (
      <p className="m-0 text-xs text-muted">No arguments or output recorded.</p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {input?.query && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted">Query</span>
            <CopySnippetButton text={input.query} label="query" />
          </div>
          <pre className="m-0 max-h-48 overflow-auto rounded-lg border border-line bg-bg p-2.5 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap break-words">
            {input.query}
          </pre>
        </div>
      )}
      {input?.variables && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted">Variables</span>
            <CopySnippetButton text={input.variables} label="variables" />
          </div>
          <pre className="m-0 max-h-40 overflow-auto rounded-lg border border-line bg-bg p-2.5 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap break-words">
            {input.variables}
          </pre>
        </div>
      )}
      {input?.raw && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted">Arguments</span>
            <CopySnippetButton text={input.raw} label="arguments" />
          </div>
          <pre className="m-0 max-h-48 overflow-auto rounded-lg border border-line bg-bg p-2.5 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap break-words">
            {input.raw}
          </pre>
        </div>
      )}
      {call.error && (
        <div
          className="flex flex-col gap-1 rounded-lg border border-danger/30 bg-danger/10 p-2.5"
          role="alert"
        >
          <span className="text-xs font-medium text-danger">Error</span>
          <p className="m-0 font-mono text-xs text-danger whitespace-pre-wrap break-words">
            {call.error}
          </p>
        </div>
      )}
      {output && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted">Result</span>
            <CopySnippetButton text={output} label="result" />
          </div>
          <pre className="m-0 max-h-56 overflow-auto rounded-lg border border-line bg-bg p-2.5 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap break-words">
            {output}
          </pre>
        </div>
      )}
    </div>
  );
}

function ToolCallRow({
  call,
  reduced,
}: {
  call: ToolCallRecord;
  reduced: boolean | null;
}) {
  const [open, setOpen] = useState(false);
  const meta = describeToolCall(call);
  const duration = formatDuration(call.durationMs);

  return (
    <Collapsible.Root
      open={open}
      onOpenChange={setOpen}
      className="rounded-lg border border-line bg-panel overflow-hidden"
      data-tool-call={call.name}
      data-tool-status={call.status}
    >
      <Collapsible.Trigger
        type="button"
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-xs transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
      >
        <StatusGlyph status={call.status} reduced={reduced} />
        <ToolGlyph kind={meta.icon} />
        <span className="shrink-0 font-medium text-foreground">
          {call.status === "running" ? `${meta.runningLabel}…` : meta.doneLabel}
        </span>
        {meta.preview && (
          <code className="min-w-0 truncate rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-muted">
            {meta.preview}
          </code>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {duration && (
            <span className="text-xs text-muted tabular-nums">{duration}</span>
          )}
          <ChevronRight
            className={`size-3.5 text-muted transition-transform duration-150 ${open ? "rotate-90" : ""}`}
            aria-hidden="true"
          />
        </span>
      </Collapsible.Trigger>
      <AnimatePresence initial={false}>
        {open && (
          <Collapsible.Content forceMount asChild>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: reduced ? 0 : 0.14 }}
              className="border-t border-line bg-subtle/40 p-3"
            >
              <ToolCallDetails call={call} />
            </motion.div>
          </Collapsible.Content>
        )}
      </AnimatePresence>
    </Collapsible.Root>
  );
}

/**
 * Collapsible tool-call trace container.
 * Always starts collapsed so the conversation stays clean and readable, while surfacing
 * live progress, tool counts, errors, and total duration on a single scannable bar.
 */
export function ToolCallGroup({ calls }: { calls: ToolCallRecord[] }) {
  const [open, setOpen] = useState(false);
  const reduced = useReducedMotion();

  if (calls.length === 0) return null;

  const summary = summarizeToolGroup(calls);
  const totalDuration = formatDuration(summary.totalMs);
  const single = calls.length === 1 ? calls[0] : null;
  const singleMeta = single ? describeToolCall(single) : null;

  return (
    <Collapsible.Root
      open={open}
      onOpenChange={setOpen}
      className="my-1 w-full rounded-xl border border-line bg-panel overflow-hidden"
      data-tool-group=""
      data-tool-group-state={summary.state}
      data-tool-count={calls.length}
    >
      <Collapsible.Trigger
        type="button"
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-sm transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
        data-tool-group-trigger=""
      >
        <StatusGlyph status={summary.state} reduced={reduced} />
        {singleMeta ? (
          <ToolGlyph kind={singleMeta.icon} />
        ) : (
          <Wrench className="size-3.5 shrink-0 text-muted" aria-hidden="true" />
        )}
        <span className="shrink-0 text-xs font-medium text-foreground">
          {summary.headline}
        </span>
        {summary.secondary && (
          <span className="hidden min-w-0 truncate rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-muted sm:inline-block">
            {summary.secondary}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {calls.length > 1 && summary.runningCount > 0 && (
            <span className="rounded-full bg-subtle px-2 py-0.5 text-xs text-muted tabular-nums">
              {calls.length - summary.runningCount}/{calls.length}
            </span>
          )}
          {summary.errorCount > 0 && (
            <span className="rounded-full bg-danger/10 px-2 py-0.5 text-xs text-danger tabular-nums">
              {summary.errorCount} failed
            </span>
          )}
          {totalDuration && (
            <span className="text-xs text-muted tabular-nums">
              {totalDuration}
            </span>
          )}
          <ChevronRight
            className={`size-4 text-muted transition-transform duration-150 ${open ? "rotate-90" : ""}`}
            aria-hidden="true"
          />
        </span>
      </Collapsible.Trigger>

      <AnimatePresence initial={false}>
        {open && (
          <Collapsible.Content forceMount asChild>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: reduced ? 0 : 0.14 }}
              className="border-t border-line bg-bg/60 p-3"
              data-tool-group-content=""
            >
              {single ? (
                <ToolCallDetails call={single} />
              ) : (
                <div className="flex flex-col gap-2 border-l border-line pl-3">
                  {calls.map((call) => (
                    <ToolCallRow key={call.id} call={call} reduced={reduced} />
                  ))}
                </div>
              )}
            </motion.div>
          </Collapsible.Content>
        )}
      </AnimatePresence>
    </Collapsible.Root>
  );
}
