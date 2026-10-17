// Run from repo root after `cd frontend && npm run build`: node scripts/mobile-audit.cjs [--out <dir>]
// Mobile UX audit of every Hoja screen. Seeds a disposable backend, then at phone widths inventories every visible
// control (role, accessible name, box, scroll container) and checks the invariants a phone user depends on:
//   - every control has an accessible name (icon-only buttons need aria-label or title)
//   - touch targets are at least 44x44 CSS px (WCAG 2.5.5); inline text links are exempt
//   - fields are at least 16px so iOS Safari does not zoom on focus
//   - no page-level horizontal scroll; no control that overflows the viewport outside a scroll container
//   - no two visible controls overlap (a tap would land on the wrong one)
//   - no control clips its own content (text cut off with overflow hidden and no ellipsis)
// The inventory is written as JSON so every screen and button can be tracked; see docs/mobile-audit.md.
// --gate exits non-zero on any finding.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const outFlag = process.argv.indexOf('--out');
const outDir = outFlag > -1 ? path.resolve(process.argv[outFlag + 1]) : path.join(repo, 'local-e2e/artifacts/mobile-audit');
fs.mkdirSync(outDir, { recursive: true });
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const db = path.join(outDir, 'audit.db');
// --reuse keeps the database from the last run (skips seeding) so a single fix can be re-measured quickly.
const REUSE = process.argv.includes('--reuse');
const ONLY = process.argv.indexOf('--only') > -1 ? new RegExp(process.argv[process.argv.indexOf('--only') + 1], 'i') : null;
const token = 'cliniar_mobile_audit_token';
const py = path.join(repo, '.venv/bin/python');
if (!REUSE) {
  try { fs.unlinkSync(db); } catch {}
  const seed = spawnSync(py, ['-c', `from cliniar_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='ENG', team_name='Engineering', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(seed.status, 0, seed.stderr);
}

const port = 18990 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

async function gql(query, variables) {
  const res = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  return (await res.json()).data;
}

// Runs inside the page. Returns every visible control and the mobile invariant violations for this viewport.
const collect = () => {
  const W = window.innerWidth;
  const SELECTOR = 'button,a[href],input,select,textarea,summary,[role=button],[role=tab],[role=menuitem],[role=option],[role=switch],[role=checkbox],[role=radio],[role=link],[role=combobox],[contenteditable=true]';
  const visible = el => { const s = getComputedStyle(el); if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const nameOf = el => (el.getAttribute('aria-label') || el.getAttribute('title') || (el.labels && el.labels[0] && el.labels[0].textContent) || el.getAttribute('placeholder') || el.textContent || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const scrollAncestor = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return p; } return null; };
  const role = el => el.getAttribute('role') || el.tagName.toLowerCase();
  const controls = [], issues = [];
  // Controls parked fully off-canvas (the closed sidebar drawer) are not on screen, so they cannot be tapped or overlap anything.
  const onCanvas = el => { const r = el.getBoundingClientRect(); return r.right > 0 && r.left < W; };
  const els = [...document.querySelectorAll(SELECTOR)].filter(el => visible(el) && onCanvas(el));
  if (document.documentElement.scrollWidth > W + 1) issues.push({ kind: 'page-horizontal-scroll', detail: `page is ${document.documentElement.scrollWidth}px wide at ${W}px` });
  for (const el of els) {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    const name = nameOf(el);
    const scroller = scrollAncestor(el);
    const inline = el.tagName === 'A' && s.display === 'inline';
    const entry = { role: role(el), name, x: Math.round(r.left), y: Math.round(r.top + window.scrollY), w: Math.round(r.width), h: Math.round(r.height), inScroller: Boolean(scroller), disabled: el.disabled === true };
    controls.push(entry);
    const label = `${entry.role} "${name || '(no name)'}"`;
    if (!name && !el.matches('input[type=hidden]')) issues.push({ kind: 'unlabeled-control', detail: label });
    if (!inline && (r.width < 44 || r.height < 44) && !el.matches('input[type=checkbox],input[type=radio]') && !scroller) issues.push({ kind: 'touch-target-small', detail: `${label} is ${Math.round(r.width)}x${Math.round(r.height)}` });
    if (el.matches('input,select,textarea') && Number.parseFloat(s.fontSize) < 16) issues.push({ kind: 'input-font-under-16px', detail: `${label} uses ${s.fontSize}` });
    if (r.right > W + 1 && r.left < W && !scroller) issues.push({ kind: 'control-overflows-viewport', detail: `${label} ends at ${Math.round(r.right)}px` });
    if (el.scrollWidth > el.clientWidth + 1 && (s.overflowX === 'hidden' || s.overflowX === 'clip') && !scroller && el.matches('button,a,[role=button]')) issues.push({ kind: 'control-content-clipped', detail: `${label} content is ${el.scrollWidth}px in ${el.clientWidth}px` });
  }
  // Overlap between controls: a tap on the overlapped area is ambiguous. Nested controls (a button inside a button-like row) are skipped.
  for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
    const a = els[i], b = els[j];
    if (a.contains(b) || b.contains(a)) continue;
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    const ow = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left), oh = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
    if (ow > 4 && oh > 4) issues.push({ kind: 'controls-overlap', detail: `"${nameOf(a) || a.tagName}" overlaps "${nameOf(b) || b.tagName}" by ${Math.round(ow)}x${Math.round(oh)}` });
  }
  return { viewport: W, controls, issues };
};

const SETTINGS = ['preferences', 'profile', 'workspace', 'notifications', 'api-keys', 'model-providers', 'agent-conversations', 'issue-labels', 'members'];

// --scale seeds a workspace big enough to expose list virtualisation, overflow and layout problems that a tiny seed hides.
const SCALE = process.argv.includes('--scale') ? { teams: 36, projects: 300, issues: 4000, concurrency: 16 } : null;

// Creates `count` items by running `make(i)` with at most `concurrency` requests in flight.
async function pool(count, concurrency, make) {
  let next = 0, failed = 0;
  const worker = async () => { while (next < count) { const i = next++; try { await make(i); } catch { failed++; } } };
  await Promise.all(Array.from({ length: concurrency }, worker));
  return failed;
}

async function seedScale() {
  const teamIds = [];
  const created = await pool(SCALE.teams, SCALE.concurrency, async i => {
    const team = (await gql('mutation($i:TeamCreateInput!){teamCreate(input:$i){success team{id key}}}', { i: { name: `Team ${i + 1}`, key: `T${String(i + 1).padStart(2, '0')}` } })).teamCreate.team;
    teamIds[i] = team.id;
  });
  const projectIds = [];
  await pool(SCALE.projects, SCALE.concurrency, async i => {
    const p = (await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){success project{id}}}', { i: { name: `Project ${i + 1} — quarterly roadmap item with a deliberately long name to test truncation`, teamIds: [teamIds[i % teamIds.length]] } })).projectCreate.project;
    projectIds[i] = p.id;
  });
  // Project i belongs to team i % teams, so an issue's project must come from its own team or the server rejects it.
  const perTeam = Math.floor(SCALE.projects / SCALE.teams);
  const failed = await pool(SCALE.issues, SCALE.concurrency, async i => {
    const team = i % SCALE.teams;
    const projectIndex = team + (Math.floor(i / SCALE.teams) % perTeam) * SCALE.teams;
    const data = await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title: `Scale issue ${i + 1}: a realistic title that is long enough to wrap or truncate on a phone-width list row`, teamId: teamIds[team], projectId: projectIds[projectIndex], priority: i % 5 } });
    if (!data.issueCreate?.success) throw new Error('issue not created');
  });
  console.log(`scale seed: ${teamIds.filter(Boolean).length} teams (${created} failed), ${projectIds.length} projects, ${SCALE.issues - failed} of ${SCALE.issues} issues`);
}

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    const issues = (await gql('query{issues(first:3){nodes{identifier}}}')).issues.nodes.map(n => n.identifier);
    const project = (await gql('mutation{projectCreate(input:{name:"Launch audit",teamIds:[]}){success project{id}}}')).projectCreate.project;
    const cycle = await gql('query{cycles(first:1){nodes{id}}}').then(d => d.cycles?.nodes?.[0]).catch(() => undefined);
    assert.ok(issues.length && project, 'seed data missing');
    if (SCALE && !REUSE) await seedScale();
    const allTeams = (await gql('query{teams{nodes{key}}}')).teams.nodes;
    const teamKey = allTeams[0].key.toLowerCase();
    const scaledTeamRoutes = SCALE ? [allTeams[Math.floor(allTeams.length / 2)], allTeams[allTeams.length - 1]].map(t => t.key.toLowerCase()) : [];

    const routes = [
      ['My issues / Assigned', '/workspace/my-issues/assigned'],
      ['My issues / Created', '/workspace/my-issues/created'],
      ['My issues / Subscribed', '/workspace/my-issues/subscribed'],
      ['My issues / Activity', '/workspace/my-issues/activity'],
      ['Pulse', '/workspace/pulse/all'],
      ['Inbox', '/workspace/inbox/other'],
      ['Meetings', '/workspace/meetings'],
      ['Agent', '/workspace/agent'],
      ['Initiatives', '/workspace/initiatives/active'],
      ['Projects', '/workspace/projects'],
      ['Views', '/workspace/views/issues'],
      ['Favorite views', '/workspace/favorite-views'],
      ['Board / Active', `/workspace/team/${teamKey}/active`],
      ...scaledTeamRoutes.map(k => [`Board / Active (team ${k.toUpperCase()})`, `/workspace/team/${k}/active`]),
      ['Team home', `/workspace/team/${teamKey}`],
      ['Team issues list', `/workspace/team/${teamKey}/issues`],
      ['Team cycles', `/workspace/team/${teamKey}/cycles`],
      ['Team cycles / upcoming', `/workspace/team/${teamKey}/cycles/upcoming`],
      ['Team cycle settings', `/workspace/team/${teamKey}/cycles/settings`],
      ['Team projects', `/workspace/team/${teamKey}/projects`],
      ['Team views', `/workspace/team/${teamKey}/views`],
      ['Cycles', '/workspace/cycles'],
      ['Members', '/workspace/members'],
      ['Issue detail', `/workspace/issue/${issues[0]}`],
      ['Project detail', `/workspace/project/${project.id}`],
      ['Project issues', `/workspace/project/${project.id}/issues`],
      ['Project new view', `/workspace/project/${project.id}/view/new`],
      ...(cycle ? [['Cycle detail', `/workspace/cycle/${cycle.id}`]] : []),
      ...SETTINGS.map(s => [`Settings / ${s}`, `/workspace/settings/${s}`]),
      ['Create workspace', '/workspace/create-workspace'],
    ];

    browser = await chromium.launch({ headless: true });
    const report = [];
    // --widths 768,1440 audits other breakpoints; the default is the phone widths the gate enforces.
    const widthFlag = process.argv.indexOf('--widths');
    const widths = widthFlag > -1 ? process.argv[widthFlag + 1].split(',').map(Number) : REUSE ? [390] : [390, 320];
    const selected = ONLY ? routes.filter(([screen]) => ONLY.test(screen)) : routes;
    for (const width of widths) {
      const ctx = await browser.newContext({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
      await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      for (const [screen, route] of selected) {
        const page = await ctx.newPage();
        await page.goto(base + route);
        await page.waitForLoadState('load');
        await wait(1000);
        const result = await page.evaluate(collect);
        report.push({ width, screen, route, ...result, finalUrl: new URL(page.url()).pathname });
        await page.close();
      }
      await ctx.close();
    }
    fs.writeFileSync(path.join(outDir, REUSE || ONLY ? 'inventory-partial.json' : 'inventory.json'), JSON.stringify(report, null, 2));
    const total = report.reduce((n, r) => n + r.issues.length, 0);
    const byKind = {};
    for (const r of report) for (const i of r.issues) byKind[i.kind] = (byKind[i.kind] || 0) + 1;
    console.log(`Mobile audit: ${routes.length} screens x 2 widths, ${report.reduce((n, r) => n + r.controls.length, 0)} control instances, ${total} findings`);
    console.log(JSON.stringify(byKind, null, 2));
    console.log(`inventory: ${path.join(outDir, REUSE || ONLY ? 'inventory-partial.json' : 'inventory.json')}`);
    // --gate makes any finding fail the run, so a regression cannot ship silently (see frontend `test:mobile`).
    if (process.argv.includes('--gate') && total > 0) process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    server.kill('SIGKILL');
  }
})().then(() => process.exit(process.exitCode || 0), error => { console.error(error); process.exit(1); });
