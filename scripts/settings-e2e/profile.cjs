#!/usr/bin/env node
// Settings → Profile journey (HOJA-734): rename saves on Enter/blur, reloads, reads back from the
// API; Escape reverts; empty name is refused before any request; a failed save rolls back with an
// inline error; read-only rows (email, role) are present for admin and member.
const H = require('./lib/account-helpers.cjs');

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    let original = '';
    await step('admin rename', async () => {
      await H.gotoSection(page, baseUrl, 'profile');
      const field = page.getByLabel('Display name');
      original = await field.inputValue();
      const next = `Ada E2E ${Date.now() % 100000}`;
      await field.fill(next);
      await field.press('Enter');
      await page.getByText('Saved', { exact: true }).waitFor({ timeout: 8000 });
      check(true, 'Enter saves the name and shows Saved');
      const api = await H.api(page, '{viewer{name}}');
      check(api.data.viewer.name === next, `API reads back the new name (${api.data.viewer.name})`);
      await H.gotoSection(page, baseUrl, 'profile');
      check((await page.getByLabel('Display name').inputValue()) === next, 'reload shows the new name');

      // Escape reverts without saving.
      await page.getByLabel('Display name').fill('Should not save');
      await page.getByLabel('Display name').press('Escape');
      check((await page.getByLabel('Display name').inputValue()) === next, 'Escape reverts the draft');
      check((await H.api(page, '{viewer{name}}')).data.viewer.name === next, 'Escape sent nothing');

      // Empty is refused client-side.
      await page.getByLabel('Display name').fill('   ');
      await page.getByLabel('Display name').press('Enter');
      check(await page.getByRole('alert').filter({ hasText: 'Enter a name' }).isVisible(), 'empty name shows a validation error');
      check((await H.api(page, '{viewer{name}}')).data.viewer.name === next, 'empty name sent nothing');
      await page.getByLabel('Display name').press('Escape');

      // Server failure → rollback + error.
      const stop = await H.failNextGraphql(page, /profileNameUpdate/);
      await page.getByLabel('Display name').fill('Rollback me');
      await page.getByLabel('Display name').press('Enter');
      await page.getByRole('alert').filter({ hasText: 'Simulated failure' }).waitFor({ timeout: 8000 });
      check(true, 'failed save shows the error inline');
      check(await stop(), 'failure was injected');
      check((await page.getByLabel('Display name').inputValue()) === next, 'failed save rolls the field back');
      check((await H.api(page, '{viewer{name}}')).data.viewer.name === next, 'server name unchanged after failure');

      // Read-only rows.
      check(await page.getByText("Comes from how you sign in, so it can't be changed here.").isVisible(), 'email is a locked row');
      check(await page.getByText('Workspace admin', { exact: true }).isVisible(), 'admin sees the admin role');

      // Restore.
      await page.getByLabel('Display name').fill(original || 'Ada Admin');
      await page.getByLabel('Display name').blur();
      await page.getByText('Saved', { exact: true }).waitFor({ timeout: 8000 });
      check((await H.api(page, '{viewer{name}}')).data.viewer.name === (original || 'Ada Admin'), 'blur saves (name restored)');
    });
    check(!page.errors.length, `no page errors ${page.errors.join(' | ')}`);
    await page.context().close();

    await step('member', async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, 'profile');
      check(await m.getByLabel('Display name').isEditable(), 'member can edit their own name');
      check(await m.getByText('Only a workspace admin can change your role.').isVisible(), 'member sees role locked with a reason');
      check(await m.getByText('Member', { exact: true }).isVisible(), 'member sees the Member role');
      await m.context().close();
    });
    await step('overflow', () => H.noOverflow(browser, baseUrl, adminToken, 'profile', check));
    await step('screenshots', () => H.screenshots(browser, baseUrl, adminToken, 'profile', screenshotsDir));
  } finally { await browser.close(); }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, 'profile');
