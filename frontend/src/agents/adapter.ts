/* The single data seam for the agent surface (HOJA-602-A-16).
 *
 * Every screen reads through `getAgentsAdapter()`. Since slice 5 every screen but Connect reads the
 * server: WP04 gave agents their identity (`agent_member`, `AgentMember`, `Issue.delegate`), WP05
 * the audit ledger (`auditEvents`, admin-only, paged), WP06 the sessions and their transcript,
 * slice 4 the workspace-wide `workspaceAgentSessions` the run queue needs, and slice 5 the runner
 * and lease reads (`runners`, `runner`, `agentLeases` in agent_runners.py) plus the claim and
 * verdict reads (`verifierClaims`, `agentVerifications` in agent_claims.py).
 *
 * What is still a fixture is listed once, in `SAMPLE_SURFACES`, and its screen renders a visible
 * `SampleDataNotice`, so no sample row can be read as a server row.
 *
 * The interface is the *screens'* vocabulary, not the wire vocabulary, so a resolver that names
 * things differently is a translation problem inside this file rather than an edit to six
 * components. Two behaviours the backend owns are modelled here rather than in the UI, because
 * doing them in the UI would hide a missing audit trail:
 *
 *   - a policy write reports the audit row the server produced, or `null` when the server does not
 *     record one: `agentMemberUpdate` writes a `change_event`, not an `audit_event`. It never
 *     invents a row;
 *   - stopping a session is a state transition the server owns, and the server (not this file)
 *     writes the `agent.session.stop` audit event, which is read back through `auditEvents`.
 */

import {seedConnectEnabled, seedExposures} from './seed';
import type {
  Agent, AgentDetail, AgentEvent, AgentPolicy, AgentSession, AuditEvent, Claim, ClaimVerdict,
  ConnectExposure, RawExposure, Runner, RunnerLease, SessionStatus, VerificationCheck, Verdict,
} from './types';

export type PolicyPatch = Partial<AgentPolicy>;

/** One page of a connection, in the screens' vocabulary. The cursor is the server's own
 * `endCursor`, handed back verbatim: a client-side offset would silently skip a row whenever a
 * session is created between two reads. */
export type SessionPage = {sessions: AgentSession[]; hasNextPage: boolean; endCursor: string | null};
export type AuditPage = {events: AuditEvent[]; hasNextPage: boolean; endCursor: string | null};
export type RunnerPage = {runners: Runner[]; hasNextPage: boolean; endCursor: string | null};
export type ClaimPage = {claims: Claim[]; hasNextPage: boolean; endCursor: string | null};

/** The run queue's page size. Small enough that a `Load more` control is reachable in a test,
 * large enough that a normal queue is one read. */
export const SESSION_PAGE = 20;
export const AUDIT_PAGE = 25;
export const RUNNER_PAGE = 20;
export const CLAIM_PAGE = 20;

/** The create form's fields: `AgentMemberCreateInput` minus the ones the UI does not ask for. */
export type AgentDraft = {
  name: string;
  displayName?: string | null;
  capacity?: number;
  monthlyBudgetCredits?: number | null;
  workingHours?: string | null;
  timezone?: string | null;
  model?: string | null;
  runnerPool?: string | null;
};

/** What the issue page's Delegate picker needs: `user.id` (the value `delegateId` holds) and name. */
export type AgentChoice = {id: string; name: string};

export type AgentsAdapter = {
  /* -- server-backed: GraphQL, WP04 ---------------------------------------------------------- */
  listAgents(): Promise<Agent[]>;
  /** `AgentMember.id`. `null` means the workspace has no such agent, which the page states. */
  getAgent(id: string): Promise<AgentDetail | null>;
  createAgent(draft: AgentDraft): Promise<Agent>;
  /** Active agents only: an archived agent is not a valid delegate target (agent_members.py
   * `is_delegate_target`). */
  listAgentChoices(): Promise<AgentChoice[]>;
  /** Writes the member row and reports the audit row the server returned, or `null` if it keeps
   * none. */
  updatePolicy(id: string, patch: PolicyPatch, actor: string): Promise<{policy: AgentPolicy; audit: AuditEvent | null}>;

  /* -- server-backed: GraphQL, WP06 and slice 4 ---------------------------------------------- */
  /** One page of the workspace run queue, newest first. `agentId` is an `AgentMember.id`, so a
   * screen can pass the same value it uses in a route. */
  listSessions(options?: {agentId?: string; after?: string}): Promise<SessionPage>;
  /** Stops a run through `agentSessionStop`. A terminal session is returned unchanged by the
   * server's machine, which refuses the event rather than pretending it happened. */
  stopSession(sessionId: string): Promise<{session: AgentSession; audit: null}>;

  /* -- server-backed: GraphQL, WP05 ---------------------------------------------------------- */
  /** One page of the workspace audit ledger, oldest first, filtered server-side. A non-admin is
   * refused with FORBIDDEN, which the pane states instead of drawing an empty list. */
  listAuditEvents(filter?: {actor?: string; action?: string; after?: string}): Promise<AuditPage>;

  /* -- server-backed: GraphQL, slice 5 ------------------------------------------------------- */
  /** One page of the workspace's runners, newest first (`runners`, admin-gated). */
  listRunners(options?: {after?: string}): Promise<RunnerPage>;
  /** One runner (`runner`), or `null` when this workspace has no such machine. Its Connect
   * exposures come from the fixture below, and the screen says so. */
  getRunner(id: string): Promise<{runner: Runner; exposures: ConnectExposure[]} | null>;
  /** That runner's leases, newest first (`agentLeases(runnerId)`): what it took and what it did
   * with it, which is the question a machine's detail page answers. */
  listLeases(runnerId: string): Promise<RunnerLease[]>;
  /** One page of submitted claims, newest first (`verifierClaims`, admin-gated). `agentId` is an
   * `AgentMember.id`; `awaiting` narrows to the claims still waiting on a verifier. */
  listClaims(options?: {agentId?: string; awaiting?: boolean; after?: string}): Promise<ClaimPage>;
  /** One claim's verdict ledger (`agentVerifications(sessionId)`), newest first. */
  listVerdicts(sessionId: string): Promise<ClaimVerdict[]>;

  /* -- still a fixture: Connect has no server record (WP08) ----------------------------------- */
  /** Live exposures only: the supervisor revokes on TTL, so an expired row is never served. */
  listExposures(runnerId?: string): Promise<ConnectExposure[]>;
  /** The cloud-side kill switch, `ConnectSupervisor.is_enabled(org)`. */
  connectEnabled(): Promise<boolean>;
  /** Two-step on the screen; here it is the transition that also writes the audit row. */
  revokeExposure(exposureId: string, actor: string): Promise<{exposure: ConnectExposure; audit: AuditEvent}>;
};

/* One line to grep, and the single justification for every fixture row that can reach a screen. A
 * surface leaves this list when its resolver lands, not when its screen looks finished.
 *
 * Slice 5 removed `runners`: WP07 stores `runner` and `agent_lease`, and slice 5 added the
 * read-only `runners`/`agentLeases` queries. What remains is Connect itself — the tailcat exposure
 * store and the org kill switch (WP08). There is no table and no route for either: `audit.ACTIONS`
 * names `connect.exposure.open`/`connect.exposure.close` and nothing writes them. So the runners
 * list, the runner detail and every lease on them are server rows, and the exposures under the
 * Connect heading are the labelled sample. */
export const SAMPLE_SURFACES = {
  connect: {what: 'Connect exposures', pending: 'the WP08 tailcat exposure store'},
} as const;

/* ------------------------------------------------------------------ transport */

export type AgentApiCode = 'UNAUTHENTICATED' | 'FORBIDDEN' | 'NOT_FOUND' | 'BAD_USER_INPUT' | 'ERROR';

/** A GraphQL failure with the server's own code, so a 401/403 can be stated instead of drawn as
 * an empty list. `gql()` in main.tsx keeps only `errors[0].message`; the agent surface needs the
 * code and the HTTP status to tell "you are signed out" from "no agents yet". */
export class AgentApiError extends Error {
  code: AgentApiCode;
  constructor(code: AgentApiCode, message: string) {
    super(message);
    this.name = 'AgentApiError';
    this.code = code;
  }
}

function storedToken(): string | null {
  try {
    const t = localStorage.getItem('hoja_token');
    return t && t !== 'session' ? t : null;
  } catch {
    return null;
  }
}

async function request<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const token = storedToken();
  const res = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json', ...(token ? {authorization: `Bearer ${token}`} : {})},
    body: JSON.stringify({query, variables}),
  });
  let body: {data?: T; errors?: {message?: string; extensions?: {code?: string}}[]} | null = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const error = body?.errors?.[0];
  if (!res.ok || error) {
    const code = (error?.extensions?.code
      || (res.status === 401 ? 'UNAUTHENTICATED' : res.status === 403 ? 'FORBIDDEN' : res.status === 404 ? 'NOT_FOUND' : 'ERROR')) as AgentApiCode;
    const fallback = res.status === 401
      ? 'You are signed out. Sign in again to see agents.'
      : res.status === 403
        ? 'Only workspace admins can manage agents.'
        : `The agent service answered HTTP ${res.status}.`;
    throw new AgentApiError(code, error?.message || fallback);
  }
  if (!body?.data) throw new AgentApiError('ERROR', 'The agent service returned no data.');
  return body.data;
}

/* ------------------------------------------------------------------ mapping */

/* `AgentMember` (hoja_server/agent_members.py SDL) plus the `user` it wraps. The selection is the
 * whole of what a screen may show; a field added to the type but not asked for here stays absent,
 * and `fromServer` turns the three columns the server does not have into `null` explicitly. */
const AGENT_FIELDS = `{
  id userId capacity monthlyBudgetCredits workingHours timezone model runnerPool
  archivedAt createdAt updatedAt
  user { id name displayName }
}`;

type ServerAgent = {
  id: string; userId: string; capacity: number; monthlyBudgetCredits: number | null;
  workingHours: unknown; timezone: string | null; model: string | null; runnerPool: string | null;
  archivedAt: string | null; createdAt: string; updatedAt: string;
  user: { id: string; name: string | null; displayName: string | null } | null;
};

function hoursText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function fromServer(row: ServerAgent): Agent {
  return {
    id: row.id,
    key: row.id.slice(0, 8),
    userId: row.userId,
    name: row.user?.name || row.user?.displayName || row.userId,
    displayName: row.user?.displayName ?? null,
    capacity: row.capacity ?? 1,
    monthlyBudgetCredits: row.monthlyBudgetCredits ?? null,
    workingHours: hoursText(row.workingHours),
    timezone: row.timezone ?? null,
    model: row.model ?? null,
    runnerPool: row.runnerPool ?? null,
    archivedAt: row.archivedAt ?? null,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    status: row.archivedAt ? 'archived' : 'active',
    statusChangedAt: row.archivedAt || row.updatedAt,
    /* Not reported on main. `null` renders as "not reported", never as zero. */
    running: null,
    harness: null,
    owner: null,
  };
}

function policyOf(agent: Agent): AgentPolicy {
  return {
    name: agent.name,
    displayName: agent.displayName,
    capacity: agent.capacity,
    monthlyBudgetCredits: agent.monthlyBudgetCredits,
    model: agent.model,
    runnerPool: agent.runnerPool,
    timezone: agent.timezone,
    workingHours: agent.workingHours,
    archived: Boolean(agent.archivedAt),
  };
}

/** The sidebar edits working hours as JSON text; an unreadable document is refused here rather
 * than stored as a string the runner cannot parse. */
function parseHours(text: string | null): unknown {
  const trimmed = (text || '').trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    throw new AgentApiError('BAD_USER_INPUT', 'Working hours must be JSON, for example {"days":["mon","tue"],"from":"08:00","to":"19:00"}.');
  }
}

/** `AgentMemberUpdateInput` from a policy patch. Only the fields present in the patch are sent, so
 * one control's write cannot silently overwrite another's. */
function updateInput(patch: PolicyPatch): Record<string, unknown> {
  const input: Record<string, unknown> = {};
  if ('name' in patch) input.name = (patch.name || '').trim();
  if ('displayName' in patch) input.displayName = patch.displayName || null;
  if ('capacity' in patch) input.capacity = patch.capacity;
  if ('monthlyBudgetCredits' in patch) input.monthlyBudgetCredits = patch.monthlyBudgetCredits;
  if ('model' in patch) input.model = patch.model || null;
  if ('runnerPool' in patch) input.runnerPool = patch.runnerPool || null;
  if ('timezone' in patch) input.timezone = patch.timezone || null;
  if ('workingHours' in patch) input.workingHours = parseHours(patch.workingHours ?? null);
  if ('archived' in patch) input.archived = patch.archived;
  return input;
}

function createInput(draft: AgentDraft): Record<string, unknown> {
  const input: Record<string, unknown> = {name: (draft.name || '').trim()};
  if (draft.displayName) input.displayName = draft.displayName;
  if (draft.capacity !== undefined) input.capacity = draft.capacity;
  if (draft.monthlyBudgetCredits !== undefined) input.monthlyBudgetCredits = draft.monthlyBudgetCredits;
  if (draft.model) input.model = draft.model;
  if (draft.runnerPool) input.runnerPool = draft.runnerPool;
  if (draft.timezone) input.timezone = draft.timezone;
  if (draft.workingHours) input.workingHours = parseHours(draft.workingHours);
  return input;
}

/* ------------------------------------------------------------------ sessions (WP06) */

/* `AgentSession` in agent_sessions.py plus the issue the queue row shows. `activities` is a
 * separate selection because it costs a query per session on the server: the queue never renders a
 * transcript, so it never asks for one. */
const SESSION_FIELDS = `id issueId agentUserId status state queueReason admitted trigger
  ackedAt activatedAt endedAt createdAt updatedAt
  claim claimAt workerModel verdict verifiedAt verifierId verifierModel
  issue { id identifier title priority }`;
const ACTIVITY_FIELDS = `id kind authorKind authorId body payload signal createdAt`;

type ServerActivity = {
  id: string; kind: AgentEvent['kind']; authorKind: string; authorId: string | null;
  body: string | null; payload: unknown; signal: string | null; createdAt: string;
};

type ServerSession = {
  id: string; issueId: string; agentUserId: string;
  /** Linear's seven `AgentSessionStatus` values. */
  status: 'pending' | 'active' | 'awaitingInput' | 'complete' | 'error' | 'stale' | 'stopping';
  /** The ten-state assignment machine (agent_sessions.py `STATES`). */
  state: string;
  queueReason: string[]; admitted: boolean; trigger: string;
  ackedAt: string | null; activatedAt: string | null; endedAt: string | null; createdAt: string;
  updatedAt: string;
  /** WP10: the claim and the verdict, straight off the session row. */
  claim: {summary?: string; acceptance?: unknown} | null;
  claimAt: string | null; workerModel: string | null;
  verdict: Verdict | null; verifiedAt: string | null;
  verifierId: string | null; verifierModel: string | null;
  issue: {id: string; identifier: string; title: string; priority: number | null} | null;
  activities?: ServerActivity[];
};

/* The ten server states → the five groups the run queue draws (HOJA-602-A-16 screen 3), plus
 * `failed`, which the server can report and the queue must not fold into "done": a run that failed
 * verification did not finish its work. `stale` is a *waiting* session past its window, so it
 * belongs with queued, not with the terminal states. */
const STATUS_BY_STATE: Record<string, SessionStatus> = {
  delegated: 'queued',
  queued: 'queued',
  leased: 'active',
  running: 'active',
  awaiting_input: 'awaiting-input',
  verifying: 'awaiting-verification',
  in_review: 'done',
  done: 'done',
  failed: 'failed',
  cancelled: 'done',
};

function sessionStatus(state: string, status: ServerSession['status']): SessionStatus {
  if (status === 'error') return 'failed';
  return STATUS_BY_STATE[state] || 'queued';
}

function issueOf(row: ServerSession) {
  return {
    issueId: row.issueId,
    issueIdentifier: row.issue?.identifier || row.issueId.slice(0, 8),
    issueTitle: row.issue?.title || 'This task',
    priority: row.issue?.priority ?? 0,
  };
}

function fromSession(row: ServerSession, agent?: {id: string; name: string}): AgentSession {
  const reasons = Array.isArray(row.queueReason) ? row.queueReason : [];
  return {
    id: row.id,
    agentId: agent?.id || '',
    agentName: agent?.name || 'agent',
    agentUserId: row.agentUserId,
    status: sessionStatus(row.state, row.status),
    state: row.state,
    queueReason: reasons,
    reason: reasons.length ? reasons.join(', ') : undefined,
    admitted: Boolean(row.admitted),
    ...issueOf(row),
    startedAt: row.activatedAt || row.createdAt,
    endedAt: row.endedAt || undefined,
    /* WP10's two halves, as the server stores them: the claim the runner submitted (immutable
     * once written) and the verdict a separate principal recorded. `null` here means "no claim" or
     * "nobody has signed", never "unknown". */
    claim: row.claim ? {summary: String(row.claim.summary || ''), acceptance: acceptanceOf(row.claim.acceptance)} : null,
    claimAt: row.claimAt ?? undefined,
    workerModel: row.workerModel ?? null,
    verdict: row.verdict ?? null,
  };
}

/* `AgentMember.id` for a session's agent, because that is the id the agent routes take. One extra
 * read per page, not one per row. */
async function membersByUserId(): Promise<Map<string, {id: string; name: string}>> {
  const data = await request<{agentMembers: {id: string; userId: string; user: {name: string | null; displayName: string | null} | null}[]}>(
    `query SessionAgents { agentMembers { id userId user { id name displayName } } }`);
  const byUser = new Map<string, {id: string; name: string}>();
  for (const row of data.agentMembers || []) {
    byUser.set(row.userId, {id: row.id, name: row.user?.name || row.user?.displayName || row.userId});
  }
  return byUser;
}

function eventsOf(sessions: ServerSession[]): AgentEvent[] {
  const events: AgentEvent[] = [];
  for (const session of sessions) {
    for (const activity of session.activities || []) {
      events.push({
        id: activity.id,
        sessionId: session.id,
        kind: activity.kind,
        at: activity.createdAt,
        text: activity.body || '',
      });
    }
  }
  return events.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
}

/* ------------------------------------------------------------------ runners (WP07, slice 5) */

/* `Runner` and `AgentLease` in agent_runners.py. The selection is the whole of what a screen may
 * show: the credential columns (`lease_token_hash`, `token_hint`) are not in the SDL, so they
 * cannot be asked for by accident. */
const RUNNER_FIELDS = `id name organizationId status enabled revokedAt labels harnesses
  lastHeartbeatAt createdAt
  currentLease { id taskId state harness fence leaseUntil grantedAt finishState finishedAt
                 issue { identifier } }`;
const LEASE_FIELDS = `id taskId state harness fence leaseUntil grantedAt finishState finishedAt
  issue { identifier }`;

type ServerLease = {
  id: string; taskId: string; state: string; harness: string; fence: number;
  leaseUntil: string | null; grantedAt: string | null; finishState: string | null;
  finishedAt: string | null; issue: { identifier: string } | null;
};

type ServerRunner = {
  id: string; name: string; organizationId: string; status: string; enabled: boolean;
  revokedAt: string | null; labels: string[]; harnesses: string[];
  lastHeartbeatAt: string | null; createdAt: string; currentLease: ServerLease | null;
};

function fromLease(row: ServerLease): RunnerLease {
  return {
    id: row.id,
    taskId: row.taskId,
    taskIdentifier: row.issue?.identifier ?? null,
    state: row.state,
    harness: row.harness,
    fence: row.fence,
    leaseUntil: row.leaseUntil ?? null,
    grantedAt: row.grantedAt ?? null,
    finishState: row.finishState ?? null,
    finishedAt: row.finishedAt ?? null,
  };
}

function fromRunner(row: ServerRunner): Runner {
  return {
    id: row.id,
    name: row.name,
    workspaceId: row.organizationId,
    /* Derived server-side from the heartbeat and any lease it holds; this file does not
     * second-guess it with a clock of its own. */
    status: row.status === 'online' ? 'online' : 'offline',
    enabled: Boolean(row.enabled),
    /* The token hash is the credential column (FR-018). `revokedAt` is the server's own fact about
     * it: a revoked runner's token no longer authenticates. The hash itself never ships. */
    revoked: Boolean(row.revokedAt),
    revokedAt: row.revokedAt ?? null,
    labels: row.labels || [],
    harnesses: row.harnesses || [],
    lastSeenAt: row.lastHeartbeatAt ?? null,
    lease: row.currentLease ? fromLease(row.currentLease) : null,
  };
}

/* ------------------------------------------------------------------ claims (WP10, slice 5) */

/* `VerifierClaim`, `AgentVerification` and `VerificationCheck` in agent_claims.py. The verdict rows
 * come nested on the claim because the server loads them with the page: one read, not one per row. */
const CLAIM_FIELDS = `sessionId issueId agentUserId claim claimAt workerModel verdict verifiedAt
  verifierId verifierModel awaiting
  issue { identifier title priority }
  verifications { id verdict requestedVerdict verifierKind verifierModel workerModel rationale
                  createdAt checks { name outcome detail } }`;

type ServerCheck = {name: string; outcome: string; detail: string};

type ServerVerdict = {
  id: string; verdict: Verdict; requestedVerdict: Verdict; verifierKind: string;
  verifierModel: string | null; workerModel: string | null; rationale: string | null;
  createdAt: string; checks: ServerCheck[];
};

type ServerClaim = {
  sessionId: string; issueId: string; agentUserId: string;
  claim: {summary?: string; acceptance?: unknown} | null;
  claimAt: string; workerModel: string | null;
  verdict: Verdict | null; verifiedAt: string | null;
  verifierId: string | null; verifierModel: string | null; awaiting: boolean;
  issue: { identifier: string; title: string; priority: number | null } | null;
  verifications: ServerVerdict[];
};

/** The claim's acceptance answers, in the two shapes a runner may send (agent_verify.py
 * `_claimed_items`): a list of strings, or a list of `{item, evidence}` objects. */
function acceptanceOf(value: unknown): {item: string; evidence: string}[] {
  if (!Array.isArray(value)) return [];
  return value.map(entry => {
    if (typeof entry === 'string') return {item: entry, evidence: ''};
    if (entry && typeof entry === 'object') {
      const row = entry as {item?: unknown; evidence?: unknown};
      return {item: String(row.item ?? ''), evidence: String(row.evidence ?? '')};
    }
    return {item: '', evidence: ''};
  }).filter(row => row.item);
}

function fromVerdict(row: ServerVerdict): ClaimVerdict {
  return {
    id: row.id,
    verdict: row.verdict,
    requestedVerdict: row.requestedVerdict,
    verifierKind: row.verifierKind,
    verifierModel: row.verifierModel ?? null,
    workerModel: row.workerModel ?? null,
    rationale: row.rationale ?? null,
    at: row.createdAt,
    checks: (row.checks || []).map((check: ServerCheck): VerificationCheck => ({
      name: check.name, outcome: check.outcome, detail: check.detail,
    })),
  };
}

function fromClaim(row: ServerClaim, agent?: {id: string; name: string}): Claim {
  const claim = row.claim || {};
  return {
    sessionId: row.sessionId,
    issueId: row.issueId,
    issueIdentifier: row.issue?.identifier || row.issueId.slice(0, 8),
    issueTitle: row.issue?.title || 'This task',
    priority: row.issue?.priority ?? 0,
    agentId: agent?.id || '',
    agentName: agent?.name || 'agent',
    summary: String(claim.summary || ''),
    acceptance: acceptanceOf(claim.acceptance),
    claimAt: row.claimAt,
    workerModel: row.workerModel ?? null,
    verdict: row.verdict ?? null,
    verifiedAt: row.verifiedAt ?? null,
    verifierId: row.verifierId ?? null,
    verifierModel: row.verifierModel ?? null,
    awaiting: Boolean(row.awaiting),
    verifications: (row.verifications || []).map(fromVerdict),
  };
}

/* ------------------------------------------------------------------ audit (WP05) */

const AUDIT_FIELDS = `seq action actorKind actorId onBehalfOf targetKind targetId detail createdAt`;

type ServerAudit = {
  seq: number; action: string; actorKind: string; actorId: string;
  onBehalfOf: string | null; targetKind: string | null; targetId: string | null;
  detail: string | null; createdAt: string;
};

function fromAudit(row: ServerAudit): AuditEvent {
  return {
    id: `#${row.seq}`,
    at: row.createdAt,
    actor: row.actorId,
    actorKind: row.actorKind,
    action: row.action,
    target: row.targetId || row.targetKind || '—',
    detail: row.detail || '',
    onBehalfOf: row.onBehalfOf,
  };
}

/* ------------------------------------------------------------------ the GraphQL half */

/** The WP04 half of the surface: `agentMembers`/`agentMember` reads, `agentMemberCreate` and
 * `agentMemberUpdate` writes. Both mutations are admin-gated server-side; the adapter does not
 * pre-check the role, because a client-side check is not an authorization boundary and a refused
 * write should surface the server's own FORBIDDEN. */
function createGraphqlAdapter(sample: AgentsAdapter): AgentsAdapter {
  return {
    ...sample,

    async listAgents() {
      const data = await request<{agentMembers: ServerAgent[]}>(
        `query Agents { agentMembers ${AGENT_FIELDS} }`);
      return (data.agentMembers || []).map(fromServer);
    },

    async getAgent(id: string) {
      const data = await request<{agentMember: ServerAgent | null}>(
        `query Agent($id: String!) { agentMember(id: $id) ${AGENT_FIELDS} }`, {id});
      if (!data.agentMember) return null;
      const agent = fromServer(data.agentMember);
      /* WP06 stores the runs and their transcript, so the page reads both through the workspace
       * session connection: the transcript is nested on the session, which is where the server
       * keeps it, so this is one read and not one per run. */
      const runs = await request<{workspaceAgentSessions: {nodes: ServerSession[]}}>(
        `query AgentRuns($agentId: String!) {
           workspaceAgentSessions(agentId: $agentId, first: 50) {
             nodes { ${SESSION_FIELDS} activities(first: 50) { ${ACTIVITY_FIELDS} } } } }`,
        {agentId: agent.userId});
      const rows = runs.workspaceAgentSessions?.nodes || [];
      return {
        agent,
        policy: policyOf(agent),
        sessions: rows.map(row => fromSession(row, {id: agent.id, name: agent.name})),
        events: eventsOf(rows),
      };
    },

    async createAgent(draft: AgentDraft) {
      const data = await request<{agentMemberCreate: {success: boolean; agentMember: ServerAgent | null}}>(
        `mutation AgentCreate($input: AgentMemberCreateInput!) {
           agentMemberCreate(input: $input) { success agentMember ${AGENT_FIELDS} }
         }`, {input: createInput(draft)});
      const row = data.agentMemberCreate?.agentMember;
      if (!data.agentMemberCreate?.success || !row) throw new AgentApiError('ERROR', 'The agent was not created.');
      return fromServer(row);
    },

    async listAgentChoices() {
      const data = await request<{agentMembers: ServerAgent[]}>(
        `query AgentChoices { agentMembers { id userId archivedAt user { id name displayName } } }`);
      return (data.agentMembers || [])
        .filter(row => !row.archivedAt)
        .map(row => ({id: row.userId, name: row.user?.name || row.user?.displayName || row.userId}));
    },

    async updatePolicy(id: string, patch: PolicyPatch, actor: string) {
      const input = updateInput(patch);
      if (!Object.keys(input).length) {
        const current = await this.getAgent(id);
        if (!current) throw new AgentApiError('NOT_FOUND', 'That agent no longer exists.');
        return {policy: current.policy, audit: null};
      }
      const data = await request<{agentMemberUpdate: {success: boolean; agentMember: ServerAgent | null}}>(
        `mutation AgentUpdate($id: String!, $input: AgentMemberUpdateInput!) {
           agentMemberUpdate(id: $id, input: $input) { success agentMember ${AGENT_FIELDS} }
         }`, {id, input});
      const row = data.agentMemberUpdate?.agentMember;
      if (!data.agentMemberUpdate?.success || !row) throw new AgentApiError('ERROR', 'The change was not saved.');
      /* `actor` is deliberately unused: the server records the actor on the change event (WP04
       * change_event.actor_*), not in the response, and inventing an audit row here would claim a
       * trail that `auditEvents` (WP05) cannot read back. */
      void actor;
      return {policy: policyOf(fromServer(row)), audit: null};
    },

    async listSessions(options) {
      /* `workspaceAgentSessions` filters by the agent's `user.id` (it is
       * `agent_session.agent_user_id`) while every route carries the `AgentMember.id`, so the
       * translation happens here, once, instead of in each screen. */
      const [members, agentUserId] = await Promise.all([
        membersByUserId(),
        options?.agentId ? agentUserFor(options.agentId) : Promise.resolve(null),
      ]);
      const data = await request<{workspaceAgentSessions: {nodes: ServerSession[]; pageInfo: {hasNextPage: boolean; endCursor: string | null}}}>(
        `query Sessions($first: Int!, $after: String, $agentId: String) {
           workspaceAgentSessions(first: $first, after: $after, agentId: $agentId) {
             nodes { ${SESSION_FIELDS} }
             pageInfo { hasNextPage endCursor } } }`,
        {first: SESSION_PAGE, after: options?.after ?? null, agentId: agentUserId});
      const page = data.workspaceAgentSessions;
      return {
        sessions: (page?.nodes || []).map(row => fromSession(row, members.get(row.agentUserId))),
        hasNextPage: Boolean(page?.pageInfo?.hasNextPage),
        endCursor: page?.pageInfo?.endCursor ?? null,
      };
    },

    async stopSession(sessionId: string) {
      const data = await request<{agentSessionStop: {success: boolean; agentSession: ServerSession | null}}>(
        `mutation Stop($id: String!) {
           agentSessionStop(id: $id) { success agentSession { ${SESSION_FIELDS} } } }`,
        {id: sessionId});
      const row = data.agentSessionStop?.agentSession;
      if (!data.agentSessionStop?.success || !row) throw new AgentApiError('ERROR', 'The session was not stopped.');
      /* `null`, not a fabricated row: the server writes the `agent.session.stop` event into the
       * ledger itself, and the audit screen reads it back through `auditEvents`. */
      return {session: fromSession(row), audit: null};
    },

    async listAuditEvents(filter) {
      const data = await request<{auditEvents: {nodes: ServerAudit[]; pageInfo: {hasNextPage: boolean; endCursor: string | null}}}>(
        `query Audit($first: Int!, $after: String, $actorId: String, $action: String) {
           auditEvents(first: $first, after: $after, actorId: $actorId, action: $action) {
             nodes { ${AUDIT_FIELDS} }
             pageInfo { hasNextPage endCursor } } }`,
        {first: AUDIT_PAGE, after: filter?.after ?? null,
          actorId: filter?.actor || null, action: filter?.action || null});
      const page = data.auditEvents;
      return {
        events: (page?.nodes || []).map(fromAudit),
        hasNextPage: Boolean(page?.pageInfo?.hasNextPage),
        endCursor: page?.pageInfo?.endCursor ?? null,
      };
    },

    /* -- slice 5: runners and leases ---------------------------------------------------- */

    async listRunners(options) {
      const data = await request<{runners: {nodes: ServerRunner[]; pageInfo: {hasNextPage: boolean; endCursor: string | null}}}>(
        `query Runners($first: Int!, $after: String) {
           runners(first: $first, after: $after) {
             nodes { ${RUNNER_FIELDS} }
             pageInfo { hasNextPage endCursor } } }`,
        {first: RUNNER_PAGE, after: options?.after ?? null});
      const page = data.runners;
      return {
        runners: (page?.nodes || []).map(fromRunner),
        hasNextPage: Boolean(page?.pageInfo?.hasNextPage),
        endCursor: page?.pageInfo?.endCursor ?? null,
      };
    },

    async getRunner(id: string) {
      const data = await request<{runner: ServerRunner | null}>(
        `query RunnerOne($id: String!) { runner(id: $id) { ${RUNNER_FIELDS} } }`, {id});
      if (!data.runner) return null;
      /* The machine is a server row; the exposures under the Connect heading are not. They belong
       * to no real runner (WP08 has no store), and the detail screen says exactly that under its
       * sample notice rather than passing them off as this machine's. */
      const exposures = await sample.listExposures();
      return {runner: fromRunner(data.runner), exposures};
    },

    async listLeases(runnerId: string) {
      const data = await request<{agentLeases: {nodes: ServerLease[]}}>(
        `query Leases($runnerId: String) {
           agentLeases(first: 20, runnerId: $runnerId) { nodes { ${LEASE_FIELDS} } } }`,
        {runnerId});
      return (data.agentLeases?.nodes || []).map(fromLease);
    },

    /* -- slice 5: claims and verdicts --------------------------------------------------- */

    async listClaims(options) {
      /* `verifierClaims` filters by the agent's `user.id`, exactly as `workspaceAgentSessions`
       * does, while every route carries the `AgentMember.id`. The translation happens here once. */
      const [members, agentUserId] = await Promise.all([
        membersByUserId(),
        options?.agentId ? agentUserFor(options.agentId) : Promise.resolve(null),
      ]);
      const data = await request<{verifierClaims: {nodes: ServerClaim[]; pageInfo: {hasNextPage: boolean; endCursor: string | null}}}>(
        `query Claims($first: Int!, $after: String, $agentId: String, $awaiting: Boolean) {
           verifierClaims(first: $first, after: $after, agentId: $agentId, awaiting: $awaiting) {
             nodes { ${CLAIM_FIELDS} }
             pageInfo { hasNextPage endCursor } } }`,
        {first: CLAIM_PAGE, after: options?.after ?? null,
          agentId: agentUserId, awaiting: options?.awaiting ?? null});
      const page = data.verifierClaims;
      return {
        claims: (page?.nodes || []).map(row => fromClaim(row, members.get(row.agentUserId))),
        hasNextPage: Boolean(page?.pageInfo?.hasNextPage),
        endCursor: page?.pageInfo?.endCursor ?? null,
      };
    },

    async listVerdicts(sessionId: string) {
      const data = await request<{agentVerifications: ServerVerdict[]}>(
        `query Verdicts($sessionId: String) {
           agentVerifications(sessionId: $sessionId) {
             id verdict requestedVerdict verifierKind verifierModel workerModel rationale createdAt
             checks { name outcome detail } } }`, {sessionId});
      return (data.agentVerifications || []).map(fromVerdict);
    },
  };
}

async function agentUserFor(memberId: string): Promise<string | null> {
  const data = await request<{agentMember: {userId: string} | null}>(
    `query AgentUser($id: String!) { agentMember(id: $id) { userId } }`, {id: memberId});
  return data.agentMember?.userId ?? null;
}

/* ------------------------------------------------------------------ the sample half */

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function nowIso(): string {
  return new Date().toISOString();
}

function auditId(): string {
  return `a-${Math.random().toString(36).slice(2, 10)}`;
}

/* The projection exists for the same reason `Exposure.public_row()` does in
 * runner/hoja_runnerd/connect.py: the tailcat address is a bearer secret (connect.py:8-12), so it
 * is dropped by structural destructuring rather than by remembering not to render it. There is no
 * runner credential to drop any more: the runner half is read from GraphQL, where the SDL does not
 * carry `lease_token_hash` or `token_hint` at all. */
export function publicExposure(row: RawExposure): ConnectExposure {
  const {address: _address, ...rest} = row;
  return rest;
}

/** The fixture behind `SAMPLE_SURFACES.connect`, and nothing else. Every other read on this
 * interface is server-backed and replaced by `createGraphqlAdapter`; the stubs below exist so a
 * missing override fails loudly instead of silently serving a fixture to a real screen. */
function createSampleAdapter(): AgentsAdapter {
  let exposures: RawExposure[] = clone(seedExposures);
  /* The kill switch, sample-only. `ConnectSupervisor.set_org_enabled` is an admin action owned by
   * WP12's policy API, not by this screen, so nothing here toggles it: until the real adapter
   * lands, `localStorage.hoja_agents_connect = 'off'` reproduces the disabled org (the same
   * per-browser affordance flag.ts uses for the surface itself) and the geometry test uses it. */
  const connectOn = (): boolean => {
    try {
      if ((localStorage.getItem('hoja_agents_connect') || '').trim().toLowerCase() === 'off') return false;
    } catch {
      /* no storage: fall through to the fixture's state */
    }
    return seedConnectEnabled;
  };

  /* `_expire` revokes at TTL (connect.py:683-689); serving an expired row would claim a tunnel
   * that the supervisor has already killed. */
  const live = (): RawExposure[] => {
    const now = Date.now() / 1000;
    return exposures.filter(e => e.live && e.expiresAt > now);
  };

  const serverOnly = (what: string) => {
    throw new Error(`No backend for ${what} on this server — see SAMPLE_SURFACES in agents/adapter.ts.`);
  };

  return {
    async listAgents() { return serverOnly('the agent list'); },
    async getAgent() { return serverOnly('an agent'); },
    async createAgent() { return serverOnly('agent create'); },
    async listAgentChoices() { return serverOnly('the delegate picker'); },
    async updatePolicy() { return serverOnly('agent edits'); },

    async listSessions() { return serverOnly('the run queue'); },
    async listAuditEvents() { return serverOnly('the audit log'); },
    async stopSession() { return serverOnly('stopping a session'); },

    async listRunners() { return serverOnly('the runner list'); },
    async getRunner() { return serverOnly('a runner'); },
    async listLeases() { return serverOnly('a runner\'s leases'); },
    async listClaims() { return serverOnly('the claims waiting on a verifier'); },
    async listVerdicts() { return serverOnly('a claim\'s verdicts'); },

    async listExposures(runnerId?: string) {
      /* The fixture belongs to no runner, so the filter is only ever "all of them". A caller that
       * passes an id gets the sample rows, which the screen labels as a sample. */
      void runnerId;
      return clone(live().map(publicExposure));
    },
    async connectEnabled() {
      return connectOn();
    },
    async revokeExposure(exposureId: string, actor: string) {
      if (!connectOn()) throw new Error('Connect is disabled for this workspace.');
      const row = exposures.find(e => e.id === exposureId);
      if (!row) throw new Error('That exposure is not live.');
      exposures = exposures.filter(e => e.id !== exposureId);
      const entry: AuditEvent = {
        id: auditId(), at: nowIso(), actor, action: 'connect.revoked', target: row.id,
        detail: `ports ${row.ports.join(',')} · peer ${row.pinnedPeer.slice(0, 15)}… · process gone`,
      };
      /* The returned row is what the detail screen reports. It is *not* written to the ledger:
       * Connect is a fixture (WP08), and `auditEvents` reads the server's table, so a fixture event
       * could never appear there and must not pretend it would. */
      return clone({exposure: publicExposure({...row, live: false}), audit: entry});
    },
  };
}

let adapter: AgentsAdapter = createGraphqlAdapter(createSampleAdapter());

/** The seam WP06/WP05/WP07 use when their resolvers land: replace the whole adapter, or pass a
 * narrower one in a test. Nothing outside this file knows which halves are fixtures. */
export function setAgentsAdapter(next: AgentsAdapter): void {
  adapter = next;
}

export function getAgentsAdapter(): AgentsAdapter {
  return adapter;
}

/** The fixture implementation, complete with its loud server-only stubs, so a test can assert the
 * swap seam and that no screen is served a fixture for a server-backed read. */
export function sampleAgentsAdapter(): AgentsAdapter {
  return createSampleAdapter();
}
