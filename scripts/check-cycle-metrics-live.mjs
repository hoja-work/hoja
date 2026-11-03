// Reconcile one real cycle's numbers against the workspace itself.
//   LINEAR_API_URL=https://host/graphql LINEAR_TOKEN=... node scripts/check-cycle-metrics-live.mjs [cycleNumber]
// Defaults to the most recently completed cycle of the first team. Prints what
// the UI used to show (completed/scope) next to what it shows now, and fails if
// the buckets do not add up to the scope the server returned.
import assert from 'node:assert/strict';
import {asPct, cycleStats, deliveryRate, dropoutRate, scopeCompletion} from '../frontend/src/linear/cycleMetrics.ts';

const url = process.env.LINEAR_API_URL;
const token = process.env.LINEAR_TOKEN;
if (!url || !token) {
  console.log('skipped: set LINEAR_API_URL and LINEAR_TOKEN to reconcile against a live workspace');
  process.exit(0);
}

async function gql(query, variables = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {Authorization: token, 'Content-Type': 'application/json'},
    body: JSON.stringify({query, variables}),
  });
  const body = await res.json();
  if (body.errors) throw new Error(JSON.stringify(body.errors));
  return body.data;
}

const want = process.argv[2] ? Number(process.argv[2]) : null;
const {teams} = await gql('{ teams(first:5){ nodes { id key name } } }');
const team = teams.nodes[0];
const {cycles} = await gql('{ cycles(first:50){ nodes { id number name startsAt endsAt completedAt team { id } } } }');
const mine = cycles.nodes.filter(c => c.team?.id === team.id);
const cycle = want
  ? mine.find(c => c.number === want)
  : [...mine].filter(c => c.completedAt).sort((a, b) => (b.startsAt || '').localeCompare(a.startsAt || ''))[0];
if (!cycle) throw new Error(`no cycle ${want ?? '(completed)'} in team ${team.key}`);

const issues = [];
let after = null;
for (let page = 0; page < 20; page++) {
  const d = await gql(`query($after:String){ issues(first:100, after:$after, filter:{cycle:{id:{eq:"${cycle.id}"}}})
      { nodes { identifier state{type} } pageInfo{hasNextPage endCursor} } }`, {after});
  issues.push(...d.issues.nodes);
  if (!d.issues.pageInfo.hasNextPage) break;
  after = d.issues.pageInfo.endCursor;
}

const s = cycleStats(issues);
assert.equal(s.scope, issues.length);
assert.equal(s.completed + s.canceled + s.open, s.scope, 'buckets must cover the scope');

const row = (label, v) => console.log(`${label.padEnd(26)} ${v}`);
console.log(`${team.key} ${cycle.name} (${(cycle.startsAt || '').slice(0, 10)} → ${(cycle.endsAt || '').slice(0, 10)})`);
row('issues in scope', s.scope);
row('delivered (completed)', `${s.completed} · ${asPct(scopeCompletion(s))}% of scope`);
row('canceled', `${s.canceled} · ${asPct(dropoutRate(s))}% of scope`);
row('open', s.open);
console.log('');
row('old UI number', `${asPct(scopeCompletion(s))}% success (completed / scope)`);
row('delivery of kept work', s.kept ? `${asPct(deliveryRate(s))}% (completed / ${s.kept} kept)` : 'n/a (nothing kept)');
