// Run from repo root. Walks visible text and SVG icons in the signed-in shell, resolves each one's effective
// foreground and nearest opaque background, and reports WCAG contrast ratios below a threshold.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/contrast-audit.cjs [min-ratio=3] [width=1440] [height=900]
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const min = Number(process.argv[2] || 3);
const [w, h] = [Number(process.argv[3] || 1440), Number(process.argv[4] || 900)];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(url + '/');
    await page.waitForTimeout(1200);
    const rows = await page.evaluate(min => {
      const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
      const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
      const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
      const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
      const out = []; const seen = new Set();
      const walker = document.querySelectorAll('.sidebar *, .topbar *');
      for (const el of walker) {
        const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
        const isIcon = el.tagName.toLowerCase() === 'svg';
        if (!hasText && !isIcon) continue;
        const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
        const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
        const fg = parse(isIcon ? cs.color : cs.color); if (!fg) continue;
        const bg = bgOf(el); const f = over(fg, bg);
        const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
        const label = (isIcon ? 'svg' : el.tagName.toLowerCase()) + '.' + (el.className && el.className.baseVal === undefined ? el.className : '') ;
        const text = isIcon ? '[icon]' : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30);
        const key = label + '|' + text + '|' + ratio.toFixed(2);
        if (seen.has(key)) continue; seen.add(key);
        out.push({ ratio: Math.round(ratio * 100) / 100, label: label.slice(0, 60), text, fg: cs.color, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})` });
      }
      return out.sort((a, b) => a.ratio - b.ratio);
    }, min);
    const low = rows.filter(r => r.ratio < min);
    console.log(`checked ${rows.length} text/icon elements at ${w}x${h}; below ${min}:${low.length}`);
    for (const r of low.slice(0, 60)) console.log(`${r.ratio}\t${r.label}\t${JSON.stringify(r.text)}\tfg=${r.fg}\tbg=${r.bg}`);
    process.exitCode = low.length ? 1 : 0;
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
