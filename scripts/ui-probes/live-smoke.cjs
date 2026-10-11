// Run from repo root. Unauthenticated smoke of a deployed site: loads the shell at phone and desktop widths, reports page errors and horizontal overflow.
// Usage: PROBE_URL=https://hoja.clover4.app node scripts/ui-probes/live-smoke.cjs
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'https://hoja.clover4.app';
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 800 } });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(url + '/', { waitUntil: 'networkidle', timeout: 30000 });
      const info = await page.evaluate(() => ({ title: document.title, overflowX: document.documentElement.scrollWidth - innerWidth, broken: [...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src')), bodyText: document.body.innerText.slice(0, 80).replace(/\s+/g, ' ') }));
      console.log(`${width}px: title="${info.title}" docOverflowX=${info.overflowX} brokenImages=${JSON.stringify(info.broken)} errors=${errors.length} text="${info.bodyText}"`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
