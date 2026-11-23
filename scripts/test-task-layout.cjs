// Run via cd frontend && npm run test:layout; CSS-backed component fixtures.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const crumb = `<section class="issue-detail-page"><header class="issue-detail-crumb"><button>← My issues</button><span>›</span><span class="issue-detail-crumb-ws">WorkspaceNameWithoutSpacesVeryVeryVeryVeryLong</span><span>›</span><span class="issue-detail-crumb-id">TEAM-10000</span><span class="issue-detail-crumb-title">${'A long issue title with context '.repeat(12)}</span><span class="issue-detail-more"><button aria-label="More actions">…</button></span></header><div class="issue-detail-columns"><main class="issue-detail-main"><textarea class="issue-detail-title" aria-label="Title">A long issue title</textarea><div class="issue-detail-description"><textarea aria-label="Description">A long description</textarea></div></main><aside class="issue-detail-properties">Properties</aside></div></section>`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 390, 700, 701, 1200]) {
      const page = await browser.newPage({ viewport: { width, height: 720 }, hasTouch: width <= 700, isMobile: width <= 700 });
      await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1">${crumb}<div class="kanban-cards"><div class="kanban-card"><strong>Sample card</strong></div></div>`);
      await page.addStyleTag({ content: css });
      const boxes = await page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        const style = selector => getComputedStyle(document.querySelector(selector));
        return { bar: box('.issue-detail-crumb'), id: box('.issue-detail-crumb-id'),
          more: box('.issue-detail-more button'), ws: box('.issue-detail-crumb-ws'),
          title: box('.issue-detail-crumb-title'), input: box('.issue-detail-title'),
          touch: style('.kanban-card').touchAction, scroll: document.documentElement.scrollWidth };
      });
      assert.ok(boxes.id.left >= boxes.bar.left && boxes.id.right <= boxes.bar.right &&
        boxes.more.left >= boxes.bar.left && boxes.more.right <= boxes.bar.right,
      `${width}px: issue identifier or actions escape breadcrumb`);
      assert.ok(boxes.input.width > 0 && boxes.input.right <= width + 1,
        `${width}px: editable title exceeds viewport`);
      assert.ok(boxes.scroll <= width + 1, `${width}px: horizontal overflow`);
      if (width <= 700) assert.ok(boxes.touch.includes('pan-y'), `${width}px: touch scroll blocked on board card`);
      await page.close();
    }
    console.log('Issue components: 5 viewports passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
