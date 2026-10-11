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
const token = 'cliniar_page_invariant_token';
const py = path.join(repo, '.venv/bin/python');
const seed = spawnSync(py, ['-c', `from cliniar_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 18900 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });
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
    const sectionLine = src.split('\n').find(l => l.includes('const sections='));
    assert.ok(sectionLine, 'sidebar sections not found in main.tsx');
    const labels = [...sectionLine.matchAll(/\['([^']+)',\s*[A-Za-z]+\]/g)].map(m => m[1]);
    assert.ok(labels.length >= 10, `expected sidebar entries, found ${labels.length}`);
    const details = [`/workspace/issue/${issues[0]}`, `/workspace/project/${projectId}`];
    const extraCss = process.env.PAGE_EXTRA_CSS ? fs.readFileSync(process.env.PAGE_EXTRA_CSS, 'utf8') : '';

    browser = await chromium.launch({ headless: true });
    const failures = [];
    let audited = 0;
    for (const width of [320, 390, 1440]) {
      const ctx = await browser.newContext({ viewport: { width, height: 800 } });
      await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      const check = async (page, what) => {
        await page.waitForLoadState('load');
        await wait(900);
        if (extraCss) await page.addStyleTag({ content: extraCss });
        const problems = await page.evaluate(invariants);
        audited++;
        for (const p of problems) failures.push(`${width}px ${what}: ${p}`);
      };
      for (const label of labels) {
        const page = await ctx.newPage();
        await page.goto(`${base}/`);
        const pattern = new RegExp('^\\s*' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*$');
        const item = page.locator(':is(button,a)').filter({ hasText: pattern }).first();
        const drawerOpen = await page.evaluate(() => { const s = document.querySelector('.sidebar'); return !s || s.getBoundingClientRect().left >= 0; });
        if (!drawerOpen) await page.click('button.mobile-nav-toggle[aria-expanded="false"]');
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
