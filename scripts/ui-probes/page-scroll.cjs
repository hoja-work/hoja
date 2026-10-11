// Run from repo root. Confirms whether the page itself scrolls (wheel over the main area) for each view at phone and desktop widths.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const label of ['My issues', 'Issues', 'Inbox', 'Pulse', 'Board']) for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 800 } });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/');
      await page.waitForTimeout(900);
      if (width < 901) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.count()) { await t.click(); await page.waitForTimeout(300); } }
      await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(900);
      const info = await page.evaluate(() => ({ docH: document.documentElement.scrollHeight, winH: innerHeight, mainCls: document.querySelector('main')?.className, mainOy: getComputedStyle(document.querySelector('main')).overflowY, mainH: Math.round(document.querySelector('main').getBoundingClientRect().height), appBodyOy: getComputedStyle(document.querySelector('.app-body') || document.body).overflowY, appBodyH: Math.round((document.querySelector('.app-body') || document.body).getBoundingClientRect().height) }));
      await page.mouse.move(width / 2, 400);
      await page.mouse.wheel(0, 500);
      await page.waitForTimeout(400);
      const after = await page.evaluate(() => ({ winY: Math.round(scrollY), mainTop: document.querySelector('main')?.scrollTop ?? -1, bodyTop: document.scrollingElement.scrollTop }));
      console.log(`${label} ${width}px: doc ${info.docH}/${info.winH}, main ${info.mainH}px oy=${info.mainOy}, app-body ${info.appBodyH}px oy=${info.appBodyOy} | after wheel winY=${after.winY} mainTop=${after.mainTop}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
