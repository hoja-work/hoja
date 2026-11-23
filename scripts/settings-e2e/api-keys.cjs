#!/usr/bin/env node
// Settings → Connected apps & keys journey (HOJA-734).
//  - API key: create (FormDialog) → secret shown once in a CopyField and actually works → gone
//    after reload → failed revoke keeps the dialog open → revoke (ConfirmAction) → key refused.
//  - Signed-in app, browser sessions, another member's app: every revoke goes through a confirm and
//    reads back from the API. (Grants and sessions are seeded into the disposable stack DB.)
//  - Workspace policy: instant segmented control, reload + API read-back, the other security
//    fields untouched, failure rollback, Undo toast. Member: locked row, no workspace list.
const path = require('node:path');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const H = require('./lib/account-helpers.cjs');

const DB = process.env.SETTINGS_E2E_DB;
const PY = path.join(__dirname, '../../.venv/bin/python');
function seed(email, tag) {
  if (!fs.existsSync(DB) || !fs.existsSync(PY)) return null;
  return JSON.parse(execFileSync(PY, [path.join(__dirname, 'lib/seed_access.py'), DB, email, tag], { encoding: 'utf8' }));
}
const POLICY = '{workspaceSecurityPolicy{idleTimeoutMinutes allowedSigninMethods ipAllowlist oauthClients}}';

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch({ headless: true });
  const tag = `e2e${Date.now() % 1000000}`;
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    await step('api key lifecycle', async () => {
      await H.gotoSection(page, baseUrl, 'api-keys');
      const name = `Key ${tag}`;
      await page.getByRole('button', { name: 'Create API key' }).first().click();
      const dialog = page.getByRole('dialog', { name: 'Create API key' });
      check(await dialog.getByRole('button', { name: 'Create key' }).isDisabled(), 'create is disabled until named');
      await dialog.getByLabel('Name').fill(name);
      await dialog.getByLabel('Access').click();
      await page.getByRole('option', { name: /Read only/ }).click();
      await dialog.getByLabel('Expires after').click();
      await page.getByRole('option', { name: '30 days' }).click();
      await dialog.getByRole('button', { name: 'Create key' }).click();
      await dialog.waitFor({ state: 'hidden', timeout: 8000 });
      const panel = page.getByRole('region', { name: 'Your new API key' });
      check(await panel.getByText("it won't be shown again").isVisible(), 'secret panel says it is shown once');
      const secret = await panel.getByRole('textbox', { name: 'New API key', exact: true }).inputValue();
      check(secret.length > 20, 'secret is in a CopyField');
      const keys = (await H.api(page, '{myApiKeys{id label access expiresAt revokedAt}}')).data.myApiKeys;
      const key = keys.find((k) => k.label === name);
      check(!!key && key.access === 'read' && !!key.expiresAt, 'API has the key, read only, with an expiry');
      const days = key ? (Date.parse(key.expiresAt) - Date.now()) / 86400000 : 0;
      check(days > 29 && days < 31, `expiry is 30 days (${days.toFixed(1)})`);
      const viaKey = await page.evaluate(async (s) => (await fetch('/graphql', { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${s}` }, body: JSON.stringify({ query: '{viewer{email}}' }) })).json(), secret);
      check(viaKey.data?.viewer?.email === 'ada@acme.test', 'the new key authenticates');
      await panel.getByRole('button', { name: "I've saved it" }).click();
      check(!(await panel.count()), 'dismissing removes the secret');
      await H.gotoSection(page, baseUrl, 'api-keys');
      check(!(await page.getByRole('region', { name: 'Your new API key' }).count()), 'secret is not shown after reload');
      check(await page.getByRole('list', { name: 'API keys' }).getByText(name).isVisible(), 'reload lists the key');

      // Failed revoke keeps the dialog and the key.
      const stop = await H.failNextGraphql(page, /apiKeyRevoke/);
      await page.getByRole('button', { name: `Actions for ${name}` }).click();
      await page.getByRole('menuitem', { name: 'Revoke…' }).click();
      const confirm = page.getByRole('alertdialog');
      await confirm.getByRole('button', { name: 'Revoke key' }).click();
      await confirm.getByText('Simulated failure').waitFor({ timeout: 8000 });
      check(await stop(), 'failure injected');
      check(true, 'failed revoke shows the reason in the dialog');
      check(!(await H.api(page, '{myApiKeys{id revokedAt}}')).data.myApiKeys.find((k) => k.id === key.id).revokedAt, 'key still active after the failure');
      await confirm.getByRole('button', { name: 'Revoke key' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      check(!!(await H.api(page, '{myApiKeys{id revokedAt}}')).data.myApiKeys.find((k) => k.id === key.id).revokedAt, 'API shows the key revoked');
      check(!(await page.getByRole('list', { name: 'API keys' }).getByText(name).count()), 'revoked key leaves the Active list');
      await page.getByRole('radio', { name: /Inactive/ }).click();
      check(await page.getByRole('list', { name: 'API keys' }).getByText(name).isVisible(), 'revoked key is under Inactive');
      const refused = await page.evaluate(async (s) => (await fetch('/graphql', { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${s}` }, body: JSON.stringify({ query: '{viewer{email}}' }) })).status, secret);
      check(refused === 401 || refused === 403, `revoked key is refused (HTTP ${refused})`);
    });

    await step('apps and sessions', async () => {
      const mine = seed('ada@acme.test', tag);
      const theirs = seed('max@acme.test', `${tag}m`);
      if (!mine || !theirs) { check(false, 'could not seed grants/sessions (no stack DB)'); return; }
      await H.gotoSection(page, baseUrl, 'api-keys');
      const appName = `Hoja CLI ${tag}`;
      check(await page.getByRole('list', { name: 'Signed-in apps' }).getByText(`${appName} · laptop`).isVisible(), 'signed-in app listed');
      await page.getByRole('list', { name: 'Signed-in apps' }).getByRole('button', { name: `Actions for ${appName}` }).click();
      await page.getByRole('menuitem', { name: 'Revoke access…' }).click();
      const confirm = page.getByRole('alertdialog');
      check(await confirm.getByText('signed out everywhere').isVisible(), 'app revoke asks for confirmation and names the consequence');
      await confirm.getByRole('button', { name: 'Revoke access' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      const apps = (await H.api(page, '{myAuthorizedApps{id}}')).data.myAuthorizedApps;
      check(!apps.some((a) => a.id === mine.grant), 'API no longer lists the revoked app');

      const sessions = page.getByRole('list', { name: 'Browser sessions' });
      check((await sessions.getByRole('listitem').count()) >= 2, 'browser sessions listed');
      await sessions.getByRole('button', { name: /Actions for browser/ }).first().click();
      await page.getByRole('menuitem', { name: 'Sign out…' }).click();
      await confirm.getByRole('button', { name: 'Sign out' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      let live = (await H.api(page, '{mySessions{id}}')).data.mySessions.filter((s) => mine.sessions.includes(s.id));
      check(live.length === 1, 'API shows one session signed out');
      await page.getByRole('button', { name: 'Sign out other browsers' }).click();
      await confirm.getByRole('button', { name: 'Sign out others' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      live = (await H.api(page, '{mySessions{id}}')).data.mySessions.filter((s) => mine.sessions.includes(s.id));
      check(live.length === 0, 'sign out other browsers signs out the rest');

      const ws = page.getByRole('list', { name: 'Workspace apps' });
      const theirName = `Hoja CLI ${tag}m`;
      check(await ws.getByText(/Max Member/).first().isVisible(), "workspace list names the member who approved the app");
      await ws.getByRole('button', { name: `Actions for ${theirName}` }).click();
      await page.getByRole('menuitem', { name: 'Revoke access…' }).click();
      check(await confirm.getByText('Max Member').isVisible(), "revoking another member's app names them");
      await confirm.getByRole('button', { name: 'Revoke access' }).click();
      await confirm.waitFor({ state: 'hidden', timeout: 8000 });
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, 'api-keys');
      const theirApps = (await H.api(m, '{myAuthorizedApps{id}}')).data.myAuthorizedApps;
      check(!theirApps.some((a) => a.id === theirs.grant), "member's app revoked by the admin");
      // Clean the member's seeded sessions.
      for (const id of theirs.sessions) await H.api(m, 'mutation($id:String!){sessionRevoke(id:$id)}', { id });
      await m.context().close();
    });

    await step('workspace app policy', async () => {
      await H.gotoSection(page, baseUrl, 'api-keys');
      const before = (await H.api(page, POLICY)).data.workspaceSecurityPolicy;
      const any = page.getByRole('radio', { name: 'Any app' });
      const firstParty = page.getByRole('radio', { name: 'Hoja apps only' });
      if (before.oauthClients === 'first_party') { await any.click(); await page.waitForTimeout(800); }
      await firstParty.click();
      await page.locator('[data-save-status="saved"]').first().waitFor({ timeout: 8000 });
      const after = (await H.api(page, POLICY)).data.workspaceSecurityPolicy;
      check(after.oauthClients === 'first_party', 'API reads back first_party');
      check(JSON.stringify([after.idleTimeoutMinutes, after.allowedSigninMethods, after.ipAllowlist]) === JSON.stringify([before.idleTimeoutMinutes, before.allowedSigninMethods, before.ipAllowlist]), 'other security fields untouched');
      await H.gotoSection(page, baseUrl, 'api-keys');
      check((await page.getByRole('radio', { name: 'Hoja apps only' }).getAttribute('aria-checked')) === 'true', 'reload shows Hoja apps only');

      const stop = await H.failNextGraphql(page, /workspaceSecurityUpdate/);
      await page.getByRole('radio', { name: 'Any app' }).click();
      await page.getByRole('alert').filter({ hasText: 'Simulated failure' }).waitFor({ timeout: 8000 });
      check(await stop(), 'failure injected');
      check((await page.getByRole('radio', { name: 'Hoja apps only' }).getAttribute('aria-checked')) === 'true', 'failed save snaps back');
      check((await H.api(page, POLICY)).data.workspaceSecurityPolicy.oauthClients === 'first_party', 'server unchanged after failure');

      await page.getByRole('radio', { name: 'Any app' }).click();
      await page.getByRole('button', { name: 'Undo' }).click();
      await page.waitForTimeout(1200);
      check((await H.api(page, POLICY)).data.workspaceSecurityPolicy.oauthClients === 'first_party', 'Undo restores the previous policy');
      await page.getByRole('radio', { name: 'Any app' }).click();
      await page.waitForTimeout(1200);
      check((await H.api(page, POLICY)).data.workspaceSecurityPolicy.oauthClients === (before.oauthClients || 'all'), 'policy restored');
    });
    check(!page.errors.length, `no page errors ${page.errors.join(' | ')}`);
    await page.context().close();

    await step('member', async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, 'api-keys');
      check(await m.getByText('Only workspace admins can see and change this.').isVisible(), 'member sees the policy locked with a reason');
      check(!(await m.getByRole('radio', { name: 'Hoja apps only' }).count()), 'member has no policy control');
      check(!(await m.getByRole('heading', { name: 'Apps with access to this workspace' }).count()), "member doesn't see every member's apps");
      check(await m.getByRole('button', { name: 'Create API key' }).first().isEnabled(), 'member can create their own keys');
      check(!m.errors.length, `member: no page errors ${m.errors.join(' | ')}`);
      await m.context().close();
    });
    await step('overflow', () => H.noOverflow(browser, baseUrl, adminToken, 'api-keys', check));
    await step('screenshots', () => H.screenshots(browser, baseUrl, adminToken, 'api-keys', screenshotsDir));
  } finally { await browser.close(); }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, 'api-keys');
