#!/usr/bin/env node
// Settings → Project statuses, end to end (HOJA-734, HOJA-683).
// Admin: create → reload → API read-back; edit name/category/colour; reorder (make default,
// move down) with API read-back; forced failures (reorder rolls back with an inline error,
// edit dialog keeps values and shows the reason); delete via ConfirmAction. Member: the page
// is admin-only and the API refuses the read. Screenshots light/dark at 1440 and 390,
// and no horizontal overflow at 320.
const fs = require('node:fs');
const path = require('node:path');

const SECTION = 'project-statuses';

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

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = loadPlaywright();
  const passed = [], failed = [];
  const check = (name, cond, extra = '') => { (cond ? passed : failed).push(cond ? name : `${name}${extra ? ` (${extra})` : ''}`); };
  const step = async (name, fn) => {
    try { await fn(); passed.push(name); } catch (e) { failed.push(`${name}: ${String(e.message || e).split('\n').filter((l) => /\S/.test(l) && !/^\s*at /.test(l)).slice(0, 3).join(' / ')}`); }
  };
  const stamp = Date.now().toString(36);
  const NAME = `E2E status ${stamp}`;
  const RENAMED = `E2E renamed ${stamp}`;
  const statuses = async () => (await api(baseUrl, adminToken, '{projectStatuses{id name category color position}}')).data.projectStatuses;
  const original = (await statuses()).map((s) => s.id);
  if (screenshotsDir) fs.mkdirSync(screenshotsDir, { recursive: true });

  const browser = await chromium.launch();
  const open = async (token, viewport = { width: 1440, height: 900 }) => {
    const ctx = await browser.newContext({ viewport });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    page.errors = [];
    page.on('pageerror', (e) => page.errors.push(e.message));
    return page;
  };
  const goto = async (page) => {
    await page.goto(`${baseUrl}/workspace/settings/${SECTION}`);
    await page.getByRole('heading', { level: 1, name: 'Project statuses' }).waitFor({ timeout: 20000 });
    await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 });
    await page.getByRole('list', { name: 'Project statuses, in order' }).waitFor({ timeout: 15000 });
  };
  const list = (page) => page.getByRole('list', { name: 'Project statuses, in order' });
  const row = (page, name) => list(page).getByRole('listitem').filter({ has: page.getByText(name, { exact: true }) });
  const menu = async (page, name, item) => {
    await page.getByRole('button', { name: `Actions for status ${name}`, exact: true }).click();
    await page.getByRole('menuitem', { name: item }).click();
  };
  const firstRowName = async (page) => (await list(page).getByRole('listitem').first().innerText()).split('\n')[0].trim();

  try {
    const page = await open(adminToken);
    await step('admin: page renders with the seeded statuses', async () => {
      await goto(page);
      await list(page).getByRole('listitem').first().waitFor();
      const n = await list(page).getByRole('listitem').count();
      if (n < 1) throw new Error('no rows');
      await list(page).getByText('Default', { exact: true }).waitFor();
    });

    await step('admin: create a status through the dialog', async () => {
      await page.getByRole('button', { name: 'New status' }).first().click();
      const dlg = page.getByRole('dialog', { name: 'New project status' });
      await dlg.getByLabel('Name').fill(NAME);
      await dlg.getByRole('combobox', { name: 'Category' }).click();
      await page.getByRole('option', { name: /Completed/ }).click();
      await dlg.getByRole('radio', { name: '#eb5757' }).click();
      await dlg.getByRole('button', { name: 'Add status' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await row(page, NAME).waitFor();
    });
    await step('admin: created status survives reload and reads back from the API', async () => {
      await goto(page);
      await row(page, NAME).waitFor();
      const s = (await statuses()).find((x) => x.name === NAME);
      if (!s || s.category !== 'completed' || s.color !== '#eb5757') throw new Error(JSON.stringify(s));
      if (!(await row(page, NAME).innerText()).includes('Completed')) throw new Error('category not shown');
    });

    await step('admin: forced edit failure keeps the dialog open with the reason', async () => {
      await page.route('**/graphql', async (route) => {
        if ((route.request().postData() || '').includes('projectStatusUpdate'))
          return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ errors: [{ message: 'Simulated edit failure.' }] }) });
        return route.continue();
      });
      await menu(page, NAME, 'Edit…');
      const dlg = page.getByRole('dialog', { name: `Edit “${NAME}”` });
      await dlg.getByLabel('Name').fill(RENAMED);
      await dlg.getByRole('button', { name: 'Save status' }).click();
      await dlg.getByText('Simulated edit failure.').waitFor();
      if ((await dlg.getByLabel('Name').inputValue()) !== RENAMED) throw new Error('typed value lost');
      await dlg.getByRole('button', { name: 'Cancel' }).click();
      await page.unroute('**/graphql');
      const s = (await statuses()).find((x) => x.name === NAME);
      if (!s) throw new Error('server changed despite failure');
    });

    await step('admin: edit name, category and colour', async () => {
      await menu(page, NAME, 'Edit…');
      const dlg = page.getByRole('dialog', { name: `Edit “${NAME}”` });
      await dlg.getByLabel('Name').fill(RENAMED);
      await dlg.getByRole('combobox', { name: 'Category' }).click();
      await page.getByRole('option', { name: /Canceled/ }).click();
      await dlg.getByRole('radio', { name: '#4cb782' }).click();
      await dlg.getByRole('button', { name: 'Save status' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await row(page, RENAMED).waitFor();
      await goto(page);
      await row(page, RENAMED).waitFor();
      const s = (await statuses()).find((x) => x.name === RENAMED);
      if (!s || s.category !== 'canceled' || s.color !== '#4cb782') throw new Error(JSON.stringify(s));
    });

    await step('admin: duplicate name is refused with the server reason', async () => {
      const taken = (await statuses())[0].name;
      await menu(page, RENAMED, 'Edit…');
      const dlg = page.getByRole('dialog', { name: `Edit “${RENAMED}”` });
      await dlg.getByLabel('Name').fill(taken);
      await dlg.getByRole('button', { name: 'Save status' }).click();
      await dlg.getByText(/already exists/).waitFor();
      await dlg.getByRole('button', { name: 'Cancel' }).click();
    });

    await step('admin: make default moves it to the top and reads back as position 0', async () => {
      await menu(page, RENAMED, 'Make default');
      await page.getByText('Saved', { exact: true }).first().waitFor();
      if ((await firstRowName(page)) !== RENAMED) throw new Error(`first row is ${await firstRowName(page)}`);
      await goto(page);
      if ((await firstRowName(page)) !== RENAMED) throw new Error('not first after reload');
      if (!(await row(page, RENAMED).innerText()).includes('Default')) throw new Error('no Default badge');
      const s = (await statuses()).find((x) => x.name === RENAMED);
      if (s.position !== 0) throw new Error(`position ${s.position}`);
    });

    await step('admin: move down reads back as position 1', async () => {
      await menu(page, RENAMED, 'Move down');
      await page.getByText('Saved', { exact: true }).first().waitFor();
      const s = (await statuses()).find((x) => x.name === RENAMED);
      if (s.position !== 1) throw new Error(`position ${s.position}`);
    });

    await step('admin: forced reorder failure rolls back and shows Retry', async () => {
      await page.route('**/graphql', async (route) => {
        if ((route.request().postData() || '').includes('projectStatusReorder'))
          return route.fulfill({ status: 500, contentType: 'text/html', body: '<html>boom</html>' });
        return route.continue();
      });
      const before = await firstRowName(page);
      await menu(page, RENAMED, 'Make default');
      await page.getByRole('alert').filter({ hasText: /server is unavailable/i }).waitFor();
      await page.getByRole('button', { name: 'Retry' }).waitFor();
      if ((await firstRowName(page)) !== before) throw new Error('order did not roll back');
      await page.unroute('**/graphql');
      const s = (await statuses()).find((x) => x.name === RENAMED);
      if (s.position !== 1) throw new Error('server changed');
    });

    if (screenshotsDir) await step('screenshots light/dark at 1440 and 390', async () => {
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        const p = await open(adminToken, { width: w, height: h });
        await goto(p);
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-light.png`), fullPage: true });
        await p.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-dark.png`), fullPage: true });
        if (w === 1440) {
          await p.getByRole('button', { name: 'New status' }).first().click();
          await p.getByRole('dialog').waitFor();
          await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-dialog-dark.png`) });
        }
        await p.context().close();
      }
    });

    await step('no horizontal overflow at 320px', async () => {
      const p = await open(adminToken, { width: 320, height: 700 });
      await goto(p);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      await p.context().close();
      if (sw > 320) throw new Error(`scrollWidth ${sw}`);
    });

    await step('admin: delete through ConfirmAction', async () => {
      await menu(page, RENAMED, 'Delete…');
      const dlg = page.getByRole('alertdialog');
      await dlg.getByText(/cannot be undone/).waitFor();
      await dlg.getByRole('button', { name: 'Delete status' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await row(page, RENAMED).waitFor({ state: 'detached' });
      if ((await statuses()).some((x) => x.name === RENAMED)) throw new Error('still in API');
    });
    check('admin: no page errors', page.errors.length === 0, page.errors.join(' | '));

    const member = await open(memberToken);
    await step('member: page is admin-only', async () => {
      await member.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await member.getByText('Only workspace admins can open this page').waitFor({ timeout: 20000 });
      if (await member.getByRole('button', { name: 'New status' }).count()) throw new Error('create offered to member');
    });
    await step('member: API refuses the read', async () => {
      const r = await api(baseUrl, memberToken, '{projectStatuses{id}}');
      if (!r.errors) throw new Error('member could read statuses');
    });
  } finally {
    // Restore the original order (the new status was deleted above, or is removed here).
    const now = await statuses();
    for (const s of now) if (s.name === NAME || s.name === RENAMED) await api(baseUrl, adminToken, 'mutation($id:String!){projectStatusDelete(id:$id)}', { id: s.id });
    const left = (await statuses()).map((s) => s.id);
    if (left.length === original.length) await api(baseUrl, adminToken, 'mutation($ids:[String!]!){projectStatusReorder(ids:$ids)}', { ids: original });
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
