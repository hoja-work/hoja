#!/usr/bin/env node
/**
 * The Ready-to-merge gate, as a table.
 *
 * HOJA-417 defines readiness once, in src/reviews-gate.ts. This test is the second reader
 * of that rule: every branch — including each nullable field on its own — has a row here,
 * so a future "small fix" to the gate has to argue with a failing case instead of a comment.
 *
 *   node --experimental-strip-types scripts/test-reviews-gate.mjs
 */
import { reviewGroupOf, reviewGroupsOf, REVIEW_GROUP_NAMES } from '../src/reviews-gate.ts';

const ready = { state: 'open', mergeable: true, mergeState: 'clean', checksState: 'passing', reviewDecision: 'approved' };
const cases = [
  ['clean + passing + approved', ready, 'Ready to merge', 'clean+passing+approved'],
  ['github spells the merge state "mergeable"', { ...ready, mergeState: 'mergeable' }, 'Ready to merge', 'clean+passing+approved'],
  ['mergeable is null but the merge state is clean', { ...ready, mergeable: null }, 'Ready to merge', 'clean+passing+approved'],
  ['case/space insensitive values', { ...ready, mergeState: ' CLEAN ', checksState: 'Passing', reviewDecision: ' APPROVED ' }, 'Ready to merge', 'clean+passing+approved'],
  ['review decision null (cannot prove approval)', { ...ready, reviewDecision: null }, 'Open', 'review-unknown'],
  ['review decision requested changes', { ...ready, reviewDecision: 'changes_requested' }, 'Open', 'review-changes_requested'],
  ['checks null (enrichment has not run)', { ...ready, checksState: null }, 'Open', 'checks-unknown'],
  ['checks none (no workflows configured)', { ...ready, checksState: 'none' }, 'Open', 'checks-none'],
  ['checks failing', { ...ready, checksState: 'failing' }, 'Open', 'checks-failing'],
  ['merge state null', { ...ready, mergeState: null }, 'Open', 'merge-state-unknown'],
  ['merge state "unknown" as stored for merged PRs', { ...ready, mergeState: 'unknown' }, 'Open', 'merge-state-unknown'],
  ['merge state behind', { ...ready, mergeState: 'behind' }, 'Open', 'merge-state-behind'],
  ['mergeable false contradicts a clean merge state', { ...ready, mergeable: false }, 'Open', 'not-mergeable'],
  ['every field null', { state: null, mergeable: null, mergeState: null, checksState: null, reviewDecision: null }, 'Open', 'merge-state-unknown'],
  ['empty strings are not values', { ...ready, mergeState: '', checksState: '', reviewDecision: '' }, 'Open', 'merge-state-unknown'],
  ['draft beats a fully green signal', { ...ready, state: 'draft' }, 'Draft', 'draft'],
  ['draft with nothing enriched', { state: 'draft', mergeable: null, mergeState: null, checksState: null, reviewDecision: null }, 'Draft', 'draft'],
  ['merged is terminal, not a group', { ...ready, state: 'merged' }, 'Terminal', 'merged'],
  ['closed is terminal, not a group', { state: 'closed', mergeable: true, mergeState: 'clean', checksState: 'passing', reviewDecision: 'approved' }, 'Terminal', 'closed'],
  ['no state at all falls through to the checks', { ...ready, state: undefined }, 'Ready to merge', 'clean+passing+approved'],
];

let failures = 0;
for (const [label, input, wantGroup, wantReason] of cases) {
  const got = reviewGroupOf(input);
  const ok = got.group === wantGroup && got.reason === wantReason;
  if (!ok) {
    failures++;
    console.error(`  ✗ ${label}: got ${got.group}/${got.reason}, want ${wantGroup}/${wantReason}`);
  } else {
    console.log(`  ✓ ${label} → ${got.group}`);
  }
}

// The axis is the reference's, in the reference's order, and holds no terminal bucket.
const axis = REVIEW_GROUP_NAMES.join(' / ');
if (axis !== 'Ready to merge / Open / Draft') {
  failures++;
  console.error(`  ✗ group axis: ${axis}`);
} else {
  console.log(`  ✓ group axis: ${axis}`);
}

// Grouping preserves order and drops nothing: every link lands in exactly one bucket.
const links = [ready, { ...ready, state: 'draft' }, { ...ready, state: 'merged' }, { ...ready, reviewDecision: null }];
const { groups, terminal } = reviewGroupsOf(links);
const covered = groups.reduce((n, g) => n + g.items.length, 0) + terminal.length;
const shape = `${groups.map((g) => `${g.name}=${g.items.length}`).join(' ')} terminal=${terminal.length}`;
if (groups.map((g) => g.name).join(' / ') !== axis || covered !== links.length) {
  failures++;
  console.error(`  ✗ reviewGroupsOf: ${shape} covers ${covered}/${links.length}`);
} else {
  console.log(`  ✓ reviewGroupsOf: ${shape}`);
}

if (failures) {
  console.error(`reviews gate: ${failures} failing case(s)`);
  process.exit(1);
}
console.log(`reviews gate ok: ${cases.length} cases, axis "${axis}", every link bucketed once`);
