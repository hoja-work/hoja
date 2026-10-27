// My issues loading audit against a real seeded server. Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-my-issues-completed.cjs   (WIDTH=390 HEIGHT=844 for a phone viewport)
// Checks: the sync asks for a year of closed issues; Assigned shows done/cancelled work per the
// "Completed issues" display option; "All" fetches older closed issues scoped to the viewer (never other
// people's); Created picks up the viewer's older closed issues; there is no "Load more issues" button;
// long lists are windowed (fewer rows mounted than exist) yet the last row is reachable by scrolling.
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
const dir = fs.mkdtempSync(path.join(scratch, 'my-issues-completed-'));
const db = path.join(dir, 'my.db');
const token = 'cliniar_my_issues_completed_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from cliniar_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19200 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const BULK = 260;

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const done = team.states.nodes.find(s => s.type === 'completed').id;
  const todo = team.states.nodes.find(s => s.type === 'unstarted').id;
  const create = (title, input) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title, teamId: team.id, ...input } });
  await create('Open mine', { assigneeId: me, stateId: todo });
  for (const t of ['Done 3d', 'Done 20d', 'Done 400d']) await create(t, { assigneeId: me, stateId: done });
  await create('Done 400d unassigned', { stateId: done });
  for (let i = 0; i < BULK; i++) await create(`Bulk open ${i}`, { assigneeId: me, stateId: todo });
  // Backdate the closed issues (before any bootstrap, so no server snapshot cache holds the old values).
  run(`import sqlite3, datetime
c=sqlite3.connect(${JSON.stringify(db)}); now=datetime.datetime.now(datetime.timezone.utc)
for title,days in [('Done 3d',3),('Done 20d',20),('Done 400d',400),('Done 400d unassigned',400)]:
    ts=(now-datetime.timedelta(days=days)).isoformat()
    assert c.execute('update issue set updated_at=?, state_changed_at=? where title=?',(ts,ts,title)).rowcount==1, title
c.commit()`);

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: Number(process.env.WIDTH || 1440), height: Number(process.env.HEIGHT || 900) } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const requests = [];
    page.on('request', r => requests.push({ url: r.url(), body: r.postData() || '' }));
    const shown = async title => (await page.locator(`button[aria-label$=": ${title}"]`).count()) > 0;
    await page.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.my-issue-row');

    const boot = requests.find(r => r.url.includes('/sync/bootstrap'));
    ok(Boolean(boot && /closedDays=365/.test(boot.url)), 'bootstrap requests a 365-day closed window', boot?.url.replace(base, ''));
    ok((await page.getByText('Load more issues').count()) === 0, 'no "Load more issues" button on Assigned');
    // Narrow to the closed fixtures so they render even though the long list below is windowed.
    await page.getByRole('button', { name: 'Add filter' }).click();
    await page.getByPlaceholder('Title or ID').fill('Done');
    await wait(300);
    ok(await shown('Done 3d'), 'default "Past week": issue closed 3 days ago is shown');
    ok(!(await shown('Done 20d')), 'default "Past week": issue closed 20 days ago is hidden');

    await page.getByRole('button', { name: 'Display options' }).click();
    const sel = page.getByLabel('Completed issues');
    await sel.selectOption('month'); await wait(200);
    ok(await shown('Done 20d'), '"Past month": issue closed 20 days ago is shown');
    ok(!(await shown('Done 400d')), '"Past month": issue closed 400 days ago is hidden');

    await sel.selectOption('all');
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.getAttribute('aria-label')?.endsWith(': Done 400d')), null, { timeout: 10000 }).catch(() => {});
    ok(await shown('Done 400d'), '"All": issue closed 400 days ago (outside the sync window) is fetched and shown');
    const older = requests.filter(r => r.body.includes('MyOlderClosedIssues'));
    ok(older.length > 0 && older.every(r => JSON.parse(r.body).variables.filter.assignee?.id?.eq === me), 'older fetch is scoped to the viewer as assignee', `${older.length} request(s)`);
    ok(!(await shown('Done 400d unassigned')), '"All": someone else\'s old closed issue is not shown');
    ok(await page.evaluate(() => localStorage.getItem('hoja_my_issues_completed')) === 'all', 'choice is remembered in localStorage');

    await sel.selectOption('none'); await wait(200);
    ok(!(await shown('Done 3d')) && !(await shown('Done 400d')), '"None": no done issues on Assigned');
    await page.getByRole('button', { name: 'Display options' }).click();
    await page.getByRole('button', { name: 'Add filter' }).click();
    await page.getByPlaceholder('Title or ID').fill('');
    await page.getByRole('button', { name: 'Add filter' }).click();
    await wait(300);

    // Windowing: BULK+1 open rows, so far fewer should be mounted; the last row must be reachable.
    const total = BULK + 1;
    const mounted = await page.locator('.my-issue-row').count();
    ok(mounted < total, 'long list is windowed', `${mounted} of ${total} rows mounted`);
    ok(!(await shown('Open mine')), 'last row is not mounted before scrolling');
    await page.locator('.my-issues-list').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await wait(400);
    await page.locator('.my-issues-list').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await wait(400);
    const last = page.locator('button[aria-label$=": Open mine"]');
    const visible = (await last.count()) > 0 && await last.evaluate(el => { const r = el.getBoundingClientRect(); const l = document.querySelector('.my-issues-list').getBoundingClientRect(); return r.top >= l.top - 1 && r.bottom <= l.bottom + 1; });
    ok(visible, 'last row is mounted and inside the list viewport after scrolling to the bottom');
    const gap = await page.locator('.my-issues-list').evaluate(el => { const rows = [...el.querySelectorAll('.my-issue-row')]; const lastRow = rows[rows.length - 1].getBoundingClientRect(); return Math.round(el.scrollHeight - el.scrollTop - (lastRow.bottom - el.getBoundingClientRect().top)); });
    ok(Math.abs(gap) < 120, 'no large blank space after the last row (spacer heights match real rows)', `${gap}px`);

    await page.getByRole('tab', { name: 'Created' }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.getAttribute('aria-label')?.endsWith(': Done 400d unassigned')), null, { timeout: 10000 }).catch(() => {});
    ok(await shown('Done 400d unassigned'), 'Created: the viewer\'s own old closed issue is fetched (creator-scoped)');
    ok((await page.getByText('Load more issues').count()) === 0, 'no "Load more issues" button on Created');
    ok(!requests.some(r => r.body.includes('query MoreIssues')), 'the old workspace-wide MoreIssues query is never sent');
  } finally {
    await browser.close();
    server.kill();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
