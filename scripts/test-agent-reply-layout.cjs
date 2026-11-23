// Run from repo root: node scripts/test-agent-reply-layout.cjs
// Agent replies have no avatar; collapsed tool call group & issue mentions stay bounded; composer keeps send button inside frame.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><section class="agent-chat-page" data-agent-panel="" style="display:flex;flex-direction:column;padding:16px"><article data-agent-message="assistant" style="max-width:768px;width:100%;margin:0 auto"><div data-tool-group="" data-tool-group-state="completed" style="border:1px solid var(--line);border-radius:12px;background:var(--panel)"><button type="button" data-tool-group-trigger="" aria-expanded="false" style="display:flex;width:100%;align-items:center;gap:8px;padding:8px 12px"><span>Queried workspace</span></button></div><div class="agent-chat-reply"><p>Working on <a class="agent-issue-chip" href="/workspace/issue/TREBOL-12" style="display:inline-flex;align-items:center;gap:6px;max-width:min(320px,100%);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><svg width="14" height="14"><circle cx="7" cy="7" r="6"/></svg><span class="agent-issue-chip-id">TREBOL-12</span><span class="agent-issue-chip-title" style="overflow:hidden;text-overflow:ellipsis">Fix the login redirect loop on mobile Safari</span></a> next.</p></div></article><form data-agent-composer="" style="max-width:768px;width:100%;margin:0 auto"><div class="agent-composer-card" style="display:flex;flex-direction:column;gap:8px;padding:12px;border-radius:16px;border:1px solid var(--line);background:var(--panel)"><textarea placeholder="Ask about issues" style="width:100%;border:0;background:transparent"></textarea><div style="display:flex;justify-content:flex-end"><button type="submit" class="agent-composer-submit" aria-label="Send message" style="width:32px;height:32px;border-radius:9999px">↑</button></div></div></form></section></body></html>`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      const r = await page.evaluate(() => {
        const chip = document.querySelector('.agent-issue-chip').getBoundingClientRect();
        const toolTrigger = document.querySelector('[data-tool-group-trigger]');
        const comp = document.querySelector('.agent-composer-card').getBoundingClientRect();
        const btn = document.querySelector('.agent-composer-submit').getBoundingClientRect();
        return {
          avatar: !!document.querySelector('.agent-msg-avatar'),
          toolCollapsed: toolTrigger?.getAttribute('aria-expanded') === 'false',
          chipRight: chip.right,
          chipIcon: !!document.querySelector('.agent-issue-chip svg'),
          btnRight: btn.right,
          compRight: comp.right,
          scroll: document.documentElement.scrollWidth,
        };
      });
      assert.equal(r.avatar, false, `${width}px: assistant reply must not render an avatar`);
      assert.equal(r.toolCollapsed, true, `${width}px: tool call trace must render collapsed by default`);
      assert.ok(r.chipIcon, `${width}px: issue mention must render a status icon`);
      assert.ok(r.chipRight <= width + 1, `${width}px: issue chip overflows viewport (${r.chipRight}px)`);
      assert.ok(r.btnRight <= r.compRight + 1, `${width}px: send button outside composer`);
      assert.ok(r.scroll <= width + 1, `${width}px: page overflows horizontally (${r.scroll}px)`);
      await page.close();
    }
    console.log('Agent reply layout: no avatar, tool calls collapsed, issue chips bounded, composer contained at 320/390/1440px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
