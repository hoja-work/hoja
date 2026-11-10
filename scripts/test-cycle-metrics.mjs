// Cycle metric contract. Runs the real module the UI renders from — no mocks:
//   node scripts/test-cycle-metrics.mjs
// Numbers are derived by hand from the definitions in docs/cycle-metrics.md.
import assert from 'node:assert/strict';
import {
  asPct, cycleStats, deliveryRate, dropoutRate, remainingSteps, scopeCompletion,
} from '../frontend/src/graph/cycleMetrics.ts';

const issue = (type, extra = {}) => ({state: {type}, estimate: null, ...extra});
const many = (type, n, extra = {}) => Array.from({length: n}, () => issue(type, extra));
const pct = (fn, s) => asPct(fn(s));
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/* The reporter's hypothetical: 200 started, 60 canceled, 140 finished.
 * "I finished 100% technically" is true of what survived and false of what was
 * committed; the metric must say both and must not let one stand in for the other. */
test('200 started / 60 canceled / 140 finished', () => {
  const s = cycleStats([...many('completed', 140), ...many('canceled', 60)]);
  assert.equal(s.scope, 200);
  assert.equal(s.kept, 140);
  assert.equal(pct(deliveryRate, s), 100);
  assert.equal(pct(dropoutRate, s), 30);
  assert.equal(pct(scopeCompletion, s), 70);
});

/* Real Cycle 3 of the TREBOL workspace on 2026-11-03 (measured from the live API). */
test('TREBOL Cycle 3: 204 in scope, 162 delivered, 42 canceled', () => {
  const s = cycleStats([...many('completed', 162), ...many('canceled', 42)]);
  assert.equal(s.scope, 204);
  assert.equal(pct(deliveryRate, s), 100);
  assert.equal(pct(dropoutRate, s), 21);
  assert.equal(pct(scopeCompletion, s), 79);
});

/* Adversarial: cancel everything and the cycle must not read as a delivery.
 * This is the regression the old completed/scope math could not pass either —
 * but a naive "exclude canceled from the denominator" fix would. */
test('canceling the entire cycle is not 100% delivery', () => {
  const s = cycleStats(many('canceled', 10));
  assert.equal(s.kept, 0);
  assert.equal(deliveryRate(s), null, 'no kept work means no delivery rate, not 1.0');
  assert.equal(pct(dropoutRate, s), 100);
  assert.equal(pct(scopeCompletion, s), 0);
});

test('open work stays in the denominator', () => {
  const s = cycleStats([...many('completed', 5), ...many('canceled', 3), ...many('started', 2)]);
  assert.equal(s.scope, 10);
  assert.equal(s.open, 2);
  assert.equal(s.kept, 7);
  assert.equal(pct(deliveryRate, s), 71);
  assert.equal(pct(scopeCompletion, s), 50);
});

test('empty cycle reports nothing rather than zeros that look like failure', () => {
  const s = cycleStats([]);
  assert.equal(s.scope, 0);
  assert.equal(deliveryRate(s), null);
  assert.equal(dropoutRate(s), null);
  assert.equal(scopeCompletion(s), null);
});

test('estimate points follow the same scope rule', () => {
  const s = cycleStats([
    issue('completed', {estimate: 3}), issue('completed', {estimate: 2}),
    issue('canceled', {estimate: 8}), issue('started', {estimate: 5}),
  ]);
  assert.equal(s.points, 10, 'canceled points are not remaining work');
  assert.equal(s.deliveredPoints, 5);
});

test('scope always decomposes into delivered + canceled + open', () => {
  for (const mix of [
    [...many('completed', 3), ...many('canceled', 4), ...many('started', 1), ...many('unstarted', 2)],
    [...many('backlog', 6)],
    [],
  ]) {
    const s = cycleStats(mix);
    assert.equal(s.completed + s.canceled + s.open, s.scope);
    assert.equal(s.kept + s.canceled, s.scope);
  }
});

/* Burndown: a canceled issue leaves the remaining pool at its cancel time. The
 * old code only stepped down on completion, so the line flattened at the number
 * of canceled issues and never reached zero. */
test('burndown reaches zero when nothing is open', () => {
  const day = 864e5, start = Date.parse('2026-10-27T00:00:00Z');
  const at = d => new Date(start + d * day).toISOString();
  const steps = remainingSteps([
    issue('completed', {stateChangedAt: at(1)}),
    issue('canceled', {stateChangedAt: at(1)}),
    issue('canceled', {stateChangedAt: at(2)}),
  ], 3, start, start + 3 * day);
  assert.equal(steps[0][1], 3);
  assert.equal(steps[1][1], 1, 'delivered and canceled both leave the pool');
  assert.equal(steps[3][1], 0, 'line must reach zero, not plateau at canceled count');
});

test('burndown keeps open work on the line', () => {
  const day = 864e5, start = Date.parse('2026-10-27T00:00:00Z');
  const steps = remainingSteps([
    issue('completed', {stateChangedAt: new Date(start + day).toISOString()}),
    issue('started', {stateChangedAt: new Date(start + day).toISOString()}),
  ], 2, start, start + 2 * day);
  assert.equal(steps.at(-1)[1], 1);
  assert.deepEqual(remainingSteps(undefined, 2, start, start + day), []);
});

let failed = 0;
for (const [name, fn] of cases) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (e) { failed++; console.error(`FAIL ${name}\n     ${e.message}`); }
}
console.log(`\n${cases.length - failed}/${cases.length} cycle metric checks passed`);
process.exit(failed ? 1 : 0);
