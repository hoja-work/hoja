import * as React from "react";
import { Palette, PenLine, Plus, Tags, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverTrigger,
} from "../../components/ui/popover";
import { Select } from "../../components/ui/select";
import { useSettings } from "../context";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  Notice,
  ResourceRow,
  SaveStatusLine,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
  useSetting,
} from "../ui";
import {
  ColorSwatches,
  DEFAULT_LABEL_COLOR,
  LabelDot,
  colorName,
} from "./issue-labels/ColorSwatches";
import {
  loadLabelCatalog,
  type LabelCatalog,
  type LabelTeam,
  type ScopedLabel,
} from "./issue-labels/scopes";

const NAME_MAX = 64;
const DESCRIPTION_MAX = 240;
const WORKSPACE = "workspace";

function validateName(name: string): string | null {
  const n = name.trim();
  if (!n) return "Give the label a name.";
  if (n.length > NAME_MAX)
    return `Keep the name to ${NAME_MAX} characters (it has ${n.length}).`;
  return null;
}
function validateDescription(d: string): string | null {
  return d.trim().length > DESCRIPTION_MAX
    ? `Keep the description to ${DESCRIPTION_MAX} characters.`
    : null;
}

type LabelInput = { name?: string; color?: string; description?: string };

async function updateLabel(id: string, input: LabelInput) {
  const d = await gql<{
    issueLabelUpdate: { success: boolean } | null;
  }>(
    "mutation LabelUpdate($id:String!,$input:IssueLabelUpdateInput!){issueLabelUpdate(id:$id,input:$input){success issueLabel{id name color description}}}",
    { id, input },
  );
  if (!d.issueLabelUpdate?.success)
    throw new Error(
      "This label no longer exists. Reload the page to see the current list.",
    );
}

/* Task labels. The backend checks the label.manage capability (admins, plus members an admin
 * allowed), so the UI does too; everyone else sees the list read-only with the reason. */
export default function IssueLabelsSettings() {
  const { can } = useSettings();
  const canManage = can("label.manage");
  const catalog = useResource(loadLabelCatalog, []);
  const [creating, setCreating] = React.useState(false);

  const patchLocal = (id: string, patch: Partial<ScopedLabel> | null) =>
    catalog.setData((c) =>
      c
        ? {
            ...c,
            labels:
              patch === null
                ? c.labels.filter((l) => l.id !== id)
                : c.labels.map((l) => (l.id === id ? { ...l, ...patch } : l)),
          }
        : c,
    );

  return (
    <SettingsPage
      title="Labels"
      description="Labels group related tasks across teams so they are easy to find and filter. Anyone in the workspace can put a label on a task."
      actions={
        /* The empty state carries the one "New label" button when there are none. */
        canManage && (catalog.data?.labels.length ?? 0) > 0 ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus className="size-3.5" aria-hidden="true" />
            New label
          </Button>
        ) : undefined
      }
    >
      {!canManage && (
        <Notice tone="locked" title="You can use labels but not change them">
          Creating, renaming and deleting labels needs the “Manage labels”
          permission. Ask a workspace admin if a label is missing.
        </Notice>
      )}
      <Loadable
        resource={catalog}
        rows={4}
        isEmpty={(c) => c.labels.length === 0}
        empty={
          <EmptyState
            icon={<Tags />}
            title="No labels yet"
            description={
              canManage
                ? "Start with a few broad ones, like Bug, Feature and Improvement. You can add more as patterns appear."
                : "Nobody has created a label yet. Ask a workspace admin to add the ones your team needs."
            }
            action={
              canManage ? (
                <Button variant="primary" onClick={() => setCreating(true)}>
                  <Plus className="size-3.5" aria-hidden="true" />
                  New label
                </Button>
              ) : undefined
            }
          />
        }
      >
        {(c) => (
          <LabelGroups
            catalog={c}
            canManage={canManage}
            onChanged={patchLocal}
          />
        )}
      </Loadable>
      <LabelDialog
        open={creating}
        onOpenChange={setCreating}
        teams={catalog.data?.teams || []}
        onCreated={(label) =>
          catalog.setData((c) =>
            c ? { ...c, labels: [...c.labels, label] } : c,
          )
        }
      />
    </SettingsPage>
  );
}

function LabelGroups({
  catalog,
  canManage,
  onChanged,
}: {
  catalog: LabelCatalog;
  canManage: boolean;
  onChanged: (id: string, patch: Partial<ScopedLabel> | null) => void;
}) {
  const byName = (a: ScopedLabel, b: ScopedLabel) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  const workspace = catalog.labels.filter((l) => !l.team).sort(byName);
  const teamGroups = catalog.teams
    .map((t) => ({
      team: t,
      labels: catalog.labels.filter((l) => l.team?.id === t.id).sort(byName),
    }))
    .filter((g) => g.labels.length > 0);
  const nameCounts = new Map<string, number>();
  for (const l of catalog.labels)
    nameCounts.set(
      l.name.toLowerCase(),
      (nameCounts.get(l.name.toLowerCase()) || 0) + 1,
    );
  const row = (l: ScopedLabel) => (
    <LabelRow
      key={l.id}
      label={l}
      canManage={canManage}
      uniqueName={nameCounts.get(l.name.toLowerCase()) === 1}
      onChanged={onChanged}
    />
  );
  return (
    <>
      {workspace.length > 0 && (
        <SettingsSection
          title="Workspace labels"
          description={`${workspace.length} ${workspace.length === 1 ? "label" : "labels"} every team can use.`}
        >
          {workspace.map(row)}
        </SettingsSection>
      )}
      {teamGroups.map((g) => (
        <SettingsSection
          key={g.team.id}
          title={`${g.team.name} labels`}
          description={`Only ${g.team.name} and its sub-teams can use these.`}
        >
          {g.labels.map(row)}
        </SettingsSection>
      ))}
    </>
  );
}

function LabelRow({
  label,
  canManage,
  uniqueName,
  onChanged,
}: {
  label: ScopedLabel;
  canManage: boolean;
  uniqueName: boolean;
  onChanged: (id: string, patch: Partial<ScopedLabel> | null) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [colorOpen, setColorOpen] = React.useState(false);
  const pickerRef = React.useRef<HTMLDivElement>(null);
  const color = useSetting<string>({
    value: label.color || "",
    save: async (next) => {
      await updateLabel(label.id, { color: next });
      onChanged(label.id, { color: next });
    },
  });
  const meta =
    color.status.state === "idle" ? (
      label.description || undefined
    ) : (
      <SaveStatusLine status={color.status} />
    );
  const leading = canManage ? (
    <Popover open={colorOpen} onOpenChange={setColorOpen}>
      <PopoverAnchor asChild>
        <span className="flex">
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Colour of ${label.name}: ${colorName(color.value)}. Change colour`}
            >
              <LabelDot color={color.value} />
            </Button>
          </PopoverTrigger>
        </span>
      </PopoverAnchor>
      <PopoverContent
        ref={pickerRef}
        align="start"
        className="w-auto max-w-72 p-3"
        // Opening from the ⋯ menu: the menu hands focus back to its trigger as it closes.
        // That focus move must not close the picker; clicks outside and Escape still do.
        onFocusOutside={(e) => e.preventDefault()}
      >
        <p className="m-0 mb-2 text-sm font-medium">Colour</p>
        <ColorSwatches
          label={`Colour for ${label.name}`}
          value={color.value}
          onChange={(c) => {
            void color.set(c);
            setColorOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  ) : (
    <span className="grid size-7 place-items-center">
      <LabelDot color={label.color} />
    </span>
  );
  return (
    <>
      <ResourceRow
        leading={leading}
        title={label.name}
        meta={meta}
        badges={label.team ? <Badge>{label.team.key}</Badge> : undefined}
        menuLabel={`Actions for ${label.name}`}
        actions={
          canManage ? (
            <>
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <PenLine aria-hidden="true" />
                Rename…
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  setColorOpen(true);
                  // Once the menu has returned focus to ⋯, move it into the picker.
                  window.setTimeout(() => {
                    pickerRef.current
                      ?.querySelector<HTMLElement>(
                        '[role="radio"][data-state="checked"], [role="radio"]',
                      )
                      ?.focus();
                  }, 250);
                }}
              >
                <Palette aria-hidden="true" />
                Change colour
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
                <Trash2 aria-hidden="true" />
                Delete…
              </DropdownMenuItem>
            </>
          ) : undefined
        }
      />
      <LabelDialog
        open={editing}
        onOpenChange={setEditing}
        label={label}
        onSaved={(patch) => onChanged(label.id, patch)}
      />
      <DeleteLabel
        open={deleting}
        onOpenChange={setDeleting}
        label={label}
        uniqueName={uniqueName}
        onDeleted={() => onChanged(label.id, null)}
      />
    </>
  );
}

/** How many tasks carry this label. The task filter matches by name, so the count is
 *  only exact (and only shown) when no other label shares the name. */
function useLabelUsage(open: boolean, label: ScopedLabel, uniqueName: boolean) {
  const [count, setCount] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (!open || !uniqueName) return;
    let live = true;
    setCount(null);
    gql<{ issues: { nodes: { id: string }[] } | null }>(
      "query LabelUsage($name:String!){issues(filter:{labels:{name:{eq:$name}}},first:250){nodes{id}}}",
      { name: label.name },
    )
      .then((d) => live && setCount(d.issues?.nodes.length ?? null))
      .catch(() => live && setCount(null));
    return () => {
      live = false;
    };
  }, [open, uniqueName, label.name]);
  return count;
}

function DeleteLabel({
  open,
  onOpenChange,
  label,
  uniqueName,
  onDeleted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: ScopedLabel;
  uniqueName: boolean;
  onDeleted: () => void;
}) {
  const count = useLabelUsage(open, label, uniqueName);
  const usage =
    count === null
      ? "Every task that has it loses it."
      : count === 0
        ? "No task uses it right now."
        : count >= 250
          ? "More than 250 tasks use it; all of them lose it."
          : `${count} ${count === 1 ? "task uses" : "tasks use"} it and will lose it.`;
  return (
    <ConfirmAction
      open={open}
      onOpenChange={onOpenChange}
      title={`Delete “${label.name}”?`}
      description={`${usage} Saved views that filter on this label stop matching it. This can't be undone.`}
      confirmLabel="Delete label"
      onConfirm={async () => {
        const d = await gql<{ issueLabelDelete: { success: boolean } | null }>(
          "mutation LabelDelete($id:String!){issueLabelDelete(id:$id){success}}",
          { id: label.id },
        );
        if (!d.issueLabelDelete?.success)
          throw new Error(
            "This label was already deleted. Reload the page to see the current list.",
          );
        onDeleted();
      }}
    />
  );
}

/* Create (no `label`) or rename/describe (with `label`). Colour is chosen here on create;
 * an existing label changes colour from its swatch, which saves instantly. */
function LabelDialog({
  open,
  onOpenChange,
  label,
  teams = [],
  onCreated,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label?: ScopedLabel;
  teams?: LabelTeam[];
  onCreated?: (label: ScopedLabel) => void;
  onSaved?: (patch: Partial<ScopedLabel>) => void;
}) {
  const ids = {
    name: React.useId(),
    description: React.useId(),
    scope: React.useId(),
  };
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [color, setColor] = React.useState<string>(DEFAULT_LABEL_COLOR);
  const [scope, setScope] = React.useState(WORKSPACE);
  const [touched, setTouched] = React.useState(false);
  React.useEffect(() => {
    if (!open) return;
    setName(label?.name || "");
    setDescription(label?.description || "");
    setColor(label?.color || DEFAULT_LABEL_COLOR);
    setScope(WORKSPACE);
    setTouched(false);
  }, [open, label]);
  const nameError =
    touched || name.trim().length > NAME_MAX ? validateName(name) : null;
  const descriptionError = validateDescription(description);
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={label ? `Edit “${label.name}”` : "New label"}
      description={
        label
          ? "Renaming updates the label on every task that has it."
          : undefined
      }
      submitLabel={label ? "Save" : "Create label"}
      canSubmit={!validateName(name) && !descriptionError}
      onSubmit={async () => {
        const clean = {
          name: name.trim(),
          description: description.trim(),
        };
        if (label) {
          await updateLabel(label.id, clean);
          onSaved?.({ ...clean, description: clean.description || null });
          return;
        }
        const input = {
          ...clean,
          description: clean.description || undefined,
          color,
          teamId: scope === WORKSPACE ? undefined : scope,
        };
        const d = await gql<{
          issueLabelCreate: {
            success: boolean;
            issueLabel: Omit<ScopedLabel, "team"> | null;
          } | null;
        }>(
          "mutation LabelCreate($input:IssueLabelCreateInput!){issueLabelCreate(input:$input){success issueLabel{id name color description}}}",
          { input },
        );
        const made = d.issueLabelCreate;
        if (!made?.success || !made.issueLabel)
          throw new Error(
            "The label wasn't created. Check the name is 1–64 characters and try again.",
          );
        onCreated?.({
          ...made.issueLabel,
          team: teams.find((t) => t.id === input.teamId) || null,
        });
      }}
    >
      <Field
        label="Name"
        htmlFor={ids.name}
        error={nameError}
        help={`Up to ${NAME_MAX} characters.`}
      >
        <Input
          id={ids.name}
          autoFocus
          autoComplete="off"
          value={name}
          aria-invalid={!!nameError || undefined}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => setTouched(true)}
        />
      </Field>
      <Field
        label="Description"
        htmlFor={ids.description}
        optional
        error={descriptionError}
        help="Shown next to the label so people know when to use it."
      >
        <Input
          id={ids.description}
          autoComplete="off"
          value={description}
          aria-invalid={!!descriptionError || undefined}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      {!label && (
        <>
          <Field
            label="Available to"
            htmlFor={ids.scope}
            help="A team label can only be used by that team and its sub-teams. This can't be changed later."
          >
            <Select
              id={ids.scope}
              value={scope}
              onValueChange={setScope}
              className="sm:w-full"
              options={[
                { value: WORKSPACE, label: "Every team" },
                ...teams.map((t) => ({
                  value: t.id,
                  label: `${t.name} only`,
                })),
              ]}
            />
          </Field>
          <div className="grid gap-1.5">
            <span className="text-sm font-medium">Colour</span>
            <ColorSwatches label="Colour" value={color} onChange={setColor} />
          </div>
        </>
      )}
    </FormDialog>
  );
}
