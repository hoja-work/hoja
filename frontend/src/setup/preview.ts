// What the live preview may claim.
//
// A preview is the easiest place in a setup flow to lie: showing a "project"
// that was never created, or an address that the server has not accepted yet.
// So every row carries its provenance. `typed` is a value the person is still
// editing, `confirmed` comes from a server response, `empty` is a placeholder,
// and `unavailable` says the deployment cannot offer the step at all.

import type {SetupDraft} from './draft';

export type PreviewState = 'typed' | 'confirmed' | 'empty' | 'unavailable';

export type PreviewRow = {label: string; value: string; state: PreviewState};

export type Preview = {
  workspaceName: string;
  address: string;
  teamKey: string;
  issueExample: string;
  rows: PreviewRow[];
};

export type PreviewInput = {
  draft: SetupDraft;
  /** `hoja.work` on the hosted control plane, empty for a self-hosted install. */
  addressDomain?: string;
  googleWorkspaceAvailable: boolean;
};

export function buildPreview(input: PreviewInput): Preview {
  const {draft} = input;
  const workspaceName = draft.workspaceName.trim();
  const address = (draft.workspaceUrlKey || draft.workspaceAddress).trim();
  const teamKey = draft.teamKey.trim().toUpperCase();
  const projectName = draft.projectName.trim();

  const rows: PreviewRow[] = [
    {
      label: 'Workspace',
      value: workspaceName || 'Your workspace',
      state: draft.workspaceId ? 'confirmed' : workspaceName ? 'typed' : 'empty',
    },
    {
      label: 'Address',
      value: address ? `${address}${input.addressDomain ?? ''}` : 'Not chosen yet',
      state: draft.workspaceId ? 'confirmed' : address ? 'typed' : 'empty',
    },
    {
      label: 'Team',
      value: draft.teamName.trim() ? `${draft.teamName.trim()} (${teamKey || '—'})` : 'Not named yet',
      state: draft.teamId ? 'confirmed' : draft.teamName.trim() ? 'typed' : 'empty',
    },
  ];

  if (projectName) {
    rows.push({
      label: 'Project',
      value: projectName,
      state: draft.projectId ? 'confirmed' : 'typed',
    });
  }
  if (draft.invites.length > 0) {
    rows.push({
      label: draft.invites.length === 1 ? 'Invitation' : 'Invitations',
      value: draft.invites.length === 1 ? draft.invites[0].email : `${draft.invites.length} people`,
      state: 'typed',
    });
  }
  rows.push({
    label: 'Google Workspace',
    value: input.googleWorkspaceAvailable ? 'Available to connect' : 'Not configured here',
    state: input.googleWorkspaceAvailable ? 'typed' : 'unavailable',
  });

  return {
    workspaceName: workspaceName || 'Your workspace',
    address: address || 'your-workspace',
    teamKey: teamKey || 'TEAM',
    issueExample: `${teamKey || 'TEAM'}-1`,
    rows,
  };
}
