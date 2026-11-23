#!/usr/bin/env node
// Settings → General (section id "workspace"), end to end (HOJA-734).
// Admin: rename (Enter saves, Undo toast restores, Escape reverts, empty is refused), change the
// URL key (client validation, confirm dialog, cancel keeps the old key, confirm saves, live server
// refuses a malformed key with BAD_USER_INPUT, a duplicate shows a plain message), Agents switch
// (instant, reload, API read-back), forced failures roll back with a visible error and Retry.
// Member: page is admin-only and the API refuses workspaceUpdate with FORBIDDEN.
// Screenshots light/dark at 1440 and 390; no horizontal overflow at 320. Restores everything.
const path = require('node:path');
const fs = require('node:fs');

const SECTION = 'workspace';

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

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = loadPlaywright();
  const passed = [], failed = [];
  const check = (name, cond, extra = '') => (cond ? passed.push(name) : failed.push(`${name}${extra ? ` (${extra})` : ''}`));
  const step = async (name, fn) => {
    try { await fn(); passed.push(name); } catch (e) { failed.push(`${name}: ${String(e.message || e).split('\n')[0]}`); }
  };
  const stamp = Date.now().toString(36);
  const ws = async () => (await api(baseUrl, adminToken, '{workspace{name urlKey} workspaceFeatures{agentsEnabled}}')).data;
  const original = await ws();
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
    await p.getByRole('heading', { level: 1, name: 'General' }).waitFor({ timeout: 20000 });
    await p.getByRole('switch', { name: 'Agents' }).waitFor({ timeout: 15000 });
  };
  const waitValue = async (loc, v) => {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) { if ((await loc.inputValue().catch(() => '')) === v) return; await new Promise((r) => setTimeout(r, 150)); }
    throw new Error(`field shows ${await loc.inputValue()} instead of ${v}`);
  };
  const saved = (p) => p.locator('[data-save-status="saved"]').first().waitFor({ timeout: 8000 });

  try {
    const page = await open(adminToken);
    await goto(page);
    const name = page.getByLabel('Name', { exact: true });
    const url = page.getByLabel('URL', { exact: true });
    const NEW_NAME = `Acme e2e ${stamp}`;
    const NEW_KEY = `acme-e2e-${stamp}`;

    await step('admin: rename saves on Enter, survives reload, read back from API', async () => {
      await name.fill(NEW_NAME);
      await name.press('Enter');
      await saved(page);
      if ((await ws()).workspace.name !== NEW_NAME) throw new Error('API name not updated');
      await page.reload();
      await waitValue(name, NEW_NAME);
    });

    await step('admin: rename offers Undo, which restores the old name', async () => {
      await name.fill(`${NEW_NAME} b`);
      await name.press('Enter');
      await saved(page);
      await page.getByRole('button', { name: 'Undo' }).click();
      const deadline = Date.now() + 8000;
      while ((await ws()).workspace.name !== NEW_NAME && Date.now() < deadline) await page.waitForTimeout(200);
      if ((await ws()).workspace.name !== NEW_NAME) throw new Error('Undo did not restore');
      await page.waitForFunction((v) => [...document.querySelectorAll('input')].some((i) => i.value === v), NEW_NAME, { timeout: 8000 });
    });

    await step('admin: Escape reverts without saving', async () => {
      await name.fill('Should not save');
      await name.press('Escape');
      await name.blur();
      await page.waitForTimeout(600);
      if ((await name.inputValue()) !== NEW_NAME) throw new Error(`field shows ${await name.inputValue()}`);
      if ((await ws()).workspace.name !== NEW_NAME) throw new Error('Escape saved the draft');
    });

    await step('admin: empty name is refused before any request', async () => {
      await name.fill('   ');
      await name.press('Enter');
      await page.getByRole('alert').filter({ hasText: 'Enter a name for the workspace' }).waitFor();
      if ((await ws()).workspace.name !== NEW_NAME) throw new Error('blank name reached API');
      await name.press('Escape');
    });

    await step('admin: URL key validated client-side with the server rules', async () => {
      for (const [bad, msg] of [['-lead', /start or end with a hyphen/], ['two--dash', /two hyphens/], ['has space', /letters, numbers and hyphens/], ['x'.repeat(49), /48 characters/]]) {
        await url.fill(bad);
        await url.press('Enter');
        await page.getByRole('alert').filter({ hasText: msg }).waitFor({ timeout: 4000 });
        if (await page.getByRole('alertdialog').count()) throw new Error(`confirm opened for ${bad}`);
      }
      await url.press('Escape');
    });

    await step('admin: URL preview follows the draft', async () => {
      await url.fill(NEW_KEY.toUpperCase());
      const preview = await page.locator('[data-url-preview]').innerText();
      if (!preview.includes(`/${NEW_KEY}/issue/`)) throw new Error(preview);
      await url.press('Escape');
    });

    await step('admin: URL change asks first; Cancel keeps the old key', async () => {
      await url.fill(NEW_KEY);
      await url.press('Enter');
      const dlg = page.getByRole('alertdialog', { name: 'Change the workspace URL?' });
      await dlg.getByText(/stop working/).waitFor();
      await dlg.getByRole('button', { name: 'Cancel' }).click();
      await dlg.waitFor({ state: 'hidden' });
      if ((await url.inputValue()) !== original.workspace.urlKey) throw new Error(`field shows ${await url.inputValue()}`);
      if ((await ws()).workspace.urlKey !== original.workspace.urlKey) throw new Error('cancel saved');
    });

    await step('admin: confirmed URL change saves, survives reload, read back from API', async () => {
      await url.fill(NEW_KEY);
      await url.press('Enter');
      const dlg = page.getByRole('alertdialog', { name: 'Change the workspace URL?' });
      await dlg.getByRole('button', { name: 'Change URL' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await saved(page);
      if ((await ws()).workspace.urlKey !== NEW_KEY) throw new Error('API key not updated');
      await page.reload();
      await waitValue(url, NEW_KEY);
    });

    await step('live server: malformed key is BAD_USER_INPUT, not success:false', async () => {
      const r = await api(baseUrl, adminToken, 'mutation($i:WorkspaceUpdateInput!){workspaceUpdate(input:$i){success}}', { i: { urlKey: 'bad--key' } });
      if (r.errors?.[0]?.extensions?.code !== 'BAD_USER_INPUT') throw new Error(JSON.stringify(r));
    });

    await step('admin: a duplicate key shows a plain message and rolls back', async () => {
      await page.route('**/graphql', async (route) => {
        if ((route.request().postData() || '').includes('workspaceUpdate'))
          return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: { workspaceUpdate: null }, errors: [{ message: 'Workspace key already exists', extensions: { code: 'BAD_USER_INPUT', field: 'urlKey' } }] }) });
        return route.continue();
      });
      await url.fill(`taken-${stamp}`);
      await url.press('Enter');
      await page.getByRole('alertdialog').getByRole('button', { name: 'Change URL' }).click();
      await page.getByRole('alert').filter({ hasText: 'Another workspace already uses this URL' }).waitFor();
      await page.unroute('**/graphql');
      if ((await url.inputValue()) !== NEW_KEY) throw new Error(`field shows ${await url.inputValue()}`);
    });

    await step('admin: network failure on rename rolls back with error and Retry', async () => {
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('workspaceUpdate') ? route.abort() : route.continue()));
      await name.fill(`${NEW_NAME} fail`);
      await name.press('Enter');
      await page.getByRole('alert').filter({ hasText: "Couldn't reach the server" }).waitFor();
      if ((await name.inputValue()) !== NEW_NAME) throw new Error(`field shows ${await name.inputValue()}`);
      const retry = page.locator('[data-setting-row]').filter({ has: name }).getByRole('button', { name: 'Retry' });
      await retry.waitFor();
      await page.unroute('**/graphql');
      await retry.click();
      await saved(page);
      if ((await ws()).workspace.name !== `${NEW_NAME} fail`) throw new Error('Retry did not save');
    });

    const agents = page.getByRole('switch', { name: 'Agents' });
    await step('admin: Agents switch saves instantly, survives reload, read back from API', async () => {
      const before = (await agents.getAttribute('aria-checked')) === 'true';
      await agents.click();
      await saved(page);
      if ((await ws()).workspaceFeatures.agentsEnabled !== !before) throw new Error('API not updated');
      await page.reload();
      await agents.waitFor();
      if (((await agents.getAttribute('aria-checked')) === 'true') !== !before) throw new Error('not kept after reload');
    });

    await step('admin: Agents switch failure rolls back with an error', async () => {
      const before = await agents.getAttribute('aria-checked');
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('workspaceFeaturesUpdate') ? route.fulfill({ status: 500, contentType: 'text/html', body: '<html>boom</html>' }) : route.continue()));
      await agents.click();
      await page.getByRole('alert').filter({ hasText: /server is unavailable/i }).waitFor();
      await page.unroute('**/graphql');
      if ((await agents.getAttribute('aria-checked')) !== before) throw new Error('switch did not roll back');
    });

    await step('admin: Teams link opens the Teams page', async () => {
      await page.getByRole('button', { name: 'Open Teams' }).click();
      await page.waitForURL(/\/workspace\/settings\/teams/);
    });
    check('admin: no page errors', page.errors.length === 0, page.errors.join(' | '));
    await page.context().close();

    const member = await open(memberToken);
    await step('member: page is admin-only', async () => {
      await member.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await member.getByText('Only workspace admins can open this page').waitFor({ timeout: 20000 });
      if (await member.getByLabel('Name', { exact: true }).count()) throw new Error('name field shown to member');
    });
    await step('member: API refuses workspaceUpdate with FORBIDDEN', async () => {
      const r = await api(baseUrl, memberToken, 'mutation($i:WorkspaceUpdateInput!){workspaceUpdate(input:$i){success}}', { i: { name: 'nope' } });
      if (r.errors?.[0]?.extensions?.code !== 'FORBIDDEN') throw new Error(JSON.stringify(r));
    });
    await member.context().close();

    // Restore before screenshots so they show the real workspace.
    await api(baseUrl, adminToken, 'mutation($i:WorkspaceUpdateInput!){workspaceUpdate(input:$i){success}}', { i: { name: original.workspace.name, urlKey: original.workspace.urlKey } });
    await api(baseUrl, adminToken, 'mutation($on:Boolean!){workspaceFeaturesUpdate(agentsEnabled:$on){agentsEnabled}}', { on: original.workspaceFeatures.agentsEnabled });

    await step('no horizontal overflow at 320px', async () => {
      const p = await open(adminToken, { width: 320, height: 700 });
      await goto(p);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      await p.context().close();
      if (sw > 320) throw new Error(`scrollWidth ${sw}`);
    });
    await step('screenshots light/dark at 1440 and 390', () => shots(browser, baseUrl, adminToken, screenshotsDir, SECTION));
  } finally {
    await api(baseUrl, adminToken, 'mutation($i:WorkspaceUpdateInput!){workspaceUpdate(input:$i){success}}', { i: { name: original.workspace.name, urlKey: original.workspace.urlKey } });
    await api(baseUrl, adminToken, 'mutation($on:Boolean!){workspaceFeaturesUpdate(agentsEnabled:$on){agentsEnabled}}', { on: original.workspaceFeatures.agentsEnabled });
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
