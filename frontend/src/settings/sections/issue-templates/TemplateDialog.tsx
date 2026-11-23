import * as React from "react";
import { Input, Textarea } from "../../../components/ui/input";
import { Select } from "../../../components/ui/select";
import { Checkbox } from "../../../components/ui/switch";
import { Field, FormDialog } from "../../ui";
import { LabelDot } from "../issue-labels/ColorSwatches";
import {
  PRIORITY_LABEL,
  saveTemplate,
  toInput,
  type TaskTemplate,
  type TemplateData,
  type TemplateInput,
} from "./model";

// Radix Select items cannot have an empty value, so "nothing chosen" gets a sentinel.
const NONE = "none";
const WORKSPACE = "workspace";
const NAME_MAX = 80;

const blank: TemplateInput = {
  name: "",
  description: null,
  teamId: null,
  defaultTitle: "",
  defaultDescription: null,
  defaultPriority: null,
  defaultAssigneeId: null,
  defaultProjectId: null,
  defaultLabelIds: [],
};

export function TemplateDialog({
  open,
  onOpenChange,
  template,
  copyOf,
  data,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  template?: TaskTemplate;
  /** Prefill from another template (Duplicate); saving creates a new one. */
  copyOf?: TaskTemplate;
  data: TemplateData;
  onSaved: (t: TaskTemplate) => void;
}) {
  const ids = {
    name: React.useId(),
    scope: React.useId(),
    description: React.useId(),
    title: React.useId(),
    body: React.useId(),
    priority: React.useId(),
    assignee: React.useId(),
    project: React.useId(),
  };
  const [draft, setDraft] = React.useState<TemplateInput>(blank);
  const [touched, setTouched] = React.useState<Record<string, boolean>>({});
  React.useEffect(() => {
    if (!open) return;
    const source = template || copyOf;
    const base = source ? toInput(source) : blank;
    setDraft(
      copyOf && !template ? { ...base, name: `${base.name} (copy)` } : base,
    );
    setTouched({});
  }, [open, template, copyOf]);
  const set = <K extends keyof TemplateInput>(k: K, v: TemplateInput[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const usable = data.catalog.usableBy(draft.teamId);
  const labelOptions = data.catalog.labels
    .filter((l) => usable.has(l.id))
    .sort((a, b) => a.name.localeCompare(b.name));

  const nameProblem = !draft.name.trim()
    ? "Give the template a name."
    : draft.name.trim().length > NAME_MAX
      ? `Keep the name to ${NAME_MAX} characters.`
      : null;
  const titleProblem = !draft.defaultTitle.trim()
    ? "A template needs a default title. A prefix such as “Bug: ” works."
    : null;
  const nameError =
    touched.name || draft.name.length > NAME_MAX ? nameProblem : null;
  const titleError = touched.title ? titleProblem : null;

  return (
    <FormDialog
      wide
      open={open}
      onOpenChange={onOpenChange}
      title={template ? `Edit “${template.name}”` : "New task template"}
      description="A task created from this template gets these values for any field it leaves blank."
      submitLabel={template ? "Save template" : "Create template"}
      canSubmit={!nameProblem && !titleProblem}
      onSubmit={async () => {
        const input: TemplateInput = {
          ...draft,
          name: draft.name.trim(),
          description: draft.description?.trim() || null,
          defaultDescription: draft.defaultDescription?.trim() || null,
          // The default title is stored exactly as written: "Bug: " keeps its space.
          defaultLabelIds: draft.defaultLabelIds.filter((id) => usable.has(id)),
        };
        onSaved(await saveTemplate(template?.id || null, input));
      }}
    >
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <Field label="Template name" htmlFor={ids.name} error={nameError}>
          <Input
            id={ids.name}
            autoFocus
            autoComplete="off"
            value={draft.name}
            placeholder="e.g. Bug report"
            aria-invalid={!!nameError || undefined}
            onChange={(e) => set("name", e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, name: true }))}
          />
        </Field>
        <Field
          label="Available to"
          htmlFor={ids.scope}
          help="A team template can use that team's labels."
        >
          <Select
            id={ids.scope}
            className="sm:w-full"
            value={draft.teamId || WORKSPACE}
            onValueChange={(v) => {
              const teamId = v === WORKSPACE ? null : v;
              // Labels the new scope may not use are dropped here, not refused on save.
              const keep = data.catalog.usableBy(teamId);
              setDraft((d) => ({
                ...d,
                teamId,
                defaultLabelIds: d.defaultLabelIds.filter((id) => keep.has(id)),
              }));
            }}
            options={[
              { value: WORKSPACE, label: "Every team" },
              ...data.catalog.teams.map((t) => ({
                value: t.id,
                label: `${t.name} only`,
              })),
            ]}
          />
        </Field>
      </div>
      <Field
        label="Description"
        htmlFor={ids.description}
        optional
        help="Tells people when to pick this template."
      >
        <Input
          id={ids.description}
          autoComplete="off"
          value={draft.description || ""}
          onChange={(e) => set("description", e.target.value)}
        />
      </Field>

      <fieldset className="m-0 grid min-w-0 gap-4 border-0 border-t border-line p-0 pt-4">
        <legend className="sr-only">Defaults for new tasks</legend>
        <p className="m-0 text-sm font-medium" aria-hidden="true">
          Defaults for new tasks
        </p>
        <Field label="Title" htmlFor={ids.title} error={titleError}>
          <Input
            id={ids.title}
            autoComplete="off"
            value={draft.defaultTitle}
            placeholder="Bug: "
            aria-invalid={!!titleError || undefined}
            onChange={(e) => set("defaultTitle", e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, title: true }))}
          />
        </Field>
        <Field label="Description" htmlFor={ids.body} optional>
          <Textarea
            id={ids.body}
            rows={4}
            value={draft.defaultDescription || ""}
            placeholder={"## Steps to reproduce\n\n## Expected"}
            onChange={(e) => set("defaultDescription", e.target.value)}
          />
        </Field>
        <div className="grid items-start gap-4 sm:grid-cols-3">
          <Field label="Priority" htmlFor={ids.priority}>
            <Select
              id={ids.priority}
              className="sm:w-full"
              value={
                draft.defaultPriority === null
                  ? NONE
                  : String(draft.defaultPriority)
              }
              onValueChange={(v) =>
                set("defaultPriority", v === NONE ? null : Number(v))
              }
              options={[
                {
                  value: NONE,
                  label: "Not set",
                  description: "The task keeps whatever priority it is given.",
                },
                ...[1, 2, 3, 4, 0].map((p) => ({
                  value: String(p),
                  label: PRIORITY_LABEL[p],
                })),
              ]}
            />
          </Field>
          <Field label="Assignee" htmlFor={ids.assignee}>
            <Select
              id={ids.assignee}
              className="sm:w-full"
              value={draft.defaultAssigneeId || NONE}
              onValueChange={(v) =>
                set("defaultAssigneeId", v === NONE ? null : v)
              }
              options={[
                { value: NONE, label: "Nobody" },
                ...data.people.map((p) => ({
                  value: p.id,
                  label: p.name || p.email,
                })),
              ]}
            />
          </Field>
          <Field label="Project" htmlFor={ids.project}>
            <Select
              id={ids.project}
              className="sm:w-full"
              value={draft.defaultProjectId || NONE}
              onValueChange={(v) =>
                set("defaultProjectId", v === NONE ? null : v)
              }
              options={[
                { value: NONE, label: "None" },
                ...data.projects.map((p) => ({ value: p.id, label: p.name })),
              ]}
            />
          </Field>
        </div>
        <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
          <legend className="mb-1.5 p-0 text-sm font-medium">Labels</legend>
          {labelOptions.length ? (
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {labelOptions.map((l) => (
                <LabelCheck
                  key={l.id}
                  name={l.name}
                  color={l.color}
                  team={l.team?.key}
                  checked={draft.defaultLabelIds.includes(l.id)}
                  onChange={(on) =>
                    set(
                      "defaultLabelIds",
                      on
                        ? [...draft.defaultLabelIds, l.id]
                        : draft.defaultLabelIds.filter((x) => x !== l.id),
                    )
                  }
                />
              ))}
            </div>
          ) : (
            <p className="m-0 text-sm text-muted-foreground">
              {draft.teamId
                ? "No labels this team can use yet. Add them under Labels."
                : "No workspace labels yet. Add them under Labels."}
            </p>
          )}
        </fieldset>
      </fieldset>
    </FormDialog>
  );
}

function LabelCheck({
  name,
  color,
  team,
  checked,
  onChange,
}: {
  name: string;
  color?: string | null;
  team?: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  const id = React.useId();
  return (
    <div className="flex items-center gap-2">
      <Checkbox
        id={id}
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
      />
      <label htmlFor={id} className="flex items-center gap-1.5 text-base">
        <LabelDot color={color} />
        {name}
        {team && <span className="text-sm text-muted-foreground">{team}</span>}
      </label>
    </div>
  );
}
