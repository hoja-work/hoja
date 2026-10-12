// Run from repo root. At a viewport, opens a view and reports what element sits at the scroll owner's visible center,
// plus whether a wheel over that exact point moves the owner. Explains "wheel does nothing" cases.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/hit-at-list.cjs "Backlog" 500 700
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const [label, w, h] = [process.argv[2] || 'Backlog', Number(process.argv[3] || 500), Number(process.argv[4] || 700)];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(900);
    await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const info = await page.evaluate(() => {
      const list = document.querySelector('.my-issues-list');
      const r = list.getBoundingClientRect();
      const pts = [[r.left + r.width / 2, r.top + 30], [r.left + r.width / 2, r.top + r.height / 2], [r.left + r.width / 2, r.bottom - 20]];
      const hits = pts.map(([x, y]) => { const el = document.elementFromPoint(x, y); return el ? `${el.tagName.toLowerCase()}.${String(el.className).trim().split(/\s+/)[0] || ''}${el.closest('.my-issues-list') ? '(in-list)' : '(outside-list)'}` : 'null'; });
      const toolbar = document.querySelector('.my-issue-toolbar, .my-issue-tabs');
      return { listTop: Math.round(r.top), listBottom: Math.round(r.bottom), vh: innerHeight, hits, toolbarBottom: toolbar ? Math.round(toolbar.getBoundingClientRect().bottom) : null, scrollTop: list.scrollTop, range: list.scrollHeight - list.clientHeight };
    });
    console.log(JSON.stringify(info));
    const center = await page.evaluate(() => { const r = document.querySelector('.my-issues-list').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await page.mouse.move(center.x, center.y);
    await page.mouse.wheel(0, 500);
    await page.waitForTimeout(400);
    console.log('after wheel at list center:', await page.evaluate(() => document.querySelector('.my-issues-list').scrollTop));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
