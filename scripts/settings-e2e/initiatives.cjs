#!/usr/bin/env node
// Settings → Initiatives, end to end (HOJA-734).
// Admin: reminder cadence/day/time/follow-ups (instant, reload, API read-back) and a forced
// failure that rolls back with Retry; label groups and labels (create, inline rename on Enter,
// Escape reverts, colour + group dialog, allow-several switch, archive via ConfirmAction);
// team sidebar switch (instant, read back, forced failure rolls back). Member: schedule is
// locked (disabled + reason), labels stay editable because the API allows any signed-in member.
const fs = require('node:fs');
const path = require('node:path');

const SECTION = 'initiatives';

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
  const GROUP = `E2E horizon ${stamp}`;
  const LABEL = `E2E now ${stamp}`;
  const LABEL2 = `E2E next ${stamp}`;
  const policy = async () => (await api(baseUrl, adminToken, '{initiativeReminderPolicy{cadence weekday hour followUps}}')).data.initiativeReminderPolicy;
  const labels = async () => (await api(baseUrl, adminToken, '{initiativeLabels{id name color isGroup exclusive parent{id}}}')).data.initiativeLabels;
  const teams = async () => (await api(baseUrl, adminToken, '{teams{nodes{id name showInitiatives}}}')).data.teams.nodes;
  const originalPolicy = await policy();
  const allTeams = await teams();
  const team = allTeams.find((t) => !/e2e/i.test(t.name)) || allTeams[0];
  const originalShow = team.showInitiatives;
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
    await page.getByRole('heading', { level: 1, name: 'Initiatives' }).waitFor({ timeout: 20000 });
    await page.getByRole('combobox', { name: 'Remind owners' }).waitFor({ timeout: 15000 });
    await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 });
  };
  const pick = async (page, combobox, option) => {
    await page.getByRole('combobox', { name: combobox }).click();
    await page.getByRole('option', { name: option, exact: true }).click();
  };
  const saved = (page) => page.getByText('Saved', { exact: true }).first().waitFor();
  const comboText = (page, name) => page.getByRole('combobox', { name }).innerText();

  try {
    const page = await open(adminToken);
    await step('admin: page renders', () => goto(page));

    await step('admin: reminder cadence, day, time and follow-ups save instantly and read back', async () => {
      await pick(page, 'Remind owners', 'Every two weeks');
      await saved(page);
      await pick(page, 'Reminder day', 'Tuesday');
      await saved(page);
      await pick(page, 'Reminder time', '14:00');
      await saved(page);
      const sw = page.getByRole('switch', { name: 'Follow-ups' });
      const was = (await sw.getAttribute('aria-checked')) === 'true';
      await sw.click();
      await saved(page);
      await goto(page);
      if ((await comboText(page, 'Remind owners')).trim() !== 'Every two weeks') throw new Error('cadence not shown');
      if ((await comboText(page, 'Reminder day')).trim() !== 'Tuesday') throw new Error('day not shown');
      if ((await comboText(page, 'Reminder time')).trim() !== '14:00') throw new Error('time not shown');
      if (((await page.getByRole('switch', { name: 'Follow-ups' }).getAttribute('aria-checked')) === 'true') === was) throw new Error('follow-ups not shown');
      const p = await policy();
      if (p.cadence !== 'biweekly' || p.weekday !== 1 || p.hour !== 14 || p.followUps === was) throw new Error(JSON.stringify(p));
    });

    await step('admin: forced schedule failure rolls back with the reason and Retry', async () => {
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('initiativeReminderPolicyUpdate')
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ errors: [{ message: 'Simulated schedule failure.' }] }) })
        : route.continue()));
      await pick(page, 'Remind owners', 'Every month');
      await page.getByRole('alert').filter({ hasText: 'Simulated schedule failure.' }).waitFor();
      await page.getByRole('button', { name: 'Retry' }).waitFor();
      if ((await comboText(page, 'Remind owners')).trim() !== 'Every two weeks') throw new Error('did not roll back');
      await page.unroute('**/graphql');
      if ((await policy()).cadence !== 'biweekly') throw new Error('server changed');
    });

    await step('admin: create a label group (allow several)', async () => {
      await page.getByRole('button', { name: 'New group' }).click();
      const dlg = page.getByRole('dialog', { name: 'New label group' });
      await dlg.getByLabel('Name').fill(GROUP);
      await dlg.getByRole('switch').click();
      await dlg.getByRole('button', { name: 'Add group' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await page.getByRole('textbox', { name: `Name of group ${GROUP}` }).waitFor();
      const g = (await labels()).find((l) => l.name === GROUP);
      if (!g || !g.isGroup || g.exclusive !== false) throw new Error(JSON.stringify(g));
    });

    await step('admin: duplicate label name is caught before the request', async () => {
      await page.getByRole('button', { name: 'New label' }).first().click();
      const dlg = page.getByRole('dialog', { name: 'New initiative label' });
      await dlg.getByLabel('Name').fill(GROUP.toUpperCase());
      await dlg.getByText('already exists').waitFor();
      if (await dlg.getByRole('button', { name: 'Add label' }).isEnabled()) throw new Error('submit enabled');
      await dlg.getByRole('button', { name: 'Cancel' }).click();
    });

    await step('admin: add a label to the group with a colour', async () => {
      await page.getByRole('button', { name: `Actions for group ${GROUP}` }).click();
      await page.getByRole('menuitem', { name: /Add a label to this group/ }).click();
      const dlg = page.getByRole('dialog', { name: 'New initiative label' });
      await dlg.getByLabel('Name').fill(LABEL);
      if ((await dlg.getByRole('combobox', { name: 'Group' }).innerText()).trim() !== GROUP) throw new Error('group not preselected');
      await dlg.getByRole('radio', { name: '#4cb782' }).click();
      await dlg.getByRole('button', { name: 'Add label' }).click();
      await dlg.waitFor({ state: 'hidden' });
      const l = (await labels()).find((x) => x.name === LABEL);
      const g = (await labels()).find((x) => x.name === GROUP);
      if (!l || l.parent?.id !== g.id || l.color !== '#4cb782') throw new Error(JSON.stringify(l));
    });

    await step('admin: inline rename saves on Enter, Escape reverts, reload reads back', async () => {
      const box = page.getByRole('textbox', { name: `Name of label ${LABEL}` });
      await box.fill('throwaway');
      await box.press('Escape');
      if ((await page.getByRole('textbox', { name: `Name of label ${LABEL}` }).inputValue()) !== LABEL) throw new Error('escape did not revert');
      await page.getByRole('textbox', { name: `Name of label ${LABEL}` }).fill(LABEL2);
      await page.getByRole('textbox', { name: `Name of label ${LABEL}` }).press('Enter');
      await saved(page);
      await goto(page);
      await page.getByRole('textbox', { name: `Name of label ${LABEL2}` }).waitFor();
      if (!(await labels()).some((x) => x.name === LABEL2)) throw new Error('API not renamed');
    });

    await step('admin: empty inline name is refused before the request', async () => {
      const box = page.getByRole('textbox', { name: `Name of label ${LABEL2}` });
      await box.fill('');
      await box.press('Enter');
      await page.getByRole('alert').filter({ hasText: 'Give it a name.' }).waitFor();
      await box.press('Escape');
    });

    await step('admin: colour and group dialog moves the label out of its group', async () => {
      await page.getByRole('button', { name: `Actions for label ${LABEL2}` }).click();
      await page.getByRole('menuitem', { name: /Colour and group/ }).click();
      const dlg = page.getByRole('dialog', { name: `Edit “${LABEL2}”` });
      await dlg.getByRole('combobox', { name: 'Group' }).click();
      await page.getByRole('option', { name: 'No group', exact: true }).click();
      await dlg.getByRole('radio', { name: 'No colour' }).click();
      await dlg.getByRole('button', { name: 'Save label' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await goto(page);
      const l = (await labels()).find((x) => x.name === LABEL2);
      if (!l || l.parent || l.color) throw new Error(JSON.stringify(l));
      await page.getByText('Not in a group').waitFor();
    });

    await step('admin: allow-several switch saves instantly and reads back', async () => {
      const sw = page.getByRole('switch', { name: `Allow several labels from ${GROUP}` });
      await sw.click();
      await saved(page);
      await goto(page);
      if ((await page.getByRole('switch', { name: `Allow several labels from ${GROUP}` }).getAttribute('aria-checked')) !== 'false') throw new Error('not shown');
      if ((await labels()).find((x) => x.name === GROUP).exclusive !== true) throw new Error('API not updated');
    });

    await step('admin: team sidebar switch saves instantly and reads back', async () => {
      const name = `Show initiatives in the ${team.name} sidebar`;
      const sw = page.getByRole('switch', { name });
      const before = (await sw.getAttribute('aria-checked')) === 'true';
      await sw.click();
      await saved(page);
      await goto(page);
      if (((await page.getByRole('switch', { name }).getAttribute('aria-checked')) === 'true') === before) throw new Error('not shown');
      if ((await teams()).find((t) => t.id === team.id).showInitiatives === before) throw new Error('API not updated');
    });

    await step('admin: forced team switch failure rolls back (HOJA-734 fix: was optimistic without rollback)', async () => {
      const name = `Show initiatives in the ${team.name} sidebar`;
      const before = (await page.getByRole('switch', { name }).getAttribute('aria-checked'));
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('teamShowInitiatives')
        ? route.abort('failed') : route.continue()));
      await page.getByRole('switch', { name }).click();
      await page.getByRole('alert').filter({ hasText: /Couldn't reach the server/ }).waitFor();
      if ((await page.getByRole('switch', { name }).getAttribute('aria-checked')) !== before) throw new Error('did not roll back');
      await page.unroute('**/graphql');
    });

    if (screenshotsDir) await step('screenshots light/dark at 1440 and 390', async () => {
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        const p = await open(adminToken, { width: w, height: h });
        await goto(p);
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-light.png`), fullPage: true });
        await p.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
        await p.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}-dark.png`), fullPage: true });
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

    await step('admin: archive label and group through ConfirmAction', async () => {
      await page.getByRole('button', { name: `Actions for label ${LABEL2}` }).click();
      await page.getByRole('menuitem', { name: /Archive label/ }).click();
      let dlg = page.getByRole('alertdialog');
      await dlg.getByText(/cannot be undone/).waitFor();
      await dlg.getByRole('button', { name: 'Archive label' }).click();
      await dlg.waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: `Actions for group ${GROUP}` }).click();
      await page.getByRole('menuitem', { name: /Archive group/ }).click();
      dlg = page.getByRole('alertdialog');
      await dlg.getByRole('button', { name: 'Archive group' }).click();
      await dlg.waitFor({ state: 'hidden' });
      const left = (await labels()).filter((x) => x.name.includes(stamp));
      if (left.length) throw new Error(JSON.stringify(left));
    });
    check('admin: no page errors', page.errors.length === 0, page.errors.join(' | '));

    const member = await open(memberToken);
    await step('member: schedule is visible but locked with a reason', async () => {
      await goto(member);
      if (await member.getByRole('combobox', { name: 'Remind owners' }).isEnabled()) throw new Error('cadence enabled');
      if (await member.getByRole('switch', { name: 'Follow-ups' }).isEnabled()) throw new Error('follow-ups enabled');
      await member.getByText('Only workspace admins can change the schedule.').waitFor();
      if (!(await api(baseUrl, memberToken, 'mutation{initiativeReminderPolicyUpdate(input:{cadence:"weekly"}){cadence}}')).errors) throw new Error('API allowed member');
    });
    await step('member: labels stay editable (API allows any signed-in member)', async () => {
      if (!(await member.getByRole('button', { name: 'New label' }).first().isEnabled())) throw new Error('disabled');
    });
    if (screenshotsDir) await member.screenshot({ path: path.join(screenshotsDir, `${SECTION}-member-1440-light.png`), fullPage: true });
  } finally {
    await api(baseUrl, adminToken, 'mutation($i:InitiativeReminderPolicyInput!){initiativeReminderPolicyUpdate(input:$i){cadence}}', { i: originalPolicy });
    await api(baseUrl, adminToken, 'mutation($t:String!,$s:Boolean!){teamShowInitiatives(teamId:$t,show:$s){id}}', { t: team.id, s: Boolean(originalShow) });
    for (const l of await labels()) if (l.name.includes(stamp)) await api(baseUrl, adminToken, 'mutation($id:String!){initiativeLabelArchive(id:$id){success}}', { id: l.id });
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
