import { useEffect, useState } from "react";
import { ChevronDown, Flame } from "lucide-react";
import {
  fetchTaskUsage,
  formatDuration,
  formatTokens,
  type TaskUsageSummary,
} from "./api";

/** Compact per-task token & runtime breakdown rendered inside the task detail sidebar. */
export function TaskUsageSection({ issueId }: { issueId: string }) {
  const [usage, setUsage] = useState<TaskUsageSummary | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    setUsage(null);
    if (!issueId) return;
    fetchTaskUsage(issueId)
      .then((res) => {
        if (live) setUsage(res);
      })
      .catch(() => {
        /* Usage metrics are optional on the task detail sidebar. */
      });
    return () => {
      live = false;
    };
  }, [issueId]);

  if (!usage || (usage.tokens === 0 && usage.runs === 0)) return null;

  return (
    <div className="mt-4 flex flex-col gap-1.5" data-task-usage="">
      <div className="text-xs font-semibold text-muted">Agent usage</div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-line bg-bg px-2.5 py-2 text-left text-xs hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          <Flame size={13} className="text-muted" aria-hidden="true" />
          <span className="tabular-nums">{formatTokens(usage.tokens)}</span>
          <span className="font-normal text-muted">tokens</span>
        </span>
        <span className="flex items-center gap-1.5 tabular-nums text-muted">
          <span>
            {formatDuration(usage.runMinutes)} · {usage.runs}{" "}
            {usage.runs === 1 ? "run" : "runs"}
          </span>
          <ChevronDown
            size={13}
            aria-hidden="true"
            className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`}
          />
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-line bg-panel p-2.5 text-xs text-muted">
          <div className="flex justify-between tabular-nums">
            <span>Input / Output / Cache</span>
            <span className="text-foreground">
              {formatTokens(usage.inputTokens)} /{" "}
              {formatTokens(usage.outputTokens)} /{" "}
              {formatTokens(usage.cacheReadTokens)}
            </span>
          </div>
          {usage.breakdown.map((row) => (
            <div
              key={`${row.agentUserId}:${row.model}`}
              className="flex items-center justify-between gap-2 border-t border-line pt-1.5"
            >
              <span className="min-w-0 truncate text-foreground">
                {row.agentName} ·{" "}
                <span className="text-muted">{row.model}</span>
              </span>
              <span className="shrink-0 tabular-nums text-foreground">
                {formatTokens(row.tokens)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
