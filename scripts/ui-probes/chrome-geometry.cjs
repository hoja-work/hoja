// Run from repo root. Reports boxes (x, y, w, h) for top bar, brand, command search, sidebar header controls,
// nav groups, team row (name, collapse), and footer user block, at a viewport. Flags duplicated icons and
// overlapping or misaligned boxes. Usage: PROBE_TOKEN=... node scripts/ui-probes/chrome-geometry.cjs 1440 900
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const [w, h] = [Number(process.argv[2] || 1440), Number(process.argv[3] || 900)];
const sel = {
  topbar: '.topbar', mobileToggle: '.mobile-nav-toggle', brand: '.topbar .brand', brandMark: '.topbar .brand-mark',
  command: '.topbar .command', sidebar: '.sidebar', workspaceTrigger: '.workspace-trigger', workspaceMark: '.workspace-trigger-mark',
  sidebarSearch: '.sidebar .sidebar-icon[aria-label="Search workspace"]', sidebarCreate: '.sidebar-create',
  sidebarToggle: '.sidebar-toggle', navFirst: '.sidebar nav .nav-item', teamRow: '.team-row', teamName: '.team-name',
  teamCollapse: '.team-collapse', teamLinks: '.team-nav-links', footerUser: '.sidebar-footer, .sidebar .user-block, .sidebar .viewer',
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(1200);
    const out = await page.evaluate(sel => {
      const box = el => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), r: Math.round(r.right), b: Math.round(r.bottom) }; };
      const res = {};
      for (const [k, s] of Object.entries(sel)) { const el = document.querySelector(s); res[k] = el ? { box: box(el), text: (el.textContent || '').trim().slice(0, 24), icons: el.querySelectorAll('svg').length } : null; }
      const icons = [...document.querySelectorAll('.topbar svg, .sidebar svg')].map(s => s.getAttribute('class') || s.parentElement?.className || '?');
      res.iconCount = icons.length;
      return res;
    }, sel);
    console.log(JSON.stringify(out, null, 1));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
