// Project Activity panel audit against a real seeded server (110+ events, 3 health updates 10 days old).
// Run from the repo root after `cd frontend && npm run build`: node scripts/test-project-activity.cjs (SHOTS=dir).
// Every check compares the panel with GraphQL ground truth, not with what the UI computed.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'project-activity-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_project_activity_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);
const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const shots = process.env.SHOTS;
const FEED = 'query($id:String!,$after:String,$types:[String!]){project(id:$id){activity(first:100,after:$after,eventTypes:$types){nodes{eventType changes} pageInfo{hasNextPage endCursor}}}}';
const allEvents = async (id, types) => { let out = [], after = null; do { const p = (await gql(FEED, { id, after, types })).project.activity; out = out.concat(p.nodes); after = p.pageInfo.hasNextPage ? p.pageInfo.endCursor : null; } while (after); return out; };

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id name} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const st = t => team.states.nodes.find(s => s.type === t).id;
  const mkProject = async name => (await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}', { i: { name, teamIds: [team.id] } })).projectCreate.project.id;
  const projectId = await mkProject('Launch'), emptyId = await mkProject('Empty'), otherId = await mkProject('Other');
  for (const h of ['on track', 'at risk', 'off track']) await gql('mutation($i:ProjectUpdateCreateInput!){projectUpdateCreate(input:$i){success}}', { i: { projectId, body: `Health is ${h}`, status: h } });
  // Back-date the updates 10 days so the panel must page past 110 newer events to reach them.
  const r = spawnSync(py, ['-c', `import sqlite3;c=sqlite3.connect(${JSON.stringify(db)});c.execute("update project_update set created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','-10 days','+'||rowid||' minutes')");c.commit()`], { encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr);
  const create = async (title, project, stateId) => (await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier}}}', { i: { title, teamId: team.id, projectId: project, stateId } })).issueCreate.issue;
  const upd = (id, i) => gql('mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}', { id, i });
  const issues = [];
  for (let i = 0; i < 60; i += 10) issues.push(...await Promise.all(Array.from({ length: 10 }, (_, k) => create(`Task ${i + k + 1}`, projectId, st('unstarted')))));
  for (let i = 0; i < 45; i += 15) await Promise.all(issues.slice(i, i + 15).map(x => upd(x.id, { stateId: st('completed') })));
  await upd(issues[50].id, { assigneeId: me });
  for (const x of issues.slice(51, 54)) await gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', { i: { issueId: x.id, body: 'looks good' } });
  const stray = await create('Stray', null, st('backlog'));
  await upd(stray.id, { projectId });
  await create('Not ours', otherId, st('unstarted'));
  const truth = await allEvents(projectId);
  const count = t => truth.filter(e => e.eventType === t).length;

  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage(); page.on('pageerror', e => errors.push(e.message));
  const open = async (id = projectId) => { await page.goto(`${base}/workspace/project/${id}/activity`, { waitUntil: 'networkidle' }); await page.waitForTimeout(400); };
  const loadAll = async () => { for (let i = 0; i < 20; i++) { const b = page.locator('.activity-more'); if (!(await b.count())) break; await b.click(); await page.waitForTimeout(300); } };
  // Logical events: a collapsed burst stands for data-count events.
  const logical = sel => page.locator(sel).evaluateAll(els => els.reduce((n, e) => n + Number(e.getAttribute('data-count') || 1), 0));
  const entries = () => logical('.activity-entry');

  await open();
  if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: path.join(shots, 'activity-1440.png'), fullPage: true }); }
  // 9. Merged paging: the 10-day-old updates must not appear above newer, not-yet-loaded events.
  ok(await page.locator('.activity-more').isVisible(), 'F9 paging: "Load older activity" while more exists', `${truth.length} events on server`);
  ok(await page.locator('.activity-update').count() === 0, 'F9 paging: older updates held back until newer events are loaded');
  await loadAll();
  ok(await page.locator('.activity-end').isVisible(), 'F9 paging: end-of-feed message after loading everything');
  // 1. Unified timeline: every server event sentence + all 3 updates, newest first.
  const eventEntries = await logical('.activity-event'), updateEntries = await page.locator('.activity-update').count();
  ok(updateEntries === 3, 'F1 timeline: all 3 project updates merged in', String(updateEntries));
  const sentenceCount = truth.reduce((n, e) => n + (e.eventType === 'issueUpdated' ? Object.keys(e.changes || {}).filter(k => ['stateId', 'assigneeId', 'priority', 'projectId', 'title', 'description'].includes(k)).length || 1 : 1), 0);
  ok(eventEntries === sentenceCount, 'F1 timeline: one line per server change', `${eventEntries} vs ${sentenceCount}`);
  const times = await page.locator('.activity-entry time').evaluateAll(ts => ts.map(t => t.getAttribute('datetime')));
  ok(times.every((t, i) => i === 0 || times[i - 1] >= t), 'F1 timeline: newest first');
  ok(!(await page.locator('.activity-feed').innerText()).includes('Not ours'), 'F1 timeline: other projects excluded');
  // 2. Day groups.
  const days = await page.locator('.activity-day > h3').allInnerTexts();
  ok(days[0]?.toUpperCase() === 'TODAY' && days.length >= 2, 'F2 day groups: Today first, older day separate', days.join(' | '));
  // 4b. Bursts: the bulk move of 45 issues is one expandable line, not 45.
  const burst = page.locator('.activity-burst', { hasText: 'moved 45 issues' });
  ok(await burst.count() === 1 && await burst.getAttribute('data-count') === '45', 'F4 bursts: 45 bulk moves collapse into one line', String(await page.locator('.activity-burst summary').allInnerTexts()).replace(/\s+/g, ' ').slice(0, 120));
  ok(await page.locator('.activity-burst', { hasText: 'created 60 issues' }).count() === 1, 'F4 bursts: 60 creations collapse into one line');
  await burst.locator('summary').click();
  ok(await burst.locator('.activity-burst-item').count() === 45, 'F4 bursts: expanding shows every issue');
  await page.locator('.activity-burst summary').evaluateAll(ss => ss.forEach(s => s.closest('details').open = true));
  // 4. Readable sentences.
  const feed = await page.locator('.activity-feed').innerText();
  ok(/moved \S+ Task \d+ from\s*Todo\s*to\s*Done/.test(feed), 'F4 sentences: status move names both states');
  ok(new RegExp(`assigned \\S+ Task 51 to ${d.viewer.name}`).test(feed), 'F4 sentences: assignment names the person');
  ok(/added \S+ Stray to this project/.test(feed), 'F4 sentences: issue added to the project (projectId change recorded)');
  ok(/commented on \S+ Task 52/.test(feed), 'F4 sentences: comments');
  // 10. Time.
  const timeOk = await page.locator('.activity-entry time').evaluateAll(ts => ts.every(t => t.getAttribute('datetime') && t.getAttribute('title')));
  ok(timeOk, 'F10 time: every entry has machine datetime + exact-time tooltip');
  // 6. Health trend and staleness.
  ok((await page.locator('.activity-head .activity-health-pill').innerText()).toLowerCase() === 'off track', 'F6 health: current health is the latest update');
  ok(await page.locator('.activity-trend').getAttribute('aria-label') === 'Health of the last 3 updates: on track, at risk, off track', 'F6 health: trend oldest→newest', await page.locator('.activity-trend').getAttribute('aria-label'));
  ok(await page.locator('.activity-health [data-stale]').count() === 1, 'F6 health: 10-day-old update flagged as stale');
  // 3. Filters (server-side event types, compared with server counts).
  await page.getByRole('button', { name: 'Comments', exact: true }).click(); await page.waitForTimeout(400); await loadAll();
  ok(await entries() === count('commentCreated') && await logical('.activity-event[data-type=commentCreated]') === count('commentCreated'), 'F3 filter: Comments matches server', `${await entries()} vs ${count('commentCreated')}`);
  await page.getByRole('button', { name: 'New issues', exact: true }).click(); await page.waitForTimeout(400); await loadAll();
  ok(await entries() === count('issueCreated'), 'F3 filter: New issues matches server (paged past 50)', `${await entries()} vs ${count('issueCreated')}`);
  await page.getByRole('button', { name: 'Status changes', exact: true }).click(); await page.waitForTimeout(400); await loadAll();
  const statusTruth = truth.filter(e => e.changes?.stateId).length;
  ok(await entries() === statusTruth, 'F3 filter: Status changes matches server', `${await entries()} vs ${statusTruth}`);
  await page.getByRole('button', { name: 'Updates', exact: true }).click(); await page.waitForTimeout(300);
  ok(await page.locator('.activity-update').count() === 3 && await page.locator('.activity-event').count() === 0, 'F3 filter: Updates shows only updates');
  ok(await page.getByRole('button', { name: 'Updates', exact: true }).getAttribute('aria-pressed') === 'true', 'F3 filter: pressed state exposed');
  // 7. Composer: keyboard submit, health choice, persisted on the server.
  ok(await page.locator('.activity-composer .primary').isDisabled(), 'F7 composer: Post disabled when empty');
  ok(await page.getByRole('radio', { name: 'Off track' }).isChecked(), 'F7 composer: health defaults to the latest update');
  await page.getByRole('radio', { name: 'At risk' }).check();
  await page.locator('#activity-body').fill('Blocked on vendor review');
  await page.locator('#activity-body').press('Control+Enter');
  await page.waitForTimeout(800);
  const srvUpdates = (await gql('query($id:ID!){projectUpdates(filter:{project:{id:{eq:$id}}}){nodes{body status}}}', { id: projectId })).projectUpdates.nodes;
  ok(srvUpdates.some(u => u.body === 'Blocked on vendor review' && u.status === 'at risk'), 'F7 composer: Ctrl+Enter posts with the chosen health');
  ok((await page.locator('.activity-update').first().innerText()).includes('Blocked on vendor review'), 'F7 composer: new update at the top without reload');
  ok(await page.locator('#activity-body').inputValue() === '', 'F7 composer: field cleared after posting');
  ok((await page.locator('.activity-head .activity-health-pill').innerText()).toLowerCase() === 'at risk' && await page.locator('.activity-health [data-stale]').count() === 0, 'F6 health: header follows the new update');
  // 5. Issue links open the issue.
  await page.getByRole('button', { name: 'All', exact: true }).click(); await page.waitForTimeout(300);
  await page.locator('.activity-issue').first().click(); await page.waitForTimeout(500);
  ok(/\/issue\//.test(page.url()), 'F5 issue links: clicking an issue opens it', page.url());
  // 8. New since last visit: two events from another client after this visit.
  await gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', { i: { issueId: issues[0].id, body: 'new one' } });
  await upd(issues[46].id, { stateId: st('started') });
  await open();
  ok((await page.locator('.activity-new-count').innerText().catch(() => '')) === '2 new', 'F8 new since last visit: count', await page.locator('.activity-new-count').innerText().catch(() => 'none'));
    ok(await page.locator('.activity-entry[data-new]').count() === 2 && await page.locator('.activity-divider').count() === 1, 'F8 new since last visit: highlighted + divider');
  // States: empty project; phones.
  await open(emptyId);
  ok(await page.locator('.activity-empty').isVisible(), 'empty state on a project with no activity');
  for (const [W, H] of [[390, 844], [320, 640]]) {
    await page.setViewportSize({ width: W, height: H }); await open();
    const hs = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    if (shots) await page.screenshot({ path: path.join(shots, `activity-${W}.png`), fullPage: true });
    ok(!hs, `${W}px: no horizontal scroll`);
  }
  ok(errors.length === 0, 'no page errors', errors.join(' | '));
  await browser.close();
  server.kill();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
