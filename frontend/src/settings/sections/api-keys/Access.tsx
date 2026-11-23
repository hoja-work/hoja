import { useState } from "react";
import { AppWindow, LogOut, Monitor, Smartphone, Trash2 } from "lucide-react";
import { PhoneSignInDialog } from "../../../PhoneSignIn";
import { Button } from "../../../components/ui/button";
import { DropdownMenuItem } from "../../../components/ui/menu";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Loadable,
  ResourceRow,
  SettingRow,
  SettingsCard,
  SettingsSection,
  gql,
  useResource,
} from "../../ui";
import {
  APP_FIELDS,
  appAccess,
  appName,
  formatWhen,
  type App,
  type Session,
} from "./shared";

function appMeta(app: App, showUser: boolean) {
  return [
    showUser && app.user ? app.user.name || app.user.email || "Member" : null,
    appAccess(app.scopes),
    `Approved ${formatWhen(app.createdAt)}`,
    `Last used ${formatWhen(app.lastUsedAt, "never")}${app.lastUsedIp ? ` from ${app.lastUsedIp}` : ""}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Apps approved through OAuth: yours, or (admins) everyone's in the workspace. */
export function AppsSection({ scope }: { scope: "mine" | "workspace" }) {
  const workspace = scope === "workspace";
  const apps = useResource(() =>
    workspace
      ? gql<{ workspaceAuthorizedApps: { nodes: App[] } }>(
          `query WorkspaceApps{workspaceAuthorizedApps(first:250){nodes{${APP_FIELDS}}}}`,
        ).then((d) => d.workspaceAuthorizedApps.nodes)
      : gql<{ myAuthorizedApps: App[] }>(
          `query MyApps{myAuthorizedApps{${APP_FIELDS}}}`,
        ).then((d) => d.myAuthorizedApps),
  );
  const [revoking, setRevoking] = useState<App | null>(null);
  const owner = revoking?.user?.name || revoking?.user?.email || "this member";

  return (
    <SettingsSection
      title={
        workspace ? "Apps with access to this workspace" : "Signed-in apps"
      }
      description={
        workspace
          ? "Every app any member approved. Revoking one signs it out for that member."
          : "Apps you approved in your browser, like the Hoja CLI and MCP clients. Each one acts as you."
      }
      bare
    >
      <Loadable resource={apps} rows={2}>
        {(data) => (
          <SettingsCard>
            {data.length === 0 ? (
              <EmptyState
                icon={<AppWindow />}
                title={
                  workspace
                    ? "No apps have access to this workspace"
                    : "No apps are signed in as you"
                }
                description={
                  workspace
                    ? "Apps appear here once a member approves one."
                    : "Run hoja auth login, or add Hoja to an MCP client, using the addresses under Connect below."
                }
              />
            ) : (
              <ul
                aria-label={workspace ? "Workspace apps" : "Signed-in apps"}
                className="m-0 p-0"
              >
                {data.map((app) => (
                  <li
                    key={app.id}
                    className="list-none border-line not-first:border-t"
                  >
                    <ResourceRow
                      title={`${appName(app)}${app.deviceLabel ? ` · ${app.deviceLabel}` : ""}`}
                      badges={
                        <>
                          {app.actorKind === "agent" && (
                            <Badge tone="strong">Agent</Badge>
                          )}
                          {app.firstParty && <Badge>Hoja app</Badge>}
                        </>
                      }
                      meta={appMeta(app, workspace)}
                      menuLabel={`Actions for ${appName(app)}`}
                      actions={
                        <DropdownMenuItem
                          destructive
                          onSelect={() => setRevoking(app)}
                        >
                          <Trash2 aria-hidden="true" />
                          Revoke access…
                        </DropdownMenuItem>
                      }
                    />
                  </li>
                ))}
              </ul>
            )}
          </SettingsCard>
        )}
      </Loadable>
      <ConfirmAction
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke ${revoking ? appName(revoking) : "app"}?`}
        description={
          workspace
            ? `${revoking ? appName(revoking) : "The app"} is signed out for ${owner} everywhere, at once. They can approve it again if your workspace allows it.`
            : `${revoking ? appName(revoking) : "The app"} is signed out everywhere, at once. You can approve it again later.`
        }
        confirmLabel="Revoke access"
        onConfirm={async () => {
          if (!revoking) return;
          const field = workspace
            ? "workspaceAuthorizedAppRevoke"
            : "authorizedAppRevoke";
          const d = await gql<Record<string, boolean>>(
            `mutation($id:String!){${field}(id:$id)}`,
            { id: revoking.id },
          );
          if (!d[field])
            throw new Error(
              "Access wasn't revoked. It may already be revoked; reload to check.",
            );
          const id = revoking.id;
          apps.setData((list) => list?.filter((a) => a.id !== id));
        }}
      />
    </SettingsSection>
  );
}

const revokeSession = async (id: string) => {
  const d = await gql<{ sessionRevoke: boolean }>(
    "mutation($id:String!){sessionRevoke(id:$id)}",
    { id },
  );
  if (!d.sessionRevoke)
    throw new Error(
      "That browser wasn't signed out. It may already be signed out; reload to check.",
    );
};

export function SessionsSection() {
  const sessions = useResource(() =>
    gql<{ mySessions: Session[] }>(
      "query MySessions{mySessions{id createdAt lastSeenAt expiresAt current label kind}}",
    ).then((d) =>
      [...d.mySessions].sort(
        (a, b) =>
          Number(b.current) - Number(a.current) ||
          Date.parse(b.lastSeenAt || b.createdAt) -
            Date.parse(a.lastSeenAt || a.createdAt),
      ),
    ),
  );
  const [signingOut, setSigningOut] = useState<Session | null>(null);
  const [signingOutOthers, setSigningOutOthers] = useState(false);
  const [phoneOpen, setPhoneOpen] = useState(false);
  const others = (sessions.data || []).filter((s) => !s.current);
  const drop = (id: string) =>
    sessions.setData((list) => list?.filter((s) => s.id !== id));

  return (
    <SettingsSection
      title="Browser sessions"
      description="Browsers and phones signed in to this workspace as you. Sign out any you don't recognise."
      bare
      action={
        <>
          <Button onClick={() => setPhoneOpen(true)}>
            <Smartphone className="size-3.5" aria-hidden="true" />
            Sign in on your phone
          </Button>
          {others.length > 0 && (
            <Button onClick={() => setSigningOutOthers(true)}>
              <LogOut className="size-3.5" aria-hidden="true" />
              Sign out other browsers
            </Button>
          )}
        </>
      }
    >
      <PhoneSignInDialog
        open={phoneOpen}
        onClose={() => {
          setPhoneOpen(false);
          void sessions.reload();
        }}
      />
      <Loadable resource={sessions} rows={2}>
        {(data) => (
          <SettingsCard>
            {data.length === 0 ? (
              <EmptyState
                icon={<Monitor />}
                title="No browser sessions"
                description="You're signed in with an API key or app here, not a browser session."
              />
            ) : (
              <ul aria-label="Browser sessions" className="m-0 p-0">
                {data.map((s) => (
                  <li
                    key={s.id}
                    className="list-none border-line not-first:border-t"
                  >
                    <ResourceRow
                      leading={
                        s.kind === "app" ? (
                          <Smartphone
                            className="size-4 text-muted-foreground"
                            aria-hidden="true"
                          />
                        ) : (
                          <Monitor
                            className="size-4 text-muted-foreground"
                            aria-hidden="true"
                          />
                        )
                      }
                      title={
                        s.label || s.kind === "app"
                          ? s.label || "Hoja app"
                          : s.current
                            ? "This browser"
                            : "Browser"
                      }
                      badges={
                        s.current ? <Badge tone="strong">Current</Badge> : null
                      }
                      meta={`Signed in ${formatWhen(s.createdAt)} · Last active ${formatWhen(s.lastSeenAt || s.createdAt)}`}
                      menuLabel={
                        s.current
                          ? undefined
                          : `Actions for browser signed in ${formatWhen(s.createdAt)}`
                      }
                      actions={
                        s.current ? undefined : (
                          <DropdownMenuItem
                            destructive
                            onSelect={() => setSigningOut(s)}
                          >
                            <LogOut aria-hidden="true" />
                            Sign out…
                          </DropdownMenuItem>
                        )
                      }
                    />
                  </li>
                ))}
              </ul>
            )}
          </SettingsCard>
        )}
      </Loadable>
      <ConfirmAction
        open={!!signingOut}
        onOpenChange={(o) => !o && setSigningOut(null)}
        title="Sign out this browser?"
        description={`The browser signed in ${formatWhen(signingOut?.createdAt)} is signed out at once. Anyone using it has to sign in again.`}
        confirmLabel="Sign out"
        onConfirm={async () => {
          if (!signingOut) return;
          await revokeSession(signingOut.id);
          drop(signingOut.id);
        }}
      />
      <ConfirmAction
        open={signingOutOthers}
        onOpenChange={setSigningOutOthers}
        title="Sign out every other browser?"
        description={`${others.length} ${others.length === 1 ? "browser is" : "browsers are"} signed out at once. This browser stays signed in.`}
        confirmLabel="Sign out others"
        onConfirm={async () => {
          for (const s of others) {
            await revokeSession(s.id);
            drop(s.id);
          }
        }}
      />
    </SettingsSection>
  );
}

/** Admin-only policy; members see it locked, since the API refuses to read it for them. */
export function WorkspacePolicyLocked() {
  return (
    <SettingsSection
      title="Workspace access"
      description="Which apps members may approve, and which apps hold access, are managed by workspace admins."
    >
      <SettingRow
        label="Apps members can approve"
        description="Hoja's own apps, like the Hoja CLI, are always allowed."
        locked="Only workspace admins can see and change this."
      />
    </SettingsSection>
  );
}
