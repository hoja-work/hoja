import * as React from "react";
import { Bot, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import { Switch } from "../../components/ui/switch";
import { useSettings } from "../context";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Loadable,
  Notice,
  ResourceRow,
  SaveStatusLine,
  SettingsPage,
  SettingsSection,
  useResource,
  useSetting,
} from "../ui";
import {
  deleteProvider,
  hostOf,
  listProviders,
  updateProvider,
  type ModelProvider,
} from "./model-providers/api";
import { ProviderDialog } from "./model-providers/ProviderDialog";

/* Model providers (HOJA-734). Admin-only on the server (model_providers.py _require_admin).
 * Enable/disable applies instantly; add and edit go through one dialog; delete asks for the
 * name. Managed providers (operated by Hoja) can be paused but not edited or deleted here. */

function ProviderRow({
  provider: p,
  onChanged,
  onEdit,
  onDelete,
}: {
  provider: ModelProvider;
  onChanged: (p: ModelProvider) => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const enabled = useSetting({
    value: p.enabled,
    save: async (next) =>
      onChanged(await updateProvider(p.id, { enabled: next })),
  });
  const host = p.managed ? null : hostOf(p.baseUrl);
  const details = [
    p.managed ? "Operated by Hoja" : p.provider,
    p.modelId,
    p.contextWindow ? `${Math.round(p.contextWindow / 1000)}k context` : null,
    host,
  ]
    .filter(Boolean)
    .join(" · ");
  const busy = enabled.status.state !== "idle";
  return (
    <>
      <ResourceRow
        leading={
          <span
            aria-hidden="true"
            className="grid size-8 place-items-center rounded-lg border border-line bg-subtle text-muted-foreground"
          >
            <Bot className="size-4" />
          </span>
        }
        title={p.name}
        badges={
          <>
            {p.managed && <Badge>Managed</Badge>}
            {!p.managed && !p.hasApiKey && <Badge tone="danger">No key</Badge>}
            {!enabled.value && <Badge>Off</Badge>}
          </>
        }
        meta={details}
        onOpen={p.managed ? undefined : onEdit}
        menuLabel={`Actions for ${p.name}`}
        actions={
          p.managed ? undefined : (
            <>
              <DropdownMenuItem onSelect={onEdit}>
                <Pencil aria-hidden="true" />
                Edit
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={onDelete}>
                <Trash2 aria-hidden="true" />
                Delete
              </DropdownMenuItem>
            </>
          )
        }
      >
        <Switch
          checked={enabled.value}
          onCheckedChange={(v) => void enabled.set(v)}
          aria-label={`Enable ${p.name}`}
        />
      </ResourceRow>
      {busy && (
        <div className="-mt-2 px-(--ds-settings-row-padding) pb-2.5 pl-15">
          <SaveStatusLine status={enabled.status} />
        </div>
      )}
    </>
  );
}

export default function ModelProvidersSettings() {
  const { isAdmin } = useSettings();
  const providers = useResource(listProviders, []);
  const [dialog, setDialog] = React.useState<{
    open: boolean;
    editing: ModelProvider | null;
  }>({ open: false, editing: null });
  const [deleting, setDeleting] = React.useState<ModelProvider | null>(null);

  const upsert = (next: ModelProvider) =>
    providers.setData((list) => {
      const rows = list ?? [];
      return rows.some((r) => r.id === next.id)
        ? rows.map((r) => (r.id === next.id ? next : r))
        : [...rows, next];
    });

  const addButton = (
    <Button
      variant="primary"
      onClick={() => setDialog({ open: true, editing: null })}
    >
      <Plus className="size-3.5" aria-hidden="true" />
      Add provider
    </Button>
  );

  if (!isAdmin)
    return (
      <SettingsPage title="Model providers">
        <Notice tone="locked" title="Only workspace admins can manage models">
          Ask an admin if agents need a different model.
        </Notice>
      </SettingsPage>
    );

  return (
    <SettingsPage
      title="Model providers"
      description="The models your workspace agents run on. Keys are encrypted when you save them and are never shown again."
      actions={providers.data?.length ? addButton : undefined}
    >
      <SettingsSection
        title="Providers"
        description="Turn a provider off to stop agents from using it. Its key stays saved."
        bare
      >
        <Loadable
          resource={providers}
          isEmpty={(d) => d.length === 0}
          empty={
            <EmptyState
              icon={<Bot />}
              title="No model providers yet"
              description="Add an OpenAI-compatible model so agents have something to run on."
              action={addButton}
            />
          }
        >
          {(list) => (
            <div
              role="list"
              aria-label="Model providers"
              className="min-w-0 divide-y divide-line overflow-hidden rounded-(--ds-settings-row-radius) border border-line bg-card shadow-xs"
            >
              {list.map((p) => (
                <div role="listitem" key={p.id}>
                  <ProviderRow
                    provider={p}
                    onChanged={upsert}
                    onEdit={() => setDialog({ open: true, editing: p })}
                    onDelete={() => setDeleting(p)}
                  />
                </div>
              ))}
            </div>
          )}
        </Loadable>
      </SettingsSection>

      <ProviderDialog
        open={dialog.open}
        editing={dialog.editing}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
        onSaved={upsert}
      />

      <ConfirmAction
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "provider"}?`}
        description="Agents using this model stop working until they are switched to another one. The saved key is erased and can't be recovered."
        confirmLabel="Delete provider"
        typeToConfirm={deleting?.name}
        onConfirm={async () => {
          if (!deleting) return;
          const id = deleting.id;
          try {
            await deleteProvider(id);
          } finally {
            // Gone either way: a false answer means someone else already removed it.
            void providers.reload();
          }
        }}
      />
    </SettingsPage>
  );
}
