/* Typed model for the agent surface (HOJA-602-A-16 / WP13).
 *
 * These are the shapes the screens render, not the database rows. As of WP04 (`agent_member` and
 * `AgentMember` in app/hoja_server/agent_members.py, live on main at 1a296bc) the identity half is
 * read from GraphQL, so the fields below are the ones the server actually reports — no more.
 *
 * The rule this file enforces: a column the server does not have is `null` here, never a plausible
 * placeholder. A screen renders `null` as "not reported", which is a different sentence from "0
 * running" and from "no agents". Adding a field back is a backend change, not a fixture change.
 */

/* The only agent state the server records: an `agent_member` row is live, or it is archived
 * (`archived_at`). Session liveness (working / queued / paused) is WP06 and is not modelled here,
 * because rendering it from a fixture is how a fake "working" dot reaches a real screen. */
export type AgentStatus = 'active' | 'archived';

export type Agent = {
  /** `AgentMember.id`: the server identity and the URL segment. */
  id: string;
  /** The identifier column's short handle: the first 8 characters of `id`, so it is derived from
   * the server's own id instead of being invented. */
  key: string;
  /** The agent's `user.id`. This is the value `Issue.delegateId` holds, which is why the issue
   * page's Delegate picker is keyed by it. */
  userId: string;
  name: string;
  displayName: string | null;
  /** Concurrent turns the agent may run (`agent_member.capacity`). */
  capacity: number;
  /** Credits per calendar month; `null` means the workspace default, not unlimited. */
  monthlyBudgetCredits: number | null;
  /** Opaque working-hours document, kept as the JSON text the sidebar edits. */
  workingHours: string | null;
  timezone: string | null;
  model: string | null;
  runnerPool: string | null;
  archivedAt: string | null;
  createdAt: string;
  updatedAt: string;
  /** Derived from `archivedAt`, so the status glyph and its age are server facts. */
  status: AgentStatus;
  /** The row's status glyph reads its age from this, exactly as an issue row reads
   * `stateChangedAt`, so the two lists answer "how long has it been like this?" with the same
   * math. For an archived agent that is `archivedAt`; otherwise the last write. */
  statusChangedAt: string;
  /* ---- Facts no server column holds on main. Rendered as "not reported", never guessed. ---- */
  /** Sessions occupying a slot right now (WP06 session rows). */
  running: number | null;
  /** The harness profile (WP11). */
  harness: string | null;
  /** Who the agent works for. `agent_member` has no owner column. */
  owner: {id: string; name: string} | null;
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

/* Exactly the columns `agentMemberUpdate` accepts (agent_members.py `_clean`). Nothing else is
 * editable, and nothing here is a WP12 policy field: tools, scope, repos, secrets, network and
 * approvers have no column on main, so they are not in the type and the sidebar says so instead
 * of editing a fixture. */
export type AgentPolicy = {
  name: string;
  displayName: string | null;
  capacity: number;
  monthlyBudgetCredits: number | null;
  model: string | null;
  runnerPool: string | null;
  timezone: string | null;
  workingHours: string | null;
  archived: boolean;
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
  {id: 'active', name: 'Active', color: '#2f9e6f', type: 'started'},
  {id: 'archived', name: 'Archived', color: '#95a2b3', type: 'canceled'},
];

export const SESSION_STATUSES: {id: SessionStatus; name: string}[] = [
  {id: 'queued', name: 'Queued'},
  {id: 'active', name: 'Active'},
  {id: 'awaiting-input', name: 'Awaiting input'},
  {id: 'awaiting-verification', name: 'Awaiting verification'},
  {id: 'done', name: 'Done'},
];

export function agentStatus(id: AgentStatus) {
  return AGENT_STATUSES.find(s => s.id === id) || AGENT_STATUSES[0];
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
