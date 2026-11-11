// Route-table checks. Run with: node scripts/test-settings-routes.mjs
//
// Guards HOJA-80 / HOJA-18: the team cycle-settings page ("set up cycles") must not be
// swallowed by the generic workspace settings route.
import assert from 'node:assert/strict';
import {routeView} from '../frontend/src/routes.ts';

const cases = [
  ['team cycle settings resolves (HOJA-18)', '/workspace/team/hoja/cycles/settings', 'Team cycle settings'],
  ['team key casing does not matter', '/workspace/team/HOJA/cycles/settings', 'Team cycle settings'],
  ['team cycle settings with a trailing slash', '/workspace/team/hoja/cycles/settings/', 'Team cycle settings'],
  ['team cycles list still resolves', '/workspace/team/hoja/cycles', 'Team cycles'],
  ['one cycle keeps its detail view', '/workspace/team/hoja/cycle/abc-123', 'Team cycle'],
  ['cycle detail outside a team keeps its view', '/workspace/cycle/abc-123', 'Cycle detail'],
  ['team active issues still resolves', '/workspace/team/hoja/active', 'Team issues'],
  ['team views still resolves', '/workspace/team/hoja/views', 'Team views'],
  ['team projects still resolves', '/workspace/team/hoja/projects', 'Team projects'],
  ['workspace settings still resolves', '/workspace/settings/ws-1', 'Settings'],
  ['workspace members page still resolves', '/workspace/members', 'Members'],
  ['issue detail still resolves', '/workspace/hoja/issue/HOJA-18', 'Issue detail'],
  ['board still resolves', '/workspace/hoja/board', 'Board'],
  ['roadmap tab still resolves (HOJA-322)', '/workspace/initiatives/active', 'Roadmap'],
  ['unknown workspace page is not found', '/workspace/nonsense', 'Not found'],
];

let failed = 0;
for (const [name, path, expected] of cases) {
  const actual = routeView(path);
  try {
    assert.equal(actual, expected, `${path}: expected ${expected}, got ${actual}`);
    console.log(`ok   ${name}`);
  } catch (e) {
    failed++;
    console.error(`FAIL ${name}: ${e.message}`);
  }
}

// The regression stated positively, so a future reordering fails loudly.
try {
  assert.notEqual(routeView('/workspace/team/hoja/cycles/settings'), 'Settings',
    'cycle settings must never route to the workspace Settings page');
  console.log('ok   cycle settings is not captured by workspace settings');
} catch (e) { failed++; console.error(`FAIL ${e.message}`); }

// HOJA-322 stated positively: the Initiatives tab is now the Roadmap, and the
// legacy /initiatives URLs keep resolving to it (URLs stay backwards compatible).
try {
  assert.equal(routeView('/workspace/initiatives'), 'Roadmap',
    'the initiatives URL must render the Roadmap view (HOJA-322)');
  assert.notEqual(routeView('/workspace/initiatives'), 'Initiatives',
    'the old Initiatives view name must not survive the HOJA-322 rename');
  console.log('ok   initiatives URLs render the renamed Roadmap view');
} catch (e) { failed++; console.error(`FAIL ${e.message}`); }

console.log(`\n${cases.length + 1 - failed}/${cases.length + 1} route checks passed`);
process.exit(failed ? 1 : 0);
