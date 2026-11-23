// Agent chat short-height viewport invariant.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const fixture = `<div class="app"><header class="topbar"><button class="mobile-nav-toggle">Navigation</button></header><div class="app-body"><aside class="sidebar"></aside><div class="workspace-content"><main class="main"><section class="agent-chat-page" data-agent-panel="" style="display:flex;flex-direction:column"><div data-agent-scroll="" style="flex:1;min-height:0;overflow:auto">Messages</div><div style="flex:0 0 auto;padding:8px 16px 24px"><form data-agent-composer="" style="max-width:768px;margin:0 auto"><div style="display:flex;flex-direction:column;gap:8px;padding:12px;border-radius:16px;border:1px solid var(--line);background:var(--panel)"><textarea class="agent-composer-input" placeholder="Ask anything" style="width:100%;border:0;background:transparent"></textarea><div style="display:flex;justify-content:flex-end"><button type="submit" class="agent-composer-send" style="width:32px;height:32px;border-radius:9999px">↑</button></div></div></form></div></section></main></div></div></div>`;
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [width, height] of [[320, 360], [390, 568], [701, 360], [1200, 800]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      await page.setContent(fixture);
      await page.addStyleTag({ content: css });
      const boxes = await page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        return {
          composer: box('[data-agent-composer]'),
          input: box('.agent-composer-input'),
          send: box('.agent-composer-send'),
        };
      });
      for (const [name, box] of Object.entries(boxes)) assert.ok(box.left >= 0 && box.right <= width && box.top >= 0 && box.bottom <= height,
        `${width}x${height}: ${name} clipped outside visible viewport`);
      await page.close();
    }
    console.log('Agent chat composer: 4 viewports passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
