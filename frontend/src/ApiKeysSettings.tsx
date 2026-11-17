import React, { useEffect, useMemo, useState } from "react";
import { SettingsSelect } from "./SettingsSelect";

type ApiKey = {
  id: string;
  label: string;
  access?: string;
  hint?: string;
  expiresAt?: string;
  lastUsedAt?: string;
  createdAt?: string;
  revokedAt?: string;
};
type Access = "read" | "read_write";
type StatusFilter = "all" | "active" | "expired" | "revoked";

async function gql(q: string, variables?: Record<string, unknown>) {
  const r = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...((): Record<string, string> => {
        const t = localStorage.getItem("hoja_token");
        return t && t !== "session" ? { authorization: `Bearer ${t}` } : {};
      })(),
    },
    body: JSON.stringify({ query: q, variables }),
  });
  const b = await r.json();
  if (b.errors?.length) throw Error(b.errors[0].message);
  return b.data;
}

const EXPIRY_OPTIONS = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "never", label: "No expiration" },
];

function keyStatus(k: ApiKey, now: number): "active" | "expired" | "revoked" {
  if (k.revokedAt) return "revoked";
  if (k.expiresAt && Date.parse(k.expiresAt) <= now) return "expired";
  return "active";
}

const STATUS_LABEL = {
  active: "Active",
  expired: "Expired",
  revoked: "Revoked",
} as const;
const ACCESS_OPTIONS = [
  {
    value: "read_write",
    label: "Read and write",
    description: "View and change workspace data",
    meta: "Full access",
  },
  {
    value: "read",
    label: "Read only",
    description: "View data without making changes",
    meta: "Restricted",
  },
];

function formatDate(value?: string) {
  return value ? new Date(value).toLocaleString() : "Never";
}

export function ApiKeysSettings() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [label, setLabel] = useState("");
  const [access, setAccess] = useState<Access>("read_write");
  const [expiry, setExpiry] = useState("90");
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(
    null,
  );
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState<{
    kind: "error" | "status";
    text: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const now = Date.now();

  useEffect(() => {
    let live = true;
    // Revalidate in place: the list stays on screen; only the first load shows a loading line.
    gql(
      "query MyApiKeys{myApiKeys{id label access hint expiresAt lastUsedAt createdAt revokedAt}}",
    )
      .then((d) => {
        if (live) setKeys(d.myApiKeys);
      })
      .catch((e) => {
        if (live)
          setMessage({
            kind: "error",
            text: `Could not load API keys: ${(e as Error).message}`,
          });
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [refresh]);

  const counts = useMemo(() => {
    const c = { all: keys.length, active: 0, expired: 0, revoked: 0 };
    keys.forEach((k) => {
      c[keyStatus(k, now)]++;
    });
    return c;
  }, [keys, now]);

  const visible = keys.filter((k) => {
    if (statusFilter !== "all" && keyStatus(k, now) !== statusFilter)
      return false;
    const q = query.trim().toLowerCase();
    return (
      !q ||
      (k.label || "").toLowerCase().includes(q) ||
      (k.hint || "").toLowerCase().includes(q)
    );
  });

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    setCopied(false);
    try {
      const expiresAt =
        expiry === "never"
          ? null
          : new Date(Date.now() + Number(expiry) * 86400000).toISOString();
      const d = await gql(
        "mutation($label:String!,$access:String!,$expiresAt:DateTime){apiKeyCreate(label:$label,access:$access,expiresAt:$expiresAt){success apiKey{id} secret}}",
        { label: label.trim(), access, expiresAt },
      );
      if (!d.apiKeyCreate?.success)
        throw Error("Could not create API key. Check the name and try again.");
      setSecret({ label: label.trim(), value: d.apiKeyCreate.secret });
      setLabel("");
      setAccess("read_write");
      setExpiry("90");
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  // Shown as revoked at once; restored with an error if the server refuses.
  const revoke = async (k: ApiKey) => {
    setMessage(null);
    setConfirmId(null);
    const revokedAt = new Date().toISOString();
    setKeys((list) =>
      list.map((x) => (x.id === k.id ? { ...x, revokedAt } : x)),
    );
    try {
      const d = await gql("mutation($id:String!){apiKeyRevoke(id:$id)}", {
        id: k.id,
      });
      if (!d.apiKeyRevoke)
        throw Error("Could not revoke this key. It may already be revoked.");
      setMessage({
        kind: "status",
        text: `Revoked “${k.label || "API key"}”. Apps using it will stop working.`,
      });
      setRefresh((x) => x + 1);
    } catch (err) {
      setKeys((list) =>
        list.map((x) => (x.id === k.id ? { ...x, revokedAt: k.revokedAt } : x)),
      );
      setMessage({ kind: "error", text: (err as Error).message });
    }
  };

  const copySecret = async () => {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.value);
      setCopied(true);
    } catch {
      setMessage({
        kind: "error",
        text: "Copy failed. Select the key and copy it manually.",
      });
    }
  };

  return (
    <div className="grid min-w-0 gap-0">
      <p className="-mt-4.5 mr-0 mb-7 ml-0 text-(--settings-muted) text-[13px] leading-[1.6] max-w-142.5">
        A key acts as you, with the access level you choose. Full secrets are
        shown only once.
      </p>

      {secret && (
        <div
          className="grid border border-line gap-2 pt-4.5 pr-4.5 pb-4.5 pl-4.5 border-solid rounded-[13px] bg-(--settings-accent-soft) mb-4.5 border-[color-mix(in_srgb,var(--settings-accent)_28%,var(--settings-line))]"
          role="status"
        >
          <b className="text-sm font-semibold text-(--settings-accent-strong)">
            Copy “{secret.label}” now
          </b>
          <p className="mt-0 mr-0 mb-0 ml-0 text-[12px] text-(--settings-muted) leading-[1.55]">
            This is the only time the full key is shown. Store it in a password
            manager or secret store.
          </p>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <code
              className="min-w-0 break-all font-mono flex-[1_1_260px] wrap-anywhere pt-2 pr-2 pb-2 pl-2 rounded-md bg-(--settings-card) text-[12px] border border-solid border-(--settings-line)"
              aria-label="New API key"
            >
              {secret.value}
            </code>
            <button
              type="button"
              className="inline-flex min-h-8 items-center justify-center border border-line text-sm text-foreground hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 flex gap-1.75 border-solid rounded-md bg-panel pt-2 pr-2.75 pb-2 pl-2.75 shell-phone:min-w-11! shell-phone:min-h-11! [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
              onClick={copySecret}
            >
              {copied ? "Copied" : "Copy key"}
            </button>
          </div>
          <button
            type="button"
            className="justify-self-start p-0 underline shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 block mt-1 mr-0 mb-0 ml-0 bg-transparent text-(--settings-accent-strong) text-[12px] [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
            onClick={() => {
              setSecret(null);
              setCopied(false);
            }}
          >
            I’ve saved it
          </button>
        </div>
      )}

      <section
        className="w-full border border-line sm:p-6 mt-0 mr-0 mb-5.5 ml-0 pt-4.5 pr-5 pb-4.5 pl-5 border-solid rounded-(--ui-radius-card) bg-panel shadow-(--ui-shadow-card) min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75"
        aria-labelledby="api-key-create-title"
      >
        <h2
          className="font-semibold text-foreground text-[14px] mt-0 mr-0 mb-3 ml-0"
          id="api-key-create-title"
        >
          Create an API key
        </h2>
        <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
          Name the key by its purpose so it is easy to recognize and revoke
          later.
        </p>
        <form
          className="grid items-end sm:grid-cols-2 gap-3 max-w-115 w-full grid-cols-[minmax(150px,1.2fr)_minmax(180px,1.25fr)_minmax(110px,.65fr)_auto] [align-items:end] shell-phone:grid-cols-[1fr] min-w-0 [@media_(max-width:_1100px)]:grid-cols-2 [@media_(max-width:_700px)]:grid-cols-[minmax(0,1fr)]"
          onSubmit={create}
        >
          <label className="grid gap-1.5 text-[12px] text-(--settings-muted) min-w-0 font-[560]">
            Name
            <input
              className="border border-line sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-panel text-foreground min-w-0 max-w-full min-h-9 w-full h-9 text-[12px]"
              aria-label="API key name"
              maxLength={80}
              required
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Laptop CLI, CI deploys"
            />
          </label>
          <div className="grid gap-1.75 min-w-0">
            <span className="text-sm text-(--settings-muted) font-[560]">
              Access
            </span>
            <SettingsSelect
              label="API key access"
              value={access}
              options={ACCESS_OPTIONS}
              onChange={(value) => setAccess(value as Access)}
            />
          </div>
          <div className="grid gap-1.75 min-w-0">
            <span className="text-sm text-(--settings-muted) font-[560]">
              Expires
            </span>
            <SettingsSelect
              label="API key expiration"
              value={expiry}
              options={EXPIRY_OPTIONS.map((o) => ({
                value: o.value,
                label: o.label,
                description:
                  o.value === "never"
                    ? "Stays valid until you revoke it"
                    : `Valid until ${formatDate(new Date(Date.now() + Number(o.value) * 86400000).toISOString())}`,
                meta: o.value === "never" ? "No expiry" : `${o.value} days`,
              }))}
              onChange={setExpiry}
            />
          </div>
          <button
            className="inline-flex items-center justify-center border border-foreground text-sm hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-[7px] bg-accent text-white pt-0 pr-4 pb-0 pl-4 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] [html[data-theme=dark]_&]:text-[#171719] justify-self-start h-9 min-w-30 [align-self:end] whitespace-nowrap [@media_(max-width:_1100px)]:justify-self-start [@media_(max-width:_700px)]:w-full [@media_(max-width:_700px)]:justify-center [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
            disabled={busy || !label.trim()}
          >
            {busy ? "Creating…" : "Create API key"}
          </button>
        </form>
      </section>

      {message && (
        <p
          role={message.kind === "error" ? "alert" : "status"}
          className={
            (message.kind === "error" ? "" : "") +
            " block text-muted mt-3 mr-0 mb-3 ml-0 text-[12px]"
          }
        >
          {message.text}
        </p>
      )}

      <section
        className="w-full mt-0 mr-0 mb-5.5 ml-0 pt-4.5 pr-5 pb-4.5 pl-5 border border-solid border-line rounded-(--ui-radius-card) bg-panel shadow-(--ui-shadow-card) min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75"
        aria-labelledby="api-key-list-title"
      >
        <div className="flex flex-wrap items-center justify-between gap-4 mb-4 [@media_(max-width:_390px)]:items-start">
          <div>
            <h2
              className="text-foreground text-(length:--ds-settings-section-size) mt-0 mr-0 mb-3 ml-0 font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
              id="api-key-list-title"
            >
              Your API keys
            </h2>
          </div>
          <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
            {keys.length} {keys.length === 1 ? "key" : "keys"}
          </span>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2.5 items-center flex-wrap justify-between mb-3.75 [@media_(max-width:_700px)]:items-stretch [@media_(max-width:_700px)]:flex-col">
          <input
            className="w-full border border-line text-base sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 min-w-0 max-w-85 min-h-9 pt-1.5 pr-2 pb-1.5 pl-2 border-solid rounded-lg bg-(--settings-card) text-foreground flex-[1_1_220px] h-10.5 [@media_(max-width:_700px)]:max-w-none"
            type="search"
            aria-label="Search API keys"
            placeholder="Search by name or key ending"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div
            role="group"
            aria-label="Filter by status"
            className="flex flex-wrap border border-line gap-1 pt-0.75 pr-0.75 pb-0.75 pl-0.75 border-solid border-(--settings-line) rounded-[9px] bg-(--settings-subtle) [@media_(max-width:_700px)]:grid [@media_(max-width:_700px)]:grid-cols-4 [@media_(max-width:_390px)]:grid-cols-2"
          >
            {(["all", "active", "expired", "revoked"] as StatusFilter[]).map(
              (s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={statusFilter === s}
                  className={
                    `border ${statusFilter === s ? "border-line" : "border-transparent"}` +
                    "shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11" +
                    " pt-0.75 pr-2 pb-0.75 pl-2 border border-solid border-transparent rounded-[999px] bg-transparent text-[11px] disabled:opacity-50 disabled:cursor-not-allowed min-h-6.75 text-(--settings-muted) [@media_(max-width:_700px)]:pt-0.75 [@media_(max-width:_700px)]:pr-0.75 [@media_(max-width:_700px)]:pb-0.75 [@media_(max-width:_700px)]:pl-0.75 shell-phone:min-w-11! shell-phone:min-h-11! [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
                  }
                  onClick={() => setStatusFilter(s)}
                >
                  {s === "all" ? "All" : STATUS_LABEL[s]}{" "}
                  <span className="text-muted ml-1">{counts[s]}</span>
                </button>
              ),
            )}
          </div>
        </div>

        {loading && (
          <p className="m-0 text-[13px] leading-[1.6] text-muted" role="status">
            Loading API keys…
          </p>
        )}
        {!loading && !keys.length && (
          <div className="text-center text-sm pt-4 pr-4 pb-4 pl-4 border border-dashed border-line rounded-lg text-(--settings-muted) border-(--settings-line)">
            <b className="text-sm font-semibold text-foreground">
              No API keys yet
            </b>
            <p className="m-0 text-sm leading-relaxed text-muted">
              Create one above to connect the CLI or a script to your workspace.
            </p>
          </div>
        )}
        {!loading && keys.length > 0 && !visible.length && (
          <p
            role="status"
            className="text-center text-[13px] leading-[1.6] text-muted pt-4 pr-4 pb-4 pl-4 border border-dashed border-line rounded-lg border-(--settings-line)"
          >
            No keys match this search or filter.
          </p>
        )}

        <ul
          className="list-none overflow-hidden border border-line mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0 border-solid rounded-[11px] border-(--settings-line) bg-(--settings-card)"
          aria-label="API keys"
        >
          {visible.map((k) => {
            const status = keyStatus(k, now);
            const isConfirming = confirmId === k.id;
            return (
              <li
                className="flex flex-wrap items-center border-b border-line last:border-b-0 grid gap-2.5 pt-4 pr-4 pb-4 pl-4 border-b-line border-(--settings-line)"
                key={k.id}
              >
                <div className="flex w-full flex-wrap items-center gap-2">
                  <b className="font-semibold text-foreground text-[12px]">
                    {k.label || "API key"}
                  </b>
                  <span
                    className={
                      `border border-line ${status === "revoked" ? "" : ""}` +
                      " pt-0.5 pr-2 pb-0.5 pl-2 rounded-[999px] text-[10px] bg-(--settings-subtle) border border-solid border-(--settings-line) text-(--settings-muted)"
                    }
                  >
                    {STATUS_LABEL[status]}
                  </span>
                  <span className="border border-line pt-0.5 pr-2 pb-0.5 pl-2 rounded-[999px] text-[10px] bg-(--settings-subtle) border-solid border-(--settings-line) text-(--settings-muted)">
                    {k.access === "read" ? "Read only" : "Read and write"}
                  </span>
                </div>
                <small className="text-(--settings-muted) leading-normal text-[11px]">
                  {k.hint ? (
                    <>
                      Ends in{" "}
                      <code className="min-w-0 break-all font-mono text-xs">
                        …{k.hint}
                      </code>
                    </>
                  ) : (
                    "Key ending unavailable"
                  )}{" "}
                  · Created {formatDate(k.createdAt)} · Last used{" "}
                  {k.lastUsedAt ? formatDate(k.lastUsedAt) : "never"}
                  {" · "}
                  {k.expiresAt
                    ? `Expires ${formatDate(k.expiresAt)}`
                    : "No expiration"}
                  {status === "revoked" && k.revokedAt
                    ? ` · Revoked ${formatDate(k.revokedAt)}`
                    : ""}
                </small>
                {status !== "revoked" &&
                  (isConfirming ? (
                    <div
                      className="flex w-full flex-wrap items-center border border-line gap-2.5 text-[12px] text-foreground pt-3 pr-3 pb-3 pl-3 border-solid border-[color-mix(in_srgb,var(--settings-danger)_24%,var(--settings-line))] rounded-[9px] bg-[color-mix(in_srgb,var(--settings-danger)_5%,var(--settings-card))]"
                      role="group"
                      aria-label={`Confirm revoke ${k.label}`}
                    >
                      <span className="flex-[1_1_320px] leading-normal">
                        Revoke “{k.label}”? Apps using it will stop working
                        immediately. This cannot be undone.
                      </span>
                      <button
                        type="button"
                        className="border border-line disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 bg-transparent text-danger border-solid border-danger rounded-md pt-0 pr-3 pb-0 pl-3 disabled:cursor-not-allowed justify-self-start w-auto h-7.5 text-[12px] font-[550] hover:not-disabled:bg-[color-mix(in_srgb,var(--danger)_10%,transparent)] min-h-7.5 [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
                        disabled={busy}
                        onClick={() => revoke(k)}
                      >
                        Revoke key
                      </button>
                      <button
                        type="button"
                        className="inline-flex items-center justify-center border border-line text-foreground hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 flex gap-1.75 border-solid rounded-md bg-panel pt-0 pr-3 pb-0 pl-3 w-auto min-h-7.5 text-[12px] shell-phone:min-w-11! shell-phone:min-h-11! [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
                        onClick={() => setConfirmId(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 flex gap-1.75 border-solid rounded-md bg-panel pt-0 pr-3 pb-0 pl-3 justify-self-start w-auto min-h-7.5 text-[12px] text-danger border-[color-mix(in_srgb,var(--danger)_45%,var(--line))] shell-phone:min-w-11! shell-phone:min-h-11! [@media_(max-width:700px),(max-width:1000px)_and_(max-height:500px),(pointer:coarse)]:min-h-11!"
                      aria-label={`Revoke ${k.label}`}
                      onClick={() => setConfirmId(k.id)}
                    >
                      Revoke
                    </button>
                  ))}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
