// Run from repo root. Prints the board lane rectangle and whether its center is inside the viewport.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/board-rect.cjs 390
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const width = Number(process.argv[2] || 390);
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width, height: 800 }, hasTouch: width < 701, isMobile: width < 701 });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(900);
    if (width < 701) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.isVisible().catch(() => false)) { await t.click(); await page.waitForTimeout(300); } }
    await page.locator('button,a').filter({ hasText: /^\s*Board\s*$/ }).first().click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(900);
    const out = await page.evaluate(() => {
      const lanes = [...document.querySelectorAll('.kanban-cards')].filter(e => e.getBoundingClientRect().height > 0 && e.scrollHeight > e.clientHeight + 2);
      return lanes.slice(0, 3).map(x => { const r = x.getBoundingClientRect(); return { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), cx: Math.round(r.left + r.width / 2), cy: Math.round(r.top + r.height / 2), vw: innerWidth, vh: innerHeight, cls: x.parentElement.className }; });
    });
    console.log(JSON.stringify(out, null, 1));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
