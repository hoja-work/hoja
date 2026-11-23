import { gql } from "../../ui";

export type Member = {
  id: string;
  name?: string | null;
  email?: string | null;
  avatarSeed?: string | null;
  admin?: boolean | null;
  active?: boolean | null;
};

export type PendingInvite = {
  userId: string;
  name?: string | null;
  email?: string | null;
  expiresAt?: string | null;
};

export type MembersData = {
  members: Member[];
  /** Admin-only on the server; empty for everyone else. */
  pending: PendingInvite[];
  /** Admin-only on the server; empty for everyone else. */
  removed: Member[];
};

export async function loadMembers(isAdmin: boolean): Promise<MembersData> {
  const d = await gql<{
    organizationMembers: { nodes: Member[] };
    pendingMembershipInvitations?: PendingInvite[];
    archivedMembers?: Member[];
  }>(
    isAdmin
      ? `query SettingsMembers{organizationMembers(first:250){nodes{id name email avatarSeed admin active}} pendingMembershipInvitations{userId name email expiresAt} archivedMembers{id name email}}`
      : `query SettingsMembers{organizationMembers(first:250){nodes{id name email avatarSeed admin active}}}`,
  );
  return {
    members: d.organizationMembers?.nodes || [],
    pending: d.pendingMembershipInvitations || [],
    removed: d.archivedMembers || [],
  };
}

async function ok(mutation: string, field: string, vars: object, fail: string) {
  const d = await gql<Record<string, { success: boolean } | null>>(
    mutation,
    vars as Record<string, unknown>,
  );
  if (!d[field]?.success) throw new Error(fail);
  return d;
}

export const setRole = (id: string, admin: boolean) =>
  ok(
    `mutation SettingsMemberRole($id:String!,$admin:Boolean!){memberRoleUpdate(userId:$id,admin:$admin){success}}`,
    "memberRoleUpdate",
    { id, admin },
    admin
      ? "The role was not changed. You may not have permission to change roles."
      : "The role was not changed. The workspace must keep at least one person with full access; promote someone else first.",
  );

export const setActive = (id: string, active: boolean) =>
  active
    ? ok(
        `mutation SettingsMemberReactivate($id:String!){memberReactivate(userId:$id){success}}`,
        "memberReactivate",
        { id },
        "They were not reactivated. Reload the page and try again.",
      )
    : ok(
        `mutation SettingsMemberDeactivate($id:String!){memberDeactivate(userId:$id){success}}`,
        "memberDeactivate",
        { id },
        "They were not deactivated. You can't deactivate yourself or the last person with full access.",
      );

export const archiveMember = (id: string) =>
  ok(
    `mutation SettingsMemberArchive($id:String!){memberArchive(userId:$id){success}}`,
    "memberArchive",
    { id },
    "They were not removed. You can't remove yourself or the last person with full access.",
  );

export const restoreMember = (id: string) =>
  ok(
    `mutation SettingsMemberRestore($id:String!){memberRestore(userId:$id){success}}`,
    "memberRestore",
    { id },
    "They were not restored. Reload the page and try again.",
  );

export const revokeInvite = (id: string) =>
  ok(
    `mutation SettingsInviteRevoke($id:String!){membershipInvitationRevoke(userId:$id){success}}`,
    "membershipInvitationRevoke",
    { id },
    "The invite was not revoked. It may already have been accepted or expired.",
  );

export type InviteLink = { url: string; emailed: boolean; email: string };

/** Issue a one-time invite link (72 hours). Replaces any earlier link for this person. */
export async function createInviteLink(
  userId: string,
  email: string,
): Promise<InviteLink> {
  const d = await gql<{
    membershipInvitationCreate: {
      success: boolean;
      inviteCode?: string | null;
      emailQueued?: boolean | null;
    };
  }>(
    `mutation SettingsInviteCreate($id:String!){membershipInvitationCreate(userId:$id){success inviteCode emailQueued}}`,
    { id: userId },
  );
  const r = d.membershipInvitationCreate;
  if (!r?.success || !r.inviteCode)
    throw new Error(
      "The invite link was not created. Deactivated people can't be invited; reactivate them first.",
    );
  return {
    url: `${window.location.origin}/#invite=${encodeURIComponent(r.inviteCode)}`,
    emailed: r.emailQueued === true,
    email,
  };
}

export async function inviteMember(input: {
  name: string;
  email: string;
  admin: boolean;
  teamIds: string[];
}): Promise<Member> {
  const d = await gql<{
    memberInvite: { success: boolean; user?: Member | null };
  }>(
    `mutation SettingsMemberInvite($input:MemberInviteInput!){memberInvite(input:$input){success user{id name email avatarSeed admin active}}}`,
    { input },
  );
  if (!d.memberInvite?.success || !d.memberInvite.user)
    throw new Error(
      "The invite was not sent. You may not have permission to invite people.",
    );
  return d.memberInvite.user;
}

export const memberLabel = (m: {
  name?: string | null;
  email?: string | null;
}) => m.name || m.email || "Workspace member";
