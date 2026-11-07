// Persisted wizard progress.
//
// Only values that survive a reload without being dangerous or misleading are
// stored: names, the chosen address, ids the server already created, and which
// optional steps were skipped. Passwords, API keys, human-check tokens, invite
// codes and OAuth state never enter this module's input type, so serialising the
// whole draft cannot leak them. `loadDraft` treats storage as untrusted input.

import type {FlowMode, StepId} from './flow';

export const DRAFT_KEY = 'hoja_setup_draft_v1';
const VERSION = 1;
const MAX_INVITES = 20;
const MAX_TEXT = 120;

const STEPS: readonly StepId[] = [
  'account', 'workspace', 'team', 'project', 'invitations', 'integrations', 'ready',
];

export type DraftInvite = {name: string; email: string};

export type SetupDraft = {
  version: typeof VERSION;
  mode: FlowMode;
  step: StepId;
  workspaceName: string;
  workspaceAddress: string;
  teamName: string;
  teamKey: string;
  projectName: string;
  invites: DraftInvite[];
  workspaceId?: string;
  workspaceUrlKey?: string;
  teamId?: string;
  projectId?: string;
  /** Set before leaving for a Google sign-in, cleared once the session is seen. */
  googleStarted?: boolean;
  completed: StepId[];
  skipped: StepId[];
};

export type DraftScope = {
  edition: 'oss' | 'cloud';
  /** When present, a draft for a different workspace is discarded. */
  workspaceId?: string;
};

export function freshDraft(mode: FlowMode): SetupDraft {
  return {
    version: VERSION,
    mode,
    step: mode === 'first-run' ? 'account' : 'team',
    workspaceName: '',
    workspaceAddress: '',
    teamName: 'Engineering',
    teamKey: 'ENG',
    projectName: '',
    invites: [],
    completed: [],
    skipped: [],
  };
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, MAX_TEXT) : '';
}

function step(value: unknown): StepId | null {
  return STEPS.includes(value as StepId) ? (value as StepId) : null;
}

function stepList(value: unknown): StepId[] {
  if (!Array.isArray(value)) return [];
  const seen: StepId[] = [];
  for (const item of value) {
    const parsed = step(item);
    if (parsed && !seen.includes(parsed)) seen.push(parsed);
  }
  return seen;
}

function invites(value: unknown): DraftInvite[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_INVITES).flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const candidate = item as {name?: unknown; email?: unknown};
    const name = text(candidate.name).trim();
    const email = text(candidate.email).trim().toLowerCase();
    return name && email ? [{name, email}] : [];
  });
}

function optionalId(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 && value.length <= MAX_TEXT ? value : undefined;
}

/**
 * Serialise only known fields. This is a whitelist, not a blacklist: a future
 * caller that adds a password to the draft by mistake still gets no write.
 */
export function serialiseDraft(draft: SetupDraft): string {
  const safe: SetupDraft = {
    version: VERSION,
    mode: draft.mode === 'existing' ? 'existing' : 'first-run',
    step: step(draft.step) ?? 'account',
    workspaceName: text(draft.workspaceName),
    workspaceAddress: text(draft.workspaceAddress),
    teamName: text(draft.teamName),
    teamKey: text(draft.teamKey),
    projectName: text(draft.projectName),
    invites: invites(draft.invites),
    workspaceId: optionalId(draft.workspaceId),
    workspaceUrlKey: optionalId(draft.workspaceUrlKey),
    teamId: optionalId(draft.teamId),
    projectId: optionalId(draft.projectId),
    googleStarted: draft.googleStarted === true ? true : undefined,
    completed: stepList(draft.completed),
    skipped: stepList(draft.skipped),
  };
  return JSON.stringify(safe);
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function saveDraft(storage: StorageLike, scope: DraftScope, draft: SetupDraft): void {
  try {
    storage.setItem(key(scope), serialiseDraft(draft));
  } catch {
    // A full or disabled storage must not break setup; the draft is an optimisation.
  }
}

export function loadDraft(storage: StorageLike, scope: DraftScope): SetupDraft | null {
  let raw: string | null = null;
  try {
    raw = storage.getItem(key(scope));
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const candidate = parsed as Record<string, unknown>;
  if (candidate.version !== VERSION) return null;
  if (candidate.mode !== 'first-run' && candidate.mode !== 'existing') return null;
  const storedWorkspace = optionalId(candidate.workspaceId);
  if (scope.workspaceId && storedWorkspace && storedWorkspace !== scope.workspaceId) return null;
  const mode: FlowMode = candidate.mode;
  return {
    version: VERSION,
    mode,
    step: step(candidate.step) ?? (mode === 'first-run' ? 'account' : 'team'),
    workspaceName: text(candidate.workspaceName),
    workspaceAddress: text(candidate.workspaceAddress),
    teamName: text(candidate.teamName) || 'Engineering',
    teamKey: text(candidate.teamKey) || 'ENG',
    projectName: text(candidate.projectName),
    invites: invites(candidate.invites),
    workspaceId: storedWorkspace,
    workspaceUrlKey: optionalId(candidate.workspaceUrlKey),
    teamId: optionalId(candidate.teamId),
    projectId: optionalId(candidate.projectId),
    googleStarted: candidate.googleStarted === true ? true : undefined,
    completed: stepList(candidate.completed),
    skipped: stepList(candidate.skipped),
  };
}

export function clearDraft(storage: StorageLike, scope: DraftScope): void {
  try {
    storage.removeItem(key(scope));
  } catch {
    // Nothing to do; the stale draft is ignored once setup is finished.
  }
}

function key(scope: DraftScope): string {
  return `${DRAFT_KEY}:${scope.edition}`;
}
