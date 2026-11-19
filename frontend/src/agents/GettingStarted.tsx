/* The Agents home before anything works: connect a machine, then create an agent (HOJA-602).
 *
 * Machine first, because nothing an agent is told to do can run until a machine is there to run
 * it. Step 2 stays locked until a machine has connected, so the order is not a suggestion. Once a
 * workspace has both, this disappears; with agents but no machine, a one-line banner says why
 * nothing is running and opens the same "connect a machine" panel. */
import { Check, Lock, Monitor, Plus } from "lucide-react";
import { useEffect, useState } from "react";

import { getAgentsAdapter } from "./adapter";
import { ConnectMachine, harnessNames } from "./ConnectMachine";
import type { Runner } from "./types";

function Badge({
  n,
  done,
  locked,
}: {
  n: number;
  done: boolean;
  locked?: boolean;
}) {
  if (done)
    return (
      <span className="flex size-7 flex-none items-center justify-center rounded-full bg-emerald-600 text-white">
        <Check size={14} strokeWidth={3} aria-hidden="true" />
      </span>
    );
  return (
    <span
      className={`flex size-7 flex-none items-center justify-center rounded-full border-2 text-sm font-semibold ${locked ? "border-line text-muted" : "border-foreground text-foreground"}`}
    >
      {locked ? <Lock size={12} aria-hidden="true" /> : n}
    </span>
  );
}

export function GettingStarted({
  agentCount,
  onCreateAgent,
}: {
  agentCount: number;
  onCreateAgent: () => void;
}) {
  const [runners, setRunners] = useState<Runner[] | null>(null);
  const [expanded, setExpanded] = useState(false);
  /* The machine that connected while this page was open: its success card stays on screen
   * (the same ConnectMachine, not swapped for the list) so the person sees what happened. */
  const [justConnected, setJustConnected] = useState(false);
  useEffect(() => {
    getAgentsAdapter()
      .listRunners()
      .then((page) => setRunners(page.runners.filter((r) => !r.revoked)))
      .catch(() => setRunners([]));
  }, []);
  if (runners === null) return null;
  const machineDone = runners.length > 0;
  const agentDone = agentCount > 0;
  if (machineDone && agentDone) return null;

  const onConnected = (runner: Runner) => {
    setJustConnected(true);
    setRunners((current) => [
      ...(current ?? []).filter((r) => r.id !== runner.id),
      runner,
    ]);
  };

  if (agentDone) {
    return (
      <section
        className="m-4 rounded-lg border border-line p-4"
        aria-label="Connect a machine"
        data-getting-started="machine-banner"
      >
        <div className="flex flex-wrap items-center gap-3">
          <Monitor size={18} className="text-muted" aria-hidden="true" />
          <span className="flex-1 text-sm">
            No machine is connected, so your agents cannot run yet.
          </span>
          {!expanded && (
            <button
              type="button"
              className="rounded-md bg-foreground px-3 py-1.5 text-sm font-medium text-panel"
              onClick={() => setExpanded(true)}
            >
              Connect a machine
            </button>
          )}
        </div>
        {expanded && (
          <div className="mt-4">
            <ConnectMachine autoStart onConnected={onConnected} />
          </div>
        )}
      </section>
    );
  }

  return (
    <section
      className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-4 py-10"
      aria-label="Get started with agents"
      data-getting-started="steps"
    >
      <div className="flex flex-col gap-1">
        <h2 className="m-0 text-lg font-semibold">Get started with agents</h2>
        <p className="m-0 text-sm text-muted">
          Agents take the tasks you delegate and do them on your own machines.
          Connect one, then create an agent to run on it.
        </p>
      </div>

      <ol className="m-0 flex list-none flex-col gap-6 p-0">
        <li className="flex gap-4" data-step="machine" data-done={machineDone}>
          <Badge n={1} done={machineDone} />
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div>
              <h3 className="m-0 text-base font-medium">Connect a machine</h3>
              <p className="m-0 text-sm text-muted">
                A laptop, server or CI box. Pick it from your tailnet, or run
                one command on it.
              </p>
            </div>
            {machineDone && !justConnected ? (
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {runners.map((r) => (
                  <li key={r.id} className="flex items-center gap-2 text-sm">
                    <span
                      className={`size-2 rounded-full ${r.status === "online" ? "bg-emerald-600" : "bg-line"}`}
                      aria-hidden="true"
                    />
                    {r.name}
                    <span className="text-xs text-muted">
                      {r.status === "online" ? harnessNames(r) : "offline"}
                      {r.workRoot ? ` · works in ${r.workRoot}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <ConnectMachine onConnected={onConnected} />
            )}
          </div>
        </li>

        <li className="flex gap-4" data-step="agent" data-done={agentDone}>
          <Badge n={2} done={agentDone} locked={!machineDone} />
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div>
              <h3
                className={`m-0 text-base font-medium ${machineDone ? "" : "text-muted"}`}
              >
                Create your first agent
              </h3>
              <p className="m-0 text-sm text-muted">
                {machineDone
                  ? "Give it a name; the next screen picks its team, model and key."
                  : "Unlocks when a machine is connected."}
              </p>
            </div>
            <div>
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-md bg-foreground px-4 py-2 text-sm font-medium text-panel disabled:bg-subtle disabled:text-muted shell-phone:min-h-11 pointer-coarse:min-h-11"
                disabled={!machineDone}
                onClick={onCreateAgent}
              >
                <Plus size={16} aria-hidden="true" /> New agent
              </button>
            </div>
          </div>
        </li>
      </ol>
    </section>
  );
}
