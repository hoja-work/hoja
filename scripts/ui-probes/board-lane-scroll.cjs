// Run from repo root. Reproduces board lane scrolling on phone: wheel over a lane and report its scrollTop, plus the touch-action in effect.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/board-lane-scroll.cjs [widths...]
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const widths = process.argv.slice(2).map(Number).filter(Boolean);
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of (widths.length ? widths : [390, 1440])) {
      const page = await browser.newPage({ viewport: { width, height: 800 }, hasTouch: width < 701, isMobile: width < 701 });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/');
      await page.waitForTimeout(900);
      if (width < 701) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.isVisible().catch(() => false)) { await t.click(); await page.waitForTimeout(300); } }
      await page.locator('button,a').filter({ hasText: /^\s*Board\s*$/ }).first().click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(900);
      const lane = await page.evaluate(() => {
        const lanes = [...document.querySelectorAll('.kanban-cards')].filter(x => { const r = x.getBoundingClientRect(); return r.height > 0 && r.left < innerWidth && r.right > 0 && x.scrollHeight > x.clientHeight + 2; });
        if (!lanes.length) return null;
        const x = lanes[0]; const raw = x.getBoundingClientRect(); const r = { left: Math.max(raw.left, 0), right: Math.min(raw.right, innerWidth), top: Math.max(raw.top, 0), bottom: Math.min(raw.bottom, innerHeight), width: Math.min(raw.right, innerWidth) - Math.max(raw.left, 0), height: Math.min(raw.bottom, innerHeight) - Math.max(raw.top, 0) }; x.setAttribute('data-probe', '1');
        const card = x.querySelector('.kanban-card');
        return { x: r.left + r.width / 2, y: r.top + r.height / 2, top: x.scrollTop, max: x.scrollHeight - x.clientHeight, laneH: Math.round(r.height), touch: card ? getComputedStyle(card).touchAction : 'n/a', cardW: card ? Math.round(card.getBoundingClientRect().width) : 0, pageScroll: document.documentElement.scrollHeight };
      });
      if (!lane) { console.log(`${width}px: no lane with overflow found`); await page.close(); continue; }
      await page.mouse.move(lane.x, lane.y);
      await page.mouse.wheel(0, 400);
      await page.waitForTimeout(400);
      const after = await page.evaluate(() => document.querySelector('[data-probe="1"]').scrollTop);
      const pointer = await page.evaluate(() => matchMedia('(pointer:coarse)').matches);
      console.log(`${width}px: coarse=${pointer} lane h=${lane.laneH} card=${lane.cardW}px touch-action=${lane.touch} scrollTop ${lane.top} -> ${after} (max ${lane.max}) ${after > lane.top ? 'OK' : 'STUCK'}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
