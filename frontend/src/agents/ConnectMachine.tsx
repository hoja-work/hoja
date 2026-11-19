/* Connect a machine: one line in a terminal, and the machine shows up here (HOJA-602).
 *
 * Machine first, the way a device joins a tailnet: the person copies one command, runs it on a
 * laptop, server or CI box, and watches the machine appear. The command installs the runner into
 * ~/.hoja/runner, connects it with a single-use code, and keeps it running as a service
 * (app/hoja_server/runner_release.py `install_script`). Nothing listens on the machine; it only
 * dials out to Hoja.
 *
 * "Connected" is a fact read from the server: a runner that was not in the list when the command
 * was minted, now reporting online. */
import {
  Check,
  Copy,
  Loader2,
  Monitor,
  Network,
  RefreshCw,
  Terminal,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { getAgentsAdapter } from "./adapter";
import { TailnetConnect } from "./TailnetConnect";
import type { Runner, RunnerEnrollmentGrant, Tailnet } from "./types";

const POLL_MS = 2000;

const CLI_NAMES: Record<string, string> = {
  "hoja-coder": "Hoja Coder",
  "claude-code": "Claude Code",
  codex: "Codex",
  gemini: "Gemini CLI",
  opencode: "OpenCode",
  pi: "pi",
};

export function harnessNames(runner: Runner): string {
  const named = runner.harnesses
    .filter((h) => CLI_NAMES[h])
    .map((h) => CLI_NAMES[h]);
  return named.join(", ") || "no known harness";
}

function CommandBox({ text, big = false }: { text: string; big?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-line bg-subtle p-3">
      <code
        className={`min-w-0 flex-1 break-all ${big ? "text-sm" : "text-xs"}`}
        data-connect-command={big ? "" : undefined}
      >
        {text}
      </code>
      <button
        type="button"
        className="inline-flex flex-none items-center gap-1 rounded-md border border-line bg-panel px-2 py-1 text-xs text-foreground hover:bg-subtle shell-phone:min-h-11 pointer-coarse:min-h-11"
        onClick={() => {
          void navigator.clipboard?.writeText(text).catch(() => undefined);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        }}
      >
        {copied ? (
          <Check size={14} aria-hidden="true" />
        ) : (
          <Copy size={14} aria-hidden="true" />
        )}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/* Two ways to connect: from the workspace's tailnet (no terminal, TailnetConnect.tsx), or the
 * one-line command. Tailscale is the default once the workspace has connected it. */
export function ConnectMachine(props: {
  onConnected?: (runner: Runner) => void;
  autoStart?: boolean;
  harness?: string;
}) {
  const [tailnet, setTailnet] = useState<Tailnet | null | undefined>(undefined);
  const [tab, setTab] = useState<"tailscale" | "command" | null>(null);
  useEffect(() => {
    let live = true;
    getAgentsAdapter()
      .getTailnet()
      .then((t) => live && setTailnet(t))
      .catch(() => live && setTailnet(null)); // not an admin, or no server: the command only
    return () => {
      live = false;
    };
  }, []);
  if (tailnet === undefined) {
    return <Loader2 size={16} className="animate-spin text-muted" />;
  }
  if (tailnet === null) return <CommandConnect {...props} />;
  const shown = tab ?? (tailnet.connected ? "tailscale" : "command");
  const tabClass = (on: boolean) =>
    `inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium ${on ? "bg-panel text-foreground shadow-sm" : "text-muted hover:text-foreground"}`;
  return (
    <div className="flex flex-col gap-4" data-connect-tabs={shown}>
      <div
        className="inline-flex w-fit gap-1 rounded-lg bg-subtle p-1"
        role="tablist"
      >
        <button
          type="button"
          role="tab"
          aria-selected={shown === "tailscale"}
          className={tabClass(shown === "tailscale")}
          onClick={() => setTab("tailscale")}
        >
          <Network size={14} aria-hidden="true" /> From Tailscale
          {!tailnet.connected && (
            <span className="rounded-full bg-emerald-600 px-1.5 text-[10px] text-white">
              No terminal
            </span>
          )}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={shown === "command"}
          className={tabClass(shown === "command")}
          onClick={() => setTab("command")}
        >
          <Terminal size={14} aria-hidden="true" /> With a command
        </button>
      </div>
      {shown === "tailscale" ? (
        <TailnetConnect initial={tailnet} onConnected={props.onConnected} />
      ) : (
        <CommandConnect {...props} />
      )}
    </div>
  );
}

function CommandConnect({
  onConnected,
  autoStart = false,
  harness,
}: {
  /** Called once with the machine that just came online. */
  onConnected?: (runner: Runner) => void;
  /** Mint the command immediately instead of waiting for a click. */
  autoStart?: boolean;
  /** The harness the agent needs, to say whether the new machine can run it. */
  harness?: string;
}) {
  const adapter = getAgentsAdapter();
  const [grant, setGrant] = useState<RunnerEnrollmentGrant | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState<Runner | null>(null);
  /* Where agents work on the machine: the standard layout, or a folder already in use. */
  const [where, setWhere] = useState<"standard" | "custom">("standard");
  const [folder, setFolder] = useState("");
  const workRoot = where === "custom" ? folder.trim() : "";
  const known = useRef<Set<string> | null>(null);
  const reported = useRef(false);

  const mint = useCallback(async () => {
    setBusy(true);
    setError("");
    setConnected(null);
    reported.current = false;
    try {
      const page = await adapter.listRunners();
      known.current = new Set(page.runners.map((r) => r.id));
      setGrant(
        await adapter.createRunnerEnrollment(
          "Connect a machine",
          60,
          workRoot || null,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [adapter, workRoot]);

  useEffect(() => {
    if (autoStart && !grant && !busy && !error) void mint();
  }, [autoStart, grant, busy, error, mint]);

  /* Watch for the machine: a runner this command created, reporting online. */
  useEffect(() => {
    if (!grant || connected) return;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      adapter
        .listRunners()
        .then((page) => {
          const fresh = page.runners.find(
            (r) =>
              !known.current?.has(r.id) && r.status === "online" && !r.revoked,
          );
          if (fresh) {
            setConnected(fresh);
            if (!reported.current) {
              reported.current = true;
              onConnected?.(fresh);
            }
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [grant, connected, adapter, onConnected]);

  if (connected) {
    const ready = !harness || connected.harnesses.includes(harness);
    return (
      <div
        className="flex items-center gap-3 rounded-lg border border-emerald-600 p-4"
        data-connect-machine="connected"
        role="status"
      >
        <span className="flex size-8 flex-none items-center justify-center rounded-full bg-emerald-600 text-white">
          <Check size={16} strokeWidth={3} aria-hidden="true" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm font-medium">
            {connected.name} is connected
          </span>
          <span className="text-xs text-muted">
            {ready
              ? `Ready to run ${harnessNames(connected)}`
              : `Online, but it cannot run ${CLI_NAMES[harness ?? ""] ?? harness} yet`}
            {connected.workRoot ? ` · works in ${connected.workRoot}` : ""}
          </span>
        </span>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground"
          onClick={() => void mint()}
        >
          <RefreshCw size={12} aria-hidden="true" /> Connect another
        </button>
      </div>
    );
  }

  if (!grant) {
    const shown = workRoot || "~/hoja";
    return (
      <div className="flex flex-col gap-3" data-connect-machine="start">
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-2 text-sm font-medium">
            Where should agents work on this machine?
          </legend>
          <label
            className={`flex cursor-pointer flex-col gap-2 rounded-lg border p-3 ${where === "standard" ? "border-foreground" : "border-line"}`}
          >
            <span className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="work-root"
                checked={where === "standard"}
                onChange={() => setWhere("standard")}
              />
              Standard folder <code className="text-xs">~/hoja</code>
              <span className="rounded-full bg-emerald-600 px-2 text-xs text-white">
                Recommended
              </span>
            </span>
            <span className="pl-6 text-xs text-muted">
              A clean folder Hoja keeps tidy. Nothing else on the machine is
              touched.
            </span>
          </label>
          <label
            className={`flex cursor-pointer flex-col gap-2 rounded-lg border p-3 ${where === "custom" ? "border-foreground" : "border-line"}`}
          >
            <span className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="work-root"
                checked={where === "custom"}
                onChange={() => setWhere("custom")}
              />
              A folder I already use
            </span>
            <span className="pl-6 text-xs text-muted">
              Repos already cloned there are reused; each task still gets its
              own worktree, so your checkouts stay on their branch.
            </span>
            {where === "custom" && (
              <input
                className="ml-6 rounded-md border border-line bg-panel px-2 py-1.5 text-sm text-foreground"
                aria-label="Work folder"
                placeholder="~/code or /srv/repos"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                autoFocus
              />
            )}
          </label>
        </fieldset>
        <pre
          className="m-0 overflow-x-auto rounded-lg border border-line bg-subtle p-3 text-xs text-muted"
          aria-label="How the folder is laid out"
          data-work-layout
        >{`${shown}/
  <repo>/                    your repos, cloned once
  worktrees/<repo>/<task>/   one per task, removed after
  scratch/                   tasks with no repo`}</pre>
        <div>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md bg-foreground px-4 py-2 text-sm font-medium text-panel disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11"
            disabled={busy || (where === "custom" && !folder.trim())}
            onClick={() => void mint()}
          >
            {busy ? (
              <Loader2 size={16} className="animate-spin" aria-hidden="true" />
            ) : (
              <Monitor size={16} aria-hidden="true" />
            )}
            Connect a machine
          </button>
        </div>
        {error && (
          <p className="m-0 text-sm text-danger" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-connect-machine="waiting">
      {grant.connectCommand ? (
        <>
          <span className="text-sm">
            Run this on the machine (macOS or Linux; on Windows, inside WSL).
            Agents will work in{" "}
            <code className="text-xs">{workRoot || "~/hoja"}</code>.
          </span>
          <CommandBox text={grant.connectCommand} big />
        </>
      ) : (
        <>
          <span className="text-sm">
            This deployment does not publish the runner yet. Install it from a
            Hoja Cloud checkout, then connect and start it:
          </span>
          <CommandBox
            text={'pipx install "<path to hoja-cloud>/runner[coder]"'}
          />
          <CommandBox text={grant.command} />
          <CommandBox text={grant.startCommand} />
        </>
      )}
      <p
        className="m-0 flex items-center gap-2 text-sm text-muted"
        role="status"
        aria-live="polite"
      >
        <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        Waiting for the machine… it appears here by itself. The code in the
        command works once, for an hour.
      </p>
      <p className="m-0 text-xs text-muted">
        It installs into ~/.hoja/runner and keeps running as a background
        service. The machine only dials out to Hoja; nothing on it is opened to
        the internet.
      </p>
    </div>
  );
}
