/* Connect machines from the workspace's tailnet, the way Muse does (HOJA-602).
 *
 * Connect Tailscale once; then every machine on the tailnet is listed here, and "Use for agents"
 * asks to be allowed (always, or just this once), then installs the runner over Tailscale SSH
 * while you watch. No terminal. The server side is app/hoja_server/tailnet.py. */
import {
  Check,
  Copy,
  ExternalLink,
  Loader2,
  Monitor,
  Network,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { getAgentsAdapter } from "./adapter";
import type { Runner, Tailnet, TailnetDevice } from "./types";

const POLL_FAST_MS = 2000;
const POLL_MS = 10000;
const OAUTH_URL = "https://login.tailscale.com/admin/settings/oauth";
const POLICY_URL = "https://login.tailscale.com/admin/acls/file";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs hover:bg-subtle"
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}{" "}
      {copied ? "Copied" : label}
    </button>
  );
}

function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <span className="flex size-6 flex-none items-center justify-center rounded-full bg-subtle text-xs font-medium">
        {n}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <span className="text-sm font-medium">{title}</span>
        {children}
      </div>
    </li>
  );
}

/* Not connected yet: three steps, the last one in Hoja. */
function ConnectTailscale({
  tailnet,
  onConnected,
}: {
  tailnet: Tailnet;
  onConnected: (t: Tailnet) => void;
}) {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="flex flex-col gap-4" data-tailnet="connect">
      <p className="m-0 text-sm text-muted">
        Hoja joins your tailnet as its own machine ({tailnet.hojaTag}), lists
        your machines here, and sets up the ones you allow over Tailscale SSH.
        You never open a terminal.
      </p>
      <ol className="m-0 flex list-none flex-col gap-4 p-0">
        <Step n={1} title="Let Hoja's machine reach the ones you allow">
          <span className="text-xs text-muted">
            Add these to your tailnet policy file. Only machines tagged{" "}
            <code>{tailnet.machineTag}</code> are reachable, and Hoja tags one
            only when you allow it below.
          </span>
          <pre
            className="m-0 max-h-48 overflow-auto rounded-lg border border-line bg-subtle p-3 text-xs"
            data-tailnet-policy
          >
            {tailnet.policy}
          </pre>
          <span className="flex gap-2">
            <CopyButton text={tailnet.policy} label="Copy" />
            <a
              className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground"
              href={POLICY_URL}
              target="_blank"
              rel="noreferrer"
            >
              Open the policy file <ExternalLink size={12} />
            </a>
          </span>
        </Step>
        <Step n={2} title="Create an OAuth client for Hoja">
          <span className="text-xs text-muted">
            Scopes <b>Devices: Core</b> and <b>Auth Keys</b> (write), tags{" "}
            <code>{tailnet.hojaTag}</code> and <code>{tailnet.machineTag}</code>
            .
          </span>
          <a
            className="inline-flex w-fit items-center gap-1 text-xs text-muted hover:text-foreground"
            href={OAUTH_URL}
            target="_blank"
            rel="noreferrer"
          >
            Open OAuth clients in Tailscale <ExternalLink size={12} />
          </a>
        </Step>
        <Step n={3} title="Paste it here">
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              getAgentsAdapter()
                .connectTailnet(clientId.trim(), secret.trim())
                .then(onConnected)
                .catch((err) => setError((err as Error).message))
                .finally(() => setBusy(false));
            }}
          >
            <input
              className="rounded-md border border-line bg-panel px-2 py-1.5 text-sm"
              aria-label="Client ID"
              placeholder="Client ID"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              autoComplete="off"
            />
            <input
              className="rounded-md border border-line bg-panel px-2 py-1.5 text-sm"
              aria-label="Client secret"
              placeholder="Client secret (tskey-client-…)"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="off"
            />
            <span>
              <button
                type="submit"
                className="inline-flex items-center gap-2 rounded-md bg-foreground px-4 py-2 text-sm font-medium text-panel disabled:opacity-50"
                disabled={busy || !clientId.trim() || !secret.trim()}
              >
                {busy ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Network size={16} />
                )}
                Connect Tailscale
              </button>
            </span>
            {error && (
              <p className="m-0 text-sm text-danger" role="alert">
                {error}
              </p>
            )}
          </form>
        </Step>
      </ol>
    </div>
  );
}

/* The permission prompt: who Hoja connects as, where agents work, for how long. */
function AllowCard({
  device,
  machineTag,
  onDone,
  onCancel,
}: {
  device: TailnetDevice;
  machineTag: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [user, setUser] = useState(device.access?.sshUser ?? "");
  const [mode, setMode] = useState<"standing" | "once">("standing");
  const [folder, setFolder] = useState(device.access?.workRoot ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-foreground p-4"
      data-tailnet-allow={device.hostname}
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        getAgentsAdapter()
          .allowTailnetMachine(
            device.id,
            user.trim(),
            mode,
            folder.trim() || null,
          )
          .then(onDone)
          .catch((err) => setError((err as Error).message))
          .finally(() => setBusy(false));
      }}
    >
      <span className="flex items-center gap-2 text-sm font-medium">
        <ShieldCheck size={16} /> Allow Hoja to connect to {device.hostname}?
      </span>
      <span className="text-xs text-muted">
        Hoja connects over Tailscale SSH and installs its runner, the same
        one-line install you could run yourself.
        {device.reachable
          ? ""
          : ` It tags the machine ${machineTag} first, so your policy lets Hoja reach it; a tagged machine belongs to the tailnet rather than to a person.`}
      </span>
      <label className="flex flex-col gap-1 text-xs">
        Run as this user on {device.hostname}
        <input
          className="rounded-md border border-line bg-panel px-2 py-1.5 text-sm"
          aria-label="User on the machine"
          placeholder="the user you log in with, e.g. ada"
          value={user}
          onChange={(e) => setUser(e.target.value)}
          autoFocus
          autoComplete="off"
        />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        Work folder (optional)
        <input
          className="rounded-md border border-line bg-panel px-2 py-1.5 text-sm"
          aria-label="Work folder"
          placeholder="~/hoja (standard) or a folder you use, like ~/code"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
        />
      </label>
      <fieldset className="m-0 flex flex-col gap-1 border-0 p-0 text-sm">
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={`mode-${device.id}`}
            checked={mode === "standing"}
            onChange={() => setMode("standing")}
          />
          Always allow{" "}
          <span className="text-xs text-muted">
            (so Hoja can upgrade or restart it; revoke any time)
          </span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={`mode-${device.id}`}
            checked={mode === "once"}
            onChange={() => setMode("once")}
          />
          Just this once
        </label>
      </fieldset>
      <span className="text-xs text-muted">
        Tailscale SSH must be on for the machine. If it isn't, run{" "}
        <code>sudo tailscale set --ssh</code> there once.
      </span>
      <span className="flex gap-2">
        <button
          type="submit"
          className="inline-flex items-center gap-2 rounded-md bg-foreground px-4 py-2 text-sm font-medium text-panel disabled:opacity-50"
          disabled={busy || !user.trim()}
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          Allow and set up
        </button>
        <button
          type="button"
          className="rounded-md px-3 py-2 text-sm text-muted"
          onClick={onCancel}
        >
          Deny
        </button>
      </span>
      {error && (
        <p className="m-0 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function DeviceRow({
  device,
  tailnet,
  onChanged,
}: {
  device: TailnetDevice;
  tailnet: Tailnet;
  onChanged: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const access = device.access;
  const running = device.runnerStatus === "online";
  const installing = access?.state === "installing";
  const failed = access?.state === "failed";
  const status = running
    ? "Running agents"
    : installing
      ? "Setting up…"
      : failed
        ? "Setup failed"
        : device.online
          ? "Online"
          : "Offline";
  const lastLog = (access?.log ?? "").trim().split("\n").slice(-6).join("\n");
  return (
    <li
      className="flex flex-col gap-2 border-b border-line py-3 last:border-b-0"
      data-tailnet-device={device.hostname}
      data-state={
        running
          ? "running"
          : (access?.state ?? (device.online ? "online" : "offline"))
      }
    >
      <div className="flex items-center gap-3">
        <span
          className={`size-2 flex-none rounded-full ${running || device.online ? "bg-emerald-600" : "bg-line"}`}
          aria-hidden="true"
        />
        <Monitor
          size={16}
          className="flex-none text-muted"
          aria-hidden="true"
        />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium">
            {device.hostname}
          </span>
          <span className="truncate text-xs text-muted">
            {status}
            {device.os ? ` · ${device.os}` : ""}
            {device.address ? ` · ${device.address}` : ""}
            {access && access.state !== "revoked"
              ? ` · as ${access.sshUser}${access.mode === "once" ? ", once" : ""}`
              : ""}
          </span>
        </span>
        {installing ? (
          <Loader2 size={16} className="animate-spin text-muted" />
        ) : running ? (
          <Check size={16} className="text-emerald-600" />
        ) : (
          <button
            type="button"
            className="rounded-md border border-line px-3 py-1.5 text-xs font-medium hover:bg-subtle disabled:opacity-50"
            disabled={!device.online}
            title={device.online ? undefined : "Turn the machine on first"}
            onClick={() => setAsking(true)}
          >
            {failed ? "Try again" : "Use for agents"}
          </button>
        )}
        {access && access.state !== "revoked" && !installing && (
          <button
            type="button"
            className="text-xs text-muted hover:text-foreground"
            onClick={() =>
              void getAgentsAdapter()
                .revokeTailnetMachine(device.id)
                .then(onChanged)
            }
          >
            Revoke
          </button>
        )}
      </div>
      {asking && !installing && (
        <AllowCard
          device={device}
          machineTag={tailnet.machineTag}
          onCancel={() => setAsking(false)}
          onDone={() => {
            setAsking(false);
            onChanged();
          }}
        />
      )}
      {(installing || failed) && lastLog && (
        <pre
          className={`m-0 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border p-2 text-xs ${failed ? "border-danger text-danger" : "border-line text-muted"}`}
          data-tailnet-log
          aria-live="polite"
        >
          {lastLog}
        </pre>
      )}
    </li>
  );
}

export function TailnetConnect({
  onConnected,
  initial,
}: {
  onConnected?: (runner: Runner) => void;
  initial?: Tailnet | null;
}) {
  const adapter = getAgentsAdapter();
  const [tailnet, setTailnet] = useState<Tailnet | null>(initial ?? null);
  const [error, setError] = useState("");
  const reported = useRef<Set<string>>(new Set());

  const load = useCallback(() => {
    adapter
      .getTailnet()
      .then((t) => {
        setTailnet(t);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [adapter]);

  useEffect(() => {
    if (!initial) load();
  }, [initial, load]);

  const busy = tailnet?.devices.some((d) => d.access?.state === "installing");
  const settling =
    tailnet?.connected &&
    (tailnet.nodeState === "starting" ||
      tailnet.devices.some(
        (d) => d.access?.state === "connected" && d.runnerStatus !== "online",
      ));
  useEffect(() => {
    if (!tailnet?.connected) return;
    const id = window.setInterval(
      () => {
        if (!document.hidden) load();
      },
      busy || settling ? POLL_FAST_MS : POLL_MS,
    );
    return () => window.clearInterval(id);
  }, [tailnet?.connected, busy, settling, load]);

  /* A machine set up here that just came online: report it once, like the command flow. */
  useEffect(() => {
    if (!onConnected || !tailnet) return;
    for (const d of tailnet.devices) {
      if (
        d.runnerId &&
        d.runnerStatus === "online" &&
        d.access?.state === "connected" &&
        !reported.current.has(d.runnerId)
      ) {
        reported.current.add(d.runnerId);
        void adapter.listRunners().then((page) => {
          const found = page.runners.find((r) => r.id === d.runnerId);
          if (found) onConnected(found);
        });
      }
    }
  }, [tailnet, onConnected, adapter]);

  if (!tailnet) {
    return error ? (
      <p className="m-0 text-sm text-danger" role="alert">
        {error}
      </p>
    ) : (
      <Loader2 size={16} className="animate-spin text-muted" />
    );
  }
  if (!tailnet.connected) {
    return <ConnectTailscale tailnet={tailnet} onConnected={setTailnet} />;
  }

  const node =
    tailnet.nodeState === "running"
      ? `Hoja is on ${tailnet.name ?? "your tailnet"}${tailnet.nodeName ? ` as ${tailnet.nodeName.split(".")[0]}` : ""}`
      : tailnet.nodeState === "failed"
        ? "Hoja could not join the tailnet"
        : `Connected to ${tailnet.name ?? "your tailnet"}. Hoja joins it when it sets up a machine.`;
  return (
    <div className="flex flex-col gap-3" data-tailnet="connected">
      <div className="flex items-center gap-2 text-sm">
        <Network size={16} className="text-emerald-600" aria-hidden="true" />
        <span
          className="min-w-0 flex-1 truncate"
          data-tailnet-node={tailnet.nodeState}
        >
          {node}
        </span>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground"
          onClick={() => void adapter.disconnectTailnet().then(setTailnet)}
        >
          <Unplug size={12} /> Disconnect
        </button>
      </div>
      {tailnet.nodeError && (
        <p className="m-0 text-sm text-danger" role="alert">
          {tailnet.nodeError}
        </p>
      )}
      {tailnet.devicesError ? (
        <p className="m-0 text-sm text-danger" role="alert">
          {tailnet.devicesError}
        </p>
      ) : tailnet.devices.length === 0 ? (
        <p className="m-0 text-sm text-muted">
          No other machines on the tailnet yet. Install Tailscale on one and it
          appears here.
        </p>
      ) : (
        <ul className="m-0 list-none rounded-lg border border-line px-3 py-0">
          {tailnet.devices.map((d) => (
            <DeviceRow
              key={d.id}
              device={d}
              tailnet={tailnet}
              onChanged={load}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
