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
e.dispose()`);
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
  const cyclePath = `/workspace/team/${key}/cycle/active`;
  const referencePath = `/workspace/task/${encodeURIComponent(reference.identifier)}/issue`;

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

  /* ---- 6. the runner screens (slice 2): lists and details, measured against the same two
   * reference pages, with a non-vacuity guard ----
   *
   * The guard matters: the loops above compare with `ok(a === b)`, which passes when a selector
   * was missing and both sides are `null`. A new screen has no history to reassure us, so every
   * key is first asserted to have been *measured* on both sides. */
  const RUNNERS = '/workspace/agents/runners';
  const canary = /\btc[A-Za-z0-9_-]{20,}\b/; // mirrors TAILCAT_ADDRESS_RE in runner/hoja_runnerd/connect.py;
  // app/scripts/check-runner-contract.py proves the fixture matches the Python regex itself.

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

  await page.goto(`${base}${RUNNERS}/RUNNER-1`, { waitUntil: 'networkidle' });
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

  /* The address is a bearer secret. The fixture seeds one, the adapter drops it, and the page
   * must not contain it in any form. */
  ok(!canary.test(await page.content()), 'runner detail: no tailcat address anywhere in the rendered HTML');
  ok(await page.evaluate(() => !/nodekey:[0-9a-f]{64}/.test(document.body.innerText)),
    'runner detail: the pinned peer is shortened in the row, full only in a title attribute');

  /* Revoke: two steps, then a state transition that is visible on a different screen. */
  const exposureRows = () => page.evaluate(() => document.querySelectorAll('[data-surface="runner-detail"] [data-exposure]').length);
  const beforeRevoke = await exposureRows();
  ok(beforeRevoke >= 1, 'runner detail shows its live exposure', String(beforeRevoke));
  await page.click('[data-exposure="exp-1"] button.lin-btn');
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  ok(await exposureRows() === beforeRevoke, 'revoke is two-step: the exposure stays until the dialog is confirmed');
  await page.click('[role="dialog"] button.danger');
  await page.waitForTimeout(400);
  ok(await exposureRows() === beforeRevoke - 1, 'revoke removes the exposure only after confirmation', `${beforeRevoke} → ${await exposureRows()}`);
  ok(await page.getByText(/Revoked and audited/).count() > 0, 'revoke reports the audit row it wrote');
  await page.screenshot({ path: path.join(shots, `runner-detail-revoked-${W}.png`) });

  /* Through the app, not a fresh load: the mock store is in-memory, so a reload would re-seed it
   * and this would be asserting the fixture rather than the transition. The detail page has no
   * tab strip (it is a detail page), so go back to the list the way a person does, then switch. */
  await page.click('[data-surface="runner-detail"] button.crumb-link');
  await page.waitForSelector('[data-surface="runners-list"]', { timeout: 15000 });
  await page.getByRole('tab', { name: 'Audit log' }).click();
  await page.waitForSelector('[data-surface="audit-log"] [data-action="connect.revoked"]', { timeout: 15000 });
  ok(true, 'the revoke is visible on the audit screen in the same session, which reads the same store');

  /* The kill switch is a state, not an empty list. */
  const offConnect = await browser.newContext({ viewport: { width: W, height: H } });
  await offConnect.addInitScript(t => {
    localStorage.setItem('hoja_token', t);
    localStorage.setItem('hoja_agents_surface', 'on');
    localStorage.setItem('hoja_agents_connect', 'off');
  }, token);
  const offConnectPage = await offConnect.newPage();
  await offConnectPage.goto(`${base}${RUNNERS}/RUNNER-2`, { waitUntil: 'networkidle' });
  await offConnectPage.waitForSelector('[data-surface="runner-detail"][data-connect="off"]', { timeout: 15000 });
  ok(await offConnectPage.evaluate(() => Boolean(document.querySelector('[data-connect-notice="off"]'))),
    'Connect disabled: the screen says so instead of showing an empty list');
  ok(await offConnectPage.evaluate(() => [...document.querySelectorAll('[data-exposure] button')].every(b => b.disabled)),
    'Connect disabled: no revoke control is offered');
  await offConnectPage.screenshot({ path: path.join(shots, `runner-detail-connect-off-${W}.png`) });
  await offConnect.close();

  /* Keyboard parity with the issue list, on the new list. */
  const runnerKb = await rowKeyboard(RUNNERS);
  ok(Boolean(runnerKb.entered) && runnerKb.entered.inRow, 'runner list: Tab from the group header lands inside a row', `after ${runnerKb.tabs} tab(s)`);
  ok(runnerKb.second.isTitle && runnerKb.second.inRow, 'runner list: the next Tab reaches the row title', runnerKb.second.label || runnerKb.second.tag);
  ok(runnerKb.after < runnerKb.before && /^Expand /.test(runnerKb.afterLabel || ''), 'runner list: the group chevron collapses from the keyboard', `${runnerKb.before} → ${runnerKb.after} rows`);

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

  /* The fixture surfaces say they are fixtures; the real ones do not. */
  for (const [url, surface] of [['/workspace/agents/queue', 'run-queue'], ['/workspace/agents/audit', 'audit-log'], ['/workspace/agents/runners', 'runners-list']]) {
    await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
    await page.waitForSelector(`[data-surface="${surface}"]`, { timeout: 15000 });
    ok(await page.evaluate(s => Boolean(document.querySelector(`[data-surface="${s}"] [data-sample="on"]`)), surface),
      `${surface}: says out loud that its rows are sample data`);
  }

  /* A genuinely absent agent is stated as absent — the other half of the 403/404 distinction. */
  await page.goto(`${base}/workspace/agents/00000000-0000-0000-0000-000000000000`, { waitUntil: 'networkidle' });
  const missing = await page.waitForSelector('[data-surface="agent-detail"] [data-state="error"]', { timeout: 15000 }).catch(() => null);
  ok(Boolean(missing), 'agent page: an unknown id is stated, not left loading');
  const missingText = await page.evaluate(() => document.querySelector('[data-surface="agent-detail"] [data-state="error"]')?.innerText || '');
  ok(/no agent member/i.test(missingText), 'agent page: an unknown id says no such member exists', missingText.replace(/\n/g, ' '));

  /* ---- 8. the production build ships the surface off by default ---- */
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

  /* ---- 9. a 401 is never an empty list ---- */
  /* The shell answers a refused session before the surface renders (sign-in gate), so this asserts
   * the whole path: no agents list, and no empty-workspace state claiming the workspace has none. */
  const anonCtx = await browser.newContext({ viewport: { width: W, height: H } });
  await anonCtx.addInitScript(() => { localStorage.setItem('hoja_token', 'expired-token'); localStorage.setItem('hoja_agents_surface', 'on'); });
  const anonPage = await anonCtx.newPage();
  await anonPage.goto(`${base}/workspace/agents`, { waitUntil: 'networkidle' });
  await anonPage.waitForTimeout(700);
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
