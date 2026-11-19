#!/usr/bin/env node
/* Machine first, end to end, through the real installer (HOJA-602).
 *
 *   Agents (off) -> Turn on Agents -> "Get started": step 2 locked
 *   -> Connect a machine -> the page's one command, run in a terminal (curl | sh, the real
 *      /install.sh and the wheel this server publishes) -> the machine appears by itself
 *   -> step 2 unlocks -> New agent -> its checklist already has the machine done
 *   -> team, Hoja Coder, model, key -> test task -> the agent asks -> answered -> In Review
 *   -> the Machines tab lists the machine
 *
 * Needs app/.venv, app/frontend/dist, app/downloads/hoja_runnerd-*.whl
 * (scripts/build-runner-wheel.sh), uv or Python 3.11+, network for the coder extra's packages,
 * and Playwright. The model is scripted (runner/tests/coder_scripted_model.py); the runner runs
 * as a background process in a throwaway HOME (HOJA_RUNNER_SERVICE=none), never as a service.
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../local-e2e/node_modules/playwright');

const repo = path.resolve(__dirname, '..');
const runnerTests = path.resolve(repo, '../runner/tests');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'machine-first-'));
const home = path.join(dir, 'home');
fs.mkdirSync(home);
// A repo the agent will work on: a bare "remote" with one commit on main, already cloned into
// the folder the person says they use (~/code), under a name of their own (Demo).
const git = (args, cwd) => {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8' });
  assert.equal(r.status, 0, `git ${args.join(' ')}: ${r.stderr}`);
  return r.stdout.trim();
};
const remote = path.join(dir, 'remote', 'demo.git');
fs.mkdirSync(path.dirname(remote), { recursive: true });
git(['init', '--quiet', '--bare', '-b', 'main', remote]);
const seed = path.join(dir, 'seed');
git(['init', '--quiet', '-b', 'main', seed]);
fs.writeFileSync(path.join(seed, 'README.md'), 'demo\n');
git(['add', 'README.md'], seed);
git(['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--quiet', '-m', 'init'], seed);
git(['push', '--quiet', remote, 'main'], seed);
const code = path.join(home, 'code');
fs.mkdirSync(code);
git(['clone', '--quiet', remote, path.join(code, 'Demo')]);
const db = path.join(dir, 'm.db');
const shots = process.env.SHOTS || dir;
const token = 'machine-first-admin';
const py = path.join(repo, '.venv/bin/python');
assert.ok(fs.readdirSync(path.join(repo, 'downloads')).some((f) => f.endsWith('.whl')),
  'build the wheel first: scripts/build-runner-wheel.sh');
const r = spawnSync(py, ['-c', `from hoja_server.db import make_engine, migrate, seed_tenant
e = make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada',
            user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(r.status, 0, r.stderr);

const port = 19850 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: path.join(repo, 'frontend/dist'), PYTHONPATH: repo, HOJA_SECRET_KEY: 'mf' }, stdio: 'ignore' });
const wait = (ms) => new Promise((res) => setTimeout(res, ms));
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const until = async (fn, ms, what) => {
  const end = Date.now() + ms;
  while (Date.now() < end) { const v = await fn().catch(() => null); if (v) return v; await wait(300); }
  throw new Error(`timed out waiting for ${what}`);
};

(async () => {
  let browser;
  try {
    await until(async () => (await fetch(`${base}/ready`)).ok, 30000, 'the server');
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${base}/workspace/agents`);
    await page.getByRole('button', { name: 'Turn on Agents' }).click();
    const steps = page.locator('[data-getting-started="steps"]');
    await steps.waitFor({ timeout: 15000 });
    ok(true, 'an empty workspace opens on "Get started with agents"');
    ok(await steps.locator('[data-step="agent"]').getByRole('button', { name: 'New agent' }).isDisabled(),
      'step 2 (create an agent) is locked until a machine is connected');
    await page.screenshot({ path: path.join(shots, 'mf-1-start.png') });

    await steps.getByLabel('A folder I already use').check();
    await steps.getByLabel('Work folder').fill('~/code');
    ok((await steps.locator('[data-work-layout]').textContent()).includes('~/code/'), 'the page shows the layout it will use in that folder');
    await steps.getByRole('button', { name: 'Connect a machine' }).click();
    const command = (await steps.locator('[data-connect-machine="waiting"] [data-connect-command]').first().textContent()).trim();
    ok(/^curl -fsSL http.*\/install\.sh \| HOJA_ENROLLMENT_TOKEN=\S+ HOJA_WORK_ROOT=~\/code sh$/.test(command), 'the page prints one command', command.replace(/TOKEN=\S+/, 'TOKEN=…'));
    await page.screenshot({ path: path.join(shots, 'mf-2-command.png') });

    // The terminal: exactly the printed line, in a throwaway HOME, as a background process.
    const started = Date.now();
    const installed = spawnSync('bash', ['-c', command], {
      cwd: home, encoding: 'utf8', timeout: 600000,
      env: { PATH: process.env.PATH, HOME: home, HOJA_RUNNER_SERVICE: 'none', HOJA_RUNNER_NAME: 'office-mac',
        HOJA_CODER_MODEL_FACTORY: 'coder_scripted_model:model', PYTHONPATH: runnerTests },
    });
    ok(installed.status === 0 && /Connected: office-mac/.test(installed.stdout), 'the command installs, connects and starts the runner',
      `${((Date.now() - started) / 1000).toFixed(1)}s ${(installed.stderr || '').slice(-300)}`);
    await steps.locator('[data-connect-machine="connected"]').waitFor({ timeout: 20000 });
    ok((await steps.locator('[data-connect-machine="connected"]').textContent()).includes('office-mac is connected'),
      'the machine appears on the page by itself');
    ok((await steps.locator('[data-connect-machine="connected"]').textContent()).includes('Hoja Coder'),
      'it says the machine can run Hoja Coder');
    ok((await steps.locator('[data-connect-machine="connected"]').textContent()).includes(`works in ${code}`),
      'it says where the machine works (the ~/code it was given)');
    const create = steps.locator('[data-step="agent"]').getByRole('button', { name: 'New agent' });
    ok(await create.isEnabled(), 'step 2 unlocks');
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(shots, 'mf-3-connected.png') });

    await create.click();
    const dialog = page.getByRole('dialog', { name: 'New agent' });
    await dialog.getByLabel('Agent name').fill('Builder');
    await dialog.getByRole('button', { name: 'Create and set up' }).click();
    const setup = page.locator('section[aria-label="Setup"]');
    await setup.getByText('Get Builder working').waitFor({ timeout: 15000 });
    const stepState = (id) => setup.locator(`[data-setup-step="${id}"]`).getAttribute('data-setup-state');
    const doneSoon = (id, ms = 15000) => until(async () => (await stepState(id)) === 'done', ms, `${id} done`);
    ok((await setup.locator('[data-setup-step]').first().getAttribute('data-setup-step')) === 'machine', 'the agent checklist starts with the machine');
    await setup.getByRole('button', { name: /Trebol/ }).click();
    await doneSoon('teams');
    await setup.getByRole('button', { name: /Hoja Coder/ }).click();
    await doneSoon('harness');
    await doneSoon('machine');
    ok(true, 'the machine step is already done: office-mac runs Hoja Coder');
    await setup.getByRole('button', { name: /Claude Sonnet 5\.5/ }).click();
    await doneSoon('model');
    await setup.getByLabel('ANTHROPIC_API_KEY').fill('sk-test-not-a-real-key');
    await setup.getByRole('button', { name: 'Save key' }).click();
    await doneSoon('key');
    // Give the agent the repo, the way a person would: in "Where it can work".
    await setup.locator('[data-setup-step="teams"] > button').click();
    await setup.getByLabel('Repository').fill(remote);
    await setup.locator('[data-setup-repos]').getByRole('button', { name: 'Add' }).click();
    await setup.locator(`[data-repo-grant="${remote}"]`).waitFor({ timeout: 10000 });
    ok((await setup.locator(`[data-repo-grant="${remote}"]`).textContent()).includes('agent/'),
      'the repo is granted with a per-task branch prefix');
    await page.screenshot({ path: path.join(shots, 'mf-4-setup.png'), fullPage: true });

    await setup.locator('[data-setup-step="try"] > button').click();
    await setup.getByRole('button', { name: 'Send a test task' }).click();
    const card = page.locator('[data-style-hooks~="agent-ask"]');
    await card.waitFor({ timeout: 60000 });
    await card.getByLabel('Postgres', { exact: false }).check();
    await card.getByRole('button', { name: 'Send answer' }).click();
    const gql = async (query) => {
      const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query }) });
      const body = await res.json(); if (body.errors) throw new Error(body.errors[0].message); return body.data;
    };
    const finished = await until(async () => {
      const nodes = (await gql('{ agentLeases(first: 5) { nodes { id state finishState issue { identifier } } } }')).agentLeases.nodes;
      return nodes.find((n) => n.finishState) || null;
    }, 90000, 'the run to finish');
    ok(finished.finishState === 'completed', 'the test task ran on office-mac, asked, was answered and finished', finished.finishState);
    const branch = `agent/${finished.issue.identifier.toLowerCase()}`;
    const demo = path.join(code, 'Demo');
    ok(git(['branch', '--format=%(refname:short)'], demo).split('\n').includes(branch),
      `the work happened on its own branch in the existing checkout (${branch})`);
    ok(git(['rev-parse', '--abbrev-ref', 'HEAD'], demo) === 'main', 'the checkout itself stayed on main');
    ok(!fs.existsSync(path.join(code, 'worktrees', 'Demo')) || fs.readdirSync(path.join(code, 'worktrees', 'Demo')).length === 0,
      'the task\'s worktree was removed afterwards');
    ok(!fs.existsSync(path.join(code, 'demo')), 'no second clone was made');
    const run = (await gql(`{ agentRun(id: "${finished.id}") { session { state verdict } logs { source level kind message } } }`)).agentRun;
    ok(run.logs.some((l) => l.kind === 'session.action' && /write_file/.test(l.message)), 'the run\'s logs show what it did');
    ok(run.session.verdict === 'uncertain', 'it changed the repo without a pull request, so it waits for review instead of passing', run.session.verdict);
    await page.screenshot({ path: path.join(shots, 'mf-5-done.png'), fullPage: true });

    await page.goto(`${base}/workspace/agents/runners`);
    await page.getByText('office-mac').first().waitFor({ timeout: 15000 });
    ok(await page.getByRole('tab', { name: 'Machines' }).count() > 0, 'the Machines tab lists the machine');
    await page.getByText('office-mac').first().click();
    await page.locator('[data-work-root]').waitFor({ timeout: 15000 });
    ok((await page.locator('[data-work-root]').getAttribute('data-work-root')) === code, 'the machine page says where it works');
    await page.screenshot({ path: path.join(shots, 'mf-6-machine.png'), fullPage: true });

  } catch (e) {
    fail++; console.log(`FAIL ${e.message}`);
    try { console.log(fs.readFileSync(path.join(home, '.hoja/runner/runnerd.log'), 'utf8').slice(-1500)); } catch { /* none */ }
  } finally {
    if (browser) await browser.close();
    try { process.kill(Number(fs.readFileSync(path.join(home, '.hoja/runner/runnerd.pid'), 'utf8'))); } catch { /* not started */ }
    server.kill();
    console.log(`\n${pass} passed, ${fail} failed. Screenshots: ${shots}`);
    process.exit(fail ? 1 : 0);
  }
})();
