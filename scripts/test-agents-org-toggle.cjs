// The workspace switch for the Agents screens (revision 76), driven in a real browser against a
// real server: no localStorage opt-in, so the only thing that can show Agents is the switch.
//
//   cd frontend && npm run build
//   node scripts/test-agents-org-toggle.cjs            # 1440x900
//   WIDTH=390 HEIGHT=844 node scripts/test-agents-org-toggle.cjs
//
// What it proves, in order: off by default for admin and member (no nav item; /workspace/agents
// shows the disabled page); a member does not see the switch; the admin turns it on in
// Settings -> Workspace and the nav item appears without a reload; a member's next load shows it;
// the change is in the audit log; turning it off hides it again.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
if (!fs.existsSync(path.join(web, 'index.html'))) throw new Error('build the frontend first');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'agents-org-toggle-'));
const db = path.join(dir, 'p.db');
// Fixture tokens written into this disposable database only.
const adminToken = 'agents-org-toggle-admin-fixture';
const memberToken = 'agents-org-toggle-member-fixture';
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  if (r.status !== 0) throw new Error(r.stderr);
  return r.stdout;
};
const orgId = run(`from hoja_server.db import make_engine,migrate,seed_tenant,user,api_key,new_id,now_iso,token_hash
e=make_engine(${JSON.stringify(db)}); migrate(e)
info=seed_tenant(e, org_name='Acme', team_key='ACME', team_name='Acme', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(adminToken)}, demo_issues=False)
ts=now_iso(); uid=new_id()
with e.begin() as c:
    c.execute(user.insert().values(id=uid, organization_id=info['org_id'], name='Sam Member', display_name='Sam Member',
              email='sam@example.test', active=True, admin=False, timezone='UTC', created_at=ts, updated_at=ts))
    c.execute(api_key.insert().values(id=new_id(), token_hash=token_hash(${JSON.stringify(memberToken)}), label='member',
              user_id=uid, organization_id=info['org_id'], created_at=ts))
print(info['org_id']); e.dispose()`).trim();

const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web, PYTHONPATH: repo }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const shots = process.env.SHOTS || path.join(scratch, 'agents-org-toggle');
const W = Number(process.env.WIDTH || 1440), H = Number(process.env.HEIGHT || 900);
const phone = W < 700;

async function open(browser, token) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  // Deliberately no hoja_agents_surface opt-in: the workspace switch is the only way in.
  await ctx.addInitScript(t => { localStorage.setItem('hoja_token', t); localStorage.removeItem('hoja_agents_surface'); }, token);
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  return page;
}
// The sidebar is a drawer on a phone; open it before looking for the nav item.
async function navHasAgents(page) {
  if (phone) {
    const toggle = page.getByRole('button', { name: 'Open navigation', exact: true });
    if (await toggle.count()) { await toggle.click(); await wait(300); }
  }
  return (await page.locator('[data-nav-item]', { hasText: /^Agents$/ }).count()) > 0;
}
async function features(token) {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify({ query: '{ workspaceFeatures { agentsEnabled } }' }) });
  return (await r.json()).data.workspaceFeatures.agentsEnabled;
}

(async () => {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/ready`)).ok) break; } catch {} await wait(250); }
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    /* ---- 1. off by default ---- */
    const admin = await open(browser, adminToken);
    await admin.goto(`${base}/workspace/my-issues`, { waitUntil: 'networkidle' });
    await admin.waitForSelector('[data-nav-item]', { timeout: 15000 });
    ok(!(await navHasAgents(admin)), 'off by default: the admin has no Agents nav item');
    await admin.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
    ok(await admin.locator('[data-surface="agents-disabled"]').count() === 1, 'off by default: /workspace/agents shows the disabled page, not the list');
    ok(await admin.locator('[data-surface="agents-list"]').count() === 0, 'off by default: the agents list does not render');

    const member = await open(browser, memberToken);
    await member.goto(`${base}/workspace/settings/workspace`, { waitUntil: 'networkidle' });
    await wait(500);
    ok(await member.locator('[data-workspace-features] [role="switch"]').count() === 0, 'a member does not see the switch');

    /* ---- 2. the admin turns it on ---- */
    await admin.goto(`${base}/workspace/settings/workspace`, { waitUntil: 'networkidle' });
    const sw = admin.locator('[data-workspace-features] [role="switch"][aria-label="Agents"]');
    await sw.waitFor({ timeout: 15000 });
    await admin.waitForFunction(() => !document.querySelector('[data-workspace-features] [role="switch"]').disabled);
    ok(!(await sw.isChecked()), 'the switch starts off');
    const box = await sw.boundingBox();
    ok(box && box.x >= 0 && box.x + box.width <= W + 1, 'the switch is inside the viewport', box ? `${Math.round(box.x)}+${Math.round(box.width)} of ${W}` : 'no box');
    await admin.screenshot({ path: path.join(shots, `settings-off-${W}.png`) });
    await sw.click();
    await admin.locator('[data-workspace-features] [role="status"]', { hasText: 'Agents are on for everyone' }).waitFor({ timeout: 10000 });
    ok(await sw.isChecked(), 'after the click the switch is on');
    ok(await features(adminToken) === true, 'the server stored it (workspaceFeatures reads true)');
    await admin.screenshot({ path: path.join(shots, `settings-on-${W}.png`) });
    // Back to the app in-page (history navigation, no reload): the nav reads the switch it just set.
    const loads = await admin.evaluate(() => performance.getEntriesByType('navigation').length);
    // On a phone, Back to app lives in the settings drawer, and that drawer currently renders under its
    // own scrim (z-20 from the 850px rule beats z-35), so a pointer click cannot reach it. That bug is
    // older than this switch; here the button is clicked through the DOM so this run tests the switch.
    if (phone) await admin.evaluate(() => document.querySelector('[data-settings-back]').click());
    else await admin.getByText('Back to app', { exact: true }).locator('visible=true').first().click();
    await admin.waitForSelector('[data-nav-item]', { timeout: 15000 });
    ok(await navHasAgents(admin), 'the Agents nav item appears for the admin without a reload');
    ok(await admin.evaluate(() => performance.getEntriesByType('navigation').length) === loads, 'no page reload happened');
    await admin.screenshot({ path: path.join(shots, `admin-nav-on-${W}.png`) });

    /* ---- 3. the member's next load shows it ---- */
    await member.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
    await member.waitForSelector('[data-surface="agents-list"], [data-surface="agents-disabled"]', { timeout: 15000 });
    ok(await member.locator('[data-surface="agents-disabled"]').count() === 0, 'member: /workspace/agents is no longer the disabled page');
    ok(await navHasAgents(member), 'member: the Agents nav item is shown');
    await member.screenshot({ path: path.join(shots, `member-on-${W}.png`) });

    /* ---- 4. audited ---- */
    const audit = JSON.parse(run(`import json
from sqlalchemy import select
from hoja_server.db import make_engine, audit_event
e=make_engine(${JSON.stringify(db)})
with e.connect() as c:
    rows=[dict(r._mapping) for r in c.execute(select(audit_event.c.action, audit_event.c.detail, audit_event.c.target_id).where(audit_event.c.action=='workspace.features.change'))]
print(json.dumps(rows, default=str))`));
    const detail = audit[0] && (typeof audit[0].detail === 'string' ? JSON.parse(audit[0].detail) : audit[0].detail);
    ok(audit.length === 1 && detail?.agentsEnabled?.before === false && detail?.agentsEnabled?.after === true,
      'exactly one audit row, before false -> after true', JSON.stringify(audit));
    ok(audit[0]?.target_id === orgId, 'the audit row names this workspace');

    /* ---- 5. off again ---- */
    await admin.goto(`${base}/workspace/settings/workspace`, { waitUntil: 'networkidle' });
    await sw.waitFor({ timeout: 15000 });
    await admin.waitForFunction(() => document.querySelector('[data-workspace-features] [role="switch"]')?.checked === true);
    await sw.click();
    await admin.locator('[data-workspace-features] [role="status"]', { hasText: 'Agents are off' }).waitFor({ timeout: 10000 });
    ok(await features(adminToken) === false, 'turned off: the server stored it');
    await member.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
    await member.waitForSelector('[data-surface="agents-disabled"]', { timeout: 15000 });
    ok(true, 'turned off: the member is back on the disabled page');

    ok(admin.errors.length === 0 && member.errors.length === 0, 'no page errors', [...admin.errors, ...member.errors].join(' | '));
  } catch (e) {
    ok(false, 'run completed', e.message);
  } finally {
    await browser.close();
    server.kill();
  }
  console.log(`screenshots: ${shots}`);
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
