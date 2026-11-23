import { gql } from "../../ui";

/* Workspace templates (HOJA-742): the GraphQL surface and the words the screen shows. */

export type TemplateDocument = {
  format: string;
  version: number;
  id?: string;
  name: string;
  description?: string;
  [section: string]: unknown;
};

export type BuiltinTemplate = {
  id: string;
  name: string;
  description: string;
  document: TemplateDocument;
};

export type PlanStep = {
  section: string;
  name: string;
  action: "create" | "update" | "unchanged";
  fields: string[];
};

export type TemplatePlan = {
  name: string;
  steps: PlanStep[];
  creates: number;
  updates: number;
  unchanged: number;
  startedClocks: number;
};

const PLAN =
  "name creates updates unchanged startedClocks steps { section name action fields }";

export async function loadBuiltins(): Promise<BuiltinTemplate[]> {
  const r = await gql<{ workspaceTemplates: BuiltinTemplate[] }>(
    "query WorkspaceTemplates { workspaceTemplates { id name description document } }",
  );
  return r.workspaceTemplates;
}

export async function previewTemplate(
  document: TemplateDocument,
): Promise<TemplatePlan> {
  const r = await gql<{ workspaceTemplatePreview: TemplatePlan }>(
    `query WorkspaceTemplatePreview($d: JSON!) { workspaceTemplatePreview(document: $d) { ${PLAN} } }`,
    { d: document },
  );
  return r.workspaceTemplatePreview;
}

export async function applyTemplate(
  document: TemplateDocument,
  startSlaClocks: boolean,
): Promise<TemplatePlan> {
  const r = await gql<{ workspaceTemplateApply: TemplatePlan }>(
    `mutation WorkspaceTemplateApply($d: JSON!, $s: Boolean) { workspaceTemplateApply(document: $d, startSlaClocks: $s) { ${PLAN} } }`,
    { d: document, s: startSlaClocks },
  );
  return r.workspaceTemplateApply;
}

export async function exportWorkspace(): Promise<{
  document: TemplateDocument;
  omitted: string[];
}> {
  const r = await gql<{
    workspaceTemplateExport: { document: TemplateDocument; omitted: string[] };
  }>(
    "query WorkspaceTemplateExport { workspaceTemplateExport { document omitted } }",
  );
  return r.workspaceTemplateExport;
}

export const SECTION_LABEL: Record<string, string> = {
  labels: "Labels",
  taskTemplates: "Task templates",
  projectTemplates: "Project templates",
  slaPolicies: "SLA policies",
  knowledge: "House rules and knowledge",
  agentPolicyDefault: "Agent defaults",
  notificationDefaults: "Notification defaults",
};

export const SECTION_ORDER = Object.keys(SECTION_LABEL);

const FIELD_LABEL: Record<string, string> = {
  color: "color",
  description: "description",
  is_group: "group",
  exclusive: "one per task",
  _group: "group",
  default_title: "title",
  default_description: "description",
  default_priority: "priority",
  _labels: "labels",
  state: "status",
  icon: "icon",
  applies_priorities: "priorities",
  first_response_minutes: "first response target",
  resolution_minutes: "resolution target",
  pause_state_ids: "pause states",
  cycle_bound: "cycle deadline",
  stops_on: "what stops the clock",
  business_days_only: "business days",
  enabled: "on or off",
  body: "text",
};

/** "resolution target, pause states" for an update step's changed fields. */
export function describeFields(fields: string[]): string {
  return fields
    .map(
      (f) =>
        FIELD_LABEL[f] ?? f.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase(),
    )
    .join(", ");
}

/** What a template holds, for its row: "6 labels · 5 SLA policies · house rules". */
export function contents(doc: TemplateDocument): string {
  const parts: string[] = [];
  const count = (key: string, one: string, many: string) => {
    const list = doc[key];
    if (Array.isArray(list) && list.length)
      parts.push(`${list.length} ${list.length === 1 ? one : many}`);
  };
  count("labels", "label", "labels");
  count("taskTemplates", "task template", "task templates");
  count("projectTemplates", "project template", "project templates");
  count("slaPolicies", "SLA policy", "SLA policies");
  count("knowledge", "knowledge document", "knowledge documents");
  if (doc.agentPolicyDefault) parts.push("agent defaults");
  if (
    doc.notificationDefaults &&
    Object.keys(doc.notificationDefaults as object).length
  )
    parts.push("notification defaults");
  return parts.join(" · ");
}

/** Parse a pasted or uploaded file; throws a sentence a person can act on. */
export function parseDocument(text: string): TemplateDocument {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new Error(
      "This isn't valid JSON. Export a template from Hoja, or check the file for a missing comma or bracket.",
    );
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc))
    throw new Error(
      "A template is a JSON object with a format, a version and a name.",
    );
  const d = doc as TemplateDocument;
  if (d.format !== "hoja.workspace-template")
    throw new Error(
      "This file isn't a Hoja workspace template. Its format should be “hoja.workspace-template”.",
    );
  return d;
}

/** A file name for a downloaded template: "agents-do-most-of-the-writing.hoja-template.json". */
export function fileName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `${slug || "workspace"}.hoja-template.json`;
}
