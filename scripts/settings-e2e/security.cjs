#!/usr/bin/env node
// Settings → Security, end to end (HOJA-734).
// Admin: the policy form validates idle timeout and every allowlist line client-side (Save stays
// disabled, bad lines are named), keeps at least one sign-in method, warns about refused
// methods, asks before a stricter save (Cancel saves nothing), saves once, survives reload, is
// read back from the API; Discard restores; a failed save keeps the typed values and explains.
// Connect kill switch: turning off asks first, saves instantly, does not clear the policy; turning
// on is instant; a failure rolls back. Member: admin-only page, API FORBIDDEN, member token
// still works. The allowlist used always contains loopback, and the original policy is restored
// in a finally block, so the test tokens are never locked out.
const path = require('node:path');
const fs = require('node:fs');

const SECTION = 'security';

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

async function shots(browser, baseUrl, token, dir, prefix) {
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    for (const theme of ['light', 'dark']) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
      const p = await ctx.newPage();
      await p.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await p.waitForSelector('[data-settings-page]');
      await p.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 }).catch(() => {});
      await p.waitForLoadState('networkidle').catch(() => {});
      await p.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 }).catch(() => {});
      await p.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
      await p.waitForTimeout(300);
      await p.screenshot({ path: path.join(dir, `${prefix}-${w}-${theme}.png`), fullPage: true });
      await ctx.close();
    }
  }
}

const FIELDS = 'idleTimeoutMinutes allowedSigninMethods ipAllowlist connectEnabled';
const Q = `{workspaceSecurityPolicy{${FIELDS}}}`;
const SET = `mutation($i:WorkspaceSecurityPolicyInput!){workspaceSecurityUpdate(input:$i){${FIELDS}}}`;

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = loadPlaywright();
  const passed = [], failed = [];
  const check = (name, cond, extra = '') => (cond ? passed.push(name) : failed.push(`${name}${extra ? ` (${extra})` : ''}`));
  const step = async (name, fn) => {
    try { await fn(); passed.push(name); } catch (e) { failed.push(`${name}: ${String(e.message || e).split('\n')[0]}`); }
  };
  const policy = async () => (await api(baseUrl, adminToken, Q)).data.workspaceSecurityPolicy;
  const original = await policy();
  const restore = () => api(baseUrl, adminToken, SET, { i: {
    idleTimeoutMinutes: original.idleTimeoutMinutes, allowedSigninMethods: original.allowedSigninMethods,
    ipAllowlist: original.ipAllowlist, connectEnabled: original.connectEnabled } });
  const browser = await chromium.launch();
  const open = async (token, viewport = { width: 1440, height: 900 }) => {
    const ctx = await browser.newContext({ viewport });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    const p = await ctx.newPage();
    p.errors = [];
    p.on('pageerror', (e) => p.errors.push(e.message));
    return p;
  };
  const goto = async (p) => {
    await p.goto(`${baseUrl}/workspace/settings/${SECTION}`);
    await p.getByRole('heading', { level: 1, name: 'Security' }).waitFor({ timeout: 20000 });
    await p.getByLabel('Idle timeout').waitFor({ timeout: 15000 });
  };
  const until = async (fn, what) => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) { if (await fn()) return; await new Promise((r) => setTimeout(r, 150)); }
    throw new Error(what);
  };
  const LIST = ['127.0.0.0/8', '::1/128', '203.0.113.7/24'];

  try {
    const page = await open(adminToken);
    await goto(page);
    const idle = page.getByLabel('Idle timeout');
    const list = page.getByLabel('IP allowlist');
    const password = page.getByRole('checkbox', { name: 'Email and password' });
    const google = page.getByRole('checkbox', { name: 'Google' });
    const saveBtn = page.getByRole('button', { name: 'Save security policy' });

    await step('admin: Save is disabled until something changes', async () => {
      if (!(await saveBtn.isDisabled())) throw new Error('enabled with no changes');
    });
    await step('admin: idle timeout is validated before saving', async () => {
      for (const bad of ['abc', '0', '10081', '1.5']) {
        await idle.fill(bad);
        await page.getByRole('alert').filter({ hasText: /whole minutes from 1 to 10080/ }).waitFor({ timeout: 3000 });
        if (!(await saveBtn.isDisabled())) throw new Error(`Save enabled for ${bad}`);
      }
      await idle.fill('90');
      await page.getByText('minutes (1 h 30 min)').waitFor();
    });
    await step('admin: invalid allowlist lines are named and block saving', async () => {
      await list.fill('10.0.0.0/8\nnot-an-ip\n300.1.1.1\n10.0.0.0/33');
      const alert = page.getByRole('alert').filter({ hasText: /aren't an IP address or CIDR range/ });
      await alert.waitFor();
      const text = await alert.innerText();
      for (const bad of ['not-an-ip', '300.1.1.1', '10.0.0.0/33']) if (!text.includes(bad)) throw new Error(`${bad} not named`);
      if (text.includes('10.0.0.0/8,')) throw new Error('valid line flagged');
      if (!(await saveBtn.isDisabled())) throw new Error('Save enabled with bad lines');
      await list.fill(LIST.join('\n'));
      await page.getByText(/3 ranges\. Hoja can't see the address/).waitFor();
    });
    await step('admin: at least one sign-in method stays on; refusing one warns', async () => {
      await google.click();
      await page.getByText('Google sign-in will be refused').waitFor();
      if (!(await password.isDisabled())) throw new Error('last method can be unticked');
      await google.click();
      await password.click();
      await page.getByText('Password sign-in will be refused').waitFor();
      await password.click();
      await google.click();
    });
    await step('admin: a stricter save asks first; Cancel saves nothing', async () => {
      await saveBtn.click();
      const dlg = page.getByRole('alertdialog', { name: 'Save a stricter security policy?' });
      await dlg.getByText(/Google sign-in will be refused/).waitFor();
      await dlg.getByText(/only be let in from 3 ranges/).waitFor();
      await dlg.getByRole('button', { name: 'Cancel' }).click();
      await dlg.waitFor({ state: 'hidden' });
      const p = await policy();
      if (p.idleTimeoutMinutes !== original.idleTimeoutMinutes) throw new Error('Cancel saved');
      if ((await idle.inputValue()) !== '90') throw new Error('Cancel lost the draft');
    });
    await step('admin: confirmed save writes all three fields, survives reload, read back from API', async () => {
      await saveBtn.click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Save security policy' }).click();
      await page.locator('[data-save-status="saved"]').first().waitFor({ timeout: 8000 });
      const p = await policy();
      if (p.idleTimeoutMinutes !== 90) throw new Error(`idle ${p.idleTimeoutMinutes}`);
      if (p.allowedSigninMethods.join() !== 'password') throw new Error(`methods ${p.allowedSigninMethods}`);
      if (p.ipAllowlist.join() !== '127.0.0.0/8,::1/128,203.0.113.0/24') throw new Error(`allowlist ${p.ipAllowlist}`);
      await page.reload();
      await goto(page);
      await until(async () => (await idle.inputValue()) === '90', 'idle after reload');
      if ((await list.inputValue()) !== '127.0.0.0/8\n::1/128\n203.0.113.0/24') throw new Error('allowlist after reload (normalized)');
      if ((await google.getAttribute('aria-checked')) !== 'false') throw new Error('google after reload');
    });
    await step('admin: Discard changes restores the saved values', async () => {
      await idle.fill('30');
      await page.getByText('You have unsaved changes.').waitFor();
      await page.getByRole('button', { name: 'Discard changes' }).click();
      if ((await idle.inputValue()) !== '90') throw new Error('not discarded');
    });
    await step('admin: a failed save keeps the typed values and explains', async () => {
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('workspaceSecurityUpdate') ? route.fulfill({ status: 502, contentType: 'text/html', body: '<html>bad gateway</html>' }) : route.continue()));
      await idle.fill('45');
      await saveBtn.click();
      await page.getByRole('alert').filter({ hasText: /server is unavailable/i }).waitFor();
      await page.unroute('**/graphql');
      if ((await idle.inputValue()) !== '45') throw new Error('typed value lost');
      if ((await policy()).idleTimeoutMinutes !== 90) throw new Error('API changed');
      await page.getByRole('button', { name: 'Discard changes' }).click();
    });
    await step('live server: an out-of-range idle timeout is BAD_USER_INPUT', async () => {
      const r = await api(baseUrl, adminToken, SET, { i: { idleTimeoutMinutes: 0, allowedSigninMethods: [], ipAllowlist: [] } });
      if (r.errors?.[0]?.extensions?.code !== 'BAD_USER_INPUT') throw new Error(JSON.stringify(r));
    });
    await step('member: still signed in under the saved allowlist (loopback listed)', async () => {
      const r = await api(baseUrl, memberToken, '{viewer{id}}');
      if (!r.data?.viewer?.id) throw new Error(JSON.stringify(r));
    });

    const connect = page.getByRole('switch', { name: 'Allow Hoja Connect' });
    await step('admin: turning Connect off asks first; Cancel keeps it on', async () => {
      if ((await connect.getAttribute('aria-checked')) !== 'true') {
        await connect.click();
        await until(async () => (await policy()).connectEnabled === true, 'could not start from on');
      }
      await connect.click();
      const dlg = page.getByRole('alertdialog', { name: 'Turn off Hoja Connect?' });
      await dlg.getByRole('button', { name: 'Cancel' }).click();
      await dlg.waitFor({ state: 'hidden' });
      if ((await connect.getAttribute('aria-checked')) !== 'true') throw new Error('switched off on cancel');
      if ((await policy()).connectEnabled !== true) throw new Error('API changed on cancel');
    });
    await step('admin: Connect off saves, keeps the rest of the policy, survives reload', async () => {
      await connect.click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Turn off Connect' }).click();
      await until(async () => (await policy()).connectEnabled === false, 'API not updated');
      const p = await policy();
      if (p.idleTimeoutMinutes !== 90 || p.ipAllowlist.length !== 3 || p.allowedSigninMethods.join() !== 'password') throw new Error(`policy clobbered: ${JSON.stringify(p)}`);
      await page.reload();
      await goto(page);
      await until(async () => (await connect.getAttribute('aria-checked')) === 'false', 'not off after reload');
    });
    await step('admin: Connect on is instant; a failure rolls back with an error', async () => {
      await page.route('**/graphql', (route) => ((route.request().postData() || '').includes('workspaceSecurityUpdate') ? route.abort() : route.continue()));
      await connect.click();
      await page.getByRole('alert').filter({ hasText: "Couldn't reach the server" }).waitFor();
      if ((await connect.getAttribute('aria-checked')) !== 'false') throw new Error('did not roll back');
      await page.unroute('**/graphql');
      await connect.click();
      await until(async () => (await policy()).connectEnabled === true, 'API not updated');
      if ((await policy()).idleTimeoutMinutes !== 90) throw new Error('policy clobbered');
    });
    check('admin: no page errors', page.errors.length === 0, page.errors.join(' | '));
    await page.context().close();

    const member = await open(memberToken);
    await step('member: page is admin-only', async () => {
      await member.goto(`${baseUrl}/workspace/settings/${SECTION}`);
      await member.getByText('Only workspace admins can open this page').waitFor({ timeout: 20000 });
      if (await member.getByLabel('IP allowlist').count()) throw new Error('form shown to member');
    });
    await step('member: API refuses the read and the write', async () => {
      const r = await api(baseUrl, memberToken, Q);
      const w = await api(baseUrl, memberToken, SET, { i: { connectEnabled: false } });
      if (r.errors?.[0]?.extensions?.code !== 'FORBIDDEN' || w.errors?.[0]?.extensions?.code !== 'FORBIDDEN') throw new Error(JSON.stringify([r, w]));
    });
    await member.context().close();

    await restore();
    await step('no horizontal overflow at 320px', async () => {
      const p = await open(adminToken, { width: 320, height: 700 });
      await goto(p);
      const w = await p.evaluate(() => document.documentElement.scrollWidth);
      await p.context().close();
      if (w > 320) throw new Error(`scrollWidth ${w}`);
    });
    await step('screenshots light/dark at 1440 and 390', () => shots(browser, baseUrl, adminToken, screenshotsDir, SECTION));
  } finally {
    const r = await restore();
    if (r.errors) failed.push(`restore original policy: ${JSON.stringify(r.errors)}`);
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
