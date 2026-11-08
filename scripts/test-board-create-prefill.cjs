// A board column's create (+) button must open the new-issue composer with that column's status
// pre-selected, on every board surface: the workspace Board, My issues, Team issues and a
// project's Issues tab. Entry points that are not a column (sidebar, toolbar) keep the default.
// Run from repo root after `cd frontend && npm run build`: node scripts/test-board-create-prefill.cjs
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const repoScratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(repoScratch, { recursive: true });
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const dir = fs.mkdtempSync(path.join(repoScratch, 'board-prefill-'));
const db = path.join(dir, 'prefill.db');
const token = 'hoja_board_prefill_token';
const teamKey = 'TREBOL';
const py = path.join(repo, '.venv/bin/python');
const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key=${JSON.stringify(teamKey)}, team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 18800 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

async function gql(query, variables) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const body = await res.json();
  assert.ok(!body.errors, `graphql error: ${JSON.stringify(body.errors)}`);
  return body.data;
}

async function openFromColumn(page, column) {
  await page.getByRole('button', { name: `Create issue in ${column}`, exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New issue' });
  await dialog.waitFor({ timeout: 10000 });
  const info = await dialog.evaluate(form => {
    const pick = label => {
      const field = [...form.querySelectorAll('label.issue-property')].find(l => l.querySelector('span')?.textContent === label);
      const select = field?.querySelector('select');
      return { value: select?.value ?? null, label: select?.selectedOptions[0]?.textContent ?? null, options: [...(select?.options || [])].map(o => o.textContent) };
    };
    const read = labelText => pick(labelText);
    return { status: read('Status'), team: read('Team') };
  });
  return { dialog, info };
}

async function closeDialog(page) {
  await page.getByRole('dialog', { name: 'New issue' }).getByRole('button', { name: 'Cancel' }).click();
  await page.getByRole('dialog', { name: 'New issue' }).waitFor({ state: 'detached', timeout: 5000 });
}

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    const teams = (await gql('query{teams(first:5){nodes{id key name states{nodes{id name type}}}}}')).teams.nodes;
    const team = teams.find(t => t.key === teamKey);
    assert.ok(team, 'seeded team missing');
    // "Done" is a completed state, so it can never be the team default the composer would otherwise pick.
    const clicked = team.states.nodes.find(s => s.name === 'Done') || team.states.nodes.at(-1);
    const project = (await gql('mutation($teams:[String!]){projectCreate(input:{name:"Prefill audit",teamIds:$teams}){success project{id}}}', { teams: [team.id] })).projectCreate?.project;
    assert.ok(project?.id, 'project create failed');

    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(({ token, layout }) => { localStorage.setItem('hoja_token', token); localStorage.setItem('hoja_issue_layout', layout); }, { token, layout: 'Board' });
    const page = await ctx.newPage();
    const results = [];

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
      const { dialog, info } = await openFromColumn(page, clicked.name);
      assert.equal(info.status.label, clicked.name, `${name}: composer status was "${info.status.label}", expected the clicked column "${clicked.name}"`);
      assert.equal(info.status.value, clicked.id, `${name}: composer status select value was "${info.status.value}"`);
      assert.equal(info.team.label, team.key, `${name}: composer team was "${info.team.label}", expected ${team.key}`);
      await closeDialog(page);
      results.push(`${name}: "${clicked.name}" pre-selected`);
    }
    // The prefill is per entry point: a non-column opener must still start on the team default.
    await page.goto(base + '/workspace/board');
    await page.locator('.kanban-column').first().waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'Create new issue', exact: true }).click();
    const sidebarDialog = page.getByRole('dialog', { name: 'New issue' });
    await sidebarDialog.waitFor();
    const sidebarStatus = await sidebarDialog.evaluate(form => {
      const field = [...form.querySelectorAll('label.issue-property')].find(l => l.querySelector('span')?.textContent === 'Status');
      const select = field?.querySelector('select');
      return { value: select?.value, label: select?.selectedOptions[0]?.textContent };
    });
    assert.equal(sidebarStatus.value, '', `sidebar create should keep the team default, got "${sidebarStatus.label}"`);
    results.push('sidebar create: team default kept');
    await closeDialog(page);

    // End to end: creating from a column writes the issue into that status and shows it there.
    const title = `Prefilled from ${clicked.name} column`;
    const { dialog } = await openFromColumn(page, clicked.name);
    await dialog.getByRole('textbox', { name: 'Issue title' }).fill(title);
    await dialog.getByRole('button', { name: 'Create issue', exact: true }).click();
    await dialog.waitFor({ state: 'detached', timeout: 10000 });
    const column = page.locator('.kanban-column').filter({ has: page.locator('header', { hasText: clicked.name }) }).first();
    await column.locator('.kanban-card', { hasText: title }).first().waitFor({ timeout: 10000 });
    const created = (await gql('query{issues(first:100){nodes{title state{name} team{key}}}}')).issues.nodes.find(i => i.title === title);
    assert.ok(created, 'created issue was not returned by the API');
    assert.equal(created.state?.name, clicked.name, `created issue landed in "${created.state?.name}", expected "${clicked.name}"`);
    assert.equal(created.team?.key, team.key, `created issue team was ${created.team?.key}`);
    results.push(`create: issue created in "${clicked.name}" and rendered in that column`);

    console.log(`Board create prefill: ${results.map(r => `\n  - ${r}`).join('')}`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().then(() => process.exit(0), error => { console.error(error.message); process.exit(1); });
