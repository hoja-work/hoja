/* Hoja Connect on the agent page: what this agent's runs may reach, and what they reach now.
 *
 * A grant is a host and a port on the machine that runs the agent (`agentPolicy.connect`). A run
 * that needs one gets a private tunnel pinned to that run and closed with it; nothing else on the
 * network is reachable. This field makes the common case one click (the presets), accepts what
 * people actually paste (`localhost:5432`, `http://localhost:3000`, `postgres://db:5432/app`), names
 * the service behind a port, lets a removal be undone, and shows the tunnels open right now.
 *
 * The rules are the server's (`agent_policy.clean_connect`), mirrored so a bad value is refused
 * before the round trip; the server's own refusal is shown verbatim when it disagrees. */
import { Plus, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { getAgentsAdapter } from "./adapter";
import type { ConnectExposure, ConnectGrant } from "./types";

/** Ports people grant most, with the name they know the service by. */
const SERVICES: Record<number, string> = {
  3000: "Web app",
  4000: "Web app",
  5000: "Web app",
  5173: "Vite dev server",
  8000: "API",
  8080: "API",
  9229: "Node debugger",
  5432: "Postgres",
  3306: "MySQL",
  6379: "Redis",
  27017: "MongoDB",
  9200: "Elasticsearch",
};
const PRESETS = [5432, 3000, 8080, 6379, 3306];
/** The default port of a URL scheme, so `postgres://db/app` needs no port typed. */
const SCHEME_PORTS: Record<string, number> = {
  http: 80,
  https: 443,
  postgres: 5432,
  postgresql: 5432,
  mysql: 3306,
  redis: 6379,
  mongodb: 27017,
};
const LIVE_POLL_MS = 15000;
const UNDO_MS = 6000;

const TOUCH =
  "shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11";
const INPUT = `min-w-0 rounded-md border border-line bg-panel px-2 py-1 text-sm text-foreground ${TOUCH}`;
const CHIP = `inline-flex items-center gap-1 rounded-full border border-line bg-panel px-2 py-0.5 text-xs text-foreground hover:bg-subtle disabled:opacity-50 ${TOUCH}`;

/** `host:port`, a URL or a bare host, as {host, port?}; null when nothing usable was typed. */
export function parseAddress(
  raw: string,
): { host: string; port?: string } | null {
  const text = raw.trim();
  if (!text) return null;
  const url =
    /^([a-z][a-z0-9+.-]*):\/\/(?:[^@/]*@)?(\[[^\]]+\]|[^:/?#]+)(?::(\d+))?/i.exec(
      text,
    );
  if (url) {
    const scheme = url[1].toLowerCase();
    const port =
      url[3] ??
      (SCHEME_PORTS[scheme] ? String(SCHEME_PORTS[scheme]) : undefined);
    return { host: url[2], port };
  }
  const pair = /^(\[[^\]]+\]|[^:\s]+):(\d+)$/.exec(text);
  if (pair) return { host: pair[1], port: pair[2] };
  return { host: text };
}

function minutesLeft(expiresAt: number): string {
  const seconds = expiresAt - Date.now() / 1000;
  if (seconds <= 60) return "closing";
  const minutes = Math.round(seconds / 60);
  return minutes >= 60
    ? `${Math.round(minutes / 60)}h left`
    : `${minutes}m left`;
}

export function ConnectField({
  grants,
  disabled,
  connectOn,
  onSave,
  agentUserId,
}: {
  grants: ConnectGrant[];
  disabled: boolean;
  /** `false` only when the workspace kill switch was read and is off. */
  connectOn: boolean | null;
  onSave: (next: ConnectGrant[]) => Promise<void>;
  /** The agent's `user.id`, which is what a Connect endpoint row names; null hides "open now". */
  agentUserId?: string | null;
}) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">(
    "idle",
  );
  const [message, setMessage] = useState("");
  const [source, setSource] = useState<"local" | "server">("local");
  const [undo, setUndo] = useState<ConnectGrant | null>(null);
  const [live, setLive] = useState<ConnectExposure[]>([]);
  const timer = useRef<number | undefined>(undefined);
  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  /* The tunnels open now for this agent. Admin-gated like the policy itself; a refused read
   * leaves the list empty rather than failing the field. */
  const readLive = useCallback(() => {
    if (!agentUserId) return;
    getAgentsAdapter()
      .listExposures()
      .then((rows) =>
        setLive(rows.filter((e) => e.live && e.agentId === agentUserId)),
      )
      .catch(() => undefined);
  }, [agentUserId]);
  useEffect(() => {
    readLive();
    const id = window.setInterval(() => {
      if (!document.hidden) readLive();
    }, LIVE_POLL_MS);
    return () => window.clearInterval(id);
  }, [readLive]);

  const refuse = (text: string) => {
    setSource("local");
    setMessage(text);
    setState("failed");
  };

  const commit = async (next: ConnectGrant[], after?: () => void) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = undefined;
    setState("saving");
    setMessage("");
    try {
      await onSave(next);
      setState("saved");
      after?.();
      timer.current = window.setTimeout(() => {
        setState("idle");
        setUndo(null);
      }, UNDO_MS);
    } catch (e) {
      setSource("server");
      setMessage((e as Error).message);
      setState("failed");
      setUndo(null);
    }
  };

  /* `agent_policy.clean_connect`, in the same order. */
  const add = (rawHost: string, rawPort: string) => {
    const name = rawHost.trim();
    const digits = rawPort.trim();
    if (!name || name === "*") {
      refuse("A Connect host must be named explicitly.");
      return;
    }
    if (!/^\d+$/.test(digits)) {
      refuse("A Connect port must be an integer.");
      return;
    }
    const number = Number(digits);
    if (number < 1 || number > 65535) {
      refuse("A Connect port must be between 1 and 65535.");
      return;
    }
    if (grants.some((g) => g.host === name && g.port === number)) {
      refuse(`${name}:${number} is already granted.`);
      return;
    }
    setHost("");
    setPort("");
    setUndo(null);
    void commit([...grants, { host: name, port: number }]);
  };

  const remove = (grant: ConnectGrant) =>
    void commit(
      grants.filter((g) => !(g.host === grant.host && g.port === grant.port)),
      () => setUndo(grant),
    );

  /* What people paste is an address, not two fields: split it as it arrives. */
  const takeHost = (value: string) => {
    const parsed = value.includes(":") ? parseAddress(value) : null;
    if (parsed && parsed.port) {
      setHost(parsed.host);
      setPort(parsed.port);
      return;
    }
    if (parsed) {
      setHost(parsed.host);
      return;
    }
    setHost(value);
  };

  const presetHost = host.trim() || "localhost";
  const presets = PRESETS.filter(
    (p) => !grants.some((g) => g.host === presetHost && g.port === p),
  );
  const openFor = (grant: ConnectGrant) =>
    live.find(
      (e) => e.ports.includes(grant.port) && (!e.host || e.host === grant.host),
    );

  return (
    <div className="flex flex-col gap-2 text-sm" data-connect-field>
      <div
        className="flex min-h-8 items-center gap-2"
        data-style-hooks="lin-prop"
      >
        <span className="flex w-24 flex-none items-center gap-2 text-muted">
          <span aria-hidden="true">⇄</span>
          Connect
        </span>
        <span
          className="min-w-0 flex-1 text-xs text-muted"
          data-connect-count={grants.length}
        >
          {grants.length
            ? `${grants.length} ${grants.length === 1 ? "grant" : "grants"}`
            : "No grant"}
          {live.length ? ` · ${live.length} open now` : ""}
        </span>
      </div>
      <p className="m-0 text-xs text-muted">
        Addresses on the agent's machine its runs may reach, through a private
        tunnel that opens for one run and closes with it.
      </p>
      {connectOn === false && (
        <p className="m-0 text-xs text-muted" data-connect-notice="off">
          An admin turned Connect off for this workspace. A grant can still be
          stored, but no tunnel opens until it is back on.
        </p>
      )}

      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {grants.map((grant) => {
          const pair = `${grant.host}:${grant.port}`;
          const open = openFor(grant);
          return (
            <li
              key={pair}
              className="flex min-w-0 items-center gap-2 rounded-md border border-line px-2 py-1"
              data-connect-grant={pair}
              data-connect-open={open ? "true" : undefined}
            >
              <span
                className={`size-2 flex-none rounded-full ${open ? "bg-emerald-600" : "bg-line"}`}
                aria-hidden="true"
              />
              <span className="flex min-w-0 flex-1 flex-col">
                <code className="truncate text-xs">{pair}</code>
                <span className="truncate text-xs text-muted">
                  {SERVICES[grant.port] ?? "Port"}
                  {open
                    ? ` · open now${open.runnerName ? ` via ${open.runnerName}` : ""} · ${minutesLeft(open.expiresAt)}`
                    : ""}
                </span>
              </span>
              <button
                type="button"
                aria-label={`Remove ${pair}`}
                className={`inline-flex flex-none items-center gap-1 rounded-md px-1 text-xs text-muted hover:bg-subtle hover:text-foreground disabled:opacity-50 ${TOUCH}`}
                disabled={disabled}
                onClick={() => remove(grant)}
              >
                <X size={12} aria-hidden="true" />
                Remove
              </button>
            </li>
          );
        })}
        {!grants.length && (
          <li className="text-xs text-muted">
            No port is granted, so a run of this agent cannot expose one.
          </li>
        )}
      </ul>

      {presets.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-1"
          role="group"
          aria-label="Quick add"
        >
          {presets.map((p) => (
            <button
              key={p}
              type="button"
              className={CHIP}
              disabled={disabled}
              aria-label={`Grant ${presetHost}:${p} (${SERVICES[p]})`}
              onClick={() => add(presetHost, String(p))}
            >
              <Plus size={12} aria-hidden="true" />
              {SERVICES[p]} <span className="text-muted">{p}</span>
            </button>
          ))}
        </div>
      )}

      <form
        className="flex flex-wrap items-center gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          add(host, port);
        }}
      >
        <input
          aria-label="Connect host"
          className={`flex-1 basis-24 ${INPUT}`}
          placeholder="host or host:port"
          value={host}
          disabled={disabled}
          onChange={(e) => takeHost(e.target.value)}
          onPaste={(e) => {
            const parsed = parseAddress(e.clipboardData.getData("text"));
            if (parsed?.port) {
              e.preventDefault();
              setHost(parsed.host);
              setPort(parsed.port);
            }
          }}
        />
        <input
          aria-label="Connect port"
          className={`w-16 flex-none ${INPUT}`}
          placeholder="port"
          inputMode="numeric"
          value={port}
          disabled={disabled}
          onChange={(e) => setPort(e.target.value)}
        />
        <button
          type="submit"
          aria-label="Add Connect grant"
          className={`inline-flex flex-none items-center rounded-md border border-line bg-panel px-2 py-1 text-xs font-medium text-foreground hover:bg-subtle disabled:opacity-50 ${TOUCH}`}
          disabled={disabled}
        >
          Add
        </button>
      </form>

      {state !== "idle" && (
        <p
          className={`m-0 flex items-center gap-2 text-xs ${state === "failed" ? "text-danger" : "text-muted"}`}
          role="status"
          aria-live="polite"
          data-field-state={state}
          data-connect-source={state === "failed" ? source : undefined}
        >
          <span>
            {state === "saving"
              ? "Saving…"
              : state === "saved"
                ? undo
                  ? `Removed ${undo.host}:${undo.port}`
                  : "Saved"
                : message || "Not saved"}
          </span>
          {state === "saved" && undo && (
            <button
              type="button"
              className={`inline-flex items-center gap-1 text-xs text-foreground underline ${TOUCH}`}
              onClick={() => {
                const restored = undo;
                setUndo(null);
                void commit([...grants, restored]);
              }}
            >
              <RotateCcw size={12} aria-hidden="true" />
              Undo
            </button>
          )}
        </p>
      )}
    </div>
  );
}
