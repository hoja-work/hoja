#!/usr/bin/env node
// Settings end-to-end suite (HOJA-734).
//
// Runs against a live app (dev server or production build) with a real backend:
//   SETTINGS_E2E_URL=http://127.0.0.1:5834 SETTINGS_E2E_ADMIN_TOKEN=... SETTINGS_E2E_MEMBER_TOKEN=... \
//     SETTINGS_E2E_DB=<the stack's SQLite file> node scripts/settings-e2e/run-all.cjs [--only shell,labels] [--out <dir>]
//
// The admin token is a workspace admin's API key; the member token a non-admin's with no grants.
// SETTINGS_E2E_DB lets journeys seed rows the UI cannot create on a stack without
// HOJA_SECRET_KEY (an OAuth approval, a provider); without it those steps are skipped.
// Playwright and axe-core are read from app/local-e2e/node_modules (may be a symlink),
// app/frontend/node_modules or SETTINGS_E2E_NODE_MODULES.
//
// 1. Shell: every section in src/settings/registry.ts renders for an admin, admin-only sections
//    refuse a member, the shared sidebar navigates/searches/opens as a drawer, unknown ids 404.
// 2. Quality gates per section: axe (no serious/critical), no horizontal overflow at 320/390/1440,
//    the measured shell geometry (reference/linear/SETTINGS-SPEC.md), light and dark.
// 3. Behaviour: every scripts/settings-e2e/<section>.cjs `run()` journey (change → reload → read back
//    from the API, failure rollback, member lock-out).
// Exit code 0 only when everything passed. A JSON report is written to --out.
const fs = require('node:fs');
const path = require('node:path');

function resolve(name) {
  for (const base of [process.env.SETTINGS_E2E_NODE_MODULES, path.join(__dirname, '../../local-e2e/node_modules'),
    path.join(__dirname, '../../frontend/node_modules')].filter(Boolean)) {
    const p = path.join(base, name);
    if (fs.existsSync(p)) return p;
  }
  throw new Error(`${name} not found; set SETTINGS_E2E_NODE_MODULES to a node_modules holding it`);
}
const { chromium } = require(resolve('playwright'));
const axeSource = fs.readFileSync(path.join(resolve('axe-core'), 'axe.min.js'), 'utf8');

const arg = (flag) => { const i = process.argv.indexOf(flag); return i === -1 ? null : process.argv[i + 1]; };
const baseUrl = process.env.SETTINGS_E2E_URL || 'http://127.0.0.1:5834';
const adminToken = process.env.SETTINGS_E2E_ADMIN_TOKEN;
const memberToken = process.env.SETTINGS_E2E_MEMBER_TOKEN;
const only = (arg('--only') || '').split(',').filter(Boolean);
const outDir = arg('--out') || path.join(__dirname, '../../artifacts/settings-e2e');
if (!adminToken || !memberToken) { console.error('set SETTINGS_E2E_ADMIN_TOKEN and SETTINGS_E2E_MEMBER_TOKEN'); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });

const registrySrc = fs.readFileSync(path.join(__dirname, '../../frontend/src/settings/registry.ts'), 'utf8');
const SECTIONS = [...registrySrc.matchAll(/\{\s*id:\s*"([a-z-]+)",\s*label:\s*"([^"]+)"[^}]*?\}/g)].map((m) => ({
  id: m[1], label: m[2], admin: /admin:\s*true/.test(m[0]) || /capability:/.test(m[0]),
}));
// A member with no grants is refused both kinds of gated section; the copy differs.
const REFUSED = /Only workspace admins can open this page|You don't have access to this page/;
const WIDE = new Set(['billing', 'members']);
const passed = [], failed = [];
const ok = (m) => { passed.push(m); console.log('  ok   ' + m); };
const bad = (m) => { failed.push(m); console.log('  FAIL ' + m); };

async function open(browser, token, { width = 1440, height = 900, dark = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  page.dark = dark;
  return page;
}
async function gotoSection(page, id) {
  await page.goto(`${baseUrl}/workspace/settings/${id}`);
  await page.waitForSelector('[data-settings-page]', { timeout: 20000 });
  // Let lazy sections and their first read settle (skeleton → content).
  await page.waitForFunction(() => !document.querySelector('[data-settings-loading]'), null, { timeout: 15000 }).catch(() => {});
  if (page.dark) await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.waitForTimeout(150);
}
async function axe(page) {
  await page.addScriptTag({ content: axeSource });
  return page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    const r = await axe.run(document.querySelector('[data-settings-page]') || document, {
      resultTypes: ['violations'],
      rules: { region: { enabled: false } },
    });
    return r.violations.filter((v) => ['serious', 'critical'].includes(v.impact))
      .map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
  });
}

async function shell(browser) {
  console.log('\n# shell');
  // Every section renders for an admin, at desktop, in light and dark, accessibly, without overflow.
  for (const dark of [false, true]) {
    const page = await open(browser, adminToken, { dark });
    for (const s of SECTIONS) {
      const tag = `${s.id}${dark ? ' (dark)' : ''}`;
      try {
        await gotoSection(page, s.id);
        const h1 = (await page.locator('[data-settings-page] h1').first().innerText()).trim();
        h1 ? ok(`${tag}: renders "${h1}"`) : bad(`${tag}: no page title`);
        const current = await page.locator('#settings-navigation [aria-current="page"]').getAttribute('data-nav-id').catch(() => null);
        current === s.id ? ok(`${tag}: nav marks it current`) : bad(`${tag}: nav current is ${current}`);
        const v = await axe(page);
        v.length ? bad(`${tag}: axe ${v.join('; ')}`) : ok(`${tag}: axe clean (serious/critical)`);
        if (!dark) {
          const col = await page.locator('[data-settings-page]').boundingBox();
          const cap = WIDE.has(s.id) ? 1100 : 640;
          col && col.width <= cap + 1 ? ok(`${tag}: column ${Math.round(col.width)}px ≤ ${cap}`) : bad(`${tag}: column ${col && col.width}px > ${cap}`);
        }
        await page.screenshot({ path: path.join(outDir, `${s.id}-1440${dark ? '-dark' : ''}.png`) });
      } catch (e) { bad(`${tag}: ${e.message.split('\n')[0]}`); }
    }
    page.errors.length ? bad(`page errors: ${page.errors.slice(0, 3).join(' | ')}`) : ok(`no uncaught page errors (${dark ? 'dark' : 'light'})`);
    await page.context().close();
  }
  // Narrow widths: no horizontal overflow anywhere.
  for (const width of [320, 390]) {
    const page = await open(browser, adminToken, { width, height: 800 });
    for (const s of SECTIONS) {
      try {
        await gotoSection(page, s.id);
        const sw = await page.evaluate(() => document.documentElement.scrollWidth);
        sw <= width ? ok(`${s.id}@${width}: no horizontal overflow`) : bad(`${s.id}@${width}: scrollWidth ${sw}`);
        if (width === 390) await page.screenshot({ path: path.join(outDir, `${s.id}-390.png`), fullPage: true });
      } catch (e) { bad(`${s.id}@${width}: ${e.message.split('\n')[0]}`); }
    }
    await page.context().close();
  }
  // Geometry: one sidebar, persistent 244px at ≥881px, drawer below (SETTINGS-SPEC.md bisection).
  for (const [w, h] of [[1440, 900], [881, 900], [880, 900], [390, 844]]) {
    const page = await open(browser, adminToken, { width: w, height: h });
    await gotoSection(page, 'preferences');
    const asides = await page.locator('aside').count();
    asides === 1 ? ok(`@${w}: exactly one sidebar element`) : bad(`@${w}: ${asides} <aside> elements`);
    const box = await page.locator('#workspace-sidebar').boundingBox();
    if (w >= 881) box && Math.round(box.width) === 244 && box.x === 0 ? ok(`@${w}: sidebar persistent 244px`) : bad(`@${w}: sidebar ${JSON.stringify(box)}`);
    else {
      box && box.x + box.width <= 0 ? ok(`@${w}: sidebar is an off-canvas drawer`) : bad(`@${w}: drawer visible while closed ${JSON.stringify(box)}`);
      await page.getByRole('button', { name: 'Open navigation' }).click();
      await page.waitForTimeout(300);
      const opened = await page.locator('#workspace-sidebar').boundingBox();
      opened && opened.x >= 0 ? ok(`@${w}: drawer opens`) : bad(`@${w}: drawer did not open`);
      await page.getByRole('link', { name: 'Profile' }).click();
      await page.waitForURL(/settings\/profile/);
      await page.waitForTimeout(300);
      const closed = await page.locator('#workspace-sidebar').boundingBox();
      closed && closed.x + closed.width <= 0 ? ok(`@${w}: choosing a section closes the drawer`) : bad(`@${w}: drawer stayed open after navigation`);
      await page.getByRole('button', { name: 'Open navigation' }).click();
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      const esc = await page.locator('#workspace-sidebar').boundingBox();
      esc && esc.x + esc.width <= 0 ? ok(`@${w}: Escape closes the drawer`) : bad(`@${w}: Escape left the drawer open`);
    }
    await page.context().close();
  }
  // Search: keyword finds a section, Enter opens it; nonsense shows the empty state.
  {
    const page = await open(browser, adminToken);
    await gotoSection(page, 'profile');
    const search = page.getByRole('searchbox', { name: 'Search settings' });
    await search.fill('dark');
    const hits = await page.locator('#settings-navigation [data-nav-id]').evaluateAll((els) => els.map((e) => e.dataset.navId));
    hits.length === 1 && hits[0] === 'preferences' ? ok('search "dark" → Preferences only') : bad(`search "dark" → ${hits}`);
    await search.press('Enter');
    await page.waitForURL(/settings\/preferences/).then(() => ok('Enter opens the first match'), () => bad('Enter did not navigate'));
    await search.fill('zzzz');
    (await page.getByText('No settings match').count()) ? ok('search empty state') : bad('no search empty state');
    // Back to app leaves settings and swaps the same sidebar back to workspace navigation.
    await search.fill('');
    // Sections are real links (open in a new tab works), not buttons.
    const hrefs = await page.locator('#settings-navigation a[data-nav-id]').evaluateAll((els) => els.map((e) => e.getAttribute('href')));
    hrefs.length && hrefs.every((h) => /^\/workspace\/settings\/[a-z-]+$/.test(h)) ? ok(`nav: ${hrefs.length} sections are links with real URLs`) : bad(`nav links: ${hrefs.slice(0, 3)}`);
    await page.getByRole('button', { name: 'Back to app' }).click();
    await page.waitForFunction(() => !location.pathname.includes('/settings'));
    (await page.locator('#workspace-sidebar nav[aria-label="Workspace navigation"]').count()) ? ok('Back to app restores workspace navigation in the same sidebar') : bad('workspace navigation not restored');
    // Unknown id
    await page.goto(`${baseUrl}/workspace/settings/not-a-section`);
    await page.waitForSelector('[data-settings-page]');
    (await page.getByText('There is no “not-a-section” settings page').count()) ? ok('unknown section → not found page') : bad('unknown section not handled');
    await page.context().close();
  }
  // Member: admin sections are hidden from nav and refused by URL; member sections render.
  {
    const page = await open(browser, memberToken);
    await gotoSection(page, 'preferences');
    const navIds = await page.locator('#settings-navigation [data-nav-id]').evaluateAll((els) => els.map((e) => e.dataset.navId));
    for (const s of SECTIONS) {
      if (s.admin) {
        navIds.includes(s.id) ? bad(`member: nav shows admin section ${s.id}`) : ok(`member: nav hides ${s.id}`);
        await gotoSection(page, s.id);
        (await page.getByText(REFUSED).count()) ? ok(`member: ${s.id} URL refused`) : bad(`member: ${s.id} URL not refused`);
      } else {
        await gotoSection(page, s.id);
        const refused = await page.getByText(REFUSED).count();
        !refused ? ok(`member: ${s.id} renders`) : bad(`member: ${s.id} wrongly refused`);
      }
    }
    // Capability gating: granting the capability opens the section for this member (and the nav
    // offers it); revoking closes it again. The UI follows the same check as the API.
    const grant = (c, a) => page.evaluate(async ([cap, allow, t]) => {
      const r = await fetch('/graphql', { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${t}` },
        body: JSON.stringify({ query: 'mutation($c:String!,$a:Boolean!){workspacePermissionUpdate(capability:$c,memberAllowed:$a){memberAllowed}}', variables: { c: cap, a: allow } }) });
      return (await r.json()).data?.workspacePermissionUpdate?.memberAllowed;
    }, [c, a, adminToken]);
    for (const [cap, id] of [['extension.manage', 'extensions'], ['member.invite', 'members']]) {
      try {
        await grant(cap, true);
        await gotoSection(page, id);
        const refused = await page.getByText(REFUSED).count();
        !refused ? ok(`member with ${cap}: ${id} opens`) : bad(`member with ${cap}: ${id} still refused`);
        const inNav = await page.locator(`#settings-navigation [data-nav-id="${id}"]`).count();
        inNav ? ok(`member with ${cap}: nav offers ${id}`) : bad(`member with ${cap}: nav hides ${id}`);
      } finally { await grant(cap, false); }
    }
    page.errors.length ? bad(`member page errors: ${page.errors.slice(0, 3).join(' | ')}`) : ok('member: no uncaught page errors');
    await page.context().close();
  }
}

async function journeys() {
  const files = fs.readdirSync(__dirname).filter((f) => f.endsWith('.cjs') && f !== 'run-all.cjs');
  for (const f of files) {
    const id = f.replace(/\.cjs$/, '');
    if (only.length && !only.includes(id)) continue;
    console.log(`\n# ${id}`);
    try {
      const mod = require(path.join(__dirname, f));
      const shots = path.join(outDir, id);
      fs.mkdirSync(shots, { recursive: true });
      const r = await mod.run({ baseUrl, adminToken, memberToken, screenshotsDir: shots });
      for (const p of r.passed || []) ok(`${id}: ${p}`);
      for (const p of r.failed || []) bad(`${id}: ${p}`);
    } catch (e) { bad(`${id}: crashed: ${e.message.split('\n')[0]}`); }
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    if (!only.length || only.includes('shell')) await shell(browser);
  } finally { await browser.close(); }
  await journeys();
  const report = { baseUrl, at: new Date().toISOString(), passed: passed.length, failed };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify({ ...report, passedChecks: passed }, null, 2));
  console.log(`\n${passed.length} passed, ${failed.length} failed → ${path.join(outDir, 'report.json')}`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
