/* Types, GraphQL fields and calls shared by the initiative list, page and dialogs. */
import { gql } from "../main";

export type Person = { id: string; name?: string; avatarSeed?: string | null };
export type InitiativeLabel = {
  id: string;
  name: string;
  color?: string | null;
};
export type InitiativeRef = {
  id: string;
  identifier: string;
  name: string;
  icon?: string | null;
  color?: string | null;
  status?: string;
};
export type InitiativeProject = {
  id: string;
  name: string;
  state?: string | null;
  progress?: number | null;
  health?: string | null;
  targetDate?: string | null;
  lead?: { name?: string } | null;
};
export type InitiativeRow = InitiativeRef & {
  summary?: string | null;
  status: string;
  priority?: number;
  targetDate?: string | null;
  createdAt?: string;
  sortOrder?: number | null;
  viewerSubscribed?: boolean;
  health?: string | null;
  owner?: Person | null;
  creator?: { id: string } | null;
  parent?: { id: string } | null;
  leadTeam?: { id: string; name: string; key: string } | null;
  labels?: InitiativeLabel[];
  projects?: { nodes: InitiativeProject[] };
  allProjects?: InitiativeProject[];
};

export const LIST_FIELDS = `id identifier number name summary status priority targetDate createdAt sortOrder icon color viewerSubscribed health owner{id name avatarSeed} creator{id} parent{id} leadTeam{id name key} labels{id name color} projects{nodes{id name progress health}} allProjects{id progress state}`;

export const INITIATIVE_STATUS_IDS = [
  "planned",
  "active",
  "completed",
  "canceled",
] as const;

/** Project progress is stored as a percent (0-100); rings take a fraction. */
export const progressFraction = (projects?: { progress?: number | null }[]) =>
  projects && projects.length
    ? projects.reduce((sum, p) => sum + (p.progress || 0), 0) /
      projects.length /
      100
    : 0;

export async function fetchAllInitiatives(): Promise<InitiativeRow[]> {
  const rows: InitiativeRow[] = [];
  let after: string | null = null;
  for (let page = 0; page < 20; page++) {
    const d = await gql(
      `query InitiativeList($first:Int,$after:String){initiatives(first:$first,after:$after){nodes{${LIST_FIELDS}} pageInfo{hasNextPage endCursor}}}`,
      { first: 100, after },
    );
    rows.push(...(d.initiatives?.nodes || []));
    if (!d.initiatives?.pageInfo?.hasNextPage) break;
    after = d.initiatives.pageInfo.endCursor;
  }
  return rows;
}

export async function fetchLabels(): Promise<InitiativeLabel[]> {
  const d = await gql(`query{initiativeLabels{id name color}}`);
  return d.initiativeLabels || [];
}

export async function createLabel(name: string): Promise<InitiativeLabel> {
  const d = await gql(
    `mutation($name:String!){initiativeLabelCreate(name:$name){id name color}}`,
    { name },
  );
  return d.initiativeLabelCreate;
}

export async function updateInitiative(
  id: string,
  input: Record<string, unknown>,
  fields = "id",
) {
  const d = await gql(
    `mutation($id:String!,$input:InitiativeUpdateInput!){initiativeUpdate(id:$id,input:$input){success initiative{${fields}}}}`,
    { id, input },
  );
  if (!d.initiativeUpdate?.success)
    throw Error("The initiative could not be updated.");
  return d.initiativeUpdate.initiative;
}

export async function createInitiative(
  input: Record<string, unknown>,
): Promise<InitiativeRow> {
  const d = await gql(
    `mutation($input:InitiativeCreateInput!){initiativeCreate(input:$input){success initiative{${LIST_FIELDS}}}}`,
    { input },
  );
  if (!d.initiativeCreate?.success || !d.initiativeCreate.initiative)
    throw Error("The initiative could not be created.");
  return d.initiativeCreate.initiative;
}

/* Saved views of kind "initiative". `filters` uses the server's view filter language
 * ({status|health|owner|leadTeam|label: {is|isEitherOf: ...}}); `display` holds the
 * list's grouping, ordering and nesting. */
export type InitiativeView = {
  id: string;
  name: string;
  filters: Record<string, { is?: string; isEitherOf?: string[] }>;
  display: Record<string, unknown>;
};

export async function fetchInitiativeViews(): Promise<InitiativeView[]> {
  const d = await gql(
    `query{views(kind:"initiative"){id name filters display}}`,
  );
  return d.views || [];
}

export async function saveInitiativeView(
  name: string,
  filters: InitiativeView["filters"],
  display: Record<string, unknown>,
): Promise<InitiativeView> {
  const d = await gql(
    `mutation($input:ViewCreateInput!){viewCreate(input:$input){success view{id name filters display}}}`,
    { input: { name, kind: "initiative", filters, display } },
  );
  if (!d.viewCreate?.success || !d.viewCreate.view)
    throw Error("The view could not be saved.");
  return d.viewCreate.view;
}

export async function deleteInitiativeView(id: string) {
  const d = await gql(`mutation($id:String!){viewDelete(id:$id){success}}`, {
    id,
  });
  if (!d.viewDelete?.success) throw Error("The view could not be deleted.");
}
