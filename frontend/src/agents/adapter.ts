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

import {seedAgents, seedAudit, seedDetail, seedSessions} from './seed';
import type {Agent, AgentDetail, AgentPolicy, AgentSession, AuditEvent} from './types';

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
};

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function nowIso(): string {
  return new Date().toISOString();
}

function auditId(): string {
  return `a-${Math.random().toString(36).slice(2, 10)}`;
}

/* The mock is a small in-memory store: reads clone so a component can never mutate the fixture
 * by accident, and a policy write is visible to the next read, which is what lets the detail
 * screen and the audit screen be checked against each other by a test. */
function createMockAdapter(): AgentsAdapter {
  const agents: Agent[] = clone(seedAgents);
  const sessions: AgentSession[] = clone(seedSessions);
  const audit: AuditEvent[] = clone(seedAudit);
  const policies = new Map<string, AgentPolicy>();
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
