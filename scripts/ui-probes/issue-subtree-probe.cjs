// Sub-task tree probe (HOJA-33): a parent's sub-tasks must read as one group with a real
// progress count, and a sub-task must show the path back to its parent.
//
// Runs against a seeded local server on the built bundle:
//   cd frontend && npm run build
//   node scripts/ui-probes/issue-subtree-probe.cjs
// SHOTS=<dir> saves screenshots; WIDTH/HEIGHT override the viewport.
//
// What it measures, and why each assertion is falsifiable:
//   list  : the child row carries data-depth="1" and follows its parent, the grandchild is
//           data-depth="2", and the parent's pill reads the completed/total from the server
//           (2/3), not a hardcoded 0.
//   board : the child card is nested under the parent in the same column (data-depth), a
//           child whose parent sits in another column keeps a parent chip, and the parent
//           card carries the same rollup.
//   detail: the Sub-tasks group exposes a count and one row per child, each row showing the
//           identifier the server allocated; the child's breadcrumb links back to the parent.
//   ids   : every identifier rendered equals the server's identifier for that task. The
//           recursive TEAM-NUMBER-LETTER-NUMBER scheme is NOT in this build, so the probe
//           fails if a client ever invents one.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '../..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, {recursive: true});
const dir = fs.mkdtempSync(path.join(scratch, 'issue-subtree-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_subtree_probe_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], {cwd: repo, env: {...process.env, PYTHONPATH: repo}, encoding: 'utf8'});
  assert.equal(r.status, 0, r.stderr);
};
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19400 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  {cwd: repo, env: {...process.env, HOJA_WEB_ROOT: web}, stdio: 'ignore'});
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, {method: 'POST', headers: {'content-type': 'application/json', authorization: `Bearer ${token}`}, body: JSON.stringify({query, variables})});
  const b = await r.json();
  if (b.errors) throw new Error(b.errors[0].message);
  return b.data;
};
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => {
  cond ? pass++ : fail++;
  console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`);
};
const shots = process.env.SHOTS;
const W = Number(process.env.WIDTH || 1440), H = Number(process.env.HEIGHT || 900);

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
  const team = d.teams.nodes[0];
  const st = t => team.states.nodes.find(s => s.type === t).id;
  const create = async (title, input) => {
    const r = await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier}}}', {i: {title, teamId: team.id, ...input}});
    return r.issueCreate.issue;
  };
  // Parent with two children and a grandchild. Two children are open in the parent's column,
  // one is completed so the rollup has a non-zero numerator.
  const parent = await create('Sub-task tree parent', {stateId: st('unstarted')});
  const childA = await create('Child A open', {stateId: st('unstarted'), parentId: parent.id});
  const childB = await create('Child B done', {stateId: st('completed'), parentId: parent.id});
  const grand = await create('Grandchild of A', {stateId: st('unstarted'), parentId: childA.id});

  const truth = await gql(`query($id:String!){issue(id:$id){identifier children{nodes{id identifier title state{type} parent{identifier}}}}}`, {id: parent.id});
  const serverChildren = truth.issue.children.nodes;
  ok(serverChildren.length === 2, 'server: parent has two direct children', String(serverChildren.length));
  ok(serverChildren.some(c => c.identifier === childB.identifier && c.state.type === 'completed'), 'server: one child is completed');

  const browser = await chromium.launch({headless: true});
  const ctx = await browser.newContext({viewport: {width: W, height: H}});
  await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  const layoutOf = async l => { await page.addInitScript(v => localStorage.setItem('hoja_issue_layout', v), l); };
  const dump = async () => page.evaluate(() => ({
    depthRows: [...document.querySelectorAll('.my-issue-row')].map(r => ({
      depth: Number(r.getAttribute('data-depth') || 0),
      ident: r.querySelector('code')?.textContent?.trim() || '',
      parent: r.getAttribute('data-parent') || '',
      pill: r.getAttribute('data-rollup') || '',
    })),
    cards: [...document.querySelectorAll('.kanban-card-wrap')].map(c => ({
      depth: Number(c.getAttribute('data-depth') || 0),
      ident: (c.querySelector('.kanban-card-open small')?.textContent || '').split(' · ')[0].trim(),
      chip: c.querySelector('.kanban-parentchip')?.textContent || '',
      pill: c.querySelector('.kanban-subrollup')?.textContent?.trim() || '',
    })),
  }));

  // ---- list -------------------------------------------------------------
  await layoutOf('List');
  await page.goto(`${base}/workspace/team/${team.key.toLowerCase()}/active`, {waitUntil: 'networkidle'});
  await page.waitForTimeout(500);
  const list = await dump();
  if (shots) { fs.mkdirSync(shots, {recursive: true}); await page.screenshot({path: path.join(shots, `list-${W}.png`), fullPage: true}); }
  const rowsBy = ident => list.depthRows.filter(r => r.ident === ident);
  const parentRow = rowsBy(parent.identifier)[0];
  const childARow = rowsBy(childA.identifier)[0];
  const grandRow = rowsBy(grand.identifier)[0];
  ok(Boolean(parentRow), 'list: parent row rendered', parent.identifier);
  ok(childARow?.depth === 1, 'list: child row nests at depth 1', `depth=${childARow?.depth}`);
  ok(grandRow?.depth === 2, 'list: grandchild nests at depth 2', `depth=${grandRow?.depth}`);
  const parentIdx = list.depthRows.indexOf(parentRow);
  const childAIdx = list.depthRows.indexOf(childARow);
  ok(parentIdx >= 0 && childAIdx === parentIdx + 1, 'list: child directly follows its parent', `${parentIdx}->${childAIdx}`);
  ok(parentRow?.pill === '1/2', "list: parent pill counts real progress (1/2)", parentRow?.pill);

  // ---- board ------------------------------------------------------------
  await layoutOf('Board');
  await page.goto(`${base}/workspace/team/${team.key.toLowerCase()}/active`, {waitUntil: 'networkidle'});
  await page.waitForTimeout(500);
  const board = await dump();
  if (shots) await page.screenshot({path: path.join(shots, `board-${W}.png`), fullPage: true});
  const cardBy = ident => board.cards.filter(c => c.ident === ident);
  const parentCard = cardBy(parent.identifier)[0];
  const childACard = cardBy(childA.identifier)[0];
  const childBCard = cardBy(childB.identifier)[0];
  ok(childACard?.depth === 1, 'board: same-column child card nests at depth 1', `depth=${childACard?.depth}`);
  ok(childBCard?.depth === 0 && childBCard?.chip === parent.identifier, 'board: cross-column child keeps a parent chip', `${childBCard?.chip}`);
  ok(parentCard?.pill === '1/2', 'board: parent card shows the same rollup (1/2)', parentCard?.pill);
  const parentCardIdx = board.cards.indexOf(parentCard);
  ok(board.cards.indexOf(childACard) === parentCardIdx + 1, 'board: nested child card follows the parent card');

  // ---- parent detail ----------------------------------------------------
  await page.goto(`${base}/workspace/task/${parent.identifier}/x`, {waitUntil: 'networkidle'});
  await page.waitForTimeout(700);
  const detail = await page.evaluate(() => ({
    subCount: document.querySelector('[data-subcount]')?.getAttribute('data-subcount') || '',
    pill: document.querySelector('.lin-subsec .lin-subissue-pill')?.textContent?.trim() || '',
    rows: [...document.querySelectorAll('.lin-subrow')].map(r => ({ident: r.querySelector('.ident')?.textContent || '', title: r.querySelector('.t')?.textContent || ''})),
  }));
  if (shots) await page.screenshot({path: path.join(shots, `detail-parent-${W}.png`), fullPage: true});
  ok(detail.subCount === '2', 'detail: sub-task group exposes a count (2)', detail.subCount);
  ok(detail.pill === '1/2', 'detail: group pill is 1/2', detail.pill);
  const shown = detail.rows.map(r => r.ident).sort();
  const expected = serverChildren.map(c => c.identifier).sort();
  ok(JSON.stringify(shown) === JSON.stringify(expected), 'detail: each sub-task row shows the server identifier', `${JSON.stringify(shown)} vs ${JSON.stringify(expected)}`);

  // ---- child detail -----------------------------------------------------
  await page.goto(`${base}/workspace/task/${childA.identifier}/x`, {waitUntil: 'networkidle'});
  await page.waitForTimeout(700);
  const childDetail = await page.evaluate(() => ({
    path: document.querySelector('[data-parent-path]')?.getAttribute('data-parent-path') || '',
    crumb: document.querySelector('.lin-crumb')?.textContent || '',
    own: document.querySelector('.lin-crumb .crumb-id')?.textContent || '',
    subRows: [...document.querySelectorAll('.lin-subrow .ident')].map(e => e.textContent),
  }));
  if (shots) await page.screenshot({path: path.join(shots, `detail-child-${W}.png`), fullPage: true});
  ok(childDetail.path === parent.identifier, 'child detail: breadcrumb links back to the parent', childDetail.path);
  ok(childDetail.crumb.includes(parent.identifier) && childDetail.own === childA.identifier, 'child detail: path reads parent › child', childDetail.crumb.replace(/\s+/g, ' ').trim());
  ok(childDetail.subRows.includes(grand.identifier), 'child detail: grandchild is listed in its own Sub-tasks group', JSON.stringify(childDetail.subRows));

  ok(errors.length === 0, 'no page errors', errors.join(' | '));
  await browser.close();
  server.kill();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
