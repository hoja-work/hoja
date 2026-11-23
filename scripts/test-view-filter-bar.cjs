// The shared filter bar and Display panel on a team's Tasks page, against a real seeded server.
// Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-view-filter-bar.cjs
// Checks: F opens the type-ahead filter menu and a picked value becomes a chip; the filter is
// mirrored in ?filter= and restored on reload; group headers read "filtered / total" and the
// footer counts rows hidden by filters; the chip's operator is editable; Shift V opens the
// Display panel, a grouping change is stored through viewPreferences and lights the dot, Reset
// clears it; the board hides a column into the "Hidden columns" lane; Alt V opens the view
// editor prefilled with the chips; no My-issues help text leaks into the team page.
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
const dir = fs.mkdtempSync(path.join(scratch, 'view-filter-bar-'));
const db = path.join(dir, 'views.db');
const token = 'hoja_view_filter_bar_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19300 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
const decode = url => {
  const p = new URL(url).searchParams.get('filter');
  return p ? JSON.parse(Buffer.from(p, 'base64url').toString('utf8')) : null;
};

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const todo = team.states.nodes.find(s => s.type === 'unstarted').id;
  const doing = team.states.nodes.find(s => s.type === 'started').id;
  const create = (title, input) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title, teamId: team.id, ...input } });
  await create('Urgent fire', { priority: 1, stateId: todo, assigneeId: me });
  await create('High thing', { priority: 2, stateId: todo });
  await create('Low chore', { priority: 4, stateId: doing });
  await create('Unprioritised idea', { stateId: todo });
  const viewType = `team:${team.id}:all`;
  const prefs = async () => (await gql('query($t:String){viewPreferences(viewType:$t){user effective hasUserOverrides}}', { t: viewType })).viewPreferences;

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: Number(process.env.WIDTH || 1440), height: Number(process.env.HEIGHT || 900) } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const teamUrl = `${base}/workspace/team/${team.key.toLowerCase()}/active`;
    await page.goto(teamUrl, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-my-issue-row]');
    const rows = () => page.locator('[data-my-issue-row]').count();
    ok(await rows() === 4, 'team page lists every task', String(await rows()));
    ok(!(await page.getByText('Group assigned tasks').count()), 'no My-issues help text on the team page');

    // F opens the menu; typing searches values across categories.
    await page.locator('[data-my-issues-head] h1').click();
    await page.keyboard.press('f');
    await page.waitForSelector('[data-filter-menu]');
    ok(true, 'F opens the filter menu');
    await page.keyboard.type('urg');
    await wait(150);
    ok(await page.getByRole('menuitemcheckbox', { name: /Priority: Urgent/ }).count() === 1, 'type-ahead finds "Priority: Urgent"');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Escape');
    await wait(300);
    ok(await page.locator('[data-filter-chip][data-field="priority"]').count() === 1, 'the value becomes a Priority chip');
    ok(await rows() === 1, 'the list is filtered to the urgent task', String(await rows()));
    ok(JSON.stringify(decode(page.url())) === JSON.stringify({ priority: { is: 1 } }), 'the filter is mirrored in ?filter=', page.url().replace(base, ''));
    const counts = await page.locator('[data-my-issue-group-count]').allTextContents();
    ok(counts.some(t => /^1 \/ 3$/.test(t.trim())), 'group header shows filtered / total', counts.join(','));
    ok(/3 tasks hidden by filters/.test(await page.locator('[data-hidden-by-filters]').textContent() || ''), 'footer counts the rows filters hide');

    // A category's per-value counts.
    await page.getByRole('button', { name: 'Add another filter' }).click();
    await page.getByRole('menuitem', { name: 'Assignee' }).click();
    const current = page.getByRole('menuitemcheckbox', { name: /Current user/ });
    ok((await current.textContent()).trim().endsWith('1'), 'menu values carry counts', (await current.textContent()).trim());
    await page.keyboard.press('Escape');
    await wait(150);

    // Reload restores the filter from the URL.
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('[data-filter-chip]');
    ok(await rows() === 1, 'reload restores the filter from the URL');

    // The operator is clickable: "is" → "is not".
    await page.getByRole('button', { name: /Priority operator/ }).click();
    await page.getByRole('menuitemradio', { name: 'is not' }).click();
    await wait(300);
    ok(JSON.stringify(decode(page.url())) === JSON.stringify({ priority: { isNot: 1 } }), 'operator change writes isNot', page.url().replace(base, ''));
    ok(await rows() === 3, 'is not Urgent shows the other three');

    // Shift V: grouping is stored server-side and lights the dot; Reset clears it.
    await page.locator('[data-my-issues-head] h1').click();
    await page.keyboard.press('Shift+V');
    await page.waitForSelector('[data-display-panel]');
    ok(true, 'Shift V opens the Display panel');
    await page.getByLabel('Grouping', { exact: true }).selectOption('priority');
    await wait(900);
    const labels = await page.locator('[data-my-issue-group] header b').allTextContents();
    ok(labels.join(',') === 'High,Low,No priority', 'the list groups by priority in Linear order', labels.join(','));
    ok((await prefs()).effective.grouping === 'priority', 'grouping is stored through viewPreferences');
    ok(await page.locator('[data-display-dot]').count() === 1, 'the Display button shows the changed-from-default dot');
    await page.getByRole('button', { name: 'Reset' }).click();
    await wait(600);
    ok((await prefs()).hasUserOverrides === false, 'Reset drops the user layer');
    ok(await page.locator('[data-display-dot]').count() === 0, 'the dot clears after Reset');

    // Board: hide a column into the lane.
    await page.getByRole('radio', { name: /board/i }).click();
    await page.keyboard.press('Escape');
    await page.waitForSelector('[data-kanban-column]');
    const before = await page.locator('[data-kanban-column]').count();
    await page.locator('[data-kanban-column-menu]').first().click();
    await page.getByRole('menuitem', { name: 'Hide column' }).click();
    await wait(300);
    ok(await page.locator('[data-kanban-column]').count() === before - 1, 'Hide column removes it from the board');
    ok(await page.locator('[data-kanban-hidden-lane]').count() === 1, 'a "Hidden columns" lane lists it');
    await page.locator('[data-kanban-column-menu]').first().click();
    await page.getByRole('menuitem', { name: 'Select all in column' }).click();
    ok(await page.locator('[data-board-selection]').count() === 1, 'Select all in column shows the selection bar');
    await page.keyboard.press('Escape');

    // Alt V hands filters to the view editor.
    await page.locator('[data-my-issues-head] h1').click();
    await page.keyboard.press('Alt+KeyV');
    await page.waitForSelector('[data-view-editor]');
    ok(/\/views\/issues\/new/.test(page.url()), 'Alt V opens the new-view editor', page.url().replace(base, ''));
    ok(await page.locator('[data-view-editor] [data-filter-chip][data-field="priority"]').count() === 1, 'the editor is prefilled with the chips');
    const suggested = await page.locator('[data-view-name-input]').inputValue();
    ok(suggested === 'Tasks without Urgent', 'the new view is named from its chips', suggested);

    // A display edit made just before a tab switch is stored on the tab it was made on.
    const prefsOf = async t => (await gql('query($t:String){viewPreferences(viewType:$t){user}}', { t })).viewPreferences.user || {};
    await page.goto(`${base}/workspace/team/${team.key.toLowerCase()}/active`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-my-issue-tabs], [role=tablist]');
    await page.locator('[data-my-issue-tabs] [role=tab]').nth(0).click();
    await page.locator('[data-my-issues-head] h1').click();
    await page.keyboard.press('Shift+V');
    await page.waitForSelector('[data-display-panel]');
    await page.getByLabel('Ordering', { exact: true }).selectOption('title');
    await page.keyboard.press('Escape');
    await page.locator('[data-my-issue-tabs] [role=tab]').nth(1).click();
    await wait(1200);
    ok((await prefsOf(`team:${team.id}:all`)).ordering === 'title', 'the edit lands on the tab it was made on');
    ok((await prefsOf(`team:${team.id}:active`)).ordering === undefined, 'the next tab is left alone', JSON.stringify(await prefsOf(`team:${team.id}:active`)));

    // My issues: switching tabs keeps the chips in the URL a person copies.
    await page.goto(`${base}/workspace/my-issues/assigned?filter=${Buffer.from(JSON.stringify({ priority: { is: 1 } })).toString('base64url')}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-filter-chip][data-field="priority"]');
    await page.locator('[data-my-issue-tabs] [role=tab]').nth(1).click();
    await wait(300);
    ok(/\/my-issues\/created/.test(page.url()) && JSON.stringify(decode(page.url())) === JSON.stringify({ priority: { is: 1 } }),
      'a tab switch keeps ?filter=', page.url().replace(base, ''));
    await page.goBack();
    await wait(300);
    ok(await page.locator('[data-filter-chip][data-field="priority"]').count() === 1, 'Back keeps the filter of that entry');

    // Projects: the display is stored through viewPreferences and survives a reload.
    await page.goto(`${base}/workspace/projects`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Display options' }).click();
    await page.getByLabel('Change grouping').selectOption('Health');
    await page.getByLabel('View ordering').selectOption('Start date');
    await wait(900);
    const projectPrefs = await prefsOf('projects');
    ok(projectPrefs.grouping === 'health' && projectPrefs.ordering === 'startDate', 'project display is stored', JSON.stringify(projectPrefs));
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Display options' }).click();
    ok(await page.getByLabel('Change grouping').inputValue() === 'Health', 'project grouping survives a reload');
  } finally {
    await browser.close();
    server.kill();
  }
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
