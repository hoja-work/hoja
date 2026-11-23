import { gql } from "../../ui";
import { loadLabelCatalog, type LabelCatalog } from "../issue-labels/scopes";

export type TaskTemplate = {
  id: string;
  name: string;
  description: string | null;
  team: { id: string; key: string; name: string } | null;
  defaultTitle: string;
  defaultDescription: string | null;
  defaultPriority: number | null;
  defaultAssignee: { id: string; name: string | null; email: string } | null;
  defaultProject: { id: string; name: string } | null;
  defaultLabels: { id: string; name: string; color: string | null }[];
};
export type TemplateInput = {
  name: string;
  description: string | null;
  teamId: string | null;
  defaultTitle: string;
  defaultDescription: string | null;
  defaultPriority: number | null;
  defaultAssigneeId: string | null;
  defaultProjectId: string | null;
  defaultLabelIds: string[];
};
export type Person = {
  id: string;
  name: string | null;
  email: string;
  active?: boolean;
};

const FIELDS =
  "id name description team{id key name} defaultTitle defaultDescription defaultPriority defaultAssignee{id name email} defaultProject{id name} defaultLabels{id name color}";

export const PRIORITY_LABEL: Record<number, string> = {
  0: "No priority",
  1: "Urgent",
  2: "High",
  3: "Medium",
  4: "Low",
};

export type TemplateData = {
  templates: TaskTemplate[];
  people: Person[];
  projects: { id: string; name: string }[];
  catalog: LabelCatalog;
};

/* The legacy pane asked for IssueLabel.team, a field that does not exist, so the whole
 * options query failed and the project and label pickers were always empty. Labels and
 * their owning team now come from the shared label catalog; people come from the
 * workspace member list, not team rosters (a member in no team was unpickable). */
export async function loadTemplates(): Promise<TemplateData> {
  const [d, catalog] = await Promise.all([
    gql<{
      taskTemplates: TaskTemplate[];
      organizationMembers: { nodes: Person[] };
      projects: { nodes: { id: string; name: string }[] };
    }>(
      `query TemplateSettings{taskTemplates{${FIELDS}} organizationMembers(first:250){nodes{id name email active}} projects(first:250){nodes{id name}}}`,
    ),
    loadLabelCatalog(),
  ]);
  return {
    templates: d.taskTemplates,
    people: d.organizationMembers.nodes.filter((p) => p.active !== false),
    projects: d.projects.nodes,
    catalog,
  };
}

type Payload = { success: boolean; taskTemplate: TaskTemplate | null };

export async function saveTemplate(id: string | null, input: TemplateInput) {
  const d = id
    ? await gql<{ taskTemplateUpdate: Payload }>(
        `mutation TemplateUpdate($id:String!,$input:TaskTemplateInput!){taskTemplateUpdate(id:$id,input:$input){success taskTemplate{${FIELDS}}}}`,
        { id, input },
      )
    : await gql<{ taskTemplateCreate: Payload }>(
        `mutation TemplateCreate($input:TaskTemplateInput!){taskTemplateCreate(input:$input){success taskTemplate{${FIELDS}}}}`,
        { input },
      );
  const out =
    "taskTemplateUpdate" in d ? d.taskTemplateUpdate : d.taskTemplateCreate;
  if (!out?.success || !out.taskTemplate)
    throw new Error("The template wasn't saved. Try again.");
  return out.taskTemplate;
}

export function toInput(t: TaskTemplate): TemplateInput {
  return {
    name: t.name,
    description: t.description,
    teamId: t.team?.id || null,
    defaultTitle: t.defaultTitle,
    defaultDescription: t.defaultDescription,
    defaultPriority: t.defaultPriority,
    defaultAssigneeId: t.defaultAssignee?.id || null,
    defaultProjectId: t.defaultProject?.id || null,
    defaultLabelIds: t.defaultLabels.map((l) => l.id),
  };
}
