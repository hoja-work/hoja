#!/usr/bin/env node
/* The first minute with Agents, as an admin of a workspace that never turned them on (HOJA-602).
 *
 *   sidebar "Agents" -> "Turn on Agents" -> "New agent" -> a name -> "Create and set up"
 *   -> the agent's page, open on its setup checklist
 *
 * and a member who is not an admin does not get the entry while the switch is off.
 * Needs app/.venv, app/frontend/dist and Playwright (PLAYWRIGHT_MODULE or local-e2e).
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../local-e2e/node_modules/playwright');

const repo = path.resolve(__dirname, '..');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agents-first-'));
const db = path.join(dir, 'first.db');
const shots = process.env.SHOTS || dir;
const token = 'agents-first-run-admin';
const memberToken = 'agents-first-run-member';
const py = path.join(repo, '.venv/bin/python');
const r = spawnSync(py, ['-c', `from hoja_server.db import make_engine, migrate, seed_tenant, user, api_key, new_id, now_iso, token_hash
e = make_engine(${JSON.stringify(db)}); migrate(e)
info = seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada',
                   user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
ts = now_iso(); uid = new_id()
with e.begin() as c:
    c.execute(user.insert().values(id=uid, organization_id=info['org_id'], name='Sam', display_name='Sam',
        email='sam@example.test', active=True, admin=False, timezone='UTC', created_at=ts, updated_at=ts))
    c.execute(api_key.insert().values(id=new_id(), token_hash=token_hash(${JSON.stringify(memberToken)}),
        label='member', user_id=uid, organization_id=info['org_id'], created_at=ts))
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(r.status, 0, r.stderr);

const port = 19700 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: path.join(repo, 'frontend/dist'), PYTHONPATH: repo }, stdio: 'ignore' });
const wait = (ms) => new Promise((res) => setTimeout(res, ms));
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

(async () => {
  let browser;
  try {
    for (let i = 0; i < 100; i++) { if ((await fetch(`${base}/ready`).catch(() => null))?.ok) break; await wait(300); }
    browser = await chromium.launch();
    const open = async (who) => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      await page.addInitScript((t) => localStorage.setItem('hoja_token', t), who);
      await page.goto(`${base}/`);
      await page.waitForSelector('[data-nav-item]', { timeout: 20000 });
      await wait(800);
      return page;
    };
    const navAgents = (page) => page.locator('[data-nav-item]', { hasText: /^Agents$/ });

    const member = await open(memberToken);
    ok(await navAgents(member).count() === 0, 'a member does not see Agents while the workspace has them off');
    await member.close();

    const page = await open(token);
    ok(await navAgents(page).count() === 1, 'an admin sees Agents in the sidebar even while they are off');
    await navAgents(page).click();
    const turnOn = page.getByRole('button', { name: 'Turn on Agents' });
    await turnOn.waitFor({ timeout: 10000 });
    ok(true, 'the off page offers to turn them on, in place');
    await page.screenshot({ path: path.join(shots, 'first-1-off.png') });
    await turnOn.click();
    const create = page.getByRole('button', { name: 'New agent' }).first();
    await create.waitFor({ timeout: 10000 });
    ok(true, 'one click later the Agents list is there');
    await page.screenshot({ path: path.join(shots, 'first-2-empty.png') });
    await create.click();
    const dialog = page.getByRole('dialog', { name: 'New agent' });
    ok(await dialog.locator('input').count() === 1, 'the new-agent dialog asks for one thing: a name');
    await page.screenshot({ path: path.join(shots, 'first-3-dialog.png') });
    await dialog.getByLabel('Agent name').fill('Builder');
    await dialog.getByRole('button', { name: 'Create and set up' }).click();
    await page.getByText('Get Builder working').waitFor({ timeout: 15000 });
    ok(true, 'creating it lands on its setup checklist');
    await page.screenshot({ path: path.join(shots, 'first-4-setup.png') });
  } catch (e) {
    fail++; console.log(`FAIL ${e.message}`);
  } finally {
    if (browser) await browser.close();
    server.kill();
    console.log(`\n${pass} passed, ${fail} failed. Screenshots: ${shots}`);
    process.exit(fail ? 1 : 0);
  }
})();
