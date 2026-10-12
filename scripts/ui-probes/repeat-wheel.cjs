// Run from repo root. Repeats "open view, wheel over its list, read scrollTop" N times to separate flakiness from a real defect.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/repeat-wheel.cjs Backlog 500 700 5
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const [label, w, h, n] = [process.argv[2] || 'Backlog', Number(process.argv[3] || 500), Number(process.argv[4] || 700), Number(process.argv[5] || 5)];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const results = [];
    for (let i = 0; i < n; i++) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/');
      await page.waitForTimeout(900);
      await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(1000);
      const c = await page.evaluate(() => { const r = document.querySelector('.my-issues-list').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
      await page.mouse.move(c.x, c.y);
      await page.mouse.wheel(0, 800);
      await page.waitForTimeout(400);
      results.push(await page.evaluate(() => document.querySelector('.my-issues-list').scrollTop));
      await page.close();
    }
    console.log(label, w + 'x' + h, 'scrollTop per run:', results.join(', '));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
