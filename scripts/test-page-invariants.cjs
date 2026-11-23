// Run from repo root after `cd frontend && npm run build`: node scripts/test-page-invariants.cjs
// Audits every sidebar page (read from frontend/src/main.tsx) plus detail routes against a seeded backend,
// at 320, 390, and 1440px. New sidebar entries are audited automatically.
// Invariants: no horizontal page scroll; no control outside the viewport; no browser-default (inset/outset) field or button
// border, which is what an unstyled library component looks like; no broken images.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const repoScratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(repoScratch, { recursive: true });
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const dir = fs.mkdtempSync(path.join(repoScratch, 'page-invariants-'));
const db = path.join(dir, 'pages.db');
const token = 'hoja_page_invariant_token';
const py = path.join(repo, '.venv/bin/python');
const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 18900 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

async function gql(query) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query }) });
  return (await res.json()).data;
}

const invariants = () => {
  const W = window.innerWidth, out = [];
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const name = el => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 60);
  if (document.documentElement.scrollWidth > W + 1) out.push(`page scrolls horizontally (${document.documentElement.scrollWidth}px at ${W}px)`);
  // Sidebar pages use the shared main content container: an unrounded .main gap surface
  // with a rounded, bordered shell flush to the sidebar and inset on the other edges.
  const main = document.querySelector('.main');
  const shell = document.querySelector('[data-workspace-page]');
  if (main && shell) {
    const mr = main.getBoundingClientRect(), sr = shell.getBoundingClientRect();
    const ms = getComputedStyle(main), ss = getComputedStyle(shell);
    const topbar = document.querySelector('[data-topbar]');
    const topAnchor = topbar && visible(topbar) ? topbar.getBoundingClientRect().bottom : 0;
    if (Math.abs(sr.left - mr.left) > 1) out.push(`content shell has a left gap (${Math.round(sr.left - mr.left)}px)`);
    if (sr.top - topAnchor > 16) out.push(`content shell has a top gap (${Math.round(sr.top - topAnchor)}px)`);
    if (sr.right > mr.right + 1) out.push('content shell overflows the main surface');
    if (sr.width < 1 || sr.height < 1) out.push('content shell has no size');
    if (ms.borderTopLeftRadius !== '0px') out.push(`main gap surface is rounded (${ms.borderTopLeftRadius})`);
    if (parseFloat(ss.borderTopLeftRadius) <= 0) out.push('content shell is not rounded');
    if (parseFloat(ss.borderTopWidth) <= 0) out.push('content shell has no border');
  }
  // A control inside an overflow-scrolling ancestor (tab rows, kanban lanes) is reachable by scrolling that container; only the page itself must not scroll.
  const clipped = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
  for (const el of document.querySelectorAll('button,a[href],input,select,textarea,[role=button]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (r.right > W + 1 && !clipped(el)) out.push(`control outside viewport: "${name(el)}" ends at ${Math.round(r.right)}px`);
    if (el.matches('input,select,textarea') && s.borderTopStyle === 'inset') out.push(`unstyled field (browser default border): "${name(el)}"`);
    if (el.matches('button') && s.borderTopStyle === 'outset') out.push(`unstyled button (browser default border): "${name(el)}"`);
  }
  for (const img of document.images) if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) out.push(`broken image ${img.getAttribute('src')}`);
  return out;
};

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    const issues = (await gql('query{issues(first:3){nodes{identifier}}}')).issues.nodes.map(n => n.identifier);
    const created = await gql('mutation{projectCreate(input:{name:"Launch audit",teamIds:[]}){success project{id}}}');
    const projectId = created.projectCreate?.project?.id;
    assert.ok(issues.length && projectId, 'seed data missing');

    const src = fs.readFileSync(path.join(repo, 'frontend/src/main.tsx'), 'utf8');
    const sectionMatch = src.match(/const sections\s*=\s*\[[\s\S]*?\]\s*(?:as const)?;/);
    assert.ok(sectionMatch, 'sidebar sections not found in main.tsx');
    const labels = [...sectionMatch[0].matchAll(/\[\s*['"]([^'"]+)['"]\s*,\s*[A-Za-z][A-Za-z0-9]*\s*\]/g)].map(m => m[1]);
    assert.ok(labels.length >= 7 && labels.includes('Projects'), `expected sidebar entries including Projects, found ${labels.length}: ${labels.join(', ')}`);
    const details = [`/workspace/issue/${issues[0]}`, `/workspace/project/${projectId}`, '/workspace/members'];
    const extraCss = process.env.PAGE_EXTRA_CSS ? fs.readFileSync(process.env.PAGE_EXTRA_CSS, 'utf8') : '';

    browser = await chromium.launch({ headless: true });
    const failures = [];
    let audited = 0;
    for (const width of [320, 390, 1440]) {
      const ctx = await browser.newContext({ viewport: { width, height: 800 } });
      await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      // Exercise the actual workspace sidebar, not a synthetic fixture. Layout
      // regressions must fail even when every destination is still clickable.
      const navProbe = await ctx.newPage();
      await navProbe.goto(`${base}/`);
      await navProbe.waitForSelector('[data-workspace-sidebar]');
      const sidebarGeometry = () => {
        const el = document.querySelector('[data-workspace-sidebar]');
        const rect = el.getBoundingClientRect(), css = getComputedStyle(el);
        return {left: rect.left, right: rect.right, width: rect.width, height: rect.height,
          display: css.display, direction: css.flexDirection, position: css.position,
          overflow: css.overflowY, rem: parseFloat(getComputedStyle(document.documentElement).fontSize)};
      };
      const navigationColors = await navProbe.locator('[aria-label="Workspace navigation"] [data-nav-item]').evaluateAll(nodes => nodes.map(node => ({
        current: node.getAttribute('aria-current') === 'page',
        background: getComputedStyle(node).backgroundColor,
        color: getComputedStyle(node).color,
      })));
      assert.equal(navigationColors.filter(item => item.current).length, 1, 'one sidebar destination is current');
      assert.notEqual(navigationColors.find(item => item.current).background,
        navigationColors.find(item => !item.current).background, 'current and inactive sidebar destinations differ');
      let nav = await navProbe.evaluate(sidebarGeometry);
      assert.equal(nav.display, 'flex', `${width}px sidebar is a flex layout`);
      assert.equal(nav.direction, 'column', `${width}px sidebar stacks navigation`);
      assert.equal(nav.overflow, 'auto', `${width}px navigation remains scrollable`);
      if (width >= 1001) {
        assert.ok(nav.width >= 200 && nav.left >= 0 && nav.height >= 700, 'desktop rail has usable geometry');
        await navProbe.getByRole('button', {name: 'Collapse sidebar', exact: true}).click();
        await wait(250);
        nav = await navProbe.evaluate(sidebarGeometry);
        assert.ok(Math.abs(nav.width - 44) < 1, 'collapsed rail preserves original 44px width');
        assert.equal(await navProbe.locator('[data-workspace-sidebar] [data-nav-item] > span').first().isVisible(), false,
          'collapsed rail hides destination labels');
        assert.equal(await navProbe.locator('[data-workspace-sidebar] [data-nav-group-toggle]').first().isVisible(), false,
          'collapsed rail hides group toggles');
        assert.equal(await navProbe.locator('[data-workspace-sidebar] [data-team-nav-links]').first().isVisible(), false,
          'collapsed rail hides team sublinks');
        await navProbe.getByRole('button', {name: 'Expand sidebar', exact: true}).click();
        await wait(250);
        assert.ok((await navProbe.evaluate(sidebarGeometry)).width >= 200, 'expanded rail restores its width');
      } else {
        assert.equal(nav.position, 'fixed', 'phone drawer is fixed');
        assert.ok(nav.right <= 1, 'closed phone drawer is off canvas');
        await navProbe.getByRole('button', {name: 'Open navigation', exact: true}).click();
        await wait(250);
        nav = await navProbe.evaluate(sidebarGeometry);
        assert.ok(nav.left >= -1 && nav.width >= 200 && nav.right <= width + 1, `open phone drawer stays inside viewport: ${JSON.stringify(nav)}`);
        await navProbe.keyboard.press('Escape');
        await wait(250);
        assert.ok((await navProbe.evaluate(sidebarGeometry)).right <= 1, 'phone drawer closes off canvas');
      }
      if (width < 1001) {
        await navProbe.getByRole('button', {name: 'Open navigation', exact: true}).click();
        await wait(250);
      }
      await navProbe.locator('[data-workspace-trigger]').click();
      const menuItem = navProbe.locator('#workspace-switcher-menu [role="menuitem"]').first();
      await menuItem.waitFor();
      const menuGeometry = await menuItem.evaluate(node => {
        const style = getComputedStyle(node), parent = node.parentElement.getBoundingClientRect();
        return {display: style.display, padding: parseFloat(style.paddingLeft), radius: parseFloat(style.borderRadius),
          width: node.getBoundingClientRect().width, panelWidth: parent.width};
      });
      assert.equal(menuGeometry.display, 'flex', 'workspace menu items use flex alignment');
      assert.ok(menuGeometry.padding > 0 && menuGeometry.radius > 0, 'workspace menu items retain padding and rounding');
      assert.ok(menuGeometry.width > menuGeometry.panelWidth - 24, 'workspace menu items fill the panel width');
      const menuBackground = await menuItem.evaluate(node => getComputedStyle(node).backgroundColor);
      await menuItem.hover();
      await wait(180);
      assert.notEqual(await menuItem.evaluate(node => getComputedStyle(node).backgroundColor), menuBackground,
        'workspace menu items show a hover background');
      await navProbe.keyboard.press('Escape');
      assert.equal(await navProbe.locator('#workspace-switcher-menu').count(), 0, 'workspace menu dismisses on Escape');
      if (width < 1001 && !(await navProbe.locator('[data-workspace-sidebar]').getAttribute('data-open'))) {
        await navProbe.getByRole('button', {name: 'Open navigation', exact: true}).click();
        await wait(250);
      }
      await navProbe.getByRole('button', {name: 'Workspace More', exact: true}).click();
      const more = navProbe.locator('#workspace-more-menu');
      await more.waitFor();
      const moreBox = await more.boundingBox();
      assert.ok(moreBox && moreBox.y >= 0 && moreBox.x >= 0 && moreBox.x + moreBox.width <= width + 1,
        'More menu is positioned inside the viewport');
      await navProbe.keyboard.press('Escape');
      await navProbe.close();
      const tabsProbe = await ctx.newPage();
      await tabsProbe.goto(`${base}/workspace/my-issues/assigned`);
      await tabsProbe.waitForSelector('[aria-label="My task views"] [role="tab"][aria-selected="true"]');
      const tabAppearance = async () => tabsProbe.locator('[aria-label="My task views"] [role="tab"]').evaluateAll(nodes => nodes.map(node => ({
        selected: node.getAttribute('aria-selected') === 'true',
        background: getComputedStyle(node).backgroundColor,
        color: getComputedStyle(node).color,
      })));
      let tabs = await tabAppearance();
      assert.equal(tabs.filter(tab => tab.selected).length, 1, 'one My tasks tab is selected');
      assert.notEqual(tabs.find(tab => tab.selected).background, tabs.find(tab => !tab.selected).background,
        'selected and inactive My tasks tabs have distinct backgrounds');
      await tabsProbe.getByRole('tab', {name: 'Created', exact: true}).click();
      tabs = await tabAppearance();
      assert.equal(tabs.filter(tab => tab.selected).length, 1, 'tab change keeps a single selection');
      assert.notEqual(tabs.find(tab => tab.selected).background, tabs.find(tab => !tab.selected).background,
        'selected appearance follows the clicked My tasks tab');
      const persistentState = async (page, control, state, attribute) => {
        await control.waitFor();
        await page.mouse.move(0, 0);
        const before = await control.evaluate(node => ({background: getComputedStyle(node).backgroundColor, color: getComputedStyle(node).color}));
        await control.click();
        await page.mouse.move(0, 0);
        await wait(180);
        assert.equal(await control.getAttribute(attribute), state, `${attribute} state activates`);
        const after = await control.evaluate(node => ({background: getComputedStyle(node).backgroundColor, color: getComputedStyle(node).color}));
        assert.notDeepEqual(after, before, `${attribute} active appearance persists after pointer leaves`);
      };
      await persistentState(tabsProbe, tabsProbe.getByRole('button', {name: 'Add filter', exact: true}), 'true', 'aria-expanded');
      await tabsProbe.keyboard.press('Escape');
      await persistentState(tabsProbe, tabsProbe.getByRole('button', {name: 'Display options', exact: true}), 'true', 'aria-expanded');
      const displayPanel = tabsProbe.locator('[data-my-issue-popover][aria-label="Display options"]');
      await displayPanel.waitFor();
      const panelGeometry = await displayPanel.evaluate(node => {
        const style = getComputedStyle(node), rect = node.getBoundingClientRect();
        const labels = [...node.querySelectorAll('label')].map(label => label.getBoundingClientRect());
        return {display: style.display, position: style.position, zIndex: Number(style.zIndex), overflow: style.overflowY,
          left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, height: rect.height,
          stacked: labels.length > 1 && labels[1].top >= labels[0].bottom - 1};
      });
      assert.equal(panelGeometry.display, 'grid', 'Display options is a grid panel, not a horizontal button');
      assert.ok(['absolute', 'fixed'].includes(panelGeometry.position), 'Display options is anchored out of flow');
      assert.ok(panelGeometry.zIndex > 0, 'Display options paints above page content');
      assert.equal(panelGeometry.overflow, 'auto', 'Display options can scroll');
      assert.ok(panelGeometry.left >= 0 && panelGeometry.right <= width + 1 && panelGeometry.top >= 0 && panelGeometry.bottom <= 801,
        'Display options stays inside the viewport');
      assert.ok(panelGeometry.height > 150 && panelGeometry.stacked, 'Display options controls stack vertically');
      await displayPanel.evaluate(node => { node.style.maxHeight = '140px'; node.scrollTop = node.scrollHeight; });
      assert.ok(await displayPanel.evaluate(node => node.scrollTop > 0), 'Display options final controls are reachable by scrolling');
      await tabsProbe.keyboard.press('Escape');
      assert.equal(await displayPanel.count(), 0, 'Display options dismisses on Escape');
      await tabsProbe.goto(`${base}/workspace/inbox`);
      await persistentState(tabsProbe, tabsProbe.locator('[data-inbox-head-actions] button[aria-pressed]'), 'true', 'aria-pressed');
      await tabsProbe.close();
      const check = async (page, what) => {
        await page.waitForLoadState('load');
        await wait(900);
        if (extraCss) await page.addStyleTag({ content: extraCss });
        const problems = await page.evaluate(invariants);
        if (what.startsWith('page ') && await page.locator('[data-workspace-page]').count() === 0) problems.push('sidebar page is missing the main content container shell');
        // Meetings owns the desktop height; on phones the header stays as the navigation drawer toggle.
        if (what.includes('page "Meetings"')) {
          const topbar = await page.evaluate(() => { const t = document.querySelector('[data-topbar]'); return t ? getComputedStyle(t).display : 'none'; });
          if (width >= 1001 && topbar !== 'none') problems.push('desktop Meetings keeps the app header');
          if (width <= 700 && topbar === 'none') problems.push('phone Meetings loses the navigation header');
        }
        audited++;
        for (const p of problems) failures.push(`${width}px ${what}: ${p}`);
      };
      for (const label of labels) {
        const page = await ctx.newPage();
        await page.goto(`${base}/`);
        const pattern = new RegExp('^\\s*' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*$');
        const item = page.locator('[aria-label="Workspace navigation"] [data-nav-item]').filter({ hasText: pattern }).first();
        const drawerOpen = await page.evaluate(() => { const s = document.querySelector('[data-workspace-sidebar]'); return !s || s.getBoundingClientRect().left >= 0; });
        if (!drawerOpen) await page.click('button[data-mobile-nav-toggle][aria-expanded="false"]');
        await page.waitForTimeout(350);
        await item.click({ timeout: 10000 }).catch(() => failures.push(`${width}px nav "${label}": not reachable from sidebar`));
        await check(page, `page "${label}"`);
        await page.close();
      }
      for (const url of details) {
        const page = await ctx.newPage();
        await page.goto(base + url);
        await check(page, `detail ${url}`);
        await page.close();
      }
      await ctx.close();
    }
    assert.ok(failures.length === 0, `${failures.length} invariant failure(s):\n  ${failures.join('\n  ')}`);
    console.log(`Page invariants: ${labels.length} sidebar pages + ${details.length} detail routes, ${audited} renders at 320/390/1440px passed`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().then(() => process.exit(0), error => { console.error(error.message); process.exit(1); });
