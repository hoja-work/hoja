// HOJA-109 OSS parity port of hoja-cloud#47: the shared workspace drawer must close on Escape
// and on a backdrop tap, return focus to .mobile-nav-toggle, and expose dialog semantics only
// while it is open at phone width. Settings keeps its own drawer behaviour.
//
// Self-contained: seeds a throwaway SQLite db, serves frontend/dist with hoja-serve and drives
// it headless at 390x844 (phone) and 1440x900 (desktop).
//
//   cd frontend && npm run build
//   HOJA_VENV=/path/to/venv PLAYWRIGHT_MODULE=/path/to/playwright node scripts/ui-probes/workspace-drawer-escape.cjs
//
// Exit 0 means every assertion passed.
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

function loadPlaywright() {
  for (const c of [process.env.PLAYWRIGHT_MODULE, 'playwright',
                   path.join(__dirname, '../../local-e2e/node_modules/playwright')].filter(Boolean)) {
    try { return require(c) } catch { /* next */ }
  }
  throw new Error('Playwright not found; set PLAYWRIGHT_MODULE.');
}
const { chromium } = loadPlaywright();

const repo = path.resolve(__dirname, '../..');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');

const venv = process.env.HOJA_VENV || path.join(repo, '.venv');
const py = path.join(venv, 'bin/python');
const serve = path.join(venv, 'bin/hoja-serve');
assert.ok(fs.existsSync(py) && fs.existsSync(serve), `no venv at ${venv}; set HOJA_VENV`);

const token = 'hoja_drawer_escape_token';
const dir = fs.mkdtempSync(path.join(scratch, 'drawer-escape-'));
const db = path.join(dir, 'drawer.db');
const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='ENG', team_name='Engineering', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 19000 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(serve, ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
const ok = (m) => console.log('ok   ' + m);
const check = (cond, m, detail) => {
  if (cond) ok(m);
  else { failures++; console.log('FAIL ' + m + (detail ? ' :: ' + JSON.stringify(detail) : '')); }
};

const read = (page) => page.evaluate(() => {
  const el = document.getElementById('workspace-sidebar');
  const settings = document.querySelector('.settings-sidebar');
  const drawer = settings || el;
  const toggle = document.querySelector('.mobile-nav-toggle');
  const scrim = document.querySelector('.workspace-drawer-scrim');
  const cs = drawer ? getComputedStyle(drawer) : null;
  const box = drawer ? drawer.getBoundingClientRect() : null;
  const m = cs && cs.transform !== 'none' ? new DOMMatrixReadOnly(cs.transform) : null;
  const active = document.activeElement;
  return {
    drawer: drawer ? (settings ? 'settings-sidebar' : 'workspace-sidebar') : null,
    left: box ? Math.round(box.left) : null,
    right: box ? Math.round(box.right) : null,
    width: box ? Math.round(box.width) : null,
    display: cs && cs.display, visibility: cs && cs.visibility,
    transform: cs && cs.transform,
    translateX: m ? Math.round(m.m41) : 0,
    z: cs && cs.zIndex,
    role: drawer && drawer.getAttribute('role'),
    ariaModal: drawer && drawer.getAttribute('aria-modal'),
    ariaExpanded: toggle && toggle.getAttribute('aria-expanded'),
    focusInside: !!(drawer && drawer.contains(active)),
    activeClass: String((active && active.className) || ''),
    activeLabel: (active && active.getAttribute('aria-label')) || '',
    scrimPresent: !!scrim,
    scrimDisplay: scrim ? getComputedStyle(scrim).display : null,
    scrimVisibility: scrim ? getComputedStyle(scrim).visibility : null,
    scrimZ: scrim ? getComputedStyle(scrim).zIndex : null,
    view: (document.querySelector('.page-head h1, .not-found-page h1') || {}).textContent || '',
    pageScrollW: document.documentElement.scrollWidth,
    innerW: window.innerWidth,
  };
});

const openDrawer = async (page) => {
  await page.click('.mobile-nav-toggle');
  await wait(300);
};

(async () => {
  let browser;
  const pageErrors = [];
  const httpErrors = [];
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch { /* not up yet */ } await wait(250); }
    const gql = async (query) => (await (await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query }) })).json()).data;
    const issue = (await gql('query{issues(first:1){nodes{identifier}}}')).issues.nodes[0].identifier;
    const routes = [
      ['My issues', '/workspace/my-issues/assigned'],
      ['Board', '/workspace/board'],
      ['Inbox', '/workspace/inbox/other'],
      ['Issue detail', `/workspace/issue/${issue}`],
    ];

    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => pageErrors.push('pageerror: ' + String(e.message || e)));
    page.on('response', (r) => { if (r.status() >= 400) httpErrors.push(`${r.status()} ${r.request().method()} ${r.url().replace(base, '')}`); });

    for (const [label, route] of routes) {
      await page.goto(base + route);
      await wait(1100);
      const closed = await read(page);
      check(closed.drawer === 'workspace-sidebar' && closed.right <= 0 && closed.display !== 'none' && closed.visibility !== 'hidden',
        `${label}: drawer starts translated off-screen (right=${closed.right}, transform=${closed.transform})`, closed);

      await openDrawer(page);
      const open = await read(page);
      check(open.left >= -1 && open.right > 0, `${label}: drawer slides in (left=${open.left}, width=${open.width})`, open);
      check(open.ariaExpanded === 'true', `${label}: toggle aria-expanded=true while open`, open);
      check(open.role === 'dialog' && open.ariaModal === 'true', `${label}: role=dialog + aria-modal while open at phone width`, open);
      check(open.focusInside, `${label}: focus moved into the drawer (active=${open.activeClass})`, open);
      check(open.scrimPresent && open.scrimVisibility === 'visible' && Number(open.scrimZ) < Number(open.z),
        `${label}: backdrop visible under the drawer (scrim z=${open.scrimZ} < drawer z=${open.z})`, open);

      // Tab must not escape the open drawer.
      const startTab = await page.evaluate(() => document.querySelector('.settings-sidebar, #workspace-sidebar').querySelectorAll('button:not(:disabled),a[href],input:not(:disabled)').length);
      await page.keyboard.press('Tab'); await wait(60);
      let stays = true;
      for (let i = 0; i < startTab + 4; i++) { await page.keyboard.press('Tab'); if (!(await read(page)).focusInside) { stays = false; break; } }
      check(stays, `${label}: Tab stays inside the open drawer (${startTab} focusables, ${startTab + 5} presses)`, await read(page));

      await page.keyboard.press('Escape');
      await wait(350);
      const after = await read(page);
      check(after.right <= 0 && after.display !== 'none' && after.visibility !== 'hidden' && after.translateX < 0,
        `${label}: Escape translates the drawer off-screen (right=${after.right}, translateX=${after.translateX}px, display=${after.display}, visibility=${after.visibility})`, after);
      check(after.ariaExpanded === 'false', `${label}: toggle aria-expanded=false after Escape`, after);
      check(after.role === null && after.ariaModal === null, `${label}: dialog semantics removed when closed (role=${after.role}, aria-modal=${after.ariaModal})`, after);
      check(after.activeClass.includes('mobile-nav-toggle'), `${label}: focus restored to .mobile-nav-toggle (active=${after.activeClass})`, after);

      // Backdrop tap closes it too.
      await openDrawer(page);
      const beforeTap = await read(page);
      const tapTarget = await page.evaluate(() => {
        const el = document.elementFromPoint(window.innerWidth - 16, window.innerHeight - 16);
        return el ? String(el.className || el.tagName) : null;
      });
      check(tapTarget === 'workspace-drawer-scrim visible', `${label}: backdrop is the hit target outside the drawer (hit=${tapTarget})`, beforeTap);
      await page.mouse.click(390 - 16, 844 - 16);
      await wait(350);
      const afterTap = await read(page);
      check(afterTap.right <= 0 && afterTap.ariaExpanded === 'false' && afterTap.activeClass.includes('mobile-nav-toggle'),
        `${label}: backdrop tap closes the drawer and restores focus (right=${afterTap.right}, active=${afterTap.activeClass})`, afterTap);
    }

    // Settings drawer must not regress: it still closes on Escape.
    await page.goto(base + '/workspace/settings/preferences');
    await wait(1100);
    await openDrawer(page);
    const sOpen = await read(page);
    check(sOpen.drawer === 'settings-sidebar' && sOpen.left >= -1 && sOpen.focusInside,
      `Settings: drawer opens with focus inside (left=${sOpen.left}, active=${sOpen.activeClass})`, sOpen);
    check(!sOpen.scrimPresent, 'Settings: no workspace scrim is rendered in the Settings branch', sOpen);
    await page.keyboard.press('Escape');
    await wait(350);
    const sAfter = await read(page);
    check(sAfter.right <= 0 && sAfter.ariaExpanded === 'false' && sAfter.activeClass.includes('mobile-nav-toggle'),
      `Settings: Escape still closes the settings drawer and restores focus (right=${sAfter.right}, active=${sAfter.activeClass})`, sAfter);

    // Desktop: no drawer behaviour, no scrim, layout intact.
    const deskCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await deskCtx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    const desk = await deskCtx.newPage();
    desk.on('pageerror', (e) => pageErrors.push('desktop: ' + String(e.message || e)));
    await desk.goto(base + '/workspace/my-issues/assigned');
    await wait(1100);
    const d = await read(desk);
    const toggleBox = await desk.evaluate(() => {
      const t = document.querySelector('.mobile-nav-toggle');
      return t ? getComputedStyle(t).display : null;
    });
    check(d.left === 0 && d.right === d.width, `Desktop: drawer is inline at left=${d.left} width=${d.width}`, d);
    check(d.role === null && d.ariaModal === null && d.scrimDisplay === 'none',
      `Desktop: no dialog semantics and the scrim is display:none (role=${d.role}, scrim=${d.scrimDisplay})`, d);
    check(toggleBox === 'none', `Desktop: the phone toggle is not rendered (display=${toggleBox})`);
    check(d.pageScrollW <= d.innerW + 1, `Desktop: no horizontal page scroll (${d.pageScrollW}px at ${d.innerW}px)`, d);

    check(pageErrors.length === 0, 'No uncaught page errors during the run', pageErrors);
    // Requests the seeded probe token cannot authorize are reported, not asserted: they are
    // endpoint-scoped and unrelated to the drawer (verified identical on pristine main).
    console.log(`     observed HTTP >=400 responses: ${httpErrors.length ? [...new Set(httpErrors)].join(' | ') : 'none'}`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
  console.log(failures ? `\n${failures} assertion(s) failed` : '\nall drawer assertions passed');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error('ERROR ' + e.stack); server.kill('SIGTERM'); process.exit(1); });
