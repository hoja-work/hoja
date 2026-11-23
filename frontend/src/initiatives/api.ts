/* Types, GraphQL fields and calls shared by the initiative list, page, dialogs and
 * settings. */
import { gql } from "../main";

export type Person = { id: string; name?: string; avatarSeed?: string | null };
export type InitiativeLabel = {
  id: string;
  name: string;
  color?: string | null;
  isGroup?: boolean;
  exclusive?: boolean;
  parent?: { id: string; name: string } | null;
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
  teams?: { nodes: { id: string; name?: string; key?: string }[] };
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
  parents?: { id: string }[];
  leadTeam?: { id: string; name: string; key: string } | null;
  labels?: InitiativeLabel[];
  projects?: { nodes: InitiativeProject[] };
  allProjects?: InitiativeProject[];
};

export const LIST_FIELDS = `id identifier number name summary status priority targetDate createdAt sortOrder icon color viewerSubscribed health owner{id name avatarSeed} creator{id} parents{id} leadTeam{id name key} labels{id name color} projects{nodes{id name progress health}} allProjects{id progress state teams{nodes{id}}}`;

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

const LABEL_FIELDS = "id name color isGroup exclusive parent{id name}";

export async function fetchLabels(): Promise<InitiativeLabel[]> {
  const d = await gql(`query{initiativeLabels{${LABEL_FIELDS}}}`);
  return d.initiativeLabels || [];
}

export async function createLabel(
  name: string,
  opts: {
    parentId?: string | null;
    isGroup?: boolean;
    color?: string | null;
  } = {},
): Promise<InitiativeLabel> {
  const d = await gql(
    `mutation($name:String!,$p:String,$g:Boolean,$c:String){initiativeLabelCreate(name:$name,parentId:$p,isGroup:$g,color:$c){${LABEL_FIELDS}}}`,
    {
      name,
      p: opts.parentId || null,
      g: Boolean(opts.isGroup),
      c: opts.color || null,
    },
  );
  return d.initiativeLabelCreate;
}

export async function updateLabel(
  id: string,
  patch: {
    name?: string;
    color?: string;
    exclusive?: boolean;
    parentId?: string;
  },
): Promise<InitiativeLabel> {
  const d = await gql(
    `mutation($id:String!,$n:String,$c:String,$x:Boolean,$p:String){initiativeLabelUpdate(id:$id,name:$n,color:$c,exclusive:$x,parentId:$p){${LABEL_FIELDS}}}`,
    {
      id,
      n: patch.name ?? null,
      c: patch.color ?? null,
      x: patch.exclusive ?? null,
      p: patch.parentId ?? null,
    },
  );
  return d.initiativeLabelUpdate;
}

export async function archiveLabel(id: string) {
  const d = await gql(
    `mutation($id:String!){initiativeLabelArchive(id:$id){success}}`,
    { id },
  );
  if (!d.initiativeLabelArchive?.success)
    throw Error("The label could not be archived.");
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

/* Saved views. On the list they are kind "initiative" with the server's filter language
 * ({status|health|owner|leadTeam|label: {is|isEitherOf: ...}}) and a display document
 * (grouping, ordering, nesting). On an initiative page they are kind "project", scoped
 * to the initiative, filtering its projects by state and team. */
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

export type PageView = InitiativeView & { initiative?: { id: string } | null };

export async function fetchPageViews(
  initiativeId: string,
): Promise<PageView[]> {
  const d = await gql(
    `query{views(kind:"project",scope:"initiative"){id name filters display initiative{id}}}`,
  );
  return ((d.views || []) as PageView[]).filter(
    (v) => v.initiative?.id === initiativeId,
  );
}

export async function savePageView(
  initiativeId: string,
  name: string,
  filters: InitiativeView["filters"],
): Promise<PageView> {
  const d = await gql(
    `mutation($input:ViewCreateInput!){viewCreate(input:$input){success view{id name filters display initiative{id}}}}`,
    {
      input: {
        name,
        kind: "project",
        scope: "initiative",
        initiativeId,
        filters,
      },
    },
  );
  if (!d.viewCreate?.success || !d.viewCreate.view)
    throw Error("The view could not be saved.");
  return d.viewCreate.view;
}

export type ReminderPolicy = {
  cadence: string;
  weekday: number;
  hour: number;
  followUps: boolean;
};
export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

export async function fetchReminderPolicy(): Promise<ReminderPolicy> {
  const d = await gql(
    `query{initiativeReminderPolicy{cadence weekday hour followUps}}`,
  );
  return d.initiativeReminderPolicy;
}

export async function saveReminderPolicy(
  input: Partial<ReminderPolicy>,
): Promise<ReminderPolicy> {
  const d = await gql(
    `mutation($i:InitiativeReminderPolicyInput!){initiativeReminderPolicyUpdate(input:$i){cadence weekday hour followUps}}`,
    { i: input },
  );
  return d.initiativeReminderPolicyUpdate;
}

export const policyLabel = (p?: ReminderPolicy | null) =>
  !p || p.cadence === "never"
    ? "No workspace schedule"
    : `${{ weekly: "Every", biweekly: "Every other", monthly: "First" }[p.cadence] || ""} ${WEEKDAYS[p.weekday]}${p.cadence === "monthly" ? " of the month" : ""} at ${String(p.hour).padStart(2, "0")}:00`;

export type SlackChannel = { id: string; name: string; isPrivate: boolean };
export async function fetchSlackChannels(): Promise<SlackChannel[]> {
  const d = await gql(`query{slackChannels{id name isPrivate}}`);
  return d.slackChannels || [];
}

export async function setTeamShowsInitiatives(teamId: string, show: boolean) {
  await gql(
    `mutation($t:String!,$s:Boolean!){teamShowInitiatives(teamId:$t,show:$s){id}}`,
    { t: teamId, s: show },
  );
}
