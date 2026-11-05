// Markdown on the real surfaces: seeded server + built frontend + GraphQL ground truth.
// Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-markdown-surfaces.cjs            (1440x900)
//   WIDTH=390 HEIGHT=844 node scripts/test-markdown-surfaces.cjs
//   SHOTS=artifacts/markdown node scripts/test-markdown-surfaces.cjs
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
const dir = fs.mkdtempSync(path.join(scratch, 'markdown-surfaces-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_markdown_surfaces_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => { const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }); assert.equal(r.status, 0, r.stderr); };
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
e.dispose()`);

const port = 19400 + Math.floor(Math.random() * 90);
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

/* Bodies a team would actually write. Every construct is one CommonMark/GFM feature the app now
 * has to get right in situ, not just in isolation. */
const COMMENT = [
  '# Reproduction',
  '',
  'Steps to reproduce the **login loop**:',
  '',
  '1. Open the *workspace* switcher',
  '2. Pick a second workspace',
  '3. Watch `location.href` bounce back',
  '',
  '- [x] tested on Safari',
  '- [ ] tested on Firefox',
  '',
  '> Workaround: hard reload with TREBOL-2 open.',
  '',
  '```js',
  'if (redirected) location.reload();',
  '```',
  '',
  'See [the runbook](https://example.com/runbook) or `TREBOL-3` (inside code).',
].join('\n');

const TABLE = [
  '| browser | verdict |',
  '| --- | --- |',
  '| Safari | broken |',
  '| Firefox | fine |',
  '| Chrome | fine |',
].join('\n');

const DESCRIPTION = [
  '## Goal',
  '',
  'Keep the local-first store authoritative while the server catches up.',
  '',
  '## Steps',
  '',
  '1. Patch optimistically',
  '2. Revalidate in the background',
  '',
  'Links: [docs](https://example.com/docs) and [issue](/workspace/issue/TREBOL-2).',
].join('\n');

const UPDATE = ['**Update** — we shipped the queue.', '', '- retries are capped at 5', '- dashboards are green', '', '| metric | before |', '| --- | --- |', '| p99 | 900ms |'].join('\n');

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const st = t => team.states.nodes.find(s => s.type === t).id;
  const p = await gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}', { i: { name: 'Markdown project', teamIds: [team.id], description: ['Project **description** with a list:', '', '- one', '- two', '', 'See [docs](https://example.com/p).'].join('\n') } });
  const projectId = p.projectCreate.project.id;
  const made = await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier}}}',
    { i: { title: 'Login redirect loop', teamId: team.id, assigneeId: me, projectId, stateId: st('started'), description: DESCRIPTION } });
  const issue = made.issueCreate.issue;
  for (const body of [COMMENT, TABLE, COMMENT]) {
    await gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', { i: { issueId: issue.id, body } });
  }
  // A comment that is only an attack payload: nothing in the DOM may execute or render it.
  await gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}',
    { i: { issueId: issue.id, body: '<img src=x onerror="window.__pwned=1">\n\n<script>window.__pwned=1</script>' } });
  await gql('mutation($i:ProjectUpdateCreateInput!){projectUpdateCreate(input:$i){success}}', { i: { projectId, body: UPDATE, status: 'on track' } });

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  const page = await ctx.newPage();
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const shot = async name => { if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: path.join(shots, `${name}-${W}.png`), fullPage: true }); } };
  const issueUrl = `${base}/workspace/issue/${issue.identifier}/login-redirect-loop`;

  /* ---------------- issue detail ---------------- */
  await page.goto(issueUrl, { waitUntil: 'networkidle' });
  await page.waitForSelector('.lin-comment .md', { timeout: 15000 });
  await shot('issue');

  const comments = await page.evaluate(() => [...document.querySelectorAll('.lin-comment')].map(c => ({
    text: c.innerText,
    md: !!c.querySelector('.md'),
    bullets: c.querySelectorAll('.md ul > li').length,
    ordered: c.querySelectorAll('.md ol > li').length,
    strong: c.querySelectorAll('.md strong').length,
    code: c.querySelectorAll('.md code').length,
    pre: c.querySelectorAll('.md pre').length,
    quote: c.querySelectorAll('.md blockquote').length,
    tasks: c.querySelectorAll('.md input[type=checkbox]').length,
    headings: [...c.querySelectorAll('.md h1,.md h2,.md h3,.md h4,.md h5,.md h6')].map(h => h.tagName),
    tables: c.querySelectorAll('.md .md-table').length,
    chips: c.querySelectorAll('.md .agent-issue-chip').length,
    imgs: c.querySelectorAll('.md img').length,
    scripts: c.querySelectorAll('.md script').length,
  })));
  ok(comments.length === 4, 'issue: four comments render', `${comments.length}`);
  ok(comments.every(c => c.md), 'issue: every comment body is a markdown body');
  const rich = comments[0];
  ok(rich.bullets === 2 && rich.tasks === 2, 'comment: bullets and GFM task list', `bullets=${rich.bullets} tasks=${rich.tasks}`);
  ok(rich.ordered === 3, 'comment: ordered list keeps three items', `${rich.ordered}`);
  ok(rich.strong >= 1 && rich.code >= 2 && rich.pre === 1, 'comment: bold, inline code and one fenced block', JSON.stringify({ s: rich.strong, c: rich.code, p: rich.pre }));
  ok(rich.quote === 1, 'comment: blockquote');
  ok(rich.headings.length === 1 && rich.headings[0] === 'H3', 'comment: heading demoted to h3, never h1/h2', rich.headings.join(','));
  ok(rich.chips === 1, 'comment: bare TREBOL-2 becomes an issue chip', `${rich.chips}`);
  ok(!rich.text.includes('reload();') || rich.pre === 1, 'comment: fence not rendered as prose');
  const tableComment = comments[1];
  ok(tableComment.tables === 1, 'comment: GFM table gets a scroll box');

  const pageHeads = await page.evaluate(() => ({
    h1: document.querySelectorAll('h1').length,
    hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    tableScrolls: [...document.querySelectorAll('.md-table')].every(t => getComputedStyle(t).overflowX === 'auto'),
  }));
  ok(pageHeads.h1 <= 1, 'issue: at most one h1 on the page (bodies cannot compete)', `${pageHeads.h1}`);
  ok(pageHeads.tableScrolls, 'tables scroll inside their own box');

  const hostile = comments[3];
  ok(hostile.imgs === 0 && hostile.scripts === 0, 'raw HTML in a comment is not turned into nodes', JSON.stringify({ imgs: hostile.imgs, scripts: hostile.scripts }));
  ok(await page.evaluate(() => window.__pwned === undefined), 'raw HTML in a comment does not execute');
  ok(await page.locator('a[href^="javascript:"]').count() === 0, 'no javascript: hrefs in the DOM');

  /* description: rendered, editable, source-preserving */
  const desc = await page.evaluate(() => {
    const el = document.querySelector('.lin-detail-desc .md');
    return el && {
      headings: [...el.querySelectorAll('.md-h')].map(h => h.tagName),
      ordered: el.querySelectorAll('ol > li').length,
      external: [...el.querySelectorAll('a')].filter(a => a.host && a.host !== location.host).map(a => [a.getAttribute('target'), a.getAttribute('rel')]),
      internal: [...el.querySelectorAll('a')].filter(a => !a.host || a.host === location.host).map(a => a.getAttribute('href')),
    };
  });
  ok(!!desc, 'description: rendered as markdown');
  ok(desc.headings.length === 2 && desc.headings.every(t => t === 'H4'), 'description: both ## headings demote to h4', desc.headings.join(','));
  ok(desc.ordered === 2, 'description: ordered list survives', `${desc.ordered}`);
  ok(desc.external.length === 1 && desc.external[0][0] === '_blank' && /noopener/.test(desc.external[0][1]), 'description: external link opens with rel=noopener', JSON.stringify(desc.external));
  ok(desc.internal.length === 1 && desc.internal[0] === '/workspace/issue/TREBOL-2', 'description: workspace link stays internal', JSON.stringify(desc.internal));

  const editBefore = await page.locator('textarea[aria-label="Description"]').count();
  await page.evaluate(() => document.querySelector('.lin-detail-desc a').click());
  await page.waitForTimeout(250);
  ok(editBefore === 0 && await page.locator('textarea[aria-label="Description"]').count() === 0,
    'description: clicking a link does not open the editor');
  await page.goto(issueUrl, { waitUntil: 'networkidle' });
  await page.waitForSelector('.lin-detail-desc .md');
  await page.click('[aria-label="Edit description"]');
  const editorText = await page.inputValue('textarea[aria-label="Description"]');
  ok(editorText.includes('## Steps') && !editorText.includes('<h3'), 'description: editor shows the original markdown source', editorText.slice(0, 40));

  const stats = await page.evaluate(() => window.__hojaMarkdown && window.__hojaMarkdown.stats);
  ok(stats && stats.hits >= 1, 'identical comment bodies are parsed once', JSON.stringify(stats));

  /* ---------------- pulse ---------------- */
  await page.goto(`${base}/workspace/pulse/all`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.pulse-post-body .md', { timeout: 15000 });
  await shot('pulse');
  const pulse = await page.evaluate(() => {
    const el = document.querySelector('.pulse-post-body .md');
    return {
      bullets: el.querySelectorAll('ul > li').length,
      strong: el.querySelectorAll('strong').length,
      tables: el.querySelectorAll('.md-table table').length,
      hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
  ok(pulse.bullets === 2 && pulse.strong >= 1, 'pulse: update body renders a list and bold', JSON.stringify(pulse));
  ok(pulse.tables === 1, 'pulse: table renders');
  ok(!pulse.hscroll, `pulse: no horizontal page scroll at ${W}px`);

  /* ---------------- project overview + activity ---------------- */
  await page.goto(`${base}/workspace/project/${projectId}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.project-overview-description', { timeout: 15000 });
  const overview = await page.evaluate(() => ({
    descriptionMd: !!document.querySelector('.project-overview-description .md'),
    descriptionBullets: document.querySelectorAll('.project-overview-description .md ul > li').length,
    updateMd: !!document.querySelector('.project-overview-update .md'),
    tables: document.querySelectorAll('.project-overview-update .md-table').length,
  }));
  ok(overview.descriptionMd && overview.descriptionBullets === 2, 'overview: project description is markdown with its list', JSON.stringify(overview));
  ok(overview.updateMd && overview.tables === 1, 'overview: latest update is markdown with a table', JSON.stringify(overview));
  await shot('overview');

  await page.goto(`${base}/workspace/project/${projectId}/activity`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.activity-update .md', { timeout: 15000 });
  const activity = await page.evaluate(() => {
    const el = document.querySelector('.activity-update .md');
    return {
      bullets: el.querySelectorAll('ul > li').length,
      tables: el.querySelectorAll('.md-table').length,
      hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
  ok(activity.bullets === 2 && activity.tables === 1, 'activity: update body is markdown', JSON.stringify(activity));
  ok(!activity.hscroll, `activity: no horizontal page scroll at ${W}px`);
  await shot('activity');

  ok(errors.length === 0, 'no page errors on any surface', errors.slice(0, 3).join(' | '));

  await browser.close();
  server.kill('SIGKILL');
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(async e => { console.error(e); server.kill('SIGKILL'); process.exit(1); });
