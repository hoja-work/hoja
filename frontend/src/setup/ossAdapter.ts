// The self-hosted binding: the endpoints the OSS app already exposes.
//
// There is no cloud service here. `status` is `/onboarding/status`, the single
// irreversible write is `/onboarding/bootstrap`, and everything after it uses the
// same GraphQL API the rest of the product uses. Google Workspace is optional and
// only reported available when the deployment configured its OAuth client.

import {getJson, graphql, postJson} from './api';
import type {SetupAdapter} from './types';

type TeamNodes = {teams: {nodes: {id: string; key: string; name: string}[]}};
type ProjectNodes = {projects: {nodes: {id: string; name: string}[]}};
type MemberNodes = {organizationMembers: {nodes: {id: string; email: string}[]}};
type InviteNodes = {pendingMembershipInvitations: {userId: string; email: string}[]};

async function findMember(email: string): Promise<{id: string; email: string} | null> {
  const data = await graphql<MemberNodes>('query{organizationMembers(first:100){nodes{id email}}}');
  const wanted = email.trim().toLowerCase();
  return data.organizationMembers.nodes.find((member) => (member.email || '').toLowerCase() === wanted) ?? null;
}

export function createOssAdapter(): SetupAdapter {
  return {
    edition: 'oss',
    addressDomain: '',

    async status() {
      const body = await getJson<{initialized?: unknown; ownerBound?: unknown}>('/onboarding/status');
      return {
        initialized: body.initialized === true,
        ownerBound: body.ownerBound === true,
        googleSignIn: null,
      };
    },

    async bootstrap(input) {
      const body = await postJson<{
        status?: string;
        apiKey?: string;
        teamId?: string;
        workspace?: {id?: string; urlKey?: string};
      }>('/onboarding/bootstrap', {
        name: input.name.trim(),
        email: input.email.trim().toLowerCase(),
        password: input.password,
        workspaceName: input.workspaceName.trim(),
        workspaceKey: input.workspaceAddress.trim().toLowerCase(),
        teamName: input.teamName.trim(),
        teamKey: input.teamKey.trim().toUpperCase(),
      });
      if (!body.workspace?.id || !body.teamId) {
        throw new Error('Hoja created the workspace but did not confirm it. Reload to check.');
      }
      return {
        workspaceId: body.workspace.id,
        workspaceUrlKey: body.workspace.urlKey ?? input.workspaceAddress,
        teamId: body.teamId,
        apiKey: body.apiKey,
      };
    },

    async createWorkspace(input) {
      const data = await graphql<{workspaceCreate: {success: boolean; organization?: {id: string; urlKey: string}}}>(
        'mutation($input:WorkspaceCreateInput!){workspaceCreate(input:$input){success organization{id name urlKey}}}',
        {input: {name: input.name.trim(), urlKey: input.address.trim().toLowerCase()}},
      );
      const organization = data.workspaceCreate?.organization;
      if (!data.workspaceCreate?.success || !organization) throw new Error('Workspace creation was rejected.');
      const switched = await graphql<{workspaceSwitch: {success: boolean}}>(
        'mutation($id:String!){workspaceSwitch(organizationId:$id){success}}',
        {id: organization.id},
      );
      if (!switched.workspaceSwitch?.success) {
        throw new Error('The workspace exists, but switching into it failed. Open it from the workspace menu.');
      }
      return {id: organization.id, urlKey: organization.urlKey};
    },

    async createTeam(input) {
      const data = await graphql<{teamCreate: {success: boolean; team?: {id: string; key: string}}}>(
        'mutation($input:TeamCreateInput!){teamCreate(input:$input){success team{id key}}}',
        {input: {name: input.name.trim(), key: input.key.trim().toUpperCase()}},
      );
      if (!data.teamCreate?.success || !data.teamCreate.team) throw new Error('The team could not be created.');
      return data.teamCreate.team;
    },

    async createProject(input) {
      const data = await graphql<{projectCreate: {success: boolean; project?: {id: string; name: string}}}>(
        'mutation($input:ProjectCreateInput!){projectCreate(input:$input){success project{id name}}}',
        {input: {name: input.name.trim(), teamIds: [input.teamId]}},
      );
      if (!data.projectCreate?.success || !data.projectCreate.project) throw new Error('The project could not be created.');
      return data.projectCreate.project;
    },

    async findProject(name) {
      const data = await graphql<ProjectNodes>('query{projects(first:100){nodes{id name}}}');
      const wanted = name.trim().toLowerCase();
      return data.projects.nodes.find((project) => project.name.trim().toLowerCase() === wanted) ?? null;
    },

    findMember,

    async invite(input) {
      const existing = await findMember(input.email);
      let userId = existing?.id;
      if (!userId) {
        const created = await graphql<{memberInvite: {success: boolean; user?: {id: string}}}>(
          'mutation($input:MemberInviteInput!){memberInvite(input:$input){success user{id}}}',
          {input: {name: input.name.trim(), email: input.email.trim().toLowerCase(), teamIds: [input.teamId]}},
        );
        if (!created.memberInvite?.success || !created.memberInvite.user) {
          throw new Error('That person could not be added. Check the email and admin rights.');
        }
        userId = created.memberInvite.user.id;
      }
      const invitation = await graphql<{membershipInvitationCreate: {success: boolean; inviteCode?: string}}>(
        'mutation($id:String!){membershipInvitationCreate(userId:$id){success inviteCode}}',
        {id: userId},
      );
      const code = invitation.membershipInvitationCreate?.inviteCode;
      if (!invitation.membershipInvitationCreate?.success || !code) {
        throw new Error('The person was added, but their invite link could not be created. Retry from Members.');
      }
      return {userId, link: `${location.origin}/#invite=${encodeURIComponent(code)}`};
    },

    async pendingInvites() {
      const data = await graphql<InviteNodes>('query{pendingMembershipInvitations{userId email}}');
      return data.pendingMembershipInvitations;
    },

    async connectorConfig() {
      const body = await getJson<{googleWorkspace?: unknown}>('/api/connectors/config');
      return {googleWorkspace: body.googleWorkspace === true};
    },

    async connectorAccounts() {
      const body = await getJson<{accounts?: {provider?: string}[]}>('/api/connectors/accounts');
      return (body.accounts ?? []).map((account) => account.provider ?? '').filter(Boolean);
    },

    googleConnectUrl() {
      return '/api/connectors/google/start?scope=org';
    },

    openWorkspace() {
      window.location.assign('/workspace/my-issues/assigned');
    },
  };
}
