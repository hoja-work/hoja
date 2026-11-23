import * as React from "react";
import { Blocks, Info, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import { useSettings } from "../context";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Loadable,
  Notice,
  ResourceRow,
  SettingsPage,
  SettingsSection,
  useResource,
} from "../ui";
import {
  installExtension,
  listExtensions,
  uninstallExtension,
  updateExtension,
  type Extension,
} from "./extensions/api";
import { CredentialsDialog, DetailsDialog } from "./extensions/Dialogs";
import { ManifestDialog } from "./extensions/ManifestDialog";

/* Extensions (HOJA-734). The server checks the extension.manage capability (extensions_api.py
 * _require_manage), not admin, so this page gates on can("extension.manage"). Every install
 * gets its own bot, API key and signing secret; the secrets are shown once. */

function host(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function plural(n: number, one: string) {
  return `${n} ${n === 1 ? one : `${one}s`}`;
}

export default function ExtensionsSettings() {
  const { can } = useSettings();
  const allowed = can("extension.manage");
  const extensions = useResource<Extension[]>(
    () => (allowed ? listExtensions() : Promise.resolve([])),
    [allowed],
  );
  const [editor, setEditor] = React.useState<{
    open: boolean;
    editing: Extension | null;
  }>({ open: false, editing: null });
  const [details, setDetails] = React.useState<Extension | null>(null);
  const [removing, setRemoving] = React.useState<Extension | null>(null);
  const [credentials, setCredentials] = React.useState<{
    name: string;
    apiKey: string | null;
    signingSecret: string | null;
  } | null>(null);

  const upsert = (next: Extension) =>
    extensions.setData((list) => {
      const rows = list ?? [];
      return rows.some((r) => r.id === next.id)
        ? rows.map((r) => (r.id === next.id ? next : r))
        : [...rows, next];
    });

  const installButton = (
    <Button
      variant="primary"
      onClick={() => setEditor({ open: true, editing: null })}
    >
      <Plus className="size-3.5" aria-hidden="true" />
      Install extension
    </Button>
  );

  if (!allowed)
    return (
      <SettingsPage title="Extensions">
        <Notice tone="locked" title="You can't manage extensions">
          Your workspace role doesn't include “Install and manage extensions”.
          Ask a workspace admin to install one or to give you that permission.
        </Notice>
      </SettingsPage>
    );

  return (
    <SettingsPage
      title="Extensions"
      description="Extensions are services you run that Hoja calls over signed HTTPS. Each one acts through its own bot, so its changes are never made as you."
      actions={extensions.data?.length ? installButton : undefined}
    >
      <SettingsSection title="Installed" bare>
        <Loadable
          resource={extensions}
          isEmpty={(d) => d.length === 0}
          empty={
            <EmptyState
              icon={<Blocks />}
              title="No extensions installed"
              description="Install one from its manifest. Its actions then show up in every task's menu."
              action={installButton}
            />
          }
        >
          {(list) => (
            <div
              role="list"
              aria-label="Installed extensions"
              className="min-w-0 divide-y divide-line overflow-hidden rounded-(--ds-settings-row-radius) border border-line bg-card shadow-xs"
            >
              {list.map((ext) => (
                <div role="listitem" key={ext.id}>
                  <ResourceRow
                    leading={
                      <span
                        aria-hidden="true"
                        className="grid size-8 place-items-center rounded-lg border border-line bg-subtle text-muted-foreground"
                      >
                        <Blocks className="size-4" />
                      </span>
                    }
                    title={ext.name}
                    badges={
                      <>
                        <Badge>
                          {ext.access === "read_write"
                            ? "Read and write"
                            : "Read only"}
                        </Badge>
                        {ext.failures.length > 0 && (
                          <Badge tone="danger">
                            {plural(ext.failures.length, "failure")}
                          </Badge>
                        )}
                      </>
                    }
                    meta={[
                      ext.key,
                      `v${ext.version}`,
                      host(ext.url),
                      plural(ext.hooks.length, "hook"),
                      plural(ext.actions.length, "action"),
                    ].join(" · ")}
                    onOpen={() => setDetails(ext)}
                    menuLabel={`Actions for ${ext.name}`}
                    actions={
                      <>
                        <DropdownMenuItem onSelect={() => setDetails(ext)}>
                          <Info aria-hidden="true" />
                          View details
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() =>
                            setEditor({ open: true, editing: ext })
                          }
                        >
                          <Pencil aria-hidden="true" />
                          Edit manifest
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          destructive
                          onSelect={() => setRemoving(ext)}
                        >
                          <Trash2 aria-hidden="true" />
                          Uninstall
                        </DropdownMenuItem>
                      </>
                    }
                  />
                </div>
              ))}
            </div>
          )}
        </Loadable>
      </SettingsSection>

      <ManifestDialog
        open={editor.open}
        editing={editor.editing}
        onOpenChange={(open) => setEditor((e) => ({ ...e, open }))}
        onSubmit={async (manifest) => {
          if (editor.editing) {
            upsert(await updateExtension(editor.editing.id, manifest));
          } else {
            const result = await installExtension(manifest);
            upsert(result.extension);
            setCredentials({
              name: result.extension.name || result.extension.key,
              apiKey: result.apiKey,
              signingSecret: result.signingSecret,
            });
          }
        }}
      />

      <DetailsDialog
        extension={details}
        onOpenChange={(open) => !open && setDetails(null)}
      />

      <CredentialsDialog
        credentials={credentials}
        onDone={() => setCredentials(null)}
      />

      <ConfirmAction
        open={!!removing}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={`Uninstall ${removing?.name ?? "extension"}?`}
        description="Its bot and credentials are revoked, its hooks and actions stop for everyone, and pending deliveries are dropped. Action history is kept. Installing it again creates new credentials."
        confirmLabel="Uninstall"
        typeToConfirm={removing?.key}
        onConfirm={async () => {
          if (!removing) return;
          try {
            await uninstallExtension(removing.id);
          } finally {
            void extensions.reload();
          }
        }}
      />
    </SettingsPage>
  );
}
