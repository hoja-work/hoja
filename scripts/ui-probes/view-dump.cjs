// Run from repo root. Prints the visible text layout of the main content for a view at a width: each top-level block with its box and whether it is shown.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/view-dump.cjs "My issues" 390
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
const [label, widthArg] = [process.argv[2] || 'My issues', Number(process.argv[3] || 390)];
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: widthArg, height: 800 } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(900);
    if (widthArg < 901) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.count()) { await t.click(); await page.waitForTimeout(300); } }
    await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const dump = await page.evaluate(() => {
      const main = document.querySelector('main');
      const rows = [...main.querySelectorAll('*')].filter(e => e.children.length === 0 && e.getBoundingClientRect().height > 0).length;
      const items = [...main.querySelectorAll('.my-issue-row, .issue-row, .issue-card, .kanban-card')].length;
      return { mainH: Math.round(main.getBoundingClientRect().height), mainSH: main.scrollHeight, leafNodes: rows, issueItems: items, sample: [...main.querySelectorAll('h1,h2,h3,button,.my-issue-row')].slice(0, 14).map(e => `${e.tagName.toLowerCase()}:${(e.textContent || '').trim().slice(0, 30)}@${Math.round(e.getBoundingClientRect().top)}`) };
    });
    console.log(JSON.stringify(dump, null, 1));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
