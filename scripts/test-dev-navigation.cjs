// Run against scripts/dev-local.sh: HOJA_DEV_TEST_URL=http://100.x.y.z:5173 node scripts/test-dev-navigation.cjs
// This must use Vite, not a production build: React Grab loads CSS only in development.
const {chromium} = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.HOJA_DEV_TEST_URL || 'http://127.0.0.1:5173';
const token = fs.readFileSync(path.join(__dirname, '../.hoja/dev/token'), 'utf8').trim();
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [390, 1440]) {
      const context = await browser.newContext({viewport: {width, height: 900}});
      await context.addInitScript(value => localStorage.setItem('hoja_token', value), token);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base);
      await page.waitForSelector('[data-workspace-sidebar]');
      await page.waitForFunction(() => [...document.querySelectorAll('style[data-vite-dev-id]')].some(node => node.getAttribute('data-vite-dev-id').endsWith('/dev/react-grab.css')));
      const labels = await page.locator('[aria-label="Workspace navigation"] [data-nav-item]').allTextContents();
      let previous = null;
      for (const label of labels) {
        if (page.url().includes('/settings')) {
          await page.goto(base);
          await page.waitForSelector('[data-workspace-sidebar]');
        }
        if (width < 701) await page.getByRole('button', {name: 'Open navigation', exact: true}).click();
        await page.locator('[aria-label="Workspace navigation"] [data-nav-item]').filter({hasText: new RegExp('^' + label.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$')}).first().click();
        await page.waitForTimeout(200);
        const content = await page.locator('[data-main]').evaluate(node => {
          const rect = node.getBoundingClientRect();
          return {width: rect.width, height: rect.height, text: node.innerText.trim()};
        });
        assert.ok(content.width >= width - (width > 700 ? 300 : 30), `${width}px ${label}: content width collapsed to ${content.width}`);
        assert.ok(content.height > 100, `${label}: content height collapsed`);
        assert.ok(content.text.length > 0, `${label}: blank content`);
        assert.notEqual(content.text, previous, `${label}: content did not change after navigation`);
        previous = content.text;
      }
      if (width > 700) assert.equal(await page.locator('[data-app-body]').evaluate(node => getComputedStyle(node).display), 'contents', 'vendor utilities must not override desktop shell');
      assert.deepEqual(errors, [], 'navigation must not throw');
      console.log(`Development navigation: ${labels.length} destinations render at ${width}px after React Grab CSS loads`);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
