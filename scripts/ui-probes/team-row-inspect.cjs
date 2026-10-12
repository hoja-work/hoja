// Run from repo root. Prints the rendered team row (heading, mark, name, chevron) with computed color, background,
// and the effective background of the nearest opaque ancestor. Usage: PROBE_TOKEN=... node scripts/ui-probes/team-row-inspect.cjs
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(1200);
    const rows = await page.evaluate(() => {
      const el = document.querySelector('.team-row'); if (!el) return 'no .team-row';
      const bgAt = e => { for (let x = e; x; x = x.parentElement) { const c = getComputedStyle(x).backgroundColor; if (c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; } return 'none'; };
      const list = [el, ...el.querySelectorAll('*')].slice(0, 12);
      return list.map(e => { const cs = getComputedStyle(e); const r = e.getBoundingClientRect(); return { tag: e.tagName.toLowerCase(), cls: (e.className && e.className.baseVal === undefined ? e.className : 'svg').toString().slice(0, 40), box: `${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`, text: [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('').slice(0, 14), color: cs.color, bg: cs.backgroundColor, effBg: bgAt(e) }; });
    });
    if (typeof rows === 'string') console.log(rows); else for (const r of rows) console.log(JSON.stringify(r));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
