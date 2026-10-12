// Run from repo root. Lists every ancestor that clips (overflow other than visible) above a given selector at a viewport width.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/clip-chain.cjs <label> <width> [height]
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
const [label, widthArg, heightArg] = [process.argv[2] || 'My issues', Number(process.argv[3] || 390), Number(process.argv[4] || 800)];
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: widthArg, height: heightArg } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(900);
    if (widthArg < 901) { const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]'); if (await t.count()) { await t.click(); await page.waitForTimeout(300); } }
    await page.locator('button,a').filter({ hasText: new RegExp('^\\s*' + label + '\\s*$') }).first().click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(900);
    const chain = await page.evaluate(() => {
      const start = document.querySelector('.my-issues-view, .team-scoped-page--board, .inbox-page, .pulse-page, .projects-reference, .project-reference, .workspace-views-page, .issues-page, .board-page') || document.querySelector('main');
      const out = []; let e = start;
      for (let i = 0; i < 12 && e && e !== document.body; i++, e = e.parentElement) {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        out.push(`${e.tagName.toLowerCase()}.${String(e.className).trim().split(/\s+/).slice(0, 2).join('.')} h=${Math.round(r.height)} sh=${e.scrollHeight} oy=${s.overflowY} pos=${s.position} hgt=${s.height}`);
      }
      return out;
    });
    console.log(chain.join('\n'));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
