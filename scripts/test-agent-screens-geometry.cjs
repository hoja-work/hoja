// Agent screens vs the issue screens they are built from (HOJA-602-A-16 / WP13).
//
// The claim is "same proportions, no new design language". So the reference is not a number
// written down here: the issue list and the issue page are opened in the same browser, at the
// same viewport, in the same run, and measured with the same probe as the agent screens. A
// layout change on either side fails this file.
//
//   cd frontend && npm run build
//   node scripts/test-agent-screens-geometry.cjs             # 1440x900
//   WIDTH=390 HEIGHT=844 node scripts/test-agent-screens-geometry.cjs
//   SHOTS=dir node scripts/test-agent-screens-geometry.cjs   # choose where the shots land
//
// Which reference, and why these two:
//   - The issue list is the team cycle page. It is the live surface that renders `IssueRow`
//     (graph/tasks.tsx:422) — the row the issue names — inside `FilterMenu`, `DisplayPanel` and
//     the `.lin-*` list shell. `GraphTeamIssues` (tasks.tsx:746) is not mounted by any route, so
//     the cycle list is the only place the row renders for real.
//   - The issue page is `/workspace/task/<IDENT>`: `GraphIssueDetail` (tasks.tsx:1309), the live
//     `.lin-detail` + `.lin-props` split.
//
// Page height is deliberately not compared: on both pages it follows content length (the
// document grows and scrolls), not the column layout under test.
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
const dir = fs.mkdtempSync(path.join(scratch, 'agent-screens-'));
const db = path.join(dir, 'p.db');
/* Not shaped like a real API key on purpose: this value is written into a disposable
 * database by this file and never authenticates anything else. */
const token = 'agent-screens-geometry-fixture';
/* A second principal with no admin bit, so the run can prove that a refused read (403) is drawn as
 * a refusal and not as an empty workspace. Written to the disposable database by this file. */
const memberToken = 'agent-screens-geometry-member-fixture';
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
};
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
info=seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
# A non-admin teammate: same workspace, no admin bit. Used for the 403 check.
from hoja_server.db import user, api_key, new_id, now_iso, token_hash
ts=now_iso(); uid=new_id()
with e.begin() as c:
    c.execute(user.insert().values(id=uid, organization_id=info['org_id'], name='Sam Member',
                                   display_name='Sam Member', email='sam@example.test', active=True,
                                   admin=False, timezone='UTC', created_at=ts, updated_at=ts))
    c.execute(api_key.insert().values(id=new_id(), token_hash=token_hash(${JSON.stringify(memberToken)}),
                                      label='member', user_id=uid, organization_id=info['org_id'], created_at=ts))
# Enough ledger rows to page the audit screen: the pane asks for 25 at a time, so 30 seeded
# events (plus the stop this run performs) make the second page the one that proves paging.
from hoja_server import audit
for n in range(30):
    audit.record(e, organization_id=info['org_id'], action='api_key.create', actor_kind='human',
                 actor_id=info['user_id'], target_kind='api_key', target_id='seeded-key-%d' % n)
e.dispose()`);
/* Two enrollment tokens, minted before the server starts because minting has no route of its own
 * (`runner_api.mint_enrollment_token` is an operator action). The *registration* that spends them
 * goes through the real REST route once the server is up, which is the part the screen must agree
 * with. Each token is single use, so there is one per runner. */
const enrollments = JSON.parse(run(`import json
from sqlalchemy import select
from hoja_server.db import make_engine, organization, user
from hoja_server import runner_api
e = make_engine(${JSON.stringify(db)})
with e.connect() as c:
    org_id = c.execute(select(organization.c.id)).scalars().first()
    user_id = c.execute(select(user.c.id)).scalars().first()
tokens = []
for _n in range(2):
    _row, token = runner_api.mint_enrollment_token(e, organization_id=org_id, created_by=user_id)
    tokens.append(token)
e.dispose()
print(json.dumps(tokens))`));
assert.equal(enrollments.length, 2, 'two enrollment tokens were minted');

const port = 19400 + Math.floor(Math.random() * 90);
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
const shots = process.env.SHOTS || path.join(scratch, 'agent-screens');
const W = Number(process.env.WIDTH || 1440), H = Number(process.env.HEIGHT || 900);
const TOL = 2;

/* One probe, used on both sides of every comparison. Every value is a rendered measurement: a
 * box, a computed padding, a computed font size or gap, all relative to the page element so the
 * numbers describe the layout and not where the shell happened to place it. */
const listProbe = () => {
  const box = el => el ? el.getBoundingClientRect().toJSON() : null;
  const css = (el, prop) => el ? getComputedStyle(el)[prop] : null;
  const page = document.querySelector('.lin-page');
  const list = document.querySelector('.lin-list');
  const row = document.querySelector('.lin-row');
  const ident = document.querySelector('.lin-row .ident');
  const title = document.querySelector('.lin-row .title');
  const pageBox = box(page), rowBox = box(row);
  const rel = el => (rowBox && box(el)) ? Math.round((box(el).left - rowBox.left) * 100) / 100 : null;
  return {
    viewbarPresent: Boolean(document.querySelector('.lin-viewbar')),
    headHeight: box(document.querySelector('.lin-head'))?.height ?? null,
    toolbarHeight: box(document.querySelector('.lin-toolbar'))?.height ?? null,
    listPadTop: css(list, 'paddingTop'),
    listPadLeft: css(list, 'paddingLeft'),
    listPadRight: css(list, 'paddingRight'),
    groupHeadHeight: box(document.querySelector('.lin-group-head'))?.height ?? null,
    rowHeight: rowBox?.height ?? null,
    rowLeftFromPage: (pageBox && rowBox) ? Math.round((rowBox.left - pageBox.left) * 100) / 100 : null,
    identOffset: rel(ident),
    titleOffset: rel(title),
    rowFont: css(row, 'fontSize'),
    rowGap: css(row, 'gap'),
    rowPadX: css(row, 'paddingLeft'),
  };
};

const detailProbe = () => {
  const box = el => el ? el.getBoundingClientRect().toJSON() : null;
  const css = (el, prop) => el ? getComputedStyle(el)[prop] : null;
  const main = document.querySelector('.lin-detail-main');
  const props = document.querySelector('.lin-props');
  const headBox = box(document.querySelector('.lin-head'));
  const headBottom = headBox ? headBox.top + headBox.height : null;
  const head = document.querySelector('.lin-head');
  return {
    // Below the head the layout is fixed by the column rules; above it the head grows when its
    // own content does not fit (the issue head carries four more controls and wraps on a phone).
    mainTopFromHead: (headBottom != null && box(main)) ? Math.round((box(main).top - headBottom) * 100) / 100 : null,
    propsTopFromHead: (headBottom != null && box(props)) ? Math.round((box(props).top - headBottom) * 100) / 100 : null,
    headOverflows: head ? head.scrollWidth > head.clientWidth + 1 : null,
    pageLeft: box(document.querySelector('.lin-page'))?.left ?? null,
    pageWidth: box(document.querySelector('.lin-page'))?.width ?? null,
    headHeight: box(document.querySelector('.lin-head'))?.height ?? null,
    toolbarHeight: box(document.querySelector('.lin-toolbar'))?.height ?? null,
    detailTop: box(document.querySelector('.lin-detail'))?.top ?? null,
    mainLeft: box(main)?.left ?? null,
    mainTop: box(main)?.top ?? null,
    mainWidth: box(main)?.width ?? null,
    mainPadTop: css(main, 'paddingTop'),
    mainPadLeft: css(main, 'paddingLeft'),
    mainMaxWidth: css(main, 'maxWidth'),
    propsLeft: box(props)?.left ?? null,
    propsTop: box(props)?.top ?? null,
    propsWidth: box(props)?.width ?? null,
    propsPadTop: css(props, 'paddingTop'),
    propsPadLeft: css(props, 'paddingLeft'),
  };
};

const focusStop = page => page.evaluate(() => {
  const a = document.activeElement;
  return {
    tag: a?.tagName || null,
    cls: String(a?.className || ''),
    label: a?.getAttribute('aria-label') || null,
    inRow: Boolean(a?.closest && a.closest('.lin-row')),
    isTitle: Boolean(a?.classList && a.classList.contains('title')),
    isStatus: Boolean(a?.classList && a.classList.contains('lin-status-age')),
  };
});

(async () => {
  for (let i = 0; i < 60; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  let d = await gql('{viewer{id} teams{nodes{id key states{nodes{id name type}}}}}');
  const me = d.viewer.id, team = d.teams.nodes[0];
  const key = team.key.toLowerCase();
  const st = t => team.states.nodes.find(s => s.type === t).id;
  // A cycle, because the cycle page is where IssueRow is mounted; then issues inside it.
  await gql('mutation($t:String!,$w:Int!){teamCycleCadenceEnable(teamId:$t,weekday:$w){enabled}}', { t: team.id, w: 1 });
  d = await gql('{teams{nodes{key activeCycle{id}}}}');
  const cycle = d.teams.nodes[0].activeCycle;
  assert.ok(cycle, 'the team has an active cycle');
  const create = (title, input) => gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}',
    { i: { title, teamId: team.id, assigneeId: me, cycleId: cycle.id, ...input } });
  await create('Ship the onboarding flow', { stateId: st('started'), priority: 1 });
  await create('Write the release notes', { stateId: st('started'), priority: 3 });
  await create('Fix the flaky login test', { stateId: st('unstarted'), priority: 2 });
  await create('Draft the migration plan', { stateId: st('unstarted'), priority: 4 });
  await create('Backlog spike', { stateId: st('backlog'), priority: 3 });
  const listed = await gql('{issues(first:10){nodes{identifier title}}}');
  const reference = listed.issues.nodes.find(n => n.title === 'Ship the onboarding flow');
  assert.ok(reference, 'the reference issue exists');

  /* Two real agent members, through the mutation WP04 added (admin-gated: the seeded token is the
   * workspace admin). From here on the agent list and detail are measured on server rows, so the
   * geometry is the geometry of real data and the fixture can no longer hide a broken mapping. */
  const AGENT_SELECTION = `{id userId capacity monthlyBudgetCredits workingHours timezone model runnerPool archivedAt createdAt updatedAt user{id name displayName}}`;
  const createMember = async input => {
    const r = await gql(`mutation($input:AgentMemberCreateInput!){agentMemberCreate(input:$input){success agentMember${AGENT_SELECTION}}}`, {input});
    assert.ok(r.agentMemberCreate.success && r.agentMemberCreate.agentMember, 'agentMemberCreate succeeded');
    return r.agentMemberCreate.agentMember;
  };
  const triage = await createMember({name: 'Triage agent', capacity: 2, monthlyBudgetCredits: 500,
    model: 'claude-sonnet-4', runnerPool: 'linux-x64', timezone: 'UTC'});
  const docsAgent = await createMember({name: 'Docs agent', capacity: 1});
  /* WP12 landed on main while this slice was in review: a delegated task is only leaseable under
   * a policy that names its team (deny by default), so the machine below can only be granted work
   * if the agent has one. This is the policy surface's own mutation, not a workaround: without it
   * the correct answer to "claim a lease" is the policy's refusal. */
  const policy = await gql(`mutation($id:String!,$input:AgentPolicyInput!){
    agentPolicyUpdate(agentMemberId:$id, input:$input){success}}`,
  {id: triage.id, input: {teams: [team.id], maxConcurrency: 2,
    /* WP09: the port allowlist is the policy's, so this run can only publish what it names here. */
    connect: [{host: 'localhost', port: 3000}, {host: 'localhost', port: 9229}]}});
  assert.ok(policy.agentPolicyUpdate.success, 'the triage agent has the policy that lets it be leased');

  const cyclePath = `/workspace/team/${key}/cycle/active`;
  const referencePath = `/workspace/task/${encodeURIComponent(reference.identifier)}/issue`;

  /* Three delegated tasks, before the browser opens, so the run queue and the agent page are
   * measured on server rows: delegating a task opens its session (WP06), the triage agent has two
   * slots, so the third waits and says why. */
  const delegateTask = async (title, delegateId, priority) => {
    const r = await gql(`mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id identifier
      agentSession{id state status queueReason admitted}}}}`,
    {i: {title, teamId: team.id, cycleId: cycle.id, delegateId, priority, stateId: st('started')}});
    assert.ok(r.issueCreate.success && r.issueCreate.issue.agentSession, `delegation opened a session: ${title}`);
    return r.issueCreate.issue;
  };
  const runOne = await delegateTask('Rotate the staging credentials', triage.userId, 2);
  const runTwo = await delegateTask('Backfill the audit chain verifier', triage.userId, 3);
  const runQueued = await delegateTask('Regenerate the SDK types', triage.userId, 4);
  assert.deepEqual(runOne.agentSession.queueReason, [], 'the first delegation is admitted');
  assert.ok(runQueued.agentSession.queueReason.includes('capacity'),
    `the third waits on capacity: ${JSON.stringify(runQueued.agentSession.queueReason)}`);

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  await ctx.addInitScript(t => {
    localStorage.setItem('hoja_token', t);
    localStorage.setItem('hoja_agents_surface', 'on');
  }, token);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  fs.mkdirSync(shots, { recursive: true });

  /* ---- 1. the two lists, measured the same way ---- */
  await page.goto(`${base}${cyclePath}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  const issueList = await page.evaluate(listProbe);
  await page.screenshot({ path: path.join(shots, `issue-list-${W}.png`) });

  await page.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="agents-list"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  const agentList = await page.evaluate(listProbe);
  await page.screenshot({ path: path.join(shots, `agents-list-${W}.png`) });

  ok(Boolean(agentList.rowHeight), 'agents list renders rows');
  ok(issueList.rowHeight === 42, 'reference: the issue row is 42 px tall', String(issueList.rowHeight));
  /* `titleOffset` is reported by the probe but not compared: the title starts after the
   * identifier, and the identifier's width is its text ("TREBOL-1" is wider than "AGENT-1"), not
   * the column. `identOffset` is the column; `rowGap` is the space after it. */
  const LIST_KEYS = ['headHeight', 'toolbarHeight', 'listPadTop', 'listPadLeft', 'listPadRight', 'groupHeadHeight',
    'rowHeight', 'rowLeftFromPage', 'identOffset', 'rowFont', 'rowGap', 'rowPadX'];
  for (const k of LIST_KEYS) {
    const a = issueList[k], b = agentList[k];
    if (typeof a === 'number' && typeof b === 'number') ok(Math.abs(a - b) <= TOL, `list ${k} matches the issue list (${TOL}px)`, `${a} vs ${b}`);
    else ok(a === b, `list ${k} matches the issue list exactly`, `${a} vs ${b}`);
  }
  ok(agentList.viewbarPresent, 'agents list carries the view/tab strip the issue list shell provides');
  ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), 'agents list has no horizontal page scroll');

  /* ---- 2. the two detail pages, measured the same way ---- */
  await page.goto(`${base}${referencePath}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.lin-detail-main', { timeout: 15000 });
  await page.waitForTimeout(700);
  const issueDetail = await page.evaluate(detailProbe);
  await page.screenshot({ path: path.join(shots, `issue-detail-${W}.png`) });

  await page.goto(`${base}/workspace/agents/${encodeURIComponent(triage.id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="agent-detail"] .lin-detail-main', { timeout: 15000 });
  await page.waitForTimeout(400);
  const agentDetail = await page.evaluate(detailProbe);
  await page.screenshot({ path: path.join(shots, `agent-detail-${W}.png`) });

  /* Head height is content: at 390 px the issue head wraps because it carries a favourite star,
   * an options menu and previous/next controls. Its height is compared only when the reference
   * fits on one line; the layout under it is compared at every width through *FromHead. */
  const headWraps = issueDetail.headHeight > 60;
  const DETAIL_KEYS = ['pageLeft', 'pageWidth', 'toolbarHeight', 'mainLeft', 'mainWidth', 'mainPadTop',
    'mainPadLeft', 'mainMaxWidth', 'propsLeft', 'propsWidth', 'propsPadTop', 'propsPadLeft',
    'mainTopFromHead',
    // Side by side, the sidebar's top is the column rule. Stacked, it is wherever the main column
    // ends, which is content length, so it is compared only where the sidebar sits beside it.
    ...(W > 900 ? ['propsTopFromHead'] : []),
    ...(headWraps ? [] : ['headHeight', 'detailTop', 'mainTop', 'propsTop'])];
  for (const k of DETAIL_KEYS) {
    const a = issueDetail[k], b = agentDetail[k];
    if (typeof a === 'number' && typeof b === 'number') ok(Math.abs(a - b) <= TOL, `detail ${k} matches the issue page (${TOL}px)`, `${a} vs ${b}`);
    else ok(a === b, `detail ${k} matches the issue page exactly`, `${a} vs ${b}`);
  }
  ok(agentDetail.headOverflows === false, 'agent head does not clip its breadcrumb at this width');
  if (headWraps) console.log(`NOTE reference head wraps at ${W}px (${issueDetail.headHeight}px tall); its height and the tops below it are compared at wider viewports only`);
  if (W > 900) ok(agentDetail.propsLeft > agentDetail.mainLeft, 'wide viewport: policy sidebar sits beside the main column');
  else ok(agentDetail.propsTop > agentDetail.mainTop, 'narrow viewport: policy sidebar sits below the main column, as the issue page does');

  /* ---- 3. the run queue and the audit log render in the same list shell ---- */
  for (const [url, surface] of [['/workspace/agents/queue', 'run-queue'], ['/workspace/agents/audit', 'audit-log']]) {
    await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
    await page.waitForSelector(`[data-surface="${surface}"] .lin-row`, { timeout: 15000 });
    await page.waitForTimeout(300);
    const measured = await page.evaluate(listProbe);
    ok(Math.abs(measured.rowHeight - issueList.rowHeight) <= TOL, `${surface} row height matches the issue list`, `${measured.rowHeight} vs ${issueList.rowHeight}`);
    if (issueList.groupHeadHeight != null && measured.groupHeadHeight != null) {
      ok(Math.abs(measured.groupHeadHeight - issueList.groupHeadHeight) <= TOL, `${surface} group header height matches the issue list`, `${measured.groupHeadHeight} vs ${issueList.groupHeadHeight}`);
    }
    ok(measured.identOffset === issueList.identOffset, `${surface} identifier column matches the issue list`, `${measured.identOffset} vs ${issueList.identOffset}`);
    ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), `${surface} has no horizontal page scroll`);
    await page.screenshot({ path: path.join(shots, `${surface}-${W}.png`) });
  }

  /* ---- 4. keyboard entry into a row, on both lists ---- */
  const rowKeyboard = async url => {
    await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.lin-row .lin-status-age', { timeout: 15000 });
    /* Walk in from the group header with real key presses. The issue list has one extra stop
     * here (its "create task in this group" button); both must land inside a row within three. */
    await page.focus('.lin-group-head .chev');
    let entered = null, tabs = 0;
    for (let i = 0; i < 3 && !entered; i++) { await page.keyboard.press('Tab'); tabs++; const s = await focusStop(page); if (s.inRow) entered = s; }
    let second = entered;
    for (let i = 0; i < 2 && !(second && second.isTitle); i++) { await page.keyboard.press('Tab'); second = await focusStop(page); }
    await page.keyboard.press('Shift+Tab');
    const back = await focusStop(page);
    const before = await page.evaluate(() => document.querySelectorAll('.lin-row').length);
    await page.focus('.lin-group-head .chev');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(200);
    const after = await page.evaluate(() => document.querySelectorAll('.lin-row').length);
    const afterLabel = await page.evaluate(() => document.querySelector('.lin-group-head .chev')?.getAttribute('aria-label'));
    const expanded = await page.evaluate(() => document.querySelector('.lin-group-head .chev')?.getAttribute('aria-expanded'));
    await page.keyboard.press('Enter');
    await page.waitForTimeout(200);
    const backLabel = await page.evaluate(() => document.querySelector('.lin-group-head .chev')?.getAttribute('aria-label'));
    const expandedAgain = await page.evaluate(() => document.querySelector('.lin-group-head .chev')?.getAttribute('aria-expanded'));
    return { tabs, entered, second, back, before, after, afterLabel, backLabel, expanded, expandedAgain };
  };

  const issueKb = await rowKeyboard(cyclePath);
  const agentKb = await rowKeyboard('/workspace/agents');
  // The agent chevron is the richer of the two: it also exposes aria-expanded, as GraphTeamIssues
  // does. The reference list announces state through its label only, so that is what is compared.
  ok(agentKb.expanded === 'false' && agentKb.expandedAgain === 'true', 'agents list: the chevron also reports aria-expanded', `${agentKb.expanded} → ${agentKb.expandedAgain}`);
  for (const [name, kb] of [['issue list', issueKb], ['agents list', agentKb]]) {
    ok(Boolean(kb.entered) && kb.entered.inRow, `${name}: Tab from the group header lands inside a row`, `after ${kb.tabs} tab(s)`);
    ok(kb.second.isTitle && kb.second.inRow, `${name}: the next Tab reaches the row's title button`, kb.second.label || kb.second.tag);
    ok(kb.back.inRow, `${name}: Shift+Tab returns into the row`, kb.back.label);
    // Both lists announce the state the same way: the chevron's own label flips.
    ok(kb.after < kb.before && /^Expand /.test(kb.afterLabel || ''), `${name}: the group chevron collapses from the keyboard`, `${kb.before} → ${kb.after} rows, ${kb.afterLabel}`);
    ok(kb.backLabel === kb.afterLabel?.replace('Expand', 'Collapse'), `${name}: the same key re-expands the group`, kb.backLabel);
  }

  const opens = async (url, target) => {
    await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.lin-row .title', { timeout: 15000 });
    await page.focus('.lin-row .title');
    await page.keyboard.press('Enter');
    await page.waitForSelector(target, { timeout: 15000 });
    return page.url();
  };
  const issueOpened = await opens(cyclePath, '.lin-detail-main');
  const agentOpened = await opens('/workspace/agents', '[data-surface="agent-detail"] .lin-detail-main');
  ok(issueOpened.includes('/workspace/task/'), 'issue list: Enter on a row title opens the task page', issueOpened.replace(base, ''));
  ok(agentOpened.includes('/workspace/agents/'), 'agents list: Enter on a row title opens the agent page', agentOpened.replace(base, ''));

  /* ---- 5. with the flag on, the sidebar reaches the surface (the entry test-page-invariants
   * exempts while the flag is off) ---- */
  await page.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  // On a phone the sidebar is a drawer: open it first, as a person would.
  const drawerOpen = await page.evaluate(() => { const s = document.querySelector('.sidebar'); return !s || s.getBoundingClientRect().left >= 0; });
  if (!drawerOpen) await page.click('button.mobile-nav-toggle[aria-expanded="false"]');
  await page.waitForTimeout(350);
  const entry = page.locator('.sidebar :is(button,a)').filter({ hasText: /^\s*Agents\s*$/ }).first();
  ok(await entry.count() > 0, 'flagged-on build: the sidebar carries an Agents entry');
  if (await entry.count() > 0) {
    await entry.click({ timeout: 10000 }).catch(() => undefined);
    await page.waitForSelector('[data-surface="agents-list"]', { timeout: 15000 }).catch(() => undefined);
    ok(new URL(page.url()).pathname === '/workspace/agents', 'flagged-on build: the sidebar entry opens the agent list', new URL(page.url()).pathname);
  }

  /* ---- 6. the runner screens (slice 5): real machines, real leases, labelled Connect ----
   *
   * The guard matters: the loops above compare with `ok(a === b)`, which passes when a selector
   * was missing and both sides are `null`. A new screen has no history to reassure us, so every key
   * is first asserted to have been *measured* on both sides.
   *
   * The runners are now server rows, so this section registers them through the real REST route
   * (`/runner/register`, the enrollment token minted by the seeding pass below) and then asserts
   * the list draws exactly what the API stored. Connect is still a fixture (WP08 has no exposure
   * store), so it is the *only* thing on these screens that carries a sample notice. */
  const RUNNERS = '/workspace/agents/runners';
  const canary = /\btc[A-Za-z0-9_-]{20,}\b/; // mirrors TAILCAT_ADDRESS_RE in runner/hoja_runnerd/connect.py;
  // app/scripts/check-runner-contract.py proves the fixture matches the Python regex itself.

  /* Two machines: one that declared the harness Hoja's scheduler enqueues (`pi`), so it can take
   * the lease this file finishes later, and one that never polls, so it reads offline. */
  const registerRunner = async (enrollment, body) => {
    const response = await fetch(`${base}/runner/register`, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({enrollment_token: enrollment, ...body}),
    });
    const payload = await response.json();
    assert.equal(response.status, 200, JSON.stringify(payload));
    return payload;
  };
  const onlineRunner = await registerRunner(enrollments[0],
    {name: 'mac-mini-01', harnesses: ['pi'], labels: {pool: 'default', host: 'office'}});
  const idleRunner = await registerRunner(enrollments[1],
    {name: 'laptop-adhoc', harnesses: ['generic'], labels: {pool: 'default'}});

  const apiRunners = async () => (await gql(`{runners(first: 50) {nodes {
    id name organizationId status enabled revokedAt labels harnesses lastHeartbeatAt
    currentLease {id taskId state harness fence issue {identifier}}}}}`)).runners.nodes;

  await page.goto(`${base}${RUNNERS}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runners-list"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  const runnerList = await page.evaluate(listProbe);
  await page.screenshot({ path: path.join(shots, `runners-list-${W}.png`) });

  ok(Boolean(runnerList.rowHeight), 'runner list renders rows');
  for (const k of LIST_KEYS) {
    const a = issueList[k], b = runnerList[k];
    ok(a != null && b != null, `runner list: ${k} is measured on both sides`, `${a} vs ${b}`);
    if (typeof a === 'number' && typeof b === 'number') ok(Math.abs(a - b) <= TOL, `runner list ${k} matches the issue list (${TOL}px)`, `${a} vs ${b}`);
    else ok(a === b, `runner list ${k} matches the issue list exactly`, `${a} vs ${b}`);
  }
  ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), 'runner list has no horizontal page scroll');

  /* The rows are the machines the REST route registered — not a fixture, and not a count. */
  const serverRunners = await apiRunners();
  const listRows = await page.evaluate(() => [...document.querySelectorAll('[data-surface="runners-list"] .lin-row')]
    .map(r => ({
      ident: r.querySelector('.ident')?.textContent || '',
      title: r.querySelector('.title')?.textContent || '',
      credential: r.querySelector('[data-credential]')?.getAttribute('data-credential') || '',
      lease: r.querySelector('[data-lease]')?.getAttribute('data-lease') || '',
    })));
  ok(serverRunners.length === 2, 'runner list: the workspace holds the two machines this file registered', String(serverRunners.length));
  ok(listRows.length === serverRunners.length, 'runner list: one row per server runner (runners query)', String(listRows.length));
  ok(listRows.some(r => r.ident === onlineRunner.runner_id && r.title === 'mac-mini-01'),
    'runner list: a machine registered through /runner/register is a row, by its server id', JSON.stringify(listRows));
  ok(listRows.every(r => r.credential === 'enrolled'),
    'runner list: a fresh registration reads enrolled, never the token hash', JSON.stringify(listRows.map(r => r.credential)));
  ok(!listRows.some(r => /^RUNNER-\d+$/.test(r.ident)), 'runner list: no fixture runner id is presented as a machine',
    listRows.map(r => r.ident).join(','));
  ok(await page.evaluate(() => !document.querySelector('[data-surface="runners-list"] [data-sample="on"]')),
    'runner list: carries no sample-data notice, because every row on it is a server row');
  /* The status is the server's derivation, drawn as the issue row's status glyph. */
  const offlineRow = listRows.find(r => r.ident === idleRunner.runner_id);
  ok(Boolean(offlineRow), 'runner list: the machine that never polled is a row too', JSON.stringify(listRows.map(r => r.ident)));
  ok((await apiRunners()).find(r => r.id === idleRunner.runner_id)?.status === 'offline',
    'runner list: a machine with no heartbeat is offline, as the server derives it');
  ok(!listRows.some(r => r.lease), 'runner list: nothing is leased yet, so no row carries a lease chip');

  /* No new design language, mechanically: every class token on a runner row must already have a
   * rule in the stylesheets this app ships, and no token may be runner-specific. */
  const runnerTokens = await page.evaluate(() => [...new Set(
    [...document.querySelectorAll('[data-surface="runners-list"] .lin-row, [data-surface="runners-list"] .lin-row *')]
      .flatMap(el => [...el.classList]))]);
  const cssText = (function walk(dir) {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap(d => {
      const full = path.join(dir, d.name);
      if (d.isDirectory()) return walk(full);
      return d.name.endsWith('.css') ? [fs.readFileSync(full, 'utf8')] : [];
    });
  })(path.join(repo, 'frontend/src')).join('\n');
  /* The pinned peer is an identifier, not a secret: its full form may live in a `title`. What
   * must not happen is a class that no stylesheet styles and no shared component emits. */
  const sharedText = ['graph/shared.tsx', 'graph/tasks.tsx']
    .map(f => fs.readFileSync(path.join(repo, 'frontend/src', f), 'utf8')).join('\n');
  const unstyled = runnerTokens.filter(t =>
    !cssText.includes(`.${t}`) && !sharedText.includes(t) && !/^lucide/.test(t));
  ok(unstyled.length === 0, 'every class on a runner row is styled by the app or emitted by a shared component', unstyled.join(', '));
  ok(!runnerTokens.some(t => /runner/i.test(t)), 'no runner-specific class token was invented', runnerTokens.filter(t => /runner/i.test(t)).join(', '));

  await page.goto(`${base}${RUNNERS}/${encodeURIComponent(onlineRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runner-detail"] .lin-props', { timeout: 15000 });
  await page.waitForTimeout(400);
  const runnerDetail = await page.evaluate(detailProbe);
  await page.screenshot({ path: path.join(shots, `runner-detail-${W}.png`) });

  for (const k of DETAIL_KEYS) {
    const a = issueDetail[k], b = runnerDetail[k];
    ok(a != null && b != null, `runner detail: ${k} is measured on both sides`, `${a} vs ${b}`);
    if (typeof a === 'number' && typeof b === 'number') ok(Math.abs(a - b) <= TOL, `runner detail ${k} matches the issue page (${TOL}px)`, `${a} vs ${b}`);
    else ok(a === b, `runner detail ${k} matches the issue page exactly`, `${a} vs ${b}`);
  }
  if (W > 900) ok(runnerDetail.propsLeft > runnerDetail.mainLeft, 'wide viewport: the runner sidebar sits beside the main column');
  else ok(runnerDetail.propsTop > runnerDetail.mainTop, 'narrow viewport: the runner sidebar sits below the main column, as the issue page does');

  /* The detail is the server's row: the harnesses it declared, its credential state, and no lease
   * yet because it has not polled. */
  const detailText = await page.evaluate(() => document.querySelector('[data-surface="runner-detail"]')?.innerText || '');
  ok(/mac-mini-01/.test(detailText), 'runner detail: names the machine the row does', detailText.slice(0, 120).replace(/\n/g, ' '));
  ok(/\bpi\b/.test(detailText), 'runner detail: shows the harness the runner declared at registration');
  ok(/no heartbeat yet/i.test(detailText), 'runner detail: a machine that never polled says so instead of inventing an age');
  ok(await page.evaluate(() => document.querySelector('[data-surface="runner-detail"] [data-current-lease]')?.getAttribute('data-current-lease') === ''),
    'runner detail: no lease is claimed while the machine holds none');
  ok(await page.evaluate(() => !document.querySelector('[data-surface="runner-detail"] [data-sample="on"]')),
    'runner detail: carries no sample-data notice, because the Connect rows are server rows now (WP09)');
  ok(await page.evaluate(() => Boolean(document.querySelector('[data-surface="runner-detail"] [data-exposures]'))),
    'runner detail: the Connect block reads connectEndpoints instead of a fixture');

  /* The address is a bearer secret, and it is not in the database at all, so the page cannot
   * contain it in any form. */
  ok(!canary.test(await page.content()), 'runner detail: no tailcat address anywhere in the rendered HTML');

  /* The machine holds no lease yet, so the block is a real empty state and not a fixture's rows. */
  ok(await page.evaluate(() => document.querySelectorAll('[data-surface="runner-detail"] [data-exposure]').length === 0),
    'runner detail: a machine that exposed nothing draws no exposure rows');
  ok(/Nothing is exposed/.test(await page.evaluate(() => document.querySelector('[data-surface="runner-detail"]')?.innerText || '')),
    'runner detail: the empty Connect block says so in words');

  /* The kill switch is the workspace's own setting (WP09): turned off through the policy mutation,
   * it is a state the screen reads back — not an empty list, and not a per-browser trick. */
  await gql('mutation { workspaceSecurityUpdate(input: {connectEnabled: false}) { connectEnabled } }');
  const offConnect = await browser.newContext({ viewport: { width: W, height: H } });
  await offConnect.addInitScript(t => {
    localStorage.setItem('hoja_token', t);
    localStorage.setItem('hoja_agents_surface', 'on');
  }, token);
  const offConnectPage = await offConnect.newPage();
  await offConnectPage.goto(`${base}${RUNNERS}/${encodeURIComponent(idleRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await offConnectPage.waitForSelector('[data-surface="runner-detail"][data-connect="off"]', { timeout: 15000 });
  ok(await offConnectPage.evaluate(() => Boolean(document.querySelector('[data-connect-notice="off"]'))),
    'Connect disabled: the screen says so instead of showing an empty list');
  ok(await offConnectPage.evaluate(() => [...document.querySelectorAll('[data-exposure] button')].every(b => b.disabled)),
    'Connect disabled: no revoke control is offered');
  await offConnectPage.screenshot({ path: path.join(shots, `runner-detail-connect-off-${W}.png`) });
  await offConnect.close();
  const switchedBack = await gql('mutation { workspaceSecurityUpdate(input: {connectEnabled: true}) { connectEnabled } }');
  ok(switchedBack.workspaceSecurityUpdate.connectEnabled === true,
    'Connect switched back on through the same server setting the screen reads');

  /* Keyboard parity with the issue list, on the new list. */
  const runnerKb = await rowKeyboard(RUNNERS);
  ok(Boolean(runnerKb.entered) && runnerKb.entered.inRow, 'runner list: Tab from the group header lands inside a row', `after ${runnerKb.tabs} tab(s)`);
  ok(runnerKb.second.isTitle && runnerKb.second.inRow, 'runner list: the next Tab reaches the row title', runnerKb.second.label || runnerKb.second.tag);
  ok(runnerKb.after < runnerKb.before && /^Expand /.test(runnerKb.afterLabel || ''), 'runner list: the group chevron collapses from the keyboard', `${runnerKb.before} → ${runnerKb.after} rows`);

  /* ---- 6b. Enroll (HOJA-635): the token is shown once, and the command is the real one ----
   *
   * The only control on this surface that hands out a credential. What is asserted, in order:
   *
   *   1. the panel opens from the toolbar and mints through `runnerEnrollmentCreate`;
   *   2. the plaintext is on screen once, together with the exact `hoja-runnerd --enroll`
   *      command that spends it;
   *   3. the `runnerEnrollments` query never carries the token or its hash — the server's SDL
   *      has no column that could;
   *   4. closing the panel discards it: reopening shows the enrollment row as `pending`, with no
   *      token anywhere in the page;
   *   5. Withdraw is two-step and closes /runner/enroll for that token (asserted against the real
   *      route, not against a chip that changed colour).
   */
  await page.goto(`${base}${RUNNERS}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runners-list"]', { timeout: 15000 });
  ok(await page.evaluate(() => !document.querySelector('[data-enroll-panel]')),
    'enroll: the panel starts closed, so a credential is never minted by accident');
  await page.click('[data-surface="runners-list"] [data-action="enroll-runner"]');
  await page.waitForSelector('[data-enroll-panel]', { timeout: 5000 });
  const rowsBeforeEnroll = await page.evaluate(() =>
    document.querySelectorAll('[data-surface="runners-list"] .lin-row').length);
  ok(rowsBeforeEnroll === 2, 'enroll: opening the panel adds no runner row (the row count stays the server count)', String(rowsBeforeEnroll));

  await page.fill('[data-enroll-panel] input[aria-label="Runner name"]', 'e2e-enrolled-machine');
  await page.fill('[data-enroll-panel] input[aria-label="Token lifetime in minutes"]', '90');
  await page.click('[data-enroll-panel] button.lin-btn.primary');
  await page.waitForSelector('[data-enroll-token]', { timeout: 10000 });
  const shownToken = await page.evaluate(() =>
    document.querySelector('[data-enroll-secret]')?.textContent?.trim() || '');
  const shownCommand = await page.evaluate(() =>
    document.querySelector('[data-enroll-command]')?.textContent?.trim() || '');
  ok(shownToken.length >= 20, 'enroll: the response shows the plaintext token once', `${shownToken.length} chars`);
  ok(shownCommand.startsWith(`HOJA_ENROLLMENT_TOKEN=${shownToken} hoja-runnerd --enroll `),
    'enroll: the exact hoja-runnerd enroll command is shown, carrying this token', shownCommand);
  ok(shownCommand.includes('--name e2e-enrolled-machine') && /--base-url https?:\/\//.test(shownCommand),
    'enroll: the command names the machine and this deployment URL', shownCommand);

  /* The token is a secret that exists in exactly one place: not in the read side. */
  const apiEnrollments = async () => (await gql(
    `{runnerEnrollments{id label status usable expiresAt usedAt usedByRunner createdAt}}`)).runnerEnrollments;
  const enrollmentsNow = await apiEnrollments();
  ok(enrollmentsNow.some(e => e.label === 'e2e-enrolled-machine' && e.status === 'pending' && e.usable === true),
    'enroll: the enrollment is a server row, pending and usable', JSON.stringify(enrollmentsNow.map(e => [e.label, e.status])));
  ok(!JSON.stringify(enrollmentsNow).includes(shownToken),
    'enroll: runnerEnrollments never carries the token — only its hash is stored');
  ok(!Object.keys(enrollmentsNow[0]).some(k => /token|hash|hint/i.test(k)),
    'enroll: the enrollment shape has no credential column at all', Object.keys(enrollmentsNow[0]).join(','));
  await page.screenshot({ path: path.join(shots, `runners-enroll-token-${W}.png`) });

  /* Shown once: close, reopen, and the plaintext is gone from the DOM. */
  await page.getByRole('button', { name: 'Done' }).click();
  await page.waitForSelector('[data-enroll-token]', { state: 'detached', timeout: 5000 }).catch(() => undefined);
  ok(!(await page.content()).includes(shownToken), 'enroll: closing the panel discards the plaintext');
  await page.click('[data-surface="runners-list"] [data-action="enroll-runner"]');
  await page.waitForSelector('[data-enroll-list]', { timeout: 5000 });
  ok(!(await page.content()).includes(shownToken),
    'enroll: reopening cannot show it again — the server kept only a hash');
  const pendingRow = await page.evaluate(() => {
    const row = [...document.querySelectorAll('[data-enroll-list] [data-enrollment]')]
      .find(r => r.textContent.includes('e2e-enrolled-machine'));
    return row ? { status: row.getAttribute('data-enrollment-status'), text: row.textContent } : null;
  });
  ok(pendingRow && pendingRow.status === 'pending', 'enroll: the list shows it as pending', JSON.stringify(pendingRow));

  /* Withdraw: two steps, then the real route refuses the token. */
  await page.click('[data-enroll-list] [data-enrollment-status="pending"] button.lin-btn');
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  ok((await apiEnrollments()).some(e => e.usable === true),
    'enroll: withdraw is two-step — nothing is revoked until the dialog is confirmed');
  await page.click('[role="dialog"] button.danger');
  await page.waitForTimeout(600);
  const afterWithdraw = await apiEnrollments();
  ok(afterWithdraw.some(e => e.label === 'e2e-enrolled-machine' && e.usable === false),
    'enroll: the withdrawn token is no longer usable', JSON.stringify(afterWithdraw.map(e => [e.label, e.status])));
  ok(await page.evaluate(() => {
    const row = [...document.querySelectorAll('[data-enroll-list] [data-enrollment]')]
      .find(r => r.textContent.includes('e2e-enrolled-machine'));
    return Boolean(row) && !row.querySelector('button');
  }), 'enroll: a withdrawn token offers no second Withdraw');
  await page.screenshot({ path: path.join(shots, `runners-enroll-withdrawn-${W}.png`) });

  /* The route, not the chip: /runner/enroll is a different endpoint from /runner/register, so the
   * receipt is the registration that the revoked token can no longer buy. */
  const revokedEnroll = await fetch(`${base}/runner/register`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'should-be-refused', enrollment_token: shownToken,
      harnesses: ['generic'], labels: {} }),
  });
  ok(revokedEnroll.status === 401, 'enroll: a withdrawn token is refused at /runner/register', String(revokedEnroll.status));
  ok((await revokedEnroll.json()).code === 'unauthorized',
    'enroll: the refusal is the enroll route\'s own unauthorized, not a lease error');

  /* Closing returns the list to exactly what it was. */
  await page.getByRole('button', { name: 'Close' }).click();
  await page.waitForTimeout(200);
  ok(await page.evaluate(() => document.querySelectorAll('[data-surface="runners-list"] .lin-row').length) === 2,
    'enroll: closing leaves the runner list unchanged — minting a token enrolls no machine');

  /* ---- 6c. Revoke runner (HOJA-635): the machine is turned off, not deleted ---- */
  await page.goto(`${base}${RUNNERS}/${encodeURIComponent(idleRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runner-detail"] .lin-props', { timeout: 15000 });
  ok(await page.evaluate(() => Boolean(document.querySelector('[data-action="revoke-runner"]'))),
    'revoke runner: the detail page offers it while the machine is live');
  await page.click('[data-action="revoke-runner"]');
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  ok((await apiRunners()).find(r => r.id === idleRunner.runner_id)?.enabled === true,
    'revoke runner: two-step — the row is untouched until the dialog is confirmed');
  await page.click('[role="dialog"] button.danger');
  await page.waitForTimeout(700);
  const revokedRow = (await apiRunners()).find(r => r.id === idleRunner.runner_id);
  ok(revokedRow && revokedRow.enabled === false && Boolean(revokedRow.revokedAt),
    'revoke runner: the server turned the machine off and recorded when', JSON.stringify(revokedRow && {enabled: revokedRow.enabled, revokedAt: revokedRow.revokedAt}));
  ok(await page.getByText(/revoked\. Its token no longer authenticates/).count() > 0,
    'revoke runner: the page reports what it did to /runner/claim');
  /* And the machine really cannot claim: its own bearer token is refused. */
  const refusedClaim = await fetch(`${base}/runner/lease`, {
    method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${idleRunner.token}` },
    body: JSON.stringify({ runner_id: idleRunner.runner_id, poll_timeout_ms: 0 }),
  });
  ok(refusedClaim.status === 401, 'revoke runner: the revoked machine is refused at /runner/claim', String(refusedClaim.status));
  await page.screenshot({ path: path.join(shots, `runner-detail-revoked-token-${W}.png`) });

  /* The tab strip reaches it. */
  await page.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  await page.getByRole('tab', { name: 'Runners' }).click();
  await page.waitForSelector('[data-surface="runners-list"]', { timeout: 15000 }).catch(() => undefined);
  ok(new URL(page.url()).pathname === RUNNERS, 'the Runners tab opens the runner list', new URL(page.url()).pathname);

  /* ---- 7. the surface reads and writes WP04 through GraphQL, and states its refusals ----
   *
   * Slices 1–2 drew every agent row from a fixture. This section is what proves that changed: the
   * rows are the members the mutation created above, the create form and the sidebar write through
   * `agentMemberCreate`/`agentMemberUpdate`, the issue page's Delegate field writes
   * `issue.delegateId`, and a refusal is drawn as a refusal rather than as an empty workspace. */
  const apiMembers = async () => (await gql(`{agentMembers${AGENT_SELECTION}}`)).agentMembers;
  const apiMember = async id => (await gql(`query($id:String!){agentMember(id:$id)${AGENT_SELECTION}}`, {id})).agentMember;
  const apiIssueDelegate = async () => (await gql(
    `query($id:String!){issue(id:$id){delegateId delegate{id name}}}`, {id: reference.identifier})).issue;

  await page.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="agents-list"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(300);
  const serverRows = await page.evaluate(() => [...document.querySelectorAll('[data-surface="agents-list"] .lin-row')]
    .map(r => ({id: r.getAttribute('data-agent'), title: r.querySelector('.title')?.textContent || '', ident: r.querySelector('.ident')?.textContent || ''})));
  ok(serverRows.length === 2, 'agents list: one row per server agent member (WP04 agentMembers)', String(serverRows.length));
  ok(serverRows.some(r => r.id === triage.id && r.title === 'Triage agent'), 'agents list: the rows are the members the mutation created', JSON.stringify(serverRows));
  ok(!serverRows.some(r => /^AGENT-\d+$/.test(r.ident)), 'agents list: no fixture identifier is presented as an agent', serverRows.map(r => r.ident).join(','));
  ok(await page.evaluate(() => !document.querySelector('[data-surface="agents-list"] [data-sample="on"]')),
    'agents list: carries no sample-data notice, because no row on it is a fixture');
  ok(await page.evaluate(() => !document.body.innerText.includes('AGENT-1')), 'agents list: no fixture text reached the page');

  /* Create through the UI, then read it back through the API: a form that posts into local state
   * would pass a screenshot and fail this. */
  await page.getByRole('button', { name: /New agent/ }).first().click();
  await page.waitForSelector('[role="dialog"][aria-label="New agent"]', { timeout: 5000 });
  await page.fill('[role="dialog"][aria-label="New agent"] input[aria-label="Agent name"]', 'Release scribe');
  await page.fill('[role="dialog"][aria-label="New agent"] input[aria-label="Capacity"]', '3');
  await page.click('[role="dialog"][aria-label="New agent"] button[type="submit"]');
  await page.waitForSelector('[data-surface="agent-detail"] .lin-detail-main', { timeout: 15000 });
  const created = (await apiMembers()).find(m => m.user?.name === 'Release scribe');
  ok(Boolean(created), 'create agent: the form wrote an agentMemberCreate the API can read back');
  ok(created && created.capacity === 3, 'create agent: capacity travelled with the write', String(created && created.capacity));
  ok(created && created.userId && created.id, 'create agent: the row has its own identity and a delegate target id');

  /* Edit through the sidebar, then read it back. */
  const capacityField = '[data-surface="agent-detail"] input[aria-label="Capacity"]';
  await page.fill(capacityField, '4');
  await page.press(capacityField, 'Enter');
  await page.waitForTimeout(700);
  ok((await apiMember(created.id)).capacity === 4, 'edit agent: the sidebar wrote agentMemberUpdate the API can read back');

  /* Delegate, on the task page, with the task page's own property control. */
  await page.goto(`${base}${referencePath}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.lin-detail .lin-props', { timeout: 15000 });
  await page.waitForTimeout(500);
  const delegateRow = page.locator('.lin-props .lin-prop.lin-dd').filter({ has: page.locator('.k', { hasText: 'Delegate' }) }).first();
  ok(await delegateRow.count() === 1, 'task page: a Delegate property row sits in the sidebar');
  ok(await delegateRow.locator('.v button').innerText() === 'No delegate', 'task page: the delegate starts empty');
  await delegateRow.locator('.v button').click();
  await delegateRow.getByRole('option', { name: 'Triage agent' }).click();
  await page.waitForTimeout(700);
  const afterSet = await apiIssueDelegate();
  ok(afterSet.delegateId === triage.userId, 'task page: picking an agent wrote issue.delegateId', `${afterSet.delegateId} vs ${triage.userId}`);
  ok(afterSet.delegate && afterSet.delegate.name === 'Triage agent', 'task page: the task now reads back the delegated agent');
  ok(await delegateRow.locator('.v button').innerText() === 'Triage agent', 'task page: the field shows the delegate it stored');

  /* Clear it with the same control. */
  await delegateRow.locator('.v button').click();
  await delegateRow.getByRole('option', { name: 'No delegate' }).click();
  await page.waitForTimeout(700);
  ok((await apiIssueDelegate()).delegateId === null, 'task page: choosing No delegate cleared issue.delegateId');
  ok(await delegateRow.locator('.v button').innerText() === 'No delegate', 'task page: the field is empty again');
  /* Put it back, so the screenshot below is of the delegated state a reader expects. */
  await delegateRow.locator('.v button').click();
  await delegateRow.getByRole('option', { name: 'Triage agent' }).click();
  await page.waitForTimeout(700);
  /* Delegation is not just a column: WP06 opens a session for it, which is what the run queue and
   * the agent page read. So the write above is checked for its run, not only for its delegate. */
  const delegatedRun = (await gql(`query($id:String!){issue(id:$id){agentSession{id state queueReason admitted}}}`,
    { id: reference.identifier })).issue.agentSession;
  ok(Boolean(delegatedRun), 'task page: delegating through the UI opened a session the API can read back', JSON.stringify(delegatedRun));
  ok(['delegated', 'queued'].includes(delegatedRun?.state),
    'task page: the session is a real WP06 row, in the state machine\'s own vocabulary', JSON.stringify(delegatedRun));
  await page.screenshot({ path: path.join(shots, `issue-delegate-${W}.png`) });

  /* A refusal is not an empty workspace. The seeded non-admin is refused by `_require_admin`. */
  const memberCtx = await browser.newContext({ viewport: { width: W, height: H } });
  await memberCtx.addInitScript(t => {
    localStorage.setItem('hoja_token', t);
    localStorage.setItem('hoja_agents_surface', 'on');
  }, memberToken);
  const memberPage = await memberCtx.newPage();
  await memberPage.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  const refusal = await memberPage.waitForSelector('[data-surface="agents-list"] [data-state="error"]', { timeout: 15000 }).catch(() => null);
  ok(Boolean(refusal), 'non-admin: a 403 renders as a stated refusal, not as an empty list');
  ok(await memberPage.evaluate(() => /admin/i.test(document.body.innerText)), 'non-admin: the refusal says who can see agents');
  ok(await memberPage.evaluate(() => !document.querySelector('[data-surface="agents-list"] .lin-row')), 'non-admin: no rows are drawn for a refused read');
  ok(await memberPage.evaluate(() => !document.querySelector('[data-surface="agents-list"] [data-state="empty"]')), 'non-admin: the refusal is not the empty-workspace state');
  await memberPage.screenshot({ path: path.join(shots, `agents-forbidden-${W}.png`) });
  /* The detail page makes the same distinction: a refused read must not claim the agent is absent. */
  await memberPage.goto(`${base}/workspace/agents/${triage.id}`, { waitUntil: 'networkidle' });
  const refusedDetail = await memberPage.waitForSelector('[data-surface="agent-detail"] [data-state="error"]', { timeout: 15000 }).catch(() => null);
  ok(Boolean(refusedDetail), 'non-admin: the agent page states the refusal instead of a missing agent');
  const refusedText = await memberPage.evaluate(() => document.querySelector('[data-surface="agent-detail"] [data-state="error"]')?.innerText || '');
  ok(/admin/i.test(refusedText), 'non-admin: the agent-page refusal says who can see agents', refusedText.replace(/\n/g, ' '));
  ok(!/no agent member/i.test(refusedText), 'non-admin: a 403 is not reported as a 404', refusedText.replace(/\n/g, ' '));
  /* The same refusal inside the task page's Delegate menu: no list, no silent "No delegate". */
  await memberPage.goto(`${base}${referencePath}`, { waitUntil: 'networkidle' });
  await memberPage.waitForSelector('.lin-detail .lin-props', { timeout: 15000 });
  const memberDelegate = memberPage.locator('.lin-props .lin-prop.lin-dd')
    .filter({ has: memberPage.locator('.k', { hasText: 'Delegate' }) }).first();
  await memberDelegate.locator('.v button').click();
  await memberPage.waitForTimeout(600);
  const menuSays = await memberDelegate.locator('.lin-dd-panel').innerText();
  ok(/admin/i.test(menuSays), 'non-admin: the Delegate menu states the refusal instead of offering nothing', menuSays.replace(/\n/g, ' '));
  await memberCtx.close();

  /* There is no fixture left on any agent surface, so none of them carries a sample notice. */
  await page.goto(`${base}${RUNNERS}/${encodeURIComponent(onlineRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runner-detail"]', { timeout: 15000 });
  ok(!(await page.evaluate(() => Boolean(document.querySelector('[data-surface="runner-detail"] [data-sample="on"]')))),
    'runner detail: carries no sample-data notice, because every row on it is a server row');
  for (const [url, surface] of [['/workspace/agents/runners', 'runners-list'], ['/workspace/agents/queue', 'run-queue'], ['/workspace/agents/audit', 'audit-log']]) {
    await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
    await page.waitForSelector(`[data-surface="${surface}"] .lin-row`, { timeout: 15000 });
    ok(!(await page.evaluate(s => Boolean(document.querySelector(`[data-surface="${s}"] [data-sample="on"]`)), surface)),
      `${surface}: carries no sample-data notice, because its rows are server rows now`);
  }

  /* A genuinely absent agent is stated as absent — the other half of the 403/404 distinction. */
  await page.goto(`${base}/workspace/agents/00000000-0000-0000-0000-000000000000`, { waitUntil: 'networkidle' });
  const missing = await page.waitForSelector('[data-surface="agent-detail"] [data-state="error"]', { timeout: 15000 }).catch(() => null);
  ok(Boolean(missing), 'agent page: an unknown id is stated, not left loading');
  const missingText = await page.evaluate(() => document.querySelector('[data-surface="agent-detail"] [data-state="error"]')?.innerText || '');
  ok(/no agent member/i.test(missingText), 'agent page: an unknown id says no such member exists', missingText.replace(/\n/g, ' '));

  /* ---- 8. the run queue, the agent's runs and the audit pane read WP06 and WP05 ----
   *
   * Slice 4's claim is that these three screens stopped drawing fixtures. So the rows are checked
   * against the API that wrote them, the queue's reason is the schedule check's own code, Stop goes
   * through `agentSessionStop`, and the audit event it writes is found by paging the real ledger.
   */
  const apiSession = async id => (await gql(`query($id:String!){agentSession(id:$id){id state status queueReason
    issue{identifier title} activities{kind body}}}`, { id })).agentSession;

  await page.goto(`${base}/workspace/agents/queue`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="run-queue"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(500);
  const queueRows = await page.evaluate(() => [...document.querySelectorAll('[data-surface="run-queue"] .lin-row')]
    .map(r => ({
      ident: r.querySelector('.ident')?.textContent || '',
      chip: r.querySelector('[data-session-chip]')?.getAttribute('data-session-chip') || '',
      chipTitle: r.querySelector('[data-session-chip]')?.getAttribute('title') || '',
      text: r.innerText.replace(/\s+/g, ' '),
    })));
  /* `>= 3`: section 7 delegates the reference task twice more through the UI, so the queue may hold
   * more runs by now. The three below are the ones this file created through the API, and the count
   * is asserted to be a reading of the server, not a fixture's row count. */
  ok(queueRows.length >= 3, 'run queue: one row per session the server stored (workspaceAgentSessions)', String(queueRows.length));
  ok(['Rotate the staging credentials', 'Backfill the audit chain verifier', 'Regenerate the SDK types']
    .every(title => queueRows.some(r => r.text.includes(title))),
    'run queue: every run this file delegated through the API is a row', JSON.stringify(queueRows.map(r => r.ident)));
  ok(!queueRows.some(r => /^AGENT-\d+$/.test(r.ident)), 'run queue: no fixture identifier is presented as a run',
    queueRows.map(r => r.ident).join(','));
  ok(queueRows.some(r => r.text.includes('Rotate the staging credentials')),
    'run queue: the rows are the delegated tasks, with the issue\'s own title', JSON.stringify(queueRows.map(r => r.text)));
  ok(queueRows.every(r => r.ident.startsWith(team.key)),
    'run queue: a row keeps the issue identifier', queueRows.map(r => r.ident).join(','));
  /* The reason is written beside the chip on a wide viewport and into the chip's title on a phone,
   * which is the issue row's own responsive rule: either way it is a rendered reading, not state. */
  ok(queueRows.some(r => r.text.includes('Regenerate the SDK types') && /capacity/i.test(`${r.text} ${r.chipTitle}`)),
    'run queue: a queued run shows the schedule check\'s queue_reason',
    JSON.stringify(queueRows.find(r => r.text.includes('Regenerate'))));
  ok(queueRows.some(r => r.text.includes('Triage agent')), 'run queue: the session chip names the agent the server filed the run under');

  await page.goto(`${base}/workspace/agents/${encodeURIComponent(triage.id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="agent-detail"] .lin-detail-main', { timeout: 15000 });
  await page.waitForTimeout(500);
  const timeline = await page.evaluate(() => [...document.querySelectorAll('[data-surface="agent-detail"] .lin-event[data-kind]')]
    .map(e => e.innerText.replace(/\s+/g, ' ')));
  const runs = await page.evaluate(() => [...document.querySelectorAll('[data-surface="agent-detail"] [data-session-id]')]
    .map(e => ({ id: e.getAttribute('data-session-id'), state: e.getAttribute('data-session-state'), status: e.getAttribute('data-session-status') })));
  ok(runs.length >= 3, 'agent page: lists the agent\'s sessions from the server', String(runs.length));
  ok(timeline.length >= 3, 'agent page: the timeline is the activities the server stored (WP06), not a placeholder', String(timeline.length));
  ok(timeline.some(t => /Starting now|Waiting/.test(t)), 'agent page: the delegation acknowledgement is on the timeline', timeline.join(' | ').slice(0, 160));
  ok(timeline.some(t => /capacity/i.test(t)), 'agent page: the queued run\'s timeline says why it waits', timeline.join(' | ').slice(0, 160));
  ok(await page.evaluate(() => /\d+ running/.test(document.querySelector('[data-surface="agent-detail"] .lin-detail-desc')?.textContent || '')),
    'agent page: the run count is a reading of the session rows, not "not reported"');

  /* Stop, through the mutation: the control names the run it stops, so the test follows that
   * name rather than assuming which row the page chose. */
  const stopButton = page.locator('[data-surface="agent-detail"] .lin-activity button.lin-subscribe-btn').first();
  ok(await stopButton.count() === 1, 'agent page: a non-terminal run offers its Stop control');
  const stopTarget = await page.evaluate(() => {
    const btn = document.querySelector('[data-surface="agent-detail"] .lin-activity button.lin-subscribe-btn');
    const ident = (btn?.textContent || '').replace(/^Stop\s+/, '').trim();
    const row = [...document.querySelectorAll('[data-surface="agent-detail"] [data-session-id]')]
      .find(r => (r.textContent || '').includes(ident));
    return { id: row ? row.getAttribute('data-session-id') : null, ident };
  });
  ok(Boolean(stopTarget.id), 'agent page: the Stop control names a run the session list also shows', JSON.stringify(stopTarget));
  await stopButton.click();
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  ok(await page.locator('[role="dialog"] button.danger').count() === 1, 'agent page: stopping is two-step, with a danger confirmation');
  await page.click('[role="dialog"] button.danger');
  await page.waitForTimeout(900);
  const stoppedState = await apiSession(stopTarget.id);
  ok(stoppedState.state === 'cancelled',
    'agent page: Stop wrote agentSessionStop, and the server state is cancelled',
    JSON.stringify({ state: stoppedState.state, status: stoppedState.status }));
  const stoppedRow = await page.evaluate(id => {
    const row = document.querySelector(`[data-surface="agent-detail"] [data-session-id="${id}"]`);
    return row ? { id, status: row.getAttribute('data-session-status') } : null;
  }, stopTarget.id);
  ok(Boolean(stoppedRow) && stoppedRow.status === 'done',
    'agent page: the stopped run is drawn as done, in the same page, without a reload', JSON.stringify(stoppedRow));
  ok(await page.getByText(/agent\.session\.stop/).count() > 0,
    'agent page: the notice names the audit event the server wrote, instead of inventing one');
  await page.screenshot({ path: path.join(shots, `agent-detail-stopped-${W}.png`) });

  /* The audit ledger: paged, filtered, and the stop is on the newest page. */
  await page.goto(`${base}/workspace/agents/audit`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="audit-log"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  const firstPage = await page.evaluate(() => document.querySelectorAll('[data-surface="audit-log"] .lin-row').length);
  ok(firstPage === 25, 'audit log: the first page is one bounded page of the ledger, not the whole ledger', String(firstPage));
  const loadNewer = page.locator('[data-surface="audit-log"] [data-action="load-more"]');
  ok(await loadNewer.count() === 1, 'audit log: a ledger longer than one page offers the next page');
  await loadNewer.click();
  await page.waitForFunction(() => !document.querySelector('[data-surface="audit-log"] [data-action="load-more"]'), null, { timeout: 15000 });
  const ledger = await page.evaluate(() => ({
    rows: document.querySelectorAll('[data-surface="audit-log"] .lin-row').length,
    stops: [...document.querySelectorAll('[data-surface="audit-log"] [data-action="agent.session.stop"]')]
      .map(r => r.innerText.replace(/\s+/g, ' ')),
  }));
  /* The expected count is read from the ledger, not written down here: this run now writes more
   * than the seed plus the stop (WP12 audits its own policy writes, and this file grants a policy
   * so a lease can be granted at all). The invariant is that `Load newer events` reaches every row
   * the server holds, which is what a pinned number was standing in for. */
  const ledgerTotal = async () => {
    let after = null, total = 0;
    for (;;) {
      const page = (await gql(`query($after:String){auditEvents(first:100, after:$after){
        nodes{seq} pageInfo{hasNextPage endCursor}}}`, {after})).auditEvents;
      total += page.nodes.length;
      if (!page.pageInfo.hasNextPage) return total;
      after = page.pageInfo.endCursor;
    }
  };
  const total = await ledgerTotal();
  ok(ledger.rows === total, 'audit log: Load more reaches every row the ledger holds',
    `${ledger.rows} drawn vs ${total} stored`);
  ok(ledger.rows > firstPage, 'audit log: the second page appends rows to the first', `${firstPage} → ${ledger.rows}`);
  ok(ledger.stops.length === 1, 'audit log: the stop is in the server\'s ledger', JSON.stringify(ledger.stops));
  ok(await page.locator(`[data-surface="audit-log"] [data-action="agent.session.stop"][data-target="${stopTarget.id}"]`).count() === 1,
    'audit log: the row names the session it stopped', JSON.stringify(ledger.stops[0] || ''));
  ok(await page.locator('[data-surface="audit-log"] [data-action="agent.session.stop"] [data-actor-kind="human"]').count() === 1,
    'audit log: the row names the actor kind the credential carried');
  await page.screenshot({ path: path.join(shots, `audit-log-paged-${W}.png`) });

  /* A server-side filter, not a pass over the loaded rows: the resolver takes action and actorId. */
  await page.selectOption('[data-surface="audit-log"] select[aria-label="Filter by action"]', 'agent.session.stop');
  await page.waitForTimeout(700);
  const filtered = await page.evaluate(() => ({
    rows: document.querySelectorAll('[data-surface="audit-log"] .lin-row').length,
    actions: [...new Set([...document.querySelectorAll('[data-surface="audit-log"] .lin-row')].map(r => r.getAttribute('data-action')))],
    more: Boolean(document.querySelector('[data-surface="audit-log"] [data-action="load-more"]')),
  }));
  ok(filtered.rows === 1 && filtered.actions.length === 1 && filtered.actions[0] === 'agent.session.stop',
    'audit log: the action filter is the resolver\'s, and it re-pages the filtered ledger', JSON.stringify(filtered));
  ok(filtered.more === false, 'audit log: a filtered page that fits reports no further page');

  /* The ledger is admin-only, and a refused read is stated as a refusal. */
  const auditCtx = await browser.newContext({ viewport: { width: W, height: H } });
  await auditCtx.addInitScript(t => { localStorage.setItem('hoja_token', t); localStorage.setItem('hoja_agents_surface', 'on'); }, memberToken);
  const auditMember = await auditCtx.newPage();
  await auditMember.goto(`${base}/workspace/agents/audit`, { waitUntil: 'networkidle' });
  const auditRefusal = await auditMember.waitForSelector('[data-surface="audit-log"] [data-refusal="forbidden"]', { timeout: 15000 }).catch(() => null);
  ok(Boolean(auditRefusal), 'audit log: a non-admin gets a stated refusal, not an empty ledger');
  ok(await auditMember.evaluate(() => !document.querySelector('[data-surface="audit-log"] .lin-row')),
    'audit log: a refused read draws no rows');
  ok(await auditMember.evaluate(() => !document.querySelector('[data-surface="audit-log"] [data-state="empty"]')),
    'audit log: the refusal is not the empty-ledger state');
  ok(/admin/i.test(await auditMember.evaluate(() => document.querySelector('[data-surface="audit-log"]')?.innerText || '')),
    'audit log: the refusal says who can read the ledger');
  await auditMember.screenshot({ path: path.join(shots, `audit-forbidden-${W}.png`) });
  /* The run queue is the same admin-gated read, so it refuses the same way. */
  await auditMember.goto(`${base}/workspace/agents/queue`, { waitUntil: 'networkidle' });
  const queueRefusal = await auditMember.waitForSelector('[data-surface="run-queue"] .lin-error', { timeout: 15000 }).catch(() => null);
  ok(Boolean(queueRefusal), 'run queue: a non-admin gets a stated refusal, not an empty queue');
  ok(/admin/i.test(await auditMember.evaluate(() => document.querySelector('[data-surface="run-queue"]')?.innerText || '')),
    'run queue: the refusal says who can read every agent\'s runs');
  await auditCtx.close();

  /* ---- 8b. WP07's lease and WP10's verifier, over the real routes, and what the screens show ----
   *
   * The runners list, the run queue and the agent page each gained a half of this in slice 5:
   * `runners`/`agentLeases` for the machines, and `verifierClaims`/`agentVerifications` for the
   * claims. So this section drives the actual runner contract — claim the queued lease, finish with
   * a claim that answers the task's acceptance item — and then reads the result back on each
   * screen, signs it through `agentSessionVerify`, and reads the verdict and its checks back the
   * same way. Nothing here asserts a fixture: Connect is the only sample left, and it is not
   * touched below.
   */
  /* Which task the runner takes is the server's choice (`_grantable` takes the oldest queued
   * attempt), and section 8 stopped one run, so this section reads the task off the grant instead
   * of assuming which one it is. */
  const runnerAuth = {authorization: `Bearer ${onlineRunner.token}`, 'content-type': 'application/json'};
  const leaseResponse = await fetch(`${base}/runner/lease`, {
    method: 'POST', headers: runnerAuth,
    body: JSON.stringify({runner_id: onlineRunner.runner_id, poll_timeout_ms: 0}),
  });
  const lease = (await leaseResponse.json()).lease;
  ok(Boolean(lease), 'runner lease: the machine registered above was granted the queued work');
  ok(lease && lease.harness === 'pi', 'runner lease: the harness is the one Hoja enqueued and the runner declared', String(lease && lease.harness));

  /* The task's acceptance list is what a verifier checks a claim against (agent_verify.py
   * `acceptance_items`), so it is written before the claim that answers it. */
  await gql(`mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}`,
    {id: lease.task_id, input: {description: '## Acceptance\n\n- the widget spins\n'}});
  const leasedTask = (await gql(`query($id:String!){issue(id:$id){identifier title agentSession{id state}}}`,
    {id: lease.task_id})).issue;
  ok(Boolean(leasedTask && leasedTask.agentSession), 'runner lease: the task it took has the session the delegation opened',
    JSON.stringify(leasedTask && leasedTask.agentSession));
  const runOneSession = leasedTask.agentSession.id;

  /* ---- 8c. Hoja Connect: the exposure rows are the ones the runner reported (WP09) ----
   *
   * The runner half (PR #215) opens a `tailcat serve` per exposure; this half is the record the
   * screen reads. Two exposures are reported through the real `/runner/connect/open` route, with
   * the lease the runner just took: one is revoked through the UI, the other is left to prove the
   * lease's end closes it. The address never exists client-side — the server has no column for it.
   */
  const connectAuth = {authorization: `Bearer ${onlineRunner.token}`, 'content-type': 'application/json'};
  const reportExposure = async (exposureId, ports) => {
    const response = await fetch(`${base}/runner/connect/open`, {
      method: 'POST', headers: connectAuth,
      body: JSON.stringify({
        lease_id: lease.lease_id, run_id: lease.run_id, fence: lease.fence, token: lease.token,
        exposure_id: exposureId, ports, host: 'localhost',
        pinned_peer: 'nodekey:' + 'b'.repeat(64),
      }),
    });
    const payload = await response.json();
    assert.equal(response.status, 200, JSON.stringify(payload));
    return payload;
  };
  const uiExposure = await reportExposure('exp-ui', [3000]);
  const leaseExposure = await reportExposure('exp-lease', [9229]);
  ok(uiExposure.state === 'open' && uiExposure.ports[0] === 3000,
    'connect: the open is recorded through /runner/connect/open', JSON.stringify(uiExposure));
  const apiEndpoints = async () => (await gql(`{connectEndpoints(live: true) {nodes {
    id exposureId runnerId leaseId port host pinnedPeer state expiresAt}}}`)).connectEndpoints.nodes;
  const liveRows = await apiEndpoints();
  ok(liveRows.length === 2, 'connect: two endpoints are live in the workspace', JSON.stringify(liveRows.map(r => r.port)));
  ok(liveRows.every(r => r.leaseId === lease.lease_id && r.state === 'open'),
    'connect: both rows belong to the lease the runner holds, and both are open');

  await page.goto(`${base}${RUNNERS}/${encodeURIComponent(onlineRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runner-detail"] [data-exposure]', { timeout: 15000 });
  await page.waitForTimeout(300);
  const exposureRows = () => page.evaluate(() => [...document.querySelectorAll('[data-surface="runner-detail"] [data-exposure]')]
    .map(r => ({
      id: r.getAttribute('data-exposure'),
      ttl: r.querySelector('[data-ttl]')?.getAttribute('data-ttl') || '',
      text: r.innerText.replace(/\s+/g, ' '),
    })));
  const drawn = await exposureRows();
  ok(drawn.length === 2 && drawn.some(r => r.id === 'exp-ui') && drawn.some(r => r.id === 'exp-lease'),
    'runner detail: the exposures the runner reported are the rows the screen draws', JSON.stringify(drawn));
  ok(drawn.every(r => Number(r.ttl) > 0),
    'runner detail: each row carries the TTL left, read from the exposure\'s own deadline', JSON.stringify(drawn.map(r => r.ttl)));
  ok(drawn.some(r => /3000/.test(r.text)) && drawn.some(r => /9229/.test(r.text)),
    'runner detail: the rows name the ports the allowlist published', JSON.stringify(drawn.map(r => r.text)));
  ok(!canary.test(await page.content()),
    'runner detail: the tailcat address is nowhere in the page, because no column stores it');
  ok(await page.evaluate(() => !/nodekey:[0-9a-f]{64}/.test(document.body.innerText)),
    'runner detail: the pinned peer is shortened in the row, full only in a title attribute');
  await page.screenshot({ path: path.join(shots, `runner-detail-connect-${W}.png`) });

  /* Revoke, two steps, and the ledger says so afterwards: the close is a server write now. */
  await page.click('[data-exposure="exp-ui"] button.lin-btn');
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  ok(await exposureRows().then(r => r.length) === 2, 'connect: revoke is two-step, the row stays until confirmed');
  await page.click('[role="dialog"] button.danger');
  await page.waitForTimeout(600);
  const afterRevoke = await exposureRows();
  ok(afterRevoke.length === 1 && afterRevoke[0].id === 'exp-lease',
    'connect: the revoke removes that exposure and only that one', JSON.stringify(afterRevoke.map(r => r.id)));
  ok(await page.getByText(/Revoked and audited/).count() > 0, 'connect: the revoke reports the audit action it wrote');
  await page.screenshot({ path: path.join(shots, `runner-detail-revoked-${W}.png`) });

  /* Through the app, not a fresh load: the audit pane reads the same ledger the revoke wrote to.
   * The ledger is oldest-first and paged, so the close is on the newest page — which is what the
   * pane's own "Load newer events" control is for. */
  await page.click('[data-surface="runner-detail"] button.crumb-link');
  await page.waitForSelector('[data-surface="runners-list"]', { timeout: 15000 });
  await page.getByRole('tab', { name: 'Audit log' }).click();
  await page.waitForSelector('[data-surface="audit-log"] .lin-row', { timeout: 15000 });
  const newer = page.locator('[data-surface="audit-log"] [data-action="load-more"]');
  if (await newer.count()) {
    await newer.click();
    await page.waitForTimeout(600);
  }
  const closes = await page.evaluate(() => [...document.querySelectorAll('[data-surface="audit-log"] [data-action="connect.exposure.close"]')]
    .map(r => ({target: r.getAttribute('data-target') || '', text: r.innerText.replace(/\s+/g, ' ')})));
  ok(closes.some(r => r.target === 'exp-ui'),
    'audit log: the revoke is a connect.exposure.close row in the server ledger', JSON.stringify(closes));
  ok(closes.some(r => /exp-ui/.test(r.text) && /revoked/.test(r.text)),
    'audit log: the close says which exposure it closed and why', JSON.stringify(closes.map(r => r.text)));
  ok(!(await apiEndpoints()).some(r => r.exposureId === 'exp-ui'),
    'connect: the revoked exposure is closed in the record, not merely hidden');

  /* The runner holding a lease is a fact the Runners screen must draw: the row's chip and the
   * machine's own page, both read from `runners` and `agentLeases`. */
  await page.goto(`${base}${RUNNERS}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runners-list"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  const leasedRow = await page.evaluate(id => {
    const row = [...document.querySelectorAll('[data-surface="runners-list"] .lin-row')]
      .find(r => r.querySelector('.ident')?.textContent === id);
    return row ? {lease: row.querySelector('[data-lease]')?.getAttribute('data-lease') || '',
      state: row.querySelector('[data-lease]')?.getAttribute('data-lease-state') || '',
      text: row.innerText.replace(/\s+/g, ' ')} : null;
  }, onlineRunner.runner_id);
  ok(Boolean(leasedRow) && leasedRow.lease === lease.lease_id,
    'runner list: the row carries the lease the machine is holding (agentLeases)', JSON.stringify(leasedRow));
  ok(Boolean(leasedRow) && leasedRow.state === 'active', 'runner list: the lease chip says the lease is active', String(leasedRow && leasedRow.state));
  ok(Boolean(leasedRow) && leasedRow.text.includes('TREBOL'),
    'runner list: the chip names the task the machine took, by its identifier', String(leasedRow && leasedRow.text));
  ok((await apiRunners()).find(r => r.id === onlineRunner.runner_id)?.status === 'online',
    `runner list: holding a lease is Hoja's own evidence of life, so the row reads online`);

  await page.goto(`${base}${RUNNERS}/${encodeURIComponent(onlineRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runner-detail"] [data-current-lease]', { timeout: 15000 });
  await page.waitForTimeout(300);
  ok(await page.evaluate(() => document.querySelector('[data-surface="runner-detail"] [data-current-lease]')?.getAttribute('data-current-lease')) === lease.lease_id,
    'runner detail: names the lease the machine holds right now');
  ok(await page.locator(`[data-surface="runner-detail"] [data-lease-row="${lease.lease_id}"]`).count() === 1,
    `runner detail: that lease is in the machine's lease list too (agentLeases(runnerId))`);

  /* Finish with a claim that answers the acceptance item. */
  const finishResponse = await fetch(`${base}/runner/finish`, {
    method: 'POST', headers: runnerAuth,
    body: JSON.stringify({
      lease_id: lease.lease_id, run_id: lease.run_id, fence: lease.fence, token: lease.token,
      state: 'completed', summary: 'the widget spins', model: 'e2e-worker',
      idempotency_key: `${lease.run_id}:1`, evidence: {backend: 'e2e'},
      acceptance: [{item: 'the widget spins', evidence: 'the e2e run'}],
    }),
  });
  const ack = await finishResponse.json();
  assert.equal(finishResponse.status, 200, JSON.stringify(ack));
  ok(ack.session_state === 'verifying', 'runner finish: a claim leaves the session awaiting verification, never done', String(ack.session_state));

  /* WP09: an exposure cannot outlive the lease that opened it. The finish closes the record. */
  const closedRows = (await gql(`{connectEndpoints(leaseId: "${lease.lease_id}") {nodes {
    exposureId port state closeReason closedAt}}}`)).connectEndpoints.nodes;
  const left = closedRows.find(r => r.exposureId === 'exp-lease');
  ok(Boolean(left) && left.state === 'closed' && left.closeReason === 'lease_finished',
    'connect: finishing the lease closes the exposure it opened, with the reason', JSON.stringify(left));
  ok(Boolean(left) && Boolean(left.closedAt), 'connect: the close carries the time it happened');
  ok(!(await apiEndpoints()).length, 'connect: no exposure of this workspace is live after the lease ended');

  const claimedSession = async () => (await gql(
    `query($id:String!){agentSession(id:$id){id state verdict claimAt claim}}`, {id: runOneSession})).agentSession;
  const afterFinish = await claimedSession();
  ok(afterFinish.state === 'verifying', 'the stored session is verifying', JSON.stringify(afterFinish.state));
  ok(afterFinish.verdict === null, 'no verdict is recorded until a separate principal signs');
  ok(Boolean(afterFinish.claimAt), 'the claim carries the time it was submitted');

  /* The run queue groups it, and the row says a verifier is what it waits on. */
  await page.goto(`${base}/workspace/agents/queue`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="run-queue"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  const awaitingRow = await page.evaluate(title => {
    const row = [...document.querySelectorAll('[data-surface="run-queue"] .lin-row')]
      .find(r => (r.innerText || '').includes(title));
    const group = row?.closest('.lin-group');
    return row ? {
      claim: row.querySelector('[data-claim]')?.getAttribute('data-claim') || '',
      group: group?.querySelector('.lin-group-head b')?.textContent || '',
      text: row.innerText.replace(/\s+/g, ' '),
    } : null;
  }, leasedTask.title);
  ok(Boolean(awaitingRow), 'run queue: the finished run is a row', JSON.stringify(awaitingRow));
  ok(Boolean(awaitingRow) && awaitingRow.group === 'Awaiting verification',
    'run queue: it is in the awaiting-verification group, because the session is verifying', String(awaitingRow && awaitingRow.group));
  ok(Boolean(awaitingRow) && awaitingRow.claim === 'awaiting',
    'run queue: the row says a verifier has not signed it yet', String(awaitingRow && awaitingRow.claim));

  /* The agent page's claim view: what was claimed, by which model, and that it waits. */
  const claimsOnPage = () => page.evaluate(() => [...document.querySelectorAll('[data-surface="agent-detail"] [data-claim-row]')]
    .map(r => ({
      id: r.getAttribute('data-claim-row'),
      verdict: r.getAttribute('data-verdict'),
      chip: r.querySelector('[data-verdict-chip]')?.getAttribute('data-verdict-chip') || '',
      text: r.innerText.replace(/\s+/g, ' '),
    })));
  await page.goto(`${base}/workspace/agents/${encodeURIComponent(triage.id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="agent-detail"] [data-claim-row]', { timeout: 15000 });
  const waitingClaims = await claimsOnPage();
  const mine = waitingClaims.find(c => c.id === runOneSession);
  ok(Boolean(mine), `agent page: the run's claim is on the agent page (verifierClaims)`, JSON.stringify(waitingClaims.map(c => c.id)));
  ok(Boolean(mine) && mine.verdict === 'awaiting', 'agent page: the claim has no verdict yet', String(mine && mine.verdict));
  ok(Boolean(mine) && /the widget spins/.test(mine.text),
    'agent page: the claim is the summary the runner submitted', String(mine && mine.text).slice(0, 160));
  ok(Boolean(mine) && /TREBOL-\d+/.test(mine.text), 'agent page: the claim names the task it belongs to', String(mine && mine.text).slice(0, 160));
  /* Scroll the claim into view before the artefact: it is the part of this page that is new, and a
   * screenshot of the fold above it would show none of it. */
  await page.locator(`[data-surface="agent-detail"] [data-claim-row="${runOneSession}"]`).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(shots, `agent-detail-claims-${W}.png`) });

  /* Sign it, as a separate principal: the seeded admin did not do this work, so separation passes
   * and the evidence is re-read. No pull request is linked, so `uncertain` is what may be recorded
   * even though the verifier asks for `uncertain` anyway — a gap can never become a pass. */
  const signed = await gql(`mutation($id:String!){agentSessionVerify(id:$id, verdict:"uncertain",
    rationale:"no pull request is linked yet"){success agentSession{id state verdict}}}`, {id: runOneSession});
  ok(signed.agentSessionVerify.success && signed.agentSessionVerify.agentSession.verdict === 'uncertain',
    'verifier: agentSessionVerify recorded the verdict on the claim', JSON.stringify(signed));
  ok(signed.agentSessionVerify.agentSession.state === 'verifying',
    'verifier: an uncertain verdict leaves the claim awaiting evidence, not done');

  /* The queue's chip becomes the verdict, and the agent page shows the checks it was recorded on. */
  await page.goto(`${base}/workspace/agents/queue`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="run-queue"] .lin-row', { timeout: 15000 });
  await page.waitForTimeout(400);
  ok(await page.evaluate(() => Boolean(document.querySelector('[data-surface="run-queue"] [data-verdict="uncertain"]'))),
    'run queue: the row now carries the verdict a separate principal recorded');

  await page.goto(`${base}/workspace/agents/${encodeURIComponent(triage.id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="agent-detail"] [data-claim-row]', { timeout: 15000 });
  await page.waitForTimeout(300);
  const settled = await claimsOnPage();
  const settledMine = settled.find(c => c.id === runOneSession);
  ok(Boolean(settledMine) && settledMine.verdict === 'uncertain',
    'agent page: the claim row reports the verdict (met / not_met / uncertain)', JSON.stringify(settledMine));
  const checks = await page.evaluate(id => {
    const row = document.querySelector(`[data-surface="agent-detail"] [data-claim-row="${id}"]`);
    return [...(row?.querySelectorAll('[data-check]') || [])]
      .map(c => ({name: c.getAttribute('data-check'), outcome: c.getAttribute('data-outcome')}));
  }, runOneSession);
  ok(checks.length >= 3, 'agent page: the verdict is shown with the checks it was recorded on', JSON.stringify(checks));
  ok(checks.some(c => c.name === 'acceptance_list' && c.outcome === 'passed'),
    `agent page: the acceptance check passed, because the claim answers the task's acceptance item`, JSON.stringify(checks));
  ok(checks.some(c => c.name === 'pull_request' && c.outcome !== 'passed'),
    'agent page: the missing pull request is a gap, not a pass', JSON.stringify(checks));
  /* `agent_verify.CHECK_ORDER` is the whole vocabulary, and none of the five reads a CI status:
   * this project runs no CI, so a check that consulted one would be reading a signal that does not
   * exist. Asserting membership catches both an invented check and a renamed one. */
  const KNOWN_CHECKS = ['principal_separation', 'acceptance_list', 'pull_request', 'merged_on_main', 'deployed'];
  ok(checks.every(c => KNOWN_CHECKS.includes(c.name)),
    'agent page: every check is one agent_verify.py re-reads, and none reads CI',
    JSON.stringify(checks.map(c => c.name)));
  await page.locator(`[data-surface="agent-detail"] [data-claim-row="${runOneSession}"]`).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(shots, `agent-detail-verdict-${W}.png`) });

  /* And the machine's own page: the lease it finished is history, not a current grant. */
  await page.goto(`${base}${RUNNERS}/${encodeURIComponent(onlineRunner.runner_id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runner-detail"] [data-leases]', { timeout: 15000 });
  await page.waitForTimeout(300);
  ok(await page.locator(`[data-surface="runner-detail"] [data-lease-row="${lease.lease_id}"][data-lease-state="finished"]`).count() === 1,
    `runner detail: the finished lease is in the machine's history (agentLeases reads the state back)`);
  ok(await page.evaluate(() => document.querySelector('[data-surface="runner-detail"] [data-current-lease]')?.getAttribute('data-current-lease') === ''),
    'runner detail: a finished lease is no longer the current one');
  await page.goto(`${base}${RUNNERS}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-surface="runners-list"] .lin-row', { timeout: 15000 });
  ok(await page.evaluate(() => !document.querySelector('[data-surface="runners-list"] [data-lease]')),
    'runner list: with the lease finished, no row claims to be holding one');

  /* ---- 9. the production build ships the surface off by default ---- */
  const offCtx = await browser.newContext({ viewport: { width: W, height: H } });
  await offCtx.addInitScript(t => { localStorage.setItem('hoja_token', t); }, token);
  const offPage = await offCtx.newPage();
  await offPage.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  await offPage.waitForTimeout(500);
  ok(await offPage.evaluate(() => Boolean(document.querySelector('[data-surface="agents-disabled"]'))), 'unflagged production build: the surface renders the disabled notice');
  ok(await offPage.evaluate(() => !document.querySelector('[data-surface="agents-list"]')), 'unflagged production build: no agent rows are rendered');
  ok(await offPage.evaluate(() => ![...document.querySelectorAll('.nav-item')].some(b => b.textContent?.trim() === 'Agents')), 'unflagged production build: no Agents entry in the sidebar');
  await offPage.screenshot({ path: path.join(shots, `agents-disabled-${W}.png`) });
  await offCtx.close();

  /* ---- 10. a 401 is never an empty list ---- */
  /* The shell answers a refused session before the surface renders (sign-in gate), so this asserts
   * the whole path: no agents list, and no empty-workspace state claiming the workspace has none. */
  const anonCtx = await browser.newContext({ viewport: { width: W, height: H } });
  await anonCtx.addInitScript(() => { localStorage.setItem('hoja_token', 'expired-token'); localStorage.setItem('hoja_agents_surface', 'on'); });
  const anonPage = await anonCtx.newPage();
  await anonPage.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  /* Wait for the gate to answer, deterministically, and fail loudly when it never does. The shell
   * renders the sign-in screen once `viewer` comes back refused, and under load that answer can
   * take seconds: the old wait swallowed its own timeout with `.catch(() => undefined)` and then
   * asserted anyway, so a slow gate read as "the agent list is missing" — the flake this replaces.
   * `.login-page` is the gate's own container, so this waits for the thing, not for a phrase. */
  const gate = await anonPage.waitForSelector('.login-page', { timeout: 30000 })
    .then(() => null).catch(e => e.message.split('\n')[0]);
  ok(gate === null, 'signed-out: the sign-in gate answered before anything is asserted', gate || '');
  ok(await anonPage.evaluate(() => Boolean(document.querySelector('.login-page'))),
    'signed-out: the gate is the page that was rendered');
  ok(await anonPage.evaluate(() => !document.querySelector('[data-surface="agents-list"]')), 'signed-out: no agent list is drawn');
  ok(await anonPage.evaluate(() => !document.querySelector('[data-surface="agents-list"] [data-state="empty"]')), 'signed-out: the workspace is not reported as having no agents');
  ok(await anonPage.evaluate(() => /sign in|welcome back|authentication required/i.test(document.body.innerText)), 'signed-out: the page asks the reader to sign in');
  await anonPage.screenshot({ path: path.join(shots, `agents-signed-out-${W}.png`) });
  await anonCtx.close();

  ok(errors.length === 0, 'no page errors', errors.join(' | '));
  await browser.close();
  server.kill();
  console.log(`\nscreenshots: ${shots}`);
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
