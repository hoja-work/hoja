import { gql } from "../../ui";
import type { SettingsTeam } from "../../context";

export type Person = {
  id: string;
  name?: string | null;
  email?: string | null;
  avatarSeed?: string | null;
  active?: boolean | null;
};

export type Visibility = "public" | "restricted" | "private";

export type TeamRow = {
  id: string;
  name: string;
  key: string;
  description?: string | null;
  visibility: Visibility;
  parentTeamId?: string | null;
  timezone?: string | null;
  members: Person[];
};

const TEAM_FIELDS = `id name key description visibility parentTeamId timezone members{nodes{id name email avatarSeed}}`;

type RawTeam = Omit<TeamRow, "members"> & { members?: { nodes: Person[] } };
const toRow = (t: RawTeam): TeamRow => ({
  ...t,
  visibility: (t.visibility || "public") as Visibility,
  members: t.members?.nodes || [],
});

export async function loadTeams(): Promise<TeamRow[]> {
  const d = await gql<{ teams: { nodes: RawTeam[] } }>(
    `query SettingsTeams{teams(first:250){nodes{${TEAM_FIELDS}}}}`,
  );
  return (d.teams?.nodes || []).map(toRow);
}

export async function loadPeople(): Promise<Person[]> {
  const d = await gql<{ organizationMembers: { nodes: Person[] } }>(
    `query SettingsTeamPeople{organizationMembers(first:250){nodes{id name email avatarSeed active}}}`,
  );
  return (d.organizationMembers?.nodes || []).filter((p) => p.active !== false);
}

/* The app shell's team list (sidebar, pickers) has its own, richer shape. Refetch it after
 * any team change so the sidebar shows the new name, key or team at once. */
const SHELL_TEAMS_QUERY = `query Teams{teams{nodes{id name key description showInitiatives states{nodes{id name color type}} members{nodes{id name avatarSeed email}} activeCycle{id name number startsAt endsAt progress}}}}`;

export async function refreshShellTeams(
  onTeamsChanged: (teams: SettingsTeam[]) => void,
) {
  try {
    const d = await gql<{ teams: { nodes: SettingsTeam[] } }>(
      SHELL_TEAMS_QUERY,
    );
    onTeamsChanged(d.teams?.nodes || []);
  } catch {
    /* The settings list already shows the change; the sidebar catches up on the next sync. */
  }
}

export async function updateTeam(
  id: string,
  input: Partial<{
    name: string;
    key: string;
    description: string;
    visibility: Visibility;
    parentTeamId: string | null;
  }>,
) {
  const d = await gql<{ teamUpdate: { success: boolean } }>(
    `mutation SettingsTeamUpdate($id:String!,$input:TeamUpdateInput!){teamUpdate(id:$id,input:$input){success}}`,
    { id, input },
  );
  if (!d.teamUpdate?.success) throw new Error("The team was not updated.");
}

export async function createTeam(input: {
  name: string;
  key: string;
  description?: string;
  visibility: Visibility;
  parentTeamId?: string | null;
  timezone?: string;
  memberIds: string[];
}) {
  const d = await gql<{
    teamCreate: { success: boolean; team: { id: string; key: string } };
  }>(
    `mutation SettingsTeamCreate($input:TeamCreateInput!){teamCreate(input:$input){success team{id key}}}`,
    { input },
  );
  if (!d.teamCreate?.success) throw new Error("The team was not created.");
  return d.teamCreate.team;
}

export async function setTeamMember(
  teamId: string,
  userId: string,
  add: boolean,
) {
  const field = add ? "teamMemberAdd" : "teamMemberRemove";
  const d = await gql<Record<string, { success: boolean }>>(
    `mutation SettingsTeamMember($t:String!,$u:String!){${field}(teamId:$t,userId:$u){success}}`,
    { t: teamId, u: userId },
  );
  if (!d[field]?.success)
    throw new Error(
      "You don't have permission to change team members. Ask a workspace admin.",
    );
}

export async function archiveTeam(id: string) {
  const d = await gql<{ teamArchive: { success: boolean } }>(
    `mutation SettingsTeamArchive($id:String!){teamArchive(id:$id){success}}`,
    { id },
  );
  if (!d.teamArchive?.success) throw new Error("The team was not archived.");
}

export async function deleteTeam(id: string, deleteIssues: boolean) {
  const d = await gql<{ teamDelete: { success: boolean } }>(
    `mutation SettingsTeamDelete($id:String!,$issues:Boolean!){teamDelete(id:$id,deleteIssues:$issues){success}}`,
    { id, issues: deleteIssues },
  );
  if (!d.teamDelete?.success) throw new Error("The team was not deleted.");
}

export const VISIBILITY_OPTIONS: {
  value: Visibility;
  label: string;
  description: string;
}[] = [
  {
    value: "public",
    label: "Public",
    description: "Everyone in the workspace can see it and its tasks.",
  },
  {
    value: "restricted",
    label: "Restricted",
    description: "Its members and members of the parent team.",
  },
  {
    value: "private",
    label: "Private",
    description: "Only its members (and workspace admins).",
  },
];

export const personLabel = (p: Person) =>
  p.name || p.email || "Workspace member";

/** A team and every team beneath it: none of these can become its parent. */
export function descendantIds(teams: TeamRow[], id: string): Set<string> {
  const out = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const t of teams)
      if (t.parentTeamId && out.has(t.parentTeamId) && !out.has(t.id)) {
        out.add(t.id);
        grew = true;
      }
  }
  return out;
}

export function depthOf(teams: TeamRow[], id: string | null | undefined) {
  let depth = 0;
  const seen = new Set<string>();
  let current = id;
  while (current && !seen.has(current)) {
    seen.add(current);
    depth += 1;
    current = teams.find((t) => t.id === current)?.parentTeamId;
  }
  return depth;
}
