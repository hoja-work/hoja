// Status-age contract. Runs the real module both surfaces import:
//   node scripts/test-status-age.mjs
// The expected strings are derived by hand from the format in the module.
import assert from 'node:assert/strict';
import {formatDuration, millisInStatus, statusAgeLabel, timeInStatus} from '../frontend/src/graph/statusAge.ts';

const T = Date.parse('2026-11-03T12:00:00.000Z');
const iso = ms => new Date(T - ms).toISOString();
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('formatDuration boundaries', () => {
  assert.equal(formatDuration(0), 'just now');
  assert.equal(formatDuration(59_999), 'just now');
  assert.equal(formatDuration(60_000), '1m');
  assert.equal(formatDuration(59 * 60_000), '59m');
  assert.equal(formatDuration(60 * 60_000), '1h 0m');
  assert.equal(formatDuration(65 * 60_000), '1h 5m');
  assert.equal(formatDuration(24 * 60 * 60_000), '1d 0h');
  assert.equal(formatDuration(51 * 60 * 60_000), '2d 3h');
});

test('stateChangedAt wins over updatedAt and createdAt', () => {
  const issue = {state: {name: 'In Progress'}, stateChangedAt: iso(2 * 86_400_000), updatedAt: iso(60_000), createdAt: iso(9 * 86_400_000)};
  assert.equal(timeInStatus(issue, T), '2d 0h');
  assert.equal(statusAgeLabel(issue, T), 'In In Progress for 2d 0h');
});

test('falls back to updatedAt, then createdAt', () => {
  assert.equal(timeInStatus({state: {name: 'Todo'}, updatedAt: iso(3 * 3_600_000), createdAt: iso(9 * 86_400_000)}, T), '3h 0m');
  assert.equal(timeInStatus({state: {name: 'Todo'}, createdAt: iso(4 * 60_000)}, T), '4m');
});

test('missing or unparseable timestamps are Unknown, not a fake duration', () => {
  assert.equal(timeInStatus({state: {name: 'Todo'}}, T), 'Unknown');
  assert.equal(timeInStatus({state: {name: 'Todo'}, stateChangedAt: 'not-a-date'}, T), 'Unknown');
  assert.equal(statusAgeLabel({state: {name: 'Todo'}}, T), 'In Todo for Unknown');
});

test('missing status name still produces a readable sentence', () => {
  assert.equal(statusAgeLabel({stateChangedAt: iso(86_400_000)}, T), 'In No status for 1d 0h');
});

test('future timestamps clamp to just now instead of a negative duration', () => {
  assert.equal(millisInStatus({stateChangedAt: new Date(T + 60_000).toISOString()}, T), 0);
  assert.equal(timeInStatus({stateChangedAt: new Date(T + 60_000).toISOString()}, T), 'just now');
});

let failed = 0;
for (const [name, fn] of cases) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (error) { failed++; console.error(`FAIL ${name}\n     ${error.message}`); }
}
console.log(`\n${cases.length - failed}/${cases.length} status-age checks passed`);
process.exitCode = failed ? 1 : 0;
