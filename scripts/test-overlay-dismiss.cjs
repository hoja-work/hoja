// Overlay dismissal audit against a real seeded server. Run from the repo root after
// `cd frontend && npm run build`:
//   node scripts/test-overlay-dismiss.cjs   (WIDTH=390 HEIGHT=844 for a phone viewport)
// Checks that both overlay surfaces close on an outside press and on Escape, and that a
// press inside them leaves them open: the sidebar "More" menu (Popover) and the inline
// My issues filter/display panels (my-issue-popover).
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'overlay-dismiss-'));
const db = path.join(dir, 'overlay.db');
const token = 'hoja_overlay_dismiss_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19400 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id states{nodes{id type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const todo = team.states.nodes.find(s => s.type === 'unstarted').id;
  await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title: 'Dismiss probe', teamId: team.id, assigneeId: me, stateId: todo } });

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: Number(process.env.WIDTH || 1440), height: Number(process.env.HEIGHT || 900) } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    await page.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-my-issue-row]');

    const moreMenu = page.locator('#workspace-more-menu');
    const moreTrigger = page.getByRole('button', { name: 'Workspace More' });
    const filterPanel = page.locator('[data-my-issue-popover][aria-label="Task filters"]');
    const displayPanel = page.locator('[data-my-issue-popover][aria-label="Display options"]');
    const filterTrigger = page.getByRole('button', { name: 'Add filter' });
    const displayTrigger = page.getByRole('button', { name: 'Display options' });
    // A heading is inert: pressing it proves dismissal without also navigating. Narrow
    // viewports fold the page head away, so fall back to a press on the inert body.
    const pressElsewhere = async () => {
      for (const selector of ['[data-my-issues-head] h1', '[data-settings-page] h1']) {
        const heading = page.locator(selector).first();
        try { await heading.click({ timeout: 1500 }); return; } catch { /* folded or covered on narrow screens */ }
      }
      await page.mouse.click(Number(process.env.WIDTH || 1440) - 3, 3);
    };

    if (await page.getByRole('button', { name: 'Open navigation' }).isVisible().catch(() => false)) {
      await page.getByRole('button', { name: 'Open navigation' }).click();
      await wait(250);
    }

    // --- sidebar More menu (Popover) ---
    await moreTrigger.click();
    await wait(150);
    ok(await moreMenu.count() === 1, 'More menu opens');
    ok(await moreMenu.getAttribute('data-slot') === 'popover-content', 'More menu uses ui/popover');
    await page.keyboard.press('Escape'); await wait(150);
    ok(await moreMenu.count() === 0, 'More menu closes on Escape');

    await moreTrigger.click(); await wait(150);
    await pressElsewhere(); await wait(200);
    ok(await moreMenu.count() === 0, 'More menu closes on an outside press');
    ok(await moreTrigger.getAttribute('aria-expanded') === 'false', 'More menu trigger reports aria-expanded=false');

    await moreTrigger.click(); await wait(150);
    await moreMenu.locator('p').first().click(); await wait(150);
    ok(await moreMenu.count() === 1, 'More menu stays open on an inside press');
    await pressElsewhere(); await wait(200);

    // The mobile navigation drawer is an overlay of its own: close it before pressing on
    // the page, otherwise it intercepts the press the panel checks rely on.
    if (await page.locator('#workspace-sidebar[data-open]').count()) {
      await page.getByRole('button', { name: 'Open navigation' }).click();
      await wait(300);
    }

    // --- My issues filter and display panels (inline popovers) ---
    // Each check starts from a known state, driven by the triggers, so one failure
    // cannot cascade into the next assertion.
    const closePanels = async () => {
      await page.keyboard.press('Escape'); await wait(150);
      if (await filterPanel.count()) { await filterTrigger.click({ timeout: 2000 }).catch(() => {}); await wait(120); }
      if (await displayPanel.count()) { await displayTrigger.click({ timeout: 2000 }).catch(() => {}); await wait(120); }
    };

    await closePanels();
    await filterTrigger.click(); await wait(150);
    ok(await filterPanel.count() === 1, 'filter panel opens');
    ok(await filterPanel.getAttribute('data-slot') === 'popover-content', 'filter uses ui/popover');
    ok(await filterPanel.getAttribute('data-side') === 'bottom', 'filter opens downward');
    await page.keyboard.press('Escape'); await wait(150);
    ok(await filterPanel.count() === 0, 'filter panel closes on Escape');

    await closePanels();
    await filterTrigger.click(); await wait(150);
    await pressElsewhere(); await wait(200);
    ok(await filterPanel.count() === 0, 'filter panel closes on an outside press');
    ok(await filterTrigger.getAttribute('aria-expanded') === 'false', 'filter trigger reports aria-expanded=false');

    await closePanels();
    await filterTrigger.click(); await wait(150);
    await filterPanel.getByRole('menuitem', { name: 'Status', exact: true }).click(); await wait(150);
    ok(await filterPanel.count() === 1, 'filter panel stays open for an inside press');

    await closePanels();
    await displayTrigger.click(); await wait(150);
    ok(await displayPanel.count() === 1, 'display panel opens');
    ok(await displayPanel.getAttribute('data-slot') === 'popover-content', 'display uses ui/popover');
    await pressElsewhere(); await wait(200);
    ok(await displayPanel.count() === 0, 'display panel closes on an outside press');

    // Pressing one toolbar trigger while the other panel is open switches panels. On a
    // narrow viewport the open panel covers the toolbar, so this is a desktop guarantee.
    await closePanels();
    if (Number(process.env.WIDTH || 1440) < 700) {
      console.log('SKIP toolbar triggers switch panels: the open panel covers the toolbar below 700px');
    } else {
      await filterTrigger.click(); await wait(150);
      await displayTrigger.click(); await wait(200);
      ok(await filterPanel.count() === 0 && await displayPanel.count() === 1, 'toolbar triggers switch panels without closing both');
    }
    if (!(await displayPanel.count())) { await displayTrigger.click(); await wait(150); }

    // A press on a control inside the panel must not dismiss it.
    await displayPanel.getByText('Display properties', { exact: true }).click(); await wait(150);
    ok(await displayPanel.count() === 1, 'display panel survives a press on non-interactive text inside it');

    // --- meetings call popover ---
    await page.goto(`${base}/workspace/meetings`, { waitUntil: 'networkidle' });
    const callTrigger = page.getByRole('button', { name: 'Call someone', exact: true });
    await callTrigger.click(); await wait(150);
    const callPanel = page.locator('[data-slot="popover-content"][aria-label="Call someone"]');
    ok(await callPanel.count() === 1, 'meeting call uses ui/popover');
    await page.keyboard.press('Escape'); await wait(200);
    ok(await callPanel.count() === 0, 'meeting call closes on Escape');
    ok(await callTrigger.evaluate(el => el === document.activeElement), 'meeting call restores trigger focus');

    // --- settings dropdown (SettingsSelect), whose panel is a portal on document.body ---
    await page.goto(`${base}/workspace/settings/preferences`, { waitUntil: 'networkidle' });
    const combo = page.getByRole('combobox', { name: 'Font size' });
    const listbox = page.locator('[role=listbox][aria-label="Font size"]');
    await combo.click(); await wait(200);
    ok(await listbox.count() === 1, 'settings dropdown opens');
    ok(await listbox.getAttribute('data-slot') === 'popover-content', 'settings uses ui/popover');
    await listbox.locator('div').first().click(); await wait(150);
    ok(await listbox.count() === 1, 'settings dropdown stays open for a press inside its portal');
    await pressElsewhere(); await wait(200);
    ok(await listbox.count() === 0, 'settings dropdown closes on an outside press');

    await combo.click(); await wait(200);
    await page.keyboard.press('Escape'); await wait(250);
    ok(await listbox.count() === 0, 'settings dropdown closes on Escape');
    ok(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')) === 'Font size', 'Escape returns focus to the settings trigger');
  } finally {
    await browser.close();
    server.kill('SIGTERM');
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(async error => { console.error(error); server.kill('SIGTERM'); process.exit(1); });
