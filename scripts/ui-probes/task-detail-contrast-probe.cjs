// READ-ONLY audit helper for issue detail + create sheet dark contrast. Copy of dark-contrast.cjs logic with scenario steps.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/issue-detail-contrast-probe.cjs <scenario> [min=4.5]
// scenarios: issue | issue-status | create | create-team
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const scenario = process.argv[2] || 'issue';
const min = Number(process.argv[3] || 4.5);
const AUDIT = `(${(min => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
  const out = []; const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isIcon = el.tagName.toLowerCase() === 'svg';
    if (!hasText && !isIcon) continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el); const f = over(fg, bg);
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const text = isIcon ? '[icon]' : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30);
    const cls = (el.className && el.className.baseVal === undefined ? el.className : '').toString().slice(0, 50);
    const key = el.tagName + cls + text + ratio.toFixed(2);
    if (seen.has(key)) continue; seen.add(key);
    out.push({ ratio: Math.round(ratio * 100) / 100, tag: el.tagName.toLowerCase(), cls, text, fg: cs.color, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`, size: cs.fontSize, weight: cs.fontWeight });
  }
  return out.sort((a, b) => a.ratio - b.ratio);
}).toString()})(${min})`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/');
    await page.waitForTimeout(1500);
    const steps = {
      'issue': [['.nav-item:has-text("Board")', 'first'], ['.kanban-card', 'first']],
      'issue-status': [['.nav-item:has-text("Board")', 'first'], ['.kanban-card', 'first'], ['.issue-detail-page .status-picker-trigger', 'first']],
      'create': [['[aria-label="Create new issue"]', 'first']],
      'create-team': [['[aria-label="Create new issue"]', 'first'], ['.issue-create-modal select, .issue-create-modal [aria-haspopup="listbox"]', 'first']],
    }[scenario];
    if (!steps) { console.error('unknown scenario'); process.exit(2); }
    for (const [sel] of steps) { await page.locator(sel).first().click(); await page.waitForTimeout(500); }
    const rows = await page.evaluate(AUDIT);
    const low = rows.filter(r => r.ratio < min);
    console.log(`scenario=${scenario} checked=${rows.length} below ${min}:${low.length}`);
    for (const r of low.slice(0, 60)) console.log(`${r.ratio}\t${r.tag}.${r.cls}\t${JSON.stringify(r.text)}\tfg=${r.fg}\tbg=${r.bg}\t${r.size}/${r.weight}`);
    process.exitCode = low.length ? 1 : 0;
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
