// Roadmap ordering unit checks. Run with: node scripts/test-roadmap-order.mjs
//
// Guards HOJA-322: the Roadmap tab presents initiatives as a sequential
// timeline, so the ordering rule (target date ascending, undated last, then
// creation date) is pure and testable without a browser.
import assert from 'node:assert/strict';
import { orderRoadmap } from '../frontend/src/roadmap.ts';

const rows = [
  { id: 'undated', name: 'Undated effort', status: 'planned', createdAt: '2026-01-01T00:00:00Z' },
  { id: 'later', name: 'Later milestone', status: 'active', targetDate: '2027-06-30', createdAt: '2026-02-01T00:00:00Z' },
  { id: 'earlier', name: 'Earlier milestone', status: 'active', targetDate: '2026-12-01', createdAt: '2026-03-01T00:00:00Z' },
  { id: 'tie-b', name: 'Tie created second', status: 'planned', targetDate: '2026-12-01', createdAt: '2026-05-01T00:00:00Z' },
  { id: 'tie-a', name: 'Tie created first', status: 'planned', targetDate: '2026-12-01', createdAt: '2026-04-01T00:00:00Z' },
];

const ordered = orderRoadmap(rows);
const ids = ordered.map(row => row.id);
assert.deepEqual(ids, ['earlier', 'tie-a', 'tie-b', 'later', 'undated'],
  `expected date-ascending order with undated last, got ${ids.join(', ')}`);

// The input array must not be mutated: the timeline is derived state.
assert.deepEqual(rows.map(row => row.id), ['undated', 'later', 'earlier', 'tie-b', 'tie-a'],
  'orderRoadmap must not mutate its input');

assert.deepEqual(orderRoadmap([]), [], 'an empty roadmap stays empty');
console.log('ok   roadmap order: target date ascending, undated last, creation date breaks ties, input untouched');
