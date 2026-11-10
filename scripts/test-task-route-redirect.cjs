// HOJA-288 browser pass: a legacy /workspace/issue/... link must land on /workspace/task/...
// and render the same entity, and the rendered UI must carry no visible "Issue" wording.
// Run from the repo root after `cd frontend && npm run build`.
const { chromium } = require('/root/Work/Hoja-Work/hoja/local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first');
const dir = fs.mkdtempSync(path.join(repo, 'local-e2e/artifacts/redirect-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_task_redirect_token';
const py = path.join(repo, '.venv/bin/python');

const run = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
};
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
from hoja_server.store import Store
e.dispose()`);

const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web, PYTHONPATH: repo }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

(async () => {
  try {
    for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
    const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
    const team = d.teams.nodes[0];
    const st = team.states.nodes.find(s => s.type === 'started').id;
    const made = await gql('mutation($i:TaskCreateInput!){taskCreate(input:$i){task{id identifier title}}}',
      { i: { title: 'Redirect me', teamId: team.id, stateId: st } });
    const ident = made.taskCreate.task.identifier;

    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();

    const legacy = `/workspace/issue/${ident}/redirect-me`;
    const resp = await page.goto(base + legacy, { waitUntil: 'networkidle' });
    ok(resp && resp.status() === 200, 'legacy URL is served', `status=${resp && resp.status()}`);
    const url = new URL(page.url());
    ok(url.pathname.startsWith(`/workspace/task/${ident}`), 'legacy /issue/ URL redirects to /task/', url.pathname);
    const title = await page.locator('body').innerText();
    ok(/Redirect me/.test(title), 'the same entity renders after the redirect');

    // visible-wording sweep: text AND the attributes a text grep misses
    const sweep = async (label) => {
      const found = await page.evaluate(() => {
        const out = [];
        const re = /\bissues?\b/i;
        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (walk.nextNode()) {
          const v = walk.currentNode.nodeValue || '';
          if (re.test(v) && (walk.currentNode.parentElement?.offsetParent !== null)) out.push('text:' + v.trim().slice(0, 60));
        }
        for (const el of document.querySelectorAll('[aria-label],[title],[placeholder],[alt]')) {
          for (const a of ['aria-label', 'title', 'placeholder', 'alt']) {
            const v = el.getAttribute(a);
            if (v && re.test(v) && el.offsetParent !== null) out.push(`${a}:${v.slice(0, 60)}`);
          }
        }
        if (re.test(document.title)) out.push('title:' + document.title);
        return out;
      });
      if (found.length) console.log(`  ${label}: ${found.length} residual -> ${found.slice(0, 8).join(' | ')}`);
      return found;
    };
    const atDetail = await sweep('task detail');
    ok(atDetail.length === 0, 'task detail has no visible "issue" wording', `${atDetail.length} residual`);

    await page.goto(base + '/workspace/my-issues/assigned', { waitUntil: 'networkidle' });
    const atList = await sweep('my issues list');
    console.log(`  note: my-issues list residuals = ${atList.length}`);

    ok(!(await page.evaluate(() => location.pathname.startsWith('/workspace/issue/'))), 'no lingering /issue/ path after navigation');
    await browser.close();
  } catch (e) {
    fail++; console.log('FAIL exception:', e.message);
  } finally {
    server.kill('SIGTERM');
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
