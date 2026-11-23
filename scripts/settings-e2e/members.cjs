// Members journey (HOJA-734): invite a uniquely named person (link shown once), change role,
// edit teams, deactivate/reactivate, revoke the invite, remove/restore, roll back on a refused
// deactivate, guard the last admin, and check the member lock-out. Read back from /graphql.
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

const state = async (page, email) => {
  const d = await api(page, '{organizationMembers(first:250){nodes{id email admin active}} pendingMembershipInvitations{userId email} archivedMembers{id email} teams(first:250){nodes{key members{nodes{email}}}}}');
  const m = d.organizationMembers.nodes.find((x) => x.email === email);
  return {
    member: m,
    pending: d.pendingMembershipInvitations.some((x) => x.email === email),
    archived: d.archivedMembers.some((x) => x.email === email),
    teams: d.teams.nodes.filter((t) => t.members.nodes.some((x) => x.email === email)).map((t) => t.key),
  };
};

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
    await page.waitForTimeout(900);
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
  const stamp = Date.now().toString(36);
  const NAME = `E2E Invitee ${stamp}`;
  const EMAIL = `e2e-${stamp}@acme.test`;
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), adminToken);
    const page = await ctx.newPage();
    page.setDefaultTimeout(60000);
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${baseUrl}/workspace/settings/members`);
    await page.getByRole('heading', { name: 'Members', level: 1 }).waitFor();
    const menu = async (item) => {
      await page.getByRole('button', { name: `Actions for ${NAME}` }).click();
      await page.getByRole('menuitem', { name: item }).click();
    };
    const confirm = async (label) => {
      const dlg = page.getByRole('alertdialog');
      await dlg.getByRole('button', { name: label }).click();
      await dlg.waitFor({ state: 'hidden', timeout: 20000 });
    };

    // --- invite: validation, then success with a one-time link
    step = 'invite: validation, then success with a one-time link';
    await page.getByRole('button', { name: 'Invite people' }).first().click();
    const inv = page.getByRole('dialog', { name: 'Invite people' });
    await inv.getByLabel('Name').fill(NAME);
    await inv.getByLabel('Email').fill('not-an-email');
    await inv.getByRole('button', { name: 'Send invite' }).click();
    await soft(inv.getByText(/Enter an email address/).first().waitFor({ timeout: 20000 }), 'admin: bad email is refused before sending');
    await inv.getByLabel('Email').fill(EMAIL);
    await inv.getByRole('checkbox', { name: /Engineering/ }).click();
    await inv.getByRole('button', { name: 'Send invite' }).click();
    const linkField = page.getByRole('textbox', { name: 'Invite link' });
    await soft(linkField.waitFor({ timeout: 20000 }), 'admin: invite link shown');
    check(/#invite=/.test(await linkField.inputValue().catch(() => '')), 'admin: invite link contains a code');
    check(await page.getByText(/won't be shown again/).isVisible(), 'admin: link says it is shown once');
    await page.getByRole('button', { name: 'Done' }).click();
    let s = await state(page, EMAIL);
    check(!!s.member && s.pending && s.teams.includes('ENG'), 'admin: invitee, pending invite and team read back from API');
    await page.reload();
    const row = page.locator('[data-resource-row]').filter({ hasText: NAME });
    await soft(row.getByText('Invite pending').waitFor({ timeout: 20000 }), 'admin: invitee shows Invite pending after reload');

    // --- search
    step = 'search';
    await page.getByRole('searchbox', { name: 'Search members' }).fill(stamp);
    await page.locator('[data-resource-row]').filter({ hasText: 'Ada Admin' }).waitFor({ state: 'detached', timeout: 20000 }).catch(() => {});
    check(await page.locator('[data-resource-row]').filter({ hasText: 'Ada Admin' }).count() === 0 && await row.count() === 1, 'admin: search narrows the list');
    await page.getByRole('searchbox', { name: 'Search members' }).fill('');

    // --- role: promote, then demote
    step = 'role: promote, then demote';
    await menu('Change role…');
    let dlg = page.getByRole('dialog', { name: /Change role/ });
    await dlg.getByRole('radio', { name: /Admin/ }).click();
    await dlg.getByRole('button', { name: 'Change role' }).click();
    await dlg.waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    await page.reload();
    await soft(row.getByText('Admin', { exact: true }).waitFor({ timeout: 20000 }), 'admin: Admin badge after reload');
    check((await state(page, EMAIL)).member.admin === true, 'admin: role admin read back from API');
    await menu('Change role…');
    dlg = page.getByRole('dialog', { name: /Change role/ });
    await dlg.getByRole('radio', { name: /Member/ }).click();
    await dlg.getByRole('button', { name: 'Change role' }).click();
    await dlg.waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    check((await state(page, EMAIL)).member.admin === false, 'admin: role member read back from API');

    // --- last-admin guard on yourself
    step = 'last-admin guard on yourself';
    await page.getByRole('button', { name: 'Actions for Ada Admin' }).click();
    await page.getByRole('menuitem', { name: 'Change role…' }).click();
    dlg = page.getByRole('dialog', { name: /Change role/ });
    await dlg.getByRole('radio', { name: /Member/ }).click();
    check(await dlg.getByText(/only active admin/).isVisible(), 'admin: last active admin warning shown');
    check(await dlg.getByRole('button', { name: 'Change role' }).isDisabled(), 'admin: demoting the last active admin is blocked');
    await dlg.getByRole('button', { name: 'Cancel' }).click();

    // --- teams dialog: leave ENG (warns)
    step = 'teams dialog: leave ENG (warns)';
    await menu('Teams…');
    dlg = page.getByRole('dialog', { name: /Teams for/ });
    await dlg.getByRole('checkbox', { name: /Engineering/ }).click();
    check(await dlg.getByText(/will be removed from ENG/).isVisible(), 'admin: team removal is spelled out before saving');
    await dlg.getByRole('button', { name: 'Save teams' }).click();
    await dlg.waitFor({ state: 'hidden', timeout: 20000 }).catch(() => {});
    check(!(await state(page, EMAIL)).teams.includes('ENG'), 'admin: team removal read back from API');

    // --- failure: refused deactivate keeps the dialog open with the reason, nothing changes
    step = 'failure: refused deactivate keeps the dialog open with the reason, nothing changes';
    await page.route('**/graphql', (route) => {
      const body = route.request().postData() || '';
      if (body.includes('memberDeactivate')) return route.fulfill({ status: 500, contentType: 'text/html', body: '<h1>oops</h1>' });
      return route.continue();
    });
    await menu('Deactivate…');
    await page.getByRole('alertdialog').getByRole('button', { name: 'Deactivate' }).click();
    await soft(page.getByRole('alertdialog').getByRole('alert').waitFor({ timeout: 20000 }), 'admin: refused deactivate shows the error in the dialog');
    await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
    await page.unroute('**/graphql');
    check(await row.getByText('Deactivated').count() === 0, 'admin: row not marked deactivated after failure');
    check((await state(page, EMAIL)).member.active === true, 'admin: API still active after failure');

    // --- deactivate / reactivate
    step = 'deactivate / reactivate';
    await menu('Deactivate…');
    await confirm('Deactivate');
    await page.reload();
    await soft(row.getByText('Deactivated').waitFor({ timeout: 20000 }), 'admin: Deactivated badge after reload');
    check((await state(page, EMAIL)).member.active === false, 'admin: deactivation read back from API');
    await menu('Reactivate');
    await page.waitForTimeout(800);
    check((await state(page, EMAIL)).member.active === true, 'admin: reactivation read back from API');

    // --- revoke invite
    step = 'revoke invite';
    await page.reload();
    await row.getByText('Invite pending').waitFor({ timeout: 20000 }).catch(() => {});
    await menu('Revoke invite…');
    await confirm('Revoke invite');
    check(!(await state(page, EMAIL)).pending, 'admin: invite revoked (API)');

    // --- remove and restore
    step = 'remove and restore';
    await menu('Remove from workspace…');
    await confirm('Remove from workspace');
    s = await state(page, EMAIL);
    check(!s.member && s.archived, 'admin: removed member is archived (API)');
    await page.getByRole('button', { name: `Actions for removed ${NAME}` }).click();
    await page.getByRole('menuitem', { name: 'Restore…' }).click();
    check(await page.getByRole('alertdialog').getByText(/API keys, browser sessions and their password do not/).isVisible(), 'admin: restore says keys and sessions do not come back');
    await confirm('Restore');
    s = await state(page, EMAIL);
    check(!!s.member && s.member.active && !s.archived, 'admin: restored member is active (API)');

    // tidy up: remove the invitee again so the list stays short
    await menu('Remove from workspace…');
    await confirm('Remove from workspace');
    check(errors.length === 0, `admin: no page errors (${errors.join('; ')})`);
    await ctx.close();

    // --- member: page is admin-only (registry), and the API refuses an invite
    step = 'member: page is admin-only (registry), and the API refuses an invite';
    const mctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await mctx.addInitScript((t) => localStorage.setItem('hoja_token', t), memberToken);
    const mp = await mctx.newPage();
    mp.setDefaultTimeout(60000);
    step = 'member';
    await mp.goto(`${baseUrl}/workspace/settings/members`);
    await soft(mp.getByText("You don't have access to this page").waitFor({ timeout: 20000 }), 'member: members settings locked');
    check(!(await mp.getByRole('button', { name: 'Invite people' }).count()), 'member: no Invite people button');
    const refused = await api(mp, 'mutation($i:MemberInviteInput!){memberInvite(input:$i){success}}', { i: { name: 'Nope', email: `nope-${stamp}@acme.test` } });
    check(refused && refused.memberInvite && refused.memberInvite.success === false, 'member: API refuses memberInvite without member.invite');
    await mctx.close();

    step = 'screenshots';
    for (const [ok, msg] of await screenshots(browser, baseUrl, adminToken, screenshotsDir, '/workspace/settings/members', 'members')) check(ok, msg);
    for (const [ok, msg] of await screenshots(browser, baseUrl, memberToken, screenshotsDir, '/workspace/settings/members', 'members-member')) check(ok, msg);
  } catch (e) {
    failed.push(`members journey crashed at '${step}': ${String(e.message || e).split('\n')[0]}`);
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
