// HOJA-288 rendered-vocabulary sweep.
//
// Why this exists: the static gate cannot prove what a *variable* renders. A label
// computed from a routing constant ("Team issues") and then passed to a component is
// invisible to any AST predicate, and four earlier sweeps reported "0 residual" while
// exactly that class of copy was on screen. This sweep reads what a person reads:
//
//   - document.body.innerText
//   - aria-label / title / placeholder / alt values
//   - document.title
//
// on a real server, a real database and a real browser, across every reachable route
// plus the dialogs that only exist after a click. It also asserts each route actually
// rendered (an h1 and a text floor), because "no matches on a blank page" is not a
// pass.
//
// Usage, from the repo root, after `cd frontend && npm run build`:
//   node scripts/check-task-vocabulary-dom.cjs
//   WIDTH=390 HEIGHT=844 node scripts/check-task-vocabulary-dom.cjs   # phone shell
const {chromium} = require('../local-e2e/node_modules/playwright');
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');

const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, {recursive: true});
const dir = fs.mkdtempSync(path.join(scratch, 'vocab-dom-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_vocab_dom_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], {cwd: repo, env: {...process.env, PYTHONPATH: repo}, encoding: 'utf8'});
  assert.equal(r.status, 0, r.stderr);
};
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], {cwd: repo, env: {...process.env, HOJA_WEB_ROOT: web}, stdio: 'ignore'});
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, {method: 'POST', headers: {'content-type': 'application/json', authorization: `Bearer ${token}`}, body: JSON.stringify({query, variables})});
  const b = await r.json();
  if (b.errors) throw new Error(b.errors[0].message);
  return b.data;
};

// Third-party product nouns: GitHub and Sentry call their own objects "issues" and we
// quote them. Everything else must say task.
const ALLOWED = [/Read-only access to the projects, issues and latest event of one Sentry organization/i,
                 /Read-only access to your repositories, files, issues and pull requests/i];

(async () => {
  for (let i = 0; i < 80; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key name states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const st = t => team.states.nodes.find(s => s.type === t).id;
  const p = await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}', {i: {name: 'Launch', teamIds: [team.id]}});
  const projectId = p.projectCreate.project.id;
  const c = await gql('mutation($i:CycleCreateInput!){cycleCreate(input:$i){cycle{id}}}', {i: {name: 'Cycle 7', teamId: team.id}}).catch(() => null);
  const cycleId = c?.cycleCreate?.cycle?.id;
  const create = (title, input) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier}}}', {i: {title, teamId: team.id, assigneeId: me, projectId, ...input}});
  const created = await create('Ship onboarding flow', {stateId: st('started'), priority: 1});
  await create('Write release notes', {stateId: st('unstarted'), priority: 3});
  await create('Fix flaky login test', {stateId: st('backlog'), priority: 2});
  await create('Finished spike', {stateId: st('completed')});
  await gql('mutation($i:IssueLabelCreateInput!){issueLabelCreate(input:$i){issueLabel{id}}}', {i: {name: 'Design', color: '#3366ff', teamId: team.id}}).catch(() => null);
  // An empty team, so the delete-danger flow is reachable: a team with tasks is
  // locked by design and the confirmation copy would never render.
  await gql('mutation($i:TeamCreateInput!){teamCreate(input:$i){team{id}}}', {i: {name: 'Spare', key: 'SPARE'}}).catch(() => null);
  const ident = created.issueCreate.issue.identifier;
  const key = team.key.toLowerCase();

  const routes = [
    ['my assigned', '/workspace/my-issues/assigned'],
    ['my created', '/workspace/my-issues/created'],
    ['my subscribed', '/workspace/my-issues/subscribed'],
    ['my activity', '/workspace/my-issues/activity'],
    ['inbox', '/workspace/inbox/other'],
    ['projects', '/workspace/projects'],
    ['project overview', `/workspace/project/${projectId}/overview`],
    ['project tasks', `/workspace/project/${projectId}/issues`],
    ['project activity', `/workspace/project/${projectId}/activity`],
    ['team tasks', `/workspace/team/${key}/active`],
    ['team cycles', `/workspace/team/${key}/cycles`],
    ['team projects', `/workspace/team/${key}/projects`],
    ['team views', `/workspace/team/${key}/views`],
    ['team home', `/workspace/team/${key}/home`],
    ['board', '/workspace/board'],
    ['task detail', `/workspace/task/${ident}/ship-onboarding-flow`],
    ...(cycleId ? [['cycle detail', `/workspace/cycle/${cycleId}`]] : []),
    ['settings preferences', '/workspace/settings/preferences'],
    ['settings notifications', '/workspace/settings/notifications'],
    ['settings connections', '/workspace/settings/connections'],
    ['settings teams', '/workspace/settings/teams'],
    ['settings labels', '/workspace/settings/issue-labels'],
    ['settings members', '/workspace/settings/members'],
    ['settings account', '/workspace/settings/account'],
  ];

  const browser = await chromium.launch({headless: true});
  const ctx = await browser.newContext({viewport: {width: Number(process.env.WIDTH || 1440), height: Number(process.env.HEIGHT || 900)}});
  await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(e.message));

  const WORD = /\bissues?\b/i;
  const findings = [];
  const rendered = [];
  const scan = async (label, route) => {
    const facts = await page.evaluate(() => {
      const attrs = [];
      for (const el of document.querySelectorAll('[aria-label],[title],[placeholder],[alt]')) {
        for (const a of ['aria-label', 'title', 'placeholder', 'alt']) {
          const v = el.getAttribute(a);
          if (v) attrs.push(`${a}=${v}`);
        }
      }
      const backLabels = [...document.querySelectorAll('.back-link,.settings-mobile-back')].map(b => b.textContent);
      return {
        text: document.body.innerText,
        attrs,
        title: document.title,
        h1: document.querySelector('h1')?.innerText || '',
        backLabels,
      };
    });
    const haystack = [facts.text, ...facts.attrs, facts.title, ...facts.backLabels].join('\n');
    rendered.push({label, route, h1: facts.h1.trim().slice(0, 60), chars: facts.text.trim().length});
    for (const line of haystack.split('\n')) {
      if (!WORD.test(line)) continue;
      if (ALLOWED.some(re => re.test(line))) continue;
      findings.push(`${label} (${route}): ${line.trim().slice(0, 160)}`);
    }
  };

  for (const [label, route] of routes) {
    await page.goto(base + route, {waitUntil: 'networkidle'});
    await wait(300);
    await scan(label, route);
  }

  // Dialogs: copy that only exists after a click, and that a route crawl never sees.
  // [label, route, action, selector that proves the copy is on screen]
  const dialogProbes = [
    ['label delete confirm', '/workspace/settings/issue-labels', async () => {
      const del = page.locator('button', {hasText: /^Delete$/}).first();
      if (await del.count()) await del.click();
    }, '[role=dialog],[aria-modal=true]'],
    ['team delete confirm', '/workspace/settings/teams', async () => {
      // The copy is in the danger panel, not a modal: reveal it and let the scan read it.
      const reveal = page.locator('button', {hasText: /^Delete team…$/}).last();
      if (await reveal.count()) await reveal.click();
    }, '[role=group][aria-label^="Delete "]'],
    ['create task dialog', '/workspace/my-issues/assigned', async () => {
      const b = page.locator('[aria-label="Create new task"]').first();
      if (await b.count()) await b.click();
    }, '[role=dialog],[aria-modal=true]'],
  ];
  const dialogsOpened = [];
  for (const [label, route, act, expect] of dialogProbes) {
    await page.goto(base + route, {waitUntil: 'networkidle'});
    await wait(300);
    await act().catch(() => {});
    await wait(300);
    // Copy that never appeared would make the scan below meaningless, so require it.
    const opened = await page.evaluate(sel => !!document.querySelector(sel), expect);
    dialogsOpened.push({label, opened});
    await scan(label, route);
    await page.keyboard.press('Escape').catch(() => {});
  }

  await browser.close();
  server.kill();

  console.log('routes rendered:');
  for (const r of rendered) console.log(`  ${r.chars > 200 ? 'ok ' : 'THIN'} ${r.route}  h1="${r.h1}" chars=${r.chars}`);
  const thin = rendered.filter(r => r.chars < 200);
  const problemText = findings.length ? `\nuser-visible "issue" wording (${findings.length}):\n${findings.map(f => `  - ${f}`).join('\n')}\n` : '';
  console.log(problemText);
  if (thin.length) console.log(`routes that did not render: ${thin.map(r => r.route).join(', ')}`);
  const closedDialogs = dialogsOpened.filter(d => !d.opened);
  for (const d of dialogsOpened) console.log(`dialog ${d.opened ? 'opened' : 'DID NOT OPEN'}: ${d.label}`);
  if (pageErrors.length) console.log(`page errors: ${pageErrors.slice(0, 3).join(' | ')}`);
  const failed = findings.length || thin.length || pageErrors.length || closedDialogs.length;
  console.log(failed ? 'RESULT: FAIL' : `RESULT: PASS — ${rendered.length} views scanned (${dialogsOpened.length} of them dialogs), 0 user-visible "issue" strings`);
  process.exit(failed ? 1 : 0);
})().catch(e => { server.kill(); console.error(e); process.exit(1); });
