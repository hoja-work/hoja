// Run from repo root. For My issues at a width, prints each ancestor of the first issue row: tag, class, box height, scroll height, overflow.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/list-owner.cjs 1440
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const width = Number(process.argv[2] || 1440);
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(900);
    if (width < 701) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.isVisible().catch(() => false)) { await t.click(); await page.waitForTimeout(300); } }
    await page.locator('button,a').filter({ hasText: /^\s*My issues\s*$/ }).first().click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const chain = await page.evaluate(() => {
      const row = document.querySelector('.my-issues-view .my-issue-row');
      if (!row) return ['no row rendered at this width'];
      const out = []; let e = row;
      for (let i = 0; i < 9 && e && e !== document.body; i++, e = e.parentElement) {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        out.push(`${e.tagName.toLowerCase()}.${String(e.className).trim().split(/\s+/).slice(0, 2).join('.')} h=${Math.round(r.height)} sh=${e.scrollHeight} oy=${s.overflowY} disp=${s.display} minH=${s.minHeight}`);
      }
      return out;
    });
    console.log(chain.join('\n'));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
