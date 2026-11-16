/* Fixture data for the one surface whose backend is not on main (HOJA-602-A-16).
 *
 * Scope, as of WP13 slice 5: agents, the issue page's delegate field, session runs and their
 * transcript, the audit ledger, the **runners list** (`runners` / `agentLeases` in agent_runners.py)
 * and the **claims and verdicts** (`verifierClaims` / `agentVerifications` in agent_claims.py) are
 * all read from GraphQL, so nothing for them lives here any more — no session rows, no audit rows,
 * no runner rows, no claim rows.
 *
 * What is still a fixture is exactly what has no server read on this branch:
 *
 *   - **Connect** — the tailcat exposure store and the org kill switch (WP08). No table exists:
 *     `audit.ACTIONS` names `connect.exposure.open`/`connect.exposure.close` and no call site writes
 *     them, because no supervisor is wired to the server yet.
 *
 * Every screen that renders one of these carries a visible `SampleDataNotice` (agents/chrome.tsx),
 * and `SAMPLE_SURFACES` in adapter.ts names them in one place, so a fixture row can never be
 * mistaken for a server row. Values are fixed, not random: the geometry test screenshots the same
 * rows on every run.
 *
 * Because a real runner has no exposure record, these rows belong to *no* runner: the runner detail
 * renders them under the sample notice with a sentence saying so, rather than pretending they are
 * the machine in the URL.
 */

import type {RawExposure} from './types';

/* ---------------- Connect (slice 2, still a fixture) ----------------
 *
 * Anchored to the moment the module loads, because an exposure is a relative fact: one seeded with
 * a fixed 2026 timestamp would read "0:00 left". The geometry probe measures boxes and computed
 * styles, never this text, so the relative anchor costs nothing.
 */
const SEED_NOW = Date.now();
const inSeconds = (s: number) => Math.floor(SEED_NOW / 1000) + s;

/* Raw exposures, including the lease channel's bearer `address`. Those values are canaries, not
 * usable addresses: they are shaped to match `TAILCAT_ADDRESS_RE` in runner/hoja_runnerd/connect.py
 * so the geometry script can assert their byte-for-byte absence from the rendered page. Nothing
 * here authenticates anything.
 *
 * `ConnectSupervisor` owns the tailcat children of *one runner* (connect.py:495-503), so a real
 * fixture would be keyed by runner id. This one is not keyed at all, because a server-backed screen
 * no longer has a fixture runner id to key by: it is a demonstration of the rows the surface draws.
 */
export const seedExposures: RawExposure[] = [
  {id: 'exp-1', workspaceId: 'ws-sample', ports: [8080],
    pinnedPeer: 'nodekey:8f2c0a1d4e6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b4c6d8e0f2a4b6c8d',
    expiresAt: inSeconds(42 * 60), live: true,
    address: 'tcQLd9m3zXk2Vd8sTn6Rf4Yb1Wc7Hg5Jp0Lr3Qe8Ut2Mz6Nv'},
  {id: 'exp-2', workspaceId: 'ws-sample', ports: [3000, 9229],
    pinnedPeer: 'nodekey:1b3d5f7a9c1e3f5a7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e',
    expiresAt: inSeconds(7 * 60), live: true,
    address: 'tcRt6yHn8Kj4Lm2Pw9Zx3Cv5Bn7Mq1Ws4Ed0Fg6Hj8Kl2Po5'},
];

/** `ConnectSupervisor.set_org_enabled` / `is_enabled`: an org admin can turn Connect off, and a
 * disabled org is not an empty list. */
export const seedConnectEnabled = true;
