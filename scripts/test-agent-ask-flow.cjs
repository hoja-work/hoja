#!/usr/bin/env node
/* The agent screen answers a live run's question, and follows the run without a reload
 * (HOJA-602 e2e loop).
 *
 * Real server (hoja-serve), real hoja-runnerd, real hoja-coder on Pydantic AI Harness; only the
 * model is scripted (runner/tests/coder_scripted_model.py). The browser:
 *
 *   1. opens the agent's page while the run is waiting on its question;
 *   2. sees the question card (header, question, both options) and answers "Postgres";
 *   3. without reloading, sees the transcript reach "Work is ready for review."
 *
 * Needs: app/.venv (uv sync --all-extras), app/frontend/dist (npm run build), runner/.venv with the
 * coder extra, and Playwright (local-e2e/node_modules, or PLAYWRIGHT_MODULE=<path to playwright>).
 *   node scripts/test-agent-ask-flow.cjs        # SHOTS=<dir> to keep the screenshots elsewhere
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
const coderPython = process.env.HOJA_CODER_PYTHON || path.join(runnerRoot, '.venv/bin/python');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-ask-'));
const db = path.join(dir, 'ask.db');
const shots = process.env.SHOTS || dir;
const token = 'agent-ask-flow-fixture';
const py = path.join(repo, '.venv/bin/python');

const run = (code) => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
};
const seeded = JSON.parse(run(`import json
from hoja_server.db import make_engine, migrate, seed_tenant, organization
from hoja_server import runner_api
e = make_engine(${JSON.stringify(db)}); migrate(e)
info = seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada',
                   user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
with e.begin() as c:
    c.execute(organization.update().values(agents_enabled=True))
_row, enrollment = runner_api.mint_enrollment_token(e, organization_id=info['org_id'], created_by=info['user_id'])
e.dispose()
print(json.dumps({'team_id': info['team_id'], 'enrollment': enrollment}))`));

const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const children = [];
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web, PYTHONPATH: repo, HOJA_SECRET_KEY: 'ask-flow' }, stdio: 'ignore' });
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

(async () => {
  try {
    await until(async () => (await fetch(`${base}/ready`)).ok, 30000, 'the server');
    const agent = (await gql(`mutation { agentMemberCreate(input: { name: "Coder", capacity: 1, model: "scripted-coder" }) { agentMember { id userId } } }`)).agentMemberCreate.agentMember;
    await gql(`mutation($id: String!, $team: String!) { agentPolicyUpdate(agentMemberId: $id, input: { teams: [$team], defaultHarness: "hoja-coder" }) { success } }`, { id: agent.id, team: seeded.team_id });
    const task = (await gql(`mutation($input: IssueCreateInput!) { issueCreate(input: $input) { issue { id identifier } } }`, { input: {
      teamId: seeded.team_id, title: 'Choose a database', delegateId: agent.userId,
      description: 'Pick one.\n\n## Acceptance\n- a database is chosen\n- the choice is written to choice.txt' } })).issueCreate.issue;
    const identity = await (await fetch(`${base}/runner/register`, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'ui-runner', enrollment_token: seeded.enrollment, harnesses: ['hoja-coder'] }) })).json();
    const daemon = spawn(coderPython, ['-m', 'hoja_runnerd.main', '--base-url', base, '--state-dir', path.join(dir, 'runner'),
      '--max-leases', '1', '--no-herdr', '--allow-harness', 'hoja-coder',
      '--env-passthrough', 'HOJA_CODER_MODEL_FACTORY', '--env-passthrough', 'PYTHONPATH'],
      { cwd: runnerRoot, stdio: 'ignore', env: { PATH: process.env.PATH, HOME: dir, HOJA_RUNNER_ID: identity.runner_id, HOJA_RUNNER_TOKEN: identity.token,
        HOJA_CODER_PYTHON: coderPython, HOJA_CODER_MODEL_FACTORY: 'coder_scripted_model:model',
        PYTHONPATH: `${runnerRoot}:${path.join(runnerRoot, 'tests')}` } });
    children.push(daemon);
    const state = async () => (await gql(`query($id: String!) { issue(id: $id) { state { name } agentSession { state } } }`, { id: task.id })).issue;
    await until(async () => (await state()).agentSession.state === 'awaiting_input', 60000, 'the agent question');
    ok(true, 'the run is waiting on its question before the page opens');

    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${base}/workspace/agents/${agent.id}`);
    const card = page.locator('[data-style-hooks~="agent-ask"]');
    await card.waitFor({ timeout: 20000 });
    ok(true, 'the question card is shown');
    ok((await card.textContent()).includes('Which database should the service use?'), 'the card shows the question');
    ok(await card.getByLabel('Postgres', { exact: false }).count() > 0 && await card.getByLabel('SQLite', { exact: false }).count() > 0, 'both options are offered');
    ok(await card.getByRole('button', { name: 'Send answer' }).isDisabled(), 'nothing can be sent before an option is picked');
    await page.screenshot({ path: path.join(shots, 'agent-ask-open.png') });

    await card.getByLabel('Postgres', { exact: false }).check();
    await card.getByRole('button', { name: 'Send answer' }).click();
    await card.waitFor({ state: 'detached', timeout: 15000 });
    ok(true, 'the card goes away once answered');

    // No reload from here on: the page must follow the run by itself.
    await page.getByText('Work is ready for review.').waitFor({ timeout: 45000 });
    ok(true, 'the transcript reaches "ready for review" without a reload');
    const final = await state();
    ok(final.state.name === 'In Review', 'the task is In Review', final.state.name);
    await page.screenshot({ path: path.join(shots, 'agent-ask-done.png') });
    await browser.close();
  } catch (e) {
    fail++; console.log(`FAIL ${e.message}`);
  } finally {
    for (const child of children) child.kill();
    console.log(`\n${pass} passed, ${fail} failed. Screenshots: ${shots}`);
    process.exit(fail ? 1 : 0);
  }
})();
