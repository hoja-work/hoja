// Rendered shell top-alignment invariants against the built frontend and a seeded backend.
// Verifies that [data-workspace-content] and [data-workspace-page] stay anchored to the top of
// the desktop viewport (and directly below the mobile topbar on phones) across Leaderboard,
// Member profile, and sibling workspace routes — preventing an implicit grid row from inserting
// a blank vertical gap above shorter views (HOJA-744).
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hoja-shell-alignment-'));
const db = path.join(dir, 'shell-alignment.db');
const token = 'hoja_shell_alignment_token';
const py = path.join(repo, '.venv/bin/python');

const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 18900 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], {
  cwd: repo,
  env: { ...process.env, HOJA_WEB_ROOT: web },
  stdio: 'ignore',
});
const wait = ms => new Promise(r => setTimeout(r, ms));

const ROUTES = [
  '/workspace/leaderboard',
  '/workspace/member',
  '/workspace/inbox',
  '/workspace/my-issues/assigned',
  '/workspace/pulse',
  '/workspace/initiatives',
  '/workspace/projects',
  '/workspace/views/issues',
  '/workspace/members',
  '/workspace/settings/profile',
];

async function measureShell(page) {
  return page.evaluate(() => {
    const visible = el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const app = document.querySelector('.app');
    const content = document.querySelector('[data-workspace-content]');
    const shell = document.querySelector('[data-workspace-page]');
    const topbar = document.querySelector('[data-topbar]');
    const leaderboard = document.querySelector('[data-leaderboard-page]');
    const topAnchor = topbar && visible(topbar) ? topbar.getBoundingClientRect().bottom : 0;
    const cr = content ? content.getBoundingClientRect() : null;
    const sr = shell ? shell.getBoundingClientRect() : null;
    const lr = leaderboard ? leaderboard.getBoundingClientRect() : null;
    const cs = content ? getComputedStyle(content) : null;
    const as = app ? getComputedStyle(app) : null;
    return {
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
      topAnchor: Math.round(topAnchor),
      contentTop: cr ? Math.round(cr.top) : null,
      contentHeight: cr ? Math.round(cr.height) : null,
      contentGridRow: cs ? `${cs.gridRowStart}/${cs.gridRowEnd}` : null,
      appGridRows: as ? as.gridTemplateRows : null,
      shellTop: sr ? Math.round(sr.top) : null,
      shellHeight: sr ? Math.round(sr.height) : null,
      leaderboardTop: lr ? Math.round(lr.top) : null,
    };
  });
}

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) {
      try { if ((await fetch(`${base}/health`)).ok) break; } catch {}
      await wait(200);
    }
    browser = await chromium.launch({ headless: true });
    const failures = [];

    for (const vp of [{ width: 1600, height: 1000 }, { width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      const isDesktop = vp.width >= 1001;
      const ctx = await browser.newContext({ viewport: vp });
      await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      const page = await ctx.newPage();

      for (const route of ROUTES) {
        await page.goto(base + route);
        await page.waitForSelector('[data-workspace-content]', { timeout: 10000 });
        await wait(450);
        const m = await measureShell(page);
        if (m.contentTop === null || m.contentTop - m.topAnchor > 16) {
          failures.push(`${vp.width}x${vp.height} ${route}: [data-workspace-content] dropped by ${m.contentTop - m.topAnchor}px (gridRow=${m.contentGridRow}, appRows=${m.appGridRows})`);
        }
        if (m.shellTop !== null && m.shellTop - m.topAnchor > 16) {
          failures.push(`${vp.width}x${vp.height} ${route}: [data-workspace-page] dropped by ${m.shellTop - m.topAnchor}px (shellTop=${m.shellTop}, topAnchor=${m.topAnchor})`);
        }
        if (isDesktop && m.contentHeight !== null && Math.abs(m.contentHeight - vp.height) > 2) {
          failures.push(`${vp.width}x${vp.height} ${route}: desktop [data-workspace-content] height ${m.contentHeight}px != viewport ${vp.height}px (gridRow=${m.contentGridRow}, appRows=${m.appGridRows})`);
        }
        if (route === '/workspace/leaderboard') {
          if (m.leaderboardTop === null || m.shellTop === null || m.leaderboardTop - m.shellTop > 4) {
            failures.push(`${vp.width}x${vp.height} ${route}: [data-leaderboard-page] offset inside shell (${m.leaderboardTop} vs ${m.shellTop})`);
          }
        }
      }

      // Also verify client-side sidebar transition from Inbox -> Leaderboard on desktop.
      if (isDesktop) {
        await page.goto(`${base}/workspace/inbox`);
        await page.waitForSelector('[aria-label="Workspace navigation"] [data-nav-item]');
        await page.locator('[aria-label="Workspace navigation"] [data-nav-item]').filter({ hasText: /^\s*Leaderboard\s*$/ }).first().click();
        await page.waitForSelector('[data-leaderboard-page]', { timeout: 10000 });
        await wait(350);
        const m = await measureShell(page);
        if (m.shellTop - m.topAnchor > 16 || Math.abs(m.contentHeight - vp.height) > 2) {
          failures.push(`${vp.width}x${vp.height} nav Inbox->Leaderboard: shellTop=${m.shellTop}, contentHeight=${m.contentHeight}/${vp.height}`);
        }
      }

      await ctx.close();
    }

    assert.equal(failures.length, 0, `${failures.length} shell alignment failure(s):\n  ${failures.join('\n  ')}`);
    console.log(`Shell top-alignment invariants passed across ${ROUTES.length} routes at 1600x1000, 1440x900, and 390x844.`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().then(() => process.exit(0), err => { console.error(err.message); process.exit(1); });
