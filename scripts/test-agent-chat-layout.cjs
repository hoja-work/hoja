// Agent chat short-height viewport invariant (CSS fixture, not authenticated UI).
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const fixture = `<div class="app"><header class="topbar"><button class="mobile-nav-toggle">Navigation</button></header><div class="app-body"><aside class="sidebar"></aside><div class="workspace-content"><main class="main"><section class="agent-chat-page"><div class="openui-agent-container" style="display:flex;flex-direction:column"><div class="openui-agent-thread-scroll" style="flex:1;min-height:0;overflow:auto">Messages</div><div class="openui-agent-composer-slot"><div class="openui-agent-thread-composer"><textarea class="openui-agent-thread-composer__input" placeholder="Ask anything"></textarea><button class="openui-agent-thread-composer__submit-button">↑</button></div></div></div></section></main></div></div></div>`;
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [width, height] of [[320, 360], [390, 568], [701, 360], [1200, 800]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      await page.setContent(fixture);
      await page.addStyleTag({ content: css });
      const boxes = await page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        return { composer: box('.openui-agent-thread-composer'), input: box('.openui-agent-thread-composer__input'),
          send: box('.openui-agent-thread-composer__submit-button') };
      });
      for (const [name, box] of Object.entries(boxes)) assert.ok(box.left >= 0 && box.right <= width && box.top >= 0 && box.bottom <= height,
        `${width}x${height}: ${name} clipped outside visible viewport`);
      await page.close();
    }
    console.log('Agent chat composer: 4 viewports passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
