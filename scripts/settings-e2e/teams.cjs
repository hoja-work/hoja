// Teams journey (HOJA-734): create a team, edit name/key/description/visibility/parent, add and
// remove a member, archive and delete (type the key), roll back on a refused save, and check that a
// member sees every value locked. Every change is read back from /graphql after a reload.
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

const api = (page, query, variables) =>
  page.evaluate(async ({ query, variables }) => {
    const r = await fetch('/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${localStorage.getItem('hoja_token')}` },
      body: JSON.stringify({ query, variables }),
    });
    return (await r.json()).data;
  }, { query, variables });

const teamByKey = async (page, key) =>
  (await api(page, '{teams(first:250){nodes{id key name description visibility parentTeamId members{nodes{id name}}}}}'))
    .teams.nodes.find((t) => t.key === key);

async function screenshots(browser, baseUrl, token, dir, route, name) {
  if (!dir) return [];
  fs.mkdirSync(dir, { recursive: true });
  const notes = [];
  for (const [w, h] of [[1440, 900], [390, 844], [320, 800]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    page.setDefaultTimeout(60000);
    await page.goto(`${baseUrl}${route}`);
    await page.locator('[data-settings-page]').first().waitFor();
    await page.waitForTimeout(800);
    if (w === 320) {
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      notes.push([sw <= 320, `${name}: no horizontal overflow at 320px (scrollWidth ${sw})`]);
    } else {
      for (const theme of ['light', 'dark']) {
        await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
        await page.waitForTimeout(150);
        await page.screenshot({ path: path.join(dir, `${name}-${w}-${theme}.png`), fullPage: true });
      }
    }
    await ctx.close();
  }
  return notes;
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = require(resolve('playwright'));
  const passed = [], failed = [];
  const check = (cond, msg) => (cond ? passed : failed).push(msg);
  let step = 'start';
  const soft = async (p, msg) => p.then(() => check(true, msg), (e) => check(false, `${msg} (${String(e.message || e).split('\n')[0]})`));
  const browser = await chromium.launch();
  const stamp = Date.now().toString(36).slice(-5).toUpperCase();
  const KEY = `T${stamp}`;
  const NAME = `E2E team ${stamp}`;
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), adminToken);
    const page = await ctx.newPage();
    page.setDefaultTimeout(60000);
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${baseUrl}/workspace/settings/teams`);
    await page.getByRole('heading', { name: 'Teams', level: 1 }).waitFor();

    // --- create
    step = 'create';
    await page.getByRole('button', { name: 'Create team' }).first().click();
    const dlg = page.getByRole('dialog', { name: 'Create team' });
    await dlg.getByLabel('Name').fill(NAME);
    await dlg.getByLabel('Key').fill(KEY);
    await dlg.getByLabel('Description').fill('Made by the settings e2e');
    await dlg.getByRole('button', { name: 'Create team' }).click();
    await soft(page.getByRole('heading', { name: NAME, level: 1 }).waitFor({ timeout: 20000 }), 'admin: create opens the new team');
    check(page.url().includes(`team=${KEY}`), 'admin: detail lives at ?team=KEY');
    let t = await teamByKey(page, KEY);
    check(!!t && t.name === NAME && t.description === 'Made by the settings e2e', 'admin: created team read back from API');
    check(!!t && t.members.nodes.length === 1, 'admin: creator seated as the only member');

    // --- name (blur/Enter), description, key
    step = 'name (blur/Enter), description, key';
    const NAME2 = `${NAME} renamed`;
    await page.getByLabel('Name', { exact: true }).fill(NAME2);
    await page.getByLabel('Name', { exact: true }).press('Enter');
    await soft(page.getByText('Saved').first().waitFor({ timeout: 20000 }), 'admin: name shows Saved');
    await page.getByLabel('Description', { exact: true }).fill('Edited description');
    await page.getByLabel('Description', { exact: true }).blur();
    await page.getByText('Saved').first().waitFor({ timeout: 20000 }).catch(() => {});
    const KEY2 = `${KEY}X`.slice(0, 8);
    await page.getByLabel('Key', { exact: true }).fill(KEY2);
    await page.getByLabel('Key', { exact: true }).press('Enter');
    await page.waitForFunction((k) => location.search.includes(`team=${k}`), KEY2, { timeout: 20000 }).then(
      () => check(true, 'admin: key change updates the URL'), () => check(false, 'admin: key change did not update the URL'));
    // invalid key is refused before any request
    await page.getByLabel('Key', { exact: true }).fill('A-B');
    await page.getByLabel('Key', { exact: true }).press('Enter');
    await soft(page.getByRole('alert').filter({ hasText: 'letters and numbers' }).waitFor({ timeout: 20000 }), 'admin: invalid key shows validation');
    await page.getByLabel('Key', { exact: true }).press('Escape');

    // --- visibility (instant select)
    step = 'visibility (instant select)';
    await page.getByRole('combobox', { name: 'Visibility' }).click();
    await page.getByRole('option', { name: /Restricted/ }).click();
    await page.getByText('Saved').first().waitFor({ timeout: 20000 }).catch(() => {});

    await page.reload();
    await page.getByRole('heading', { name: NAME2, level: 1 }).waitFor();
    check(await page.getByLabel('Name', { exact: true }).inputValue() === NAME2, 'admin: name survives reload');
    check(await page.getByLabel('Key', { exact: true }).inputValue() === KEY2, 'admin: key survives reload');
    check(await page.getByLabel('Description', { exact: true }).inputValue() === 'Edited description', 'admin: description survives reload');
    check((await page.getByRole('combobox', { name: 'Visibility' }).textContent()).includes('Restricted'), 'admin: visibility survives reload');
    t = await teamByKey(page, KEY2);
    check(t && t.name === NAME2 && t.description === 'Edited description' && t.visibility === 'restricted', 'admin: name/key/description/visibility read back from API');

    // --- parent team (confirm, then saved)
    step = 'parent team (confirm, then saved)';
    await page.getByRole('combobox', { name: 'Parent team' }).click();
    await page.getByRole('option', { name: /Engineering/ }).click();
    const move = page.getByRole('alertdialog');
    await soft(move.getByText(/joins Engineering/).waitFor({ timeout: 20000 }), 'admin: re-parenting explains side effects');
    await move.getByRole('button', { name: 'Move team' }).click();
    await move.waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    const eng = await teamByKey(page, 'ENG');
    t = await teamByKey(page, KEY2);
    check(t && eng && t.parentTeamId === eng.id, 'admin: parent team read back from API');
    await page.getByRole('combobox', { name: 'Parent team' }).click();
    await page.getByRole('option', { name: 'None' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Make top-level' }).click();
    await page.getByRole('alertdialog').waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    t = await teamByKey(page, KEY2);
    check(t && !t.parentTeamId, 'admin: team made top-level again');

    // --- members: add, then remove (confirm)
    step = 'members';
    await page.getByRole('button', { name: 'Add people' }).first().click();
    const add = page.getByRole('dialog', { name: /Add people/ });
    await add.getByRole('checkbox', { name: /Max Member/ }).click();
    await add.getByRole('button', { name: 'Add to team' }).click();
    await add.waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    t = await teamByKey(page, KEY2);
    check(t && t.members.nodes.some((m) => m.name === 'Max Member'), 'admin: member added (API)');
    await soft(page.getByRole('button', { name: 'Actions for Max Member' }).waitFor({ timeout: 20000 }), 'admin: added member listed');
    await page.getByRole('button', { name: 'Actions for Max Member' }).click();
    await page.getByRole('menuitem', { name: 'Remove from team…' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Remove from team' }).click();
    await page.getByRole('alertdialog').waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    t = await teamByKey(page, KEY2);
    check(t && !t.members.nodes.some((m) => m.name === 'Max Member'), 'admin: member removed after confirm (API)');

    // --- failure: refused rename rolls back with a visible error and Retry
    step = 'failure: refused rename rolls back with a visible error and Retry';
    await page.route('**/graphql', (route) => {
      const body = route.request().postData() || '';
      if (body.includes('teamUpdate')) return route.fulfill({ status: 500, contentType: 'text/html', body: '<h1>oops</h1>' });
      return route.continue();
    });
    await page.getByLabel('Name', { exact: true }).fill('Should not stick');
    await page.getByLabel('Name', { exact: true }).press('Enter');
    await soft(page.getByRole('alert').filter({ hasText: /server/i }).first().waitFor({ timeout: 20000 }), 'admin: refused rename shows a readable error');
    check(await page.getByLabel('Name', { exact: true }).inputValue() === NAME2, 'admin: refused rename rolls back');
    check(await page.getByRole('button', { name: 'Retry' }).isVisible(), 'admin: Retry offered');
    await page.unroute('**/graphql');
    t = await teamByKey(page, KEY2);
    check(t && t.name === NAME2, 'admin: refused rename did not reach the API');

    // --- archive
    step = 'archive';
    await page.getByRole('button', { name: 'Archive…' }).click();
    const arch = page.getByRole('alertdialog');
    await soft(arch.getByText(/can't be undone from Hoja/).waitFor({ timeout: 20000 }), 'admin: archive confirm says there is no unarchive');
    await arch.getByRole('button', { name: 'Archive team' }).click();
    await soft(page.getByRole('heading', { name: 'Teams', level: 1 }).waitFor({ timeout: 20000 }), 'admin: archive returns to the list');
    check(!(await teamByKey(page, KEY2)), 'admin: archived team gone from API list');

    // --- delete (type the key)
    step = 'delete (type the key)';
    const DEL = `D${stamp}`;
    await page.getByRole('button', { name: 'Create team' }).first().click();
    const d2 = page.getByRole('dialog', { name: 'Create team' });
    await d2.getByLabel('Name').fill(`E2E delete ${stamp}`);
    await d2.getByLabel('Key').fill(DEL);
    await d2.getByRole('button', { name: 'Create team' }).click();
    await page.getByRole('heading', { name: `E2E delete ${stamp}`, level: 1 }).waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'Delete…' }).click();
    const del = page.getByRole('alertdialog');
    const confirmBtn = del.getByRole('button', { name: 'Delete team' });
    check(await confirmBtn.isDisabled(), 'admin: delete is blocked until the key is typed');
    await del.getByRole('textbox').fill(DEL);
    await confirmBtn.click();
    await soft(page.getByRole('heading', { name: 'Teams', level: 1 }).waitFor({ timeout: 20000 }), 'admin: delete returns to the list');
    check(!(await teamByKey(page, DEL)), 'admin: deleted team gone from API');
    check(errors.length === 0, `admin: no page errors (${errors.join('; ')})`);
    await ctx.close();

    // --- member: everything visible, nothing editable
    step = 'member: everything visible, nothing editable';
    const mctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await mctx.addInitScript((t) => localStorage.setItem('hoja_token', t), memberToken);
    const mp = await mctx.newPage();
    mp.setDefaultTimeout(60000);
    step = 'member';
    await mp.goto(`${baseUrl}/workspace/settings/teams`);
    await mp.getByRole('heading', { name: 'Teams', level: 1 }).waitFor();
    check(!(await mp.getByRole('button', { name: 'Create team' }).count()), 'member: no Create team (no team.create)');
    await mp.getByRole('button', { name: /Engineering/ }).first().click({ timeout: 60000 });
    await mp.getByRole('heading', { name: 'Engineering', level: 1 }).waitFor({ timeout: 60000 });
    check(await mp.getByLabel('Name', { exact: true }).isDisabled(), 'member: name is disabled');
    check(await mp.getByLabel('Key', { exact: true }).isDisabled(), 'member: key is disabled');
    check(await mp.getByRole('combobox', { name: 'Visibility' }).isDisabled(), 'member: visibility is disabled');
    check(await mp.getByText('Only workspace admins can change this.').first().isVisible(), 'member: locked reason shown');
    check(!(await mp.getByRole('button', { name: 'Add people' }).count()), 'member: cannot add people (no member.invite)');
    check(await mp.getByRole('button', { name: 'Archive…' }).isDisabled(), 'member: archive disabled');
    check(await mp.getByRole('button', { name: 'Delete…' }).isDisabled(), 'member: delete disabled');
    await mctx.close();

    step = 'screenshots';
    for (const [ok, msg] of await screenshots(browser, baseUrl, adminToken, screenshotsDir, '/workspace/settings/teams', 'teams-list')) check(ok, msg);
    for (const [ok, msg] of await screenshots(browser, baseUrl, adminToken, screenshotsDir, '/workspace/settings/teams?team=ENG', 'teams-detail')) check(ok, msg);
    for (const [ok, msg] of await screenshots(browser, baseUrl, memberToken, screenshotsDir, '/workspace/settings/teams?team=ENG', 'teams-detail-member')) check(ok, msg);
  } catch (e) {
    failed.push(`teams journey crashed at '${step}': ${String(e.message || e).split('\n')[0]}`);
  } finally {
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
    screenshotsDir: process.env.SETTINGS_E2E_SHOTS,
  }).then((r) => {
    r.passed.forEach((m) => console.log('ok   ' + m));
    r.failed.forEach((m) => console.log('FAIL ' + m));
    process.exit(r.failed.length ? 1 : 0);
  });
}
