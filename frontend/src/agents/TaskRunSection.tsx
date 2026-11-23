/* The run behind one task, on the task page (HOJA-712).
 *
 * Before this section the task page said nothing about an agent run: delegating a task opened an
 * `agent_session` row and the only window onto it was the run queue or the agent's own page. The
 * page a reader already has open now answers "who picked this up, where is it, and what did they
 * say" with one read — `agentSessions(issueId)` (agent_sessions.py), the server's per-task query.
 *
 * Two rules this file keeps:
 *
 *   - nothing is invented. A run with no verdict says nothing about a verdict; a queued run shows
 *     the schedule check's own `queue_reason` words (`queueReasonText`), and a field the server
 *     has not filled is simply absent from the row;
 *   - the section exists only when the task has been delegated or has runs. An undelegated task
 *     draws no Run heading at all, so the page does not carry a permanent empty shelf.
 *
 * It refreshes the way the chat's delegation card does, with one difference that matters: a run in
 * `verifying` is settled *for now* (nobody in this browser can move it), so it re-reads on a slow
 * cadence instead of a five-second loop. When the verifier's verdict lands, the same read picks it
 * up.
 */

import { useEffect, useState } from "react";
import { relTime } from "../main";
import {
  getAgentsAdapter,
  queueReasonText,
  sessionLabel,
  sessionPolling,
} from "./adapter";
import { SESSION_TYPE, verdictColor, verdictLabel } from "./types";
import type { AgentSession } from "./types";

const POLL_MS = 5000;
/* A run waiting on a verifier changes when a human acts, not on any clock this page can see. */
const SLOW_MS = 30000;

export function TaskRunSection({
  issueId,
  delegated,
  refresh,
}: {
  /** `Issue.id`: the value `agentSessions(issueId)` takes. */
  issueId: string;
  /** `Issue.delegateId` is set, so the task is assigned to an agent even before a run starts. */
  delegated: boolean;
  /** Bumped by the page after a delegate write lands. The write opens the run on the server, so
   * this is how the section sees the run it was mounted too early to read. */
  refresh?: number;
}) {
  const [runs, setRuns] = useState<AgentSession[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () => {
      getAgentsAdapter()
        .listIssueSessions(issueId)
        .then((rows) => {
          if (cancelled) return;
          setRuns(rows);
          setError("");
          /* Re-read while something can still change: a five-second loop while a run is moving,
           * a slow one while a verifier has it, and nothing once every run is settled. A delegated
           * task with no rows yet keeps the fast cadence: the delegate write that mounts this
           * section races the session the server opens for it, so the first read can land before
           * the run exists. */
          const waiting = rows.map((r) => sessionPolling(r.state));
          const moving = waiting.some((p) => p === "moving");
          const verifying = waiting.some((p) => p === "verifying");
          if (!rows.length && delegated) timer = setTimeout(read, POLL_MS);
          else if (moving) timer = setTimeout(read, POLL_MS);
          else if (verifying) timer = setTimeout(read, SLOW_MS);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setError(e.message);
          setRuns([]);
        });
    };
    read();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [issueId, delegated, refresh]);

  const loaded = runs || [];
  if (!delegated && !loaded.length && !error) return null;

  return (
    <section
      className="mt-1.5 border-t border-line pt-4"
      aria-label="Run"
      data-task-runs=""
      data-task-runs-count={loaded.length}
    >
      <header className="mb-3 flex items-center gap-2">
        <b className="text-base font-semibold">Run</b>
        <span className="text-xs font-medium text-muted">
          {loaded.length
            ? `${loaded.length} run${loaded.length === 1 ? "" : "s"}`
            : "not started"}
        </span>
      </header>
      {error && (
        <p className="text-sm text-danger" role="alert" data-run-error="">
          {error}
        </p>
      )}
      {!error && runs === null && (
        <p className="text-sm text-muted" role="status">
          Reading the run…
        </p>
      )}
      {!error && runs !== null && !loaded.length && (
        <p className="text-sm text-muted" data-run-empty="">
          Delegated, but no run has started yet.
        </p>
      )}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {loaded.map((run) => (
          <RunRow key={run.id} run={run} />
        ))}
      </ul>
    </section>
  );
}

function RunRow({ run }: { run: AgentSession }) {
  const tone = SESSION_TYPE[run.status];
  const label = sessionLabel(run.state, run.status);
  /* A run that has settled keeps the reason the schedule check wrote when it queued it; reading
   * that beside "Ended" would claim the run is still waiting for capacity. */
  const queued =
    sessionPolling(run.state) === "settled"
      ? ""
      : queueReasonText(run.queueReason);
  /* The server's own clock: when the run last heard from anything, which is what "how long has it
   * been like this" reads from. `endedAt` wins because it is the run's last word. */
  const at = run.endedAt || run.claimAt || run.startedAt || null;
  return (
    <li
      className="flex flex-wrap items-center gap-2 text-sm"
      data-run-session={run.id}
      data-run-state={run.state}
    >
      <span
        className="inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-full border border-line bg-panel px-2 text-xs font-medium"
        data-run-chip={run.status}
        title={`${run.agentName} · ${label} · ${run.state}`}
      >
        <i
          className="size-2 shrink-0 rounded-full not-italic"
          style={{ background: tone.color }}
        />
        {label}
      </span>
      <span className="min-w-0 truncate text-muted" data-run-agent="">
        {run.agentName}
      </span>
      {run.verdict && (
        <span
          className="inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-full border border-line bg-panel px-2 text-xs font-medium"
          data-run-verdict={run.verdict}
        >
          <i
            className="size-2 shrink-0 rounded-full not-italic"
            style={{ background: verdictColor(run.verdict) }}
          />
          {verdictLabel(run.verdict)}
        </span>
      )}
      {queued && <span className="min-w-0 text-muted">Queued: {queued}</span>}
      {at && (
        <span
          className="ml-auto shrink-0 text-xs text-muted"
          data-run-at={run.endedAt ? "ended" : "started"}
        >
          {run.endedAt ? "Ended" : "Started"} {relTime(at)}
        </span>
      )}
    </li>
  );
}
