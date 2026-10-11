// Run from repo root: node scripts/test-overflow-layout.cjs
// Long unbroken text in list rows and feedback banners must wrap, not push actions or the page sideways.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const long = 'Averyveryveryverylongunbrokenwordthatcannotwrapatall'.repeat(3);
const cases = {
  list: `<div class="app"><main class="main"><div class="issue-list"><div class="issue-row"><button type="button" class="issue-row-open"><span class="priority" data-p="2"></span><span class="issue-copy"><b>${long}</b><small>ENG-1 · Team</small></span></button><button class="status-picker">Todo</button></div></div></main></div>`,
  feedback: `<div class="app"><main class="main"><div class="app-error" role="alert"><b>Couldn’t load this workspace</b><span>${long}</span><button class="outline">Dismiss</button></div></main></div>`,
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 390, 701, 1200]) {
      for (const [name, body] of Object.entries(cases)) {
        const page = await browser.newPage({ viewport: { width, height: 720 } });
        await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>${body}</body></html>`);
        await page.addStyleTag({ content: css });
        const r = await page.evaluate(() => {
          const actions = [...document.querySelectorAll('.status-picker, .app-error button')].map(el => el.getBoundingClientRect().right);
          return { actionRight: Math.max(...actions), scrollWidth: document.documentElement.scrollWidth };
        });
        assert.ok(r.actionRight <= width + 1, `${name} ${width}px: action pushed off-screen (${r.actionRight}px)`);
        assert.ok(r.scrollWidth <= width + 1, `${name} ${width}px: page overflows horizontally (${r.scrollWidth}px)`);
        await page.close();
      }
    }
    console.log('Overflow layout: list rows and feedback banners wrap at 320-1200px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
