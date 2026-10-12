// Run from repo root. Reproduces "cannot scroll down" in a constrained split-screen window.
// For each view (Backlog, Todo, My issues, Issues) at a given viewport: open the view, find the
// scroll owner, wheel over the list, and report whether it moved and whether the last row is reachable.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/constrained-scroll.cjs [width] [height]
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const width = Number(process.argv[2] || 500), height = Number(process.argv[3] || 700);
const views = [['Backlog', /^\s*Backlog\s*$/], ['Todo', /^\s*Todo\s*$/], ['My issues', /^\s*My issues\s*$/], ['Issues', /^\s*Issues\s*$/]];
const measure = page => page.evaluate(() => {
  const scrollers = [...document.querySelectorAll('*')].filter(x => { const s = getComputedStyle(x); return (s.overflowY === 'auto' || s.overflowY === 'scroll') && x.scrollHeight > x.clientHeight + 2 && x.getBoundingClientRect().height > 0 && !x.closest('.sidebar'); }).map(x => ({ c: String(x.className).slice(0, 28), top: Math.round(x.scrollTop), range: x.scrollHeight - x.clientHeight, h: Math.round(x.getBoundingClientRect().height) }));
  const rows = document.querySelectorAll('.my-issue-row, .issue-row, .kanban-card').length;
  return { rows, docRange: document.documentElement.scrollHeight - innerHeight, scrollers };
});
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, rx] of views) {
      const page = await browser.newPage({ viewport: { width, height }, hasTouch: false });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/');
      await page.waitForTimeout(900);
      if (width < 701) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.isVisible().catch(() => false)) { await t.click(); await page.waitForTimeout(300); } }
      await page.locator('button,a').filter({ hasText: rx }).first().click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(1000);
      const before = await measure(page);
      const target = await page.evaluate(() => {
        const x = document.querySelector('.my-issues-list') || document.querySelector('main');
        const r = x.getBoundingClientRect();
        return { x: Math.min(r.left + r.width / 2, innerWidth - 2), y: Math.min(r.top + r.height / 2, innerHeight - 2) };
      });
      await page.mouse.move(target.x, target.y);
      await page.mouse.wheel(0, 800);
      await page.waitForTimeout(400);
      const after = await measure(page);
      const listTop = await page.evaluate(() => document.querySelector('.my-issues-list')?.scrollTop ?? -1);
      const moved = listTop > 0 || after.scrollers.some((s, i) => (before.scrollers[i]?.top ?? 0) !== s.top);
      console.log(`${name} ${width}x${height}: rows=${before.rows} doc=${before.docRange} before=${JSON.stringify(before.scrollers)} after=${JSON.stringify(after.scrollers)} listTop=${listTop} => ${moved ? 'SCROLLS' : 'STUCK'}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
