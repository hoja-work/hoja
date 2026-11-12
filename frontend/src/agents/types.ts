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
