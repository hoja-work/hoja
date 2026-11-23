#!/usr/bin/env node
// Settings → Notifications journey (HOJA-734): every kind the backend accepts has a switch that
// saves instantly, survives a reload and reads back from the API; "Assigned to me" also writes the
// legacy issueCreated kind; a failed save snaps the switch back with an inline error; a member
// manages their own preferences.
const H = require('./lib/account-helpers.cjs');

const ROWS = {
  issueAssigned: 'Assigned to me', issueMention: 'Mentions', issueDecision: 'Decision requests',
  issueComment: 'New comments', issueStatusChanged: 'Status changes', issueUpdated: 'Priority and due date changes',
  projectLeadAssigned: 'Made project lead', projectMemberAdded: 'Added to a project', projectMention: 'Mentions',
  projectUpdatePosted: 'Project updates', projectUpdateReply: 'Replies', projectUpdated: 'Status, target date and lead changes',
  initiativeOwnerAssigned: 'Made initiative owner', initiativeUpdatePosted: 'Initiative updates',
  initiativeUpdated: 'Status, target date and owner changes', cycleStarted: 'Cycle started', cycleCompleted: 'Cycle ended',
  viewIssueAdded: 'Tasks added to a view', viewIssueCompleted: 'Tasks finished in a view',
};
const FIELDS = [...Object.keys(ROWS), 'issueCreated'].join(' ');
const sw = (page, kind) => page.locator(`[data-notification-kind="${kind}"]`);
const prefs = async (page) => (await H.api(page, `{myNotificationPreferences{${FIELDS}}}`)).data.myNotificationPreferences;

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    await step('admin toggles every kind', async () => {
      await H.gotoSection(page, baseUrl, 'notifications');
      const before = await prefs(page);
      // Accessible names: each switch is labelled by its row.
      check(await page.getByRole('switch', { name: 'Tasks added to a view' }).isVisible(), 'view kinds are exposed (viewIssueAdded)');
      check(await page.getByRole('switch', { name: 'Tasks finished in a view' }).isVisible(), 'view kinds are exposed (viewIssueCompleted)');
      check((await page.getByRole('switch').count()) === Object.keys(ROWS).length, `one switch per kind (${Object.keys(ROWS).length})`);
      for (const kind of Object.keys(ROWS)) {
        await sw(page, kind).click();
        await page.waitForFunction((k) => document.querySelector(`[data-notification-kind="${k}"]`)
          ?.closest('[data-setting-row]')?.querySelector('[data-save-status="saved"]'), kind, { timeout: 8000 });
      }
      const after = await prefs(page);
      const flipped = Object.keys(ROWS).filter((k) => after[k] === !before[k]);
      check(flipped.length === Object.keys(ROWS).length, `API reads back every flipped kind (${flipped.length}/${Object.keys(ROWS).length})`);
      check(after.issueCreated === after.issueAssigned, 'legacy issueCreated follows "Assigned to me"');
      await H.gotoSection(page, baseUrl, 'notifications');
      let shown = 0;
      for (const kind of Object.keys(ROWS)) if ((await sw(page, kind).getAttribute('aria-checked')) === String(after[kind])) shown++;
      check(shown === Object.keys(ROWS).length, `reload shows every saved value (${shown})`);

      // Failure: switch snaps back, error inline with Retry, server unchanged.
      const kind = 'issueComment';
      const was = (await sw(page, kind).getAttribute('aria-checked')) === 'true';
      const stop = await H.failNextGraphql(page, /notificationPreferenceSet/);
      await sw(page, kind).click();
      const row = sw(page, kind).locator('xpath=ancestor::*[@data-setting-row][1]');
      await row.getByRole('alert').waitFor({ timeout: 8000 });
      check(await stop(), 'failure injected');
      check((await sw(page, kind).getAttribute('aria-checked')) === String(was), 'failed save snaps the switch back');
      check(await row.getByRole('button', { name: 'Retry' }).isVisible(), 'error offers Retry');
      check((await prefs(page))[kind] === was, 'server value unchanged after failure');
      await row.getByRole('button', { name: 'Retry' }).click();
      await row.locator('[data-save-status="saved"]').waitFor({ timeout: 8000 });
      check((await prefs(page))[kind] === !was, 'Retry saves the change');

      // Restore the starting state (idempotent re-runs).
      const now = await prefs(page);
      for (const k of Object.keys(ROWS)) if (now[k] !== before[k]) {
        await sw(page, k).click();
        await page.waitForFunction((x) => document.querySelector(`[data-notification-kind="${x}"]`)
          ?.closest('[data-setting-row]')?.querySelector('[data-save-status="saved"]'), k, { timeout: 8000 });
      }
      const restored = await prefs(page);
      check(Object.keys(ROWS).every((k) => restored[k] === before[k]), 'preferences restored');
    });
    check(!page.errors.length, `no page errors ${page.errors.join(' | ')}`);
    await page.context().close();

    await step('member', async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, 'notifications');
      const before = (await prefs(m)).cycleStarted;
      await sw(m, 'cycleStarted').click();
      await m.locator('[data-save-status="saved"]').first().waitFor({ timeout: 8000 });
      check((await prefs(m)).cycleStarted === !before, 'member changes their own preference');
      await sw(m, 'cycleStarted').click();
      await m.waitForTimeout(800);
      check((await prefs(m)).cycleStarted === before, 'member preference restored');
      await m.context().close();
    });
    await step('overflow', () => H.noOverflow(browser, baseUrl, adminToken, 'notifications', check));
    await step('screenshots', () => H.screenshots(browser, baseUrl, adminToken, 'notifications', screenshotsDir));
  } finally { await browser.close(); }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, 'notifications');
