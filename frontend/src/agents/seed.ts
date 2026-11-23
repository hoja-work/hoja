/* Fixture data for the agent surface (HOJA-602-A-16).
 *
 * As of WP13 slice 6 there is none. Every read on `AgentsAdapter` is served by
 * `createGraphqlAdapter` in adapter.ts:
 *
 *   - agents and their policies  — `agentMembers` / `agentMemberUpdate` (WP04, WP12)
 *   - sessions and the transcript — `workspaceAgentSessions` / `agentSessionStop` (WP06)
 *   - the audit ledger            — `auditEvents` (WP05)
 *   - runners and their leases    — `runners` / `runner` / `agentLeases` (WP07, agent_runners.py)
 *   - claims and verdicts         — `verifierClaims` / `agentVerifications` (WP10, agent_claims.py)
 *   - Connect exposures           — `connectEndpoints` / `connectEndpointClose` (WP09,
 *                                   connect_endpoints.py, revision 71)
 *
 * This file is kept, empty, so the deletion of the last fixture is a fact a reader can find rather
 * than a file that quietly vanished: a new fixture belongs here, and a screen that renders one
 * needs the `SampleDataNotice` back (agents/chrome.tsx) before it may be trusted.
 */
export {};
