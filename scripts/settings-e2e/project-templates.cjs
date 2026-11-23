#!/usr/bin/env node
// Settings → Project templates, end to end (HOJA-734).
// Admin: create a template with every field (name, description, starting status, icon, colour,
// lead, team, tasks) → reload → API read-back; edit; client-side "tasks need a team" guard;
// forced create failure keeps the dialog and values; duplicate; archive via ConfirmAction.
// Member: admin-only page, API refuses. Screenshots light/dark 1440/390, no overflow at 320.
const fs = require('node:fs');
const path = require('node:path');

const SECTION = 'project-templates';

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
  const NAME = `E2E template ${stamp}`;
  const RENAMED = `E2E launch ${stamp}`;
  const templates = async () => (await api(baseUrl, adminToken, '{projectTemplates{id name description defaultState taskSet icon color lead{id name} teams{id name}}}')).data.projectTemplates;
  const meta = (await api(baseUrl, adminToken, '{teams{nodes{id name}} projectStatuses{name} organizationMembers{nodes{id name}}}')).data;
  const team = meta.teams.nodes.find((t) => !/e2e/i.test(t.name)) || meta.teams.nodes[0];
  const status = meta.projectStatuses[1] || meta.projectStatuses[0];
  const lead = meta.organizationMembers.nodes[0];
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
    await page.getByRole('heading', { level: 1, name: 'Project templates' }).waitFor({ timeout: 20000 });
    await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 });
    await page.getByRole('heading', { name: 'Templates', exact: true }).waitFor();
  };
  const menu = async (page, name, item) => {
    await page.getByRole('button', { name: `Actions for template ${name}`, exact: true }).click();
    await page.getByRole('menuitem', { name: item }).click();
  };
  const pick = async (page, dlg, combobox, option) => {
    await dlg.getByRole('combobox', { name: combobox }).click();
    await page.getByRole('option', { name: option, exact: true }).click();
  };

  try {
    const page = await open(adminToken);
    await step('admin: page renders', () => goto(page));

    await step('admin: tasks without a team are blocked before the request', async () => {
      await page.getByRole('button', { name: 'New template' }).first().click();
      const dlg = page.getByRole('dialog', { name: 'New project template' });
      await dlg.getByLabel('Name').fill(NAME);
      await dlg.getByLabel(/^Tasks/).fill('Write the brief\nShip it');
      await dlg.getByText('Pick at least one team.', { exact: false }).waitFor();
      if (await dlg.getByRole('button', { name: 'Create template' }).isEnabled()) throw new Error('submit enabled');
    });

    await step('admin: forced create failure keeps the dialog and values', async () => {
      const dlg = page.getByRole('dialog', { name: 'New project template' });
      await dlg.getByRole('checkbox', { name: team.name }).click();
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('projectTemplateCreate')
        ? route.abort('failed') : route.continue()));
      await dlg.getByRole('button', { name: 'Create template' }).click();
      await dlg.getByText(/Couldn't reach the server/).waitFor();
      if ((await dlg.getByLabel('Name').inputValue()) !== NAME) throw new Error('lost values');
      await page.unroute('**/graphql');
      if ((await templates()).some((t) => t.name === NAME)) throw new Error('created despite failure');
    });

    await step('admin: create with every field', async () => {
      const dlg = page.getByRole('dialog', { name: 'New project template' });
      await dlg.getByLabel(/^Description/).fill('Made by the e2e suite');
      await pick(page, dlg, 'Starting status', status.name);
      await pick(page, dlg, /^Lead/, lead.name);
      await dlg.getByRole('radio', { name: 'Icon 🚀' }).click();
      await dlg.getByRole('radio', { name: '#5e6ad2' }).click();
      await dlg.getByRole('button', { name: 'Create template' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await page.getByText(NAME, { exact: true }).waitFor();
    });
    await step('admin: created template survives reload and reads back from the API', async () => {
      await goto(page);
      await page.getByText(NAME, { exact: true }).waitFor();
      const t = (await templates()).find((x) => x.name === NAME);
      const want = t && t.description === 'Made by the e2e suite' && t.defaultState === status.name && t.icon === '🚀'
        && t.color === '#5e6ad2' && t.lead?.id === lead.id && t.teams.some((x) => x.id === team.id)
        && t.taskSet.join('|') === 'Write the brief|Ship it';
      if (!want) throw new Error(JSON.stringify(t));
    });

    await step('admin: edit (rename, clear icon, first status, drop a task)', async () => {
      await menu(page, NAME, 'Edit…');
      const dlg = page.getByRole('dialog', { name: `Edit “${NAME}”` });
      if ((await dlg.getByLabel(/^Tasks/).inputValue()) !== 'Write the brief\nShip it') throw new Error('task set not loaded');
      await dlg.getByLabel('Name').fill(RENAMED);
      await dlg.getByRole('radio', { name: 'No icon' }).click();
      await pick(page, dlg, 'Starting status', 'The first status');
      await dlg.getByLabel(/^Tasks/).fill('Write the brief');
      await dlg.getByRole('button', { name: 'Save template' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await goto(page);
      await page.getByText(RENAMED, { exact: true }).waitFor();
      const t = (await templates()).find((x) => x.name === RENAMED);
      if (!t || t.icon !== null || t.defaultState !== null || t.taskSet.length !== 1) throw new Error(JSON.stringify(t));
      if (!(await page.getByText(/1 task · Starts in the first status/).count())) throw new Error('meta not updated');
    });

    await step('admin: duplicate opens a prefilled create dialog', async () => {
      await menu(page, RENAMED, 'Duplicate…');
      const dlg = page.getByRole('dialog', { name: 'New project template' });
      if ((await dlg.getByLabel('Name').inputValue()) !== `${RENAMED} copy`) throw new Error('not prefilled');
      await dlg.getByRole('button', { name: 'Create template' }).click();
      await dlg.waitFor({ state: 'hidden' });
      if (!(await templates()).some((x) => x.name === `${RENAMED} copy`)) throw new Error('copy missing');
    });

    if (screenshotsDir) await step('screenshots light/dark at 1440 and 390', async () => {
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        const p = await open(adminToken, { width: w, height: h });
        await goto(p);
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-light.png`), fullPage: true });
        await p.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-dark.png`), fullPage: true });
        await p.getByRole('button', { name: `Actions for template ${RENAMED}`, exact: true }).click();
        await p.getByRole('menuitem', { name: 'Edit…' }).click();
        await p.getByRole('dialog').waitFor();
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-dialog-dark.png`) });
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

    await step('admin: archive through ConfirmAction (both)', async () => {
      for (const n of [`${RENAMED} copy`, RENAMED]) {
        await menu(page, n, 'Archive…');
        const dlg = page.getByRole('alertdialog');
        await dlg.getByText(/not changed/).waitFor();
        await dlg.getByRole('button', { name: 'Archive template' }).click();
        await dlg.waitFor({ state: 'hidden' });
        await page.getByText(n, { exact: true }).waitFor({ state: 'detached' });
      }
      if ((await templates()).some((x) => x.name.startsWith(RENAMED))) throw new Error('still in API');
    });
    check('admin: no page errors', page.errors.length === 0, page.errors.join(' | '));

    const member = await open(memberToken);
    await step('member: page is admin-only', async () => {
      await member.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await member.getByText('Only workspace admins can open this page').waitFor({ timeout: 20000 });
      if (await member.getByRole('button', { name: 'New template' }).count()) throw new Error('create offered');
    });
    await step('member: API refuses the read', async () => {
      if (!(await api(baseUrl, memberToken, '{projectTemplates{id}}')).errors) throw new Error('member could read');
    });
  } finally {
    for (const t of await templates())
      if (t.name.includes(stamp)) await api(baseUrl, adminToken, 'mutation($id:String!){projectTemplateArchive(id:$id)}', { id: t.id });
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
