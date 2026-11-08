// End-to-end browser checks for HOJA-80 (cycle settings) and HOJA-81 (remove member).
//
// Requires a running app and a browser — neither is available in the box this was
// written on, so treat it as unrun until someone executes it:
//
//   PROBE_URL=http://127.0.0.1:8797 PROBE_TOKEN=<token> PROBE_TEAM=HOJA \
//     node scripts/ui-probes/settings-e2e.cjs
//
// Exit code 0 means both journeys passed.
const path = require('node:path');
function loadPlaywright() {
  for (const c of [process.env.PLAYWRIGHT_MODULE, 'playwright',
                   path.join(__dirname, '../../local-e2e/node_modules/playwright')].filter(Boolean)) {
    try { return require(c) } catch { /* next */ }
  }
  throw new Error('Playwright not found; set PLAYWRIGHT_MODULE.');
}
const { chromium } = loadPlaywright();
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
const team = (process.env.PROBE_TEAM || 'HOJA').toLowerCase();
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }

const fail = (m) => { console.error('FAIL ' + m); process.exitCode = 1; };
const ok = (m) => console.log('ok   ' + m);

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    page.on('dialog', d => d.accept());

    // 1. The cycle settings URL must render cycle settings, not workspace Settings.
    await page.goto(`${url}/workspace/team/${team}/cycles/settings`);
    await page.waitForTimeout(1200);
    const cyclesText = await page.evaluate(() => document.body.innerText);
    if (/cadence|cycle/i.test(cyclesText) && !/Preferences/.test(cyclesText)) ok('cycle settings route renders');
    else fail('cycle settings route fell through: ' + cyclesText.slice(0, 160).replace(/\s+/g, ' '));

    // 2. A member can be removed from Settings -> Members and the row disappears.
    await page.goto(`${url}/workspace/settings/members`);
    await page.waitForTimeout(1200);
    const rows = page.locator('.member-role');
    const before = await rows.count();
    if (!before) { fail('no member rows rendered'); }
    else {
      const name = await page.locator('.member-row, .member-role').first().innerText().catch(() => 'first member');
      const remove = page.getByRole('button', { name: '^Remove$' }).last();
      if (!(await remove.count())) fail('no Remove action in the members list');
      else {
        await remove.click();
        await page.waitForTimeout(1000);
        const after = await rows.count();
        if (after === before - 1) ok(`removed a member (${before} -> ${after})`);
        else fail(`row count unchanged after Remove (${before} -> ${after}) for ${name}`);
      }
    }
  } finally { await browser.close(); }
})().catch(e => { console.error('ERROR ' + e.message); process.exit(1); });
