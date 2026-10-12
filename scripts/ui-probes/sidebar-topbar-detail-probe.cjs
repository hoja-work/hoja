// Read-only: which element classes produce sidebar/topbar failures; opens workspace menu via its real button.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const DESC = `(() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
  const path = el => { const parts = []; for (let e = el; e && e !== document.body && parts.length < 4; e = e.parentElement) { const cls = (e.getAttribute('class') || '').split(/\\s+/).filter(Boolean).slice(0, 2).join('.'); parts.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : '')); } return parts.join(' > '); };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const isIcon = el.tagName.toLowerCase() === 'svg';
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!hasText && !isIcon) continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el); const f = over(fg, bg);
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    if (ratio >= 4.5) continue;
    out.push({ ratio: Math.round(ratio * 100) / 100, path: path(el), fg: cs.color, bg: 'rgb(' + Math.round(bg.r) + ',' + Math.round(bg.g) + ',' + Math.round(bg.b) + ')', size: cs.fontSize, weight: cs.fontWeight, text: isIcon ? '[icon]' : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30) });
  }
  return out;
})()`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/');
    await page.waitForTimeout(1500);
    const show = async label => { const rows = await page.evaluate(DESC); console.log('STATE ' + label + ' below4.5=' + rows.length); for (const r of rows.slice(0, 12)) console.log(r.ratio + '\t' + r.path + '\t' + JSON.stringify(r.text) + '\tfg=' + r.fg + '\tbg=' + r.bg + '\t' + r.size + '/' + r.weight); };
    await show('default');
    const ws = page.getByRole('button', { name: /Acme/ }).first();
    console.log('workspace-button-count=' + await ws.count());
    await ws.click({ timeout: 3000 }).catch(e => console.log('ws-click-failed ' + e.message.split('\n')[0]));
    await page.waitForTimeout(500);
    await show('workspace-menu-open');
    console.log('menu-classes=' + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('[class*="workspace-menu"], [role="menu"]')].map(e => e.className.toString()).slice(0, 5))));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    const cr = page.getByRole('button', { name: 'Create new issue', exact: true }).first();
    await cr.click({ timeout: 3000 }).catch(() => console.log('create-click-failed'));
    await page.waitForTimeout(500);
    await show('create-issue-open');
    await page.keyboard.press('Escape');
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
