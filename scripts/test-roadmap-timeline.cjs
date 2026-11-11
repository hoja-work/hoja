// Run with: node scripts/test-roadmap-timeline.cjs after `cd local-e2e && npm ci`
// (or set HOJA_E2E_MODULES to an installed local-e2e node_modules directory).
//
// Guards HOJA-322: the renamed Roadmap tab renders its entries as a sequential
// timeline — a vertical spine with one marker per entry — and the timeline fits
// every audited viewport without horizontal overflow, phone widths included.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const playwrightPath = process.env.HOJA_E2E_MODULES
  ? path.join(process.env.HOJA_E2E_MODULES, 'playwright')
  : '../local-e2e/node_modules/playwright';
const { chromium } = require(playwrightPath);
const css = fs.readFileSync(
  process.env.UI_LAYOUT_CSS || path.resolve(__dirname, '../frontend/src/styles.css'), 'utf8');

const long = 'A deliberately long roadmap summary that must wrap inside its card instead of pushing the timeline past the viewport edge. '.repeat(4);
const item = (status, name, date) => `<li class="roadmap-item" data-status="${status}"><span class="roadmap-marker" aria-hidden="true"></span><div class="roadmap-card"><div class="roadmap-card-top"><strong>${name}</strong><button type="button">Archive</button></div><p>${long}</p><div class="roadmap-meta"><select class="initiative-status-select" aria-label="Status of ${name}"><option selected value="${status}">${status}</option></select><div class="roadmap-projects"><button type="button">Website revamp</button><button type="button">Mobile shell with a deliberately long project name to force wrapping</button></div><time>${date}</time></div></div></li>`;
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div class="app"><div class="app-body"><div class="workspace-content"><main class="main"><section class="initiatives-page"><header class="initiatives-head"><h1>Roadmap</h1><button type="button">New initiative</button></header><div class="initiatives-toolbar"><nav aria-label="Roadmap views"><a aria-current="page" href="/workspace/initiatives/active">Active</a><a href="/workspace/initiatives/planned">Planned</a><a href="/workspace/initiatives">All</a></nav><div><button aria-label="Add filter" aria-expanded="false"></button><button aria-label="Display options" aria-expanded="false"></button></div></div><div class="initiatives-content"><ol class="roadmap-timeline">${item('active', 'Earlier milestone', '2026-12-01')}${item('planned', 'Tie created first', '2026-12-01')}${item('completed', 'Design system', '2026-09-30')}</ol></div></section></main></div></div></div></body></html>`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 701, 700, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 720 } });
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      const result = await page.evaluate(() => {
        const items = [...document.querySelectorAll('.roadmap-timeline > .roadmap-item')];
        const markers = items.map(item => item.querySelector('.roadmap-marker'));
        const rects = markers.map(marker => marker.getBoundingClientRect());
        const spine = getComputedStyle(items[0], '::before');
        const overflowing = [...document.querySelectorAll('body *')]
          .filter(element => element.getBoundingClientRect().right > window.innerWidth + 1);
        return {
          count: items.length,
          markerHeights: rects.map(r => r.height),
          spineVisible: spine.content !== 'none' && spine.width !== '0px',
          cardRight: Math.max(...items.map(i => i.querySelector('.roadmap-card').getBoundingClientRect().right)),
          markerWithinCard: rects.every((r, i) => r.right <= items[i].getBoundingClientRect().right + 1),
          scrollWidth: document.documentElement.scrollWidth,
          overflowSample: overflowing.slice(0, 5).map(e => e.className),
          statusColors: items.map(i => getComputedStyle(i.querySelector('.roadmap-marker')).backgroundColor),
        };
      });
      assert.equal(result.count, 3, `${width}px: the roadmap renders one timeline item per entry`);
      assert.ok(result.markerHeights.every(h => h > 0), `${width}px: every roadmap entry carries a visible timeline marker`);
      assert.ok(result.spineVisible, `${width}px: the timeline draws its sequential spine between markers`);
      assert.ok(result.markerWithinCard, `${width}px: markers sit on the spine, inside their item`);
      assert.ok(result.cardRight <= width + 1, `${width}px: roadmap card escapes the viewport (right ${Math.round(result.cardRight)})`);
      assert.ok(result.scrollWidth <= width + 1, `${width}px: page overflows horizontally ${JSON.stringify(result.overflowSample)}`);
      // Status must remain legible from the marker alone: active and completed differ.
      assert.notEqual(result.statusColors[0], result.statusColors[2],
        `${width}px: active and completed markers are visually distinct`);
      await page.close();
    }
    console.log('ok   roadmap timeline: spine + markers render sequentially and fit 1440/701/700/390/320 without overflow');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(`FAIL ${error.message}`); process.exit(1); });
