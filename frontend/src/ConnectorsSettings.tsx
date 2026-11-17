import React, { useEffect, useState } from "react";
import {
  Activity,
  CalendarDays,
  ChartNoAxesColumn,
  Code2,
  FileText,
  Hash,
  Mail,
  Video,
} from "lucide-react";

type ConnectorAccount = {
  id: string;
  provider: string;
  externalAccount: string;
  scope: "user" | "org";
  status: string;
  scopes: string[];
  /** Per-API answer for a Google grant: a user may untick individual permissions on the consent
   *  screen, so "the account exists" is not the same as "Meet is available". */
  capabilities?: Record<string, boolean>;
  /** Customer-supplied Grafana origin. Not a secret; shown so an account is identifiable. */
  externalUrl?: string | null;
};

type ConnectorConfig = {
  github?: boolean;
  /** The app's public install page. Present only on a deployment with a configured GitHub App. */
  githubInstallUrl?: string | null;
  /** `/api/connectors/config`: per-host reachability, not just "the deployment is configured".
   *  A provider whose callback is not registered for this workspace host would fail at the
   *  consent screen, so Settings says so instead of offering the button. */
  availableOnThisHost?: Record<string, boolean>;
  unavailableReason?: Record<string, string | null>;
};

type PendingAction = {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  createdAt: string;
};

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("hoja_token");
  return token && token !== "session"
    ? { authorization: `Bearer ${token}` }
    : {};
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      ...authHeaders(),
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body as T;
}

/** One glyph per connector, chosen by capability rather than brand art: the icon set ships no
 *  third-party logos, and vendoring brand marks would add trademark obligations for no
 *  functional gain. Each glyph is decorative; the tile's heading carries the accessible name. */
/** The connector ids this surface knows how to draw. Narrower than `string` on purpose: a
 *  typo in a provider id must fail the typecheck instead of silently rendering nothing. */
export type ProviderId =
  | "google_workspace"
  | "github"
  | "notion"
  | "slack"
  | "calendly"
  | "sentry"
  | "zoom"
  | "grafana";

const PROVIDER_GLYPH: Record<
  ProviderId,
  React.ComponentType<{ size?: number }>
> = {
  google_workspace: Mail,
  github: Code2,
  notion: FileText,
  slack: Hash,
  calendly: CalendarDays,
  sentry: Activity,
  zoom: Video,
  grafana: ChartNoAxesColumn,
};

export function ConnectorsSettings({ isAdmin }: { isAdmin: boolean }) {
  const [accounts, setAccounts] = useState<ConnectorAccount[]>([]);
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [config, setConfig] = useState<ConnectorConfig>({});
  const [message, setMessage] = useState("");
  const [grafanaUrl, setGrafanaUrl] = useState("");
  const [grafanaToken, setGrafanaToken] = useState("");
  const [grafanaNotice, setGrafanaNotice] = useState("");
  const [grafanaBusy, setGrafanaBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const googleAccounts = accounts.filter(
    (a) => a.provider === "google_workspace",
  );
  const meetAccounts = googleAccounts.filter((a) => a.capabilities?.meet);
  const chatAccounts = googleAccounts.filter((a) => a.capabilities?.chat);
  const notionAccounts = accounts.filter((a) => a.provider === "notion");
  const slackAccounts = accounts.filter((a) => a.provider === "slack");
  const calendlyAccounts = accounts.filter((a) => a.provider === "calendly");
  const sentryAccounts = accounts.filter((a) => a.provider === "sentry");
  const zoomAccounts = accounts.filter((a) => a.provider === "zoom");
  const githubAccounts = accounts.filter((a) => a.provider === "github");
  const githubInstallUrl =
    typeof config.githubInstallUrl === "string"
      ? config.githubInstallUrl
      : null;
  const grafanaAccounts = accounts.filter((a) => a.provider === "grafana");

  useEffect(() => {
    let live = true;
    Promise.all([
      api<{ accounts: ConnectorAccount[] }>("/api/connectors/accounts"),
      api<{ actions: PendingAction[] }>("/api/connectors/actions"),
      api<ConnectorConfig>("/api/connectors/config").catch(
        () => ({}) as ConnectorConfig,
      ),
    ])
      .then(([a, p, c]) => {
        if (!live) return;
        setAccounts(a.accounts);
        setActions(p.actions);
        setConfig(c);
      })
      .catch((e) => live && setMessage((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [refresh]);

  /** The provider's availability on *this* workspace host, or undefined when unreported. */
  const available = (provider: string) =>
    config.availableOnThisHost?.[provider];

  /** The tile's state, derived from loaded accounts and this host's reported reachability.
   *  `loading` wins so a tile never claims "not connected" before the first answer arrives. */
  const badge = (provider: ProviderId, count: number, unavailable = false) => {
    if (loading)
      return (
        <span
          className="border border-line text-[12px] font-semibold pt-0.5 pr-2 pb-0.5 pl-2 rounded-[999px] border-solid text-muted whitespace-nowrap data-[state='connected']:text-(--ok,#157347) data-[state='connected']:border-current data-[state='unavailable']:text-(--danger,#b42318) data-[state='unavailable']:border-current"
          data-state="loading"
        >
          Checking…
        </span>
      );
    if (unavailable || available(provider) === false)
      return (
        <span
          className="border border-line text-[12px] font-semibold pt-0.5 pr-2 pb-0.5 pl-2 rounded-[999px] border-solid text-muted whitespace-nowrap data-[state='connected']:text-(--ok,#157347) data-[state='connected']:border-current data-[state='unavailable']:text-(--danger,#b42318) data-[state='unavailable']:border-current"
          data-state="unavailable"
        >
          Unavailable
        </span>
      );
    if (count > 0)
      return (
        <span
          className="border border-line text-[12px] font-semibold pt-0.5 pr-2 pb-0.5 pl-2 rounded-[999px] border-solid text-muted whitespace-nowrap data-[state='connected']:text-(--ok,#157347) data-[state='connected']:border-current data-[state='unavailable']:text-(--danger,#b42318) data-[state='unavailable']:border-current"
          data-state="connected"
        >
          Connected{count > 1 ? ` · ${count}` : ""}
        </span>
      );
    return (
      <span
        className="border border-line text-[12px] font-semibold pt-0.5 pr-2 pb-0.5 pl-2 rounded-[999px] border-solid text-muted whitespace-nowrap data-[state='connected']:text-(--ok,#157347) data-[state='connected']:border-current data-[state='unavailable']:text-(--danger,#b42318) data-[state='unavailable']:border-current"
        data-state="idle"
      >
        Not connected
      </span>
    );
  };

  /** Either the Connect link, or the reason this host cannot use the provider. */
  const connect = (
    provider: string,
    href: string,
    label: string,
    primary = true,
  ) => {
    if (available(provider) === false) {
      return (
        <small className="text-xs leading-relaxed text-muted">
          {config.unavailableReason?.[provider] ||
            "not available on this workspace host"}
        </small>
      );
    }
    return (
      <a
        className={
          (primary
            ? "inline-flex min-h-9 items-center justify-center border border-foreground text-sm hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
            : undefined) +
          " inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[650] shadow-[0_1px_2px_#0002] hover:filter-[brightness(1.08)]"
        }
        href={href}
      >
        {label}
      </a>
    );
  };

  const revoke = async (id: string) => {
    setMessage("");
    try {
      await api(`/api/connectors/accounts/${encodeURIComponent(id)}/revoke`, {
        method: "POST",
      });
      setRefresh((n) => n + 1);
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  const grafana = async (path: "validate" | "connect") => {
    setMessage("");
    setGrafanaNotice("");
    setGrafanaBusy(true);
    try {
      const body = await api<{ account: string; instance: string }>(
        `/api/connectors/grafana/${path}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            url: grafanaUrl.trim(),
            token: grafanaToken.trim(),
          }),
        },
      );
      if (path === "validate") {
        setGrafanaNotice(`Reached ${body.instance} as ${body.account}.`);
      } else {
        setGrafanaToken("");
        setGrafanaNotice(`Connected ${body.instance}.`);
        setRefresh((n) => n + 1);
      }
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setGrafanaBusy(false);
    }
  };

  const decide = async (id: string, decision: "approve" | "reject") => {
    setMessage("");
    try {
      await api(
        `/api/connectors/actions/${encodeURIComponent(id)}/${decision}`,
        { method: "POST" },
      );
      setRefresh((n) => n + 1);
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  return (
    <section className="w-[min(100%,var(--ds-settings-column-width))] mt-0 mr-auto mb-0 ml-auto pt-(--ds-settings-column-padding) pr-(--ds-settings-column-padding) pb-16 pl-(--ds-settings-column-padding) font-(family-name:--hoja-font-ui) [font-optical-sizing:auto] min-w-0 shell-phone:pt-0 text-(--settings-ink) max-w-(--ds-settings-column-width) [@media_(max-width:_700px)]:pt-0 [html[data-theme=dark]_&]:bg-transparent [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) [--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-on-accent:#fff] [--settings-danger:#242424] [--settings-success:#242424] [html[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [html[data-theme=dark]_&]:[--settings-on-accent:#171717] [html[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [html[data-theme=dark]_&]:[--settings-muted:#aaa] [html[data-theme=dark]_&]:[--settings-line:#353535] [html[data-theme=dark]_&]:[--settings-card:#1b1b1b] [html[data-theme=dark]_&]:[--settings-subtle:#262626] [html[data-theme=dark]_&]:[--settings-danger:#eee] [html[data-theme=dark]_&]:[--settings-success:#d8d8d8]">
      <header className="mt-0 mr-0 mb-8 ml-0 [@media_(max-width:_700px)]:mb-6.75 pb-0 border-b-0 [@media_(max-width:_700px)]:pb-5">
        <h1 className="text-(length:--ds-settings-title-size) tracking-[-.035em] mt-0 mr-0 mb-2 ml-0 font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) text-(--settings-ink) font-(family-name:--hoja-font-ui) [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Connections
        </h1>
        <p className="text-(--settings-muted) text-[13px] leading-[1.6] wrap-anywhere max-w-full mt-0 mr-0 mb-0 ml-0 [@media_(max-width:_700px)]:text-[13px]">
          Connect a workspace so the assistant can read the content you share
          with it.
        </p>
      </header>

      <div
        className="grid items-start lg:grid-cols-2 grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4 [align-items:start] [@media_(max-width:640px)]:grid-cols-[1fr]"
        role="list"
      >
        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-google_workspace"
        >
          {/* The product name is the heading, not an uppercase kicker: app/docs/UI-RULES.md forbids
            letter-spaced capitals, which `.settings-section-kicker` applies. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["google_workspace"], {
                size: 20,
              })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-google_workspace"
              >
                Google Workspace
              </h2>
              {badge("google_workspace", googleAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access for Gmail, Drive, Calendar, Docs, Meet and Chat.
            Writes always require approval.
          </p>
          {message && (
            <p
              className="m-0 text-[13px] leading-[1.6] text-muted"
              role="alert"
            >
              {message}
            </p>
          )}
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your account
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Read-only access. Writes always need your approval.
              </p>
            </div>
            {connect(
              "google_workspace",
              "/api/connectors/google/start?scope=user",
              "Connect Google",
            )}
          </div>
          {isAdmin && (
            <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
              <div className="first:min-w-0 first:wrap-anywhere">
                <b className="text-[13px] text-(--settings-ink) font-[620]">
                  Organization account
                </b>
                <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                  Shared with everyone in this workspace. Admins only.
                </p>
              </div>
              {connect(
                "google_workspace",
                "/api/connectors/google/start?scope=org",
                "Connect organization account",
                false,
              )}
            </div>
          )}
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Google Meet
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Read past meetings, participants, recordings and transcripts.
                Meet rides on the same Google permission as the account above,
                so Disconnecting it also removes Meet access.
              </p>
              {meetAccounts.length > 0 && (
                <small className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal">
                  Added to{" "}
                  {meetAccounts.map((a) => a.externalAccount).join(", ")}
                </small>
              )}
            </div>
            {meetAccounts.length > 0 ? (
              <small className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal">
                Meet access granted
              </small>
            ) : googleAccounts.length > 0 ? (
              connect(
                "google_workspace",
                "/api/connectors/google/start?addon=meet",
                "Add Meet access",
              )
            ) : (
              <small className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal">
                Connect Google first
              </small>
            )}
          </div>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Google Chat
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Read the spaces you belong to, their messages and their members.
                Chat rides on the same Google permission as the account above,
                so Disconnecting it also removes Chat access.
              </p>
              {chatAccounts.length > 0 && (
                <small className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal">
                  Added to{" "}
                  {chatAccounts.map((a) => a.externalAccount).join(", ")}
                </small>
              )}
            </div>
            {chatAccounts.length > 0 ? (
              <small className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal">
                Chat access granted
              </small>
            ) : googleAccounts.length > 0 ? (
              connect(
                "google_workspace",
                "/api/connectors/google/start?addon=chat",
                "Add Chat access",
              )
            ) : (
              <small className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal">
                Connect Google first
              </small>
            )}
          </div>
          <ul
            aria-label="Connected Google accounts"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected accounts…
              </li>
            )}
            {!loading && googleAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Google accounts connected.
              </li>
            )}
            {googleAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    {a.scope === "org"
                      ? "Organization account"
                      : "Personal account"}{" "}
                    · {a.status}
                    {a.capabilities?.meet ? " · Meet access" : ""}
                    {a.capabilities?.chat ? " · Chat access" : ""}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-sentry"
        >
          {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["sentry"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-sentry"
              >
                Sentry
              </h2>
              {badge("sentry", sentryAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to the projects, issues and latest event of one
            Sentry organization. Nothing is resolved, assigned or deleted.
          </p>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your organization
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                You choose the Sentry organization on Sentry’s install page. The
                connection is personal to you and reads only that organization.
              </p>
            </div>
            {connect(
              "sentry",
              "/api/connectors/sentry/start",
              "Connect Sentry",
            )}
          </div>
          <ul
            aria-label="Connected Sentry organizations"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected organizations…
              </li>
            )}
            {!loading && sentryAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Sentry organization connected.
              </li>
            )}
            {sentryAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    Sentry organization · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-zoom"
        >
          {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["zoom"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-zoom"
              >
                Zoom
              </h2>
              {badge("zoom", zoomAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to your meetings, cloud recordings and meeting
            transcripts. Nothing is scheduled, edited or shared.
          </p>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your meetings
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Hoja reads with your own Zoom permissions. A Zoom connection is
                personal to you and is never shared with the workspace.
              </p>
            </div>
            {connect("zoom", "/api/connectors/zoom/start", "Connect Zoom")}
          </div>
          <ul
            aria-label="Connected Zoom accounts"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected accounts…
              </li>
            )}
            {!loading && zoomAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Zoom account connected.
              </li>
            )}
            {zoomAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    Personal account · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-notion"
        >
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["notion"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-notion"
              >
                Notion
              </h2>
              {badge("notion", notionAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to the pages and databases you select in Notion.
            Nothing is created or edited.
          </p>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your workspace
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Choose the pages to share on Notion’s consent screen. Reconnect
                if you shared nothing or want to add pages.
              </p>
            </div>
            {connect(
              "notion",
              "/api/connectors/notion/start",
              "Connect Notion",
            )}
          </div>
          <ul
            aria-label="Connected Notion workspaces"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected workspaces…
              </li>
            )}
            {!loading && notionAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Notion workspace connected.
              </li>
            )}
            {notionAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    Personal workspace · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-slack"
        >
          {/* No `.settings-section-kicker` here: app/docs/UI-RULES.md forbids uppercase label
            text, and that class sets `text-transform: uppercase`. The product name is the
            group heading instead. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["slack"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-slack"
              >
                Slack
              </h2>
              {badge("slack", slackAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to channels, channel history, search and the people
            directory. Nothing is posted or edited.
          </p>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your workspace
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Hoja reads with your own Slack permissions. Private channels
                work only where you are a member.
              </p>
            </div>
            {connect("slack", "/api/connectors/slack/start", "Connect Slack")}
          </div>
          <ul
            aria-label="Connected Slack workspaces"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected workspaces…
              </li>
            )}
            {!loading && slackAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Slack workspace connected.
              </li>
            )}
            {slackAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    Personal workspace · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-calendly"
        >
          {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["calendly"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-calendly"
              >
                Calendly
              </h2>
              {badge("calendly", calendlyAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to your scheduling pages, booked meetings and their
            invitees. Nothing is booked, moved or cancelled.
          </p>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your calendar
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Hoja reads with your own Calendly permissions. A Calendly
                connection is personal to you and is never shared with the
                workspace.
              </p>
            </div>
            {connect(
              "calendly",
              "/api/connectors/calendly/start",
              "Connect Calendly",
            )}
          </div>
          <ul
            aria-label="Connected Calendly accounts"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected accounts…
              </li>
            )}
            {!loading && calendlyAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Calendly account connected.
              </li>
            )}
            {calendlyAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    Personal calendar · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-github"
        >
          {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["github"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-github"
              >
                GitHub
              </h2>
              {badge("github", githubAccounts.length, config.github === false)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to your repositories, files, issues and pull
            requests. Nothing is created, commented, merged or pushed.
          </p>
          <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your repositories
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Hoja reads with your own GitHub permissions and the app’s
                read-only permissions. A GitHub connection is personal to you
                and is never shared with the workspace.
              </p>
            </div>
            {connect(
              "github",
              "/api/connectors/github/start",
              "Connect GitHub",
            )}
          </div>
          {githubInstallUrl && (
            <div className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center items-center gap-5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0">
              <div className="first:min-w-0 first:wrap-anywhere">
                <b className="text-[13px] text-(--settings-ink) font-[620]">
                  Private repositories
                </b>
                <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                  GitHub only lets the app read what it is installed on, and
                  only what you can already see. Install it on the account that
                  owns the private repositories you want Hoja to read. Public
                  repositories need no installation.
                </p>
              </div>
              <a
                href={githubInstallUrl}
                className="font-[inherit] focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2"
              >
                Install on GitHub
              </a>
            </div>
          )}
          {config.github === false && (
            <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
              This deployment has no GitHub App configured.
            </p>
          )}
          <ul
            aria-label="Connected GitHub accounts"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected accounts…
              </li>
            )}
            {!loading && githubAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No GitHub account connected.
              </li>
            )}
            {githubAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    Personal account · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div
          className="border border-line mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="listitem"
          aria-labelledby="connector-grafana"
        >
          {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
          <header className="flex items-center gap-2.5 mb-2">
            <span
              className="flex shrink-0 items-center justify-center inline-flex w-8.5 h-8.5 rounded-[9px] bg-(--surface-2,var(--bg-2,#f1f2f4)) text-muted flex-none"
              aria-hidden="true"
            >
              {React.createElement(PROVIDER_GLYPH["grafana"], { size: 20 })}
            </span>
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="connector-grafana"
              >
                Grafana
              </h2>
              {badge("grafana", grafanaAccounts.length)}
            </div>
          </header>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Read-only access to dashboards, data sources, alert rules and
            annotations of one Grafana instance. Queries are bounded and nothing
            is created, edited or deleted.
          </p>
          <form
            className="flex flex-col items-start justify-between border-t border-line sm:flex-row sm:items-center grid items-center gap-3.5 pt-(--ds-settings-row-padding) pr-0 pb-(--ds-settings-row-padding) pl-0 border-t-line max-w-none w-full min-w-0 shell-phone:items-start shell-phone:flex-col shell-phone:gap-3 min-h-(--ds-settings-row-height) border-(--settings-line) first-of-type:border-t-0 last-of-type:pb-0.5 [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-3 first-of-type:pt-0"
            onSubmit={(event) => {
              event.preventDefault();
              void grafana("connect");
            }}
          >
            <div className="first:min-w-0 first:wrap-anywhere">
              <b className="text-[13px] text-(--settings-ink) font-[620]">
                Your instance
              </b>
              <p className="block text-(--settings-muted) text-[12px] mt-1 mr-0 mb-0 ml-0 leading-normal">
                Use the instance origin (for example
                https://example.grafana.net) and a service-account token with
                the Viewer role. The token is stored sealed and checked with a
                live read.
              </p>
              <label className="grid gap-1.5 text-[12px] font-[550] text-foreground mt-2.5">
                Instance URL
                <input
                  className="border border-line sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-(--settings-card) text-(--settings-ink) font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-(--settings-line) w-full text-[13px] [@media_(max-width:_700px)]:text-[16px]!"
                  type="url"
                  inputMode="url"
                  autoComplete="off"
                  placeholder="https://example.grafana.net"
                  value={grafanaUrl}
                  onChange={(event) => setGrafanaUrl(event.target.value)}
                  required
                />
              </label>
              <label className="grid gap-1.5 text-[12px] font-[550] text-foreground mt-2.5">
                Service-account token
                <input
                  className="border border-line sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-(--settings-card) text-(--settings-ink) font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-(--settings-line) w-full text-[13px] [@media_(max-width:_700px)]:text-[16px]!"
                  type="password"
                  autoComplete="off"
                  placeholder="glsa_..."
                  value={grafanaToken}
                  onChange={(event) => setGrafanaToken(event.target.value)}
                  required
                />
              </label>
              {grafanaNotice && (
                <small
                  className="block text-(--settings-muted) text-[10px] mt-1.25 mr-0 mb-0 ml-0 leading-normal"
                  role="status"
                >
                  {grafanaNotice}
                </small>
              )}
            </div>
            <div className="flex flex-wrap first:min-w-0 first:wrap-anywhere gap-2">
              <button
                type="button"
                className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-0 pr-4 pb-0 pl-4 font-[inherit] h-9 min-w-30 justify-self-start focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] shell-phone:min-w-11! shell-phone:min-h-11!"
                disabled={grafanaBusy}
                onClick={() => void grafana("validate")}
              >
                Test connection
              </button>
              <button
                type="submit"
                className="inline-flex items-center justify-center border border-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-lg bg-(--settings-accent-strong) text-(--settings-on-accent) pt-0 pr-4 pb-0 pl-4 font-[620] shadow-[0_1px_2px_#24242412] hover:filter-[brightness(1.08)] min-h-9 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--settings-accent-strong)_88%,var(--settings-ink))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] [html[data-theme=dark]_&]:text-[#171719] justify-self-start font-[inherit] h-9 min-w-30 focus-visible:outline-solid focus-visible:outline-(--settings-accent) border-solid border-(--settings-accent-strong) text-[12px]"
                disabled={grafanaBusy}
              >
                Connect Grafana
              </button>
            </div>
          </form>
          <ul
            aria-label="Connected Grafana instances"
            className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
          >
            {loading && (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                role="status"
              >
                Checking connected instances…
              </li>
            )}
            {!loading && grafanaAccounts.length === 0 && (
              <li className="justify-center flex items-center gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) text-(--settings-muted) text-[12px] [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap">
                No Grafana instance connected.
              </li>
            )}
            {grafanaAccounts.map((a) => (
              <li
                className="flex flex-wrap items-center border border-line gap-2.75 min-w-0 pt-3 pr-3 pb-3 pl-3 border-solid border-(--settings-line) rounded-[10px] bg-(--settings-subtle) [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-wrap"
                key={a.id}
              >
                <span
                  className="flex shrink-0 items-center justify-center grid place-items-center w-7.75 h-7.75 flex-none rounded-[9px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[11px] font-bold"
                  aria-hidden="true"
                >
                  {a.externalAccount.slice(0, 1).toUpperCase()}
                </span>
                <span className="grid flex-1 gap-0.75 min-w-0">
                  <b className="font-semibold text-foreground wrap-anywhere text-[12px]">
                    {a.externalAccount}
                  </b>
                  <small className="leading-relaxed text-(--settings-muted) text-[10px]">
                    {a.externalUrl || "Grafana instance"} · {a.status}
                  </small>
                </span>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:ml-10.5 shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => revoke(a.id)}
                >
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]">
        <h2 className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)">
          Pending actions
        </h2>
        <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
          Review every proposed write before the assistant can act.
        </p>
        <ul
          aria-label="Pending actions"
          className="grid list-none gap-2.25 mt-4.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
        >
          {!loading && actions.length === 0 && (
            <li className="justify-center pt-3.5 pr-3.5 pb-3.5 pl-3.5 border border-solid border-(--settings-line) rounded-[10px] text-(--settings-muted) text-[12px]">
              Nothing waiting for approval.
            </li>
          )}
          {actions.map((a) => (
            <li
              className="flex min-w-0 flex-wrap items-center gap-3 border border-line bg-subtle pt-3.5 pr-3.5 pb-3.5 pl-3.5 border-solid border-(--settings-line) rounded-[10px]"
              key={a.id}
            >
              <b className="text-sm font-semibold text-foreground">{a.tool}</b>
              <pre className="overflow-auto whitespace-pre-wrap break-all max-w-full mt-2.5 mr-0 mb-2.5 ml-0 pt-3 pr-3 pb-3 pl-3 rounded-lg bg-(--settings-subtle) text-(--settings-muted) text-[11px] leading-[1.55] wrap-anywhere">
                {JSON.stringify(a.args, null, 2)}
              </pre>
              <div className="flex flex-wrap gap-2">
                <button
                  className="inline-flex items-center justify-center border border-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-lg bg-(--settings-accent-strong) text-(--settings-on-accent) pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[620] shadow-[0_1px_2px_#24242412] hover:filter-[brightness(1.08)] min-h-9 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--settings-accent-strong)_88%,var(--settings-ink))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] [html[data-theme=dark]_&]:text-[#171719] font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) border-solid border-(--settings-accent-strong) text-[12px]"
                  onClick={() => decide(a.id, "approve")}
                >
                  Approve
                </button>
                <button
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => decide(a.id, "reject")}
                >
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
