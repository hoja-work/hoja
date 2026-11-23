// Probe: Settings → Workspace templates (HOJA-742), end to end in a real browser.
//
// Seeds a throwaway workspace, serves frontend/dist, and walks the admin journey:
// list the ready-made templates, preview one, apply it, see that a second preview has
// nothing to change, export the workspace, import an edited copy and apply the update.
// Then checks the page at phone width and in dark mode for horizontal overflow.
//
//   cd app && npm --prefix frontend run build && node scripts/ui-probes/workspace-templates-probe.cjs
//
// Needs `.venv` and Playwright (PLAYWRIGHT_MODULE, `playwright`, or local-e2e/node_modules).
// PROBE_SHOTS=<dir> saves screenshots there.
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

const results = [];
const ok = m => { results.push(`ok   ${m}`); console.log(`ok   ${m}`); };
const fail = m => { results.push(`FAIL ${m}`); console.error(`FAIL ${m}`); };
const check = (cond, m) => (cond ? ok(m) : fail(m));
const wait = ms => new Promise(r => setTimeout(r, ms));
const shots = process.env.PROBE_SHOTS || '';
const shot = async (page, name) => { if (!shots) return; await wait(500); await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true }); };

let server = null;
let base = '';
const token = 'hoja_workspace_templates_probe';

function seedAndServe() {
  const web = path.join(repo, 'frontend/dist');
  if (!fs.existsSync(path.join(web, 'index.html'))) { console.error('build the frontend first'); process.exit(2); }
  const scratch = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'wt-probe-'));
  const db = path.join(scratch, 'probe.db');
  const py = path.join(repo, '.venv/bin/python');
  const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='ACME', team_name='Acme', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  if (seed.status !== 0) { console.error(seed.stderr || 'seed failed'); process.exit(2); }
  const port = 18900 + Math.floor(Math.random() * 90);
  base = `http://127.0.0.1:${port}`;
  server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
                 { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
}

async function gql(query, variables) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const body = await res.json();
  if (body.errors) throw new Error(JSON.stringify(body.errors));
  return body.data;
}

const overflow = page => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

(async () => {
  let browser;
  try {
    seedAndServe();
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));

    await page.goto(`${base}/workspace/settings/workspace-templates`);
    await page.getByRole('heading', { name: 'Workspace templates' }).waitFor({ timeout: 15000 });
    ok('the page opens from its settings route');
    const nav = page.locator('#workspace-sidebar').getByText('Templates', { exact: true });
    check(await nav.count() > 0, 'the settings navigation lists it under Workspace');
    const names = ['Agents do most of the writing', 'Small product team', 'Customer support desk'];
    for (const n of names) await page.getByText(n, { exact: true }).first().waitFor({ timeout: 10000 });
    ok('three ready-made templates are listed');
    await shot(page, '1-list-desktop');

    await page.getByRole('button', { name: /Agents do most of the writing/ }).first().click();
    const dialog = page.getByRole('dialog', { name: /Apply “Agents do most of the writing”/ });
    await dialog.waitFor({ timeout: 10000 });
    await dialog.getByText(/to add · 0 to update/).waitFor({ timeout: 10000 });
    ok('preview shows the plan before anything changes');
    check(await dialog.getByText('SLA policies', { exact: true }).count() === 1, 'the plan is grouped by section');
    check(await dialog.getByLabel(/Start SLA clocks for open tasks now/).count() === 1, 'SLA clock option is offered');
    check((await gql('{ slaPolicies { id } }')).slaPolicies.length === 0, 'preview wrote nothing');
    await shot(page, '2-preview-desktop');
    await dialog.getByLabel(/Start SLA clocks for open tasks now/).click();
    await dialog.getByRole('button', { name: 'Apply template' }).click();
    await dialog.waitFor({ state: 'detached', timeout: 10000 });
    await page.getByText('Applied “Agents do most of the writing”', { exact: true }).waitFor({ timeout: 5000 });
    ok('apply closes the dialog and confirms with a toast');
    const policies = (await gql('{ slaPolicies { name pauseStates { id } } }')).slaPolicies;
    check(policies.length === 5 && policies.every(p => p.pauseStates.length === 0), 'five SLA policies, none pausing in Backlog');

    await page.getByRole('button', { name: /Agents do most of the writing/ }).first().click();
    const again = page.getByRole('dialog', { name: /Apply “Agents do most of the writing”/ });
    await again.getByText('Already in place', { exact: true }).waitFor({ timeout: 10000 });
    check(await again.getByRole('button', { name: 'Nothing to change' }).isDisabled(), 're-applying has nothing to change');
    await again.getByRole('button', { name: 'Cancel' }).click();
    await again.waitFor({ state: 'detached', timeout: 5000 });

    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export' }).click()]);
    const exported = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
    check(exported.format === 'hoja.workspace-template' && exported.labels.length === 6, `export downloads ${download.suggestedFilename()}`);

    exported.name = 'Edited copy';
    exported.labels.find(l => l.name === 'Bug').color = '#000000';
    await page.getByRole('button', { name: 'Import…' }).click();
    const imp = page.getByRole('dialog', { name: 'Import a template' });
    await imp.getByLabel('Template contents').fill(JSON.stringify(exported));
    await imp.getByRole('button', { name: 'Preview changes' }).click();
    const edited = page.getByRole('dialog', { name: /Apply “Edited copy”/ });
    await edited.getByText(/0 to add · 1 to update/).waitFor({ timeout: 10000 });
    check(await edited.getByText(/Bug.*color/).count() === 1, 'an imported edit previews as one update naming the field');
    await edited.getByRole('button', { name: 'Apply template' }).click();
    await edited.waitFor({ state: 'detached', timeout: 10000 });
    const bug = (await gql('{ issueLabels { nodes { name color } } }')).issueLabels.nodes.find(l => l.name === 'Bug');
    check(bug && bug.color === '#000000', 'the imported change is applied');

    await page.getByRole('button', { name: 'Import…' }).click();
    const bad = page.getByRole('dialog', { name: 'Import a template' });
    await bad.getByLabel('Template contents').fill('{"format":"something"}');
    await bad.getByRole('button', { name: 'Preview changes' }).click();
    await bad.getByText(/isn't a Hoja workspace template/).waitFor({ timeout: 5000 });
    ok('a file that is not a template is refused with a readable reason');
    await bad.getByRole('button', { name: 'Cancel' }).click();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${base}/workspace/settings/workspace-templates`);
    await page.getByRole('heading', { name: 'Workspace templates' }).waitFor({ timeout: 15000 });
    check(await overflow(page) <= 0, 'no horizontal scroll at phone width');
    await shot(page, '3-list-phone');
    await page.getByRole('button', { name: /Small product team/ }).first().click();
    const phoneDialog = page.getByRole('dialog', { name: /Apply “Small product team”/ });
    await phoneDialog.getByText(/to add/).waitFor({ timeout: 10000 });
    check(await overflow(page) <= 0, 'the preview dialog fits at phone width');
    await shot(page, '4-preview-phone');

    // The app reads its theme from the saved preference at load, so set it and reload.
    await gql('mutation { userPreferenceSet(key: "theme", value: "dark") { success } }');
    await page.evaluate(() => localStorage.setItem('theme', 'dark'));
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${base}/workspace/settings/workspace-templates`);
    await page.getByRole('button', { name: /Customer support desk/ }).first().click();
    await page.getByRole('dialog', { name: /Apply “Customer support desk”/ }).getByText(/to add/).waitFor({ timeout: 10000 });
    await shot(page, '5-preview-dark');
    check(errors.length === 0, `no page errors${errors.length ? ': ' + errors.join(' | ') : ''}`);
  } catch (e) {
    fail(`probe crashed: ${e && e.stack || e}`);
  } finally {
    if (browser) await browser.close();
    if (server) server.kill();
    const failed = results.filter(r => r.startsWith('FAIL')).length;
    console.log(`\n${results.length - failed} passed, ${failed} failed`);
    process.exit(failed ? 1 : 0);
  }
})();
