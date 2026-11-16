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

export type SessionStatus = 'queued' | 'active' | 'awaiting-input' | 'awaiting-verification' | 'done' | 'failed';

export type AgentSession = {
  id: string;
  /** The owning agent's `AgentMember.id`: the value the agent routes take. A session reports the
   * agent's `user.id`, so the adapter resolves the member id rather than the screen guessing it. */
  agentId: string;
  /** The agent's name, for the queue row's session chip. */
  agentName: string;
  /** `AgentSession.agentUserId`: the `user.id` the session is filed under. */
  agentUserId: string;
  /** The session's own `state`, verbatim from the server's ten-state machine. The chip's title
   * says where the run really is, which "queued" alone would flatten away. */
  state: string;
  /** `AgentSession.queueReason`, in the schedule check's fixed order. Empty once admitted. */
  queueReason: string[];
  /** `queueReason` as one sentence, which is what the row shows inline. */
  reason?: string;
  status: SessionStatus;
  admitted: boolean;
  issueId: string;
  issueIdentifier: string;
  issueTitle: string;
  priority: number;
  startedAt?: string;
  endedAt?: string;
  /** `AgentSession.claim`: what the runner claimed at finish, or null while it runs. */
  claim: ClaimSummary | null;
  /** When the claim was recorded; the awaiting-verification row's age. */
  claimAt?: string;
  /** The model that produced the work, as the runner reported it. */
  workerModel: string | null;
  /** met | not_met | uncertain — null until a separate principal signs. Never set by the runner. */
  verdict: Verdict | null;
};

/** The claim as a queue row needs it: the summary the runner wrote, and its acceptance answers. */
export type ClaimSummary = {
  summary: string;
  acceptance: {item: string; evidence: string}[];
};

/** `AgentActivityType`, including `prompt`, which only a human may author (agent_sessions.py). */
export type AgentEventKind = 'thought' | 'action' | 'elicitation' | 'response' | 'error' | 'prompt';

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
  /** `#seq`: the ledger's own ordering key, which is also the page cursor's raw form. */
  id: string;
  at: string;
  /** `audit_event.actor_id`: the person, bot or agent the server named, never a display guess. */
  actor: string;
  /** `human` | `bot` | `agent`, as the credential named it. */
  actorKind?: string;
  action: string;
  /** `target_id`, or the target kind when the action names nothing narrower. */
  target: string;
  detail: string;
  /** For an agent turn: the human whose request the agent acted on. */
  onBehalfOf?: string | null;
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
  {id: 'failed', name: 'Failed'},
];

export function agentStatus(id: AgentStatus) {
  return AGENT_STATUSES.find(s => s.id === id) || AGENT_STATUSES[0];
}

export function sessionStatusName(status: SessionStatus): string {
  return SESSION_STATUSES.find(s => s.id === status)?.name || status;
}

/* ---------------- Runners, leases and Connect (slice 5) ---------------- */

/* The server sources this slice reads, in the server's own vocabulary, so the contract check can
 * compare them field for field:
 *
 *   app/hoja_server/agent_runners.py  ->  Runner (id, name, organizationId, status, enabled,
 *                                          revokedAt, labels, harnesses, lastHeartbeatAt,
 *                                          createdAt, currentLease)
 *   app/hoja_server/db.py             ->  runner: org, name, labels, lease_token_hash, last_seen
 *   runner/hoja_runnerd/connect.py    ->  Exposure.public_row()
 *   spec.md glossary                  ->  "Runner — org, name, labels, lease token hash, last seen"
 *
 * A mapping here is not documentation: app/scripts/check-runner-contract.py reads these objects
 * and fails when a server field has no local one, or a local one has no server field. The two
 * secrets are mapped to facts about them on purpose — `lease_token_hash` becomes `revoked` (the
 * credential is the column; the screen's question is whether it still authenticates) and the
 * tailcat address is not mapped at all, because it is a bearer secret (connect.py:8-12).
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
  'lease token hash': 'revoked',
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

/** The lease a runner is holding right now (`AgentLease` in agent_runners.py), as the screens draw
 * it. The fence, the harness and the task are the three facts a person checks on a machine. */
export type RunnerLease = {
  id: string;
  taskId: string;
  /** The task's identifier, from `AgentLease.issue`. `null` when the task is not readable here. */
  taskIdentifier: string | null;
  /** queued | active | finished | released | canceled | lost */
  state: string;
  harness: string;
  /** Monotonic per task; a stale fence is refused with 409 on every route (FR-018). */
  fence: number;
  /** Hoja's deadline for this grant, not the runner's. */
  leaseUntil: string | null;
  grantedAt: string | null;
  finishState: string | null;
  finishedAt: string | null;
};

/** A runner as the screens render it: the server's `Runner` plus the derived `revoked` flag. */
export type Runner = {
  /** Stable server id. */
  id: string;
  /** Server name, e.g. "mac-mini-01". */
  name: string;
  workspaceId: string;
  /** online | offline, derived server-side from the heartbeat and any lease it holds. */
  status: RunnerStatus;
  /** False once an admin disabled it; a disabled runner is refused at the lease route. */
  enabled: boolean;
  /** True once the token no longer authenticates. Derived from `revokedAt`, which is the server's
   * own column — never from the token hash itself. */
  revoked: boolean;
  revokedAt: string | null;
  labels: string[];
  /** The harnesses this runner declared at registration: the lease route's allowlist. */
  harnesses: string[];
  /** The last heartbeat Hoja recorded; `null` before the first one. */
  lastSeenAt: string | null;
  /** The lease it holds right now, or `null`. */
  lease: RunnerLease | null;
};

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

/* ---------------- Claims and verdicts (WP10) ---------------- */

/** The three verdicts agent_verify.py records. `met` is the only one that moves a task on. */
export type Verdict = 'met' | 'not_met' | 'uncertain';

export const VERDICTS: {id: Verdict; name: string; color: string}[] = [
  {id: 'met', name: 'Met', color: '#2f9e6f'},
  {id: 'not_met', name: 'Not met', color: '#c0392b'},
  {id: 'uncertain', name: 'Uncertain', color: '#c98a1b'},
];

export function verdictLabel(id: Verdict | string | null): string {
  return VERDICTS.find(v => v.id === id)?.name || (id ? String(id) : 'Awaiting verifier');
}

export function verdictColor(id: Verdict | string | null): string {
  return VERDICTS.find(v => v.id === id)?.color || '#c98a1b';
}

/** One check a verifier re-read: `passed` | `failed` | `uncertain`, never a CI conclusion. */
export type VerificationCheck = {name: string; outcome: string; detail: string};

/** One verdict row from `agent_verification` (`AgentVerification`): append-only evidence. */
export type ClaimVerdict = {
  id: string;
  verdict: Verdict;
  /** What the verifier asked for; it differs from `verdict` when the evidence was weaker. */
  requestedVerdict: Verdict;
  verifierKind: string;
  verifierModel: string | null;
  workerModel: string | null;
  rationale: string | null;
  at: string;
  checks: VerificationCheck[];
};

/** One finish claim waiting on a verifier (`VerifierClaim` in agent_claims.py). */
export type Claim = {
  /** The session the claim belongs to: what `agentSessionVerify` takes. */
  sessionId: string;
  issueId: string;
  issueIdentifier: string;
  issueTitle: string;
  priority: number;
  /** The `AgentMember.id` and name of the agent that did the work, resolved by the adapter. */
  agentId: string;
  agentName: string;
  /** The claim itself, as the runner submitted it. */
  summary: string;
  acceptance: {item: string; evidence: string}[];
  claimAt: string;
  workerModel: string | null;
  verdict: Verdict | null;
  verifiedAt: string | null;
  verifierId: string | null;
  verifierModel: string | null;
  /** True until a separate principal settles it. `uncertain` keeps it true. */
  awaiting: boolean;
  verifications: ClaimVerdict[];
};

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
