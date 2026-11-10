// HOJA-288: rendered-DOM wording sweep across every reachable route.
// A source grep cannot tell visible copy from dead code, and it misses attributes.
const { chromium } = require('/root/Work/Hoja-Work/hoja/local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs'); const path = require('node:path'); const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
const dir = fs.mkdtempSync(path.join(repo, 'local-e2e/artifacts/sweep-'));
const db = path.join(dir, 'p.db'); const token = 'hoja_wording_sweep';
const py = path.join(repo, '.venv/bin/python');
const run = c => { const r = spawnSync(py, ['-c', c], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);
const port = 19700 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web, PYTHONPATH: repo }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (q, v) => { const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query: q, variables: v }) }); const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data; };

(async () => {
  try {
    for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
    const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
    const team = d.teams.nodes[0];
    const st = team.states.nodes.find(s => s.type === 'started').id;
    const made = await gql('mutation($i:TaskCreateInput!){taskCreate(input:$i){task{id identifier}}}', { i: { title: 'Sweep task', teamId: team.id, stateId: st } });
    const ident = made.taskCreate.task.identifier;
    const p = await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}', { i: { name: 'Sweep project', teamIds: [team.id] } });

    const k = team.key.toLowerCase();
    const routes = [
      `/workspace/task/${ident}/sweep-task`,
      `/workspace/my-issues/assigned`, `/workspace/my-issues/created`, `/workspace/my-issues/activity`,
      `/workspace/inbox/other`, `/workspace/team/${k}/active`, `/workspace/team/${k}/home`,
      `/workspace/team/${k}/cycles`, `/workspace/project/${p.projectCreate.project.id}`,
      `/workspace/views/issues`, `/workspace/board`, `/workspace/pulse/all`,
      `/workspace/initiatives/active`, `/workspace/cycles`, `/workspace/members`,
      `/workspace/team/${k}/views/issues`,
      `/workspace/settings/labels`, `/workspace/settings/preferences`,
      `/workspace/team/${k}/cycles/settings`,
    ];
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const all = [];
    for (const r of routes) {
      try { await page.goto(base + r, { waitUntil: 'networkidle', timeout: 15000 }); } catch {}
      await wait(150);
      const found = await page.evaluate(() => {
        const out = []; const re = /\bissues?\b/i;
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (w.nextNode()) { const v = w.currentNode.nodeValue || ''; const el = w.currentNode.parentElement;
          if (re.test(v) && el && el.offsetParent !== null) out.push('text: ' + v.trim().slice(0, 80)); }
        for (const el of document.querySelectorAll('[aria-label],[title],[placeholder],[alt]')) {
          for (const a of ['aria-label', 'title', 'placeholder', 'alt']) { const v = el.getAttribute(a);
            if (v && re.test(v) && el.offsetParent !== null) out.push(`${a}: ${v.slice(0, 80)}`); } }
        if (re.test(document.title)) out.push('document.title: ' + document.title);
        return [...new Set(out)];
      });
      for (const f of found) all.push({ route: r, found: f });
    }
    // the create-task modal is never reachable by URL, so open it explicitly
    try {
      await page.goto(base + '/workspace/my-issues/assigned', { waitUntil: 'networkidle', timeout: 15000 });
      await page.locator('button[aria-label="Create new task"], button[aria-label="Create task"]').first().click({ timeout: 4000 });
      await wait(400);
      const modal = await page.evaluate(() => {
        const out = []; const re = /\bissues?\b/i;
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (w.nextNode()) { const v = w.currentNode.nodeValue || ''; if (re.test(v) && w.currentNode.parentElement?.offsetParent !== null) out.push('text: ' + v.trim().slice(0, 80)); }
        for (const el of document.querySelectorAll('[aria-label],[title],[placeholder]')) {
          for (const a of ['aria-label', 'title', 'placeholder']) { const v = el.getAttribute(a); if (v && re.test(v) && el.offsetParent !== null) out.push(`${a}: ${v.slice(0, 80)}`); } }
        return [...new Set(out)];
      });
      for (const f of modal) all.push({ route: 'create-task modal', found: f });
    } catch (e) { console.log('note: modal sweep skipped:', e.message.split('\n')[0]); }
    await browser.close();
    const uniq = [...new Set(all.map(a => a.found))].sort();
    fs.writeFileSync(path.join(repo, 'artifacts/hoja-288-wording-residuals.txt'), uniq.join('\n') + '\n');
    console.log(`routes: ${routes.length}  residual strings (unique): ${uniq.length}`);
    for (const u of uniq) { const r = all.find(a => a.found === u); console.log(`  [${r ? r.route : '?'}] ${u}`); }
    process.exit(uniq.length ? 1 : 0);
  } catch (e) { console.log('ERROR', e.message); process.exit(2); }
  finally { server.kill('SIGTERM'); }
})();
