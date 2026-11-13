/* The single data seam for the agent surface (HOJA-602-A-16).
 *
 * Every screen reads through `getAgentsAdapter()`. Nothing outside this file knows that the
 * data is a fixture. WP04 (agent identity, delegate actor) and WP06 (delegation, schedule,
 * session) replace the implementation with GraphQL calls against the fields they add:
 *
 *   setAgentsAdapter(graphqlAgentsAdapter);   // once, at module load in main.tsx
 *
 * The interface is deliberately the *screens'* vocabulary, not the wire vocabulary, so a
 * resolver that names things differently is a translation problem inside one file rather than
 * an edit to five components. Two behaviours the backend owns are modelled here rather than in
 * the UI, because doing them in the UI would hide a missing audit trail:
 *
 *   - every policy write returns the audit row it produced (WP05 reads the same table);
 *   - stopping a session is a state transition, and a session already done stays done.
 */

import {seedAgents, seedAudit, seedConnectEnabled, seedDetail, seedExposuresByRunner, seedRunnerRecords, seedSessions} from './seed';
import type {Agent, AgentDetail, AgentPolicy, AgentSession, AuditEvent, ConnectExposure, RawExposure, Runner, RunnerRecord} from './types';

export type PolicyPatch = Partial<AgentPolicy>;

export type AgentsAdapter = {
  listAgents(): Promise<Agent[]>;
  getAgent(key: string): Promise<AgentDetail | null>;
  listSessions(agentKey?: string): Promise<AgentSession[]>;
  listAuditEvents(filter?: {actor?: string; action?: string}): Promise<AuditEvent[]>;
  /** Writes the policy and appends the audit row for the diff; returns both. */
  updatePolicy(key: string, patch: PolicyPatch, actor: string): Promise<{policy: AgentPolicy; audit: AuditEvent}>;
  /** Stops the running session for an agent; a finished session is returned unchanged. */
  stopSession(sessionId: string, actor: string): Promise<{session: AgentSession; audit: AuditEvent}>;

  /* -- runners and Connect (slice 2) -------------------------------------------------------
   * Same rule as above: the screens' vocabulary, and the two things the server owns (the
   * redaction and the kill switch) are modelled here rather than in the UI, because doing them
   * in the UI would make a leaked address or a disabled org look like a rendering choice.
   */
  listRunners(): Promise<Runner[]>;
  getRunner(id: string): Promise<{runner: Runner; exposures: ConnectExposure[]} | null>;
  /** Live exposures only: the supervisor revokes on TTL, so an expired row is never served. */
  listExposures(runnerId?: string): Promise<ConnectExposure[]>;
  /** The cloud-side kill switch, `ConnectSupervisor.is_enabled(org)`. */
  connectEnabled(): Promise<boolean>;
  /** Two-step on the screen; here it is the transition that also writes the audit row. */
  revokeExposure(exposureId: string, actor: string): Promise<{exposure: ConnectExposure; audit: AuditEvent}>;
};

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
 * rather than by remembering not to render them. A field added to `RawExposure` or `RunnerRecord`
 * therefore reaches the screens only if somebody maps it here on purpose.
 */
export function publicExposure(row: RawExposure): ConnectExposure {
  const {address: _address, ...rest} = row;
  return rest;
}

export function publicRunner(record: RunnerRecord): Runner {
  const {leaseTokenHash: _hash, ...rest} = record;
  return rest;
}

/* The mock is a small in-memory store: reads clone so a component can never mutate the fixture
 * by accident, and a policy write is visible to the next read, which is what lets the detail
 * screen and the audit screen be checked against each other by a test. */
function createMockAdapter(): AgentsAdapter {
  const agents: Agent[] = clone(seedAgents);
  const sessions: AgentSession[] = clone(seedSessions);
  const audit: AuditEvent[] = clone(seedAudit);
  const policies = new Map<string, AgentPolicy>();
  const runners: RunnerRecord[] = clone(seedRunnerRecords);
  const exposuresByRunner: Record<string, RawExposure[]> = clone(seedExposuresByRunner);
  /* The kill switch, mock-only. `ConnectSupervisor.set_org_enabled` is an admin action owned by
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
  for (const a of agents) {
    const detail = seedDetail(a.key);
    if (detail) policies.set(a.key, clone(detail.policy));
  }

  const describePolicyDiff = (before: AgentPolicy, after: AgentPolicy): string => {
    const parts: string[] = [];
    (Object.keys(after) as (keyof AgentPolicy)[]).forEach(k => {
      const a = JSON.stringify(before[k]);
      const b = JSON.stringify(after[k]);
      if (a !== b) parts.push(`${k} ${a} → ${b}`);
    });
    return parts.join(', ') || 'no change';
  };

  /* `_expire` revokes at TTL (connect.py:683-689); serving an expired row would claim a tunnel
   * that the supervisor has already killed. */
  const liveExposures = (runnerId: string): RawExposure[] => {
    const now = Date.now() / 1000;
    return (exposuresByRunner[runnerId] || []).filter(e => e.live && e.expiresAt > now);
  };

  return {
    async listAgents() {
      return clone(agents);
    },
    async getAgent(key: string) {
      const agent = agents.find(a => a.key.toLowerCase() === key.toLowerCase());
      const policy = policies.get(agent?.key || '');
      if (!agent || !policy) return null;
      const mine = sessions.filter(s => s.agentId === agent.id);
      const detail = seedDetail(agent.key);
      return clone({
        agent,
        policy,
        sessions: mine,
        events: (detail?.events || []).filter(e => mine.some(s => s.id === e.sessionId)),
      });
    },
    async listSessions(agentKey?: string) {
      const agent = agentKey ? agents.find(a => a.key.toLowerCase() === agentKey.toLowerCase()) : undefined;
      return clone(agent ? sessions.filter(s => s.agentId === agent.id) : sessions);
    },
    async listAuditEvents(filter?: {actor?: string; action?: string}) {
      const actor = filter?.actor?.trim().toLowerCase();
      const action = filter?.action?.trim().toLowerCase();
      return clone(audit.filter(e =>
        (!actor || e.actor.toLowerCase().includes(actor)) &&
        (!action || e.action.toLowerCase().includes(action))));
    },
    async updatePolicy(key: string, patch: PolicyPatch, actor: string) {
      const agent = agents.find(a => a.key.toLowerCase() === key.toLowerCase());
      const before = policies.get(agent?.key || '');
      if (!agent || !before) throw new Error(`Agent ${key} was not found.`);
      const after: AgentPolicy = {...before, ...patch};
      policies.set(agent.key, after);
      if (typeof patch.budgetUsd === 'number') agent.budgetLimitUsd = patch.budgetUsd;
      agent.updatedAt = nowIso();
      const row: AuditEvent = {
        id: auditId(), at: nowIso(), actor, action: 'policy.update', target: agent.key,
        detail: describePolicyDiff(before, after),
      };
      audit.unshift(row);
      return clone({policy: after, audit: row});
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
      const agent = agents.find(a => a.id === session.agentId);
      if (agent) {
        agent.status = agent.running > 1 ? 'working' : 'idle';
        agent.running = Math.max(0, agent.running - 1);
        agent.updatedAt = session.endedAt;
      }
      const row: AuditEvent = {
        id: auditId(), at: session.endedAt, actor, action: 'session.stop', target: session.issueIdentifier,
        detail: `stopped ${session.id} on ${agent?.runnerPool || 'unknown pool'}`,
      };
      audit.unshift(row);
      return clone({session, audit: row});
    },
  };
}

let adapter: AgentsAdapter = createMockAdapter();

/** WP04/WP06 call this once with the GraphQL implementation; nothing else changes. */
export function setAgentsAdapter(next: AgentsAdapter): void {
  adapter = next;
}

export function getAgentsAdapter(): AgentsAdapter {
  return adapter;
}

/** The fixture implementation, exported so a test can assert the swap seam exists. */
export function mockAgentsAdapter(): AgentsAdapter {
  return createMockAdapter();
}
