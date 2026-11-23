// Read-only probe: the My issues → Activity feed loads its next page when the list is scrolled
// near the end, with no click; the manual button stays as the explicit keyboard path and the
// fallback when IntersectionObserver is unavailable; a page that does not advance stops the
// feed with an error instead of looping.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/activity-lazy-load-probe.cjs
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8811';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
let failures = 0;
const ok = (label, cond, detail) => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail === undefined ? '' : ' ' + JSON.stringify(detail)}`);
  if (!cond) failures += 1;
};
const isActivity = body => (body || '').includes('MyIssueActivity');
const isNextPage = body => isActivity(body) && /"after":"[^"]+"/.test(body || '');
const rows = page => page.locator('.my-issue-row').count();
const scrollToEnd = page => page.evaluate(() => { const l = document.querySelector('.my-issues-list'); l.scrollTop = l.scrollHeight; });
const waitFor = async (page, fn, ms) => {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await fn()) return true; await page.waitForTimeout(100); }
  return false;
};
async function open(page) {
  await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
  await page.goto(url + '/workspace/my-issues/activity');
  await page.waitForSelector('.my-issues-view', { timeout: 8000 });
  await page.waitForSelector('.my-issue-row', { timeout: 8000 });
  await page.waitForTimeout(600);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    // 1) Scrolling near the end loads the next page with no click, and the feed ends cleanly.
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      let activity = 0, nextPage = 0;
      page.on('request', r => { const d = r.postData(); if (isActivity(d)) activity += 1; if (isNextPage(d)) nextPage += 1; });
      await open(page);
      const initialRows = await rows(page);
      ok('initial activity page rendered', initialRows > 0, { rows: initialRows });
      ok('manual button is present as the fallback', await page.locator('.my-issue-load-more').count() === 1);
      ok('no next page requested before scrolling', nextPage === 0, { nextPage });

      await scrollToEnd(page);
      const grew = await waitFor(page, async () => (await rows(page)) > initialRows, 6000);
      ok('scrolling to the end loads more rows without a click', grew && nextPage === 1, { rows: await rows(page), nextPage });

      // Keep scrolling until the feed reports no more pages; the loop must terminate.
      let steps = 0, lastRows = await rows(page);
      while (steps < 6 && await page.locator('.my-issue-load-more').count()) {
        await scrollToEnd(page);
        await page.waitForTimeout(900);
        const now = await rows(page);
        if (now === lastRows && steps > 3) break;
        lastRows = now; steps += 1;
      }
      const totalRows = await rows(page);
      ok('the end of the feed removes the load-more button', await page.locator('.my-issue-load-more').count() === 0, { rows: totalRows });

      const before = activity;
      await page.waitForTimeout(1500);
      ok('no fetch loop at the end of the list', activity === before, { requestsBefore: before, after: activity });

      const domTitles = await page.locator('.my-issue-row code').allInnerTexts();
      const unique = new Set(domTitles);
      ok('no duplicate rows rendered', unique.size === domTitles.length, { rows: domTitles.length, unique: unique.size });
      await page.close();
    }

    // 2) Two rapid intersections while a page is in flight must not start a second page fetch.
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      let nextPage = 0;
      await page.route('**/graphql', async route => {
        if (isNextPage(route.request().postData())) { nextPage += 1; await page.waitForTimeout(1200); }
        await route.continue();
      });
      await open(page);
      await Promise.all([scrollToEnd(page), page.waitForTimeout(50), scrollToEnd(page), page.waitForTimeout(50), scrollToEnd(page)]);
      await page.waitForTimeout(2200);
      ok('duplicate concurrent page fetches are suppressed', nextPage === 1, { nextPage });
      await page.unroute('**/graphql');
      await page.close();
    }

    // 3) A page that does not advance surfaces the error once and stops auto-loading.
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      let nextPage = 0;
      await page.route('**/graphql', async route => {
        if (!isNextPage(route.request().postData())) return route.continue();
        nextPage += 1;
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { myIssueActivity: { nodes: [], pageInfo: { hasNextPage: true, endCursor: null } } } }) });
      });
      await open(page);
      await scrollToEnd(page);
      const errored = await waitFor(page, async () => (await page.locator('.my-issue-page-error').count()) > 0, 5000);
      const text = errored ? await page.locator('.my-issue-page-error').first().innerText() : '';
      ok('a non-advancing page reports an error', errored && /pagination did not advance/i.test(text), { text });
      const seen = nextPage;
      await scrollToEnd(page);
      await page.waitForTimeout(1200);
      ok('an errored page is not retried in a loop', nextPage === seen, { requests: nextPage });
      await page.unroute('**/graphql');
      await page.close();
    }

    // 4) Without IntersectionObserver the button remains the way to page, and no click happens by itself.
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.addInitScript(() => { try { delete window.IntersectionObserver; } catch { window.IntersectionObserver = undefined; } });
      let nextPage = 0;
      page.on('request', r => { if (isNextPage(r.postData())) nextPage += 1; });
      await open(page);
      const initialRows = await rows(page);
      await scrollToEnd(page);
      await page.waitForTimeout(900);
      ok('no auto-load when IntersectionObserver is unavailable', nextPage === 0 && (await rows(page)) === initialRows, { nextPage, rows: await rows(page) });
      const button = page.locator('.my-issue-load-more');
      ok('the fallback button is keyboard reachable', await button.count() === 1);
      await button.focus();
      await button.press('Enter');
      const grew = await waitFor(page, async () => (await rows(page)) > initialRows, 6000);
      ok('the fallback button still loads the next page', grew, { rows: await rows(page) });
      await page.close();
    }

    // 5) A filter that hides every row must not page the whole feed in the background.
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      let nextPage = 0;
      page.on('request', r => { if (isNextPage(r.postData())) nextPage += 1; });
      await open(page);
      await page.locator('button[aria-label="Add filter"]').click();
      await page.locator('.my-issue-popover input[type=search]').fill('zzzz-no-such-issue');
      await page.waitForTimeout(500);
      await scrollToEnd(page);
      await page.waitForTimeout(1500);
      ok('a filter that hides every row does not auto-page', nextPage === 0, { nextPage });
      ok('the empty state is still shown for a filter with no matches', await page.locator('.my-issues-empty').count() === 1);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log(failures === 0 ? 'RESULT PASS' : `RESULT FAIL (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
