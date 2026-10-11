// Run from repo root with a backend on $PROBE_URL (default http://127.0.0.1:8797) and PROBE_TOKEN set.
// Reproduces "cannot scroll in list views": for each list view at phone and desktop widths, finds the
// deepest scrollable container with overflow and dispatches a real wheel event over it.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const views = [['My issues', 'My issues'], ['Issues', 'Issues'], ['Inbox', 'Inbox'], ['Pulse', 'Pulse'], ['Board', 'Board']];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, label] of views) for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 800 } });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/');
      await page.waitForTimeout(900);
      if (width < 901) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.count()) { await t.click(); await page.waitForTimeout(300); } }
      await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(900);
      const target = await page.evaluate(() => {
        const els = [...document.querySelectorAll('main *')].filter(x => { const s = getComputedStyle(x); const r = x.getBoundingClientRect(); return (s.overflowY === 'auto' || s.overflowY === 'scroll') && x.scrollHeight > x.clientHeight + 2 && r.height > 0; });
        if (!els.length) return null;
        const x = els[0]; const r = x.getBoundingClientRect();
        x.setAttribute('data-probe', '1');
        return { x: r.left + r.width / 2, y: r.top + r.height / 2, top: x.scrollTop, max: x.scrollHeight - x.clientHeight, cls: String(x.className).slice(0, 40) };
      });
      let result = 'no inner scroller (page-level only)';
      if (target) {
        await page.mouse.move(target.x, target.y);
        await page.mouse.wheel(0, 400);
        await page.waitForTimeout(400);
        const after = await page.evaluate(() => document.querySelector('[data-probe="1"]')?.scrollTop ?? -1);
        result = `${target.cls} scrollTop ${target.top} -> ${after} (max ${target.max}) ${after > target.top ? 'OK' : 'STUCK'}`;
      }
      console.log(`${name} ${width}px: ${result}`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e.message); process.exit(1); });
