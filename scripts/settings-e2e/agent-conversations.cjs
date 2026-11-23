#!/usr/bin/env node
// Settings → Agent conversations journey (HOJA-734): conversations created through the agent
// thread API are listed with their titles; rename (FormDialog) and delete (ConfirmAction) persist
// and read back from the API; a failed delete keeps the dialog open with the reason; "Delete all"
// (run as the member, so the admin's history is untouched) empties the list.
const H = require('./lib/account-helpers.cjs');

const threads = async (page) => (await H.restCall(page, '/api/agent/threads/get')).body.threads || [];
const create = async (page, text) => (await H.restCall(page, '/api/agent/threads/create', 'POST', { messages: [{ role: 'user', content: text }] })).body;

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch({ headless: true });
  const tag = `e2e${Date.now() % 1000000}`;
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    await step('admin rename and delete', async () => {
      await H.gotoSection(page, baseUrl, 'agent-conversations');
      const a = await create(page, `${tag} first chat`);
      const b = await create(page, `${tag} second chat`);
      await H.gotoSection(page, baseUrl, 'agent-conversations');
      const list = page.getByRole('list', { name: 'Saved conversations' });
      check(await list.getByText(`${tag} first chat`).isVisible(), 'conversation listed with its title');
      check(await page.getByText(/messages? · .* · Last active/).first().isVisible(), 'row shows messages, size and last activity');

      // Rename.
      const renamed = `${tag} renamed`;
      await page.getByRole('button', { name: `Actions for ${tag} first chat` }).click();
      await page.getByRole('menuitem', { name: 'Rename…' }).click();
      const dialog = page.getByRole('dialog', { name: 'Rename conversation' });
      await dialog.getByLabel('Name').fill(renamed);
      await dialog.getByRole('button', { name: 'Rename' }).click();
      await dialog.waitFor({ state: 'hidden', timeout: 8000 });
      check(await list.getByText(renamed).isVisible(), 'rename shows in the list');
      check((await threads(page)).find((t) => t.id === a.id)?.title === renamed, 'API reads back the new title');
      await H.gotoSection(page, baseUrl, 'agent-conversations');
      check(await page.getByRole('list', { name: 'Saved conversations' }).getByText(renamed).isVisible(), 'reload shows the new title');

      // Rename validation: too long disables submit.
      await page.getByRole('button', { name: `Actions for ${renamed}` }).click();
      await page.getByRole('menuitem', { name: 'Rename…' }).click();
      await dialog.getByLabel('Name').fill('x'.repeat(61));
      check(await dialog.getByRole('button', { name: 'Rename' }).isDisabled(), 'over-long name cannot be submitted');
      check(await dialog.getByText('Use 60 characters or fewer.').isVisible(), 'over-long name explains the limit');
      await dialog.getByRole('button', { name: 'Cancel' }).click();

      // Failed delete keeps the dialog open with the reason.
      const fail = async (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Simulated failure' }) });
      await page.route('**/api/agent/threads/delete/**', fail);
      await page.getByRole('button', { name: `Actions for ${tag} second chat` }).click();
      await page.getByRole('menuitem', { name: 'Delete…' }).click();
      const confirm = page.getByRole('alertdialog');
      await confirm.getByRole('button', { name: 'Delete conversation' }).click();
      await confirm.getByText('Simulated failure').waitFor({ timeout: 8000 });
      check(true, 'failed delete shows the reason in the dialog');
      check((await threads(page)).some((t) => t.id === b.id), 'failed delete kept the conversation');
      await page.unroute('**/api/agent/threads/delete/**', fail);
      await confirm.getByRole('button', { name: 'Delete conversation' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      check(!(await page.getByRole('list', { name: 'Saved conversations' }).getByText(`${tag} second chat`).count()), 'delete removes the row');
      check(!(await threads(page)).some((t) => t.id === b.id), 'API no longer has the deleted conversation');

      // Clean up the renamed one through the UI too.
      await page.getByRole('button', { name: `Actions for ${renamed}` }).click();
      await page.getByRole('menuitem', { name: 'Delete…' }).click();
      await confirm.getByRole('button', { name: 'Delete conversation' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      check(!(await threads(page)).some((t) => t.id === a.id), 'second delete persisted');
    });
    check(!page.errors.length, `no page errors ${page.errors.join(' | ')}`);
    await page.context().close();

    await step('member delete all', async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, 'agent-conversations');
      await create(m, `${tag} member one`);
      await create(m, `${tag} member two`);
      await H.gotoSection(m, baseUrl, 'agent-conversations');
      check((await m.locator('[data-conversation-count]').innerText()).trim() === String((await threads(m)).length), 'storage count matches the API');
      await m.getByRole('button', { name: 'Delete all…' }).click();
      const confirm = m.getByRole('alertdialog');
      await confirm.getByRole('button', { name: 'Delete all' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 15000 });
      check(await m.getByText('No saved conversations').isVisible(), 'delete all shows the empty state');
      check((await threads(m)).length === 0, 'API has no conversations left for the member');
      await m.context().close();
    });
    await step('overflow', () => H.noOverflow(browser, baseUrl, adminToken, 'agent-conversations', check));
    await step('screenshots', async () => {
      // Seed one conversation so the screenshots show a populated list, then remove it.
      const p = await H.openPage(browser, baseUrl, adminToken);
      await H.gotoSection(p, baseUrl, 'agent-conversations');
      const t = await create(p, 'Plan the Q3 launch checklist');
      await H.screenshots(browser, baseUrl, adminToken, 'agent-conversations', screenshotsDir);
      await H.restCall(p, `/api/agent/threads/delete/${t.id}`, 'DELETE', {});
      await p.context().close();
    });
  } finally { await browser.close(); }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, 'agent-conversations');
