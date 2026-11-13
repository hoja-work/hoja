/* Typed model for the agent surface (HOJA-602-A-16 / WP13).
 *
 * These are the shapes the screens render, not the database rows: WP04 (agent identity and
 * delegate actor) and WP06 (delegation, schedule, session) own the server contract. Keeping the
 * wire names out of here means the adapter below is the only place that has to change when the
 * GraphQL fields land.
 */

export type AgentStatus = 'idle' | 'working' | 'queued' | 'paused';

export type Agent = {
  /** Stable server id. The key is what the URL and the row identifier use. */
  id: string;
  key: string;
  name: string;
  harness: string;
  model: string;
  status: AgentStatus;
  /** Sessions occupying a slot right now, and the slots the policy allows. */
  running: number;
  capacity: number;
  budgetUsedUsd: number;
  budgetLimitUsd: number;
  runnerPool: string;
  owner: {id: string; name: string};
  /** When the status last changed. The row's status glyph reads its age from this, exactly as an
   * issue row reads `stateChangedAt`, so the two lists answer "how long has it been like this?"
   * with the same math. */
  statusChangedAt: string;
  updatedAt: string;
};

export type SessionStatus = 'queued' | 'active' | 'awaiting-input' | 'awaiting-verification' | 'done';

export type AgentSession = {
  id: string;
  agentId: string;
  agentKey: string;
  status: SessionStatus;
  /** Why it is waiting, when it is: the run queue shows this inline instead of a bare badge. */
  reason?: string;
  issueId: string;
  issueIdentifier: string;
  issueTitle: string;
  priority: number;
  startedAt?: string;
  endedAt?: string;
};

export type AgentEventKind = 'thought' | 'action' | 'elicitation' | 'response' | 'error';

export type AgentEvent = {
  id: string;
  sessionId: string;
  kind: AgentEventKind;
  at: string;
  text: string;
  /** Set on `action`: the tool the agent invoked. */
  tool?: string;
};

export type AgentPolicy = {
  tools: string[];
  scope: 'repo' | 'workspace';
  repos: string[];
  /** Names only. A secret value never reaches the browser (WP03 owns the lease). */
  secrets: string[];
  network: string[];
  budgetUsd: number;
  workingHours: string;
  approvers: string[];
};

export type AgentDetail = {
  agent: Agent;
  policy: AgentPolicy;
  sessions: AgentSession[];
  events: AgentEvent[];
};

export type AuditEvent = {
  id: string;
  at: string;
  actor: string;
  action: string;
  target: string;
  detail: string;
};

export const AGENT_STATUSES: {id: AgentStatus; name: string; color: string; type: 'started' | 'unstarted' | 'backlog' | 'canceled'}[] = [
  {id: 'working', name: 'Working', color: '#5e6ad2', type: 'started'},
  {id: 'queued', name: 'Queued', color: '#e2a03f', type: 'unstarted'},
  {id: 'idle', name: 'Idle', color: '#95a2b3', type: 'backlog'},
  {id: 'paused', name: 'Paused', color: '#d4553f', type: 'canceled'},
];

export const SESSION_STATUSES: {id: SessionStatus; name: string}[] = [
  {id: 'queued', name: 'Queued'},
  {id: 'active', name: 'Active'},
  {id: 'awaiting-input', name: 'Awaiting input'},
  {id: 'awaiting-verification', name: 'Awaiting verification'},
  {id: 'done', name: 'Done'},
];

export function agentStatus(id: AgentStatus) {
  return AGENT_STATUSES.find(s => s.id === id) || AGENT_STATUSES[2];
}

export function sessionStatusName(status: SessionStatus): string {
  return SESSION_STATUSES.find(s => s.id === status)?.name || status;
}

/* ---------------- Runners and Connect (slice 2) ---------------- */

/* The two server sources this slice is allowed to read from, kept in the server's own
 * vocabulary so the contract check can compare them field for field:
 *
 *   runner/hoja_runnerd/connect.py  ->  Exposure.public_row()
 *   spec.md glossary            ->  "Runner — org, name, labels, lease token hash, last seen"
 *
 * A mapping here is not documentation: app/scripts/check-runner-contract.py reads these objects
 * and fails when a server field has no local one, or a local one has no server field. The two
 * secrets are mapped to booleans on purpose — `lease_token_hash` becomes `enrolled` (FR-018: the
 * token is single-use and its hash is a server column) and the tailcat address is not mapped at
 * all, because it is a bearer secret (connect.py:8-12).
 */
export const EXPOSURE_SERVER_FIELDS = {
  exposure_id: 'id',
  workspace_id: 'workspaceId',
  ports: 'ports',
  pinned_peer: 'pinnedPeer',
  expires_at: 'expiresAt',
  live: 'live',
} as const;

export const RUNNER_SERVER_FIELDS = {
  org: 'workspaceId',
  name: 'name',
  labels: 'labels',
  'lease token hash': 'enrolled',
  'last seen': 'lastSeenAt',
} as const;

export type RunnerStatus = 'online' | 'offline';

export const RUNNER_STATUSES: {id: RunnerStatus; name: string; color: string; type: 'started' | 'canceled'}[] = [
  {id: 'online', name: 'Online', color: '#2f9e6f', type: 'started'},
  {id: 'offline', name: 'Offline', color: '#95a2b3', type: 'canceled'},
];

export function runnerStatus(id: RunnerStatus) {
  return RUNNER_STATUSES.find(s => s.id === id) || RUNNER_STATUSES[1];
}

/** A runner as the screens render it. `enrolled` replaces the token hash; nothing else from the
 * registration is a credential. */
export type Runner = {
  /** Stable server id. */
  id: string;
  /** Server name, e.g. "mac-mini-01". */
  name: string;
  workspaceId: string;
  status: RunnerStatus;
  labels: string[];
  /** A single-use token was presented and accepted (FR-018); the hash itself never ships. */
  enrolled: boolean;
  /** The heartbeat the server uses to derive `status` (FR-020: our clock, not the runner's). */
  lastSeenAt: string;
};

/** The raw registration the runner's own record holds. Present in the fixture so the projection
 * to `Runner` has a real secret to drop, and so the contract check can prove it is dropped. */
export type RunnerRecord = Runner & {leaseTokenHash: string};

/** A live Connect exposure, exactly the public row `Exposure.public_row()` returns. */
export type ConnectExposure = {
  id: string;
  workspaceId: string;
  ports: number[];
  /** The worker node key this exposure is pinned to (`--allow=nodekey:…`). */
  pinnedPeer: string;
  /** Unix seconds, as the server sends it. */
  expiresAt: number;
  live: boolean;
};

/** The lease channel's payload (connect.py `LeasePayload`): the bearer `address` lives here and
 * is projected away before anything reaches a screen. */
export type RawExposure = ConnectExposure & {address: string};

export function ttlSecondsLeft(expiresAt: number, at: number = Date.now()): number {
  return Math.max(0, Math.round(expiresAt - at / 1000));
}

/** `m:ss` while under an hour, which is the only lifetime Connect allows (MAX_TTL_SECONDS). */
export function ttlLabel(expiresAt: number, at: number = Date.now()): string {
  const left = ttlSecondsLeft(expiresAt, at);
  const m = Math.floor(left / 60);
  const s = left % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
