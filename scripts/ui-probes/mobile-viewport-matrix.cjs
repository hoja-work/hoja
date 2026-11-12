// Mobile viewport matrix for the Hoja web frontend.
//
// Boots a disposable seeded backend, serves the built frontend, then loads each screen at
// representative phone viewports in light AND dark themes, taking a screenshot and measuring the
// properties a phone user depends on:
//   - page-level horizontal scroll (the whole page sliding sideways)
//   - controls parked outside the viewport without a scroll container
//   - touch targets below 44x44 CSS px (WCAG 2.5.5)
//   - inputs below 16px (iOS Safari zooms on focus)
//   - text clipped by overflow:hidden/clip with no ellipsis (silent truncation)
//   - elements with a fixed width wider than the viewport
//   - WCAG contrast for visible text and icons in the current theme
//   - sidebar/drawer state: closed off-canvas, opens on the toggle, closes on Escape/backdrop,
//     and the toggle itself is reachable and >= 44px
//
// Usage (from app/): node scripts/ui-probes/mobile-viewport-matrix.cjs [--out DIR] [--only REGEX]
//                    [--screenshots] [--viewports 390x844,360x800,320x568,844x390]
// Requires: frontend/dist built (`cd frontend && npm run build`) and app/.venv installed.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const appRoot = path.resolve(__dirname, '..', '..');
const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > -1 ? process.argv[i + 1] : fallback;
};
const outDir = path.resolve(arg('--out', path.join(appRoot, 'local-e2e/artifacts/mobile-matrix')));
const shotsDir = path.join(outDir, 'shots');
fs.mkdirSync(shotsDir, { recursive: true });
const web = path.join(appRoot, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');

const viewports = arg('--viewports', '390x844,360x800,320x568,844x390').split(',').map(spec => {
  const [width, height] = spec.split('x').map(Number);
  return { width, height, label: spec };
});
const ONLY = arg('--only', null) ? new RegExp(arg('--only', null), 'i') : null;
const SCREENSHOTS = process.argv.includes('--screenshots');
const token = 'hoja_mobile_matrix_token';
const py = path.join(appRoot, '.venv/bin/python');
const db = path.join(outDir, 'matrix.db');
for (const suffix of ['', '-wal', '-shm']) { try { fs.unlinkSync(db + suffix); } catch {} }

const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='ENG', team_name='Engineering', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: appRoot, env: { ...process.env, PYTHONPATH: appRoot }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 19190 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(appRoot, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: appRoot, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

async function gql(query, variables) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  return (await res.json()).data;
}

// Runs in the page. Returns measurements for the current screen at the current viewport.
const measure = () => {
  const W = window.innerWidth, H = window.innerHeight;
  const issues = [];
  const parse = c => { const m = c && c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = el => { const stack = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } } let b = { r: 255, g: 255, b: 255, a: 1 }; for (const c of stack.reverse()) b = over(c, b); return b; };
  const visible = el => { const s = getComputedStyle(el); if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const nameOf = el => (el.getAttribute('aria-label') || el.getAttribute('title') || (el.labels && el.labels[0] && el.labels[0].textContent) || el.getAttribute('placeholder') || el.textContent || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const scrollAncestor = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return p; } return null; };
  const onCanvas = r => r.right > 0 && r.left < W;

  if (document.documentElement.scrollWidth > W + 1) issues.push({ kind: 'page-horizontal-scroll', detail: `page is ${document.documentElement.scrollWidth}px wide at ${W}px` });
  if (document.body.scrollWidth > W + 1) issues.push({ kind: 'body-horizontal-scroll', detail: `body is ${document.body.scrollWidth}px wide at ${W}px` });

  // Controls.
  const SELECTOR = 'button,a[href],input,select,textarea,summary,[role=button],[role=tab],[role=menuitem],[role=option],[role=switch],[role=checkbox],[role=radio],[role=link],[role=combobox],[contenteditable=true]';
  const controls = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (!onCanvas(r)) continue;
    const s = getComputedStyle(el);
    const label = `${el.getAttribute('role') || el.tagName.toLowerCase()} "${nameOf(el) || '(no name)'}"`;
    const scroller = scrollAncestor(el);
    const inline = el.tagName === 'A' && s.display === 'inline';
    if (!nameOf(el)) issues.push({ kind: 'unlabeled-control', detail: label });
    if (!inline && (r.width < 44 || r.height < 44) && !el.matches('input[type=checkbox],input[type=radio]') && !scroller) issues.push({ kind: 'touch-target-small', detail: `${label} is ${Math.round(r.width)}x${Math.round(r.height)}` });
    if (el.matches('input,select,textarea') && Number.parseFloat(s.fontSize) < 16) issues.push({ kind: 'input-font-under-16px', detail: `${label} uses ${s.fontSize}` });
    if (r.right > W + 1 && r.left < W && !scroller) issues.push({ kind: 'control-overflows-viewport', detail: `${label} ends at ${Math.round(r.right)}px` });
    if (r.left < -1 && !scroller) issues.push({ kind: 'control-offscreen-left', detail: `${label} starts at ${Math.round(r.left)}px` });
    controls.push({ role: el.getAttribute('role') || el.tagName.toLowerCase(), name: nameOf(el), w: Math.round(r.width), h: Math.round(r.height) });
  }

  // Fixed-width boxes wider than the viewport and not inside a scroller.
  for (const el of document.querySelectorAll('main *, .sidebar *')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= W + 1 || !onCanvas(r)) continue;
    if (scrollAncestor(el)) continue;
    if (['SCRIPT', 'STYLE', 'SVG', 'PATH'].includes(el.tagName)) continue;
    issues.push({ kind: 'element-wider-than-viewport', detail: `${el.tagName.toLowerCase()}.${String(el.className || '').slice(0, 40)} is ${Math.round(r.width)}px at ${W}px` });
  }

  // Silent truncation: text clipped with overflow hidden/clip and no ellipsis.
  const textEls = document.querySelectorAll('main *, .topbar *, .sidebar *');
  const seenText = new Set();
  for (const el of textEls) {
    if (!visible(el)) continue;
    const s = getComputedStyle(el);
    const hasOwnText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!hasOwnText) continue;
    if (!(s.overflowX === 'hidden' || s.overflowX === 'clip' || s.overflow === 'hidden')) continue;
    if (el.scrollWidth <= el.clientWidth + 1) continue;
    if (s.textOverflow === 'ellipsis' || el.matches('input,textarea,select')) continue;
    const text = el.textContent.trim().replace(/\s+/g, ' ').slice(0, 50);
    const key = text + '|' + Math.round(el.getBoundingClientRect().width);
    if (seenText.has(key)) continue; seenText.add(key);
    issues.push({ kind: 'text-clipped-no-ellipsis', detail: `"${text}" needs ${el.scrollWidth}px in ${el.clientWidth}px (${s.overflow})` });
  }

  // Contrast in the active theme.
  const low = [];
  for (const el of document.querySelectorAll('main *, .topbar *, .sidebar *')) {
    if (!visible(el)) continue;
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isIcon = el.tagName.toLowerCase() === 'svg';
    if (!hasText && !isIcon) continue;
    const r = el.getBoundingClientRect();
    if (!onCanvas(r)) continue;
    const s = getComputedStyle(el);
    // Text hidden with font-size:0 is a deliberate icon-only affordance; its icon is checked separately.
    if (!isIcon && parseFloat(s.fontSize) < 1) continue;
    const fg = parse(s.color); if (!fg) continue;
    const bg = bgOf(el);
    const f = over(fg, bg);
    const L1 = lum(f), L2 = lum(bg);
    const ratio = Math.round(((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)) * 100) / 100;
    const size = parseFloat(s.fontSize), weight = Number(s.fontWeight) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const min = large ? 3 : 4.5;
    if (ratio >= min) continue;
    const text = isIcon ? '[icon]' : [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 40);
    const cls = String(el.className && el.className.baseVal === undefined ? el.className : '').slice(0, 46);
    const chain = (() => { const parts = []; for (let e = el; e && e !== document.body && parts.length < 4; e = e.parentElement) parts.push(e.tagName.toLowerCase() + (e.className && e.className.baseVal === undefined && e.className ? '.' + String(e.className).split(' ').slice(0, 2).join('.') : '')); return parts.join(' < '); })();
    low.push({ ratio, min, text, cls, chain, tag: el.tagName.toLowerCase(), fg: s.color, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`, size: s.fontSize, weight: s.fontWeight });
  }
  const dedup = new Map();
  for (const row of low) { const key = `${row.tag}.${row.cls}|${row.text}|${row.ratio}`; if (!dedup.has(key)) dedup.set(key, row); }
  const contrast = [...dedup.values()].sort((a, b) => a.ratio - b.ratio);
  for (const row of contrast.slice(0, 8)) issues.push({ kind: 'contrast-below-wcag', detail: `${row.ratio}:1 (min ${row.min}) "${row.text}" in ${row.chain}` });

  return { viewportWidth: W, viewportHeight: H, scrollWidth: document.documentElement.scrollWidth, themeAttr: document.documentElement.dataset.theme || null, bodyBackground: getComputedStyle(document.body).backgroundColor, controls, contrast, issues };
};

// Sidebar/drawer behaviour at phone widths.
const probeDrawer = async page => {
  const toggle = page.locator('.mobile-nav-toggle').first();
  const result = { toggleExists: await toggle.count() > 0 };
  if (!result.toggleExists) return result;
  const box = await toggle.boundingBox();
  result.toggleBox = box && { width: Math.round(box.width), height: Math.round(box.height) };
  const sidebar = page.locator('.sidebar').first();
  result.closedLeft = Math.round((await sidebar.boundingBox())?.x ?? 0);
  await toggle.click({ timeout: 3000 }).catch(() => { result.toggleClickFailed = true; });
  await page.waitForTimeout(400);
  result.openLeft = Math.round((await sidebar.boundingBox())?.x ?? 0);
  result.openClass = await sidebar.getAttribute('class');
  // Bounding box of the first nav item inside the drawer tells us whether it is reachable.
  const navItem = sidebar.locator('.nav-item').first();
  if (await navItem.count()) { const nb = await navItem.boundingBox(); result.firstNavItem = nb && { x: Math.round(nb.x), y: Math.round(nb.y), width: Math.round(nb.width), height: Math.round(nb.height) }; }
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  result.afterEscapeLeft = Math.round((await sidebar.boundingBox())?.x ?? 0);
  return result;
};

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    const issues = (await gql('query{issues(first:3){nodes{identifier}}}')).issues.nodes.map(n => n.identifier);
    const project = (await gql('mutation{projectCreate(input:{name:"Mobile audit project with a deliberately long name",teamIds:[]}){success project{id}}}')).projectCreate.project;
    assert.ok(issues.length && project, 'seed data missing');
    const teamKey = (await gql('query{teams{nodes{key}}}')).teams.nodes[0].key.toLowerCase();

    const screens = [
      ['My issues', '/workspace/my-issues/assigned'],
      ['Pulse', '/workspace/pulse/all'],
      ['Inbox', '/workspace/inbox/other'],
      ['Projects', '/workspace/projects'],
      ['Board', '/workspace/board'],
      ['Team issues', `/workspace/team/${teamKey}/active`],
      ['Team home', `/workspace/team/${teamKey}`],
      ['Issue detail', `/workspace/issue/${issues[0]}`],
      ['Project detail', `/workspace/project/${project.id}`],
      ['Agent', '/workspace/agent'],
      ['Settings / preferences', '/workspace/settings/preferences'],
      ['Settings / api-keys', '/workspace/settings/api-keys'],
      ['Members', '/workspace/members'],
    ].filter(([name]) => !ONLY || ONLY.test(name));

    browser = await chromium.launch({ headless: true });
    const report = [];
    for (const theme of ['light', 'dark']) {
      // The app loads the theme from the account preference at start, so the audit sets it server-side.
      const saved = await gql(`mutation($v:String!){userPreferenceSet(key:"theme",value:$v){success}}`, { v: theme });
      assert.ok(saved.userPreferenceSet.success, `could not set ${theme} theme`);
      for (const vp of viewports) {
        const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, isMobile: vp.width < vp.height, hasTouch: true, deviceScaleFactor: 2 });
        await ctx.addInitScript(([t, th]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', th); }, [token, theme]);
        for (const [screen, route] of screens) {
          const page = await ctx.newPage();
          const errors = [], consoleMsgs = [], failedRequests = [];
          page.on('pageerror', e => errors.push(e.message));
          page.on('console', m => { if (m.type() === 'error') consoleMsgs.push(m.text().slice(0, 160)); });
          page.on('response', r => { if (r.status() >= 400) failedRequests.push(`${r.status()} ${r.url().replace(base, '')}`); });
          await page.goto(base + route);
          await page.waitForLoadState('load');
          await page.waitForTimeout(900);
          const result = await page.evaluate(measure);
          const shot = path.join(shotsDir, `${theme}-${vp.label}-${screen.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.png`);
          if (SCREENSHOTS) await page.screenshot({ path: shot, fullPage: true });
          const drawer = vp.width < vp.height ? await probeDrawer(page) : null;
          if (drawer) {
            if (drawer.toggleExists && drawer.toggleBox && (drawer.toggleBox.width < 44 || drawer.toggleBox.height < 44)) issues.push({ kind: 'sidebar-toggle-small', detail: `mobile nav toggle is ${drawer.toggleBox.width}x${drawer.toggleBox.height}` });
            if (drawer.toggleExists && !drawer.toggleClickFailed && drawer.openLeft < 0) issues.push({ kind: 'sidebar-does-not-open', detail: `sidebar left=${drawer.openLeft} after toggle click` });
            if (drawer.toggleExists && drawer.afterEscapeLeft > -1) issues.push({ kind: 'sidebar-does-not-close-on-escape', detail: `sidebar left=${drawer.afterEscapeLeft} after Escape` });
          }
          report.push({ theme, viewport: vp.label, width: vp.width, height: vp.height, screen, route, finalUrl: new URL(page.url()).pathname, drawer, pageErrors: errors, consoleErrors: consoleMsgs, failedRequests: [...new Set(failedRequests)], issues: [...result.issues], contrast: result.contrast.slice(0, 10), themeAttr: result.themeAttr, bodyBackground: result.bodyBackground, controlCount: result.controls.length, screenshot: SCREENSHOTS ? shot : null });
          await page.close();
        }
        await ctx.close();
      }
    }
    fs.writeFileSync(path.join(outDir, 'matrix.json'), JSON.stringify(report, null, 2));
    const byKind = {}, byScreen = {};
    let total = 0;
    for (const r of report) { total += r.issues.length; const k = `${r.theme} ${r.viewport}`; byScreen[k] = (byScreen[k] || 0) + r.issues.length; for (const i of r.issues) byKind[i.kind] = (byKind[i.kind] || 0) + 1; }
    console.log(`mobile matrix: ${report.length} runs (screens x viewports x themes), ${total} findings`);
    console.log('by viewport/theme: ' + JSON.stringify(byScreen));
    console.log('by kind: ' + JSON.stringify(byKind, null, 2));
    console.log(`report: ${path.join(outDir, 'matrix.json')}`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
  }
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
