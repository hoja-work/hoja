// Issue surface parity: My issues, team issues and project issues render the same list and board.
// Run from the repo root after `cd frontend && npm run build`: node scripts/test-issue-surface-parity.cjs
// (WIDTH=390 HEIGHT=844 for a phone viewport; SHOTS=dir to save screenshots).
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
const dir = fs.mkdtempSync(path.join(scratch, 'surface-parity-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_surface_parity_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);
const port = 19300 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const shots = process.env.SHOTS;
const W = Number(process.env.WIDTH || 1440), H = Number(process.env.HEIGHT || 900);

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const st = t => team.states.nodes.find(s => s.type === t).id;
  const p = await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}', { i: { name: 'Launch', teamIds: [team.id] } });
  const projectId = p.projectCreate.project.id;
  const create = (title, input) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}', { i: { title, teamId: team.id, assigneeId: me, projectId, ...input } });
  await create('Ship onboarding flow', { stateId: st('started'), priority: 1 });
  await create('Write release notes', { stateId: st('unstarted'), priority: 3 });
  await create('Fix flaky login test', { stateId: st('backlog'), priority: 2 });
  await create('Finished spike', { stateId: st('completed') });

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const surfaces = [
    ['my', `/workspace/my-issues/assigned`],
    ['team', `/workspace/team/${team.key.toLowerCase()}/active`],
    ['project', `/workspace/project/${projectId}/issues`],
  ];
  const sig = {};
  for (const layout of ['List', 'Board']) {
    for (const [name, url] of surfaces) {
      await ctx.addInitScript(l => localStorage.setItem('hoja_issue_layout', l), layout);
      await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(400);
      const info = await page.evaluate(() => ({
        view: !!document.querySelector('.my-issues-view'),
        rows: document.querySelectorAll('.my-issue-row').length,
        rowTitles: [...document.querySelectorAll('.my-issue-row .my-issue-title')].map(e => e.textContent).sort(),
        groups: [...document.querySelectorAll('.my-issue-group>header>b')].map(e => e.textContent),
        cards: document.querySelectorAll('.kanban-card').length,
        columns: [...document.querySelectorAll('.kanban-column>header>b')].map(e => e.textContent),
        toolbar: [...document.querySelectorAll('.my-issue-toolbar button')].map(b => b.getAttribute('aria-label')),
        rowHeight: document.querySelector('.my-issue-row')?.getBoundingClientRect().height || 0,
        hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      }));
      sig[`${name}:${layout}`] = info;
      if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: path.join(shots, `${name}-${layout.toLowerCase()}-${W}.png`) }); }
      ok(info.view, `${name} ${layout}: renders the My issues view`);
      ok(!info.hscroll, `${name} ${layout}: no horizontal page scroll`);
      if (layout === 'List') ok(info.rows === 4, `${name} List: four issue rows`, String(info.rows));
      else ok(info.cards === 4, `${name} Board: four kanban cards`, String(info.cards));
    }
    const m = sig[`my:${layout}`];
    for (const name of ['team', 'project']) {
      const s = sig[`${name}:${layout}`];
      ok(JSON.stringify(s.toolbar) === JSON.stringify(m.toolbar), `${name} ${layout}: same toolbar as My issues`, JSON.stringify(s.toolbar));
      if (layout === 'List') {
        ok(JSON.stringify(s.groups) === JSON.stringify(m.groups), `${name} List: same groups as My issues`, JSON.stringify(s.groups));
        ok(JSON.stringify(s.rowTitles) === JSON.stringify(m.rowTitles), `${name} List: same rows as My issues`);
        ok(Math.abs(s.rowHeight - m.rowHeight) < 0.5, `${name} List: same row height`, `${s.rowHeight} vs ${m.rowHeight}`);
      } else ok(JSON.stringify(s.columns) === JSON.stringify(m.columns), `${name} Board: same columns as My issues`, JSON.stringify(s.columns));
    }
  }
  ok(errors.length === 0, 'no page errors', errors.join(' | '));
  await browser.close();
  server.kill();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
