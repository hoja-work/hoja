// The seam between the wizard (presentation + flow) and an edition's server.
//
// Every method is an observed fact from the server, never an optimistic guess:
// ids come from responses, `null` means "not established", and a rejected
// promise leaves the wizard on the same step with a message.

export type BootstrapInput = {
  name: string;
  email: string;
  password: string;
  workspaceName: string;
  workspaceAddress: string;
  teamName: string;
  teamKey: string;
};

export type BootstrapResult = {
  workspaceId: string;
  workspaceUrlKey: string;
  teamId: string;
  /** Shown once; never persisted by the wizard. */
  apiKey?: string;
};

export type InviteResult = {
  userId: string;
  /** A single-use link. Neither edition sends invitation email today. */
  link: string;
};

export type SetupAdapter = {
  edition: 'oss' | 'cloud';
  /** Where a workspace address lives, e.g. `.hoja.work`; empty for self-hosted. */
  addressDomain: string;
  status(): Promise<{initialized: boolean; ownerBound: boolean; googleSignIn: string | null}>;
  bootstrap(input: BootstrapInput): Promise<BootstrapResult>;
  createWorkspace(input: {name: string; address: string}): Promise<{id: string; urlKey: string}>;
  createTeam(input: {name: string; key: string}): Promise<{id: string; key: string}>;
  createProject(input: {name: string; teamId: string}): Promise<{id: string; name: string}>;
  findProject(name: string): Promise<{id: string; name: string} | null>;
  findMember(email: string): Promise<{id: string; email: string} | null>;
  invite(input: {name: string; email: string; teamId: string}): Promise<InviteResult>;
  pendingInvites(): Promise<{userId: string; email: string}[]>;
  connectorConfig(): Promise<{googleWorkspace: boolean}>;
  connectorAccounts(): Promise<string[]>;
  googleConnectUrl(): string;
  openWorkspace(): void;
};
