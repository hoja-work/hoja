/* The single data seam for the agent surface (HOJA-602-A-16).
 *
 * Every screen reads through `getAgentsAdapter()`. Since WP04 landed on main (1a296bc:
 * `agent_member`, `AgentMember`, `Issue.delegate`) the identity half of this surface is real: the
 * agent list and detail, create and edit, and the issue page's Delegate field are GraphQL reads and
 * writes. What is still a fixture is listed once, in `SAMPLE_SURFACES`, and each of those screens
 * renders a visible `SampleDataNotice`, so no sample row can be read as a server row.
 *
 * The interface is the *screens'* vocabulary, not the wire vocabulary, so a resolver that names
 * things differently is a translation problem inside this file rather than an edit to six
 * components. Two behaviours the backend owns are modelled here rather than in the UI, because
 * doing them in the UI would hide a missing audit trail:
 *
 *   - a policy write reports the audit row the server produced, or `null` when the server does not
 *     record one (WP05 `auditEvents` is not on main yet). It never invents a row;
 *   - stopping a session is a state transition, and a session already done stays done.
 */

import {seedAudit, seedConnectEnabled, seedExposuresByRunner, seedRunnerRecords, seedSessions} from './seed';
import type {Agent, AgentDetail, AgentPolicy, AgentSession, AuditEvent, ConnectExposure, RawExposure, Runner, RunnerRecord} from './types';

export type PolicyPatch = Partial<AgentPolicy>;

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

  /* -- sample-backed: no server table on main yet --------------------------------------------
   * Session rows (WP06), audit rows (WP05) and runner/Connect rows (WP07/WP08). Marked here and on
   * screen; see SAMPLE_SURFACES below. */
  listSessions(sampleAgentKey?: string): Promise<AgentSession[]>;
  listAuditEvents(filter?: {actor?: string; action?: string}): Promise<AuditEvent[]>;
  /** Stops the running session for an agent; a finished session is returned unchanged. */
  stopSession(sessionId: string, actor: string): Promise<{session: AgentSession; audit: AuditEvent}>;

  listRunners(): Promise<Runner[]>;
  getRunner(id: string): Promise<{runner: Runner; exposures: ConnectExposure[]} | null>;
  /** Live exposures only: the supervisor revokes on TTL, so an expired row is never served. */
  listExposures(runnerId?: string): Promise<ConnectExposure[]>;
  /** The cloud-side kill switch, `ConnectSupervisor.is_enabled(org)`. */
  connectEnabled(): Promise<boolean>;
  /** Two-step on the screen; here it is the transition that also writes the audit row. */
  revokeExposure(exposureId: string, actor: string): Promise<{exposure: ConnectExposure; audit: AuditEvent}>;
};

/* One line to grep, and the single justification for every fixture row that can reach a screen. A
 * surface leaves this list when its resolver lands, not when its screen looks finished. */
export const SAMPLE_SURFACES = {
  sessions: {what: 'Session runs and their activity', pending: 'WP06 delegated sessions'},
  audit: {what: 'The audit log', pending: 'WP05 auditEvents (admin-only), not on main at 1a296bc'},
  runners: {what: 'Runners and Connect', pending: 'WP07/WP08 lease and tailcat storage'},
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
      /* Sessions and events are WP06; the server stores none, so the page gets none. It states
       * that instead of borrowing the run queue's fixture rows. */
      return {agent, policy: policyOf(agent), sessions: [], events: []};
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
  };
}

/* ------------------------------------------------------------------ the sample half */

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function nowIso(): string {
  return new Date().toISOString();
}

function auditId(): string {
  return `a-${Math.random().toString(36).slice(2, 10)}`;
}

/* The two projections. They exist for the same reason `Exposure.public_row()` does in
 * runner/hoja_runnerd/connect.py: the address is a bearer secret (connect.py:8-12) and the lease
 * token hash is a credential column (FR-018), so both are dropped by structural destructuring
 * rather than by remembering not to render them. */
export function publicExposure(row: RawExposure): ConnectExposure {
  const {address: _address, ...rest} = row;
  return rest;
}

export function publicRunner(record: RunnerRecord): Runner {
  const {leaseTokenHash: _hash, ...rest} = record;
  return rest;
}

/** The fixtures behind SAMPLE_SURFACES, and nothing else. Read-only for the agent screens: the
 * only writes here are the ones whose screen is itself a fixture. */
function createSampleAdapter(): AgentsAdapter {
  const sessions: AgentSession[] = clone(seedSessions);
  const audit: AuditEvent[] = clone(seedAudit);
  const runners: RunnerRecord[] = clone(seedRunnerRecords);
  const exposuresByRunner: Record<string, RawExposure[]> = clone(seedExposuresByRunner);
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
  const liveExposures = (runnerId: string): RawExposure[] => {
    const now = Date.now() / 1000;
    return (exposuresByRunner[runnerId] || []).filter(e => e.live && e.expiresAt > now);
  };

  const serverOnly = (what: string) => {
    throw new Error(`No backend for ${what} on main — see SAMPLE_SURFACES in agents/adapter.ts.`);
  };

  return {
    /* Server-backed methods are replaced by `createGraphqlAdapter`; these stubs exist so the
     * interface has one implementation and a missing override fails loudly instead of silently
     * serving a fixture to a real screen. */
    async listAgents() { return serverOnly('the agent list'); },
    async getAgent() { return serverOnly('an agent'); },
    async createAgent() { return serverOnly('agent create'); },
    async listAgentChoices() { return serverOnly('the delegate picker'); },
    async updatePolicy() { return serverOnly('agent edits'); },

    async listSessions(sampleAgentKey?: string) {
      return clone(sampleAgentKey
        ? sessions.filter(s => s.agentKey.toLowerCase() === sampleAgentKey.toLowerCase())
        : sessions);
    },
    async listAuditEvents(filter?: {actor?: string; action?: string}) {
      const actor = filter?.actor?.trim().toLowerCase();
      const action = filter?.action?.trim().toLowerCase();
      return clone(audit.filter(e =>
        (!actor || e.actor.toLowerCase().includes(actor)) &&
        (!action || e.action.toLowerCase().includes(action))));
    },
    async listRunners() {
      return clone(runners.map(publicRunner));
    },
    async getRunner(id: string) {
      const record = runners.find(r => r.id.toLowerCase() === id.toLowerCase());
      if (!record) return null;
      const live = liveExposures(record.id).map(publicExposure);
      return clone({runner: publicRunner(record), exposures: live});
    },
    async listExposures(runnerId?: string) {
      const ids = runnerId ? [runnerId] : Object.keys(exposuresByRunner);
      return clone(ids.flatMap(id => liveExposures(id).map(publicExposure)));
    },
    async connectEnabled() {
      return connectOn();
    },
    async revokeExposure(exposureId: string, actor: string) {
      if (!connectOn()) throw new Error('Connect is disabled for this workspace.');
      const owner = Object.keys(exposuresByRunner).find(id => exposuresByRunner[id].some(e => e.id === exposureId));
      const row = owner ? exposuresByRunner[owner].find(e => e.id === exposureId) : undefined;
      if (!owner || !row) throw new Error('That exposure is not live.');
      exposuresByRunner[owner] = exposuresByRunner[owner].filter(e => e.id !== exposureId);
      const entry: AuditEvent = {
        id: auditId(), at: nowIso(), actor, action: 'connect.revoked', target: row.id,
        detail: `ports ${row.ports.join(',')} · peer ${row.pinnedPeer.slice(0, 15)}… · process gone`,
      };
      audit.unshift(entry);
      return clone({exposure: publicExposure({...row, live: false}), audit: entry});
    },
    async stopSession(sessionId: string, actor: string) {
      const session = sessions.find(s => s.id === sessionId);
      if (!session) throw new Error('That session was not found.');
      if (session.status === 'done') {
        return clone({session, audit: {id: auditId(), at: nowIso(), actor, action: 'session.stop', target: session.issueIdentifier, detail: 'already done'}});
      }
      session.status = 'done';
      session.endedAt = nowIso();
      const row: AuditEvent = {
        id: auditId(), at: session.endedAt, actor, action: 'session.stop', target: session.issueIdentifier,
        detail: `stopped ${session.id} on ${session.agentKey}`,
      };
      audit.unshift(row);
      return clone({session, audit: row});
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
