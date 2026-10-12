// Read-only dark-theme contrast audit for My issues tabs, popovers, and team Issues/Board views.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/my-issues-contrast-probe.cjs <min>
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const min = Number(process.argv[2] || 4.5);
const audit = min => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isIcon = el.tagName.toLowerCase() === 'svg';
    if (!hasText && !isIcon) continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el); const f = over(fg, bg);
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    if (ratio >= min) continue;
    const text = isIcon ? '[icon]' : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30);
    const cls = (el.className && el.className.baseVal === undefined ? el.className : '').toString().slice(0, 60);
    out.push({ ratio: Math.round(ratio * 100) / 100, tag: el.tagName.toLowerCase(), cls, text, fg: cs.color, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`, size: cs.fontSize, weight: cs.fontWeight });
  }
  const seen = new Set();
  return out.sort((a, b) => a.ratio - b.ratio).filter(r => { const k = r.tag + r.cls + r.text + r.ratio; if (seen.has(k)) return false; seen.add(k); return true; });
};
const report = (label, rows) => {
  console.log(`STATE ${label} below ${min}: ${rows.length}`);
  for (const r of rows.slice(0, 40)) console.log(`${r.ratio}\t${r.tag}.${r.cls}\t${JSON.stringify(r.text)}\tfg=${r.fg}\tbg=${r.bg}\t${r.size}/${r.weight}`);
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/workspace/my-issues/assigned');
    await page.waitForTimeout(1500);
    const tabs = ['Assigned', 'Created', 'Subscribed', 'Activity'];
    for (const t of tabs) {
      await page.getByRole('tab', { name: t, exact: true }).click();
      await page.waitForTimeout(700);
      report(`tab=${t}`, await page.evaluate(audit, min));
      if (t === 'Assigned') {
        console.log(`STATE tab=Assigned rowCount=${await page.locator('.my-issue-row').count()}`);
        await page.locator('.my-issue-row').first().hover().catch(() => {});
        await page.waitForTimeout(300);
        report('tab=Assigned row-hover', await page.evaluate(audit, min));
      }
    }
    await page.getByRole('tab', { name: 'Assigned', exact: true }).click();
    await page.waitForTimeout(500);
    await page.getByRole('button', { name: 'Add filter' }).click();
    await page.waitForTimeout(400);
    report('tab=Assigned filter-popover', await page.evaluate(audit, min));
    await page.getByRole('button', { name: 'Add filter' }).click();
    await page.getByRole('button', { name: 'Display options' }).click();
    await page.waitForTimeout(400);
    report('tab=Assigned display-popover', await page.evaluate(audit, min));
    await page.getByRole('button', { name: 'Display options' }).click();
    await page.waitForTimeout(200);
    await page.getByRole('button', { name: 'Add filter' }).click();
    await page.getByRole('button', { name: 'Todo', exact: true }).click().catch(() => {});
    await page.waitForTimeout(400);
    report('tab=Assigned filter-popover status=Todo selected', await page.evaluate(audit, min));
    await page.getByRole('button', { name: 'Add filter' }).click();
    await page.waitForTimeout(200);
    // Team views via sidebar "Your teams"
    await page.locator('.team-nav-links button', { hasText: /^Issues$/ }).first().click();
    await page.waitForTimeout(1000);
    report('team-issues (sidebar Your teams > Issues)', await page.evaluate(audit, min));
    await page.locator('.nav-item', { hasText: 'Board' }).first().click().catch(e => console.log('BOARD NAV ERR', e.message.split('\n')[0]));
    await page.waitForTimeout(1000);
    report('board (sidebar More > Board)', await page.evaluate(audit, min));
  } finally { await browser.close(); }
})().catch(e => { console.error('ERR', e.message.split('\n')[0]); process.exit(1); });
