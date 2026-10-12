// Run from repo root. Prints the rendered box of every top-level child of the .topbar and the computed display/size of the
// topbar, its parent grid, and the main shell rows, so layout sizing can be explained from measurements.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/topbar-boxes.cjs 1440 900
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
    const out = await page.evaluate(() => {
      const b = el => { const r = el.getBoundingClientRect(); return `${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`; };
      const top = document.querySelector('.topbar');
      const cs = el => getComputedStyle(el);
      const app = document.querySelector('.app');
      return {
        app: { display: cs(app).display, cols: cs(app).gridTemplateColumns, rows: cs(app).gridTemplateRows, box: b(app) },
        topbar: { display: cs(top).display, height: cs(top).height, box: b(top), position: cs(top).position, grid: [cs(top).gridColumnStart, cs(top).gridRowStart] },
        children: [...top.children].map(c => ({ tag: c.tagName.toLowerCase(), cls: c.className, box: b(c), display: cs(c).display, visible: cs(c).visibility, text: (c.textContent || '').trim().slice(0, 20) })),
        sidebarBox: b(document.querySelector('.sidebar')),
        appBodyBox: b(document.querySelector('.app-body')),
      };
    });
    console.log(JSON.stringify(out, null, 1));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
