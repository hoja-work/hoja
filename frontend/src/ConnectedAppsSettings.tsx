import { useCallback, useEffect, useState } from "react";
import { Smartphone } from "lucide-react";
import { PhoneSignInDialog } from "./PhoneSignIn";
import { ApiKeysSettings } from "./ApiKeysSettings";
import { ConnectSettings } from "./ConnectSettings";

async function gql(q: string, variables?: Record<string, unknown>) {
  const t = localStorage.getItem("hoja_token");
  const r = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(t && t !== "session" ? { authorization: `Bearer ${t}` } : {}),
    },
    body: JSON.stringify({ query: q, variables }),
  });
  const b = await r.json();
  if (b.errors?.length) throw Error(b.errors[0].message);
  return b.data;
}

type App = {
  id: string;
  clientId: string;
  clientName?: string;
  firstParty: boolean;
  scopes: string[];
  deviceLabel?: string;
  createdAt: string;
  lastUsedAt?: string;
  lastUsedIp?: string;
  user?: { id: string; name?: string; email?: string };
};
type Session = {
  id: string;
  createdAt: string;
  lastSeenAt?: string;
  expiresAt: string;
  current: boolean;
  /** Set for app sessions, e.g. "Pixel 8 · Hoja app · via QR". */
  label?: string | null;
  kind?: "browser" | "app" | null;
};

const APP_FIELDS =
  "id clientId clientName firstParty scopes deviceLabel createdAt lastUsedAt lastUsedIp user { id name email }";

function when(value?: string) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function access(scopes: string[]) {
  return scopes.includes("hoja:write") ? "Read and write" : "Read only";
}

const card =
  "mb-8 flex min-w-0 flex-col gap-4 rounded-xl border border-line bg-panel p-4";
const muted = "text-sm text-(--settings-muted)";
const button =
  "inline-flex min-h-8 items-center justify-center rounded-md border border-line bg-panel px-3 py-2 text-sm text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11";

function Section({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className={card} aria-labelledby={id}>
      <div className="flex flex-col gap-1">
        <h2 id={id} className="text-base font-semibold">
          {title}
        </h2>
        <p className={muted}>{description}</p>
      </div>
      {children}
    </section>
  );
}

function Row({
  title,
  detail,
  action,
}: {
  title: React.ReactNode;
  detail: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <li className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-line pt-4 first:border-t-0 first:pt-0">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="break-words font-medium">{title}</span>
        <span className={`${muted} break-words`}>{detail}</span>
      </div>
      {action}
    </li>
  );
}

function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    load()
      .then((d) => live && setData(d))
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, [load, tick]);
  return { data, error, reload: () => setTick((x) => x + 1) };
}

function AppList({
  apps,
  showUser,
  onRevoke,
}: {
  apps: App[];
  showUser: boolean;
  onRevoke: (app: App) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  if (!apps.length) return <p className={muted}>No apps are signed in.</p>;
  return (
    <ul className="flex flex-col gap-4">
      {apps.map((app) => (
        <Row
          key={app.id}
          title={
            <>
              {app.clientName || app.clientId}
              {app.deviceLabel ? ` · ${app.deviceLabel}` : ""}
            </>
          }
          detail={
            <>
              {showUser && app.user
                ? `${app.user.name || app.user.email || "Member"} · `
                : ""}
              {access(app.scopes)} · Approved {when(app.createdAt)} · Last used{" "}
              {when(app.lastUsedAt)}
              {app.lastUsedIp ? ` from ${app.lastUsedIp}` : ""}
            </>
          }
          action={
            <button
              type="button"
              className={button}
              disabled={busy === app.id}
              aria-label={`Revoke ${app.clientName || app.clientId}`}
              onClick={async () => {
                setBusy(app.id);
                try {
                  await onRevoke(app);
                } finally {
                  setBusy(null);
                }
              }}
            >
              {busy === app.id ? "Revoking…" : "Revoke"}
            </button>
          }
        />
      ))}
    </ul>
  );
}

function YourApps() {
  const [message, setMessage] = useState<string | null>(null);
  const [phoneOpen, setPhoneOpen] = useState(false);
  const loadApps = useCallback(
    () =>
      gql(`query MyApps{myAuthorizedApps{${APP_FIELDS}}}`).then(
        (d) => d.myAuthorizedApps as App[],
      ),
    [],
  );
  const loadSessions = useCallback(
    () =>
      gql(
        "query MySessions{mySessions{id createdAt lastSeenAt expiresAt current label kind}}",
      ).then((d) => d.mySessions as Session[]),
    [],
  );
  const apps = useLoad(loadApps);
  const sessions = useLoad(loadSessions);
  const revokeApp = async (app: App) => {
    setMessage(null);
    try {
      const d = await gql(
        "mutation($id:String!){authorizedAppRevoke(id:$id)}",
        { id: app.id },
      );
      if (!d.authorizedAppRevoke) throw Error("It may already be revoked.");
      setMessage(
        `Revoked ${app.clientName || app.clientId}. It is signed out now.`,
      );
      apps.reload();
    } catch (e) {
      setMessage(`Could not revoke: ${(e as Error).message}`);
    }
  };
  const revokeSession = async (s: Session) => {
    setMessage(null);
    try {
      const d = await gql("mutation($id:String!){sessionRevoke(id:$id)}", {
        id: s.id,
      });
      if (!d.sessionRevoke) throw Error("It may already be signed out.");
      setMessage("Signed out that browser.");
      sessions.reload();
    } catch (e) {
      setMessage(`Could not sign out: ${(e as Error).message}`);
    }
  };
  return (
    <>
      {message && (
        <p role="status" className={`${muted} mb-4`}>
          {message}
        </p>
      )}
      <ConnectSettings />
      <Section
        id="signed-in-apps"
        title="Signed-in apps"
        description="Apps you approved in your browser: the Hoja CLI, MCP clients and other tools. Revoking one signs it out everywhere."
      >
        {apps.error && (
          <p role="alert" className={muted}>
            Could not load apps: {apps.error}
          </p>
        )}
        {!apps.data && !apps.error && <p className={muted}>Loading…</p>}
        {apps.data && (
          <AppList apps={apps.data} showUser={false} onRevoke={revokeApp} />
        )}
      </Section>
      <div className="mb-8 flex flex-col gap-1">
        <h2 id="api-keys-section" className="text-base font-semibold">
          API keys
        </h2>
        <p className={muted}>
          For automation that cannot sign in through a browser.
        </p>
      </div>
      <ApiKeysSettings />
      <Section
        id="browser-sessions"
        title="Browser sessions"
        description="Browsers and phones signed in to this workspace as you."
      >
        <button
          type="button"
          className={`${button} self-start gap-2`}
          onClick={() => setPhoneOpen(true)}
        >
          <Smartphone size={16} aria-hidden="true" />
          Sign in on your phone
        </button>
        <PhoneSignInDialog
          open={phoneOpen}
          onClose={() => {
            setPhoneOpen(false);
            sessions.reload();
          }}
        />
        {sessions.error && (
          <p role="alert" className={muted}>
            Could not load sessions: {sessions.error}
          </p>
        )}
        {!sessions.data && !sessions.error && <p className={muted}>Loading…</p>}
        {sessions.data && !sessions.data.length && (
          <p className={muted}>No browser sessions.</p>
        )}
        {sessions.data && sessions.data.length > 0 && (
          <ul className="flex flex-col gap-4">
            {sessions.data.map((s) => (
              <Row
                key={s.id}
                title={
                  s.label || s.kind === "app" ? (
                    <span className="inline-flex min-w-0 items-center gap-2">
                      {s.kind === "app" && (
                        <Smartphone
                          size={16}
                          aria-hidden="true"
                          className="flex-none text-muted"
                        />
                      )}
                      {s.label || "Hoja app"}
                      {s.current ? " (this device)" : ""}
                    </span>
                  ) : s.current ? (
                    "This browser"
                  ) : (
                    "Browser"
                  )
                }
                detail={`Signed in ${when(s.createdAt)} · Last active ${when(s.lastSeenAt || s.createdAt)}`}
                action={
                  s.current ? undefined : (
                    <button
                      type="button"
                      className={button}
                      onClick={() => revokeSession(s)}
                    >
                      Sign out
                    </button>
                  )
                }
              />
            ))}
          </ul>
        )}
      </Section>
      <Section
        id="integrations-link"
        title="Integrations"
        description="Services Hoja connects to on your behalf, such as GitHub or Slack, are managed under Connections."
      >
        <a
          className={`${button} self-start`}
          href="/workspace/settings/connections"
        >
          Open Connections
        </a>
      </Section>
    </>
  );
}

function WorkspaceApps() {
  const [message, setMessage] = useState<string | null>(null);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);
  const [saving, setSaving] = useState(false);
  const loadApps = useCallback(
    () =>
      gql(
        `query WorkspaceApps{workspaceAuthorizedApps(first:250){nodes{${APP_FIELDS}}}}`,
      ).then((d) => d.workspaceAuthorizedApps.nodes as App[]),
    [],
  );
  const apps = useLoad(loadApps);
  useEffect(() => {
    gql(
      "query Policy{workspaceSecurityPolicy{idleTimeoutMinutes allowedSigninMethods ipAllowlist oauthClients}}",
    )
      .then((d) => setPolicy(d.workspaceSecurityPolicy))
      .catch((e) => setMessage((e as Error).message));
  }, []);
  const setOauthClients = async (value: string) => {
    if (!policy) return;
    setSaving(true);
    setMessage(null);
    try {
      // The mutation replaces the three older fields when they are omitted,
      // so send them back exactly as read.
      const d = await gql(
        "mutation($input:WorkspaceSecurityPolicyInput!){workspaceSecurityUpdate(input:$input){idleTimeoutMinutes allowedSigninMethods ipAllowlist oauthClients}}",
        {
          input: {
            idleTimeoutMinutes: policy.idleTimeoutMinutes,
            allowedSigninMethods: policy.allowedSigninMethods,
            ipAllowlist: policy.ipAllowlist,
            oauthClients: value,
          },
        },
      );
      setPolicy(d.workspaceSecurityUpdate);
      setMessage("Saved.");
    } catch (e) {
      setMessage(`Could not save: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };
  const revoke = async (app: App) => {
    setMessage(null);
    try {
      const d = await gql(
        "mutation($id:String!){workspaceAuthorizedAppRevoke(id:$id)}",
        { id: app.id },
      );
      if (!d.workspaceAuthorizedAppRevoke)
        throw Error("It may already be revoked.");
      setMessage(`Revoked ${app.clientName || app.clientId}.`);
      apps.reload();
    } catch (e) {
      setMessage(`Could not revoke: ${(e as Error).message}`);
    }
  };
  return (
    <>
      {message && (
        <p role="status" className={`${muted} mb-4`}>
          {message}
        </p>
      )}
      <Section
        id="oauth-clients-policy"
        title="Which apps members can approve"
        description="Hoja's own apps, such as the Hoja CLI, are always allowed."
      >
        <fieldset className="flex flex-col gap-2" disabled={!policy || saving}>
          <legend className="sr-only">Apps members can approve</legend>
          {[
            [
              "all",
              "Any app",
              "Members can connect MCP clients and other apps.",
            ],
            [
              "first_party",
              "Only Hoja's own apps",
              "Other apps are refused on the approval page.",
            ],
          ].map(([value, label, hint]) => (
            <label
              key={value}
              aria-label={label}
              className="flex cursor-pointer items-start gap-2 rounded-md border border-line p-4"
            >
              <input
                type="radio"
                name="oauth-clients"
                value={value}
                checked={(policy?.oauthClients || "all") === value}
                onChange={() => setOauthClients(value)}
              />
              <span className="flex flex-col gap-1">
                <span className="font-medium">{label}</span>
                <span className={muted}>{hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
      </Section>
      <Section
        id="workspace-apps"
        title="Apps with access to this workspace"
        description="Every app any member approved. Revoking one signs it out for that member."
      >
        {apps.error && (
          <p role="alert" className={muted}>
            Could not load apps: {apps.error}
          </p>
        )}
        {!apps.data && !apps.error && <p className={muted}>Loading…</p>}
        {apps.data && <AppList apps={apps.data} showUser onRevoke={revoke} />}
      </Section>
    </>
  );
}

/** Settings > Connected apps: signed-in apps, API keys, browser sessions; admins also see the workspace. */
export function ConnectedAppsSettings({ isAdmin }: { isAdmin: boolean }) {
  const [tab, setTab] = useState<"mine" | "workspace">("mine");
  return (
    <div className="flex min-w-0 flex-col">
      {isAdmin && (
        <div
          role="tablist"
          aria-label="Connected apps"
          className="mb-8 flex gap-2"
        >
          {(
            [
              ["mine", "Yours"],
              ["workspace", "Workspace"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className="border border-transparent hover:bg-subtle hover:text-foreground aria-selected:bg-subtle aria-selected:text-foreground aria-selected:font-semibold shell-phone:min-h-11 pointer-coarse:min-h-11 pt-1.25 pr-2.75 pb-1.25 pl-2.75 rounded-[99px] bg-transparent text-muted text-[13px] font-medium"
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {tab === "workspace" && isAdmin ? <WorkspaceApps /> : <YourApps />}
    </div>
  );
}
