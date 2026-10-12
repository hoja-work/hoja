// Run from repo root. Lists every CSS rule matching an element (by selector) with its declared display, so the winning rule can be found.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/which-rule.cjs 1440 900 ".topbar"
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const [w, h, target] = [Number(process.argv[2] || 1440), Number(process.argv[3] || 900), process.argv[4] || '.topbar'];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(1200);
    const rules = await page.evaluate(sel => {
      const el = document.querySelector(sel);
      const hits = [];
      const walk = list => { for (const r of list) { if (r.cssRules) { const media = r.media ? r.media.mediaText : ''; if (r.cssRules && !r.selectorText) { walk(r.cssRules); } else if (r.selectorText) { if (media) {} try { if (el.matches(r.selectorText)) hits.push({ sel: r.selectorText.slice(0, 120), decl: r.style.display || '', h: r.style.height || '' }); } catch {} } } } };
      for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch {} }
      return hits;
    }, target);
    const displays = rules.filter(r => r.decl);
    console.log(`${target} matching rules with display: ${displays.length}`);
    for (const r of displays) console.log(`  display:${r.decl}  <- ${r.sel}`);
    console.log('computed display:', await page.evaluate(sel => getComputedStyle(document.querySelector(sel)).display, target));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
