#!/usr/bin/env node
/**
 * The view filter and display models, as tables (src/views/filterModel.ts, filterEval.ts,
 * displayModel.ts). The chip bar, the URL and saved views all go through these functions,
 * so a round trip that loses a predicate shows up here first.
 *
 *   npm run test:views
 */
import assert from 'node:assert/strict';
import {
  chipsToFilterTree, filterTreeToChips, isSimple, toggleValue, setChipValues, removeNode, appendTo,
  emptyGroup, newChip, parseDateInput, describeDate, resolveDate, encodeFilterParam, decodeFilterParam,
  withFilterParam, opLabel, fieldSpec, PROJECT_FIELDS, chipOps, effectiveOp,
} from '../src/views/filterModel.ts';
import {matchesFilter, countBy, parentIdsOf} from '../src/views/filterEval.ts';
import {
  groupTasks, sortTasks, applyCompletedWindow, withSubIssues, divergentKeys, mergeDisplay, ISSUE_DEFAULTS,
  toggleProperty, ISSUE_PROPERTIES, projectPageOptions, projectPagePatch, PROJECT_DEFAULTS,
} from '../src/views/displayModel.ts';
import {suggestViewName, suggestViewIdentity, uniqueName} from '../src/views/filterLabels.ts';
import {
  viewPath, viewIdentOf, redirectLegacyViewPath, viewSections, sortViews, pinnedTabs, targetInput, targetOf,
  sameDocument, displayChanges, canEditView, isEmojiIcon, VIEW_COLORS, reorderPinned, loadViewEdits, saveViewEdits,
} from '../src/views/viewModel.ts';
import {createViewListCache} from '../src/views/viewListCache.ts';
import {routeView} from '../src/routes.ts';
import {ISSUE_FIELDS} from '../src/views/filterModel.ts';
import {keyBetween, validate as validateRank, compareRank} from '../src/views/ranking.ts';
import {planDrop, planKey, groupPatch, optimisticRank} from '../src/views/manualOrderPlan.ts';
import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';

let failures = 0, passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}\n  ${error.message.split('\n').join('\n  ')}`);
  }
}
const strip = node => node.type === 'chip'
  ? {field: node.field, op: node.op, values: node.values, ...(node.raw ? {raw: node.raw} : {})}
  : {conj: node.conj, items: node.items.map(strip)};

/* ---------------------------------------------------------- round trips */

const roundTrips = [
  ['empty', {}],
  ['one value', {priority: {is: 1}}],
  ['several values', {priority: {isEitherOf: [1, 2]}}],
  ['negative several', {assignee: {isNeitherOf: ['u1', 'u2']}}],
  ['no assignee', {assignee: {is: null}}],
  ['me', {assignee: {is: 'me'}}],
  ['merged distinct keys', {priority: {is: 1}, labels: {includesAny: ['l1']}, state: {isEitherOf: ['Todo', 'In Progress']}}],
  ['labels all', {labels: {includesAll: ['l1', 'l2']}}],
  ['labels none', {labels: {includesNone: ['l1']}}],
  ['relative date', {createdAt: {after: '-P1W'}}],
  ['overdue', {dueDate: {is: 'overdue'}}],
  ['no due date', {dueDate: {is: null}}],
  ['estimate range', {and: [{estimate: {isGreaterThan: 1}}, {estimate: {isLessThanOrEqual: 5}}]}],
  ['content', {or: [{title: {contains: 'crash'}}, {description: {contains: 'crash'}}]}],
  ['title', {title: {contains: 'login'}}],
  ['relation parent', {hasSubIssues: {is: true}}],
  ['relation not sub', {isSubIssue: {is: false}}],
  ['relation top', {parent: {isNull: true}}],
  ['or at root', {or: [{priority: {is: 1}}, {assignee: {is: 'me'}}]}],
  ['nested and/or', {and: [{state: {isNot: 'Canceled'}}, {or: [{priority: {is: 1}}, {labels: {includesAny: ['bug']}}]}]}],
  ['three levels', {or: [{and: [{priority: {is: 1}}, {or: [{team: {is: 't1'}}, {team: {is: 't2'}}]}]}, {cycle: {is: null}}]}],
  ['repeated key keeps and', {and: [{priority: {is: 1}}, {priority: {isNot: 2}}]}],
  ['subscriber', {subscriber: {is: 'me'}}],
  ['myTeams', {team: {is: 'myTeams'}}],
];
for (const [name, tree] of roundTrips)
  test(`round trip: ${name}`, () => assert.deepEqual(chipsToFilterTree(filterTreeToChips(tree)), tree));

test('unknown predicates survive as raw chips', () => {
  const tree = {and: [{number: {isGreaterThan: 5}}, {parent: {is: 'abc'}}, {identifier: {equalsIgnoreCase: 'X-1'}}]};
  const root = filterTreeToChips(tree);
  assert.ok(root.items.every(i => i.field === 'raw'));
  // Distinct properties under `and` come back merged into one object: the same selection.
  assert.deepEqual(chipsToFilterTree(root), Object.assign({}, ...tree.and));
  assert.deepEqual(chipsToFilterTree(filterTreeToChips(chipsToFilterTree(root))), Object.assign({}, ...tree.and));
});

test('isMe reads as a "me" chip', () => {
  const root = filterTreeToChips({creator: {isMe: true}});
  assert.deepEqual(strip(root.items[0]), {field: 'creator', op: 'is', values: ['me']});
  assert.deepEqual(chipsToFilterTree(root), {creator: {is: 'me'}});
});

test('labels written as is / isEitherOf read as includes', () => {
  assert.equal(filterTreeToChips({labels: {is: 'l1'}}).items[0].op, 'includesAny');
  assert.equal(filterTreeToChips({labels: {isNeitherOf: ['a', 'b']}}).items[0].op, 'includesNone');
});

test('simple vs advanced', () => {
  assert.equal(isSimple(filterTreeToChips({priority: {is: 1}, assignee: {is: 'me'}})), true);
  assert.equal(isSimple(filterTreeToChips({or: [{priority: {is: 1}}, {assignee: {is: 'me'}}]})), false);
  assert.equal(isSimple(filterTreeToChips({and: [{priority: {is: 1}}, {or: [{a: 1}, {b: 2}]}]})), false);
});

test('a two-property and inside an or stays a group', () => {
  const root = filterTreeToChips({or: [{priority: {is: 1}, assignee: {is: 'me'}}, {cycle: {is: null}}]});
  assert.equal(root.conj, 'or');
  assert.equal(root.items[0].type, 'group');
  assert.equal(root.items[0].items.length, 2);
});

test('project fields map status/lead/targetDate', () => {
  const tree = {status: {isEitherOf: ['Started', 'Planned']}, lead: {is: 'me'}, targetDate: {before: 'P1M'}};
  assert.deepEqual(chipsToFilterTree(filterTreeToChips(tree, PROJECT_FIELDS), PROJECT_FIELDS), tree);
});

/* ---------------------------------------------------------- edits */

test('toggleValue adds, extends and removes a chip', () => {
  let root = emptyGroup();
  root = toggleValue(root, 'priority', 1);
  assert.deepEqual(chipsToFilterTree(root), {priority: {is: 1}});
  root = toggleValue(root, 'priority', 2);
  assert.deepEqual(chipsToFilterTree(root), {priority: {isEitherOf: [1, 2]}});
  root = toggleValue(root, 'priority', 1);
  root = toggleValue(root, 'priority', 2);
  assert.equal(root.items.length, 0);
});

test('single-valued fields replace their value', () => {
  let root = toggleValue(emptyGroup(), 'createdAt', '-P1W', undefined, 'after');
  root = toggleValue(root, 'createdAt', '-P1M', undefined, 'after');
  assert.deepEqual(chipsToFilterTree(root), {createdAt: {after: '-P1M'}});
});

test('isNot chip with several values writes isNeitherOf', () => {
  const chip = newChip('assignee', ['me', null], 'isNot');
  assert.deepEqual(chipsToFilterTree({...emptyGroup(), items: [chip]}), {assignee: {isNeitherOf: ['me', null]}});
});

test('removing the last chip of a nested group removes the group', () => {
  const inner = {...emptyGroup('or'), items: [newChip('priority', [1])]};
  let root = {...emptyGroup(), items: [newChip('assignee', ['me']), inner]};
  root = removeNode(root, inner.items[0].id);
  assert.deepEqual(chipsToFilterTree(root), {assignee: {is: 'me'}});
  root = appendTo(root, root.id, newChip('cycle', [null]));
  assert.deepEqual(chipsToFilterTree(root), {assignee: {is: 'me'}, cycle: {is: null}});
  root = setChipValues(root, root.items[0].id, []);
  assert.deepEqual(chipsToFilterTree(root), {cycle: {is: null}});
});

test('chips without values write nothing', () => {
  assert.deepEqual(chipsToFilterTree({...emptyGroup(), items: [newChip('priority', [])]}), {});
});

test('operator labels', () => {
  assert.equal(opLabel(fieldSpec('priority'), 'is', 1), 'is');
  assert.equal(opLabel(fieldSpec('priority'), 'is', 2), 'is either of');
  assert.equal(opLabel(fieldSpec('labels'), 'includesAll', 2), 'include all of');
  assert.equal(opLabel(fieldSpec('dueDate'), 'before', 1), 'before');
});

test('the empty value only takes identity operators', () => {
  assert.deepEqual(chipOps(fieldSpec('estimate'), [3]), ['is', 'isNot', 'gt', 'gte', 'lt', 'lte']);
  assert.deepEqual(chipOps(fieldSpec('estimate'), [null]), ['is', 'isNot']);
  assert.deepEqual(chipOps(fieldSpec('labels'), [null, 'l1']), ['includesAny', 'includesNone']);
  assert.deepEqual(chipOps(fieldSpec('dueDate'), [null]), ['is']);
  // A chip left on "greater than" when "No estimate" is picked writes "is", not {gt: null}.
  const estimate = {...newChip('estimate', [null], 'gt')};
  assert.equal(effectiveOp(fieldSpec('estimate'), estimate), 'is');
  assert.deepEqual(chipsToFilterTree(estimate), {estimate: {is: null}});
  assert.deepEqual(chipsToFilterTree(newChip('labels', [null], 'includesAll')), {labels: {includesAny: [null]}});
  assert.deepEqual(chipsToFilterTree(newChip('dueDate', [null], 'before')), {dueDate: {is: null}});
});

test('"overdue" only takes "is"', () => {
  assert.deepEqual(chipOps(fieldSpec('dueDate'), ['overdue']), ['is']);
  assert.ok(chipOps(fieldSpec('dueDate'), ['P1W']).includes('before'));
  // A date chip left on "before" when Overdue is picked writes {is: "overdue"}, which the
  // server accepts; {before: "overdue"} is refused.
  assert.deepEqual(chipsToFilterTree(newChip('dueDate', ['overdue'], 'before')), {dueDate: {is: 'overdue'}});
});

/* ---------------------------------------------------------- dates */

const dateCases = [
  ['10 days ago', 'past', '-P10D'],
  ['2 weeks', 'past', '-P2W'],
  ['2 weeks', 'future', 'P2W'],
  ['in 3 days', 'past', 'P3D'],
  ['3 days from now', 'past', 'P3D'],
  ['1 month ago', 'future', '-P1M'],
  ['a year ago', 'past', '-P1Y'],
  ['two wks', 'past', '-P2W'],
  ['12 hours ago', 'past', '-PT12H'],
  ['last week', 'future', '-P1W'],
  ['next month', 'past', 'P1M'],
  ['Today', 'past', 'today'],
  ['overdue', 'future', 'overdue'],
  ['2026-11-30', 'past', '2026-11-30'],
  ['Nov 30 2026', 'past', '2026-11-30'],
  ['-p7d', 'past', '-P7D'],
  ['next tuesday', 'past', null],
  ['soon', 'past', null],
  ['2026-13-45', 'past', null],
];
for (const [text, direction, want] of dateCases)
  test(`parseDateInput(${JSON.stringify(text)}, ${direction})`, () => assert.equal(parseDateInput(text, direction), want));

test('describeDate', () => {
  assert.equal(describeDate('-P1W'), '1 week ago');
  assert.equal(describeDate('P3D'), '3 days from now');
  assert.equal(describeDate('-PT12H'), '12 hours ago');
  assert.equal(describeDate('overdue'), 'Overdue');
  assert.equal(describeDate('2026-11-05'), 'Nov 5, 2026');
  assert.equal(describeDate(null), 'No date');
});

const NOW = Date.parse('2026-11-23T15:30:00Z');
test('resolveDate: durations name a UTC day, hours an instant', () => {
  assert.deepEqual(resolveDate('-P1W', NOW), {start: Date.parse('2026-11-16T00:00:00Z'), end: Date.parse('2026-11-17T00:00:00Z')});
  assert.deepEqual(resolveDate('P1M', NOW), {start: Date.parse('2026-12-23T00:00:00Z'), end: Date.parse('2026-12-24T00:00:00Z')});
  const h = Date.parse('2026-11-23T03:30:00Z');
  assert.deepEqual(resolveDate('-PT12H', NOW), {start: h, end: h});
  assert.deepEqual(resolveDate('today', NOW), {start: Date.parse('2026-11-23T00:00:00Z'), end: Date.parse('2026-11-24T00:00:00Z')});
  assert.equal(resolveDate('garbage', NOW), null);
});

/* ---------------------------------------------------------- URL */

test('URL param round trip, including non-ASCII text', () => {
  const tree = {and: [{title: {contains: 'café ☕'}}, {or: [{priority: {is: 1}}, {assignee: {is: 'me'}}]}]};
  const encoded = encodeFilterParam(tree);
  assert.match(encoded, /^[A-Za-z0-9_-]+$/);
  assert.deepEqual(decodeFilterParam(encoded), tree);
  assert.equal(encodeFilterParam({}), '');
  assert.equal(decodeFilterParam('%%%'), null);
  assert.equal(decodeFilterParam(encodeFilterParam({a: 1}).slice(0, -2) + '!!'), null);
  assert.equal(decodeFilterParam(Buffer.from('[1,2]').toString('base64url')), null);
});

test('withFilterParam keeps other parameters', () => {
  const s = withFilterParam('?tab=active&x=1', {priority: {is: 1}});
  const p = new URLSearchParams(s);
  assert.equal(p.get('tab'), 'active');
  assert.deepEqual(decodeFilterParam(p.get('filter')), {priority: {is: 1}});
  assert.equal(withFilterParam(s, {}), '?tab=active&x=1');
  assert.equal(withFilterParam('?filter=abc', null), '');
});

/* ---------------------------------------------------------- evaluation */

const todo = {id: 'st1', name: 'Todo', type: 'unstarted'};
const doing = {id: 'st2', name: 'In Progress', type: 'started'};
const done = {id: 'st3', name: 'Done', type: 'completed'};
const tasks = [
  {id: 'a', identifier: 'HOJ-1', title: 'Fix login crash', description: 'Stack trace', priority: 1, state: doing, assignee: {id: 'me1', name: 'Me'}, creator: {id: 'u2', name: 'Ann'}, team: {id: 't1', key: 'HOJ'}, labels: {nodes: [{id: 'l1', name: 'Bug'}]}, createdAt: '2026-11-20T10:00:00Z', updatedAt: '2026-11-22T10:00:00Z', dueDate: '2026-11-20', estimate: 3, subscribers: {nodes: [{id: 'me1'}]}},
  {id: 'b', identifier: 'HOJ-2', title: 'Write docs', priority: 0, state: todo, team: {id: 't1', key: 'HOJ'}, labels: {nodes: [{id: 'l2', name: 'Docs'}, {id: 'l1', name: 'Bug'}]}, createdAt: '2026-10-01T10:00:00Z', updatedAt: '2026-11-01T10:00:00Z', parent: {id: 'a'}, project: {id: 'p1', name: 'Launch'}},
  {id: 'c', identifier: 'OPS-3', title: 'Rotate keys', priority: 3, state: done, assignee: {id: 'u2', name: 'Ann'}, team: {id: 't2', key: 'OPS'}, labels: {nodes: []}, createdAt: '2026-11-23T09:00:00Z', updatedAt: '2026-11-23T09:00:00Z', stateChangedAt: '2026-11-23T09:00:00Z', dueDate: '2026-12-01'},
];
const ctx = {viewerId: 'me1', myTeamIds: new Set(['t1']), now: NOW, parentIds: parentIdsOf(tasks)};
const ids = tree => tasks.filter(t => matchesFilter(tree, t, ctx)).map(t => t.id).join('');
const evalCases = [
  ['everything', {}, 'abc'],
  ['priority is urgent', {priority: {is: 1}}, 'a'],
  ['no priority', {priority: {is: 0}}, 'b'],
  ['assignee me', {assignee: {is: 'me'}}, 'a'],
  ['assignee isMe false', {assignee: {isMe: false}}, 'bc'],
  ['assignee isNot me keeps unassigned', {assignee: {isNot: 'me'}}, 'bc'],
  ['no assignee', {assignee: {is: null}}, 'b'],
  ['state by name', {state: {isEitherOf: ['todo', 'Done']}}, 'bc'],
  ['state by key', {state: {is: 'In Progress|started'}}, 'a'],
  ['stateType', {stateType: {isNot: 'completed'}}, 'ab'],
  ['labels any', {labels: {includesAny: ['Bug']}}, 'ab'],
  ['labels all', {labels: {includesAll: ['l1', 'l2']}}, 'b'],
  ['labels none', {labels: {includesNone: ['l1']}}, 'c'],
  ['no labels', {labels: {is: null}}, 'c'],
  ['my teams', {team: {is: 'myTeams'}}, 'ab'],
  ['team key', {team: {is: 'ops'}}, 'c'],
  ['project none', {project: {is: null}}, 'ac'],
  ['created after a week ago', {createdAt: {after: '-P1W'}}, 'ac'],
  ['created today', {createdAt: {is: 'today'}}, 'c'],
  ['overdue', {dueDate: {is: 'overdue'}}, 'a'],
  ['due before a week from now', {dueDate: {before: 'P1W'}}, 'a'],
  ['due before two weeks from now', {dueDate: {before: 'P2W'}}, 'ac'],
  ['no due date', {dueDate: {is: null}}, 'b'],
  ['completed today', {completedAt: {onOrAfter: 'today'}}, 'c'],
  ['estimate > 2', {estimate: {isGreaterThan: 2}}, 'a'],
  ['content', {or: [{title: {contains: 'TRACE'}}, {description: {contains: 'TRACE'}}]}, 'a'],
  ['parent issues', {hasSubIssues: {is: true}}, 'a'],
  ['sub-issues', {isSubIssue: {is: true}}, 'b'],
  ['top level', {parent: {isNull: true}}, 'ac'],
  ['subscriber me', {subscriber: {is: 'me'}}, 'a'],
  ['nested', {and: [{team: {is: 'myTeams'}}, {or: [{priority: {is: 1}}, {labels: {includesAny: ['Docs']}}]}]}, 'ab'],
  ['empty or matches everything', {or: []}, 'abc'],
];
for (const [name, tree, want] of evalCases) test(`matchesFilter: ${name}`, () => assert.equal(ids(tree), want));

test('countBy counts each value, null and me', () => {
  const byAssignee = countBy(tasks, 'assignee', ctx);
  assert.equal(byAssignee.get('me1'), 1);
  assert.equal(byAssignee.get('me'), 1);
  assert.equal(byAssignee.get('null'), 1);
  const byLabel = countBy(tasks, 'labels', ctx);
  assert.equal(byLabel.get('l1'), 2);
  assert.equal(byLabel.get('null'), 1);
  assert.equal(countBy(tasks, 'state', ctx).get('Todo'), 1);
});

/* ---------------------------------------------------------- display */

const states = [todo, doing, done];
test('groupTasks by status keeps workflow order, closed last, with totals', () => {
  const groups = groupTasks([tasks[0]], 'status', {states}, {totals: tasks});
  assert.deepEqual(groups.map(g => [g.label, g.items.length, g.total]), [['In Progress', 1, 1]]);
  const all = groupTasks(tasks.slice(1), 'status', {states}, {showEmpty: true, totals: tasks});
  assert.deepEqual(all.map(g => [g.label, g.items.length, g.total]), [['Todo', 1, 1], ['In Progress', 0, 1], ['Done', 1, 1]]);
});

test('groupTasks by labels puts a task in each label group, no labels last', () => {
  const groups = groupTasks(tasks, 'labels');
  assert.deepEqual(groups.map(g => [g.label, g.items.map(t => t.id).join('')]), [['Bug', 'ab'], ['Docs', 'b'], ['No labels', 'c']]);
});

test('groupTasks by priority orders Urgent first and No priority last', () => {
  assert.deepEqual(groupTasks(tasks, 'priority').map(g => g.label), ['Urgent', 'Medium', 'No priority']);
});

test('sortTasks: priority with No priority last in both directions', () => {
  assert.deepEqual(sortTasks(tasks, {ordering: 'priority'}).map(t => t.id), ['a', 'c', 'b']);
  assert.deepEqual(sortTasks(tasks, {ordering: 'priority', reverse: true}).map(t => t.id), ['c', 'a', 'b']);
});

test('sortTasks: due date, title, created, completed by recency', () => {
  assert.deepEqual(sortTasks(tasks, {ordering: 'dueDate'}).map(t => t.id), ['a', 'c', 'b']);
  assert.deepEqual(sortTasks(tasks, {ordering: 'title'}).map(t => t.id), ['a', 'c', 'b']);
  assert.deepEqual(sortTasks(tasks, {ordering: 'created'}).map(t => t.id), ['c', 'a', 'b']);
  assert.deepEqual(sortTasks(tasks, {ordering: 'created', orderCompletedByRecency: true}).map(t => t.id), ['a', 'b', 'c']);
});

test('applyCompletedWindow', () => {
  const old = {...tasks[2], id: 'd', stateChangedAt: '2026-10-01T00:00:00Z'};
  const set = [...tasks, old];
  assert.deepEqual(applyCompletedWindow(set, 'day', NOW).map(t => t.id), ['a', 'b', 'c']);
  assert.deepEqual(applyCompletedWindow(set, 'none', NOW).map(t => t.id), ['a', 'b']);
  assert.deepEqual(applyCompletedWindow(set, 'all', NOW).map(t => t.id), ['a', 'b', 'c', 'd']);
});

test('withSubIssues: show all adds children, hide drops them', () => {
  assert.deepEqual(withSubIssues([tasks[0]], tasks, {nestedSubIssues: 'all'}).map(t => t.id), ['a', 'b']);
  assert.deepEqual(withSubIssues([tasks[0]], tasks, {nestedSubIssues: 'matching'}).map(t => t.id), ['a']);
  assert.deepEqual(withSubIssues(tasks, tasks, {showSubIssues: false}).map(t => t.id), ['a', 'c']);
});

test('divergentKeys and mergeDisplay layers', () => {
  const base = mergeDisplay({grouping: 'assignee'});
  assert.equal(base.ordering, ISSUE_DEFAULTS.ordering);
  assert.deepEqual(divergentKeys({grouping: 'assignee', collapsedGroups: ['x']}, base), []);
  assert.deepEqual(divergentKeys({grouping: 'priority'}, base), ['grouping']);
  assert.equal(mergeDisplay({layout: 'board'}, {layout: null}).layout, 'board');
});

/* ---------------------------------------------------------- review fixes */

test('a single-valued field picked again with another operator replaces its chip', () => {
  // "Created after 1 week ago", then the "Today" preset (is): one chip, not two contradicting ones.
  let root = toggleValue(emptyGroup(), 'createdAt', '-P1W', undefined, 'after');
  root = toggleValue(root, 'createdAt', 'today', undefined, 'is');
  assert.equal(root.items.length, 1);
  assert.deepEqual(chipsToFilterTree(root), {createdAt: {is: 'today'}});
  // "No due date" after "before 1 week from now".
  root = toggleValue(emptyGroup(), 'dueDate', 'P1W', undefined, 'before');
  root = toggleValue(root, 'dueDate', null, undefined, 'is');
  assert.deepEqual(chipsToFilterTree(root), {dueDate: {is: null}});
});

test('multi-valued fields still keep one chip per operator', () => {
  let root = toggleValue(emptyGroup(), 'labels', 'l1', undefined, 'includesAny');
  root = toggleValue(root, 'labels', 'l2', undefined, 'includesNone');
  assert.deepEqual(chipsToFilterTree(root), {and: [{labels: {includesAny: ['l1']}}, {labels: {includesNone: ['l2']}}]});
});

test('nested sub-issues default to "Show matching", as Linear does', () => {
  assert.equal(ISSUE_DEFAULTS.nestedSubIssues, 'matching');
  assert.equal(mergeDisplay().nestedSubIssues, 'matching');
});

test('toggleProperty keeps the panel order and properties the panel does not list', () => {
  assert.deepEqual(toggleProperty(['cycle', 'status', 'identifier'], ISSUE_PROPERTIES, 'assignee'),
    ['identifier', 'status', 'assignee', 'cycle']);
  assert.deepEqual(toggleProperty(['cycle', 'status'], ISSUE_PROPERTIES, 'status'), ['cycle']);
});

test('the Projects page options round-trip through a stored display', () => {
  const base = mergeDisplay(PROJECT_DEFAULTS);
  assert.deepEqual(projectPageOptions(base),
    {layout: 'List', group: 'No grouping', ordering: 'Name', showClosed: true, summary: false});
  const patch = {
    ...projectPagePatch({layout: 'Board'}, base),
    ...projectPagePatch({group: 'Health'}, base),
    ...projectPagePatch({ordering: 'Start date'}, base),
    ...projectPagePatch({showClosed: false}, base),
    ...projectPagePatch({summary: true}, base),
  };
  assert.deepEqual(patch, {layout: 'board', grouping: 'health', ordering: 'startDate', completedWindow: 'none',
    visibleProperties: [...PROJECT_DEFAULTS.visibleProperties, 'summary']});
  assert.deepEqual(projectPageOptions(mergeDisplay(PROJECT_DEFAULTS, patch)),
    {layout: 'Board', group: 'Health', ordering: 'Start date', showClosed: false, summary: true});
  assert.deepEqual(projectPagePatch({summary: false}, mergeDisplay(PROJECT_DEFAULTS, patch)).visibleProperties,
    PROJECT_DEFAULTS.visibleProperties);
});

const catalog = {
  fields: ISSUE_FIELDS,
  menu: [],
  options: field => field === 'priority'
    ? [{value: 1, label: 'Urgent'}, {value: 2, label: 'High'}]
    : field === 'assignee' ? [{value: 'me', label: 'Current user'}] : [],
};

test('suggestViewName reads the chips as a short name', () => {
  assert.equal(suggestViewName(filterTreeToChips({priority: {isEitherOf: [1, 2]}, assignee: {is: 'me'}}), catalog),
    'Urgent, High · Assigned to Current user');
  assert.equal(suggestViewName(filterTreeToChips({createdAt: {after: '-P1W'}}), catalog), 'Created date after 1 week ago');
  assert.equal(suggestViewName(filterTreeToChips({or: [{priority: {is: 1}}, {assignee: {is: 'me'}}]}), catalog),
    'Filtered tasks');
  assert.equal(suggestViewName(filterTreeToChips({}), catalog), 'Filtered tasks');
});

test('uniqueName numbers a taken name', () => {
  assert.equal(uniqueName('Urgent', ['Other']), 'Urgent');
  assert.equal(uniqueName('Urgent', ['urgent', 'Urgent 2']), 'Urgent 3');
});

test('suggestViewIdentity names a draft from its first chip, like Linear', () => {
  assert.deepEqual(suggestViewIdentity(filterTreeToChips({priority: {is: 1}}), catalog),
    {name: 'Urgent tasks', description: 'Tasks with urgent priority', icon: 'Siren'});
  assert.equal(suggestViewIdentity(filterTreeToChips({assignee: {is: 'me'}}), catalog).name, 'Assigned to me');
  assert.equal(suggestViewIdentity(filterTreeToChips({priority: {isNot: 2}}), catalog).name, 'Tasks without High');
  assert.equal(suggestViewIdentity(filterTreeToChips({priority: {is: 2}}), catalog, 'projects').description,
    'Projects with high priority');
  assert.equal(suggestViewIdentity(filterTreeToChips({}), catalog), null);
});

const view = (over = {}) => ({
  id: 'v1', name: 'View', kind: 'issue', scope: 'workspace', visibility: 'workspace', favorite: false,
  filters: {}, display: {}, ...over,
});

test('view paths: slug URL, legacy ids and the stable /<workspace>/view/<slug> link', () => {
  assert.equal(viewPath(view({slugId: 'bugs-1a2b3c4d'})), '/workspace/view/bugs-1a2b3c4d');
  assert.equal(viewPath(view()), '/workspace/view/v1');
  assert.equal(viewIdentOf('/workspace/view/bugs-1a2b?x=1'), 'bugs-1a2b');
  assert.equal(viewIdentOf('/workspace/views/issues'), null);
  assert.equal(redirectLegacyViewPath('/workspace/views/issues/abc'), '/workspace/view/abc');
  assert.equal(redirectLegacyViewPath('/workspace/team/eng/views/projects/abc?q=1'), '/workspace/view/abc?q=1');
  assert.equal(redirectLegacyViewPath('/acme/view/bugs-1a2b'), '/workspace/view/bugs-1a2b');
  for (const kept of ['/workspace/views/issues', '/workspace/views/issues/new', '/workspace/team/eng/views/issues/new',
    '/workspace/project/p1/view/new', '/workspace/task/ENG-1/x'])
    assert.equal(redirectLegacyViewPath(kept), kept);
});

test('views list sections: personal first, then workspace, teams and projects', () => {
  const rows = [
    view({id: 'a', name: 'b', visibility: 'private'}),
    view({id: 'b', name: 'a', visibility: 'workspace'}),
    view({id: 'c', scope: 'team', visibility: 'team', team: {id: 't2', key: 'WEB'}}),
    view({id: 'd', scope: 'team', visibility: 'team', team: {id: 't1', key: 'API'}}),
    view({id: 'e', scope: 'project', visibility: 'workspace', project: {id: 'p1'}}),
  ];
  const sections = viewSections(rows, {team: id => ({t1: 'Api', t2: 'Web'})[id], project: () => 'Site'});
  assert.deepEqual(sections.map(s => s.key), ['personal', 'workspace', 'team:t1', 'team:t2', 'project:p1']);
  assert.equal(sections[0].hint, 'Only visible to you');
  assert.deepEqual(sortViews(rows.slice(0, 2), 'name').map(v => v.id), ['b', 'a']);
  assert.deepEqual(sortViews(rows.slice(0, 2), 'name', true).map(v => v.id), ['a', 'b']);
});

test('pinned team tabs keep their stored order', () => {
  const rows = [
    view({id: 'x', pinned: true, sortOrder: 2}), view({id: 'y', pinned: false}),
    view({id: 'z', pinned: true, sortOrder: 1}), view({id: 'w', pinned: true, sortOrder: null, createdAt: '2026'}),
  ];
  assert.deepEqual(pinnedTabs(rows).map(v => v.id), ['z', 'x', 'w']);
});

test('save targets map to scope and visibility, and back', () => {
  assert.deepEqual(targetInput({kind: 'personal'}), {scope: 'workspace', visibility: 'private'});
  assert.deepEqual(targetInput({kind: 'team', teamId: 't'}), {scope: 'team', visibility: 'team', teamId: 't'});
  assert.deepEqual(targetInput({kind: 'project', projectId: 'p', shared: true}),
    {scope: 'project', visibility: 'workspace', projectId: 'p'});
  assert.deepEqual(targetOf(view({visibility: 'private'})), {kind: 'personal'});
  assert.deepEqual(targetOf(view({scope: 'team', visibility: 'team', team: {id: 't', key: 'T'}})), {kind: 'team', teamId: 't'});
  // "Personal" saved from a team stays a team view (its preview keeps to the team's tasks).
  assert.deepEqual(targetInput({kind: 'team', teamId: 't', personal: true}),
    {scope: 'team', visibility: 'private', teamId: 't'});
  assert.deepEqual(targetOf(view({scope: 'team', visibility: 'private', team: {id: 't', key: 'T'}})),
    {kind: 'team', teamId: 't', personal: true});
});

test('dragging a pinned tab writes the fewest sort orders that keep every tab in place', () => {
  const tabs = [view({id: 'a', sortOrder: 1}), view({id: 'b', sortOrder: 2}), view({id: 'c', sortOrder: 3})];
  assert.deepEqual(reorderPinned(tabs, 'c', 0), [{id: 'c', sortOrder: 0}]);
  assert.deepEqual(reorderPinned(tabs, 'a', 2), [{id: 'a', sortOrder: 4}]);
  assert.deepEqual(reorderPinned(tabs, 'a', 1), [{id: 'a', sortOrder: 2.5}]);
  assert.deepEqual(reorderPinned(tabs, 'b', 1), []);
  assert.deepEqual(reorderPinned(tabs, 'nope', 0), []);
  // A tab without an order would sort after every ordered one: number them all instead.
  const loose = [view({id: 'a', sortOrder: 1}), view({id: 'b', sortOrder: null}), view({id: 'c', sortOrder: null})];
  const writes = reorderPinned(loose, 'a', 2);
  const after = pinnedTabs(loose.map(v => ({...v, pinned: true,
    sortOrder: writes.find(w => w.id === v.id)?.sortOrder ?? v.sortOrder})));
  assert.deepEqual(after.map(v => v.id), ['b', 'c', 'a']);
});

test('unsaved view edits belong to one person', () => {
  const store = new Map();
  globalThis.localStorage = {
    getItem: k => store.has(k) ? store.get(k) : null,
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
  };
  saveViewEdits('v1', {filters: {priority: {eq: 1}}}, 'ada');
  assert.deepEqual(loadViewEdits('v1', 'ada'), {filters: {priority: {eq: 1}}});
  assert.equal(loadViewEdits('v1', 'bob'), null);
  saveViewEdits('v1', null, 'ada');
  assert.equal(loadViewEdits('v1', 'ada'), null);
  assert.equal(store.size, 0);
  delete globalThis.localStorage;
});

test('unsaved edits compare documents, not key order', () => {
  assert.ok(sameDocument({and: [{a: 1, b: 2}]}, {and: [{b: 2, a: 1}]}));
  assert.ok(!sameDocument({priority: {is: 1}}, {priority: {is: 2}}));
  assert.ok(sameDocument(undefined, {}));
  assert.deepEqual(displayChanges({grouping: 'assignee', ordering: 'priority', collapsedGroups: ['x']},
    {grouping: 'status', ordering: 'priority'}), {grouping: 'assignee'});
});

test('who may edit a view, and which icons are emoji', () => {
  assert.ok(canEditView({owner: {id: 'u'}}, {id: 'u'}));
  assert.ok(canEditView({owner: {id: 'u'}}, {id: 'a', admin: true}));
  assert.ok(!canEditView({owner: {id: 'u'}}, {id: 'b'}));
  assert.ok(!canEditView({owner: {id: 'u'}}, undefined));
  assert.ok(isEmojiIcon('🐛') && !isEmojiIcon('Bug') && !isEmojiIcon(null));
  assert.equal(VIEW_COLORS.length, 9);
});

test('routes: a saved view page, the views list, project and team views', () => {
  assert.equal(routeView('/workspace/view/bugs-1a2b'), 'View');
  assert.equal(routeView('/acme/view/bugs-1a2b'), 'View');
  assert.equal(routeView('/workspace/views/issues'), 'Views');
  assert.equal(routeView('/workspace/views/issues/new'), 'Views');
  assert.equal(routeView('/workspace/project/p1/view/new'), 'Project view');
  assert.equal(routeView('/workspace/team/eng/views/issues'), 'Team views');
});

async function testAsync(name, fn) {
  try {
    await fn();
    passed += 1;
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}\n  ${error.message.split('\n').join('\n  ')}`);
  }
}

await testAsync('the shared views list never outlives the person or workspace it was read for', async () => {
  let who = 'ada@acme', calls = 0;
  const lists = {
    'ada@acme': [view({id: 'secret', name: 'Ada private', visibility: 'private'}), view({id: 'i', kind: 'initiative'})],
    'bob@acme': [view({id: 'b1', name: 'Bob'})],
  };
  let release;
  const cache = createViewListCache(() => { calls += 1; const rows = lists[who];
    return who === 'slow' ? new Promise(r => { release = () => r(rows); }) : Promise.resolve(rows); });
  cache.setIdentity('acme:ada');
  assert.deepEqual((await cache.load()).map(v => v.id), ['secret'], 'initiative views have no page: left out');
  await cache.load();
  assert.equal(calls, 1, 'one shared request');
  // Signing in as someone else without a reload drops the list at once.
  who = 'bob@acme';
  assert.ok(cache.setIdentity('acme:bob'));
  assert.deepEqual(cache.last(), []);
  assert.deepEqual((await cache.load()).map(v => v.id), ['b1']);
  assert.ok(!cache.setIdentity('acme:bob'), 'same identity keeps the cache');
  // A read started before a change never becomes the cached list.
  who = 'slow'; lists.slow = [view({id: 'old'})];
  cache.invalidate();
  const pending = cache.load();
  cache.setIdentity('other:bob');
  release();
  await pending;
  assert.deepEqual(cache.last(), []);
});


/* ------------------------------------------------------------- manual order (views depth) */

test('ranking: keys stay strictly between and sorted under random inserts', () => {
  let seed = 7;
  const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
  const keys = [keyBetween(null, null)];
  for (let i = 0; i < 1500; i++) {
    const at = rnd(keys.length + 1);
    const a = at > 0 ? keys[at - 1] : null, b = at < keys.length ? keys[at] : null;
    const k = keyBetween(a, b);
    validateRank(k);
    assert.ok((a === null || a < k) && (b === null || k < b), `${a} < ${k} < ${b}`);
    keys.splice(at, 0, k);
  }
  assert.deepEqual([...keys].sort(), keys);
});

test('ranking: the browser and the server compute the same keys', () => {
  const py = new URL('../../.venv/bin/python', import.meta.url).pathname;
  if (!existsSync(py)) { console.log('  (skipped: no app/.venv)'); return; }
  let seed = 11;
  const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
  const keys = [keyBetween(null, null)], pairs = [];
  for (let i = 0; i < 400; i++) {
    const at = rnd(keys.length + 1);
    const a = at > 0 ? keys[at - 1] : null, b = at < keys.length ? keys[at] : null;
    pairs.push([a, b]);
    keys.splice(at, 0, keyBetween(a, b));
  }
  for (let i = 0; i < 50; i++) { pairs.push([null, keys[0]]); keys.unshift(keyBetween(null, keys[0])); }
  const out = execFileSync(py, ['-c', 'import json,sys\nfrom hoja_server.ranking import key_between\nprint(json.dumps([key_between(a,b) for a,b in json.load(sys.stdin)]))'],
    {cwd: new URL('../..', import.meta.url).pathname, input: JSON.stringify(pairs)});
  assert.deepEqual(JSON.parse(out), pairs.map(([a, b]) => keyBetween(a, b)));
});

test('manual ordering follows the rank; unranked tasks last, newest first', () => {
  const t = (id, rank, createdAt = '2026-11-01T00:00:00Z') => ({id, identifier: id, title: id, rank, createdAt});
  const got = sortTasks([t('c', 'a2'), t('n1', null, '2026-11-02T00:00:00Z'), t('a', 'Zz'), t('b', 'a0'),
                         t('n2', null, '2026-11-03T00:00:00Z')], {ordering: 'manual'});
  assert.deepEqual(got.map(x => x.id), ['a', 'b', 'c', 'n2', 'n1']);
  assert.equal(compareRank('a0', 'B0') > 0, true, 'byte-wise: lower-case sorts after upper-case');
});

test('priority ordering breaks ties with the priority rank', () => {
  const t = (id, priority, priorityRank) => ({id, identifier: id, title: id, priority, priorityRank});
  const got = sortTasks([t('x', 2, 'a3'), t('y', 2, 'a1'), t('u', 1, 'a9'), t('z', 0, 'a0')], {ordering: 'priority'});
  assert.deepEqual(got.map(x => x.id), ['u', 'y', 'x', 'z']);
});

test('a label group filter matches tasks carrying one of its labels', () => {
  const group = {id: 'g', name: 'area'};
  const t = (id, labels) => ({id, identifier: id, title: id, labels: {nodes: labels}});
  const tasks = [t('A', [{id: 'api', name: 'api', parent: group}, {id: 'bug', name: 'Bug'}]),
                 t('B', [{id: 'ui', name: 'ui', parent: group}]), t('C', [{id: 'bug', name: 'Bug'}]), t('D', [])];
  const pick = (f) => tasks.filter(x => matchesFilter(f, x)).map(x => x.id);
  assert.deepEqual(pick({labels: {includesAny: ['g']}}), ['A', 'B']);
  assert.deepEqual(pick({labels: {includesAll: ['g', 'bug']}}), ['A']);
  assert.deepEqual(pick({labels: {includesNone: ['area']}}), ['C', 'D']);
  assert.equal(countBy(tasks, 'labels').get('g'), 2);
});


test('planDrop: neighbours inside a group and across groups', () => {
  const groups = [{key: 'Todo', ids: ['t1', 't2', 't3']}, {key: 'Backlog', ids: ['b1', 'b2']}];
  assert.deepEqual(planDrop(groups, 't3', 'Todo', 0), {id: 't3', groupKey: 'Todo', fromGroupKey: 'Todo', afterId: null, beforeId: 't1'});
  assert.deepEqual(planDrop(groups, 'b1', 'Todo', 1), {id: 'b1', groupKey: 'Todo', fromGroupKey: 'Backlog', afterId: 't1', beforeId: 't2'});
  assert.equal(planDrop(groups, 't2', 'Todo', 1), null, 'dropped where it was');
  assert.deepEqual(planDrop(groups, 't1', 'Backlog', 9), {id: 't1', groupKey: 'Backlog', fromGroupKey: 'Todo', afterId: 'b2', beforeId: null});
});

test('planKey: Alt+arrows step, cross group edges, and Alt+Shift goes to the edge', () => {
  const groups = [{key: 'Todo', ids: ['t1', 't2']}, {key: 'Backlog', ids: ['b1', 'b2']}];
  assert.deepEqual(planKey(groups, 't2', -1), {id: 't2', groupKey: 'Todo', fromGroupKey: 'Todo', afterId: null, beforeId: 't1'});
  assert.deepEqual(planKey(groups, 't1', 1), {id: 't1', groupKey: 'Todo', fromGroupKey: 'Todo', afterId: 't2', beforeId: null});
  // Linear stops here (JOBS.md); Hoja carries the task into the next group.
  assert.deepEqual(planKey(groups, 'b1', -1), {id: 'b1', groupKey: 'Todo', fromGroupKey: 'Backlog', afterId: 't2', beforeId: null});
  assert.deepEqual(planKey(groups, 't2', 1), {id: 't2', groupKey: 'Backlog', fromGroupKey: 'Todo', afterId: null, beforeId: 'b1'});
  assert.equal(planKey(groups, 't1', -1), null, 'nothing above the first group');
  assert.deepEqual(planKey(groups, 'b2', -1, true), {id: 'b2', groupKey: 'Backlog', fromGroupKey: 'Backlog', afterId: null, beforeId: 'b1'});
  assert.equal(planKey(groups, 'b2', 1, true), null, 'already at the bottom');
});

test('groupPatch: the field a drop into a group sets', () => {
  const task = {labels: {nodes: [{id: 'api', parent: {id: 'area'}}, {id: 'bug'}]}};
  assert.deepEqual(groupPatch('status', 'Todo', task, {stateIdForName: n => (n === 'Todo' ? 's-todo' : undefined)}), {stateId: 's-todo'});
  assert.deepEqual(groupPatch('assignee', 'none', task, {}), {assigneeId: null});
  assert.deepEqual(groupPatch('priority', '2', task, {}), {priority: 2});
  assert.deepEqual(groupPatch('labelGroup', 'ui', task, {labelGroupId: 'area'}), {labelIds: ['bug', 'ui']});
  assert.equal(groupPatch('labels', 'x', task, {}), null, 'a label grouping has no single target');
  assert.deepEqual(groupPatch('none', 'all', task, {}), {});
});

test('optimisticRank: between the visible neighbours, tolerant of stale order', () => {
  const ranks = {a: 'a0', b: 'a2', c: 'a1'};
  const r = optimisticRank({id: 'x', groupKey: 'g', fromGroupKey: 'g', afterId: 'a', beforeId: 'b'}, id => ranks[id]);
  assert.ok('a0' < r && r < 'a2');
  const stale = optimisticRank({id: 'x', groupKey: 'g', fromGroupKey: 'g', afterId: 'b', beforeId: 'c'}, id => ranks[id]);
  assert.ok(stale > 'a2', 'after wins when before is out of order');
  assert.equal(optimisticRank({id: 'x', groupKey: 'g', fromGroupKey: 'g', afterId: 'zz', beforeId: null}, () => null), undefined);
});

console.log(`${passed} passed, ${failures} failed`);
if (failures) process.exit(1);
