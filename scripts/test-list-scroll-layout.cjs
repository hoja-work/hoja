// Run from repo root: node scripts/test-list-scroll-layout.cjs
// A long My issues list must scroll inside the fixed-height shell, not get clipped by it.
// Fault check: UI_LAYOUT_CSS=/path/to/css-without-fix must fail.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const rows = Array.from({ length: 42 }, (_, i) => `<div class="my-issue-row"><span>Issue ${i + 1}</span></div>`).join('');
const body = `<div class="app"><div class="app-body"><div class="workspace-content"><main class="main"><div class="my-issues-view"><div class="my-issue-toolbar">Toolbar</div><div class="my-issues-list"><section class="my-issue-group">${rows}</section></div></div></main></div></div></div>`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 800 } });
      await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>${body}</body></html>`);
      await page.addStyleTag({ content: css });
      await page.addStyleTag({ content: '.app{height:800px;display:flex;flex-direction:column}.app-body{display:flex;flex:1;min-height:0}.workspace-content{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column}' });
      await page.addStyleTag({ content: '.main{overflow:hidden}' });
      const list = page.locator('.my-issues-list');
      const before = await list.evaluate(el => ({ oy: getComputedStyle(el).overflowY, range: el.scrollHeight - el.clientHeight }));
      assert.ok(['auto', 'scroll'].includes(before.oy), `${width}px: list is not a scroll container (overflow-y=${before.oy})`);
      assert.ok(before.range > 0, `${width}px: list has no scroll range`);
      await list.hover();
      await page.mouse.wheel(0, 400);
      await page.waitForTimeout(200);
      const top = await list.evaluate(el => el.scrollTop);
      assert.ok(top > 0, `${width}px: wheel did not scroll the list (scrollTop=${top})`);
      await page.close();
    }
    console.log('List scroll layout: My issues list scrolls inside the shell at 390 and 1440px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
