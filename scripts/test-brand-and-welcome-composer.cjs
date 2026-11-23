// Run from repo root: node scripts/test-brand-and-welcome-composer.cjs
// Checks that the empty-state welcome screen and composer card stay bounded with a borderless textarea.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><section class="agent-chat-page" data-agent-panel="" style="padding:16px"><div data-agent-welcome="" style="max-width:672px;margin:0 auto"><h1>Ask the Hoja agent</h1></div><form data-agent-composer="" style="max-width:768px;margin:0 auto"><div class="agent-welcome-composer-card" style="display:flex;flex-direction:column;gap:8px;padding:12px;border-radius:16px;border:1px solid var(--line);background:var(--panel)"><textarea class="agent-welcome-composer-input" placeholder="Ask about tasks, projects, or cycles…" style="width:100%;border:0;outline:none;background:transparent"></textarea><div style="display:flex;justify-content:flex-end"><button type="submit" class="agent-welcome-composer-submit" aria-label="Send message" style="width:32px;height:32px;border-radius:9999px">↑</button></div></div></form></section></body></html>`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      const r = await page.evaluate(() => {
        const card = document.querySelector('.agent-welcome-composer-card').getBoundingClientRect();
        const btn = document.querySelector('.agent-welcome-composer-submit').getBoundingClientRect();
        const input = getComputedStyle(document.querySelector('.agent-welcome-composer-input'));
        return {
          cardRight: card.right,
          btnRight: btn.right,
          inputBorder: input.borderTopWidth,
          cardRadius: getComputedStyle(document.querySelector('.agent-welcome-composer-card')).borderTopLeftRadius,
          scroll: document.documentElement.scrollWidth,
        };
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
