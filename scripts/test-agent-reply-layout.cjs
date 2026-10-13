// Run from repo root: node scripts/test-agent-reply-layout.cjs
// Agent replies have no avatar; issue mentions render as bounded chips; the composer keeps its send button inside the frame.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><section class="agent-chat-page"><div class="openui-agent-container"><div class="openui-agent-thread-messages"><div class="agent-msg agent-msg-assistant"><div class="agent-msg-body agent-chat-reply"><p>Working on <a class="agent-issue-chip" href="/workspace/issue/TREBOL-12"><svg width="14" height="14"><circle cx="7" cy="7" r="6"/></svg><span class="agent-issue-chip-id">TREBOL-12</span><span class="agent-issue-chip-title">Fix the login redirect loop on mobile Safari</span></a> next.</p></div></div></div><div class="openui-agent-composer-slot"><div class="agent-composer"><textarea class="agent-composer-input" placeholder="Ask about issues"></textarea><button class="agent-composer-send" aria-label="Send">↑</button></div></div></div></section></body></html>`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      const r = await page.evaluate(() => {
        const chip = document.querySelector('.agent-issue-chip').getBoundingClientRect();
        const comp = document.querySelector('.agent-composer').getBoundingClientRect();
        const btn = document.querySelector('.agent-composer-send').getBoundingClientRect();
        return { avatar: !!document.querySelector('.agent-msg-avatar'), chipRight: chip.right, chipIcon: !!document.querySelector('.agent-issue-chip svg'), btnRight: btn.right, compRight: comp.right, scroll: document.documentElement.scrollWidth };
      });
      assert.equal(r.avatar, false, `${width}px: assistant reply must not render an avatar`);
      assert.ok(r.chipIcon, `${width}px: issue mention must render a status icon`);
      assert.ok(r.chipRight <= width + 1, `${width}px: issue chip overflows viewport (${r.chipRight}px)`);
      assert.ok(r.btnRight <= r.compRight + 1, `${width}px: send button outside composer`);
      assert.ok(r.scroll <= width + 1, `${width}px: page overflows horizontally (${r.scroll}px)`);
      await page.close();
    }
    console.log('Agent reply layout: no avatar, issue chips bounded, composer contained at 320/390/1440px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
