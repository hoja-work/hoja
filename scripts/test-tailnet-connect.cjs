#!/usr/bin/env node
/* Connect a machine from the tailnet, no terminal (HOJA-602).
 *
 *   Get started -> From Tailscale -> policy + OAuth client -> paste -> the tailnet's machines
 *   -> office-mac: Use for agents -> Allow (as a user, a folder, always) -> it installs while you
 *   watch -> the runner is online -> step 2 unlocks.  build-box: Tailscale SSH off -> says so.
 *
 * Tailscale is faked (scripts/fake_tailnet_serve.py); the installer and the runner are real, run
 * in a throwaway HOME as a background process. Needs app/.venv, frontend/dist, the runner wheel
 * (scripts/build-runner-wheel.sh) and Playwright (PLAYWRIGHT_MODULE or local-e2e).
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../local-e2e/node_modules/playwright');

const repo = path.resolve(__dirname, '..');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tailnet-'));
const home = path.join(dir, 'home');
fs.mkdirSync(home);
const db = path.join(dir, 't.db');
const shots = process.env.SHOTS || dir;
const token = 'tailnet-admin';
const py = path.join(repo, '.venv/bin/python');
assert.ok(fs.readdirSync(path.join(repo, 'downloads')).some((f) => f.endsWith('.whl')),
  'build the wheel first: scripts/build-runner-wheel.sh');
const r = spawnSync(py, ['-c', `from hoja_server.db import make_engine, migrate, seed_tenant
e = make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada',
            user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(r.status, 0, r.stderr);

const port = 19700 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(py, ['scripts/fake_tailnet_serve.py', 'serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], {
  cwd: repo, stdio: 'ignore',
  env: { ...process.env, HOJA_WEB_ROOT: path.join(repo, 'frontend/dist'), PYTHONPATH: repo, HOJA_SECRET_KEY: 'tn',
    FAKE_TAILNET_HOME: home, FAKE_TAILNET_PYTHONPATH: path.resolve(repo, '../runner/tests') },
});
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
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    await page.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${base}/workspace/agents`);
    await page.getByRole('button', { name: 'Turn on Agents' }).click();
    const steps = page.locator('[data-getting-started="steps"]');
    await steps.waitFor({ timeout: 15000 });

    await steps.getByRole('tab', { name: /From Tailscale/ }).click();
    const connect = steps.locator('[data-tailnet="connect"]');
    await connect.waitFor({ timeout: 10000 });
    const policy = JSON.parse(await connect.locator('[data-tailnet-policy]').textContent());
    ok(policy.ssh[0].src[0] === 'tag:hoja' && policy.ssh[0].dst[0] === 'tag:hoja-machine', 'it shows the exact policy lines to add');
    await page.screenshot({ path: path.join(shots, 'tn-1-connect.png') });

    await connect.getByLabel('Client ID').fill('kfake');
    await connect.getByLabel('Client secret').fill('tskey-client-kfake-WRONG');
    await connect.getByRole('button', { name: 'Connect Tailscale' }).click();
    await connect.getByRole('alert').waitFor({ timeout: 10000 });
    ok((await connect.getByRole('alert').textContent()).includes('refused'), 'a wrong secret is refused, in words');
    await connect.getByLabel('Client secret').fill('tskey-client-kfake-SECRET');
    await connect.getByRole('button', { name: 'Connect Tailscale' }).click();

    const list = steps.locator('[data-tailnet="connected"]');
    await list.waitFor({ timeout: 10000 });
    await list.locator('[data-tailnet-node="running"]').waitFor({ timeout: 10000 });
    ok((await list.textContent()).includes('Hoja is on tail1234.ts.net as hoja-acme'), 'Hoja says it joined the tailnet');
    const names = await list.locator('[data-tailnet-device]').evaluateAll((els) => els.map((e) => e.getAttribute('data-tailnet-device')));
    ok(JSON.stringify(names) === JSON.stringify(['build-box', 'office-mac', 'pixel']), 'the tailnet\'s machines are listed, online first', names.join(','));
    ok(await list.locator('[data-tailnet-device="pixel"]').getByRole('button', { name: 'Use for agents' }).isDisabled(), 'an offline machine cannot be picked');
    await page.screenshot({ path: path.join(shots, 'tn-2-machines.png') });

    const office = list.locator('[data-tailnet-device="office-mac"]');
    await office.getByRole('button', { name: 'Use for agents' }).click();
    const allow = office.locator('[data-tailnet-allow]');
    ok((await allow.textContent()).includes('Allow Hoja to connect to office-mac?'), 'it asks to be allowed, Muse-style');
    ok(await allow.getByRole('button', { name: 'Allow and set up' }).isDisabled(), 'not without saying which user');
    await allow.getByLabel('User on the machine').fill('ada');
    await allow.getByLabel('Work folder').fill('~/code');
    await page.screenshot({ path: path.join(shots, 'tn-3-allow.png') });
    const started = Date.now();
    await allow.getByRole('button', { name: 'Allow and set up' }).click();
    await office.locator('[data-tailnet-log]').waitFor({ timeout: 20000 });
    ok(true, 'the install streams onto the page');
    await page.screenshot({ path: path.join(shots, 'tn-4-installing.png') });
    await steps.locator('[data-connect-machine="connected"], [data-tailnet-device="office-mac"][data-state="running"]').first().waitFor({ timeout: 600000 });
    ok(true, 'office-mac runs agents, set up with no terminal', `${((Date.now() - started) / 1000).toFixed(1)}s`);
    await until(async () => !(await steps.locator('[data-step="agent"]').getByRole('button', { name: 'New agent' }).isDisabled()), 30000, 'step 2');
    ok(true, 'step 2 (create an agent) unlocks');
    const runners = await (await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ query: '{ runners { nodes { name status workRoot } } }' }) })).json();
    const mac = runners.data.runners.nodes.find((n) => n.name === 'office-mac');
    ok(mac && mac.status === 'online' && mac.workRoot && mac.workRoot.endsWith('/code'), 'the runner is online and works in ~/code', JSON.stringify(mac));
    await page.screenshot({ path: path.join(shots, 'tn-5-running.png') });

    const build = steps.locator('[data-tailnet-device="build-box"]');
    if (!(await build.count())) await steps.getByRole('tab', { name: /From Tailscale/ }).click();
    await build.getByRole('button', { name: 'Use for agents' }).click();
    await build.getByLabel('User on the machine').fill('ci');
    await build.getByLabel('Just this once').check();
    await build.getByRole('button', { name: 'Allow and set up' }).click();
    await build.locator('[data-state="failed"], [data-tailnet-log]').first().waitFor({ timeout: 20000 });
    await until(async () => (await build.getAttribute('data-state')) === 'failed', 20000, 'the failure');
    ok((await build.locator('[data-tailnet-log]').textContent()).includes('sudo tailscale set --ssh'), 'a machine without Tailscale SSH says how to fix it');
    ok(await build.getByRole('button', { name: 'Try again' }).isVisible(), 'and offers to try again');
    await page.screenshot({ path: path.join(shots, 'tn-6-failed.png') });

    // The Machines tab offers the same tailnet list under "Connect a machine".
    await page.getByRole('link', { name: 'Machines' }).or(page.getByRole('tab', { name: 'Machines' })).or(page.getByText('Machines', { exact: true })).first().click();
    await page.locator('[data-action="enroll-runner"]').click();
    const panel = page.locator('[data-enroll-tailnet] [data-tailnet="connected"]');
    await panel.waitFor({ timeout: 10000 });
    ok(await panel.locator('[data-tailnet-device="office-mac"][data-state="running"]').count() === 1, 'the Machines tab lists the tailnet too');
    await page.screenshot({ path: path.join(shots, 'tn-7-machines-tab.png') });
  } catch (e) {
    fail++; console.log(`FAIL ${e.message}`);
    try { const p = browser && browser.contexts()[0]?.pages()[0]; if (p) await p.screenshot({ path: path.join(shots, 'tn-fail.png') }); } catch { /* none */ }
  } finally {
    if (browser) await browser.close();
    try { process.kill(Number(fs.readFileSync(path.join(home, '.hoja/runner/runnerd.pid'), 'utf8'))); } catch { /* not started */ }
    server.kill();
    console.log(`\n${pass} passed, ${fail} failed. Screenshots: ${shots}`);
    process.exit(fail ? 1 : 0);
  }
})();
