// The wizard's step list is derived, not hard-coded: a deployment without a
// Google Workspace connector must not show an integrations step it could never
// complete, and a first run must not pretend a project can exist before a team
// does. Keeping this pure means the branching can be tested without React.

export type StepId =
  | 'account'
  | 'workspace'
  | 'team'
  | 'project'
  | 'invitations'
  | 'integrations'
  | 'ready';

export type FlowMode = 'first-run' | 'existing';

export type FlowContext = {
  mode: FlowMode;
  /** The account step renders a Google button as its primary path. */
  googleSignIn: boolean;
  /**
   * A hosted tenant already knows who its owner is. The address field is shown
   * as fixed context instead of being asked again.
   */
  ownerBound: boolean;
  /** The workspace organization already exists (created before this screen). */
  workspaceCreated: boolean;
  /** A team exists, so project and invitation steps have somewhere to point. */
  teamExists: boolean;
  /** `/api/connectors/config` says this deployment can connect Google Workspace. */
  googleWorkspaceAvailable: boolean;
};

const ALWAYS_OPTIONAL: ReadonlySet<StepId> = new Set<StepId>(['project', 'invitations', 'integrations']);

/**
 * Ordered steps for one concrete flow. `ready` is always last and always present:
 * it is the truthful summary, not an optional extra.
 */
export function buildSteps(context: FlowContext): StepId[] {
  // A first run always shows the same path, even after its workspace exists:
  // removing finished steps would change "step 3 of 6" mid-flow, and revisiting
  // one must not re-run its write. Existing-workspace setups start after the
  // workspace, and skip the team step when a team is already there.
  const steps: StepId[] = [];
  if (context.mode === 'first-run') {
    steps.push('account', 'workspace', 'team');
  } else {
    if (!context.workspaceCreated) steps.push('workspace');
    if (!context.teamExists) steps.push('team');
  }
  // Project and invitations always follow the team step: that step is where a
  // first team is created, and it cannot be skipped, so these steps will have a
  // team to point at by the time they render.
  steps.push('project', 'invitations');
  if (context.googleWorkspaceAvailable) steps.push('integrations');
  steps.push('ready');
  return steps;
}

export function stepIndex(steps: readonly StepId[], step: StepId): number {
  return steps.indexOf(step);
}

export function nextStep(steps: readonly StepId[], current: StepId): StepId | null {
  const index = stepIndex(steps, current);
  if (index < 0 || index >= steps.length - 1) return null;
  return steps[index + 1];
}

export function previousStep(steps: readonly StepId[], current: StepId): StepId | null {
  const index = stepIndex(steps, current);
  if (index <= 0) return null;
  return steps[index - 1];
}

export function canSkip(step: StepId): boolean {
  return ALWAYS_OPTIONAL.has(step);
}

/** Only a step that is already behind the current one may be revisited. */
export function canVisit(steps: readonly StepId[], current: StepId, target: StepId): boolean {
  const from = stepIndex(steps, current);
  const to = stepIndex(steps, target);
  return from >= 0 && to >= 0 && to < from;
}

export function progress(steps: readonly StepId[], current: StepId): {index: number; total: number; label: string} {
  const index = Math.max(0, stepIndex(steps, current));
  return {index, total: steps.length, label: `Step ${index + 1} of ${steps.length}`};
}

export const STEP_TITLES: Record<StepId, string> = {
  account: 'Your account',
  workspace: 'Workspace',
  team: 'First team',
  project: 'First project',
  invitations: 'Invite teammates',
  integrations: 'Connect Google Workspace',
  ready: 'Ready',
};
