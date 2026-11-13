/* Fixture data for the agent surface (HOJA-602-A-16).
 *
 * Values are fixed, not random: the geometry test screenshots the same rows on every run, so a
 * random budget or timestamp would make two screenshots of "the same" screen differ for no
 * reason. Everything here is replaced when WP04/WP06 swap the adapter for GraphQL.
 */

import type {Agent, AgentDetail, AgentEvent, AgentPolicy, AgentSession, AuditEvent, RawExposure, RunnerRecord} from './types';

const OWNER_A = {id: 'u-ada', name: 'Ada Lovelace'};
const OWNER_K = {id: 'u-kevin', name: 'Kevin Sanchez'};
const OWNER_L = {id: 'u-luis', name: 'Luis Rincon'};

export const seedAgents: Agent[] = [
  {id: 'ag-1', key: 'AGENT-1', name: 'Triage', harness: 'claude-code', model: 'claude-sonnet-4', status: 'working',
    running: 2, capacity: 3, budgetUsedUsd: 12.4, budgetLimitUsd: 40, runnerPool: 'linux-x64', owner: OWNER_A,
    statusChangedAt: '2026-11-12T21:12:00Z', updatedAt: '2026-11-12T21:40:00Z'},
  {id: 'ag-2', key: 'AGENT-2', name: 'Docs writer', harness: 'codex', model: 'gpt-5', status: 'idle',
    running: 0, capacity: 2, budgetUsedUsd: 3.1, budgetLimitUsd: 20, runnerPool: 'linux-x64', owner: OWNER_K,
    statusChangedAt: '2026-11-12T19:05:00Z', updatedAt: '2026-11-12T19:05:00Z'},
  {id: 'ag-3', key: 'AGENT-3', name: 'Review sentinel', harness: 'claude-code', model: 'claude-haiku-4.5', status: 'queued',
    running: 1, capacity: 1, budgetUsedUsd: 8.9, budgetLimitUsd: 25, runnerPool: 'linux-arm64', owner: OWNER_L,
    statusChangedAt: '2026-11-12T20:12:00Z', updatedAt: '2026-11-12T20:12:00Z'},
  {id: 'ag-4', key: 'AGENT-4', name: 'Migration runner', harness: 'custom', model: 'qwen3-coder', status: 'paused',
    running: 0, capacity: 2, budgetUsedUsd: 27.8, budgetLimitUsd: 30, runnerPool: 'linux-x64', owner: OWNER_A,
    statusChangedAt: '2026-11-11T09:30:00Z', updatedAt: '2026-11-11T09:30:00Z'},
  {id: 'ag-5', key: 'AGENT-5', name: 'Flake hunter', harness: 'codex', model: 'gpt-5-mini', status: 'idle',
    running: 0, capacity: 4, budgetUsedUsd: 1.2, budgetLimitUsd: 15, runnerPool: 'macos-arm64', owner: OWNER_L,
    statusChangedAt: '2026-11-12T16:48:00Z', updatedAt: '2026-11-12T16:48:00Z'},
  {id: 'ag-6', key: 'AGENT-6', name: 'Release scribe', harness: 'claude-code', model: 'claude-sonnet-4', status: 'working',
    running: 1, capacity: 2, budgetUsedUsd: 5.5, budgetLimitUsd: 20, runnerPool: 'linux-arm64', owner: OWNER_K,
    statusChangedAt: '2026-11-12T22:01:00Z', updatedAt: '2026-11-12T22:01:00Z'},
];

export const seedSessions: AgentSession[] = [
  {id: 's-1', agentId: 'ag-1', agentKey: 'AGENT-1', status: 'active', issueId: 'i-1', issueIdentifier: 'HOJA-612', issueTitle: 'Resolve the failing RLS test on a fresh Postgres', priority: 2, startedAt: '2026-11-12T21:20:00Z'},
  {id: 's-2', agentId: 'ag-1', agentKey: 'AGENT-1', status: 'active', issueId: 'i-2', issueIdentifier: 'HOJA-618', issueTitle: 'Split the writer into ordered migrations', priority: 3, startedAt: '2026-11-12T21:34:00Z'},
  {id: 's-3', agentId: 'ag-3', agentKey: 'AGENT-3', status: 'awaiting-input', reason: 'Needs a decision: which branch is the base?', issueId: 'i-3', issueIdentifier: 'HOJA-620', issueTitle: 'Gate the review lane on a green check', priority: 1},
  {id: 's-4', agentId: 'ag-2', agentKey: 'AGENT-2', status: 'queued', reason: 'Waiting for a linux-x64 slot (2 of 2 busy)', issueId: 'i-4', issueIdentifier: 'HOJA-621', issueTitle: 'Document the runner lease contract', priority: 4},
  {id: 's-5', agentId: 'ag-6', agentKey: 'AGENT-6', status: 'awaiting-verification', reason: 'Evidence attached; a human must confirm', issueId: 'i-5', issueIdentifier: 'HOJA-623', issueTitle: 'Write the release notes for the agent surface', priority: 2, startedAt: '2026-11-12T20:02:00Z'},
  {id: 's-6', agentId: 'ag-5', agentKey: 'AGENT-5', status: 'done', issueId: 'i-6', issueIdentifier: 'HOJA-611', issueTitle: 'Reproduce the flaky login test', priority: 3, startedAt: '2026-11-11T14:00:00Z', endedAt: '2026-11-11T15:12:00Z'},
];

export const seedEvents: AgentEvent[] = [
  {id: 'e-1', sessionId: 's-1', kind: 'thought', at: '2026-11-12T21:20:12Z', text: 'Read the failing test and the resolver it exercises before changing anything.'},
  {id: 'e-2', sessionId: 's-1', kind: 'action', at: '2026-11-12T21:20:41Z', text: 'Read app/tests/test_rls.py', tool: 'read_file'},
  {id: 'e-3', sessionId: 's-1', kind: 'action', at: '2026-11-12T21:22:03Z', text: 'Ran pytest -q app/tests/test_rls.py', tool: 'bash'},
  {id: 'e-4', sessionId: 's-1', kind: 'elicitation', at: '2026-11-12T21:24:10Z', text: 'A decision is needed: keep the fail-closed gate or skip it behind HOJA_TEST_RLS?'},
  {id: 'e-5', sessionId: 's-1', kind: 'response', at: '2026-11-12T21:26:00Z', text: 'Kept the gate; the test now seeds a tenant before asserting the refusal.'},
  {id: 'e-6', sessionId: 's-1', kind: 'error', at: '2026-11-12T21:27:30Z', text: 'Postgres refused the connection: migration 0042 is not applied on this host.'},
];

export const seedPolicy: AgentPolicy = {
  tools: ['read_file', 'write_file', 'bash', 'gh', 'git'],
  scope: 'repo',
  repos: ['hoja-work/hoja-cloud'],
  secrets: ['HOJA_TOKEN', 'POSTGRES_URL'],
  network: ['api.anthropic.com', 'github.com'],
  budgetUsd: 40,
  workingHours: 'Mon–Fri 08:00–19:00 UTC',
  approvers: ['Ada Lovelace', 'Kevin Sanchez'],
};

export const seedAudit: AuditEvent[] = [
  {id: 'a-1', at: '2026-11-12T22:03:00Z', actor: 'Ada Lovelace', action: 'policy.update', target: 'AGENT-1', detail: 'budget 30 → 40 USD'},
  {id: 'a-2', at: '2026-11-12T21:58:00Z', actor: 'AGENT-6', action: 'session.start', target: 'HOJA-623', detail: 'session s-5 on linux-arm64'},
  {id: 'a-3', at: '2026-11-12T21:40:00Z', actor: 'Kevin Sanchez', action: 'policy.update', target: 'AGENT-3', detail: 'tools + gh'},
  {id: 'a-4', at: '2026-11-12T21:12:00Z', actor: 'AGENT-1', action: 'session.stop', target: 'HOJA-610', detail: 'stopped by the owner'},
  {id: 'a-5', at: '2026-11-12T20:05:00Z', actor: 'Luis Rincon', action: 'runner.revoke', target: 'runner-3', detail: 'lease revoked, last seen 20:04'},
  {id: 'a-6', at: '2026-11-11T09:30:00Z', actor: 'Ada Lovelace', action: 'agent.pause', target: 'AGENT-4', detail: 'budget at 93%'},
];

export function seedDetail(key: string): AgentDetail | null {
  const agent = seedAgents.find(a => a.key.toLowerCase() === key.toLowerCase());
  if (!agent) return null;
  const sessions = seedSessions.filter(s => s.agentId === agent.id);
  const ids = new Set(sessions.map(s => s.id));
  return {
    agent,
    policy: {...seedPolicy, budgetUsd: agent.budgetLimitUsd},
    sessions,
    events: seedEvents.filter(e => ids.has(e.sessionId)),
  };
}

/* ---------------- Runners and Connect (slice 2) ----------------
 *
 * Anchored to the moment the module loads, because a live lease and a heartbeat are relative
 * facts: an exposure seeded with a fixed 2026 timestamp would read "0:00 left" and an online
 * runner would read "3w ago". The geometry probe measures boxes and computed styles, never this
 * text, so the relative anchor costs nothing.
 */
const SEED_NOW = Date.now();
const minutesAgo = (m: number) => new Date(SEED_NOW - m * 60000).toISOString();
const inSeconds = (s: number) => Math.floor(SEED_NOW / 1000) + s;

/* The registration records, `leaseTokenHash` included, so the adapter has a real secret to drop
 * when it projects to `Runner` (app/frontend/src/agents/adapter.ts, mirroring connect.py's
 * `public_row`). The hashes are shaped like the server's column and carry no ability to
 * authenticate anything: they are fixture values for a disposable in-memory store. */
export const seedRunnerRecords: RunnerRecord[] = [
  {id: 'RUNNER-1', name: 'mac-mini-01', workspaceId: 'ws-acme', status: 'online', labels: ['macos-arm64', 'office'],
    enrolled: true, lastSeenAt: minutesAgo(1), leaseTokenHash: 'sha256:9f2c41d0b7a3e5c6d8f10a2b4c6d8e0f1a3b5c7d9e0f2a4b6c8d0e2f4a6b8c0d'},
  {id: 'RUNNER-2', name: 'linux-x64-01', workspaceId: 'ws-acme', status: 'online', labels: ['linux-x64', 'ci'],
    enrolled: true, lastSeenAt: minutesAgo(0), leaseTokenHash: 'sha256:1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809'},
  {id: 'RUNNER-3', name: 'linux-arm64-01', workspaceId: 'ws-acme', status: 'offline', labels: ['linux-arm64'],
    enrolled: true, lastSeenAt: '2026-11-11T09:12:00Z', leaseTokenHash: 'sha256:deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef012345'},
  {id: 'RUNNER-4', name: 'laptop-adhoc', workspaceId: 'ws-acme', status: 'offline', labels: ['linux-x64'],
    enrolled: false, lastSeenAt: '2026-11-09T17:40:00Z', leaseTokenHash: ''},
];

/* Raw exposures, including the lease channel's bearer `address`. Those values are canaries, not
 * usable addresses: they are shaped to match `TAILCAT_ADDRESS_RE` in runner/hoja_runnerd/connect.py
 * so the geometry script can assert their byte-for-byte absence from the rendered page. Nothing
 * here authenticates anything.
 *
 * `ConnectSupervisor` owns the tailcat children of *one runner* (connect.py:495-503), so the
 * fixture is keyed the same way. Ownership is not a `public_row` field and is deliberately not on
 * the row: it is a property of which supervisor holds it.
 */
export const seedExposuresByRunner: Record<string, RawExposure[]> = {
  'RUNNER-1': [
    {id: 'exp-1', workspaceId: 'ws-acme', ports: [8080], pinnedPeer: 'nodekey:8f2c0a1d4e6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b4c6d8e0f2a4b6c8d',
      expiresAt: inSeconds(42 * 60), live: true, address: 'tcQLd9m3zXk2Vd8sTn6Rf4Yb1Wc7Hg5Jp0Lr3Qe8Ut2Mz6Nv'},
  ],
  'RUNNER-2': [
    {id: 'exp-2', workspaceId: 'ws-acme', ports: [3000, 9229], pinnedPeer: 'nodekey:1b3d5f7a9c1e3f5a7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e',
      expiresAt: inSeconds(7 * 60), live: true, address: 'tcRt6yHn8Kj4Lm2Pw9Zx3Cv5Bn7Mq1Ws4Ed0Fg6Hj8Kl2Po5'},
  ],
  'RUNNER-3': [],
  'RUNNER-4': [],
};

/** `ConnectSupervisor.set_org_enabled` / `is_enabled`: an org admin can turn Connect off, and a
 * disabled org is not an empty list. */
export const seedConnectEnabled = true;
