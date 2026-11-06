import assert from 'node:assert/strict';
import {cycleKeyTarget} from '../frontend/src/linear/cycleSelection.ts';

const ids = ['completed', 'current', 'upcoming'];
const cases = [
  ['down selects the next cycle', 'current', 'ArrowDown', 'upcoming'],
  ['right selects the next cycle', 'current', 'ArrowRight', 'upcoming'],
  ['up selects the previous cycle', 'current', 'ArrowUp', 'completed'],
  ['left selects the previous cycle', 'current', 'ArrowLeft', 'completed'],
  ['Home selects the first cycle', 'current', 'Home', 'completed'],
  ['End selects the last cycle', 'current', 'End', 'upcoming'],
  ['next does not wrap at the end', 'upcoming', 'ArrowDown', 'upcoming'],
  ['previous does not wrap at the start', 'completed', 'ArrowUp', 'completed'],
  ['a missing selection recovers to the first cycle', 'removed', 'ArrowDown', 'completed'],
  ['Tab is left to the browser', 'current', 'Tab', null],
  ['Enter is left to the button', 'current', 'Enter', null],
  ['typing is not intercepted', 'current', 'j', null],
];

for (const [name, current, key, expected] of cases) {
  assert.equal(cycleKeyTarget(ids, current, key), expected, name);
  console.log(`ok   ${name}`);
}
assert.equal(cycleKeyTarget([], 'none', 'Home'), null);
console.log(`\n${cases.length + 1}/${cases.length + 1} cycle keyboard checks passed`);
