// Proves the My issues → Activity panel is live (HOJA-164): an event written by a second user through a
// separate client shows up without a reload, survives a socket drop without duplicating or losing rows,
// and the panel's rows match myIssueActivity ground truth.
// Run from app/ after `cd frontend && npm run build`: node scripts/test-activity-realtime.cjs (SHOTS=dir).
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = process.env.SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'activity-realtime-'));
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'run-'));
const db = path.join(dir, 'a.db');
const TOKEN = 'hoja_activity_me_token';
const TOKEN_OTHER = 'hoja_activity_other_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
};

// One tenant, viewer Ada with a token; a second human Grace with a token so activity has a real other actor.
run(`
from hoja_server.db import make_engine, migrate, seed_tenant, user, api_key, team_member, token_hash, new_id, now_iso
e = make_engine(${JSON.stringify(db)}); migrate(e)
s = seed_tenant(e, org_name='Acme', team_key='HOJ', team_name='Hoja', user_name='Ada Lovelace',
                user_email='ada@example.test', token=${JSON.stringify(TOKEN)}, demo_issues=False)
ts = now_iso(); uid = new_id()
with e.begin() as c:
    c.execute(user.insert().values(id=uid, organization_id=s['org_id'], name='Grace Hopper', display_name='Grace Hopper',
        email='grace@example.test', active=True, admin=False, timezone='UTC', created_at=ts, updated_at=ts))
    c.execute(team_member.insert().values(team_id=s['team_id'], user_id=uid, role='member'))
    c.execute(api_key.insert().values(id=new_id(), token_hash=token_hash(${JSON.stringify(TOKEN_OTHER)}), label='other',
        user_id=uid, organization_id=s['org_id'], created_at=ts))
e.dispose()
`);

const port = 19600 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'),
  ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const api = async (token, query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const shots = process.env.SHOTS;

(async () => {
  for (let i = 0; i < 80; i++) { try { await api(TOKEN, '{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await api(TOKEN, '{viewer{id name} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const state = t => team.states.nodes.find(s => s.type === t).id;
  const made = await api(TOKEN, 'mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier}}}',
    { i: { title: 'Live activity probe', teamId: team.id, assigneeId: me } });
  const issue = made.issueCreate.issue;
  ok(Boolean(issue.identifier), 'fixture issue created by the viewer', issue.identifier);
  // myIssueActivity ground truth, paged the same way the panel pages it.
  const truth = async () => {
    let out = [], after = null;
    for (let i = 0; i < 10; i++) {
      const page = (await api(TOKEN, 'query($after:String){myIssueActivity(first:50,after:$after){nodes{id eventType createdAt issue{identifier title}} pageInfo{hasNextPage endCursor}}}', { after })).myIssueActivity;
      out = out.concat(page.nodes);
      if (!page.pageInfo?.hasNextPage) break;
      after = page.pageInfo.endCursor;
    }
    return out;
  };

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), TOKEN);
    const page = await ctx.newPage();
    let navigations = 0;
    page.on('framenavigated', f => { if (f === page.mainFrame()) navigations++; });
    const loadRequests = [];
    page.on('request', r => { if (r.method() === 'POST' && /\/graphql$/.test(new URL(r.url()).pathname)) loadRequests.push(r.postData() || ''); });

    await page.goto(`${base}/workspace/my-issues/activity`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.my-issue-row', { timeout: 20000 });
    await page.evaluate(() => { window.__probeNoReload = 1; });

    const rowText = () => page.$$eval('.my-issue-row', els => els.map(e => e.textContent.replace(/\s+/g, ' ').trim()));
    const countKind = async kind => (await rowText()).filter(t => t.includes(kind)).length;
    const open = await rowText();
    ok(open.some(t => t.includes(issue.identifier) && t.includes('issueCreated')), 'initial page renders the issueCreated event', `${open.length} row(s)`);
    ok((await countKind('commentCreated')) === 0, 'no commentCreated row before the other user comments');

    // 1. Live insert: another client (Grace's token, plain HTTP) writes a comment while the panel is open.
    const t1 = Date.now();
    await api(TOKEN_OTHER, 'mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', { i: { issueId: issue.id, body: 'checked the numbers' } });
    await page.waitForFunction(() => [...document.querySelectorAll('.my-issue-row')].some(e => /commentCreated/.test(e.textContent)), null, { timeout: 15000 });
    const liveMs = Date.now() - t1;
    ok(true, 'a comment by another user appears without a reload', `${liveMs}ms after the write`);
    ok(await page.evaluate(() => window.__probeNoReload) === 1, 'the page was never reloaded');

    // 2. Socket drop: go offline, have Grace change the issue, come back online and let the client resume.
    await ctx.setOffline(true);
    await wait(1500);
    ok(await countKind('issueUpdated') === 0, 'no issueUpdated row while the socket is down and nothing was written yet');
    await api(TOKEN_OTHER, 'mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}', { id: issue.id, i: { stateId: state('started') } });
    await wait(800);
    ok(await countKind('issueUpdated') === 0, 'the offline state change is not in the panel yet (socket is down)');
    await page.screenshot({ path: path.join(shots || dir, 'activity-offline.png') }).catch(() => {});
    await ctx.setOffline(false);
    const t2 = Date.now();
    await page.waitForFunction(() => [...document.querySelectorAll('.my-issue-row')].some(e => /issueUpdated/.test(e.textContent)), null, { timeout: 30000 });
    ok(true, 'the change made while offline arrives after reconnecting', `${Date.now() - t2}ms after going back online`);
    await wait(2500); // give a duplicate to show up if the replay double-applies
    ok(await countKind('issueUpdated') === 1, 'replay after reconnect does not duplicate the event');
    ok(await countKind('commentCreated') === 1, 'the earlier comment stays exactly once after reconnect');

    // 3. Pagination still works and survives a live refresh: fill more than one page, "Load more
    //    activity" to the end, then let another live event arrive.
    for (let i = 0; i < 60; i++) await api(TOKEN_OTHER, 'mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', { i: { issueId: issue.id, body: `burst ${i + 1}` } });
    await page.waitForFunction(() => document.querySelectorAll('.my-issue-row').length > 3, null, { timeout: 15000 });
    const moreButton = page.locator('.my-issue-load-more');
    ok(await moreButton.count() === 1, 'a full first page shows "Load more activity"');
    let guard = 0;
    while (await moreButton.count() && guard++ < 6) {
      const before = (await rowText()).length;
      await moreButton.click();
      await page.waitForFunction(n => document.querySelectorAll('.my-issue-row').length > n || !document.querySelector('.my-issue-load-more'), before, { timeout: 15000 }).catch(() => {});
      await wait(200);
    }
    const pagedRows = await rowText();
    const pagedTruth = await truth();
    ok(pagedRows.length === pagedTruth.length, 'paging to the end loads every event exactly once', `${pagedRows.length} rendered / ${pagedTruth.length} on the server`);
    ok(await moreButton.count() === 0, 'no "Load more activity" left at the oldest event');
    const oldestFirst = pagedRows.some(t => t.includes('issueCreated'));
    ok(oldestFirst, 'the oldest event (issueCreated) is on screen before the live refresh');

    const t3 = Date.now();
    await api(TOKEN_OTHER, 'mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', { i: { issueId: issue.id, body: 'after paging' } });
    const want = pagedTruth.length + 1;
    await page.waitForFunction(n => document.querySelectorAll('.my-issue-row').length >= n, want, { timeout: 15000 });
    ok(true, 'a live event still lands after paging to the end', `${Date.now() - t3}ms after the write`);
    const afterRows = await rowText();
    ok(afterRows.length === want, 'the live refresh keeps the pages already loaded instead of resetting to page one', `${afterRows.length} rows`);
    ok(afterRows.some(t => t.includes('issueCreated')), 'the oldest event survives the live refresh');
    const afterTruth = await truth();
    ok(afterRows.length === afterTruth.length, 'the live refresh matches ground truth row for row', `${afterRows.length} rendered / ${afterTruth.length} on the server`);

    // 4. The panel agrees with the server, row for row, and has no duplicate rows.
    const rows = await rowText();
    const server = await truth();
    ok(rows.length === server.length, 'rendered rows equal myIssueActivity ground truth', `${rows.length} rendered / ${server.length} on the server`);
    // Rows carry no event id in the DOM, so duplicates are detected as an inflated per-kind count.
    const kinds = ['issueCreated', 'issueUpdated', 'commentCreated'];
    for (const kind of kinds) {
      const shown = rows.filter(t => t.includes(kind)).length;
      const real = server.filter(e => e.eventType === kind).length;
      ok(shown === real, `no duplicate ${kind} rows`, `${shown} rendered / ${real} on the server`);
    }

    // 5. The refresh came from the socket, not from polling loops.
    const activityQueries = loadRequests.filter(b => b.includes('MyIssueActivity')).length;
    ok(activityQueries >= 4, 'the panel refetched its newest page after the realtime events', `${activityQueries} MyIssueActivity request(s)`);
    ok(navigations === 1, 'single navigation for the whole run (no reload, no route change)', `${navigations} navigation(s)`);
    await page.screenshot({ path: path.join(shots || dir, 'activity-live.png') }).catch(() => {});
  } finally {
    await browser.close();
  }

  console.log(`\n${fail ? 'FAILED' : 'OK'}: ${pass} passed, ${fail} failed  (artifacts: ${dir})`);
  server.kill();
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
