// Read-only multi-state dark contrast audit for BOARD, INBOX, PULSE, AGENT.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/board-inbox-pulse-agent-probe.cjs <minRatio>
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const min = Number(process.argv[2] || 4.5);
const AUDIT = `(() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) base = over(c, base); return base; };
  const chain = el => { const names = []; for (let e = el, i = 0; e && i < 4; e = e.parentElement, i++) { const c = (e.className && e.className.baseVal === undefined ? e.className : '').toString().trim().split(/\\s+/)[0]; names.push(e.tagName.toLowerCase() + (c ? '.' + c : '')); } return names.join('<'); };
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
    const key = el.tagName + chain(el) + text + ratio.toFixed(2);
    if (seen.has(key)) continue; seen.add(key);
    out.push({ ratio: Math.round(ratio * 100) / 100, chain: chain(el), text, fg: cs.color, bg: 'rgb(' + Math.round(bg.r) + ',' + Math.round(bg.g) + ',' + Math.round(bg.b) + ')', size: cs.fontSize, weight: cs.fontWeight });
  }
  return out.sort((a, b) => a.ratio - b.ratio);
})()`;
const audit = async (page, state) => {
  const rows = await page.evaluate(AUDIT);
  const low = rows.filter(r => r.ratio < min);
  console.log(`STATE ${state} checked=${rows.length} below=${min}:${low.length}`);
  for (const r of low.slice(0, 20)) console.log(`${r.ratio}\t${r.chain}\t${JSON.stringify(r.text)}\tfg=${r.fg}\tbg=${r.bg}\t${r.size}/${r.weight}`);
};
const nav = async (page, name) => { await page.getByRole('button', { name, exact: true }).first().click({ timeout: 5000 }); await page.waitForTimeout(900); };
const tryClick = async (page, locator, label) => {
  try { await locator.first().click({ timeout: 4000 }); await page.waitForTimeout(500); return true; }
  catch (e) { console.log(`NOT_REACHED ${label}: ${e.message.split('\n')[0]}`); return false; }
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/'); await page.waitForTimeout(1200);
    const errs = []; page.on('pageerror', e => errs.push(e.message));
    await nav(page, 'Board');
    await audit(page, 'board:' + 'default(All issues)');
    for (const t of ['Active', 'Backlog', 'All issues']) { await tryClick(page, page.getByRole('tab', { name: t, exact: true }), 'board tab ' + t); await audit(page, 'board-tab:' + t); }
    const teamBtns = page.locator('.board-team-filter button');
    console.log('team filter options=' + await teamBtns.count());
    if (await teamBtns.count() > 1) { await tryClick(page, teamBtns.nth(1), 'team filter 2'); await audit(page, 'board-team:' + (await teamBtns.nth(1).innerText())); }
    if (await tryClick(page, page.locator('.status-picker-trigger').first(), 'status trigger')) await audit(page, 'board-status-menu-open');
    await page.keyboard.press('Escape'); await page.waitForTimeout(300);
    await nav(page, 'Inbox');
    await audit(page, 'inbox:default');
    console.log('INBOX_TABS ' + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('button,[role=tab]')].map(b => (b.getAttribute('role')||'') + ':' + (b.innerText||'').trim().slice(0, 25)).filter(s => !s.endsWith(':')).slice(0, 30))));
    await nav(page, 'Pulse');
    await audit(page, 'pulse:default');
    console.log('PULSE_TABS ' + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('button,[role=tab]')].map(b => (b.getAttribute('role')||'') + ':' + (b.innerText||'').trim().slice(0, 25)).filter(s => !s.endsWith(':')).slice(0, 30))));
    await nav(page, 'Agent');
    await audit(page, 'agent:welcome');
    console.log('pageerrors=' + JSON.stringify(errs.slice(0, 5)));
  } finally { await browser.close(); }
})().catch(e => { console.error('PROBE_ERROR', e.message); process.exit(1); });
