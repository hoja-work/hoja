// Comment permissions on the real server and the built UI, for an admin and two members.
// Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-comment-permissions.cjs
//   WIDTH=390 HEIGHT=844 SHOTS=artifacts/comment-permissions node scripts/test-comment-permissions.cjs
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
const dir = fs.mkdtempSync(path.join(scratch, 'comment-permissions-'));
const db = path.join(dir, 'p.db');
const adminToken = 'cliniar_comment_perm_admin_token';
const py = path.join(repo, '.venv/bin/python');
const python = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout.trim();
};
const seeded = JSON.parse(python(`import json
from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
t=seed_tenant(e, org_name='Acme', team_key='PERM', team_name='Perm', user_name='Ada Admin', user_email='ada@example.test', token=${JSON.stringify(adminToken)}, demo_issues=False)
print(json.dumps({'org': t['org_id']})); e.dispose()`));

const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const raw = async (token, query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  return r.json();
};
const gql = async (token, query, variables) => { const b = await raw(token, query, variables); if (b.errors) throw new Error(b.errors[0].message); return b.data; };
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const shots = process.env.SHOTS;
const W = Number(process.env.WIDTH || 1440), H = Number(process.env.HEIGHT || 900);

const CREATE = 'mutation($i:CommentCreateInput!){commentCreate(input:$i){comment{id}}}';
const UPDATE = 'mutation($id:String!,$i:CommentUpdateInput!){commentUpdate(id:$id,input:$i){success}}';
const DELETE = 'mutation($id:String!){commentDelete(id:$id){success}}';
const BODIES = 'query($id:String!){issue(id:$id){comments{nodes{id body editedAt}}}}';

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql(adminToken, '{viewer{id}}'); break; } catch { await wait(250); } }
  const team = (await gql(adminToken, '{teams{nodes{id key}}}')).teams.nodes[0];
  const members = {};
  for (const name of ['Mia Member', 'Max Member']) {
    const u = (await gql(adminToken, 'mutation($i:MemberInviteInput!){memberInvite(input:$i){user{id}}}',
      { i: { name, email: `${name.split(' ')[0].toLowerCase()}@example.test`, teamIds: [team.id] } })).memberInvite.user;
    const key = python(`from hoja_server.db import make_engine
from hoja_server.store import Store
print(Store(make_engine(${JSON.stringify(db)})).create_api_key(${JSON.stringify(seeded.org)}, ${JSON.stringify(u.id)}, 'e2e', 'read_write', None)[1])`);
    members[name.split(' ')[0]] = { id: u.id, token: key, name };
  }
  const { Mia, Max } = members;
  const issue = (await gql(adminToken, 'mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier}}}',
    { i: { title: 'Comment permissions', teamId: team.id } })).issueCreate.issue;
  const say = async (token, body) => (await gql(token, CREATE, { i: { issueId: issue.id, body } })).commentCreate.comment.id;
  const mia = await say(Mia.token, 'Mia wrote this');
  const ada = await say(adminToken, 'Ada wrote this');
  const max = await say(Max.token, 'Max wrote this');
  const doomed = await say(Mia.token, 'Mia will lose this one');

  // ---- wire: the policy, independent of any UI ----
  const hijack = async (token, id) => (await raw(token, UPDATE, { id, i: { body: 'HIJACK' } })).errors?.[0];
  for (const [who, token, id] of [['Max→Mia', Max.token, mia], ['Mia→Ada', Mia.token, ada], ['Ada(admin)→Mia', adminToken, mia]]) {
    const e = await hijack(token, id);
    ok(e?.extensions?.code === 'FORBIDDEN' && e.message === "Only the comment's author can edit it.", `wire: ${who} edit is FORBIDDEN`, e?.message);
  }
  const maxDel = (await raw(Max.token, DELETE, { id: mia })).errors?.[0];
  ok(maxDel?.extensions?.code === 'FORBIDDEN', 'wire: Max cannot delete Mia\'s comment', maxDel?.message);
  const bodies = async () => Object.fromEntries((await gql(adminToken, BODIES, { id: issue.id })).issue.comments.nodes.map(c => [c.id, c]));
  ok(!Object.values(await bodies()).some(c => c.body === 'HIJACK'), 'wire: no body was changed by a denied edit');

  // ---- UI ----
  const browser = await chromium.launch({ headless: true });
  const issueUrl = `${base}/workspace/issue/${issue.identifier}/comment-permissions`;
  const open = async token => {
    const ctx = await browser.newContext({ viewport: { width: W, height: H } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(issueUrl, { waitUntil: 'networkidle' });
    await page.waitForSelector('.lin-comment .md', { timeout: 15000 });
    return { page, errors, ctx };
  };
  const article = (page, text) => page.locator('article.lin-comment', { hasText: text });
  const controls = async (page, text) => ({
    edit: await article(page, text).getByRole('button', { name: 'Edit comment' }).count(),
    del: await article(page, text).getByRole('button', { name: 'Delete comment' }).count(),
  });
  const expect = async (page, who, text, edit, del) => {
    const c = await controls(page, text);
    ok(c.edit === edit && c.del === del, `ui(${who}): "${text}" shows edit=${edit} delete=${del}`, JSON.stringify(c));
  };

  const asMia = await open(Mia.token);
  await expect(asMia.page, 'Mia', 'Mia wrote this', 1, 1);
  await expect(asMia.page, 'Mia', 'Ada wrote this', 0, 0);
  await expect(asMia.page, 'Mia', 'Max wrote this', 0, 0);
  if (shots) { fs.mkdirSync(shots, { recursive: true }); await asMia.page.screenshot({ path: path.join(shots, `member-${W}.png`), fullPage: true }); }

  // Author edits through the UI; the server stores it and the comment is marked edited.
  await article(asMia.page, 'Mia wrote this').getByRole('button', { name: 'Edit comment' }).click();
  await asMia.page.getByLabel('Edit comment').fill('Mia wrote this, then fixed a typo');
  await asMia.page.getByRole('button', { name: 'Save', exact: true }).click();
  await article(asMia.page, 'fixed a typo').locator('.lin-comment-edited').waitFor({ timeout: 10000 });
  const afterEdit = (await bodies())[mia];
  ok(afterEdit.body === 'Mia wrote this, then fixed a typo' && Boolean(afterEdit.editedAt), 'ui(Mia): own edit persisted with editedAt');
  ok(await article(asMia.page, 'fixed a typo').locator('.lin-comment-edited').innerText() === '(edited)', 'ui(Mia): "(edited)" marker shown');

  // Race: an admin removes Mia's comment while her editor is open; the UI reports it and drops the comment.
  await article(asMia.page, 'Mia will lose this one').getByRole('button', { name: 'Edit comment' }).click();
  await gql(adminToken, DELETE, { id: doomed });
  await asMia.page.getByLabel('Edit comment').fill('too late');
  await asMia.page.getByRole('button', { name: 'Save', exact: true }).click();
  const alert = asMia.page.locator('.lin-error[role=alert]');
  await alert.waitFor({ timeout: 10000 });
  ok((await alert.innerText()).includes('no longer exists'), 'ui(Mia): saving a comment deleted meanwhile shows an error', await alert.innerText());
  await article(asMia.page, 'Mia will lose this one').waitFor({ state: 'detached', timeout: 10000 }).catch(() => undefined);
  ok(await article(asMia.page, 'Mia will lose this one').count() === 0 && await asMia.page.getByLabel('Edit comment').count() === 0,
    'ui(Mia): the deleted comment and its editor disappear after the refresh');
  ok(!asMia.errors.length, 'ui(Mia): no page errors', asMia.errors.join(' | '));

  const asAda = await open(adminToken);
  await expect(asAda.page, 'Ada admin', 'Ada wrote this', 1, 1);
  await expect(asAda.page, 'Ada admin', 'fixed a typo', 0, 1);
  await expect(asAda.page, 'Ada admin', 'Max wrote this', 0, 1);
  if (shots) await asAda.page.screenshot({ path: path.join(shots, `admin-${W}.png`), fullPage: true });
  // Admin moderation through the UI names whose comment is being removed.
  asAda.page.once('dialog', d => d.accept());
  await article(asAda.page, 'Max wrote this').getByRole('button', { name: 'Delete comment' }).click();
  const dialog = asAda.page.getByRole('alertdialog').or(asAda.page.getByRole('dialog'));
  await dialog.first().waitFor({ timeout: 5000 });
  const prompt = await dialog.first().innerText();
  ok(prompt.includes("Max Member's comment"), 'ui(Ada admin): delete confirm names the author', prompt.split('\n')[0]);
  await dialog.first().getByRole('button', { name: /delete|confirm|ok/i }).first().click();
  await article(asAda.page, 'Max wrote this').waitFor({ state: 'detached', timeout: 10000 });
  // The row hides optimistically; the server write lands a moment later.
  let gone = false;
  for (let i = 0; i < 40 && !gone; i++) { gone = !(max in await bodies()); if (!gone) await wait(100); }
  const adaAlert = await asAda.page.locator('.lin-error[role=alert]').count();
  ok(gone && !adaAlert && await article(asAda.page, 'Max wrote this').count() === 0,
    'ui(Ada admin): moderation delete persisted', `serverGone=${gone} alert=${adaAlert}`);
  ok(!asAda.errors.length, 'ui(Ada admin): no page errors', asAda.errors.join(' | '));

  const asMax = await open(Max.token);
  await expect(asMax.page, 'Max', 'fixed a typo', 0, 0);
  await expect(asMax.page, 'Max', 'Ada wrote this', 0, 0);

  await browser.close();
  server.kill('SIGKILL');
  console.log(`SUMMARY: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(async e => { console.error(e); server.kill('SIGKILL'); process.exit(1); });
