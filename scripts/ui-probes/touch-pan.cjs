// Run from repo root. Simulates a finger pan (touch drag) over a view's list in a touch-enabled constrained viewport
// and reports the list scrollTop and page scrollY. Use when wheel input works but a touch/trackpad pan is suspected.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/touch-pan.cjs Backlog 500 700
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const [label, w, h] = [process.argv[2] || 'Backlog', Number(process.argv[3] || 500), Number(process.argv[4] || 700)];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(900);
    await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const c = await page.evaluate(() => { const r = document.querySelector('.my-issues-list').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height * 0.7 }; });
    const cdp = await ctx.newCDPSession(page);
    const touch = (type, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: c.x, y, id: 1 }] });
    await touch('touchStart', c.y);
    for (let i = 1; i <= 12; i++) { await touch('touchMove', c.y - i * 25); await page.waitForTimeout(16); }
    await touch('touchEnd', 0);
    await page.waitForTimeout(600);
    const after = await page.evaluate(() => ({ list: document.querySelector('.my-issues-list').scrollTop, page: scrollY, touchAction: getComputedStyle(document.querySelector('.my-issue-row')).touchAction }));
    console.log(label, w + 'x' + h, 'touch pan ->', JSON.stringify(after));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
