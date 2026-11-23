import * as React from "react";
import { FileText, Pencil, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
} from "../../../components/ui/dialog";
import { Input, Textarea } from "../../../components/ui/input";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../../components/ui/menu";
import { Select } from "../../../components/ui/select";
import { Switch } from "../../../components/ui/switch";
import {
  Badge,
  EmptyState,
  ErrorState,
  Field,
  FormDialog,
  LoadingRows,
  ResourceRow,
  SaveStatusLine,
  useResource,
  useSetting,
} from "../../ui";
import {
  MAX_CONTEXT_CHARS,
  loadContext,
  updateDoc,
  type DocInput,
  type KnowledgeDoc,
  type Named,
  type Scope,
} from "./api";

export function scopeLabel(
  doc: Pick<KnowledgeDoc, "scope" | "scopeId">,
  teams: Named[],
  projects: Named[],
): string {
  if (doc.scope === "workspace") return "Whole workspace";
  if (doc.scope === "team") {
    const t = teams.find((x) => x.id === doc.scopeId);
    return t ? `Team ${t.name}` : "A team you can't see";
  }
  const p = projects.find((x) => x.id === doc.scopeId);
  return p ? `Project ${p.name}` : "A project you can't see";
}

function updatedLine(doc: KnowledgeDoc): string {
  const d = new Date(doc.updatedAt);
  const when = Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      });
  const who = doc.updatedBy?.displayName || doc.updatedBy?.name;
  return [when && `Updated ${when}`, who && `by ${who}`]
    .filter(Boolean)
    .join(" ");
}

function excerpt(body: string): string {
  return body
    .replace(/[#>*_`-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 160);
}

/** One document: the switch saves instantly (the only place `enabled` is changed). */
export function DocRow({
  doc,
  scope,
  onSaved,
  onEdit,
  onDelete,
}: {
  doc: KnowledgeDoc;
  scope: string;
  onSaved: (d: KnowledgeDoc) => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const setting = useSetting({
    value: doc.enabled,
    save: async (next) => onSaved(await updateDoc(doc.id, { enabled: next })),
  });
  return (
    <div data-knowledge-doc={doc.title} className="grid">
      <ResourceRow
        leading={
          <span
            aria-hidden="true"
            className="grid size-8 place-items-center rounded-lg border border-line bg-subtle text-muted-foreground"
          >
            <FileText className="size-4" />
          </span>
        }
        title={doc.title}
        badges={!setting.value && <Badge>Off</Badge>}
        meta={excerpt(doc.body) || updatedLine(doc)}
        menuLabel={`Actions for ${doc.title}`}
        actions={
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
        }
      >
        <Switch
          checked={setting.value}
          onCheckedChange={(v) => void setting.set(v)}
          aria-label={`Give agents "${doc.title}"`}
        />
      </ResourceRow>
      <div className="-mt-1.5 flex flex-wrap gap-x-3 pr-(--ds-settings-row-padding) pb-2.5 pl-15 text-sm text-muted-foreground empty:hidden">
        {setting.status.state === "idle" ? (
          <span>{[scope, updatedLine(doc)].filter(Boolean).join(" · ")}</span>
        ) : (
          <SaveStatusLine status={setting.status} />
        )}
      </div>
    </div>
  );
}

const SCOPE_OPTIONS = [
  {
    value: "workspace",
    label: "Whole workspace",
    description: "Every agent, in every team and project.",
  },
  {
    value: "team",
    label: "One team",
    description: "Agents working on that team's tasks.",
  },
  {
    value: "project",
    label: "One project",
    description: "Agents working on that project's tasks.",
  },
];

/** Create or edit a document. Closes only after the server saved it. */
export function DocDialog({
  open,
  onOpenChange,
  doc,
  teams,
  projects,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  doc: KnowledgeDoc | null;
  teams: Named[];
  projects: Named[];
  onSubmit: (input: DocInput) => Promise<unknown>;
}) {
  const [scope, setScope] = React.useState<Scope>("workspace");
  const [scopeId, setScopeId] = React.useState("");
  const [title, setTitle] = React.useState("");
  const [body, setBody] = React.useState("");
  React.useEffect(() => {
    if (!open) return;
    setScope(doc?.scope ?? "workspace");
    setScopeId(doc?.scopeId ?? "");
    setTitle(doc?.title ?? "");
    setBody(doc?.body ?? "");
  }, [open, doc]);
  const targets = scope === "team" ? teams : projects;
  const needsTarget = scope !== "workspace";
  const canSubmit =
    !!title.trim() && !!body.trim() && (!needsTarget || !!scopeId);
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title={doc ? "Edit document" : "New document"}
      description="Write it the way you'd brief a new teammate: conventions, architecture decisions, words to use and avoid."
      submitLabel={doc ? "Save changes" : "Add document"}
      canSubmit={canSubmit}
      onSubmit={() =>
        onSubmit({
          scope,
          scopeId: needsTarget ? scopeId : null,
          title: title.trim(),
          body: body.trim(),
        })
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Applies to"
          htmlFor="knowledge-scope"
          help="Narrower documents are added after broader ones."
        >
          <Select
            id="knowledge-scope"
            className="sm:w-full"
            value={scope}
            onValueChange={(v) => {
              setScope(v as Scope);
              setScopeId(
                v === "team"
                  ? (teams[0]?.id ?? "")
                  : v === "project"
                    ? (projects[0]?.id ?? "")
                    : "",
              );
            }}
            options={SCOPE_OPTIONS}
          />
        </Field>
        {needsTarget && (
          <Field
            label={scope === "team" ? "Team" : "Project"}
            htmlFor="knowledge-target"
            error={
              targets.length
                ? null
                : scope === "team"
                  ? "There are no teams to choose from."
                  : "There are no projects yet. Create one first, or choose another scope."
            }
          >
            <Select
              id="knowledge-target"
              className="sm:w-full"
              value={scopeId || undefined}
              onValueChange={setScopeId}
              disabled={!targets.length}
              placeholder={
                scope === "team" ? "Choose a team" : "Choose a project"
              }
              options={targets.map((t) => ({
                value: t.id,
                label: t.key ? `${t.name} (${t.key})` : t.name,
              }))}
            />
          </Field>
        )}
      </div>
      <Field label="Title" htmlFor="knowledge-title">
        <Input
          id="knowledge-title"
          autoComplete="off"
          placeholder="Architecture principles"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </Field>
      <Field
        label="Content"
        htmlFor="knowledge-body"
        help={`Markdown. ${body.length.toLocaleString()} characters; agents receive at most ${MAX_CONTEXT_CHARS.toLocaleString()} across all documents that apply.`}
      >
        <Textarea
          id="knowledge-body"
          rows={14}
          className="font-mono text-sm"
          placeholder={
            "## Conventions\n\n- Use snake_case in Python\n- Every API change needs a test"
          }
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </Field>
    </FormDialog>
  );
}

/** What an agent actually receives for a given team and project. */
export function PreviewDialog({
  open,
  onOpenChange,
  teams,
  projects,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  teams: Named[];
  projects: Named[];
}) {
  const [teamId, setTeamId] = React.useState("none");
  const [projectId, setProjectId] = React.useState("none");
  const ctx = useResource(
    () =>
      open
        ? loadContext(
            teamId === "none" ? null : teamId,
            projectId === "none" ? null : projectId,
          )
        : Promise.resolve(""),
    [open, teamId, projectId],
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader
          title="What agents read"
          description="Enabled documents merged in order: workspace, then team, then project. Pick where an agent is working to see exactly what it receives."
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Team" htmlFor="knowledge-preview-team">
            <Select
              id="knowledge-preview-team"
              className="sm:w-full"
              value={teamId}
              onValueChange={setTeamId}
              options={[
                { value: "none", label: "No team" },
                ...teams.map((t) => ({ value: t.id, label: t.name })),
              ]}
            />
          </Field>
          <Field label="Project" htmlFor="knowledge-preview-project">
            <Select
              id="knowledge-preview-project"
              className="sm:w-full"
              value={projectId}
              onValueChange={setProjectId}
              options={[
                { value: "none", label: "No project" },
                ...projects.map((p) => ({ value: p.id, label: p.name })),
              ]}
            />
          </Field>
        </div>
        {ctx.error ? (
          <ErrorState
            title="Couldn't build the preview"
            error={ctx.error}
            onRetry={ctx.reload}
          />
        ) : ctx.loading && ctx.data === undefined ? (
          <LoadingRows rows={2} />
        ) : ctx.data ? (
          <div className="grid gap-1.5">
            <pre
              aria-label="Merged knowledge"
              tabIndex={0}
              className="m-0 max-h-80 overflow-auto rounded-lg border border-line bg-subtle p-3 font-mono text-sm leading-relaxed whitespace-pre-wrap text-foreground"
            >
              {ctx.data}
            </pre>
            <p className="m-0 text-sm text-muted-foreground">
              {ctx.data.length.toLocaleString()} of{" "}
              {MAX_CONTEXT_CHARS.toLocaleString()} characters
              {ctx.data.length >= MAX_CONTEXT_CHARS - 200
                ? ". Anything past the limit is cut off."
                : "."}
            </p>
          </div>
        ) : (
          <EmptyState
            icon={<FileText />}
            title="Agents get no knowledge here"
            description="No enabled document applies to this team and project."
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
