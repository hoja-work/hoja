// Behavior check for issue list rows at phone width. Run after scripts/mobile-audit.cjs (it reuses that database):
//   node scripts/mobile-row-check.cjs [--db <sqlite>] [--per-screen 25]
// The click-through only proves a row responds. This proves each row opens the issue it names: rows are matched by their
// identifier (not by position, because lists reorder on reload), and the check passes only when the URL becomes that issue.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');

const repo = path.resolve(__dirname, '..');
const arg = (flag, fallback) => { const i = process.argv.indexOf(flag); return i > -1 ? process.argv[i + 1] : fallback; };
const db = path.resolve(arg('--db', path.join(repo, 'local-e2e/artifacts/mobile-audit-scale/audit.db')));
const perScreen = Number(arg('--per-screen', '25'));
const token = 'cliniar_mobile_audit_token';
const web = path.join(repo, 'frontend/dist');
const port = 19200 + Math.floor(Math.random() * 80);
const base = `http://127.0.0.1:${port}`;
const wait = ms => new Promise(r => setTimeout(r, ms));
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });

// Project rows are labelled "Open project <name>" and open the project's own page, whose heading is that name.
const PROJECT_SCREENS = [['Projects', '/workspace/projects']];

const SCREENS = [
  ['My issues / Assigned', '/workspace/my-issues/assigned'],
  ['My issues / Created', '/workspace/my-issues/created'],
  ['Board / Active', '/workspace/team/eng/active'],
  ['Team issues list', '/workspace/team/eng/issues'],
  ['Inbox', '/workspace/inbox/other'],
];

// Identifiers of the rows currently on the page, read from the row buttons' "Open ENG-1: title" labels.
const rowIdents = () => [...document.querySelectorAll('button[aria-label^="Open "]')]
  .map(b => (b.getAttribute('aria-label').match(/^Open ([A-Z0-9]+-\d+):/) || [])[1])
  .filter(Boolean);
const projectNames = () => [...document.querySelectorAll('button[aria-label^="Open project "]')]
  .map(b => b.getAttribute('aria-label').replace(/^Open project /, ''));

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const results = [];
    for (const [screen, route] of SCREENS) {
      const probe = await ctx.newPage();
      await probe.goto(base + route);
      await probe.waitForLoadState('load');
      await wait(1000);
      // Load more pages (up to the sample size) so rows behind "Load more" are sampled too.
      for (let pages = 0; pages < 10; pages++) {
        const seen = new Set(await probe.evaluate(rowIdents));
        if (seen.size >= perScreen) break;
        const more = probe.getByRole('button', { name: /^Load more/ }).first();
        if (!(await more.isVisible().catch(() => false))) break;
        await more.click();
        await wait(900);
      }
      const idents = [...new Set(await probe.evaluate(rowIdents))].slice(0, perScreen);
      await probe.close();
      for (const ident of idents) {
        const page = await ctx.newPage();
        await page.goto(base + route);
        await page.waitForLoadState('load');
        await wait(900);
        const row = page.locator(`button[aria-label^="Open ${ident}:"]`).first();
        let outcome;
        try {
          await row.click({ timeout: 3000 });
          await wait(700);
          const url = new URL(page.url()).pathname;
          // The issue URL is the identifier, optionally followed by a title slug.
          const named = url === `/workspace/issue/${ident}` || url.startsWith(`/workspace/issue/${ident}/`);
          outcome = named ? 'opens-named-issue' : `wrong-destination:${url}`;
        } catch (err) {
          outcome = `click-failed:${err.message.split('\n')[0]}`;
        }
        results.push({ screen, ident, outcome });
        await page.close();
      }
    }
    for (const [screen, route] of PROJECT_SCREENS) {
      const probe = await ctx.newPage();
      await probe.goto(base + route);
      await probe.waitForLoadState('load');
      await wait(1000);
      const names = [...new Set(await probe.evaluate(projectNames))].slice(0, perScreen);
      await probe.close();
      for (const name of names) {
        const page = await ctx.newPage();
        await page.goto(base + route);
        await page.waitForLoadState('load');
        await wait(900);
        const target = page.locator(`button[aria-label="Open project ${name}"]`).first();
        let outcome;
        try {
          await target.click({ timeout: 3000 });
          await wait(900);
          const pathname = new URL(page.url()).pathname;
          const heading = (await page.locator('h1').first().textContent().catch(() => '') || '').trim();
          outcome = pathname.startsWith('/workspace/project/') && heading === name.trim() ? 'opens-named-project' : `wrong-destination:${pathname} heading="${heading}"`;
        } catch (err) {
          outcome = `click-failed:${err.message.split('\n')[0]}`;
        }
        results.push({ screen, ident: name, outcome });
        await page.close();
      }
    }
    // Pulse: each post's project link opens that project; the comments toggle expands its panel.
    {
      const page = await ctx.newPage();
      await page.goto(base + '/workspace/pulse/all');
      await page.waitForLoadState('load');
      await wait(1000);
      const links = [...new Set(await page.locator('.pulse-page h2 a, .pulse-page h2 + * a').evaluateAll(as => as.map(a => a.textContent.trim()).filter(Boolean)))].slice(0, perScreen);
      for (const name of links) {
        const p2 = await ctx.newPage();
        await p2.goto(base + '/workspace/pulse/all');
        await p2.waitForLoadState('load');
        await wait(900);
        let outcome;
        try {
          await p2.getByRole('link', { name, exact: true }).first().click({ timeout: 3000 });
          await wait(900);
          const pathname = new URL(p2.url()).pathname;
          const heading = ((await p2.locator('h1').first().textContent().catch(() => '')) || '').trim();
          outcome = pathname.startsWith('/workspace/project/') && heading === name ? 'opens-named-project' : `wrong-destination:${pathname} heading="${heading}"`;
        } catch (err) {
          outcome = `click-failed:${err.message.split('\n')[0]}`;
        }
        results.push({ screen: 'Pulse (project link)', ident: name, outcome });
        await p2.close();
      }
      const toggles = await page.getByRole('button', { name: /^Open comments for / }).count();
      if (toggles) {
        const first = page.getByRole('button', { name: /^Open comments for / }).first();
        const label = await first.getAttribute('aria-label');
        await first.click({ timeout: 3000 }).catch(() => {});
        await wait(500);
        // The label flips from "Open comments for X" to "Close comments for X" once expanded.
        const expanded = await page.getByRole('button', { name: /(Open|Close) comments for / }).first().getAttribute('aria-expanded').catch(() => null);
        results.push({ screen: 'Pulse (comments toggle)', ident: label, outcome: expanded === 'true' ? 'opens-comments' : `no-expand:aria-expanded=${expanded}` });
      }
      await page.close();
    }

    // Cycles: a cycle row and its "Cycle detail" control open that cycle, whose heading names it.
    {
      const page = await ctx.newPage();
      await page.goto(base + '/workspace/team/eng/cycles/current');
      await page.waitForLoadState('load');
      await wait(1000);
      const rows = [...new Set(await page.locator('button').evaluateAll(bs => bs.map(b => b.textContent.trim()).filter(t => /^Cycle \d+$/.test(t))))].slice(0, perScreen);
      await page.close();
      for (const name of rows) {
        const p3 = await ctx.newPage();
        await p3.goto(base + '/workspace/team/eng/cycles/current');
        await p3.waitForLoadState('load');
        await wait(900);
        let outcome;
        try {
          await p3.locator('button', { hasText: name }).first().click({ timeout: 3000 });
          await wait(900);
          const pathname = new URL(p3.url()).pathname;
          // The cycle page names itself in its region label, not always in h1.
          const named = await p3.locator(`[aria-label="${name}"]`).count();
          outcome = pathname.startsWith('/workspace/cycle/') && named > 0 ? 'opens-named-cycle' : `wrong-destination:${pathname}`;
        } catch (err) {
          outcome = `click-failed:${err.message.split('\n')[0]}`;
        }
        results.push({ screen: 'Cycles (cycle row)', ident: name, outcome });
        await p3.close();
      }
    }

    const counts = {};
    for (const r of results) counts[r.outcome.split(':')[0]] = (counts[r.outcome.split(':')[0]] || 0) + 1;
    console.log(`Row check: ${results.length} rows across ${SCREENS.length + PROJECT_SCREENS.length + 2} screens`);
    console.log(JSON.stringify(counts, null, 2));
    const passing = new Set(['opens-named-issue', 'opens-named-project', 'opens-comments', 'opens-named-cycle']);
    const bad = results.filter(r => !passing.has(r.outcome));
    if (bad.length) console.log(JSON.stringify(bad.slice(0, 10), null, 2));
    process.exitCode = bad.length ? 1 : 0;
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
  }
})().then(() => process.exit(process.exitCode || 0), error => { console.error(error); process.exit(1); });
