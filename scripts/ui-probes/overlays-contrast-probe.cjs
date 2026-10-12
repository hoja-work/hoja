// Read-only overlay contrast probe (dark theme, 1440x900). Copy of dark-contrast.cjs logic, but clicks by CSS selector.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/overlays-contrast-probe.cjs <steps> [min=4.5]
// steps: comma-separated; "sel:<css>" clicks, "key:<name>" presses a key, "audit:<label>" reports, "reload" reloads page, "wait:<ms>".
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const steps = (process.argv[2] || 'audit:initial').split(',');
const min = Number(process.argv[3] || 4.5);
const AUDIT = min => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
  const out = []; const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isIcon = el.tagName.toLowerCase() === 'svg';
    const isPh = (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && el.placeholder;
    if (!hasText && !isIcon && !isPh) continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    let fgStr = isPh ? getComputedStyle(el, '::placeholder').color : cs.color;
    const fg = parse(fgStr); if (!fg) continue;
    const bg = bgOf(el); const f = over(fg, bg);
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const text = isIcon ? '[icon]' : isPh ? `placeholder:${el.placeholder}`.slice(0, 40) : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30);
    const cls = (el.className && el.className.baseVal === undefined ? el.className : '').toString().slice(0, 50);
    const key = el.tagName + cls + text + ratio.toFixed(2);
    if (seen.has(key)) continue; seen.add(key);
    out.push({ ratio: Math.round(ratio * 100) / 100, tag: el.tagName.toLowerCase(), cls, text, fg: fgStr, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`, size: cs.fontSize, weight: cs.fontWeight });
  }
  return out.sort((a, b) => a.ratio - b.ratio);
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  let failed = false;
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/');
    await page.waitForTimeout(1500);
    for (const s of steps) {
      const [kind, arg] = [s.slice(0, s.indexOf(':') < 0 ? s.length : s.indexOf(':')), s.slice(s.indexOf(':') + 1)];
      if (kind === 'sel') { await page.locator(arg).first().click({ timeout: 8000 }); await page.waitForTimeout(500); }
      else if (kind === 'key') { await page.keyboard.press(arg); await page.waitForTimeout(300); }
      else if (kind === 'type') { await page.keyboard.type(arg); await page.waitForTimeout(300); }
      else if (kind === 'wait') { await page.waitForTimeout(Number(arg)); }
      else if (kind === 'reload') { await page.reload(); await page.waitForTimeout(1500); }
      else if (kind === 'audit') {
        const rows = await page.evaluate(AUDIT, min);
        const low = rows.filter(r => r.ratio < min);
        console.log(`view=${arg} checked=${rows.length} below ${min}:${low.length}`);
        for (const r of low.slice(0, 40)) console.log(`${r.ratio}\t${r.tag}.${r.cls}\t${JSON.stringify(r.text)}\tfg=${r.fg}\tbg=${r.bg}\t${r.size}/${r.weight}`);
        if (low.length) failed = true;
      }
    }
  } finally { await browser.close(); }
  process.exitCode = failed ? 1 : 0;
})().catch(e => { console.error(String(e.message).split('\n').slice(0, 3).join(' | ')); process.exit(1); });
