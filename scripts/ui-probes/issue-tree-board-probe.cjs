// HOJA-33 (Cloud port): the board and the task page must show the same hierarchy the list shows.
//
// Run against a seeded local server serving the built bundle:
//   node scripts/ui-probes/issue-tree-board-probe.cjs
// Env: PROBE_URL (default http://127.0.0.1:8811), PROBE_TOKEN (required), PROBE_TEAM (default eng),
//      PROBE_PARENT / PROBE_CHILD (identifiers of a parent that has 3 sub-tasks, one done, and of
//      a sub-task of it), SEED=1 to create the tree itself through GraphQL.
//
// Every assertion is falsifiable on the pre-change tree: the board rendered one flat card per
// issue (no data-depth, no rollup, no parent chip) and the task page had no parent crumb.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const assert = require('node:assert/strict');

const url = process.env.PROBE_URL || 'http://127.0.0.1:8811';
const token = process.env.PROBE_TOKEN;
const team = process.env.PROBE_TEAM || 'eng';
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }

async function gql(query, variables) {
  const r = await fetch(`${url}/graphql`, {
    method: 'POST',
    headers: {'content-type': 'application/json', authorization: `Bearer ${token}`},
    body: JSON.stringify({query, variables: variables || {}}),
  });
  const b = await r.json();
  if (b.errors) throw new Error(b.errors[0].message);
  return b.data;
}

async function seed() {
  const meta = await gql('{teams(first:5){nodes{id key states{nodes{id name type}}}}}');
  const t = meta.teams.nodes.find(x => x.key.toLowerCase() === team) || meta.teams.nodes[0];
  const byType = {};
  for (const s of t.states.nodes) byType[s.type === 'started' ? s.name : s.type] = s.id;
  const M = 'mutation($t:String!,$ti:String!,$s:String,$p:String){issueCreate(input:{teamId:$t,title:$ti,stateId:$s,parentId:$p}){issue{id identifier}}}';
  const mk = async (title, state, parent) => (await gql(M, {t: t.id, ti: title, s: state, p: parent})).issueCreate.issue;
  const p = await mk('Parent epic (probe)', byType.unstarted);
  const c1 = await mk('Child one todo', byType.unstarted, p.id);
  const c2 = await mk('Child two done', byType.completed, p.id);
  const c3 = await mk('Child three in progress', byType['In Progress'], p.id);
  const g1 = await mk('Grandchild under child one', byType.unstarted, c1.id);
  return {parent: p.identifier, child: c1.identifier, done: c2.identifier, other: c3.identifier, grand: g1.identifier};
}

const results = [];
const step = async (name, fn) => {
  try { await fn(); results.push(`PASS  ${name}`); }
  catch (e) { results.push(`FAIL  ${name}: ${e.message}`); throw Object.assign(new Error(name), {results}); }
};

(async () => {
  const ids = process.env.SEED
    ? await seed()
    : {parent: process.env.PROBE_PARENT, child: process.env.PROBE_CHILD,
       done: process.env.PROBE_DONE, other: process.env.PROBE_OTHER, grand: process.env.PROBE_GRAND};
  if (!ids.parent || !ids.child) { console.error('set PROBE_PARENT and PROBE_CHILD, or SEED=1'); process.exit(2); }

  const browser = await chromium.launch({headless: true, executablePath: process.env.PROBE_CHROMIUM || undefined});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  try {
    // ---- board -------------------------------------------------------------
    await page.goto(`${url}/workspace/team/${team}/active`);
    await page.waitForSelector('.my-issue-row, .kanban-card', {timeout: 20000});
    const toBoard = page.locator('button[aria-label="Switch to board view"]');
    if (await toBoard.count()) await toBoard.first().click();
    await page.waitForSelector('.kanban .kanban-card-wrap', {timeout: 15000});

    const card = ident => page.locator('.kanban .kanban-card-wrap', {
      has: page.locator(`button[aria-label^="Open ${ident}:"]`),
    });

    await step('the parent card carries the real completed/total rollup', async () => {
      const roll = card(ids.parent).locator('.kanban-rollup');
      assert.equal(await roll.count(), 1, 'parent card has no rollup');
      assert.equal((await roll.getAttribute('aria-label')).trim(), '1 of 3 sub-tasks complete');
      assert.equal((await roll.innerText()).trim(), '1/3');
    });

    await step('a same-column sub-task is nested at depth 1 under its parent', async () => {
      const d = await card(ids.child).getAttribute('data-depth');
      assert.equal(d, '1', `child data-depth is ${d}`);
      const order = await page.locator('.kanban .kanban-card-wrap').evaluateAll(
        els => els.map(e => (e.querySelector('button[aria-label^="Open "]')?.getAttribute('aria-label') || '')));
      const pi = order.findIndex(l => l.startsWith(`Open ${ids.parent}:`));
      const ci = order.findIndex(l => l.startsWith(`Open ${ids.child}:`));
      assert.ok(pi >= 0 && ci === pi + 1, `child does not directly follow parent (parent ${pi}, child ${ci})`);
    });

    await step('a grandchild is nested at depth 2', async () => {
      assert.equal(await card(ids.grand).getAttribute('data-depth'), '2');
    });

    await step('a sub-task in another column keeps a parent chip instead of looking like a root', async () => {
      const chip = card(ids.done).locator('.kanban-parent-chip');
      assert.equal(await chip.count(), 1, 'cross-column child has no parent chip');
      assert.equal((await chip.innerText()).trim(), `↳${ids.parent}`);
      assert.equal(await card(ids.done).getAttribute('data-depth'), '0');
    });

    // ---- task page ---------------------------------------------------------
    await step('the parent task page lists each sub-task with its identifier and a count', async () => {
      await page.goto(`${url}/workspace/task/${ids.parent}`);
      await page.waitForSelector('.lin-page', {timeout: 20000});
      const rows = page.locator('.lin-subsec[aria-label="Sub-tasks"] .lin-subrow');
      await rows.first().waitFor({timeout: 15000});
      assert.equal(await rows.count(), 3, `sub-task rows: ${await rows.count()}`);
      const idents = await rows.locator('.ident').allInnerTexts();
      assert.ok(idents.includes(ids.done), `sub-task rows expose ${JSON.stringify(idents)}`);
      const pill = page.locator('.lin-subsec[aria-label="Sub-tasks"] .lin-subissue-pill');
      assert.match(await pill.first().getAttribute('title'), /1 of 3 sub-tasks complete/);
    });

    await step('a child task page links back to its parent', async () => {
      await page.goto(`${url}/workspace/task/${ids.child}`);
      await page.waitForSelector('.lin-page', {timeout: 20000});
      // The task body loads after the shell, so wait for the crumb instead of sampling once.
      await page.waitForSelector(`.lin-crumb-parent[data-parent-path="${ids.parent}"]`, {timeout: 15000}).catch(() => {});
      const crumb = page.locator(`.lin-crumb-parent[data-parent-path="${ids.parent}"]`);
      assert.equal(await crumb.count(), 1, 'child page has no parent crumb');
      await crumb.click();
      await page.waitForFunction(p => location.pathname.includes(`/workspace/task/${p}`), ids.parent, {timeout: 10000});
      await page.waitForSelector('.lin-page', {timeout: 15000});
    });

    await step('the page logged no errors while doing all of the above', async () => {
      assert.deepEqual(errors, []);
    });
  } catch {
    /* step() already recorded the failure */
  } finally {
    await browser.close();
  }
  console.log(results.join('\n'));
  const failed = results.filter(r => r.startsWith('FAIL')).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})();
