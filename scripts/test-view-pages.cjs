// The views pages against a real seeded server: the full-page editor, the saved-view page, the
// views list, team tabs, the sidebar favorites and the view switcher.
// Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-view-pages.cjs
// Checks: the editor previews the draft on the server and names it from its first chip
// ("Urgent tasks"); Create view lands on /workspace/view/<slug> with the count line; editing a
// saved view's filters shows Clear / Save, survives a reload, and Save writes the view; the star
// lists the view in the sidebar; Duplicate and Delete (with its confirm) work; the list groups
// rows into Personal and Workspace sections; an old /views/issues/<id> link settles on the slug
// URL; a team view created from "Add new view" is pinned as a tab and 1 returns to the first tab;
// O then V opens the view switcher.
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
const dir = fs.mkdtempSync(path.join(scratch, 'view-pages-'));
const db = path.join(dir, 'views.db');
const token = 'hoja_view_pages_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19400 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
  const team = d.teams.nodes[0];
  const todo = team.states.nodes.find(s => s.type === 'unstarted').id;
  const create = (title, input) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title, teamId: team.id, ...input } });
  await create('Urgent fire', { priority: 1, stateId: todo });
  await create('Another fire', { priority: 1, stateId: todo });
  await create('High thing', { priority: 2, stateId: todo });
  await create('Low chore', { priority: 4, stateId: todo });

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: Number(process.env.WIDTH || 1440), height: Number(process.env.HEIGHT || 900) } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));

    // ---- the editor
    await page.goto(`${base}/workspace/views/issues`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-workspace-views-page]');
    await page.locator('[data-new-view]').click();
    await page.waitForSelector('[data-view-editor]');
    await page.waitForSelector('[data-my-issue-row]');
    ok(await page.locator('[data-my-issue-row]').count() === 4, 'the editor previews every task before a filter');
    await page.locator('[data-view-editor] [data-filter-button]').click();
    await page.waitForSelector('[data-filter-menu]');
    await page.keyboard.type('urg');
    await wait(150);
    await page.getByRole('menuitemcheckbox', { name: /Priority: Urgent/ }).click();
    await page.keyboard.press('Escape');
    await wait(700);
    ok(await page.locator('[data-my-issue-row]').count() === 2, 'the preview runs the draft on the server', String(await page.locator('[data-my-issue-row]').count()));
    ok(await page.locator('[data-view-name-input]').inputValue() === 'Urgent tasks', 'the name is suggested from the first chip', await page.locator('[data-view-name-input]').inputValue());
    ok(await page.locator('[data-view-description-input]').inputValue() === 'Tasks with urgent priority', 'so is the description');
    ok((await page.locator('[data-view-save-to]').textContent()).includes('Personal'), 'Save to defaults to Personal');
    await page.locator('[data-view-create]').click();
    await page.waitForURL(/\/workspace\/view\/urgent-tasks-/);
    ok(true, 'Create view opens /workspace/view/<slug>', page.url().replace(base, ''));
    await page.waitForSelector('[data-view-page] [data-my-issue-row]');
    ok((await page.locator('[data-view-count]').textContent()).trim() === '2 tasks', 'the count line', await page.locator('[data-view-count]').textContent());
    const views = (await gql('{views{id slugId name description icon visibility filters}}')).views;
    const urgent = views.find(v => v.name === 'Urgent tasks');
    ok(urgent && urgent.visibility === 'private' && urgent.icon === 'Siren' && JSON.stringify(urgent.filters) === '{"priority":{"is":1}}',
      'the view is stored as built', JSON.stringify(urgent));

    // ---- unsaved changes
    await page.locator('[aria-label="Remove Priority filter"]').click();
    await page.waitForSelector('[data-view-unsaved]');
    await wait(700);
    ok(await page.locator('[data-my-issue-row]').count() === 4, 'editing the filters re-runs the view');
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('[data-view-page]');
    ok(await page.locator('[data-view-unsaved]').count() === 1, 'unsaved edits survive a reload');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    ok(await page.locator('[data-view-unsaved]').count() === 0, 'Clear drops the edits');
    await page.locator('[data-view-page] [data-filter-button]').click();
    await page.keyboard.type('high');
    await wait(150);
    await page.getByRole('menuitemcheckbox', { name: /Priority: High/ }).click();
    await page.keyboard.press('Escape');
    await page.locator('[data-view-head]').click({ position: { x: 600, y: 10 } });
    await page.keyboard.press('Alt+KeyS');
    await wait(800);
    const saved = (await gql('query($id:String!){view(id:$id){filters}}', { id: urgent.id })).view.filters;
    ok(JSON.stringify(saved) === '{"priority":{"isEitherOf":[1,2]}}', 'Alt S saves the edited filters to the view', JSON.stringify(saved));
    ok(await page.locator('[data-view-unsaved]').count() === 0, 'the bar goes once saved');

    // ---- favorite, sidebar, details
    await page.locator('[data-view-favorite]').click();
    await page.waitForSelector('[data-nav-favorite-view]');
    ok((await page.locator('[data-nav-favorite-view]').textContent()).includes('Urgent tasks'), 'the sidebar lists the favorited view by name');
    await page.locator('[data-view-details-toggle]').click();
    await page.waitForSelector('[data-view-details]');
    ok((await page.locator('[data-view-details]').textContent()).includes('Personal'), 'the details panel shows the visibility');
    await page.locator('[data-view-breakdown] button').first().click();
    await page.waitForSelector('[data-view-unsaved]');
    ok(await page.locator('[data-filter-chip][data-field="assignee"]').count() === 1, 'a breakdown row adds its filter');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();

    // ---- duplicate and delete
    await page.locator('[data-view-menu]').click();
    await page.getByRole('menuitem', { name: 'Duplicate…' }).click();
    await page.waitForSelector('[role=dialog] input');
    await page.locator('[role=dialog] input').fill('Urgent copy');
    await page.keyboard.press('Enter');
    await page.waitForURL(/\/workspace\/view\/urgent-copy-/);
    ok(true, 'Duplicate opens the copy');
    await page.waitForSelector('[data-view-page]');
    await page.locator('[data-view-menu]').click();
    await page.getByRole('menuitem', { name: 'Move to' }).click();
    await page.getByRole('menuitem', { name: 'Workspace' }).click();
    await wait(500);
    const copy = (await gql('{views{id name visibility}}')).views.find(v => v.name === 'Urgent copy');
    ok(copy && copy.visibility === 'workspace', 'Move to ▸ Workspace shares the view');
    await page.locator('[data-view-menu]').click();
    await page.getByRole('menuitem', { name: 'Delete' }).click();
    await page.waitForSelector('[role=dialog][aria-label*="Delete the view"]');
    await page.getByRole('button', { name: 'Confirm' }).click();
    await page.waitForURL(/\/workspace\/views\/issues$/);
    ok(!(await gql('{views{name}}')).views.some(v => v.name === 'Urgent copy'), 'Delete removes the view after the confirm');

    // ---- the list
    await page.waitForSelector('[data-views-section]');
    ok(await page.locator('[data-views-section="personal"]').count() === 1, 'the list has a Personal views section');
    ok((await page.locator('[data-views-section="personal"]').textContent()).includes('Only visible to you'), 'with Linear’s hint');

    // ---- legacy link
    await page.goto(`${base}/workspace/views/issues/${urgent.id}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-view-page]');
    ok(page.url().endsWith(`/workspace/view/${urgent.slugId}`), 'an old /views/issues/<id> link settles on the slug URL', page.url().replace(base, ''));

    // ---- O then V
    await page.locator('[data-view-head]').click({ position: { x: 600, y: 10 } });
    await page.keyboard.press('o');
    await page.keyboard.press('v');
    await page.waitForSelector('[data-command-views]');
    ok(await page.locator('[data-command-views] button').count() >= 1, 'O then V lists the views');
    await page.keyboard.press('Escape');

    // ---- team tabs
    await page.goto(`${base}/workspace/team/${team.key.toLowerCase()}/active`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-team-add-view]');
    await page.locator('[data-team-add-view]').click();
    await page.waitForSelector('[data-view-editor]');
    await page.locator('[data-view-name-input]').fill('Team board');
    await page.locator('[data-view-create]').click();
    await page.waitForSelector('[data-team-view-tab]');
    ok(await page.locator('[data-team-view-tab][aria-selected=true]').count() === 1, 'Add new view pins the new view as the active tab');
    const pinned = (await gql('{views(scope:"team"){name pinned}}')).views.find(v => v.name === 'Team board');
    ok(pinned && pinned.pinned, 'the pin is stored on the server');
    await page.locator('[data-view-head]').click({ position: { x: 600, y: 10 } });
    await page.keyboard.press('1');
    await wait(200);
    ok(await page.locator('[data-team-tabs] [role=tab]').first().getAttribute('aria-selected') === 'true', '1 selects the first tab');

    // "Personal" from a team is a private view of that team: still pinned, still team-scoped.
    await page.locator('[data-team-add-view]').click();
    await page.waitForSelector('[data-view-editor]');
    await page.locator('[data-view-name-input]').fill('My team slice');
    await page.locator('[data-view-save-to]').click();
    await page.getByRole('menuitemradio', { name: /^Personal/ }).click();
    await page.locator('[data-view-create]').click();
    await page.waitForSelector('[data-team-view-tab] >> text=My team slice');
    const mine = (await gql('{views(scope:"team"){id name scope visibility pinned sortOrder team{id}}}')).views;
    const slice = mine.find(v => v.name === 'My team slice');
    ok(slice && slice.scope === 'team' && slice.visibility === 'private' && slice.team.id === team.id && slice.pinned,
      'Personal from a team stays a pinned, private team view', JSON.stringify(slice));
    // Alt ← on a focused pinned tab moves it left, stored as viewPin(sortOrder).
    await page.locator(`[data-team-view-tab="${slice.id}"]`).focus();
    await page.keyboard.press('Alt+ArrowLeft');
    await wait(600);
    const order = await page.locator('[data-team-view-tab]').evaluateAll(els => els.map(e => e.textContent.trim()));
    ok(order[0] === 'My team slice', 'the moved tab shows first', order.join(','));
    const stored = (await gql('{views(scope:"team"){name pinned sortOrder}}')).views.filter(v => v.pinned)
      .sort((a, b) => a.sortOrder - b.sortOrder).map(v => v.name);
    ok(stored[0] === 'My team slice', 'the new order is stored', stored.join(','));
    // Saved to the workspace from a team, the view is no tab of it: its page opens instead.
    await page.locator('[data-team-add-view]').click();
    await page.waitForSelector('[data-view-editor]');
    await page.locator('[data-view-name-input]').fill('Shared from team');
    await page.locator('[data-view-save-to]').click();
    await page.getByRole('menuitemradio', { name: 'Workspace' }).click();
    await page.locator('[data-view-create]').click();
    await page.waitForURL(/\/workspace\/view\/shared-from-team-/);
    ok(true, 'a workspace view made from the team tabs opens on its own page');
    ok(errors.length === 0, 'no page errors', errors.join(' | '));
  } finally {
    await browser.close();
    server.kill();
  }
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
