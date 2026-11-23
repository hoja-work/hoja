import * as React from "react";
import { Plus, RotateCw, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { DropdownMenuItem } from "../../components/ui/menu";
import { toast } from "../../components/ui/toast";
import { useSettings } from "../context";
import {
  ConfirmAction,
  ErrorState,
  LoadingRows,
  Notice,
  ResourceRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  useResource,
} from "../ui";
import {
  AddonRow,
  ConnectLink,
  GrafanaDialog,
  ManageDialog,
  ProviderIcon,
  ProviderRow,
  WorkspaceIcon,
  type RowState,
} from "./connections/parts";
import {
  MARKER_NAMES,
  PROVIDERS,
  UNAVAILABLE_COPY,
  availability,
  connectedMarker,
  loadAccounts,
  loadActions,
  loadConfig,
  rejectAction,
  type ConnectorAccount,
  type ConnectorConfig,
  type PendingAction,
  type Provider,
  type RevokeResult,
} from "./connections/providers";

/* Settings → Connectors (HOJA-734, HOJA-680). A directory of the services agents can read,
 * grouped by who owns the connection. Server diagnostics (hosts, env variables) are never
 * shown: an unavailable provider gets a plain reason and, for admins, what to do about it. */

type Data = {
  accounts: ConnectorAccount[];
  actions: PendingAction[];
  config: ConnectorConfig | null;
};

const GOOGLE = PROVIDERS[0];

const TOOL_NAMES: Record<string, string> = {
  gmail_send: "Send an email",
  calendar_event_create: "Create a calendar event",
  docs_append_text: "Add text to a Google Doc",
};

function argsSummary(args: Record<string, unknown>): string {
  return Object.entries(args)
    .filter(([, v]) => typeof v === "string" || typeof v === "number")
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${String(v).slice(0, 60)}`)
    .join(" · ");
}

function connectedLabel(list: ConnectorAccount[]): string {
  const first = list[0]?.externalAccount ?? "";
  return list.length > 1
    ? `Connected as ${first} and ${list.length - 1} more`
    : `Connected as ${first}`;
}

export default function ConnectionsSettings() {
  const { isAdmin } = useSettings();
  const resource = useResource<Data>(async () => {
    const [accounts, actions, config] = await Promise.all([
      loadAccounts(),
      loadActions(),
      loadConfig(),
    ]);
    return { accounts, actions, config };
  });
  const [managing, setManaging] = React.useState<string | null>(null);
  const [grafanaOpen, setGrafanaOpen] = React.useState(false);
  const [rejecting, setRejecting] = React.useState<PendingAction | null>(null);
  const [after, setAfter] = React.useState<{
    title: string;
    hint: string | null;
  } | null>(null);
  const [justConnected] = React.useState(connectedMarker);

  const data = resource.data;
  const config = data?.config ?? null;
  const accounts = data?.accounts ?? [];
  const personal = (p: Provider) =>
    accounts.filter((a) => a.provider === p.id && a.scope !== "org");
  const orgGoogle = accounts.filter(
    (a) => a.provider === GOOGLE.id && a.scope === "org",
  );

  const onDisconnected = (r: RevokeResult, a: ConnectorAccount) => {
    const p = PROVIDERS.find((x) => x.id === a.provider);
    // The hint (Notion, Grafana, Sentry) is a step the person still has to take, so it stays
    // on the page until dismissed instead of vanishing with a toast.
    if (r.hint)
      setAfter({
        title: `${a.externalAccount} is disconnected from Hoja`,
        hint: r.hint,
      });
    else
      toast({
        title: `${p?.name ?? "Account"} disconnected`,
        description: a.externalAccount,
      });
    void resource.reload();
  };

  const stateFor = (p: Provider, list: ConnectorAccount[]): RowState => {
    const avail = availability(p, config);
    if (list.length) {
      if (!avail.ok || (config && config[p.agentKey] === false))
        return {
          kind: "attention",
          label: `${connectedLabel(list)}, but agents can't use it yet`,
        };
      return { kind: "connected", label: connectedLabel(list) };
    }
    if (!avail.ok)
      return { kind: "unavailable", label: UNAVAILABLE_COPY[avail.kind].short };
    return { kind: "idle", label: "Not connected" };
  };

  const unavailable = PROVIDERS.filter((p) => !availability(p, config).ok);
  const allUnavailable = !!config && unavailable.length === PROVIDERS.length;
  const kinds = new Set(
    unavailable.map((p) => {
      const a = availability(p, config);
      return a.ok ? "" : a.kind;
    }),
  );

  const action = (p: Provider, list: ConnectorAccount[]) => {
    if (list.length)
      return (
        <Button
          onClick={() => setManaging(p.id)}
          aria-label={`Manage ${p.name}`}
        >
          Manage
        </Button>
      );
    if (!availability(p, config).ok) return null;
    if (!p.startUrl)
      return (
        <Button
          onClick={() => setGrafanaOpen(true)}
          aria-label={`Connect ${p.name}`}
        >
          Connect
        </Button>
      );
    return (
      <ConnectLink href={p.startUrl} label={`Connect ${p.name}`}>
        Connect
      </ConnectLink>
    );
  };

  const managed = PROVIDERS.find((p) => p.id === managing) ?? null;
  const orgManaging = managing === "google_org";
  const googleList = personal(GOOGLE);
  const googleAvail = availability(GOOGLE, config).ok;

  return (
    <SettingsPage
      title="Connectors"
      description="Connect the tools your team already uses so agents can read from them. Connections are read-only: anything that would send or change something waits for approval."
    >
      {justConnected && (
        <Notice
          title={`${MARKER_NAMES[justConnected] ?? "Connector"} connected`}
        >
          Agents can start reading from it in their next run.
        </Notice>
      )}
      {after && (
        <Notice
          tone="warning"
          title={after.title}
          action={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Dismiss"
              onClick={() => setAfter(null)}
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          }
        >
          {after.hint}
        </Notice>
      )}

      {resource.error && !data ? (
        <ErrorState
          title="Couldn't load your connectors"
          error={resource.error}
          onRetry={resource.reload}
        />
      ) : !data ? (
        <>
          <LoadingRows rows={4} />
          <LoadingRows rows={1} />
        </>
      ) : (
        <>
          {resource.error && (
            <Notice
              tone="error"
              title="Couldn't refresh your connectors"
              action={
                <Button size="sm" onClick={() => void resource.reload()}>
                  <RotateCw className="size-3.5" aria-hidden="true" />
                  Try again
                </Button>
              }
            >
              {resource.error}
            </Notice>
          )}
          {allUnavailable ? (
            <Notice title="Connectors aren't available on this server">
              {isAdmin
                ? kinds.has("not-configured")
                  ? "This Hoja server has no connector credentials set up yet. Whoever runs the server adds each service's app credentials to its configuration; the services then appear here, ready to connect."
                  : UNAVAILABLE_COPY["wrong-address"].admin
                : "A workspace admin needs to set connectors up for this server before you can connect your accounts."}
            </Notice>
          ) : unavailable.length > 0 ? (
            <Notice
              title={`${unavailable.length} ${unavailable.length === 1 ? "service isn't" : "services aren't"} available on this server`}
            >
              {isAdmin
                ? [...kinds]
                    .filter(Boolean)
                    .map(
                      (k) =>
                        UNAVAILABLE_COPY[k as keyof typeof UNAVAILABLE_COPY]
                          .admin,
                    )
                    .join(" ")
                : "Ask a workspace admin if you need one of them."}
            </Notice>
          ) : null}

          <SettingsSection
            title="Your connections"
            description="Personal to you. Agents working for you read with your own permissions; nobody else's agents can use them."
            bare
          >
            <SettingsCard>
              {PROVIDERS.map((p) => {
                const list = personal(p);
                return (
                  <ProviderRow
                    key={p.id}
                    provider={p.id}
                    name={p.name}
                    icon={<ProviderIcon icon={p.icon} tint={p.tint} />}
                    state={stateFor(p, list)}
                    summary={p.summary}
                    action={action(p, list)}
                  />
                );
              })}
            </SettingsCard>
          </SettingsSection>

          <SettingsSection
            title="Workspace connections"
            description="Shared by everyone in this workspace. Only admins can connect or disconnect them."
            bare
          >
            <SettingsCard>
              <ProviderRow
                provider="google_workspace_org"
                name="Shared Google account"
                icon={<ProviderIcon icon={WorkspaceIcon} tint={GOOGLE.tint} />}
                state={
                  orgGoogle.length
                    ? stateFor(GOOGLE, orgGoogle)
                    : googleAvail
                      ? { kind: "idle", label: "Not connected" }
                      : stateFor(GOOGLE, [])
                }
                summary="One Google Workspace account that every member's agents can read from, such as a shared drive or team calendar."
                locked={
                  isAdmin
                    ? undefined
                    : "Only workspace admins can connect or disconnect this."
                }
                action={
                  orgGoogle.length ? (
                    <Button
                      onClick={() => setManaging("google_org")}
                      aria-label={`${isAdmin ? "Manage" : "View"} shared Google account`}
                    >
                      {isAdmin ? "Manage" : "View"}
                    </Button>
                  ) : isAdmin && googleAvail ? (
                    <ConnectLink
                      href="/api/connectors/google/start?scope=org"
                      label="Connect shared Google account"
                    >
                      Connect
                    </ConnectLink>
                  ) : null
                }
              />
            </SettingsCard>
          </SettingsSection>

          {data.actions.length > 0 && (
            <SettingsSection
              title="Waiting for your approval"
              description="Agents asked to make these changes with your connected accounts."
              bare
            >
              <Notice>
                Approving changes isn't turned on for this server yet, so these
                requests can only be rejected. Nothing runs until then.
              </Notice>
              <SettingsCard>
                {data.actions.map((a) => (
                  <ResourceRow
                    key={a.id}
                    title={TOOL_NAMES[a.tool] ?? a.tool}
                    meta={
                      argsSummary(a.args) ||
                      new Date(a.createdAt).toLocaleString()
                    }
                    menuLabel={`Actions for ${TOOL_NAMES[a.tool] ?? a.tool}`}
                    actions={
                      <DropdownMenuItem
                        destructive
                        onSelect={() => setRejecting(a)}
                      >
                        <X aria-hidden="true" />
                        Reject request
                      </DropdownMenuItem>
                    }
                  />
                ))}
              </SettingsCard>
            </SettingsSection>
          )}
        </>
      )}

      {managed && (
        <ManageDialog
          open
          onOpenChange={(o) => !o && setManaging(null)}
          provider={managed}
          title={managed.name}
          description={managed.detail}
          accounts={personal(managed)}
          canDisconnect
          onDisconnected={(r, a) => {
            if (personal(managed).length <= 1) setManaging(null);
            onDisconnected(r, a);
          }}
          extra={
            managed.id === "google_workspace" ? (
              <section aria-label="Extra Google access" className="grid gap-2">
                <h3 className="m-0 text-sm font-medium text-muted-foreground">
                  Extra access
                </h3>
                <AddonRow
                  title="Google Meet"
                  description="Past meetings, participants, recordings and transcripts."
                  granted={googleList.some((a) => a.capabilities?.meet)}
                  href={
                    googleAvail
                      ? "/api/connectors/google/start?addon=meet"
                      : null
                  }
                  actionLabel="Add Meet access"
                />
                <AddonRow
                  title="Google Chat"
                  description="Spaces you belong to, their messages and members."
                  granted={googleList.some((a) => a.capabilities?.chat)}
                  href={
                    googleAvail
                      ? "/api/connectors/google/start?addon=chat"
                      : null
                  }
                  actionLabel="Add Chat access"
                />
              </section>
            ) : managed.id === "github" && config?.githubInstallUrl ? (
              <AddonRow
                title="Private repositories"
                description="GitHub only lets the app read what it is installed on. Install it on the account that owns the private repositories you want agents to read."
                href={config.githubInstallUrl}
                actionLabel="Install on GitHub"
                external
              />
            ) : null
          }
          addAnother={
            availability(managed, config).ok ? (
              managed.startUrl ? (
                <ConnectLink
                  href={managed.startUrl}
                  label={`Connect another ${managed.name} ${managed.noun}`}
                >
                  <Plus className="size-3.5" aria-hidden="true" />
                  Connect another {managed.noun}
                </ConnectLink>
              ) : (
                <Button
                  onClick={() => {
                    setManaging(null);
                    setGrafanaOpen(true);
                  }}
                >
                  <Plus className="size-3.5" aria-hidden="true" />
                  Connect another {managed.noun}
                </Button>
              )
            ) : null
          }
        />
      )}

      {orgManaging && (
        <ManageDialog
          open
          onOpenChange={(o) => !o && setManaging(null)}
          provider={GOOGLE}
          title="Shared Google account"
          description="Every member's agents can read from this account. Disconnecting it removes that access for the whole workspace."
          accounts={orgGoogle}
          canDisconnect={isAdmin}
          lockedReason="Admins only"
          onDisconnected={(r, a) => {
            if (orgGoogle.length <= 1) setManaging(null);
            onDisconnected(r, a);
          }}
        />
      )}

      <GrafanaDialog
        open={grafanaOpen}
        onOpenChange={setGrafanaOpen}
        onConnected={(instance) => {
          toast({ title: "Grafana connected", description: instance });
          void resource.reload();
        }}
      />

      <ConfirmAction
        open={!!rejecting}
        onOpenChange={(o) => !o && setRejecting(null)}
        title="Reject this request?"
        description={
          rejecting
            ? `${TOOL_NAMES[rejecting.tool] ?? rejecting.tool} will not run, and the request is removed from this list.`
            : undefined
        }
        confirmLabel="Reject"
        onConfirm={async () => {
          if (!rejecting) return;
          await rejectAction(rejecting.id);
          toast({ title: "Request rejected" });
          await resource.reload();
        }}
      />
    </SettingsPage>
  );
}
