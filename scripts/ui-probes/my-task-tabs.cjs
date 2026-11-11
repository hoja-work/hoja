// Run from repo root. Dark-theme contrast of each My issues tab (Assigned, Created, Subscribed, Activity) when selected.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/my-issue-tabs.cjs
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/');
    await page.waitForTimeout(1500);
    const labels = await page.evaluate(() => [...document.querySelectorAll('.my-issue-tabs > button')].map(b => ({ text: b.textContent.trim(), selected: b.classList.contains('selected'), fg: getComputedStyle(b).color, bg: getComputedStyle(b).backgroundColor })));
    console.log(JSON.stringify(labels, null, 1));
    for (const t of ['Created', 'Subscribed', 'Activity']) {
      await page.locator('.my-issue-tabs > button', { hasText: new RegExp('^' + t + '$') }).first().click();
      await page.waitForTimeout(400);
      const r = await page.evaluate(() => { const b = document.querySelector('.my-issue-tabs > button.selected'); const c = getComputedStyle(b); return { text: b.textContent.trim(), fg: c.color, bg: c.backgroundColor }; });
      console.log(JSON.stringify(r));
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message.slice(0, 400)); process.exit(1); });
