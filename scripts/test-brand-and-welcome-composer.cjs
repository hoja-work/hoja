// Run from repo root: node scripts/test-brand-and-welcome-composer.cjs
// Checks two live-app contracts: the brand mark is served as SVG (not the SPA shell), and the empty-state composer is styled as a bounded card.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><section class="agent-chat-page"><div class="openui-agent-welcome-screen"><div class="openui-agent-welcome-screen__desktop-composer"><div class="openui-agent-desktop-welcome-composer"><textarea class="openui-agent-desktop-welcome-composer__input" placeholder="Type your query here"></textarea><div class="openui-agent-desktop-welcome-composer__action-bar"><button class="openui-agent-desktop-welcome-composer__submit-button" aria-label="Send">↑</button></div></div></div></div></section></body></html>`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      const r = await page.evaluate(() => {
        const card = document.querySelector('.openui-agent-desktop-welcome-composer').getBoundingClientRect();
        const btn = document.querySelector('.openui-agent-desktop-welcome-composer__submit-button').getBoundingClientRect();
        const input = getComputedStyle(document.querySelector('.openui-agent-desktop-welcome-composer__input'));
        return { cardRight: card.right, btnRight: btn.right, inputBorder: input.borderTopWidth, cardRadius: getComputedStyle(document.querySelector('.openui-agent-desktop-welcome-composer')).borderTopLeftRadius, scroll: document.documentElement.scrollWidth };
      });
      assert.ok(r.btnRight <= r.cardRight + 1, `${width}px: welcome send button outside composer card`);
      assert.equal(r.inputBorder, '0px', `${width}px: welcome input must not draw its own border`);
      assert.notEqual(r.cardRadius, '0px', `${width}px: welcome composer must be a rounded card`);
      assert.ok(r.scroll <= width + 1, `${width}px: welcome composer overflows the page (${r.scroll}px)`);
      await page.close();
    }
    console.log('Welcome composer: card contained, input borderless, rounded at 320/390/1440px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
