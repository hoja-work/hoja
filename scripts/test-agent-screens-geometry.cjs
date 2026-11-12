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
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
};
run(`from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
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

  await page.goto(`${base}/workspace/agents/AGENT-1`, { waitUntil: 'networkidle' });
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

  /* ---- 6. the production build ships the surface off by default ---- */
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

  ok(errors.length === 0, 'no page errors', errors.join(' | '));
  await browser.close();
  server.kill();
  console.log(`\nscreenshots: ${shots}`);
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); server.kill(); process.exit(1); });
