// Run with: node scripts/test-project-layout.cjs (requires local-e2e Playwright Chromium).
// A DOM fixture exercises the production stylesheet without backend credentials.
const { chromium } = require('../local-e2e/node_modules/playwright');
const path = require('node:path');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const css = fs.readFileSync(path.resolve(__dirname, process.env.PROJECT_LAYOUT_CSS || '../frontend/src/styles.css'), 'utf8');
const longText = 'A project description with enough detail to wrap across the page. '.repeat(80);
const fixture = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div class="app"><div class="app-body"><aside class="sidebar">Navigation</aside><div class="workspace-content"><main class="main"><section class="project-detail project-reference"><div class="project-reference-head">Project</div><nav class="project-reference-tabs">Overview</nav><header><div class="project-mark"></div><div><h1>Long-running project</h1><p>${longText}</p></div></header><div class="project-reference-properties"><h2>Properties</h2><div><span>Status</span><b>In Progress</b></div><div><span>Teams</span><b>Engineering</b></div><button class="outline">Edit project</button></div><button class="project-update-entry">Write project update</button><section class="project-overview-description"><h2>Description</h2><p>${longText}</p></section><div class="project-detail-stats">Progress</div></section></main></div></div></div></body></html>`;

function intersects(a, b) {
  return a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1200, 901, 900, 700, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.goto('about:blank');
      await page.setContent(fixture, { waitUntil: 'load' });
      await page.addStyleTag({ content: css });
      const boxes = await page.evaluate(() => Object.fromEntries(
        ['header', '.project-reference-properties', '.project-update-entry', '.project-overview-description', '.project-reference-properties button']
          .map(selector => [selector, document.querySelector('.project-reference > ' + selector)?.getBoundingClientRect().toJSON()
            || document.querySelector(selector).getBoundingClientRect().toJSON()])));
      const properties = boxes['.project-reference-properties'];
      for (const selector of ['header', '.project-update-entry', '.project-overview-description']) {
        assert.ok(!intersects(boxes[selector], properties), `${width}px: ${selector} overlaps properties`);
      }
      const button = boxes['.project-reference-properties button'];
      assert.ok(button.width > 0 && button.height > 0 && button.left >= 0 && button.right <= width,
        `${width}px: settings control must be visible and within the viewport`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        `${width}px: no horizontal page overflow`);
      // A tall settings panel must contribute to the detail section's height;
      // otherwise the following page content can scroll/paint behind it.
      await page.evaluate(() => {
        document.querySelector('.project-reference > header p').textContent = 'Short description';
        document.querySelector('.project-overview-description p').textContent = 'Short description';
        const panel = document.querySelector('.project-reference-properties');
        for (let i = 0; i < 18; i++) {
          const row = document.createElement('div');
          row.innerHTML = '<span>Setting</span><b>Extra metadata and project controls</b>';
          panel.append(row);
        }
      });
      const heights = await page.evaluate(() => ({
        section: document.querySelector('.project-reference').getBoundingClientRect().bottom,
        panel: document.querySelector('.project-reference-properties').getBoundingClientRect().bottom,
      }));
      assert.ok(heights.section >= heights.panel - 1,
        `${width}px: tall settings panel escapes the project detail section`);
      await page.close();
    }
    console.log('Project detail layout: 5 viewports passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
