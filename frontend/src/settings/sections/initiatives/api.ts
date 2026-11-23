/* Initiative settings calls over the settings transport (settings/ui gql), so errors are
 * normalised the same way as every other settings screen. Types and the schedule wording
 * are shared with the initiatives feature. */
import { gql } from "../../ui";
import type { InitiativeLabel, ReminderPolicy } from "../../../initiatives/api";

export type { InitiativeLabel, ReminderPolicy };
export { WEEKDAYS, policyLabel } from "../../../initiatives/api";

const LABEL_FIELDS = "id name color isGroup exclusive parent{id name}";
const POLICY_FIELDS = "cadence weekday hour followUps";

export type LabelData = {
  labels: InitiativeLabel[];
  /** label id → number of initiatives carrying it; null when it could not be counted. */
  usage: Record<string, number> | null;
};

export async function fetchLabelData(): Promise<LabelData> {
  const d = await gql<{ initiativeLabels: InitiativeLabel[] }>(
    `query{initiativeLabels{${LABEL_FIELDS}}}`,
  );
  let usage: Record<string, number> | null = {};
  try {
    let after: string | null = null;
    for (let page = 0; page < 10; page++) {
      const r: {
        initiatives: {
          nodes: { labels?: { id: string }[] }[];
          pageInfo: { hasNextPage: boolean; endCursor: string | null };
        };
      } = await gql(
        `query($after:String){initiatives(first:100,after:$after){nodes{labels{id}} pageInfo{hasNextPage endCursor}}}`,
        { after },
      );
      for (const n of r.initiatives.nodes)
        for (const l of n.labels || []) usage[l.id] = (usage[l.id] || 0) + 1;
      if (!r.initiatives.pageInfo.hasNextPage) break;
      after = r.initiatives.pageInfo.endCursor;
    }
  } catch {
    usage = null;
  }
  return { labels: d.initiativeLabels || [], usage };
}

export async function createLabel(
  name: string,
  opts: {
    parentId?: string | null;
    isGroup?: boolean;
    exclusive?: boolean;
    color?: string | null;
  } = {},
): Promise<InitiativeLabel> {
  const d = await gql<{ initiativeLabelCreate: InitiativeLabel }>(
    `mutation($name:String!,$p:String,$g:Boolean,$x:Boolean,$c:String){initiativeLabelCreate(name:$name,parentId:$p,isGroup:$g,exclusive:$x,color:$c){${LABEL_FIELDS}}}`,
    {
      name,
      p: opts.parentId || null,
      g: Boolean(opts.isGroup),
      x: opts.exclusive ?? true,
      c: opts.color || null,
    },
  );
  return d.initiativeLabelCreate;
}

/** `color: ""` clears the colour; `parentId: ""` takes the label out of its group. */
export async function updateLabel(
  id: string,
  patch: {
    name?: string;
    color?: string;
    exclusive?: boolean;
    parentId?: string;
  },
): Promise<InitiativeLabel> {
  const d = await gql<{ initiativeLabelUpdate: InitiativeLabel }>(
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
  const d = await gql<{ initiativeLabelArchive: { success: boolean } }>(
    `mutation($id:String!){initiativeLabelArchive(id:$id){success}}`,
    { id },
  );
  if (!d.initiativeLabelArchive?.success)
    throw new Error(
      "This label was already archived. Reload the page to see the current list.",
    );
}

export async function fetchReminderPolicy(): Promise<ReminderPolicy> {
  const d = await gql<{ initiativeReminderPolicy: ReminderPolicy }>(
    `query{initiativeReminderPolicy{${POLICY_FIELDS}}}`,
  );
  return d.initiativeReminderPolicy;
}

export async function saveReminderPolicy(
  input: Partial<ReminderPolicy>,
): Promise<ReminderPolicy> {
  const d = await gql<{ initiativeReminderPolicyUpdate: ReminderPolicy }>(
    `mutation($i:InitiativeReminderPolicyInput!){initiativeReminderPolicyUpdate(input:$i){${POLICY_FIELDS}}}`,
    { i: input },
  );
  return d.initiativeReminderPolicyUpdate;
}

export async function setTeamShowsInitiatives(teamId: string, show: boolean) {
  const d = await gql<{ teamShowInitiatives: { id: string } | null }>(
    `mutation($t:String!,$s:Boolean!){teamShowInitiatives(teamId:$t,show:$s){id}}`,
    { t: teamId, s: show },
  );
  if (!d.teamShowInitiatives)
    throw new Error("That team no longer exists. Reload the page.");
}
