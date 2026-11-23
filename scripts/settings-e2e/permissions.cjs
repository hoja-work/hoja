#!/usr/bin/env node
// Settings → Permissions, end to end (HOJA-734).
// Admin: every member-capability switch saves instantly (optimistic), survives a reload and is
// read back from the API; the grant really reaches a member (viewerCapabilities); Undo from the
// toast restores; a refused save rolls the switch back with an error and Retry.
// Member: page is admin-only and the API refuses workspacePermissions with FORBIDDEN.
// Screenshots light/dark at 1440 and 390; no horizontal overflow at 320. Restores all grants.
const path = require('node:path');
const fs = require('node:fs');

const SECTION = 'permissions';

function loadPlaywright() {
  for (const c of [process.env.PLAYWRIGHT_MODULE, 'playwright', require('node:path').join(__dirname, '../../local-e2e/node_modules/playwright')].filter(Boolean)) {
    try { return require(c); } catch { /* next */ }
  }
  throw new Error('playwright not found; set PLAYWRIGHT_MODULE');
}

async function api(baseUrl, token, query, variables) {
  const r = await fetch(`${baseUrl}/graphql`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify({ query, variables }),
  });
  return r.json();
}

async function shots(browser, baseUrl, token, dir, prefix) {
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    for (const theme of ['light', 'dark']) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
      const p = await ctx.newPage();
      await p.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await p.waitForSelector('[data-settings-page]');
      await p.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 }).catch(() => {});
      await p.waitForLoadState('networkidle').catch(() => {});
      await p.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 }).catch(() => {});
      await p.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
      await p.waitForTimeout(300);
      await p.screenshot({ path: path.join(dir, `${prefix}-${w}-${theme}.png`), fullPage: true });
      await ctx.close();
    }
  }
}

const Q = '{workspacePermissions{capability label memberAllowed}}';
const SET = 'mutation($c:String!,$on:Boolean!){workspacePermissionUpdate(capability:$c,memberAllowed:$on){memberAllowed}}';
const lower = (s) => s.charAt(0).toLowerCase() + s.slice(1);

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = loadPlaywright();
  const passed = [], failed = [];
  const check = (name, cond, extra = '') => (cond ? passed.push(name) : failed.push(`${name}${extra ? ` (${extra})` : ''}`));
  const step = async (name, fn) => {
    try { await fn(); passed.push(name); } catch (e) { failed.push(`${name}: ${String(e.message || e).split('\n')[0]}`); }
  };
  const grants = async () => Object.fromEntries((await api(baseUrl, adminToken, Q)).data.workspacePermissions.map((r) => [r.capability, r.memberAllowed]));
  const rows = (await api(baseUrl, adminToken, Q)).data.workspacePermissions;
  const original = Object.fromEntries(rows.map((r) => [r.capability, r.memberAllowed]));
  const browser = await chromium.launch();
  const open = async (token, viewport = { width: 1440, height: 900 }) => {
    const ctx = await browser.newContext({ viewport });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    const p = await ctx.newPage();
    p.errors = [];
    p.on('pageerror', (e) => p.errors.push(e.message));
    return p;
  };
  const goto = async (p) => {
    await p.goto(`${baseUrl}/workspace/settings/${SECTION}`);
    await p.getByRole('heading', { level: 1, name: 'Permissions' }).waitFor({ timeout: 20000 });
    await p.getByRole('switch').first().waitFor({ timeout: 15000 });
  };
  const sw = (p, r) => p.getByRole('switch', { name: `Members can ${lower(r.label)}` });
  const until = async (fn, what) => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) { if (await fn()) return; await new Promise((r) => setTimeout(r, 150)); }
    throw new Error(what);
  };

  try {
    const page = await open(adminToken);
    await goto(page);
    await step('admin: explains that admins hold every capability', async () => {
      await page.getByText('Admins always hold every capability').waitFor();
      await page.getByRole('button', { name: 'Manage admins' }).waitFor();
    });
    await step('admin: descriptions come from the API', async () => {
      for (const r of rows) {
        const desc = (await api(baseUrl, adminToken, '{workspacePermissions{capability description}}')).data.workspacePermissions.find((x) => x.capability === r.capability).description;
        await page.getByText(desc, { exact: true }).waitFor({ timeout: 3000 });
      }
    });
    for (const r of rows) {
      await step(`admin: "${r.label}" switch saves instantly and is read back from the API`, async () => {
        const before = (await sw(page, r).getAttribute('aria-checked')) === 'true';
        await sw(page, r).click();
        if (((await sw(page, r).getAttribute('aria-checked')) === 'true') === before) throw new Error('not optimistic');
        await until(async () => (await grants())[r.capability] === !before, 'API not updated');
      });
    }
    await step('admin: every change survives a reload', async () => {
      await page.reload();
      await goto(page);
      for (const r of rows) {
        const on = (await sw(page, r).getAttribute('aria-checked')) === 'true';
        if (on === original[r.capability]) throw new Error(`${r.capability} reverted after reload`);
      }
    });
    await step('member: a granted capability really reaches the member', async () => {
      const g = await grants();
      const caps = (await api(baseUrl, memberToken, '{viewerCapabilities}')).data.viewerCapabilities;
      for (const [c, on] of Object.entries(g)) if (caps.includes(c) !== on) throw new Error(`${c}: grant ${on}, member holds ${caps.includes(c)}`);
    });
    await step('admin: Undo in the toast restores the previous value', async () => {
      const r = rows[0];
      const before = (await grants())[r.capability];
      await sw(page, r).click();
      await until(async () => (await grants())[r.capability] === !before, 'first change not saved');
      await page.getByRole('button', { name: 'Undo' }).last().click();
      await until(async () => (await grants())[r.capability] === before, 'Undo not saved');
      await until(async () => ((await sw(page, r).getAttribute('aria-checked')) === 'true') === before, 'switch not restored');
    });
    await step('admin: refused save rolls the switch back with an error and Retry', async () => {
      const r = rows[1];
      const before = await sw(page, r).getAttribute('aria-checked');
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('workspacePermissionUpdate') ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ errors: [{ message: 'Unknown capability', extensions: { code: 'BAD_USER_INPUT' } }] }) }) : route.continue()));
      await sw(page, r).click();
      await page.getByRole('alert').filter({ hasText: 'Unknown capability' }).waitFor();
      if ((await sw(page, r).getAttribute('aria-checked')) !== before) throw new Error('switch did not roll back');
      await page.unroute('**/graphql');
      await page.getByRole('button', { name: 'Retry' }).click();
      await until(async () => (await grants())[r.capability] === (before !== 'true'), 'Retry not saved');
    });
    check('admin: no page errors', page.errors.length === 0, page.errors.join(' | '));
    await page.context().close();

    const member = await open(memberToken);
    await step('member: page is admin-only', async () => {
      await member.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await member.getByText('Only workspace admins can open this page').waitFor({ timeout: 20000 });
      if (await member.getByRole('switch').count()) throw new Error('switches shown to member');
    });
    await step('member: API refuses the read and the write', async () => {
      const r = await api(baseUrl, memberToken, Q);
      const w = await api(baseUrl, memberToken, SET, { c: 'task.delete', on: true });
      if (r.errors?.[0]?.extensions?.code !== 'FORBIDDEN' || w.errors?.[0]?.extensions?.code !== 'FORBIDDEN') throw new Error(JSON.stringify([r, w]));
    });
    await member.context().close();

    for (const [c, on] of Object.entries(original)) await api(baseUrl, adminToken, SET, { c, on });
    await step('no horizontal overflow at 320px', async () => {
      const p = await open(adminToken, { width: 320, height: 700 });
      await goto(p);
      const w = await p.evaluate(() => document.documentElement.scrollWidth);
      await p.context().close();
      if (w > 320) throw new Error(`scrollWidth ${w}`);
    });
    await step('screenshots light/dark at 1440 and 390', () => shots(browser, baseUrl, adminToken, screenshotsDir, SECTION));
  } finally {
    for (const [c, on] of Object.entries(original)) await api(baseUrl, adminToken, SET, { c, on });
    await browser.close();
  }
  return { passed, failed };
}

module.exports = { run };

if (require.main === module) {
  run({
    baseUrl: process.env.SETTINGS_E2E_URL || 'http://127.0.0.1:5834',
    adminToken: process.env.SETTINGS_E2E_ADMIN_TOKEN,
    memberToken: process.env.SETTINGS_E2E_MEMBER_TOKEN,
    screenshotsDir: process.env.SETTINGS_E2E_SHOTS || require('node:path').join(require('node:os').tmpdir(), 'settings-e2e', SECTION),
  }).then(({ passed, failed }) => {
    for (const p of passed) console.log(`  ok   ${p}`);
    for (const f of failed) console.log(`  FAIL ${f}`);
    console.log(`${passed.length} passed, ${failed.length} failed`);
    process.exit(failed.length ? 1 : 0);
  }, (e) => { console.error(e); process.exit(1); });
}
