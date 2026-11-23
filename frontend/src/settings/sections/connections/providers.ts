import {
  Activity,
  CalendarDays,
  ChartNoAxesColumn,
  Code2,
  FileText,
  Hash,
  Mail,
  Video,
  type LucideIcon,
} from "lucide-react";
import { rest } from "../../ui";

/* Connector data and transport for Settings → Connectors. Copied from the legacy
 * ConnectorsSettings.tsx (provider list, endpoints) so the new screen owns its logic; the
 * legacy file stays untouched until the lead removes it. */

export type ProviderId =
  | "google_workspace"
  | "github"
  | "notion"
  | "slack"
  | "calendly"
  | "sentry"
  | "zoom"
  | "grafana";

export type ConnectorAccount = {
  id: string;
  provider: string;
  externalAccount: string;
  scope: "user" | "org";
  status: string;
  scopes: string[];
  /** Per-API answer for a Google grant (Meet, Chat): a person may untick a permission. */
  capabilities?: Record<string, boolean>;
  /** Grafana instance origin; not a secret. */
  externalUrl?: string | null;
};

export type ConnectorConfig = {
  googleWorkspace?: boolean;
  googleWorkspaceAgent?: boolean;
  notion?: boolean;
  notionAgent?: boolean;
  slack?: boolean;
  slackAgent?: boolean;
  calendly?: boolean;
  calendlyAgent?: boolean;
  sentry?: boolean;
  sentryAgent?: boolean;
  zoom?: boolean;
  zoomAgent?: boolean;
  github?: boolean;
  githubAgent?: boolean;
  githubInstallUrl?: string | null;
  grafana?: boolean;
  grafanaAgent?: boolean;
  availableOnThisHost?: Partial<Record<ProviderId, boolean>>;
  // `host` and `unavailableReason` are also returned. They are server diagnostics (they name
  // hosts and environment variables) and are deliberately never read by this screen.
};

export type PendingAction = {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  createdAt: string;
};

export type RevokeResult = {
  revoked: boolean;
  providerRevoked: boolean;
  hint: string | null;
};

export type Provider = {
  id: ProviderId;
  name: string;
  icon: LucideIcon;
  /** Tailwind classes for the icon tile (soft tint that reads in light and dark). */
  tint: string;
  /** One sentence: what agents can read. */
  summary: string;
  /** Longer note for the manage dialog. */
  detail: string;
  /** Key in ConnectorConfig that says the deployment has credentials for it. */
  configKey: keyof ConnectorConfig;
  agentKey: keyof ConnectorConfig;
  /** Where Connect sends the browser (full-page OAuth redirect). Null: form-based (Grafana). */
  startUrl: string | null;
  /** Noun for one connected thing ("account", "workspace", "organization", "instance"). */
  noun: string;
  /** What the confirm dialog says Disconnect does beyond forgetting the credential. */
  disconnectNote: string;
};

export const PROVIDERS: Provider[] = [
  {
    id: "google_workspace",
    name: "Google Workspace",
    icon: Mail,
    tint: "bg-blue-500/10 text-blue-500",
    summary: "Gmail, Drive, Calendar and Docs, read-only.",
    detail:
      "Agents read with your own Google permissions. Anything that would send or change something waits for your approval.",
    configKey: "googleWorkspace",
    agentKey: "googleWorkspaceAgent",
    startUrl: "/api/connectors/google/start?scope=user",
    noun: "account",
    disconnectNote:
      "Hoja revokes the grant at Google, including Meet and Chat access.",
  },
  {
    id: "github",
    name: "GitHub",
    icon: Code2,
    tint: "bg-subtle text-foreground",
    summary: "Repositories, files, issues and pull requests, read-only.",
    detail:
      "Agents read with your GitHub permissions and the app's read-only permissions. Nothing is created, commented, merged or pushed.",
    configKey: "github",
    agentKey: "githubAgent",
    startUrl: "/api/connectors/github/start",
    noun: "account",
    disconnectNote: "Hoja revokes the grant at GitHub.",
  },
  {
    id: "slack",
    name: "Slack",
    icon: Hash,
    tint: "bg-fuchsia-500/10 text-fuchsia-500",
    summary: "Channels, history, search and people, read-only.",
    detail:
      "Agents read with your own Slack permissions. Private channels work only where you are a member. Nothing is posted.",
    configKey: "slack",
    agentKey: "slackAgent",
    startUrl: "/api/connectors/slack/start",
    noun: "workspace",
    disconnectNote: "Hoja revokes the token at Slack.",
  },
  {
    id: "notion",
    name: "Notion",
    icon: FileText,
    tint: "bg-subtle text-foreground",
    summary: "The pages and databases you choose, read-only.",
    detail:
      "You pick the pages on Notion's consent screen. Connect again to share more pages.",
    configKey: "notion",
    agentKey: "notionAgent",
    startUrl: "/api/connectors/notion/start",
    noun: "workspace",
    disconnectNote:
      "Notion has no way for Hoja to revoke the token. Hoja forgets it; to invalidate it in Notion too, remove Hoja in Notion Settings > Connections.",
  },
  {
    id: "zoom",
    name: "Zoom",
    icon: Video,
    tint: "bg-sky-500/10 text-sky-500",
    summary: "Meetings, cloud recordings and transcripts, read-only.",
    detail:
      "Agents read with your own Zoom permissions. Nothing is scheduled, edited or shared.",
    configKey: "zoom",
    agentKey: "zoomAgent",
    startUrl: "/api/connectors/zoom/start",
    noun: "account",
    disconnectNote: "Hoja revokes the grant at Zoom.",
  },
  {
    id: "calendly",
    name: "Calendly",
    icon: CalendarDays,
    tint: "bg-indigo-500/10 text-indigo-500",
    summary: "Scheduling pages, booked meetings and invitees, read-only.",
    detail:
      "Agents read with your own Calendly permissions. Nothing is booked, moved or cancelled.",
    configKey: "calendly",
    agentKey: "calendlyAgent",
    startUrl: "/api/connectors/calendly/start",
    noun: "account",
    disconnectNote: "Hoja revokes the grant at Calendly.",
  },
  {
    id: "sentry",
    name: "Sentry",
    icon: Activity,
    tint: "bg-violet-500/10 text-violet-500",
    summary: "Projects, issues and latest events of one organization.",
    detail:
      "You choose the organization on Sentry's install page. Nothing is resolved, assigned or deleted.",
    configKey: "sentry",
    agentKey: "sentryAgent",
    startUrl: "/api/connectors/sentry/start",
    noun: "organization",
    disconnectNote:
      "Hoja uninstalls the integration from your Sentry organization. Sentry may take a few minutes to finish.",
  },
  {
    id: "grafana",
    name: "Grafana",
    icon: ChartNoAxesColumn,
    tint: "bg-orange-500/10 text-orange-500",
    summary: "Dashboards, data sources, alerts and annotations, read-only.",
    detail:
      "Connect with the instance address and a service-account token that has the Viewer role. The token is stored encrypted.",
    configKey: "grafana",
    agentKey: "grafanaAgent",
    startUrl: null,
    noun: "instance",
    disconnectNote:
      "Hoja forgets the token but cannot delete it in Grafana. Delete the service account token in Grafana to invalidate it there.",
  },
];

/** Why a provider can't be connected here, in plain words. Never names hosts or variables. */
export type Availability =
  { ok: true } | { ok: false; kind: "not-configured" | "wrong-address" };

export function availability(
  p: Provider,
  config: ConnectorConfig | null,
): Availability {
  if (!config) return { ok: true }; // unknown: let the server answer on Connect
  if (config[p.configKey] === false)
    return { ok: false, kind: "not-configured" };
  if (config.availableOnThisHost?.[p.id] === false)
    return { ok: false, kind: "wrong-address" };
  return { ok: true };
}

export const UNAVAILABLE_COPY = {
  "not-configured": {
    short: "Not set up on this server",
    admin:
      "This server has no credentials for this service yet. Whoever runs your Hoja server adds them to its configuration, then it appears here.",
  },
  "wrong-address": {
    short: "Not available at this address",
    admin:
      "The service only accepts sign-ins coming back to addresses registered for it. Open Hoja from your workspace's main address, or register this address in the service's app settings.",
  },
} as const;

/** Server errors for connectors can name environment variables or hosts (they are written for
 *  operators). Replace those with a plain sentence; keep everything else as the server said it. */
export function plainError(e: unknown): Error {
  const raw = e instanceof Error ? e.message : String(e || "");
  if (
    /\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b/.test(raw) ||
    /workspace host|deployment serves|callback|redirect_uri|environment variable/i.test(
      raw,
    )
  )
    return new Error(
      "This service isn't fully set up on this server yet. Ask whoever runs your Hoja server to finish its setup.",
    );
  return e instanceof Error ? e : new Error(raw);
}
const plain = <T>(p: Promise<T>) =>
  p.catch((e: unknown) => {
    throw plainError(e);
  });

export const loadAccounts = () =>
  plain(
    rest<{ accounts: ConnectorAccount[] }>("/api/connectors/accounts"),
  ).then((r) => r.accounts || []);
export const loadActions = () =>
  plain(rest<{ actions: PendingAction[] }>("/api/connectors/actions")).then(
    (r) => r.actions || [],
  );
export const loadConfig = () =>
  rest<ConnectorConfig>("/api/connectors/config").catch(() => null);

export const revokeAccount = (id: string) =>
  plain(
    rest<RevokeResult>(
      `/api/connectors/accounts/${encodeURIComponent(id)}/revoke`,
      { method: "POST" },
    ),
  );

export const rejectAction = (id: string) =>
  plain(
    rest(`/api/connectors/actions/${encodeURIComponent(id)}/reject`, {
      method: "POST",
    }),
  );

export const grafanaRequest = (
  path: "validate" | "connect",
  body: { url: string; token: string },
) =>
  plain(
    rest<{ account: string; instance: string }>(
      `/api/connectors/grafana/${path}`,
      { json: body },
    ),
  );

/** `?connector=<x>-connected`, set by the OAuth callbacks after a successful grant. */
export function connectedMarker(): string | null {
  try {
    const v = new URLSearchParams(window.location.search).get("connector");
    return v && /-connected$/.test(v) ? v.replace(/-connected$/, "") : null;
  } catch {
    return null;
  }
}

export const MARKER_NAMES: Record<string, string> = {
  google: "Google Workspace",
  meet: "Google Meet access",
  chat: "Google Chat access",
  notion: "Notion",
  slack: "Slack",
  zoom: "Zoom",
  github: "GitHub",
  calendly: "Calendly",
  sentry: "Sentry",
};
