// Board sideways-scroll probe: a horizontal swipe (touch) or horizontal wheel/trackpad gesture made
// over the cards of a Kanban column must scroll the board's columns sideways, the same as the gesture
// made over the column headers. Vertical scrolling inside a column must keep working.
//
// Run from the app root after `cd frontend && npm run build`:
//   PLAYWRIGHT_MODULE=$PWD/frontend/node_modules/playwright node scripts/ui-probes/board-swipe-scroll.cjs
// It seeds a throwaway database and serves frontend/dist, so it needs `.venv` and Playwright.
// Against an already-running app skip the seeding:
//   PROBE_URL=http://127.0.0.1:8797 PROBE_TOKEN=<token> node scripts/ui-probes/board-swipe-scroll.cjs
//
// Exit 0 means every gesture scrolled the board; exit 1 means a check failed; exit 2 means it could not run.
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const repo = path.resolve(__dirname, '../..');
function loadPlaywright() {
  for (const c of [process.env.PLAYWRIGHT_MODULE, 'playwright',
                   path.join(repo, 'frontend/node_modules/playwright'),
                   path.join(repo, 'local-e2e/node_modules/playwright')].filter(Boolean)) {
    try { return require(c) } catch { /* next */ }
  }
  throw new Error('Playwright not found; set PLAYWRIGHT_MODULE.');
}
const { chromium } = loadPlaywright();

let failed = 0;
const ok = m => console.log(`ok   ${m}`);
const fail = m => { failed++; console.error(`FAIL ${m}`); };
const wait = ms => new Promise(r => setTimeout(r, ms));

let base = (process.env.PROBE_URL || '').replace(/\/$/, '');
let token = process.env.PROBE_TOKEN || '';
let server = null;
let scratchDir = null;

function seedAndServe() {
  const web = path.join(repo, 'frontend/dist');
  if (!fs.existsSync(path.join(web, 'index.html'))) {
    console.error('build the frontend first: cd frontend && npm run build');
    process.exit(2);
  }
  const artifacts = path.join(repo, 'local-e2e/artifacts');
  fs.mkdirSync(artifacts, { recursive: true });
  scratchDir = fs.mkdtempSync(path.join(artifacts, 'board-swipe-'));
  const db = path.join(scratchDir, 'swipe.db');
  token = 'hoja_board_swipe_token';
  const py = path.join(repo, '.venv/bin/python');
  const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  if (seed.status !== 0) { console.error(seed.stderr || 'seed failed'); process.exit(2); }
  const port = 18900 + Math.floor(Math.random() * 90);
  base = `http://127.0.0.1:${port}`;
  server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
    { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
}

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(base + '/')).ok) return; } catch { /* not up yet */ }
    await wait(250);
  }
  throw new Error('server did not start');
}

const scrollLeft = page => page.$eval('[data-kanban-columns]', el => el.scrollLeft);
const resetScroll = page => page.$eval('[data-kanban-columns]', el => { el.style.scrollBehavior = 'auto'; el.scrollLeft = 0; });

// A point inside the visible part of the first card, the first column header and the first
// column's body below its cards, kept near the right edge so a leftward swipe stays on screen.
async function points(page) {
  return page.evaluate(() => {
    const view = document.querySelector('[data-kanban-columns]').getBoundingClientRect();
    const right = Math.min(view.right, window.innerWidth) - 16;
    const at = (el, y) => {
      const r = el.getBoundingClientRect();
      return { x: Math.round(Math.min(r.right - 16, right)), y: Math.round(y ?? r.top + Math.min(r.height / 2, 40)) };
    };
    const card = document.querySelector('[data-kanban-columns] [data-kanban-card]');
    const column = document.querySelector('[data-kanban-columns] [data-kanban-column]');
    const header = column.querySelector(':scope > header');
    const body = column.getBoundingClientRect();
    return { card: at(card), header: at(header), body: at(column, Math.round(body.bottom - 60)) };
  });
}

async function openBoard(browser, contextOptions) {
  const ctx = await browser.newContext(contextOptions);
  await ctx.addInitScript(t => { localStorage.setItem('hoja_token', t); localStorage.setItem('hoja_issue_layout', 'Board'); }, token);
  const page = await ctx.newPage();
  await page.goto(base + '/workspace/board');
  await page.waitForSelector('[data-kanban-columns] [data-kanban-card]', { timeout: 20000 });
  await wait(500);
  const overflow = await page.$eval('[data-kanban-columns]', el => el.scrollWidth - el.clientWidth);
  if (overflow <= 0) throw new Error(`board does not overflow sideways at this width (${contextOptions.viewport.width}px)`);
  return { ctx, page };
}

async function desktop(browser) {
  const { ctx, page } = await openBoard(browser, { viewport: { width: 900, height: 800 } });
  const p = await points(page);
  for (const [where, at] of [['header', p.header], ['card', p.card], ['column body', p.body]]) {
    await resetScroll(page);
    await page.mouse.move(at.x, at.y);
    for (let i = 0; i < 6; i++) { await page.mouse.wheel(60, 0); await wait(30); }
    await wait(400);
    const left = await scrollLeft(page);
    left > 0 ? ok(`desktop: horizontal wheel over a ${where} scrolls the board (scrollLeft ${left})`)
             : fail(`desktop: horizontal wheel over a ${where} did not scroll the board (scrollLeft ${left})`);
  }
  await ctx.close();
}

// A finger drag made of real touch events, so touch-action and scroll chaining apply as on a
// phone. (Input.synthesizeScrollGesture is a no-op in headless Chromium, so it cannot be used.)
async function drag(cdp, from, dx, dy) {
  const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
  await touch('touchStart', from.x, from.y);
  const steps = 20;
  for (let i = 1; i <= steps; i++) {
    await touch('touchMove', Math.round(from.x + (dx * i) / steps), Math.round(from.y + (dy * i) / steps));
    await wait(16);
  }
  await touch('touchEnd');
  await wait(400);
}

async function mobile(browser) {
  const { ctx, page } = await openBoard(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const cdp = await ctx.newCDPSession(page);
  const p = await points(page);
  for (const [where, at] of [['header', p.header], ['card', p.card], ['column body', p.body]]) {
    await resetScroll(page);
    await drag(cdp, at, -Math.min(200, at.x - 8), 0);
    const left = await scrollLeft(page);
    left > 0 ? ok(`mobile: swipe left over a ${where} scrolls the board (scrollLeft ${left})`)
             : fail(`mobile: swipe left over a ${where} did not scroll the board (scrollLeft ${left})`);
  }
  // A swipe right brings the board back.
  await page.$eval('[data-kanban-columns]', el => { el.scrollLeft = 300; });
  await drag(cdp, { x: 40, y: p.card.y }, 200, 0);
  const back = await scrollLeft(page);
  back < 300 ? ok(`mobile: swipe right over a card scrolls the board back (scrollLeft 300 -> ${back})`)
             : fail(`mobile: swipe right over a card did not scroll the board back (scrollLeft ${back})`);
  // A vertical swipe over a card never moves the board sideways.
  await resetScroll(page);
  await drag(cdp, p.card, 0, -150);
  const leftAfterVertical = await scrollLeft(page);
  leftAfterVertical === 0 ? ok('mobile: a vertical swipe over a card does not move the board sideways')
                          : fail(`mobile: a vertical swipe moved the board sideways (scrollLeft ${leftAfterVertical})`);
  await ctx.close();
}

(async () => {
  if (!base || !token) seedAndServe();
  let browser;
  try {
    await waitForServer();
    browser = await chromium.launch();
    await desktop(browser);
    await mobile(browser);
  } catch (e) {
    console.error(`could not run: ${e.message}`);
    failed = failed || -1;
  } finally {
    if (browser) await browser.close();
    if (server) server.kill();
    if (scratchDir) fs.rmSync(scratchDir, { recursive: true, force: true });
  }
  process.exit(failed < 0 ? 2 : failed ? 1 : 0);
})();
