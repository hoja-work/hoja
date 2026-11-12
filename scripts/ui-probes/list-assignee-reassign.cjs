// List view: the assignee avatar in a team issue row must open a picker that
// reassigns the issue, mirroring the board card's assignee control.
//
// Run from repo root against a seeded server:
//   node scripts/ui-probes/list-assignee-reassign.cjs
// Env: PROBE_URL (default http://127.0.0.1:8797), PROBE_TOKEN (required),
//      PROBE_TEAM (default eng).
//
// It exercises the interaction the way a keyboard user does, and it fails on the
// pre-change tree: the avatar was a static <span>, so there is no button, no
// menu, no mutation and no focus return. Exit 1 on the first failed assertion.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const assert = require('node:assert/strict');

const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
const team = process.env.PROBE_TEAM || 'eng';
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }

const results = [];
const step = async (name, fn) => {
  try { await fn(); results.push(`PASS  ${name}`); }
  catch (e) { results.push(`FAIL  ${name}: ${e.message}`); throw Object.assign(new Error(name), {results}); }
};

(async () => {
  // The pinned browsers in this image are a newer revision than the local
  // playwright package expects, so allow an explicit binary.
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PROBE_CHROMIUM || undefined,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const mutations = [];
    const errors = [];
    page.on('response', async r => {
      if (!r.url().includes('/graphql')) return;
      try {
        const req = r.request().postDataJSON();
        if (req?.query && /\bmutation\b/i.test(req.query) && /assignee/i.test(JSON.stringify(req.variables || {}))) {
          const body = await r.json().catch(() => null);
          mutations.push({vars: req.variables, errors: body?.errors || null});
        }
      } catch { /* not a graphql json response */ }
    });
    page.on('pageerror', e => errors.push(e.message));

    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${url}/workspace/team/${team}/active`);
    await page.waitForSelector('.my-issue-row', {timeout: 15000});
    const row = page.locator('.my-issue-row').first();
    const before = (await row.locator('code').first().innerText()).trim();

    await step('assignee control is a labelled menu button inside the row', async () => {
      const btn = row.locator('button[aria-haspopup="listbox"][aria-label^="Assignee of "]');
      assert.equal(await btn.count(), 1, 'row has no assignee listbox button');
      await btn.waitFor({state: 'visible', timeout: 4000});
    });

    const btn = row.locator('button[aria-haspopup="listbox"][aria-label^="Assignee of "]').first();
    const label0 = await btn.getAttribute('aria-label');

    await step('click opens a menu listing the assignee choices', async () => {
      await btn.click();
      const menu = row.locator('[role="listbox"]');
      await menu.waitFor({state: 'visible', timeout: 3000});
      const items = menu.locator('[role="option"]');
      assert.ok(await items.count() >= 2, `menu has ${await items.count()} items`);
      assert.match(await items.first().innerText(), /Unassigned/);
      assert.equal(await btn.getAttribute('aria-expanded'), 'true');
    });

    await step('Escape closes the menu and returns focus to the trigger', async () => {
      await page.keyboard.press('Escape');
      await row.locator('[role="listbox"]').waitFor({state: 'detached', timeout: 3000});
      assert.equal(await btn.getAttribute('aria-expanded'), 'false');
      const focused = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName);
      assert.match(String(focused), /^Assignee of /, `focus went to ${focused}`);
    });

    await step('Enter reopens and ArrowDown moves focus through the items', async () => {
      await btn.focus();
      await page.keyboard.press('Enter');
      const menu = row.locator('[role="listbox"]');
      await menu.waitFor({state: 'visible', timeout: 3000});
      await page.keyboard.press('ArrowDown');
      const idx = await page.evaluate(() => {
        const items = [...document.querySelectorAll('[role="listbox"] [role="option"]')];
        return items.indexOf(document.activeElement);
      });
      assert.equal(idx, 1, `ArrowDown left focus on item ${idx}`);
    });

    await step('outside click closes the menu', async () => {
      await page.mouse.click(5, 5);
      await row.locator('[role="listbox"]').waitFor({state: 'detached', timeout: 3000});
    });

    await step('choosing a member reassigns via the existing mutation and does not navigate', async () => {
      await btn.click();
      const menu = row.locator('[role="listbox"]');
      await menu.waitFor({state: 'visible', timeout: 3000});
      // Pick an option that actually differs from the current assignee, otherwise the
      // picker (correctly) skips the mutation and the row keeps its label. The option's
      // visible text is avatar-initial + name, so read the trailing text node.
      const nameOf = el => (el.childNodes[el.childNodes.length - 1].textContent || '').trim();
      const current = (await btn.getAttribute('aria-label')).split(': ').slice(1).join(': ');
      const options = menu.locator('[role="option"]');
      let target = null, targetLabel = '';
      for (let i = 0; i < await options.count(); i++) {
        const option = options.nth(i);
        const name = await option.evaluate(nameOf);
        if (name !== current) { target = option; targetLabel = name; break; }
      }
      assert.ok(target, `every assignee option equals the current assignee "${current}"`);
      const pathBefore = await page.evaluate(() => location.pathname);
      await target.click();
      await menu.waitFor({state: 'detached', timeout: 4000});
      await page.waitForTimeout(1200);
      assert.equal(await page.evaluate(() => location.pathname), pathBefore, 'row click navigated away');
      assert.ok(mutations.length > 0, 'no assignee mutation was sent');
      assert.equal(mutations.at(-1).errors, null, `mutation returned errors: ${JSON.stringify(mutations.at(-1).errors)}`);
      const label1 = await btn.getAttribute('aria-label');
      assert.notEqual(label1, label0, 'aria-label of the assignee button did not change');
      assert.ok(label1.endsWith(targetLabel), `label "${label1}" does not report "${targetLabel}"`);
    });

    await step('the row still opens the issue from the title button', async () => {
      await row.locator('button.my-issue-title-open').click();
      await page.waitForTimeout(600);
      const opened = await page.evaluate(() => location.pathname);
      assert.ok(opened.includes(before) && opened !== '/workspace/team/' + process.env.PROBE_TEAM,
        `title click did not open ${before}: ${opened}`);
    });

    assert.deepEqual(errors, [], `page errors: ${errors.join(' | ')}`);
    console.log(results.join('\n'));
    console.log(`\nlive list assignee picker: ${results.length} checks passed${before ? ` (row ${before})` : ''}`);
  } catch (e) {
    console.log(results.join('\n'));
    console.error(`\nprobe failed: ${e.message}`);
    process.exitCode = 1;
  } finally { await browser.close(); }
})();
