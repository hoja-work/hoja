// Project Overview audit against a real seeded server. Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-project-overview.cjs   (SHOTS=dir to save screenshots)
// Checks at 1440/390/320px: every property is shown once, the description once, progress is derived from the
// project's issues (completed / non-canceled, not the never-recomputed project.progress), per-status counts
// match the server, the latest project update is on the overview, and the page does not scroll sideways.
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
const dir = fs.mkdtempSync(path.join(scratch, 'project-overview-'));
const db = path.join(dir, 'p.db');
const token = 'cliniar_project_overview_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from cliniar_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);
const port = 19400 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const shots = process.env.SHOTS;
const DESC = 'Ship the self-serve onboarding flow to every new workspace.';
const UPDATE = 'Onboarding is in review; release notes drafted.';

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id name} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const st = t => team.states.nodes.find(s => s.type === t).id;
  const p = await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}', { i: { name: 'Launch', description: DESC, teamIds: [team.id], leadId: me, state: 'started', startDate: '2026-10-01', targetDate: '2026-12-15' } });
  const projectId = p.projectCreate.project.id;
  const create = (title, stateId) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title, teamId: team.id, assigneeId: me, projectId, stateId } });
  // A realistic project: 120 issues across statuses (30 in progress, 40 todo, 36 done, 14 canceled).
  const plan = [['started', 30], ['unstarted', 40], ['completed', 36], ['canceled', 14]].flatMap(([t, n]) => Array.from({ length: n }, (_, i) => [`${t} issue ${i + 1}`, st(t)]));
  for (let i = 0; i < plan.length; i += 10) await Promise.all(plan.slice(i, i + 10).map(([title, s]) => create(title, s)));
  await gql('mutation($i:ProjectUpdateCreateInput!){projectUpdateCreate(input:$i){success}}', { i: { projectId, body: UPDATE, status: 'on track' } });
  // Ground truth from the server, independent of the UI: 1 completed of 3 non-canceled issues.
  const srv = await gql('query($id:ID!){issues(first:250,filter:{project:{id:{eq:$id}}}){nodes{state{type}}}}', { id: projectId });
  const types = srv.issues.nodes.map(n => n.state.type);
  const done = types.filter(t => t === 'completed').length, scope = types.filter(t => t !== 'canceled').length;
  const expected = `${Math.round(done / scope * 100)}%`;

  const browser = await chromium.launch({ headless: true });
  const errors = [];
  for (const [W, H] of [[1440, 900], [390, 844], [320, 640]]) {
    const ctx = await browser.newContext({ viewport: { width: W, height: H } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage(); page.on('pageerror', e => errors.push(e.message));
    await page.goto(`${base}/workspace/project/${projectId}/overview`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    const info = await page.evaluate(() => {
      const root = document.querySelector('.project-reference');
      const text = root?.innerText || '';
      const count = s => text.split(s).length - 1;
      const labels = [...root.querySelectorAll('dt, .project-reference-properties span, .project-detail-stats span')].map(e => e.textContent.trim());
      return { text, labels, count: null, hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        oldCard: !!root.querySelector('.issue-card'), issueRows: root.querySelectorAll('.issue-row,.my-issue-row,.project-issue-item,.kanban-card').length, height: root.scrollHeight, tabsAbove: (root.querySelector('.project-reference-tabs')?.getBoundingClientRect().bottom || 1e9) <= (root.querySelector('.project-overview-progress, .project-detail-stats')?.getBoundingClientRect().top || 0), progress: root.querySelector('[data-overview="progress"]')?.textContent || '' };
    });
    const c = s => info.text.split(s).length - 1;
    const tag = `${W}px`;
    if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: path.join(shots, `overview-${W}.png`), fullPage: true }); }
    for (const label of ['Status', 'Lead', 'Teams', 'Target date', 'Start date']) {
      const n = info.labels.filter(l => l === label).length;
      ok(n === 1, `${tag}: property "${label}" shown once`, String(n));
    }
    ok(c(DESC) === 1, `${tag}: description shown once`, String(c(DESC)));
    ok(info.progress.includes(expected), `${tag}: progress matches server (${done}/${scope} = ${expected})`, info.progress || 'no progress element');
    ok(/In Progress\s*30\b/.test(info.text) && /Todo\s*40\b/.test(info.text) && /Done\s*36\b/.test(info.text), `${tag}: per-status issue counts match the server`);
    ok(info.issueRows === 0, `${tag}: overview summarizes instead of listing issues`, String(info.issueRows));
    ok(info.height < 1600, `${tag}: overview height stays bounded with 120 issues`, `${info.height}px`);
    ok(/View all 120 issues/.test(info.text), `${tag}: link to the full Issues tab with the total`);
    ok(c(UPDATE) === 1, `${tag}: latest project update on the overview`, String(c(UPDATE)));
    ok(!info.oldCard, `${tag}: old IssueList card gone`);
    ok(info.tabsAbove, `${tag}: Overview/Activity/Issues tabs sit above the content`);
    ok(!info.hscroll, `${tag}: no horizontal page scroll`);
    await ctx.close();
  }
  ok(errors.length === 0, 'no page errors', errors.join(' | '));
  await browser.close();
  server.kill();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
