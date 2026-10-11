// Run from repo root. Reproduces "cannot scroll the issue list once it is long or a dropdown is open".
// For My issues (populated, assigned) at phone and desktop widths: measure the scroll range of the page and every
// scroll container, wheel over the list, then open the first row's status dropdown and wheel again.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/list-dropdown-scroll.cjs
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const measure = () => page => page.evaluate(() => {
  const rows = document.querySelectorAll('.my-issues-view .my-issue-row').length;
  const card = document.querySelector('.my-issues-list .issue-card, .my-issues-view .issue-card');
  const cardR = card?.getBoundingClientRect();
  const scrollers = [...document.querySelectorAll('*')].filter(x => { const s = getComputedStyle(x); return (s.overflowY === 'auto' || s.overflowY === 'scroll') && x.scrollHeight > x.clientHeight + 2 && x.getBoundingClientRect().height > 0; }).map(x => ({ c: String(x.className).slice(0, 30), top: x.scrollTop, range: x.scrollHeight - x.clientHeight }));
  return { rows, cardH: cardR ? Math.round(cardR.height) : null, cardSH: card?.scrollHeight, cardOY: card ? getComputedStyle(card).overflowY : null, docRange: document.documentElement.scrollHeight - innerHeight, scrollers };
});
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 800 }, hasTouch: width < 701, isMobile: width < 701 });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/');
      await page.waitForTimeout(900);
      if (width < 701) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.isVisible().catch(() => false)) { await t.click(); await page.waitForTimeout(300); } }
      await page.locator('button,a').filter({ hasText: /^\s*My issues\s*$/ }).first().click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(1000);
      const m = measure();
      console.log(`${width}px list:`, JSON.stringify(await m(page)));
      const row = page.locator('.my-issues-view .my-issue-row').first();
      await row.scrollIntoViewIfNeeded().catch(() => {});
      const box = await row.boundingBox().catch(() => null);
      if (box) { await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.wheel(0, 600); await page.waitForTimeout(400); }
      console.log(`${width}px after wheel:`, JSON.stringify(await m(page)));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
