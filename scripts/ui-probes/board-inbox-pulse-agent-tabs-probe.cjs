// Read-only: clicks Inbox/Pulse tab links and the Inbox actions menu; audits contrast of each state.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const min = 4.5;
const AUDIT = `(() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
  const chain = el => { const names = []; for (let e = el, i = 0; e && i < 3; e = e.parentElement, i++) { const c = (e.className && e.className.baseVal === undefined ? e.className : '').toString().trim().split(/\\s+/)[0]; names.push(e.tagName.toLowerCase() + (c ? '.' + c : '')); } return names.join('<'); };
  const out = [];
  for (const el of document.querySelectorAll('.main *')) {
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isIcon = el.tagName.toLowerCase() === 'svg';
    if (!hasText && !isIcon) continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el); const f = over(fg, bg);
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const text = isIcon ? '[icon]' : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30);
    out.push({ ratio: Math.round(ratio * 100) / 100, chain: chain(el), text, fg: cs.color, bg: 'rgb(' + Math.round(bg.r) + ',' + Math.round(bg.g) + ',' + Math.round(bg.b) + ')', size: cs.fontSize, weight: cs.fontWeight });
  }
  return out.sort((a, b) => a.ratio - b.ratio);
})()`;
const audit = async (page, state) => {
  const rows = await page.evaluate(AUDIT);
  const low = rows.filter(r => r.ratio < min);
  console.log(`STATE ${state} checked(.main)=${rows.length} below=${min}:${low.length}`);
  for (const r of low.slice(0, 12)) console.log(`${r.ratio}\t${r.chain}\t${JSON.stringify(r.text)}\tfg=${r.fg}\tbg=${r.bg}\t${r.size}/${r.weight}`);
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/'); await page.waitForTimeout(1200);
    const nav = async n => { await page.getByRole('button', { name: n, exact: true }).first().click(); await page.waitForTimeout(900); };
    await nav('Inbox'); await audit(page, 'inbox:other');
    await page.locator('.inbox-tabs a', { hasText: 'Priority' }).click(); await page.waitForTimeout(700); await audit(page, 'inbox:priority');
    const menu = page.locator('button[aria-label="Notification actions"]');
    if (await menu.count()) { await menu.first().click(); await page.waitForTimeout(400); await audit(page, 'inbox:notification-actions-open'); await page.keyboard.press('Escape'); }
    await nav('Pulse'); await audit(page, 'pulse:default');
    for (const t of ['For me', 'Popular', 'Recent']) { await page.locator('.pulse-tabs a', { hasText: t }).click(); await page.waitForTimeout(700); await audit(page, 'pulse:' + t); }
  } finally { await browser.close(); }
})().catch(e => { console.error('PROBE_ERROR', e.message); process.exit(1); });
