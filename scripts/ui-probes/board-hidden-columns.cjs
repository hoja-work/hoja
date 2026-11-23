// Board hidden columns (HOJA-749): hiding a column through its menu removes it from the board and
// lists it in a "Hidden columns" lane that stays in view beside the scroller; the choice is stored
// in the viewer's viewPreferences layer, survives a reload, stays per page, and wins over a shared
// default in both directions; a hidden column stays listed even when it empties (so it can always
// be shown again); a sub-grouped board lists hidden columns too; and the column header follows
// DESIGN.md typography (Tailwind's text-sm, medium weight, no uppercase or tracking).
//
// Run from the app root after `cd frontend && npm run build`:
//   node scripts/ui-probes/board-hidden-columns.cjs
// It seeds a throwaway database and serves frontend/dist, so it needs `.venv` and Playwright.
// SHOTS=<dir> saves screenshots of the board, the lane and the sub-grouped board there.
//
// Exit 0 means every check passed; exit 1 means a check failed; exit 2 means it could not run.
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const repo = path.resolve(__dirname, '../..');
function loadPlaywright() {
  for (const c of [process.env.PLAYWRIGHT_MODULE, 'playwright',
                   path.join(repo, 'local-e2e/node_modules/playwright')].filter(Boolean)) {
    try { return require(c) } catch { /* next */ }
  }
  throw new Error('Playwright not found; set PLAYWRIGHT_MODULE.');
}
const { chromium } = loadPlaywright();

let pass = 0, failed = 0;
const ok = (cond, label, detail = '') => {
  cond ? pass++ : failed++;
  console.log(`${cond ? 'ok  ' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`);
};
const wait = ms => new Promise(r => setTimeout(r, ms));
const shots = process.env.SHOTS || '';

const web = path.join(repo, 'frontend/dist');
if (!fs.existsSync(path.join(web, 'index.html'))) {
  console.error('build the frontend first: cd frontend && npm run build');
  process.exit(2);
}
const artifacts = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(artifacts, { recursive: true });
const scratch = fs.mkdtempSync(path.join(artifacts, 'board-hidden-'));
const db = path.join(scratch, 'hidden.db');
const token = 'hoja_board_hidden_token';
const py = path.join(repo, '.venv/bin/python');
const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
if (seed.status !== 0) { console.error(seed.stderr || 'seed failed'); process.exit(2); }
const port = 19400 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });

async function gql(query, variables) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const body = await res.json();
  if (body.errors) throw new Error(`graphql error: ${JSON.stringify(body.errors)}`);
  return body.data;
}

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
    const team = d.teams.nodes[0];
    const state = type => team.states.nodes.find(s => s.type === type).id;
    const create = async (title, input) => (await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id}}}', { i: { title, teamId: team.id, ...input } })).issueCreate.issue.id;
    await create('Plan the week', { stateId: state('backlog'), priority: 3 });
    await create('Fix the login', { stateId: state('unstarted'), priority: 1 });
    const high = await create('Ship the board', { stateId: state('started'), priority: 2 });
    const viewType = `team:${team.id}:all`;
    const prefs = async () => (await gql('query($t:String){viewPreferences(viewType:$t){default user effective}}', { t: viewType })).viewPreferences;

    browser = await chromium.launch({ headless: true });
    // Narrow enough that five status columns overflow the scroller.
    const ctx = await browser.newContext({ viewport: { width: 1180, height: 800 } });
    await ctx.addInitScript(t => { localStorage.setItem('hoja_token', t); localStorage.setItem('hoja_issue_layout', 'Board'); }, token);
    const page = await ctx.newPage();
    const names = () => page.$$eval('[data-kanban-column] [data-kanban-column-name]', els => els.map(e => e.textContent.trim()));
    const lane = page.locator('[data-kanban-hidden-lane]');
    const settle = async () => { await wait(700); };
    const open = async () => {
      await page.goto(`${base}/workspace/team/trebol/active`);
      await page.locator('[data-kanban-column]').first().waitFor({ timeout: 20000 });
      await settle();
    };
    const hide = async name => {
      await page.getByRole('button', { name: `${name} column options`, exact: true }).click();
      await page.getByRole('menuitem', { name: 'Hide column' }).click();
      await settle();
    };
    const display = async fn => {
      await page.locator('[data-my-issue-toolbar]').getByRole('button', { name: /display/i }).first().click();
      await page.waitForSelector('[data-display-panel]');
      await fn(page.locator('[data-display-panel]'));
      await settle();
      await page.keyboard.press('Escape');
      await settle();
    };

    await open();
    const all = await names();
    ok(all.includes('Backlog') && all.includes('Todo'), 'the board starts with every workflow column', all.join(', '));

    // Header typography (DESIGN.md: Tailwind scale, no uppercase, the app's medium weight for titles).
    const type = await page.$eval('[data-kanban-column] header', h => {
      const name = getComputedStyle(h.querySelector('[data-kanban-column-name]'));
      const count = getComputedStyle(h.querySelector('[data-kanban-count]'));
      // text-sm is 0.875rem; the root size follows the workspace's font scale.
      const sm = `${0.875 * parseFloat(getComputedStyle(document.documentElement).fontSize)}px`;
      return { sm, size: name.fontSize, weight: name.fontWeight, transform: name.textTransform, spacing: name.letterSpacing, countSize: count.fontSize, countWeight: count.fontWeight };
    });
    ok(type.size === type.sm && type.countSize === type.sm, 'column name and count use text-sm', `${type.size}/${type.countSize}, text-sm=${type.sm}`);
    ok(type.weight === '500' && type.countWeight === '400', 'column name is medium, count regular', `${type.weight}/${type.countWeight}`);
    ok(type.transform === 'none' && type.spacing === 'normal', 'column name has no uppercase or tracking', `${type.transform}/${type.spacing}`);
    if (shots) await page.screenshot({ path: path.join(shots, 'after-board.png') });

    // Hide a column: it leaves the board, joins the lane, and the lane is in view without scrolling.
    await hide('Backlog');
    ok(!(await names()).includes('Backlog'), 'Hide column removes Backlog from the board');
    ok(await lane.count() === 1, 'a "Hidden columns" lane appears');
    ok(await lane.getByRole('button', { name: 'Show column Backlog' }).count() === 1, 'the lane lists Backlog');
    const box = await lane.boundingBox();
    ok(box && box.x + box.width <= 1180 && box.width < 260, 'the lane is narrow and in view beside the scroller', box ? `x=${Math.round(box.x)} w=${Math.round(box.width)}` : 'none');
    if (shots) await page.screenshot({ path: path.join(shots, 'after-hidden.png') });
    ok(JSON.stringify((await prefs()).user?.hiddenColumns) === '["Backlog"]', 'the hidden column is stored in the viewer layer');

    await page.reload();
    await page.locator('[data-kanban-column]').first().waitFor({ timeout: 20000 });
    await settle();
    ok(!(await names()).includes('Backlog') && await lane.count() === 1, 'Backlog stays hidden after a reload');

    // Per page: the Backlog tab keeps its own columns.
    await page.getByRole('tab', { name: 'Backlog', exact: true }).or(page.getByRole('button', { name: 'Backlog', exact: true })).first().click();
    await settle();
    ok(await lane.count() === 0, 'another tab does not inherit the hidden column');

    // Shared default: a hidden column set for everyone, then shown again by the viewer.
    await open();
    await display(async panel => { await panel.getByRole('button', { name: 'Set default for everyone' }).click(); });
    let p = await prefs();
    ok(JSON.stringify(p.default?.hiddenColumns) === '["Backlog"]', 'Set default for everyone stores the hidden column in the shared default');
    await lane.getByRole('button', { name: 'Show column Backlog' }).click();
    await settle();
    ok((await names()).includes('Backlog') && await lane.count() === 0, 'Show column brings Backlog back and the lane goes away');
    p = await prefs();
    ok(JSON.stringify(p.user?.hiddenColumns) === '[]' && JSON.stringify(p.effective?.hiddenColumns) === '[]', 'the viewer layer overrides the shared default', JSON.stringify(p.user));
    await page.reload();
    await page.locator('[data-kanban-column]').first().waitFor({ timeout: 20000 });
    await settle();
    ok((await names()).includes('Backlog'), 'Backlog stays shown after a reload');
    await display(async panel => { await panel.getByRole('button', { name: 'Reset' }).click(); });
    ok(!(await names()).includes('Backlog'), 'Reset falls back to the shared default (Backlog hidden)');
    await lane.getByRole('button', { name: 'Show column Backlog' }).click();
    await settle();

    // A hidden column that empties stays in the lane (other groupings drop empty columns).
    await display(async panel => { await panel.getByLabel('Columns', { exact: true }).selectOption('priority'); });
    ok((await names()).includes('High'), 'grouped by priority, High is a column', (await names()).join(', '));
    await hide('High');
    ok(await lane.getByRole('button', { name: 'Show column High' }).count() === 1, 'the hidden High column is in the lane');
    await gql('mutation($id:String!){issueUpdate(id:$id,input:{priority:3}){success}}', { id: high });
    await page.reload();
    await page.locator('[data-kanban-column]').first().waitFor({ timeout: 20000 });
    await settle();
    ok(await lane.getByRole('button', { name: /Show column High/ }).count() === 1, 'an emptied hidden column is still listed, so it can be shown again');
    await lane.getByRole('button', { name: 'Show column High' }).click();
    await settle();
    ok(await lane.count() === 0 && !(await names()).includes('High'), 'showing an empty column follows "Show empty columns" (off)');

    // Sub-grouped board: the hidden columns are still listed.
    await hide('Medium');
    await display(async panel => { await panel.getByLabel('Rows', { exact: true }).selectOption('assignee'); });
    ok(await page.locator('[data-board-rows]').count() === 1, 'the board is sub-grouped into rows');
    ok(await lane.getByRole('button', { name: 'Show column Medium' }).count() === 1, 'a sub-grouped board lists its hidden columns');
    ok(!(await names()).includes('Medium'), 'the hidden column stays out of every row');
    if (shots) await page.screenshot({ path: path.join(shots, 'after-subgrouped.png') });
    await lane.getByRole('button', { name: 'Show column Medium' }).click();
    await settle();
    ok((await names()).includes('Medium'), 'Show column works from the sub-grouped lane');

    console.log(`\nboard-hidden-columns: ${pass} passed, ${failed} failed`);
    process.exitCode = failed ? 1 : 0;
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
    fs.rmSync(scratch, { recursive: true, force: true });
  }
})().catch(e => { console.error('ERROR ' + e.message); server.kill('SIGKILL'); process.exit(1); });
