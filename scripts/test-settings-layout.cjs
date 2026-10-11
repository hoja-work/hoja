// Run with: node scripts/test-settings-layout.cjs after cd local-e2e && npm ci.
// Exercises the real stylesheet with representative settings and issues shells.
const { chromium } = require('../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const css = fs.readFileSync(path.resolve(__dirname, process.env.UI_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const long = 'Long workspace description and team settings must wrap without hiding controls. '.repeat(12);
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div class="app"><header class="topbar"><button class="mobile-nav-toggle" aria-label="Open settings navigation">☰</button></header><div class="app-body"><aside class="sidebar settings-sidebar" aria-label="Settings navigation"><button class="settings-back">Back to app</button><nav><div class="nav-group"><p>Settings</p><button class="nav-item active">Preferences</button><button class="nav-item">Profile</button></div></nav></aside><div class="workspace-content"><main class="main"><section class="settings-page"><header class="settings-page-heading"><h1>Preferences</h1><p>${long}</p></header><div class="settings-group"><h2>Appearance</h2><div class="setting-row"><div><b>${long}</b><p>${long}</p></div><div class="setting-control"><button>Light</button><button>Dark</button></div></div></div><form class="settings-group"><label>Display name<input value="Sample user"></label><button>Save changes</button></form></section></main></div></div></div></body></html>`;
const issueHeader = `<div class="main"><div class="my-issues-head"><h1>My issues</h1></div><section class="my-issues-view"></section></div>`;
const workspacePanels = `<div class="app"><div class="app-body"><aside class="sidebar settings-sidebar"></aside><div class="workspace-content"><main class="main"><section class="projects-page"><header class="projects-heading"><h1>Settings</h1></header><section class="modal workspace-settings-form"><h2>Workspace settings</h2><label>Workspace name<input value="${long}"></label><label>Workspace key<input></label></section><form class="modal workspace-settings-form"><h2>Create team</h2><label>Team name<input></label><button>Save team</button></form><section class="modal workspace-settings-form api-keys-settings"><h2>API keys</h2><form class="api-key-create"><label>Name<input></label><label>Access<select><option>Read and write</option></select></label><label>Expires<select><option>Never</option></select></label><button>Create API key</button></form></section></section></main></div></div></div>`;
const providerCard = `<div class="app"><div class="app-body"><aside class="sidebar settings-sidebar"></aside><div class="workspace-content"><main class="main"><section class="settings-page"><div class="settings-group"><div class="issue-card"><div class="issue-row"><span class="issue-copy"><b>VeryLongModelProviderNameThatShouldNeverDisplaceActionsWhenConfigured</b><small>provider · an-extremely-long-model-identifier-with-no-spaces-to-force-layout</small></span><button class="outline">Disable</button><button class="outline danger">Delete</button></div></div></div></section></main></div></div></div>`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 701, 700, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      // Wait for the sidebar's closing transition before measuring geometry.
      if (width <= 700) await page.locator('.settings-sidebar').evaluate(el => el.getAnimations().map(animation => animation.finish()));
      const measure = () => page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        const style = selector => getComputedStyle(document.querySelector(selector));
        return { sidebar: box('.settings-sidebar'), heading: box('.settings-page-heading'), card: box('.settings-group'),
          form: box('form.settings-group'), save: box('form.settings-group button'), nav: box('.mobile-nav-toggle'),
          titleSize: parseFloat(style('.settings-page-heading h1').fontSize),
          navSize: parseFloat(style('.settings-sidebar .nav-item').height),
          scrollWidth: document.documentElement.scrollWidth };
      });
      const closed = await measure();
      const issuePage = await browser.newPage({ viewport: { width, height: 720 } });
      await issuePage.setContent(issueHeader);
      await issuePage.addStyleTag({ content: css });
      const issueSize = await issuePage.locator('.my-issues-head h1').evaluate(el => parseFloat(getComputedStyle(el).fontSize));
      assert.ok(Math.abs(closed.titleSize - issueSize) < 1,
        `${width}px: settings and issues page headings differ (${closed.titleSize}px vs ${issueSize}px)`);
      await issuePage.close();
      assert.ok(closed.navSize <= 30, `${width}px: settings navigation uses shared row height`);
      assert.ok(closed.card.right <= width + 1 && closed.form.right <= width + 1 && closed.save.right <= width + 1,
        `${width}px: card and save action fit viewport`);
      assert.ok(closed.scrollWidth <= width + 1, `${width}px: page overflows horizontally`);
      if (width <= 700) {
        assert.ok(closed.nav.width > 0 && closed.nav.height > 0, `${width}px: navigation opener visible`);
        assert.ok(closed.sidebar.right <= closed.heading.left + 1, `${width}px: closed sidebar covers settings content`);
        await page.locator('.settings-sidebar').evaluate(el => el.classList.add('open'));
        await page.locator('.settings-sidebar').evaluate(el => el.getAnimations().map(animation => animation.finish()));
        const open = await measure();
        assert.ok(open.sidebar.right > 0 && open.sidebar.left < width, `${width}px: open navigation unreachable`);
      } else {
        assert.ok(closed.sidebar.right <= closed.heading.left + 1, `${width}px: sidebar and settings overlap`);
      }
      // Legacy settings panels are page cards, not dialogs: every following
      // panel must begin below its predecessor even when fields contain long text.
      const panels = await browser.newPage({ viewport: { width, height: 720 } });
      await panels.setContent(workspacePanels);
      await panels.addStyleTag({ content: css });
      const boxes = await panels.evaluate(() => ({
        cards: [...document.querySelectorAll('.workspace-settings-form')].map(el => el.getBoundingClientRect().toJSON()),
        controls: [...document.querySelectorAll('.workspace-settings-form input,.workspace-settings-form select,.workspace-settings-form button')].map(el => el.getBoundingClientRect().toJSON()),
        scrollWidth: document.documentElement.scrollWidth,
      }));
      for (let i = 1; i < boxes.cards.length; i++) {
        assert.ok(boxes.cards[i].top >= boxes.cards[i - 1].bottom,
          `${width}px: settings card ${i + 1} overlaps previous card`);
      }
      assert.ok(boxes.controls.every(box => box.left >= 0 && box.right <= width),
        `${width}px: a settings input or action escapes the viewport`);
      assert.ok(boxes.scrollWidth <= width + 1, `${width}px: legacy settings cause horizontal overflow`);
      await panels.close();
      const provider = await browser.newPage({ viewport: { width, height: 720 } });
      await provider.setContent(providerCard);
      await provider.addStyleTag({ content: css });
      const providerBoxes = await provider.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        return { card: box('.issue-card'), copy: box('.issue-copy'),
          actions: [...document.querySelectorAll('.issue-row>button')].map(el => el.getBoundingClientRect().toJSON()) };
      });
      for (const item of [providerBoxes.copy, ...providerBoxes.actions]) {
        assert.ok(item.left >= providerBoxes.card.left && item.right <= providerBoxes.card.right &&
          item.top >= providerBoxes.card.top && item.bottom <= providerBoxes.card.bottom,
        `${width}px: provider text or action escapes its card`);
      }
      await provider.close();
      await page.close();
    }
    console.log('Settings layout: 5 viewports passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
