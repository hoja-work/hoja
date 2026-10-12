// Run from repo root. Dumps every SVG in the sidebar with its computed stroke, fill, color and effective
// background, so icons that use stroke/fill (not color) are checked too.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/team-icons.cjs [width=1440] [height=900]
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const [w, h] = [Number(process.argv[2] || 1440), Number(process.argv[3] || 900)];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(1200);
    const rows = await page.evaluate(() => [...document.querySelectorAll('.sidebar svg')].map(s => {
      const cs = getComputedStyle(s); const r = s.getBoundingClientRect();
      const parent = s.parentElement; const pcs = getComputedStyle(parent);
      return { parent: (parent.className || parent.tagName).toString().slice(0, 40), text: (parent.textContent || '').trim().slice(0, 18), x: Math.round(r.left), y: Math.round(r.top), color: cs.color, stroke: cs.stroke, fill: cs.fill, parentBg: pcs.backgroundColor, parentColor: pcs.color };
    }));
    for (const r of rows) console.log(`${r.x},${r.y}\t${r.parent}\t${JSON.stringify(r.text)}\tcolor=${r.color}\tstroke=${r.stroke}\tfill=${r.fill}\tparentBg=${r.parentBg}`);
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
