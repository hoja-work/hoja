#!/usr/bin/env node
// Settings → Connectors end-to-end journey (HOJA-734, HOJA-680).
//
//   node scripts/settings-e2e/connections.cjs            (uses the HOJA-734 stack defaults)
//
// What is real and what is simulated:
//  - Real backend: the page against /api/connectors/{config,accounts,actions}, plain-language
//    unavailability (no hosts or env names leak), the pending-action Reject round trip (an
//    action row is seeded into the stack's SQLite DB, rejected in the UI, read back from the API),
//    the Grafana connect failure path, and the OAuth start routes the Connect links point at.
//  - Simulated with page.route: a deployment where providers are configured and accounts exist
//    (the local stack has no provider credentials and no HOJA_SECRET_KEY, so nothing can really be
//    connected or revoked). Third-party OAuth consent cannot be completed locally at all.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

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
  screenshotsDir: process.env.SETTINGS_E2E_SHOTS || require('node:path').join(require('node:os').tmpdir(), 'settings-e2e', 'connections'),
};
const DB = process.env.SETTINGS_E2E_DB;
const SECTION = 'connections';
const PROVIDERS = ['google_workspace', 'github', 'slack', 'notion', 'zoom', 'calendly', 'sentry', 'grafana'];
const START = {
  google_workspace: '/api/connectors/google/start?scope=user', github: '/api/connectors/github/start',
  slack: '/api/connectors/slack/start', notion: '/api/connectors/notion/start', zoom: '/api/connectors/zoom/start',
  calendly: '/api/connectors/calendly/start', sentry: '/api/connectors/sentry/start',
};
const NAMES = { google_workspace: 'Google Workspace', github: 'GitHub', slack: 'Slack', notion: 'Notion', zoom: 'Zoom', calendly: 'Calendly', sentry: 'Sentry', grafana: 'Grafana' };

const CONFIGURED = {
  googleWorkspace: true, googleWorkspaceAgent: true, notion: true, notionAgent: true, slack: true, slackAgent: true,
  calendly: true, calendlyAgent: true, sentry: true, sentryAgent: true, zoom: true, zoomAgent: true, github: true,
  githubAgent: true, githubInstallUrl: 'https://github.com/apps/hoja-test/installations/new', grafana: true, grafanaAgent: true,
  host: 'acme.example', availableOnThisHost: Object.fromEntries(PROVIDERS.map((p) => [p, true])),
  unavailableReason: Object.fromEntries(PROVIDERS.map((p) => [p, null])),
};
const acct = (id, provider, externalAccount, extra = {}) => ({
  id, provider, externalAccount, scope: 'user', status: 'active', scopes: [], ...extra,
});

async function open(browser, baseUrl, token, viewport = { width: 1440, height: 900 }) {
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
      await page.getByRole('heading', { level: 1, name: 'Connectors' }).waitFor({ timeout: 15000 });
      await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 });
      return;
    } catch (e) {
      if (i >= 2) throw e;
    }
  }
}
async function api(baseUrl, token, p, init = {}) {
  const r = await fetch(baseUrl + p, { ...init, headers: { authorization: `Bearer ${token}`, ...(init.headers || {}) }, redirect: 'manual' });
  let body = null;
  try { body = await r.json(); } catch { /* not json */ }
  return { status: r.status, body, location: r.headers.get('location') };
}
const row = (page, name) => page.getByRole('group', { name, exact: true });

/** Simulate a configured deployment with accounts. `state.accounts` is mutable so a revoke sticks. */
async function simulate(page, state) {
  await page.route('**/api/connectors/config', (r) => r.fulfill({ json: CONFIGURED }));
  await page.route('**/api/connectors/accounts', (r) => r.fulfill({ json: { accounts: state.accounts } }));
  await page.route('**/api/connectors/accounts/*/revoke', (r) => {
    if (state.revokeFails) return r.fulfill({ status: 502, json: { error: 'Notion did not confirm revocation: timeout' } });
    const id = r.request().url().split('/accounts/')[1].split('/')[0];
    const a = state.accounts.find((x) => x.id === id);
    state.accounts = state.accounts.filter((x) => x.id !== id);
    const hint = a && a.provider === 'notion' ? 'Remove Hoja in Notion Settings > Connections to invalidate the token there.' : null;
    return r.fulfill({ json: { revoked: true, providerRevoked: !hint, hint } });
  });
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir } = DEFAULTS) {
  const passed = [], failed = [];
  const check = (cond, msg) => (cond ? passed : failed).push(msg);
  const attempt = async (name, fn) => { try { await fn(); } catch (e) { failed.push(`${name}: ${e.message.split('\n')[0]}`); } };
  fs.mkdirSync(screenshotsDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    // ── 1. Real backend, admin: plain language, nothing internal leaks.
    await attempt('real config', async () => {
      const page = await open(browser, baseUrl, adminToken);
      await goto(page, baseUrl);
      const cfg = (await api(baseUrl, adminToken, '/api/connectors/config')).body;
      const text = await page.locator('[data-settings-page]').innerText();
      check(!/127\.0\.0\.1|localhost|HOJA_|workspace host|deployment serves|callback/i.test(text), 'no hosts, env names or callback jargon on the page');
      if (cfg && cfg.host) check(!text.includes(cfg.host), `the request host (${cfg.host}) is not printed`);
      const anyAvailable = PROVIDERS.some((p) => cfg[{ google_workspace: 'googleWorkspace' }[p] || p] !== false && cfg.availableOnThisHost[p] !== false);
      if (!anyAvailable) {
        check(await page.getByText("Connectors aren't available on this server").isVisible(), 'all-unavailable server shows one plain notice');
        check(await page.getByText('Whoever runs the server').isVisible(), 'admin is told what to do');
        for (const p of PROVIDERS) {
          const r = row(page, NAMES[p]);
          check((await r.getByText(/Not set up on this server|Not available at this address/).count()) === 1, `${NAMES[p]}: plain unavailable state`);
          check((await r.getByRole('link', { name: `Connect ${NAMES[p]}` }).count()) === 0, `${NAMES[p]}: no Connect that would fail`);
        }
      }
      check((await page.getByRole('button', { name: /approve/i }).count()) === 0, 'no Approve control (server has no write executor)');
      check(page.errors.length === 0, `no page errors (${page.errors.join(' | ')})`);
      await page.context().close();
    });

    // ── 2. Real backend: OAuth start routes exist and answer (cannot complete consent locally).
    await attempt('oauth start routes', async () => {
      for (const [p, href] of Object.entries(START)) {
        const r = await api(baseUrl, adminToken, href);
        // 302 → provider consent on a configured deployment; 503/409 here (no credentials / host).
        check([302, 409, 503].includes(r.status) && (r.status !== 302 || /^https:\/\//.test(r.location || '')),
          `${NAMES[p]} start route answers ${r.status}${r.location ? ' → ' + new URL(r.location).host : ''}`);
      }
    });

    // ── 3. Real backend: pending request → Reject round trip (seeded row), with a forced failure first.
    await attempt('reject pending action', async () => {
      if (!fs.existsSync(DB)) { passed.push('SKIP reject round trip: no stack DB'); return; }
      const viewer = await (await fetch(baseUrl + '/graphql', { method: 'POST', headers: { authorization: `Bearer ${adminToken}`, 'content-type': 'application/json' }, body: JSON.stringify({ query: '{ viewer { id } }' }) })).json();
      const uid = viewer.data.viewer.id;
      const tag = Date.now().toString(36);
      execFileSync('python3', ['-c', `
import sqlite3, sys, json, datetime
db, uid, tag = sys.argv[1:4]
c = sqlite3.connect(db)
org = c.execute('select organization_id from user where id=?', (uid,)).fetchone()[0]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute("insert into connector_account (id, organization_id, owner_user_id, provider, external_account, scopes, encrypted_refresh_token, status, created_at, updated_at, revoked_at) values (?,?,?,?,?,?,?,?,?,?,?)",
  ('e2e-acct-'+tag, org, uid, 'google_workspace', 'e2e@acme.test', '[]', '', 'revoked', now, now, now))
c.execute("insert into connector_action (id, organization_id, account_id, requested_by_user_id, tool, args_json, status, created_at) values (?,?,?,?,?,?,?,?)",
  ('cact_e2e_'+tag, org, 'e2e-acct-'+tag, uid, 'gmail_send', json.dumps({'to':'ops@acme.test','subject':'E2E '+tag,'body':'hi'}), 'pending', now))
c.commit()
`, DB, uid, tag]);
      const page = await open(browser, baseUrl, adminToken);
      await goto(page, baseUrl);
      check(await page.getByRole('heading', { name: 'Waiting for your approval' }).isVisible(), 'pending request listed');
      check(await page.getByText(`subject: E2E ${tag}`).isVisible(), 'request shows its arguments in plain text');
      check((await page.getByRole('button', { name: /approve/i }).count()) === 0, 'Approve is hidden (would always 409)');
      // forced failure: dialog stays open with the reason, request stays
      await page.route('**/api/connectors/actions/*/reject', (r) => r.abort());
      await page.getByRole('button', { name: 'Actions for Send an email' }).last().click();
      await page.getByRole('menuitem', { name: 'Reject request' }).click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Reject' }).click();
      await page.getByRole('alertdialog').getByRole('alert').waitFor();
      check(await page.getByRole('alertdialog').isVisible(), 'failed reject keeps the dialog open with the reason');
      await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
      await page.unroute('**/api/connectors/actions/*/reject');
      // real reject
      await page.getByRole('button', { name: 'Actions for Send an email' }).last().click();
      await page.getByRole('menuitem', { name: 'Reject request' }).click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Reject' }).click();
      await page.getByRole('alertdialog').waitFor({ state: 'hidden' });
      await goto(page, baseUrl);
      check((await page.getByText(`subject: E2E ${tag}`).count()) === 0, 'rejected request gone after reload');
      const left = (await api(baseUrl, adminToken, '/api/connectors/actions')).body.actions;
      check(!left.some((a) => a.id === `cact_e2e_${tag}`), 'API no longer lists the rejected request');
      await page.context().close();
    });

    // ── 4. Real backend: Grafana connect fails readably (not configured here), form keeps values.
    await attempt('grafana real failure', async () => {
      const page = await open(browser, baseUrl, adminToken);
      await page.route('**/api/connectors/config', async (r) => {
        const res = await r.fetch(); const j = await res.json();
        j.grafana = true; // pretend the sealing key exists so the form opens; the POST stays real
        r.fulfill({ json: j });
      });
      await goto(page, baseUrl);
      await row(page, 'Grafana').getByRole('button', { name: 'Connect Grafana' }).click();
      const dlg = page.getByRole('dialog', { name: 'Connect Grafana' });
      await dlg.getByLabel('Instance address').fill('https://example.grafana.net');
      await dlg.getByLabel('Service account token').fill('glsa_e2e_not_a_real_token_000000000000');
      await dlg.getByRole('button', { name: 'Connect', exact: true }).click();
      await dlg.getByRole('alert').first().waitFor();
      const msg = await dlg.getByRole('alert').first().innerText();
      check(await dlg.isVisible() && !/\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b/.test(msg), `real Grafana connect error shown in plain words: "${msg.slice(0, 90)}"`);
      check((await dlg.getByLabel('Instance address').inputValue()) === 'https://example.grafana.net', 'form keeps its values on failure');
      const accounts = (await api(baseUrl, adminToken, '/api/connectors/accounts')).body.accounts;
      check(!accounts.some((a) => a.provider === 'grafana'), 'API confirms nothing was stored');
      await page.context().close();
    });

    // ── 5. Simulated configured deployment, admin: gallery states, Connect links, Manage, Disconnect.
    await attempt('simulated gallery', async () => {
      const state = {
        accounts: [
          acct('g1', 'google_workspace', 'ada@acme.test', { capabilities: { meet: true, chat: false } }),
          acct('n1', 'notion', 'Acme Wiki'),
          acct('org1', 'google_workspace', 'shared@acme.test', { scope: 'org' }),
        ],
      };
      const page = await open(browser, baseUrl, adminToken);
      await simulate(page, state);
      await goto(page, baseUrl);
      check((await row(page, 'Google Workspace').getByText('Connected as ada@acme.test').count()) === 1, 'Google: Connected as <account>');
      check((await row(page, 'Slack').getByText('Not connected').count()) === 1, 'Slack: Not connected');
      for (const [p, href] of Object.entries(START)) {
        if (p === 'google_workspace' || p === 'notion') continue;
        const link = row(page, NAMES[p]).getByRole('link', { name: `Connect ${NAMES[p]}` });
        check((await link.getAttribute('href')) === href, `${NAMES[p]}: Connect goes to ${href}`);
      }
      const org = row(page, 'Shared Google account');
      check((await org.getByText('Connected as shared@acme.test').count()) === 1, 'workspace group shows the shared Google account');
      await page.screenshot({ path: path.join(screenshotsDir, 'simulated-connected-1440.png'), fullPage: true });

      // Google manage: Meet granted, Chat offered as an add-on link
      await row(page, 'Google Workspace').getByRole('button', { name: 'Manage Google Workspace' }).click();
      const gd = page.getByRole('dialog', { name: 'Google Workspace' });
      check((await gd.getByRole('link', { name: 'Add Chat access' }).getAttribute('href')) === '/api/connectors/google/start?addon=chat', 'Google: Add Chat access link');
      check((await gd.getByText('Granted').count()) === 1, 'Google: Meet shown as granted');
      check((await gd.getByText('shared@acme.test').count()) === 0, 'personal Google dialog does not list the shared account');
      await page.screenshot({ path: path.join(screenshotsDir, 'simulated-manage-google-1440.png') });
      await gd.getByRole('button', { name: 'Done' }).click();

      // Notion disconnect: confirm names the manual step; failure keeps the dialog; success keeps the hint
      await row(page, 'Notion').getByRole('button', { name: 'Manage Notion' }).click();
      const nd = page.getByRole('dialog', { name: 'Notion' });
      await nd.getByRole('button', { name: 'Disconnect Acme Wiki' }).click();
      const confirm = page.getByRole('alertdialog');
      check(/Notion Settings > Connections/.test(await confirm.innerText()), 'Disconnect confirm names the manual revocation step');
      state.revokeFails = true;
      await confirm.getByRole('button', { name: 'Disconnect' }).click();
      await confirm.getByRole('alert').waitFor();
      check(/did not confirm/.test(await confirm.getByRole('alert').innerText()), 'failed disconnect shows the reason, dialog stays');
      check(state.accounts.some((a) => a.id === 'n1'), 'failed disconnect leaves the account');
      state.revokeFails = false;
      await confirm.getByRole('button', { name: 'Disconnect' }).click();
      await confirm.waitFor({ state: 'hidden' });
      await page.getByText('Acme Wiki is disconnected from Hoja').waitFor();
      check(await page.getByText('Remove Hoja in Notion Settings > Connections').isVisible(), 'manual-revocation hint stays on the page after disconnect');
      await row(page, 'Notion').getByText('Not connected').waitFor();
      check(true, 'Notion row returns to Not connected');
      await page.screenshot({ path: path.join(screenshotsDir, 'simulated-after-disconnect-1440.png') });

      // Grafana: test + connect (simulated success)
      await page.route('**/api/connectors/grafana/validate', (r) => r.fulfill({ json: { account: 'svc-viewer', instance: 'https://acme.grafana.net' } }));
      await page.route('**/api/connectors/grafana/connect', (r) => {
        state.accounts.push(acct('gr1', 'grafana', 'svc-viewer', { externalUrl: 'https://acme.grafana.net' }));
        r.fulfill({ json: { account: 'svc-viewer', instance: 'https://acme.grafana.net' } });
      });
      await row(page, 'Grafana').getByRole('button', { name: 'Connect Grafana' }).click();
      const gfd = page.getByRole('dialog', { name: 'Connect Grafana' });
      await gfd.getByLabel('Instance address').fill('acme.grafana.net');
      await gfd.getByLabel('Service account token').fill('glsa_x');
      await gfd.getByRole('button', { name: 'Test connection' }).click();
      check(await gfd.getByText('Enter the full address').isVisible(), 'Grafana validates the address before sending');
      await gfd.getByLabel('Instance address').fill('https://acme.grafana.net');
      await gfd.getByRole('button', { name: 'Test connection' }).click();
      await gfd.getByText('Reached https://acme.grafana.net as svc-viewer.').waitFor();
      check(true, 'Grafana Test connection reports the result');
      await page.screenshot({ path: path.join(screenshotsDir, 'simulated-grafana-1440.png') });
      await gfd.getByRole('button', { name: 'Connect', exact: true }).click();
      await gfd.waitFor({ state: 'hidden' });
      await row(page, 'Grafana').getByText('Connected as svc-viewer').waitFor();
      check(true, 'Grafana row shows Connected after connect');
      check(page.errors.length === 0, `no page errors (${page.errors.join(' | ')})`);
      await page.context().close();
    });

    // ── 6. Member: shared account is view-only, org connect is locked, real page is plain.
    await attempt('member', async () => {
      const page = await open(browser, baseUrl, memberToken);
      await goto(page, baseUrl);
      check(await page.getByText('A workspace admin needs to set connectors up').isVisible(), 'member (real): told to ask an admin');
      const org = row(page, 'Shared Google account');
      check(await org.getByText('Only workspace admins can connect or disconnect this.').isVisible(), 'member: shared account row is locked with a reason');
      await page.context().close();

      const page2 = await open(browser, baseUrl, memberToken);
      const state = { accounts: [acct('org1', 'google_workspace', 'shared@acme.test', { scope: 'org' })] };
      await simulate(page2, state);
      await goto(page2, baseUrl);
      const org2 = row(page2, 'Shared Google account');
      check((await org2.getByRole('link', { name: 'Connect shared Google account' }).count()) === 0, 'member: no Connect for the shared account');
      await org2.getByRole('button', { name: 'View shared Google account' }).click();
      const d = page2.getByRole('dialog', { name: 'Shared Google account' });
      check((await d.getByRole('button', { name: /Disconnect/ }).count()) === 0, 'member: no Disconnect on the shared account (backend would 404)');
      check(await d.getByText('Admins only').isVisible(), 'member: dialog says admins only');
      await page2.context().close();

      const admin = await open(browser, baseUrl, adminToken);
      await simulate(admin, { accounts: [acct('org1', 'google_workspace', 'shared@acme.test', { scope: 'org' })] });
      await goto(admin, baseUrl);
      await row(admin, 'Shared Google account').getByRole('button', { name: 'Manage shared Google account' }).click();
      check((await admin.getByRole('dialog', { name: 'Shared Google account' }).getByRole('button', { name: 'Disconnect shared@acme.test' }).count()) === 1, 'admin: Disconnect offered on the shared account');
      await admin.context().close();
    });

    // ── 7. Screenshots (real backend) and overflow.
    await attempt('screenshots', async () => {
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        for (const dark of [false, true]) {
          const page = await open(browser, baseUrl, adminToken, { width: w, height: h });
          await goto(page, baseUrl);
          if (dark) await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
          await page.waitForTimeout(150);
          await page.screenshot({ path: path.join(screenshotsDir, `${SECTION}-${w}${dark ? '-dark' : ''}.png`), fullPage: true });
          await page.context().close();
        }
      }
      const sim = await open(browser, baseUrl, adminToken, { width: 390, height: 844 });
      await simulate(sim, { accounts: [acct('g1', 'google_workspace', 'ada.lovelace.long.address@acme-industries.test'), acct('org1', 'google_workspace', 'shared@acme.test', { scope: 'org' })] });
      await goto(sim, baseUrl);
      await sim.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
      await sim.screenshot({ path: path.join(screenshotsDir, 'simulated-390-dark.png'), fullPage: true });
      await sim.context().close();
      for (const sim320 of [false, true]) {
        const page = await open(browser, baseUrl, adminToken, { width: 320, height: 800 });
        if (sim320) await simulate(page, { accounts: [acct('g1', 'google_workspace', 'ada.lovelace.long.address@acme-industries.test')] });
        await goto(page, baseUrl);
        const sw = await page.evaluate(() => document.documentElement.scrollWidth);
        check(sw <= 320, `no horizontal overflow at 320px${sim320 ? ' (connected)' : ''} (scrollWidth ${sw})`);
        await page.context().close();
      }
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
