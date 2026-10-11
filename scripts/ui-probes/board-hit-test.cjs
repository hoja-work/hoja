// Run from repo root. For the board lane at a width, reports what element is under the lane center and the lane's scroll-related styles.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/board-hit-test.cjs 390
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
      const x = [...document.querySelectorAll('.kanban-cards')].find(e => e.getBoundingClientRect().height > 0 && e.scrollHeight > e.clientHeight + 2);
      if (!x) return 'no lane';
      const r = x.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      const s = getComputedStyle(x);
      const chain = []; let e = hit; for (let i = 0; i < 5 && e; i++, e = e.parentElement) chain.push(`${e.tagName.toLowerCase()}.${String(e.className).trim().split(/\s+/)[0] || ''}`);
      return { hitChain: chain.join(' < '), inLane: x.contains(hit), overflowY: s.overflowY, overscroll: s.overscrollBehavior, overflowX: s.overflowX, laneTop: Math.round(r.top), vh: innerHeight, pageScrollY: scrollY, pe: s.pointerEvents };
    });
    console.log(JSON.stringify(out, null, 1));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
