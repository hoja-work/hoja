// Collapsed-sidebar rail regression gate (HOJA-209 / HOJA-739).
//
// Builds nothing: run `cd frontend && npm run build` first. Seeds a throwaway
// SQLite database (with demo issues and an unread inbox notification), starts
// hoja-serve with the built frontend, then exercises the collapsed 44px desktop
// rail in light and dark mode plus phone drawer fallback:
//   - every nav destination and team is present as a 28x28 icon with aria-label
//   - hovering a collapsed rail control reveals a Radix tooltip on the right
//   - unread inbox badge collapses to a compact dot without clipping or shifting
//   - collapsing a section while expanded still keeps its icons in rail mode
//   - clicking an icon navigates while the sidebar stays collapsed
//   - clicking a team icon navigates to that team's home and marks aria-current
//   - workspace switcher and More popovers open cleanly to the right (not clipped)
//   - dark mode create button & team badge remain properly themed
//   - phone drawer ignores desktop collapsed state and renders full labels
//   - expanding again restores labels and expanded width
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
const extraPath = [repo, process.env.HOJA_PROBE_PYTHONPATH].filter(Boolean).join(path.delimiter);

const seed = spawnSync(py, ['-c', `from datetime import datetime, timezone
from hoja_server.db import inbox_notification, issue, make_engine, migrate, seed_tenant
from sqlalchemy import select
e = make_engine(${JSON.stringify(db)}); migrate(e)
seeded = seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
with e.begin() as conn:
    first_issue = conn.execute(select(issue.c.id).limit(1)).scalar_one()
    conn.execute(inbox_notification.insert().values(
        id='notif-rail-1',
        organization_id=seeded['org_id'],
        recipient_id=seeded['user_id'],
        issue_id=first_issue,
        actor_id=seeded['user_id'],
        kind='assigned',
        created_at=datetime.now(timezone.utc).isoformat(),
    ))
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
    await page.waitForSelector('[data-workspace-sidebar]');
    await wait(1200);

    const problems = [];
    const expanded = await page.evaluate(() => [...document.querySelectorAll('[data-workspace-sidebar] [data-nav-item]')]
      .filter(el => el.getBoundingClientRect().width > 0)
      .map(el => (el.getAttribute('aria-label') || el.textContent).trim()));
    assert.ok(expanded.length >= 8, `expected the expanded sidebar to list destinations, got ${expanded.length}`);

    // Collapse a section ("Workspace") while expanded; its icons must still stay reachable in rail mode.
    await page.click('[data-nav-group-toggle]:has-text("Workspace")');
    await wait(150);

    await page.click('[data-sidebar-toggle]');
    await wait(400);

    const state = await page.evaluate(() => {
      const sidebar = document.querySelector('[data-workspace-sidebar]');
      const app = document.querySelector('.app');
      const sr = sidebar.getBoundingClientRect();
      const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
      const items = [...document.querySelectorAll('[data-workspace-sidebar] [data-nav-item]')].map(el => ({
        label: (el.getAttribute('aria-label') || '').trim(),
        textW: (el.querySelector('span')?.getBoundingClientRect().width || 0),
        visible: visible(el),
        width: el.getBoundingClientRect().width,
        height: el.getBoundingClientRect().height,
        current: el.getAttribute('aria-current'),
        bg: getComputedStyle(el).backgroundColor,
      }));
      const clipped = [...sidebar.querySelectorAll('*')]
        .filter(visible)
        .filter(el => { const r = el.getBoundingClientRect(); return r.right > sr.right + 1 || r.left < sr.left - 1; })
        .map(el => el.className && typeof el.className === 'string' ? el.className : el.tagName);
      const overflow = [...sidebar.querySelectorAll('*')]
        .filter(visible)
        .map(el => ({ el: `${el.tagName}:${el.className || ''}:${el.getAttribute('data-inbox-badge') ?? ''}`, diff: el.scrollWidth - el.clientWidth }))
        .filter(x => x.diff > 1);
      const badge = document.querySelector('[data-inbox-badge]');
      const badgeRect = badge ? badge.getBoundingClientRect() : null;
      const toggle = document.querySelector('[data-sidebar-toggle]');
      const toggleIcon = toggle?.querySelector('svg');
      const tr = toggle?.getBoundingClientRect();
      const ir = toggleIcon?.getBoundingClientRect();
      return {
        collapsed: app.hasAttribute('data-sidebar-collapsed'),
        railWidth: sr.width,
        items,
        clipped,
        overflow,
        sidebarScrolls: sidebar.scrollWidth > sidebar.clientWidth + 1,
        toggle: {
          expanded: toggle?.getAttribute('aria-expanded'),
          controls: toggle?.getAttribute('aria-controls'),
          centered: Boolean(tr && ir && Math.abs((ir.left + ir.width / 2) - (tr.left + tr.width / 2)) <= 1.5 && Math.abs((ir.top + ir.height / 2) - (tr.top + tr.height / 2)) <= 1.5),
        },
        workspace: visible(document.querySelector('[data-workspace-trigger]')),
        create: visible(document.querySelector('[data-sidebar-create]')),
        teams: [...document.querySelectorAll('[data-team-row] [data-team-name]')].filter(visible).length,
        navVisible: visible(document.querySelector('[data-workspace-sidebar] nav')),
        badge: badgeRect ? { w: badgeRect.width, h: badgeRect.height, right: badgeRect.right, srRight: sr.right } : null,
        innerWidth: sr.width - parseFloat(getComputedStyle(sidebar).paddingLeft) - parseFloat(getComputedStyle(sidebar).paddingRight),
      };
    });

    if (!state.collapsed) problems.push('sidebar is not in collapsed mode after the toggle');
    if (Math.abs(state.railWidth - RAIL) > 1) problems.push(`rail is ${state.railWidth}px, expected ${RAIL}px`);
    if (!state.navVisible) problems.push('navigation is hidden while collapsed');
    const shown = state.items.filter(i => i.visible);
    if (shown.length < 8) problems.push(`only ${shown.length} nav items render on the rail`);
    for (const item of shown) {
      if (!item.label) problems.push('nav item has no aria-label');
      if (item.textW > 1) problems.push(`nav item "${item.label}" still shows its text label (${Math.round(item.textW)}px)`);
      if (Math.abs(item.width - 28) > 1 || Math.abs(item.height - 28) > 1) problems.push(`nav item "${item.label}" is ${Math.round(item.width)}x${Math.round(item.height)}, expected 28x28`);
      if (item.width > state.innerWidth + 1) problems.push(`nav item "${item.label}" is ${Math.round(item.width)}px wide inside a ${Math.round(state.innerWidth)}px rail`);
    }
    const missing = expanded.filter(label => !shown.some(i => i.label === label));
    if (missing.length) problems.push(`destinations missing from the rail: ${missing.join(', ')}`);
    const currentItem = shown.find(i => i.current === 'page');
    const inactiveItem = shown.find(i => i.current !== 'page');
    if (!currentItem) problems.push('no visible nav item carries aria-current="page"');
    else if (inactiveItem && currentItem.bg === inactiveItem.bg) problems.push('active rail item does not have distinct background');
    if (!state.workspace) problems.push('workspace switcher is unreachable on the rail');
    if (!state.create) problems.push('create-issue control is unreachable on the rail');
    if (!state.toggle.centered) problems.push('sidebar toggle icon is not centered');
    if (state.teams < 1) problems.push('team icons are missing from the rail');
    if (!state.badge || state.badge.w > 10 || state.badge.h > 10 || state.badge.right > state.badge.srRight) {
      problems.push(`unread inbox badge invalid in rail mode: ${JSON.stringify(state.badge)}`);
    }
    if (state.clipped.length) problems.push(`elements outside the rail: ${state.clipped.join(', ')}`);
    if (state.overflow.length) problems.push(`elements clipped inside the rail: ${JSON.stringify(state.overflow)}`);
    if (state.sidebarScrolls) problems.push('the rail scrolls horizontally');
    if (state.toggle.expanded !== 'false') problems.push(`toggle aria-expanded is ${state.toggle.expanded}`);
    if (state.toggle.controls !== 'workspace-sidebar') problems.push(`toggle aria-controls is ${state.toggle.controls}`);

    // Hovering a collapsed rail icon shows its tooltip on the right.
    await page.hover('[data-workspace-sidebar] [data-nav-item][aria-label="Inbox"]');
    await wait(350);
    const tip = await page.evaluate(() => {
      const t = document.querySelector('[role="tooltip"]');
      if (!t) return null;
      const r = t.getBoundingClientRect();
      return { text: t.textContent?.trim() || '', x: r.x };
    });
    if (!tip || !tip.text.includes('Inbox') || tip.x < RAIL) problems.push(`collapsed hover tooltip failed: ${JSON.stringify(tip)}`);

    // Workspace switcher menu opens to the right at full readable width, not clipped to 28px.
    await page.click('[data-workspace-trigger]');
    await wait(250);
    const wsMenu = await page.evaluate(() => {
      const m = document.querySelector('#workspace-switcher-menu');
      if (!m) return null;
      const r = m.getBoundingClientRect();
      return { x: r.x, w: r.width, h: r.height };
    });
    if (!wsMenu || wsMenu.w < 200 || wsMenu.x < RAIL) problems.push(`collapsed workspace menu clipped or misplaced: ${JSON.stringify(wsMenu)}`);
    await page.keyboard.press('Escape');
    await wait(150);

    // More menu opens to the right cleanly.
    await page.click('[data-workspace-sidebar] [data-nav-item][aria-label="Workspace More"]');
    await wait(250);
    const moreMenu = await page.evaluate(() => {
      const m = document.querySelector('#workspace-more-menu');
      if (!m) return null;
      const r = m.getBoundingClientRect();
      return { x: r.x, w: r.width };
    });
    if (!moreMenu || moreMenu.x < RAIL) problems.push(`collapsed More menu misplaced: ${JSON.stringify(moreMenu)}`);
    await page.keyboard.press('Escape');
    await wait(150);

    // Clicking must navigate without expanding.
    const target = shown.find(i => i.label && i.label !== 'Workspace More' && i.current !== 'page');
    if (target) {
      const before = page.url();
      await page.click(`[data-workspace-sidebar] [data-nav-item][aria-label="${target.label}"]`);
      await wait(500);
      const after = page.url();
      const stillCollapsed = await page.evaluate(() => document.querySelector('.app').hasAttribute('data-sidebar-collapsed'));
      if (after === before) problems.push(`clicking the "${target.label}" icon did not navigate`);
      if (!stillCollapsed) problems.push('clicking a nav icon expanded the sidebar');
    }

    // A team icon goes to that team's home and marks aria-current.
    const teamName = await page.getAttribute('[data-team-row] [data-team-name]', 'aria-label');
    if (teamName) {
      await page.click(`[data-team-row] [data-team-name][aria-label="${teamName}"]`);
      await wait(500);
      if (!/\/workspace\/team\/trebol\/home$/.test(page.url())) problems.push(`team icon did not navigate (url ${page.url()})`);
      if ((await page.getAttribute('[data-team-row] [data-team-name]', 'aria-current')) !== 'page') problems.push('active team icon missing aria-current="page"');
    }

    // Dark mode: create button must switch off light #f9f9fa, and team badge ink must remain light.
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    await wait(150);
    const dark = await page.evaluate(() => ({
      createBg: getComputedStyle(document.querySelector('[data-sidebar-create]')).backgroundColor,
      teamInk: getComputedStyle(document.querySelector('[data-team-name] b')).color,
    }));
    if (dark.createBg === 'rgb(249, 249, 250)') problems.push(`create button kept light background in dark mode: ${dark.createBg}`);
    if (dark.teamInk === 'rgb(32, 32, 35)') problems.push(`team icon kept dark ink in dark mode: ${dark.teamInk}`);

    // Phone viewport with collapsed preference still shows the full drawer and downward workspace menu.
    await page.setViewportSize({ width: 390, height: 844 });
    await wait(250);
    await page.click('[aria-label="Open navigation"]');
    await wait(250);
    const phoneLabels = await page.evaluate(() => [...document.querySelectorAll('[data-workspace-sidebar] [data-nav-item] > span')].filter(s => s.getBoundingClientRect().width > 1).length);
    if (phoneLabels < 7) problems.push(`phone drawer hid labels when desktop rail was collapsed (${phoneLabels} visible)`);
    await page.click('[data-workspace-trigger]');
    await wait(200);
    const phoneWsMenu = await page.evaluate(() => {
      const m = document.querySelector('#workspace-switcher-menu');
      if (!m) return null;
      const r = m.getBoundingClientRect();
      return { x: r.x, w: r.width };
    });
    if (!phoneWsMenu || phoneWsMenu.w < 200 || phoneWsMenu.x > 60) problems.push(`phone workspace menu was displaced: ${JSON.stringify(phoneWsMenu)}`);
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1440, height: 900 });
    await wait(250);

    // Expanding again must restore the labels and expanded width.
    await page.click('[data-sidebar-toggle]');
    await wait(300);
    const restored = await page.evaluate(() => ({
      width: document.querySelector('[data-workspace-sidebar]').getBoundingClientRect().width,
      labels: [...document.querySelectorAll('[data-workspace-sidebar] [data-nav-item] > span')].some(s => s.getBoundingClientRect().width > 1),
    }));
    if (!restored.labels || restored.width < 200) problems.push(`expanding the sidebar failed: ${JSON.stringify(restored)}`);

    await ctx.close();
    assert.ok(problems.length === 0, `${problems.length} rail failure(s):\n  ${problems.join('\n  ')}`);
    console.log(`Sidebar rail: ${shown.length} destinations + ${state.teams} team icon(s) + unread dot + tooltips + popovers + dark mode + phone fallback verified at ${RAIL}px`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(err => { console.error(err.message || err); process.exitCode = 1; });
