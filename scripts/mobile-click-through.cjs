// Functional smoke test for every screen in docs/mobile-audit.md. Run after scripts/mobile-audit.cjs:
//   node scripts/mobile-click-through.cjs [--inventory <json>] [--db <sqlite>] [--out <json>]
// For each screen it loads a fresh page at 390px, then clicks every visible, named control one at a time and records:
//   error      the click produced a page error or a console error (other than an expected 401)
//   navigated  the URL changed
//   changed    the visible DOM changed (menu, dialog, panel, tab content)
//   no-effect  nothing observable happened; the control may be dead or need a precondition
//   click-failed  Playwright could not click it (covered, detached or off-screen)
//   not-found  the control was on the first load but not on the reload (async lists reorder); not a verdict on the control
// Destructive controls (delete, archive, log out, revoke, remove, ...) are never clicked.
// This proves a control responds, not that it does the right thing; see docs/mobile-audit.md.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const repo = path.resolve(__dirname, '..');
const arg = (flag, fallback) => { const i = process.argv.indexOf(flag); return i > -1 ? path.resolve(process.argv[i + 1]) : fallback; };
const inventoryPath = arg('--inventory', path.join(repo, 'local-e2e/artifacts/mobile-audit-scale/inventory.json'));
const db = arg('--db', path.join(repo, 'local-e2e/artifacts/mobile-audit-scale/audit.db'));
const outPath = arg('--out', path.join(repo, 'local-e2e/artifacts/mobile-audit-scale/functional.json'));
const web = path.join(repo, 'frontend/dist');
const token = 'cliniar_mobile_audit_token';
const DESTRUCTIVE = /delete|remove|archive|log ?out|sign ?out|revoke|leave|disconnect|discard|reset|disable|deactivate|rotate|transfer|close workspace|unsubscribe/i;
const MAX_PER_SCREEN = 80;
const CONCURRENCY = 4;
const wait = ms => new Promise(r => setTimeout(r, ms));

const port = 19100 + Math.floor(Math.random() * 80);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });

// Reads every visible, named control on the page, in document order, so each one can be clicked by index.
const listControls = () => {
  const SELECTOR = 'button,a[href],[role=button],[role=tab],[role=menuitem],[role=option],summary';
  const nameOf = el => (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const visible = el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth; };
  return [...document.querySelectorAll(SELECTOR)].filter(visible).map(el => nameOf(el));
};

const domSignature = () => `${document.querySelectorAll('*').length}|${document.body.innerText.length}|${[...document.querySelectorAll('[aria-expanded="true"],[role=dialog],[role=menu],[role=listbox]')].length}`;

async function openPage(ctx, route) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !m.text().includes('401')) errors.push(m.text()); });
  await page.goto(base + route);
  await page.waitForLoadState('load');
  await wait(900);
  return { page, errors };
}

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    const inventory = JSON.parse(fs.readFileSync(inventoryPath, 'utf8')).filter(r => r.width === 390);
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);

    const results = [];
    const runScreen = async ({ screen, route }) => {
      const { page, errors } = await openPage(ctx, route);
      const names = await page.evaluate(listControls);
      const seen = new Set();
      const targets = [];
      names.forEach((name, index) => {
        if (!name || seen.has(name) || DESTRUCTIVE.test(name)) return;
        seen.add(name);
        targets.push({ name, index });
      });
      await page.close();
      for (const t of targets.slice(0, MAX_PER_SCREEN)) {
        const { page: p, errors: e } = await openPage(ctx, route);
        const before = { url: p.url(), sig: await p.evaluate(domSignature) };
        e.length = 0;
        let outcome;
        try {
          const target = await p.evaluateHandle(({ name, index }) => {
            const nameOf = el => (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
            const els = [...document.querySelectorAll('button,a[href],[role=button],[role=tab],[role=menuitem],[role=option],summary')].filter(el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth; });
            const hit = els.find(el => nameOf(el) === name);
            if (hit) hit.setAttribute('data-audit-target', '1');
            return Boolean(hit);
          }, t);
          if (!(await target.jsonValue())) { outcome = 'not-found'; }
          else {
            await p.locator('[data-audit-target="1"]').first().click({ timeout: 3000 });
            await wait(500);
            const after = { url: p.url(), sig: await p.evaluate(domSignature) };
            if (e.length) outcome = 'error';
            else if (after.url !== before.url) outcome = 'navigated';
            else if (after.sig !== before.sig) outcome = 'changed';
            else outcome = 'no-effect';
          }
        } catch (err) {
          outcome = 'click-failed';
          e.push(err.message.split('\n')[0]);
        }
        results.push({ screen, route, name: t.name, outcome, errors: e.slice(0, 3) });
        await p.close();
      }
    };

    const queue = [...new Map(inventory.map(r => [r.screen, r])).values()];
    let next = 0;
    await Promise.all(Array.from({ length: CONCURRENCY }, async () => { while (next < queue.length) await runScreen(queue[next++]); }));
    fs.writeFileSync(outPath, JSON.stringify(results, null, 2));
    const counts = {};
    for (const r of results) counts[r.outcome] = (counts[r.outcome] || 0) + 1;
    console.log(`Click-through: ${queue.length} screens, ${results.length} controls clicked`);
    console.log(JSON.stringify(counts, null, 2));
    console.log(`results: ${outPath}`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
  }
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
