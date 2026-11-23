// Shared helpers for the account-section journeys (profile, notifications, api-keys,
// agent-conversations). Not a journey itself: run-all.cjs only loads top-level *.cjs files.
const path = require('node:path');
const fs = require('node:fs');

function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_MODULE, 'playwright',
    path.join(__dirname, '../../../local-e2e/node_modules/playwright'),
    require('node:path').join(__dirname, '../../../local-e2e/node_modules/playwright')].filter(Boolean);
  for (const c of tries) { try { return require(c); } catch { /* next */ } }
  throw new Error('playwright not found; set PLAYWRIGHT_MODULE');
}

function harness() {
  const passed = [], failed = [];
  const check = (cond, msg) => { (cond ? passed : failed).push(msg); console.log(`  ${cond ? 'ok  ' : 'FAIL'} ${msg}`); };
  const step = async (name, fn) => {
    try { await fn(); } catch (e) { failed.push(`${name}: ${e.message.split('\n')[0]}`); console.log(`  FAIL ${name}: ${e.message.split('\n')[0]}`); }
  };
  return { passed, failed, check, step };
}

async function openPage(browser, baseUrl, token, { width = 1440, height = 900 } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  return page;
}

async function gotoSection(page, baseUrl, id) {
  await page.goto(`${baseUrl}/workspace/settings/${id}`);
  await page.waitForSelector('[data-settings-page]', { timeout: 20000 });
  await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(200);
}

/** GraphQL from inside the page, with the page's own token (reads back what the UI saved). */
async function api(page, query, variables) {
  return page.evaluate(async ([q, v]) => {
    const t = localStorage.getItem('hoja_token');
    const r = await fetch('/graphql', { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${t}` }, body: JSON.stringify({ query: q, variables: v }) });
    return r.json();
  }, [query, variables || {}]);
}

async function restCall(page, url, method = 'GET', json) {
  return page.evaluate(async ([u, m, j]) => {
    const t = localStorage.getItem('hoja_token');
    const r = await fetch(u, { method: m, headers: { 'content-type': 'application/json', authorization: `Bearer ${t}` }, body: j === undefined ? undefined : JSON.stringify(j) });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [url, method, json]);
}

/** Fail (500) the next GraphQL request whose body matches `pattern`, then stop intercepting. */
async function failNextGraphql(page, pattern) {
  let hit = false;
  const handler = async (route) => {
    const body = route.request().postData() || '';
    if (!hit && pattern.test(body)) {
      hit = true;
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ errors: [{ message: 'Simulated failure' }] }) });
    }
    return route.continue();
  };
  await page.route('**/graphql', handler);
  return async () => { await page.unroute('**/graphql', handler); return hit; };
}

async function screenshots(browser, baseUrl, token, id, dir) {
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  for (const [w, h] of [[1440, 900], [390, 844]]) for (const dark of [false, true]) {
    const page = await openPage(browser, baseUrl, token, { width: w, height: h });
    await gotoSection(page, baseUrl, id);
    if (dark) await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await page.waitForTimeout(150);
    await page.screenshot({ path: path.join(dir, `${id}-${w}${dark ? '-dark' : ''}.png`), fullPage: true });
    await page.context().close();
  }
}

async function noOverflow(browser, baseUrl, token, id, check) {
  const page = await openPage(browser, baseUrl, token, { width: 320, height: 700 });
  await gotoSection(page, baseUrl, id);
  const sw = await page.evaluate(() => document.documentElement.scrollWidth);
  check(sw <= 320, `no horizontal overflow at 320px (scrollWidth ${sw})`);
  await page.context().close();
}

function cli(run, id) {
  const baseUrl = process.env.SETTINGS_E2E_URL || 'http://127.0.0.1:5834';
  const adminToken = process.env.SETTINGS_E2E_ADMIN_TOKEN;
  const memberToken = process.env.SETTINGS_E2E_MEMBER_TOKEN;
  const screenshotsDir = process.env.SETTINGS_E2E_SHOTS || path.join(require('node:os').tmpdir(), 'settings-e2e', id);
  run({ baseUrl, adminToken, memberToken, screenshotsDir }).then((r) => {
    console.log(`\n${id}: ${r.passed.length} passed, ${r.failed.length} failed`);
    for (const f of r.failed) console.log('  FAIL ' + f);
    process.exit(r.failed.length ? 1 : 0);
  }, (e) => { console.error(e); process.exit(1); });
}

module.exports = { loadPlaywright, harness, openPage, gotoSection, api, restCall, failNextGraphql, screenshots, noOverflow, cli };
