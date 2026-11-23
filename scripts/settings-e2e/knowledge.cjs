#!/usr/bin/env node
// Settings → Knowledge end-to-end journey (HOJA-734). All against the real backend:
// create (workspace + team scope) → reload → API read-back; instant enable switch with read-back;
// forced switch failure rolls back with a visible error + Retry; edit in the dialog; merged
// preview via knowledgeContext; delete; delete no-op (mutation answers false) shows an error;
// member sees the same controls (the backend lets any active member write: see report).
//   node scripts/settings-e2e/knowledge.cjs
const fs = require('node:fs');
const path = require('node:path');

function resolve(name) {
  for (const base of [process.env.SETTINGS_E2E_NODE_MODULES, path.join(__dirname, '../../local-e2e/node_modules'),
    path.join(__dirname, '../../frontend/node_modules'), process.env.SETTINGS_E2E_FALLBACK_MODULES].filter(Boolean)) {
    const p = path.join(base, name);
    if (fs.existsSync(p)) return p;
  }
  throw new Error(`${name} not found`);
}
const { chromium } = require(resolve('playwright'));

const DEFAULTS = {
  baseUrl: process.env.SETTINGS_E2E_URL || 'http://127.0.0.1:5834',
  adminToken: process.env.SETTINGS_E2E_ADMIN_TOKEN,
  memberToken: process.env.SETTINGS_E2E_MEMBER_TOKEN,
  screenshotsDir: process.env.SETTINGS_E2E_SHOTS || require('node:path').join(require('node:os').tmpdir(), 'settings-e2e', 'knowledge'),
};
const SECTION = 'knowledge';

async function open(browser, token, viewport = { width: 1440, height: 900 }) {
  const ctx = await browser.newContext({ viewport });
  await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  return page;
}
async function goto(page, baseUrl) {
  // The shared dev server hot-reloads while other sections are edited; retry a cold load.
  for (let i = 0; ; i++) {
    try {
      await page.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await page.getByRole('heading', { level: 1, name: 'Knowledge' }).waitFor({ timeout: 15000 });
      await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 });
      return;
    } catch (e) {
      if (i >= 2) throw e;
    }
  }
}
async function gql(baseUrl, token, query, variables) {
  const r = await fetch(baseUrl + '/graphql', {
    method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const b = await r.json();
  if (b.errors) throw new Error(b.errors[0].message);
  return b.data;
}
const docs = async (baseUrl, token) =>
  (await gql(baseUrl, token, '{ knowledgeDocs { id title body scope scopeId enabled } }')).knowledgeDocs;
const docRow = (page, title) => page.getByRole('listitem').filter({ has: page.locator(`[data-knowledge-doc="${title}"]`) });
/** Fail one GraphQL operation by name; everything else passes through. */
async function failOp(page, op, respond) {
  await page.route('**/graphql', async (route) => {
    const body = route.request().postData() || '';
    if (body.includes(op)) return respond(route);
    return route.continue();
  });
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir } = DEFAULTS) {
  const passed = [], failed = [];
  const check = (cond, msg) => (cond ? passed : failed).push(msg);
  const attempt = async (name, fn) => { try { await fn(); } catch (e) { failed.push(`${name}: ${e.message.split('\n')[0]}`); } };
  fs.mkdirSync(screenshotsDir, { recursive: true });
  const tag = Date.now().toString(36);
  const T1 = `E2E standards ${tag}`, T1b = `E2E standards v2 ${tag}`, T2 = `E2E team notes ${tag}`;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await open(browser, adminToken);
    await attempt('description', async () => {
      await goto(page, baseUrl);
      const desc = await page.locator('[data-settings-page] header').innerText();
      check(/agents read/i.test(desc) && /Agent chat, delegated agent runs and MCP/.test(desc), 'page says knowledge is injected into agents');
    });

    await attempt('create workspace doc', async () => {
      await page.getByRole('button', { name: 'New document' }).first().click();
      const d = page.getByRole('dialog', { name: 'New document' });
      check((await d.getByRole('button', { name: 'Add document' }).isDisabled()), 'Add is disabled until title and content exist');
      await d.getByLabel('Title').fill(T1);
      await d.getByLabel('Content').fill(`## Rules ${tag}\n\n- Prefer small PRs`);
      await d.getByRole('button', { name: 'Add document' }).click();
      await d.waitFor({ state: 'hidden' });
      await docRow(page, T1).waitFor();
      await goto(page, baseUrl);
      check(await docRow(page, T1).isVisible(), 'created doc shown after reload');
      const api = (await docs(baseUrl, adminToken)).find((x) => x.title === T1);
      check(api && api.scope === 'workspace' && api.enabled === true, 'API: workspace scope, enabled');
    });

    await attempt('create team doc', async () => {
      await page.getByRole('button', { name: 'New document' }).first().click();
      const d = page.getByRole('dialog', { name: 'New document' });
      await d.getByRole('combobox', { name: 'Applies to' }).click();
      await page.getByRole('option', { name: /One team/ }).click();
      await d.getByLabel('Title').fill(T2);
      await d.getByLabel('Content').fill(`Team ${tag} deploys on Tuesdays.`);
      await d.getByRole('button', { name: 'Add document' }).click();
      await d.waitFor({ state: 'hidden' });
      const api = (await docs(baseUrl, adminToken)).find((x) => x.title === T2);
      check(api && api.scope === 'team' && !!api.scopeId, 'API: team-scoped doc with a team id');
      check(/Team /.test(await docRow(page, T2).innerText()), 'row names the team scope');
      await page.getByRole('radio', { name: 'Teams' }).click();
      check((await docRow(page, T1).count()) === 0 && (await docRow(page, T2).count()) === 1, 'scope filter shows only team docs');
      await page.getByRole('radio', { name: 'All' }).click();
    });

    await attempt('switch fails and rolls back', async () => {
      await failOp(page, 'SettingsKnowledgeUpdate', (r) => r.fulfill({ status: 500, body: 'oops' }));
      const sw = page.getByRole('switch', { name: `Give agents "${T1}"` });
      await sw.click();
      await docRow(page, T1).getByRole('alert').waitFor();
      check((await sw.getAttribute('aria-checked')) === 'true', 'failed switch snaps back on');
      check(await docRow(page, T1).getByRole('button', { name: 'Retry' }).isVisible(), 'failure shows reason with Retry');
      await page.unroute('**/graphql');
    });

    await attempt('switch off instantly', async () => {
      const sw = page.getByRole('switch', { name: `Give agents "${T1}"` });
      await sw.click();
      await docRow(page, T1).getByText('Saved').waitFor();
      check((await page.getByRole('button', { name: /^Save/ }).count()) === 0, 'no Save button anywhere for the enabled flag');
      await goto(page, baseUrl);
      check((await page.getByRole('switch', { name: `Give agents "${T1}"` }).getAttribute('aria-checked')) === 'false', 'switch off after reload');
      const api = (await docs(baseUrl, adminToken)).find((x) => x.title === T1);
      check(api && api.enabled === false, 'API: enabled false');
    });

    await attempt('preview', async () => {
      await page.getByRole('button', { name: 'Preview' }).click();
      const d = page.getByRole('dialog', { name: 'What agents read' });
      await d.getByRole('combobox', { name: 'Team' }).click();
      await page.getByRole('option').filter({ hasNotText: 'No team' }).first().click();
      const pre = d.getByLabel('Merged knowledge');
      await pre.waitFor();
      const text = await pre.innerText();
      check(text.includes(`Team ${tag} deploys on Tuesdays.`), 'preview includes the enabled team doc');
      check(!text.includes(`Rules ${tag}`), 'preview leaves out the disabled doc');
      await page.screenshot({ path: path.join(screenshotsDir, 'preview-1440.png') });
      await page.keyboard.press('Escape');
    });

    await attempt('edit', async () => {
      await docRow(page, T1).getByRole('button', { name: `Actions for ${T1}` }).click();
      await page.getByRole('menuitem', { name: 'Edit' }).click();
      const d = page.getByRole('dialog', { name: 'Edit document' });
      check((await d.getByLabel('Title').inputValue()) === T1, 'edit dialog is prefilled');
      // failure keeps the dialog and its values
      await failOp(page, 'SettingsKnowledgeUpdate', (r) => r.abort());
      await d.getByLabel('Title').fill(T1b);
      await d.getByRole('button', { name: 'Save changes' }).click();
      await d.getByRole('alert').waitFor();
      check((await d.getByLabel('Title').inputValue()) === T1b, 'failed save keeps the dialog and typed values');
      await page.unroute('**/graphql');
      await d.getByLabel('Content').fill(`## Rules ${tag}\n\n- Prefer small PRs\n- Edited`);
      await d.getByRole('button', { name: 'Save changes' }).click();
      await d.waitFor({ state: 'hidden' });
      await goto(page, baseUrl);
      check(await docRow(page, T1b).isVisible(), 'edited title shown after reload');
      const api = (await docs(baseUrl, adminToken)).find((x) => x.title === T1b);
      check(api && api.body.endsWith('- Edited') && api.enabled === false, 'API: edit saved, enabled untouched by the dialog');
      await page.screenshot({ path: path.join(screenshotsDir, 'list-1440.png'), fullPage: true });
    });

    await attempt('delete no-op is an error', async () => {
      await failOp(page, 'SettingsKnowledgeDelete', (r) => r.fulfill({ json: { data: { knowledgeDocDelete: false } } }));
      await docRow(page, T1b).getByRole('button', { name: `Actions for ${T1b}` }).click();
      await page.getByRole('menuitem', { name: 'Delete' }).click();
      const c = page.getByRole('alertdialog');
      await c.getByRole('button', { name: 'Delete document' }).click();
      await c.getByRole('alert').waitFor();
      check(/no longer exists/.test(await c.getByRole('alert').innerText()), 'delete returning false shows an error, not success');
      await c.getByRole('button', { name: 'Cancel' }).click();
      await page.unroute('**/graphql');
    });

    await attempt('delete', async () => {
      for (const t of [T1b]) {
        await docRow(page, t).getByRole('button', { name: `Actions for ${t}` }).click();
        await page.getByRole('menuitem', { name: 'Delete' }).click();
        await page.getByRole('alertdialog').getByRole('button', { name: 'Delete document' }).click();
        await page.getByRole('alertdialog').waitFor({ state: 'hidden' });
      }
      await goto(page, baseUrl);
      check((await docRow(page, T1b).count()) === 0, 'deleted doc gone after reload');
      check(!(await docs(baseUrl, adminToken)).some((x) => x.title === T1b), 'API: doc deleted');
    });
    check(page.errors.length === 0, `no page errors (${page.errors.join(' | ')})`);
    await page.context().close();

    await attempt('member', async () => {
      const m = await open(browser, memberToken);
      await goto(m, baseUrl);
      check(await docRow(m, T2).isVisible(), 'member sees workspace knowledge');
      check(await m.getByRole('button', { name: 'New document' }).first().isEnabled(), 'member can add (the backend allows any active member)');
      check(await m.getByRole('switch', { name: `Give agents "${T2}"` }).isEnabled(), 'member switch is live (matches backend)');
      await m.context().close();
    });

    await attempt('screenshots', async () => {
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        for (const dark of [false, true]) {
          const p = await open(browser, adminToken, { width: w, height: h });
          await goto(p, baseUrl);
          if (dark) await p.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
          await p.waitForTimeout(150);
          await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}${dark ? '-dark' : ''}.png`), fullPage: true });
          if (w === 390 && !dark) {
            await p.getByRole('button', { name: 'New document' }).first().click();
            await p.waitForTimeout(250);
            await p.screenshot({ path: path.join(screenshotsDir, 'new-document-390.png') });
          }
          await p.context().close();
        }
      }
      const p = await open(browser, adminToken, { width: 320, height: 800 });
      await goto(p, baseUrl);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      check(sw <= 320, `no horizontal overflow at 320px (scrollWidth ${sw})`);
      await p.context().close();
    });

    // cleanup the team doc through the API so re-runs start from the same state
    await attempt('cleanup', async () => {
      for (const d of (await docs(baseUrl, adminToken)).filter((x) => x.title.includes(tag)))
        await gql(baseUrl, adminToken, 'mutation($id:String!){ knowledgeDocDelete(id:$id) }', { id: d.id });
    });
  } finally {
    await browser.close();
  }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) {
  run(DEFAULTS).then(({ passed, failed }) => {
    for (const p of passed) console.log('  ok   ' + p);
    for (const f of failed) console.log('  FAIL ' + f);
    console.log(`\n${passed.length} passed, ${failed.length} failed`);
    process.exit(failed.length ? 1 : 0);
  }).catch((e) => { console.error(e); process.exit(1); });
}
