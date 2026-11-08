// Board column create (+) parity probe (HOJA-95): clicking the + in a Kanban status column must open
// the new-issue composer with THAT column's status pre-selected, and the composer must switch to the
// team that owns the status (the workspace Board merges columns from every team). Entry points that
// are not a column — sidebar "Create new issue", the board toolbar, the empty state, the "Unknown
// state" column, and a list group header that is not grouped by status — keep the team default.
//
// Run from the app root after `cd frontend && npm run build`:
//   node scripts/ui-probes/board-create-prefill.cjs
// It seeds a throwaway database and serves frontend/dist, so it needs `.venv` and Playwright.
// Against an already-running app (the ui-probes convention) skip the seeding:
//   PROBE_URL=http://127.0.0.1:8797 PROBE_TOKEN=<token> node scripts/ui-probes/board-create-prefill.cjs
//
// Exit 0 means every surface prefilled; exit 1 means a check failed; exit 2 means it could not run.
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
function ok(m) { results.push(`ok   ${m}`); console.log(`ok   ${m}`); }
function fail(m) { results.push(`FAIL ${m}`); console.error(`FAIL ${m}`); }

const liveUrl = process.env.PROBE_URL || '';
const liveToken = process.env.PROBE_TOKEN || '';
const teamKey = process.env.PROBE_TEAM || 'TREBOL';
const wait = ms => new Promise(r => setTimeout(r, ms));

let server = null;
let scratchDir = null;
let base = liveUrl.replace(/\/$/, '');
let token = liveToken;

function seedAndServe() {
  const web = path.join(repo, 'frontend/dist');
  if (!fs.existsSync(path.join(web, 'index.html'))) {
    console.error('build the frontend first: cd frontend && npm run build');
    process.exit(2);
  }
  const artifacts = path.join(repo, 'local-e2e/artifacts');
  fs.mkdirSync(artifacts, { recursive: true });
  scratchDir = fs.mkdtempSync(path.join(artifacts, 'board-prefill-'));
  const db = path.join(scratchDir, 'prefill.db');
  token = 'hoja_board_prefill_token';
  const py = path.join(repo, '.venv/bin/python');
  const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key=${JSON.stringify(teamKey)}, team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  if (seed.status !== 0) { console.error(seed.stderr || 'seed failed'); process.exit(2); }
  const port = 18800 + Math.floor(Math.random() * 90);
  base = `http://127.0.0.1:${port}`;
  server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
}

async function gql(query, variables) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const body = await res.json();
  if (body.errors) throw new Error(`graphql error: ${JSON.stringify(body.errors)}`);
  return body.data;
}

// Reads the composer's Status and Team selects the same way the form declares them
// (label.issue-property > span + select), so the probe does not depend on ids.
const readComposer = form => {
  const pick = label => {
    const field = [...form.querySelectorAll('label.issue-property')].find(l => l.querySelector('span')?.textContent === label);
    const select = field?.querySelector('select');
    return { value: select?.value ?? null, label: select?.selectedOptions[0]?.textContent ?? null, options: [...(select?.options || [])].map(o => o.textContent) };
  };
  return { status: pick('Status'), team: pick('Team') };
};

async function openFromColumn(page, column) {
  await page.getByRole('button', { name: `Create issue in ${column}`, exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New issue' });
  await dialog.waitFor({ timeout: 10000 });
  return { dialog, info: await dialog.evaluate(readComposer) };
}

async function closeDialog(page) {
  const dialog = page.getByRole('dialog', { name: 'New issue' });
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await dialog.waitFor({ state: 'detached', timeout: 5000 });
}

(async () => {
  let browser;
  try {
    if (base && token) {
      console.log(`against ${base}`);
    } else if (base || token) {
      console.error('set both PROBE_URL and PROBE_TOKEN, or neither');
      process.exit(2);
    } else {
      seedAndServe();
    }
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }

    const teams = (await gql('query{teams(first:5){nodes{id key name states{nodes{id name type}}}}}')).teams.nodes;
    const team = teams.find(t => t.key === teamKey);
    if (!team) throw new Error(`team ${teamKey} not found; set PROBE_TEAM`);
    // "Done" is a completed state, so it can never be the team default the composer would otherwise pick.
    const clicked = team.states.nodes.find(s => s.name === 'Done') || team.states.nodes.at(-1);
    const project = (await gql('mutation($teams:[String!]){projectCreate(input:{name:"Prefill audit",teamIds:$teams}){success project{id}}}', { teams: [team.id] })).projectCreate?.project;
    if (!project?.id) throw new Error('project create failed');

    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(({ token, layout }) => { localStorage.setItem('hoja_token', token); localStorage.setItem('hoja_issue_layout', layout); }, { token, layout: 'Board' });
    const page = await ctx.newPage();

    // Every board surface that renders a status column.
    const surfaces = [
      ['workspace Board', '/workspace/board'],
      ['My issues board', '/workspace/my-issues/assigned'],
      ['Team issues board', `/workspace/team/${teamKey.toLowerCase()}/active`],
      ['Project issues board', `/workspace/project/${project.id}/issues`],
    ];
    for (const [name, url] of surfaces) {
      await page.goto(base + url);
      await page.locator('.kanban-column').first().waitFor({ timeout: 20000 });
      const { info } = await openFromColumn(page, clicked.name);
      if (info.status.label !== clicked.name) fail(`${name}: composer status was "${info.status.label}", expected the clicked column "${clicked.name}"`);
      else if (info.status.value !== clicked.id) fail(`${name}: composer status select value was "${info.status.value}", expected ${clicked.id}`);
      else if (info.team.label !== team.key) fail(`${name}: composer team was "${info.team.label}", expected ${team.key}`);
      else ok(`${name}: "${clicked.name}" pre-selected for team ${team.key}`);
      await closeDialog(page);
    }

    // The prefill is per entry point: a non-column opener must still start on the team default.
    await page.goto(base + '/workspace/board');
    await page.locator('.kanban-column').first().waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'Create new issue', exact: true }).click();
    const sidebarDialog = page.getByRole('dialog', { name: 'New issue' });
    await sidebarDialog.waitFor();
    const sidebarStatus = (await sidebarDialog.evaluate(readComposer)).status;
    if (sidebarStatus.value !== '') fail(`sidebar create should keep the team default, got "${sidebarStatus.label}"`);
    else ok('sidebar create: team default kept');
    await closeDialog(page);

    // The "Unknown state" pseudo-column has no real status, so it must not prefill either.
    const unknown = page.locator('.kanban-column').filter({ has: page.locator('header', { hasText: 'Unknown state' }) }).first();
    if (await unknown.count()) {
      await unknown.getByRole('button', { name: 'Create issue in Unknown state', exact: true }).click();
      const unknownDialog = page.getByRole('dialog', { name: 'New issue' });
      await unknownDialog.waitFor();
      const unknownStatus = (await unknownDialog.evaluate(readComposer)).status;
      if (unknownStatus.value !== '') fail(`Unknown state column should keep the team default, got "${unknownStatus.label}"`);
      else ok('Unknown state column: team default kept');
      await closeDialog(page);
    } else ok('Unknown state column: absent (nothing to prefill)');

    // End to end: creating from a column writes the issue into that status and shows it there.
    const title = `Prefilled from ${clicked.name} column`;
    const { dialog } = await openFromColumn(page, clicked.name);
    await dialog.getByRole('textbox', { name: 'Issue title' }).fill(title);
    await dialog.getByRole('button', { name: 'Create issue', exact: true }).click();
    await dialog.waitFor({ state: 'detached', timeout: 10000 });
    const column = page.locator('.kanban-column').filter({ has: page.locator('header', { hasText: clicked.name }) }).first();
    try {
      await column.locator('.kanban-card', { hasText: title }).first().waitFor({ timeout: 10000 });
      const created = (await gql('query{issues(first:100){nodes{title state{name} team{key}}}}')).issues.nodes.find(i => i.title === title);
      if (!created) fail('created issue was not returned by the API');
      else if (created.state?.name !== clicked.name) fail(`created issue landed in "${created.state?.name}", expected "${clicked.name}"`);
      else if (created.team?.key !== team.key) fail(`created issue team was ${created.team?.key}`);
      else ok(`create: issue created in "${clicked.name}" and rendered in that column`);
    } catch {
      fail(`create: the created issue never rendered in the "${clicked.name}" column`);
    }

    const failed = results.filter(r => r.startsWith('FAIL')).length;
    console.log(`\nboard-create-prefill: ${results.filter(r => r.startsWith('ok')).length} passed, ${failed} failed`);
    process.exitCode = failed ? 1 : 0;
  } finally {
    if (browser) await browser.close();
    if (server) server.kill('SIGKILL');
    if (scratchDir) fs.rmSync(scratchDir, { recursive: true, force: true });
  }
})().catch(e => { console.error('ERROR ' + e.message); process.exit(1); });
