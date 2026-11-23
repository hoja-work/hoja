import * as React from "react";
import { Archive, Copy, Layers3, Pencil, Plus } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input, Label, Textarea } from "../../components/ui/input";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import { Select } from "../../components/ui/select";
import { Checkbox } from "../../components/ui/switch";
import { useSettings } from "../context";
import {
  ConfirmAction,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  Notice,
  ResourceRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
} from "../ui";
import { ColorPicker } from "./project-statuses/ColorPicker";
import { IconPicker, TemplateMark } from "./project-templates/IconPicker";

/* Project templates (HOJA-489). A template supplies name, description, lead, teams, the
 * starting status and a task set; projectCreate(templateId) expands it server-side and
 * creates the task set as real tasks. Create and edit happen in one dialog; archive asks. */

type Template = {
  id: string;
  name: string;
  description: string | null;
  defaultState: string | null;
  taskSet: string[];
  icon: string | null;
  color: string | null;
  lead: { id: string; name?: string | null } | null;
  teams: { id: string; name: string; key: string }[];
};
type Member = { id: string; name?: string | null; email?: string | null };
type StatusOption = { id: string; name: string };

const TEMPLATE_FIELDS =
  "id name description defaultState taskSet icon color lead{id name} teams{id name key}";

type Draft = {
  name: string;
  description: string;
  defaultState: string;
  icon: string | null;
  color: string | null;
  leadId: string;
  teamIds: string[];
  taskSet: string;
};
const FIRST = "__first";
const NOBODY = "__none";
const blank: Draft = {
  name: "",
  description: "",
  defaultState: FIRST,
  icon: null,
  color: null,
  leadId: NOBODY,
  teamIds: [],
  taskSet: "",
};
const toDraft = (t: Template, copy = false): Draft => ({
  name: copy ? `${t.name} copy` : t.name,
  description: t.description || "",
  defaultState: t.defaultState || FIRST,
  icon: t.icon,
  color: t.color,
  leadId: t.lead?.id || NOBODY,
  teamIds: t.teams.map((x) => x.id),
  taskSet: t.taskSet.join("\n"),
});
const tasksOf = (d: Draft) =>
  d.taskSet
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);
const toInput = (d: Draft) => ({
  name: d.name.trim(),
  description: d.description.trim() || null,
  state: d.defaultState === FIRST ? null : d.defaultState,
  icon: d.icon,
  color: d.color,
  leadId: d.leadId === NOBODY ? null : d.leadId,
  teamIds: d.teamIds,
  taskSet: tasksOf(d),
});

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

async function loadAll() {
  const d = await gql<{ projectTemplates: Template[] }>(
    `{projectTemplates{${TEMPLATE_FIELDS}}}`,
  );
  // Options for the dialog. A failure here must not hide the templates themselves.
  const [statuses, members] = await Promise.all([
    gql<{ projectStatuses: StatusOption[] }>("{projectStatuses{id name}}")
      .then((r) => r.projectStatuses)
      .catch(() => null),
    gql<{ organizationMembers: { nodes: Member[] } }>(
      "{organizationMembers{nodes{id name email}}}",
    )
      .then((r) => r.organizationMembers.nodes)
      .catch(() => null),
  ]);
  return { templates: d.projectTemplates, statuses, members };
}

export default function ProjectTemplatesSettings() {
  const resource = useResource(loadAll);
  const [dialog, setDialog] = React.useState<{
    editing: Template | null;
    draft: Draft;
  } | null>(null);
  const [archiving, setArchiving] = React.useState<Template | null>(null);

  const upsert = (t: Template) =>
    resource.setData((cur) =>
      cur
        ? {
            ...cur,
            templates: cur.templates.some((x) => x.id === t.id)
              ? cur.templates.map((x) => (x.id === t.id ? t : x))
              : [...cur.templates, t],
          }
        : cur,
    );

  const create = () => setDialog({ editing: null, draft: blank });

  return (
    <SettingsPage
      title="Project templates"
      description="Starting points for new projects: a description, lead, teams, starting status and a set of tasks that are created with the project."
      actions={
        <Button variant="primary" onClick={create} disabled={!resource.data}>
          <Plus className="size-3.5" aria-hidden="true" />
          New template
        </Button>
      }
    >
      <SettingsSection title="Templates" bare>
        <Loadable
          resource={resource}
          rows={3}
          isEmpty={(d) => d.templates.length === 0}
          empty={
            <EmptyState
              icon={<Layers3 />}
              title="No project templates yet"
              description="Create a template for the kinds of project your teams start again and again, like a launch or an onboarding."
              action={
                <Button variant="primary" onClick={create}>
                  New template
                </Button>
              }
            />
          }
        >
          {(d) => (
            <SettingsCard>
              {d.templates.map((t) => (
                <ResourceRow
                  key={t.id}
                  leading={<TemplateMark icon={t.icon} color={t.color} />}
                  title={t.name}
                  onOpen={() => setDialog({ editing: t, draft: toDraft(t) })}
                  meta={[
                    t.description,
                    t.teams.length
                      ? t.teams.map((x) => x.name).join(", ")
                      : "No team",
                    plural(t.taskSet.length, "task"),
                    `Starts in ${t.defaultState || "the first status"}`,
                    t.lead?.name ? `Lead ${t.lead.name}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  menuLabel={`Actions for template ${t.name}`}
                  actions={
                    <>
                      <DropdownMenuItem
                        onSelect={() =>
                          setDialog({ editing: t, draft: toDraft(t) })
                        }
                      >
                        <Pencil aria-hidden="true" />
                        Edit…
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() =>
                          setDialog({ editing: null, draft: toDraft(t, true) })
                        }
                      >
                        <Copy aria-hidden="true" />
                        Duplicate…
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        destructive
                        onSelect={() => setArchiving(t)}
                      >
                        <Archive aria-hidden="true" />
                        Archive…
                      </DropdownMenuItem>
                    </>
                  }
                />
              ))}
            </SettingsCard>
          )}
        </Loadable>
      </SettingsSection>

      <Notice title="How templates are used">
        A project created with a template through the API (projectCreate with
        templateId) gets every field the request leaves out from the template,
        and its tasks are created in one of the project&apos;s teams. The new
        project form in the app does not offer templates yet.
      </Notice>

      {dialog && (
        <TemplateDialog
          key={dialog.editing?.id || "new"}
          editing={dialog.editing}
          initial={dialog.draft}
          statuses={resource.data?.statuses ?? null}
          members={resource.data?.members ?? null}
          onClose={() => setDialog(null)}
          onSaved={upsert}
        />
      )}

      <ConfirmAction
        open={!!archiving}
        onOpenChange={(o) => !o && setArchiving(null)}
        title={`Archive “${archiving?.name ?? ""}”?`}
        description="It stops being available for new projects. Projects already created from it, and their tasks, are not changed. Archived templates cannot be restored here."
        confirmLabel="Archive template"
        onConfirm={async () => {
          if (!archiving) return;
          const d = await gql<{ projectTemplateArchive: boolean }>(
            "mutation($id:String!){projectTemplateArchive(id:$id)}",
            { id: archiving.id },
          );
          if (!d.projectTemplateArchive)
            throw new Error(
              "This template was already archived. Reload the page to see the current list.",
            );
          const gone = archiving.id;
          resource.setData((cur) =>
            cur
              ? {
                  ...cur,
                  templates: cur.templates.filter((x) => x.id !== gone),
                }
              : cur,
          );
        }}
      />
    </SettingsPage>
  );
}

function TemplateDialog({
  editing,
  initial,
  statuses,
  members,
  onClose,
  onSaved,
}: {
  editing: Template | null;
  initial: Draft;
  statuses: StatusOption[] | null;
  members: Member[] | null;
  onClose: () => void;
  onSaved: (t: Template) => void;
}) {
  const { teams } = useSettings();
  const [open, setOpen] = React.useState(true);
  const [draft, setDraft] = React.useState<Draft>(initial);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));
  const tasks = tasksOf(draft);
  const teamError =
    tasks.length > 0 && draft.teamIds.length === 0
      ? "Pick at least one team. Tasks always belong to a team."
      : null;

  // A template may point at a status or member this list no longer offers; keep it visible.
  const statusOptions = [
    {
      value: FIRST,
      label: "The first status",
      description: statuses?.[0]
        ? `Currently “${statuses[0].name}”`
        : undefined,
    },
    ...(statuses || []).map((s) => ({ value: s.name, label: s.name })),
    ...(draft.defaultState !== FIRST &&
    statuses &&
    !statuses.some((s) => s.name === draft.defaultState)
      ? [{ value: draft.defaultState, label: draft.defaultState }]
      : []),
  ];
  const leadOptions = [
    { value: NOBODY, label: "No lead" },
    ...(members || []).map((m) => ({
      value: m.id,
      label: m.name || m.email || "Unnamed member",
    })),
    ...(draft.leadId !== NOBODY &&
    !(members || []).some((m) => m.id === draft.leadId)
      ? [{ value: draft.leadId, label: editing?.lead?.name || "Former lead" }]
      : []),
  ];
  const teamList = [
    ...teams.map((t) => ({ id: t.id, name: t.name, key: t.key })),
    ...(editing?.teams || []).filter((t) => !teams.some((x) => x.id === t.id)),
  ];

  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) window.setTimeout(onClose, 150);
      }}
      wide
      title={editing ? `Edit “${editing.name}”` : "New project template"}
      submitLabel={editing ? "Save template" : "Create template"}
      canSubmit={!!draft.name.trim() && !teamError}
      onSubmit={async () => {
        const input = toInput(draft);
        if (editing) {
          const d = await gql<{
            projectTemplateUpdate: {
              success: boolean;
              projectTemplate: Template;
            };
          }>(
            `mutation($id:String!,$input:ProjectTemplateInput!){projectTemplateUpdate(id:$id,input:$input){success projectTemplate{${TEMPLATE_FIELDS}}}}`,
            { id: editing.id, input },
          );
          if (!d.projectTemplateUpdate?.success)
            throw new Error("The template was not saved. Try again.");
          onSaved(d.projectTemplateUpdate.projectTemplate);
        } else {
          const d = await gql<{
            projectTemplateCreate: {
              success: boolean;
              projectTemplate: Template;
            };
          }>(
            `mutation($input:ProjectTemplateInput!){projectTemplateCreate(input:$input){success projectTemplate{${TEMPLATE_FIELDS}}}}`,
            { input },
          );
          if (!d.projectTemplateCreate?.success)
            throw new Error("The template was not created. Try again.");
          onSaved(d.projectTemplateCreate.projectTemplate);
        }
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="template-name">
          <Input
            id="template-name"
            autoFocus
            maxLength={80}
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
          />
        </Field>
        <Field
          label="Starting status"
          htmlFor="template-state"
          help={
            statuses
              ? undefined
              : "Couldn't load the status list; the current choice is kept."
          }
        >
          <Select
            id="template-state"
            value={draft.defaultState}
            onValueChange={(v) => set("defaultState", v)}
            options={statusOptions}
            className="sm:w-full"
          />
        </Field>
      </div>
      <Field label="Description" htmlFor="template-description" optional>
        <Textarea
          id="template-description"
          rows={2}
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Icon" htmlFor="template-icon" optional>
          <IconPicker
            id="template-icon"
            value={draft.icon}
            onChange={(v) => set("icon", v)}
          />
        </Field>
        <Field label="Colour" htmlFor="template-color" optional>
          <ColorPicker
            id="template-color"
            label="Template colour"
            value={draft.color}
            onChange={(v) => set("color", v)}
          />
        </Field>
      </div>
      <Field
        label="Lead"
        htmlFor="template-lead"
        optional
        help={
          members
            ? undefined
            : "Couldn't load members; the current lead is kept."
        }
      >
        <Select
          id="template-lead"
          value={draft.leadId}
          onValueChange={(v) => set("leadId", v)}
          options={leadOptions}
          className="sm:w-full"
        />
      </Field>
      <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
        <legend className="mb-1.5 p-0 text-sm font-medium">Teams</legend>
        {teamList.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {teamList.map((t) => {
              const id = `template-team-${t.id}`;
              const checked = draft.teamIds.includes(t.id);
              return (
                <div
                  key={t.id}
                  className="flex min-w-0 items-center gap-2 pointer-coarse:min-h-11"
                >
                  <Checkbox
                    id={id}
                    checked={checked}
                    onCheckedChange={(c) =>
                      set(
                        "teamIds",
                        c
                          ? [...draft.teamIds, t.id]
                          : draft.teamIds.filter((x) => x !== t.id),
                      )
                    }
                  />
                  <Label htmlFor={id} className="truncate font-normal">
                    {t.name}
                  </Label>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="m-0 text-sm text-muted-foreground">
            There are no teams yet. Create one under Teams first.
          </p>
        )}
        {teamError ? (
          <p role="alert" className="m-0 text-sm text-destructive">
            {teamError}
          </p>
        ) : (
          <p className="m-0 text-sm text-muted-foreground">
            The project joins every team you pick.
          </p>
        )}
      </fieldset>
      <Field
        label="Tasks"
        htmlFor="template-tasks"
        optional
        help={
          tasks.length
            ? `${plural(tasks.length, "task")} will be created with each project. One title per line.`
            : "One task title per line. They are created with each project."
        }
      >
        <Textarea
          id="template-tasks"
          rows={4}
          placeholder={"Write the brief\nShip the build"}
          value={draft.taskSet}
          onChange={(e) => set("taskSet", e.target.value)}
        />
      </Field>
    </FormDialog>
  );
}
