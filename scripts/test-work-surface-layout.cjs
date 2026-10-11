// Rendered My Issues and board-menu invariants; run via npm run test:layout.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const only = process.argv.find(arg => arg.startsWith('--case='))?.slice(7);

(async () => {
  const browser = await chromium.launch();
  try {
    if (!only || only === 'tabs') for (const width of [320, 390, 700]) {
      const page = await browser.newPage({ viewport: { width, height: 568 } });
      await page.setContent(`<div class="main"><header class="page-head my-issues-head"><h1>My issues</h1></header><nav class="my-issue-tabs">${['Assigned', 'Created', 'Subscribed', 'Activity'].map(t => `<button>${t}</button>`).join('')}</nav><div class="my-issues-view"><div class="my-issue-toolbar"><button>Layout</button><button>Filter</button><button>Display</button></div></div></div>`);
      await page.addStyleTag({ content: css });
      const result = await page.evaluate(() => {
        const tabs = document.querySelector('.my-issue-tabs');
        tabs.scrollLeft = tabs.scrollWidth;
        const rect = el => el.getBoundingClientRect().toJSON();
        const activity = rect(tabs.querySelector('button:last-child'));
        const toolbar = rect(document.querySelector('.my-issue-toolbar'));
        const overlap = activity.left < toolbar.right && activity.right > toolbar.left && activity.top < toolbar.bottom && activity.bottom > toolbar.top;
        return { activity, toolbar, overlap };
      });
      assert.ok(!result.overlap, `${width}px: Activity tab is covered by toolbar`);
      await page.close();
    }
    // A status menu opened on the final card must remain inside the visible
    // board viewport rather than being clipped by its scrollable column.
    if (!only || only === 'board') {
    const board = await browser.newPage({ viewport: { width: 390, height: 568 } });
    await board.setContent(`<div class="issues-board" style="height:400px"><div class="kanban-columns"><div class="kanban-column" style="height:360px"><div class="kanban-cards" style="height:340px">${Array(8).fill('<div class="kanban-card-wrap"><div class="kanban-card" style="min-height:70px">Card</div></div>').join('')}<div class="kanban-card-wrap"><div class="kanban-card" style="min-height:70px"><div class="status-picker"><button class="status-picker-trigger">Status</button><ul class="status-menu">${['Backlog','Started','Done','Paused','Canceled','Queued','Review','Blocked'].map(s=>`<li><button>${s}</button></li>`).join('')}</ul></div></div></div></div></div></div>`);
    await board.addStyleTag({ content: css });
    await board.locator('.kanban-cards').evaluate(el => { el.scrollTop = el.scrollHeight; });
    const boardBoxes = await board.evaluate(() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect();
      const menu = box('.status-menu');
      const visible = box('.kanban-cards');
      return { menuTop: menu.top, menuBottom: menu.bottom, visibleTop: visible.top, visibleBottom: visible.bottom };
    });
    assert.ok(boardBoxes.menuTop >= boardBoxes.visibleTop && boardBoxes.menuBottom <= boardBoxes.visibleBottom,
      'Last-card status menu is clipped by scrolling board column');
    await board.close();
    }
    // The filter panel has more fields than fit on short screens. The final
    // action must be scrollable inside the visible panel, not lost below it.
    if (!only || only === 'filters') for (const [width, height] of [[320, 568], [390, 844], [701, 600]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      await page.setContent(`<div class="app"><div class="app-body"><div class="workspace-content"><main class="main"><header class="page-head my-issues-head"><h1>My issues</h1></header><nav class="my-issue-tabs"><button>Assigned</button></nav><div class="my-issues-view"><div class="my-issue-toolbar"><button>Filter</button></div><div class="my-issue-popover" aria-label="Issue filters">${Array(14).fill('<label>Filter<select><option>All entries</option></select></label>').join('')}<button>Clear filters</button></div><div class="my-issues-list">List</div></div></main></div></div></div>`);
      await page.addStyleTag({ content: css });
      const state = await page.evaluate(() => {
        const panel = document.querySelector('.my-issue-popover');
        const button = panel.querySelector('button');
        panel.scrollTop = panel.scrollHeight;
        const a = panel.getBoundingClientRect(), b = button.getBoundingClientRect();
        return { top:a.top, bottom:a.bottom, buttonTop:b.top, buttonBottom:b.bottom,
          scrollHeight:panel.scrollHeight, clientHeight:panel.clientHeight };
      });
      assert.ok(state.top >= 0 && state.bottom <= height && state.scrollHeight > state.clientHeight &&
        state.buttonTop >= state.top && state.buttonBottom <= state.bottom,
        `${width}x${height}: filter action cannot be reached inside viewport`);
      await page.close();
    }
    if (!only || only === 'status-history') for (const width of [320, 390, 701]) {
      const page = await browser.newPage({ viewport: { width, height: 568 } });
      await page.setContent('<section class="issue-card"><div class="issue-row"><button class="issue-row-open">Some issue with title</button><span class="status-picker" style="position:relative"><button class="status-picker-trigger">Started</button><span class="status-history" role="tooltip">In started for 3 weeks. No status changes recorded.</span></span></div></section>');
      await page.addStyleTag({ content: css });
      const state = await page.evaluate(() => {
        const card = document.querySelector('.issue-card');
        const tip = document.querySelector('.status-history');
        const c = card.getBoundingClientRect(), t = tip.getBoundingClientRect();
        return { left:t.left, right:t.right, top:t.top, bottom:t.bottom, cardBottom:c.bottom,
          cardOverflow:getComputedStyle(card).overflow };
      });
      assert.ok(state.left >= 0 && state.right <= width &&
        !(state.cardOverflow !== 'visible' && state.bottom > state.cardBottom),
        `${width}px: status history clipped by issue row or viewport`);
      await page.close();
    }
    if (!only || only === 'status-menu') for (const width of [320, 390, 900]) {
      const page = await browser.newPage({ viewport: { width, height: 600 } });
      await page.setContent('<section class="issue-card"><div class="issue-row"><button class="issue-row-open"><span class="issue-copy"><b>Example issue</b><small>ABC-1</small></span></button><span class="status-picker"><button class="status-picker-trigger">Started</button><ul class="status-menu"><li><button>Backlog</button></li><li><button>Started</button></li><li><button>Completed</button></li></ul></span></div></section>');
      await page.addStyleTag({ content: css });
      const state = await page.evaluate(() => {
        const card = document.querySelector('.issue-card'), menu = document.querySelector('.status-menu');
        const c = card.getBoundingClientRect(), m = menu.getBoundingClientRect();
        return { left:m.left, right:m.right, bottom:m.bottom, cardBottom:c.bottom,
          cardOverflow:getComputedStyle(card).overflow };
      });
      assert.ok(state.left >= 0 && state.right <= width &&
        !(state.cardOverflow !== 'visible' && state.bottom > state.cardBottom),
        `${width}px: issue-row status menu is clipped or off-screen`);
      await page.close();
    }
    console.log('Work surfaces: tabs, board/list menus, filters and status history passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
