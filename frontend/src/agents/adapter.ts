/* The single data seam for the agent surface (HOJA-602-A-16).
 *
 * Every screen reads the server. WP04 gave agents their identity (`agent_member`, `AgentMember`,
 * `Issue.delegate`), WP05 the audit ledger (`auditEvents`, admin-only, paged), WP06 the sessions and
 * their transcript, slice 4 the workspace-wide `workspaceAgentSessions` the run queue needs, slice 5
 * the runner and lease reads (`runners`, `runner`, `agentLeases` in agent_runners.py) plus the claim
 * and verdict reads (`verifierClaims`, `agentVerifications` in agent_claims.py), and slice 6 the
 * Connect surface: `connectEndpoints` and `connectEndpointClose` in connect_endpoints.py, over the
 * `connect_endpoint` rows the runner reports on `/runner` (revision 71, WP09).
 *
 * There is no fixture left, so there is no `SAMPLE_SURFACES` list and no sample notice: a read with
 * no server behind it fails loudly through `serverOnly`, which is the only honest thing a seam can
 * do once every surface has a resolver.
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

import type {
  Agent, AgentAnswer, AgentAsk, RunAccess, SetupProbe, AgentDetail, AgentEvent, AgentQuestion, AgentPolicy, AgentSession, AuditEvent, Claim, ClaimVerdict,
  ConnectGrant, HarnessPolicy,
  ConnectEndpointRow, ConnectExposure, Runner, RunnerEnrollment, RunnerEnrollmentGrant, RunnerLease,
  SessionStatus, VerificationCheck, Verdict,
} from './types';

export type PolicyPatch = Partial<AgentPolicy>;

/** The two `agentPolicyUpdate` fields this screen edits. Absent means "leave it alone". */
export type HarnessPatch = Partial<Pick<HarnessPolicy, 'defaultHarness' | 'harnessAllowlist'>>;


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
  /** The harness half of WP12's policy (HOJA-602-A-22), written through `agentPolicyUpdate`. The
   * server validates the name against the profiles it knows and refuses a default the allowlist
   * forbids, so the caller's refusal is the server's, not a local guess. */
  updateHarnessPolicy(id: string, patch: HarnessPatch, actor: string): Promise<{harnesses: HarnessPolicy; audit: AuditEvent | null}>;
  /** The Connect half of the same policy row: the host:port pairs this agent's runs may expose.
   * The whole list is sent, because a removed grant is expressed by its absence - an absent field
   * would mean "leave the stored list alone" (`agent_policy.clean_input`). */
  updateConnectPolicy(id: string, grants: ConnectGrant[], actor: string): Promise<{connect: ConnectGrant[]; audit: AuditEvent | null}>;

  /* -- server-backed: GraphQL, WP06 and slice 4 ---------------------------------------------- */
  /** One page of the workspace run queue, newest first. `agentId` is an `AgentMember.id`, so a
   * screen can pass the same value it uses in a route. */
  listSessions(options?: {agentId?: string; after?: string}): Promise<SessionPage>;
  /** Stops a run through `agentSessionStop`. A terminal session is returned unchanged by the
   * server's machine, which refuses the event rather than pretending it happened. */
  stopSession(sessionId: string): Promise<{session: AgentSession; audit: null}>;
  /** Answer a session that is awaiting input. With a live run the answer goes back to it. */
  answerSession(sessionId: string, body: string, response?: AgentAnswer): Promise<void>;
  /** The run policy fields the setup checklist needs, plus the workspace's teams and secret names. */
  getRunAccess(agentId: string): Promise<RunAccess>;
  /** Turn the workspace's Agents switch on (admin only; the same mutation as Settings). */
  enableAgents(): Promise<void>;
  /** The repository a project's agent tasks work in (null: none). */
  getProjectRepository(projectId: string): Promise<string | null>;
  setProjectRepository(projectId: string, repository: string | null): Promise<string | null>;
  updateRunAccess(agentId: string, patch: {teams?: string[]; secrets?: string[];
    repos?: {repo: string; branches: string[]}[]}): Promise<void>;
  /** Store a named workspace secret. The value is sealed server-side and never read back. */
  setSecret(name: string, value: string): Promise<void>;
  /** Create a small task delegated to the agent, to prove the whole chain works. */
  createSetupTask(agentUserId: string, teamId: string): Promise<SetupProbe>;
  getSetupProbe(issueId: string): Promise<SetupProbe>;

  /* -- server-backed: GraphQL, WP05 ---------------------------------------------------------- */
  /** One page of the workspace audit ledger, oldest first, filtered server-side. A non-admin is
   * refused with FORBIDDEN, which the pane states instead of drawing an empty list. */
  listAuditEvents(filter?: {actor?: string; action?: string; after?: string}): Promise<AuditPage>;

  /* -- server-backed: GraphQL, slice 5 ------------------------------------------------------- */
  /** One page of the workspace's runners, newest first (`runners`, admin-gated). */
  listRunners(options?: {after?: string}): Promise<RunnerPage>;
  /** One runner (`runner`), or `null` when this workspace has no such machine. Its Connect
   * exposures are that machine's own `connect_endpoint` rows, read in the same call. */
  getRunner(id: string): Promise<{runner: Runner; exposures: ConnectExposure[]} | null>;
  /** That runner's leases, newest first (`agentLeases(runnerId)`): what it took and what it did
   * with it, which is the question a machine's detail page answers. */
  listLeases(runnerId: string): Promise<RunnerLease[]>;
  /** One page of submitted claims, newest first (`verifierClaims`, admin-gated). `agentId` is an
   * `AgentMember.id`; `awaiting` narrows to the claims still waiting on a verifier. */
  listClaims(options?: {agentId?: string; awaiting?: boolean; after?: string}): Promise<ClaimPage>;
  /** One claim's verdict ledger (`agentVerifications(sessionId)`), newest first. */
  listVerdicts(sessionId: string): Promise<ClaimVerdict[]>;

  /* -- server-backed: GraphQL, HOJA-635 (the write half of the runner surface) ---------------- */
  /** Every enrollment token in the workspace (`runnerEnrollments`), admin-gated. Never a secret:
   *  the server's `RunnerEnrollment` shape has no token and no hash to serve. */
  listRunnerEnrollments(): Promise<RunnerEnrollment[]>;
  /** Mint a single-use token. This response is the only place the plaintext exists, so the caller
   *  must hold it in component state and drop it when the panel closes. */
  createRunnerEnrollment(name: string, ttlMinutes?: number, workRoot?: string | null): Promise<RunnerEnrollmentGrant>;
  /** Withdraw an unspent token. `false` means it was already spent or already withdrawn —
   *  `/runner/enroll` refuses it either way. */
  revokeRunnerEnrollment(id: string): Promise<boolean>;
  /** Disable an enrolled machine. Its bearer token stops authenticating at /runner/claim. */
  revokeRunner(id: string): Promise<boolean>;

  /* -- server-backed: GraphQL, slice 6 (WP09) ------------------------------------------------- */
  /** Live exposures, grouped per serve process (`connectEndpoints(live: true)`, admin-gated). */
  listExposures(runnerId?: string): Promise<ConnectExposure[]>;
  /** The workspace kill switch (`workspaceSecurityPolicy.connectEnabled`): a state, not an empty list. */
  connectEnabled(): Promise<boolean>;
  /** `connectEndpointClose`: closes the rows, writes the audit event and tells the runner on its
   * next heartbeat. `audit` is `null` on purpose — the ledger row is the server's, and this file
   * does not fabricate one (the audit pane reads it back through `auditEvents`). */
  revokeExposure(exposureId: string, actor: string): Promise<{exposure: ConnectExposure; audit: null}>;
};

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
const SESSION_FIELDS = `id issueId agentUserId status state queueReason admitted trigger harness
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
  /** HOJA-602-A-22: the harness the lease names; null until a lease exists. */
  harness: string | null;
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

export function sessionStatus(state: string, status: ServerSession['status']): SessionStatus {
  if (status === 'error') return 'failed';
  /* A state this screen does not know is drawn as active, never as queued: "queued" would claim
   * the run has not started, which a newer server state almost certainly contradicts. */
  return STATUS_BY_STATE[state] || 'active';
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
    harness: row.harness ?? null,
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

/* The questions a harness asked (runner_api `session.ask`), validated: anything malformed is
 * `null`, which the screen draws as a plain "needs input" line with a free-text answer. */
function askOf(payload: Record<string, unknown>): AgentAsk | null {
  const askId = payload.ask_id;
  const raw = payload.questions;
  if (typeof askId !== 'string' || !Array.isArray(raw)) return null;
  const questions: AgentQuestion[] = [];
  for (const q of raw) {
    if (!q || typeof q !== 'object') continue;
    const item = q as Record<string, unknown>;
    if (typeof item.header !== 'string' || !Array.isArray(item.options)) continue;
    questions.push({
      header: item.header,
      question: typeof item.question === 'string' ? item.question : item.header,
      multiSelect: item.multi_select === true,
      options: (item.options as Record<string, unknown>[])
        .filter((o) => o && typeof o.label === 'string')
        .map((o) => ({label: o.label as string,
          description: typeof o.description === 'string' ? o.description : null})),
    });
  }
  return questions.length ? {askId, questions} : null;
}

function eventsOf(sessions: ServerSession[]): AgentEvent[] {
  const events: AgentEvent[] = [];
  for (const session of sessions) {
    for (const activity of session.activities || []) {
      const payload = (activity.payload && typeof activity.payload === 'object'
        ? activity.payload : {}) as Record<string, unknown>;
      events.push({
        id: activity.id,
        sessionId: session.id,
        kind: activity.kind,
        at: activity.createdAt,
        text: activity.body || '',
        author: activity.authorKind === 'user' ? 'user' : 'agent',
        tool: typeof payload.tool === 'string' ? payload.tool : undefined,
        ask: activity.kind === 'elicitation' ? askOf(payload) : undefined,
        answersAsk: activity.kind === 'prompt' && typeof payload.ask_id === 'string'
          ? payload.ask_id : null,
      });
    }
  }
  return events.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
}

/* ------------------------------------------------------------------ runners (WP07, slice 5) */

/* `Runner` and `AgentLease` in agent_runners.py. The selection is the whole of what a screen may
 * show: the credential columns (`lease_token_hash`, `token_hint`) are not in the SDL, so they
 * cannot be asked for by accident. */
const RUNNER_FIELDS = `id name organizationId status enabled revokedAt labels harnesses workRoot os runnerVersion
  lastHeartbeatAt createdAt
  currentLease { id taskId state harness fence leaseUntil grantedAt finishState finishedAt
                 issue { identifier } }`;
const LEASE_FIELDS = `id taskId state harness fence leaseUntil grantedAt finishState finishedAt
  issue { identifier }`;
/* HOJA-635: the enrollment's lifecycle columns only. `token_hash` and `hint` are not in the
 * server's SDL at all, so there is nothing here to forget to exclude. */
const ENROLLMENT_FIELDS = `id label status usable expiresAt usedAt usedByRunner createdAt`;

type ServerEnrollment = {
  id: string; label: string | null; status: 'pending' | 'spent' | 'expired'; usable: boolean;
  expiresAt: string | null; usedAt: string | null; usedByRunner: string | null; createdAt: string;
};

function fromEnrollment(row: ServerEnrollment): RunnerEnrollment {
  return {
    id: row.id,
    label: row.label,
    status: row.status,
    usable: row.usable,
    expiresAt: row.expiresAt,
    usedAt: row.usedAt,
    usedByRunner: row.usedByRunner,
    createdAt: row.createdAt,
  };
}

type ServerLease = {
  id: string; taskId: string; state: string; harness: string; fence: number;
  leaseUntil: string | null; grantedAt: string | null; finishState: string | null;
  finishedAt: string | null; issue: { identifier: string } | null;
};

type ServerRunner = {
  id: string; name: string; organizationId: string; status: string; enabled: boolean;
  revokedAt: string | null; labels: string[]; harnesses: string[];
  workRoot?: string | null; os?: string | null; runnerVersion?: string | null;
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

const ENDPOINT_FIELDS = `id exposureId runnerId runnerName leaseId sessionId agentId port host
  pinnedPeer state closeReason openedAt expiresAt closedAt revokeRequestedAt`;

type ServerEndpoint = ConnectEndpointRow;

/** One server row → the screen's per-port half. Straight through, because the server already
 * speaks the vocabulary the type names (see `EXPOSURE_SERVER_FIELDS` in types.ts). */
function fromEndpoint(row: ServerEndpoint): ConnectEndpointRow {
  return {...row};
}

/** Group one serve process's ports into the exposure a person revokes.
 *
 * The grouping key is `exposureId`, which is the runner's own identifier for the process: its rows
 * were opened together, carry one pin and one TTL, and close together. `live` is open *and* inside
 * that TTL, the same rule the server's `live` filter uses — a row past its deadline describes a
 * process its own timer has already killed.
 */
export function groupExposures(rows: ConnectEndpointRow[], now = Date.now()): ConnectExposure[] {
  const byExposure = new Map<string, ConnectEndpointRow[]>();
  for (const row of rows) {
    const list = byExposure.get(row.exposureId);
    if (list) list.push(row); else byExposure.set(row.exposureId, [row]);
  }
  return [...byExposure.entries()].map(([exposureId, group]) => {
    const first = group[0];
    const expiresAt = first.expiresAt ? Math.floor(Date.parse(first.expiresAt) / 1000) : 0;
    return {
      id: exposureId,
      ports: group.map(row => row.port).sort((a, b) => a - b),
      pinnedPeer: first.pinnedPeer,
      expiresAt,
      live: first.state === 'open' && expiresAt * 1000 > now,
      runnerName: first.runnerName,
      state: first.state,
      closeReason: first.closeReason,
      agentId: first.agentId ?? null,
      host: first.host ?? null,
    };
  }).sort((a, b) => b.expiresAt - a.expiresAt);
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
    workRoot: row.workRoot ?? null,
    os: row.os ?? null,
    runnerVersion: row.runnerVersion ?? null,
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
function createGraphqlAdapter(fallback: AgentsAdapter): AgentsAdapter {
  return {
    ...fallback,

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
      const [runs, policyRow] = await Promise.all([
        request<{workspaceAgentSessions: {nodes: ServerSession[]}}>(
          `query AgentRuns($agentId: String!) {
             workspaceAgentSessions(agentId: $agentId, first: 50) {
               nodes { ${SESSION_FIELDS} activities(first: 50) { ${ACTIVITY_FIELDS} } } } }`,
          {agentId: agent.userId}),
        /* HOJA-602-A-22: the harness half is WP12's row, not the member row, and the names it may
         * hold come from the server (`agentHarnesses`) so the field cannot offer one it refuses.
         * Null policy = deny by default, which reads here as "nobody chose" - the queue then
         * falls back to `pi`, and the server says so. */
        readPolicyRow(id),
      ]);
      const rows = runs.workspaceAgentSessions?.nodes || [];
      return {
        agent,
        policy: policyOf(agent),
        harnesses: policyRow.harnesses,
        connectGrants: policyRow.connect,
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

    async updateHarnessPolicy(id: string, patch: HarnessPatch, actor: string) {
      const input: Record<string, unknown> = {};
      if ('defaultHarness' in patch) input.defaultHarness = patch.defaultHarness || null;
      if ('harnessAllowlist' in patch) input.harnessAllowlist = patch.harnessAllowlist || [];
      if (!Object.keys(input).length) {
        const current = await this.getAgent(id);
        if (!current) throw new AgentApiError('NOT_FOUND', 'That agent no longer exists.');
        return {harnesses: current.harnesses, audit: null};
      }
      const data = await request<{agentPolicyUpdate: {success: boolean; agentPolicy: ServerHarnessPolicy | null}}>(
        `mutation AgentHarnessUpdate($id: String!, $input: AgentPolicyInput!) {
           agentPolicyUpdate(agentMemberId: $id, input: $input) {
             success agentPolicy { ${HARNESS_FIELDS} } } }`, {id, input});
      const row = data.agentPolicyUpdate?.agentPolicy;
      if (!data.agentPolicyUpdate?.success || !row) {
        throw new AgentApiError('ERROR', 'The harness policy was not saved.');
      }
      /* The audit row the server wrote is `agent.policy.change`; `auditEvents` (WP05) is where it
       * is read back, so this returns `null` rather than inventing a row the screen could show. */
      void actor;
      const current = await this.getAgent(id);
      return {
        harnesses: {defaultHarness: row.defaultHarness ?? null,
          harnessAllowlist: row.harnessAllowlist || [],
          known: current?.harnesses.known || []},
        audit: null,
      };
    },

    async updateConnectPolicy(id: string, grants: ConnectGrant[], actor: string) {
      /* `connect` is JSON on `AgentPolicyInput`, so the list travels as it will be stored. The
       * server's `clean_connect` is the authority on every entry (a named host, an integer port in
       * 1..65535, duplicates collapsed); its refusal is a `BAD_USER_INPUT` GraphQL error, which
       * `request` rethrows with the server's own sentence. The screen shows that sentence and
       * re-reads the policy row, so a refused list never sits in the field looking stored. */
      const data = await request<{agentPolicyUpdate: {success: boolean;
        agentPolicy: {connect: ServerConnect} | null}}>(
        `mutation AgentConnectUpdate($id: String!, $input: AgentPolicyInput!) {
           agentPolicyUpdate(agentMemberId: $id, input: $input) {
             success agentPolicy { connect } } }`,
        {id, input: {connect: grants.map(grant => ({host: grant.host, port: grant.port}))}});
      const row = data.agentPolicyUpdate?.agentPolicy;
      if (!data.agentPolicyUpdate?.success || !row) {
        throw new AgentApiError('ERROR', 'The Connect grants were not saved.');
      }
      /* The resolver writes `agent.policy.change` for this mutation too, and `auditEvents` (WP05)
       * is where it is read back: this returns `null` rather than a row it cannot see. */
      void actor;
      return {connect: readGrants(row.connect), audit: null};
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

    async answerSession(sessionId: string, body: string, response?: AgentAnswer) {
      const data = await request<{agentSessionPrompt: {success: boolean}}>(
        `mutation Answer($id: String!, $body: String!, $response: JSON) {
           agentSessionPrompt(id: $id, body: $body, response: $response) { success } }`,
        {id: sessionId, body, response: response ?? null});
      if (!data.agentSessionPrompt?.success) {
        throw new AgentApiError('ERROR', 'The answer was not recorded.');
      }
    },

    async getProjectRepository(projectId: string) {
      const data = await request<{project: {repository: string | null} | null}>(
        `query ProjectRepo($id: String!) { project(id: $id) { repository } }`, {id: projectId});
      return data.project?.repository ?? null;
    },

    async setProjectRepository(projectId: string, repository: string | null) {
      const data = await request<{projectRepositorySet: {repository: string | null} | null}>(
        `mutation ProjectRepoSet($id: String!, $repo: String) {
           projectRepositorySet(projectId: $id, repository: $repo) { repository } }`,
        {id: projectId, repo: repository});
      return data.projectRepositorySet?.repository ?? null;
    },

    async enableAgents() {
      const data = await request<{workspaceFeaturesUpdate: {agentsEnabled: boolean}}>(
        `mutation { workspaceFeaturesUpdate(agentsEnabled: true) { agentsEnabled } }`);
      if (!data.workspaceFeaturesUpdate?.agentsEnabled) {
        throw new AgentApiError('ERROR', 'Agents were not turned on.');
      }
    },

    async getRunAccess(agentId: string) {
      const data = await request<{
        agentPolicy: {teams: string[]; projects: string[]; secrets: string[];
          repos: {repo: string; branches: string[]}[] | null} | null;
        agentSecretNames: string[];
        teams: {nodes: {id: string; key: string; name: string}[]};
      }>(
        `query RunAccess($id: String!) {
           agentPolicy(agentMemberId: $id) { teams projects secrets repos }
           agentSecretNames
           teams { nodes { id key name } } }`,
        {id: agentId});
      return {
        teams: data.agentPolicy?.teams ?? [],
        projects: data.agentPolicy?.projects ?? [],
        secrets: data.agentPolicy?.secrets ?? [],
        repos: Array.isArray(data.agentPolicy?.repos) ? data.agentPolicy!.repos! : [],
        workspaceSecrets: data.agentSecretNames ?? [],
        allTeams: data.teams?.nodes ?? [],
      };
    },

    async updateRunAccess(agentId: string, patch: {teams?: string[]; secrets?: string[];
      repos?: {repo: string; branches: string[]}[]}) {
      const input: Record<string, unknown> = {};
      if (patch.repos) input.repos = patch.repos;
      if (patch.teams) input.teams = patch.teams;
      if (patch.secrets) input.secrets = patch.secrets;
      const data = await request<{agentPolicyUpdate: {success: boolean}}>(
        `mutation RunAccess($id: String!, $input: AgentPolicyInput!) {
           agentPolicyUpdate(agentMemberId: $id, input: $input) { success } }`,
        {id: agentId, input});
      if (!data.agentPolicyUpdate?.success) throw new AgentApiError('ERROR', 'The policy was not saved.');
    },

    async setSecret(name: string, value: string) {
      const data = await request<{agentSecretSet: boolean}>(
        `mutation Secret($name: String!, $value: String!) { agentSecretSet(name: $name, value: $value) }`,
        {name, value});
      if (!data.agentSecretSet) throw new AgentApiError('ERROR', 'The key was not stored.');
    },

    async createSetupTask(agentUserId: string, teamId: string) {
      const description = [
        'A setup check from the agent page: reply with one sentence saying hello and naming the',
        'model you run on. Do not change any files.',
        '',
        '## Acceptance',
        '- the agent replies with a greeting',
      ].join('\n');
      const data = await request<{issueCreate: {issue: {id: string; identifier: string}}}>(
        `mutation Setup($input: IssueCreateInput!) { issueCreate(input: $input) { issue { id identifier } } }`,
        {input: {teamId, title: 'Setup check: say hello', delegateId: agentUserId, description}});
      const issue = data.issueCreate.issue;
      return {issueId: issue.id, identifier: issue.identifier, stateName: null,
        sessionState: null, verdict: null};
    },

    async getSetupProbe(issueId: string) {
      const data = await request<{issue: {id: string; identifier: string; state: {name: string} | null;
        agentSession: {state: string; verdict: string | null} | null} | null}>(
        `query Probe($id: String!) { issue(id: $id) { id identifier state { name }
           agentSession { state verdict } } }`,
        {id: issueId});
      const issue = data.issue;
      if (!issue) throw new AgentApiError('NOT_FOUND', 'The setup task is gone.');
      return {issueId: issue.id, identifier: issue.identifier, stateName: issue.state?.name ?? null,
        sessionState: issue.agentSession?.state ?? null, verdict: issue.agentSession?.verdict ?? null};
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
      /* The exposures under the Connect heading are this machine's own rows: `connect_endpoint`
       * keys them by `runner_id` (revision 71), so the detail page no longer has to explain whose
       * they are. */
      const exposures = await this.listExposures(id);
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

    /* -- HOJA-635: minting and taking back credentials ---------------------------------- */

    async listRunnerEnrollments() {
      const data = await request<{runnerEnrollments: ServerEnrollment[]}>(
        `query Enrollments { runnerEnrollments { ${ENROLLMENT_FIELDS} } }`);
      return (data.runnerEnrollments || []).map(fromEnrollment);
    },

    async createRunnerEnrollment(name: string, ttlMinutes?: number, workRoot?: string | null) {
      /* The plaintext arrives here and nowhere else. It is returned to the caller, which keeps it
       * in state until the panel closes; nothing in this module stores, logs or caches it. */
      const data = await request<{
        runnerEnrollmentCreate: {
          enrollment: ServerEnrollment; token: string; command: string; heartbeatIntervalS: number;
          installCommand: string | null; connectCommand: string | null; startCommand: string; downloadUrl: string | null;
        };
      }>(`mutation Enroll($name: String!, $ttlMinutes: Int, $workRoot: String) {
            runnerEnrollmentCreate(name: $name, ttlMinutes: $ttlMinutes, workRoot: $workRoot) {
              enrollment { ${ENROLLMENT_FIELDS} }
              token command installCommand connectCommand startCommand downloadUrl heartbeatIntervalS } }`,
         {name, ttlMinutes: ttlMinutes ?? null, workRoot: workRoot || null});
      const created = data.runnerEnrollmentCreate;
      return {
        enrollment: fromEnrollment(created.enrollment),
        token: created.token,
        command: created.command,
        /* HOJA-712: the install and start halves of the same hand-off. `installCommand` is null
         * on a deployment that publishes no wheel, and the panel says so rather than linking a
         * 404. */
        installCommand: created.installCommand ?? null,
        connectCommand: created.connectCommand ?? null,
        startCommand: created.startCommand,
        downloadUrl: created.downloadUrl ?? null,
        heartbeatIntervalS: created.heartbeatIntervalS,
      };
    },

    async revokeRunnerEnrollment(id: string) {
      const data = await request<{runnerEnrollmentRevoke: boolean}>(
        `mutation Unenroll($id: String!) { runnerEnrollmentRevoke(id: $id) }`, {id});
      return Boolean(data.runnerEnrollmentRevoke);
    },

    async revokeRunner(id: string) {
      const data = await request<{runnerRevoke: boolean}>(
        `mutation RevokeRunner($id: String!) { runnerRevoke(id: $id) }`, {id});
      return Boolean(data.runnerRevoke);
    },

    /* -- slice 6: Connect (WP09) -------------------------------------------------------- */

    async listExposures(runnerId?: string) {
      /* `live: true` narrows to open rows in SQL and to rows inside their own TTL here, so an
       * exposure the supervisor's timer has already killed is never drawn as a tunnel. */
      const data = await request<{connectEndpoints: {nodes: ServerEndpoint[]}}>(
        `query Endpoints($runnerId: String) {
           connectEndpoints(first: 200, runnerId: $runnerId, live: true) {
             nodes { ${ENDPOINT_FIELDS} } } }`,
        {runnerId: runnerId ?? null});
      return groupExposures((data.connectEndpoints?.nodes || []).map(fromEndpoint));
    },

    async connectEnabled() {
      /* The kill switch is the workspace's own setting (`workspace_security_policy.connect_enabled`,
       * read through the admin-gated security policy), so a disabled org is a state a person set —
       * not this file's guess and not an empty list. */
      const data = await request<{workspaceSecurityPolicy: {connectEnabled: boolean}}>(
        `query ConnectSwitch { workspaceSecurityPolicy { connectEnabled } }`);
      return data.workspaceSecurityPolicy?.connectEnabled !== false;
    },

    async revokeExposure(exposureId: string) {
      /* `actor` is deliberately unused: the server records the actor from the authenticated
       * request (audit.record), and inventing one here would claim a trail this file cannot see. */
      const data = await request<{connectEndpointClose: {success: boolean; closed: number; endpoints: ServerEndpoint[]}}>(
        `mutation Revoke($exposureId: String!) {
           connectEndpointClose(exposureId: $exposureId) {
             success closed endpoints { ${ENDPOINT_FIELDS} } } }`,
        {exposureId});
      if (!data.connectEndpointClose?.success) throw new AgentApiError('ERROR', 'The exposure was not revoked.');
      const rows = (data.connectEndpointClose.endpoints || []).map(fromEndpoint);
      const [exposure] = groupExposures(rows);
      if (!exposure) throw new AgentApiError('NOT_FOUND', 'That exposure is not on this workspace.');
      return {exposure: {...exposure, live: false}, audit: null};
    },
  };
}

async function agentUserFor(memberId: string): Promise<string | null> {
  const data = await request<{agentMember: {userId: string} | null}>(
    `query AgentUser($id: String!) { agentMember(id: $id) { userId } }`, {id: memberId});
  return data.agentMember?.userId ?? null;
}

/* ------------------------------------------------------------------ harness policy (A-22) */

const HARNESS_FIELDS = `defaultHarness harnessAllowlist`;

type ServerHarnessPolicy = {defaultHarness: string | null; harnessAllowlist: string[]};

/* `AgentPolicy.connect` is the SDL's `JSON`: a list of `{host, port}`, or null when the policy row
 * was never written. Anything else would be a server bug, and it reads here as "no grant" rather
 * than as a row the screen draws a control for. */
type ServerConnect = {host?: unknown; port?: unknown}[] | null;

function readGrants(value: ServerConnect | undefined): ConnectGrant[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(entry => ({host: String(entry?.host ?? ''), port: Number(entry?.port)}))
    .filter(grant => grant.host !== '' && Number.isInteger(grant.port));
}

/** The two halves of the policy row this page reads, in one request: the harness the queue will
 *  use (with the profile names the server knows) and the Connect grants it may expose.
 *
 * A null policy (the member has none) answers `{defaultHarness: null, harnessAllowlist: []}` -
 * "nobody chose" - which is the same thing the queue reads, rather than an empty form that implies
 * a policy exists. The same read answers no Connect grant, which is deny-by-default too. */
async function readPolicyRow(memberId: string): Promise<{harnesses: HarnessPolicy; connect: ConnectGrant[]}> {
  const data = await request<{
    agentPolicy: (ServerHarnessPolicy & {connect: ServerConnect}) | null; agentHarnesses: string[];
  }>(`query AgentHarnessPolicy($id: String!) {
        agentPolicy(agentMemberId: $id) { ${HARNESS_FIELDS} connect }
        agentHarnesses }`, {id: memberId});
  const row = data.agentPolicy;
  return {
    harnesses: {
      defaultHarness: row?.defaultHarness ?? null,
      harnessAllowlist: row?.harnessAllowlist || [],
      known: data.agentHarnesses || [],
    },
    connect: readGrants(row?.connect),
  };
}

/* ------------------------------------------------------------------ the fallback half */

/** Every read on this interface has a server behind it, so the fallback is loud on purpose.
 *
 * Slices 1–5 used this half to serve fixtures for the surfaces that had no resolver; slice 6 was
 * the last one (Connect), and a fixture that no longer exists cannot be labelled honestly. What is
 * left is the seam: `setAgentsAdapter` still exists for tests, `sampleAgentsAdapter` still returns
 * this object (the name is kept so a test that swaps the seam keeps compiling), and every method
 * refuses instead of pretending. */
function createFallbackAdapter(): AgentsAdapter {
  const serverOnly = (what: string) => {
    throw new Error(`No backend for ${what} on this server — every agent surface reads GraphQL now.`);
  };

  return {
    async listAgents() { return serverOnly('the agent list'); },
    async getAgent() { return serverOnly('an agent'); },
    async createAgent() { return serverOnly('agent create'); },
    async listAgentChoices() { return serverOnly('the delegate picker'); },
    async updatePolicy() { return serverOnly('agent edits'); },
    async updateHarnessPolicy() { return serverOnly('an agent\'s harness policy'); },
    async updateConnectPolicy() { return serverOnly('an agent\'s Connect grants'); },

    async listSessions() { return serverOnly('the run queue'); },
    async listAuditEvents() { return serverOnly('the audit log'); },
    async stopSession() { return serverOnly('stopping a session'); },
    async answerSession() { return serverOnly('answering a session'); },
    async getRunAccess() { return serverOnly('the run policy'); },
    async enableAgents() { return serverOnly('turning Agents on'); },
    async getProjectRepository() { return serverOnly('a project repository'); },
    async setProjectRepository() { return serverOnly('a project repository'); },
    async updateRunAccess() { return serverOnly('saving the run policy'); },
    async setSecret() { return serverOnly('storing a key'); },
    async createSetupTask() { return serverOnly('a setup task'); },
    async getSetupProbe() { return serverOnly('a setup task'); },

    async listRunners() { return serverOnly('the runner list'); },
    async getRunner() { return serverOnly('a runner'); },
    async listLeases() { return serverOnly('a runner\'s leases'); },
    async listClaims() { return serverOnly('the claims waiting on a verifier'); },
    async listVerdicts() { return serverOnly('a claim\'s verdicts'); },

    /* HOJA-635 is a server surface end to end — a sample token would be a token that enrolls
     * nothing, and drawing one would be worse than the loud failure below. */
    async listRunnerEnrollments() { return serverOnly('enrollment tokens'); },
    async createRunnerEnrollment() { return serverOnly('minting an enrollment token'); },
    async revokeRunnerEnrollment() { return serverOnly('revoking an enrollment token'); },
    async revokeRunner() { return serverOnly('revoking a runner'); },

    async listExposures() { return serverOnly('Connect exposures'); },
    async connectEnabled() { return serverOnly('the Connect switch'); },
    async revokeExposure() { return serverOnly('revoking a Connect exposure'); },
  };
}

let adapter: AgentsAdapter = createGraphqlAdapter(createFallbackAdapter());

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
  return createFallbackAdapter();
}
