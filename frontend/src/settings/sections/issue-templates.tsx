import * as React from "react";
import { Archive, Copy, LayoutTemplate, PenLine, Plus } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Loadable,
  Notice,
  ResourceRow,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
} from "../ui";
import {
  PRIORITY_LABEL,
  loadTemplates,
  type TaskTemplate,
  type TemplateData,
} from "./issue-templates/model";
import { TemplateDialog } from "./issue-templates/TemplateDialog";

function summary(t: TaskTemplate) {
  const parts = [`Title “${t.defaultTitle}”`];
  if (t.defaultPriority !== null && t.defaultPriority !== undefined)
    parts.push(
      PRIORITY_LABEL[t.defaultPriority] || `Priority ${t.defaultPriority}`,
    );
  if (t.defaultAssignee)
    parts.push(t.defaultAssignee.name || t.defaultAssignee.email);
  if (t.defaultProject) parts.push(t.defaultProject.name);
  if (t.defaultLabels.length)
    parts.push(t.defaultLabels.map((l) => l.name).join(", "));
  return parts.join(" · ");
}

/* Task templates (HOJA-492). Admin-only on the server, and marked admin in the registry,
 * so everyone who reaches this page can edit it. */
export default function TaskTemplatesSettings() {
  const data = useResource(loadTemplates, []);
  const [dialog, setDialog] = React.useState<
    | { mode: "create" }
    | { mode: "edit" | "copy"; template: TaskTemplate }
    | null
  >(null);
  const [archiving, setArchiving] = React.useState<TaskTemplate | null>(null);

  const upsert = (t: TaskTemplate) =>
    data.setData((d) =>
      d
        ? {
            ...d,
            templates: d.templates.some((x) => x.id === t.id)
              ? d.templates.map((x) => (x.id === t.id ? t : x))
              : [...d.templates, t],
          }
        : d,
    );

  return (
    <SettingsPage
      title="Task templates"
      description="Saved starting points for common tasks, such as a bug report. A task created from a template gets its title, description, priority, assignee, project and labels wherever the task leaves them blank."
      actions={
        <Button variant="primary" onClick={() => setDialog({ mode: "create" })}>
          <Plus className="size-3.5" aria-hidden="true" />
          New template
        </Button>
      }
    >
      <Notice title="Where templates apply">
        Templates are applied when a task is created through the API or by an
        agent over MCP with a template id. The new-task form in the app doesn’t
        offer them yet.
      </Notice>
      <Loadable
        resource={data}
        rows={3}
        isEmpty={(d) => d.templates.length === 0}
        empty={
          <EmptyState
            icon={<LayoutTemplate />}
            title="No task templates yet"
            description="Create one for the task people file most often, so every one of them starts with the same title, labels and checklist."
            action={
              <Button
                variant="primary"
                onClick={() => setDialog({ mode: "create" })}
              >
                <Plus className="size-3.5" aria-hidden="true" />
                Create a template
              </Button>
            }
          />
        }
      >
        {(d) => (
          <TemplateGroups
            data={d}
            onEdit={(t) => setDialog({ mode: "edit", template: t })}
            onCopy={(t) => setDialog({ mode: "copy", template: t })}
            onArchive={setArchiving}
          />
        )}
      </Loadable>
      {data.data && (
        <TemplateDialog
          open={!!dialog}
          onOpenChange={(o) => !o && setDialog(null)}
          template={dialog?.mode === "edit" ? dialog.template : undefined}
          copyOf={dialog?.mode === "copy" ? dialog.template : undefined}
          data={data.data}
          onSaved={upsert}
        />
      )}
      <ConfirmAction
        open={!!archiving}
        onOpenChange={(o) => !o && setArchiving(null)}
        title={`Archive “${archiving?.name ?? ""}”?`}
        description="Tasks already created from it don't change. Creating a task with this template stops working, so update any script or integration that uses it."
        confirmLabel="Archive template"
        onConfirm={async () => {
          if (!archiving) return;
          const r = await gql<{ taskTemplateArchive: boolean }>(
            "mutation TemplateArchive($id:String!){taskTemplateArchive(id:$id)}",
            { id: archiving.id },
          );
          if (!r.taskTemplateArchive)
            throw new Error(
              "This template was already archived. Reload to see the current list.",
            );
          const id = archiving.id;
          data.setData((d) =>
            d ? { ...d, templates: d.templates.filter((x) => x.id !== id) } : d,
          );
        }}
      />
    </SettingsPage>
  );
}

function TemplateGroups({
  data,
  onEdit,
  onCopy,
  onArchive,
}: {
  data: TemplateData;
  onEdit: (t: TaskTemplate) => void;
  onCopy: (t: TaskTemplate) => void;
  onArchive: (t: TaskTemplate) => void;
}) {
  const byName = (a: TaskTemplate, b: TaskTemplate) =>
    a.name.localeCompare(b.name);
  const workspace = data.templates.filter((t) => !t.team).sort(byName);
  const teamIds = [
    ...new Set(data.templates.filter((t) => t.team).map((t) => t.team!.id)),
  ];
  const groups = teamIds
    .map((id) => {
      const list = data.templates.filter((t) => t.team?.id === id).sort(byName);
      return { team: list[0].team!, list };
    })
    .sort((a, b) => a.team.name.localeCompare(b.team.name));
  const row = (t: TaskTemplate) => (
    <ResourceRow
      key={t.id}
      title={t.name}
      meta={t.description || summary(t)}
      badges={t.team ? <Badge>{t.team.key}</Badge> : undefined}
      onOpen={() => onEdit(t)}
      menuLabel={`Actions for ${t.name}`}
      actions={
        <>
          <DropdownMenuItem onSelect={() => onEdit(t)}>
            <PenLine aria-hidden="true" />
            Edit…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => onCopy(t)}>
            <Copy aria-hidden="true" />
            Duplicate…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem destructive onSelect={() => onArchive(t)}>
            <Archive aria-hidden="true" />
            Archive…
          </DropdownMenuItem>
        </>
      }
    />
  );
  return (
    <>
      {workspace.length > 0 && (
        <SettingsSection
          title="Every team"
          description="Any team can create tasks from these."
        >
          {workspace.map(row)}
        </SettingsSection>
      )}
      {groups.map((g) => (
        <SettingsSection
          key={g.team.id}
          title={g.team.name}
          description={`Only for ${g.team.name}. These can use ${g.team.name}'s own labels.`}
        >
          {g.list.map(row)}
        </SettingsSection>
      ))}
    </>
  );
}
