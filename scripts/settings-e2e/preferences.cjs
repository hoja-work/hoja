// Preferences journey (HOJA-734): theme and text size save instantly, survive a reload, are read
// back from the API, and roll back with a visible error when the server refuses.
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

async function prefs(page) {
  return page.evaluate(async () => {
    const r = await fetch('/graphql', {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${localStorage.getItem('hoja_token')}` },
      body: JSON.stringify({ query: '{ myPreferences { theme fontSize } }' }),
    });
    return (await r.json()).data.myPreferences;
  });
}

// Click a control and wait for the preference mutation it triggers to answer, so a reload or an
// API read never races the save (a lingering "Saved" from the previous change is not proof).
async function choose(page, name) {
  const saved = page.waitForResponse((r) => (r.request().postData() || '').includes('userPreferenceSet'), { timeout: 10000 });
  await page.getByRole('radio', { name }).click();
  await saved.catch(() => {});
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = require(resolve('playwright'));
  const passed = [], failed = [];
  const check = (cond, msg) => (cond ? passed : failed).push(msg);
  const browser = await chromium.launch();
  try {
    for (const [who, token] of [['admin', adminToken], ['member', memberToken]]) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
      await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
      const page = await ctx.newPage();
      const url = `${baseUrl}/workspace/settings/preferences`;
      await page.goto(url);
      await page.getByRole('radiogroup', { name: 'Theme' }).waitFor();

      for (const theme of ['Dark', 'System', 'Light']) {
        await choose(page, theme);
        const api = await prefs(page);
        check(api.theme === theme.toLowerCase(), `${who}: theme ${theme} saved to API (${api.theme})`);
        const applied = await page.evaluate(() => document.documentElement.dataset.theme);
        check(theme === 'System' ? ['light', 'dark'].includes(applied) : applied === theme.toLowerCase(), `${who}: theme ${theme} applied to the page (${applied})`);
      }
      await choose(page, 'Large');
      await page.reload();
      await page.getByRole('radio', { name: 'Large' }).waitFor();
      const large = await page.getByRole('radio', { name: 'Large' }).getAttribute('aria-checked');
      check(large === 'true', `${who}: text size Large survives reload`);
      check((await prefs(page)).fontSize === '110', `${who}: text size read back from API`);

      // Failure: the server refuses → control snaps back, the row explains, Retry is offered.
      await page.route('**/graphql', async (route) => {
        const body = route.request().postData() || '';
        if (body.includes('userPreferenceSet')) return route.fulfill({ status: 502, contentType: 'text/html', body: '<html>Bad gateway</html>' });
        return route.continue();
      });
      await page.getByRole('radio', { name: 'Compact' }).click();
      const alert = page.getByRole('alert').filter({ hasText: /server/i });
      await alert.waitFor({ timeout: 8000 }).then(() => check(true, `${who}: failed save shows a readable error`), () => check(false, `${who}: no error shown on failed save`));
      check((await page.getByRole('radio', { name: 'Large' }).getAttribute('aria-checked')) === 'true', `${who}: control rolled back to Large`);
      check(await page.getByRole('button', { name: 'Retry' }).isVisible(), `${who}: Retry offered`);
      await page.unroute('**/graphql');
      const retried = page.waitForResponse((r) => (r.request().postData() || '').includes('userPreferenceSet'), { timeout: 10000 });
      await page.getByRole('button', { name: 'Retry' }).click();
      await retried.catch(() => {});
      check((await prefs(page)).fontSize === '90', `${who}: Retry saves the attempted value`);
      if (screenshotsDir) await page.screenshot({ path: path.join(screenshotsDir, `preferences-${who}.png`) });
      // restore defaults
      await choose(page, 'Default');
      await ctx.close();
    }
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
  }).then((r) => {
    r.passed.forEach((m) => console.log('ok   ' + m));
    r.failed.forEach((m) => console.log('FAIL ' + m));
    process.exit(r.failed.length ? 1 : 0);
  });
}
