import * as React from "react";
import { Download, Eye, FileUp, LayoutTemplate } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input, Label, Textarea } from "../../components/ui/input";
import { DropdownMenuItem } from "../../components/ui/menu";
import { Checkbox } from "../../components/ui/switch";
import { toast } from "../../components/ui/toast";
import {
  Badge,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  Notice,
  ResourceRow,
  SettingsPage,
  SettingsSection,
  errorMessage,
  useResource,
} from "../ui";
import {
  SECTION_LABEL,
  SECTION_ORDER,
  applyTemplate,
  contents,
  describeFields,
  exportWorkspace,
  fileName,
  loadBuiltins,
  parseDocument,
  previewTemplate,
  type PlanStep,
  type TemplateDocument,
  type TemplatePlan,
} from "./workspace-templates/model";

function download(doc: TemplateDocument) {
  const blob = new Blob([JSON.stringify(doc, null, 2) + "\n"], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName(doc.name);
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* Workspace templates (HOJA-742). Admin-only on the server and in the registry. A template
 * sets up labels, task and project templates, SLA policies, house rules for agents and the
 * defaults for new agents and members in one step. Applying is always previewed first, and
 * never deletes anything. */
export default function WorkspaceTemplatesSettings() {
  const builtins = useResource(loadBuiltins, []);
  const [previewing, setPreviewing] = React.useState<TemplateDocument | null>(
    null,
  );
  const [importing, setImporting] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);

  const exportNow = async () => {
    setExporting(true);
    try {
      const { document: doc, omitted } = await exportWorkspace();
      download(doc);
      toast({
        title: "Downloaded this workspace's settings as a template",
        description: omitted.length
          ? `Left out because they only exist here: ${omitted.join(", ")}.`
          : undefined,
      });
    } catch (e) {
      toast({ title: errorMessage(e), tone: "error" });
    } finally {
      setExporting(false);
    }
  };

  return (
    <SettingsPage
      title="Workspace templates"
      description="Set up labels, task and project templates, SLA policies, house rules for agents and notification defaults in one step. You see every change before it happens, and applying a template never deletes anything."
      actions={
        <>
          <Button onClick={exportNow} loading={exporting}>
            <Download className="size-3.5" aria-hidden="true" />
            Export
          </Button>
          <Button variant="primary" onClick={() => setImporting(true)}>
            <FileUp className="size-3.5" aria-hidden="true" />
            Import…
          </Button>
        </>
      }
    >
      <SettingsSection
        title="Ready-made templates"
        description="Start from one of these, then export your workspace to keep your own version."
      >
        <Loadable
          resource={builtins}
          rows={3}
          isEmpty={(d) => d.length === 0}
          empty={
            <EmptyState
              icon={<LayoutTemplate />}
              title="No ready-made templates"
              description="Import a template file to set up this workspace."
            />
          }
        >
          {(list) =>
            list.map((t) => (
              <ResourceRow
                key={t.id}
                title={t.name}
                meta={
                  <span className="grid gap-0.5">
                    <span>{t.description}</span>
                    <span>{contents(t.document)}</span>
                  </span>
                }
                onOpen={() => setPreviewing(t.document)}
                menuLabel={`Actions for ${t.name}`}
                actions={
                  <>
                    <DropdownMenuItem
                      onSelect={() => setPreviewing(t.document)}
                    >
                      <Eye aria-hidden="true" />
                      Preview and apply…
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => download(t.document)}>
                      <Download aria-hidden="true" />
                      Download to customize
                    </DropdownMenuItem>
                  </>
                }
              />
            ))
          }
        </Loadable>
      </SettingsSection>
      <Notice title="How applying works">
        A template adds what is missing and updates what it names, matching by
        name. Settings it doesn't mention stay as they are. Notification
        defaults apply to people who haven't chosen for themselves, and agent
        defaults apply to agents created afterwards.
      </Notice>
      <ImportDialog
        open={importing}
        onOpenChange={setImporting}
        onLoaded={(doc) => {
          setImporting(false);
          setPreviewing(doc);
        }}
      />
      <ApplyDialog
        document={previewing}
        onOpenChange={(o) => !o && setPreviewing(null)}
      />
    </SettingsPage>
  );
}

function ImportDialog({
  open,
  onOpenChange,
  onLoaded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLoaded: (doc: TemplateDocument) => void;
}) {
  const [text, setText] = React.useState("");
  React.useEffect(() => {
    if (open) setText("");
  }, [open]);
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Import a template"
      description="Choose a template file, or paste its contents. You'll see what it changes before anything is applied."
      submitLabel="Preview changes"
      canSubmit={text.trim().length > 0}
      onSubmit={async () => onLoaded(parseDocument(text))}
    >
      <Field label="Template file" htmlFor="template-file" optional>
        <Input
          id="template-file"
          type="file"
          accept="application/json,.json"
          onChange={async (e) => {
            const file = e.currentTarget.files?.[0];
            if (file) setText(await file.text());
          }}
        />
      </Field>
      <Field label="Template contents" htmlFor="template-text">
        <Textarea
          id="template-text"
          rows={8}
          className="font-mono text-sm"
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
          placeholder='{ "format": "hoja.workspace-template", "version": 1, "name": "…" }'
        />
      </Field>
    </FormDialog>
  );
}

function ApplyDialog({
  document: doc,
  onOpenChange,
}: {
  document: TemplateDocument | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [plan, setPlan] = React.useState<TemplatePlan | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [startClocks, setStartClocks] = React.useState(false);
  React.useEffect(() => {
    setPlan(null);
    setError(null);
    setStartClocks(false);
    if (!doc) return;
    let live = true;
    previewTemplate(doc)
      .then((p) => live && setPlan(p))
      .catch((e) => live && setError(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [doc]);
  const changes = plan ? plan.creates + plan.updates : 0;
  const hasSla = plan?.steps.some(
    (s) => s.section === "slaPolicies" && s.action !== "unchanged",
  );
  return (
    <FormDialog
      open={!!doc}
      onOpenChange={onOpenChange}
      wide
      title={doc ? `Apply “${doc.name}”?` : "Apply template"}
      description={
        doc?.description ||
        "Here is everything this template changes in this workspace."
      }
      submitLabel={
        plan && changes === 0 ? "Nothing to change" : "Apply template"
      }
      canSubmit={!!plan && changes > 0}
      onSubmit={async () => {
        if (!doc) return;
        const done = await applyTemplate(doc, startClocks);
        toast({
          title: `Applied “${done.name}”`,
          description: [
            done.creates && `${done.creates} added`,
            done.updates && `${done.updates} updated`,
            done.startedClocks &&
              `SLA clocks started on ${done.startedClocks} open tasks`,
          ]
            .filter(Boolean)
            .join(" · "),
        });
      }}
    >
      {error && <Notice tone="error">{error}</Notice>}
      {!plan && !error && (
        <p className="m-0 text-sm text-muted-foreground">
          Checking what this template changes…
        </p>
      )}
      {plan && <PlanList plan={plan} />}
      {plan && hasSla && (
        <div className="flex items-start gap-2">
          <Checkbox
            id="start-sla-clocks"
            checked={startClocks}
            onCheckedChange={(v) => setStartClocks(v === true)}
          />
          <Label htmlFor="start-sla-clocks" className="grid gap-0.5">
            <span className="text-base">
              Start SLA clocks for open tasks now
            </span>
            <span className="text-sm text-muted-foreground">
              Otherwise only tasks created from now on get a clock.
            </span>
          </Label>
        </div>
      )}
    </FormDialog>
  );
}

function PlanList({ plan }: { plan: TemplatePlan }) {
  const changed = plan.steps.filter((s) => s.action !== "unchanged");
  const bySection = SECTION_ORDER.map((section) => ({
    section,
    steps: changed.filter((s) => s.section === section),
  })).filter((g) => g.steps.length > 0);
  return (
    <div className="grid gap-4">
      <p className="m-0 text-sm text-muted-foreground">
        {plan.creates} to add · {plan.updates} to update · {plan.unchanged}{" "}
        already in place
      </p>
      {bySection.length === 0 && (
        <Notice title="Already in place">
          Everything in this template is already set up here.
        </Notice>
      )}
      {bySection.length > 0 && (
        /* The list scrolls on its own so Apply and Cancel stay in view however long the
         * template is. Focusable so a keyboard can scroll it too. */
        <div
          role="region"
          aria-label="Changes this template makes"
          tabIndex={0}
          className="grid max-h-80 gap-4 overflow-y-auto rounded-md border border-line p-4 focus-visible:outline-2 focus-visible:outline-ring"
        >
          {bySection.map((g) => (
            <div key={g.section} className="grid gap-1">
              <h3 className="m-0 text-base font-medium">
                {SECTION_LABEL[g.section] ?? g.section}
              </h3>
              <ul className="m-0 grid list-none gap-1 p-0">
                {g.steps.map((s) => (
                  <StepRow key={`${s.section}:${s.name}`} step={s} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StepRow({ step }: { step: PlanStep }) {
  return (
    <li className="flex min-w-0 items-baseline gap-2 text-sm">
      <Badge>{step.action === "create" ? "Add" : "Update"}</Badge>
      <span className="min-w-0 break-words">
        {step.name}
        {step.action === "update" && step.fields.length > 0 && (
          <span className="text-muted-foreground">
            {" "}
            · {describeFields(step.fields)}
          </span>
        )}
      </span>
    </li>
  );
}
