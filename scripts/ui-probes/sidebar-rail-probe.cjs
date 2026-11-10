// Collapsed-sidebar rail check (HOJA-209).
//
// Builds nothing: run `cd frontend && npm run build` first. Seeds a throwaway
// SQLite database, starts hoja-serve with the built frontend, then collapses the
// sidebar at a desktop viewport and asserts the rail navigates:
//   - every nav destination is present as an icon with title + aria-label
//   - clicking an icon navigates while the sidebar stays collapsed
//   - the active destination is visible and carries aria-current="page"
//   - workspace switcher, create-issue and log out are reachable
//   - nothing inside the rail is clipped or wider than the rail
//
// Exit 1 with the list of failures, so it works as a regression gate.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '../..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'sidebar-rail-'));
const db = path.join(dir, 'rail.db');
const token = 'hoja_sidebar_rail_token';
const py = path.join(repo, '.venv/bin/python');
// Connector packages are not installed in the worktree venv; the caller can point
// HOJA_PROBE_PYTHONPATH at a directory that has them (see the PR notes).
const extraPath = [repo, process.env.HOJA_PROBE_PYTHONPATH].filter(Boolean).join(path.delimiter);

const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: extraPath }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 18800 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'),
  ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: extraPath, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

const RAIL = 44;

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    await page.goto(`${base}/`);
    await page.waitForLoadState('load');
    await wait(1200);

    const problems = [];
    const expanded = await page.evaluate(() => [...document.querySelectorAll('.sidebar .nav-item')]
      .filter(el => el.getBoundingClientRect().width > 0)
      .map(el => (el.getAttribute('aria-label') || el.textContent).trim()));
    assert.ok(expanded.length >= 8, `expected the expanded sidebar to list destinations, got ${expanded.length}`);

    await page.click('.sidebar-toggle');
    await wait(400);

    const state = await page.evaluate((rail) => {
      const sidebar = document.querySelector('.sidebar');
      const app = document.querySelector('.app');
      const sr = sidebar.getBoundingClientRect();
      const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
      const items = [...document.querySelectorAll('.sidebar .nav-item')].map(el => ({
        label: (el.getAttribute('aria-label') || '').trim(),
        title: (el.getAttribute('title') || '').trim(),
        text: (el.querySelector('span')?.getBoundingClientRect().width || 0),
        visible: visible(el),
        width: el.getBoundingClientRect().width,
        current: el.getAttribute('aria-current'),
      }));
      const clipped = [...sidebar.querySelectorAll('*')]
        .filter(visible)
        .filter(el => { const r = el.getBoundingClientRect(); return r.right > sr.right + 1 || r.left < sr.left - 1; })
        .map(el => el.className && typeof el.className === 'string' ? el.className : el.tagName);
      const overflow = [...sidebar.querySelectorAll('*')]
        .filter(visible)
        .map(el => el.scrollWidth - el.clientWidth)
        .filter(diff => diff > 1);
      return {
        collapsed: app.classList.contains('sidebar-collapsed'),
        railWidth: sr.width,
        items,
        clipped,
        overflow,
        sidebarScrolls: sidebar.scrollWidth > sidebar.clientWidth + 1,
        toggle: (() => { const t = document.querySelector('.sidebar-toggle'); return { expanded: t.getAttribute('aria-expanded'), controls: t.getAttribute('aria-controls') }; })(),
        workspace: visible(document.querySelector('.workspace-trigger')),
        create: visible(document.querySelector('.sidebar-create')),
        logout: visible(document.querySelector('.logout-button')),
        teams: [...document.querySelectorAll('.team-row .team-name')].filter(visible).length,
        navVisible: visible(document.querySelector('.sidebar nav')),
        innerWidth: sr.width - parseFloat(getComputedStyle(sidebar).paddingLeft) - parseFloat(getComputedStyle(sidebar).paddingRight),
      };
    }, RAIL);

    if (!state.collapsed) problems.push('sidebar is not in collapsed mode after the toggle');
    if (Math.abs(state.railWidth - RAIL) > 1) problems.push(`rail is ${state.railWidth}px, expected ${RAIL}px`);
    if (!state.navVisible) problems.push('navigation is hidden while collapsed');
    const shown = state.items.filter(i => i.visible);
    if (shown.length < 8) problems.push(`only ${shown.length} nav items render on the rail`);
    for (const item of shown) {
      if (!item.label) problems.push(`nav item "${item.title || '?'}" has no aria-label`);
      if (!item.title) problems.push(`nav item "${item.label || '?'}" has no title tooltip`);
      if (item.text > 1) problems.push(`nav item "${item.label}" still shows its text label (${Math.round(item.text)}px)`);
      if (item.width > state.innerWidth + 1) problems.push(`nav item "${item.label}" is ${Math.round(item.width)}px wide inside a ${Math.round(state.innerWidth)}px rail`);
    }
    const missing = expanded.filter(label => !shown.some(i => i.label === label));
    if (missing.length) problems.push(`destinations missing from the rail: ${missing.join(', ')}`);
    if (!shown.some(i => i.current === 'page')) problems.push('no visible nav item carries aria-current="page"');
    if (!state.workspace) problems.push('workspace switcher is unreachable on the rail');
    if (!state.create) problems.push('create-issue control is unreachable on the rail');
    if (!state.logout) problems.push('log out is unreachable on the rail');
    if (state.teams < 1) problems.push('team icons are missing from the rail');
    if (state.clipped.length) problems.push(`elements outside the rail: ${state.clipped.join(', ')}`);
    if (state.overflow.length) problems.push(`elements clipped inside the rail: ${state.overflow.length}`);
    if (state.sidebarScrolls) problems.push('the rail scrolls horizontally');
    if (state.toggle.expanded !== 'false') problems.push(`toggle aria-expanded is ${state.toggle.expanded}`);
    if (state.toggle.controls !== 'workspace-sidebar') problems.push(`toggle aria-controls is ${state.toggle.controls}`);

    // Clicking must navigate without expanding.
    const target = shown.find(i => i.label && i.label !== 'More');
    if (target) {
      const before = page.url();
      await page.click(`.sidebar .nav-item[aria-label="${target.label}"]`);
      await wait(600);
      const after = page.url();
      const stillCollapsed = await page.evaluate(() => document.querySelector('.app').classList.contains('sidebar-collapsed'));
      if (after === before) problems.push(`clicking the "${target.label}" icon did not navigate`);
      if (!stillCollapsed) problems.push('clicking a nav icon expanded the sidebar');
    }

    // A team icon goes to that team's home, not to an invisible sub-list.
    const teamName = await page.evaluate(() => {
      const el = document.querySelector('.team-row .team-name');
      return el ? el.getAttribute('aria-label') : null;
    });
    if (teamName) {
      await page.click(`.team-row .team-name[aria-label="${teamName}"]`);
      await wait(600);
      if (!/\/workspace\/team\//.test(page.url())) problems.push(`team icon did not navigate (url ${page.url()})`);
    }

    // Expanding again must restore the labels.
    await page.click('.sidebar-toggle');
    await wait(300);
    const restored = await page.evaluate(() => [...document.querySelectorAll('.sidebar .nav-item span')]
      .some(s => s.getBoundingClientRect().width > 1));
    if (!restored) problems.push('expanding the sidebar did not restore the labels');

    await ctx.close();
    assert.ok(problems.length === 0, `${problems.length} rail failure(s):\n  ${problems.join('\n  ')}`);
    console.log(`Sidebar rail: ${shown.length} destinations + ${state.teams} team icon(s) reachable at ${RAIL}px, active item marked, nothing clipped`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(err => { console.error(err.message || err); process.exitCode = 1; });
