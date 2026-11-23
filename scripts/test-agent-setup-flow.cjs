#!/usr/bin/env node
/* From a brand-new agent to a finished test run, using only the agent page (HOJA-602).
 *
 * The browser clicks through the setup checklist (team, harness, model, key), copies the commands
 * the page prints for "Connect a machine" and runs them exactly as printed, watches that step turn
 * green on its own, sends the test task, answers the agent's question on the same page, and sees
 * the test reach In Review. Then it uses the Connect field the way a person would: pastes a URL,
 * clicks a preset, removes a grant and undoes it.
 *
 * Real server, real hoja-runnerd, real hoja-coder on Pydantic AI Harness; only the model is
 * scripted (runner/tests/coder_scripted_model.py). Needs what test-agent-ask-flow.cjs needs.
 *   node scripts/test-agent-setup-flow.cjs     # SHOTS=<dir> keeps the screenshots
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../local-e2e/node_modules/playwright');

const repo = path.resolve(__dirname, '..');
const runnerRoot = path.resolve(repo, '../runner');
const web = path.join(repo, 'frontend/dist');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-setup-'));
const db = path.join(dir, 'setup.db');
const shots = process.env.SHOTS || dir;
const token = 'agent-setup-flow-fixture';
const py = path.join(repo, '.venv/bin/python');

const run = (code) => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
};
run(`from hoja_server.db import make_engine, migrate, seed_tenant, organization
e = make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada',
            user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
with e.begin() as c:
    c.execute(organization.update().values(agents_enabled=True))
e.dispose()`);

const port = 19600 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const children = [];
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web, PYTHONPATH: repo, HOJA_SECRET_KEY: 'setup-flow' }, stdio: 'ignore' });
children.push(server);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const until = async (fn, ms, what) => {
  const end = Date.now() + ms;
  while (Date.now() < end) { const v = await fn().catch(() => null); if (v) return v; await wait(300); }
  throw new Error(`timed out waiting for ${what}`);
};

let pidFile = null;
(async () => {
  let browser;
  try {
    await until(async () => (await fetch(`${base}/ready`)).ok, 30000, 'the server');
    const agent = (await gql(`mutation { agentMemberCreate(input: { name: "Builder" }) { agentMember { id userId } } }`)).agentMemberCreate.agentMember;
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('dialog', (d) => d.accept());
    await page.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${base}/workspace/agents/${agent.id}`);
    const setup = page.locator('section[aria-label="Setup"]');
    await setup.locator('[data-setup-step]').first().waitFor({ timeout: 20000 });
    const stepState = (id) => setup.locator(`[data-setup-step="${id}"]`).getAttribute('data-setup-state');
    const doneSoon = (id, ms = 15000) => until(async () => (await stepState(id)) === 'done', ms, `${id} done`);
    ok(await setup.getByText('Get Builder working').count() === 1, 'a new agent opens on its setup checklist');
    ok((await setup.locator('[data-setup-step]').first().getAttribute('data-setup-step')) === 'machine', 'nothing is done yet: the first step is the machine');
    ok((await stepState('model')) === 'todo' && (await stepState('key')) === 'todo',
      'model and key are not "done" before a harness is chosen');
    await page.screenshot({ path: path.join(shots, 'setup-0-new.png'), fullPage: true });

    // 0. Machine: the one command the page prints, run as printed; the step turns green by itself.
    await setup.locator('[data-setup-step="machine"]').getByRole('button', { name: 'Connect a machine' }).click();
    const line = (await setup.locator('[data-connect-machine="waiting"] [data-connect-command]').first().textContent()).trim();
    ok(/install\.sh \| HOJA_ENROLLMENT_TOKEN=\S+ sh$/.test(line), 'the machine step prints one command');
    await page.screenshot({ path: path.join(shots, 'setup-2-commands.png'), fullPage: true });
    const machineHome = path.join(dir, 'machine'); fs.mkdirSync(machineHome);
    const installed = spawnSync('bash', ['-c', line], { cwd: machineHome, encoding: 'utf8', timeout: 600000,
      env: { PATH: process.env.PATH, HOME: machineHome, HOJA_RUNNER_SERVICE: 'none', HOJA_RUNNER_NAME: 'office-mac',
        HOJA_CODER_MODEL_FACTORY: 'coder_scripted_model:model', PYTHONPATH: path.join(runnerRoot, 'tests') } });
    ok(installed.status === 0, 'the command works as printed', (installed.stderr || '').slice(-200));
    pidFile = path.join(machineHome, '.hoja/runner/runnerd.pid');
    await doneSoon('machine', 30000);
    ok(true, 'the machine step turns green by itself when the runner checks in');

    // 1. Team: one click.
    await setup.getByRole('button', { name: /Trebol/ }).click();
    await doneSoon('teams');
    ok(true, 'one click on a team completes "where it can work"');

    // 2. Harness: the recommended card.
    await setup.getByRole('button', { name: /Hoja Coder/ }).click();
    await doneSoon('harness');
    ok(true, 'one click on Hoja Coder completes "how it runs"');

    // 3. Model: a preset.
    await setup.getByRole('button', { name: /Claude Sonnet 5\.5/ }).click();
    await doneSoon('model');
    ok(true, 'one click on a model preset completes "which model"');

    // 4. Key: paste and save; the page names the variable the provider reads.
    const keyField = setup.getByLabel('ANTHROPIC_API_KEY');
    ok(await keyField.count() === 1, 'the key field is named for the provider the model implies');
    await keyField.fill('sk-test-not-a-real-key');
    await setup.getByRole('button', { name: 'Save key' }).click();
    await doneSoon('key');
    const policy = (await gql(`query($id: String!) { agentPolicy(agentMemberId: $id) { secrets teams defaultHarness } agentSecretNames }`, { id: agent.id }));
    ok(policy.agentPolicy.secrets.includes('ANTHROPIC_API_KEY') && policy.agentSecretNames.includes('ANTHROPIC_API_KEY'),
      'the key is stored as a workspace secret and granted to this agent', JSON.stringify(policy.agentPolicy.secrets));
    await page.screenshot({ path: path.join(shots, 'setup-1-machine.png'), fullPage: true });

    // 6. Try it: send the test task, answer the agent's question here, watch it reach In Review.
    await setup.getByRole('button', { name: 'Send a test task' }).click();
    const card = page.locator('[data-style-hooks~="agent-ask"]');
    await card.waitFor({ timeout: 45000 });
    ok(true, 'the agent\'s question appears on the same page while the test runs');
    await page.screenshot({ path: path.join(shots, 'setup-3-question.png'), fullPage: true });
    await card.getByLabel('Postgres', { exact: false }).check();
    await card.getByRole('button', { name: 'Send answer' }).click();
    await doneSoon('try', 60000);
    ok(true, 'the test task reaches In Review and the last step turns green');
    ok(await setup.getByText('6 of 6').count() === 1, 'the checklist reads 6 of 6');
    await page.screenshot({ path: path.join(shots, 'setup-4-done.png'), fullPage: true });
    await setup.getByRole('button', { name: 'Hide' }).click();
    await page.getByText('Builder is ready to take work').waitFor({ timeout: 5000 });
    ok(true, 'once set up, the checklist folds into one "ready" line');

    // Connect: paste a URL, click a preset, remove and undo.
    const field = page.locator('[data-connect-field]');
    await field.scrollIntoViewIfNeeded();
    await field.getByLabel('Connect host').focus();
    await page.evaluate(() => {
      const input = document.querySelector('[data-connect-field] input[aria-label="Connect host"]');
      const data = new DataTransfer(); data.setData('text', 'postgres://app@db.internal/app');
      input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    });
    ok(await field.getByLabel('Connect host').inputValue() === 'db.internal' && await field.getByLabel('Connect port').inputValue() === '5432',
      'pasting a postgres URL fills host and port (the scheme\'s port)');
    await field.getByRole('button', { name: 'Add Connect grant' }).click();
    await field.locator('[data-connect-grant="db.internal:5432"]').waitFor({ timeout: 10000 });
    ok((await field.locator('[data-connect-grant="db.internal:5432"]').textContent()).includes('Postgres'), 'the grant names the service behind the port');
    await field.getByRole('button', { name: /Grant localhost:3000/ }).click();
    await field.locator('[data-connect-grant="localhost:3000"]').waitFor({ timeout: 10000 });
    ok(true, 'one click on a preset grants localhost:3000');
    await field.getByRole('button', { name: 'Remove db.internal:5432' }).click();
    await field.getByRole('button', { name: 'Undo' }).waitFor({ timeout: 10000 });
    ok(await field.locator('[data-connect-grant="db.internal:5432"]').count() === 0, 'remove takes the grant away');
    await field.getByRole('button', { name: 'Undo' }).click();
    await field.locator('[data-connect-grant="db.internal:5432"]').waitFor({ timeout: 10000 });
    const stored = (await gql(`query($id: String!) { agentPolicy(agentMemberId: $id) { connect } }`, { id: agent.id })).agentPolicy.connect;
    ok(stored.length === 2 && stored.some((g) => g.host === 'db.internal' && g.port === 5432), 'undo puts it back, on the server too', JSON.stringify(stored));
    await field.screenshot({ path: path.join(shots, 'connect-field.png') });

    // Phone width: every setup control is inside the viewport.
    await setup.getByRole('button', { name: 'Change setup' }).click();
    await page.setViewportSize({ width: 390, height: 900 });
    await page.waitForTimeout(400);
    const overflow = await page.evaluate(() => [...document.querySelectorAll('section[aria-label="Setup"] button, section[aria-label="Setup"] input')]
      .filter((el) => { const b = el.getBoundingClientRect(); return b.width && (b.right > window.innerWidth + 1 || b.left < -1); }).length);
    ok(overflow === 0, 'at phone width no setup control leaves the viewport', String(overflow));
    await page.screenshot({ path: path.join(shots, 'setup-phone.png'), fullPage: true });
  } catch (e) {
    fail++; console.log(`FAIL ${e.message}`);
    try {
      console.log('RUNNERS', JSON.stringify((await gql('{ runners(first: 20) { nodes { name status harnesses enabled revokedAt } } }')).runners.nodes));
    } catch (err) { console.log('RUNNERS?', err.message); }
    try {
      const p = (await browser.contexts())[0]?.pages()[0];
      if (p) { await p.screenshot({ path: path.join(shots, 'setup-failure.png'), fullPage: true });
        console.log('MACHINE STEP', await p.locator('[data-setup-step="machine"]').innerText().catch(() => '?')); }
    } catch (err) { console.log('SHOT?', err.message); }
    const logPath = path.join(dir, 'runnerd.log');
    if (fs.existsSync(logPath)) console.log('RUNNERD', fs.readFileSync(logPath, 'utf8').slice(-1500));
  } finally {
    if (browser) await browser.close();
    for (const child of children) child.kill();
    try { if (pidFile) process.kill(Number(fs.readFileSync(pidFile, 'utf8'))); } catch { /* not started */ }
    console.log(`\n${pass} passed, ${fail} failed. Screenshots: ${shots}`);
    process.exit(fail ? 1 : 0);
  }
})();
